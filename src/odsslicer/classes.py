# -*- coding: utf-8 -*-
"""
Created 2021

@author: elessar

TODO
récupérer la couleur, les formats ?
writer ?
"""
from bs4 import BeautifulSoup
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED
from pathlib import Path
from typing import Union, Dict, Tuple
import ast
import copy
import datetime as dt
import numpy as np
import re
import math


RE_STRING_CELL = re.compile(r"([A-Z]+)?([0-9]+)?(:)?([A-Z]+)?([0-9]+)?")
TAG_CELL = ["table:table-cell", "table:covered-table-cell"]
EMPTY_CELL_BS = BeautifulSoup("<table:table-cell/>", "xml")

# https://wiki.documentfoundation.org/Faq/Calc/022
MAX_ROWS_PER_SHEET = 2**20  # way too big for python
MAX_COLS_PER_SHEET = 2**10  # AMJ (can be handled here)
MAX_CELLS_PER_SHEET = 2**30
MAX_CHARS_PER_CELL = 2**15  # ? needs updating
MAX_SHEETS = 10_000

MAX_REPEAT_ROWS = 1_000  # Above this : if row contains one ampty cell : discarded
MAX_REPEAT_COLS = 10  # idem but for every row (much more complicated to detect)

FORMATS = {
    "string": str,
    "float": float,
    "percentage": float,
    "currency": float,
    "date": lambda s: dt.datetime.strptime(s, "%Y-%m-%d").date(),
    "time": lambda s: dt.datetime.strptime(s, "PT%HH%MM%SS").time(),
    # ODF stores booleans as the strings "true"/"false" (office:boolean-value);
    # plain `bool(s)` would treat "false" as truthy since it's a non-empty string.
    "boolean": lambda s: None if s is None else s == "true",
    None: lambda x: None,
}


def _blank_template(root, tag_name):
    """A detached, blank copy of an existing `tag_name` tag reachable from `root`.

    Building a new tag from scratch (e.g. `BeautifulSoup("<table:table-row/>")`)
    loses the "table:"/"text:" namespace prefix, since there is no `xmlns:table`
    declaration in that isolated fragment for lxml/bs4 to resolve it against.
    Copying an existing tag from the live document sidesteps the issue.
    """
    template = (
        root.find(tag_name)
        or getattr(root, "find_previous", lambda *a: None)(tag_name)
        or getattr(root, "find_next", lambda *a: None)(tag_name)
    )
    if template is None:
        raise NotImplementedError(
            f"cannot create a new {tag_name} element: no existing tag of that name "
            "was found anywhere in this document to use as a namespace template"
        )
    new_tag = copy.deepcopy(template)
    new_tag.attrs.clear()
    for child in list(new_tag.children):
        child.extract()
    return new_tag


_FORMULA_LANGUAGE_PREFIX = re.compile(r"^[A-Za-z][\w.-]*:=")

# A1-style cell reference, optionally with $ absolute markers on either part
# (A2, $A2, A$2, $A$2). Column letters capped at 3 (XFD-style) is generous
# enough and helps the "not a function name" heuristic below.
_CELL_REF = r"\$?[A-Za-z]{1,3}\$?[0-9]+"
# an optional leading sheet qualifier: SheetName. or 'Sheet Name'. (the quoted
# form allows spaces/special characters, doubled '' for a literal quote)
_SHEET_NAME = r"(?:'(?:[^']|'')*'|[A-Za-z_]\w*)"
_FRIENDLY_REF_RE = re.compile(
    rf"(?<![A-Za-z0-9_$'.])"
    rf"(?:(?P<sheet>{_SHEET_NAME})\.)?"
    rf"(?P<start>{_CELL_REF})"
    rf"(?::(?P<end>{_CELL_REF}))?"
)
# split on quoted string literals ("..." with "" as an escaped quote), so
# translation never touches text that happens to look like a cell reference
_STRING_LITERAL_RE = re.compile(r'("(?:[^"]|"")*")')


def _translate_friendly_formula(body):
    """Translate Excel/Calc-style formula text into ODF's own syntax: bare
    references (`A2`, `$A$2`) and ranges (`A1:B3`) become `[.A2]`/`[.$A$2]`/
    `[.A1:.B3]`, sheet-qualified references (`Sheet2.A1`, `'My Sheet'.A1:B3`)
    become `[Sheet2.A1]`/`['My Sheet'.A1:.B3]`, and `,` argument separators
    become `;` — outside of quoted string literals, which are left untouched.

    A token immediately followed by `(` is assumed to be a function call
    (e.g. `LOG10(`), not a cell reference, and is left alone. Checked as a
    plain lookup on the character *after* the match rather than baked into
    the regex as a lookahead: a lookahead there would only make the engine
    backtrack into a shorter (but still `(`-free) match instead of rejecting
    the token outright - e.g. "LOG10(" would wrongly become "[.LOG1]0(".
    """

    def translate_refs(segment):
        out = []
        pos = 0
        for m in _FRIENDLY_REF_RE.finditer(segment):
            if m.start() < pos:
                continue  # overlaps a token already emitted, e.g. within a range
            if m.end() < len(segment) and segment[m.end()] == "(":
                continue  # function call, e.g. LOG10( - leave it untouched
            out.append(segment[pos : m.start()])
            sheet, start, end = m.group("sheet"), m.group("start"), m.group("end")
            prefix = f"{sheet}." if sheet else "."
            out.append(f"[{prefix}{start}:.{end}]" if end else f"[{prefix}{start}]")
            pos = m.end()
        out.append(segment[pos:])
        return "".join(out)

    parts = _STRING_LITERAL_RE.split(body)
    for i in range(0, len(parts), 2):  # even indices: outside string literals
        parts[i] = translate_refs(parts[i]).replace(",", ";")
    return "".join(parts)


_TEMPLATE_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_TEMPLATE_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.FloorDiv,
    ast.USub,
    ast.UAdd,
    ast.Name,
    ast.Load,
)


def _eval_template_expr(expr, context):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _TEMPLATE_ALLOWED_NODES):
            raise ValueError(f"unsupported expression in formula template: {expr!r}")
        if isinstance(node, ast.Name) and node.id not in context:
            raise ValueError(
                f"unknown name {node.id!r} in formula template {expr!r} "
                f"(only {', '.join(context)} are available)"
            )
    code = compile(tree, "<formula-template>", "eval")
    return eval(code, {"__builtins__": {}}, context)  # noqa: S307 - restricted to arithmetic on r/c


_ESCAPED_BRACES_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# NUL-delimited stash markers: guaranteed not to look like a cell reference or
# contain a comma, so they pass unscathed through the friendly-formula
# translation that runs after templating.
_ESCAPE_PLACEHOLDER = "\x00{}\x00"


def _expand_formula_template(pattern, row, col):
    """Expand `{...}` placeholders in a formula pattern using the target
    cell's own 1-indexed row (`r`) and column (`c`) — e.g. writing the pattern
    `"$A{r-1}+1"` across A2:A10 makes A2 reference A1, A3 reference A2, etc.
    A pattern with no `{...}` is returned unchanged. Only `+`, `-`, `*`, `//`
    and the names `r`/`c` are allowed inside a placeholder.

    `{{...}}` (doubled braces, as in `str.format`) is an escape hatch for a
    literal `{`/`}` — e.g. an ODF/Excel array-constant literal such as
    `{1,2,3}` would otherwise be read as a (invalid) placeholder expression.
    Returns `(expanded_pattern, restore)`: `restore` must be called on the
    final formula string (after `_normalize_odf_formula`) to put the escaped
    content back, verbatim and untranslated - the caller must apply it last,
    since the escaped content should skip the `,` -> `;` translation too.
    """
    escaped = []

    def stash(m):
        escaped.append(m.group(1))
        return _ESCAPE_PLACEHOLDER.format(len(escaped) - 1)

    pattern = _ESCAPED_BRACES_RE.sub(stash, pattern)

    if "{" in pattern:
        context = {"r": row + 1, "c": col + 1}
        pattern = _TEMPLATE_TOKEN_RE.sub(
            lambda m: str(_eval_template_expr(m.group(1), context)), pattern
        )

    def restore(formula):
        for i, content in enumerate(escaped):
            formula = formula.replace(_ESCAPE_PLACEHOLDER.format(i), "{" + content + "}")
        return formula

    return pattern, restore


def _normalize_odf_formula(formula):
    """Turn a user-supplied formula string into ODF's `table:formula` syntax.

    ODF formulas are stored as `<language-prefix>:=<expression>`, e.g.
    `of:=[.A1]+[.A2]` — note the bracketed `[.A1]` cell references and `;`
    argument separators, which are NOT the same as Excel-style `A1`/`,`.

    Accepts either syntax: a formula written with plain `A1`-style references
    (optionally `$`-anchored) and `,` separators, e.g. `"A2+A3"` or
    `"SUM(A1,A2)"`, is translated into ODF's own reference/separator syntax.
    A formula that already contains a `[` is assumed to already be in ODF
    syntax and is left untouched (besides the language prefix below) - this
    is the escape hatch for anything the translation doesn't cover (other
    sheets, named ranges...). Either way, a leading `=` is optional, and the
    default `of:=` language prefix is added unless one is already present.
    """
    if not formula:
        raise ValueError("a formula string is required (pass None to clear the formula)")
    if _FORMULA_LANGUAGE_PREFIX.match(formula):
        return formula
    body = formula[1:] if formula.startswith("=") else formula
    if "[" not in body:
        body = _translate_friendly_formula(body)
    return f"of:={body}"


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
        p = getattr(self.cell, "text:p", None)
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
            text = self._infer_number_display(fmt, new_value) or str(new_value)
        elif isinstance(new_value, dt.date) and not isinstance(new_value, dt.datetime):
            fmt = "date"
            tag.attrs["office:date-value"] = new_value.isoformat()
            text = self._infer_date_display(new_value) or new_value.isoformat()
        elif isinstance(new_value, dt.time):
            fmt = "time"
            tag.attrs["office:time-value"] = new_value.strftime("PT%HH%MM%SS")
            text = self._infer_time_display(new_value) or new_value.isoformat()
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
            p = match.find("text:p")
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

    def _set_text(self, text):
        p = self.cell.find("text:p")
        if text is None:
            if p is not None:
                p.decompose()
            self.text = None
            return
        if p is None:
            # Building a bare `<text:p>` fragment loses its namespace (bs4/lxml can only
            # resolve the "text:" prefix within a document that actually declares it), so
            # an existing text:p elsewhere in the same document is copied as a template.
            template = self.cell.find_previous("text:p") or self.cell.find_next("text:p")
            if template is None:
                raise NotImplementedError(
                    "cannot create a new text:p element: no existing text:p tag was "
                    "found anywhere in this document to use as a namespace template"
                )
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
        return Sheet.string_address(self.row, self.col)


class Sheet:
    def __init__(self, table: BeautifulSoup, verbose: bool = False):
        self.verbose = verbose
        self.table: BeautifulSoup = table
        self.attrs: Dict[str, str] = self.table.attrs
        self.name = self.table["table:name"]
        self.stylename = self.table.attrs.get("table:style-name")
        self.rows = self.load(table)
        if len(self.rows) > 0:
            rows_len = [len(row) for row in self.rows]
            self.size = (len(self.rows), max(rows_len))
            n_cols = self.size[1]
            if sum(n_cols - len_row for len_row in rows_len) > 0:
                print(
                    f"[WARNING] At least one row does not have the same length as the others? {rows_len}"
                )
        else:
            self.size = (0, 0)
        self.n_rows, self.n_cols = self.size
        if self.verbose:
            print(f"    {repr(self)}")

    def load(self, table_bs):
        table = []
        rows = table_bs.find_all("table:table-row")
        if self.verbose:
            print(f"    Loading {self.name}, {len(rows)} unrepeated rows")
        i = 0
        for row in rows:
            n_rows = int(row.attrs.get("table:number-rows-repeated", "1"))
            # ATTENTION : if some style is applied to a whole column : you get the max length 2**20
            all_cells_bs = row.find_all(TAG_CELL)
            if (
                n_rows > MAX_REPEAT_ROWS
                and len(all_cells_bs) == 1
                and Cell(all_cells_bs[0]).is_empty
            ):
                if self.verbose:
                    print(
                        f"    Row [{i+1:04d}] repeated {n_rows} > MAX = {MAX_REPEAT_ROWS} and with one empty cell:\
 row discarded"
                    )
                continue
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
            if self.verbose:
                print("    Last row empty: removed")
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
            if self.verbose:
                print(f"    Cols {col_start} to {col_end} empty: removed")
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

    def materialize_cell(self, row, col):
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

    def _unrepeat_row(self, row):
        """Split the `table:number-rows-repeated` row tag covering `row` into
        one independent `<table:table-row>` per repetition."""
        row_tag = self.rows[row][0].cell.parent
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
                        self.rows[r][j].__init__(cell_tag, row=r, col=j, sheet=self)
                    j += 1

    def _unrepeat_col(self, row, col):
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
        for c in copies:
            c.attrs.pop("table:number-columns-repeated", None)
        cell_tag.replace_with(copies[0])
        prev = copies[0]
        for nxt in copies[1:]:
            prev.insert_after(nxt)
            prev = nxt

        for k, copy_tag in enumerate(copies):
            c = start + k
            self.rows[row][c].__init__(copy_tag, row=row, col=c, sheet=self)

    @staticmethod
    def _is_merge_master(cell):
        return (
            cell.attrs.get("table:number-columns-spanned", "1") != "1"
            or cell.attrs.get("table:number-rows-spanned", "1") != "1"
        )

    def _find_merge_master(self, row, col):
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

    def _unmerge(self, row, col):
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
        master.__init__(master.cell, row=mr, col=mc, sheet=self)

        for r in range(mr, mr + rows_span):
            for c in range(mc, mc + cols_span):
                if r == mr and c == mc:
                    continue
                covered = self.rows[r][c]
                if covered.cell.name == "covered-table-cell":
                    covered.cell.name = "table-cell"
                covered.__init__(covered.cell, row=r, col=c, sheet=self)

    def _empty_cell_template(self):
        """A detached, blank `<table:table-cell/>` tag."""
        return _blank_template(self.table, "table:table-cell")

    def _empty_row_template(self, n_cols):
        """A detached `<table:table-row>` tag with `n_cols` blank cells."""
        row = _blank_template(self.table, "table:table-row")
        for _ in range(n_cols):
            row.append(self._empty_cell_template())
        return row

    def grow_to(self, row, col):
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
            self._discard_stray_rows()

        while row >= self.n_rows:
            new_row_tag = self._empty_row_template(self.n_cols)
            self.table.append(new_row_tag)
            new_cells = [
                Cell(cell_tag, row=self.n_rows, col=c, sheet=self)
                for c, cell_tag in enumerate(new_row_tag.find_all(TAG_CELL))
            ]
            self.rows.append(new_cells)
            self.n_rows += 1

        self.size = (self.n_rows, self.n_cols)

    def _discard_stray_rows(self):
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

    def __repr__(self):
        return f"Sheet(name='{self.name}', size[rows, cols]={self.size})"

    def empty_row(self, i=None, n_cols=None, start=0, slice=None):
        step = 1
        if slice is not None:
            start, stop, step = self._unslice(slice)
        elif n_cols is not None:
            stop = n_cols
        else:
            stop = self.n_cols
        return [Cell(EMPTY_CELL_BS, i, j, sheet=self) for j in range(start, stop, step)]

    def empty_col(self, j=None, n_rows=None, start=0, slice=None):
        step = 1
        if slice is not None:
            start, stop, step = self._unslice(slice)
        elif n_rows is not None:
            stop = n_rows
        else:
            stop = self.n_rows
        return [[Cell(EMPTY_CELL_BS, i, j, sheet=self)] for i in range(start, stop, step)]

    def get_row(self, i):
        if i >= self.n_rows:
            return self.empty_row(i)
        return self.rows[i]

    def get_rows(self, slice):
        return [self.get_row(i) for i in range(*self._unslice(slice, row=True))]

    def get_cell(self, i, j):
        row = self.get_row(i)
        if j >= self.n_cols:
            return Cell(EMPTY_CELL_BS, i, j, sheet=self)
        else:
            return row[j]

    def _unslice(self, slice, row=False, col=False):
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

    def get_cells(self, row_slice, col_slice):
        return [
            [self.get_cell(i, j) for j in range(*self._unslice(col_slice, col=True))]
            for i in range(*self._unslice(row_slice, row=True))
        ]

    def get_row_slice(self, i, col_slice):
        return [self.get_cell(i, j) for j in range(*self._unslice(col_slice, col=True))]

    def get_col(self, j):
        if j >= self.n_cols:
            return self.empty_col(j)
        return [[row[j]] for row in self.rows]

    def get_cols(self, slice):
        return [
            [row[j] for j in range(*self._unslice(slice, col=True))]
            for row in self.rows
        ]

    def __getitem__(self, address):
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

    def __iter__(self):
        return iter(self.rows)

    @staticmethod
    def string_to_col(s):
        long = len(s)
        c = [26 ** (long - i - 1) * (ord(char) - 64) for i, char in enumerate(s)]
        return sum(c) - 1

    @classmethod
    def address(cls, string, n_rows=1) -> Union[int, Tuple, slice]:
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
                return r1
            elif r1 is None:
                # A
                return slice(n_rows), c1
            else:
                # A1
                return r1, c1
        if (c1 is None and r2 is None) or (c2 is None and r1 is None):
            raise ValueError
        # dp is not None
        if (c1 is not None and c2 <= c1) or (r1 is not None and r2 <= r1):
            raise ValueError
        if r1 is None:
            # A:B
            return slice(n_rows), slice(c1, c2)
        elif c1 is None:
            # 1:2
            return slice(r1, r2)
        elif r2 == r1 + 1 and c2 > c1 + 1:
            # A1:B1
            return r1, slice(c1, c2)
        elif c2 == c1 + 1 and r2 > r1 + 1:
            # A1:A2
            return slice(r1, r2), c1
        elif r2 == r1 + 1 and c2 == c1 + 1:
            # A1:A1 == A1
            return r1, c1
        return slice(r1, r2), slice(c1, c2)

    @classmethod
    def string_address(cls, row, col):
        # 0->A, 25->Z, 26->AA, 51->AZ, 52->BA
        # bijective base-26 (no digit 0, hence the "n - 1" at each step)
        n = col + 1
        c = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            c = chr(65 + r) + c
        return f"{c}{row+1}"

    def to_list(self):
        return self[:].to_list()

    def to_numpy(self):
        return self[:].to_numpy()


class ODSReader:
    def __init__(self, file: Union[Path, str], verbose: bool = False):
        self.file = file
        self.verbose = verbose
        if self.verbose:
            print(f"Opening {self.file}...")
        # http://docs.oasis-open.org/office/v1.2/
        with ZipFile(file) as zip:
            # Document content and automatic styles used in the content.
            self.content = zip.read("content.xml")
            # Styles used in the document content and automatic styles used in the styles themselves.
            self.styles = zip.read("styles.xml")
            # Document meta information, such as the author or the time of the last save action.
            self.meta = zip.read("meta.xml")
            # Application-specific settings, such as the window size or printer information.
            self.settings = zip.read("settings.xml")
        self.data = BeautifulSoup(self.content, "xml")
        self.tables = self.data.find_all("table:table")
        self.sheets_names = [table["table:name"] for table in self.tables]
        self._sheets: dict[str, Sheet | None] = {name: None for name in self.sheets_names}
        if self.verbose:
            print(f"    {repr(self)}")

    def __repr__(self):
        return f"ODSReader({self.file}, sheets={self.sheets_names})"

    def save(self, path: Union[Path, str, None] = None):
        """Write the in-memory content back out as a .ods file.

        Only cell values changed via `cell.value = ...` are reflected; styles,
        metadata, settings and all other zip members are copied through
        unchanged from the source file. Defaults to overwriting `self.file`.
        """
        if path is None:
            path = self.file
        new_content = self.data.encode("utf-8")
        with ZipFile(self.file) as src:
            entries = [
                (item, new_content if item.filename == "content.xml" else src.read(item.filename))
                for item in src.infolist()
            ]
        with ZipFile(path, "w") as dst:
            for item, data in entries:
                # the ODF spec requires `mimetype` to be the first entry and stored uncompressed
                item.compress_type = ZIP_STORED if item.filename == "mimetype" else ZIP_DEFLATED
                dst.writestr(item, data)

    def export_content_xml(self, pretty=True):
        if pretty:
            with open(f"{self.file.with_suffix('.xml')}", "w", encoding="utf8") as f:
                f.write(self.data.prettify())
        else:
            with open(f"{self.file.with_suffix('.xml')}", "wb") as f:
                f.write(self.content)

    @property
    def sheets(self):
        return [self.sheet(name) for name in self.sheets_names]

    def sheet(self, name) -> Sheet:
        if name not in self.sheets_names:
            raise IndexError(f"No sheet named {name}")
        if self._sheets[name] is None:
            self._sheets[name] = Sheet(
                self.tables[self.sheets_names.index(name)], verbose=self.verbose
            )
        return self._sheets[name]

    def add_sheet(self, name: str) -> Sheet:
        """Create a new, empty sheet named `name` and append it after the last
        existing sheet (`table:table` elements must stay grouped together and
        come before things like `table:named-expressions` in the document).

        The new sheet's XML mirrors the minimal shape of a genuinely empty ODF
        sheet (one column definition, one row with one empty cell) and carries
        no particular style, same as newly grown rows/cells.
        """
        if not name:
            raise ValueError("a sheet name is required")
        if name in self.sheets_names:
            raise ValueError(f"a sheet named {name!r} already exists")

        table = _blank_template(self.data, "table:table")
        table.attrs["table:name"] = name
        table.append(_blank_template(self.data, "table:table-column"))
        row = _blank_template(self.data, "table:table-row")
        row.append(_blank_template(self.data, "table:table-cell"))
        table.append(row)

        self.tables[-1].insert_after(table)
        self.tables.append(table)
        self.sheets_names.append(name)
        self._sheets[name] = Sheet(table, verbose=self.verbose)
        return self._sheets[name]
