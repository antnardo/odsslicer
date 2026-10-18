"""Merged cells: a range shown as one cell, its top-left cell - the master -
carrying `table:number-rows-spanned`/`table:number-columns-spanned`, every
other cell of it a `<table:covered-table-cell>` that keeps its own value,
hidden.

Merging, unmerging, finding the master of a covered cell and growing a merge
an insertion straddles are one concern: what the spans say and which cells
they cover. `Sheet` held it among a dozen others; `_Merges` needs of the
sheet its grid, `grow_to` (a merge may reach past the grid) and
`materialize_cell` (a cell about to be covered first gets an element of its
own), both public, and the unrolling of a merge's master.

Nothing here is public (`__all__` is empty): its API is `Sheet.merge`,
`Sheet.unmerge` and the merge properties of `Cell`.
"""

from typing import TYPE_CHECKING, Any

from odsslicer.addresses import string_address
from odsslicer.gridxml import _COLUMNS_SPANNED, _COVERED_CELL, _ROWS_SPANNED
from odsslicer.unrolling import _Unroller
from odsslicer.xmltree import rename, unset

if TYPE_CHECKING:
    from odsslicer.cell import Cell
    from odsslicer.sheet import Sheet

__all__: list[str] = []


def _spans(cell: "Cell") -> "tuple[int, int]":
    """The rows and columns a merge's master spans - (1, 1) for any other cell."""
    return int(cell.cell.get(_ROWS_SPANNED, "1")), int(cell.cell.get(_COLUMNS_SPANNED, "1"))


class _Merges:
    """The merged ranges of `sheet`'s grid."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet

    def master_of(self, row: int, col: int) -> "Cell | None":
        """The top-left cell of the merged range covering (row, col)."""
        for r in range(row, -1, -1):
            for master in self._sheet.rows[r]:
                if master.cell.tag == _COVERED_CELL or not master.is_merge_master:
                    continue
                rows_span, cols_span = _spans(master)
                if master.row <= row < master.row + rows_span and master.col <= col < master.col + cols_span:
                    return master
        return None

    def masters(self) -> "list[tuple[Cell, int, int]]":
        """Every merge master on the sheet, with its row and column spans."""
        return [
            (cell, *_spans(cell))
            for cells_row in self._sheet.rows
            for cell in cells_row
            if cell.cell.tag != _COVERED_CELL and cell.is_merge_master
        ]

    def merge(self, address: "str | int | tuple[Any, ...] | slice") -> None:
        """`Sheet.merge`, which says what it does."""
        sheet = self._sheet
        row0, row1, col0, col1 = sheet._resolve_range(address)
        if row0 == row1 and col0 == col1:
            raise ValueError(f"{address!r} is a single cell - nothing to merge")
        if self._merged_as(row0, row1, col0, col1):
            # In 0.13, writing into the master undid the merge, and the documented
            # remedy was to merge the range again after the write: now that the
            # write keeps the merge, that code would raise here.
            return
        sheet.grow_to(row1, col1)
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                if sheet.rows[r][c].is_merged:
                    raise ValueError(
                        f"cell {string_address(r, c)} is already part of a merged range - unmerge it first"
                    )
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                sheet.materialize_cell(r, c)
        self._span(row0, col0, row1 - row0 + 1, col1 - col0 + 1)

    def _merged_as(self, row0: int, row1: int, col0: int, col1: int) -> bool:
        """Whether rows `row0`-`row1` and columns `col0`-`col1` are merged
        already, as one range."""
        sheet = self._sheet
        return (
            row1 < sheet.n_rows
            and col1 < sheet.n_cols
            and sheet.rows[row0][col0].is_merge_master
            and sheet.rows[row0][col0].merge_span == (row1 - row0 + 1, col1 - col0 + 1)
        )

    def _span(self, row: int, col: int, span_rows: int, span_cols: int) -> None:
        """Make the cell at (row, col) a master spanning `span_rows` rows and
        `span_cols` columns, and every other cell of the range a covered
        cell - its value/formatting stays in the XML, just hidden, as
        `unmerge_at` expects to find it. Each `Cell` reads its element
        again."""
        sheet = self._sheet
        master = sheet.rows[row][col]
        master.cell.set(_ROWS_SPANNED, str(span_rows))
        master.cell.set(_COLUMNS_SPANNED, str(span_cols))
        for r in range(row, row + span_rows):
            for c in range(col, col + span_cols):
                covered = sheet.rows[r][c]
                if (r, c) == (row, col) or covered.cell.tag == _COVERED_CELL:
                    continue
                rename(covered.cell, "table:covered-table-cell")
                covered.__init__(covered.cell, row=r, col=c, sheet=sheet)  # type: ignore[misc]
        master.__init__(master.cell, row=row, col=col, sheet=sheet)  # type: ignore[misc]

    def unmerge(self, address: "str | int | tuple[Any, ...] | slice") -> None:
        """`Sheet.unmerge`, which says what it does."""
        row0, row1, col0, col1 = self._sheet._resolve_range(address)
        if row0 != row1 or col0 != col1:
            raise ValueError(f"{address!r} must resolve to a single cell")
        if not self._sheet.rows[row0][col0].is_merged:
            raise ValueError(f"cell {string_address(row0, col0)} is not part of a merged range")
        self.unmerge_at(row0, col0)

    def unmerge_at(self, row: int, col: int) -> None:
        """Turn the merged range covering (row, col) back into independent cells.

        The top-left ("master") cell keeps its value and simply loses its span
        attributes. Every other cell in the range is a `table:covered-table-cell`
        that ODF already stores its own (otherwise-hidden) value/format under —
        renaming it to a plain `table:table-cell` is enough to make it independent
        and reveal that value, exactly like un-merging in LibreOffice does.
        """
        sheet = self._sheet
        cell = sheet.rows[row][col]
        if cell.cell.tag == _COVERED_CELL:
            master = self.master_of(row, col)
            if master is None:
                raise RuntimeError(
                    f"cell {cell.address} looks merged/covered but its top-left "
                    "cell could not be found (internal error)"
                )
        else:
            master = cell

        mr, mc = master.row, master.col
        rows_span, cols_span = _spans(master)
        # a merge can run into trailing columns the grid leaves out (see
        # `loading`): every cell it covers is about to become one of the grid's
        sheet.grow_to(mr + rows_span - 1, mc + cols_span - 1)
        unset(master.cell, _ROWS_SPANNED)
        unset(master.cell, _COLUMNS_SPANNED)
        master.__init__(master.cell, row=mr, col=mc, sheet=sheet)  # type: ignore[misc]

        for r in range(mr, mr + rows_span):
            for c in range(mc, mc + cols_span):
                if r == mr and c == mc:
                    continue
                covered = sheet.rows[r][c]
                if covered.cell.tag == _COVERED_CELL:
                    rename(covered.cell, "table:table-cell")
                covered.__init__(covered.cell, row=r, col=c, sheet=sheet)  # type: ignore[misc]

    def grow(self, row: int, col: int, span_rows: int, span_cols: int) -> None:
        """Widen the merge whose master sits at (row, col) to the new spans,
        turning every cell newly inside it into a covered cell - the
        insertion counterpart of `unmerge_at`, which also explains the growth."""
        self._sheet.grow_to(row + span_rows - 1, col + span_cols - 1)
        unroller = _Unroller(self._sheet)
        unroller.row(row)
        unroller.column(row, col)
        self._span(row, col, span_rows, span_cols)
