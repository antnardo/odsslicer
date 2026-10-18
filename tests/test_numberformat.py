"""Number formats: reading them, their conditions, and making new ones."""

import datetime as dt

import pytest
from lxml import etree

from conftest import document_in
from odsslicer import NumberFormat, NumberFormatSpec, ODSReader, xmltree
from odsslicer.display import _render_number_from_format

# ---------------------------------------------------------------------------
# Styles, read: conditional number formats (NumberFormat.resolve)
# ---------------------------------------------------------------------------


def test_number_format_resolves_conditional_currency_by_value(reader):
    # regression-shaped, real data: N108 (A7's currency format) is negative-
    # only (red text) with a style:map switching to N108P0 (no color) for
    # value()>=0 - A7's own value is 2.0 (positive).
    s = reader.sheet("Sheet1")
    assert s["A7"].value == 2.0
    resolved = s["A7"].style.number_format
    assert resolved.name == "N108P0"
    assert resolved.font_color is None


def _number_style(family, children):
    """A `NumberFormat` over a `<number:{family}-style>` holding `children`,
    with the `loext` namespace and an unknown one, `x`, declared."""
    xml = (
        f'<number:{family}-style xmlns:number="{xmltree.NAMESPACES["number"]}"'
        f' xmlns:style="{xmltree.NAMESPACES["style"]}" xmlns:loext="{xmltree.NAMESPACES["loext"]}"'
        f' xmlns:x="urn:example:other" style:name="N1">{children}</number:{family}-style>'
    )
    return NumberFormat(etree.fromstring(xml))


@pytest.mark.parametrize("name", ["number:text", "loext:text"])
def test_number_format_text_is_rendered_whether_number_or_loext(name):
    # LibreOffice writes <loext:text> next to a fill character, and shows it
    # as text: the displayed text inferred on write keeps it either way
    fmt = _number_style("number", f'<number:number number:decimal-places="1"/><{name}> pcs</{name}>')
    assert _render_number_from_format(fmt, 3.0) == "3.0 pcs"


def test_number_format_element_of_another_namespace_is_no_part():
    fmt = _number_style("number", '<number:number number:decimal-places="1"/><x:text> pcs</x:text>')
    assert _render_number_from_format(fmt, 3.0) == "3.0"


def test_date_format_components_read_loext_text_and_skip_other_namespaces():
    fmt = _number_style(
        "date", "<number:day/><loext:text>/</loext:text><x:month/><x:text>-</x:text><number:year/>"
    )
    assert fmt.family == "date"
    assert fmt.components == [("day", "short"), ("text", "/"), ("year", "short")]


def test_number_format_condition_and_manual_resolve(reader):
    number_tag = reader._catalog.find_number_style("N108")
    from odsslicer import NumberFormat

    base = NumberFormat(number_tag, reader=reader)
    assert base.font_color == "#ff0000"
    assert [c for c, _ in base.conditions] == ["value()>=0"]

    assert base.resolve(2.0).name == "N108P0"
    assert base.resolve(2.0).font_color is None
    assert base.resolve(-5.0).name == "N108"
    assert base.resolve(-5.0).font_color == "#ff0000"
    # an unresolvable/no-condition-matching case falls back to self
    assert base.resolve("not a number").name == "N108"


# ---------------------------------------------------------------------------
# NumberFormat.create / .add_condition
# ---------------------------------------------------------------------------


class TestCreate:
    """`NumberFormat.create`: a new format from a `NumberFormatSpec`."""

    def test_a_number_format_has_its_places_and_grouping(self, writable_reader):
        r = writable_reader
        fmt = NumberFormat.create(r, NumberFormatSpec("number", decimal_places=3, grouping=True))
        assert fmt.family == "number"
        assert fmt.decimal_places == 3
        assert fmt.grouping is True

        s = r.sheet("Sheet1")
        s["A1"].value = 1234.5678
        s["A1"].style.number_format = fmt
        assert s["A1"].style.number_format.name == fmt.name

    def test_a_percentage_format_has_its_places(self, writable_reader):
        fmt = NumberFormat.create(writable_reader, NumberFormatSpec("percentage", decimal_places=1))
        assert fmt.family == "percentage"
        assert fmt.decimal_places == 1

    @pytest.mark.parametrize(
        ("language", "country", "text"),
        [
            # where LibreOffice's standard percentage format puts the sign
            ("en", "US", "50%"),
            ("fr", "FR", "50 %"),
            ("de", "DE", "50\xa0%"),
            ("tr", "TR", "%50"),
            ("de", None, "50\xa0%"),  # a language alone: its first country's
            ("zxx", None, "50%"),  # a locale LibreOffice does not know
            (None, None, "50%"),
        ],
    )
    def test_a_percentage_format_puts_its_sign_where_the_locale_does(self, language, country, text):
        # regression: a space before the sign in every document, 50 % in en-US
        r = document_in(language, country)
        fmt = NumberFormat.create(r, NumberFormatSpec("percentage", decimal_places=0))
        cell = r.sheet("Sheet1")["A1"]
        cell.style.number_format = fmt
        cell.value = 0.5
        assert cell.text == text

    def test_an_elapsed_time_format_counts_past_a_day(self):
        # regression: no way to make [HH]:MM - 26 hours showed as 02:00
        r = ODSReader.new()
        fmt = NumberFormat.create(
            r,
            NumberFormatSpec(
                "time", components=[("hours", "long"), ("text", ":"), ("minutes", "long")], elapsed=True
            ),
        )
        assert fmt.elapsed
        cell = r.sheet("Sheet1")["A1"]
        cell.style.number_format = fmt
        cell.value = dt.timedelta(hours=26)
        assert cell.text == "26:00"
        wrapping = NumberFormat.create(
            r, NumberFormatSpec("time", components=[("hours", "long"), ("text", ":"), ("minutes", "long")])
        )
        assert not wrapping.elapsed

    def test_only_a_time_format_is_elapsed(self):
        with pytest.raises(ValueError):
            NumberFormat.create(
                ODSReader.new(), NumberFormatSpec("date", components=[("day", "long")], elapsed=True)
            )

    def test_a_currency_format_has_its_symbol(self, writable_reader):
        fmt = NumberFormat.create(
            writable_reader, NumberFormatSpec("currency", decimal_places=2, currency_symbol="$")
        )
        assert fmt.family == "currency"
        assert fmt.currency_symbol == "$"

    def test_a_currency_without_its_symbol_raises(self, writable_reader):
        with pytest.raises(ValueError):
            NumberFormat.create(writable_reader, NumberFormatSpec("currency"))

    def test_the_0x_parameters_raise_type_error_naming_the_spec(self):
        with pytest.raises(TypeError, match="NumberFormatSpec"):
            NumberFormat.create(ODSReader.new(), "number")

    def test_a_date_format_has_its_components(self, writable_reader):
        components = [("day", "long"), ("text", "-"), ("month", "long"), ("text", "-"), ("year", "long")]
        fmt = NumberFormat.create(writable_reader, NumberFormatSpec("date", components=components))
        assert fmt.family == "date"
        assert fmt.components == components

    def test_a_date_without_components_raises(self, writable_reader):
        with pytest.raises(ValueError):
            NumberFormat.create(writable_reader, NumberFormatSpec("date"))

    def test_a_boolean_format_has_its_family(self, writable_reader):
        fmt = NumberFormat.create(writable_reader, NumberFormatSpec("boolean"))
        assert fmt.family == "boolean"

    def test_an_unknown_family_raises(self, writable_reader):
        with pytest.raises(ValueError):
            NumberFormat.create(writable_reader, NumberFormatSpec("fraction"))

    def test_a_font_color_is_kept(self, writable_reader):
        fmt = NumberFormat.create(
            writable_reader, NumberFormatSpec("currency", currency_symbol="€", font_color="#FF0000")
        )
        assert fmt.font_color == "#FF0000"


class TestNumberFormatSpec:
    """`NumberFormatSpec`: what a new format is to be."""

    def test_components_given_as_a_list_are_kept_as_a_tuple(self):
        spec = NumberFormatSpec("date", components=[["day", "long"], ("text", "/"), ("month", "long")])
        assert spec.components == (("day", "long"), ("text", "/"), ("month", "long"))
        # immutable, so equal specs hash alike
        assert hash(spec) == hash(NumberFormatSpec("date", components=spec.components))

    def test_flags_by_position_raise_type_error(self):
        with pytest.raises(TypeError):
            NumberFormatSpec("number", 2, True)


def test_add_condition_makes_the_format_resolve_by_value(writable_reader):
    r = writable_reader
    negative = NumberFormat.create(
        r, NumberFormatSpec("currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
    )
    base = NumberFormat.create(r, NumberFormatSpec("currency", decimal_places=2, currency_symbol="€"))
    base.add_condition("value()<0", negative)

    assert base.resolve(-5).name == negative.name
    assert base.resolve(-5).font_color == "#FF0000"
    assert base.resolve(5).name == base.name
    assert base.resolve(5).font_color is None


def test_add_condition_with_non_number_format_target_raises(writable_reader):
    fmt = NumberFormat.create(writable_reader, NumberFormatSpec("number"))
    with pytest.raises(TypeError):
        fmt.add_condition("value()<0", "not a NumberFormat")


def test_a_created_number_format_reads_back_after_a_save(writable_reader, tmp_path):
    r = writable_reader
    negative = NumberFormat.create(
        r, NumberFormatSpec("currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
    )
    base = NumberFormat.create(r, NumberFormatSpec("currency", decimal_places=2, currency_symbol="€"))
    base.add_condition("value()<0", negative)

    s = r.sheet("Sheet1")
    s["A1"].value = -10.0
    s["A1"].style.number_format = base

    out = tmp_path / "out.ods"
    r.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    resolved = reread["A1"].style.number_format
    assert resolved.currency_symbol == "€"
    assert resolved.font_color == "#FF0000"
