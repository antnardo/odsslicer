"""Loading a sheet: the grid of `Cell`s, one per position, built from the
`<table:table>` element - and kept from the padding applications write
around the data.

Excel and LibreOffice declare a sheet's full size in the file: rows padded
with empty cells to the 16,384th column, a run of a million empty rows at
the bottom. Unrolled, they would be millions of `Cell`s for a few hundred
of data, and opening a 50 KB file took minutes. The loader leaves that
padding out of the grid and in the file, in two passes over the elements
before any `Cell` is built - what an element holds does not depend on where
it stands, so one look answers for every position it repeats over - and
splits the elements at the grid's edge, so that none straddles it (`grow_to`
takes what lies past it back). Only trailing *empty* cells and rows are ever
left out: empty rows and columns between data are real, and leaving them
out would move everything after them (issue #6).

`Sheet.load` was this, a single method of complexity 28 holding the four
steps; `_GridLoader` names them - where the grid ends, how wide it is, the
cells, the trimming - and needs of the sheet only its name, its log level,
and the sheet each `Cell` belongs to. The innermost loop, which builds a
`Cell` per position and runs for every cell of every sheet opened, is kept
as it was: it is the path a refactoring must not slow down
(`benchmarks/time_open.py`).
"""

import logging
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from lxml import etree

from odsslicer.cell import Cell
from odsslicer.constants import MAX_REPEAT_COLS, MAX_REPEAT_ROWS
from odsslicer.gridxml import (
    _COLUMNS_REPEATED,
    _ROWS_REPEATED,
    _carries_formatting,
    _holds_content,
    _repeat,
    _row_cells,
    _set_repeat,
    _sheet_rows,
    _split_cells_at,
    _split_row_element,
)
from odsslicer.unrolling import _run_in, _runs_in
from odsslicer.xmltree import clone, insert_after, new, remove

if TYPE_CHECKING:
    from odsslicer.sheet import Sheet

__all__: list[str] = []

logger = logging.getLogger("odsslicer")


@dataclass(frozen=True, slots=True)
class _RowMeasure:
    """One row element, measured once, for both passes over the rows: what
    each of its cells repeats over, where its trailing run of empty cells
    starts, and its width up to the first huge (> MAX_REPEAT_ROWS) cell of
    that run - its *content width*, the widest of which is the sheet's real
    width."""

    repeats: "tuple[int, ...]"
    suffix_start: int
    width: int

    @classmethod
    def of(cls, cells: "list[etree._Element]") -> "_RowMeasure":
        repeats = tuple([int(cell.get(_COLUMNS_REPEATED, "1")) for cell in cells])
        suffix_start = len(cells)
        while suffix_start > 0 and not _holds_content(cells[suffix_start - 1]):
            suffix_start -= 1
        width = 0
        for k, n_cols in enumerate(repeats):
            if k >= suffix_start and n_cols > MAX_REPEAT_ROWS:
                break
            width += n_cols
        return cls(repeats, suffix_start, width)


def _grid_end(rows: "list[etree._Element]", row_cells: "list[list[etree._Element]]") -> int:
    """How many of `rows` the grid reads: up to the first filler - a row
    repeated absurdly many times whose cells are all empty (Excel and
    LibreOffice both write one to declare the sheet's full 1,048,576-row
    height) - but only below the last row holding anything. Above it, empty
    rows are real, however many: leaving them out would move every row
    below them up (issue #6)."""
    last_content = len(rows) - 1
    while last_content >= 0 and not any(_holds_content(c) for c in row_cells[last_content]):
        last_content -= 1
    fillers = (
        k for k in range(last_content + 1, len(rows)) if _repeat(rows[k], _ROWS_REPEATED) > MAX_REPEAT_ROWS
    )
    return next(fillers, len(rows))


def _built_width(normalized: "list[list[tuple[etree._Element, int]]]", real_width: int) -> int:
    """How many columns the grid builds a `Cell` for: `real_width`, but for
    trailing columns empty in every row when there are more than
    MAX_REPEAT_COLS of them (see `_GridLoader._trim_columns`) - decided here,
    on the elements: LibreOffice writes empty cells as far as the last
    formatted column, and a sheet formatted to its 1,021st column but
    holding 58 had 150,000 cells built to keep 8,500."""
    content_end = 0
    for pairs in normalized:
        col = 0
        for cell, n_cols in pairs:
            col += n_cols
            if col > content_end and _holds_content(cell):
                content_end = col
    # an all-empty sheet keeps the general path, which takes it apart
    if content_end > 0 and real_width - content_end > MAX_REPEAT_COLS:
        return content_end
    return real_width


class _GridLoader:
    """Building `sheet`'s grid from its `<table:table>` element, the
    padding around the data left out, the XML rewritten to agree."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet
        self._log_level = sheet._log_level

    def load(self, table_element: etree._Element) -> "list[list[Cell]]":
        """The grid: a list of rows, each a list of the `Cell`s of its
        positions, every row as wide as the others."""
        rows = _sheet_rows(table_element)
        logger.log(self._log_level, "    Loading %s, %d unrepeated rows", self._sheet.name, len(rows))
        # Each row's cells, listed once: every pass below walks them.
        row_cells = [_row_cells(row) for row in rows]
        end = _grid_end(rows, row_cells)
        measures = [_RowMeasure.of(cells) for cells in row_cells[:end]]
        real_width = max((measure.width for measure in measures), default=0)
        normalized = self._normalize(rows[:end], row_cells[:end], measures, real_width)
        grid = self._build(rows[:end], normalized, _built_width(normalized, real_width))
        if end < len(rows):
            logger.log(
                self._log_level,
                "    Row [%04d] repeated %d > MAX = %d, empty to the end: grid ends",
                len(grid) + 1,
                _repeat(rows[end], _ROWS_REPEATED),
                MAX_REPEAT_ROWS,
            )
        # What the grid leaves out below stays in the file, and must stay out
        # of the elements the grid points at: unrolling gives a copy to every
        # repetition of an element, and `grow_to` takes rows back from the
        # element after the grid's last (issues #5, #10).
        if not grid or not grid[0]:
            return []
        self._trim_last_row(grid, rows[end + 1 :])
        if not grid:
            return grid
        return self._trim_columns(grid, real_width)

    def _normalize(
        self,
        rows: "list[etree._Element]",
        row_cells: "list[list[etree._Element]]",
        measures: "list[_RowMeasure]",
        real_width: int,
    ) -> "list[list[tuple[etree._Element, int]]]":
        """Every row's cells of the grid, each with the number of columns it
        stands for, the XML of each row rewritten to be exactly `real_width`
        wide (`_normalize_row`): the rest of the library assumes rectangular
        rows, and the XML and the grid must agree."""
        normalized = []
        i = 0
        for row, cells, measure in zip(rows, row_cells, measures, strict=True):
            normalized.append(self._normalize_row(row, cells, measure, real_width, i))
            i += _repeat(row, _ROWS_REPEATED)
        return normalized

    def _normalize_row(
        self,
        row_tag: etree._Element,
        cells: "list[etree._Element]",
        measure: _RowMeasure,
        real_width: int,
        row_index: int,
    ) -> "list[tuple[etree._Element, int]]":
        """Rewrite one row's XML so its total width is exactly `real_width`
        cells, touching only its trailing run of *empty* cells: a huge grid
        filler has its repeat count reduced, and a row that falls short is
        padded with a blank repeated cell. Rows already the right width come
        back untouched. Returns the row's cell tags of the grid, after the
        rewrite, each with the number of columns it stands for.

        What a trailing cell says about itself is kept: one carrying a style
        stays in the file, out of the grid, where it used to be deleted -
        LibreOffice writes a row formatted as a whole as its cells followed by
        one repeated to the last column, and a plain save dropped that
        formatting (issue #23). `grow_to` takes such a cell back into the grid,
        formatting and all, when the sheet widens (issue #6)."""
        repeats = measure.repeats
        width = 0
        for k, n_cols in enumerate(repeats):
            # only the trailing run of empty cells is ever clamped - a huge
            # repeat *before* real data is real (and counted in `real_width`)
            if k < measure.suffix_start or (width + n_cols <= real_width and n_cols <= MAX_REPEAT_ROWS):
                width += n_cols
                continue
            logger.log(
                self._log_level,
                "    Row [%04d]: trailing empty cells repeated %d clamped to the sheet's real width (%d)",
                row_index + 1,
                n_cols,
                real_width,
            )
            return _clamped(cells, repeats, k, real_width - width)
        pairs = list(zip(cells, repeats, strict=True))
        if width < real_width:
            pad = new("table:table-cell")
            if real_width - width > 1:
                pad.set(_COLUMNS_REPEATED, str(real_width - width))
            row_tag.append(pad)
            pairs.append((pad, real_width - width))
        return pairs

    def _build(
        self,
        rows: "list[etree._Element]",
        normalized: "list[list[tuple[etree._Element, int]]]",
        built_width: int,
    ) -> "list[list[Cell]]":
        """One `Cell` per position, and no other: the grid's. The positions
        an element repeats over read alike, and all but the first are
        copies of it (`Cell._at`), save a time value, whose reading may
        depend on the column (`Cell._counts_elapsed_time`). The cells whose
        value could not be read get one warning (`_warn_unreadable`)."""
        sheet = self._sheet
        grid = []
        unreadable: list[Cell] = []  # read as their text instead, see Cell.__init__
        i = 0
        for row, pairs in zip(rows, normalized, strict=True):
            n_rows = int(row.get(_ROWS_REPEATED, "1"))
            first: list[Cell | None] = [None] * len(pairs)
            for _ in range(n_rows):
                cells = []
                j = 0
                for k, (cell, n_cols) in enumerate(pairs):
                    template = first[k]
                    for _ in range(min(n_cols, built_width - j)):
                        if template is None:
                            built = Cell(cell, i, j, sheet)
                            if built.format != "time":
                                template = first[k] = built
                        else:
                            built = template._at(i, j)
                        cells.append(built)
                        if built._unreadable is not None:
                            unreadable.append(built)
                        j += 1
                    if j >= built_width:
                        break
                grid.append(cells)
                i += 1
        if unreadable:
            self._warn_unreadable(unreadable)
        return grid

    def _trim_last_row(self, grid: "list[list[Cell]]", after_filler: "list[etree._Element]") -> None:
        """Leave the sheet's last row out of the grid if it holds nothing.
        LibreOffice writes it apart - after the filler, if there is one
        (`after_filler`, the rows past it): the grid then ends before, and has
        nothing to trim, its last row being a real one. A row with no
        content may still hold a chart or formatting: it stays in the file,
        past the grid, its element split from the grid's rows."""
        written_apart = any(_repeat(row, _ROWS_REPEATED) <= MAX_REPEAT_ROWS for row in after_filler)
        if written_apart or any(_holds_content(cell.cell) for cell in grid[-1]):
            return
        row_tag = cast(etree._Element, grid[-1][0].cell.getparent())
        keep = len(_run_in(grid, len(grid) - 1)) - 1
        grid.pop()
        logger.log(self._log_level, "    Last row empty: removed")
        _split_row_element(row_tag, keep)

    def _trim_columns(self, grid: "list[list[Cell]]", real_width: int) -> "list[list[Cell]]":
        """Leave trailing columns empty in every row out of the grid when
        there are more than MAX_REPEAT_COLS of them: LibreOffice writes
        empty cells as far as the last formatted column, say. Only trailing
        ones - empty columns between data are real, and leaving them out
        would move every column to their right (issue #6). They stay in the
        file, formatting and all: each row element is split at the grid's
        edge, so that none straddles it, and `grow_to` takes them back as the
        sheet grows."""
        width = len(grid[0])
        while width > 0 and not any(_holds_content(row[width - 1].cell) for row in grid):
            width -= 1
        if real_width - width <= MAX_REPEAT_COLS:
            return grid
        logger.log(self._log_level, "    Cols %d to %d empty: left out", width + 1, real_width)
        for run in _runs_in(grid):
            _split_cells_at(cast(etree._Element, grid[run.start][0].cell.getparent()), width)
        # no column left: every row is empty, and so is the sheet
        return [row[:width] for row in grid] if width > 0 else []

    def _warn_unreadable(self, cells: "list[Cell]") -> None:
        """One warning for all the cells of this sheet whose value could not
        be read as its declared type - one per cell would bury the user
        under a generator's systematic quirk, repeated all down a column."""
        listed = ", ".join(f"{cell.address} ({cell._unreadable})" for cell in cells[:3])
        if len(cells) > 3:
            listed += f" and {len(cells) - 3} more"
        warnings.warn(
            f"sheet {self._sheet.name!r}: {listed}: not readable as the declared type,"
            " read as the displayed text instead (cell.raw_value keeps the value"
            " as written)",
            stacklevel=1,
        )


def _clamped(
    cells: "list[etree._Element]", repeats: "tuple[int, ...]", k: int, missing: int
) -> "list[tuple[etree._Element, int]]":
    """A row's cells of the grid once its `k`-th cell - a huge filler, or a
    cell crossing the real width - is clamped to exactly fill the row
    (`missing` columns), and everything after it dropped: a cell carrying
    formatting stays in the file past the grid, a part of the clamped one
    too (see `_GridLoader._normalize_row`)."""
    cell, n_cols = cells[k], repeats[k]
    keep = list(zip(cells[:k], repeats[:k], strict=True))
    if missing > 0:
        _set_repeat(cell, _COLUMNS_REPEATED, missing)
        keep.append((cell, missing))
        remainder = n_cols - missing
        if remainder > 0 and _carries_formatting(cell):
            # the part past the grid keeps saying what it says
            rest = clone(cell)
            _set_repeat(rest, _COLUMNS_REPEATED, remainder)
            insert_after(cell, rest)
        dropped = cells[k + 1 :]
    else:
        dropped = cells[k:]
    for extra in dropped:
        if not _carries_formatting(extra):
            remove(extra)
    return keep
