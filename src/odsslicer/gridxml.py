"""A sheet's XML, read and reshaped without a `Cell`: its rows and their
cells, how many columns or rows an element repeats over, and the splits,
insertions and removals the structural edits make of them.

ODF compresses a sheet: one `<table:table-row>` stands for many identical
rows (`table:number-rows-repeated`), one cell for many columns, and the
padding applications write to declare a sheet's full size is such a run. The
grid of `Cell`s (`loading`) points at these elements, several positions at
one; every edit first has to split an element so that it stands for the
positions being changed alone, and must never place one by index (see
`_insert_chain`). These functions are that layer, on elements only: what a
row element holds, past the grid as within it, is the same XML, and the
functions that handle the rows a file holds below the grid
(`_insert_cells_at`, `_delete_cell_at`, `_rows_after`) are the same ones
`Sheet` once kept as methods next to its grid.

Split out of `sheet.py` at 1.0's audit, verbatim but for the methods turned
functions: none of them needed a `Sheet`.

Nothing here is public (`__all__` is empty): the modules of the sheet call it.
"""

import itertools
from collections.abc import Iterable

from lxml import etree

from odsslicer.constants import MAX_REPEAT_ROWS, TAG_CELL
from odsslicer.xmltree import (
    NAMESPACES,
    clone,
    elements,
    insert_after,
    insert_before,
    namespace_of,
    new,
    qn,
    remove,
    unset,
)

__all__: list[str] = []

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


def _cell_starting_at(row_tag: etree._Element, col: int) -> etree._Element:
    """The cell element standing for logical column `col` of `row_tag`
    alone, split out of a repeated one if need be, and created - after
    empty cells up to it - when the row element ends before `col`: where
    a drawing anchored past the grid's columns goes."""
    _split_cells_at(row_tag, col)
    _split_cells_at(row_tag, col + 1)
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


def _cells_between(row_tag: etree._Element, start: int, stop: int) -> "list[etree._Element]":
    """The cell elements of `row_tag` for logical columns `start` to
    `stop` (excluded), one entry per column - a repeated cell once per
    column it covers - after splitting any that straddles either bound
    (see `_split_cells_at`), and padding the row with a blank cell if it
    ends before `stop`."""
    _split_cells_at(row_tag, start)
    _split_cells_at(row_tag, stop)
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
        pad = new("table:table-cell")
        _set_repeat(pad, _COLUMNS_REPEATED, stop - position)
        row_tag.append(pad)
        tags += [pad] * (stop - max(position, start))
    return tags


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


def _empty_row(n_cols: int) -> etree._Element:
    """A detached `<table:table-row>` with `n_cols` blank cells, each an
    element of its own."""
    row = new("table:table-row")
    for _ in range(n_cols):
        row.append(new("table:table-cell"))
    return row


def _rows_after(last_row: "etree._Element | None", table: etree._Element) -> "list[etree._Element]":
    """The `<table:table-row>` elements of `table` after `last_row`, in
    order, grouped or not - every one if `last_row` is `None`. It never
    looks inside a row: a cell may hold a table of its own."""
    if last_row is None:
        return _sheet_rows(table)
    found: list[etree._Element] = []
    node: etree._Element | None = last_row
    while node is not None and node is not table:
        for sibling in node.itersiblings(*_ROW_LEVEL):
            if sibling.tag == _ROW:
                found.append(sibling)
            else:
                found.extend(_sheet_rows(sibling))
        node = node.getparent()
    return found


def _insert_cells_at(row_tag: etree._Element, col: int, count: int) -> None:
    """Insert `count` blank cells before logical column `col` of `row_tag`,
    as one repeated element, keeping the row's full width if it was padded
    to it (see `_give_back_cells`). A row ending before `col` has nothing to
    move."""
    _split_cells_at(row_tag, col)
    position = 0
    for cell_tag in _row_cells(row_tag):
        if position == col:
            new_tag = new("table:table-cell")
            _set_repeat(new_tag, _COLUMNS_REPEATED, count)
            insert_before(cell_tag, new_tag)
            _give_back_cells(row_tag, count)
            return
        position += _repeat(cell_tag, _COLUMNS_REPEATED)


def _delete_cell_at(row_tag: etree._Element, col: int) -> None:
    """Delete logical column `col` of `row_tag`. A chart or a shape
    anchored to the deleted cell stays where it was, in the cell that takes
    its place, as LibreOffice keeps it; a note goes with its cell. A row
    left with no cell gets a blank one: ODF wants at least one."""
    for position in (col, col + 1, col + 2):  # the next cell alone too
        _split_cells_at(row_tag, position)
    cells = _row_cells(row_tag)
    position = 0
    for k, cell_tag in enumerate(cells):
        if position == col:
            if _drawings_in(cell_tag) or len(cells) == 1:
                heir = cells[k + 1] if k + 1 < len(cells) else None
                if heir is None:
                    heir = new("table:table-cell")
                    insert_after(cell_tag, heir)
                _rescue_drawings(cell_tag, heir)
            remove(cell_tag)
            return
        position += _repeat(cell_tag, _COLUMNS_REPEATED)
