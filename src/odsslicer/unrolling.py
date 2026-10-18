"""Unrolling: giving positions of the grid that share an element - a run of
repeated rows, a cell repeated over columns - an element each, before one
of them is written to.

The grid keeps a `Cell` per position, and the positions an element repeats
over point at that same element (see `loading`). Writing to one of them
would write to all: the element is first split (`gridxml._split_repetitions`)
and every `Cell` of the run repointed at its own copy, so that a `Cell`
obtained before the split keeps reading and writing its own position. That
needs the grid itself - which rows share an element (`_run_in`), which
`Cell` stands where - and nothing else of the sheet: `_Unroller` takes the
sheet for its grid, and the cells it repoints.

Nothing here is public (`__all__` is empty): its API is
`Sheet.materialize_cell`.
"""

from typing import TYPE_CHECKING, cast

from lxml import etree

from odsslicer.gridxml import _COLUMNS_REPEATED, _ROWS_REPEATED, _repeat, _row_cells, _split_repetitions

if TYPE_CHECKING:
    from odsslicer.cell import Cell
    from odsslicer.sheet import Sheet

__all__: list[str] = []


def _run_in(rows: "list[list[Cell]]", row: int) -> range:
    """The rows of `rows` sharing `row`'s `<table:table-row>` element: a
    run of repeated rows (`table:number-rows-repeated`), or `row` alone.
    The grid keeps one list of cells per row, but they all point at the
    same elements, so reshaping any row of a run has to reach them all."""
    tag = rows[row][0].cell.getparent()
    first, stop = row, row + 1
    while first > 0 and rows[first - 1][0].cell.getparent() is tag:
        first -= 1
    while stop < len(rows) and rows[stop][0].cell.getparent() is tag:
        stop += 1
    return range(first, stop)


def _runs_in(rows: "list[list[Cell]]") -> "list[range]":
    """`rows`, grouped into runs (see `_run_in`), in order."""
    runs: list[range] = []
    start = 0
    while start < len(rows):
        runs.append(_run_in(rows, start))
        start = runs[-1].stop
    return runs


def _row_element(cells: "list[Cell]") -> etree._Element:
    """The `<table:table-row>` a row of the grid stands for."""
    return cast(etree._Element, cells[0].cell.getparent())


class _Unroller:
    """Splitting the element a row or a cell of `sheet`'s grid shares with
    others into one per position, and repointing the grid's cells."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet

    def row(self, row: int) -> None:
        """Split the `table:number-rows-repeated` row tag covering `row` into
        one independent `<table:table-row>` per repetition."""
        sheet = self._sheet
        rows = sheet.rows
        row_tag = _row_element(rows[row])
        n = _repeat(row_tag, _ROWS_REPEATED)
        if n <= 1:
            return

        run = _run_in(rows, row)
        copies = _split_repetitions(row_tag, _ROWS_REPEATED, n)

        for r, copy_tag in zip(run, copies, strict=True):
            j = 0
            for cell_tag in _row_cells(copy_tag):
                for _ in range(_repeat(cell_tag, _COLUMNS_REPEATED)):
                    if j < len(rows[r]):
                        rows[r][j].__init__(cell_tag, row=r, col=j, sheet=sheet)  # type: ignore[misc]
                    j += 1

    def column(self, row: int, col: int) -> None:
        """Split the `table:number-columns-repeated` cell tag covering (row, col)
        into one independent `<table:table-cell>` per repetition - for every
        row of the run sharing it (see `_run_in`), which the split reaches
        all the same."""
        sheet = self._sheet
        rows = sheet.rows
        cell_tag = rows[row][col].cell
        n = _repeat(cell_tag, _COLUMNS_REPEATED)
        if n <= 1:
            return

        start = col
        while start > 0 and rows[row][start - 1].cell is cell_tag:
            start -= 1
        run = _run_in(rows, row)  # before the split detaches `cell_tag`

        copies = _split_repetitions(cell_tag, _COLUMNS_REPEATED, n)

        for r in run:
            for c, copy_tag in enumerate(copies, start=start):
                rows[r][c].__init__(copy_tag, row=r, col=c, sheet=sheet)  # type: ignore[misc]
