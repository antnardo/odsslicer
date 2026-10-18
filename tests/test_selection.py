"""ArrayValues: a selection of cells, read and written as one."""

import datetime as dt

import numpy as np
import pytest

from conftest import _blank_document
from odsslicer import ODSReader

# ---------------------------------------------------------------------------
# ArrayValues: dimensions, to_list / to_numpy / to_vector, equality
# ---------------------------------------------------------------------------


def test_array_values_dimension_and_size(sheet1):
    row = sheet1["A1:B1"]
    assert row.dimension == 1
    assert row.size == (2,)

    box = sheet1["A1:B3"]
    assert box.dimension == 2
    assert box.size == (3, 2)


def test_array_values_to_list(sheet1):
    assert sheet1["A1:B1"].to_list() == ["texte simple", "seconde colonne"]


def test_array_values_to_numpy(sheet_repeat):
    arr = sheet_repeat["A1:D2"].to_numpy()
    assert arr.tolist() == [[1, 1, 1, 1], [1, 1, 1, 1]]


def test_array_values_to_vector(sheet1):
    column = sheet1[0:2, 0]  # (2 x 1) shape
    vector = column.to_vector()
    assert vector.to_list() == ["texte simple", 3.4]


def test_array_values_equality_compares_values_not_identity(sheet1):
    assert sheet1["A1:B1"] == sheet1[0, 0:2]


def test_writing_to_an_empty_selection_does_nothing(writable_reader):
    writable_reader.sheet("SheetEmpty")[:, :].value = 1  # no cell to write to


def test_a_numpy_array_or_scalar_fills_a_range(tmp_path):
    # regression: TypeError, 'numpy.int64' object is not iterable for the
    # scalar, and for each of the array's integers
    r = ODSReader.new()
    s = r.sheet("Sheet1")
    s["A1:B2"].value = np.array([[1250, 38], [1320, 41]])
    s["C1:D2"].value = np.int64(0)
    s["E1:E2"].value = np.array(5)  # an array of no dimension, a scalar
    r.save(tmp_path / "out.ods")
    back = ODSReader(tmp_path / "out.ods").sheet("Sheet1")
    assert back["A1:E2"].to_list() == [
        [1250.0, 38.0, 0.0, 0.0, 5.0],
        [1320.0, 41.0, 0.0, 0.0, 5.0],
    ]


def test_a_duration_is_written_to_every_cell_of_a_range():
    r = _blank_document()
    s = r.sheet("Sheet1")
    s["A1:A3"].value = dt.timedelta(hours=30)
    assert s["A1:A3"].to_list() == [[dt.timedelta(hours=30)]] * 3


# ---------------------------------------------------------------------------
# Writing to selections of several cells (ArrayValues.value / .formula)
# ---------------------------------------------------------------------------


def test_slice_value_broadcasts_a_scalar(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].value = 5
    assert s["A30:C30"].to_list() == [5, 5, 5]


def test_slice_value_broadcasts_a_string_without_splitting_it(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].value = "hi"
    assert s["A30:C30"].to_list() == ["hi", "hi", "hi"]


def test_slice_value_assigns_element_wise_for_a_1d_row(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].value = [7, 8, 9]
    assert s["A30:C30"].to_list() == [7, 8, 9]


def test_slice_value_assigns_element_wise_for_a_2d_block(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A40:B41"].value = [[1, 2], [3, 4]]
    assert s["A40:B41"].to_list() == [[1, 2], [3, 4]]


def test_slice_value_element_wise_shape_mismatch_raises(writable_reader):
    s = writable_reader.sheet("Sheet1")
    with pytest.raises(ValueError):
        s["A30:C30"].value = [1, 2]


def test_slice_formula_broadcasts_the_same_pattern(writable_reader):
    s = writable_reader.sheet("Sheet1")
    s["A30:C30"].formula = "SUM(B1:B10)"
    for addr in ("A30", "B30", "C30"):
        assert s[addr].formula == "of:=SUM([.B1:.B10])"


def test_slice_formula_expands_placeholders_per_cell(writable_reader):
    # the user's own motivating example: A2 references A1, A3 references A2, etc.
    s = writable_reader.sheet("Sheet1")
    s["A2:A6"].formula = "$A{r-1}+1"
    for row in range(1, 6):  # 0-indexed rows 1..5 == A2..A6
        assert s[row, 0].formula == f"of:=[.$A{row}]+1"


def test_save_round_trip_after_slice_writes(writable_reader, tmp_path):
    # regression: a formula-only cell (no cached value/text) was wrongly
    # treated as "empty" by Cell.is_empty, so if it ended up as the sheet's
    # last row, load()'s "trim a trailing empty row" cleanup silently
    # dropped it on the next save/reload.
    s = writable_reader.sheet("Sheet1")
    s["A20:A25"].formula = "$A{r-1}+1"
    s["C1:C3"].value = [[1], [2], [3]]

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out).sheet("Sheet1")
    for row in range(19, 25):
        assert reread[row, 0].formula == f"of:=[.$A{row}]+1"
    assert reread["C1:C3"].to_list() == [[1], [2], [3]]
