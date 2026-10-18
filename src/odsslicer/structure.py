"""Structural edits: rows and columns inserted and deleted, as a spreadsheet
inserts and deletes them - the grid, the XML, and everything that points at
the cells moving together.

An edit reaches more than the grid: the merged ranges it cuts through
(`merges`), what the file holds past the grid (`growth`), the column
definitions (`columns`), a chart anchored in a deleted row, and every
formula and drawing of the document (`references`). Each of those is
another module's; the order in which an edit asks them is this one's - the
charts read before anything changes, merges undone or grown, elements
unrolled before they are split, the references remapped once the grid is in
its new shape. `Sheet` held the four edits as methods of up to 15 branches;
`_StructureEdits` holds them as named steps.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from odsslicer.cell import Cell
from odsslicer.columns import _delete_column_definition, _insert_column_definitions
from odsslicer.formulas import _deletion_remap, _insertion_remap
from odsslicer.gridxml import (
    _COLUMNS_REPEATED,
    _cell_starting_at,
    _drawings_in,
    _empty_row,
    _give_back_cells,
    _insert_chain,
    _repeat,
    _rescue_drawings,
    _row_cells,
)
from odsslicer.growth import _Growth
from odsslicer.merges import _Merges
from odsslicer.references import _References
from odsslicer.unrolling import _row_element, _runs_in, _Unroller
from odsslicer.xmltree import clone, insert_before, new, remove

if TYPE_CHECKING:
    from lxml import etree

    from odsslicer.sheet import Sheet

__all__: list[str] = []


class _StructureEdits:
    """Inserting and deleting rows and columns of `sheet` (see the methods
    of `Sheet` of the same names, which say what each does)."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet
        self._references = _References(sheet.reader)
        self._merges = _Merges(sheet)
        self._unroller = _Unroller(sheet)
        self._growth = _Growth(sheet)

    # rows

    def delete_rows(self, rows: "Iterable[int]") -> None:
        sheet = self._sheet
        targets = sorted(set(rows))
        if not targets:
            return
        for row in targets:
            if row < 0 or row >= sheet.n_rows:
                raise IndexError(f"row {row} out of range (sheet has {sheet.n_rows} rows)")
        self._references.read_first()
        for row in targets:
            self._unmerge_row(row)
        for row in reversed(targets):  # bottom-up: earlier indexes stay valid
            self._delete_row(row)
        self._renumber_rows(targets[0])
        sheet.n_rows = len(sheet.rows)
        self._references.remap(sheet.name, _deletion_remap(deleted_rows=targets))

    def _unmerge_row(self, row: int) -> None:
        """Undo every merge intersecting `row` (master, covered, or entirely
        on another row but spanning through it)."""
        sheet = self._sheet
        c = 0
        while c < sheet.n_cols:  # an unmerge may widen the grid
            if sheet.rows[row][c].is_merged:
                self._merges.unmerge_at(row, c)
            c += 1

    def _delete_row(self, row: int) -> None:
        """Remove row `row` from the grid and the file, what is anchored in
        it moved to the row taking its place (`_rescue_row_drawings`)."""
        sheet = self._sheet
        self._unroller.row(row)
        # what is anchored in the row stays at its address, in the row
        # that takes its place - the one below, or the one above for the
        # last row of the sheet (issue #24)
        heir_row = row + 1 if row + 1 < len(sheet.rows) else row - 1
        if 0 <= heir_row < len(sheet.rows):
            self._rescue_row_drawings(row, heir_row)
        remove(_row_element(sheet.rows[row]))
        del sheet.rows[row]

    def _rescue_row_drawings(self, row: int, heir_row: int) -> None:
        """Move what is anchored in the cells of `row` into the same column
        of `heir_row` - and so for what is anchored past the grid's columns,
        in the cells the row element keeps beyond them: LibreOffice writes a
        chart there when the data stops short of it."""
        sheet = self._sheet
        for c, cell in enumerate(sheet.rows[row]):
            if _drawings_in(cell.cell):
                self._unroller.column(heir_row, c)
                _rescue_drawings(cell.cell, sheet.rows[heir_row][c].cell)
        start = 0
        for cell_tag in _row_cells(_row_element(sheet.rows[row])):
            if start >= sheet.n_cols and _drawings_in(cell_tag):
                self._unroller.row(heir_row)
                _rescue_drawings(cell_tag, _cell_starting_at(_row_element(sheet.rows[heir_row]), start))
            start += _repeat(cell_tag, _COLUMNS_REPEATED)

    def _renumber_rows(self, start: int) -> None:
        """Have every `Cell` from row `start` down say the row it stands in."""
        rows = self._sheet.rows
        for r in range(start, len(rows)):
            for cell in rows[r]:
                cell.row = r

    def insert_rows(self, row: int, count: int) -> None:
        sheet = self._sheet
        if count < 1:
            raise ValueError(f"count must be at least 1, got {count}")
        if row < 0 or row > sheet.n_rows:
            raise IndexError(f"row {row} out of range for insertion (sheet has {sheet.n_rows} rows)")
        self._references.read_first()
        if sheet.n_cols == 0 and sheet.n_rows > 0:  # every column deleted
            sheet.grow_to(sheet.n_rows - 1, 0)
        straddling = [
            (master.row, master.col, span_rows, span_cols)
            for master, span_rows, span_cols in self._merges.masters()
            if master.row < row < master.row + span_rows
        ]
        sheet.n_cols = max(sheet.n_cols, 1)  # a row holds a cell at least
        template = _empty_row(sheet.n_cols)
        row_tags = [clone(template) for _ in range(count)]
        self._place_rows(row, row_tags)
        sheet.rows[row:row] = [
            [Cell(t, row=row + k, col=c, sheet=sheet) for c, t in enumerate(_row_cells(row_tag))]
            for k, row_tag in enumerate(row_tags)
        ]
        self._renumber_rows(row + count)
        sheet.n_rows = len(sheet.rows)
        self._growth.give_back_rows(count)

        for m_row, m_col, span_rows, span_cols in straddling:
            self._merges.grow(m_row, m_col, span_rows + count, span_cols)
        self._references.remap(sheet.name, _insertion_remap(at_row=row, count=count))

    def _place_rows(self, row: int, row_tags: "list[etree._Element]") -> None:
        """Put `row_tags` in the file before logical row `row`, or below the
        grid for `row == n_rows` - above what the file holds there, so that
        it moves down with the rows after `row`, as LibreOffice moves a chart
        anchored there (it used to be deleted, issue #10).

        Each next to a known element, never by index (see `_insert_chain`):
        placed by index, or with bs4's insert_before, each row cost the
        insertion point's position - 8 s for 20,000 rows inserted below
        20,000."""
        sheet = self._sheet
        after: etree._Element | None = None
        before: etree._Element | None = None
        if row < sheet.n_rows:
            self._unroller.row(row)
            before = _row_element(sheet.rows[row])
        else:
            below = self._growth.rows_past_grid()
            if below:
                before = below[0]
            elif sheet.n_rows:
                after = _row_element(sheet.rows[-1])
        if before is not None:
            for row_tag in row_tags:
                insert_before(before, row_tag)
        elif after is not None:
            _insert_chain(after, row_tags)
        else:
            for row_tag in row_tags:
                sheet.table.append(row_tag)

    # columns

    def delete_column(self, col: int) -> None:
        sheet = self._sheet
        if col < 0 or col >= sheet.n_cols:
            raise IndexError(f"column {col} out of range (sheet has {sheet.n_cols} columns)")
        self._references.read_first()
        self._unmerge_column(col)
        self._growth.delete_cells_past_grid(col)  # while the grid still has a column
        for r in range(sheet.n_rows):
            self._unroller.column(r, col)
        _delete_column_definition(sheet.table, col)
        self._delete_column_cells(col)
        sheet.n_cols -= 1
        self._references.remap(sheet.name, _deletion_remap(deleted_cols=[col]))

    def _unmerge_column(self, col: int) -> None:
        """Undo every merge intersecting column `col`, as `_unmerge_row`."""
        sheet = self._sheet
        r = 0
        while r < sheet.n_rows:  # an unmerge may grow the grid
            if sheet.rows[r][col].is_merged:
                self._merges.unmerge_at(r, col)
            r += 1

    def _delete_column_cells(self, col: int) -> None:
        """Remove column `col`'s cells from the grid and the file, once per
        run of rows sharing an element - the chart anchored in the column
        moved to the cell taking its place rather than going with it, as for
        a row (issue #24)."""
        sheet = self._sheet
        heir_col = col + 1 if col + 1 < sheet.n_cols else col - 1
        for run in _runs_in(sheet.rows):
            cells = sheet.rows[run.start]
            if 0 <= heir_col < sheet.n_cols:
                self._unroller.column(run.start, heir_col)
                _rescue_drawings(cells[col].cell, cells[heir_col].cell)
            remove(cells[col].cell)  # once for the whole run
            for r in run:
                del sheet.rows[r][col]
                for c in range(col, len(sheet.rows[r])):
                    sheet.rows[r][c].col = c

    def insert_columns(self, col: int, count: int) -> None:
        sheet = self._sheet
        if count < 1:
            raise ValueError(f"count must be at least 1, got {count}")
        if col < 0 or col > sheet.n_cols:
            raise IndexError(f"column {col} out of range for insertion (sheet has {sheet.n_cols} columns)")
        self._references.read_first()
        straddling = [
            (master.row, master.col, span_rows, span_cols)
            for master, span_rows, span_cols in self._merges.masters()
            if master.col < col < master.col + span_cols
        ]

        # rows sharing one repeated <table:table-row> would otherwise each
        # insert their new cells into that same tag
        for r in range(sheet.n_rows):
            self._unroller.row(r)
        for r in range(sheet.n_rows):
            self._insert_cells(r, col, count)
        if sheet.n_rows > 0:
            sheet.n_cols += count
        self._growth.insert_cells_past_grid(col, count)
        _insert_column_definitions(sheet.table, col, count)

        for m_row, m_col, span_rows, span_cols in straddling:
            self._merges.grow(m_row, m_col, span_rows, span_cols + count)
        self._references.remap(sheet.name, _insertion_remap(at_col=col, count=count))

    def _insert_cells(self, r: int, col: int, count: int) -> None:
        """Insert `count` blank cells before column `col` of row `r` - its
        own element, unrolled - next to a known cell, never by index, as in
        `_place_rows`; the row keeps its full width if it was padded to it
        (`_give_back_cells`)."""
        sheet = self._sheet
        new_tags = [new("table:table-cell") for _ in range(count)]
        if col < sheet.n_cols:
            self._unroller.column(r, col)
            anchor = sheet.rows[r][col].cell
            for new_tag in new_tags:
                insert_before(anchor, new_tag)
        else:
            _insert_chain(sheet.rows[r][-1].cell, new_tags)
        sheet.rows[r][col:col] = [Cell(t, row=r, col=col + k, sheet=sheet) for k, t in enumerate(new_tags)]
        for c in range(col + count, len(sheet.rows[r])):
            sheet.rows[r][c].col = c
        _give_back_cells(_row_element(sheet.rows[r]), count, sheet.n_cols + count)
