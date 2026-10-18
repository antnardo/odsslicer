# mypy: disable-error-code="union-attr"
# (`Element | None` unions are narrowed dynamically all over this module,
# guarded by runtime checks mypy can't see through - silencing that one error
# class here beats dozens of value-free asserts/casts. Every other error
# class, and all signatures, remain fully checked.)
"""Resolved ODF styles: Border, CellStyle, RowStyle, ColumnStyle, TableStyle -
views of the style elements behind a cell, a row, a column or a sheet, read
and written through. The number formats a cell style names are
`numberformat`'s."""

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Protocol, TypeVar, cast, overload

from lxml import etree

from odsslicer.numberformat import (
    NumberFormat,
)
from odsslicer.xmltree import attributes, child, qn, unset
from odsslicer.xmlutils import _ensure_style_child

if TYPE_CHECKING:
    from odsslicer.cell import Cell
    from odsslicer.reader import ODSReader
    from odsslicer.sheet import Sheet


__all__ = ["Border", "CellStyle", "ColumnStyle", "RowStyle", "TableStyle"]

_STYLE_NAME = qn("style:name")


def _make_border(raw: "str | None") -> "Border | None":
    # ODF uses the literal string "none" (not just an absent attribute) to
    # explicitly cancel a border/diagonal - both mean "no border" here.
    return Border(raw) if raw and raw != "none" else None


class Border:
    """One side of a cell's border, parsed from ODF's `"<width> <style>
    <color>"` shorthand (e.g. `"0.74pt solid #808080"`)."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        parts = raw.split()
        self.width = parts[0] if len(parts) > 0 else None
        self.style = parts[1] if len(parts) > 1 else None
        self.color = parts[2] if len(parts) > 2 else None

    def __repr__(self) -> str:
        return f"Border({self.raw!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Border) and self.raw == other.raw


def _border_to_raw(value: "Border | str | None") -> "str | None":
    """Normalize a border value accepted on write - a `Border`, a raw ODF
    shorthand string (`"0.5pt solid #000000"`), or `None` - down to the raw
    string form (or `None`)."""
    if value is None:
        return None
    if isinstance(value, Border):
        return value.raw
    if isinstance(value, str):
        return value
    raise TypeError(f"expected a Border, str, or None, got {type(value)!r}")


_TEXT = "style:text-properties"
_CELL = "style:table-cell-properties"
_PARAGRAPH = "style:paragraph-properties"
_ROW = "style:table-row-properties"
_COLUMN = "style:table-column-properties"
_TABLE = "style:table-properties"

_Read = TypeVar("_Read")
_Write = TypeVar("_Write")


class _Style(Protocol):
    """What a `_StyleProperty` reads and writes through: the raw text of an
    attribute as the style resolves it now, and a write to the style its
    owner gets a private copy of."""

    def _resolve(self, element: str, attribute: str) -> "str | None": ...

    def _write_attrs(self, element: str, attributes: "dict[str, str | None]") -> None: ...


class _StyleProperty(Generic[_Read, _Write]):
    """One property of a style, declared once: the properties element and
    the attribute holding it, how the attribute's text reads, and what a
    value writes back (`None` removes the attribute).

    Until 1.0, a style view's `__init__` resolved every property into an
    attribute of the object, and each setter wrote the XML and then that
    copy. Two views of one style diverged as soon as one of them wrote: the
    other kept reading what the style said when it was made. Read from the
    XML on every access, a property has no state to keep in step. The price
    is a walk of the inheritance chain per property read - stopping at the
    nearest style that sets it - where the copy walked it once per view, for
    every property, read or not: a view made for three properties costs about
    twice what it did, one made per property (`cell.style.bold`) less."""

    def __init__(
        self,
        element: str,
        attribute: str,
        read: "Callable[[str | None], _Read]",
        write: "Callable[[_Write], str | None]",
        doc: str,
    ) -> None:
        self.element = element
        self.attribute = attribute
        self._read = read
        self._write = write
        self.__doc__ = doc

    @overload
    def __get__(self, style: None, owner: type) -> "_StyleProperty[_Read, _Write]": ...

    @overload
    def __get__(self, style: _Style, owner: type) -> _Read: ...

    def __get__(self, style: "_Style | None", owner: "type | None" = None) -> Any:
        if style is None:
            return self
        return self._read(style._resolve(self.element, self.attribute))

    def __set__(self, style: _Style, value: _Write) -> None:
        style._write_attrs(self.element, {self.attribute: self._write(value)})


def _same(raw: "str | None") -> "str | None":
    return raw


def _text(element: str, attribute: str, doc: str) -> "_StyleProperty[str | None, str | None]":
    """A property whose value is the attribute's text, `None` when unset."""
    return _StyleProperty(element, attribute, _same, _same, doc)


def _flag(element: str, attribute: str, on: str, off: str, doc: str) -> "_StyleProperty[bool, bool]":
    """A property that is on or off: `on` or `off` written, on when the
    attribute's text is `on`."""
    return _StyleProperty(element, attribute, lambda raw: raw == on, lambda value: on if value else off, doc)


def _drawn(raw: "str | None") -> bool:
    """Whether a line style - an underline's, a strikethrough's - draws one."""
    return raw not in (None, "none")


def _line(attribute: str, doc: str) -> "_StyleProperty[bool, bool]":
    """A line drawn through or under the text: a solid one written, any
    line style but `none` read as one (see `_drawn`)."""
    return _StyleProperty(_TEXT, attribute, _drawn, lambda value: "solid" if value else "none", doc)


def _int_or_none(raw: "str | None") -> "int | None":
    return int(raw) if raw is not None else None


def _int_to_raw(value: "int | None") -> "str | None":
    return None if value is None else str(int(value))


def _diagonal(attribute: str, doc: str) -> "_StyleProperty[Border | None, Border | str | None]":
    return _StyleProperty(_CELL, attribute, _make_border, _border_to_raw, doc)


class _BorderSide:
    """One side of a cell's border. The four are resolved as a unit (see
    `CellStyle._borders`) and written as one (`CellStyle._write_border_sides`),
    which a `_StyleProperty`, one attribute at a time, cannot do."""

    def __init__(self, attribute: str, doc: str) -> None:
        self.attribute = attribute
        self.__doc__ = doc

    @overload
    def __get__(self, style: None, owner: type) -> "_BorderSide": ...

    @overload
    def __get__(self, style: "CellStyle", owner: type) -> "Border | None": ...

    def __get__(self, style: "CellStyle | None", owner: "type | None" = None) -> Any:
        if style is None:
            return self
        return style._borders()[self.attribute]

    def __set__(self, style: "CellStyle", value: "Border | str | None") -> None:
        style._write_border_sides({self.attribute: value})


class CellStyle:
    """Resolved visual formatting and number format behind a cell's
    `table:style-name`. Walks the `style:parent-style-name` inheritance
    chain on read (the nearest style to the cell wins for whichever
    property it sets directly; a property no style in the chain sets is
    `None`). Look up a cell's style via `Cell.style`, not directly.

    A view, not a copy: every property reads the document as it is now, so
    two views of one cell's style - `a = cell.style`, `b = cell.style` -
    agree after either writes, and a view of a cell reads the style the cell
    shows with now, its value too for a conditional number format.

    Writable: setting a property (e.g. `cell.style.bold = True`) forks the
    cell its own private automatic style on first use (see
    `Cell._ensure_own_style`) - the cell's *own* value going forward, on
    top of whatever it already inherited. `border_top`/`border_bottom`/
    `border_left`/`border_right` accept a `Border`, a raw ODF shorthand
    string (`"0.5pt solid #000000"`), or `None` (explicitly no border on
    that side) - setting any one side re-writes all four explicitly on the
    forked style, carrying the other three over from what's currently
    resolved (see `_borders`), so they don't silently fall back to
    unstyled. `diagonal_bl_tr`/`diagonal_tl_br` take the same three forms,
    but `None` simply removes the override (falls back to whatever's
    inherited) - pass the literal string `"none"` to force no diagonal
    regardless of inheritance.

    Not yet writable: `.conditions` (conditional formatting via
    `style:map`) and creating a brand new number format from scratch -
    `number_format` can only be *assigned* an existing `NumberFormat` (or
    its style name) already present in the document."""

    _BORDER_ATTRS = ("fo:border", "fo:border-top", "fo:border-bottom", "fo:border-left", "fo:border-right")

    def __init__(
        self,
        reader: "ODSReader",
        name: "str | None",
        value: object = None,
        cell: "Cell | None" = None,
    ) -> None:
        # a cell's view reads its style's name and its value off the cell, as
        # they are now: `name` serves a view of no cell, and `value` one
        # resolving its number format against a value about to be written
        # (`Cell._resolved_number_format`)
        self._reader = reader
        self._name = name
        self._value = value
        self._cell = cell

    @property
    def name(self) -> "str | None":
        """The name of the style resolved: for a cell's, the one the cell
        shows with now - its own, else its column's default cell style."""
        return self._cell._style_name() if self._cell is not None else self._name

    def _chain(self) -> "Iterator[etree._Element]":
        """The styles this one resolves through, itself first, then each
        `style:parent-style-name` in turn - a cycle, in a malformed file,
        cut where it closes. Lazily: a property the nearest style sets
        looks no parent up."""
        seen = set()
        current = self._reader._catalog.find_style(self.name, family="table-cell")
        while current is not None and current.get(_STYLE_NAME) not in seen:
            seen.add(current.get(_STYLE_NAME))
            yield current
            parent = current.get(qn("style:parent-style-name"))
            current = self._reader._catalog.find_style(parent, family="table-cell") if parent else None

    def _resolve(self, element: str, attribute: str) -> "str | None":
        """The text of `attribute` on the `element` properties of the
        nearest style of the chain setting it, `None` if none does."""
        qualified = qn(attribute)
        for style in self._chain():
            props = child(style, element)
            if props is not None and qualified in props.attrib:
                return cast(str, props.get(qualified))
        return None

    def _borders(self) -> "dict[str, Border | None]":
        """The four sides of the border, by attribute (`"fo:border-top"`...).

        Borders are resolved as a unit from the nearest style that defines
        *any* border info (rather than merging per-side across inheritance
        levels): within that one style, a specific side (fo:border-top...)
        overrides the fo:border shorthand for that side, exactly as ODF
        itself resolves it within a single style declaration."""
        sides = self._BORDER_ATTRS[1:]
        for style in self._chain():
            cell_props = child(style, _CELL)
            if cell_props is None or not any(qn(a) in cell_props.attrib for a in self._BORDER_ATTRS):
                continue
            shorthand = cell_props.get(qn("fo:border"))
            return {side: _make_border(cell_props.get(qn(side), shorthand)) for side in sides}
        return dict.fromkeys(sides)

    def _flattened(self, element: str) -> dict[str, str]:
        """Every attribute of the `element` properties along the chain, the
        nearest style's winning - keyed with the specification's prefixes,
        whatever the file binds (`xmltree.attributes`)."""
        flattened: dict[str, str] = {}
        for style in reversed(list(self._chain())):
            props = child(style, element)
            if props is not None:
                flattened.update(attributes(props))
        return flattened

    def _require_cell(self) -> None:
        if self._cell is None:
            raise RuntimeError(f"style {self.name!r} has no owning Cell and cannot be written to")

    def _write_attrs(self, element: str, attributes: "dict[str, str | None]") -> None:
        """Set each of `attributes` on the `element` properties of this
        cell's own automatic style - removing one whose value is `None`."""
        self._require_cell()
        tag = self._cell._ensure_own_style()
        props = child(tag, element)
        for attribute, raw in attributes.items():
            if raw is None:
                if props is not None:
                    unset(props, qn(attribute))
            else:
                if props is None:
                    props = _ensure_style_child(tag, element)
                props.set(qn(attribute), raw)

    def _write_border_sides(self, updates: "dict[str, Border | str | None]") -> None:
        """Set 1+ of the 4 border sides, carrying the other (currently
        resolved) sides along explicitly - see the class docstring. They are
        read before the cell's style is forked: the fork merges its
        ancestors attribute by attribute, where borders resolve as a unit."""
        self._require_cell()
        sides = {**self._borders(), **updates}
        tag = self._cell._ensure_own_style()
        props = _ensure_style_child(tag, _CELL)
        unset(props, qn("fo:border"))
        for side, value in sides.items():
            raw = _border_to_raw(value)
            props.set(qn(side), raw if raw is not None else "none")

    bold = _flag(_TEXT, "fo:font-weight", "bold", "normal", "Bold text.")
    italic = _flag(_TEXT, "fo:font-style", "italic", "normal", "Italic text.")
    strikethrough = _line(
        "style:text-line-through-style",
        "Struck-through text: a solid line written, any line style but `none` read.",
    )
    font_family = _text(_TEXT, "style:font-name", "The font, by its `style:font-face` name.")
    font_color = _text(_TEXT, "fo:color", 'The text\'s color, `"#rrggbb"`.')
    font_size = _text(_TEXT, "fo:font-size", 'The text\'s size, with its unit: `"10pt"`.')
    text_position = _text(
        _TEXT,
        "style:text-position",
        'Raised or lowered text: `"super 58%"`, `"sub 58%"` - see `superscript`/`subscript`.',
    )
    background_color = _text(_CELL, "fo:background-color", 'The cell\'s background, `"#rrggbb"`.')
    vertical_align = _text(_CELL, "style:vertical-align", '`"top"`, `"middle"`, `"bottom"` or `"automatic"`.')
    writing_mode = _text(_CELL, "style:writing-mode", 'The direction of the text: `"lr-tb"`, `"tb-rl"`...')
    protection = _text(
        _CELL, "style:cell-protect", '`"protected"`, `"none"`, `"formula-hidden"`... when the sheet is.'
    )
    horizontal_align = _text(_PARAGRAPH, "fo:text-align", '`"start"`, `"center"`, `"end"`, `"justify"`...')
    rotation = _StyleProperty(
        _CELL, "style:rotation-angle", _int_or_none, _int_to_raw, "The text's rotation, in degrees."
    )
    wrap_text = _flag(_CELL, "fo:wrap-option", "wrap", "no-wrap", "Text wrapped within the cell.")
    shrink_to_fit = _flag(_CELL, "style:shrink-to-fit", "true", "false", "Text shrunk to fit the cell.")
    diagonal_bl_tr = _diagonal("style:diagonal-bl-tr", "The diagonal line from bottom left to top right.")
    diagonal_tl_br = _diagonal("style:diagonal-tl-br", "The diagonal line from top left to bottom right.")
    border_top = _BorderSide("fo:border-top", "The top side of the border.")
    border_bottom = _BorderSide("fo:border-bottom", "The bottom side of the border.")
    border_left = _BorderSide("fo:border-left", "The left side of the border.")
    border_right = _BorderSide("fo:border-right", "The right side of the border.")

    @property
    def underline(self) -> bool:
        """Underlined text: written as a solid line in the text's color,
        three attributes a `_StyleProperty` - one attribute - cannot set;
        any line style but `none` reads as underlined."""
        return _drawn(self._resolve(_TEXT, "style:text-underline-style"))

    @underline.setter
    def underline(self, value: bool) -> None:
        self._write_attrs(
            _TEXT,
            {
                "style:text-underline-style": "solid" if value else "none",
                "style:text-underline-width": "auto" if value else None,
                "style:text-underline-color": "font-color" if value else None,
            },
        )

    @property
    def superscript(self) -> bool:
        """Text raised as an exponent - `text_position` starting `super`."""
        return (self.text_position or "").startswith("super")

    @superscript.setter
    def superscript(self, value: bool) -> None:
        self.text_position = "super 58%" if value else None

    @property
    def subscript(self) -> bool:
        """Text lowered as an index - `text_position` starting `sub`."""
        return (self.text_position or "").startswith("sub")

    @subscript.setter
    def subscript(self, value: bool) -> None:
        self.text_position = "sub 58%" if value else None

    @property
    def cell_properties(self) -> dict[str, str]:
        """Every attribute of `style:table-cell-properties`, flattened along
        the chain - an escape hatch for what no property surfaces, keyed
        `"fo:background-color"`. A copy: writing to it changes nothing."""
        return self._flattened(_CELL)

    @property
    def text_properties(self) -> dict[str, str]:
        """Every attribute of `style:text-properties`, flattened like
        `cell_properties`."""
        return self._flattened(_TEXT)

    @property
    def number_format(self) -> "NumberFormat | None":
        """The `NumberFormat` the cell shows with, `None` if the chain names
        none: a conditional one resolved against the cell's value (see
        `NumberFormat.resolve`). Assign an existing `NumberFormat`, or its
        name, to change it; `None` removes the cell's own."""
        data_style = qn("style:data-style-name")
        name = next((s.get(data_style) for s in self._chain() if s.get(data_style)), None)
        tag = self._reader._catalog.find_number_style(name)
        if tag is None:
            return None
        base = NumberFormat(tag, reader=self._reader)
        value = self._value if self._value is not None or self._cell is None else self._cell._value
        return base.resolve(value) if value is not None else base

    @number_format.setter
    def number_format(self, fmt: "NumberFormat | str | None") -> None:
        self._require_cell()
        tag = self._cell._ensure_own_style()
        if fmt is None:
            unset(tag, qn("style:data-style-name"))
            self._cell._read_by_format()
            return
        if isinstance(fmt, NumberFormat):
            name = fmt.name
        elif isinstance(fmt, str):
            name = fmt
        else:
            raise TypeError(f"number_format must be a NumberFormat, str, or None, got {type(fmt)!r}")
        if self._cell.sheet.reader._catalog.find_number_style(name) is None:
            raise ValueError(f"no number format named {name!r} exists in this document")
        tag.set(qn("style:data-style-name"), cast(str, name))
        self._cell._read_by_format()

    def __repr__(self) -> str:
        return (
            f"CellStyle(name={self.name!r}, bold={self.bold}, italic={self.italic}, "
            f"background_color={self.background_color!r})"
        )


class _SheetPartStyle:
    """What `RowStyle`, `ColumnStyle` and `TableStyle` share: one style
    element, no inheritance chain. A view with an owner reads the style its
    row, column or sheet points at now - a write through another view forks
    it one of its own - and one without reads the element it was given."""

    # what the error of a write with no owner names
    _OWNER: ClassVar[str]

    def __init__(self, tag: "etree._Element | None" = None, sheet: "Sheet | None" = None) -> None:
        self._tag = tag
        self._sheet = sheet

    def _owned(self) -> bool:
        return self._sheet is not None

    def _current_tag(self) -> "etree._Element | None":
        """The style the owner points at now."""
        raise NotImplementedError

    def _own_tag(self) -> etree._Element:
        """The owner's own style, forked on first use."""
        raise NotImplementedError

    def _style_tag(self) -> "etree._Element | None":
        return self._current_tag() if self._owned() else self._tag

    @property
    def name(self) -> "str | None":
        """The name of the style, `None` for no style."""
        tag = self._style_tag()
        return tag.get(_STYLE_NAME) if tag is not None else None

    def _resolve(self, element: str, attribute: str) -> "str | None":
        tag = self._style_tag()
        props = child(tag, element) if tag is not None else None
        return props.get(qn(attribute)) if props is not None else None

    def _write_attrs(self, element: str, attributes: "dict[str, str | None]") -> None:
        """Set each of `attributes` on the owner's own style - removing one
        whose value is `None`."""
        if not self._owned():
            raise RuntimeError(f"style {self.name!r} has no owning {self._OWNER} and cannot be written to")
        props = _ensure_style_child(self._own_tag(), element)
        for attribute, raw in attributes.items():
            if raw is None:
                unset(props, qn(attribute))
            else:
                props.set(qn(attribute), raw)


def _visible(raw: "str | None") -> bool:
    return raw in (None, "visible")


def _visibility(value: bool) -> str:  # noqa: FBT001 - the value, not a flag
    return "visible" if value else "collapse"


class RowStyle(_SheetPartStyle):
    """Resolved `<style:style style:family="table-row">` behind a row's
    `table:style-name` - no inheritance chain (rows don't meaningfully use
    `style:parent-style-name` in practice). Look up via
    `Sheet.row_style(row)`, not directly.

    Writable: setting `.height`/`.optimal_height`/`.visible` forks the row
    its own private automatic style on first use (see
    `Sheet._ensure_row_style`), carrying over whatever the row's current
    style already had (all 3 properties come from one single style tag,
    not a chain, so nothing here is resolved independently)."""

    _OWNER = "Sheet/row"

    def __init__(
        self, tag: "etree._Element | None" = None, sheet: "Sheet | None" = None, row: "int | None" = None
    ) -> None:
        super().__init__(tag, sheet)
        self._row = row

    def _owned(self) -> bool:
        return self._sheet is not None and self._row is not None

    def _current_tag(self) -> "etree._Element | None":
        return self._sheet._row_style_tag(cast(int, self._row))

    def _own_tag(self) -> etree._Element:
        return self._sheet._ensure_row_style(cast(int, self._row))

    optimal_height = _flag(
        _ROW, "style:use-optimal-row-height", "true", "false", "The row's height follows its content."
    )
    visible = _StyleProperty(
        _ROW, "table:visibility", _visible, _visibility, "The row is shown: hidden when `False`."
    )

    @property
    def height(self) -> "str | None":
        """The row's height, with its unit: `"0.452cm"`. Setting it turns
        `optimal_height` off, or the content would size the row again."""
        return self._resolve(_ROW, "style:row-height")

    @height.setter
    def height(self, value: "str | None") -> None:
        self._write_attrs(_ROW, {"style:row-height": value, "style:use-optimal-row-height": "false"})

    def __repr__(self) -> str:
        return f"RowStyle(name={self.name!r}, height={self.height!r})"


class ColumnStyle(_SheetPartStyle):
    """Resolved `<style:style style:family="table-column">` behind a
    column's `table:style-name` - no inheritance chain. Look up via
    `Sheet.column_style(col)`, not directly.

    Writable: setting `.width`/`.visible` forks the column its own private
    automatic style on first use (see `Sheet._ensure_column_style`),
    carrying over whatever the column's current style already had."""

    _OWNER = "Sheet/column"

    def __init__(
        self, tag: "etree._Element | None" = None, sheet: "Sheet | None" = None, col: "int | None" = None
    ) -> None:
        super().__init__(tag, sheet)
        self._col = col

    def _owned(self) -> bool:
        return self._sheet is not None and self._col is not None

    def _current_tag(self) -> "etree._Element | None":
        return self._sheet._column_style_tag(cast(int, self._col))

    def _own_tag(self) -> etree._Element:
        return self._sheet._ensure_column_style(cast(int, self._col))

    width = _text(_COLUMN, "style:column-width", 'The column\'s width, with its unit: `"2.258cm"`.')
    visible = _StyleProperty(
        _COLUMN, "table:visibility", _visible, _visibility, "The column is shown: hidden when `False`."
    )

    def __repr__(self) -> str:
        return f"ColumnStyle(name={self.name!r}, width={self.width!r})"


def _displayed(raw: "str | None") -> bool:
    return raw != "false"


def _display(value: bool) -> str:  # noqa: FBT001 - the value, not a flag
    return "true" if value else "false"


class TableStyle(_SheetPartStyle):
    """Resolved `<style:style style:family="table">` behind a sheet's
    `table:style-name` - no inheritance chain. Look up via `Sheet.style`,
    not directly.

    Writable: setting `.tab_color`/`.visible` forks the sheet its own
    private automatic style on first use (see
    `Sheet._ensure_table_style`), carrying over whatever the sheet's
    current style already had."""

    _OWNER = "Sheet"

    def _current_tag(self) -> "etree._Element | None":
        return self._sheet._table_style_tag()

    def _own_tag(self) -> etree._Element:
        return self._sheet._ensure_table_style()

    tab_color = _text(_TABLE, "table:tab-color", 'The color of the sheet\'s tab, `"#rrggbb"`.')
    visible = _StyleProperty(
        _TABLE, "table:display", _displayed, _display, "The sheet is shown: hidden when `False`."
    )

    def __repr__(self) -> str:
        return f"TableStyle(name={self.name!r}, tab_color={self.tab_color!r})"
