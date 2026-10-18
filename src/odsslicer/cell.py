# mypy: disable-error-code="union-attr"
# (`Element | None` unions are narrowed dynamically all over this module,
# guarded by runtime checks mypy can't see through - silencing that one error
# class here beats dozens of value-free asserts/casts. Every other error
# class, and all signatures, remain fully checked.)
"""ArrayValues (multi-cell selections), Comment (cell notes) and Cell itself."""

import datetime as dt
import decimal
import itertools
import math
import numbers
import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from lxml import etree

from odsslicer.addresses import string_address
from odsslicer.columns import _column_default_cell_style, _column_definitions
from odsslicer.constants import EMPTY_CELL, FORMATS, TAG_CELL
from odsslicer.datetimes import (
    _as_datetime,
    _as_timedelta,
    _duration_text,
    _format_date_value,
    _format_time_value,
    _parse_date_value,
    _parse_time_value,
)
from odsslicer.formulas import (
    _expand_formula_template,
    _friendly_formula,
    _normalize_odf_formula,
    _shift_odf_formula,
)
from odsslicer.styles import CellStyle, _render_date_time_from_format, _render_number_from_format
from odsslicer.xmltree import (
    child,
    children,
    clone,
    descendants,
    following,
    insert_before,
    new,
    paragraph_text,
    preceding,
    qn,
    remove,
    set_paragraph_text,
    set_text,
    text,
    unset,
    unwrap,
    wrap_contents,
)

if TYPE_CHECKING:
    from odsslicer.numberformat import NumberFormat
    from odsslicer.reader import ODSReader
    from odsslicer.sheet import Sheet

# The attributes a cell is read from, by namespace URI: `Cell.__init__` runs
# once per cell of a sheet being loaded, where `qn`'s lookup would add up
_VALUE_TYPE = qn("office:value-type")
_VALUE = qn("office:value")
_DATE_VALUE = qn("office:date-value")
_TIME_VALUE = qn("office:time-value")
_BOOLEAN_VALUE = qn("office:boolean-value")
_FORMULA = qn("table:formula")
_STYLE_NAME = qn("table:style-name")
_CALCEXT_VALUE_TYPE = qn("calcext:value-type")
_COLUMNS_REPEATED = qn("table:number-columns-repeated")
_COLUMNS_SPANNED = qn("table:number-columns-spanned")
_ROWS_SPANNED = qn("table:number-rows-spanned")
_DATA_STYLE_NAME = qn("style:data-style-name")
_TEXT_P = qn("text:p")
_CELL_TAGS = tuple(qn(name) for name in TAG_CELL)
_COVERED_CELL = qn("table:covered-table-cell")


def _is_broadcastable_scalar(value: object) -> bool:
    """True for a value that should be written as-is to every cell of a
    multi-cell selection, rather than unpacked element-wise (a `str` is
    iterable but clearly meant as one value, not one cell per character).
    numpy's scalars count, and a numpy array of no dimension (issue #11)."""
    return (
        value is None
        or isinstance(
            value,
            (str, numbers.Number, decimal.Decimal, np.generic, dt.date, dt.time, dt.timedelta),
        )
        or (isinstance(value, np.ndarray) and value.ndim == 0)
    )


def _plain_value(value: object) -> object:
    """`value` as one of the Python types a cell holds, when it stands for
    one (issue #11): numpy's scalars - what pandas hands out, `df.to_numpy()`
    of a frame of integers say - and any other number, a `decimal.Decimal`
    from a database driver, a `fractions.Fraction`. NaN and numpy's NaT are
    missing values: `None`, an empty cell, as pandas writes one to a
    spreadsheet - LibreOffice reads `office:value="nan"` as 0, which sums
    and averages then count. An infinite number has no place in a cell:
    `ValueError`. Anything else comes back as it is.

    numpy's `timedelta64` is registered as an integer, and its `item()`
    gives an int for nanoseconds, or a date out of `datetime`'s range:
    dates and durations go through microseconds first."""
    if isinstance(value, np.ndarray) and value.ndim == 0:
        value = value[()]
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            return None
        days = np.datetime_data(value.dtype)[0] in ("Y", "M", "W", "D")
        converted = value.astype("datetime64[D]" if days else "datetime64[us]").item()
        if not isinstance(converted, dt.date):
            raise ValueError(f"{value!r} is out of the range of datetime.date")
        return converted
    if isinstance(value, np.timedelta64):
        return None if np.isnat(value) else value.astype("timedelta64[us]").item()
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        return str(value)  # numpy's str_ too
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, (numbers.Real, decimal.Decimal)):
        number = float(value)
        if math.isnan(number):
            return None
        if math.isinf(number):
            raise ValueError(f"a cell cannot hold an infinite number: {value!r}")
        return number
    return value


def _only_string(element: etree._Element) -> "str | None":
    """The text of `element` when it is all one text node, at any depth -
    bs4's `tag.string`, which odsslicer learnt a cell's displayed text by
    example from: `<text:p>1,5 €</text:p>` and `<text:p><text:span>1,5
    €</text:span></text:p>` say "1,5 €", an empty paragraph or one of
    several nodes nothing. A lone comment is a text node in bs4."""
    while True:
        if len(element) == 0:
            return element.text
        if element.text or len(element) > 1:
            return None
        only = element[0]
        if only.tail:
            return None
        if not isinstance(only.tag, str):  # a comment, or a processing instruction
            return only.text
        element = only


class ArrayValues:
    def __init__(self, array: Any) -> None:
        self.array = array
        self.dimension = self._get_dimension(array)
        self.size = self._get_size()

    def _get_size(self) -> tuple[int, ...]:
        size = []
        cut = self.array
        for _ in range(self.dimension):
            size.append(len(cut))
            if not cut:
                break  # an empty level has nothing deeper to measure
            cut = cut[0]
        return tuple(size)

    @classmethod
    def _get_dimension(cls, array: object) -> int:
        """How deeply the selection is nested: 0 for a single `Cell`, 1 for a
        row/column, 2 for a block - measured from the first element, since
        every level holds the same kind."""
        try:
            return cls._get_dimension(array[0]) + 1  # type: ignore[index]
        except IndexError:
            # an empty list is an empty *selection* - every row of a sheet
            # with no rows, say - so it is a (zero-length) row, not a cell
            return 1
        except TypeError:
            return 0  # a Cell: not indexable, so this is the bottom

    def _iter_cells(self) -> "Iterator[Cell]":
        """Yield every underlying `Cell`, regardless of this selection's
        dimension (a single cell, a row/column, or a 2D block)."""
        if self.dimension == 0:
            yield self.array
        else:
            for item in self.array:
                yield from ArrayValues(item)._iter_cells()

    def __repr__(self) -> str:
        return f"ArrayValue({self.array})"

    def __getitem__(self, i: int) -> Any:
        return self.array[i]

    def __len__(self) -> int:
        return len(self.array)

    def to_numpy(self) -> Any:
        return np.array(self.to_list())

    @property
    def value(self) -> Any:
        return self.cell.value

    @value.setter
    def value(self, new_value: Any) -> None:
        if self.dimension == 0:
            self.cell.value = new_value
            return
        if _is_broadcastable_scalar(new_value):
            for item in self.array:
                ArrayValues(item).value = new_value
            return
        values = list(new_value)
        if len(values) != len(self.array):
            raise ValueError(f"shape mismatch: {len(self.array)} cell(s) but {len(values)} value(s) given")
        for item, v in zip(self.array, values, strict=True):
            ArrayValues(item).value = v

    @property
    def formula(self) -> Any:
        return self.cell.formula

    @formula.setter
    def formula(self, new_formula: "str | None") -> None:
        """Write a formula to every cell in this selection.

        For a multi-cell selection, the same pattern is applied to each cell -
        if it contains `{r}`/`{c}` placeholders (see `Cell.formula`), each
        cell expands them using its own row/column, so e.g. writing the
        pattern `"$A{r-1}+1"` across `sheet["A2:A10"]` makes A2 reference A1,
        A3 reference A2, and so on.
        """
        if self.dimension == 0:
            self.cell.formula = new_formula
            return
        for item in self.array:
            ArrayValues(item).formula = new_formula

    @property
    def formula_friendly(self) -> Any:
        return self.cell.formula_friendly

    @property
    def cell(self) -> "Cell":
        assert self.dimension == 0
        return self.array

    def to_list(self) -> Any:
        if self.dimension == 0:
            return self.value
        if self.dimension == 1:
            return [cell.value for cell in self.array]
        if self.dimension == 2:
            return [[cell.value for cell in row] for row in self.array]
        raise ValueError(f"a selection cannot be {self.dimension} levels deep")

    def to_vector(self) -> "ArrayValues":
        assert self.dimension == 2 and self.size[1] == 1
        return ArrayValues([row[0] for row in self.array])

    def __eq__(self, array: Any) -> bool:
        return array.to_list() == self.to_list()


# what a note holds as its text, after its author and date
_NOTE_TEXT = frozenset({qn("text:p"), qn("text:list")})


class Comment:
    """A cell's note (`<office:annotation>`) - `.text` (the note body,
    `\\n`-joined across ODF's own multiple `<text:p>` paragraphs),
    `.author` (`dc:creator`), `.date` (`dc:date`, a `datetime.datetime`),
    `.visible` (`office:display` - shown pinned open vs. only on hover).
    Look up via `Cell.comment`, not directly - writable: set any property
    to update it in place, once the comment itself exists (see
    `Cell.comment`'s setter to create one)."""

    def __init__(self, tag: etree._Element) -> None:
        self._tag = tag

    def _insert_before_text(self, element: etree._Element) -> None:
        # keeps dc:creator/dc:date ahead of the note's text, as ODF orders
        # them, whichever property gets set first: before its first
        # paragraph or list - a paragraph searched among all descendants
        # could be one inside a list, and put the author inside it
        first = next((c for c in self._tag.iterchildren(*_NOTE_TEXT)), None)
        if first is not None:
            insert_before(first, element)
        else:
            self._tag.append(element)

    @property
    def text(self) -> "str | None":
        # every paragraph, descendants: a note set as a bulleted list keeps
        # its paragraphs in text:list, and LibreOffice shows them as its text
        paragraphs = [paragraph_text(p) for p in descendants(self._tag, "text:p")]
        return "\n".join(paragraphs) if paragraphs else None

    @text.setter
    def text(self, value: "str | None") -> None:
        # the whole text goes, a list with it: removing its paragraphs only
        # left the list behind, empty items and all
        for element in list(self._tag.iterchildren(*_NOTE_TEXT)):
            remove(element)
        for line in (value or "").split("\n"):
            p = new("text:p")
            set_paragraph_text(p, line)
            self._tag.append(p)

    @property
    def author(self) -> "str | None":
        tag = child(self._tag, "dc:creator")
        return text(tag) if tag is not None else None

    @author.setter
    def author(self, value: "str | None") -> None:
        tag = child(self._tag, "dc:creator")
        if value is None:
            if tag is not None:
                remove(tag)
            return
        if tag is None:
            tag = new("dc:creator")
            self._insert_before_text(tag)
        set_text(tag, value)

    @property
    def date(self) -> "dt.datetime | None":
        tag = child(self._tag, "dc:date")
        if tag is None:
            return None
        try:
            return dt.datetime.fromisoformat(text(tag))
        except ValueError:
            return None

    @date.setter
    def date(self, value: "dt.datetime | None") -> None:
        tag = child(self._tag, "dc:date")
        if value is None:
            if tag is not None:
                remove(tag)
            return
        if not isinstance(value, dt.datetime):
            raise TypeError(f"Comment.date must be a datetime.datetime or None, got {type(value)!r}")
        if tag is None:
            tag = new("dc:date")
            self._insert_before_text(tag)
        set_text(tag, value.isoformat())

    @property
    def visible(self) -> bool:
        return self._tag.get(qn("office:display")) == "true"

    @visible.setter
    def visible(self, value: bool) -> None:
        self._tag.set(qn("office:display"), "true" if value else "false")

    def __repr__(self) -> str:
        return f"Comment(author={self.author!r}, text={self.text!r})"


class Cell:
    #: attributes touched when a value is written to a cell
    _VALUE_ATTRS = (_VALUE, _DATE_VALUE, _TIME_VALUE, _BOOLEAN_VALUE)

    def __init__(
        self, cell: etree._Element, row: int = 0, col: int = 0, sheet: "Sheet | None" = None
    ) -> None:
        self.row = row
        self.col = col
        self.cell: etree._Element = cell
        self.sheet = sheet
        self.format = cell.get(_VALUE_TYPE)
        if self.format == "date":
            self.raw_value = cell.get(_DATE_VALUE)
        elif self.format == "time":
            self.raw_value = cell.get(_TIME_VALUE)
        elif self.format == "boolean":
            self.raw_value = cell.get(_BOOLEAN_VALUE)
        else:
            self.raw_value = cell.get(_VALUE)
        # ODF has no newline inside a paragraph: a multi-line cell is one
        # <text:p> per line (what applications write when you type
        # Ctrl+Enter), so every paragraph counts, joined back with "\n".
        # recursive=False: a cell's own value paragraphs are always direct
        # children - an unscoped find_all() would also match those nested
        # inside an office:annotation (a cell comment, see Cell.comment).
        # `paragraph_text`, not `p.string` - None both for a genuinely empty
        # <text:p/> (a formula whose cached result is "") and for one with
        # several children (spans...) - nor `get_text()`, which drops the
        # spaces, tabs and line breaks ODF writes as elements (issue #27).
        # A cell of no child or of one paragraph, nearly all of them, is read
        # without listing its children: this runs once per cell of a sheet
        # being loaded, and the list cost a fifth of it.
        n_children = len(cell)
        self.text: str | None
        if n_children == 0:
            self.text = None
        elif n_children == 1:
            only = cell[0]
            if only.tag != _TEXT_P:
                self.text = None
            elif len(only) == 0:  # `paragraph_text`'s first line: one text node
                self.text = only.text or ""
            else:
                self.text = paragraph_text(only)
        else:
            paragraphs = list(cell.iterchildren(_TEXT_P))
            self.text = "\n".join([paragraph_text(p) for p in paragraphs]) if paragraphs else None
        # A value its declared type cannot be read from - a malformed date, a
        # type no reader knows (KeyError) - must not take the whole sheet
        # down with it: the cell reads as its displayed text instead, and
        # Sheet.load warns once per sheet, from `_unreadable`.
        self._unreadable: str | None = None
        if self.format == "string":
            self._value = self.text
        else:
            try:
                value = FORMATS[self.format](self.raw_value)
            except (KeyError, TypeError, ValueError):
                self._value = self.text
                self._unreadable = f"{self.format} value {self.raw_value!r}"
            else:
                # PT07H30M is a time of day, or 7 h 30 of a duration: the
                # format tells them apart - see `_counts_elapsed_time`
                if isinstance(value, dt.time) and self._counts_elapsed_time():
                    value = _as_timedelta(value)
                self._value = value
        self._formula = cell.get(_FORMULA)

        self.is_formula = self._formula is not None
        self.is_empty = self._compute_is_empty()
        # tracks the style forked by _ensure_own_style, if any - a Python-side
        # attribute rather than a "does the name look forked" check on
        # `table:style-name`, since `cell.style = other_cell.style`/`Sheet.copy`
        # can legitimately point two different cells at the very same forked
        # style name, and a name-based check can't tell those apart.
        self._own_style_name: str | None = None

    def _at(self, row: int, col: int) -> "Cell":
        """This cell, read at (`row`, `col`): another position of the same
        element - a cell repeated over columns, or a row over rows - which
        reads alike, save a time value (see `_counts_elapsed_time`). A copy of
        what `__init__` read rather than a second reading of the element: lxml
        builds a string for each attribute it is asked, and a sheet's blank
        padding is most of its positions."""
        copy = Cell.__new__(type(self))
        copy.__dict__ = self.__dict__.copy()
        copy.row, copy.col = row, col
        return copy

    @property
    def value(self) -> Any:
        return self._value

    @value.setter
    def value(self, new_value: Any) -> None:
        new_value = _plain_value(new_value)  # before anything changes: it may raise
        self._prepare_for_write()
        tag = self.cell

        for attr in self._VALUE_ATTRS:
            unset(tag, attr)
        unset(tag, _FORMULA)

        # `self.format`/`self.raw_value`/`self.text` still hold the pre-write state here
        # (they're only reassigned further down) - used below as one of the display-text
        # inference candidates for a cell being overwritten in place.
        if new_value is None:
            fmt, text = None, None
        elif isinstance(new_value, bool):
            fmt = "boolean"
            tag.set(_BOOLEAN_VALUE, "true" if new_value else "false")
            text = self._infer_boolean_display(new_value) or ("true" if new_value else "false")
        elif isinstance(new_value, str):
            fmt, text = "string", new_value
        elif isinstance(new_value, (int, float)):
            # keep the existing display format (float/percentage/currency) if there was one
            fmt = self.format if self.format in ("float", "percentage", "currency") else "float"
            tag.set(_VALUE, str(new_value))
            text = (
                self._infer_number_display(fmt, new_value)
                or self._render_display_from_number_format(new_value)
                or str(new_value)
            )
        elif isinstance(new_value, dt.date):  # a datetime.datetime too
            fmt = "date"
            tag.set(_DATE_VALUE, _format_date_value(new_value))
            # as reading it back gives it: an aware datetime turns naive, in UTC
            new_value = _parse_date_value(cast(str, tag.get(_DATE_VALUE)))
            kind = "datetime" if isinstance(new_value, dt.datetime) else "date"
            self._format_if_unformatted(kind, new_value)
            # the cell's own format first - a date one now, unless it had one
            # of another kind - as an example could be a cell formatted otherwise
            same_format = self._has_date_time_format(new_value)
            text = (
                self._render_display_from_number_format(new_value)
                or self._infer_date_display(new_value, same_format=same_format)
                or str(new_value)  # the ISO format, a space before a time part
            )
        elif isinstance(new_value, (dt.time, dt.timedelta)):
            fmt = "time"
            # the kind written, not the one read back: 7 h 30 written as a
            # duration shows as one, 07:30:00, where the format of a time of
            # day showed 07:30:00 AM in an en-US document (issue #16)
            kind = "duration" if isinstance(new_value, dt.timedelta) else "time"
            tag.set(_TIME_VALUE, _format_time_value(new_value))
            # as reading it back gives it: a time if it fits in a day, else a
            # timedelta - and a timedelta in a format counting elapsed time
            new_value = _parse_time_value(cast(str, tag.get(_TIME_VALUE)))
            self._format_if_unformatted(kind, new_value)
            if isinstance(new_value, dt.time) and self._counts_elapsed_time():
                new_value = _as_timedelta(new_value)
            same_format = self._has_date_time_format(new_value)
            text = (  # as for a date
                self._render_display_from_number_format(new_value)
                or self._infer_time_display(new_value, same_format=same_format)
                or (
                    _duration_text(new_value)
                    if isinstance(new_value, dt.timedelta)
                    else new_value.isoformat()
                )
            )
        else:
            raise TypeError(f"unsupported value type for a Cell: {type(new_value)}")

        if fmt is None:
            unset(tag, _VALUE_TYPE)
        else:
            tag.set(_VALUE_TYPE, fmt)
        if _CALCEXT_VALUE_TYPE in tag.attrib:
            if fmt is None:
                unset(tag, _CALCEXT_VALUE_TYPE)
            else:
                tag.set(_CALCEXT_VALUE_TYPE, fmt)

        self._set_text(text)
        self.format = fmt
        self.raw_value = (
            tag.get(_VALUE) or tag.get(_DATE_VALUE) or tag.get(_TIME_VALUE) or tag.get(_BOOLEAN_VALUE)
        )
        self._formula = None
        self.is_formula = False
        self._value = new_value
        self._unreadable = None
        self.is_empty = self._compute_is_empty()

    @property
    def formula(self) -> "str | None":
        return self._formula

    @formula.setter
    def formula(self, new_formula: "str | None") -> None:
        if new_formula is not None:
            expanded, restore_escapes = _expand_formula_template(new_formula, self.row, self.col)
            new_formula = restore_escapes(_normalize_odf_formula(expanded))
            if new_formula.startswith("of:=") and self.sheet is not None and self.sheet.reader is not None:
                # `of:` only means OpenFormula where the document says so: the
                # prefix it binds to it, or `of` declared (DOCS.md, 1.0)
                new_formula = self.sheet.reader._formula_prefix() + new_formula[2:]
        self._write_formula(new_formula)

    def _write_formula(self, formula: "str | None") -> None:
        """Write `formula` as `table:formula` holds it, in ODF syntax with
        its prefix, where the `formula` setter expands and translates what a
        user writes. The edits that rewrite a formula read from the file
        come here: its braces are text or an inline array, never a `{r}`
        placeholder, which the setter would evaluate (issue #22)."""
        self._prepare_for_write()
        tag = self.cell

        for attr in self._VALUE_ATTRS:
            unset(tag, attr)
        unset(tag, _VALUE_TYPE)
        unset(tag, _CALCEXT_VALUE_TYPE)
        self._set_text(None)  # any previously cached/displayed value would now be stale

        if formula is None:
            unset(tag, _FORMULA)
        else:
            tag.set(_FORMULA, formula)
        self._formula = formula

        self.is_formula = self._formula is not None
        self.format = None
        self.raw_value = None
        self._value = None
        self._unreadable = None
        self.is_empty = self._compute_is_empty()

    @property
    def formula_friendly(self) -> "str | None":
        """`.formula` translated back into ordinary `A1`-style syntax for
        readability, e.g. `"of:=[.A2]+[.A3]"` reads as `"=A2+A3"` — the
        reverse of what `.formula = "A2+A3"` accepts on write. `None` if the
        cell has no formula. Best-effort: a construct the write-side
        translation doesn't cover either (a named range, an unusual
        reference shape) is passed through untranslated rather than guessed
        at."""
        return _friendly_formula(self._formula)

    def fill_formula(self, target: "str | Cell | ArrayValues") -> None:
        """Copy this cell's formula into every cell of `target`, shifting
        relative references the way a spreadsheet's fill handle does when a
        formula is dragged across a range: if this cell's formula is
        `"=A1+1"`, filling it one row down produces `"=A2+1"`; a
        `"$A$1"`-style absolute reference stays put regardless of direction.

        `target` is a sheet address string (resolved on this cell's own
        sheet) or a selection (e.g. `sheet["A3:A10"]`) - any shape, not just
        "downward": filling right or across a 2D block works the same way.
        Raises `ValueError` if a shifted reference would fall off the sheet.
        """
        if self._formula is None:
            raise ValueError(f"cell {self.address} has no formula to fill from")
        if isinstance(target, str):
            if self.sheet is None:
                raise RuntimeError(f"cell {self.address} has no owning sheet to resolve {target!r} on")
            target = self.sheet[target]
        if isinstance(target, Cell):  # a single-cell address resolves to a bare Cell
            target = ArrayValues(target)
        if not isinstance(target, ArrayValues):
            raise TypeError(f"fill_formula target must be a sheet address or selection, got {type(target)!r}")

        for cell in target._iter_cells():
            drow, dcol = cell.row - self.row, cell.col - self.col
            cell._write_formula(_shift_odf_formula(self._formula, drow, dcol))

    def _compute_is_empty(self) -> bool:
        return (
            self.raw_value is None
            and self._value is None
            and self.format is None
            and self.text is None
            and self._formula is None
        )

    def _prepare_for_write(self) -> None:
        """Make sure this cell is safe to mutate on its own.

        Cells inside a compressed repeated row/column, or inside a merged range,
        share their underlying XML element with other cells. Rather than refuse
        to write (or silently corrupt the neighbours), the sheet materializes
        ("unrepeats"/"unmerges") the relevant structure into independent elements
        first, repointing every affected `Cell` (this one included) at its own tag.

        Writing beyond the sheet's current extent grows it first (new rows/cells
        are created from an existing tag as a namespace template, same idea as
        `_set_text`): since `sheet[row, col]` builds a throw-away `Cell` for any
        out-of-range position rather than storing it, `self` is repointed at the
        newly created tag and installed as the sheet's canonical `Cell` for that
        position, so later reads/writes of the same position see this object.
        A grown cell is then materialized like any other: a run of repeated
        rows gets one new cell for all its rows (issue #5).
        """
        if self.sheet is None:
            raise RuntimeError(f"cell {self.address} has no owning sheet and cannot be written to")
        if self.cell is EMPTY_CELL:
            self.sheet.grow_to(self.row, self.col)
            new_cell = self.sheet.rows[self.row][self.col]
            self.__init__(new_cell.cell, row=self.row, col=self.col, sheet=self.sheet)  # type: ignore[misc]
            self.sheet.rows[self.row][self.col] = self
        self.sheet.materialize_cell(self.row, self.col)

    _DATE_PATTERNS = (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d-%m-%Y",
    )
    _TIME_PATTERNS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")
    # a date-time shows as a date pattern alone when its format has no time part
    _DATE_TIME_PATTERNS = _DATE_PATTERNS + tuple(
        " ".join(pair) for pair in itertools.product(_DATE_PATTERNS, _TIME_PATTERNS)
    )

    def _has_date_time_format(self, value: object) -> bool:
        """Whether this cell shows `value` with a date or time format. Only a
        cell shown with the same format is then an example to learn from -
        see `_format_template_candidates`."""
        number_format = self._resolved_number_format(value)
        return number_format is not None and number_format.family in ("date", "time")

    def _counts_elapsed_time(self) -> bool:
        """Whether this cell shows time in a format counting it in full -
        `[HH]:MM:SS`, `number:truncate-on-overflow="false"` - a duration's,
        as the format odsslicer gives a written timedelta is. ODF stores a
        duration and a time of day alike, PT07H30M: a value that fits in a
        day reads as a duration in such a format, as the timedelta written
        there, and as a time of day otherwise (issue #20). The answer is
        kept per number format, a column of times asking it for every cell."""
        if self.sheet is None or self.sheet.reader is None:
            return False
        catalog = self.sheet.reader._catalog
        return catalog.counts_elapsed_time(catalog.data_style_name(self._style_name()))

    def _read_by_format(self) -> None:
        """Read a time value again once the cell's format has changed, as
        `__init__` reads it: a duration in a format counting time in full,
        and a time of day if it fits in a day in any other (issue #20)."""
        if self.format != "time" or self.raw_value is None or self._unreadable is not None:
            return
        value: Any = _parse_time_value(self.raw_value)
        if isinstance(value, dt.time) and self._counts_elapsed_time():
            value = _as_timedelta(value)
        self._value = value

    def _number_format_at(self, tag: etree._Element) -> "str | None":
        """The name of the number format the cell element `tag` shows with:
        its own style's, else its column's default cell style's, following
        the styles' parents."""
        reader = self.sheet.reader
        name = tag.get(_STYLE_NAME)
        if name is None:
            table = next(tag.iterancestors(qn("table:table")), None)
            if table is None:
                return None
            col = sum(
                int(cell.get(_COLUMNS_REPEATED, "1"))
                for cell in tag.itersiblings(*_CELL_TAGS, preceding=True)
            )
            seen = 0
            for definition in _column_definitions(table):
                seen += int(definition.get(_COLUMNS_REPEATED, "1"))
                if col < seen:
                    name = definition.get(qn("table:default-cell-style-name"))
                    break
        return cast("ODSReader", reader)._catalog.data_style_name(name)

    def _format_template_candidates(
        self, fmt: "str | None", raw_attr: str, *, same_format: bool = False
    ) -> Iterator[tuple[str, str]]:
        """Other cells with the same ODF format (this cell's own pre-write state
        first, then the rest of the document) to learn a display pattern from,
        as (raw attribute value, displayed text) pairs.

        A *lazy* generator, and that matters a lot: each `preceding`/
        `following` probe traverses the document until it hits a matching
        cell - which means a full document scan when there is none in that
        direction. Consumers stop at the first candidate whose pattern
        checks out (usually the cell's own prior state, or a neighbour
        found backwards), so yielding lazily keeps the forward scan from
        running at all in the common case. Building the list eagerly made
        every write of a format with no example anywhere in the document
        cost two full scans - ~33 ms per cell on a 10,000-row sheet.

        With `same_format`, another cell counts only if it shows with the
        number format this one does: a long date took the layout of a short
        one, `09/27/26` for "Sunday, September 27, 2026" (issue #16). Past
        50 cells shown otherwise, the search stops in that direction."""
        if self.format == fmt and self.raw_value is not None and self.text is not None:
            yield (self.raw_value, self.text)
        wanted = self._number_format_at(self.cell) if same_format else None
        raw = qn(raw_attr)

        def matches(tag: etree._Element) -> bool:
            return tag.get(_VALUE_TYPE) == fmt and bool(tag.get(raw)) and tag is not self.cell

        for found in (preceding(self.cell, "table:table-cell"), following(self.cell, "table:table-cell")):
            skipped = 0
            for match in found:
                if not matches(match):
                    continue
                if wanted is not None and self._number_format_at(match) != wanted:
                    skipped += 1
                    if skipped == 50:
                        break
                    continue
                p = next(match.iterchildren(_TEXT_P), None)
                only = _only_string(p) if p is not None else None
                if only is not None:
                    yield (cast(str, match.get(raw)), only)
                break

    def _infer_number_display(self, fmt: str, new_value: float) -> "str | None":
        """Render `new_value` the way another float/percentage/currency cell in
        this document renders its own value (decimal separator, decimal count,
        prefix/suffix such as " %" or " €"), or None if no example is usable."""
        for template_raw, template_text in self._format_template_candidates(fmt, "office:value"):
            try:
                template_value = float(template_raw)
            except (TypeError, ValueError):
                continue
            m = re.search(r"-?\d+(?:[.,]\d+)?", template_text)
            if m is None:
                continue
            prefix, numeric, suffix = template_text[: m.start()], m.group(), template_text[m.end() :]
            decimal_sep = "," if "," in numeric else "."

            render: Callable[[float], str]
            if fmt == "float":
                # plain "General"-style cells show as many digits as the value
                # needs: rounding to the template's own decimal count would lose
                # precision (e.g. "3.4" as template -> only borrow the separator)
                def render_general(
                    value: float, _sep: str = decimal_sep, _prefix: str = prefix, _suffix: str = suffix
                ) -> str:
                    return f"{_prefix}{str(value).replace('.', _sep)}{_suffix}"

                render = render_general
            else:
                decimals = len(numeric.split(decimal_sep)[1]) if decimal_sep in numeric else 0
                scale = 100 if fmt == "percentage" else 1

                def render_fixed(
                    value: float,
                    _dec: int = decimals,
                    _scale: int = scale,
                    _sep: str = decimal_sep,
                    _prefix: str = prefix,
                    _suffix: str = suffix,
                ) -> str:
                    rendered = f"{value * _scale:.{_dec}f}".replace(".", _sep)
                    return f"{_prefix}{rendered}{_suffix}"

                render = render_fixed

            if render(template_value) == template_text:  # sanity check before trusting the pattern
                return render(new_value)
        return None

    def _infer_date_display(self, new_value: dt.date, *, same_format: bool = False) -> "str | None":
        # dates and date-times alike go through as date-times: a date-time
        # pattern then also covers a date - LibreOffice saves a date-time
        # falling on midnight as a bare date - and a date pattern a date-time
        for template_raw, template_text in self._format_template_candidates(
            "date", "office:date-value", same_format=same_format
        ):
            try:
                template_value = _as_datetime(_parse_date_value(template_raw))
            except (TypeError, ValueError):
                continue
            for pattern in self._DATE_TIME_PATTERNS:
                if template_value.strftime(pattern) == template_text:
                    return _as_datetime(new_value).strftime(pattern)
        return None

    def _infer_time_display(
        self, new_value: "dt.time | dt.timedelta", *, same_format: bool = False
    ) -> "str | None":
        # a duration past a day shows wrapped in a clock format (08:45:00) but
        # not in an elapsed-time one (128:45:00), and an example within a day
        # looks the same in both: only the cell's own format can tell
        if isinstance(new_value, dt.timedelta):
            return None
        for template_raw, template_text in self._format_template_candidates(
            "time", "office:time-value", same_format=same_format
        ):
            try:
                template_value = _parse_time_value(template_raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(template_value, dt.time):
                continue
            for pattern in self._TIME_PATTERNS:
                if template_value.strftime(pattern) == template_text:
                    return new_value.strftime(pattern)
        return None

    def _infer_boolean_display(self, new_value: bool) -> "str | None":  # noqa: FBT001 - the value, not a flag
        target_raw = "true" if new_value else "false"
        candidates = self._format_template_candidates("boolean", "office:boolean-value")
        for template_raw, template_text in candidates:
            if template_raw == target_raw:
                return template_text
        return None

    def _resolved_number_format(self, value: object) -> "NumberFormat | None":
        """This cell's real `NumberFormat`, resolved against `value` (for
        a conditional format, e.g. red-negative-currency) - unlike the
        `.style` property, this always resolves against `value` directly
        rather than `self._value` (which, mid-write, still holds the
        *previous* value). `None` if this cell has no owning
        `ODSReader`/style, or no `style:data-style-name` at all."""
        if self.sheet is None or self.sheet.reader is None:
            return None
        style = CellStyle(self.sheet.reader, self._style_name(), value=value, cell=self)
        return style.number_format

    def _format_if_unformatted(self, kind: str, value: object) -> None:
        """Give this cell the format a `kind` of date or time value gets
        (see `dateformats`) if it shows with no number format at all, as
        LibreOffice does to a date typed there - and leave any other format
        alone, whatever its kind, as LibreOffice does too (issue #7).

        The cell gets a style shared by every cell whose style it was, plus
        the format - not one of its own, of which a column of dates would
        make a thousand (`_StyleCatalog.date_formatted_style`); a later
        write to its style forks one as usual."""
        if self.sheet is None or self.sheet.reader is None:
            return
        if self._resolved_number_format(value) is not None:
            return
        catalog = self.sheet.reader._catalog
        self.cell.set(_STYLE_NAME, catalog.date_formatted_style(self._style_name(), kind))
        self._own_style_name = None

    def _render_display_from_number_format(self, new_value: object) -> "str | None":
        """The display text `new_value` should have per this cell's own
        real, resolved number format - decimal places, grouping, currency
        symbol, or a date/time layout - read directly from the document
        rather than guessed from another cell's example. Used as a
        fallback in `.value`'s setter, once `_infer_*_display` finds no
        example cell to learn from (previously the silent gap this closes
        - see the README's known limitations). `None` if this cell has no
        resolvable format, or the format's family doesn't apply to
        `new_value`'s type."""
        number_format = self._resolved_number_format(new_value)
        if isinstance(new_value, (int, float)) and not isinstance(new_value, bool):
            return _render_number_from_format(number_format, new_value)
        if isinstance(new_value, dt.date):  # a datetime.datetime too
            return _render_date_time_from_format(number_format, new_value, "date")
        if isinstance(new_value, (dt.time, dt.timedelta)):
            return _render_date_time_from_format(number_format, new_value, "time")
        return None

    def _set_text(self, text: "str | None") -> None:
        paragraphs = children(self.cell, "text:p")  # see __init__'s note on scoping
        if text is None:
            for p in paragraphs:
                remove(p)
            self.text = None
            return
        # one paragraph per line, the only way ODF represents a multi-line
        # cell (a literal "\n" inside a paragraph is just whitespace there)
        lines = text.split("\n")
        for extra in paragraphs[len(lines) :]:
            remove(extra)
        for i, line in enumerate(lines):
            p = paragraphs[i] if i < len(paragraphs) else self._new_text_paragraph()
            set_paragraph_text(p, line)
        self.text = text

    def _new_text_paragraph(self) -> etree._Element:
        """A fresh, empty `<text:p>` appended to this cell.

        An existing text:p elsewhere in the document is preferred as a
        template (keeps whatever incidental formatting lxml would otherwise
        not know how to reproduce), falling back to building one from
        scratch (see `xmltree.new`) if there is none."""
        # `is None`, not `or`: a paragraph with no child element is false
        template = next(preceding(self.cell, "text:p"), None)
        if template is None:
            template = next(following(self.cell, "text:p"), None)
        if template is None:
            p = new("text:p")
        else:
            p = clone(template)
            set_text(p, "")
        self.cell.append(p)
        return p

    def __call__(self) -> Any:
        return self.value

    def __repr__(self) -> str:
        text = None if self.text is None else f"'{self.text}'"
        return f"Cell(address={self.address}, value={self.value}, format={self.format}, text={text})"

    def __str__(self) -> str:
        return self.text if self.text is not None else str(None)

    def __eq__(self, cell: Any) -> bool:
        if type(cell) is Cell:
            return cell.value == self.value
        return cell == self.value

    def __gt__(self, cell: Any) -> bool:
        return cell.value < self.value

    def __lt__(self, cell: Any) -> bool:
        return cell.value > self.value

    def __ge__(self, cell: Any) -> bool:
        return cell.value <= self.value

    def __le__(self, cell: Any) -> bool:
        return cell.value >= self.value

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)

    def __abs__(self) -> Any:
        return abs(self.value)

    def __neg__(self) -> Any:
        return -self.value

    def __pos__(self) -> Any:
        return self.value

    def __round__(self, ndigits: "int | None" = None) -> Any:
        return round(self.value, ndigits)

    def __trunc__(self) -> int:
        return math.trunc(self.value)

    def __ceil__(self) -> int:
        return math.ceil(self.value)

    def __floor__(self) -> int:
        return math.floor(self.value)

    @property
    def address(self) -> str:
        return string_address(self.row, self.col)

    @property
    def style(self) -> "CellStyle | None":
        """This cell's resolved `CellStyle` (visual formatting + real number
        format) - writable, see `CellStyle`. `None` only if the cell has no
        owning `ODSReader` at all (nothing to resolve or create styles
        against). A cell with no `table:style-name` shows with its column's
        default cell style, and resolves to it; with neither, it still gets
        a `CellStyle` back with every property `None`/`False`. Setting any
        writable property on it (e.g. `cell.style.bold = True`) gives the
        cell its own style on the spot, carrying its column's over.

        `.number_format` is already resolved against this cell's own value
        when the format is conditional (e.g. currency shown in red only when
        negative) - see `NumberFormat.resolve`.
        """
        if self.sheet is None or self.sheet.reader is None:
            return None
        return CellStyle(self.sheet.reader, self._style_name(), cell=self)

    @style.setter
    def style(self, other: "CellStyle | Cell | str | None") -> None:
        """Copy another cell's whole style onto this one in one shot -
        `cell.style = other_cell.style`, `= other_cell`, or `= "ce9"` (a
        raw style name) all work; `= None` clears this cell's style.

        This points this cell at the *same* underlying style as `other`
        (or the named one) rather than deep-copying its properties - safe
        even if that style is shared with many other cells, since setting
        any individual property later (`cell.style.bold = True`) forks a
        private copy on the spot (see `_ensure_own_style`) without
        affecting `other` or anything else that still uses it."""
        self._prepare_for_write()
        if other is None:
            name = None
        elif isinstance(other, CellStyle):
            name = other.name
        elif isinstance(other, Cell):
            name = other.style_name
        elif isinstance(other, str):
            name = other
        else:
            raise TypeError(f"cell.style must be a CellStyle, Cell, str, or None, got {type(other)!r}")
        if name is None:
            unset(self.cell, _STYLE_NAME)
        else:
            self.cell.set(_STYLE_NAME, name)
        self._read_by_format()

    @property
    def style_name(self) -> "str | None":
        """The name of this cell's own style - its `table:style-name`, `"ce9"`
        say - or `None` for a cell that has none and shows with its column's
        default cell style. What `cell.attrs.get("table:style-name")` read
        before 1.0 removed `attrs` (DOCS.md, "What changes in 1.0");
        `cell.style` gives the style itself, resolved."""
        return self.cell.get(_STYLE_NAME)

    def _style_name(self) -> "str | None":
        """The name of the style this cell shows with: its own
        `table:style-name`, else its column's default cell style, where
        LibreOffice keeps the format of a column formatted as a whole."""
        own = self.cell.get(_STYLE_NAME)
        if own is not None or self.sheet is None:
            return own
        return _column_default_cell_style(self.sheet.table, self.col)

    def _ensure_own_style(self) -> etree._Element:
        """This cell's own, uniquely-owned automatic style tag - safe to
        mutate in place without affecting any other cell, even one that
        currently points at the very same style name (e.g. right after
        `cell.style = other_cell.style`, or `Sheet.copy`).

        The first call forks one off the cell's current style (if any),
        carrying over everything that style resolves to (see
        `_StyleCatalog.fork_cell_style`), so every already-resolved property
        keeps applying except the ones a later write explicitly overrides;
        further calls on this same `Cell` (e.g. setting several properties
        one after another) reuse that same forked style instead of forking
        again - tracked via `self._own_style_name`, a Python-side flag
        rather than a check on the style name's shape, since two different
        cells can legitimately share a forked-looking name and a
        name-based check can't tell those apart."""
        self._prepare_for_write()
        reader = self.sheet.reader
        if reader is None:
            raise RuntimeError(f"cell {self.address} has no owning ODSReader and cannot be styled")
        current_name = self.cell.get(_STYLE_NAME)
        if self._own_style_name is not None and current_name == self._own_style_name:
            tag = reader._catalog.find_style(current_name, family="table-cell")
            if tag is not None:
                return tag
        # a cell with no style of its own shows with its column's: the fork
        # has to carry that one, or the cell would lose the column's format
        tag = reader._catalog.fork_cell_style(self._style_name())
        new_name = cast(str, tag.get(qn("style:name")))
        self.cell.set(_STYLE_NAME, new_name)
        self._own_style_name = new_name
        return tag

    @property
    def is_merge_master(self) -> bool:
        """True if this cell is the top-left cell of a merged range (it
        carries `table:number-rows-spanned`/`table:number-columns-spanned`
        greater than 1)."""
        return self.cell.get(_COLUMNS_SPANNED, "1") != "1" or self.cell.get(_ROWS_SPANNED, "1") != "1"

    @property
    def is_covered(self) -> bool:
        """True if this cell is hidden inside another cell's merged range
        (a `table:covered-table-cell`) - its own value/formatting is still
        there in the XML, just not shown, until `Sheet.unmerge(...)`
        reveals it again."""
        return self.cell.tag == _COVERED_CELL

    @property
    def is_merged(self) -> bool:
        """True if this cell participates in a merged range at all -
        either as the master or as one of the covered cells."""
        return self.is_merge_master or self.is_covered

    @property
    def merge_master(self) -> "Cell | None":
        """The top-left `Cell` of this cell's merged range - `self` if this
        cell already is the master, or `None` if it isn't part of any merge
        (or has no owning sheet to look the master up on)."""
        if self.is_merge_master:
            return self
        if not self.is_covered or self.sheet is None:
            return None
        return self.sheet._merge_master(self.row, self.col)

    @property
    def merge_span(self) -> "tuple[int, int] | None":
        """`(n_rows, n_cols)` spanned by this cell's merged range (from its
        master's point of view), or `None` if this cell isn't merged."""
        master = self.merge_master
        if master is None:
            return None
        return (
            int(master.cell.get(_ROWS_SPANNED, "1")),
            int(master.cell.get(_COLUMNS_SPANNED, "1")),
        )

    @property
    def merge_range(self) -> "str | None":
        """This cell's merged range as an `"A1:B2"`-style address string
        (resolvable from any cell in the range, not just the master), or
        `None` if it isn't merged."""
        master = self.merge_master
        if master is None:
            return None
        span = self.merge_span
        assert span is not None  # a master exists, so the span resolves
        n_rows, n_cols = span
        start = string_address(master.row, master.col)
        if n_rows == 1 and n_cols == 1:
            return start
        end = string_address(master.row + n_rows - 1, master.col + n_cols - 1)
        return f"{start}:{end}"

    @property
    def comment(self) -> "Comment | None":
        """This cell's `Comment` (note/`office:annotation`) - `None` if it
        has none. Writable: `cell.comment = "some text"` creates one (or
        updates an existing one's `.text`); `cell.comment = None` removes
        it entirely. Once a comment exists, set its other properties
        directly - `cell.comment.author = "Jane"`, `.date = datetime.now()`,
        `.visible = True`."""
        tag = child(self.cell, "office:annotation")
        return Comment(tag) if tag is not None else None

    @comment.setter
    def comment(self, value: "str | None") -> None:
        self._prepare_for_write()
        tag = child(self.cell, "office:annotation")
        if value is None:
            if tag is not None:
                remove(tag)
            return
        if not isinstance(value, str):
            raise TypeError(f"cell.comment must be a str or None, got {type(value)!r}")
        if tag is None:
            tag = new("office:annotation")
            # after any text the cell starts with, where bs4 put it before:
            # indentation at most, in a cell
            self.cell.insert(0, tag)
        Comment(tag).text = value

    @property
    def hyperlink(self) -> "str | None":
        """This cell's hyperlink URL (`xlink:href` on a `<text:a>`
        wrapping the cell's whole text), or `None` if it has none.
        Writable: `cell.hyperlink = "https://..."` wraps the cell's
        current text in a link (giving it empty text first if it has
        none yet); `cell.hyperlink = None` unwraps it, leaving the plain
        text in place. Only a whole-cell link is supported - a link on
        just part of the text, mixed with plain text, isn't modeled.

        Writing a new `.value` afterwards replaces the cell's text (link
        included) same as it always does - the link isn't carried over,
        since it was tied to that specific text."""
        p = child(self.cell, "text:p")
        if p is None:
            return None
        a = child(p, "text:a")
        return a.get(qn("xlink:href")) if a is not None else None

    @hyperlink.setter
    def hyperlink(self, url: "str | None") -> None:
        self._prepare_for_write()
        p = child(self.cell, "text:p")
        if url is None:
            if p is not None:
                a = child(p, "text:a")
                if a is not None:
                    unwrap(a)
            return
        if not isinstance(url, str):
            raise TypeError(f"cell.hyperlink must be a str or None, got {type(url)!r}")
        if p is None:
            self._set_text(self.text or "")
            p = cast(etree._Element, child(self.cell, "text:p"))
        a = child(p, "text:a")
        if a is None:
            # the link given at creation, so that `new` declares `xlink`,
            # which a document that has no link yet may not bind
            a = new("text:a", {"xlink:href": url})
            # the paragraph's content moves into the link as it is - spans and
            # encoded spaces included - rather than flattened to its text
            wrap_contents(p, a)
        else:
            a.set(qn("xlink:href"), url)
