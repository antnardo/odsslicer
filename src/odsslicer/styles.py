# -*- coding: utf-8 -*-
# mypy: disable-error-code="union-attr"
# (bs4 Tag/NavigableString/None unions are narrowed dynamically all over this
# module, guarded by runtime checks mypy can't see through - silencing that
# one error class here beats dozens of value-free asserts/casts. Every other
# error class, and all signatures, remain fully checked.)
"""Resolved ODF styles: NumberFormat (incl. creation and conditional formats),
Border, CellStyle, RowStyle, ColumnStyle, TableStyle."""

import datetime as dt
import re
from typing import TYPE_CHECKING, Any, Dict, cast

from bs4 import Tag

from .xmlutils import _blank_template, _ensure_style_child

if TYPE_CHECKING:
    from .cell import Cell
    from .reader import ODSReader
    from .sheet import Sheet


_NUMBER_STYLE_TAGS = [
    "number:number-style",
    "number:percentage-style",
    "number:currency-style",
    "number:date-style",
    "number:time-style",
    "number:boolean-style",
    "number:text-style",
]
_DATE_TIME_COMPONENT_NAMES = (
    "day", "month", "year", "day-of-week", "week-of-year", "quarter", "era",
    "hours", "minutes", "seconds", "am-pm", "text",
)


_CONDITION_RE = re.compile(r"^value\(\)\s*(<=|>=|!=|<|>|=)\s*(-?\d+(?:\.\d+)?)$")


def _evaluate_number_format_condition(condition: "str | None", value: Any) -> bool:
    """Evaluate an ODF `style:condition` (the subset used by `style:map` on
    number styles: a comparison of `value()` against a number, e.g.
    `"value()>=0"`). Returns False for a condition outside that subset (a
    cell-content-is-text() condition, a between-range, ...) rather than
    guessing, and False if `value` isn't itself numeric."""
    if not condition:
        return False
    m = _CONDITION_RE.match(condition.strip())
    if not m:
        return False
    op, operand = m.group(1), float(m.group(2))
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return {
        "<": value < operand,
        "<=": value <= operand,
        ">": value > operand,
        ">=": value >= operand,
        "=": value == operand,
        "!=": value != operand,
    }[op]


class NumberFormat:
    """The `<number:*-style>` behind a cell's `style:data-style-name` - the
    document's own real display format (decimal places, currency symbol,
    date/time layout), as opposed to `odsslicer`'s own learn-by-example
    heuristic used when *writing* a value (see `Cell._infer_*_display`).

    A number style can be conditional (`<style:map style:condition="..."
    style:apply-style-name="...">`, e.g. currency amounts shown in red when
    negative): `.conditions` holds `(condition, NumberFormat)` pairs in
    document order, and `.resolve(value)` follows them to the `NumberFormat`
    that actually applies to a given value (itself, if none match).

    Writable: `NumberFormat.create(reader, family, ...)` builds a brand new
    format from scratch (for when no existing one in the document already
    has the layout you want); `.add_condition(condition, target)` appends
    a conditional variant to an existing format."""

    _FAMILY_TAGS = {
        "number": "number:number-style",
        "percentage": "number:percentage-style",
        "currency": "number:currency-style",
        "date": "number:date-style",
        "time": "number:time-style",
        "boolean": "number:boolean-style",
    }

    def __init__(self, tag: Tag, reader: "ODSReader | None" = None, _seen: "set[str | None] | None" = None) -> None:
        self._tag = tag
        self._reader = reader
        self.name = cast("str | None", tag.get("style:name"))
        self.family = tag.name[: -len("-style")] if tag.name.endswith("-style") else tag.name
        number_tag = tag.find("number:number") or tag.find("number:boolean")
        self.decimal_places = None
        self.grouping = None
        if number_tag is not None:
            places = number_tag.attrs.get("number:decimal-places")
            self.decimal_places = int(places) if places is not None else None
            self.grouping = number_tag.attrs.get("number:grouping") == "true"
        symbol = tag.find("number:currency-symbol")
        self.currency_symbol = symbol.get_text() if symbol is not None else None
        text_props = tag.find("style:text-properties")
        self.font_color = text_props.attrs.get("fo:color") if text_props is not None else None
        # for date/time styles: the ordered sequence of components/literal
        # text making up the layout, e.g. [("day", "long"), ("text", "/"), ...]
        self.components = None
        if self.family in ("date", "time"):
            self.components = [
                (
                    child.name,
                    child.get_text() if child.name == "text" else child.attrs.get("number:style", "short"),
                )
                for child in tag.find_all(True, recursive=False)
                if child.name in _DATE_TIME_COMPONENT_NAMES
            ]

        _seen = _seen or set()
        _seen.add(self.name)
        self.conditions = []
        for style_map in tag.find_all("style:map", recursive=False):
            condition = style_map.attrs.get("style:condition")
            apply_name = style_map.attrs.get("style:apply-style-name")
            target = None
            if reader is not None and apply_name and apply_name not in _seen:
                target_tag = reader._find_number_style(apply_name)
                if target_tag is not None:
                    target = NumberFormat(target_tag, reader=reader, _seen=_seen)
            self.conditions.append((condition, target))

    def resolve(self, value: object) -> "NumberFormat":
        """The `NumberFormat` that actually applies to `value`, following
        `.conditions` in order (first match wins) - `self` if none match, a
        condition's target couldn't be resolved, or there are no conditions."""
        for condition, target in self.conditions:
            if target is not None and _evaluate_number_format_condition(condition, value):
                return target
        return self

    @classmethod
    def create(
        cls,
        reader: "ODSReader",
        family: str,
        decimal_places: "int | None" = None,
        grouping: bool = False,
        min_integer_digits: int = 1,
        currency_symbol: "str | None" = None,
        components: "list[tuple[str, str]] | None" = None,
        font_color: "str | None" = None,
    ) -> "NumberFormat":
        """Build a brand new `<number:*-style>` from scratch and register
        it in the document's automatic styles, returning a `NumberFormat`
        bound to it.

        `family` is one of `"number"`/`"percentage"`/`"currency"`/
        `"date"`/`"time"`/`"boolean"`. `decimal_places`/`grouping`/
        `min_integer_digits` apply to `"number"`/`"percentage"`/
        `"currency"`; `currency_symbol` is required for `"currency"`.
        `"date"`/`"time"` take `components` - the same ordered
        `[(component, style_or_text), ...]` list `.components` itself
        already exposes on read, e.g. `[("day", "long"), ("text", "/"),
        ("month", "long"), ("text", "/"), ("year", "long")]` (a `"text"`
        entry is a literal separator - its second element is the literal
        text itself, rather than a component style). `"boolean"` takes
        none of the above. `font_color` works for any family - the
        classic use is the "positive/negative" pair behind conditional
        formatting (see `add_condition`): a plain black format for the
        base, and a `font_color="#FF0000"` variant applied when
        `"value()<0"` matches.
        """
        if family not in cls._FAMILY_TAGS:
            raise ValueError(
                f"unknown number format family {family!r} - expected one of {sorted(cls._FAMILY_TAGS)}"
            )
        if family == "currency" and not currency_symbol:
            raise ValueError("family='currency' requires currency_symbol")
        if family in ("date", "time") and not components:
            raise ValueError(f"family={family!r} requires components")

        tag = _blank_template(reader.data, cls._FAMILY_TAGS[family])
        tag.attrs["style:name"] = reader._new_style_name("N")
        if font_color is not None:
            text_props = _blank_template(reader.data, "style:text-properties")
            text_props.attrs["fo:color"] = font_color
            tag.append(text_props)

        if family in ("number", "percentage", "currency"):
            number_child = _blank_template(reader.data, "number:number")
            if decimal_places is not None:
                number_child.attrs["number:decimal-places"] = str(decimal_places)
                number_child.attrs["number:min-decimal-places"] = str(decimal_places)
            number_child.attrs["number:min-integer-digits"] = str(min_integer_digits)
            if grouping:
                number_child.attrs["number:grouping"] = "true"
            tag.append(number_child)
            if family == "percentage":
                text_child = _blank_template(reader.data, "number:text")
                text_child.string = " %"
                tag.append(text_child)
            elif family == "currency":
                assert currency_symbol is not None  # validated above
                separator = _blank_template(reader.data, "number:text")
                separator.string = " "
                tag.append(separator)
                symbol_child = _blank_template(reader.data, "number:currency-symbol")
                symbol_child.string = currency_symbol
                tag.append(symbol_child)
        elif family == "boolean":
            tag.append(_blank_template(reader.data, "number:boolean"))
        else:  # date / time
            for kind, value in components:
                if kind == "text":
                    child = _blank_template(reader.data, "number:text")
                    child.string = value
                else:
                    child = _blank_template(reader.data, f"number:{kind}")
                    if value:
                        child.attrs["number:style"] = value
                tag.append(child)

        reader._automatic_styles().append(tag)
        return cls(tag, reader=reader)

    def add_condition(self, condition: str, target: "NumberFormat") -> None:
        """Add a `<style:map>` entry (see the class docstring): `target` -
        an existing `NumberFormat` in the same document - applies instead
        of this one whenever `condition` (ODF's `style:condition` syntax,
        e.g. `"value()<0"`) matches. Appended after any conditions already
        there; `.resolve(value)` tries them in order, first match wins."""
        if self._tag is None or self._reader is None:
            raise RuntimeError(f"number format {self.name!r} has no underlying tag and cannot be written to")
        if not isinstance(target, NumberFormat):
            raise TypeError(f"target must be a NumberFormat, got {type(target)!r}")
        style_map = _blank_template(self._reader.data, "style:map")
        style_map.attrs["style:condition"] = condition
        style_map.attrs["style:apply-style-name"] = target.name
        self._tag.append(style_map)
        self.conditions.append((condition, target))

    def __repr__(self) -> str:
        return f"NumberFormat(name={self.name!r}, family={self.family!r})"


_DATE_TIME_RENDER_LONG = {
    "day": lambda v: f"{v.day:02d}",
    "month": lambda v: f"{v.month:02d}",
    "year": lambda v: f"{v.year:04d}",
    "hours": lambda v: f"{v.hour:02d}",
    "minutes": lambda v: f"{v.minute:02d}",
    "seconds": lambda v: f"{v.second:02d}",
}
_DATE_TIME_RENDER_SHORT = {
    "day": lambda v: str(v.day),
    "month": lambda v: str(v.month),
    "year": lambda v: str(v.year % 100),
    "hours": lambda v: str(v.hour),
    "minutes": lambda v: str(v.minute),
    "seconds": lambda v: str(v.second),
}


def _render_number_from_format(number_format: "NumberFormat | None", value: float) -> "str | None":
    """Render `value` per `number_format`'s own real definition - decimal
    places, thousands grouping, currency symbol - a genuine read of the
    document's ODF format rather than a guess from another cell's
    example (see `Cell._infer_number_display`). `None` if `number_format`
    isn't a `"number"`/`"percentage"`/`"currency"` family.

    Renders with a plain `.`/`,` (decimal/grouping) convention - the
    document's actual locale isn't captured by `NumberFormat` - so this
    is only ever tried as a fallback once no example cell is available to
    learn the real locale-specific separators from."""
    if number_format is None or number_format.family not in ("number", "percentage", "currency"):
        return None
    scale = 100 if number_format.family == "percentage" else 1
    decimals = number_format.decimal_places or 0
    rendered = f"{value * scale:,.{decimals}f}" if number_format.grouping else f"{value * scale:.{decimals}f}"
    if number_format.family == "percentage":
        return f"{rendered} %"
    if number_format.family == "currency" and number_format.currency_symbol:
        return f"{rendered} {number_format.currency_symbol}"
    return rendered


def _render_date_time_from_format(
    number_format: "NumberFormat | None", value: "dt.date | dt.time", family: str
) -> "str | None":
    """Render `value` (a `datetime.date` or `datetime.time`) by walking
    `number_format.components` - the same ordered layout `.components`
    itself exposes on read. `None` if `number_format` isn't a `family`
    format, has no `.components`, or uses a component this doesn't know
    how to render (`day-of-week`/`week-of-year`/`quarter`/`era`) - safer
    to fall back than to silently drop part of the layout."""
    if number_format is None or number_format.family != family or not number_format.components:
        return None
    parts = []
    for kind, style in number_format.components:
        if kind == "text":
            parts.append(style)
        elif kind == "am-pm":
            parts.append("PM" if value.hour >= 12 else "AM")
        elif kind in _DATE_TIME_RENDER_LONG:
            renderer = _DATE_TIME_RENDER_LONG if style == "long" else _DATE_TIME_RENDER_SHORT
            parts.append(renderer[kind](value))
        else:
            return None
    return "".join(parts)


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


class CellStyle:
    """Resolved visual formatting and number format behind a cell's
    `table:style-name`. Walks the `style:parent-style-name` inheritance
    chain on read (the nearest style to the cell wins for whichever
    property it sets directly; a property no style in the chain sets is
    `None`). Look up a cell's style via `Cell.style`, not directly.

    Writable: setting a property (e.g. `cell.style.bold = True`) forks the
    cell its own private automatic style on first use (see
    `Cell._ensure_own_style`) - the cell's *own* value going forward, on
    top of whatever it already inherited. `border_top`/`border_bottom`/
    `border_left`/`border_right` accept a `Border`, a raw ODF shorthand
    string (`"0.5pt solid #000000"`), or `None` (explicitly no border on
    that side) - setting any one side re-writes all four explicitly on the
    forked style, carrying the other three over from what's currently
    resolved (see `CellStyle` border resolution below), so they don't
    silently fall back to unstyled. `diagonal_bl_tr`/`diagonal_tl_br` take
    the same three forms, but `None` simply removes the override (falls
    back to whatever's inherited) - pass the literal string `"none"` to
    force no diagonal regardless of inheritance.

    Not yet writable: `.conditions` (conditional formatting via
    `style:map`) and creating a brand new number format from scratch -
    `number_format` can only be *assigned* an existing `NumberFormat` (or
    its style name) already present in the document."""

    _BORDER_ATTRS = ("fo:border", "fo:border-top", "fo:border-bottom", "fo:border-left", "fo:border-right")
    _BORDER_SIDE_ATTRS = {
        "border_top": "fo:border-top",
        "border_bottom": "fo:border-bottom",
        "border_left": "fo:border-left",
        "border_right": "fo:border-right",
    }

    def __init__(
        self,
        reader: "ODSReader",
        name: "str | None",
        value: object = None,
        cell: "Cell | None" = None,
    ) -> None:
        self.name = name
        self._reader = reader
        self._cell = cell
        self._value = value
        chain = []
        seen = set()
        current = reader._find_style(name, family="table-cell")
        while current is not None and current.get("style:name") not in seen:
            seen.add(current.get("style:name"))
            chain.append(current)
            parent = cast("str | None", current.get("style:parent-style-name"))
            current = reader._find_style(parent, family="table-cell") if parent else None

        def prop(tag_name: str, attr: str) -> "str | None":
            for style in chain:
                child = style.find(tag_name)
                if child is not None and attr in child.attrs:
                    return child.attrs[attr]
            return None

        self._bold = prop("style:text-properties", "fo:font-weight") == "bold"
        self._italic = prop("style:text-properties", "fo:font-style") == "italic"
        self._underline = prop("style:text-properties", "style:text-underline-style") not in (None, "none")
        self._strikethrough = prop("style:text-properties", "style:text-line-through-style") not in (
            None,
            "none",
        )
        self._font_family = prop("style:text-properties", "style:font-name")
        self._font_color = prop("style:text-properties", "fo:color")
        self._font_size = prop("style:text-properties", "fo:font-size")
        self._background_color = prop("style:table-cell-properties", "fo:background-color")
        self._vertical_align = prop("style:table-cell-properties", "style:vertical-align")
        self._horizontal_align = prop("style:paragraph-properties", "fo:text-align")
        rotation = prop("style:table-cell-properties", "style:rotation-angle")
        self._rotation = int(rotation) if rotation is not None else None
        self._writing_mode = prop("style:table-cell-properties", "style:writing-mode")
        self._wrap_text = prop("style:table-cell-properties", "fo:wrap-option") == "wrap"
        self._shrink_to_fit = prop("style:table-cell-properties", "style:shrink-to-fit") == "true"
        self._protection = prop("style:table-cell-properties", "style:cell-protect")
        self._text_position = prop("style:text-properties", "style:text-position")
        self._diagonal_bl_tr = _make_border(prop("style:table-cell-properties", "style:diagonal-bl-tr"))
        self._diagonal_tl_br = _make_border(prop("style:table-cell-properties", "style:diagonal-tl-br"))

        # Borders are resolved as a unit from the nearest style that defines
        # *any* border info (rather than merging per-side across inheritance
        # levels): within that one style, a specific side (fo:border-top...)
        # overrides the fo:border shorthand for that side, exactly as ODF
        # itself resolves it within a single style declaration.
        self._border_top = self._border_bottom = self._border_left = self._border_right = None
        for style in chain:
            cell_props = style.find("style:table-cell-properties")
            if cell_props is None or not any(a in cell_props.attrs for a in self._BORDER_ATTRS):
                continue
            attrs = cell_props.attrs
            shorthand = attrs.get("fo:border")
            self._border_top = _make_border(attrs.get("fo:border-top", shorthand))
            self._border_bottom = _make_border(attrs.get("fo:border-bottom", shorthand))
            self._border_left = _make_border(attrs.get("fo:border-left", shorthand))
            self._border_right = _make_border(attrs.get("fo:border-right", shorthand))
            break

        # raw, flattened property dicts as an escape hatch for anything not
        # surfaced above - base style first, so a nearer style overrides it
        self.cell_properties: Dict[str, str] = {}
        self.text_properties: Dict[str, str] = {}
        for style in reversed(chain):
            cell_props = style.find("style:table-cell-properties")
            if cell_props is not None:
                self.cell_properties.update(cell_props.attrs)
            text_props = style.find("style:text-properties")
            if text_props is not None:
                self.text_properties.update(text_props.attrs)

        data_style_name = cast(
            "str | None",
            next((s.get("style:data-style-name") for s in chain if s.get("style:data-style-name")), None),
        )
        self._number_format = None
        if data_style_name:
            number_tag = reader._find_number_style(data_style_name)
            if number_tag is not None:
                base_format = NumberFormat(number_tag, reader=reader)
                self._number_format = base_format.resolve(value) if value is not None else base_format

    def _require_cell(self) -> None:
        if self._cell is None:
            raise RuntimeError(f"style {self.name!r} has no owning Cell and cannot be written to")

    def _write_attr(self, props_tag_name: str, attr: str, raw_value: "str | None") -> None:
        """Set (or, if `raw_value` is `None`, remove) one attribute on this
        cell's own automatic style."""
        self._require_cell()
        tag = self._cell._ensure_own_style()
        if raw_value is None:
            child = tag.find(props_tag_name)
            if child is not None:
                child.attrs.pop(attr, None)
            return
        _ensure_style_child(tag, props_tag_name).attrs[attr] = raw_value

    def _write_border_sides(self, updates: "dict[str, Border | str | None]") -> None:
        """Set 1+ of the 4 border sides, carrying the other (currently
        resolved) sides along explicitly - see the class docstring."""
        self._require_cell()
        tag = self._cell._ensure_own_style()
        values: "dict[str, Border | str | None]" = {
            "border_top": self._border_top,
            "border_bottom": self._border_bottom,
            "border_left": self._border_left,
            "border_right": self._border_right,
        }
        values.update(updates)
        props = _ensure_style_child(tag, "style:table-cell-properties")
        props.attrs.pop("fo:border", None)
        for key, value in values.items():
            raw = _border_to_raw(value)
            props.attrs[self._BORDER_SIDE_ATTRS[key]] = raw if raw is not None else "none"
            setattr(self, f"_{key}", _make_border(raw))

    @property
    def bold(self) -> bool:
        return self._bold

    @bold.setter
    def bold(self, value: bool) -> None:
        self._write_attr("style:text-properties", "fo:font-weight", "bold" if value else "normal")
        self._bold = bool(value)

    @property
    def italic(self) -> bool:
        return self._italic

    @italic.setter
    def italic(self, value: bool) -> None:
        self._write_attr("style:text-properties", "fo:font-style", "italic" if value else "normal")
        self._italic = bool(value)

    @property
    def underline(self) -> bool:
        return self._underline

    @underline.setter
    def underline(self, value: bool) -> None:
        self._require_cell()
        tag = self._cell._ensure_own_style()
        props = _ensure_style_child(tag, "style:text-properties")
        if value:
            props.attrs["style:text-underline-style"] = "solid"
            props.attrs["style:text-underline-width"] = "auto"
            props.attrs["style:text-underline-color"] = "font-color"
        else:
            props.attrs["style:text-underline-style"] = "none"
            props.attrs.pop("style:text-underline-width", None)
            props.attrs.pop("style:text-underline-color", None)
        self._underline = bool(value)

    @property
    def strikethrough(self) -> bool:
        return self._strikethrough

    @strikethrough.setter
    def strikethrough(self, value: bool) -> None:
        self._write_attr("style:text-properties", "style:text-line-through-style", "solid" if value else "none")
        self._strikethrough = bool(value)

    @property
    def font_family(self) -> "str | None":
        return self._font_family

    @font_family.setter
    def font_family(self, value: "str | None") -> None:
        self._write_attr("style:text-properties", "style:font-name", value)
        self._font_family = value

    @property
    def font_color(self) -> "str | None":
        return self._font_color

    @font_color.setter
    def font_color(self, value: "str | None") -> None:
        self._write_attr("style:text-properties", "fo:color", value)
        self._font_color = value

    @property
    def font_size(self) -> "str | None":
        return self._font_size

    @font_size.setter
    def font_size(self, value: "str | None") -> None:
        self._write_attr("style:text-properties", "fo:font-size", value)
        self._font_size = value

    @property
    def background_color(self) -> "str | None":
        return self._background_color

    @background_color.setter
    def background_color(self, value: "str | None") -> None:
        self._write_attr("style:table-cell-properties", "fo:background-color", value)
        self._background_color = value

    @property
    def vertical_align(self) -> "str | None":
        return self._vertical_align

    @vertical_align.setter
    def vertical_align(self, value: "str | None") -> None:
        self._write_attr("style:table-cell-properties", "style:vertical-align", value)
        self._vertical_align = value

    @property
    def horizontal_align(self) -> "str | None":
        return self._horizontal_align

    @horizontal_align.setter
    def horizontal_align(self, value: "str | None") -> None:
        self._write_attr("style:paragraph-properties", "fo:text-align", value)
        self._horizontal_align = value

    @property
    def rotation(self) -> "int | None":
        return self._rotation

    @rotation.setter
    def rotation(self, value: "int | None") -> None:
        self._write_attr(
            "style:table-cell-properties", "style:rotation-angle", None if value is None else str(int(value))
        )
        self._rotation = None if value is None else int(value)

    @property
    def writing_mode(self) -> "str | None":
        return self._writing_mode

    @writing_mode.setter
    def writing_mode(self, value: "str | None") -> None:
        self._write_attr("style:table-cell-properties", "style:writing-mode", value)
        self._writing_mode = value

    @property
    def wrap_text(self) -> bool:
        return self._wrap_text

    @wrap_text.setter
    def wrap_text(self, value: bool) -> None:
        self._write_attr("style:table-cell-properties", "fo:wrap-option", "wrap" if value else "no-wrap")
        self._wrap_text = bool(value)

    @property
    def shrink_to_fit(self) -> bool:
        return self._shrink_to_fit

    @shrink_to_fit.setter
    def shrink_to_fit(self, value: bool) -> None:
        self._write_attr("style:table-cell-properties", "style:shrink-to-fit", "true" if value else "false")
        self._shrink_to_fit = bool(value)

    @property
    def protection(self) -> "str | None":
        return self._protection

    @protection.setter
    def protection(self, value: "str | None") -> None:
        self._write_attr("style:table-cell-properties", "style:cell-protect", value)
        self._protection = value

    @property
    def text_position(self) -> "str | None":
        return self._text_position

    @text_position.setter
    def text_position(self, value: "str | None") -> None:
        self._write_attr("style:text-properties", "style:text-position", value)
        self._text_position = value

    @property
    def superscript(self) -> bool:
        return (self._text_position or "").startswith("super")

    @superscript.setter
    def superscript(self, value: bool) -> None:
        self.text_position = "super 58%" if value else None

    @property
    def subscript(self) -> bool:
        return (self._text_position or "").startswith("sub")

    @subscript.setter
    def subscript(self, value: bool) -> None:
        self.text_position = "sub 58%" if value else None

    @property
    def diagonal_bl_tr(self) -> "Border | None":
        return self._diagonal_bl_tr

    @diagonal_bl_tr.setter
    def diagonal_bl_tr(self, value: "Border | str | None") -> None:
        raw = _border_to_raw(value)
        self._write_attr("style:table-cell-properties", "style:diagonal-bl-tr", raw)
        self._diagonal_bl_tr = _make_border(raw)

    @property
    def diagonal_tl_br(self) -> "Border | None":
        return self._diagonal_tl_br

    @diagonal_tl_br.setter
    def diagonal_tl_br(self, value: "Border | str | None") -> None:
        raw = _border_to_raw(value)
        self._write_attr("style:table-cell-properties", "style:diagonal-tl-br", raw)
        self._diagonal_tl_br = _make_border(raw)

    @property
    def border_top(self) -> "Border | None":
        return self._border_top

    @border_top.setter
    def border_top(self, value: "Border | str | None") -> None:
        self._write_border_sides({"border_top": value})

    @property
    def border_bottom(self) -> "Border | None":
        return self._border_bottom

    @border_bottom.setter
    def border_bottom(self, value: "Border | str | None") -> None:
        self._write_border_sides({"border_bottom": value})

    @property
    def border_left(self) -> "Border | None":
        return self._border_left

    @border_left.setter
    def border_left(self, value: "Border | str | None") -> None:
        self._write_border_sides({"border_left": value})

    @property
    def border_right(self) -> "Border | None":
        return self._border_right

    @border_right.setter
    def border_right(self, value: "Border | str | None") -> None:
        self._write_border_sides({"border_right": value})

    @property
    def number_format(self) -> "NumberFormat | None":
        return self._number_format

    @number_format.setter
    def number_format(self, fmt: "NumberFormat | str | None") -> None:
        self._require_cell()
        tag = self._cell._ensure_own_style()
        if fmt is None:
            tag.attrs.pop("style:data-style-name", None)
            self._number_format = None
            return
        if isinstance(fmt, NumberFormat):
            name = fmt.name
        elif isinstance(fmt, str):
            name = fmt
        else:
            raise TypeError(f"number_format must be a NumberFormat, str, or None, got {type(fmt)!r}")
        number_tag = self._cell.sheet.reader._find_number_style(name)
        if number_tag is None:
            raise ValueError(f"no number format named {name!r} exists in this document")
        tag.attrs["style:data-style-name"] = name
        self._number_format = NumberFormat(number_tag, reader=self._cell.sheet.reader)

    def __repr__(self) -> str:
        return (
            f"CellStyle(name={self.name!r}, bold={self.bold}, italic={self.italic}, "
            f"background_color={self.background_color!r})"
        )


class RowStyle:
    """Resolved `<style:style style:family="table-row">` behind a row's
    `table:style-name` - no inheritance chain (rows don't meaningfully use
    `style:parent-style-name` in practice). Look up via
    `Sheet.row_style(row)`, not directly.

    Writable: setting `.height`/`.optimal_height`/`.visible` forks the row
    its own private automatic style on first use (see
    `Sheet._ensure_row_style`), carrying over whatever the row's current
    style already had (all 3 properties come from one single style tag,
    not a chain, so nothing here is resolved independently)."""

    def __init__(self, tag: "Tag | None", sheet: "Sheet | None" = None, row: "int | None" = None) -> None:
        self._sheet = sheet
        self._row = row
        self.name = tag.get("style:name") if tag is not None else None
        props = tag.find("style:table-row-properties") if tag is not None else None
        self._height = props.attrs.get("style:row-height") if props is not None else None
        self._optimal_height = (
            props is not None and props.attrs.get("style:use-optimal-row-height") == "true"
        )
        self._visible = props is None or props.attrs.get("table:visibility", "visible") == "visible"

    def _require_owner(self) -> None:
        if self._sheet is None or self._row is None:
            raise RuntimeError(f"style {self.name!r} has no owning Sheet/row and cannot be written to")

    @property
    def height(self) -> "str | None":
        return self._height

    @height.setter
    def height(self, value: "str | None") -> None:
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_row_style(cast(int, self._row)), "style:table-row-properties")
        if value is None:
            props.attrs.pop("style:row-height", None)
        else:
            props.attrs["style:row-height"] = value
        props.attrs["style:use-optimal-row-height"] = "false"
        self._height = value
        self._optimal_height = False

    @property
    def optimal_height(self) -> bool:
        return self._optimal_height

    @optimal_height.setter
    def optimal_height(self, value: bool) -> None:
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_row_style(cast(int, self._row)), "style:table-row-properties")
        props.attrs["style:use-optimal-row-height"] = "true" if value else "false"
        self._optimal_height = bool(value)

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_row_style(cast(int, self._row)), "style:table-row-properties")
        props.attrs["table:visibility"] = "visible" if value else "collapse"
        self._visible = bool(value)

    def __repr__(self) -> str:
        return f"RowStyle(name={self.name!r}, height={self.height!r})"


class ColumnStyle:
    """Resolved `<style:style style:family="table-column">` behind a
    column's `table:style-name` - no inheritance chain. Look up via
    `Sheet.column_style(col)`, not directly.

    Writable: setting `.width`/`.visible` forks the column its own private
    automatic style on first use (see `Sheet._ensure_column_style`),
    carrying over whatever the column's current style already had."""

    def __init__(self, tag: "Tag | None", sheet: "Sheet | None" = None, col: "int | None" = None) -> None:
        self._sheet = sheet
        self._col = col
        self.name = tag.get("style:name") if tag is not None else None
        props = tag.find("style:table-column-properties") if tag is not None else None
        self._width = props.attrs.get("style:column-width") if props is not None else None
        self._visible = props is None or props.attrs.get("table:visibility", "visible") == "visible"

    def _require_owner(self) -> None:
        if self._sheet is None or self._col is None:
            raise RuntimeError(f"style {self.name!r} has no owning Sheet/column and cannot be written to")

    @property
    def width(self) -> "str | None":
        return self._width

    @width.setter
    def width(self, value: "str | None") -> None:
        self._require_owner()
        props = _ensure_style_child(
            self._sheet._ensure_column_style(cast(int, self._col)), "style:table-column-properties"
        )
        if value is None:
            props.attrs.pop("style:column-width", None)
        else:
            props.attrs["style:column-width"] = value
        self._width = value

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._require_owner()
        props = _ensure_style_child(
            self._sheet._ensure_column_style(cast(int, self._col)), "style:table-column-properties"
        )
        props.attrs["table:visibility"] = "visible" if value else "collapse"
        self._visible = bool(value)

    def __repr__(self) -> str:
        return f"ColumnStyle(name={self.name!r}, width={self.width!r})"


class TableStyle:
    """Resolved `<style:style style:family="table">` behind a sheet's
    `table:style-name` - no inheritance chain. Look up via `Sheet.style`,
    not directly.

    Writable: setting `.tab_color`/`.visible` forks the sheet its own
    private automatic style on first use (see
    `Sheet._ensure_table_style`), carrying over whatever the sheet's
    current style already had."""

    def __init__(self, tag: "Tag | None", sheet: "Sheet | None" = None) -> None:
        self._sheet = sheet
        self.name = tag.get("style:name") if tag is not None else None
        props = tag.find("style:table-properties") if tag is not None else None
        self._tab_color = props.attrs.get("table:tab-color") if props is not None else None
        self._visible = props is None or props.attrs.get("table:display", "true") != "false"

    def _require_owner(self) -> None:
        if self._sheet is None:
            raise RuntimeError(f"style {self.name!r} has no owning Sheet and cannot be written to")

    @property
    def tab_color(self) -> "str | None":
        return self._tab_color

    @tab_color.setter
    def tab_color(self, value: "str | None") -> None:
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_table_style(), "style:table-properties")
        if value is None:
            props.attrs.pop("table:tab-color", None)
        else:
            props.attrs["table:tab-color"] = value
        self._tab_color = value

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_table_style(), "style:table-properties")
        props.attrs["table:display"] = "true" if value else "false"
        self._visible = bool(value)

    def __repr__(self) -> str:
        return f"TableStyle(name={self.name!r}, tab_color={self.tab_color!r})"
