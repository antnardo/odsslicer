"""The text a cell shows for a value written to it."""

import datetime as dt

import pytest

from conftest import (
    _blank_document,
    _saved_with_raw_values,
    document_in,
    first,
    ods_with_sheet,
    table_row,
    text_cell,
)
from odsslicer import NumberFormat, NumberFormatSpec, ODSReader, xmltree
from odsslicer.xmltree import qn

# ---------------------------------------------------------------------------
# Writing: the displayed text learnt from an existing example
# ---------------------------------------------------------------------------


def test_percentage_display_text_learned_from_own_prior_state(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A6"].text == "200,00 %"
    s["A6"].value = 0.5
    assert s["A6"].value == 0.5
    assert s["A6"].text == "50,00 %"


def test_currency_display_text_learned_from_own_prior_state(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A7"].text == "2,00 €"
    s["A7"].value = 12.5
    assert s["A7"].text == "12,50 €"


def test_date_display_text_learned_from_own_prior_state(writable_reader):
    s = writable_reader.sheet("Sheet1")
    assert s["A8"].text == "28/02/21"
    s["A8"].value = dt.date(2030, 1, 5)
    assert s["A8"].text == "05/01/30"


def test_plain_float_borrows_only_the_decimal_separator_not_the_decimal_count(writable_reader):
    # regression: naively reusing the template's decimal COUNT (as is correct for
    # percentage/currency) would round 7.25 down to "7,2" and lose precision for
    # a plain "General"-format float cell, which shows as many digits as needed.
    s = writable_reader.sheet("Sheet1")
    assert s["A2"].text == "3,4"
    s["A2"].value = 7.25
    assert s["A2"].text == "7,25"
    s["A2"].value = 7
    assert s["A2"].text == "7"


def test_date_display_text_learned_from_another_cell_in_the_sheet(writable_reader):
    # C5 has no prior date of its own: the pattern must come from A8 instead
    s = writable_reader.sheet("Sheet1")
    s["C5"].value = dt.date(2030, 1, 5)
    assert s["C5"].text == "05/01/30"


def test_boolean_display_text_falls_back_without_a_template(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = True
    assert s["C1"].text == "true"


def test_boolean_display_text_learned_from_another_cell_of_the_same_polarity(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = True
    xmltree.set_text(first(s["C1"].cell, "text:p"), "VRAI")  # simulate a French-locale boolean cell
    s["C2"].value = True
    assert s["C2"].text == "VRAI"
    s["C3"].value = False  # opposite polarity has no template: falls back to default
    assert s["C3"].text == "false"


def test_number_inference_falls_through_to_the_real_format_when_it_cannot_reproduce_an_example(
    writable_reader,
):
    # if the self-consistency check fails, the inferred (learn-by-example) pattern
    # is discarded - but A6 still has a real, resolvable percentage NumberFormat of
    # its own (ce1 -> N11, 2 decimal places), so the *real format* fallback (see
    # below) renders it correctly instead of giving up to a bare str() conversion
    s = writable_reader.sheet("Sheet1")
    cell = s["A6"]
    xmltree.set_text(first(cell.cell, "text:p"), "deux cents")  # not a model our regex can parse
    cell.__init__(cell.cell, row=cell.row, col=cell.col, sheet=cell.sheet)  # refresh the cache
    cell.value = 0.75
    assert cell.text == "75.00 %"


# ---------------------------------------------------------------------------
# Writing: the displayed text read from the ODF format itself, rather than
# learnt by example, when no example exists
# ---------------------------------------------------------------------------


def test_number_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(
        r, NumberFormatSpec("currency", decimal_places=2, currency_symbol="$", grouping=True)
    )
    s["A1"].style.number_format = fmt  # no prior .value write - a truly virgin cell
    s["A1"].value = 1234.5
    assert s["A1"].text == "1,234.50 $"


def test_percentage_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, NumberFormatSpec("percentage", decimal_places=1))
    s["A1"].style.number_format = fmt
    s["A1"].value = 0.256
    assert s["A1"].text == "25.6 %"


def test_date_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(
        r,
        NumberFormatSpec(
            "date",
            components=[("year", "long"), ("text", "-"), ("month", "long"), ("text", "-"), ("day", "long")],
        ),
    )
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.date(2026, 3, 5)
    assert s["A1"].text == "2026-03-05"


def test_time_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(
        r, NumberFormatSpec("time", components=[("hours", "long"), ("text", "h"), ("minutes", "long")])
    )
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.time(9, 5)
    assert s["A1"].text == "09h05"


def test_display_falls_back_to_plain_conversion_with_no_example_and_no_style():
    # the ultimate fallback still applies when there's truly nothing to go on
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].value = 1234.5
    assert s["A1"].text == "1234.5"


def test_number_format_with_an_unsupported_date_component_falls_back_safely():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(
        r, NumberFormatSpec("date", components=[("quarter", "long"), ("text", " "), ("day", "long")])
    )
    s["A1"].style.number_format = fmt
    s["A1"].value = dt.date(2026, 3, 5)
    assert s["A1"].text == "2026-03-05"  # isoformat() fallback, not a partial/garbled render


_DMY = [
    ("day", "long"),
    ("text", "/"),
    ("month", "long"),
    ("text", "/"),
    ("year", "long"),
]
_HMS = [
    ("hours", "long"),
    ("text", ":"),
    ("minutes", "long"),
    ("text", ":"),
    ("seconds", "long"),
]


def test_date_time_display_reads_the_real_format_with_no_example_anywhere():
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(
        r, NumberFormatSpec("date", components=[*_DMY, ("text", " "), *_HMS])
    )
    s["A1"].value = dt.datetime(2023, 11, 30, 13, 5, 9)
    assert s["A1"].text == "30/11/2023 13:05:09"


def test_a_date_in_a_date_time_format_displays_as_its_midnight():
    # used to raise AttributeError: a date has no .hour to render
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(
        r, NumberFormatSpec("date", components=[*_DMY, ("text", " "), *_HMS])
    )
    s["A1"].value = dt.date(2023, 11, 30)
    assert s["A1"].text == "30/11/2023 00:00:00"


@pytest.mark.parametrize(
    ("elapsed", "value", "text"),
    [
        # a clock format wraps around the day
        (False, dt.timedelta(hours=128, minutes=45), "08:45:00"),
        (False, dt.timedelta(hours=-1, minutes=-30), "22:30:00"),
        # [HH]:MM:SS counts every hour
        (True, dt.timedelta(hours=128, minutes=45), "128:45:00"),
        (True, dt.timedelta(hours=-1, minutes=-30), "-01:30:00"),
    ],
)
def test_duration_display_follows_the_real_format_like_libreoffice(elapsed, value, text):
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, NumberFormatSpec("time", components=_HMS, elapsed=elapsed))
    s["A1"].style.number_format = fmt
    s["A1"].value = value
    assert s["A1"].text == text


def test_an_elapsed_minutes_format_counts_the_hours_in_the_minutes():
    r = _blank_document()
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(
        r,
        NumberFormatSpec(
            "time", components=[("minutes", "long"), ("text", ":"), ("seconds", "long")], elapsed=True
        ),
    )
    s["A1"].style.number_format = fmt
    # within a day, so it reads back as a time of day
    s["A1"].value = dt.timedelta(hours=2, minutes=5, seconds=3)
    assert s["A1"].text == "125:03"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.datetime(2023, 11, 30, 13), "2023-11-30 13:00:00"),
        (dt.timedelta(hours=128, minutes=45), "128:45:00"),
        (dt.timedelta(hours=-1, minutes=-30), "-01:30:00"),
    ],
)
def test_date_time_and_duration_display_fall_back_to_a_plain_rendering(value, text):
    # a cell formatted for numbers keeps its format, as it would in
    # LibreOffice, and no date or time layout applies to it
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(r, NumberFormatSpec("number", decimal_places=2))
    s["A1"].value = value
    assert s["A1"].text == text


def _date_formatted(path, *addresses):
    """The first sheet of `path`, A2 and `addresses` given a date format
    whose components nothing renders - a quarter - so that the text of
    `addresses` has to be learnt from another cell of that format, A2's."""
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    fmt = NumberFormat.create(r, NumberFormatSpec("date", components=[("quarter", "long")]))
    for address in ("A2", *addresses):
        s[address].style.number_format = fmt
    return s


def test_date_time_display_is_learnt_from_another_date_time_cell(tmp_path):
    path = _saved_with_raw_values(tmp_path, [("date", "2023-11-30T13:00:00", "30/11/2023 13:00")])
    s = _date_formatted(path, "A3", "A4")
    s["A3"].value = dt.datetime(2024, 1, 5, 8, 30)
    s["A4"].value = dt.date(2024, 1, 6)  # a bare date, as LibreOffice saves a midnight
    assert s["A3"].text == "05/01/2024 08:30"
    assert s["A4"].text == "06/01/2024 00:00"


def test_a_date_time_displays_as_a_date_next_to_dates(tmp_path):
    # as a date-only format shows it: the example is taken to share the cell's format
    path = _saved_with_raw_values(tmp_path, [("date", "2023-11-30", "30/11/2023")])
    s = _date_formatted(path, "A3")
    s["A3"].value = dt.datetime(2024, 1, 5, 8, 30)
    assert s["A3"].text == "05/01/2024"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.time(13, 45), "01:45 PM"),  # used to show 13:45 PM
        (dt.time(12, 5), "12:05 PM"),
        (dt.time(0, 30), "12:30 AM"),
        (dt.time(9, 5), "09:05 AM"),
    ],
)
def test_an_am_pm_format_counts_the_hours_on_a_12_hour_clock(value, text):
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1"].style.number_format = NumberFormat.create(
        r, NumberFormatSpec("time", components=[*_HMS[:3], ("text", " "), ("am-pm", "")])
    )
    s["A1"].value = value
    assert s["A1"].text == text


def test_a_date_in_a_system_format_takes_its_text_from_a_cell_libreoffice_saved(
    tmp_path,
):
    # number:format-source="language": LibreOffice shows the system's own
    # short date - 07/03/2022 on a French macOS - whatever the elements say
    styles = (
        '<number:date-style style:name="Nsystem" number:automatic-order="true"'
        ' number:format-source="language"><number:day/><number:text>/</number:text>'
        "<number:month/><number:text>/</number:text><number:year/></number:date-style>"
        '<style:style style:name="day" style:family="table-cell"'
        ' style:data-style-name="Nsystem"/>'
    )
    saved = (
        '<table:table-cell office:value-type="date" office:date-value="2022-03-07">'
        "<text:p>07/03/2022</text:p></table:table-cell>"
    )
    xml = (
        '<table:table-column table:default-cell-style-name="day"/>'
        + table_row(saved)
        + table_row(text_cell("end"))
    )
    r = ODSReader(ods_with_sheet(tmp_path / "system.ods", xml, styles))
    s = r.sheet("Sheet1")
    s["A2"].value = dt.date(2022, 3, 8)
    assert s["A2"].style.number_format.name == "Nsystem"  # kept
    assert s["A2"].text == "08/03/2022"  # not 8/3/22, as the elements read


_SYSTEM_STYLES = (
    '<number:date-style style:name="Nsystem" number:automatic-order="true"'
    ' number:format-source="language"><number:day/><number:text>/</number:text>'
    "<number:month/><number:text>/</number:text><number:year/></number:date-style>"
    '<style:style style:name="system" style:family="table-cell" style:data-style-name="Nsystem"/>'
    '<style:style style:name="child" style:family="table-cell" style:parent-style-name="system"/>'
)


def _saved_date(text, style=None):
    style_attr = f' table:style-name="{style}"' if style else ""
    return (
        f'<table:table-cell{style_attr} office:value-type="date" office:date-value="2022-03-07">'
        f"<text:p>{text}</text:p></table:table-cell>"
    )


@pytest.mark.parametrize(
    "layout",
    [
        # the formats given by the columns' default cell styles
        (
            '<table:table-column table:default-cell-style-name="system"/>'
            '<table:table-column table:default-cell-style-name="short"/>'
            + table_row(_saved_date("07/03/2022"), _saved_date("03/07/22"))
            + table_row("<table:table-cell/>", "<table:table-cell/>")
        ),
        # by the cells' own styles, one of them through its parent
        (
            '<table:table-column table:number-columns-repeated="2"/>'
            + table_row(_saved_date("07/03/2022", "child"), _saved_date("03/07/22", "short"))
            + table_row('<table:table-cell table:style-name="child"/>', "<table:table-cell/>")
        ),
    ],
    ids=["column defaults", "parent style"],
)
def test_a_date_learns_its_text_only_from_a_cell_of_its_own_format(tmp_path, layout):
    # B1, nearer to A2 than A1, shows another format: it lent its layout
    styles = _SYSTEM_STYLES + _SHORT_DATE_STYLE
    r = ODSReader(ods_with_sheet(tmp_path / "formats.ods", layout, styles))
    s = r.sheet("Sheet1")
    s["A2"].value = dt.date(2022, 3, 8)
    assert s["A2"].text == "08/03/2022"


_SHORT_DATE_STYLE = (
    '<number:date-style style:name="Nshort"><number:month number:style="long"/>'
    '<number:text>/</number:text><number:day number:style="long"/>'
    "<number:text>/</number:text><number:year/></number:date-style>"
    '<style:style style:name="short" style:family="table-cell" style:data-style-name="Nshort"/>'
)


def _written_in(tmp_path, number_style, value):
    """The text odsslicer writes for `value` into A2, a cell shown with
    `number_style` - the elements of a `<number:*-style>` named N1 - in a
    French document where A1 shows a date in a short format, an example no
    text of A2's may be learnt from."""
    family = "time" if isinstance(value, (dt.time, dt.timedelta)) else "date"
    styles = (
        _SHORT_DATE_STYLE
        + number_style.replace("<style", f'<number:{family}-style style:name="N1"', 1).replace(
            "</style>", f"</number:{family}-style>"
        )
        + '<style:style style:name="own" style:family="table-cell" style:data-style-name="N1"/>'
    )
    example = (
        '<table:table-cell table:style-name="short" office:value-type="date"'
        ' office:date-value="2026-09-13"><text:p>09/13/26</text:p></table:table-cell>'
    )
    xml = (
        "<table:table-column/>" + table_row(example) + table_row('<table:table-cell table:style-name="own"/>')
    )
    r = ODSReader(ods_with_sheet(tmp_path / "formats.ods", xml, styles))
    r.sheet("Sheet1")["A2"].value = value
    return r.sheet("Sheet1")["A2"].text


_DAY_LONG = '<number:day-of-week number:style="long"/>'
_MONTH_NAME = '<number:month number:style="long" number:textual="true"/>'
_YEAR = '<number:year number:style="long"/>'


# ---------------------------------------------------------------------------
# The text of a date or a time follows the cell's own format (issue #16): the
# names of days and months in its language, fractions of a second, a
# duration under a day
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number_style", "expected"),
    [
        # the report's, as LibreOffice shows it: NNNN, MMMM D, YYYY
        pytest.param(
            f'<style number:language="en" number:country="US">{_DAY_LONG}'
            f"<number:text>, </number:text>{_MONTH_NAME}<number:text> </number:text>"
            f"<number:day/><number:text>, </number:text>{_YEAR}</style>",
            "Sunday, September 27, 2026",
            id="en-US",
        ),
        # no language: the document's, French
        pytest.param(
            f"<style>{_DAY_LONG}<number:text> </number:text><number:day/>"
            f"<number:text> </number:text>{_MONTH_NAME}<number:text> </number:text>{_YEAR}</style>",
            "dimanche 27 septembre 2026",
            id="fr-FR",
        ),
        pytest.param(
            '<style number:language="en" number:country="US"><number:day-of-week/>'
            '<number:text>, </number:text><number:month number:textual="true"/>'
            "<number:text> </number:text><number:day/></style>",
            "Sun, Sep 27",
            id="abbreviated",
        ),
        # a month next to a day is declined in Polish, and alone is not
        pytest.param(
            f'<style number:language="pl" number:country="PL"><number:day/>'
            f"<number:text> </number:text>{_MONTH_NAME}<number:text> </number:text>{_YEAR}</style>",
            "27 września 2026",
            id="pl-PL with a day",
        ),
        pytest.param(
            f'<style number:language="pl" number:country="PL">{_MONTH_NAME}'
            f"<number:text> </number:text>{_YEAR}</style>",
            "wrzesień 2026",
            id="pl-PL alone",
        ),
        # a language without names: ISO 8601, never A1's layout
        pytest.param(
            f'<style number:language="tlh">{_DAY_LONG}<number:text> </number:text><number:day/></style>',
            "2026-09-27",
            id="unknown language",
        ),
    ],
)
def test_a_date_is_written_in_its_own_format_with_its_names(tmp_path, number_style, expected):
    # regression: the names could not be rendered, and A1's 09/13/26 lent
    # its layout: 09/27/26
    assert _written_in(tmp_path, number_style, dt.date(2026, 9, 27)) == expected


_HMS_STYLE = (
    '<number:hours number:style="long"/><number:text>:</number:text>'
    '<number:minutes number:style="long"/><number:text>:</number:text>'
)


@pytest.mark.parametrize(
    ("number_style", "value", "expected"),
    [
        # as LibreOffice shows them, through its number formatter
        pytest.param(
            '<style number:language="en" number:country="US"><number:minutes number:style="long"/>'
            '<number:text>:</number:text><number:seconds number:style="long"'
            ' number:decimal-places="2"/></style>',
            dt.timedelta(minutes=1, seconds=24, microseconds=750000),
            "01:24.75",
            id="MM:SS.00",
        ),
        pytest.param(
            f'<style number:language="en" number:country="US">{_HMS_STYLE}'
            '<number:seconds number:style="long" number:decimal-places="2"/></style>',
            dt.time(8, 15, 42, 250000),
            "08:15:42.25",
            id="HH:MM:SS.00",
        ),
        pytest.param(
            f'<style number:language="en" number:country="US">{_HMS_STYLE}'
            '<number:seconds number:style="long" number:decimal-places="1"/></style>',
            dt.timedelta(seconds=84, microseconds=750000),
            "00:01:24.8",
            id="rounded",
        ),
        # rounded but never carried into the seconds, in a clock format...
        pytest.param(
            '<style number:language="en" number:country="US"><number:minutes number:style="long"/>'
            '<number:text>:</number:text><number:seconds number:style="long"'
            ' number:decimal-places="2"/></style>',
            dt.timedelta(seconds=59, microseconds=996000),
            "00:59.99",
            id="no carry",
        ),
        # ... where an elapsed-time one rounds the whole duration
        pytest.param(
            f'<style number:language="en" number:country="US" number:truncate-on-overflow="false">'
            f'{_HMS_STYLE}<number:seconds number:style="long" number:decimal-places="2"/></style>',
            dt.timedelta(seconds=59, microseconds=996000),
            "00:01:00.00",
            id="elapsed carry",
        ),
        # the document's separator: French
        pytest.param(
            '<style><number:minutes number:style="long"/><number:text>:</number:text>'
            '<number:seconds number:style="long" number:decimal-places="2"/></style>',
            dt.timedelta(minutes=1, seconds=24, microseconds=750000),
            "01:24,75",
            id="fr-FR",
        ),
        # without decimals, seconds are cut
        pytest.param(
            f'<style number:language="en" number:country="US">{_HMS_STYLE}'
            '<number:seconds number:style="long"/></style>',
            dt.time(12, 30, 15, 500000),
            "12:30:15",
            id="cut",
        ),
    ],
)
def test_fractions_of_a_second_show_as_libreoffice_shows_them(tmp_path, number_style, value, expected):
    # regression: the fraction was dropped, 01:24 for 01:24.75
    assert _written_in(tmp_path, number_style, value) == expected


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (dt.timedelta(hours=7, minutes=30), "07:30:00"),
        (dt.timedelta(minutes=1, seconds=23, microseconds=450000), "00:01:23"),
    ],
)
def test_a_duration_under_a_day_shows_as_a_duration(value, text):
    # regression: it was given the format of a time of day, and showed as
    # one in an American document: 07:30:00 AM
    r = document_in("en", "US")
    cell = r.sheet("Sheet1")["A1"]
    cell.value = value
    assert cell.text == text
    assert cell.style.number_format._tag.get(qn("number:truncate-on-overflow")) == "false"


def test_the_text_of_a_percentage_follows_its_format(tmp_path):
    # regression: always a space before the sign, whatever the format said:
    # 50.00 % in an en-US 0.00% cell
    styles = (
        '<number:percentage-style style:name="Npct"><number:number number:decimal-places="2"'
        ' number:min-decimal-places="2" number:min-integer-digits="1"/><number:text>%</number:text>'
        "</number:percentage-style>"
        '<style:style style:name="pct" style:family="table-cell" style:data-style-name="Npct"/>'
    )
    xml = "<table:table-column/>" + table_row('<table:table-cell table:style-name="pct"/>')
    r = ODSReader(ods_with_sheet(tmp_path / "pct.ods", xml, styles))
    r.sheet("Sheet1")["A1"].value = 0.5
    assert r.sheet("Sheet1")["A1"].text == "50.00%"
