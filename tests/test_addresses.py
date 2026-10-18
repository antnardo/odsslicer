"""Addresses: column letters and numbers, and the text addresses `sheet[...]` reads."""

import re

import pytest

from odsslicer import string_address, string_to_col
from odsslicer.addresses import _parse_address

# ---------------------------------------------------------------------------
# _parse_address: "A1" / "A1:B3" / "A:B" / "1:2" -> an index or a slice
# ---------------------------------------------------------------------------


def test_address_simple_cell():
    assert _parse_address("A1", 1) == (0, 0)
    assert _parse_address("Z2", 1) == (1, 25)
    assert _parse_address("AA1", 1) == (0, 26)


def test_address_row_only():
    assert _parse_address("1", 1) == 0
    assert _parse_address("3", 1) == 2


def test_address_col_only():
    row, col = _parse_address("A", 5)
    assert col == 0
    assert (row.start, row.stop, row.step) == (None, 5, None)


def test_address_row_range():
    row, col = _parse_address("A1:A10", 1)
    assert (row.start, row.stop, row.step) == (0, 10, None)
    assert col == 0


def test_address_col_range():
    row, col = _parse_address("A1:C1", 1)
    assert row == 0
    assert (col.start, col.stop, col.step) == (0, 3, None)


def test_address_box_range():
    row, col = _parse_address("B2:C5", 1)
    assert (row.start, row.stop, row.step) == (1, 5, None)
    assert (col.start, col.stop, col.step) == (1, 3, None)


def test_address_single_cell_range_collapses():
    row, col = _parse_address("A1:A1", 1)
    assert row == 0 and col == 0


def test_address_rows_range_only():
    assert _parse_address("1:2", 1) == slice(0, 2)


@pytest.mark.parametrize(
    "bad, says",
    [
        ("1A", "is not a cell or range address"),
        ("A1=", "is not a cell or range address"),
        ("a1", "is not a cell or range address"),
        ("A1B2", "is not a cell or range address"),
        ("", "is not a cell or range address"),
        ("A1:", "is not a cell or range address"),
        ("A1:B2:C3", "is not a cell or range address"),
        ("A0", "rows are numbered from 1"),
        ("0", "rows are numbered from 1"),
        ("A1:B0", "rows are numbered from 1"),
        ("A:2", "a range joins two cells"),
        ("2:A", "a range joins two cells"),
        ("A1:B", "a range joins two cells"),
        ("A:B1", "a range joins two cells"),
        ("1:B2", "a range joins two cells"),
        ("B:A", "a range runs from its first row and column to its last"),
        ("3:1", "a range runs from its first row and column to its last"),
        ("B1:A2", "a range runs from its first row and column to its last"),
        ("A3:B1", "a range runs from its first row and column to its last"),
    ],
)
def test_address_invalid_raises_saying_what_is_wrong(bad, says):
    with pytest.raises(ValueError, match=re.escape(f"{bad!r}") + ".*" + re.escape(says)):
        _parse_address(bad, 1)


def test_address_single_column_and_row_ranges():
    assert _parse_address("A:A", 4) == (slice(4), slice(0, 1))
    assert _parse_address("2:2", 4) == slice(1, 2)


def test_sheet_indexed_with_a_malformed_address_raises_value_error(sheet1):
    with pytest.raises(ValueError, match="a range joins two cells"):
        sheet1["A1:B"]
    with pytest.raises(ValueError, match="cannot index a sheet with"):
        sheet1[1.5]


# ---------------------------------------------------------------------------
# string_address / string_to_col: index <-> column letters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "col, expected",
    [
        (0, "A"),
        (1, "B"),
        (25, "Z"),
        (26, "AA"),
        (27, "AB"),  # regression: used to give "BB"
        (51, "AZ"),  # regression: used to give "ZZ"
        (52, "BA"),  # regression: used to give "AA"
        (53, "BB"),
        (701, "ZZ"),
        (702, "AAA"),
    ],
)
def test_string_address_columns(col, expected):
    assert string_address(0, col) == f"{expected}1"


def test_string_address_row_is_one_indexed():
    assert string_address(0, 0) == "A1"
    assert string_address(9, 0) == "A10"


@pytest.mark.parametrize("col", [*range(60), 100, 300, 701, 702, 703, 728, 729, 1000])
def test_string_address_round_trips_through_string_to_col(col):
    letters = string_address(0, col)[:-1]
    assert string_to_col(letters) == col


def test_string_to_col_matches_docstring_examples():
    assert string_to_col("A") == 0
    assert string_to_col("Z") == 25
    assert string_to_col("AA") == 26
    assert string_to_col("AZ") == 51
    assert string_to_col("BA") == 52
