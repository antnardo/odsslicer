"""Inserting and deleting rows and columns."""

import datetime as dt

import pytest

from conftest import (
    _FORMATTED_PAST_DATA_XML,
    _YELLOW_XML,
    FIXTURES_DIR,
    _defined_extent,
    _is_shape,
    _is_yellow,
    _repeat,
    addresses_holding,
    cells_with_content,
    empty_cells,
    note_cell,
    ods_with_sheet,
    saved_table,
    shape_cell,
    table_row,
    text_cell,
)
from odsslicer import ODSReader, string_address
from odsslicer.constants import TAG_CELL


@pytest.mark.parametrize(
    ("col", "expected"),
    [(0, {"A1": "b"}), (1, {"A1": "a", "A7": "end"})],
)
def test_deleting_a_column_through_a_run_of_repeated_cells(repeated_run_ods, col, expected):
    # regression: ValueError, the run's repeated cell being split once per
    # row of the run - the second time once it had left the tree
    r = ODSReader(repeated_run_ods)
    r.sheet("Sheet1").delete_column(col)
    r.save()
    assert cells_with_content(ODSReader(repeated_run_ods).sheet("Sheet1")) == expected


def test_deleting_a_column_of_a_libreoffice_sheet_with_repeated_rows(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet2Repeat")
    s.delete_column(2)
    out = tmp_path / "out.ods"
    writable_reader.save(out)
    reread = ODSReader(out).sheet("Sheet2Repeat")
    assert reread.size == (9, 5)
    ones = {f"{col}{r}": 1.0 for col in "ABC" for r in (1, 2)}
    assert cells_with_content(reread) == {**ones, "E9": 5.0}


# edit -> where a shape anchored in B2, inside the data, ends up. LibreOffice
# keeps it at the same address when the row or the column it is anchored in
# goes, rather than deleting it with them (issue #24).
_IN_GRID_EDITS = {
    "delete_rows([1]) (its own row)": (lambda s: s.delete_rows([1]), "B2"),
    "delete_rows([0, 1]) (its row among others)": (lambda s: s.delete_rows([0, 1]), "B1"),
    "delete_column(1) (its own column)": (lambda s: s.delete_column(1), "B2"),
    "delete_rows([2]) (another row)": (lambda s: s.delete_rows([2]), "B2"),
    "delete_column(0) (another column)": (lambda s: s.delete_column(0), "A2"),
}


@pytest.mark.parametrize("edit", list(_IN_GRID_EDITS))
def test_a_shape_inside_the_data_survives_the_deletion_of_its_row_or_column(tmp_path, edit):
    # regression (issue #24): a shape, a chart or an image is a child of the
    # cell it is anchored to, so deleting that cell's row or column deleted it
    # with them - a report template lost its chart on a plain delete_rows
    change, shape_at = _IN_GRID_EDITS[edit]
    xml = '<table:table-column table:number-columns-repeated="3"/>' + "".join(
        table_row(text_cell(str(v)), shape_cell("Box") if v == 2 else empty_cells(), empty_cells())
        for v in (1, 2, 3)
    )
    path = ods_with_sheet(tmp_path / "shape-in-grid.ods", xml)
    r = ODSReader(path)
    sheet = r.sheet("Sheet1")
    assert addresses_holding(saved_table(path), _is_shape) == ["B2"]
    change(sheet)
    r.save()
    assert addresses_holding(saved_table(path), _is_shape) == [shape_at]


@pytest.mark.parametrize("row", [0, 100, 200])
def test_inserting_rows_never_places_a_row_by_position(positional, row):
    # regression: each new row went in with bs4's insert_before or
    # insert_after, whose parent.index() scans the table from its first row -
    # insert_rows(n, n) on n rows took 8.2 s at 20,000 rows, not 0.25 s; 0.15.0
    # inserted them by an index found once, which lxml's insert() walks to
    sheet = ODSReader.new().sheet("Sheet1")
    sheet[0:200, 0].value = [[float(i)] for i in range(200)]
    positional.clear()
    sheet.insert_rows(row, 200)
    assert "table-row" not in positional
    values = [float(i) for i in range(200)]
    assert sheet.to_list() == [[v] for v in values[:row] + [None] * 200 + values[row:]]


@pytest.mark.parametrize("col", [0, 50, 100])
def test_inserting_columns_never_places_a_cell_by_position(positional, col):
    # same within each row, one scan per new cell: insert_columns(1000, 1000)
    # on 200 rows of 1,000 columns took 6.7 s, not 2.9 s
    sheet = ODSReader.new().sheet("Sheet1")
    sheet[0:20, 0:100].value = [[float(c) for c in range(100)] for _ in range(20)]
    positional.clear()
    sheet.insert_columns(col, 100)
    assert "table-cell" not in positional
    values = [float(c) for c in range(100)]
    assert sheet.to_list() == [values[:col] + [None] * 100 + values[col:]] * 20


def test_insertions_keep_a_full_size_sheet_full_size(tmp_path):
    # rows and columns padded to 1,048,576 x 16,384 as LibreOffice and Excel
    # write them: the rows past the grid used to be skipped by column
    # insertions, and deleted by row insertions
    xml = (
        '<table:table-column table:number-columns-repeated="16384"/>'
        + table_row(text_cell("a"), empty_cells(16383))
        + table_row(empty_cells(16384), repeat=1_048_574)
        + table_row(empty_cells(16384))
    )
    path = ods_with_sheet(tmp_path / "full.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    s.insert_rows(0, 2)
    s["B10"].value = "b"  # rows 4 to 10 taken back from the filler, full width
    s.insert_columns(0, 3)
    r.save()
    assert cells_with_content(ODSReader(path).sheet("Sheet1")) == {"D3": "a", "E10": "b"}
    rows = saved_table(path).find_all("table:table-row")
    assert sum(_repeat(row, "table:number-rows-repeated") for row in rows) == 1_048_576
    widths = [
        sum(_repeat(c, "table:number-columns-repeated") for c in row.find_all(TAG_CELL, recursive=False))
        for row in rows
    ]
    # the rows taken back into the grid, from the filler, are full width too
    assert widths[3:] == [16_384] * (len(rows) - 3)


def test_inserting_columns_keeps_the_formatted_cells_past_the_data(tmp_path):
    # far from the sheet's maximum width, the rows give nothing back
    path = ods_with_sheet(tmp_path / "formatted.ods", _FORMATTED_PAST_DATA_XML, _YELLOW_XML)
    r = ODSReader(path)
    r.sheet("Sheet1").insert_columns(0, 2)
    r.save()
    yellow = addresses_holding(saved_table(path), _is_yellow)
    # D:R moved to F:T, on both rows
    assert yellow == [string_address(r, c) for r in (0, 1) for c in range(5, 20)]


def test_inserting_a_column_into_a_row_as_wide_as_the_sheet_keeps_the_grid(tmp_path):
    # a note in XFD, the last column: nothing lies past the grid to give
    # back, and the grid's own cells must keep their elements
    xml = '<table:table-column table:number-columns-repeated="16384"/>' + table_row(
        text_cell("a"), empty_cells(16382), note_cell("last column")
    )
    path = ods_with_sheet(tmp_path / "wide.ods", xml)
    r = ODSReader(path)
    s = r.sheet("Sheet1")
    assert s.size == (1, 16384)
    s.insert_columns(0)
    r.save()
    reread = ODSReader(path).sheet("Sheet1")
    assert reread.size == s.size == (1, 16385)
    assert reread[0, 1].value == "a"
    assert reread[0, 16384].comment.text == "last column"


# ---------------------------------------------------------------------------
# Sheet.delete_row / delete_column / ODSReader.delete_sheet
# ---------------------------------------------------------------------------


class TestDelete:
    """`Sheet.delete_row`, `delete_rows` and `delete_column`."""

    def test_a_row_shifts_everything_below_up(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        before = [s[i, 0].value for i in range(s.n_rows)]
        n_rows_before = s.n_rows
        # row 7 (the date) isn't referenced by A5's SUM(A2:A3) formula, so this
        # exercises the plain row-shift in isolation - see the dedicated
        # "adjust formulas on delete" tests below for the formula-adjustment
        # side effect itself
        s.delete_row(7)
        after = [s[i, 0].value for i in range(s.n_rows)]
        assert s.n_rows == n_rows_before - 1
        assert s.size == (n_rows_before - 1, s.n_cols)
        assert after == before[:7] + before[8:]

    def test_a_column_shifts_everything_right_of_it_left(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n_cols_before = s.n_cols
        assert s[0, 1].value == "seconde colonne"
        s.delete_column(0)
        assert s.n_cols == n_cols_before - 1
        assert s[0, 0].value == "seconde colonne"

    def test_a_batch_matches_sequential_deletes(self, writable_reader, test_ods_path):
        from odsslicer import ODSReader

        s = writable_reader.sheet("Sheet1")
        s["C5"].formula = "A6+A8"
        s.delete_rows([1, 3, 6])

        sequential = ODSReader(test_ods_path)
        s2 = sequential.sheet("Sheet1")
        s2["C5"].formula = "A6+A8"
        for row in [6, 3, 1]:  # same set, one by one, bottom-up
            s2.delete_row(row)

        assert s.size == s2.size
        assert [c.value for c in s[0]] == [c.value for c in s2[0]]
        assert s["C3"].formula_friendly == s2["C3"].formula_friendly == "=A4+A5"

    def test_duplicate_rows_are_ignored(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n = s.n_rows
        s.delete_rows([2, 2, 2])
        assert s.n_rows == n - 1

    def test_no_row_is_a_no_op(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n = s.n_rows
        s.delete_rows([])
        assert s.n_rows == n

    def test_a_row_out_of_range_removes_nothing(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n = s.n_rows
        with pytest.raises(IndexError):
            s.delete_rows([1, 999])
        assert s.n_rows == n  # atomic: the valid index was not removed either

    def test_rows_through_merges_leave_no_broken_merge(self, writable_reader):
        s = writable_reader.sheet("SheetFusion")
        s.delete_rows([0, 3])  # intersects the A1:C1 and A3:A5 merges
        for row in s.rows:
            for cell in row:
                if cell.is_merged:
                    assert cell.merge_master is not None  # any surviving merge is coherent

    def test_a_row_out_of_range_raises(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        with pytest.raises(IndexError):
            s.delete_row(999)

    def test_a_column_out_of_range_raises(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        with pytest.raises(IndexError):
            s.delete_column(999)

    def test_a_row_through_a_merge_unmerges_only_that_merge(self, writable_reader):
        # A6:D7 and A8:D9 are two separate rectangular merges in SheetFusion -
        # deleting row 5 ("A6", the first merge's master row) must dissolve
        # only that one, leaving the untouched A8:D9 merge intact (just
        # shifted up by one row, to A7:D8)
        s = writable_reader.sheet("SheetFusion")
        assert s["A6"].merge_span == (2, 4)
        assert s["A8"].merge_span == (2, 4)
        s.delete_row(5)
        assert s["A7"].is_merge_master
        assert s["A7"].merge_span == (2, 4)
        # the two other, untouched merges (A1:C1 and A3:A5, 3 cells each) plus
        # the surviving, shifted A7:D8 (8 cells) - nothing from the dissolved
        # A6:D7 merge left over
        merged_count = sum(cell.is_merged for row in s.rows for cell in row)
        assert merged_count == 3 + 3 + 8

    def test_a_column_through_a_merge_unmerges_it_first(self, writable_reader):
        s = writable_reader.sheet("SheetFusion")
        s.delete_column(0)  # column A carries the master of every merge in this fixture
        for row in s.rows:
            for cell in row:
                assert not cell.is_merged

    def test_deleted_rows_and_columns_stay_deleted_after_a_save(self, writable_reader, tmp_path):
        s = writable_reader.sheet("Sheet1")
        s.delete_row(1)  # drops the row holding 3.4
        s.delete_column(1)  # drops "seconde colonne" - keeps col 0's real values
        # in every remaining row, so none of them ends up empty and gets
        # trimmed as a trailing blank row on reload (see load()'s cleanup)
        out = tmp_path / "out.ods"
        writable_reader.save(out)

        reread = ODSReader(out).sheet("Sheet1")
        assert reread.size == s.size
        assert reread[0, 0].value == "texte simple"
        assert reread[s.n_rows - 1, 0].value == dt.time(15, 0)


# ---------------------------------------------------------------------------
# Sheet.insert_rows / insert_columns
# ---------------------------------------------------------------------------


class TestInsert:
    """`Sheet.insert_row`, `insert_rows`, `insert_column` and `insert_columns`."""

    def test_rows_shift_everything_below_down(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        before = [s[i, 0].value for i in range(s.n_rows)]
        n_rows, n_cols = s.size
        # below A5's SUM(A2:A3), so no formula gets rewritten: this exercises the
        # plain shift - see the formula tests below for the rest
        s.insert_rows(5, 3)
        assert s.size == (n_rows + 3, n_cols)
        assert [s[i, 0].value for i in range(s.n_rows)] == before[:5] + [None] * 3 + before[5:]

    def test_insert_row_inserts_a_single_row(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n_rows = s.n_rows
        s.insert_row(0)
        assert s.n_rows == n_rows + 1
        assert s[0, 0].value is None
        assert s[1, 0].value == "texte simple"

    def test_rows_at_the_end_are_appended(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n_rows = s.n_rows
        s.insert_rows(n_rows, 2)
        assert s.n_rows == n_rows + 2
        s[n_rows + 1, 0].value = "appended"
        assert s[n_rows + 1, 0].value == "appended"

    def test_columns_shift_everything_right(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n_rows, n_cols = s.size
        s.insert_columns(1, 2)
        assert s.size == (n_rows, n_cols + 2)
        assert s[0, :4].to_list() == ["texte simple", None, None, "seconde colonne"]
        assert all(len(row) == n_cols + 2 for row in s.rows)

    def test_a_column_at_the_end_is_appended(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        n_cols = s.n_cols
        s.insert_column(n_cols)
        assert s.n_cols == n_cols + 1
        assert s[0, :3].to_list() == ["texte simple", "seconde colonne", None]

    @pytest.mark.parametrize("method", ["insert_rows", "insert_columns"])
    @pytest.mark.parametrize("position", [-1, 999])
    def test_an_index_out_of_range_raises(self, writable_reader, method, position):
        s = writable_reader.sheet("Sheet1")
        with pytest.raises(IndexError):
            getattr(s, method)(position)

    @pytest.mark.parametrize("method", ["insert_rows", "insert_columns"])
    def test_a_count_below_one_raises(self, writable_reader, method):
        s = writable_reader.sheet("Sheet1")
        with pytest.raises(ValueError):
            getattr(s, method)(0, 0)

    def test_rows_between_repeated_rows_keep_them_independent(self, writable_reader):
        # rows 0 and 1 share one <table:table-row table:number-rows-repeated="2">
        s = writable_reader.sheet("Sheet2Repeat")
        s.insert_row(1)
        assert s[0, :4].to_list() == [1.0] * 4
        assert s[1, :4].to_list() == [None] * 4
        assert s[2, :4].to_list() == [1.0] * 4
        s[2, 0].value = 9.0
        assert s[0, 0].value == 1.0

    def test_columns_through_repeated_rows_give_each_row_its_own_cells(self, writable_reader, tmp_path):
        # rows 0-1 are one repeated row of repeated cells: the new cells must go
        # into each logical row exactly once, and stay independent
        s = writable_reader.sheet("Sheet2Repeat")
        s.insert_column(2)
        assert s[0, :5].to_list() == [1.0, 1.0, None, 1.0, 1.0]
        s[0, 2].value = "new"
        assert s[1, 2].value is None
        out = tmp_path / "out.ods"
        writable_reader.save(out)
        reread = ODSReader(out).sheet("Sheet2Repeat")
        assert reread[0, :5].to_list() == [1.0, 1.0, "new", 1.0, 1.0]
        assert reread[1, :5].to_list() == [1.0, 1.0, None, 1.0, 1.0]

    @pytest.mark.parametrize(
        ("at", "master_row", "span"),
        [
            (3, 2, (4, 1)),  # strictly inside A3:A5 (rows 2-4): the merge grows
            (2, 3, (3, 1)),  # at its first row: the merge moves down whole
            (5, 2, (3, 1)),  # just below its last row: untouched
        ],
    )
    def test_a_row_moves_or_grows_a_merge(self, writable_reader, at, master_row, span):
        s = writable_reader.sheet("SheetFusion")
        assert s["A3"].merge_span == (3, 1)
        s.insert_row(at)
        assert s[master_row, 0].merge_span == span
        assert all(s[r, 0].is_covered for r in range(master_row + 1, master_row + span[0]))
        assert not s[master_row + span[0], 0].is_covered

    def test_columns_inside_a_merge_grow_it(self, writable_reader, tmp_path):
        s = writable_reader.sheet("SheetFusion")
        assert s["A1"].merge_span == (1, 3)
        s.insert_columns(1, 2)
        assert s["A1"].merge_span == (1, 5)
        assert all(s[0, c].is_covered for c in range(1, 5))
        out = tmp_path / "out.ods"
        writable_reader.save(out)
        assert ODSReader(out).sheet("SheetFusion")["A1"].merge_span == (1, 5)

    def test_inserted_rows_and_columns_read_back_after_a_save(self, writable_reader, tmp_path):
        s = writable_reader.sheet("Sheet1")
        s.insert_rows(1, 2)
        s.insert_column(0)
        s[1, 0].value = "inserted"
        out = tmp_path / "out.ods"
        writable_reader.save(out)
        reread = ODSReader(out).sheet("Sheet1")
        assert reread.size == s.size
        assert reread[0, 1].value == "texte simple"
        assert reread[1, 0].value == "inserted"
        assert reread[3, 1].value == 3.4
        assert reread["B7"].formula_friendly == "=SUM(B4:B5)"


def test_insertions_stay_within_the_applications_grid(tmp_path):
    # LibreOffice declares its full 16,384 x 1,048,576 grid through trailing
    # filler rows/columns: insertions must not push the document past it
    table = ODSReader(FIXTURES_DIR / "wild" / "libreoffice26_linux_streets.ods")
    s = table.sheet("Feuille1")
    rows_before, cols_before = _defined_extent(s)
    assert (rows_before, cols_before) == (1048576, 16384)
    first_values = [s[i, 0].value for i in range(2)]
    s.insert_rows(1, 5)
    s.insert_columns(1, 2)
    rows_after, cols_after = _defined_extent(s)
    assert rows_after <= rows_before
    assert cols_after == cols_before
    out = tmp_path / "out.ods"
    table.save(out)
    reread = ODSReader(out).sheet("Feuille1")
    assert reread.size == s.size
    assert reread[0, 0].value == first_values[0]
    assert reread[6, 0].value == first_values[1]
