"""Sheet: indexing the grid, and the shape of what it gives past the data."""

import enum

import numpy as np
import pytest
from lxml import etree

from odsslicer import ArrayValues, ODSReader, Sheet


def test_text_and_numeric_indexes_give_the_same_cells(sheet1):
    assert sheet1["A1"] == sheet1[0, 0]
    assert sheet1["1"] == sheet1[0]
    assert sheet1["A"] == sheet1[:, 0]
    assert sheet1["A1:B3"] == sheet1[0:3, 0:2]


def test_empty_row_slice_is_empty(sheet1):
    assert len(sheet1[1:1]) == 0


def test_cell_out_of_range_is_empty_not_an_error(sheet1):
    c = sheet1["ZZZ100000"]
    assert c.value is None
    assert c.is_empty is True


# ---------------------------------------------------------------------------
# An empty sheet: every cell reads None, in the right shape
# ---------------------------------------------------------------------------


def test_every_cell_of_an_empty_sheet_reads_none(sheet_empty):
    assert sheet_empty["A1"].value is None
    assert sheet_empty["ZZ1"].value is None
    assert sheet_empty["ZZZ2222222"].value is None
    assert sheet_empty["B1"].value is None
    assert sheet_empty["A2"].value is None


def test_empty_sheet_ranges_have_correct_shape(sheet_empty):
    assert sheet_empty["A1:C1"].to_list() == [None] * 3
    assert sheet_empty["A1:C2"].to_list() == [[None] * 3, [None] * 3]
    assert sheet_empty[0:10:2, 0].to_list() == [[None] for _ in range(5)]
    assert sheet_empty[0:10:2, :2].to_list() == [[None, None] for _ in range(5)]
    assert sheet_empty[0, 0:5:2].to_list() == [None] * 3


# ---------------------------------------------------------------------------
# Rows and columns past the data: blank cells, in the right shape
# ---------------------------------------------------------------------------


def test_row_past_the_data_is_blank(sheet1):
    row = sheet1[sheet1.n_rows]
    assert row.size == (sheet1.n_cols,)
    assert all(cell.is_empty for cell in row.array)


def test_column_past_the_data_is_blank(sheet1):
    col = sheet1[:, sheet1.n_cols]
    assert col.size == (sheet1.n_rows, 1)
    assert all(cell[0].is_empty for cell in col.array)


def test_slices_past_the_data_keep_their_length(sheet1):
    # regression of empty_row/empty_col, which 1.0 removed: a slice was
    # turned into a count and passed to range() as a stop, dropping one cell
    assert len(sheet1[sheet1.n_rows, 2:10]) == 8
    assert len(sheet1[2:10, sheet1.n_cols]) == 8


def _fake_table():
    """A bare `<table:table>`, all Sheet.__init__ reads once `load` is
    stubbed out."""
    return etree.fromstring(
        f'<table:table xmlns:table="{TABLE_NS}" table:name="Fake" table:style-name="st"/>'
    )


TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"


# ---------------------------------------------------------------------------
# The "rows of different lengths" warning (Sheet.__init__)
# ---------------------------------------------------------------------------


def test_ragged_rows_trigger_warning(monkeypatch, caplog):
    # regression: rows_len was a `map` object consumed twice (once by max(),
    # once by the warning check), so the warning never actually fired.
    # (The message goes through the "odsslicer" logger at WARNING level.)
    import logging

    monkeypatch.setattr(Sheet, "load", lambda self, table_bs: [[1, 2, 3], [1, 2]])
    with caplog.at_level(logging.WARNING, logger="odsslicer"):
        sheet = Sheet(_fake_table())
    assert sheet.size == (2, 3)
    assert any("same length" in r.message for r in caplog.records)


def test_uniform_rows_do_not_trigger_warning(monkeypatch, capsys):
    monkeypatch.setattr(Sheet, "load", lambda self, table_bs: [[1, 2], [3, 4]])
    sheet = Sheet(_fake_table())
    assert sheet.size == (2, 2)
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


@pytest.mark.parametrize(
    "index",
    [(slice(None), slice(None)), slice(None), (slice(0, 0), slice(0, 0))],
    ids=["[:, :]", "[:]", "[0:0, 0:0]"],
)
def test_reading_a_whole_empty_sheet_gives_an_empty_selection(reader, index):
    # regression: an empty list was taken for a single cell (dimension 0), so
    # reading every cell of a sheet with no rows - what a plain scan over a
    # workbook does - crashed with AttributeError on a real file
    s = reader.sheet("SheetEmpty")
    assert s.size == (0, 0)
    selection = s[index]
    assert selection.dimension == 1
    assert selection.size == (0,)
    assert selection.to_list() == []
    assert selection.to_numpy().shape == (0,)


def test_a_row_of_an_empty_sheet_keeps_its_two_dimensions(reader):
    # [[]] is a one-row selection of zero cells, not a zero-row one

    selection = ArrayValues([[]])
    assert selection.dimension == 2
    assert selection.size == (1, 0)
    assert selection.to_list() == [[]]


class _Column(enum.IntEnum):
    B = 1


class TestIndexing:
    """What `Sheet.__getitem__` takes for a position."""

    @pytest.fixture()
    def sheet(self):
        sheet = ODSReader.new().sheet("Sheet1")
        sheet["B1"].value = 7
        sheet["B2"].value = 8
        return sheet

    @pytest.mark.parametrize(
        "key",
        [(0, 1), (np.int64(0), np.int64(1)), (np.int32(0), 1), (0, _Column.B), (np.uint8(0), np.intp(1))],
        ids=["int", "numpy-int64", "numpy-int32", "int-enum", "numpy-unsigned"],
    )
    def test_an_integer_of_any_kind_gives_the_cell(self, sheet, key):
        assert sheet[key].value == 7

    @pytest.mark.parametrize("row", [1, np.int64(1)], ids=["int", "numpy-int64"])
    def test_an_integer_of_any_kind_gives_the_row(self, sheet, row):
        assert sheet[row].to_list() == [None, 8]

    def test_numpy_slice_bounds_give_the_cells(self, sheet):
        assert sheet[np.int64(0) : np.int64(2), np.int64(1)].to_list() == [[7], [8]]

    @pytest.mark.parametrize(
        "key",
        [(True, 1), (0, False), True, (0, 1.5), (0, "B")],
        ids=["bool-row", "bool-col", "bool", "float", "str-col"],
    )
    def test_what_is_no_position_is_refused(self, sheet, key):
        with pytest.raises(ValueError, match="cannot index a sheet"):
            sheet[key]
