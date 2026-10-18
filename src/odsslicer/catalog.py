"""The document's styles, found and made: the one place that knows where they
live - the automatic styles of `content.xml`, then `styles.xml` - and that a
style odsslicer makes can only go in the first.

`ODSReader` looked styles up and named new ones until 1.0's audit, while
`Cell` forked a cell's style and `Sheet` a row's, a column's or a sheet's,
each with its own copy of what a fork must carry. Finding and making share
what matters: a lookup must search the automatic styles before `styles.xml`,
a new name must be free in both parts, and a fork must carry what its source
resolves to through the parts' rules. `_StyleCatalog` holds that, over the
package's trees; the style views (`styles`) read through it, and the cells,
rows, columns and sheets ask it for a style of their own.

It keeps three caches, each of what the document gains as it is written and
never loses: the number format each kind of date gets in a cell with no
format, the cell styles carrying it, and whether a number format counts time
in full. A style it finds is looked up afresh every time: an index by name
would be faster (B1, in ROADMAP.md, measured it) but a cache to keep in step
with every write.

Nothing here is public (`__all__` is empty): `ODSReader` holds the catalog,
and every other module reaches it through the reader.
"""

import re
from collections.abc import Iterator
from typing import cast

from lxml import etree

from odsslicer.dateformats import _default_format_code, _format_code_components
from odsslicer.numberformat import _NUMBER_STYLE_TAGS, NumberFormatSpec, _number_format_element
from odsslicer.package import _Package
from odsslicer.xmltree import (
    NAMESPACES,
    child,
    children,
    clone,
    elements,
    insert_before,
    name_of,
    new,
    qn,
    remove,
    text,
)
from odsslicer.xmlutils import _ensure_style_child, _is_forked_style_name

__all__: list[str] = []

_STYLE_NAME = qn("style:name")
_STYLE_FAMILY = qn("style:family")
_PARENT_STYLE_NAME = qn("style:parent-style-name")
_DATA_STYLE_NAME = qn("style:data-style-name")
# the prefix of the name of a cell's own style, forked by odsslicer
_CELL_STYLE_PREFIX = "ocs"
# `<style:style>` children whose attributes merge along the inheritance
# chain (a nearer style overriding one attribute must not drop the
# others); any other child - `style:map`, carrying conditional formats -
# is taken whole from the nearest style that has one.
_STYLE_PROPERTY_TAGS = frozenset(
    qn(name)
    for name in ("style:text-properties", "style:table-cell-properties", "style:paragraph-properties")
)


def _same_number_format(a: etree._Element, b: etree._Element) -> bool:
    """True for two number styles defining the same format, whatever their
    names."""

    def definition(tag: etree._Element) -> object:
        attrs = {k: v for k, v in tag.attrib.items() if k != _STYLE_NAME}
        content = [(element.tag, dict(element.attrib), text(element)) for element in elements(tag)]
        return tag.tag, attrs, content

    return definition(a) == definition(b)


class _StyleCatalog:
    """Finding a style of the document by name, and making new ones in the
    automatic styles of `content.xml`."""

    def __init__(self, package: _Package) -> None:
        self._package = package
        # the formats dates written into unformatted cells get, by kind, and
        # the cell styles carrying them, shared by every cell (see
        # `date_formatted_style`)
        self._default_date_formats: dict[str, str] = {}
        self._date_formatted_styles: dict[tuple[str | None, str], str] = {}
        # number formats, by name: whether each counts time in full, [HH]:MM
        self._elapsed_formats: dict[str, bool] = {}

    # finding

    def find_style(self, name: "str | None", family: "str | None" = None) -> "etree._Element | None":
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
        return self._find("style:style", attrs)

    def find_number_style(self, name: "str | None") -> "etree._Element | None":
        """A `<number:*-style>` by name, wherever it lives - like cell
        styles, a number format can be defined in either file."""
        if not name:
            return None
        return self._find(_NUMBER_STYLE_TAGS, {"style:name": name})

    def _find(self, names: "str | list[str]", attrs: "dict[str, str]") -> "etree._Element | None":
        """The first element named `names` with `attrs` among the automatic
        styles of `content.xml`, else in `styles.xml`, anywhere in it, its
        root included. The only styles `content.xml` holds are its automatic
        ones: searching the whole of it for a style of `styles.xml` - its
        column's default, `Default`, for most cells - cost a pass over every
        cell of every sheet, each time."""
        tags = [qn(name) for name in ([names] if isinstance(names, str) else names)]
        wanted = [(qn(key), value) for key, value in attrs.items()]

        # a loop, not `all()` over a generator: a cell's style is looked up
        # along its whole chain on every read of one of its properties, and
        # building a generator per element compared took a third of the time
        def first(candidates: "Iterator[etree._Element]") -> "etree._Element | None":
            for element in candidates:
                for key, value in wanted:
                    if element.get(key) != value:
                        break
                else:
                    return element
            return None

        automatic = child(self._package.content_root(), "office:automatic-styles")
        found = first(automatic.iterchildren(*tags)) if automatic is not None else None
        # `is None`, not `or`: a style with no child element is false in lxml
        return found if found is not None else first(self._package.styles_data.getroot().iter(*tags))

    def data_style_name(self, name: "str | None") -> "str | None":
        """The name of the number format the cell style `name` gives a cell,
        following its parents."""
        visited: set[str] = set()
        while name and name not in visited:
            visited.add(name)
            style = self.find_style(name, family="table-cell")
            if style is None:
                return None
            if _DATA_STYLE_NAME in style.attrib:
                return cast(str, style.get(_DATA_STYLE_NAME))
            name = style.get(_PARENT_STYLE_NAME)
        return None

    def counts_elapsed_time(self, number_format: "str | None") -> bool:
        """Whether the number format named `number_format` counts time in
        full - `[HH]:MM:SS`, `number:truncate-on-overflow="false"` - a
        duration's (see `Cell._counts_elapsed_time`). The answer is kept per
        number format, a column of times asking it for every cell."""
        if number_format is None:
            return False
        if number_format not in self._elapsed_formats:
            tag = self.find_number_style(number_format)
            elapsed = tag is not None and tag.get(qn("number:truncate-on-overflow")) == "false"
            self._elapsed_formats[number_format] = elapsed
        return self._elapsed_formats[number_format]

    def document_locale(self) -> "tuple[str | None, str | None]":
        """The document's default language and country - `fo:language` and
        `fo:country` of its default cell style in `styles.xml`, where
        LibreOffice records them - `None` for either one it does not give."""
        default = next(
            (
                element
                for element in self._package.styles_data.getroot().iter(qn("style:default-style"))
                if element.get(_STYLE_FAMILY) == "table-cell"
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

    # making

    def automatic_styles(self) -> etree._Element:
        """The `<office:automatic-styles>` element in `content.xml` - the
        only place a newly created style can go and still survive `save()`
        (styles already in `styles.xml` are copied through unchanged, see
        `ODSReader.save`)."""
        root = self._package.content_root()
        styles = child(root, "office:automatic-styles")
        if styles is None:
            styles = new("office:automatic-styles")
            body = child(root, "office:body")
            if body is not None:
                insert_before(body, styles)
            else:
                root.append(styles)
        return styles

    def new_style_name(self, prefix: str) -> str:
        """A `style:name` not already used by any style in the document
        (either file), of the form `f"{prefix}{n}"` for the smallest
        available `n`."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        namespaces = {"style": NAMESPACES["style"]}
        for doc in (self._package.data, self._package.styles_data):
            # every element's, the root's included, whatever the prefix
            for name in cast("list[str]", doc.xpath("//@style:name", namespaces=namespaces)):
                m = pattern.match(name)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"{prefix}{max_n + 1}"

    def new_style(self, family: str, prefix: str) -> etree._Element:
        """A brand new, empty `<style:style style:family=family>` with a
        fresh unique name, already inserted into `content.xml`'s automatic
        styles.

        Deliberately parentless: callers fill it in themselves (see
        `fork_cell_style`), because `style:parent-style-name` only means
        anything when it names a style from `styles.xml`."""
        tag = new("style:style", {"style:name": self.new_style_name(prefix), "style:family": family})
        self.automatic_styles().append(tag)
        return tag

    def fork_cell_style(self, source: "str | None") -> etree._Element:
        """A new cell style carrying everything the cell style `source`
        resolves to - a cell's own, to write to without touching any other.

        The obvious implementation - pointing the fork at the old style via
        `style:parent-style-name` - writes a file no spreadsheet renders as
        the library reads it back: only *named* styles (`styles.xml`) are
        addressable ancestors, and LibreOffice silently falls back to
        `Default` when `style:parent-style-name` names an automatic style
        (issue #1). A cell's style is almost always an automatic one, so the
        fork has to carry the properties itself: walk up to the nearest
        named ancestor - which becomes the fork's real parent - and copy
        every automatic style passed on the way, farthest first so a nearer
        style wins, exactly as `CellStyle` resolves them on read."""
        tag = self.new_style("table-cell", _CELL_STYLE_PREFIX)
        automatic: list[etree._Element] = []
        seen: set[str | None] = set()
        current = self.find_style(source, family="table-cell")
        while current is not None:
            name = current.get(_STYLE_NAME)
            if name in seen:
                break  # a malformed file could cycle; resolve what we have
            seen.add(name)
            if next(current.iterancestors(qn("office:styles")), None) is not None:
                # a named style: a parent link to it does hold, and keeping
                # the link (rather than copying) preserves its own updates
                tag.set(_PARENT_STYLE_NAME, cast(str, name))
                break
            automatic.append(current)
            parent = current.get(_PARENT_STYLE_NAME)
            current = self.find_style(parent, family="table-cell") if parent else None
        for style in reversed(automatic):
            _copy_style_into(tag, style)
        return tag

    def fork_sheet_part_style(
        self, current_name: "str | None", family: str, prefix: str, props_tag_name: str
    ) -> etree._Element:
        """The single, uniquely-owned automatic style behind one row's,
        column's or sheet's `table:style-name` - `current_name`'s own if it
        is one odsslicer forked already, a fork of it otherwise.

        Unlike cell styles, row/column/sheet styles don't meaningfully use
        `style:parent-style-name` inheritance in practice (see `RowStyle`/
        `ColumnStyle`/`TableStyle`), so forking instead copies the current
        style's properties over verbatim - otherwise a later write of just
        one property (e.g. `.height`) would silently lose whatever else
        (e.g. `.visible`) the previously-shared style carried, since these
        are resolved from one single style tag rather than a chain."""
        if _is_forked_style_name(current_name, prefix):
            # a fork lives in the automatic styles of `content.xml`, and only
            # a tag from there is written to in place: a style of
            # `styles.xml` that happens to bear such a name is forked like
            # any other, since save() copies that part through untouched
            for tag in children(self.automatic_styles(), "style:style"):
                if tag.get(_STYLE_NAME) == current_name and tag.get(_STYLE_FAMILY) == family:
                    return tag
        old_tag = self.find_style(current_name, family=family) if current_name else None
        tag = self.new_style(family, prefix)
        if old_tag is not None:
            old_props = child(old_tag, props_tag_name)
            if old_props is not None:
                props = _ensure_style_child(tag, props_tag_name)
                for key, value in old_props.attrib.items():
                    props.set(key, value)
        return tag

    def default_date_format(self, kind: str) -> str:
        """The name of the number format a `kind` of date or time value (see
        `_default_format_code`) gets in a cell with no format, as it would in
        LibreOffice if typed there: created in the automatic styles on first
        use, unless the document already has an identical one."""
        if kind in self._default_date_formats:
            return self._default_date_formats[kind]
        code, language, country = _default_format_code(kind, *self.document_locale())
        components, elapsed = _format_code_components(code)
        family = "time" if kind in ("time", "duration") else "date"
        spec = NumberFormatSpec(family, components=components)
        tag = _number_format_element(spec, self.new_style_name("N"), ("", ""))
        self.automatic_styles().append(tag)
        if language is not None:
            tag.set(qn("number:language"), language)
            tag.set(qn("number:country"), cast(str, country))
        if elapsed:  # [HH]: hours in full, not around the clock
            tag.set(qn("number:truncate-on-overflow"), "false")
        name = cast(str, tag.attrib[_STYLE_NAME])
        # a document odsslicer saved before has it already
        for other in children(self.automatic_styles(), name_of(tag)):
            if other is not tag and _same_number_format(other, tag):
                remove(tag)
                name = cast(str, other.attrib[_STYLE_NAME])
                break
        self._default_date_formats[kind] = name
        return name

    def date_formatted_style(self, source: "str | None", kind: str) -> str:
        """The name of a cell style carrying what `source` resolves to, plus
        the format a `kind` of date or time gets (`default_date_format`):
        one shared by every cell whose style `source` was - not one per cell,
        of which a column of dates would make a thousand."""
        key = (source, self.default_date_format(kind))
        name = self._date_formatted_styles.get(key)
        if name is None:
            tag = self.fork_cell_style(source)
            tag.set(_DATA_STYLE_NAME, key[1])
            name = self._date_formatted_styles[key] = cast(str, tag.get(_STYLE_NAME))
        return name


def _copy_style_into(tag: etree._Element, source: etree._Element) -> None:
    """Copy what the automatic style `source` sets onto `tag`, over what
    `tag` has: its number format, the attributes of its properties one by
    one, and any other child whole (see `_STYLE_PROPERTY_TAGS`)."""
    if _DATA_STYLE_NAME in source.attrib:  # the number format
        tag.set(_DATA_STYLE_NAME, cast(str, source.get(_DATA_STYLE_NAME)))
    for element in elements(source):
        if element.tag in _STYLE_PROPERTY_TAGS:
            properties = _ensure_style_child(tag, name_of(element))
            for key, value in element.attrib.items():
                properties.set(key, value)
        else:
            for previous in list(tag.iterchildren(element.tag)):
                remove(previous)  # this nearer style replaces them
            tag.append(clone(element))
