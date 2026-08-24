# -*- coding: utf-8 -*-
"""ArrayValues (multi-cell selections), Comment (cell notes) and Cell itself."""

import copy
import datetime as dt
import math
import re
from typing import TYPE_CHECKING, Dict

import numpy as np
from bs4 import BeautifulSoup

from .addresses import string_address
from .constants import EMPTY_CELL_BS, FORMATS
from .formulas import (
    _expand_formula_template,
    _friendly_formula,
    _normalize_odf_formula,
    _shift_odf_formula,
)
from .styles import CellStyle, _render_date_time_from_format, _render_number_from_format
from .xmlutils import _ODF_NAMESPACES, _blank_template, _new_qualified_tag

if TYPE_CHECKING:
    from .sheet import Sheet


def _is_broadcastable_scalar(value):
    """True for a value that should be written as-is to every cell of a
    multi-cell selection, rather than unpacked element-wise (a `str` is
    iterable but clearly meant as one value, not one cell per character)."""
    return value is None or isinstance(value, (str, bool, int, float, dt.date, dt.time))


class ArrayValues:
    def __init__(self, array):
        self.array = array
        self.dimension = self._get_dimension(array)
        self.size = self._get_size()

    def _get_size(self):
        size = []
        cut = self.array
        for d in range(self.dimension):
            size.append(len(cut))
            cut = cut[0]
        return tuple(size)

    @classmethod
    def _get_dimension(cls, array):
        try:
            return cls._get_dimension(array[0]) + 1
        except (TypeError, IndexError):
            return 0

    def _iter_cells(self):
        """Yield every underlying `Cell`, regardless of this selection's
        dimension (a single cell, a row/column, or a 2D block)."""
        if self.dimension == 0:
            yield self.array
        else:
            for item in self.array:
                yield from ArrayValues(item)._iter_cells()

    def __repr__(self):
        return f"ArrayValue({self.array})"

    def __getitem__(self, i):
        return self.array[i]

    def __len__(self):
        return len(self.array)

    def to_numpy(self):
        return np.array(self.to_list())

    @property
    def value(self):
        return self.cell.value

    @value.setter
    def value(self, new_value):
        if self.dimension == 0:
            self.cell.value = new_value
            return
        if _is_broadcastable_scalar(new_value):
            for item in self.array:
                ArrayValues(item).value = new_value
            return
        values = list(new_value)
        if len(values) != len(self.array):
            raise ValueError(
                f"shape mismatch: {len(self.array)} cell(s) but {len(values)} value(s) given"
            )
        for item, v in zip(self.array, values):
            ArrayValues(item).value = v

    @property
    def formula(self):
        return self.cell.formula

    @property
    def formula_friendly(self):
        return self.cell.formula_friendly

    @formula.setter
    def formula(self, new_formula):
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
    def cell(self) -> "Cell":
        assert self.dimension == 0
        return self.array

    def to_list(self):
        if self.dimension == 0:
            return self.value
        elif self.dimension == 1:
            return [cell.value for cell in self.array]
        elif self.dimension == 2:
            return [[cell.value for cell in row] for row in self.array]

    def to_vector(self):
        assert self.dimension == 2 and self.size[1] == 1
        return ArrayValues([row[0] for row in self.array])

    def __eq__(self, array):
        return array.to_list() == self.to_list()


class Comment:
    """A cell's note (`<office:annotation>`) - `.text` (the note body,
    `\\n`-joined across ODF's own multiple `<text:p>` paragraphs),
    `.author` (`dc:creator`), `.date` (`dc:date`, a `datetime.datetime`),
    `.visible` (`office:display` - shown pinned open vs. only on hover).
    Look up via `Cell.comment`, not directly - writable: set any property
    to update it in place, once the comment itself exists (see
    `Cell.comment`'s setter to create one)."""

    def __init__(self, tag):
        self._tag = tag

    def _insert_before_text(self, child):
        # keeps dc:creator/dc:date ahead of the text:p paragraphs, matching
        # real ODF layout, regardless of which property gets set first
        first_p = self._tag.find("text:p")
        if first_p is not None:
            first_p.insert_before(child)
        else:
            self._tag.append(child)

    @property
    def text(self):
        paragraphs = [p.get_text() for p in self._tag.find_all("text:p")]
        return "\n".join(paragraphs) if paragraphs else None

    @text.setter
    def text(self, value):
        for p in self._tag.find_all("text:p"):
            p.decompose()
        for line in (value or "").split("\n"):
            p = _blank_template(self._tag, "text:p")
            p.string = line
            self._tag.append(p)

    @property
    def author(self):
        tag = self._tag.find("dc:creator")
        return tag.get_text() if tag is not None else None

    @author.setter
    def author(self, value):
        tag = self._tag.find("dc:creator")
        if value is None:
            if tag is not None:
                tag.decompose()
            return
        if tag is None:
            tag = _blank_template(self._tag, "dc:creator")
            self._insert_before_text(tag)
        tag.string = value

    @property
    def date(self):
        tag = self._tag.find("dc:date")
        if tag is None:
            return None
        try:
            return dt.datetime.fromisoformat(tag.get_text())
        except ValueError:
            return None

    @date.setter
    def date(self, value):
        tag = self._tag.find("dc:date")
        if value is None:
            if tag is not None:
                tag.decompose()
            return
        if not isinstance(value, dt.datetime):
            raise TypeError(f"Comment.date must be a datetime.datetime or None, got {type(value)!r}")
        if tag is None:
            tag = _blank_template(self._tag, "dc:date")
            self._insert_before_text(tag)
        tag.string = value.isoformat()

    @property
    def visible(self):
        return self._tag.get("office:display") == "true"

    @visible.setter
    def visible(self, value):
        self._tag.attrs["office:display"] = "true" if value else "false"

    def __repr__(self):
        return f"Comment(author={self.author!r}, text={self.text!r})"


class Cell:
    #: attributes touched when a value is written to a cell
    _VALUE_ATTRS = (
        "office:value",
        "office:date-value",
        "office:time-value",
        "office:boolean-value",
    )

    def __init__(self, cell: BeautifulSoup, row: int = 0, col: int = 0, sheet: "Sheet" = None):
        self.row = row
        self.col = col
        self.cell: BeautifulSoup = cell
        self.sheet = sheet
        self.attrs: Dict[str, str] = self.cell.attrs
        self.format = self.attrs.get("office:value-type", None)
        if self.format == "date":
            self.raw_value = self.attrs.get("office:date-value")
        elif self.format == "time":
            self.raw_value = self.attrs.get("office:time-value")
        elif self.format == "boolean":
            self.raw_value = self.attrs.get("office:boolean-value")
        else:
            self.raw_value = self.attrs.get("office:value", None)
        # recursive=False: a cell's own value text:p is always a direct child -
        # an unscoped find() would instead match one nested inside an
        # office:annotation (a cell comment, see Cell.comment) if there is one
        p = self.cell.find("text:p", recursive=False)
        if p is not None:
            # `p.string` is only ever a plain str when text:p has EXACTLY one text
            # child; it's None both for a genuinely empty <text:p/> (e.g. a formula
            # whose cached result is "") and for one with several children (spans,
            # line breaks...) - `str(None)` would then wrongly become the literal
            # string "None" instead of the cell's actual (possibly empty) text.
            self.text = p.get_text()
        else:
            self.text = None
        if self.format == "string":
            self._value = self.text
        else:
            self._value = FORMATS[self.format](self.raw_value)
        self._formula = self.attrs.get("table:formula", None)

        self.is_formula = self._formula is not None
        self.is_empty = self._compute_is_empty()
        # tracks the style forked by _ensure_own_style, if any - a Python-side
        # attribute rather than a "does the name look forked" check on
        # `table:style-name`, since `cell.style = other_cell.style`/`Sheet.copy`
        # can legitimately point two different cells at the very same forked
        # style name, and a name-based check can't tell those apart.
        self._own_style_name = None

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new_value):
        self._prepare_for_write()
        tag = self.cell

        for attr in self._VALUE_ATTRS:
            tag.attrs.pop(attr, None)
        tag.attrs.pop("table:formula", None)

        # `self.format`/`self.raw_value`/`self.text` still hold the pre-write state here
        # (they're only reassigned further down) - used below as one of the display-text
        # inference candidates for a cell being overwritten in place.
        if new_value is None:
            fmt, text = None, None
        elif isinstance(new_value, bool):
            fmt = "boolean"
            tag.attrs["office:boolean-value"] = "true" if new_value else "false"
            text = self._infer_boolean_display(new_value) or ("true" if new_value else "false")
        elif isinstance(new_value, str):
            fmt, text = "string", new_value
        elif isinstance(new_value, (int, float)):
            # keep the existing display format (float/percentage/currency) if there was one
            fmt = self.format if self.format in ("float", "percentage", "currency") else "float"
            tag.attrs["office:value"] = str(new_value)
            text = (
                self._infer_number_display(fmt, new_value)
                or self._render_display_from_number_format(new_value)
                or str(new_value)
            )
        elif isinstance(new_value, dt.date) and not isinstance(new_value, dt.datetime):
            fmt = "date"
            tag.attrs["office:date-value"] = new_value.isoformat()
            text = (
                self._infer_date_display(new_value)
                or self._render_display_from_number_format(new_value)
                or new_value.isoformat()
            )
        elif isinstance(new_value, dt.time):
            fmt = "time"
            tag.attrs["office:time-value"] = new_value.strftime("PT%HH%MM%SS")
            text = (
                self._infer_time_display(new_value)
                or self._render_display_from_number_format(new_value)
                or new_value.isoformat()
            )
        else:
            raise TypeError(f"unsupported value type for a Cell: {type(new_value)}")

        if fmt is None:
            tag.attrs.pop("office:value-type", None)
        else:
            tag.attrs["office:value-type"] = fmt
        if "calcext:value-type" in tag.attrs:
            if fmt is None:
                del tag.attrs["calcext:value-type"]
            else:
                tag.attrs["calcext:value-type"] = fmt

        self._set_text(text)
        self.format = fmt
        self.raw_value = (
            tag.attrs.get("office:value")
            or tag.attrs.get("office:date-value")
            or tag.attrs.get("office:time-value")
            or tag.attrs.get("office:boolean-value")
        )
        self._formula = None
        self.is_formula = False
        self._value = new_value
        self.is_empty = self._compute_is_empty()

    @property
    def formula(self):
        return self._formula

    @property
    def formula_friendly(self):
        """`.formula` translated back into ordinary `A1`-style syntax for
        readability, e.g. `"of:=[.A2]+[.A3]"` reads as `"=A2+A3"` — the
        reverse of what `.formula = "A2+A3"` accepts on write. `None` if the
        cell has no formula. Best-effort: a construct the write-side
        translation doesn't cover either (a named range, an unusual
        reference shape) is passed through untranslated rather than guessed
        at."""
        return _friendly_formula(self._formula)

    @formula.setter
    def formula(self, new_formula):
        self._prepare_for_write()
        tag = self.cell

        for attr in self._VALUE_ATTRS:
            tag.attrs.pop(attr, None)
        tag.attrs.pop("office:value-type", None)
        tag.attrs.pop("calcext:value-type", None)
        self._set_text(None)  # any previously cached/displayed value would now be stale

        if new_formula is None:
            tag.attrs.pop("table:formula", None)
            self._formula = None
        else:
            expanded, restore_escapes = _expand_formula_template(new_formula, self.row, self.col)
            self._formula = restore_escapes(_normalize_odf_formula(expanded))
            tag.attrs["table:formula"] = self._formula

        self.is_formula = self._formula is not None
        self.format = None
        self.raw_value = None
        self._value = None
        self.is_empty = self._compute_is_empty()

    def fill_formula(self, target):
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
                raise RuntimeError(
                    f"cell {self.address} has no owning sheet to resolve {target!r} on"
                )
            target = self.sheet[target]
        if isinstance(target, Cell):  # a single-cell address resolves to a bare Cell
            target = ArrayValues(target)
        if not isinstance(target, ArrayValues):
            raise TypeError(f"fill_formula target must be a sheet address or selection, got {type(target)!r}")

        for cell in target._iter_cells():
            drow, dcol = cell.row - self.row, cell.col - self.col
            cell.formula = _shift_odf_formula(self._formula, drow, dcol)

    def _compute_is_empty(self):
        return (
            self.raw_value is None
            and self._value is None
            and self.format is None
            and self.text is None
            and self._formula is None
        )

    def _prepare_for_write(self):
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
        """
        if self.sheet is None:
            raise RuntimeError(
                f"cell {self.address} has no owning sheet and cannot be written to"
            )
        if self.cell is EMPTY_CELL_BS:
            self.sheet.grow_to(self.row, self.col)
            new_cell = self.sheet.rows[self.row][self.col]
            self.__init__(new_cell.cell, row=self.row, col=self.col, sheet=self.sheet)
            self.sheet.rows[self.row][self.col] = self
            return
        self.sheet.materialize_cell(self.row, self.col)

    _DATE_PATTERNS = (
        "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y",
        "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y",
    )
    _TIME_PATTERNS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")

    def _format_template_candidates(self, fmt, raw_attr):
        """Other cells with the same ODF format (this cell's own pre-write state
        first, then the rest of the document) to learn a display pattern from,
        as (raw attribute value, displayed text) pairs."""
        candidates = []
        if self.format == fmt and self.raw_value is not None and self.text is not None:
            candidates.append((self.raw_value, self.text))

        def matches(tag):
            return (
                getattr(tag, "name", None) == "table-cell"
                and tag.attrs.get("office:value-type") == fmt
                and tag.attrs.get(raw_attr)
                and tag is not self.cell
            )

        for finder in (self.cell.find_previous, self.cell.find_next):
            match = finder(matches)
            if match is None:
                continue
            p = match.find("text:p", recursive=False)
            if p is not None and p.string is not None:
                candidates.append((match.attrs.get(raw_attr), str(p.string)))
        return candidates

    def _infer_number_display(self, fmt, new_value):
        """Render `new_value` the way another float/percentage/currency cell in
        this document renders its own value (decimal separator, decimal count,
        prefix/suffix such as " %" or " €"), or None if no example is usable."""
        for template_raw, template_text in self._format_template_candidates(fmt, "office:value"):
            try:
                template_raw = float(template_raw)
            except (TypeError, ValueError):
                continue
            m = re.search(r"-?\d+(?:[.,]\d+)?", template_text)
            if m is None:
                continue
            prefix, numeric, suffix = template_text[: m.start()], m.group(), template_text[m.end() :]
            decimal_sep = "," if "," in numeric else "."

            if fmt == "float":
                # plain "General"-style cells show as many digits as the value
                # needs: rounding to the template's own decimal count would lose
                # precision (e.g. "3.4" as template -> only borrow the separator)
                def render(value, _sep=decimal_sep, _prefix=prefix, _suffix=suffix):
                    return f"{_prefix}{str(value).replace('.', _sep)}{_suffix}"
            else:
                decimals = len(numeric.split(decimal_sep)[1]) if decimal_sep in numeric else 0
                scale = 100 if fmt == "percentage" else 1

                def render(value, _dec=decimals, _scale=scale, _sep=decimal_sep, _prefix=prefix, _suffix=suffix):
                    rendered = f"{value * _scale:.{_dec}f}".replace(".", _sep)
                    return f"{_prefix}{rendered}{_suffix}"

            if render(template_raw) == template_text:  # sanity check before trusting the pattern
                return render(new_value)
        return None

    def _infer_date_display(self, new_value):
        for template_raw, template_text in self._format_template_candidates("date", "office:date-value"):
            try:
                template_value = dt.datetime.strptime(template_raw, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
            for pattern in self._DATE_PATTERNS:
                if template_value.strftime(pattern) == template_text:
                    return new_value.strftime(pattern)
        return None

    def _infer_time_display(self, new_value):
        for template_raw, template_text in self._format_template_candidates("time", "office:time-value"):
            try:
                template_value = dt.datetime.strptime(template_raw, "PT%HH%MM%SS").time()
            except (TypeError, ValueError):
                continue
            for pattern in self._TIME_PATTERNS:
                if template_value.strftime(pattern) == template_text:
                    return new_value.strftime(pattern)
        return None

    def _infer_boolean_display(self, new_value):
        target_raw = "true" if new_value else "false"
        for template_raw, template_text in self._format_template_candidates("boolean", "office:boolean-value"):
            if template_raw == target_raw:
                return template_text
        return None

    def _resolved_number_format(self, value):
        """This cell's real `NumberFormat`, resolved against `value` (for
        a conditional format, e.g. red-negative-currency) - unlike the
        `.style` property, this always resolves against `value` directly
        rather than `self._value` (which, mid-write, still holds the
        *previous* value). `None` if this cell has no owning
        `ODSReader`/style, or no `style:data-style-name` at all."""
        if self.sheet is None or self.sheet.reader is None:
            return None
        style = CellStyle(self.sheet.reader, self.attrs.get("table:style-name"), value=value, cell=self)
        return style.number_format

    def _render_display_from_number_format(self, new_value):
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
        if isinstance(new_value, dt.date) and not isinstance(new_value, dt.datetime):
            return _render_date_time_from_format(number_format, new_value, "date")
        if isinstance(new_value, dt.time):
            return _render_date_time_from_format(number_format, new_value, "time")
        return None

    def _set_text(self, text):
        p = self.cell.find("text:p", recursive=False)  # see __init__'s note on scoping
        if text is None:
            if p is not None:
                p.decompose()
            self.text = None
            return
        if p is None:
            # An existing text:p elsewhere in the document is preferred as a
            # template (keeps whatever incidental formatting bs4/lxml would
            # otherwise not know how to reproduce), falling back to building
            # one from scratch (see `_new_qualified_tag`) if there is none.
            template = self.cell.find_previous("text:p") or self.cell.find_next("text:p")
            if template is None:
                p = _new_qualified_tag("text:p")
            else:
                p = copy.copy(template)
                p.string = ""
            self.cell.append(p)
        p.string = text
        self.text = text

    def __call__(self):
        return self.value

    def __repr__(self):
        text = None if self.text is None else f"'{self.text}'"
        return f"Cell(address={self.address}, value={self.value}, format={self.format}, text={text})"

    def __str__(self):
        return self.text if self.text is not None else str(None)

    def __eq__(self, cell):
        if type(cell) is Cell:
            return cell.value == self.value
        return cell == self.value

    def __gt__(self, cell):
        return cell.value < self.value

    def __lt__(self, cell):
        return cell.value > self.value

    def __ge__(self, cell):
        return cell.value <= self.value

    def __le__(self, cell):
        return cell.value >= self.value

    def __int__(self):
        return int(self.value)

    def __float__(self):
        return float(self.value)

    def __abs__(self):
        return abs(self.value)

    def __neg__(self):
        return -self.value

    def __pos__(self):
        return self.value

    def __round__(self, ndigits=None):
        return round(self.value, ndigits)

    def __trunc__(self):
        return math.trunc(self.value)

    def __ceil__(self):
        return math.ceil(self.value)

    def __floor__(self):
        return math.floor(self.value)

    @property
    def address(self):
        return string_address(self.row, self.col)

    @property
    def style(self):
        """This cell's resolved `CellStyle` (visual formatting + real number
        format) - writable, see `CellStyle`. `None` only if the cell has no
        owning `ODSReader` at all (nothing to resolve or create styles
        against); a cell with no `table:style-name` yet still gets a
        `CellStyle` back with every property `None`/`False` - setting any
        writable property on it (e.g. `cell.style.bold = True`) gives the
        cell its own style on the spot.

        `.number_format` is already resolved against this cell's own value
        when the format is conditional (e.g. currency shown in red only when
        negative) - see `NumberFormat.resolve`.
        """
        if self.sheet is None or self.sheet.reader is None:
            return None
        name = self.attrs.get("table:style-name")
        return CellStyle(self.sheet.reader, name, value=self._value, cell=self)

    @style.setter
    def style(self, other):
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
            name = other.attrs.get("table:style-name")
        elif isinstance(other, str):
            name = other
        else:
            raise TypeError(f"cell.style must be a CellStyle, Cell, str, or None, got {type(other)!r}")
        if name is None:
            self.attrs.pop("table:style-name", None)
        else:
            self.attrs["table:style-name"] = name

    _OWN_STYLE_PREFIX = "ocs"

    def _ensure_own_style(self):
        """This cell's own, uniquely-owned automatic style tag - safe to
        mutate in place without affecting any other cell, even one that
        currently points at the very same style name (e.g. right after
        `cell.style = other_cell.style`, or `Sheet.copy`).

        The first call forks one off the cell's current style (if any) as
        `style:parent-style-name`, so every already-resolved property keeps
        applying except the ones a later write explicitly overrides;
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
        current_name = self.attrs.get("table:style-name")
        if self._own_style_name is not None and current_name == self._own_style_name:
            tag = reader._find_style(current_name, family="table-cell")
            if tag is not None:
                return tag
        tag = reader._new_style_tag("table-cell", self._OWN_STYLE_PREFIX, parent_style_name=current_name)
        self.attrs["table:style-name"] = tag["style:name"]
        self._own_style_name = tag["style:name"]
        return tag

    @property
    def is_merge_master(self):
        """True if this cell is the top-left cell of a merged range (it
        carries `table:number-rows-spanned`/`table:number-columns-spanned`
        greater than 1)."""
        return (
            self.attrs.get("table:number-columns-spanned", "1") != "1"
            or self.attrs.get("table:number-rows-spanned", "1") != "1"
        )

    @property
    def is_covered(self):
        """True if this cell is hidden inside another cell's merged range
        (a `table:covered-table-cell`) - its own value/formatting is still
        there in the XML, just not shown, until `Sheet.unmerge(...)`
        reveals it again."""
        return self.cell.name == "covered-table-cell"

    @property
    def is_merged(self):
        """True if this cell participates in a merged range at all -
        either as the master or as one of the covered cells."""
        return self.is_merge_master or self.is_covered

    @property
    def merge_master(self):
        """The top-left `Cell` of this cell's merged range - `self` if this
        cell already is the master, or `None` if it isn't part of any merge
        (or has no owning sheet to look the master up on)."""
        if self.is_merge_master:
            return self
        if not self.is_covered or self.sheet is None:
            return None
        return self.sheet._find_merge_master(self.row, self.col)

    @property
    def merge_span(self):
        """`(n_rows, n_cols)` spanned by this cell's merged range (from its
        master's point of view), or `None` if this cell isn't merged."""
        master = self.merge_master
        if master is None:
            return None
        return (
            int(master.attrs.get("table:number-rows-spanned", "1")),
            int(master.attrs.get("table:number-columns-spanned", "1")),
        )

    @property
    def merge_range(self):
        """This cell's merged range as an `"A1:B2"`-style address string
        (resolvable from any cell in the range, not just the master), or
        `None` if it isn't merged."""
        master = self.merge_master
        if master is None:
            return None
        n_rows, n_cols = self.merge_span
        start = string_address(master.row, master.col)
        if n_rows == 1 and n_cols == 1:
            return start
        end = string_address(master.row + n_rows - 1, master.col + n_cols - 1)
        return f"{start}:{end}"

    @property
    def comment(self):
        """This cell's `Comment` (note/`office:annotation`) - `None` if it
        has none. Writable: `cell.comment = "some text"` creates one (or
        updates an existing one's `.text`); `cell.comment = None` removes
        it entirely. Once a comment exists, set its other properties
        directly - `cell.comment.author = "Jane"`, `.date = datetime.now()`,
        `.visible = True`."""
        tag = self.cell.find("office:annotation", recursive=False)
        return Comment(tag) if tag is not None else None

    @comment.setter
    def comment(self, value):
        self._prepare_for_write()
        tag = self.cell.find("office:annotation", recursive=False)
        if value is None:
            if tag is not None:
                tag.decompose()
            return
        if not isinstance(value, str):
            raise TypeError(f"cell.comment must be a str or None, got {type(value)!r}")
        if tag is None:
            tag = _blank_template(self.cell, "office:annotation")
            self.cell.insert(0, tag)
        Comment(tag).text = value

    @property
    def hyperlink(self):
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
        p = self.cell.find("text:p", recursive=False)
        if p is None:
            return None
        a = p.find("text:a", recursive=False)
        return a.get("xlink:href") if a is not None else None

    @hyperlink.setter
    def hyperlink(self, url):
        self._prepare_for_write()
        p = self.cell.find("text:p", recursive=False)
        if url is None:
            if p is not None:
                a = p.find("text:a", recursive=False)
                if a is not None:
                    a.unwrap()
            return
        if not isinstance(url, str):
            raise TypeError(f"cell.hyperlink must be a str or None, got {type(url)!r}")
        if p is None:
            self._set_text(self.text or "")
            p = self.cell.find("text:p", recursive=False)
        a = p.find("text:a", recursive=False)
        if a is None:
            a = _blank_template(self.cell, "text:a")
            a.attrs["xmlns:xlink"] = _ODF_NAMESPACES["xlink"]
            text = p.get_text()
            for child in list(p.children):
                child.extract()
            a.string = text
            p.append(a)
        a.attrs["xlink:href"] = url
