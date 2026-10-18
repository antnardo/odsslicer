"""Copying ranges and sorting rows."""

import datetime as dt

import pytest

from conftest import _blank_document
from odsslicer import ODSReader
from odsslicer.columns import _column_default_cell_style


def test_copying_a_time_of_day_onto_a_duration_keeps_it_a_time_of_day():
    # the copy takes the source's format before its value, which reads back
    # through it: the target's [HH]:MM:SS made 09:00 a duration
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1"].value = dt.time(9)
    s["B1"].value = dt.timedelta(hours=26)
    s.copy("A1", "B1")
    assert s["B1"].value == dt.time(9)


@pytest.mark.parametrize(
    "values",
    [
        # LibreOffice saves a date-time falling on midnight as a bare date
        [
            dt.datetime(2023, 11, 29, 13),
            dt.date(2023, 11, 30),
            dt.datetime(2023, 11, 30, 8),
        ],
        [dt.timedelta(hours=-1), dt.time(9), dt.timedelta(hours=26)],
    ],
)
def test_sort_orders_the_types_a_date_or_time_column_mixes(values):
    # Python refuses to order a date and a date-time, or a time and a timedelta
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1:A3"].value = [[values[2]], [values[0]], [values[1]]]
    s.sort("A1:A3", by=0)
    assert [s[row, 0].value for row in range(3)] == values


def test_copy_carries_date_times_and_durations(writable_reader):
    # writing either type raised TypeError, so Sheet.copy could not carry them
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = dt.datetime(2023, 11, 30, 13)
    s["A2"].value = dt.timedelta(hours=128, minutes=45)
    s.copy("A1:A2", "C1")
    assert s["C1"].value == dt.datetime(2023, 11, 30, 13)
    assert s["C2"].value == dt.timedelta(hours=128, minutes=45)


def test_a_copy_carries_the_format_a_cell_takes_from_its_column(formatted_columns):
    # the copy used to take the source's own style only - none - which then
    # wiped the date format the target had just been given
    s = formatted_columns.sheet("Sheet1")
    s["B2"].value = dt.date(2022, 3, 7)
    s.copy("B2", "D2")
    assert s["D2"].style.number_format.name == "Ndmy"
    assert s["D2"].style.background_color == "#e2efda"


def test_sorting_a_formatted_column_gives_its_cells_no_style_of_their_own(
    formatted_columns,
):
    s = formatted_columns.sheet("Sheet1")
    s["B2:B4"].value = [[dt.date(2022, 3, day)] for day in (9, 7, 8)]
    s.sort("B2:B4", by=1)
    assert s["B2:B4"].to_list() == [[dt.date(2022, 3, day)] for day in (7, 8, 9)]
    assert all(s[r, 1].style_name is None for r in (1, 2, 3))


def test_sorting_across_formatted_columns_compares_each_cell_with_its_column(
    formatted_columns,
):
    # `sort` looks each column's default style up once: a cell with no style
    # of its own is compared with its own column's, and keeps none; a cell
    # whose style a row with none replaces takes that column's
    s = formatted_columns.sheet("Sheet1")
    s["A2:B4"].value = [[3.0, dt.date(2022, 3, 9)], [1.0, dt.date(2022, 3, 7)], [2.0, dt.date(2022, 3, 8)]]
    s["B2"].style.bold = True
    s.sort("A2:B4", by=0)
    assert s["A2:B4"].to_list() == [
        [1.0, dt.date(2022, 3, 7)],
        [2.0, dt.date(2022, 3, 8)],
        [3.0, dt.date(2022, 3, 9)],
    ]
    assert (
        s["B2"].style_name == _column_default_cell_style(s.table, 1) != _column_default_cell_style(s.table, 0)
    )
    assert s["B4"].style.bold
    assert [s[r, c].style_name for r, c in ((1, 0), (2, 0), (3, 0), (2, 1))] == [None] * 4


# ---------------------------------------------------------------------------
# Sheet.copy (copy and paste of cells and ranges)
# ---------------------------------------------------------------------------


def test_copying_a_single_cell_carries_its_value_and_style(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s.copy("A1", "C1")
    assert s["C1"].value == "texte simple"
    assert s["C1"].style.bold is True


def test_copy_a_range_preserves_shape(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.copy("A1:B2", "D5")
    assert s["D5"].value == s["A1"].value
    assert s["E5"].value == s["B1"].value
    assert s["D6"].value == s["A2"].value
    assert s["E6"].value == s["B2"].value


def test_copy_shifts_relative_formula_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "A2+A3"
    s.copy("C1", "E5")
    assert s["E5"].formula_friendly == "=C6+C7"


def test_copy_keeps_absolute_formula_references_in_place(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].formula = "$A$2+A3"
    s.copy("C1", "E5")
    assert s["E5"].formula_friendly == "=$A$2+C7"


def test_copy_grows_the_sheet_if_needed(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s.copy("A1:B2", "Z20")
    assert s.n_rows >= 21 and s.n_cols >= 27
    assert s["Z20"].value == s["A1"].value


def test_copy_is_safe_with_overlapping_source_and_dest(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["H1"].value = 1
    s["H2"].value = 2
    s["H3"].value = 3
    s.copy("H1:H3", "H2")
    assert [s[f"H{i}"].value for i in (1, 2, 3, 4)] == [1, 1, 2, 3]


def test_copy_an_empty_cell_clears_the_destination(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["C1"].value = "will be cleared"
    s.copy("Z1", "C1")  # Z1 is out of range -> empty
    assert s["C1"].value is None


def test_a_copy_reads_back_after_a_save(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].style.bold = True
    s["C1"].formula = "A2+A3"
    s.copy("A1:C1", "E5")

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert reread["E5"].value == "texte simple"
    assert reread["E5"].style.bold is True
    assert reread["G5"].formula_friendly == "=E6+E7"


def _fill_sort_table(s):
    s["A1"].value = "Charlie"
    s["B1"].value = 3.0
    s["C1"].formula = "B1*10"
    s["A2"].value = "Alice"
    s["B2"].value = 1.0
    s["C2"].formula = "B2*10"
    s["A3"].value = "Bob"
    s["B3"].value = None
    s["A4"].value = "Dana"
    s["B4"].value = 2.0
    s["C4"].formula = "B4*10"
    s["A1"].style.bold = True


# ---------------------------------------------------------------------------
# Sheet.sort
# ---------------------------------------------------------------------------


def test_sorting_by_a_column_orders_the_rows_ascending(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    assert [s[i, 0].value for i in range(4)] == ["Alice", "Dana", "Charlie", "Bob"]


def test_sort_none_always_sorts_last(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=False)
    # descending would put a "biggest" None first under naive reverse=True -
    # it must still sort last
    assert [s[i, 0].value for i in range(4)] == ["Charlie", "Dana", "Alice", "Bob"]


def test_sort_is_stable(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "first"
    s["B1"].value = 1.0
    s["A2"].value = "second"
    s["B2"].value = 1.0
    s["A3"].value = "third"
    s["B3"].value = 1.0
    s.sort("A1:B3", by=1)
    assert [s[i, 0].value for i in range(3)] == ["first", "second", "third"]


def test_sort_moves_style_with_its_row(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    assert s["A3"].value == "Charlie" and s["A3"].style.bold is True
    assert s["A1"].style.bold is False


def test_sort_shifts_same_row_formula_references(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    # Alice's row (was row 2, now row 1) keeps a formula referring to its
    # own (now relocated) row
    assert s["A1"].value == "Alice"
    assert s["C1"].formula_friendly == "=B1*10"


def test_sort_column_out_of_range_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    with pytest.raises(ValueError):
        s.sort("A1:C4", by=5)


def test_sorted_rows_read_back_after_a_save(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    _fill_sort_table(s)
    s.sort("A1:C4", by=1, ascending=True)
    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    assert [reread[i, 0].value for i in range(4)] == ["Alice", "Dana", "Charlie", "Bob"]
    assert reread["C1"].formula_friendly == "=B1*10"
