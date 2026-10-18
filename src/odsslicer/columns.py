"""A sheet's column definitions - the `<table:table-column>` elements that
carry each column's width, visibility and default cell style - found by
logical column, split, inserted and deleted.

They are no part of the grid: ODF writes them before the rows, a definition
repeated over many columns (`table:number-columns-repeated`), grouped or not
(the print titles' in `<table:table-header-columns>`), and a sheet declares
only the columns it uses. `Sheet` looked them up and reshaped them with
methods of its own, next to the grid; a `Cell` asks one for its column's
default cell style, and a style view for its column's style. These
functions work on the `<table:table>` element alone, so that each of those
can ask without a `Sheet`.

Nothing here is public (`__all__` is empty): `Sheet`, `Cell`, the style views
and the structural edits call it.
"""

from collections.abc import Iterator

from lxml import etree

from odsslicer.gridxml import (
    _COLUMN,
    _COLUMN_GROUPS,
    _COLUMNS_REPEATED,
    _FULL_WIDTHS,
    _ROW_LEVEL,
    _give_back,
    _repeat,
    _set_repeat,
    _split_repetitions,
)
from odsslicer.xmltree import clone, descendants, insert_after, insert_before, qn, remove

__all__: list[str] = []

_DEFAULT_CELL_STYLE_NAME = qn("table:default-cell-style-name")


def _column_definitions(parent: etree._Element) -> "Iterator[etree._Element]":
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
            yield from _column_definitions(element)


def _definition_starting(table: etree._Element, col: int) -> "tuple[etree._Element, int] | None":
    """The `<table:table-column>` covering logical column `col` of `table`,
    with the logical column it starts at, or `None` - grouped or not:
    LibreOffice groups the print-title columns' definitions in a
    `<table:table-header-columns>`, which shifted every column after them
    when skipped (issue #13)."""
    seen = 0
    for col_tag in _column_definitions(table):
        n = _repeat(col_tag, _COLUMNS_REPEATED)
        if seen <= col < seen + n:
            return col_tag, seen
        seen += n
    return None


def _column_definition_at(table: etree._Element, col: int) -> "etree._Element | None":
    """The `<table:table-column>` covering logical column `col` (accounting
    for `table:number-columns-repeated`), or `None`."""
    found = _definition_starting(table, col)
    return found[0] if found is not None else None


def _column_default_cell_style(table: etree._Element, col: int) -> "str | None":
    """The default cell style of logical column `col` - the style a cell
    of that column with none of its own shows with, where LibreOffice
    keeps the format of a column formatted as a whole - or `None`."""
    seen = 0
    for tag in _column_definitions(table):
        seen += _repeat(tag, _COLUMNS_REPEATED)
        if col < seen:
            return tag.get(_DEFAULT_CELL_STYLE_NAME)
    return None


def _unrepeat_column_definition(table: etree._Element, col: int) -> etree._Element:
    """Split the repeated `<table:table-column>` covering `col` into one
    independent definition per repetition (as `_Unroller.column` splits
    cells). Returns the definition now covering `col` alone - already
    independent if it wasn't repeated to begin with."""
    found = _definition_starting(table, col)
    assert found is not None  # callers only ask for a column the sheet has
    col_tag, start = found
    n = _repeat(col_tag, _COLUMNS_REPEATED)
    if n <= 1:
        return col_tag
    return _split_repetitions(col_tag, _COLUMNS_REPEATED, n)[col - start]


def _insert_column_definitions(table: etree._Element, col: int, count: int) -> None:
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
    found = _definition_starting(table, col)
    if found is None:
        return
    tag, seen = found
    if col > seen:  # split the repeated definition at `col`
        n = _repeat(tag, _COLUMNS_REPEATED)
        rest = clone(tag)
        _set_repeat(tag, _COLUMNS_REPEATED, col - seen)
        _set_repeat(rest, _COLUMNS_REPEATED, seen + n - col)
        insert_after(tag, rest)
        tag = rest
    # `tag` now starts at `col`: it is the definition of the column being
    # pushed right, and the one to repeat
    new_tag = clone(tag)
    _set_repeat(new_tag, _COLUMNS_REPEATED, count)
    insert_before(tag, new_tag)
    definitions = list(_column_definitions(table))
    if sum(_repeat(t, _COLUMNS_REPEATED) for t in definitions) - count in _FULL_WIDTHS:
        after = next(k for k, t in enumerate(definitions) if t is new_tag) + 1
        _give_back(definitions[after:], _COLUMNS_REPEATED, count)


def _delete_column_definition(table: etree._Element, col: int) -> None:
    """Delete the definition of logical column `col`, if the sheet declares
    one: a column written past the declared ones has none - asserting one
    did raised (issue #13). A group of definitions left with none goes too."""
    if _column_definition_at(table, col) is None:
        return
    definition = _unrepeat_column_definition(table, col)
    group = definition.getparent()
    remove(definition)
    # a group of definitions - the print titles' - holds one at least
    # descendants: a group holding only a group of definitions is not empty
    if (
        group is not None
        and group is not table
        and next(descendants(group, "table:table-column"), None) is None
    ):
        remove(group)
