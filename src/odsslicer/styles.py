# mypy: disable-error-code="union-attr"
# (`Element | None` unions are narrowed dynamically all over this module,
# guarded by runtime checks mypy can't see through - silencing that one error
# class here beats dozens of value-free asserts/casts. Every other error
# class, and all signatures, remain fully checked.)
"""Resolved ODF styles: NumberFormat (incl. creation and conditional formats),
Border, CellStyle, RowStyle, ColumnStyle, TableStyle."""

import datetime as dt
import re
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Protocol, TypeVar, cast, overload

from lxml import etree

from odsslicer.dateformats import _STANDARD_FORMATS
from odsslicer.datenames import _date_names
from odsslicer.datetimes import _ONE_DAY, _as_datetime, _as_timedelta, _clock_parts
from odsslicer.xmltree import attributes, child, elements, insert_before, new, qn, set_text, text, unset
from odsslicer.xmlutils import _ensure_style_child

if TYPE_CHECKING:
    from odsslicer.cell import Cell
    from odsslicer.reader import ODSReader
    from odsslicer.sheet import Sheet


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
    "day",
    "month",
    "year",
    "day-of-week",
    "week-of-year",
    "quarter",
    "era",
    "hours",
    "minutes",
    "seconds",
    "am-pm",
    "text",
)
_TIME_OF_DAY_COMPONENT_NAMES = frozenset({"hours", "minutes", "seconds", "am-pm"})
# A number format's family by its element: "number:date-style" -> "date"
_NUMBER_STYLE_FAMILIES = {qn(name): name[len("number:") : -len("-style")] for name in _NUMBER_STYLE_TAGS}
# The parts a number format is laid out in, by element, named by their local
# name: the elements of the number namespace that hold a component, the number
# or the currency symbol, and LibreOffice's `<loext:text>`. LibreOffice writes
# that one in a `number:number-style`, next to a fill character (307 of the
# author's 2,112 real files, 27 of LibreOffice's own test files:
# benchmarks/namespace_scan.py), and shows it as `<number:text>`, literal text
# the displayed text inferred on write has to keep. Until 1.0, parts were
# compared by local name, which took an element of any namespace for one;
# compared by URI, as every element is elsewhere in the package, these alone
# are.
_FORMAT_PARTS = {
    **{qn(f"number:{kind}"): kind for kind in (*_DATE_TIME_COMPONENT_NAMES, "number", "currency-symbol")},
    qn("loext:text"): "text",
}


def _part(element: etree._Element) -> "str | None":
    """Which part of its number format `element` is - "text", "number",
    "day"... (see `_FORMAT_PARTS`) - `None` for an element that is none,
    `style:map` or `style:text-properties` say."""
    return _FORMAT_PARTS.get(cast(str, element.tag))


_STYLE_NAME = qn("style:name")

# The text around the sign of LibreOffice's standard percentage format, by
# locale, where it is not a bare "%" after the number: a space in French, a
# no-break space in German, the sign first in Turkish (issue #17). Generated
# from LibreOffice 25.8 - `getStandardFormat(PERCENT)` for the locales of
# `dateformats` - as `(before the number, after it)`.
_PERCENT_AFFIXES: "dict[str, tuple[str, str]]" = {
    "cs-CZ": ("", " %"),
    "da-DK": ("", "\xa0%"),
    "de-AT": ("", "\xa0%"),
    "de-DE": ("", "\xa0%"),
    "de-LU": ("", "\xa0%"),
    "es-AR": ("", "\xa0%"),
    "es-CL": ("", "\xa0%"),
    "es-ES": ("", "\xa0%"),
    "es-PE": ("", "\xa0%"),
    "es-US": ("", "\xa0%"),
    "eu-ES": ("%", ""),
    "fi-FI": ("", " %"),
    "fr-BE": ("", "\xa0%"),
    "fr-CA": ("", " %"),
    "fr-FR": ("", " %"),
    "fr-LU": ("", " %"),
    "is-IS": ("", "\xa0%"),
    "lt-LT": ("", "\xa0%"),
    "ro-RO": ("", "\xa0%"),
    "sk-SK": ("", " %"),
    "sl-SI": ("", "\xa0%"),
    "sv-FI": ("", " %"),
    "sv-SE": ("", " %"),
    "tr-TR": ("%", ""),
}


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

    _FAMILY_TAGS: ClassVar[dict[str, str]] = {
        "number": "number:number-style",
        "percentage": "number:percentage-style",
        "currency": "number:currency-style",
        "date": "number:date-style",
        "time": "number:time-style",
        "boolean": "number:boolean-style",
    }

    def __init__(
        self, tag: etree._Element, reader: "ODSReader | None" = None, _seen: "set[str | None] | None" = None
    ) -> None:
        self._tag = tag
        self._reader = reader
        self.name = tag.get(qn("style:name"))
        self.family = _NUMBER_STYLE_FAMILIES[cast(str, tag.tag)]
        # `is None`, not `or`: an element with no children is false in lxml
        number_tag = child(tag, "number:number")
        if number_tag is None:
            number_tag = child(tag, "number:boolean")
        self.decimal_places = None
        self.grouping = None
        if number_tag is not None:
            places = number_tag.get(qn("number:decimal-places"))
            self.decimal_places = int(places) if places is not None else None
            self.grouping = number_tag.get(qn("number:grouping")) == "true"
        symbol = child(tag, "number:currency-symbol")
        self.currency_symbol = text(symbol) if symbol is not None else None
        text_props = child(tag, "style:text-properties")
        self.font_color = text_props.get(qn("fo:color")) if text_props is not None else None
        # for date/time styles: the ordered sequence of components/literal
        # text making up the layout, e.g. [("day", "long"), ("text", "/"), ...]
        self.components = None
        # a time counted in full, [HH]:MM, rather than around the clock
        self.elapsed = tag.get(qn("number:truncate-on-overflow")) == "false"
        if self.family in ("date", "time"):
            self.components = [
                (
                    kind,
                    text(component) if kind == "text" else component.get(qn("number:style"), "short"),
                )
                for component in elements(tag)
                if (kind := _part(component)) in _DATE_TIME_COMPONENT_NAMES
            ]

        _seen = _seen or set()
        _seen.add(self.name)
        self.conditions = []
        for style_map in tag.iterchildren(qn("style:map")):
            condition = style_map.get(qn("style:condition"))
            apply_name = style_map.get(qn("style:apply-style-name"))
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
        *,
        grouping: bool = False,
        min_integer_digits: int = 1,
        currency_symbol: "str | None" = None,
        components: "list[tuple[str, str]] | None" = None,
        font_color: "str | None" = None,
        elapsed: bool = False,
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

        `elapsed=True` makes a `"time"` format count the time in full, as
        LibreOffice's `[HH]:MM` does, where it wraps it around the clock: a
        duration of 26 hours shows as 26:00, not 02:00 (issue #17). A
        percentage's sign sits where the document's locale puts it in
        LibreOffice's standard format - `50%` in en-US, `50 %` in fr-FR,
        with a no-break space in de-DE - bare after the number for a locale
        LibreOffice does not know.
        """
        if family not in cls._FAMILY_TAGS:
            raise ValueError(
                f"unknown number format family {family!r} - expected one of {sorted(cls._FAMILY_TAGS)}"
            )
        if family == "currency" and not currency_symbol:
            raise ValueError("family='currency' requires currency_symbol")
        if family in ("date", "time") and not components:
            raise ValueError(f"family={family!r} requires components")
        if elapsed and family != "time":
            raise ValueError(f"elapsed=True applies to family='time', not {family!r}")

        # its name given here, where the element is made: an attribute of
        # another namespace set on a detached element gets a prefix of
        # lxml's own (see `xmltree.new`)
        tag = new(cls._FAMILY_TAGS[family], {"style:name": reader._new_style_name("N")})
        if font_color is not None:
            tag.append(new("style:text-properties", {"fo:color": font_color}))

        if family in ("number", "percentage", "currency"):
            number_child = new("number:number")
            if decimal_places is not None:
                number_child.set(qn("number:decimal-places"), str(decimal_places))
                number_child.set(qn("number:min-decimal-places"), str(decimal_places))
            number_child.set(qn("number:min-integer-digits"), str(min_integer_digits))
            if grouping:
                number_child.set(qn("number:grouping"), "true")
            tag.append(number_child)
            if family == "percentage":
                # a space before the sign always, where en-US has none
                for affix, before in zip(_percent_affixes(reader), (True, False), strict=True):
                    if affix:
                        text_child = new("number:text")
                        set_text(text_child, affix)
                        if before:
                            insert_before(number_child, text_child)
                        else:
                            tag.append(text_child)
            elif family == "currency":
                assert currency_symbol is not None  # validated above
                separator = new("number:text")
                set_text(separator, " ")
                tag.append(separator)
                symbol_child = new("number:currency-symbol")
                set_text(symbol_child, currency_symbol)
                tag.append(symbol_child)
        elif family == "boolean":
            tag.append(new("number:boolean"))
        else:  # date / time
            if elapsed:
                tag.set(qn("number:truncate-on-overflow"), "false")
            for kind, value in components:
                if kind == "text":
                    component = new("number:text")
                    set_text(component, value)
                else:
                    component = new(f"number:{kind}")
                    if value:
                        component.set(qn("number:style"), value)
                tag.append(component)

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
        style_map = new(
            "style:map", {"style:condition": condition, "style:apply-style-name": cast(str, target.name)}
        )
        self._tag.append(style_map)
        # a format a named cell style uses lives in `styles.xml`, which
        # save() copies through unless told something wrote to it
        self._reader._touched_tag(self._tag)
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
    learn the real locale-specific separators from. The rest of the text is
    the format's own, in its order: the text around a percentage's sign or
    a currency's symbol, where a space before either was taken for granted,
    `50 %` for the `50%` of an en-US format (issue #17)."""
    if number_format is None or number_format.family not in ("number", "percentage", "currency"):
        return None
    scale = 100 if number_format.family == "percentage" else 1
    decimals = number_format.decimal_places or 0
    rendered = f"{value * scale:,.{decimals}f}" if number_format.grouping else f"{value * scale:.{decimals}f}"
    parts = []
    for component in elements(number_format._tag):
        if _part(component) == "number":
            parts.append(rendered)
        elif _part(component) in ("text", "currency-symbol"):
            parts.append(text(component))
    return "".join(parts) if rendered in parts else rendered


def _percent_affixes(reader: "ODSReader") -> "tuple[str, str]":
    """The text before and after the number of a percentage, in the
    document's locale - see `_PERCENT_AFFIXES`."""
    language, country = reader._document_locale()
    locale = f"{language}-{country}"
    if locale not in _STANDARD_FORMATS:  # the language's first country's
        locale = next((code for code in _STANDARD_FORMATS if code.startswith(f"{language}-")), "")
    return _PERCENT_AFFIXES.get(locale, ("", "%"))


def _render_date_time_from_format(
    number_format: "NumberFormat | None",
    value: "dt.date | dt.time | dt.timedelta",
    family: str,
) -> "str | None":
    """Render `value` (a `datetime.date` or `datetime.datetime`, a
    `datetime.time` or a `datetime.timedelta`) by walking the elements of
    `number_format` - the ordered layout `.components` exposes on read. `None`
    if `number_format` isn't a `family` format, has no `.components`, or uses
    a component this doesn't know how to render (`week-of-year`/`quarter`/
    `era`) - safer to fall back than to silently drop part of the layout.

    As LibreOffice shows them: a date in a format with a time part reads
    as its midnight, and a duration wraps around the clock in an ordinary
    time format (128:45 shows as 08:45) but counts in full, sign included,
    in an elapsed-time one (`[HH]:MM:SS`, flagged
    `number:truncate-on-overflow="false"`). Days of the week and textual
    months are named in the format's language, the document's if it gives
    none (see `datenames`, issue #16); a language without names gives
    `None`. Seconds show their decimals, in the locale's separator: rounded,
    but never carried into the seconds - LibreOffice shows 59.996 as
    `00:59.99` - except in an elapsed-time format, where the whole duration
    rounds: `[HH]:MM:SS.00` shows it as `00:01:00.00`. Without decimals,
    seconds are cut, never rounded."""
    if number_format is None or number_format.family != family or not number_format.components:
        return None
    tag = number_format._tag
    # `number:format-source="language"`: LibreOffice shows the system's own
    # short date or date-time instead, whatever the elements - on macOS, the
    # one set in its regional settings. Nothing here can render that.
    if tag.get(qn("number:format-source")) == "language":
        return None
    children = [component for component in elements(tag) if _part(component) in _DATE_TIME_COMPONENT_NAMES]
    kinds = {kind for component in children if (kind := _part(component)) is not None}
    named = any(
        _part(component) == "day-of-week"
        or (_part(component) == "month" and component.get(qn("number:textual")) == "true")
        for component in children
    )
    decimals = max(
        (
            int(component.get(qn("number:decimal-places"), "0"))
            for component in children
            if _part(component) == "seconds"
        ),
        default=0,
    )
    names = None
    if named or decimals:
        language = tag.get(qn("number:language"))
        locale = (
            (language, tag.get(qn("number:country")))
            if language
            else number_format._reader._document_locale()
            if number_format._reader is not None
            else (None, None)
        )
        names = _date_names(*locale)
        if named and (names is None or not isinstance(value, dt.date)):
            return None
    sign, fields, fraction = "", cast(Any, value), 0
    if isinstance(value, (dt.time, dt.timedelta)):
        # a time of day too: an elapsed [MM]:SS format shows 02:05:03 as 125:03
        delta = _as_timedelta(value)
        if tag.get(qn("number:truncate-on-overflow")) == "false":
            if decimals:
                delta = _rounded(delta, decimals)
            sign, fields = _elapsed_fields(delta, kinds)
            fraction = abs(delta).microseconds
        else:
            fields = (dt.datetime.min + delta % _ONE_DAY).time()
            fraction = fields.microsecond
    elif isinstance(value, dt.date) and kinds & _TIME_OF_DAY_COMPONENT_NAMES:
        fields = _as_datetime(value)
        fraction = fields.microsecond
    parts = []
    for component in children:
        kind, style = _part(component), component.get(qn("number:style"), "short")
        if kind == "text":
            parts.append(text(component))
        elif kind == "am-pm":
            parts.append("PM" if fields.hour >= 12 else "AM")
        elif kind == "hours" and "am-pm" in kinds:  # 13:45 shows as 01:45 PM
            hour = fields.hour % 12 or 12
            parts.append(f"{hour:02d}" if style == "long" else str(hour))
        elif kind == "day-of-week":
            assert names is not None  # checked above, for a date
            days = names.days if style == "long" else names.short_days
            parts.append(days[(fields.weekday() + 1) % 7])  # Sunday first
        elif kind == "month" and component.get(qn("number:textual")) == "true":
            assert names is not None
            if "day" in kinds:  # declined next to a day, in some languages
                months = names.months_with_day if style == "long" else names.short_months_with_day
            else:
                months = names.months if style == "long" else names.short_months
            parts.append(months[fields.month - 1])
        elif kind in _DATE_TIME_RENDER_LONG:
            renderer = _DATE_TIME_RENDER_LONG if style == "long" else _DATE_TIME_RENDER_SHORT
            parts.append(renderer[kind](fields))
            places = int(component.get(qn("number:decimal-places"), "0")) if kind == "seconds" else 0
            if places:
                separator = names.decimal if names is not None else "."
                parts.append(separator + _fraction_digits(fraction, places))
        else:
            return None
    return sign + "".join(parts)


def _rounded(value: dt.timedelta, places: int) -> dt.timedelta:
    """`value` rounded half up to `places` decimals of a second, carrying
    over into the seconds, minutes and hours."""
    unit = 10 ** (6 - places)
    microseconds = abs(value) // dt.timedelta(microseconds=1)
    rounded = (microseconds + unit // 2) // unit * unit
    return dt.timedelta(microseconds=rounded if value >= dt.timedelta(0) else -rounded)


def _fraction_digits(microseconds: int, places: int) -> str:
    """The `places` digits of a fraction of a second, rounded half up - but
    cut where rounding would carry into the seconds, which LibreOffice never
    does in a clock format: 59.996 shows as 59.99."""
    unit = 10 ** (6 - places)
    digits = (microseconds + unit // 2) // unit
    if digits == 10**places:
        digits = microseconds // unit
    return f"{digits:0{places}d}"


def _elapsed_fields(value: dt.timedelta, kinds: set[str]) -> tuple[str, SimpleNamespace]:
    """The sign of a duration, and its `.hour`/`.minute`/`.second` as an
    elapsed-time format shows them: the leading unit - the hours, else the
    minutes, else the seconds - holds the whole duration, however long."""
    sign, hours, minutes, seconds, _ = _clock_parts(value)
    if "hours" not in kinds:
        hours, minutes = 0, minutes + 60 * hours
        if "minutes" not in kinds:
            minutes, seconds = 0, seconds + 60 * minutes
    return sign, SimpleNamespace(hour=hours, minute=minutes, second=seconds)


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


def _flag(
    element: str, attribute: str, on: str, off: str, doc: str, *, reads_on: "Callable[[str | None], bool]"
) -> "_StyleProperty[bool, bool]":
    """A property that is on or off: `on` or `off` written, `reads_on`
    telling which the attribute's text is."""
    return _StyleProperty(element, attribute, reads_on, lambda value: on if value else off, doc)


def _equals(expected: str) -> "Callable[[str | None], bool]":
    return lambda raw: raw == expected


def _drawn(raw: "str | None") -> bool:
    """Whether a line style - an underline's, a strikethrough's - draws one."""
    return raw not in (None, "none")


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
        current = self._reader._find_style(self.name, family="table-cell")
        while current is not None and current.get(_STYLE_NAME) not in seen:
            seen.add(current.get(_STYLE_NAME))
            yield current
            parent = current.get(qn("style:parent-style-name"))
            current = self._reader._find_style(parent, family="table-cell") if parent else None

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

    bold = _flag(_TEXT, "fo:font-weight", "bold", "normal", "Bold text.", reads_on=_equals("bold"))
    italic = _flag(_TEXT, "fo:font-style", "italic", "normal", "Italic text.", reads_on=_equals("italic"))
    strikethrough = _flag(
        _TEXT,
        "style:text-line-through-style",
        "solid",
        "none",
        "Struck-through text: a solid line written, any line style but `none` read.",
        reads_on=_drawn,
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
    wrap_text = _flag(
        _CELL, "fo:wrap-option", "wrap", "no-wrap", "Text wrapped within the cell.", reads_on=_equals("wrap")
    )
    shrink_to_fit = _flag(
        _CELL,
        "style:shrink-to-fit",
        "true",
        "false",
        "Text shrunk to fit the cell.",
        reads_on=_equals("true"),
    )
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
        tag = self._reader._find_number_style(name)
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
        if self._cell.sheet.reader._find_number_style(name) is None:
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
        _ROW,
        "style:use-optimal-row-height",
        "true",
        "false",
        "The row's height follows its content.",
        reads_on=_equals("true"),
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
