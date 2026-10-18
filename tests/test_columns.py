"""Column definitions: widths, default styles and print titles, by logical column."""

import pytest

from conftest import (
    PRINT_TITLE_WIDTHS_XML,
    PRINT_TITLE_XML,
    _defined_extent,
    _repeat,
    cells_with_content,
    empty_cells,
    every,
    ods_with_sheet,
    saved_table,
    table_row,
    text_cell,
)
from odsslicer import ODSReader


def test_inserted_columns_take_the_width_of_the_column_they_push_right(writable_reader):
    # issue #25: they took the default width, where LibreOffice repeats the
    # definition of the column they push right - its width, its visibility,
    # the default cell style of a column formatted as a whole
    s = writable_reader.sheet("Sheet1")
    s.column_style(1).width = "5cm"
    s.insert_columns(1, 2)
    assert [s.column_style(c).width for c in (1, 2, 3)] == ["5cm", "5cm", "5cm"]
    assert s.column_style(0).width != "5cm"


def test_styling_a_repeated_column_never_places_a_definition_by_position(tmp_path, positional):
    # in a definition repeated 16,384 times, as LibreOffice pads a sheet: 1.6 s, not 0.04 s
    xml = '<table:table-column table:number-columns-repeated="200"/>' + table_row(text_cell("a"))
    sheet = ODSReader(ods_with_sheet(tmp_path / "columns.ods", xml)).sheet("Sheet1")
    positional.clear()
    sheet.column_style(100).width = "3cm"
    assert "table-column" not in positional
    assert len(every(sheet.table, "table:table-column")) == 200
    assert [sheet.column_style(c).width for c in (99, 100, 101)] == [None, "3cm", None]


def test_inserting_columns_keeps_the_definitions_past_the_data(tmp_path):
    # regression: the last definition gave the inserted columns back though
    # the sheet was far from its maximum width, and the columns it defines
    # lost their width
    xml = (
        '<table:table-column table:number-columns-repeated="3"/>'
        '<table:table-column table:style-name="wide" table:number-columns-repeated="15"/>'
        + table_row(text_cell("a"), text_cell("b"), text_cell("c"), empty_cells(15))
    )
    wide = (
        '<style:style style:name="wide" style:family="table-column">'
        '<style:table-column-properties style:column-width="5cm"/></style:style>'
    )
    path = ods_with_sheet(tmp_path / "wide.ods", xml, wide)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_columns(0, 2)
    r.save()
    s = ODSReader(path).sheet("Sheet1")
    styles = [s.column_style(col) for col in (4, 5, 19, 20)]
    assert [style.width if style else None for style in styles] == [None, "5cm", "5cm", None]


def test_a_cell_reads_its_columns_default_cell_style(formatted_columns):
    s = formatted_columns.sheet("Sheet1")
    assert s["A2"].style_name is None
    assert s["A2"].style.background_color == "#fff2cc"
    assert s["B2"].style.number_format.name == "Ndmy"


def test_insert_columns_keeps_column_widths_with_their_columns(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.column_style(1).width = "5cm"
    s.insert_columns(1, 2)
    assert s.column_style(3).width == "5cm"
    # the new ones repeat the column they push right, as LibreOffice does
    # (issue #25) - see test_inserted_columns_take_the_width_of_the_column...
    assert [s.column_style(c).width for c in (1, 2)] == ["5cm", "5cm"]
    assert _defined_extent(s)[1] == s.n_cols


# ---------------------------------------------------------------------------
# Column definitions (issue #13): a sheet declares only the columns it uses,
# and LibreOffice groups the print titles' definitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("col", "expected"),
    [(1, {"A1": "a", "B1": "c"}), (2, {"A1": "a"})],
)
def test_deleting_a_column_past_the_declared_ones(tmp_path, col, expected):
    # regression: AssertionError, no definition covering the column
    xml = "<table:table-column/>" + table_row(text_cell("a"))  # as LibreOffice writes it
    path = ods_with_sheet(tmp_path / "one.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    s["C1"].value = "c"
    s.delete_column(col)
    r.save()
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == expected


def _widths(sheet):
    styles = [sheet.column_style(col) for col in range(sheet.n_cols)]
    return [style.width if style else None for style in styles]


@pytest.mark.parametrize(
    ("edit", "widths"),
    [
        pytest.param(lambda s: None, ["1cm", "2cm", "3cm"], id="read"),
        pytest.param(lambda s: s.delete_column(1), ["1cm", "3cm"], id="delete_column(1)"),
        pytest.param(lambda s: s.delete_column(0), ["2cm", "3cm"], id="delete_column(0)"),
        pytest.param(lambda s: s.insert_columns(1), ["1cm", "2cm", "2cm", "3cm"], id="insert_columns(1)"),
        pytest.param(lambda s: s.insert_columns(0), ["1cm", "1cm", "2cm", "3cm"], id="insert_columns(0)"),
    ],
)
def test_the_widths_of_print_title_columns_stay_with_their_columns(tmp_path, edit, widths):
    # regression: the grouped definition was skipped, and every column took
    # the width of the one after it
    path = ods_with_sheet(tmp_path / "titles.ods", PRINT_TITLE_XML, PRINT_TITLE_WIDTHS_XML)
    r = ODSReader(path)
    edit(r.sheet("Sheet1"))
    assert _widths(r.sheet("Sheet1")) == widths
    r.save()
    assert _widths(ODSReader(path).sheet("Sheet1")) == widths
    # an emptied group goes: ODF wants one definition in it at least
    for group in saved_table(path).find_all("table:table-header-columns"):
        assert group.find("table:table-column") is not None


@pytest.mark.parametrize(
    ("edit", "widths"),
    [
        pytest.param(lambda s: s.delete_column(1), ["1cm", "3cm"], id="delete_column(1)"),
        pytest.param(lambda s: s.insert_columns(1), ["1cm", "1cm", "1cm", "3cm"], id="insert_columns(1)"),
    ],
)
def test_a_repeated_print_title_definition_is_split_where_it_stands(tmp_path, edit, widths):
    # A and B print titles, one definition repeated twice
    xml = (
        '<table:table-header-columns><table:table-column table:style-name="w1"'
        ' table:number-columns-repeated="2"/></table:table-header-columns>'
        '<table:table-column table:style-name="w3"/>'
        + table_row(text_cell("a"), text_cell("b"), text_cell("c"))
    )
    path = ods_with_sheet(tmp_path / "titles.ods", xml, PRINT_TITLE_WIDTHS_XML)
    r = ODSReader(path)
    edit(r.sheet("Sheet1"))
    r.save()
    assert _widths(ODSReader(path).sheet("Sheet1")) == widths


def test_a_full_width_sheet_with_print_titles_keeps_its_width(tmp_path):
    # the print title's definition counts in the sheet's full width
    xml = (
        '<table:table-header-columns><table:table-column table:style-name="w1"/>'
        "</table:table-header-columns>"
        '<table:table-column table:style-name="w3" table:number-columns-repeated="16383"/>'
        + table_row(text_cell("a"), text_cell("b"))
    )
    path = ods_with_sheet(tmp_path / "titles.ods", xml, PRINT_TITLE_WIDTHS_XML)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_columns(1, 2)
    r.save()
    definitions = saved_table(path).find_all("table:table-column")
    assert sum(_repeat(d, "table:number-columns-repeated") for d in definitions) == 16_384
