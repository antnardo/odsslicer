"""The sheet's extent: growing the grid to a position, and the rows the file
holds below it.

The loader leaves the padding around the data out of the grid, and in the
file (see `loading`): a last row with no content, the filler declaring the
sheet's full height, the cells past the last column. Growing the grid takes
them back before making anything new, so that what they hold - a chart
anchored below the data, a formatted cell - stays where it was (issues #6,
#10); and every structural edit has to carry them along, as LibreOffice
moves a chart anchored there. Both are the one question of what lies past
the grid, and `_Growth` holds it; the edits of `structure` and the writes of
`Cell` and `merges` grow the grid through `Sheet.grow_to`.

Nothing here is public (`__all__` is empty): its API is `Sheet.grow_to`.
"""

from typing import TYPE_CHECKING

from lxml import etree

from odsslicer.cell import Cell
from odsslicer.gridxml import (
    _FULL_HEIGHTS,
    _ROWS_REPEATED,
    _cells_between,
    _delete_cell_at,
    _empty_row,
    _give_back,
    _insert_cells_at,
    _repeat,
    _row_cells,
    _rows_after,
    _split_row_element,
)
from odsslicer.unrolling import _row_element, _runs_in
from odsslicer.xmltree import clone, insert_after

if TYPE_CHECKING:
    from odsslicer.sheet import Sheet

__all__: list[str] = []


class _Growth:
    """Growing `sheet`'s grid, and editing the rows its file holds below it."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet

    def grow_to(self, row: int, col: int) -> None:
        """`Sheet.grow_to`, which says what it does."""
        if col + 1 > self._sheet.n_cols:
            self._widen(col + 1)
        if row >= self._sheet.n_rows:
            self._take_back_rows(row + 1)

    def _widen(self, n_cols: int) -> None:
        """Widen every row of the grid to `n_cols` columns, each row element
        once - every row of a run points at the same new cells, as `load`
        would have them (issue #5) - with the cells it holds past the grid
        first (issue #6)."""
        sheet = self._sheet
        for run in _runs_in(sheet.rows):
            new_tags = _cells_between(_row_element(sheet.rows[run.start]), sheet.n_cols, n_cols)
            for r in run:
                sheet.rows[r].extend(
                    Cell(tag, row=r, col=c, sheet=sheet) for c, tag in enumerate(new_tags, start=sheet.n_cols)
                )
        sheet.n_cols = n_cols

    def _take_back_rows(self, n_rows: int) -> None:
        """Grow the grid to `n_rows` rows, first with the rows the file holds
        below it - what `load` left out: a last row with no content, the
        filler padding the sheet to its full height, the rows past it - then
        with new blank rows.

        Those rows keep what they hold: a chart or a shape anchored to one of
        their cells, a formatted cell. They used to be deleted instead, and a
        chart anchored below the data went as soon as the sheet grew (issue
        #10). A repeated element is split, as `load` splits a run, so that
        the grid takes only the rows it needs; each row is split at the
        grid's edge, as `load` splits the grid's, what lies past it staying
        in the file - a filler row is as wide as the sheet."""
        sheet = self._sheet
        previous = _row_element(sheet.rows[-1]) if sheet.n_rows else None
        for row_tag in self.rows_past_grid():
            if sheet.n_rows >= n_rows:
                return
            _split_row_element(row_tag, n_rows - sheet.n_rows)
            tags = _cells_between(row_tag, 0, sheet.n_cols)
            for _ in range(_repeat(row_tag, _ROWS_REPEATED)):
                sheet.rows.append(
                    [Cell(tag, row=sheet.n_rows, col=c, sheet=sheet) for c, tag in enumerate(tags)]
                )
                sheet.n_rows += 1
            previous = row_tag
        template = _empty_row(sheet.n_cols)
        # each new row right after the one before, never by index (see
        # `_insert_chain`)
        while sheet.n_rows < n_rows:
            new_row_tag = clone(template)
            if previous is None:
                sheet.table.append(new_row_tag)
            else:
                insert_after(previous, new_row_tag)
            previous = new_row_tag
            sheet.rows.append(
                [
                    Cell(cell_tag, row=sheet.n_rows, col=c, sheet=sheet)
                    for c, cell_tag in enumerate(_row_cells(new_row_tag))
                ]
            )
            sheet.n_rows += 1

    def rows_past_grid(self) -> "list[etree._Element]":
        """The `<table:table-row>` elements the file holds after the grid's
        last row, in order, grouped or not: what `load` left out below the
        grid."""
        sheet = self._sheet
        return _rows_after(_row_element(sheet.rows[-1]) if sheet.n_rows else None, sheet.table)

    def give_back_rows(self, count: int) -> None:
        """Keep the sheet's height after `count` rows were inserted, if it was
        padded to an application's full height (see `_FULL_HEIGHTS`), with
        the rows padding it below the grid (see `_give_back`)."""
        below = self.rows_past_grid()
        if self._sheet.n_rows + sum(_repeat(r, _ROWS_REPEATED) for r in below) - count in _FULL_HEIGHTS:
            _give_back(below, _ROWS_REPEATED, count)

    def insert_cells_past_grid(self, col: int, count: int) -> None:
        """Insert `count` blank cells before logical column `col` in every
        row the file holds below the grid, so that what those rows hold moves
        sideways with the columns of the grid, as LibreOffice moves a chart
        anchored there (issue #10)."""
        for row_tag in self.rows_past_grid():
            _insert_cells_at(row_tag, col, count)

    def delete_cells_past_grid(self, col: int) -> None:
        """Delete logical column `col` from every row the file holds below
        the grid - see `insert_cells_past_grid`, and `_delete_cell_at` for
        what becomes of a chart anchored there."""
        for row_tag in self.rows_past_grid():
            _delete_cell_at(row_tag, col)
