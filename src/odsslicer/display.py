"""The text a cell shows for a value written to it.

ODF stores a cell's value and, beside it, the text a spreadsheet displayed
for it (`<text:p>`): a reader that does not compute formats - most of them,
odsslicer included - shows that text. Writing a value means writing that
text too, and nothing in the library can compute it as LibreOffice does,
locale and all. Two ways get close, tried in this order for each type:

- **by example**: another cell of the document of the same type - this
  cell's own former value first - shows its value in a layout whose pattern
  can be checked against it (decimal separator, decimal count, the text
  around a percentage, a date pattern), and the new value is shown the same
  way (`_DisplayText`);
- **by format**: the cell's own number format, read element by element
  (`_render_number_from_format`, `_render_date_time_from_format`) - exact
  for a date or a time, approximate for a number, whose separators the
  format does not say.

Until 1.0's audit, `Cell` held the first as eleven methods, some 220 lines,
and `styles` the second; both answer one question, which no other part of
a cell asks. `_DisplayText` takes the cell for its value before the write
(the first example), its element (where the search for others starts) and
its number format; the renderers take a `NumberFormat` only.

Nothing here is public (`__all__` is empty): what it writes is read back as
`Cell.text`.
"""

import datetime as dt
import itertools
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from lxml import etree

from odsslicer.columns import _column_definitions
from odsslicer.datenames import _date_names, _DateNames
from odsslicer.datetimes import (
    _ONE_DAY,
    _as_datetime,
    _as_timedelta,
    _clock_parts,
    _duration_text,
    _parse_date_value,
    _parse_time_value,
)
from odsslicer.numberformat import _DATE_TIME_COMPONENT_NAMES, _TIME_OF_DAY_COMPONENT_NAMES, _part
from odsslicer.xmltree import elements, following, preceding, qn, text

if TYPE_CHECKING:
    from odsslicer.cell import Cell
    from odsslicer.numberformat import NumberFormat
    from odsslicer.reader import ODSReader

__all__: list[str] = []

_VALUE_TYPE = qn("office:value-type")
_STYLE_NAME = qn("table:style-name")
_COLUMNS_REPEATED = qn("table:number-columns-repeated")
_DEFAULT_CELL_STYLE_NAME = qn("table:default-cell-style-name")
_TEXT_P = qn("text:p")
_CELL_TAGS = (qn("table:table-cell"), qn("table:covered-table-cell"))

_DATE_PATTERNS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d-%m-%Y",
)
_TIME_PATTERNS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")
# a date-time shows as a date pattern alone when its format has no time part
_DATE_TIME_PATTERNS = _DATE_PATTERNS + tuple(
    " ".join(pair) for pair in itertools.product(_DATE_PATTERNS, _TIME_PATTERNS)
)


def _only_string(element: etree._Element) -> "str | None":
    """The text of `element` when it is all one text node, at any depth -
    bs4's `tag.string`, which odsslicer learnt a cell's displayed text by
    example from: `<text:p>1,5 €</text:p>` and `<text:p><text:span>1,5
    €</text:span></text:p>` say "1,5 €", an empty paragraph or one of
    several nodes nothing. A lone comment is a text node in bs4."""
    while True:
        if len(element) == 0:
            return element.text
        if element.text or len(element) > 1:
            return None
        only = element[0]
        if only.tail:
            return None
        if not isinstance(only.tag, str):  # a comment, or a processing instruction
            return only.text
        element = only


class _DisplayText:
    """The text `cell` is to show for a value about to be written to it -
    read before the write changes anything, its former value being the
    first example to learn from."""

    def __init__(self, cell: "Cell") -> None:
        self._cell = cell

    def of(self, value: Any, value_type: "str | None") -> "str | None":
        """The text for `value`, written as ODF type `value_type` (a number
        keeps its cell's percentage or currency type): by example, by
        format, else a plain rendering - `None` for no value."""
        if value is None:
            return None
        if isinstance(value, bool):
            return self._boolean(value) or ("true" if value else "false")
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return self._number(cast(str, value_type), value) or self._rendered(value) or str(value)
        # a date or a time: the cell's own format first - a date one now,
        # unless it had one of another kind - as an example could be a cell
        # formatted otherwise
        if isinstance(value, dt.date):  # a datetime.datetime too
            return (
                self._rendered(value)
                or self._date(value, same_format=self._shown_as_date_time(value))
                or str(value)  # the ISO format, a space before a time part
            )
        return (
            self._rendered(value)
            or self._time(value, same_format=self._shown_as_date_time(value))
            or (_duration_text(value) if isinstance(value, dt.timedelta) else value.isoformat())
        )

    def _rendered(self, value: object) -> "str | None":
        """The display text `value` should have per the cell's own real,
        resolved number format - decimal places, grouping, currency symbol,
        or a date/time layout - read directly from the document rather than
        guessed from another cell's example. `None` if the cell has no
        resolvable format, or the format's family doesn't apply to `value`'s
        type."""
        number_format = self._cell._resolved_number_format(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _render_number_from_format(number_format, value)
        if isinstance(value, dt.date):  # a datetime.datetime too
            return _render_date_time_from_format(number_format, value, "date")
        if isinstance(value, (dt.time, dt.timedelta)):
            return _render_date_time_from_format(number_format, value, "time")
        return None

    def _shown_as_date_time(self, value: object) -> bool:
        """Whether the cell shows `value` with a date or time format. Only a
        cell shown with the same format is then an example to learn from -
        see `_candidates`."""
        number_format = self._cell._resolved_number_format(value)
        return number_format is not None and number_format.family in ("date", "time")

    def _number_format_at(self, tag: etree._Element) -> "str | None":
        """The name of the number format the cell element `tag` shows with:
        its own style's, else its column's default cell style's, following
        the styles' parents."""
        sheet = self._cell.sheet
        assert sheet is not None  # a cell of no sheet has no example to look for
        name = tag.get(_STYLE_NAME)
        if name is None:
            table = next(tag.iterancestors(qn("table:table")), None)
            if table is None:
                return None
            col = sum(
                int(cell.get(_COLUMNS_REPEATED, "1"))
                for cell in tag.itersiblings(*_CELL_TAGS, preceding=True)
            )
            seen = 0
            for definition in _column_definitions(table):
                seen += int(definition.get(_COLUMNS_REPEATED, "1"))
                if col < seen:
                    name = definition.get(_DEFAULT_CELL_STYLE_NAME)
                    break
        return cast("ODSReader", sheet.reader)._catalog.data_style_name(name)

    def _candidates(
        self, fmt: "str | None", raw_attr: str, *, same_format: bool = False
    ) -> Iterator[tuple[str, str]]:
        """Other cells with the same ODF format (this cell's own pre-write state
        first, then the rest of the document) to learn a display pattern from,
        as (raw attribute value, displayed text) pairs.

        A *lazy* generator, and that matters a lot: each `preceding`/
        `following` probe traverses the document until it hits a matching
        cell - which means a full document scan when there is none in that
        direction. Consumers stop at the first candidate whose pattern
        checks out (usually the cell's own prior state, or a neighbour
        found backwards), so yielding lazily keeps the forward scan from
        running at all in the common case. Building the list eagerly made
        every write of a format with no example anywhere in the document
        cost two full scans - ~33 ms per cell on a 10,000-row sheet.

        With `same_format`, another cell counts only if it shows with the
        number format this one does: a long date took the layout of a short
        one, `09/27/26` for "Sunday, September 27, 2026" (issue #16). Past
        50 cells shown otherwise, the search stops in that direction."""
        cell = self._cell
        if cell.format == fmt and cell.raw_value is not None and cell.text is not None:
            yield (cell.raw_value, cell.text)
        wanted = self._number_format_at(cell.cell) if same_format else None
        raw = qn(raw_attr)

        def matches(tag: etree._Element) -> bool:
            return tag.get(_VALUE_TYPE) == fmt and bool(tag.get(raw)) and tag is not cell.cell

        for found in (preceding(cell.cell, "table:table-cell"), following(cell.cell, "table:table-cell")):
            skipped = 0
            for match in found:
                if not matches(match):
                    continue
                if wanted is not None and self._number_format_at(match) != wanted:
                    skipped += 1
                    if skipped == 50:
                        break
                    continue
                p = next(match.iterchildren(_TEXT_P), None)
                only = _only_string(p) if p is not None else None
                if only is not None:
                    yield (cast(str, match.get(raw)), only)
                break

    def _number(self, fmt: str, new_value: float) -> "str | None":
        """Render `new_value` the way another float/percentage/currency cell in
        this document renders its own value (decimal separator, decimal count,
        prefix/suffix such as " %" or " €"), or None if no example is usable."""
        for template_raw, template_text in self._candidates(fmt, "office:value"):
            try:
                template_value = float(template_raw)
            except (TypeError, ValueError):
                continue
            render = _number_pattern(fmt, template_text)
            if render is not None and render(template_value) == template_text:  # checked before trusting it
                return render(new_value)
        return None

    def _date(self, new_value: dt.date, *, same_format: bool = False) -> "str | None":
        # dates and date-times alike go through as date-times: a date-time
        # pattern then also covers a date - LibreOffice saves a date-time
        # falling on midnight as a bare date - and a date pattern a date-time
        for template_raw, template_text in self._candidates(
            "date", "office:date-value", same_format=same_format
        ):
            try:
                template_value = _as_datetime(_parse_date_value(template_raw))
            except (TypeError, ValueError):
                continue
            for pattern in _DATE_TIME_PATTERNS:
                if template_value.strftime(pattern) == template_text:
                    return _as_datetime(new_value).strftime(pattern)
        return None

    def _time(self, new_value: "dt.time | dt.timedelta", *, same_format: bool = False) -> "str | None":
        # a duration past a day shows wrapped in a clock format (08:45:00) but
        # not in an elapsed-time one (128:45:00), and an example within a day
        # looks the same in both: only the cell's own format can tell
        if isinstance(new_value, dt.timedelta):
            return None
        for template_raw, template_text in self._candidates(
            "time", "office:time-value", same_format=same_format
        ):
            try:
                template_value = _parse_time_value(template_raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(template_value, dt.time):
                continue
            for pattern in _TIME_PATTERNS:
                if template_value.strftime(pattern) == template_text:
                    return new_value.strftime(pattern)
        return None

    def _boolean(self, new_value: bool) -> "str | None":  # noqa: FBT001 - the value, not a flag
        target_raw = "true" if new_value else "false"
        for template_raw, template_text in self._candidates("boolean", "office:boolean-value"):
            if template_raw == target_raw:
                return template_text
        return None


def _number_pattern(fmt: str, template_text: str) -> "Callable[[float], str] | None":
    """How the text `template_text` of a number of type `fmt` lays a number
    out - its separator, decimals, and the text around it - as a function
    rendering another number that way; `None` if it shows no number.

    Plain "General"-style cells show as many digits as the value needs:
    rounding to the template's own decimal count would lose precision ("3.4"
    as template only lends its separator). A percentage or a currency shows
    its own count of decimals."""
    m = re.search(r"-?\d+(?:[.,]\d+)?", template_text)
    if m is None:
        return None
    prefix, numeric, suffix = template_text[: m.start()], m.group(), template_text[m.end() :]
    decimal_sep = "," if "," in numeric else "."
    if fmt == "float":

        def render_general(value: float) -> str:
            return f"{prefix}{str(value).replace('.', decimal_sep)}{suffix}"

        return render_general
    decimals = len(numeric.split(decimal_sep)[1]) if decimal_sep in numeric else 0
    scale = 100 if fmt == "percentage" else 1

    def render_fixed(value: float) -> str:
        rendered = f"{value * scale:.{decimals}f}".replace(".", decimal_sep)
        return f"{prefix}{rendered}{suffix}"

    return render_fixed


# rendering by format


def _render_number_from_format(number_format: "NumberFormat | None", value: float) -> "str | None":
    """Render `value` per `number_format`'s own real definition - decimal
    places, thousands grouping, currency symbol - a genuine read of the
    document's ODF format rather than a guess from another cell's
    example (see `_DisplayText._number`). `None` if `number_format`
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


@dataclass(frozen=True, slots=True)
class _DateTimeLayout:
    """What a date or time format is made of, read once: its components in
    order (literal text included), the kinds among them, whether one is a
    name (a day of the week, a textual month), how many decimals its seconds
    show, and whether it counts time in full (`[HH]:MM`,
    `number:truncate-on-overflow="false"`)."""

    components: "tuple[etree._Element, ...]"
    kinds: "frozenset[str]"
    named: bool
    decimals: int
    elapsed: bool

    @classmethod
    def of(cls, tag: etree._Element) -> "_DateTimeLayout":
        components = tuple(c for c in elements(tag) if _part(c) in _DATE_TIME_COMPONENT_NAMES)
        return cls(
            components=components,
            kinds=frozenset(kind for c in components if (kind := _part(c)) is not None),
            named=any(
                _part(c) == "day-of-week" or (_part(c) == "month" and c.get(qn("number:textual")) == "true")
                for c in components
            ),
            decimals=max(
                (int(c.get(qn("number:decimal-places"), "0")) for c in components if _part(c) == "seconds"),
                default=0,
            ),
            elapsed=tag.get(qn("number:truncate-on-overflow")) == "false",
        )


@dataclass(frozen=True, slots=True)
class _ElapsedFields:
    """A duration as an elapsed-time format shows it: the leading unit holds
    the whole duration - `.hour`, `.minute`, `.second` read like a
    `datetime.time`'s, by the component renderers."""

    hour: int
    minute: int
    second: int


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
    layout = _DateTimeLayout.of(tag)
    names = None
    if layout.named or layout.decimals:
        names = _date_names(*_format_locale(number_format))
        if layout.named and (names is None or not isinstance(value, dt.date)):
            return None
    sign, fields, fraction = _shown_fields(value, layout)
    rendered = _ComponentRenderer(layout, fields, fraction, names).render()
    return None if rendered is None else sign + rendered


def _format_locale(number_format: "NumberFormat") -> "tuple[str | None, str | None]":
    """The language and country `number_format` names its days and months
    in: its own, else the document's."""
    tag = number_format._tag
    language = tag.get(qn("number:language"))
    if language:
        return language, tag.get(qn("number:country"))
    if number_format._reader is not None:
        return number_format._reader._catalog.document_locale()
    return None, None


def _shown_fields(
    value: "dt.date | dt.time | dt.timedelta", layout: _DateTimeLayout
) -> "tuple[str, Any, int]":
    """The sign, the fields (`.hour`, `.day`...) and the microseconds the
    format shows of `value`: a time of day and a duration through a
    `timedelta` - wrapped around the clock, or in full in an elapsed-time
    format, where an elapsed [MM]:SS format shows 02:05:03 as 125:03 - and a
    date through its midnight when the format has a time part."""
    if isinstance(value, (dt.time, dt.timedelta)):
        delta = _as_timedelta(value)
        if layout.elapsed:
            if layout.decimals:
                delta = _rounded(delta, layout.decimals)
            sign, fields = _elapsed_fields(delta, layout.kinds)
            return sign, fields, abs(delta).microseconds
        clock = (dt.datetime.min + delta % _ONE_DAY).time()
        return "", clock, clock.microsecond
    if isinstance(value, dt.date) and layout.kinds & _TIME_OF_DAY_COMPONENT_NAMES:
        midnight = _as_datetime(value)
        return "", midnight, midnight.microsecond
    return "", value, 0


class _ComponentRenderer:
    """The text of each component of a date or time format, for the fields
    of one value - `None` for the whole if one is beyond it."""

    def __init__(
        self, layout: _DateTimeLayout, fields: Any, fraction: int, names: "_DateNames | None"
    ) -> None:
        self._layout = layout
        self._fields = fields
        self._fraction = fraction
        self._names = names

    def render(self) -> "str | None":
        parts = []
        for component in self._layout.components:
            part = self._component(component)
            if part is None:
                return None
            parts.append(part)
        return "".join(parts)

    def _component(self, component: etree._Element) -> "str | None":
        kind, style = _part(component), component.get(qn("number:style"), "short")
        fields = self._fields
        if kind == "text":
            return text(component)
        if kind == "am-pm":
            return "PM" if fields.hour >= 12 else "AM"
        if kind == "hours" and "am-pm" in self._layout.kinds:  # 13:45 shows as 01:45 PM
            hour = fields.hour % 12 or 12
            return f"{hour:02d}" if style == "long" else str(hour)
        if kind == "day-of-week":
            names = cast("_DateNames", self._names)  # checked by the caller, for a date
            days = names.days if style == "long" else names.short_days
            return days[(fields.weekday() + 1) % 7]  # Sunday first
        if kind == "month" and component.get(qn("number:textual")) == "true":
            return self._month_name(style)
        if kind in _DATE_TIME_RENDER_LONG:
            return self._number(cast(str, kind), style, component)
        return None

    def _month_name(self, style: str) -> str:
        names = cast("_DateNames", self._names)  # checked by the caller
        if "day" in self._layout.kinds:  # declined next to a day, in some languages
            months = names.months_with_day if style == "long" else names.short_months_with_day
        else:
            months = names.months if style == "long" else names.short_months
        return months[self._fields.month - 1]

    def _number(self, kind: str, style: str, component: etree._Element) -> str:
        renderer = _DATE_TIME_RENDER_LONG if style == "long" else _DATE_TIME_RENDER_SHORT
        rendered = renderer[kind](self._fields)
        places = int(component.get(qn("number:decimal-places"), "0")) if kind == "seconds" else 0
        if places:
            separator = self._names.decimal if self._names is not None else "."
            rendered += separator + _fraction_digits(self._fraction, places)
        return rendered


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


def _elapsed_fields(value: dt.timedelta, kinds: "frozenset[str]") -> "tuple[str, _ElapsedFields]":
    """The sign of a duration, and its hours, minutes and seconds as an
    elapsed-time format shows them: the leading unit - the hours, else the
    minutes, else the seconds - holds the whole duration, however long."""
    sign, hours, minutes, seconds, _ = _clock_parts(value)
    if "hours" not in kinds:
        hours, minutes = 0, minutes + 60 * hours
        if "minutes" not in kinds:
            minutes, seconds = 0, seconds + 60 * minutes
    return sign, _ElapsedFields(hour=hours, minute=minutes, second=seconds)
