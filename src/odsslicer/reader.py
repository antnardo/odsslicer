# -*- coding: utf-8 -*-
"""ODSReader: the document itself - zip I/O, sheets, styles lookup, save."""

import logging
import re
from pathlib import Path
from typing import Union, cast
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from bs4 import BeautifulSoup, Tag

from .dateformats import _default_format_code, _format_code_components
from .fileutils import _replacing
from .formulas import _Remap, _remap_range_list, _rename_odf_formula_sheet, _rename_range_list
from .libreoffice import _recalculate_file
from .properties import DocumentProperties
from .sheet import Sheet
from .styles import _NUMBER_STYLE_TAGS, NumberFormat
from .xmlutils import _blank_template, _new_qualified_tag

logger = logging.getLogger("odsslicer")

# Minimal stand-ins for the optional package parts (ODF 1.2 part 3 makes
# everything but `content.xml` and the mimetype optional, and e.g. Excel
# really does omit `settings.xml`): enough structure for the rest of the
# library - style lookup, `.properties` - to work unchanged.
_BLANK_STYLES_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<office:document-styles'
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    b' office:version="1.2">'
    b"<office:styles/><office:automatic-styles/><office:master-styles/>"
    b"</office:document-styles>"
)
_BLANK_META_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<office:document-meta'
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
    b' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' office:version="1.2">'
    b"<office:meta/>"
    b"</office:document-meta>"
)


def _same_number_format(a: Tag, b: Tag) -> bool:
    """True for two number styles defining the same format, whatever their
    names."""

    def definition(tag: Tag) -> object:
        attrs = {k: v for k, v in tag.attrs.items() if k != "style:name"}
        children = [
            (child.prefix, child.name, child.attrs, child.get_text())
            for child in tag.find_all(True, recursive=False)
        ]
        return tag.prefix, tag.name, attrs, children

    return definition(a) == definition(b)


class ODSReader:
    _from_template: bool = False  # set by new(): no source file to default save() to

    def __init__(self, file: Union[Path, str], verbose: bool = False) -> None:
        self.file: Path = Path(file)
        self.verbose = verbose
        # chatty progress messages go to the "odsslicer" logger: DEBUG
        # normally, INFO with verbose=True - configure logging to see them
        self._log_level = logging.INFO if verbose else logging.DEBUG
        logger.log(self._log_level, "Opening %s...", self.file)
        # http://docs.oasis-open.org/office/v1.2/
        # Only `content.xml` (and the mimetype) is guaranteed: real-world
        # generators omit the rest - Excel ships no `settings.xml` at all.
        with ZipFile(file) as zip:
            members = set(zip.namelist())

            def read_optional(name: str, fallback: bytes) -> bytes:
                return zip.read(name) if name in members else fallback

            # Document content and automatic styles used in the content.
            self.content = zip.read("content.xml")
            # Styles used in the document content and automatic styles used in the styles themselves.
            self.styles = read_optional("styles.xml", _BLANK_STYLES_XML)
            # Document meta information, such as the author or the time of the last save action.
            self.meta = read_optional("meta.xml", _BLANK_META_XML)
            # Application-specific settings, such as the window size or printer information.
            self.settings = read_optional("settings.xml", b"")
        self.data = BeautifulSoup(self.content, "xml")
        self.styles_data = BeautifulSoup(self.styles, "xml")
        self.meta_data = BeautifulSoup(self.meta, "xml")
        self.tables = self.data.find_all("table:table")
        self.sheets_names = [table["table:name"] for table in self.tables]
        self._sheets: dict[str, Sheet | None] = {name: None for name in self.sheets_names}
        # the formats dates written into unformatted cells get, and the cell
        # styles carrying them, shared by every cell (see Cell.value)
        self._default_date_formats: dict[str, str] = {}
        self._date_formatted_styles: dict[tuple[str | None, str], str] = {}
        # the package's other parts parsed on demand - the content of the
        # charts, for their cell ranges - which save() writes back; None
        # for an embedded object that is no chart
        self._parts: dict[str, BeautifulSoup | None] = {}
        self._drawing_tags: tuple[list[Tag], list[Tag]] | None = None  # see _drawings
        logger.log(self._log_level, "    %r", self)

    def __repr__(self) -> str:
        return f"ODSReader({self.file}, sheets={self.sheets_names})"

    @property
    def properties(self) -> "DocumentProperties":
        """Structured, writable access to `meta.xml`'s document properties
        - see `DocumentProperties`."""
        return DocumentProperties(self)

    def _find_style(self, name: "str | None", family: "str | None" = None) -> "Tag | None":
        """A `<style:style>` by name (optionally constrained to a
        `style:family`, e.g. `"table-cell"`/`"table-row"`/`"table-column"`/
        `"table"` - names are conventionally unique per family in real
        files, but nothing enforces that): automatic styles (`content.xml`)
        first, then named/common styles (`styles.xml`) - a `table:style-name`
        can point at either."""
        if not name:
            return None
        attrs = {"style:name": name}
        if family is not None:
            attrs["style:family"] = family
        return self._find_in_styles("style:style", attrs)

    def _find_number_style(self, name: "str | None") -> "Tag | None":
        """A `<number:*-style>` by name, wherever it lives - like cell
        styles, a number format can be defined in either file."""
        if not name:
            return None
        return self._find_in_styles(_NUMBER_STYLE_TAGS, {"style:name": name})

    def _find_in_styles(
        self, names: "str | list[str]", attrs: "dict[str, str]"
    ) -> "Tag | None":
        """The first element named `names` with `attrs` among the automatic
        styles of `content.xml`, else in `styles.xml`. The only styles
        `content.xml` holds are its automatic ones: searching the whole of it
        for a style of `styles.xml` - its column's default, `Default`, for
        most cells - cost a pass over every cell of every sheet, each time."""
        automatic = cast("Tag | None", self.data.find("office:automatic-styles"))
        found = automatic.find(names, attrs=attrs) if automatic is not None else None
        return cast("Tag | None", found or self.styles_data.find(names, attrs=attrs))

    def _document_locale(self) -> "tuple[str | None, str | None]":
        """The document's default language and country - `fo:language` and
        `fo:country` of its default cell style in `styles.xml`, where
        LibreOffice records them - `None` for either one it does not give."""
        default = self.styles_data.find(
            "style:default-style", attrs={"style:family": "table-cell"}
        )
        props = cast(
            "Tag | None",
            default.find("style:text-properties") if default is not None else None,
        )
        if props is None:
            return None, None
        language, country = props.get("fo:language"), props.get("fo:country")
        return (
            None if language in (None, "", "none") else cast(str, language),
            None if country in (None, "", "none") else cast(str, country),
        )

    def _default_date_format(self, kind: str) -> str:
        """The name of the number format a `kind` of date or time value (see
        `_default_format_code`) gets in a cell with no format, as it would in
        LibreOffice if typed there: created in the automatic styles on first
        use, unless the document already has an identical one."""
        if kind in self._default_date_formats:
            return self._default_date_formats[kind]
        code, language, country = _default_format_code(kind, *self._document_locale())
        components, elapsed = _format_code_components(code)
        family = "time" if kind in ("time", "duration") else "date"
        tag = NumberFormat.create(self, family, components=components)._tag
        if language is not None:
            tag.attrs["number:language"] = language
            tag.attrs["number:country"] = cast(str, country)
        if elapsed:  # [HH]: hours in full, not around the clock
            tag.attrs["number:truncate-on-overflow"] = "false"
        name = cast(str, tag["style:name"])
        # a document odsslicer saved before has it already
        qualified_name = f"{tag.prefix}:{tag.name}"
        for other in self._automatic_styles().find_all(qualified_name, recursive=False):
            if other is not tag and _same_number_format(other, tag):
                tag.decompose()
                name = cast(str, other["style:name"])
                break
        self._default_date_formats[kind] = name
        return name

    def _automatic_styles(self) -> Tag:
        """The `<office:automatic-styles>` element in `content.xml` - the
        only place a newly created style can go and still survive `save()`
        (styles already in `styles.xml` are copied through unchanged, see
        `save()`)."""
        styles = cast("Tag | None", self.data.find("office:automatic-styles"))
        if styles is None:
            styles = _new_qualified_tag("office:automatic-styles")
            body = self.data.find("office:body")
            if body is not None:
                body.insert_before(styles)
            else:
                self.data.append(styles)
        return styles

    def _new_style_name(self, prefix: str) -> str:
        """A `style:name` not already used by any style in the document
        (either file), of the form `f"{prefix}{n}"` for the smallest
        available `n`."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        for doc in (self.data, self.styles_data):
            for tag in doc.find_all(attrs={"style:name": True}):
                m = pattern.match(tag.get("style:name") or "")
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"{prefix}{max_n + 1}"

    def _new_style_tag(self, family: str, prefix: str) -> Tag:
        """A brand new, empty `<style:style style:family=family>` with a
        fresh unique name, already inserted into `content.xml`'s automatic
        styles.

        Deliberately parentless: callers fill it in themselves (see
        `Cell._copy_resolved_style_into`), because `style:parent-style-name`
        only means anything when it names a style from `styles.xml`."""
        tag = _blank_template(self.data, "style:style")
        tag.attrs["style:name"] = self._new_style_name(prefix)
        tag.attrs["style:family"] = family
        self._automatic_styles().append(tag)
        return tag

    _TEMPLATE_PATH = Path(__file__).parent / "_template.ods"

    @classmethod
    def new(cls, sheet_name: str = "Sheet1", verbose: bool = False) -> "ODSReader":
        """Create a brand new, empty spreadsheet - not backed by any file on
        disk yet - with a single sheet named `sheet_name`.

        Bootstrapped from a minimal template bundled with the package (a
        valid, empty ODF document needs several non-trivial pieces - a
        `mimetype`, `META-INF/manifest.xml`, `styles.xml`... - that only a
        real spreadsheet application can produce correctly, so this reuses
        one rather than hand-assembling them). `save(path)` requires an
        explicit path (there is no source file to default to).
        """
        reader = cls(cls._TEMPLATE_PATH, verbose=verbose)
        reader._from_template = True
        if sheet_name != "Sheet1":
            reader.tables[0]["table:name"] = sheet_name
            reader.sheets_names[0] = sheet_name
            reader._sheets = {sheet_name: reader._sheets.pop("Sheet1")}
        return reader

    def save(
        self,
        path: Union[Path, str, None] = None,
        recalculate: bool = False,
        timeout: int = 120,
        update_links: bool = False,
    ) -> None:
        """Write the in-memory content back out as a .ods file.

        `content.xml` (sheets, cell data, automatic styles, formulas) and
        `meta.xml` (`.properties` - title, author, custom properties...)
        are regenerated from their in-memory trees; every other zip member
        (`styles.xml`, `settings.xml`, `manifest.xml`, thumbnail...) is
        copied through unchanged from the source file. Defaults to
        overwriting `self.file` - except for a document created with
        `ODSReader.new()`, which has no source file of its own and
        requires an explicit `path`.

        The file is written under a temporary name in the same folder, then
        renamed over `path`: a program reading it meanwhile sees either the
        old version or the new one, never a partial file, and a save that
        fails or is interrupted leaves the old version as it was. The saved
        file keeps the permissions of the one it replaces, a symbolic link
        is followed, and a read-only file raises `PermissionError`.

        With `recalculate=True`, a local LibreOffice is then run headless on
        the saved file to compute every formula and refresh every pivot
        table - see the module-level `recalculate()` for details and
        requirements. Its result replaces the file the same way, through a
        temporary file and a rename. The in-memory document is *not*
        reloaded: reopen the file (`ODSReader(path)`) to read the computed
        values back.
        `update_links=True`, only with `recalculate=True`, has LibreOffice
        update links to other workbooks first, so that formulas reading them
        compute instead of giving `Err:540` - for workbooks you trust only,
        see `recalculate()`.
        """
        if update_links and not recalculate:
            # unlike an unused `timeout`, ignoring it would silently leave
            # every formula reading another workbook as it was
            raise ValueError("update_links=True requires recalculate=True")
        if path is None:
            if self._from_template:
                raise ValueError(
                    "this document was created with ODSReader.new() and has no "
                    "source file of its own - pass an explicit path to save(...)"
                )
            path = self.file
        regenerated = {
            "content.xml": self.data.encode("utf-8"),
            "meta.xml": self.meta_data.encode("utf-8"),
        }
        regenerated.update(
            (name, part.encode("utf-8")) for name, part in self._parts.items() if part is not None
        )
        with ZipFile(self.file) as src:
            entries = [(item, regenerated.get(item.filename, src.read(item.filename))) for item in src.infolist()]
        # a regenerated part absent from the source package (every part but
        # `content.xml` is optional in ODF, see __init__) still has to be
        # written, or the in-memory edits to it would be dropped
        present = {item.filename for item, _ in entries}
        entries += [(ZipInfo(name), data) for name, data in regenerated.items() if name not in present]
        with _replacing(Path(path)) as file, ZipFile(file, "w") as dst:
            for item, data in entries:
                # the ODF spec requires `mimetype` to be the first entry and stored uncompressed
                item.compress_type = ZIP_STORED if item.filename == "mimetype" else ZIP_DEFLATED
                dst.writestr(item, data)
        if recalculate:
            # the parameter shadows the module function
            _recalculate_file(path, timeout=timeout, update_links=update_links)

    # A chart's attributes holding cell ranges, in its own content.xml
    _CHART_RANGE_ATTRS = (
        "table:cell-range-address",
        "chart:values-cell-range-address",
        "chart:label-cell-address",
        "chart:error-lower-range",
        "chart:error-upper-range",
    )

    def _drawings(self) -> "tuple[list[Tag], list[Tag]]":
        """The elements of `content.xml` recording the cell they end in,
        `table:end-cell-address`, and the embedded objects, `<draw:object>`,
        looked for once: a structural edit would otherwise scan the whole
        document for them, which took a dozen times longer than the edit on
        a 10,000-row sheet. odsslicer never creates any, and one deleted
        with its row is only left detached. A document holding neither, as
        its raw text shows, skips the scan."""
        if self._drawing_tags is None:
            if b"table:end-cell-address" in self.content or b"draw:object" in self.content:
                self._drawing_tags = (
                    self.data.find_all(attrs={"table:end-cell-address": True}),
                    self.data.find_all("draw:object"),
                )
            else:
                self._drawing_tags = ([], [])
        return self._drawing_tags

    def _charts(self) -> "list[BeautifulSoup]":
        """The content of every chart embedded in the document, each the
        `content.xml` of an object of the package (`Object 1/`...) that a
        `<draw:object>` points at, parsed once and kept for save()."""
        wanted = []
        for tag in self._drawings()[1]:
            href = cast(str, tag.get("xlink:href", "")).removeprefix("./").rstrip("/")
            if href:
                wanted.append(f"{href}/content.xml")
        missing = [name for name in wanted if name not in self._parts]
        if missing:
            with ZipFile(self.file) as package:
                members = set(package.namelist())
                for name in missing:
                    part = BeautifulSoup(package.read(name), "xml") if name in members else None
                    self._parts[name] = part if part and part.find("office:chart") else None
        return [part for name in wanted if (part := self._parts[name]) is not None]

    def _remap_drawing_references(self, target_sheet: str, remap: _Remap) -> None:
        """After a structural edit of `target_sheet`, have what drawings hold
        of its cells follow them, as LibreOffice has them follow (issue #15):
        a chart's ranges - in its own content, and listed again on its
        `<draw:object>` for LibreOffice to know when to redraw it - and the
        cell a drawing anchored to a cell ends in, `table:end-cell-address`,
        from which LibreOffice sizes a chart on open. They used to stay as
        they were: a row inserted in a chart's data left the last day out
        of it, and the chart shrank by a row."""
        ended, objects = self._drawings()
        for tag in ended:
            ends = cast(str, tag["table:end-cell-address"])
            # deleted, the end keeps its address - the cell taking its place -
            # as LibreOffice keeps it: that of a range's first cell
            tag["table:end-cell-address"] = _remap_range_list(ends, target_sheet, remap, "start")
        for tag in objects:
            if "draw:notify-on-update-of-ranges" in tag.attrs:
                ranges = cast(str, tag["draw:notify-on-update-of-ranges"])
                tag["draw:notify-on-update-of-ranges"] = _remap_range_list(ranges, target_sheet, remap)
        for chart in self._charts():
            for attr in self._CHART_RANGE_ATTRS:
                for tag in chart.find_all(attrs={attr: True}):
                    tag[attr] = _remap_range_list(cast(str, tag[attr]), target_sheet, remap)

    def _rename_drawing_references(self, old_name: str, new_name: str) -> None:
        """`_remap_drawing_references`' counterpart for a sheet renamed."""
        ended, objects = self._drawings()
        for tag in ended:
            ends = cast(str, tag["table:end-cell-address"])
            tag["table:end-cell-address"] = _rename_range_list(ends, old_name, new_name)
        for tag in objects:
            if "draw:notify-on-update-of-ranges" in tag.attrs:
                ranges = cast(str, tag["draw:notify-on-update-of-ranges"])
                tag["draw:notify-on-update-of-ranges"] = _rename_range_list(ranges, old_name, new_name)
        for chart in self._charts():
            for attr in self._CHART_RANGE_ATTRS:
                for tag in chart.find_all(attrs={attr: True}):
                    tag[attr] = _rename_range_list(cast(str, tag[attr]), old_name, new_name)

    def export_content_xml(self, pretty: bool = True) -> None:
        if pretty:
            with open(f"{self.file.with_suffix('.xml')}", "w", encoding="utf8") as f:
                f.write(self.data.prettify())
        else:
            with open(f"{self.file.with_suffix('.xml')}", "wb") as f:
                f.write(self.content)

    @property
    def sheets(self) -> list[Sheet]:
        return [self.sheet(name) for name in self.sheets_names]

    def sheet(self, name: str) -> Sheet:
        if name not in self.sheets_names:
            raise KeyError(f"No sheet named {name}")
        cached = self._sheets[name]
        if cached is None:
            cached = Sheet(self.tables[self.sheets_names.index(name)], verbose=self.verbose, reader=self)
            self._sheets[name] = cached
        return cached

    def add_sheet(self, name: str) -> Sheet:
        """Create a new, empty sheet named `name` and append it after the last
        existing sheet (`table:table` elements must stay grouped together and
        come before things like `table:named-expressions` in the document).

        The new sheet's XML mirrors the minimal shape of a genuinely empty ODF
        sheet (one column definition, one row with one empty cell) and carries
        no particular style, same as newly grown rows/cells.
        """
        if not name:
            raise ValueError("a sheet name is required")
        if name in self.sheets_names:
            raise ValueError(f"a sheet named {name!r} already exists")

        table = _blank_template(self.data, "table:table")
        table.attrs["table:name"] = name
        table.append(_blank_template(self.data, "table:table-column"))
        row = _blank_template(self.data, "table:table-row")
        row.append(_blank_template(self.data, "table:table-cell"))
        table.append(row)

        self.tables[-1].insert_after(table)
        self.tables.append(table)
        self.sheets_names.append(name)
        new_sheet = Sheet(table, verbose=self.verbose, reader=self)
        self._sheets[name] = new_sheet
        return new_sheet

    def delete_sheet(self, name: str) -> None:
        """Remove the sheet named `name` entirely.

        Raises `KeyError` for an unknown name, and `ValueError` for the
        document's last remaining sheet (an ODF spreadsheet needs at least
        one). Any `Sheet`/`Cell` object obtained before the call that
        pointed into this sheet is now backed by a decomposed, detached
        XML element - stop using it."""
        if name not in self.sheets_names:
            raise KeyError(f"No sheet named {name}")
        if len(self.sheets_names) <= 1:
            raise ValueError("cannot delete the only remaining sheet in the document")
        idx = self.sheets_names.index(name)
        self.tables[idx].decompose()
        del self.tables[idx]
        del self.sheets_names[idx]
        del self._sheets[name]

    def rename_sheet(self, old_name: str, new_name: str) -> None:
        """Rename sheet `old_name` to `new_name`.

        Also rewrites any formula elsewhere in the document (in this
        sheet or any other) that references this sheet by name -
        `OldName.A1` becomes `NewName.A1`, quoted (`'New Name'.A1`) if
        `new_name` needs it. An unqualified reference within the renamed
        sheet's own formulas (`.A1`, meaning "this sheet") needs no
        rewrite - it already means the same thing regardless of the name.

        Raises `KeyError` for an unknown `old_name`, and `ValueError`
        for an empty `new_name` or one already used by another sheet."""
        if old_name not in self.sheets_names:
            raise KeyError(f"No sheet named {old_name}")
        if not new_name:
            raise ValueError("a sheet name is required")
        if new_name != old_name and new_name in self.sheets_names:
            raise ValueError(f"a sheet named {new_name!r} already exists")

        idx = self.sheets_names.index(old_name)
        self.tables[idx].attrs["table:name"] = new_name
        self.sheets_names[idx] = new_name
        sheet_obj = self._sheets.pop(old_name, None)
        if sheet_obj is not None:
            sheet_obj.name = new_name
        self._sheets[new_name] = sheet_obj

        for sheet in self.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell.formula is None:
                        continue
                    renamed = _rename_odf_formula_sheet(cell.formula, old_name, new_name)
                    if renamed != cell.formula:
                        cell.formula = renamed
        self._rename_drawing_references(old_name, new_name)

    def move_sheet(self, name: str, index: int) -> None:
        """Move sheet `name` to position `index` (0-based) among the
        document's sheets, shifting the others - e.g. `move_sheet("Data",
        0)` makes it the first tab.

        Raises `KeyError` for an unknown name, and `ValueError` if
        `index` is out of range."""
        if name not in self.sheets_names:
            raise KeyError(f"No sheet named {name}")
        if not 0 <= index < len(self.sheets_names):
            raise ValueError(f"index {index} out of range (document has {len(self.sheets_names)} sheets)")

        old_index = self.sheets_names.index(name)
        if index == old_index:
            return

        table = self.tables[old_index]
        table.extract()
        del self.tables[old_index]
        del self.sheets_names[old_index]

        self.tables.insert(index, table)
        self.sheets_names.insert(index, name)

        if index == 0:
            self.tables[1].insert_before(table)
        else:
            self.tables[index - 1].insert_after(table)
