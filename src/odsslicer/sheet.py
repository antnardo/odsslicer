# mypy: disable-error-code="union-attr"
# (`Element | None` unions are narrowed dynamically all over this module,
# guarded by runtime checks mypy can't see through - silencing that one error
# class here beats dozens of value-free asserts/casts. Every other error
# class, and all signatures, remain fully checked.)
"""Sheet: numpy-style indexing over one table, structural edits (grow, insert,
delete, copy, sort, merge), pivot definitions, row/column/table style access."""

import functools
import itertools
import logging
import warnings
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, cast

from lxml import etree

from .addresses import _parse_address, string_address
from .cell import ArrayValues, Cell
from .constants import (
    EMPTY_CELL,
    MAX_REPEAT_COLS,
    MAX_REPEAT_ROWS,
    TAG_CELL,
)
from .datetimes import _comparable
from .formulas import (
    _PIVOT_DATA_FUNCTIONS,
    _SHEET_QUALIFIED_RE,
    _deletion_remap,
    _insertion_remap,
    _quote_odf_sheet_name,
    _Remap,
    _remap_odf_formula_references,
    _shift_odf_formula,
    _unquote_odf_sheet_name,
)
from .styles import ColumnStyle, RowStyle, TableStyle
from .xmltree import (
    NAMESPACES,
    child,
    children,
    clone,
    descendants,
    elements,
    insert_after,
    insert_before,
    namespace_of,
    new,
    qn,
    remove,
    rename,
    unset,
)
from .xmlutils import _ensure_style_child, _is_forked_style_name

if TYPE_CHECKING:
    from .reader import ODSReader

logger = logging.getLogger("odsslicer")


# The attributes the structural edits read and write on every row and cell,
# by namespace URI: precomputed, since `load` reads them once per element
_ROWS_REPEATED = qn("table:number-rows-repeated")
_COLUMNS_REPEATED = qn("table:number-columns-repeated")
_ROWS_SPANNED = qn("table:number-rows-spanned")
_COLUMNS_SPANNED = qn("table:number-columns-spanned")
_STYLE_NAME = qn("table:style-name")


def _set_repeat(tag: etree._Element, attr: str, n: int) -> None:
    """Set a `table:number-*-repeated` attribute (`attr`, by namespace URI),
    omitting it for 1 (its default) the way applications write it."""
    if n == 1:
        unset(tag, attr)
    else:
        tag.set(attr, str(n))


def _repeat(tag: etree._Element, attr: str) -> int:
    """A `table:number-*-repeated` attribute's value (`attr`, by namespace
    URI): 1 when absent."""
    return int(tag.get(attr, "1"))


# Element names, by namespace URI, compared with `tag`: an element is what
# its namespace and local name say, whatever prefix the file binds, and one
# of another namespace that happens to share a local name is no row and no
# cell - LibreOffice skips it. `etree.QName(element).localname`, which
# compared local names alone until 1.0, also built an object per call, on
# the path that loads every row. A tuple where `iterchildren` takes the names
# as its arguments: lxml filters the children, rather than a Python loop.
_CELL_TAGS = tuple(qn(name) for name in TAG_CELL)
_ROW = qn("table:table-row")
_COVERED_CELL = qn("table:covered-table-cell")
# what holds a sheet's rows besides the sheet itself: header rows, and row
# groups at any depth
_ROW_CONTAINERS = frozenset(
    qn(name) for name in ("table:table-rows", "table:table-header-rows", "table:table-row-group")
)
_ROW_LEVEL = (_ROW, *_ROW_CONTAINERS)
_COLUMN = qn("table:table-column")
_COLUMN_GROUPS = frozenset(
    qn(name) for name in ("table:table-columns", "table:table-header-columns", "table:table-column-group")
)
# what `_holds_content` reads, see there
_VALUE_TYPE = qn("office:value-type")
_VALUE = qn("office:value")
_FORMULA = qn("table:formula")
_PARAGRAPH_OR_NOTE = (qn("text:p"), qn("office:annotation"))


def _row_cells(row: etree._Element) -> list[etree._Element]:
    """The cells of `row`: its children. Searching every descendant went
    into each cell's content as well, where a frame may hold a table of its
    own, and counted that table's cells in the row - a column the sheet does
    not have, which LibreOffice does not show either."""
    return list(row.iterchildren(*_CELL_TAGS))


def _sheet_rows(parent: etree._Element) -> list[etree._Element]:
    """The `<table:table-row>` elements of `parent` - a sheet, or a group of
    rows - in order, through header rows and row groups at any depth, and
    never inside a cell: a cell may hold a table of its own, whose rows a
    search of every descendant counted among the sheet's."""
    found: list[etree._Element] = []
    for element in parent.iterchildren(*_ROW_LEVEL):
        if element.tag == _ROW:
            found.append(element)
        else:
            found.extend(_sheet_rows(element))
    return found


def _holds_content(cell_tag: etree._Element) -> bool:
    """Whether the cell element `cell_tag` holds what LibreOffice counts in
    a sheet's used area, formatting aside: a value, a text or a formula - what
    makes `Cell.is_empty` false - or a note (issue #10). The grid reaches
    every such cell. Formatting stays out on purpose: files carry it in the
    rows padding a sheet to its full height, down to the millionth, and a
    formatted row the grid leaves out stays in the file all the same (see
    `grow_to`).

    Read from the element, without building a `Cell`: `load` asks it of
    every element before the grid exists, and a `Cell` built to answer it,
    then another for the grid, made `Cell.__init__` most of the loading time.
    A `Cell` is empty when the element has no `office:value-type`, no
    `office:value`, no formula and no paragraph of its own (`Cell.__init__`:
    with no type, the value is `None` whatever is written); a test holds the
    two answers equal."""
    return (
        cell_tag.get(_VALUE_TYPE) is not None
        or cell_tag.get(_VALUE) is not None
        or cell_tag.get(_FORMULA) is not None
        or next(cell_tag.iterchildren(*_PARAGRAPH_OR_NOTE), None) is not None
    )


# the namespaces of what a cell may hold anchored to it: shapes, charts and
# images (draw:frame), 3D scenes
_DRAWING_NAMESPACES = frozenset({NAMESPACES["draw"], NAMESPACES["dr3d"]})


def _carries_formatting(cell_tag: etree._Element) -> bool:
    """True if the element says anything about the cell beyond how many
    columns it stands for - a style, most of all. Such a cell holds no
    content, yet deleting it loses the formatting of a row formatted as a
    whole (issue #23)."""
    return bool(set(cell_tag.attrib) - {_COLUMNS_REPEATED})


def _drawings_in(cell_tag: etree._Element) -> "list[etree._Element]":
    """What is anchored in `cell_tag`: a chart, a shape, an image."""
    return [element for element in elements(cell_tag) if namespace_of(element) in _DRAWING_NAMESPACES]


def _rescue_drawings(cell_tag: etree._Element, heir: "etree._Element | None") -> None:
    """Move what is anchored in `cell_tag` into `heir`, the cell taking its
    place, so deleting a row or a column does not delete the chart anchored
    there - LibreOffice keeps it, at the same address (issue #24). Nothing
    to do it with, and it goes with the cell, as it did everywhere."""
    if heir is None:
        return
    for drawing in _drawings_in(cell_tag):
        heir.append(remove(drawing))


def _insert_chain(reference: etree._Element, new_elements: "Iterable[etree._Element]") -> None:
    """Put `new_elements` right after `reference`, in order, each after the
    one before: `insert_after`, which costs nothing in lxml - `addnext`
    relinks a node.

    Never by index. lxml's `parent.insert(i, el)` and `parent.index(el)`
    walk the parent's children to `i`, and so did bs4's `insert_after`,
    which looked `reference` up with `parent.index()`: placing rows one
    after the other below a sheet's last row scanned the whole sheet each
    time (minutes instead of seconds for 100,000 rows written past the data,
    0.14.0 to 0.14.3). 0.15.0 inserted by an index found once, right on bs4
    only: in lxml, 20,000 rows 50,000 rows down take 2.6 s by index, 9 ms
    as a chain (`benchmarks/LXML_STUDY.md`)."""
    for element in new_elements:
        insert_after(reference, element)
        reference = element


def _split_repetitions(tag: etree._Element, attr: str, n: int) -> list[etree._Element]:
    """Replace `tag`, repeated `n` times by `attr` (by namespace URI), with
    `n` copies of it that no longer carry `attr`, in place. Returns the
    copies, in order.

    The copies go in as a chain after `tag`, which then leaves (see
    `_insert_chain`): placed by index, or each after the one before with
    bs4's `insert_after`, splitting a run of n rows p rows down cost
    n × (p + n/2) comparisons - 23 s to write one cell in a run of 20,000
    repeated rows below 50,000 others, 1.6 s to set the width of a column
    defined by one `<table:table-column>` repeated 16,384 times, as
    LibreOffice pads a sheet."""
    copies = [clone(tag) for _ in range(n)]
    for copy_tag in copies:
        unset(copy_tag, attr)
    _insert_chain(tag, copies)
    remove(tag)
    return copies


# The sizes applications pad a sheet to with repeated blank rows and cells:
# 1,048,576 rows - 65,536 before LibreOffice 3.3 and Excel 2007 - and 16,384
# columns - 1,024 before LibreOffice 7.4. A sheet padded to one of them keeps
# it through insertions: LibreOffice drops what an insertion pushed past its
# maximum on open, with a warning. Any other size is no maximum, and grows.
_FULL_HEIGHTS = frozenset({65_536, 1_048_576})
_FULL_WIDTHS = frozenset({1_024, 16_384})


def _give_back(tags: "list[etree._Element]", attr: str, count: int) -> None:
    """Take `count` repetitions (`attr`) off the elements of `tags` - rows,
    cells or column definitions past the grid - from the last, each keeping
    one: what an insertion pushed past a sheet's full size comes off the
    padding at its end. Only a blank element is repeated there: a chart, a
    note, a value stands in one cell of one row."""
    for tag in reversed(tags):
        if count == 0:
            return
        n = _repeat(tag, attr)
        taken = min(count, n - 1)
        _set_repeat(tag, attr, n - taken)
        count -= taken


class Sheet:
    def __init__(
        self, table: etree._Element, verbose: bool = False, reader: "ODSReader | None" = None
    ) -> None:
        self.verbose = verbose
        # chatty progress messages go to the "odsslicer" logger: DEBUG
        # normally, INFO with verbose=True - configure logging to see them
        self._log_level = logging.INFO if verbose else logging.DEBUG
        self.reader = reader
        self.table: etree._Element = table
        self.name: str = cast(str, table.attrib[qn("table:name")])
        self.stylename = table.get(_STYLE_NAME)
        self.rows = self.load(table)
        if len(self.rows) > 0:
            rows_len = [len(row) for row in self.rows]
            self.size = (len(self.rows), max(rows_len))
            n_cols = self.size[1]
            if sum(n_cols - len_row for len_row in rows_len) > 0:
                logger.warning("At least one row does not have the same length as the others? %s", rows_len)
        else:
            self.size = (0, 0)
        self.n_rows, self.n_cols = self.size
        logger.log(self._log_level, "    %r", self)

    def load(self, table_element: etree._Element) -> list[list[Cell]]:
        table = []
        unreadable: list[Cell] = []  # read as their text instead, see Cell.__init__
        rows = _sheet_rows(table_element)
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
        # Each row is measured once, and both passes read the measure: what
        # each cell repeats over, and where its trailing empty cells start.
        def measure(cells_bs: "list[etree._Element]") -> "tuple[list[int], int, int]":
            """(each cell's repeat, the start of the trailing run of empty
            cells, the width up to the first huge cell of that run)."""
            repeats = [int(cell.get(_COLUMNS_REPEATED, "1")) for cell in cells_bs]
            suffix_start = len(cells_bs)
            while suffix_start > 0 and not _holds_content(cells_bs[suffix_start - 1]):
                suffix_start -= 1
            width = 0
            for k, n_cols in enumerate(repeats):
                if k >= suffix_start and n_cols > MAX_REPEAT_ROWS:
                    break
                width += n_cols
            return repeats, suffix_start, width

        # Its row-direction counterpart: a row repeated absurdly many times
        # whose cells are all empty is grid filler too (Excel and LibreOffice
        # both write one to declare the sheet's full 1,048,576-row height) -
        # but only below the last row holding anything. Above it, empty rows
        # are real, however many: leaving them out would move every row
        # below them up (issue #6). The grid ends at the first filler.
        # Each row's cells, listed once: every pass below walks them.
        row_cells = [_row_cells(row) for row in rows]

        def is_empty_row(k: int) -> bool:
            return not any(_holds_content(c) for c in row_cells[k])

        last_content = len(rows) - 1
        while last_content >= 0 and is_empty_row(last_content):
            last_content -= 1
        fillers = (
            k
            for k in range(last_content + 1, len(rows))
            if _repeat(rows[k], _ROWS_REPEATED) > MAX_REPEAT_ROWS
        )
        end = next(fillers, len(rows))

        measures = [measure(cells_bs) for cells_bs in row_cells[:end]]
        real_width = max((width for _, _, width in measures), default=0)
        normalized = []
        i = 0
        for row, cells_bs, (repeats, suffix_start, _) in zip(
            rows[:end], row_cells[:end], measures, strict=True
        ):
            normalized.append(self._normalize_row_width(row, cells_bs, repeats, suffix_start, real_width, i))
            i += _repeat(row, _ROWS_REPEATED)

        # Trailing columns empty in every row are left out of the grid when
        # there are more than MAX_REPEAT_COLS of them (see the clean-up
        # below). Decided here, on the elements, before a `Cell` is built for
        # each position: LibreOffice writes empty cells as far as the last
        # formatted column, and a sheet formatted to its 1,021st column but
        # holding 58 had 150,000 cells built to keep 8,500. Whether an element
        # holds content does not depend on where it stands, so one look per
        # element answers for every column it repeats over.
        built_width = real_width
        content_end = 0
        for pairs in normalized:
            col = 0
            for cell, n_cols in pairs:
                col += n_cols
                if col > content_end and _holds_content(cell):
                    content_end = col
        # an all-empty sheet keeps the general path below, which takes it apart
        if content_end > 0 and real_width - content_end > MAX_REPEAT_COLS:
            built_width = content_end

        # One `Cell` per position, and no other: the grid's. The positions
        # an element repeats over read alike, and all but the first are
        # copies of it (`Cell._at`), save a time value, whose reading may
        # depend on the column (`Cell._counts_elapsed_time`).
        i = 0
        for row, pairs in zip(rows[:end], normalized, strict=True):
            n_rows = int(row.get(_ROWS_REPEATED, "1"))
            first: list[Cell | None] = [None] * len(pairs)
            for _ in range(n_rows):
                cells = []
                j = 0
                for k, (cell, n_cols) in enumerate(pairs):
                    template = first[k]
                    for _ in range(min(n_cols, built_width - j)):
                        if template is None:
                            built = Cell(cell, i, j, self)
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
                table.append(cells)
                i += 1
        if end < len(rows):
            logger.log(
                self._log_level,
                "    Row [%04d] repeated %d > MAX = %d, empty to the end: grid ends",
                i + 1,
                _repeat(rows[end], _ROWS_REPEATED),
                MAX_REPEAT_ROWS,
            )
        if unreadable:
            self._warn_unreadable(unreadable)
        # CLEAN UP
        # What the grid leaves out below stays in the file, and must stay out
        # of the elements the grid points at: `_unrepeat_row` gives a copy
        # to every repetition of an element, and `grow_to` takes rows back
        # from the element after the grid's last (issues #5, #10).
        if not table or not table[0]:
            return []
        # The empty row trimmed here is the sheet's last, which LibreOffice
        # writes apart - after the filler, if there is one. The grid ends
        # before, and has nothing to trim: its last row is a real one. A row
        # with no content may still hold a chart or formatting: it stays in
        # the file, past the grid.
        after_filler = rows[end + 1 :]
        written_apart = any(_repeat(row, _ROWS_REPEATED) <= MAX_REPEAT_ROWS for row in after_filler)
        if not written_apart and not any(_holds_content(cell.cell) for cell in table[-1]):
            row_tag = cast(etree._Element, table[-1][0].cell.getparent())
            keep = len(self._run_in(table, len(table) - 1)) - 1
            table.pop()
            logger.log(self._log_level, "    Last row empty: removed")
            self._split_row_element(row_tag, keep)
        if len(table) == 0:
            return table
        # Trailing columns empty in every row are left out of the grid when
        # there are more than MAX_REPEAT_COLS of them: LibreOffice writes
        # empty cells as far as the last formatted column, say. Only
        # trailing ones - empty columns between data are real, and leaving
        # them out would move every column to their right (issue #6). They
        # stay in the file, formatting and all: each row element is split at
        # the grid's edge, so that none straddles it, and `grow_to` takes
        # them back as the sheet grows.
        n_cols = real_width
        width = len(table[0])
        while width > 0 and not any(_holds_content(row[width - 1].cell) for row in table):
            width -= 1
        if n_cols - width > MAX_REPEAT_COLS:
            logger.log(self._log_level, "    Cols %d to %d empty: left out", width + 1, n_cols)
            for run in self._runs_in(table):
                self._split_cells_at(cast(etree._Element, table[run.start][0].cell.getparent()), width)
            # no column left: every row is empty, and so is the sheet
            table = [row[:width] for row in table] if width > 0 else []
        return table

    @staticmethod
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

    @classmethod
    def _runs_in(cls, rows: "list[list[Cell]]") -> "list[range]":
        """`rows`, grouped into runs (see `_run_in`), in order."""
        runs: list[range] = []
        start = 0
        while start < len(rows):
            runs.append(cls._run_in(rows, start))
            start = runs[-1].stop
        return runs

    def _run_of(self, row: int) -> range:
        """The grid's rows sharing `row`'s element - see `_run_in`."""
        return self._run_in(self.rows, row)

    @staticmethod
    def _split_row_element(row_tag: etree._Element, keep: int) -> None:
        """Split a repeated `<table:table-row>` after its first `keep`
        repetitions: the element keeps those, since the grid's cells point
        at its cells, and a copy inserted after it takes the others."""
        attr = _ROWS_REPEATED
        n = _repeat(row_tag, attr)
        if not 0 < keep < n:
            return
        rest = clone(row_tag)
        _set_repeat(row_tag, attr, keep)
        _set_repeat(rest, attr, n - keep)
        insert_after(row_tag, rest)

    @staticmethod
    def _split_cells_at(row_tag: etree._Element, position: int) -> None:
        """Make logical column `position` of `row_tag` start a cell element
        of its own, splitting the repeated cell straddling it if there is
        one: the element keeps the columns before `position` and a copy
        inserted after it takes the others, as in `_split_row_element`."""
        attr = _COLUMNS_REPEATED
        start = 0
        for cell_tag in _row_cells(row_tag):
            n = _repeat(cell_tag, attr)
            if start < position < start + n:
                rest = clone(cell_tag)
                _set_repeat(cell_tag, attr, position - start)
                _set_repeat(rest, attr, start + n - position)
                insert_after(cell_tag, rest)
            if start + n >= position:
                return
            start += n

    @classmethod
    def _cell_starting_at(cls, row_tag: etree._Element, col: int) -> etree._Element:
        """The cell element standing for logical column `col` of `row_tag`
        alone, split out of a repeated one if need be, and created - after
        empty cells up to it - when the row element ends before `col`: where
        a drawing anchored past the grid's columns goes."""
        cls._split_cells_at(row_tag, col)
        cls._split_cells_at(row_tag, col + 1)
        start = 0
        for cell_tag in _row_cells(row_tag):
            if start == col:
                return cell_tag
            start += _repeat(cell_tag, _COLUMNS_REPEATED)
        if start < col:
            padding = new("table:table-cell")
            _set_repeat(padding, _COLUMNS_REPEATED, col - start)
            row_tag.append(padding)
        cell_tag = new("table:table-cell")
        row_tag.append(cell_tag)
        return cell_tag

    def _warn_unreadable(self, cells: list[Cell]) -> None:
        """One warning for all the cells of this sheet whose value could not
        be read as its declared type - one per cell would bury the user
        under a generator's systematic quirk, repeated all down a column."""
        listed = ", ".join(f"{cell.address} ({cell._unreadable})" for cell in cells[:3])
        if len(cells) > 3:
            listed += f" and {len(cells) - 3} more"
        warnings.warn(
            f"sheet {self.name!r}: {listed}: not readable as the declared type,"
            " read as the displayed text instead (cell.raw_value keeps the value"
            " as written)",
            stacklevel=1,
        )

    def _normalize_row_width(
        self,
        row_tag: etree._Element,
        cells_bs: "list[etree._Element]",
        repeats: "list[int]",
        suffix_start: int,
        real_width: int,
        row_index: int,
    ) -> "list[tuple[etree._Element, int]]":
        """Rewrite one row's XML so its total width is exactly `real_width`
        cells, touching only its trailing run of *empty* cells, which starts
        at `suffix_start`: a huge grid filler (see `load`) has its repeat
        count reduced, and a row that falls short is padded with a blank
        repeated cell. Rows already the right width come back untouched.
        `repeats` is what each cell of `cells_bs` repeats over. Returns the
        row's cell tags of the grid, after the rewrite, each with the number
        of columns it stands for.

        What a trailing cell says about itself is kept: one carrying a style
        stays in the file, out of the grid, where it used to be deleted -
        LibreOffice writes a row formatted as a whole as its cells followed by
        one repeated to the last column, and a plain save dropped that
        formatting (issue #23). `grow_to` takes such a cell back into the grid,
        formatting and all, when the sheet widens (issue #6)."""
        width = 0
        for k, (cell, n_cols) in enumerate(zip(cells_bs, repeats, strict=True)):
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
                row_index + 1,
                n_cols,
                real_width,
            )
            keep = list(zip(cells_bs[:k], repeats[:k], strict=True))
            if missing > 0:
                _set_repeat(cell, _COLUMNS_REPEATED, missing)
                keep.append((cell, missing))
                remainder = n_cols - missing
                if remainder > 0 and _carries_formatting(cell):
                    # the part past the grid keeps saying what it says
                    rest = clone(cell)
                    _set_repeat(rest, _COLUMNS_REPEATED, remainder)
                    insert_after(cell, rest)
                dropped = cells_bs[k + 1 :]
            else:
                dropped = cells_bs[k:]
            for extra in dropped:
                if not _carries_formatting(extra):
                    remove(extra)
            return keep
        pairs = list(zip(cells_bs, repeats, strict=True))
        if width < real_width:
            pad = self._empty_cell_template()
            if real_width - width > 1:
                pad.set(_COLUMNS_REPEATED, str(real_width - width))
            row_tag.append(pad)
            pairs.append((pad, real_width - width))
        return pairs

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
        for _ in range(8):  # a handful of structural layers is more than any real file has
            cell = self.rows[row][col]
            tag = cell.cell
            parent = tag.getparent()
            if parent is not None and parent.get(_ROWS_REPEATED, "1") != "1":
                self._unrepeat_row(row)
                continue
            if tag.get(_COLUMNS_REPEATED, "1") != "1":
                self._unrepeat_col(row, col)
                continue
            if tag.tag == _COVERED_CELL:
                self._unmerge(row, col)
                continue
            return
        raise RuntimeError(
            f"failed to materialize cell (row={row}, col={col}) as an independent element (internal error)"
        )

    def _unrepeat_row(self, row: int) -> None:
        """Split the `table:number-rows-repeated` row tag covering `row` into
        one independent `<table:table-row>` per repetition."""
        row_tag = cast(etree._Element, self.rows[row][0].cell.getparent())
        n = int(row_tag.get(_ROWS_REPEATED, "1"))
        if n <= 1:
            return

        run = self._run_of(row)
        copies = _split_repetitions(row_tag, _ROWS_REPEATED, n)

        for r, copy_tag in zip(run, copies, strict=True):
            j = 0
            for cell_tag in _row_cells(copy_tag):
                n_cols = int(cell_tag.get(_COLUMNS_REPEATED, "1"))
                for _ in range(n_cols):
                    if j < len(self.rows[r]):
                        self.rows[r][j].__init__(cell_tag, row=r, col=j, sheet=self)  # type: ignore[misc]
                    j += 1

    def _unrepeat_col(self, row: int, col: int) -> None:
        """Split the `table:number-columns-repeated` cell tag covering (row, col)
        into one independent `<table:table-cell>` per repetition - for every
        row of the run sharing it (see `_run_in`), which the split reaches
        all the same."""
        cell_tag = self.rows[row][col].cell
        n = int(cell_tag.get(_COLUMNS_REPEATED, "1"))
        if n <= 1:
            return

        start = col
        while start > 0 and self.rows[row][start - 1].cell is cell_tag:
            start -= 1
        run = self._run_of(row)  # before the split detaches `cell_tag`

        copies = _split_repetitions(cell_tag, _COLUMNS_REPEATED, n)

        for r in run:
            for c, copy_tag in enumerate(copies, start=start):
                self.rows[r][c].__init__(copy_tag, row=r, col=c, sheet=self)  # type: ignore[misc]

    @staticmethod
    def _is_merge_master(cell: Cell) -> bool:
        return cell.cell.get(_COLUMNS_SPANNED, "1") != "1" or cell.cell.get(_ROWS_SPANNED, "1") != "1"

    def _find_merge_master(self, row: int, col: int) -> "Cell | None":
        """Find the top-left cell of the merged range covering (row, col)."""
        for r in range(row, -1, -1):
            for master in self.rows[r]:
                if master.cell.tag == _COVERED_CELL or not self._is_merge_master(master):
                    continue
                rows_span = int(master.cell.get(_ROWS_SPANNED, "1"))
                cols_span = int(master.cell.get(_COLUMNS_SPANNED, "1"))
                if master.row <= row < master.row + rows_span and master.col <= col < master.col + cols_span:
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
        if cell.cell.tag == _COVERED_CELL:
            master = self._find_merge_master(row, col)
            if master is None:
                raise RuntimeError(
                    f"cell {cell.address} looks merged/covered but its top-left "
                    "cell could not be found (internal error)"
                )
        else:
            master = cell

        mr, mc = master.row, master.col
        rows_span = int(master.cell.get(_ROWS_SPANNED, "1"))
        cols_span = int(master.cell.get(_COLUMNS_SPANNED, "1"))
        # a merge can run into trailing columns the grid leaves out (see
        # `load`): every cell it covers is about to become one of the grid's
        self.grow_to(mr + rows_span - 1, mc + cols_span - 1)
        unset(master.cell, _ROWS_SPANNED)
        unset(master.cell, _COLUMNS_SPANNED)
        master.__init__(master.cell, row=mr, col=mc, sheet=self)  # type: ignore[misc]

        for r in range(mr, mr + rows_span):
            for c in range(mc, mc + cols_span):
                if r == mr and c == mc:
                    continue
                covered = self.rows[r][c]
                if covered.cell.tag == _COVERED_CELL:
                    rename(covered.cell, "table:table-cell")
                covered.__init__(covered.cell, row=r, col=c, sheet=self)  # type: ignore[misc]

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
        row0, row1, col0, col1 = self._resolve_range(address)
        if row0 == row1 and col0 == col1:
            raise ValueError(f"{address!r} is a single cell - nothing to merge")
        if (
            row1 < self.n_rows
            and col1 < self.n_cols
            and self.rows[row0][col0].is_merge_master
            and self.rows[row0][col0].merge_span == (row1 - row0 + 1, col1 - col0 + 1)
        ):
            # In 0.13, writing into the master undid the merge, and the documented
            # remedy was to merge the range again after the write: now that the
            # write keeps the merge, that code would raise here.
            return
        self.grow_to(row1, col1)
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                if self.rows[r][c].is_merged:
                    raise ValueError(
                        f"cell {string_address(r, c)} is already part of a merged range - unmerge it first"
                    )
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                self.materialize_cell(r, c)

        master = self.rows[row0][col0]
        master.cell.set(_ROWS_SPANNED, str(row1 - row0 + 1))
        master.cell.set(_COLUMNS_SPANNED, str(col1 - col0 + 1))
        for r in range(row0, row1 + 1):
            for c in range(col0, col1 + 1):
                if r == row0 and c == col0:
                    continue
                covered = self.rows[r][c]
                rename(covered.cell, "table:covered-table-cell")
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
            raise ValueError(f"cell {string_address(row0, col0)} is not part of a merged range")
        self._unmerge(row0, col0)

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
        row0, row1, col0, col1 = self._resolve_range(source)
        dest_row0, _, dest_col0, _ = self._resolve_range(dest)
        n_rows, n_cols = row1 - row0 + 1, col1 - col0 + 1
        self.grow_to(dest_row0 + n_rows - 1, dest_col0 + n_cols - 1)

        snapshot = [
            [
                (
                    self._cell(r, c).value,
                    self._cell(r, c).formula,
                    # the style it shows with, its column's if it has none
                    self._cell(r, c)._style_name(),
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
                # the style first, as a value reads back through its format - a
                # time of day as a duration in a [HH]:MM one (issue #20); a cell
                # showing with it already, through its column, is left without
                # a style of its own
                if target._style_name() != style_name:
                    target.style = style_name
                if formula is not None:
                    target._write_formula(_shift_odf_formula(formula, drow, dcol))
                else:
                    target.value = value

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

        # `Cell._style_name`, each column's default cell style looked up once:
        # a cell with no style of its own asks its column for one, by a walk
        # of the column definitions, twice per cell here - which made sorting
        # 1,000 rows slower than with bs4. Nothing here changes the columns.
        column_defaults: dict[int, str | None] = {}

        def style_name(cell: Cell) -> "str | None":
            own = cell.cell.get(_STYLE_NAME)
            if own is not None:
                return own
            if cell.col not in column_defaults:
                column_defaults[cell.col] = self._column_default_cell_style(cell.col)
            return column_defaults[cell.col]

        snapshot = [
            (
                r,
                [
                    (
                        self.rows[r][c].value,
                        self.rows[r][c].formula,
                        style_name(self.rows[r][c]),  # as in `copy`
                    )
                    for c in range(col0, col1 + 1)
                ],
            )
            for r in range(row0, row1 + 1)
        ]
        key_offset = by - col0

        def compare(
            a: tuple[int, list[tuple[Any, Any, Any]]], b: tuple[int, list[tuple[Any, Any, Any]]]
        ) -> int:
            # _comparable: a column of dates can hold date-times too, and one
            # of times durations - Python refuses to order either pair as is
            va, vb = _comparable(a[1][key_offset][0]), _comparable(b[1][key_offset][0])
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
            for j, (value, formula, target_style) in enumerate(row_data):
                target = self.rows[new_row][col0 + j]
                if style_name(target) != target_style:  # as in `copy`
                    target.style = target_style
                if formula is not None:
                    target._write_formula(_shift_odf_formula(formula, drow, 0))
                else:
                    target.value = value

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

        headers = [source_sheet._cell(row0, c).value for c in range(col0, col1 + 1)]
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

        spreadsheet = cast(etree._Element, self.reader._spreadsheet())
        tables = child(spreadsheet, "table:data-pilot-tables")
        if tables is None:
            tables = new("table:data-pilot-tables")
            spreadsheet.append(tables)

        existing = children(tables, "table:data-pilot-table")
        pivot_name = name or f"DataPilotTable{len(existing) + 1}"
        if any(t.get(qn("table:name")) == pivot_name for t in existing):
            raise ValueError(f"a pivot table named {pivot_name!r} already exists")

        pivot = new("table:data-pilot-table")
        pivot.set(qn("table:name"), pivot_name)
        target_row0, _, target_col0, _ = self._resolve_range(target)
        pivot.set(
            qn("table:target-range-address"),
            f"{_quote_odf_sheet_name(self.name)}.{string_address(target_row0, target_col0)}",
        )

        source_range_tag = new("table:source-cell-range")
        source_range_tag.set(
            qn("table:cell-range-address"),
            f"{_quote_odf_sheet_name(source_sheet_name)}."
            f"{string_address(row0, col0)}:{string_address(row1, col1)}",
        )
        pivot.append(source_range_tag)

        for field in rows:
            self._append_pivot_field(pivot, field, "row", "auto")
        for field in columns:
            self._append_pivot_field(pivot, field, "column", "auto")
        for field, func in values.items():
            self._append_pivot_field(pivot, field, "data", func)

        tables.append(pivot)

    def _append_pivot_field(self, pivot: etree._Element, field: str, orientation: str, function: str) -> None:
        field_tag = new(
            "table:data-pilot-field",
            {"table:source-field-name": field, "table:orientation": orientation, "table:function": function},
        )
        pivot.append(field_tag)

    def _empty_cell_template(self) -> etree._Element:
        """A detached, blank `<table:table-cell/>` tag."""
        return new("table:table-cell")

    def _empty_row_template(self, n_cols: int) -> etree._Element:
        """A detached `<table:table-row>` tag with `n_cols` blank cells."""
        row = new("table:table-row")
        for _ in range(n_cols):
            row.append(self._empty_cell_template())
        return row

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
        target_cols = max(col + 1, self.n_cols)
        if target_cols > self.n_cols:
            for run in self._runs_in(self.rows):
                row_tag = cast(etree._Element, self.rows[run.start][0].cell.getparent())
                new_tags = self._cells_between(row_tag, self.n_cols, target_cols)
                for r in run:
                    self.rows[r].extend(
                        Cell(tag, row=r, col=c, sheet=self)
                        for c, tag in enumerate(new_tags, start=self.n_cols)
                    )
            self.n_cols = target_cols

        if row >= self.n_rows:
            self._take_back_rows(row + 1)

        self.size = (self.n_rows, self.n_cols)

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
        previous = cast(etree._Element, self.rows[-1][0].cell.getparent()) if self.n_rows else None
        for row_tag in self._rows_past_grid():
            if self.n_rows >= n_rows:
                return
            self._split_row_element(row_tag, n_rows - self.n_rows)
            tags = self._cells_between(row_tag, 0, self.n_cols)
            for _ in range(_repeat(row_tag, _ROWS_REPEATED)):
                self.rows.append(
                    [Cell(tag, row=self.n_rows, col=c, sheet=self) for c, tag in enumerate(tags)]
                )
                self.n_rows += 1
            previous = row_tag
        template = self._empty_row_template(self.n_cols)
        # each new row right after the one before, never by index (see
        # `_insert_chain`)
        while self.n_rows < n_rows:
            new_row_tag = clone(template)
            if previous is None:
                self.table.append(new_row_tag)
            else:
                insert_after(previous, new_row_tag)
            previous = new_row_tag
            self.rows.append(
                [
                    Cell(cell_tag, row=self.n_rows, col=c, sheet=self)
                    for c, cell_tag in enumerate(_row_cells(new_row_tag))
                ]
            )
            self.n_rows += 1

    def _rows_past_grid(self) -> "list[etree._Element]":
        """The `<table:table-row>` elements the file holds after the grid's
        last row, in order, grouped or not: what `load` left out below the
        grid. It never looks inside a row: a cell may hold a table of its
        own."""
        found: list[etree._Element] = []
        if self.n_rows == 0:
            return _sheet_rows(self.table)
        node: etree._Element | None = self.rows[-1][0].cell.getparent()
        while node is not None and node is not self.table:
            for sibling in node.itersiblings(*_ROW_LEVEL):
                if sibling.tag == _ROW:
                    found.append(sibling)
                else:
                    found.extend(_sheet_rows(sibling))
            node = node.getparent()
        return found

    def _give_back_rows(self, count: int) -> None:
        """Keep the sheet's height after `count` rows were inserted, if it was
        padded to an application's full height (see `_FULL_HEIGHTS`), with
        the rows padding it below the grid (see `_give_back`)."""
        attr = _ROWS_REPEATED
        below = self._rows_past_grid()
        if self.n_rows + sum(_repeat(r, attr) for r in below) - count in _FULL_HEIGHTS:
            _give_back(below, attr, count)

    @staticmethod
    def _give_back_cells(row_tag: etree._Element, count: int, start: int = 0) -> None:
        """Keep the width of `row_tag` after `count` cells were inserted into
        it, if it was padded to an application's full width, with its blank
        cells from logical column `start` on - past the grid, whose cells
        must keep their elements."""
        attr = _COLUMNS_REPEATED
        # a row reaches its full width through a filler, one blank cell
        # repeated to the end: without one, no need to add up its cells -
        # which, for every row of a big sheet, doubled insert_columns' time
        last = next(row_tag.iterchildren(etree.Element, reversed=True), None)
        if last is None or _repeat(last, attr) <= MAX_REPEAT_ROWS:
            return
        cells = _row_cells(row_tag)
        positions = list(itertools.accumulate((_repeat(c, attr) for c in cells), initial=0))
        if positions[-1] - count in _FULL_WIDTHS:
            _give_back([c for c, p in zip(cells, positions[:-1], strict=True) if p >= start], attr, count)

    def _insert_cells_past_grid(self, col: int, count: int) -> None:
        """Insert `count` blank cells before logical column `col` in every
        row the file holds below the grid, so that what those rows hold moves
        sideways with the columns of the grid, as LibreOffice moves a chart
        anchored there (issue #10). A row ending before `col` has nothing to
        move."""
        for row_tag in self._rows_past_grid():
            self._split_cells_at(row_tag, col)
            position = 0
            for cell_tag in _row_cells(row_tag):
                if position == col:
                    new_tag = self._empty_cell_template()
                    _set_repeat(new_tag, _COLUMNS_REPEATED, count)
                    insert_before(cell_tag, new_tag)
                    self._give_back_cells(row_tag, count)
                    break
                position += _repeat(cell_tag, _COLUMNS_REPEATED)

    def _delete_cells_past_grid(self, col: int) -> None:
        """Delete logical column `col` from every row the file holds below
        the grid - see `_insert_cells_past_grid`.

        A chart or a shape anchored to a deleted cell stays where it was, in
        the cell that takes its place, as LibreOffice keeps it; a note goes
        with its cell. A row left with no cell gets a blank one: ODF wants at
        least one."""
        for row_tag in self._rows_past_grid():
            for position in (col, col + 1, col + 2):  # the next cell alone too
                self._split_cells_at(row_tag, position)
            cells = _row_cells(row_tag)
            position = 0
            for k, cell_tag in enumerate(cells):
                if position == col:
                    if _drawings_in(cell_tag) or len(cells) == 1:
                        heir = cells[k + 1] if k + 1 < len(cells) else None
                        if heir is None:
                            heir = self._empty_cell_template()
                            insert_after(cell_tag, heir)
                        _rescue_drawings(cell_tag, heir)
                    remove(cell_tag)
                    break
                position += _repeat(cell_tag, _COLUMNS_REPEATED)

    def _cells_between(self, row_tag: etree._Element, start: int, stop: int) -> "list[etree._Element]":
        """The cell elements of `row_tag` for logical columns `start` to
        `stop` (excluded), one entry per column - a repeated cell once per
        column it covers - after splitting any that straddles either bound
        (see `_split_cells_at`), and padding the row with a blank cell if it
        ends before `stop`."""
        self._split_cells_at(row_tag, start)
        self._split_cells_at(row_tag, stop)
        tags: list[etree._Element] = []
        position = 0
        for cell_tag in _row_cells(row_tag):
            if position >= stop:
                break
            n = _repeat(cell_tag, _COLUMNS_REPEATED)
            if position >= start:
                tags += [cell_tag] * n
            position += n
        if position < stop:
            pad = self._empty_cell_template()
            _set_repeat(pad, _COLUMNS_REPEATED, stop - position)
            row_tag.append(pad)
            tags += [pad] * (stop - max(position, start))
        return tags

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
        targets = sorted(set(rows))
        if not targets:
            return
        for row in targets:
            if row < 0 or row >= self.n_rows:
                raise IndexError(f"row {row} out of range (sheet has {self.n_rows} rows)")
        self._read_charts_first()
        for row in targets:
            c = 0
            while c < self.n_cols:
                if self.rows[row][c].is_merged:
                    self._unmerge(row, c)
                c += 1

        for row in reversed(targets):  # bottom-up: earlier indexes stay valid
            self._unrepeat_row(row)
            # what is anchored in the row stays at its address, in the row
            # that takes its place - the one below, or the one above for the
            # last row of the sheet (issue #24)
            heir_row = row + 1 if row + 1 < len(self.rows) else row - 1
            if 0 <= heir_row < len(self.rows):
                for c, cell in enumerate(self.rows[row]):
                    if _drawings_in(cell.cell):
                        self._unrepeat_col(heir_row, c)
                        _rescue_drawings(cell.cell, self.rows[heir_row][c].cell)
                # and so does what is anchored past the grid's columns, in
                # the cells the row element keeps beyond them: LibreOffice
                # writes a chart there when the data stops short of it
                start = 0
                for cell_tag in _row_cells(cast(etree._Element, self.rows[row][0].cell.getparent())):
                    if start >= self.n_cols and _drawings_in(cell_tag):
                        self._unrepeat_row(heir_row)
                        heir_tag = cast(etree._Element, self.rows[heir_row][0].cell.getparent())
                        _rescue_drawings(cell_tag, self._cell_starting_at(heir_tag, start))
                    start += _repeat(cell_tag, _COLUMNS_REPEATED)
            remove(cast(etree._Element, self.rows[row][0].cell.getparent()))
            del self.rows[row]
        for r in range(targets[0], len(self.rows)):
            for cell in self.rows[r]:
                cell.row = r
        self.n_rows = len(self.rows)
        self.size = (self.n_rows, self.n_cols)
        self._remap_formula_references(_deletion_remap(deleted_rows=targets))

    def delete_column(self, col: int) -> None:
        """Remove logical column `col` entirely, shifting every column to
        its right left by one (`sheet.size` shrinks accordingly).

        Any merge intersecting `col` is undone first, same as
        `delete_row`; formula references elsewhere in the document are
        adjusted the same way too - see `_remap_formula_references`. The
        rows the file holds below the grid lose the column too (see
        `_delete_cells_past_grid`).
        """
        if col < 0 or col >= self.n_cols:
            raise IndexError(f"column {col} out of range (sheet has {self.n_cols} columns)")
        self._read_charts_first()
        r = 0
        while r < self.n_rows:
            if self.rows[r][col].is_merged:
                self._unmerge(r, col)
            r += 1

        self._delete_cells_past_grid(col)  # while the grid still has a column
        for r in range(self.n_rows):
            self._unrepeat_col(r, col)
        # a sheet declares only the columns it uses: one written past them
        # has no definition to delete - asserting one did raised (issue #13)
        if self._find_column_tag(col) is not None:
            definition = self._unrepeat_column_tag(col)
            group = cast(etree._Element, definition.getparent())
            remove(definition)
            # a group of definitions - the print titles' - holds one at least
            # descendants: a group holding only a group of definitions is not empty
            if group is not self.table and next(descendants(group, "table:table-column"), None) is None:
                remove(group)
        for run in self._runs_in(self.rows):
            # same as for a row: the chart anchored in the column moves to the
            # cell taking its place, rather than going with it (issue #24)
            heir_col = col + 1 if col + 1 < self.n_cols else col - 1
            if 0 <= heir_col < self.n_cols:
                self._unrepeat_col(run.start, heir_col)
                _rescue_drawings(self.rows[run.start][col].cell, self.rows[run.start][heir_col].cell)
            remove(self.rows[run.start][col].cell)  # once for the whole run
            for r in run:
                del self.rows[r][col]
                for c in range(col, len(self.rows[r])):
                    self.rows[r][c].col = c
        self.n_cols -= 1
        self.size = (self.n_rows, self.n_cols)
        self._remap_formula_references(_deletion_remap(deleted_cols=[col]))

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
        if count < 1:
            raise ValueError(f"count must be at least 1, got {count}")
        if row < 0 or row > self.n_rows:
            raise IndexError(f"row {row} out of range for insertion (sheet has {self.n_rows} rows)")
        self._read_charts_first()
        if self.n_cols == 0 and self.n_rows > 0:  # every column deleted
            self.grow_to(self.n_rows - 1, 0)
        straddling = [
            (master.row, master.col, span_rows, span_cols)
            for master, span_rows, span_cols in self._merge_masters()
            if master.row < row < master.row + span_rows
        ]

        # What the file holds below the grid moves down with the rows after
        # the insertion point, as LibreOffice moves a chart anchored there -
        # it used to be deleted (issue #10) - and the filler padding the
        # sheet to its full height gives back the rows inserted
        after: etree._Element | None = None
        before: etree._Element | None = None
        if row < self.n_rows:
            self._unrepeat_row(row)
            before = cast(etree._Element, self.rows[row][0].cell.getparent())
        else:
            below = self._rows_past_grid()
            if below:
                before = below[0]
            elif self.n_rows:
                after = cast(etree._Element, self.rows[-1][0].cell.getparent())
        self.n_cols = max(self.n_cols, 1)  # a row holds a cell at least
        template = self._empty_row_template(self.n_cols)
        # each next to a known element, never by index (see `_insert_chain`):
        # placed by index, or with bs4's insert_before, each row cost the
        # insertion point's position - 8 s for 20,000 rows inserted below
        # 20,000
        row_tags = [clone(template) for _ in range(count)]
        if before is not None:
            for row_tag in row_tags:
                insert_before(before, row_tag)
        elif after is not None:
            _insert_chain(after, row_tags)
        else:
            for row_tag in row_tags:
                self.table.append(row_tag)
        new_rows = [
            [Cell(t, row=row + k, col=c, sheet=self) for c, t in enumerate(_row_cells(row_tag))]
            for k, row_tag in enumerate(row_tags)
        ]
        self.rows[row:row] = new_rows
        for r in range(row + count, len(self.rows)):
            for cell in self.rows[r]:
                cell.row = r
        self.n_rows = len(self.rows)
        self.size = (self.n_rows, self.n_cols)
        self._give_back_rows(count)

        for m_row, m_col, span_rows, span_cols in straddling:
            self._grow_merge(m_row, m_col, span_rows + count, span_cols)
        self._remap_formula_references(_insertion_remap(at_row=row, count=count))

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
        if count < 1:
            raise ValueError(f"count must be at least 1, got {count}")
        if col < 0 or col > self.n_cols:
            raise IndexError(f"column {col} out of range for insertion (sheet has {self.n_cols} columns)")
        self._read_charts_first()
        straddling = [
            (master.row, master.col, span_rows, span_cols)
            for master, span_rows, span_cols in self._merge_masters()
            if master.col < col < master.col + span_cols
        ]

        # rows sharing one repeated <table:table-row> would otherwise each
        # insert their new cells into that same tag
        for r in range(self.n_rows):
            self._unrepeat_row(r)
        for r in range(self.n_rows):
            new_tags = [self._empty_cell_template() for _ in range(count)]
            # next to a known cell, never by index, as in `insert_rows`
            if col < self.n_cols:
                self._unrepeat_col(r, col)
                anchor = self.rows[r][col].cell
                row_tag = cast(etree._Element, anchor.getparent())
                for new_tag in new_tags:
                    insert_before(anchor, new_tag)
            else:
                previous = self.rows[r][-1].cell
                row_tag = cast(etree._Element, previous.getparent())
                _insert_chain(previous, new_tags)
            self.rows[r][col:col] = [Cell(t, row=r, col=col + k, sheet=self) for k, t in enumerate(new_tags)]
            for c in range(col + count, len(self.rows[r])):
                self.rows[r][c].col = c
            self._give_back_cells(row_tag, count, self.n_cols + count)
        if self.n_rows > 0:
            self.n_cols += count
            self.size = (self.n_rows, self.n_cols)
        self._insert_cells_past_grid(col, count)
        self._insert_column_definitions(col, count)

        for m_row, m_col, span_rows, span_cols in straddling:
            self._grow_merge(m_row, m_col, span_rows, span_cols + count)
        self._remap_formula_references(_insertion_remap(at_col=col, count=count))

    def _merge_masters(self) -> "list[tuple[Cell, int, int]]":
        """Every merge master on the sheet, with its row and column spans."""
        return [
            (
                cell,
                int(cell.cell.get(_ROWS_SPANNED, "1")),
                int(cell.cell.get(_COLUMNS_SPANNED, "1")),
            )
            for cells_row in self.rows
            for cell in cells_row
            if cell.cell.tag != _COVERED_CELL and self._is_merge_master(cell)
        ]

    def _grow_merge(self, row: int, col: int, span_rows: int, span_cols: int) -> None:
        """Widen the merge whose master sits at (row, col) to the new spans,
        turning every cell newly inside it into a covered cell - the
        insertion counterpart of `_unmerge`, which also explains the growth."""
        self.grow_to(row + span_rows - 1, col + span_cols - 1)
        self._unrepeat_row(row)
        self._unrepeat_col(row, col)
        master = self.rows[row][col]
        master.cell.set(_ROWS_SPANNED, str(span_rows))
        master.cell.set(_COLUMNS_SPANNED, str(span_cols))
        master.__init__(master.cell, row=row, col=col, sheet=self)  # type: ignore[misc]
        for r in range(row, row + span_rows):
            for c in range(col, col + span_cols):
                covered = self.rows[r][c]
                if (r, c) == (row, col) or covered.cell.tag == _COVERED_CELL:
                    continue
                rename(covered.cell, "table:covered-table-cell")
                covered.__init__(covered.cell, row=r, col=c, sheet=self)  # type: ignore[misc]

    def _insert_column_definitions(self, col: int, count: int) -> None:
        """Insert `count` `<table:table-column>` definitions before logical
        column `col`, so existing columns keep their widths.

        The new ones repeat the definition of the column they push right -
        its width, its visibility, the default cell style of a column
        formatted as a whole - which is what LibreOffice gives a column
        inserted before another (issue #25). Appending past the last column
        has nothing to copy, and takes the default width.

        Applications declare definitions up to the sheet's maximum width
        (an empty one repeated thousands of times after the last used
        column): if they reach it, the definitions at the end give back the
        columns just added, or the document would exceed the maximum and be
        truncated on open (see `_FULL_WIDTHS`). Short of it, they stay: they
        used to give back any definition past the data, and the formatted
        columns there lost their widths. No-op when the definitions don't
        reach `col` at all."""
        seen = 0
        for tag in list(self._column_definitions(self.table)):
            n = int(tag.get(_COLUMNS_REPEATED, "1"))
            if seen <= col < seen + n:
                if col > seen:  # split the repeated definition at `col`
                    rest = clone(tag)
                    _set_repeat(tag, _COLUMNS_REPEATED, col - seen)
                    _set_repeat(rest, _COLUMNS_REPEATED, seen + n - col)
                    insert_after(tag, rest)
                    tag = rest
                # `tag` now starts at `col`: it is the definition of the
                # column being pushed right, and the one to repeat
                new_tag = clone(tag)
                _set_repeat(new_tag, _COLUMNS_REPEATED, count)
                insert_before(tag, new_tag)
                break
            seen += n
        else:
            return
        attr = _COLUMNS_REPEATED
        definitions = list(self._column_definitions(self.table))
        if sum(_repeat(t, attr) for t in definitions) - count in _FULL_WIDTHS:
            after = next(k for k, t in enumerate(definitions) if t is new_tag) + 1
            _give_back(definitions[after:], attr, count)

    def _read_charts_first(self) -> None:
        """Parse the content of the document's charts, if not done yet,
        before a structural edit changes anything: a chart's part that is
        not well-formed raises `MalformedDocumentError` with the sheet as it
        was, rather than once the edit is halfway through (see
        `ODSReader._read_objects`)."""
        if self.reader is not None:
            self.reader._read_objects()

    def _remap_formula_references(self, remap: _Remap) -> None:
        """After a structural edit of this sheet (rows or columns deleted or
        inserted), rewrite every formula in the whole document - this
        sheet's own, and any other sheet's formula that references into
        this sheet by name - through `remap` (see `_deletion_remap`/
        `_insertion_remap`), so each reference still points at the cell it
        did before.

        Charts follow too, their ranges and the cell their frame ends in
        (see `ODSReader._remap_drawing_references`, issue #15); pivot-table
        source ranges, named ranges, conditional-format and validation
        ranges are left as they are. No-op if this sheet has no owning
        `ODSReader` (nothing else to scan). One full-document sweep per call
        - which is why `delete_rows` batches N rows into one call.
        """
        if self.reader is None:
            return
        for sheet in self.reader.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell._formula is None:
                        continue
                    adjusted = _remap_odf_formula_references(cell._formula, self.name, sheet.name, remap)
                    if adjusted != cell._formula:
                        cell._write_formula(adjusted)
        self.reader._remap_drawing_references(self.name, remap)

    def __repr__(self) -> str:
        return f"Sheet(name='{self.name}', size[rows, cols]={self.size})"

    @property
    def style(self) -> "TableStyle | None":
        """This sheet's resolved, writable `TableStyle` (e.g. `.tab_color`
        - see `TableStyle`), or `None` if there's no owning `ODSReader`."""
        if self.reader is None:
            return None
        name = self.table.get(_STYLE_NAME)
        tag = self.reader._find_style(name, family="table") if name else None
        return TableStyle(tag, sheet=self)

    def row_style(self, row: int) -> "RowStyle | None":
        """The resolved, writable `RowStyle` for logical row `row` (see
        `RowStyle`), or `None` if `row` is out of range or there's no
        owning `ODSReader`."""
        if self.reader is None or row >= self.n_rows:
            return None
        row_tag = self.rows[row][0].cell.getparent()
        name = row_tag.get(_STYLE_NAME)
        tag = self.reader._find_style(name, family="table-row") if name else None
        return RowStyle(tag, sheet=self, row=row)

    # what may hold `<table:table-column>` elements, and what comes after them

    def _column_default_cell_style(self, col: int) -> "str | None":
        """The default cell style of logical column `col` - the style a cell
        of that column with none of its own shows with, where LibreOffice
        keeps the format of a column formatted as a whole - or `None`."""
        seen = 0
        for tag in self._column_definitions(self.table):
            seen += _repeat(tag, _COLUMNS_REPEATED)
            if col < seen:
                return tag.get(qn("table:default-cell-style-name"))
        return None

    @classmethod
    def _column_definitions(cls, parent: etree._Element) -> "Iterator[etree._Element]":
        """The `<table:table-column>` elements of `parent`, in order, grouped
        or not. ODF puts them before the rows, and the scan stops there:
        looking a column up costs nothing like a pass over the sheet."""
        for element in parent.iterchildren(etree.Element):
            name = element.tag
            if name in _ROW_LEVEL:
                return
            if name == _COLUMN:
                yield element
            elif name in _COLUMN_GROUPS:
                yield from cls._column_definitions(element)

    def _find_column_tag(self, col: int) -> "etree._Element | None":
        """The `<table:table-column>` covering logical column `col`
        (accounting for `table:number-columns-repeated`), or `None` - grouped
        or not: LibreOffice groups the print-title columns' definitions in a
        `<table:table-header-columns>`, which shifted every column after
        them when skipped (issue #13)."""
        seen = 0
        for col_tag in self._column_definitions(self.table):
            n = int(col_tag.get(_COLUMNS_REPEATED, "1"))
            if seen <= col < seen + n:
                return col_tag
            seen += n
        return None

    def _unrepeat_column_tag(self, col: int) -> etree._Element:
        """Split the `table:number-columns-repeated` column-definition tag
        covering `col` into one independent `<table:table-column>` per
        repetition (mirrors `_unrepeat_col`, but for column *definitions*
        rather than cell data). Returns the tag now covering `col` alone -
        already independent if it wasn't repeated to begin with."""
        col_tag = self._find_column_tag(col)
        assert col_tag is not None  # callers only ask for a column the sheet has
        n = int(col_tag.get(_COLUMNS_REPEATED, "1"))
        if n <= 1:
            return col_tag

        seen = 0
        for tag in self._column_definitions(self.table):
            if tag is col_tag:
                start = seen
                break
            seen += int(tag.get(_COLUMNS_REPEATED, "1"))

        copies = _split_repetitions(col_tag, _COLUMNS_REPEATED, n)
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
        name = col_tag.get(_STYLE_NAME)
        tag = self.reader._find_style(name, family="table-column") if name else None
        return ColumnStyle(tag, sheet=self, col=col)

    def _fork_style(
        self, current_name: "str | None", family: str, prefix: str, props_tag_name: str
    ) -> etree._Element:
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
            # a fork lives in the automatic styles of `content.xml`, and only
            # a tag from there is written to in place: a style of
            # `styles.xml` that happens to bear such a name is forked like
            # any other, since save() copies that part through untouched
            name, style_family = qn("style:name"), qn("style:family")
            for tag in children(self.reader._automatic_styles(), "style:style"):
                if tag.get(name) == current_name and tag.get(style_family) == family:
                    return tag
        old_tag = self.reader._find_style(current_name, family=family) if current_name else None
        tag = self.reader._new_style_tag(family, prefix)
        if old_tag is not None:
            old_props = child(old_tag, props_tag_name)
            if old_props is not None:
                props = _ensure_style_child(tag, props_tag_name)
                for key, value in old_props.attrib.items():
                    props.set(key, value)
        return tag

    def _ensure_row_style(self, row: int) -> etree._Element:
        self._unrepeat_row(row)
        row_tag = self.rows[row][0].cell.getparent()
        tag = self._fork_style(row_tag.get(_STYLE_NAME), "table-row", "ors", "style:table-row-properties")
        row_tag.set(_STYLE_NAME, tag.attrib[qn("style:name")])
        return tag

    def _ensure_column_style(self, col: int) -> etree._Element:
        col_tag = self._unrepeat_column_tag(col)
        tag = self._fork_style(
            col_tag.get(_STYLE_NAME), "table-column", "ocos", "style:table-column-properties"
        )
        col_tag.set(_STYLE_NAME, tag.attrib[qn("style:name")])
        return tag

    def _ensure_table_style(self) -> etree._Element:
        tag = self._fork_style(self.table.get(_STYLE_NAME), "table", "ots", "style:table-properties")
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
