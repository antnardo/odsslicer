"""Number formats: the `<number:*-style>` elements a cell style names in
`style:data-style-name`, read as a `NumberFormat`, and made from scratch.

Split from `styles` at the 1.0 audit: a number format is no style of a cell
but a format its style names, read by the style views and by the inference of
the text a written value shows, and made by the style catalog for a date
written into a cell with no format. What `NumberFormat.create` is asked for
is a `_NumberFormatSpec`, checked once, from which `_number_format_element`
builds the element: the checks and the building used to share one function
of a hundred lines and nine parameters.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from lxml import etree

from odsslicer.dateformats import _STANDARD_FORMATS
from odsslicer.xmltree import child, elements, insert_before, new, qn, set_text, text

if TYPE_CHECKING:
    from odsslicer.reader import ODSReader

__all__ = ["NumberFormat"]

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
    heuristic used when *writing* a value (see `display._DisplayText`).

    A number style can be conditional (`<style:map style:condition="..."
    style:apply-style-name="...">`, e.g. currency amounts shown in red when
    negative): `.conditions` holds `(condition, NumberFormat)` pairs in
    document order, and `.resolve(value)` follows them to the `NumberFormat`
    that actually applies to a given value (itself, if none match).

    Writable: `NumberFormat.create(reader, family, ...)` builds a brand new
    format from scratch (for when no existing one in the document already
    has the layout you want); `.add_condition(condition, target)` appends
    a conditional variant to an existing format."""

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
                target_tag = reader._catalog.find_number_style(apply_name)
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
        spec = _NumberFormatSpec(
            family,
            decimal_places,
            grouping=grouping,
            min_integer_digits=min_integer_digits,
            currency_symbol=currency_symbol,
            components=tuple(components or ()),
            font_color=font_color,
            elapsed=elapsed,
        )
        catalog = reader._catalog
        # the locale is looked up for a percentage alone: it walks styles.xml
        affixes = _percent_affixes(*catalog.document_locale()) if family == "percentage" else ("", "")
        tag = _number_format_element(spec, catalog.new_style_name("N"), affixes)
        catalog.automatic_styles().append(tag)
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
        self._reader._package.touch_tag(self._tag)
        self.conditions.append((condition, target))

    def __repr__(self) -> str:
        return f"NumberFormat(name={self.name!r}, family={self.family!r})"


_FAMILY_TAGS = {
    "number": "number:number-style",
    "percentage": "number:percentage-style",
    "currency": "number:currency-style",
    "date": "number:date-style",
    "time": "number:time-style",
    "boolean": "number:boolean-style",
}
_NUMERIC_FAMILIES = frozenset({"number", "percentage", "currency"})


@dataclass(frozen=True, slots=True)
class _NumberFormatSpec:
    """What a new number format is to be - `NumberFormat.create`'s
    arguments, which say what each means - checked on creation, before
    anything is written: a family odsslicer can build, a currency with its
    symbol, a date or a time with its components, `elapsed` for a time."""

    family: str
    decimal_places: "int | None" = None
    grouping: bool = False
    min_integer_digits: int = 1
    currency_symbol: "str | None" = None
    components: "tuple[tuple[str, str], ...]" = ()
    font_color: "str | None" = None
    elapsed: bool = False

    def __post_init__(self) -> None:
        if self.family not in _FAMILY_TAGS:
            raise ValueError(
                f"unknown number format family {self.family!r} - expected one of {sorted(_FAMILY_TAGS)}"
            )
        if self.family == "currency" and not self.currency_symbol:
            raise ValueError("family='currency' requires currency_symbol")
        if self.family in ("date", "time") and not self.components:
            raise ValueError(f"family={self.family!r} requires components")
        if self.elapsed and self.family != "time":
            raise ValueError(f"elapsed=True applies to family='time', not {self.family!r}")


def _number_format_element(
    spec: _NumberFormatSpec, name: str, percent_affixes: "tuple[str, str]"
) -> etree._Element:
    """The `<number:*-style>` element `spec` describes, named `name`, not
    yet in any document; `percent_affixes` is the text around a
    percentage's number (see `_percent_affixes`)."""
    # its name given here, where the element is made: an attribute of
    # another namespace set on a detached element gets a prefix of
    # lxml's own (see `xmltree.new`)
    tag = new(_FAMILY_TAGS[spec.family], {"style:name": name})
    if spec.font_color is not None:
        tag.append(new("style:text-properties", {"fo:color": spec.font_color}))
    if spec.family in _NUMERIC_FAMILIES:
        _append_number(tag, spec, percent_affixes)
    elif spec.family == "boolean":
        tag.append(new("number:boolean"))
    else:
        _append_date_time(tag, spec)
    return tag


def _literal(value: str) -> etree._Element:
    """A `<number:text>` holding `value`, literal text of a format."""
    element = new("number:text")
    set_text(element, value)
    return element


def _append_number(tag: etree._Element, spec: _NumberFormatSpec, percent_affixes: "tuple[str, str]") -> None:
    """The number of a number, percentage or currency format, and the text
    around it: a percentage's sign, a currency's symbol."""
    number_child = new("number:number")
    if spec.decimal_places is not None:
        number_child.set(qn("number:decimal-places"), str(spec.decimal_places))
        number_child.set(qn("number:min-decimal-places"), str(spec.decimal_places))
    number_child.set(qn("number:min-integer-digits"), str(spec.min_integer_digits))
    if spec.grouping:
        number_child.set(qn("number:grouping"), "true")
    tag.append(number_child)
    if spec.family == "percentage":
        # a space before the sign always, where en-US has none
        before, after = percent_affixes
        if before:
            insert_before(number_child, _literal(before))
        if after:
            tag.append(_literal(after))
    elif spec.family == "currency":
        tag.append(_literal(" "))
        symbol_child = new("number:currency-symbol")
        set_text(symbol_child, cast(str, spec.currency_symbol))  # checked by the spec
        tag.append(symbol_child)


def _append_date_time(tag: etree._Element, spec: _NumberFormatSpec) -> None:
    """The components of a date or time format, in order."""
    if spec.elapsed:
        tag.set(qn("number:truncate-on-overflow"), "false")
    for kind, value in spec.components:
        if kind == "text":
            component = _literal(value)
        else:
            component = new(f"number:{kind}")
            if value:
                component.set(qn("number:style"), value)
        tag.append(component)


def _percent_affixes(language: "str | None", country: "str | None") -> "tuple[str, str]":
    """The text before and after the number of a percentage, in the locale
    `language`-`country` - see `_PERCENT_AFFIXES`."""
    locale = f"{language}-{country}"
    if locale not in _STANDARD_FORMATS:  # the language's first country's
        locale = next((code for code in _STANDARD_FORMATS if code.startswith(f"{language}-")), "")
    return _PERCENT_AFFIXES.get(locale, ("", "%"))
