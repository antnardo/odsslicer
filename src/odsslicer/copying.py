"""Moving what cells hold within a sheet: a range copied onto another, the
rows of a range sorted.

Both are the same three steps, which is why they share a module: read what
every source cell holds - its value, its formula, the style it shows with -
before anything is written, so that a source and a destination may overlap;
then write each onto its destination, the style first, as a value reads
back through its format; and shift a formula by how far it moved, as a
spreadsheet's fill handle does (`formulas._shift_odf_formula`). Copying
moves a block, sorting moves each row by its own distance.

What a cell holds is a `_CellContent`, the record both read and write; it
used to be a tuple of three, in two copies of the same comprehension.
`Sheet.copy` and `Sheet.sort` keep their signatures and docstrings, and
hand the work to `_RangeCopy` and `_RangeSort`.

Nothing here is public (`__all__` is empty): its API is `Sheet.copy` and
`Sheet.sort`.
"""

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from odsslicer.columns import _column_default_cell_style
from odsslicer.datetimes import _comparable
from odsslicer.formulas import _shift_odf_formula
from odsslicer.gridxml import _STYLE_NAME

if TYPE_CHECKING:
    from odsslicer.cell import Cell
    from odsslicer.sheet import Sheet

__all__: list[str] = []


@dataclass(frozen=True, slots=True)
class _CellContent:
    """What a cell holds, as a copy or a sort moves it: its value, its
    formula (in ODF syntax, `None` if it has none), and the name of the
    style it shows with - its own, else its column's default cell style."""

    value: Any
    formula: "str | None"
    style_name: "str | None"


def _write(target: "Cell", content: _CellContent, style_name: "str | None", drow: int, dcol: int) -> None:
    """Write `content` onto `target`, which shows with the style
    `style_name`: the style first, as a value reads back through its format
    - a time of day as a duration in a [HH]:MM one (issue #20) - and a cell
    showing with it already, through its column, is left without a style of
    its own; then the formula shifted by (`drow`, `dcol`), or the value."""
    if style_name != content.style_name:
        target.style = content.style_name
    if content.formula is not None:
        target._write_formula(_shift_odf_formula(content.formula, drow, dcol))
    else:
        target.value = content.value


class _RangeCopy:
    """Copying a range of `sheet` onto another (see `Sheet.copy`)."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet

    def copy(
        self, source: "str | int | tuple[Any, ...] | slice", dest: "str | int | tuple[Any, ...] | slice"
    ) -> None:
        sheet = self._sheet
        row0, row1, col0, col1 = sheet._resolve_range(source)
        dest_row0, _, dest_col0, _ = sheet._resolve_range(dest)
        n_rows, n_cols = row1 - row0 + 1, col1 - col0 + 1
        sheet.grow_to(dest_row0 + n_rows - 1, dest_col0 + n_cols - 1)

        snapshot = [
            [
                # the style it shows with, its column's if it has none
                _CellContent(cell.value, cell.formula, cell._style_name())
                for cell in (sheet._cell(r, c) for c in range(col0, col1 + 1))
            ]
            for r in range(row0, row1 + 1)
        ]

        drow, dcol = dest_row0 - row0, dest_col0 - col0
        for i in range(n_rows):
            for j in range(n_cols):
                target = sheet.rows[dest_row0 + i][dest_col0 + j]
                _write(target, snapshot[i][j], target._style_name(), drow, dcol)


def _compare(a: Any, b: Any, *, ascending: bool) -> int:
    """How a sort orders keys `a` and `b`: `None` last whatever the order,
    as a spreadsheet's sort treats blanks. `_comparable`: a column of dates
    can hold date-times too, and one of times durations - Python refuses to
    order either pair as is."""
    va, vb = _comparable(a), _comparable(b)
    if va is None and vb is None:
        return 0
    if va is None:
        return 1
    if vb is None:
        return -1
    if va < vb:
        return -1 if ascending else 1
    if va > vb:
        return 1 if ascending else -1
    return 0


class _RangeSort:
    """Sorting the rows of a range of `sheet` by one column (see `Sheet.sort`)."""

    def __init__(self, sheet: "Sheet") -> None:
        self._sheet = sheet
        # each column's default cell style, looked up once: a cell with no
        # style of its own asks its column for one, by a walk of the column
        # definitions, twice per cell here - which made sorting 1,000 rows
        # slower than with bs4. Nothing here changes the columns.
        self._column_defaults: dict[int, str | None] = {}

    def _style_name(self, cell: "Cell") -> "str | None":
        """`Cell._style_name`, the column's default looked up once."""
        own = cell.cell.get(_STYLE_NAME)
        if own is not None:
            return own
        if cell.col not in self._column_defaults:
            self._column_defaults[cell.col] = _column_default_cell_style(self._sheet.table, cell.col)
        return self._column_defaults[cell.col]

    def sort(self, source: "str | int | tuple[Any, ...] | slice", by: int, *, ascending: bool) -> None:
        sheet = self._sheet
        row0, row1, col0, col1 = sheet._resolve_range(source)
        if not col0 <= by <= col1:
            raise ValueError(f"sort column {by} is outside {source!r}'s columns ({col0}-{col1})")

        snapshot = [
            (r, [self._content(sheet.rows[r][c]) for c in range(col0, col1 + 1)])
            for r in range(row0, row1 + 1)
        ]
        key = by - col0

        def compare(a: "tuple[int, list[_CellContent]]", b: "tuple[int, list[_CellContent]]") -> int:
            return _compare(a[1][key].value, b[1][key].value, ascending=ascending)

        ordered = sorted(snapshot, key=functools.cmp_to_key(compare))

        for new_offset, (old_row, row_data) in enumerate(ordered):
            new_row = row0 + new_offset
            for j, content in enumerate(row_data):
                target = sheet.rows[new_row][col0 + j]
                _write(target, content, self._style_name(target), new_row - old_row, 0)

    def _content(self, cell: "Cell") -> _CellContent:
        return _CellContent(cell.value, cell.formula, self._style_name(cell))  # as in `_RangeCopy`
