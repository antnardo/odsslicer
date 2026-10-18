# mypy: disable-error-code="union-attr"
# (`Element | None` unions are narrowed dynamically all over this module,
# guarded by runtime checks mypy can't see through - silencing that one error
# class here beats dozens of value-free asserts/casts. Every other error
# class, and all signatures, remain fully checked.)
"""Sheet: one table of the document, its grid of cells indexed numpy-style,
and the entry points of everything that reads or reshapes it.

`Sheet` did all of it itself until 1.0's audit, in some 1,900 lines: loading
the grid, merges, structural edits, copy and sort, pivot tables, the
styles of rows and columns, the formulas following an edit. Each is now a
module of its own, a class taking the sheet and doing one thing - the sheet
is the state they share, its grid (`rows`, `n_rows`, `n_cols`) and its
element (`table`):

- `loading._GridLoader` builds the grid from the element;
- `unrolling._Unroller` gives a position an element of its own, `merges._Merges`
  handles merged ranges, and `materialize_cell` - here - asks both;
- `growth._Growth` grows the grid and edits what lies past it,
  `structure._StructureEdits` inserts and deletes rows and columns;
- `copying._RangeCopy` and `_RangeSort` move what cells hold,
  `pivots._PivotTables` writes a pivot table's definition;
- `gridxml` and `columns` are the sheet's XML, functions on elements.

What stays here is the API, with its docstrings - every public method hands
its work to one of them - the indexing of the grid, and what a `Cell` and a
style view ask of their sheet: its row, column and table styles, the master
of a merge. The helpers are made per call: they hold no state of their own,
and a sheet's grid is never in two places.
"""

import logging
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, NoReturn, cast

from lxml import etree

from odsslicer.addresses import _parse_address
from odsslicer.cell import Cell
from odsslicer.columns import _column_definition_at, _unrepeat_column_definition
from odsslicer.constants import EMPTY_CELL
from odsslicer.copying import _RangeCopy, _RangeSort
from odsslicer.gridxml import _COLUMNS_REPEATED, _COVERED_CELL, _ROWS_REPEATED, _STYLE_NAME
from odsslicer.growth import _Growth
from odsslicer.loading import _GridLoader
from odsslicer.merges import _Merges
from odsslicer.pivots import _PivotFields, _PivotTables
from odsslicer.selection import ArrayValues
from odsslicer.structure import _StructureEdits
from odsslicer.styles import ColumnStyle, RowStyle, TableStyle
from odsslicer.unrolling import _Unroller
from odsslicer.xmltree import qn

if TYPE_CHECKING:
    from odsslicer.reader import ODSReader

__all__ = ["Sheet"]

logger = logging.getLogger("odsslicer")

_TABLE_NAME = qn("table:name")


class Sheet:
    def __init__(
        self, table: etree._Element, *, verbose: bool = False, reader: "ODSReader | None" = None
    ) -> None:
        self.verbose = verbose
        # chatty progress messages go to the "odsslicer" logger: DEBUG
        # normally, INFO with verbose=True - configure logging to see them
        self._log_level = logging.INFO if verbose else logging.DEBUG
        self.reader = reader
        self.table: etree._Element = table
        self.rows = self.load(table)
        if len(self.rows) > 0:
            rows_len = [len(row) for row in self.rows]
            self.n_rows, self.n_cols = len(self.rows), max(rows_len)
            if sum(self.n_cols - len_row for len_row in rows_len) > 0:
                logger.warning("At least one row does not have the same length as the others? %s", rows_len)
        else:
            self.n_rows, self.n_cols = 0, 0
        logger.log(self._log_level, "    %r", self)

    @property
    def name(self) -> str:
        """The sheet's name, its `table:name` - read from the element, where
        it used to be a copy taken on loading, which `ODSReader.rename_sheet`
        had to keep up to date. Renamed through `rename_sheet` alone, which
        also rewrites the formulas and charts that name the sheet."""
        return cast(str, self.table.get(_TABLE_NAME))

    @name.setter
    def name(self, value: NoReturn) -> NoReturn:
        # typed NoReturn, so that a type checker rejects the assignment too
        raise AttributeError(
            f"a sheet is renamed by reader.rename_sheet({self.name!r}, {value!r}), "
            "which also rewrites the formulas and charts that name it"
        )

    @property
    def size(self) -> "tuple[int, int]":
        """`(n_rows, n_cols)`: the grid's extent, which the structural edits
        change. Derived, where it used to be an attribute every edit had to
        set again after `n_rows` and `n_cols`."""
        return self.n_rows, self.n_cols

    @property
    def stylename(self) -> "str | None":
        """The name of the sheet's style, its `table:style-name` - read from
        the element, where it used to be a copy taken on loading, which a
        write to `style` left behind."""
        return self.table.get(_STYLE_NAME)

    def load(self, table_element: etree._Element) -> list[list[Cell]]:
        """The grid of `table_element`: a list of rows, each a list of `Cell`s,
        the padding applications write around the data left out (see
        `loading`)."""
        return _GridLoader(self).load(table_element)

    def materialize_cell(self, row: int, col: int) -> None:
        """Ensure the cell at (row, col) has its own, independent XML element,
        ready to be written.

        Repeatedly un-repeats the enclosing row, then the enclosing column, then
        un-merges the enclosing merged range if the cell is a covered one - its
        value would stay hidden otherwise - until the cell shares nothing with
        any other cell anymore. A merge's master keeps the merge, as LibreOffice
        keeps it when one types into it: undoing it made a long date written
        into a title merged across three columns show as ### (issue #19).
        Each step repoints every affected `Cell` object (via re-running its
        `__init__`) at its own new tag, so existing `Cell` references obtained
        before the call keep working correctly afterwards.
        """
        unroller = _Unroller(self)
        for _ in range(8):  # a handful of structural layers is more than any real file has
            tag = self.rows[row][col].cell
            parent = tag.getparent()
            if parent is not None and parent.get(_ROWS_REPEATED, "1") != "1":
                unroller.row(row)
            elif tag.get(_COLUMNS_REPEATED, "1") != "1":
                unroller.column(row, col)
            elif tag.tag == _COVERED_CELL:
                _Merges(self).unmerge_at(row, col)
            else:
                return
        raise RuntimeError(
            f"failed to materialize cell (row={row}, col={col}) as an independent element (internal error)"
        )

    def _merge_master(self, row: int, col: int) -> "Cell | None":
        """The top-left cell of the merged range covering (row, col) - what
        `Cell.merge_master` asks."""
        return _Merges(self).master_of(row, col)

    def _resolve_range(self, address: "str | int | tuple[Any, ...] | slice") -> tuple[int, int, int, int]:
        """Turn a range address - `"A1:C2"`, `"A1"`, a slice, or anything
        else `sheet[...]` accepts - into `(row0, row1, col0, col1)`
        (inclusive, 0-based) bounds, without actually reading any cell."""
        if isinstance(address, str):
            address = _parse_address(address, self.n_rows)
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
        any cell in it is already part of another merge (`unmerge(...)` it
        first). A range already merged the same way is left as it is."""
        _Merges(self).merge(address)

    def unmerge(self, address: "str | int | tuple[Any, ...] | slice") -> None:
        """Undo `merge(...)` for the merged range covering `address` (any
        single cell within it, master or covered) - every cell in the
        range becomes independent again, regaining whatever value/
        formatting ODF was keeping hidden underneath it.

        Raises `ValueError` if `address` doesn't resolve to a single cell,
        or if that cell isn't part of any merged range."""
        _Merges(self).unmerge(address)

    def copy(
        self, source: "str | int | tuple[Any, ...] | slice", dest: "str | int | tuple[Any, ...] | slice"
    ) -> None:
        """Copy the cells in `source` (any address `sheet[...]` accepts,
        e.g. `"A1:B2"` or a single cell) onto `dest` (the *top-left*
        address of the target range - the copy always has the same shape
        as `source`, any larger selection there is ignored) - like a
        spreadsheet's copy-paste: value, formula (shifted the same way
        `Cell.fill_formula` shifts it - a relative reference like `A1`
        moves with the copy, `$A$1` stays put), and style (see
        `Cell.style`'s setter - the destination points at the same
        underlying style as the source, forking its own private copy only
        once something on it is actually changed) all come along. The
        style is the one the source shows with, its column's default cell
        style if it has none of its own.

        Grows the sheet first if `dest` extends past its current extent.
        Safe when `source` and `dest` overlap (every source cell is read
        before any destination cell is written). A merged source cell
        copies whatever value/style it individually carries (its own
        hidden value, if it's a covered cell) - the merge itself is not
        replicated at the destination.
        """
        _RangeCopy(self).copy(source, dest)

    def sort(self, source: "str | int | tuple[Any, ...] | slice", by: int, *, ascending: bool = True) -> None:
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
        _RangeSort(self).sort(source, by, ascending=ascending)

    def create_pivot_table(
        self,
        source: str,
        target: str,
        *,
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
        `"varp"`. `name` defaults to `f"DataPilotTable{n}"`. All four are
        passed by name: `create_pivot_table("A1:C100", "E1",
        rows=["Category"], values={"Amount": "sum"})`.

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
        _PivotTables(self).create(source, target, _PivotFields.of(rows, columns, values), name)

    def grow_to(self, row: int, col: int) -> None:
        """Extend the sheet, if needed, so that (row, col) exists.

        Widens every existing row with new blank cells first (if `col` is past
        the current width), then appends new, full-width blank rows (if `row` is
        past the current height) — `sheet.size` reflects the new extent
        afterwards, and every newly created cell is a real `Cell` of the grid
        (not the `EMPTY_CELL` placeholder used for reads), which writing
        unrolls like any other (see `materialize_cell`).

        A row element is widened once, however many rows of the grid it
        stands for: every row of a run of repeated rows points at the same
        new cells, as `load` would have them (issue #5). The cells a row
        already has past the grid - trailing empty columns `load` left out
        of it - are taken back before any new one is added, formatting and
        all (issue #6), and so are the rows the file holds below the grid
        (issue #10).
        """
        _Growth(self).grow_to(row, col)

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
        pointing at the same cells - see `_remap_formula_references`
        for what that does and doesn't cover. Raises `IndexError` if any
        index is out of range (nothing is removed in that case)."""
        _StructureEdits(self).delete_rows(rows)

    def delete_column(self, col: int) -> None:
        """Remove logical column `col` entirely, shifting every column to
        its right left by one (`sheet.size` shrinks accordingly).

        Any merge intersecting `col` is undone first, same as
        `delete_row`; formula references elsewhere in the document are
        adjusted the same way too - see `_remap_formula_references`. The
        rows the file holds below the grid lose the column too (see
        `_delete_cells_past_grid`).
        """
        _StructureEdits(self).delete_column(col)

    def insert_row(self, row: int) -> None:
        """Insert one blank row before logical row `row` - equivalent to
        `insert_rows(row, 1)`, see there."""
        self.insert_rows(row, 1)

    def insert_rows(self, row: int, count: int = 1) -> None:
        """Insert `count` blank rows before logical row `row`, shifting it
        and every row below it down (`sheet.size` grows accordingly);
        `row == sheet.n_rows` inserts them below the grid, above whatever
        the file holds there.

        Behaves like a spreadsheet's "insert rows above":

        - formula references anywhere in the document that point at this
          sheet (its own formulas, and other sheets' formulas qualified
          with its name) move with the cells they point at - a range that
          straddles the insertion point stretches, see
          `_remap_formula_references`;
        - a merged range straddling the insertion point grows to include
          the new rows rather than being undone;
        - what the file holds below the grid - a chart or a shape anchored
          to a cell there, a formatted cell - moves down with the rows
          after `row` (issue #10). A sheet padded to its application's
          full height keeps it, its padding giving back the rows inserted.

        The new rows are blank: no values, no cell or row styles (copy
        formatting onto them with `copy` if needed). A formula whose
        references get rewritten loses its cached result, like any formula
        odsslicer writes - a spreadsheet recomputes it on open, and
        `save(recalculate=True)` fills it back in. Raises `IndexError` if
        `row` is out of `0..n_rows`, `ValueError` if `count < 1`."""
        _StructureEdits(self).insert_rows(row, count)

    def insert_column(self, col: int) -> None:
        """Insert one blank column before logical column `col` - equivalent
        to `insert_columns(col, 1)`, see there."""
        self.insert_columns(col, 1)

    def insert_columns(self, col: int, count: int = 1) -> None:
        """Insert `count` blank columns before logical column `col`,
        shifting it and every column to its right (`sheet.size` grows
        accordingly); `col == sheet.n_cols` appends them on the right.

        Same semantics as `insert_rows`: formula references follow the cells
        they point at, a merged range straddling the insertion point grows,
        what the file holds below the grid moves too, sideways. The column
        definitions (`<table:table-column>`, which carry widths and
        visibility) are shifted too, so every existing column keeps its own
        width; the new columns get default ones. Raises `IndexError` if
        `col` is out of `0..n_cols`, `ValueError` if `count < 1`."""
        _StructureEdits(self).insert_columns(col, count)

    def __repr__(self) -> str:
        return f"Sheet(name='{self.name}', size[rows, cols]={self.size})"

    @property
    def style(self) -> "TableStyle | None":
        """This sheet's resolved, writable `TableStyle` (e.g. `.tab_color`
        - see `TableStyle`), or `None` if there's no owning `ODSReader`."""
        if self.reader is None:
            return None
        return TableStyle(sheet=self)

    def _table_style_tag(self) -> "etree._Element | None":
        """The `<style:style>` this sheet's `table:style-name` names now."""
        return self.reader._catalog.find_style(self.table.get(_STYLE_NAME), family="table")

    def row_style(self, row: int) -> "RowStyle | None":
        """The resolved, writable `RowStyle` for logical row `row` (see
        `RowStyle`), or `None` if `row` is out of range or there's no
        owning `ODSReader`."""
        if self.reader is None or row >= self.n_rows:
            return None
        return RowStyle(sheet=self, row=row)

    def _row_style_tag(self, row: int) -> "etree._Element | None":
        """The `<style:style>` logical row `row`'s `table:style-name` names now."""
        row_tag = self.rows[row][0].cell.getparent()
        return self.reader._catalog.find_style(row_tag.get(_STYLE_NAME), family="table-row")

    # what may hold `<table:table-column>` elements, and what comes after them

    def column_style(self, col: int) -> "ColumnStyle | None":
        """The resolved, writable `ColumnStyle` for logical column `col`
        (see `ColumnStyle`), or `None` if no `<table:table-column>` covers
        it or there's no owning `ODSReader`."""
        if self.reader is None or _column_definition_at(self.table, col) is None:
            return None
        return ColumnStyle(sheet=self, col=col)

    def _column_style_tag(self, col: int) -> "etree._Element | None":
        """The `<style:style>` the `<table:table-column>` covering logical
        column `col` names now, `None` if none covers it."""
        col_tag = _column_definition_at(self.table, col)
        return (
            self.reader._catalog.find_style(col_tag.get(_STYLE_NAME), family="table-column")
            if col_tag is not None
            else None
        )

    def _ensure_row_style(self, row: int) -> etree._Element:
        _Unroller(self).row(row)
        row_tag = self.rows[row][0].cell.getparent()
        tag = self.reader._catalog.fork_sheet_part_style(
            row_tag.get(_STYLE_NAME), "table-row", "ors", "style:table-row-properties"
        )
        row_tag.set(_STYLE_NAME, tag.attrib[qn("style:name")])
        return tag

    def _ensure_column_style(self, col: int) -> etree._Element:
        col_tag = _unrepeat_column_definition(self.table, col)
        tag = self.reader._catalog.fork_sheet_part_style(
            col_tag.get(_STYLE_NAME), "table-column", "ocos", "style:table-column-properties"
        )
        col_tag.set(_STYLE_NAME, tag.attrib[qn("style:name")])
        return tag

    def _ensure_table_style(self) -> etree._Element:
        tag = self.reader._catalog.fork_sheet_part_style(
            self.table.get(_STYLE_NAME), "table", "ots", "style:table-properties"
        )
        self.table.set(_STYLE_NAME, tag.attrib[qn("style:name")])
        return tag

    def _cell(self, row: int, col: int) -> Cell:
        """The cell at `(row, col)`; past the data, a blank one reading from
        `EMPTY_CELL`, in no document, which writing to grows the sheet first
        (`Cell._prepare_for_write`)."""
        if row < self.n_rows and col < self.n_cols:
            return self.rows[row][col]
        return Cell(EMPTY_CELL, row, col, sheet=self)

    def _row(self, row: int) -> list[Cell]:
        """Row `row`'s cells: the sheet's own list, or blank ones past the data."""
        if row < self.n_rows:
            return self.rows[row]
        return [Cell(EMPTY_CELL, row, col, sheet=self) for col in range(self.n_cols)]

    def _unslice(self, slice: slice, *, row: bool = False, col: bool = False) -> tuple[int, int, int]:
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

    def _cells(self, rows: slice, cols: slice) -> list[list[Cell]]:
        return [
            [self._cell(i, j) for j in range(*self._unslice(cols, col=True))]
            for i in range(*self._unslice(rows, row=True))
        ]

    def __getitem__(self, address: "str | int | tuple[Any, ...] | slice") -> Any:
        """Row first, column second, as in numpy and as the data is laid out:
        `sheet[0, 1]`, `sheet[0]`, `sheet[:, 1]`, `sheet[0:3, 1:4]`, or a text
        address read by `_parse_address` - `sheet["B1"]`, `sheet["B1:D3"]`.
        A `Cell` for one position, an `ArrayValues` of them otherwise."""
        if type(address) is str:
            address = _parse_address(address, self.n_rows)
        if type(address) is int:
            return ArrayValues(self._row(address))
        if type(address) is slice:
            return ArrayValues([self._row(i) for i in range(*self._unslice(address, row=True))])
        if type(address) is tuple and len(address) == 2:
            rows_address, cols_address = address
            if type(rows_address) is int and type(cols_address) is int:
                return self._cell(rows_address, cols_address)
            if type(rows_address) is int and type(cols_address) is slice:
                return ArrayValues(
                    [self._cell(rows_address, j) for j in range(*self._unslice(cols_address, col=True))]
                )
            if type(rows_address) is slice and type(cols_address) is int:
                return ArrayValues(self._cells(rows_address, slice(cols_address, cols_address + 1)))
            if type(rows_address) is slice and type(cols_address) is slice:
                return ArrayValues(self._cells(rows_address, cols_address))
        raise ValueError(f"cannot index a sheet with {address!r}: expected an address, an index or a slice")

    def __iter__(self) -> Iterator[list[Cell]]:
        return iter(self.rows)

    def to_list(self) -> Any:
        return self[:].to_list()

    def to_numpy(self) -> Any:
        return self[:].to_numpy()
