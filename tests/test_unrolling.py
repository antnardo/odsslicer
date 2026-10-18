"""Giving a position of the grid an element of its own before it is written to."""

import pytest

from conftest import cells_with_content, number_cell, ods_with_sheet, table_row, text_cell
from odsslicer import ODSReader


def test_write_column_repeated_cell_splits_the_block(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    assert s.size == (9, 6)
    s["B1"].value = 999
    assert s["B1"].value == 999
    # siblings that shared the same compressed XML element keep their original value
    assert s["A1"].value == 1.0
    assert s["C1"].value == 1.0
    assert s["D1"].value == 1.0
    # a different row using its own, separate col-repeat block is unaffected
    assert s["A2"].value == 1.0 and s["B2"].value == 1.0
    assert s.size == (9, 6)


def test_write_row_and_column_repeated_cell_splits_both_layers(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["C5"].value = 42
    assert s["C5"].value == 42
    # rest of row 5 preserved
    for addr in ("A5", "B5", "D5", "E5", "F5"):
        assert s[addr].value is None
    # other rows that were part of the same repeated-row block are untouched
    for addr in ("A3", "B3", "A4", "B4", "A6", "A7", "A8"):
        assert s[addr].value is None
    assert s.size == (9, 6)


def test_write_already_individual_cell_is_a_no_op_split(writable_reader):
    # F9 has no repeat/merge attributes at all: baseline sanity check
    s = writable_reader.sheet("Sheet2Repeat")
    assert s["F9"].value == 5.0
    s["F9"].value = 6.0
    assert s["F9"].value == 6.0


def test_writing_cells_of_one_repeated_block_in_turn_keeps_each_value(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["A1"].value = "a"
    s["C1"].value = "c"
    assert s["A1"].value == "a" and s["C1"].value == "c"
    assert s["B1"].value == 1.0 and s["D1"].value == 1.0

    s["C3"].value = "x"
    s["C6"].value = "y"
    assert s["C3"].value == "x" and s["C6"].value == "y"
    assert s["A3"].value is None and s["A6"].value is None
    assert s.size == (9, 6)


# ---------------------------------------------------------------------------
# Runs of repeated rows (issue #5) - every row of a run points at the same
# `<table:table-row>` element, so reshaping one row has to reach them all.
# The damage only shows once saved: read back from the file.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "values", "written"),
    [
        # past the run's two columns: every row of the run got D = 1, F = 2
        ("D2:D4", [[1], [2], [3]], {"D2": 1.0, "D3": 2.0, "D4": 3.0}),
        ("C6", 4, {"C6": 4.0}),
        (
            "B3:E4",
            [[1, 2, 3, 4], [5, 6, 7, 8]],
            {"B3": 1, "C3": 2, "D3": 3, "E3": 4, "B4": 5, "C4": 6, "D4": 7, "E4": 8},
        ),
        # inside them, which already worked
        ("B2:B4", [[1], [2], [3]], {"B2": 1.0, "B3": 2.0, "B4": 3.0}),
    ],
)
def test_writing_into_a_run_of_repeated_rows_changes_only_the_written_cells(
    repeated_run_ods, address, values, written
):
    r = ODSReader(repeated_run_ods)
    r.sheet("Sheet1")[address].value = values
    r.save()
    s = ODSReader(repeated_run_ods).sheet("Sheet1")
    assert cells_with_content(s) == {"A1": "a", "B1": "b", "A7": "end", **written}


def test_writing_past_the_width_of_a_libreoffice_run_of_repeated_rows_keeps_the_other_cells(
    writable_reader, tmp_path
):
    # Sheet2Repeat, saved by LibreOffice: rows 3 to 8 are one element whose
    # cells are one repeated element too, and F9 comes after them
    s = writable_reader.sheet("Sheet2Repeat")
    before = cells_with_content(s)
    s["G3:G5"].value = [[1], [2], [3]]
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet2Repeat")
    assert reread.size == (9, 7)
    assert cells_with_content(reread) == {**before, "G3": 1.0, "G4": 2.0, "G5": 3.0}


# Splitting a repeated element places its copies next to it, wherever it lies
# and however many times it repeats. Regression: each copy went in with
# insert_after, a scan from the first child up to the copy before.


def test_writing_into_repeated_rows_never_places_a_row_by_position(tmp_path, positional):
    # writing one cell in a run of 20,000 repeated rows below 50,000 took 23 s, not 0.36 s
    xml = "".join(table_row(number_cell(i)) for i in range(100)) + table_row(text_cell("x"), repeat=200)
    sheet = ODSReader(ods_with_sheet(tmp_path / "rows.ods", xml)).sheet("Sheet1")
    positional.clear()
    sheet[200, 0].value = "y"
    assert "table-row" not in positional
    expected = [[float(i)] for i in range(100)] + [["x"]] * 200
    expected[200] = ["y"]
    assert sheet.to_list() == expected


def test_writing_into_repeated_cells_never_places_a_cell_by_position(tmp_path, positional):
    # in a cell repeated 16,384 times: 1.7 s, not 0.14 s
    repeated = (
        '<table:table-cell table:number-columns-repeated="200" office:value-type="string">'
        "<text:p>x</text:p></table:table-cell>"
    )
    xml = table_row(*(number_cell(c) for c in range(100)), repeated)
    sheet = ODSReader(ods_with_sheet(tmp_path / "cells.ods", xml)).sheet("Sheet1")
    positional.clear()
    sheet[0, 200].value = "y"
    assert "table-cell" not in positional
    expected = [float(c) for c in range(100)] + ["x"] * 200
    expected[200] = "y"
    assert sheet.to_list() == [expected]


def test_write_formula_on_a_repeated_cell_materializes_it(writable_reader):
    s = writable_reader.sheet("Sheet2Repeat")
    s["B1"].formula = "=[.A1]+1"
    assert s["B1"].is_formula
    assert s["A1"].value == 1.0  # sibling in the former repeat block untouched
