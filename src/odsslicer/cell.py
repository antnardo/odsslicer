# mypy: disable-error-code="union-attr"
# (`Element | None` unions are narrowed dynamically all over this module,
# guarded by runtime checks mypy can't see through - silencing that one error
# class here beats dozens of value-free asserts/casts. Every other error
# class, and all signatures, remain fully checked.)
"""Cell: one position of a sheet's grid, over the element that stands for it
- its value, text and formula read on loading, and written in place; its
style, merge, note and link.

What a written value becomes in the element is `values`', and the text it
shows `display`'s; a selection of cells is `selection`'s `ArrayValues`, a
note `comment`'s `Comment`, and a style the catalog's to find or fork.
`Cell` was all of them, with the text inferred on writing its largest part,
until 1.0's audit. `Cell.__init__` runs for every position of every sheet
loaded that does not copy another's reading (`Cell._at`, see `loading`):
it reads the element's attributes once each, and its children only when it
has more than one.
"""

import datetime as dt
import math
from typing import TYPE_CHECKING, Any, cast

from lxml import etree

from odsslicer.addresses import string_address
from odsslicer.columns import _column_default_cell_style
from odsslicer.comment import Comment
from odsslicer.constants import EMPTY_CELL, FORMATS
from odsslicer.datetimes import _as_timedelta, _parse_time_value
from odsslicer.display import _DisplayText
from odsslicer.formulas import (
    _expand_formula_template,
    _friendly_formula,
    _normalize_odf_formula,
    _shift_odf_formula,
)
from odsslicer.selection import ArrayValues
from odsslicer.styles import CellStyle
from odsslicer.values import (
    _CALCEXT_VALUE_TYPE,
    _RAW_VALUE_ATTRIBUTES,
    _STRING_VALUE,
    _VALUE,
    _VALUE_ATTRS,
    _VALUE_TYPE,
    _encoded,
    _plain_value,
    _set_value_type,
)
from odsslicer.xmltree import (
    child,
    children,
    clone,
    following,
    new,
    paragraph_text,
    preceding,
    qn,
    remove,
    set_paragraph_text,
    set_text,
    unset,
    unwrap,
    wrap_contents,
)

if TYPE_CHECKING:
    from odsslicer.numberformat import NumberFormat
    from odsslicer.sheet import Sheet

__all__ = ["Cell"]

_FORMULA = qn("table:formula")
_STYLE_NAME = qn("table:style-name")
_COLUMNS_SPANNED = qn("table:number-columns-spanned")
_ROWS_SPANNED = qn("table:number-rows-spanned")
_TEXT_P = qn("text:p")
_COVERED_CELL = qn("table:covered-table-cell")


class Cell:
    def __init__(
        self, cell: etree._Element, row: int = 0, col: int = 0, sheet: "Sheet | None" = None
    ) -> None:
        self.row = row
        self.col = col
        self.cell: etree._Element = cell
        self.sheet = sheet
        self.format = cell.get(_VALUE_TYPE)
        self.raw_value = cell.get(_RAW_VALUE_ATTRIBUTES.get(self.format, _VALUE))
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
        # A string cell may carry its value in `office:string-value` rather
        # than in its paragraphs - a formula's cached result, what some
        # producers write - and LibreOffice then shows that, over the
        # paragraphs: so does odsslicer, value and text alike. But for an
        # error: ODF has no such type, and LibreOffice writes a formula's
        # error as an empty string marked `calcext:value-type="error"`,
        # showing its paragraph, "Err:540".
        if self.format == "string":
            string_value = cell.get(_STRING_VALUE)
            if string_value is not None and cell.get(_CALCEXT_VALUE_TYPE) != "error":
                self.text = string_value
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
        # what the value stands for and how it is written, before anything
        # changes: either may raise
        encoded = _encoded(_plain_value(new_value), self.format)
        self._prepare_for_write()
        tag = self.cell
        for attr in _VALUE_ATTRS:
            unset(tag, attr)
        unset(tag, _FORMULA)
        if encoded.attribute is not None:
            tag.set(encoded.attribute, cast(str, encoded.raw))
        value = encoded.value
        if encoded.kind is not None:
            # a date or a time: the format a cell with none gets, then the
            # value as it reads in its format - a time of day as a duration
            # in one counting time in full
            self._format_if_unformatted(encoded.kind, value)
            if isinstance(value, dt.time) and self._counts_elapsed_time():
                value = _as_timedelta(value)
        # `self.format`/`self.raw_value`/`self.text` still hold the pre-write
        # state here: the first example the displayed text is learnt from
        text = _DisplayText(self).of(value, encoded.value_type)
        _set_value_type(tag, encoded.value_type)
        self._set_text(text)
        self.format = encoded.value_type
        self.raw_value = encoded.raw
        self._formula = None
        self.is_formula = False
        self._value = value
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

        for attr in _VALUE_ATTRS:
            unset(tag, attr)
        _set_value_type(tag, None)
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
