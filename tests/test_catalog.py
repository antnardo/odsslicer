"""The style catalog: the format a date gets in a cell with no format, and forked styles."""

import datetime as dt

import pytest

from conftest import _blank_document, document_in, every, first
from odsslicer import NumberFormat, NumberFormatSpec, ODSReader
from odsslicer.xmltree import qn

_DATE_AND_TIMES = [
    dt.date(2022, 3, 7),
    dt.datetime(2022, 3, 7, 13, 45, 30),
    dt.time(9, 30),
    dt.timedelta(hours=128, minutes=45),
    dt.timedelta(hours=-1, minutes=-30),
]


# ---------------------------------------------------------------------------
# A date or time written into a cell with no number format gets the one
# LibreOffice would give it if typed there (issue #7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "country", "texts"),
    [
        ("fr", "FR", ["07/03/22", "07/03/22 13:45", "09:30:00"]),
        ("en", "US", ["03/07/22", "03/07/22 01:45 PM", "09:30:00 AM"]),
        ("de", None, ["07.03.22", "07.03.22 13:45", "09:30:00"]),
        (None, None, ["2022-03-07", "2022-03-07 13:45:30", "09:30:00"]),
    ],
)
def test_a_date_or_time_written_into_an_unformatted_cell_gets_the_locale_format(
    tmp_path, language, country, texts
):
    # regression: no format at all - LibreOffice showed 44627 for the date
    # past the declared columns, and 08:45:00 for 128:45 within them
    texts = [*texts, "128:45:00", "-01:30:00"]  # durations: the same everywhere
    r = document_in(language, country)
    s = r.sheet("Sheet1")
    for row, value in enumerate(_DATE_AND_TIMES):
        s[row, 0].value = value  # in the one declared column
        s[row, 2].value = value  # past it
    assert [s[row, 0].text for row in range(5)] == texts
    assert [s[row, 2].text for row in range(5)] == texts
    out = tmp_path / "out.ods"
    r.save(out)
    reread = ODSReader(out).sheet("Sheet1")
    assert [reread[row, 2].value for row in range(5)] == _DATE_AND_TIMES
    formats = [reread[row, 2].style.number_format for row in range(5)]
    assert [f.family for f in formats] == ["date", "date", "time", "time", "time"]
    # [HH]:MM:SS counts the hours in full
    assert formats[3]._tag.get(qn("number:truncate-on-overflow")) == "false"


def test_one_format_and_one_style_serve_every_unformatted_cell(tmp_path):
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1:A3"].value = [[dt.date(2022, 3, day)] for day in (7, 8, 9)]
    assert len({s[row, 0].style_name for row in range(3)}) == 1
    out = tmp_path / "out.ods"
    r.save(out)
    # nor does the next session add a second format
    r2 = ODSReader(out)
    formats = len(every(r2.data, "number:date-style"))
    s2 = r2.sheet("Sheet1")
    s2["A4"].value = dt.date(2022, 3, 10)
    assert len(every(r2.data, "number:date-style")) == formats
    assert s2["A4"].style.number_format.name == s2["A1"].style.number_format.name


def test_a_cell_formatted_for_dates_keeps_its_format(writable_reader):
    s = writable_reader.sheet("Sheet1")
    before = s["A8"].style.number_format.name  # DD/MM/YY
    s["A8"].value = dt.datetime(2030, 1, 5, 8, 30)
    assert s["A8"].style.number_format.name == before
    assert s["A8"].text == "05/01/30"


def test_a_cell_formatted_for_numbers_keeps_its_format_as_in_libreoffice():
    r = _blank_document()
    s = r.sheet("Sheet1")
    eur = NumberFormat.create(r, NumberFormatSpec("currency", decimal_places=2, currency_symbol="€"))
    s["A1"].style.number_format = eur
    s["A1"].value = dt.date(2022, 3, 7)
    assert s["A1"].style.number_format.name == eur.name


def test_a_date_written_into_a_column_formatted_for_dates_keeps_its_format(
    formatted_columns,
):
    s = formatted_columns.sheet("Sheet1")
    s["B2"].value = dt.date(2022, 3, 7)
    assert s["B2"].style_name is None  # still the column's
    assert s["B2"].text == "07/03/2022"
    formatted_columns.save()
    reread = ODSReader(formatted_columns.file).sheet("Sheet1")
    assert reread["B2"].style.number_format.name == "Ndmy"


def test_a_date_written_into_a_formatted_column_keeps_the_columns_look(
    formatted_columns,
):
    # the column has a background but no number format: the cell gets a
    # style of its own, which has to carry the column's background
    s = formatted_columns.sheet("Sheet1")
    s["A2"].value = dt.date(2022, 3, 7)
    assert s["A2"].style.background_color == "#fff2cc"
    assert s["A2"].style.number_format.family == "date"
    assert s["A2"].text == "07/03/22"


def test_setting_a_style_property_keeps_the_columns_formatting(formatted_columns):
    # the fork used to start from nothing, dropping the column's background
    s = formatted_columns.sheet("Sheet1")
    s["B3"].style.bold = True
    assert s["B3"].style.bold
    assert s["B3"].style.background_color == "#e2efda"
    assert s["B3"].style.number_format.name == "Ndmy"


def test_forking_after_a_style_copy_does_not_affect_the_source_cell(writable_reader):
    # regression: the "already forked, reuse" cache used to be keyed off
    # whether table:style-name *looked* like a forked name, which broke as
    # soon as two different cells legitimately shared one via this setter
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["B1"].style = s["A1"].style
    assert s["A1"].style_name == s["B1"].style_name

    s["B1"].style.italic = True
    assert s["A1"].style.italic is False
    assert s["B1"].style.italic is True
    assert s["B1"].style.bold is True  # still carried over from the shared parent
    assert s["A1"].style_name != s["B1"].style_name


def _own_style_tag(reader, cell):
    """The cell's own <style:style> element, exactly as save() will
    serialise it."""
    return reader._catalog.find_style(cell.style_name, family="table-cell")


def _assert_parent_is_usable(reader, tag):
    """A style:parent-style-name is only honoured when it names a style from
    styles.xml: LibreOffice ignores an automatic one (content.xml) and falls
    back to Default, dropping everything the fork meant to inherit."""
    parent = tag.get(qn("style:parent-style-name"))
    if parent is not None:
        assert first(reader.data, "style:style", style_name=parent) is None, (
            f"style:parent-style-name={parent!r} points at an automatic style"
        )


def test_forking_off_an_automatic_style_writes_its_properties_into_the_fork(writable_reader):
    # regression (issue #1): the fork used to inherit through
    # style:parent-style-name pointing at the automatic style it forked off,
    # which LibreOffice ignores - bold and border silently vanished on screen
    # while odsslicer still read them back. Assert on the serialised style
    # alone: following the chain (what CellStyle does on read) passed either
    # way, which is exactly what hid the bug.
    s = writable_reader.sheet("Sheet1")
    a, b = s["A1"], s["B1"]
    a.style.bold = True
    a.style.border_left = "2pt solid #000000"
    b.style = a.style  # both cells now share that one automatic style
    b.style.background_color = "#ffff00"

    tag = _own_style_tag(writable_reader, b)
    _assert_parent_is_usable(writable_reader, tag)
    assert first(tag, "style:text-properties").get(qn("fo:font-weight")) == "bold"
    cell_props = first(tag, "style:table-cell-properties")
    assert cell_props.get(qn("fo:border-left")) == "2pt solid #000000"
    assert cell_props.get(qn("fo:background-color")) == "#ffff00"
    # and the source cell keeps its own, untouched by the fork
    source = _own_style_tag(writable_reader, a)
    assert first(source, "style:table-cell-properties").get(qn("fo:background-color")) is None


def test_forking_off_a_named_style_keeps_it_as_the_parent(writable_reader):
    # the other half of the rule: a *named* style is a real ancestor, so the
    # fork links to it rather than copying it (later edits to it still apply)
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    c.cell.set(qn("table:style-name"), "Default")
    c.style.italic = True
    assert _own_style_tag(writable_reader, c).get(qn("style:parent-style-name")) == "Default"


def test_forking_carries_the_number_format_over(writable_reader):
    # style:data-style-name used to come along through the parent link too
    s = writable_reader.sheet("Sheet1")
    c = s["A7"]  # currency cell: its automatic style carries a data style
    data_style = _own_style_tag(writable_reader, c).get(qn("style:data-style-name"))
    displayed_before = c.text
    c.style.bold = True
    assert _own_style_tag(writable_reader, c).get(qn("style:data-style-name")) == data_style
    assert c.text == displayed_before


def test_repeated_forks_keep_accumulating_properties(writable_reader):
    # the reported symptom, in its shortest form: each new fork must still
    # carry everything the previous ones set
    s = writable_reader.sheet("Sheet1")
    c = s["A1"]
    for setter, value in (("bold", True), ("italic", True), ("background_color", "#ffff00")):
        setattr(c.style, setter, value)
        c._own_style_name = None  # force the next write to fork again
    tag = _own_style_tag(writable_reader, c)
    _assert_parent_is_usable(writable_reader, tag)
    text_props = first(tag, "style:text-properties")
    assert text_props.get(qn("fo:font-weight")) == "bold"
    assert text_props.get(qn("fo:font-style")) == "italic"
    assert first(tag, "style:table-cell-properties").get(qn("fo:background-color")) == "#ffff00"
