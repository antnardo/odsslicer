"""ODSReader: the document itself - zip I/O, sheets, styles lookup, save."""

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import cast
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from lxml import etree

from .dateformats import _default_format_code, _format_code_components
from .fileutils import _replacing
from .formulas import _Remap, _remap_range_list, _rename_odf_formula_sheet, _rename_range_list
from .libreoffice import _recalculate_file
from .properties import DocumentProperties
from .sheet import Sheet
from .styles import _NUMBER_STYLE_TAGS, NumberFormat
from .xmltree import (
    NAMESPACES,
    child,
    children,
    elements,
    insert_after,
    insert_before,
    name_of,
    new,
    parse,
    prefix_of,
    qn,
    remove,
    serialize,
    text,
)
from .xmlutils import _encrypted_parts

logger = logging.getLogger("odsslicer")

# Minimal stand-ins for the optional package parts (ODF 1.2 part 3 makes
# everything but `content.xml` and the mimetype optional, and e.g. Excel
# really does omit `settings.xml`): enough structure for the rest of the
# library - style lookup, `.properties` - to work unchanged.
_BLANK_STYLES_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<office:document-styles"
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    b' office:version="1.2">'
    b"<office:styles/><office:automatic-styles/><office:master-styles/>"
    b"</office:document-styles>"
)
_BLANK_META_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<office:document-meta"
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
    b' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' office:version="1.2">'
    b"<office:meta/>"
    b"</office:document-meta>"
)
_BLANK_SETTINGS_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<office:document-settings"
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
    b' office:version="1.2">'
    b"<office:settings/>"
    b"</office:document-settings>"
)


_STYLE_NAME = qn("style:name")


def _same_number_format(a: etree._Element, b: etree._Element) -> bool:
    """True for two number styles defining the same format, whatever their
    names."""

    def definition(tag: etree._Element) -> object:
        attrs = {k: v for k, v in tag.attrib.items() if k != _STYLE_NAME}
        content = [(element.tag, dict(element.attrib), text(element)) for element in elements(tag)]
        return tag.tag, attrs, content

    return definition(a) == definition(b)


# A chart's attributes holding cell ranges, in its own content.xml, by
# namespace URI (`xmltree.qn`)
_CHART_RANGE_ATTRS = tuple(
    qn(name)
    for name in (
        "table:cell-range-address",
        "chart:values-cell-range-address",
        "chart:label-cell-address",
        "chart:error-lower-range",
        "chart:error-upper-range",
    )
)
_OFFICE_CHART = qn("office:chart")
_END_CELL_ADDRESS = qn("table:end-cell-address")
_NOTIFY_ON_UPDATE = qn("draw:notify-on-update-of-ranges")
_OPENFORMULA = NAMESPACES["of"]


def _carrying(chart: "etree._ElementTree", attr: str) -> "list[etree._Element]":
    """The elements of `chart` with attribute `attr` (by URI), in document
    order, the root included - bs4's `find_all(attrs={attr: True})`, which
    searched the whole document. Wherever a range sits in a chart, it
    follows the cells."""
    return [element for element in chart.getroot().iter(etree.Element) if attr in element.attrib]


def _root_of(element: etree._Element) -> etree._Element:
    """The topmost ancestor of `element`, itself if it has no parent - the
    root of its part's tree, or of a detached subtree (see
    `ODSReader._touched_tag` for why not `getroottree()`)."""
    while (parent := element.getparent()) is not None:
        element = parent
    return element


class EncryptedDocumentError(ValueError):
    """The package is password-protected: its content is encrypted, and
    odsslicer reads none of it rather than read it as an empty document."""


class MalformedDocumentError(ValueError):
    """A part of the package is not well-formed XML. odsslicer reads it as
    written or not at all: a parser that recovers would complete a truncated
    part with whatever it guesses, and `save()` would write that guess back
    over the original."""


def _parse_part(file: Path, name: str, markup: bytes) -> "etree._ElementTree":
    """Package part `name` of `file` as an lxml tree (`xmltree.parse`),
    `MalformedDocumentError` naming the part and the place if it is not
    well-formed."""
    try:
        return parse(markup)
    except etree.XMLSyntaxError as exc:
        raise MalformedDocumentError(f"{file}: {name} is not well-formed XML ({exc})") from exc


class ODSReader:
    _from_template: bool = False  # set by new(): no source file to default save() to

    def __init__(self, file: Path | str, *, verbose: bool = False) -> None:
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

            # Before anything is parsed: an encrypted part parses as an
            # empty document, and a save would write it over the data -
            # see `_encrypted_parts`.
            if "content.xml" in _encrypted_parts(read_optional("META-INF/manifest.xml", b"")):
                raise EncryptedDocumentError(
                    f"{self.file} is password-protected: odsslicer cannot decrypt it."
                    " Open it in LibreOffice and save it without a password to read it here."
                )
            # Document content and automatic styles used in the content.
            self.content = zip.read("content.xml")
            # Styles used in the document content and automatic styles used in the styles themselves.
            self.styles = read_optional("styles.xml", _BLANK_STYLES_XML)
            # Document meta information, such as the author or the time of the last save action.
            self.meta = read_optional("meta.xml", _BLANK_META_XML)
            # Application-specific settings, such as the window size or printer information.
            self.settings = read_optional("settings.xml", _BLANK_SETTINGS_XML)
        # parsed as written, whitespace and attribute order kept, and
        # strictly: a part that is not well-formed XML raises
        # `MalformedDocumentError` here, rather than open repaired
        self.data = _parse_part(self.file, "content.xml", self.content)
        self.styles_data = _parse_part(self.file, "styles.xml", self.styles)
        self.meta_data = _parse_part(self.file, "meta.xml", self.meta)
        # `settings.xml` holds nothing a cell needs, so it is parsed only if
        # something asks for it - see `settings_data`
        self._settings_data: etree._ElementTree | None = None
        # The parts save() regenerates only once something here wrote to
        # their tree; until then it copies their bytes through, which is
        # what keeps the promise that editing values leaves the styling
        # exactly as the spreadsheet application wrote it (see save()).
        self._touched: set[str] = set()
        # the sheets are the tables directly under office:spreadsheet: one
        # held in a cell's frame is no sheet, and LibreOffice shows none for it
        spreadsheet = self._spreadsheet()
        self.tables = children(spreadsheet, "table:table") if spreadsheet is not None else []
        self.sheets_names = [cast(str, table.attrib[qn("table:name")]) for table in self.tables]
        self._sheets: dict[str, Sheet | None] = dict.fromkeys(self.sheets_names)
        # the formats dates written into unformatted cells get, and the cell
        # styles carrying them, shared by every cell (see Cell.value)
        self._default_date_formats: dict[str, str] = {}
        self._date_formatted_styles: dict[tuple[str | None, str], str] = {}
        # the package's other parts parsed on demand - the content of the
        # charts, for their cell ranges - which save() writes back, each with
        # the bytes it was read from; None for an embedded object that is no
        # chart
        self._parts: dict[str, tuple[etree._ElementTree, bytes] | None] = {}
        # see _drawings
        self._drawing_tags: tuple[list[etree._Element], list[etree._Element]] | None = None
        # the prefix the document binds to OpenFormula, see _formula_prefix
        self._openformula: str | None = None
        self._objects_read = False  # see _read_objects
        # number formats, by name: whether each counts time in full, [HH]:MM
        # (see Cell._counts_elapsed_time)
        self._elapsed_formats: dict[str, bool] = {}
        logger.log(self._log_level, "    %r", self)

    def __repr__(self) -> str:
        return f"ODSReader({self.file}, sheets={self.sheets_names})"

    @property
    def properties(self) -> "DocumentProperties":
        """Structured, writable access to `meta.xml`'s document properties
        - see `DocumentProperties`."""
        return DocumentProperties(self)

    @property
    def settings_data(self) -> "etree._ElementTree":
        """`settings.xml` - what the application remembers of the window
        rather than of the document: frozen panes, the active sheet, the
        cursor's position - as an lxml tree, parsed on first access.
        Elements are named by namespace URI, whatever prefix the file
        binds: `xmltree.qn("config:config-item")`.

        Reading a cell never needs it, and `save()` copies its bytes
        through, so a document nothing asks it of is never parsed at all:
        the cost is paid by the code that wants it. A missing part (Excel
        ships none) gives the blank skeleton above, so that writing to it
        works the same way. A part that is not well-formed XML raises
        `MalformedDocumentError`.

        Whatever writes here must call `_touched_tag` on what it wrote (or
        `_touched_part("settings.xml")`), or `save()` will copy the original
        bytes over the change."""
        if self._settings_data is None:
            self._settings_data = _parse_part(self.file, "settings.xml", self.settings)
        return self._settings_data

    def _touched_part(self, name: str) -> None:
        """Record that the in-memory tree of package part `name` no longer
        matches the file, so that `save()` serialises it instead of copying
        its bytes through."""
        self._touched.add(name)

    def _touched_tag(self, tag: etree._Element) -> None:
        """Record a write to `tag`, wherever it lives: the part whose tree
        holds it is then serialised by `save()`. For a tag of `content.xml`
        or `meta.xml`, which are always regenerated, there is nothing to
        record. The way for code that writes to a tag it found rather than
        made - a number format of `styles.xml`, say - to say so without
        knowing which part it came from.

        The element climbs to its tree's root. Not by `getroottree()`: an
        element taken out of its tree still belongs to the same lxml
        document, and `getroottree()` answers that document's root - a
        write to a detached element would count as one to the part.
        Climbing, it is a root of its own, and belongs to no part."""
        top = tag
        while (parent := top.getparent()) is not None:
            top = parent
        for name, part in self._lazy_parts().items():
            if part.getroot() is top:
                self._touched_part(name)

    def _lazy_parts(self) -> "dict[str, etree._ElementTree]":
        """The parsed parts `save()` writes back only once touched, by the
        name they have in the package. `settings.xml` is absent until
        something parses it; so is a chart's content (see `_charts`)."""
        parts: dict[str, etree._ElementTree] = {"styles.xml": self.styles_data}
        if self._settings_data is not None:
            parts["settings.xml"] = self._settings_data
        parts.update({name: part[0] for name, part in self._parts.items() if part is not None})
        return parts

    def _serialized(self, name: str, part: "etree._ElementTree") -> bytes:
        """Parsed part `name` back to bytes, by `xmltree.serialize`, under
        the XML declaration it was read with."""
        if name == "styles.xml":
            return serialize(part, self.styles)
        if name == "settings.xml":
            return serialize(part, self.settings)
        return serialize(part, cast("tuple[etree._ElementTree, bytes]", self._parts[name])[1])

    def _find_style(self, name: "str | None", family: "str | None" = None) -> "etree._Element | None":
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

    def _find_number_style(self, name: "str | None") -> "etree._Element | None":
        """A `<number:*-style>` by name, wherever it lives - like cell
        styles, a number format can be defined in either file."""
        if not name:
            return None
        return self._find_in_styles(_NUMBER_STYLE_TAGS, {"style:name": name})

    def _find_in_styles(self, names: "str | list[str]", attrs: "dict[str, str]") -> "etree._Element | None":
        """The first element named `names` with `attrs` among the automatic
        styles of `content.xml`, else in `styles.xml`, anywhere in it, its
        root included. The only styles `content.xml` holds are its automatic
        ones: searching the whole of it for a style of `styles.xml` - its
        column's default, `Default`, for most cells - cost a pass over every
        cell of every sheet, each time."""
        tags = [qn(name) for name in ([names] if isinstance(names, str) else names)]
        wanted = [(qn(key), value) for key, value in attrs.items()]

        def first(candidates: "Iterator[etree._Element]") -> "etree._Element | None":
            for element in candidates:
                if all(element.get(key) == value for key, value in wanted):
                    return element
            return None

        automatic = child(self._content_root(), "office:automatic-styles")
        found = first(automatic.iterchildren(*tags)) if automatic is not None else None
        # `is None`, not `or`: a style with no child element is false in lxml
        return found if found is not None else first(self.styles_data.getroot().iter(*tags))

    def _document_locale(self) -> "tuple[str | None, str | None]":
        """The document's default language and country - `fo:language` and
        `fo:country` of its default cell style in `styles.xml`, where
        LibreOffice records them - `None` for either one it does not give."""
        family = qn("style:family")
        default = next(
            (
                element
                for element in self.styles_data.getroot().iter(qn("style:default-style"))
                if element.get(family) == "table-cell"
            ),
            None,
        )
        props = child(default, "style:text-properties") if default is not None else None
        if props is None:
            return None, None
        language, country = props.get(qn("fo:language")), props.get(qn("fo:country"))
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
            tag.set(qn("number:language"), language)
            tag.set(qn("number:country"), cast(str, country))
        if elapsed:  # [HH]: hours in full, not around the clock
            tag.set(qn("number:truncate-on-overflow"), "false")
        name = cast(str, tag.attrib[_STYLE_NAME])
        # a document odsslicer saved before has it already
        for other in children(self._automatic_styles(), name_of(tag)):
            if other is not tag and _same_number_format(other, tag):
                remove(tag)
                name = cast(str, other.attrib[_STYLE_NAME])
                break
        self._default_date_formats[kind] = name
        return name

    def _content_root(self) -> etree._Element:
        """`<office:document-content>`, the root element of `content.xml`:
        the automatic styles and the body are its children."""
        return self.data.getroot()

    def _spreadsheet(self) -> "etree._Element | None":
        """`<office:spreadsheet>`, whose `<table:table>` children are the
        sheets - `None` in a document that has none."""
        body = child(self._content_root(), "office:body")
        return child(body, "office:spreadsheet") if body is not None else None

    def _automatic_styles(self) -> etree._Element:
        """The `<office:automatic-styles>` element in `content.xml` - the
        only place a newly created style can go and still survive `save()`
        (styles already in `styles.xml` are copied through unchanged, see
        `save()`)."""
        root = self._content_root()
        styles = child(root, "office:automatic-styles")
        if styles is None:
            styles = new("office:automatic-styles")
            body = child(root, "office:body")
            if body is not None:
                insert_before(body, styles)
            else:
                root.append(styles)
        return styles

    def _formula_prefix(self) -> str:
        """The prefix a formula odsslicer writes names OpenFormula with -
        `of` in `of:=SUM([.A1:.A3])`: the one the document binds to its
        namespace, which only the document's declarations resolve, since a
        prefix inside a value is text to any XML parser. A document binding
        none gets `of` declared on its root (see `_declare_on_root`): a
        formula written `of:=` with no declaration says nothing a reader can
        resolve, though LibreOffice takes it - and the corpus has files
        written that way by another tool."""
        if self._openformula is None:
            root = self._content_root()
            prefix = prefix_of(root, _OPENFORMULA)
            if prefix is None:
                prefix = "of"
                if root.nsmap.get(prefix) is None:
                    self._declare_on_root(prefix, _OPENFORMULA)
            self._openformula = prefix
        return self._openformula

    def _declare_on_root(self, prefix: str, uri: str) -> None:
        """Declare `prefix` for `uri` on the root of `content.xml`. lxml
        cannot add a declaration to an existing element: the root is made
        again, its name, attributes, declarations and content kept, and the
        content moved under it - the elements, and every `Sheet` and `Cell`
        holding one, stay the same objects. A pass over the document, paid
        once, by a document that binds no prefix to OpenFormula and is given
        a formula."""
        old = self._content_root()
        nsmap = cast("dict[str, str]", dict(old.nsmap))
        nsmap[prefix] = uri
        root = etree.Element(old.tag, nsmap=nsmap)
        for key, value in old.attrib.items():
            root.set(key, value)
        root.text = old.text
        for element in list(old):
            root.append(element)  # its tail along
        self.data._setroot(root)

    def _new_style_name(self, prefix: str) -> str:
        """A `style:name` not already used by any style in the document
        (either file), of the form `f"{prefix}{n}"` for the smallest
        available `n`."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        namespaces = {"style": NAMESPACES["style"]}
        for doc in (self.data, self.styles_data):
            # every element's, the root's included, whatever the prefix
            for name in cast("list[str]", doc.xpath("//@style:name", namespaces=namespaces)):
                m = pattern.match(name)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"{prefix}{max_n + 1}"

    def _new_style_tag(self, family: str, prefix: str) -> etree._Element:
        """A brand new, empty `<style:style style:family=family>` with a
        fresh unique name, already inserted into `content.xml`'s automatic
        styles.

        Deliberately parentless: callers fill it in themselves (see
        `Cell._copy_resolved_style_into`), because `style:parent-style-name`
        only means anything when it names a style from `styles.xml`."""
        tag = new("style:style", {"style:name": self._new_style_name(prefix), "style:family": family})
        self._automatic_styles().append(tag)
        return tag

    _TEMPLATE_PATH = Path(__file__).parent / "_template.ods"

    @classmethod
    def new(cls, sheet_name: str = "Sheet1", *, verbose: bool = False) -> "ODSReader":
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
            reader.tables[0].set(qn("table:name"), sheet_name)
            reader.sheets_names[0] = sheet_name
            reader._sheets = {sheet_name: reader._sheets.pop("Sheet1")}
        return reader

    def save(
        self,
        path: Path | str | None = None,
        *,
        recalculate: bool = False,
        timeout: int = 120,
        update_links: bool = False,
    ) -> None:
        """Write the in-memory content back out as a .ods file.

        `content.xml` (sheets, cell data, automatic styles, formulas) and
        `meta.xml` (`.properties` - title, author, custom properties...)
        are regenerated from their in-memory trees. Every other zip member
        - `styles.xml`, `settings.xml`, `manifest.xml`, a chart's own
        content, the thumbnail - is copied through byte for byte, unless
        something in the library wrote to its tree, in which case that tree
        is serialised (`_touched_part`). Defaults to overwriting
        `self.file` - except for a document created with `ODSReader.new()`,
        which has no source file of its own and requires an explicit
        `path`.

        Copying by default is what makes "we change values, not styling" a
        property of the code rather than a hope: parsing and serialising
        back is faithful in meaning but not to the byte - attributes come
        out in another order, an empty element collapses to `<x/>`, `&apos;`
        becomes `'`. (It used to lose something too: a run of spaces in a
        text node was squeezed to one, which for `<number:text>   </number:text>`
        in a number format cost it its padding - every part is now parsed so
        that whitespace comes back as written, as lxml keeps it.) A part
        nothing touched never goes through any of it.

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
            "content.xml": serialize(self.data, self.content),
            "meta.xml": serialize(self.meta_data, self.meta),
        }
        regenerated.update(
            (name, self._serialized(name, part))
            for name, part in self._lazy_parts().items()
            if name in self._touched
        )
        with ZipFile(self.file) as src:
            entries = [
                (item, regenerated.get(item.filename, src.read(item.filename))) for item in src.infolist()
            ]
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

    def _drawings(self) -> "tuple[list[etree._Element], list[etree._Element]]":
        """The elements of `content.xml` recording the cell they end in,
        `table:end-cell-address`, and the embedded objects, `<draw:object>`,
        looked for once: a structural edit would otherwise scan the whole
        document for them, which took a dozen times longer than the edit on
        a 10,000-row sheet. odsslicer never creates any, and one deleted
        with its row is only left detached. A document holding neither, as
        its raw text shows, skips the scan - whatever the prefix, which a
        document may bind otherwise: the search itself goes by namespace."""
        if self._drawing_tags is None:
            if b":end-cell-address" in self.content or b":object" in self.content:
                root = self._content_root()
                self._drawing_tags = (
                    [element for element in root.iter(etree.Element) if _END_CELL_ADDRESS in element.attrib],
                    list(root.iter(qn("draw:object"))),
                )
            else:
                self._drawing_tags = ([], [])
        # an element deleted with its row or column since is out of the
        # document: there is nothing left to remap, and its chart is no
        # longer drawn there - a drawing moved elsewhere is moved, not
        # deleted. lxml keeps a detached element readable, where bs4 emptied
        # it: it has to be told apart, by the root it climbs to
        ended, objects = self._drawing_tags
        root = self._content_root()
        if not all(_root_of(tag) is root for tag in (*ended, *objects)):
            self._drawing_tags = (
                [tag for tag in ended if _root_of(tag) is root],
                [tag for tag in objects if _root_of(tag) is root],
            )
        return self._drawing_tags

    def _charts(self) -> "list[tuple[str, etree._ElementTree]]":
        """The content of every chart embedded in the document, each the
        `content.xml` of an object of the package (`Object 1/`...) that a
        `<draw:object>` points at, parsed once with lxml and kept for
        save(), with the name it has in the package: parsing one is not
        editing it, so a caller about to rewrite its ranges has to say so
        (`_touched_part`) for save() to write the tree back.

        Its elements are found by namespace, whatever prefixes it binds.
        An object's part that is not well-formed XML raises
        `MalformedDocumentError` - before the edit, see `_read_objects`."""
        wanted = []
        for tag in self._drawings()[1]:
            href = tag.get(qn("xlink:href"), "").removeprefix("./").rstrip("/")
            if href:
                wanted.append(f"{href}/content.xml")
        missing = [name for name in wanted if name not in self._parts]
        if missing:
            with ZipFile(self.file) as package:
                members = set(package.namelist())
                for name in missing:
                    self._parts[name] = (
                        self._chart_part(name, package.read(name)) if name in members else None
                    )
        return [(name, part[0]) for name in wanted if (part := self._parts[name]) is not None]

    def _chart_part(self, name: str, markup: bytes) -> "tuple[etree._ElementTree, bytes] | None":
        """Object part `name` parsed, with the bytes it was read from, if it
        is a chart's - `None` for another object, an embedded formula say."""
        tree = _parse_part(self.file, name, markup)
        # `office:chart` anywhere, the root included, where bs4's `find` looked for it
        return (tree, markup) if next(tree.getroot().iter(_OFFICE_CHART), None) is not None else None

    def _read_objects(self) -> None:
        """Parse the content of every object the package embeds, if not done
        yet, before a structural edit changes anything: one that is not
        well-formed XML raises `MalformedDocumentError` with the document as
        it was, rather than once the edit is halfway through.

        Found among the package's members, not through the `<draw:object>`
        elements: looking those up now would fill `_drawings`' cache before
        the edit, with elements the edit may then discard. `_drawings` leaves
        those out since a row's drawings past the grid are rescued (it raised
        TypeError on LibreOffice's ser_labels.ods), but the cache is still
        better filled once the document is in the shape the edit left it. A
        document whose content names no object has nothing to read, as for
        `_drawings`."""
        if self._objects_read:
            return
        if b":object" in self.content:
            with ZipFile(self.file) as package:
                for name in package.namelist():
                    if name.endswith("/content.xml") and name not in self._parts:
                        self._parts[name] = self._chart_part(name, package.read(name))
        self._objects_read = True  # once every part has parsed: a malformed one raises again

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
            ends = cast(str, tag.attrib[_END_CELL_ADDRESS])
            # deleted, the end keeps its address - the cell taking its place -
            # as LibreOffice keeps it: that of a range's first cell
            tag.set(_END_CELL_ADDRESS, _remap_range_list(ends, target_sheet, remap, "start"))
        for tag in objects:
            if _NOTIFY_ON_UPDATE in tag.attrib:
                ranges = cast(str, tag.attrib[_NOTIFY_ON_UPDATE])
                tag.set(_NOTIFY_ON_UPDATE, _remap_range_list(ranges, target_sheet, remap))
        for name, chart in self._charts():
            self._touched_part(name)
            for attr in _CHART_RANGE_ATTRS:
                for element in _carrying(chart, attr):
                    element.set(attr, _remap_range_list(cast(str, element.get(attr)), target_sheet, remap))

    def _rename_drawing_references(self, old_name: str, new_name: str) -> None:
        """`_remap_drawing_references`' counterpart for a sheet renamed."""
        ended, objects = self._drawings()
        for tag in ended:
            ends = cast(str, tag.attrib[_END_CELL_ADDRESS])
            tag.set(_END_CELL_ADDRESS, _rename_range_list(ends, old_name, new_name))
        for tag in objects:
            if _NOTIFY_ON_UPDATE in tag.attrib:
                ranges = cast(str, tag.attrib[_NOTIFY_ON_UPDATE])
                tag.set(_NOTIFY_ON_UPDATE, _rename_range_list(ranges, old_name, new_name))
        for name, chart in self._charts():
            self._touched_part(name)
            for attr in _CHART_RANGE_ATTRS:
                for element in _carrying(chart, attr):
                    element.set(attr, _rename_range_list(cast(str, element.get(attr)), old_name, new_name))

    def export_content_xml(self, *, pretty: bool = True) -> None:
        """Write `content.xml` next to the document, as `<name>.xml`: the
        in-memory tree indented by lxml with `pretty=True` - where an element
        holds text, lxml leaves its content as it is - else the part's bytes
        as read."""
        target = self.file.with_suffix(".xml")
        if pretty:
            with target.open("wb") as f:
                f.write(etree.tostring(self.data, encoding="UTF-8", xml_declaration=True, pretty_print=True))
        else:
            with target.open("wb") as f:
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

        table = new("table:table", {"table:name": name})
        table.append(new("table:table-column"))
        row = new("table:table-row")
        row.append(new("table:table-cell"))
        table.append(row)

        insert_after(self.tables[-1], table)
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
        pointed into this sheet is now backed by a detached XML element,
        which save() no longer writes - stop using it."""
        if name not in self.sheets_names:
            raise KeyError(f"No sheet named {name}")
        if len(self.sheets_names) <= 1:
            raise ValueError("cannot delete the only remaining sheet in the document")
        idx = self.sheets_names.index(name)
        remove(self.tables[idx])
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
        self._read_objects()  # a malformed chart raises before anything is renamed

        idx = self.sheets_names.index(old_name)
        self.tables[idx].set(qn("table:name"), new_name)
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
                        cell._write_formula(renamed)
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
        remove(table)
        del self.tables[old_index]
        del self.sheets_names[old_index]

        self.tables.insert(index, table)
        self.sheets_names.insert(index, name)

        if index == 0:
            insert_before(self.tables[1], table)
        else:
            insert_after(self.tables[index - 1], table)
