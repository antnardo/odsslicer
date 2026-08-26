# -*- coding: utf-8 -*-
# mypy: disable-error-code="union-attr"
# (bs4 Tag/NavigableString/None unions are narrowed dynamically all over this
# module, guarded by runtime checks mypy can't see through - silencing that
# one error class here beats dozens of value-free asserts/casts. Every other
# error class, and all signatures, remain fully checked.)
"""Sheet: numpy-style indexing over one table, structural edits (grow, delete,
copy, sort, merge), pivot definitions, row/column/table style access."""

import copy
import functools
import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, Tuple, Union, cast

from bs4 import BeautifulSoup, Tag

from .addresses import string_address, string_to_col
from .cell import ArrayValues, Cell
from .constants import (
    EMPTY_CELL_BS,
    MAX_REPEAT_COLS,
    MAX_REPEAT_ROWS,
    RE_STRING_CELL,
    TAG_CELL,
)
from .formulas import (
    _PIVOT_DATA_FUNCTIONS,
    _SHEET_QUALIFIED_RE,
    _adjust_odf_formula_for_deletion,
    _quote_odf_sheet_name,
    _shift_odf_formula,
    _unquote_odf_sheet_name,
)
from .styles import ColumnStyle, RowStyle, TableStyle
from .xmlutils import _blank_template, _ensure_style_child, _is_forked_style_name

if TYPE_CHECKING:
    from .reader import ODSReader

logger = logging.getLogger("odsslicer")


class Sheet:
    def __init__(self, table: Tag, verbose: bool = False, reader: "ODSReader | None" = None) -> None:
        self.verbose = verbose
        # chatty progress messages go to the "odsslicer" logger: DEBUG
        # normally, INFO with verbose=True - configure logging to see them
        self._log_level = logging.INFO if verbose else logging.DEBUG
        self.reader = reader
        self.table: Tag = table
        self.attrs: Dict[str, str] = self.table.attrs
        self.name: str = cast(str, self.table["table:name"])
        self.stylename = self.table.attrs.get("table:style-name")
        self.rows = self.load(table)
        if len(self.rows) > 0:
            rows_len = [len(row) for row in self.rows]
            self.size = (len(self.rows), max(rows_len))
            n_cols = self.size[1]
            if sum(n_cols - len_row for len_row in rows_len) > 0:
                logger.warning(
                    "At least one row does not have the same length as the others? %s", rows_len
                )
        else:
            self.size = (0, 0)
        self.n_rows, self.n_cols = self.size
        logger.log(self._log_level, "    %r", self)

    def load(self, table_bs: Tag) -> list[list[Cell]]:
        table = []
        rows = table_bs.find_all("table:table-row")
        logger.log(self._log_level, "    Loading %s, %d unrepeated rows", self.name, len(rows))
        # Grid-filler guard, column direction: Excel and LibreOffice pad rows
        # up to the sheet's full width (16,384 columns) with trailing empty
        # repeated cells - unrolling those would create millions of Cell
        # objects. First pass: measure each row's *content width*, i.e. its
        # width up to the first huge (> MAX_REPEAT_ROWS) empty cell of its
        # trailing run of empty cells; the widest row is the sheet's real
        # width. Second pass (_normalize_row_width): rewrite each row's XML so
        # it is exactly that wide - huge fillers clamped, short rows padded -
        # keeping the XML and the in-memory model in agreement (the rest of
        # the library assumes rectangular rows). Only trailing *empty* cells
        # are ever touched, so no data moves and nothing visible is lost.
        def content_width(cells_bs: "list[Tag]") -> "tuple[int, Tag | None]":
            """(width up to the first huge trailing empty cell, that cell)."""
            suffix_start = len(cells_bs)
            while suffix_start > 0 and Cell(cells_bs[suffix_start - 1]).is_empty:
                suffix_start -= 1
            width, first_big = 0, None
            for k, cell in enumerate(cells_bs):
                n_cols = int(cell.attrs.get("table:number-columns-repeated", "1"))
                if k >= suffix_start and n_cols > MAX_REPEAT_ROWS:
                    first_big = cell
                    break
                width += n_cols
            return width, first_big

        # Its row-direction counterpart: a row repeated absurdly many times
        # whose cells are all empty is grid filler too (Excel and LibreOffice
        # both write one to declare the sheet's full 1,048,576-row height).
        def is_filler_row(row: Tag, cells_bs: "list[Tag]") -> bool:
            n_rows = int(row.attrs.get("table:number-rows-repeated", "1"))
            return n_rows > MAX_REPEAT_ROWS and all(Cell(c).is_empty for c in cells_bs)

        real_width = 0
        for row in rows:
            cells_bs = row.find_all(TAG_CELL)
            if is_filler_row(row, cells_bs):
                continue  # discarded by the row-direction guard below
            real_width = max(real_width, content_width(cells_bs)[0])
        i = 0
        for row in rows:
            n_rows = int(row.attrs.get("table:number-rows-repeated", "1"))
            # ATTENTION : if some style is applied to a whole column : you get the max length 2**20
            all_cells_bs = row.find_all(TAG_CELL)
            if is_filler_row(row, all_cells_bs):
                logger.log(
                    self._log_level,
                    "    Row [%04d] repeated %d > MAX = %d and all-empty: row discarded",
                    i + 1, n_rows, MAX_REPEAT_ROWS,
                )
                continue
            all_cells_bs = self._normalize_row_width(row, all_cells_bs, real_width, i)
            for j in range(n_rows):
                cells = []
                j = 0
                for cell in all_cells_bs:
                    n_cols = int(cell.attrs.get("table:number-columns-repeated", "1"))
                    for _ in range(n_cols):
                        cells.append(Cell(cell, row=i, col=j, sheet=self))
                        j += 1
                table.append(cells)
                i += 1
        # CLEAN UP
        if len(table) == 0:
            return table
        # Check if last row is empty and remove
        if sum(not cell.is_empty for cell in table[-1]) == 0:
            table = table[:-1]
            logger.log(self._log_level, "    Last row empty: removed")
        # Remove repeated columns > MAX and empty
        # Transpose
        if len(table) == 0:
            return table
        n_cols = len(table[0])
        columns = [[row[j] for row in table] for j in range(n_cols)]
        # Get empty columns
        empty_cols = [sum(not cell.is_empty for cell in col) == 0 for col in columns]
        # Get numbers of empty cols "in a row"
        empty_cols_pos = [(i, int(b)) for i, b in enumerate(empty_cols)]
        for i in range(1, len(empty_cols_pos)):
            jp, vp = empty_cols_pos[i - 1]
            j, v = empty_cols_pos[i]
            if v == 1 and vp >= 1:
                empty_cols_pos[i] = (jp, vp + 1)
        # Remove
        empty_cols_aggr = [e[1] for e in empty_cols_pos]
        m = max(empty_cols_aggr)
        while max(empty_cols_aggr) > MAX_REPEAT_COLS:
            col_start = empty_cols_pos[empty_cols_aggr.index(m)][0]
            col_end = col_start + m
            logger.log(self._log_level, "    Cols %d to %d empty: removed", col_start, col_end)
            table = [row[:col_start] + row[col_end:] for row in table]
            empty_cols_pos = empty_cols_pos[:col_start] + empty_cols_pos[col_end:]
            empty_cols_aggr = [e[1] for e in empty_cols_pos]
            m = max(empty_cols_aggr)
            if m > MAX_REPEAT_COLS:
                # dot it again to get the indexes right (works but nasty)
                n_cols = len(table[0])
                columns = [[row[j] for row in table] for j in range(n_cols)]
                # Get empty columns
                empty_cols = [
                    sum(not cell.is_empty for cell in col) == 0 for col in columns
                ]
                # Get numbers of empty cols "in a row"
                empty_cols_pos = [(i, int(b)) for i, b in enumerate(empty_cols)]
                for i in range(1, len(empty_cols_pos)):
                    jp, vp = empty_cols_pos[i - 1]
                    j, v = empty_cols_pos[i]
                    if v == 1 and vp >= 1:
                        empty_cols_pos[i] = (jp, vp + 1)
                empty_cols_aggr = [e[1] for e in empty_cols_pos]
        return table

    def _normalize_row_width(self, row_tag: Tag, cells_bs: "list[Tag]", real_width: int, row_index: int) -> "list[Tag]":
        """Rewrite one row's XML so its total width is exactly `real_width`
        cells, touching only its trailing run of *empty* cells: a huge grid
        filler (see `load`) has its repeat count reduced (cells beyond it
        removed), and a row that falls short is padded with a blank repeated
        cell. Rows already the right width come back untouched. Returns the
        row's cell tags after the rewrite."""
        suffix_start = len(cells_bs)
        while suffix_start > 0 and Cell(cells_bs[suffix_start - 1]).is_empty:
            suffix_start -= 1
        width = 0
        for k, cell in enumerate(cells_bs):
            n_cols = int(cell.attrs.get("table:number-columns-repeated", "1"))
            # only the trailing run of empty cells is ever clamped - a huge
            # repeat *before* real data is real (and counted in `real_width`)
            if k < suffix_start or (width + n_cols <= real_width and n_cols <= MAX_REPEAT_ROWS):
                width += n_cols
                continue
            # a huge filler, or a cell crossing the real width: clamp it to
            # exactly fill the row, and drop everything after it
            missing = real_width - width
            logger.log(
                self._log_level,
                "    Row [%04d]: trailing empty cells repeated %d clamped to the sheet's real width (%d)",
                row_index + 1, n_cols, real_width,
            )
            keep = cells_bs[:k]
            if missing > 0:
                if missing == 1:
                    cell.attrs.pop("table:number-columns-repeated", None)
                else:
                    cell.attrs["table:number-columns-repeated"] = str(missing)
                keep.append(cell)
            for extra in cells_bs[k if missing <= 0 else k + 1:]:
                extra.decompose()
            return keep
        if width < real_width:
            pad = self._empty_cell_template()
            if real_width - width > 1:
                pad.attrs["table:number-columns-repeated"] = str(real_width - width)
            row_tag.append(pad)
            return [*cells_bs, pad]
        return cells_bs

    def materialize_cell(self, row: int, col: int) -> None:
        """Ensure the cell at (row, col) has its own, independent XML element.

        Repeatedly un-repeats the enclosing row, then the enclosing column, then
        un-merges the enclosing merged range, until the cell shares nothing with
        any other cell anymore. Each step repoints every affected `Cell` object
        (via re-running its `__init__`) at its own new tag, so existing `Cell`
        references obtained before the call keep working correctly afterwards.
        """
        for _ in range(8):  # a handful of structural layers is more than any real file has
            cell = self.rows[row][col]
            tag = cell.cell
            parent = tag.parent
            if parent is not None and parent.attrs.get("table:number-rows-repeated", "1") != "1":
                self._unrepeat_row(row)
                continue
            if cell.attrs.get("table:number-columns-repeated", "1") != "1":
                self._unrepeat_col(row, col)
                continue
            if tag.name == "covered-table-cell" or self._is_merge_master(cell):
                self._unmerge(row, col)
                continue
            return
        raise RuntimeError(
            f"failed to materialize cell (row={row}, col={col}) as an independent "
            "element (internal error)"
        )

    def _unrepeat_row(self, row: int) -> None:
        """Split the `table:number-rows-repeated` row tag covering `row` into
        one independent `<table:table-row>` per repetition."""
        row_tag = cast(Tag, self.rows[row][0].cell.parent)
        n = int(row_tag.attrs.get("table:number-rows-repeated", "1"))
        if n <= 1:
            return

        start = row
        while start > 0 and self.rows[start - 1][0].cell.parent is row_tag:
            start -= 1

        copies = [copy.deepcopy(row_tag) for _ in range(n)]
        for c in copies:
            c.attrs.pop("table:number-rows-repeated", None)
        row_tag.replace_with(copies[0])
        prev = copies[0]
        for nxt in copies[1:]:
            prev.insert_after(nxt)
            prev = nxt

        for k, copy_tag in enumerate(copies):
            r = start + k
            j = 0
            for cell_tag in copy_tag.find_all(TAG_CELL):
                n_cols = int(cell_tag.attrs.get("table:number-columns-repeated", "1"))
                for _ in range(n_cols):
                    if j < len(self.rows[r]):
                        self.rows[r][j].__init__(cell_tag, row=r, col=j, sheet=self)  # type: ignore[misc]
                    j += 1

    def _unrepeat_col(self, row: int, col: int) -> None:
        """Split the `table:number-columns-repeated` cell tag covering (row, col)
        into one independent `<table:table-cell>` per repetition."""
        cell_tag = self.rows[row][col].cell
        n = int(cell_tag.attrs.get("table:number-columns-repeated", "1"))
        if n <= 1:
            return

        start = col
        while start > 0 and self.rows[row][start - 1].cell is cell_tag:
            start -= 1

        copies = [copy.deepcopy(cell_tag) for _ in range(n)]
        for copy_of_cell in copies:
            copy_of_cell.attrs.pop("table:number-columns-repeated", None)
        cell_tag.replace_with(copies[0])
        prev = copies[0]
        for nxt in copies[1:]:
            prev.insert_after(nxt)
            prev = nxt

        for k, copy_tag in enumerate(copies):
            c = start + k
            self.rows[row][c].__init__(copy_tag, row=row, col=c, sheet=self)  # type: ignore[misc]

    @staticmethod
    def _is_merge_master(cell: Cell) -> bool:
        return (
            cell.attrs.get("table:number-columns-spanned", "1") != "1"
            or cell.attrs.get("table:number-rows-spanned", "1") != "1"
        )

    def _find_merge_master(self, row: int, col: int) -> "Cell | None":
        """Find the top-left cell of the merged range covering (row, col)."""
        for r in range(row, -1, -1):
            for master in self.rows[r]:
                if master.cell.name == "covered-table-cell" or not self._is_merge_master(master):
                    continue
                rows_span = int(master.attrs.get("table:number-rows-spanned", "1"))
                cols_span = int(master.attrs.get("table:number-columns-spanned", "1"))
                if (
                    master.row <= row < master.row + rows_span
                    and master.col <= col < master.col + cols_span
                ):
                    return master
        return None

    def _unmerge(self, row: int, col: int) -> None:
        """Turn the merged range covering (row, col) back into independent cells.

        The top-left ("master") cell keeps its value and simply loses its span
        attributes. Every other cell in the range is a `table:covered-table-cell`
        that ODF already stores its own (otherwise-hidden) value/format under —
        renaming it to a plain `table:table-cell` is enough to make it independent
        and reveal that value, exactly like un-merging in LibreOffice does.
        """
        cell = self.rows[row][col]
        if cell.cell.name == "covered-table-cell":
            master = self._find_merge_master(row, col)
            if master is None:
                raise RuntimeError(
                    f"cell {cell.address} looks merged/covered but its top-left "
                    "cell could not be found (internal error)"
                )
        else:
            master = cell

        mr, mc = master.row, master.col
        rows_span = int(master.attrs.get("table:number-rows-spanned", "1"))
        cols_span = int(master.attrs.get("table:number-columns-spanned", "1"))
        master.cell.attrs.pop("table:number-rows-spanned", None)
        master.cell.attrs.pop("table:number-columns-spanned", None)
        master.__init__(master.cell, row=mr, col=mc, sheet=self)  # type: ignore[misc]

        for r in range(mr, mr + rows_span):
            for c in range(mc, mc + cols_span):
                if r == mr and c == mc:
                    continue
                covered = self.rows[r][c]
                if covered.cell.name == "covered-table-cell":
                    covered.cell.name = "table-cell"
                covered.__init__(covered.cell, row=r, col=c, sheet=self)  # type: ignore[misc]

    def _resolve_range(self, address: "str | int | tuple[Any, ...] | slice") -> tuple[int, int, int, int]:
        """Turn a range address - `"A1:C2"`, `"A1"`, a slice, or anything
        else `sheet[...]` accepts - into `(row0, row1, col0, col1)`
        (inclusive, 0-based) bounds, without actually reading any cell."""
        if isinstance(address, str):
            address = self.address(address, self.n_rows)
        if isinstance(address, int):
            return address, address, 0, self.n_cols - 1
        if isinstance(address, slice):
            start, stop, _ = self._unslice(address, row=True)
            return start, stop - 1, 0, self.n_cols - 1
        if isinstance(address, tuple) and len(address) == 2:
            rows_address, cols_address = address
            if isinstance(rows_address, int):
                row0 = row1 = rows_address
            else:
                row0, stop, _ = self._unslice(rows_address, row=True)
                row1 = stop - 1
            if isinstance(cols_address, int):
                col0 = col1 = cols_address
            else:
                col0, stop, _ = self._unslice(cols_address, col=True)
                col1 = stop - 1
            return row0, row1, col0, col1
        raise ValueError(f"unrecognized range address: {address!r}")

    def merge(self, address: "str | int | tuple[Any, ...] | slice") -> None:
        """Merge the cells in `address` (e.g. `"A1:C2"`, or any rectangular
        selection accepted by `sheet[...]`) into one cell: the top-left
        cell becomes the merge's master, keeping its value and gaining
        `table:number-rows-spanned`/`table:number-columns-spanned`; every
        other cell in the range becomes a `table:covered-table-cell` - its
        value/formatting stays in the XML, just hidden, exactly as
        `unmerge(...)` expects to find it (nothing is erased).

        Grows the sheet first if `address` extends past its current
        extent. Raises `ValueError` if the range is a single cell, or if
        any cell in it is already part of a merge (`unmerge(...)` it
        first)."""
        row0, row1, col0, col1 = self._resolve_range(address)
        if row0 == row1 and col0 == col1:
            raise ValueError(f"{address!r} is a single cell - nothing to merge")
        self.grow_to(row1, col1)
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                if self.rows[r][c].is_merged:
                    raise ValueError(
                        f"cell {Sheet.string_address(r, c)} is already part of a "
                        "merged range - unmerge it first"
                    )
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                self.materialize_cell(r, c)

        master = self.rows[row0][col0]
        master.cell.attrs["table:number-rows-spanned"] = str(row1 - row0 + 1)
        master.cell.attrs["table:number-columns-spanned"] = str(col1 - col0 + 1)
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                if r == row0 and c == col0:
                    continue
                covered = self.rows[r][c]
                covered.cell.name = "covered-table-cell"
                covered.__init__(covered.cell, row=r, col=c, sheet=self)  # type: ignore[misc]
        master.__init__(master.cell, row=row0, col=col0, sheet=self)  # type: ignore[misc]

    def unmerge(self, address: "str | int | tuple[Any, ...] | slice") -> None:
        """Undo `merge(...)` for the merged range covering `address` (any
        single cell within it, master or covered) - every cell in the
        range becomes independent again, regaining whatever value/
        formatting ODF was keeping hidden underneath it.

        Raises `ValueError` if `address` doesn't resolve to a single cell,
        or if that cell isn't part of any merged range."""
        row0, row1, col0, col1 = self._resolve_range(address)
        if row0 != row1 or col0 != col1:
            raise ValueError(f"{address!r} must resolve to a single cell")
        if not self.rows[row0][col0].is_merged:
            raise ValueError(f"cell {Sheet.string_address(row0, col0)} is not part of a merged range")
        self._unmerge(row0, col0)

    def copy(self, source: "str | int | tuple[Any, ...] | slice", dest: "str | int | tuple[Any, ...] | slice") -> None:
        """Copy the cells in `source` (any address `sheet[...]` accepts,
        e.g. `"A1:B2"` or a single cell) onto `dest` (the *top-left*
        address of the target range - the copy always has the same shape
        as `source`, any larger selection there is ignored) - like a
        spreadsheet's copy-paste: value, formula (shifted the same way
        `Cell.fill_formula` shifts it - a relative reference like `A1`
        moves with the copy, `$A$1` stays put), and style (see
        `Cell.style`'s setter - the destination points at the same
        underlying style as the source, forking its own private copy only
        once something on it is actually changed) all come along.

        Grows the sheet first if `dest` extends past its current extent.
        Safe when `source` and `dest` overlap (every source cell is read
        before any destination cell is written). A merged source cell
        copies whatever value/style it individually carries (its own
        hidden value, if it's a covered cell) - the merge itself is not
        replicated at the destination.
        """
        row0, row1, col0, col1 = self._resolve_range(source)
        dest_row0, _, dest_col0, _ = self._resolve_range(dest)
        n_rows, n_cols = row1 - row0 + 1, col1 - col0 + 1
        self.grow_to(dest_row0 + n_rows - 1, dest_col0 + n_cols - 1)

        snapshot = [
            [
                (
                    self.get_cell(r, c).value,
                    self.get_cell(r, c).formula,
                    self.get_cell(r, c).attrs.get("table:style-name"),
                )
                for c in range(col0, col1 + 1)
            ]
            for r in range(row0, row1 + 1)
        ]

        drow, dcol = dest_row0 - row0, dest_col0 - col0
        for i in range(n_rows):
            for j in range(n_cols):
                value, formula, style_name = snapshot[i][j]
                target = self.rows[dest_row0 + i][dest_col0 + j]
                if formula is not None:
                    target.formula = _shift_odf_formula(formula, drow, dcol)
                else:
                    target.value = value
                target.style = style_name

    def sort(self, source: "str | int | tuple[Any, ...] | slice", by: int, ascending: bool = True) -> None:
        """Sort the rows of `source` (a range address, e.g. `"A2:C10"`) in
        place by the values in column `by` (an absolute column index,
        which must fall within `source`'s own columns) - a stable sort
        (rows with equal keys keep their relative order), and `None`
        values always sort last regardless of `ascending`, matching how a
        real spreadsheet's sort treats blanks.

        Each row's value/formula/style moves together as a unit; a
        formula's references shift by that row's own displacement - the
        same relative-reference semantics as `Cell.fill_formula`/
        `Sheet.copy` (a `$`-anchored reference stays put) - so e.g. a
        same-row `=B2*C2` formula still refers to its own, now-relocated
        row afterwards. A merged cell within `source` moves only its own
        raw content, same caveat as `Sheet.copy` - the merge itself isn't
        preserved.

        Raises `ValueError` if `by` falls outside `source`'s columns.
        """
        row0, row1, col0, col1 = self._resolve_range(source)
        if not col0 <= by <= col1:
            raise ValueError(f"sort column {by} is outside {source!r}'s columns ({col0}-{col1})")

        snapshot = [
            (
                r,
                [
                    (
                        self.rows[r][c].value,
                        self.rows[r][c].formula,
                        self.rows[r][c].attrs.get("table:style-name"),
                    )
                    for c in range(col0, col1 + 1)
                ],
            )
            for r in range(row0, row1 + 1)
        ]
        key_offset = by - col0

        def compare(a: tuple[int, list[tuple[Any, Any, Any]]], b: tuple[int, list[tuple[Any, Any, Any]]]) -> int:
            va, vb = a[1][key_offset][0], b[1][key_offset][0]
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

        ordered = sorted(snapshot, key=functools.cmp_to_key(compare))

        for new_offset, (old_row, row_data) in enumerate(ordered):
            new_row = row0 + new_offset
            drow = new_row - old_row
            for j, (value, formula, style_name) in enumerate(row_data):
                target = self.rows[new_row][col0 + j]
                if formula is not None:
                    target.formula = _shift_odf_formula(formula, drow, 0)
                else:
                    target.value = value
                target.style = style_name

    def create_pivot_table(
        self,
        source: str,
        target: str,
        rows: "list[str] | None" = None,
        columns: "list[str] | None" = None,
        values: "dict[str, str] | None" = None,
        name: "str | None" = None,
    ) -> None:
        """Define a pivot table ("data pilot table" in ODF terms) sourced
        from `source` (a range address whose first row is field/column
        headers, e.g. `"A1:C100"` - optionally sheet-qualified,
        `"Data.A1:C100"`, if the source lives on another sheet) and
        targeting `target` (the top-left cell where the computed output
        will be placed once refreshed) on this sheet.

        `rows`/`columns` are lists of field names (matching header row
        values) to use as row/column categories; `values` maps a field
        name to an aggregation function - `"sum"`, `"average"`,
        `"count"`, `"countnums"` (count of numeric values only),
        `"max"`, `"min"`, `"product"`, `"stdev"`, `"stdevp"`, `"var"`,
        `"varp"`. `name` defaults to `f"DataPilotTable{n}"`.

        odsslicer writes only the pivot's ODF definition - there's no
        calculation engine, same as for formulas. Unlike a formula
        (which every conformant reader recomputes on open), a pivot
        table needs an *explicit* refresh (Data > Pivot Table > Refresh
        in LibreOffice) before its result appears - confirmed against a
        real LibreOffice, opening a file with only the definition shows
        nothing at `target` until refreshed. Raises `ValueError` for a
        field name not found in `source`'s header row, an unknown
        aggregation function, or a pivot table name already in use.
        """
        m = _SHEET_QUALIFIED_RE.match(source)
        source_sheet_name = _unquote_odf_sheet_name(m.group("sheet")) if m.group("sheet") else self.name
        source_sheet = self if source_sheet_name == self.name else self.reader.sheet(source_sheet_name)
        row0, row1, col0, col1 = source_sheet._resolve_range(m.group("addr"))

        headers = [source_sheet.get_cell(row0, c).value for c in range(col0, col1 + 1)]
        rows = list(rows or ())
        columns = list(columns or ())
        values = dict(values or {})
        for field in rows + columns + list(values):
            if field not in headers:
                raise ValueError(f"field {field!r} not found in source header row {headers!r}")
        for field, func in values.items():
            if func not in _PIVOT_DATA_FUNCTIONS:
                raise ValueError(
                    f"unknown aggregation function {func!r} for field {field!r} - "
                    f"expected one of {sorted(_PIVOT_DATA_FUNCTIONS)}"
                )

        spreadsheet = self.reader.data.find("office:spreadsheet")
        tables = spreadsheet.find("table:data-pilot-tables")
        if tables is None:
            tables = _blank_template(self.reader.data, "table:data-pilot-tables")
            spreadsheet.append(tables)

        existing = tables.find_all("table:data-pilot-table", recursive=False)
        pivot_name = name or f"DataPilotTable{len(existing) + 1}"
        if any(t.get("table:name") == pivot_name for t in existing):
            raise ValueError(f"a pivot table named {pivot_name!r} already exists")

        pivot = _blank_template(self.reader.data, "table:data-pilot-table")
        pivot.attrs["table:name"] = pivot_name
        target_row0, _, target_col0, _ = self._resolve_range(target)
        pivot.attrs["table:target-range-address"] = (
            f"{_quote_odf_sheet_name(self.name)}.{Sheet.string_address(target_row0, target_col0)}"
        )

        source_range_tag = _blank_template(self.reader.data, "table:source-cell-range")
        source_range_tag.attrs["table:cell-range-address"] = (
            f"{_quote_odf_sheet_name(source_sheet_name)}."
            f"{Sheet.string_address(row0, col0)}:{Sheet.string_address(row1, col1)}"
        )
        pivot.append(source_range_tag)

        for field in rows:
            self._append_pivot_field(pivot, field, "row", "auto")
        for field in columns:
            self._append_pivot_field(pivot, field, "column", "auto")
        for field, func in values.items():
            self._append_pivot_field(pivot, field, "data", func)

        tables.append(pivot)

    def _append_pivot_field(self, pivot: Tag, field: str, orientation: str, function: str) -> None:
        field_tag = _blank_template(self.reader.data, "table:data-pilot-field")
        field_tag.attrs["table:source-field-name"] = field
        field_tag.attrs["table:orientation"] = orientation
        field_tag.attrs["table:function"] = function
        pivot.append(field_tag)

    def _empty_cell_template(self) -> Tag:
        """A detached, blank `<table:table-cell/>` tag."""
        return _blank_template(self.table, "table:table-cell")

    def _empty_row_template(self, n_cols: int) -> Tag:
        """A detached `<table:table-row>` tag with `n_cols` blank cells."""
        row = _blank_template(self.table, "table:table-row")
        for _ in range(n_cols):
            row.append(self._empty_cell_template())
        return row

    def grow_to(self, row: int, col: int) -> None:
        """Extend the sheet, if needed, so that (row, col) exists.

        Widens every existing row with new blank cells first (if `col` is past
        the current width), then appends new, full-width blank rows (if `row` is
        past the current height) — `sheet.size` reflects the new extent
        afterwards, and every newly created cell is a real, independently
        writable `Cell` (not the `EMPTY_CELL_BS` placeholder used for reads).
        """
        target_cols = max(col + 1, self.n_cols)
        extra_cols = target_cols - self.n_cols
        if extra_cols > 0:
            for r, cells_row in enumerate(self.rows):
                row_tag = cells_row[0].cell.parent
                for _ in range(extra_cols):
                    new_cell_tag = self._empty_cell_template()
                    row_tag.append(new_cell_tag)
                    cells_row.append(Cell(new_cell_tag, row=r, col=len(cells_row), sheet=self))
            self.n_cols = target_cols

        if row >= self.n_rows:
            # Captured *before* discarding whatever stray row currently sits in
            # the XML: on a sheet that's the only one in the whole document
            # (nothing else to fall back on), that stray row may be the only
            # namespace-template source available at all.
            row_template = self._empty_row_template(self.n_cols)
            self._discard_stray_rows()
            while row >= self.n_rows:
                new_row_tag = copy.deepcopy(row_template)
                self.table.append(new_row_tag)
                new_cells = [
                    Cell(cell_tag, row=self.n_rows, col=c, sheet=self)
                    for c, cell_tag in enumerate(new_row_tag.find_all(TAG_CELL))
                ]
                self.rows.append(new_cells)
                self.n_rows += 1

        self.size = (self.n_rows, self.n_cols)

    def _discard_stray_rows(self) -> None:
        """Remove any `<table:table-row>` physically present in the XML beyond
        what `self.rows` accounts for.

        `load()`'s cleanup (a lone blank row, a trimmed trailing empty row, a
        huge discarded repeated block...) only affects the in-memory `self.rows`
        view - it never touches the underlying XML. Appending new rows without
        first clearing these out would silently leave them in place, ready to
        resurface as extra "phantom" rows the next time the file is parsed.
        """
        anchor = self.rows[-1][0].cell.parent if self.n_rows > 0 else None
        stray = anchor.find_next_sibling("table:table-row") if anchor else self.table.find("table:table-row")
        while stray is not None:
            following = stray.find_next_sibling("table:table-row")
            stray.decompose()
            stray = following

    def delete_row(self, row: int) -> None:
        """Remove logical row `row` entirely, shifting every row below it
        up by one (`sheet.size` shrinks accordingly). Equivalent to
        `delete_rows([row])` - see there for the merge and formula
        semantics; when removing many rows, prefer one `delete_rows`
        call, which does the (document-wide) formula-reference
        adjustment once instead of once per row.
        """
        self.delete_rows([row])

    def delete_rows(self, rows: "Iterable[int]") -> None:
        """Remove every logical row in `rows` (0-based indexes as they
        currently are, duplicates ignored) in one operation, shifting the
        remaining rows up (`sheet.size` shrinks accordingly).

        Any merge intersecting a removed row (master, covered, or entirely
        on another row but spanning through it) is undone first (see
        `unmerge`) rather than left with a now-wrong span - there's no
        general way to "shrink" a span instead. Formula references
        elsewhere in the document that point at this sheet (this sheet's
        own formulas, and any other sheet's formula explicitly qualified
        with this sheet's name) are shifted in a single pass so they keep
        pointing at the same cells - see `_adjust_formulas_for_deletion`
        for what that does and doesn't cover. Raises `IndexError` if any
        index is out of range (nothing is removed in that case)."""
        targets = sorted(set(rows))
        if not targets:
            return
        for row in targets:
            if row < 0 or row >= self.n_rows:
                raise IndexError(f"row {row} out of range (sheet has {self.n_rows} rows)")
        for row in targets:
            c = 0
            while c < self.n_cols:
                if self.rows[row][c].is_merged:
                    self._unmerge(row, c)
                c += 1

        for row in reversed(targets):  # bottom-up: earlier indexes stay valid
            self._unrepeat_row(row)
            self.rows[row][0].cell.parent.decompose()
            del self.rows[row]
        for r in range(targets[0], len(self.rows)):
            for cell in self.rows[r]:
                cell.row = r
        self.n_rows = len(self.rows)
        self.size = (self.n_rows, self.n_cols)
        self._adjust_formulas_for_deletion(deleted_rows=targets)

    def delete_column(self, col: int) -> None:
        """Remove logical column `col` entirely, shifting every column to
        its right left by one (`sheet.size` shrinks accordingly).

        Any merge intersecting `col` is undone first, same as
        `delete_row`; formula references elsewhere in the document are
        adjusted the same way too - see `_adjust_formulas_for_deletion`.
        """
        if col < 0 or col >= self.n_cols:
            raise IndexError(f"column {col} out of range (sheet has {self.n_cols} columns)")
        r = 0
        while r < self.n_rows:
            if self.rows[r][col].is_merged:
                self._unmerge(r, col)
            r += 1

        for r in range(self.n_rows):
            self._unrepeat_col(r, col)
        self._unrepeat_column_tag(col).decompose()
        for r in range(self.n_rows):
            self.rows[r][col].cell.decompose()
            del self.rows[r][col]
            for c in range(col, len(self.rows[r])):
                self.rows[r][c].col = c
        self.n_cols -= 1
        self.size = (self.n_rows, self.n_cols)
        self._adjust_formulas_for_deletion(deleted_cols=[col])

    def _adjust_formulas_for_deletion(
        self, deleted_rows: "list[int] | None" = None, deleted_cols: "list[int] | None" = None
    ) -> None:
        """After physically removing the (sorted) `deleted_rows` (or
        `deleted_cols`) from this sheet, rewrite every formula in the whole
        document - this sheet's own, and any other sheet's formula that
        references into this sheet by name - so a reference past the
        removed positions still points at the same cell it did before
        (exactly one of the two is given, matching
        `delete_rows`/`delete_column`).

        A reference that pointed *exactly* at a removed row/column is
        left unchanged rather than modeled as a `#REF!`-style error - see
        the README's known limitations. No-op if this sheet has no owning
        `ODSReader` (nothing else to scan). One full-document sweep per
        call - which is why `delete_rows` batches N rows into one call.
        """
        if self.reader is None:
            return
        for sheet in self.reader.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell._formula is None:
                        continue
                    adjusted = _adjust_odf_formula_for_deletion(
                        cell._formula, self.name, sheet.name, deleted_rows, deleted_cols
                    )
                    if adjusted != cell._formula:
                        cell.formula = adjusted

    def __repr__(self) -> str:
        return f"Sheet(name='{self.name}', size[rows, cols]={self.size})"

    @property
    def style(self) -> "TableStyle | None":
        """This sheet's resolved, writable `TableStyle` (e.g. `.tab_color`
        - see `TableStyle`), or `None` if there's no owning `ODSReader`."""
        if self.reader is None:
            return None
        name = self.table.attrs.get("table:style-name")
        tag = self.reader._find_style(name, family="table") if name else None
        return TableStyle(tag, sheet=self)

    def row_style(self, row: int) -> "RowStyle | None":
        """The resolved, writable `RowStyle` for logical row `row` (see
        `RowStyle`), or `None` if `row` is out of range or there's no
        owning `ODSReader`."""
        if self.reader is None or row >= self.n_rows:
            return None
        row_tag = self.rows[row][0].cell.parent
        name = row_tag.attrs.get("table:style-name")
        tag = self.reader._find_style(name, family="table-row") if name else None
        return RowStyle(tag, sheet=self, row=row)

    def _find_column_tag(self, col: int) -> "Tag | None":
        """The `<table:table-column>` covering logical column `col`
        (accounting for `table:number-columns-repeated`), or `None`."""
        seen = 0
        for col_tag in self.table.find_all("table:table-column", recursive=False):
            n = int(col_tag.attrs.get("table:number-columns-repeated", "1"))
            if seen <= col < seen + n:
                return col_tag
            seen += n
        return None

    def _unrepeat_column_tag(self, col: int) -> Tag:
        """Split the `table:number-columns-repeated` column-definition tag
        covering `col` into one independent `<table:table-column>` per
        repetition (mirrors `_unrepeat_col`, but for column *definitions*
        rather than cell data). Returns the tag now covering `col` alone -
        already independent if it wasn't repeated to begin with."""
        col_tag = self._find_column_tag(col)
        assert col_tag is not None  # callers only ask for a column the sheet has
        n = int(col_tag.attrs.get("table:number-columns-repeated", "1"))
        if n <= 1:
            return col_tag

        seen = 0
        for tag in self.table.find_all("table:table-column", recursive=False):
            if tag is col_tag:
                start = seen
                break
            seen += int(tag.attrs.get("table:number-columns-repeated", "1"))

        copies = [copy.deepcopy(col_tag) for _ in range(n)]
        for c in copies:
            c.attrs.pop("table:number-columns-repeated", None)
        col_tag.replace_with(copies[0])
        prev = copies[0]
        for nxt in copies[1:]:
            prev.insert_after(nxt)
            prev = nxt
        return copies[col - start]

    def column_style(self, col: int) -> "ColumnStyle | None":
        """The resolved, writable `ColumnStyle` for logical column `col`
        (see `ColumnStyle`), or `None` if no `<table:table-column>` covers
        it or there's no owning `ODSReader`."""
        if self.reader is None:
            return None
        col_tag = self._find_column_tag(col)
        if col_tag is None:
            return None
        name = col_tag.attrs.get("table:style-name")
        tag = self.reader._find_style(name, family="table-column") if name else None
        return ColumnStyle(tag, sheet=self, col=col)

    def _fork_style(self, current_name: "str | None", family: str, prefix: str, props_tag_name: str) -> Tag:
        """The single, uniquely-owned automatic style (see
        `Cell._ensure_own_style` for the cell-level equivalent) behind one
        row/column/sheet's `table:style-name`.

        Unlike cell styles, row/column/sheet styles don't meaningfully use
        `style:parent-style-name` inheritance in practice (see `RowStyle`/
        `ColumnStyle`/`TableStyle`), so forking instead copies the current
        style's properties over verbatim - otherwise a later write of just
        one property (e.g. `.height`) would silently lose whatever else
        (e.g. `.visible`) the previously-shared style carried, since these
        are resolved from one single style tag rather than a chain."""
        if _is_forked_style_name(current_name, prefix):
            tag = self.reader._find_style(current_name, family=family)
            if tag is not None:
                return tag
        old_tag = self.reader._find_style(current_name, family=family) if current_name else None
        tag = self.reader._new_style_tag(family, prefix)
        if old_tag is not None:
            old_props = old_tag.find(props_tag_name)
            if old_props is not None:
                _ensure_style_child(tag, props_tag_name).attrs.update(old_props.attrs)
        return tag

    def _ensure_row_style(self, row: int) -> Tag:
        self._unrepeat_row(row)
        row_tag = self.rows[row][0].cell.parent
        tag = self._fork_style(
            row_tag.attrs.get("table:style-name"), "table-row", "ors", "style:table-row-properties"
        )
        row_tag.attrs["table:style-name"] = tag["style:name"]
        return tag

    def _ensure_column_style(self, col: int) -> Tag:
        col_tag = self._unrepeat_column_tag(col)
        tag = self._fork_style(
            col_tag.attrs.get("table:style-name"), "table-column", "ocos", "style:table-column-properties"
        )
        col_tag.attrs["table:style-name"] = tag["style:name"]
        return tag

    def _ensure_table_style(self) -> Tag:
        tag = self._fork_style(
            self.table.attrs.get("table:style-name"), "table", "ots", "style:table-properties"
        )
        self.table.attrs["table:style-name"] = tag["style:name"]
        return tag

    def empty_row(self, i: "int | None" = None, n_cols: "int | None" = None, start: int = 0, slice: "slice | None" = None) -> list[Cell]:
        step = 1
        if slice is not None:
            start, stop, step = self._unslice(slice)
        elif n_cols is not None:
            stop = n_cols
        else:
            stop = self.n_cols
        return [Cell(EMPTY_CELL_BS, cast(int, i), j, sheet=self) for j in range(start, stop, step)]

    def empty_col(self, j: "int | None" = None, n_rows: "int | None" = None, start: int = 0, slice: "slice | None" = None) -> list[list[Cell]]:
        step = 1
        if slice is not None:
            start, stop, step = self._unslice(slice)
        elif n_rows is not None:
            stop = n_rows
        else:
            stop = self.n_rows
        return [[Cell(EMPTY_CELL_BS, i, cast(int, j), sheet=self)] for i in range(start, stop, step)]

    def get_row(self, i: int) -> list[Cell]:
        if i >= self.n_rows:
            return self.empty_row(i)
        return self.rows[i]

    def get_rows(self, slice: slice) -> list[list[Cell]]:
        return [self.get_row(i) for i in range(*self._unslice(slice, row=True))]

    def get_cell(self, i: int, j: int) -> Cell:
        row = self.get_row(i)
        if j >= self.n_cols:
            return Cell(EMPTY_CELL_BS, i, j, sheet=self)
        else:
            return row[j]

    def _unslice(self, slice: slice, row: bool = False, col: bool = False) -> tuple[int, int, int]:
        start = slice.start
        if start is None:
            start = 0
        stop = slice.stop
        if stop is None:
            if row:
                stop = self.n_rows
            elif col:
                stop = self.n_cols
        step = slice.step
        if step is None:
            step = 1
        return start, stop, step

    def get_cells(self, row_slice: slice, col_slice: slice) -> list[list[Cell]]:
        return [
            [self.get_cell(i, j) for j in range(*self._unslice(col_slice, col=True))]
            for i in range(*self._unslice(row_slice, row=True))
        ]

    def get_row_slice(self, i: int, col_slice: slice) -> list[Cell]:
        return [self.get_cell(i, j) for j in range(*self._unslice(col_slice, col=True))]

    def get_col(self, j: int) -> list[list[Cell]]:
        if j >= self.n_cols:
            return self.empty_col(j)
        return [[row[j]] for row in self.rows]

    def get_cols(self, slice: slice) -> list[list[Cell]]:
        return [
            [row[j] for j in range(*self._unslice(slice, col=True))]
            for row in self.rows
        ]

    def __getitem__(self, address: "str | int | tuple[Any, ...] | slice") -> Any:
        """ROW en premier, COL en second (plus naturel, comme numpy, et correspond aux données)"""
        if type(address) is str:
            # "A2" ou "A2:B3" ou...
            address = self.address(address, self.n_rows)
        if type(address) is int:
            return ArrayValues(self.get_row(address))
        elif type(address) is slice:
            return ArrayValues(self.get_rows(address))
        elif type(address) is tuple and len(address) == 2:
            # 1, 2 ou 1:3, 2 ou 1:3, 2:5 ou 1, 2:5
            rows_address, cols_address = address
            if type(rows_address) is int and type(cols_address) is int:
                return self.get_cell(*address)
            elif type(rows_address) is int and type(cols_address) is slice:
                return ArrayValues(self.get_row_slice(rows_address, cols_address))
            elif type(rows_address) is slice and type(cols_address) is int:
                return ArrayValues(
                    self.get_cells(rows_address, slice(cols_address, cols_address + 1))
                )
            elif type(rows_address) is slice and type(cols_address) is slice:
                return ArrayValues(self.get_cells(rows_address, cols_address))
        raise ValueError(
            f"Format demandé non conforme ou données non définie dans le tableur : {address}"
        )

    def __iter__(self) -> Iterator[list[Cell]]:
        return iter(self.rows)

    @staticmethod
    def string_to_col(s: str) -> int:
        return string_to_col(s)

    @classmethod
    def address(cls, string: str, n_rows: int = 1) -> Union[int, Tuple[Any, ...], slice]:
        m = RE_STRING_CELL.fullmatch(string)
        if m is None:
            raise ValueError
        c1, r1, dp, c2, r2 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if c1 is not None:
            c1 = cls.string_to_col(c1)
        if r1 is not None:
            r1 = int(r1) - 1
        if c2 is not None:
            c2 = cls.string_to_col(c2) + 1
        if r2 is not None:
            r2 = int(r2)
        if dp is None and (r2 is not None or c2 is not None):
            raise ValueError
        if dp is None:
            if c1 is None:
                # 1
                return r1  # type: ignore[return-value]
            elif r1 is None:
                # A
                return slice(n_rows), c1
            else:
                # A1
                return r1, c1
        if (c1 is None and r2 is None) or (c2 is None and r1 is None):
            raise ValueError
        # dp is not None
        if (c1 is not None and c2 <= c1) or (r1 is not None and r2 <= r1):  # type: ignore[operator]
            raise ValueError
        if r1 is None:
            # A:B
            return slice(n_rows), slice(c1, c2)
        elif c1 is None:
            # 1:2
            return slice(r1, r2)
        elif r2 == r1 + 1 and c2 > c1 + 1:  # type: ignore[operator]
            # A1:B1
            return r1, slice(c1, c2)
        elif c2 == c1 + 1 and r2 > r1 + 1:  # type: ignore[operator]
            # A1:A2
            return slice(r1, r2), c1
        elif r2 == r1 + 1 and c2 == c1 + 1:
            # A1:A1 == A1
            return r1, c1
        return slice(r1, r2), slice(c1, c2)

    @classmethod
    def string_address(cls, row: int, col: int) -> str:
        # 0->A, 25->Z, 26->AA, 51->AZ, 52->BA (bijective base-26)
        return string_address(row, col)

    def to_list(self) -> Any:
        return self[:].to_list()

    def to_numpy(self) -> Any:
        return self[:].to_numpy()
