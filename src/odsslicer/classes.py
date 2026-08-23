# -*- coding: utf-8 -*-
"""
Created 2021

@author: elessar
"""
from bs4 import BeautifulSoup
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED
from pathlib import Path
from typing import Union, Dict, Tuple
import ast
import copy
import datetime as dt
import functools
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


# Standard OASIS namespace URIs, used as a last-resort fallback to build a
# namespace-qualified tag from scratch when the document has no existing tag
# of that name to copy (e.g. a brand new sheet with no cells at all yet).
_ODF_NAMESPACES = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _new_qualified_tag(tag_name):
    """Build a detached `<tag_name/>` from scratch, with its own `xmlns:`
    declaration so the "table:"/"text:" prefix resolves correctly - safe to
    insert anywhere in an ODF document even though the declaration is then
    redundant with the one at the document root (harmless, plain valid XML).
    """
    prefix = tag_name.split(":", 1)[0]
    uri = _ODF_NAMESPACES[prefix]
    fragment = BeautifulSoup(f'<{tag_name} xmlns:{prefix}="{uri}"/>', "xml")
    return fragment.find(tag_name)


def _blank_template(root, tag_name):
    """A detached, blank copy of an existing `tag_name` tag reachable from
    `root`, or a freshly built one (see `_new_qualified_tag`) if the document
    has none to copy from.

    Building a new tag from scratch (e.g. `BeautifulSoup("<table:table-row/>")`)
    without its own `xmlns:` declaration loses the "table:"/"text:" namespace
    prefix, since there is nothing in that isolated fragment for lxml/bs4 to
    resolve it against - copying an existing tag from the live document (when
    there is one) sidesteps having to hardcode the namespace URI at all.
    """
    template = (
        root.find(tag_name)
        or getattr(root, "find_previous", lambda *a: None)(tag_name)
        or getattr(root, "find_next", lambda *a: None)(tag_name)
    )
    if template is None:
        return _new_qualified_tag(tag_name)
    new_tag = copy.deepcopy(template)
    new_tag.attrs.clear()
    for child in list(new_tag.children):
        child.extract()
    return new_tag


def _ensure_style_child(style_tag, tag_name):
    """The `tag_name` properties child of `style_tag` (e.g.
    `<style:table-cell-properties>` under a `<style:style>`), creating a
    blank one (see `_blank_template`) if it isn't there yet."""
    child = style_tag.find(tag_name)
    if child is None:
        child = _blank_template(style_tag, tag_name)
        style_tag.append(child)
    return child


def _is_forked_style_name(name, prefix):
    """True if `name` looks like one odsslicer itself generated for a
    single owner (a specific cell/row/column/sheet) via `prefix` - safe to
    mutate in place rather than fork again. Real documents don't use these
    reserved prefixes in practice."""
    return bool(name) and re.match(rf"^{re.escape(prefix)}\d+$", name) is not None


def _border_to_raw(value):
    """Normalize a border value accepted on write - a `Border`, a raw ODF
    shorthand string (`"0.5pt solid #000000"`), or `None` - down to the raw
    string form (or `None`)."""
    if value is None:
        return None
    if isinstance(value, Border):
        return value.raw
    if isinstance(value, str):
        return value
    raise TypeError(f"expected a Border, str, or None, got {type(value)!r}")


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


_ODF_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")


def _odf_ref_to_friendly(inner):
    """`.A1` -> `A1`, `.$A$1` -> `$A$1`, `Sheet2.A1` -> `Sheet2.A1` (already
    friendly) - the reverse of the single-reference half of
    `_translate_friendly_formula`."""
    return inner[1:] if inner.startswith(".") else inner


def _translate_odf_formula_to_friendly(body):
    """Reverse of `_translate_friendly_formula`: turn ODF bracket references
    (`[.A1]`, `[.A1:.B3]`, `[Sheet2.A1]`) back into ordinary `A1`-style
    syntax, and `;` argument separators back into `,` (outside quoted string
    literals). Best-effort - a bracket whose content isn't a plain reference
    or range (a named range, an unusual construct) is passed through as-is.
    """

    def translate_refs(segment):
        def replace(m):
            inner = m.group(1)
            if ":" in inner:
                start, end = inner.split(":", 1)
                return f"{_odf_ref_to_friendly(start)}:{_odf_ref_to_friendly(end)}"
            return _odf_ref_to_friendly(inner)

        return _ODF_BRACKET_RE.sub(replace, segment)

    parts = _STRING_LITERAL_RE.split(body)
    for i in range(0, len(parts), 2):
        parts[i] = translate_refs(parts[i]).replace(";", ",")
    return "".join(parts)


_ODF_CELL_ADDRESS_RE = re.compile(r"^(\$?)([A-Za-z]+)(\$?)([0-9]+)$")
_SHEET_QUALIFIED_RE = re.compile(rf"^(?:(?P<sheet>{_SHEET_NAME})\.)?(?P<addr>.+)$")


def _shift_cell_address(addr, drow, dcol):
    """Shift a single ODF cell address (e.g. `.A1`, `.$A$1`, or `A1`/`$A$1`
    without the leading dot) by `(drow, dcol)`, honoring `$` locks on each
    axis independently - the way a spreadsheet's fill handle adjusts
    relative references when a formula is copied. Anything that isn't a
    plain cell address (e.g. a named range) is returned untouched. Raises
    `ValueError` if the shifted position would fall off the sheet.
    """
    dotted = addr.startswith(".")
    body = addr[1:] if dotted else addr
    m = _ODF_CELL_ADDRESS_RE.match(body)
    if m is None:
        return addr
    col_abs, col_letters, row_abs, row_digits = m.groups()
    col = Sheet.string_to_col(col_letters)
    row = int(row_digits) - 1
    if not col_abs:
        col += dcol
    if not row_abs:
        row += drow
    if row < 0 or col < 0:
        raise ValueError(
            f"shifting {addr!r} by (row={drow}, col={dcol}) would move it off the sheet"
        )
    new_letters = Sheet.string_address(0, col)[:-1]
    shifted = f"{col_abs}{new_letters}{row_abs}{row + 1}"
    return f".{shifted}" if dotted else shifted


def _shift_odf_reference(inner, drow, dcol):
    """Shift the address part(s) of one bracket's content (`.A1`, `.A1:.B3`,
    `Sheet2.A1`, `Sheet2.A1:.B3`), leaving any sheet-name prefix untouched."""

    def shift_one(part):
        m = _SHEET_QUALIFIED_RE.match(part)
        sheet, addr = m.group("sheet"), m.group("addr")
        shifted_addr = _shift_cell_address(addr, drow, dcol)
        return f"{sheet}.{shifted_addr}" if sheet else shifted_addr

    if ":" in inner:
        start, end = inner.split(":", 1)
        return f"{shift_one(start)}:{shift_one(end)}"
    return shift_one(inner)


def _shift_odf_formula(formula, drow, dcol):
    """Shift every reference in an already ODF-syntax formula (as stored by
    `Cell.formula`) by `(drow, dcol)`. Used by `Cell.fill_formula` to
    replicate a formula across a range the way a spreadsheet's fill handle
    does."""
    return _ODF_BRACKET_RE.sub(
        lambda m: f"[{_shift_odf_reference(m.group(1), drow, dcol)}]", formula
    )


def _unquote_odf_sheet_name(raw):
    """`'My Sheet'` -> `My Sheet` (undoing the doubled-`''` escape), or
    `Sheet2` unchanged - the reverse of the quoting `_SHEET_NAME` matches."""
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw


def _delete_shift_cell_address(addr, deleted_row=None, deleted_col=None):
    """Adjust a single ODF cell address (`.A1`, `.$A$1`, or bare `A1`) for
    row `deleted_row` (or column `deleted_col`) having been physically
    removed from the sheet: shifts the index down by one if it was
    strictly past the deleted position, *regardless* of any `$` lock -
    unlike `_shift_cell_address`'s fill/copy semantics, `$` is irrelevant
    here, since the referenced cell itself moved rather than the formula.
    A reference that pointed exactly at the removed row/column is left
    unchanged (there's no `#REF!`-style error value to represent "this
    reference is now broken" - see the README's known limitations)."""
    dotted = addr.startswith(".")
    body = addr[1:] if dotted else addr
    m = _ODF_CELL_ADDRESS_RE.match(body)
    if m is None:
        return addr
    col_abs, col_letters, row_abs, row_digits = m.groups()
    col = Sheet.string_to_col(col_letters)
    row = int(row_digits) - 1
    if deleted_row is not None and row > deleted_row:
        row -= 1
    if deleted_col is not None and col > deleted_col:
        col -= 1
    new_letters = Sheet.string_address(0, col)[:-1]
    shifted = f"{col_abs}{new_letters}{row_abs}{row + 1}"
    return f".{shifted}" if dotted else shifted


def _adjust_odf_reference_for_deletion(inner, target_sheet, containing_sheet, deleted_row, deleted_col):
    """Adjust the address part(s) of one bracket's content for a row/
    column deletion in `target_sheet` - only references that actually
    resolve to `target_sheet` (explicitly sheet-qualified, or bare and
    `containing_sheet is target_sheet`) are touched; anything pointing
    elsewhere is returned as-is."""

    def adjust_one(part):
        m = _SHEET_QUALIFIED_RE.match(part)
        ref_sheet, addr = m.group("sheet"), m.group("addr")
        effective_sheet = _unquote_odf_sheet_name(ref_sheet) if ref_sheet else containing_sheet
        if effective_sheet != target_sheet:
            return part
        shifted_addr = _delete_shift_cell_address(addr, deleted_row, deleted_col)
        return f"{ref_sheet}.{shifted_addr}" if ref_sheet else shifted_addr

    if ":" in inner:
        start, end = inner.split(":", 1)
        return f"{adjust_one(start)}:{adjust_one(end)}"
    return adjust_one(inner)


def _adjust_odf_formula_for_deletion(formula, target_sheet, containing_sheet, deleted_row=None, deleted_col=None):
    """Adjust every reference in an already ODF-syntax formula that
    resolves to `target_sheet`, for a row/column deletion there - used by
    `Sheet.delete_row`/`.delete_column` to keep formulas (in this sheet or
    any other) pointing at the same cells they did before."""
    return _ODF_BRACKET_RE.sub(
        lambda m: f"[{_adjust_odf_reference_for_deletion(m.group(1), target_sheet, containing_sheet, deleted_row, deleted_col)}]",
        formula,
    )


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


def _friendly_formula(formula):
    """The reverse of `_normalize_odf_formula`, for display: strip the
    `<language>:=` prefix and translate ODF references/separators back into
    ordinary `A1`-style syntax, e.g. `"of:=[.A2]+[.A3]"` -> `"=A2+A3"`."""
    if formula is None:
        return None
    m = _FORMULA_LANGUAGE_PREFIX.match(formula)
    body = formula[m.end() :] if m else formula
    return f"={_translate_odf_formula_to_friendly(body)}"


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
        return Sheet.string_address(self.row, self.col)

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
        start = Sheet.string_address(master.row, master.col)
        if n_rows == 1 and n_cols == 1:
            return start
        end = Sheet.string_address(master.row + n_rows - 1, master.col + n_cols - 1)
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


class Sheet:
    def __init__(self, table: BeautifulSoup, verbose: bool = False, reader: "ODSReader" = None):
        self.verbose = verbose
        self.reader = reader
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

    def _resolve_range(self, address):
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

    def merge(self, address):
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
                covered.__init__(covered.cell, row=r, col=c, sheet=self)
        master.__init__(master.cell, row=row0, col=col0, sheet=self)

    def unmerge(self, address):
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

    def copy(self, source, dest):
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

    def sort(self, source, by, ascending=True):
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

        def compare(a, b):
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

    def delete_row(self, row):
        """Remove logical row `row` entirely, shifting every row below it
        up by one (`sheet.size` shrinks accordingly).

        Any merge intersecting `row` (master, covered, or entirely on
        another row but spanning through it) is undone first (see
        `unmerge`) rather than left with a now-wrong span - there's no
        general way to "shrink" a span by one row instead. Formula
        references elsewhere in the document that point at this sheet
        (this sheet's own formulas, and any other sheet's formula
        explicitly qualified with this sheet's name) are shifted to keep
        pointing at the same cell - see `_adjust_formulas_for_deletion`
        for what that does and doesn't cover.
        """
        if row < 0 or row >= self.n_rows:
            raise IndexError(f"row {row} out of range (sheet has {self.n_rows} rows)")
        c = 0
        while c < self.n_cols:
            if self.rows[row][c].is_merged:
                self._unmerge(row, c)
            c += 1

        self._unrepeat_row(row)
        self.rows[row][0].cell.parent.decompose()
        del self.rows[row]
        for r in range(row, len(self.rows)):
            for cell in self.rows[r]:
                cell.row = r
        self.n_rows = len(self.rows)
        self.size = (self.n_rows, self.n_cols)
        self._adjust_formulas_for_deletion(deleted_row=row)

    def delete_column(self, col):
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
        self._adjust_formulas_for_deletion(deleted_col=col)

    def _adjust_formulas_for_deletion(self, deleted_row=None, deleted_col=None):
        """After physically removing row `deleted_row` (or column
        `deleted_col`) from this sheet, rewrite every formula in the whole
        document - this sheet's own, and any other sheet's formula that
        references into this sheet by name - so a reference past the
        removed position still points at the same cell it did before
        (exactly one of `deleted_row`/`deleted_col` is given, matching
        `delete_row`/`delete_column`).

        A reference that pointed *exactly* at the removed row/column is
        left unchanged rather than modeled as a `#REF!`-style error - see
        the README's known limitations. No-op if this sheet has no owning
        `ODSReader` (nothing else to scan)."""
        if self.reader is None:
            return
        for sheet in self.reader.sheets:
            for row in sheet.rows:
                for cell in row:
                    if cell.formula is None:
                        continue
                    adjusted = _adjust_odf_formula_for_deletion(
                        cell.formula, self.name, sheet.name, deleted_row, deleted_col
                    )
                    if adjusted != cell.formula:
                        cell.formula = adjusted

    def __repr__(self):
        return f"Sheet(name='{self.name}', size[rows, cols]={self.size})"

    @property
    def style(self):
        """This sheet's resolved, writable `TableStyle` (e.g. `.tab_color`
        - see `TableStyle`), or `None` if there's no owning `ODSReader`."""
        if self.reader is None:
            return None
        name = self.table.attrs.get("table:style-name")
        tag = self.reader._find_style(name, family="table") if name else None
        return TableStyle(tag, sheet=self)

    def row_style(self, row):
        """The resolved, writable `RowStyle` for logical row `row` (see
        `RowStyle`), or `None` if `row` is out of range or there's no
        owning `ODSReader`."""
        if self.reader is None or row >= self.n_rows:
            return None
        row_tag = self.rows[row][0].cell.parent
        name = row_tag.attrs.get("table:style-name")
        tag = self.reader._find_style(name, family="table-row") if name else None
        return RowStyle(tag, sheet=self, row=row)

    def _find_column_tag(self, col):
        """The `<table:table-column>` covering logical column `col`
        (accounting for `table:number-columns-repeated`), or `None`."""
        seen = 0
        for col_tag in self.table.find_all("table:table-column", recursive=False):
            n = int(col_tag.attrs.get("table:number-columns-repeated", "1"))
            if seen <= col < seen + n:
                return col_tag
            seen += n
        return None

    def _unrepeat_column_tag(self, col):
        """Split the `table:number-columns-repeated` column-definition tag
        covering `col` into one independent `<table:table-column>` per
        repetition (mirrors `_unrepeat_col`, but for column *definitions*
        rather than cell data). Returns the tag now covering `col` alone -
        already independent if it wasn't repeated to begin with."""
        col_tag = self._find_column_tag(col)
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

    def column_style(self, col):
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

    def _fork_style(self, current_name, family, prefix, props_tag_name):
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

    def _ensure_row_style(self, row):
        self._unrepeat_row(row)
        row_tag = self.rows[row][0].cell.parent
        tag = self._fork_style(
            row_tag.attrs.get("table:style-name"), "table-row", "ors", "style:table-row-properties"
        )
        row_tag.attrs["table:style-name"] = tag["style:name"]
        return tag

    def _ensure_column_style(self, col):
        col_tag = self._unrepeat_column_tag(col)
        tag = self._fork_style(
            col_tag.attrs.get("table:style-name"), "table-column", "ocos", "style:table-column-properties"
        )
        col_tag.attrs["table:style-name"] = tag["style:name"]
        return tag

    def _ensure_table_style(self):
        tag = self._fork_style(
            self.table.attrs.get("table:style-name"), "table", "ots", "style:table-properties"
        )
        self.table.attrs["table:style-name"] = tag["style:name"]
        return tag

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


_NUMBER_STYLE_TAGS = [
    "number:number-style",
    "number:percentage-style",
    "number:currency-style",
    "number:date-style",
    "number:time-style",
    "number:boolean-style",
    "number:text-style",
]
_DATE_TIME_COMPONENT_NAMES = (
    "day", "month", "year", "day-of-week", "week-of-year", "quarter", "era",
    "hours", "minutes", "seconds", "am-pm", "text",
)


_CONDITION_RE = re.compile(r"^value\(\)\s*(<=|>=|!=|<|>|=)\s*(-?\d+(?:\.\d+)?)$")


def _evaluate_number_format_condition(condition, value):
    """Evaluate an ODF `style:condition` (the subset used by `style:map` on
    number styles: a comparison of `value()` against a number, e.g.
    `"value()>=0"`). Returns False for a condition outside that subset (a
    cell-content-is-text() condition, a between-range, ...) rather than
    guessing, and False if `value` isn't itself numeric."""
    if not condition:
        return False
    m = _CONDITION_RE.match(condition.strip())
    if not m:
        return False
    op, operand = m.group(1), float(m.group(2))
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return {
        "<": value < operand,
        "<=": value <= operand,
        ">": value > operand,
        ">=": value >= operand,
        "=": value == operand,
        "!=": value != operand,
    }[op]


class NumberFormat:
    """The `<number:*-style>` behind a cell's `style:data-style-name` - the
    document's own real display format (decimal places, currency symbol,
    date/time layout), as opposed to `odsslicer`'s own learn-by-example
    heuristic used when *writing* a value (see `Cell._infer_*_display`).

    A number style can be conditional (`<style:map style:condition="..."
    style:apply-style-name="...">`, e.g. currency amounts shown in red when
    negative): `.conditions` holds `(condition, NumberFormat)` pairs in
    document order, and `.resolve(value)` follows them to the `NumberFormat`
    that actually applies to a given value (itself, if none match).

    Writable: `NumberFormat.create(reader, family, ...)` builds a brand new
    format from scratch (for when no existing one in the document already
    has the layout you want); `.add_condition(condition, target)` appends
    a conditional variant to an existing format."""

    _FAMILY_TAGS = {
        "number": "number:number-style",
        "percentage": "number:percentage-style",
        "currency": "number:currency-style",
        "date": "number:date-style",
        "time": "number:time-style",
        "boolean": "number:boolean-style",
    }

    def __init__(self, tag, reader: "ODSReader" = None, _seen=None):
        self._tag = tag
        self._reader = reader
        self.name = tag.get("style:name")
        self.family = tag.name[: -len("-style")] if tag.name.endswith("-style") else tag.name
        number_tag = tag.find("number:number") or tag.find("number:boolean")
        self.decimal_places = None
        self.grouping = None
        if number_tag is not None:
            places = number_tag.attrs.get("number:decimal-places")
            self.decimal_places = int(places) if places is not None else None
            self.grouping = number_tag.attrs.get("number:grouping") == "true"
        symbol = tag.find("number:currency-symbol")
        self.currency_symbol = symbol.get_text() if symbol is not None else None
        text_props = tag.find("style:text-properties")
        self.font_color = text_props.attrs.get("fo:color") if text_props is not None else None
        # for date/time styles: the ordered sequence of components/literal
        # text making up the layout, e.g. [("day", "long"), ("text", "/"), ...]
        self.components = None
        if self.family in ("date", "time"):
            self.components = [
                (
                    child.name,
                    child.get_text() if child.name == "text" else child.attrs.get("number:style", "short"),
                )
                for child in tag.find_all(True, recursive=False)
                if child.name in _DATE_TIME_COMPONENT_NAMES
            ]

        _seen = _seen or set()
        _seen.add(self.name)
        self.conditions = []
        for style_map in tag.find_all("style:map", recursive=False):
            condition = style_map.attrs.get("style:condition")
            apply_name = style_map.attrs.get("style:apply-style-name")
            target = None
            if reader is not None and apply_name and apply_name not in _seen:
                target_tag = reader._find_number_style(apply_name)
                if target_tag is not None:
                    target = NumberFormat(target_tag, reader=reader, _seen=_seen)
            self.conditions.append((condition, target))

    def resolve(self, value):
        """The `NumberFormat` that actually applies to `value`, following
        `.conditions` in order (first match wins) - `self` if none match, a
        condition's target couldn't be resolved, or there are no conditions."""
        for condition, target in self.conditions:
            if target is not None and _evaluate_number_format_condition(condition, value):
                return target
        return self

    @classmethod
    def create(
        cls,
        reader: "ODSReader",
        family: str,
        decimal_places: int = None,
        grouping: bool = False,
        min_integer_digits: int = 1,
        currency_symbol: str = None,
        components: list = None,
        font_color: str = None,
    ):
        """Build a brand new `<number:*-style>` from scratch and register
        it in the document's automatic styles, returning a `NumberFormat`
        bound to it.

        `family` is one of `"number"`/`"percentage"`/`"currency"`/
        `"date"`/`"time"`/`"boolean"`. `decimal_places`/`grouping`/
        `min_integer_digits` apply to `"number"`/`"percentage"`/
        `"currency"`; `currency_symbol` is required for `"currency"`.
        `"date"`/`"time"` take `components` - the same ordered
        `[(component, style_or_text), ...]` list `.components` itself
        already exposes on read, e.g. `[("day", "long"), ("text", "/"),
        ("month", "long"), ("text", "/"), ("year", "long")]` (a `"text"`
        entry is a literal separator - its second element is the literal
        text itself, rather than a component style). `"boolean"` takes
        none of the above. `font_color` works for any family - the
        classic use is the "positive/negative" pair behind conditional
        formatting (see `add_condition`): a plain black format for the
        base, and a `font_color="#FF0000"` variant applied when
        `"value()<0"` matches.
        """
        if family not in cls._FAMILY_TAGS:
            raise ValueError(
                f"unknown number format family {family!r} - expected one of {sorted(cls._FAMILY_TAGS)}"
            )
        if family == "currency" and not currency_symbol:
            raise ValueError("family='currency' requires currency_symbol")
        if family in ("date", "time") and not components:
            raise ValueError(f"family={family!r} requires components")

        tag = _blank_template(reader.data, cls._FAMILY_TAGS[family])
        tag.attrs["style:name"] = reader._new_style_name("N")
        if font_color is not None:
            text_props = _blank_template(reader.data, "style:text-properties")
            text_props.attrs["fo:color"] = font_color
            tag.append(text_props)

        if family in ("number", "percentage", "currency"):
            number_child = _blank_template(reader.data, "number:number")
            if decimal_places is not None:
                number_child.attrs["number:decimal-places"] = str(decimal_places)
                number_child.attrs["number:min-decimal-places"] = str(decimal_places)
            number_child.attrs["number:min-integer-digits"] = str(min_integer_digits)
            if grouping:
                number_child.attrs["number:grouping"] = "true"
            tag.append(number_child)
            if family == "percentage":
                text_child = _blank_template(reader.data, "number:text")
                text_child.string = " %"
                tag.append(text_child)
            elif family == "currency":
                separator = _blank_template(reader.data, "number:text")
                separator.string = " "
                tag.append(separator)
                symbol_child = _blank_template(reader.data, "number:currency-symbol")
                symbol_child.string = currency_symbol
                tag.append(symbol_child)
        elif family == "boolean":
            tag.append(_blank_template(reader.data, "number:boolean"))
        else:  # date / time
            for kind, value in components:
                if kind == "text":
                    child = _blank_template(reader.data, "number:text")
                    child.string = value
                else:
                    child = _blank_template(reader.data, f"number:{kind}")
                    if value:
                        child.attrs["number:style"] = value
                tag.append(child)

        reader._automatic_styles().append(tag)
        return cls(tag, reader=reader)

    def add_condition(self, condition, target):
        """Add a `<style:map>` entry (see the class docstring): `target` -
        an existing `NumberFormat` in the same document - applies instead
        of this one whenever `condition` (ODF's `style:condition` syntax,
        e.g. `"value()<0"`) matches. Appended after any conditions already
        there; `.resolve(value)` tries them in order, first match wins."""
        if self._tag is None or self._reader is None:
            raise RuntimeError(f"number format {self.name!r} has no underlying tag and cannot be written to")
        if not isinstance(target, NumberFormat):
            raise TypeError(f"target must be a NumberFormat, got {type(target)!r}")
        style_map = _blank_template(self._reader.data, "style:map")
        style_map.attrs["style:condition"] = condition
        style_map.attrs["style:apply-style-name"] = target.name
        self._tag.append(style_map)
        self.conditions.append((condition, target))

    def __repr__(self):
        return f"NumberFormat(name={self.name!r}, family={self.family!r})"


_DATE_TIME_RENDER_LONG = {
    "day": lambda v: f"{v.day:02d}",
    "month": lambda v: f"{v.month:02d}",
    "year": lambda v: f"{v.year:04d}",
    "hours": lambda v: f"{v.hour:02d}",
    "minutes": lambda v: f"{v.minute:02d}",
    "seconds": lambda v: f"{v.second:02d}",
}
_DATE_TIME_RENDER_SHORT = {
    "day": lambda v: str(v.day),
    "month": lambda v: str(v.month),
    "year": lambda v: str(v.year % 100),
    "hours": lambda v: str(v.hour),
    "minutes": lambda v: str(v.minute),
    "seconds": lambda v: str(v.second),
}


def _render_number_from_format(number_format, value):
    """Render `value` per `number_format`'s own real definition - decimal
    places, thousands grouping, currency symbol - a genuine read of the
    document's ODF format rather than a guess from another cell's
    example (see `Cell._infer_number_display`). `None` if `number_format`
    isn't a `"number"`/`"percentage"`/`"currency"` family.

    Renders with a plain `.`/`,` (decimal/grouping) convention - the
    document's actual locale isn't captured by `NumberFormat` - so this
    is only ever tried as a fallback once no example cell is available to
    learn the real locale-specific separators from."""
    if number_format is None or number_format.family not in ("number", "percentage", "currency"):
        return None
    scale = 100 if number_format.family == "percentage" else 1
    decimals = number_format.decimal_places or 0
    rendered = f"{value * scale:,.{decimals}f}" if number_format.grouping else f"{value * scale:.{decimals}f}"
    if number_format.family == "percentage":
        return f"{rendered} %"
    if number_format.family == "currency" and number_format.currency_symbol:
        return f"{rendered} {number_format.currency_symbol}"
    return rendered


def _render_date_time_from_format(number_format, value, family):
    """Render `value` (a `datetime.date` or `datetime.time`) by walking
    `number_format.components` - the same ordered layout `.components`
    itself exposes on read. `None` if `number_format` isn't a `family`
    format, has no `.components`, or uses a component this doesn't know
    how to render (`day-of-week`/`week-of-year`/`quarter`/`era`) - safer
    to fall back than to silently drop part of the layout."""
    if number_format is None or number_format.family != family or not number_format.components:
        return None
    parts = []
    for kind, style in number_format.components:
        if kind == "text":
            parts.append(style)
        elif kind == "am-pm":
            parts.append("PM" if value.hour >= 12 else "AM")
        elif kind in _DATE_TIME_RENDER_LONG:
            renderer = _DATE_TIME_RENDER_LONG if style == "long" else _DATE_TIME_RENDER_SHORT
            parts.append(renderer[kind](value))
        else:
            return None
    return "".join(parts)


def _make_border(raw):
    # ODF uses the literal string "none" (not just an absent attribute) to
    # explicitly cancel a border/diagonal - both mean "no border" here.
    return Border(raw) if raw and raw != "none" else None


class Border:
    """One side of a cell's border, parsed from ODF's `"<width> <style>
    <color>"` shorthand (e.g. `"0.74pt solid #808080"`)."""

    def __init__(self, raw: str):
        self.raw = raw
        parts = raw.split()
        self.width = parts[0] if len(parts) > 0 else None
        self.style = parts[1] if len(parts) > 1 else None
        self.color = parts[2] if len(parts) > 2 else None

    def __repr__(self):
        return f"Border({self.raw!r})"

    def __eq__(self, other):
        return isinstance(other, Border) and self.raw == other.raw


class CellStyle:
    """Resolved visual formatting and number format behind a cell's
    `table:style-name`. Walks the `style:parent-style-name` inheritance
    chain on read (the nearest style to the cell wins for whichever
    property it sets directly; a property no style in the chain sets is
    `None`). Look up a cell's style via `Cell.style`, not directly.

    Writable: setting a property (e.g. `cell.style.bold = True`) forks the
    cell its own private automatic style on first use (see
    `Cell._ensure_own_style`) - the cell's *own* value going forward, on
    top of whatever it already inherited. `border_top`/`border_bottom`/
    `border_left`/`border_right` accept a `Border`, a raw ODF shorthand
    string (`"0.5pt solid #000000"`), or `None` (explicitly no border on
    that side) - setting any one side re-writes all four explicitly on the
    forked style, carrying the other three over from what's currently
    resolved (see `CellStyle` border resolution below), so they don't
    silently fall back to unstyled. `diagonal_bl_tr`/`diagonal_tl_br` take
    the same three forms, but `None` simply removes the override (falls
    back to whatever's inherited) - pass the literal string `"none"` to
    force no diagonal regardless of inheritance.

    Not yet writable: `.conditions` (conditional formatting via
    `style:map`) and creating a brand new number format from scratch -
    `number_format` can only be *assigned* an existing `NumberFormat` (or
    its style name) already present in the document."""

    _BORDER_ATTRS = ("fo:border", "fo:border-top", "fo:border-bottom", "fo:border-left", "fo:border-right")
    _BORDER_SIDE_ATTRS = {
        "border_top": "fo:border-top",
        "border_bottom": "fo:border-bottom",
        "border_left": "fo:border-left",
        "border_right": "fo:border-right",
    }

    def __init__(self, reader: "ODSReader", name: str, value=None, cell: "Cell" = None):
        self.name = name
        self._reader = reader
        self._cell = cell
        self._value = value
        chain = []
        seen = set()
        current = reader._find_style(name, family="table-cell")
        while current is not None and current.get("style:name") not in seen:
            seen.add(current.get("style:name"))
            chain.append(current)
            parent = current.get("style:parent-style-name")
            current = reader._find_style(parent, family="table-cell") if parent else None

        def prop(tag_name, attr):
            for style in chain:
                child = style.find(tag_name)
                if child is not None and attr in child.attrs:
                    return child.attrs[attr]
            return None

        self._bold = prop("style:text-properties", "fo:font-weight") == "bold"
        self._italic = prop("style:text-properties", "fo:font-style") == "italic"
        self._underline = prop("style:text-properties", "style:text-underline-style") not in (None, "none")
        self._strikethrough = prop("style:text-properties", "style:text-line-through-style") not in (
            None,
            "none",
        )
        self._font_family = prop("style:text-properties", "style:font-name")
        self._font_color = prop("style:text-properties", "fo:color")
        self._font_size = prop("style:text-properties", "fo:font-size")
        self._background_color = prop("style:table-cell-properties", "fo:background-color")
        self._vertical_align = prop("style:table-cell-properties", "style:vertical-align")
        self._horizontal_align = prop("style:paragraph-properties", "fo:text-align")
        rotation = prop("style:table-cell-properties", "style:rotation-angle")
        self._rotation = int(rotation) if rotation is not None else None
        self._writing_mode = prop("style:table-cell-properties", "style:writing-mode")
        self._wrap_text = prop("style:table-cell-properties", "fo:wrap-option") == "wrap"
        self._shrink_to_fit = prop("style:table-cell-properties", "style:shrink-to-fit") == "true"
        self._protection = prop("style:table-cell-properties", "style:cell-protect")
        self._text_position = prop("style:text-properties", "style:text-position")
        self._diagonal_bl_tr = _make_border(prop("style:table-cell-properties", "style:diagonal-bl-tr"))
        self._diagonal_tl_br = _make_border(prop("style:table-cell-properties", "style:diagonal-tl-br"))

        # Borders are resolved as a unit from the nearest style that defines
        # *any* border info (rather than merging per-side across inheritance
        # levels): within that one style, a specific side (fo:border-top...)
        # overrides the fo:border shorthand for that side, exactly as ODF
        # itself resolves it within a single style declaration.
        self._border_top = self._border_bottom = self._border_left = self._border_right = None
        for style in chain:
            cell_props = style.find("style:table-cell-properties")
            if cell_props is None or not any(a in cell_props.attrs for a in self._BORDER_ATTRS):
                continue
            attrs = cell_props.attrs
            shorthand = attrs.get("fo:border")
            self._border_top = _make_border(attrs.get("fo:border-top", shorthand))
            self._border_bottom = _make_border(attrs.get("fo:border-bottom", shorthand))
            self._border_left = _make_border(attrs.get("fo:border-left", shorthand))
            self._border_right = _make_border(attrs.get("fo:border-right", shorthand))
            break

        # raw, flattened property dicts as an escape hatch for anything not
        # surfaced above - base style first, so a nearer style overrides it
        self.cell_properties: Dict[str, str] = {}
        self.text_properties: Dict[str, str] = {}
        for style in reversed(chain):
            cell_props = style.find("style:table-cell-properties")
            if cell_props is not None:
                self.cell_properties.update(cell_props.attrs)
            text_props = style.find("style:text-properties")
            if text_props is not None:
                self.text_properties.update(text_props.attrs)

        data_style_name = next(
            (s.get("style:data-style-name") for s in chain if s.get("style:data-style-name")), None
        )
        self._number_format = None
        if data_style_name:
            number_tag = reader._find_number_style(data_style_name)
            if number_tag is not None:
                base_format = NumberFormat(number_tag, reader=reader)
                self._number_format = base_format.resolve(value) if value is not None else base_format

    def _require_cell(self):
        if self._cell is None:
            raise RuntimeError(f"style {self.name!r} has no owning Cell and cannot be written to")

    def _write_attr(self, props_tag_name, attr, raw_value):
        """Set (or, if `raw_value` is `None`, remove) one attribute on this
        cell's own automatic style."""
        self._require_cell()
        tag = self._cell._ensure_own_style()
        if raw_value is None:
            child = tag.find(props_tag_name)
            if child is not None:
                child.attrs.pop(attr, None)
            return
        _ensure_style_child(tag, props_tag_name).attrs[attr] = raw_value

    def _write_border_sides(self, updates):
        """Set 1+ of the 4 border sides, carrying the other (currently
        resolved) sides along explicitly - see the class docstring."""
        self._require_cell()
        tag = self._cell._ensure_own_style()
        values = {
            "border_top": self._border_top,
            "border_bottom": self._border_bottom,
            "border_left": self._border_left,
            "border_right": self._border_right,
        }
        values.update(updates)
        props = _ensure_style_child(tag, "style:table-cell-properties")
        props.attrs.pop("fo:border", None)
        for key, value in values.items():
            raw = _border_to_raw(value)
            props.attrs[self._BORDER_SIDE_ATTRS[key]] = raw if raw is not None else "none"
            setattr(self, f"_{key}", _make_border(raw))

    @property
    def bold(self):
        return self._bold

    @bold.setter
    def bold(self, value):
        self._write_attr("style:text-properties", "fo:font-weight", "bold" if value else "normal")
        self._bold = bool(value)

    @property
    def italic(self):
        return self._italic

    @italic.setter
    def italic(self, value):
        self._write_attr("style:text-properties", "fo:font-style", "italic" if value else "normal")
        self._italic = bool(value)

    @property
    def underline(self):
        return self._underline

    @underline.setter
    def underline(self, value):
        self._require_cell()
        tag = self._cell._ensure_own_style()
        props = _ensure_style_child(tag, "style:text-properties")
        if value:
            props.attrs["style:text-underline-style"] = "solid"
            props.attrs["style:text-underline-width"] = "auto"
            props.attrs["style:text-underline-color"] = "font-color"
        else:
            props.attrs["style:text-underline-style"] = "none"
            props.attrs.pop("style:text-underline-width", None)
            props.attrs.pop("style:text-underline-color", None)
        self._underline = bool(value)

    @property
    def strikethrough(self):
        return self._strikethrough

    @strikethrough.setter
    def strikethrough(self, value):
        self._write_attr("style:text-properties", "style:text-line-through-style", "solid" if value else "none")
        self._strikethrough = bool(value)

    @property
    def font_family(self):
        return self._font_family

    @font_family.setter
    def font_family(self, value):
        self._write_attr("style:text-properties", "style:font-name", value)
        self._font_family = value

    @property
    def font_color(self):
        return self._font_color

    @font_color.setter
    def font_color(self, value):
        self._write_attr("style:text-properties", "fo:color", value)
        self._font_color = value

    @property
    def font_size(self):
        return self._font_size

    @font_size.setter
    def font_size(self, value):
        self._write_attr("style:text-properties", "fo:font-size", value)
        self._font_size = value

    @property
    def background_color(self):
        return self._background_color

    @background_color.setter
    def background_color(self, value):
        self._write_attr("style:table-cell-properties", "fo:background-color", value)
        self._background_color = value

    @property
    def vertical_align(self):
        return self._vertical_align

    @vertical_align.setter
    def vertical_align(self, value):
        self._write_attr("style:table-cell-properties", "style:vertical-align", value)
        self._vertical_align = value

    @property
    def horizontal_align(self):
        return self._horizontal_align

    @horizontal_align.setter
    def horizontal_align(self, value):
        self._write_attr("style:paragraph-properties", "fo:text-align", value)
        self._horizontal_align = value

    @property
    def rotation(self):
        return self._rotation

    @rotation.setter
    def rotation(self, value):
        self._write_attr(
            "style:table-cell-properties", "style:rotation-angle", None if value is None else str(int(value))
        )
        self._rotation = None if value is None else int(value)

    @property
    def writing_mode(self):
        return self._writing_mode

    @writing_mode.setter
    def writing_mode(self, value):
        self._write_attr("style:table-cell-properties", "style:writing-mode", value)
        self._writing_mode = value

    @property
    def wrap_text(self):
        return self._wrap_text

    @wrap_text.setter
    def wrap_text(self, value):
        self._write_attr("style:table-cell-properties", "fo:wrap-option", "wrap" if value else "no-wrap")
        self._wrap_text = bool(value)

    @property
    def shrink_to_fit(self):
        return self._shrink_to_fit

    @shrink_to_fit.setter
    def shrink_to_fit(self, value):
        self._write_attr("style:table-cell-properties", "style:shrink-to-fit", "true" if value else "false")
        self._shrink_to_fit = bool(value)

    @property
    def protection(self):
        return self._protection

    @protection.setter
    def protection(self, value):
        self._write_attr("style:table-cell-properties", "style:cell-protect", value)
        self._protection = value

    @property
    def text_position(self):
        return self._text_position

    @text_position.setter
    def text_position(self, value):
        self._write_attr("style:text-properties", "style:text-position", value)
        self._text_position = value

    @property
    def superscript(self):
        return (self._text_position or "").startswith("super")

    @superscript.setter
    def superscript(self, value):
        self.text_position = "super 58%" if value else None

    @property
    def subscript(self):
        return (self._text_position or "").startswith("sub")

    @subscript.setter
    def subscript(self, value):
        self.text_position = "sub 58%" if value else None

    @property
    def diagonal_bl_tr(self):
        return self._diagonal_bl_tr

    @diagonal_bl_tr.setter
    def diagonal_bl_tr(self, value):
        raw = _border_to_raw(value)
        self._write_attr("style:table-cell-properties", "style:diagonal-bl-tr", raw)
        self._diagonal_bl_tr = _make_border(raw)

    @property
    def diagonal_tl_br(self):
        return self._diagonal_tl_br

    @diagonal_tl_br.setter
    def diagonal_tl_br(self, value):
        raw = _border_to_raw(value)
        self._write_attr("style:table-cell-properties", "style:diagonal-tl-br", raw)
        self._diagonal_tl_br = _make_border(raw)

    @property
    def border_top(self):
        return self._border_top

    @border_top.setter
    def border_top(self, value):
        self._write_border_sides({"border_top": value})

    @property
    def border_bottom(self):
        return self._border_bottom

    @border_bottom.setter
    def border_bottom(self, value):
        self._write_border_sides({"border_bottom": value})

    @property
    def border_left(self):
        return self._border_left

    @border_left.setter
    def border_left(self, value):
        self._write_border_sides({"border_left": value})

    @property
    def border_right(self):
        return self._border_right

    @border_right.setter
    def border_right(self, value):
        self._write_border_sides({"border_right": value})

    @property
    def number_format(self):
        return self._number_format

    @number_format.setter
    def number_format(self, fmt):
        self._require_cell()
        tag = self._cell._ensure_own_style()
        if fmt is None:
            tag.attrs.pop("style:data-style-name", None)
            self._number_format = None
            return
        if isinstance(fmt, NumberFormat):
            name = fmt.name
        elif isinstance(fmt, str):
            name = fmt
        else:
            raise TypeError(f"number_format must be a NumberFormat, str, or None, got {type(fmt)!r}")
        number_tag = self._cell.sheet.reader._find_number_style(name)
        if number_tag is None:
            raise ValueError(f"no number format named {name!r} exists in this document")
        tag.attrs["style:data-style-name"] = name
        self._number_format = NumberFormat(number_tag, reader=self._cell.sheet.reader)

    def __repr__(self):
        return (
            f"CellStyle(name={self.name!r}, bold={self.bold}, italic={self.italic}, "
            f"background_color={self.background_color!r})"
        )


class RowStyle:
    """Resolved `<style:style style:family="table-row">` behind a row's
    `table:style-name` - no inheritance chain (rows don't meaningfully use
    `style:parent-style-name` in practice). Look up via
    `Sheet.row_style(row)`, not directly.

    Writable: setting `.height`/`.optimal_height`/`.visible` forks the row
    its own private automatic style on first use (see
    `Sheet._ensure_row_style`), carrying over whatever the row's current
    style already had (all 3 properties come from one single style tag,
    not a chain, so nothing here is resolved independently)."""

    def __init__(self, tag, sheet: "Sheet" = None, row: int = None):
        self._sheet = sheet
        self._row = row
        self.name = tag.get("style:name") if tag is not None else None
        props = tag.find("style:table-row-properties") if tag is not None else None
        self._height = props.attrs.get("style:row-height") if props is not None else None
        self._optimal_height = (
            props is not None and props.attrs.get("style:use-optimal-row-height") == "true"
        )
        self._visible = props is None or props.attrs.get("table:visibility", "visible") == "visible"

    def _require_owner(self):
        if self._sheet is None or self._row is None:
            raise RuntimeError(f"style {self.name!r} has no owning Sheet/row and cannot be written to")

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, value):
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_row_style(self._row), "style:table-row-properties")
        if value is None:
            props.attrs.pop("style:row-height", None)
        else:
            props.attrs["style:row-height"] = value
        props.attrs["style:use-optimal-row-height"] = "false"
        self._height = value
        self._optimal_height = False

    @property
    def optimal_height(self):
        return self._optimal_height

    @optimal_height.setter
    def optimal_height(self, value):
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_row_style(self._row), "style:table-row-properties")
        props.attrs["style:use-optimal-row-height"] = "true" if value else "false"
        self._optimal_height = bool(value)

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_row_style(self._row), "style:table-row-properties")
        props.attrs["table:visibility"] = "visible" if value else "collapse"
        self._visible = bool(value)

    def __repr__(self):
        return f"RowStyle(name={self.name!r}, height={self.height!r})"


class ColumnStyle:
    """Resolved `<style:style style:family="table-column">` behind a
    column's `table:style-name` - no inheritance chain. Look up via
    `Sheet.column_style(col)`, not directly.

    Writable: setting `.width`/`.visible` forks the column its own private
    automatic style on first use (see `Sheet._ensure_column_style`),
    carrying over whatever the column's current style already had."""

    def __init__(self, tag, sheet: "Sheet" = None, col: int = None):
        self._sheet = sheet
        self._col = col
        self.name = tag.get("style:name") if tag is not None else None
        props = tag.find("style:table-column-properties") if tag is not None else None
        self._width = props.attrs.get("style:column-width") if props is not None else None
        self._visible = props is None or props.attrs.get("table:visibility", "visible") == "visible"

    def _require_owner(self):
        if self._sheet is None or self._col is None:
            raise RuntimeError(f"style {self.name!r} has no owning Sheet/column and cannot be written to")

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, value):
        self._require_owner()
        props = _ensure_style_child(
            self._sheet._ensure_column_style(self._col), "style:table-column-properties"
        )
        if value is None:
            props.attrs.pop("style:column-width", None)
        else:
            props.attrs["style:column-width"] = value
        self._width = value

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._require_owner()
        props = _ensure_style_child(
            self._sheet._ensure_column_style(self._col), "style:table-column-properties"
        )
        props.attrs["table:visibility"] = "visible" if value else "collapse"
        self._visible = bool(value)

    def __repr__(self):
        return f"ColumnStyle(name={self.name!r}, width={self.width!r})"


class TableStyle:
    """Resolved `<style:style style:family="table">` behind a sheet's
    `table:style-name` - no inheritance chain. Look up via `Sheet.style`,
    not directly.

    Writable: setting `.tab_color`/`.visible` forks the sheet its own
    private automatic style on first use (see
    `Sheet._ensure_table_style`), carrying over whatever the sheet's
    current style already had."""

    def __init__(self, tag, sheet: "Sheet" = None):
        self._sheet = sheet
        self.name = tag.get("style:name") if tag is not None else None
        props = tag.find("style:table-properties") if tag is not None else None
        self._tab_color = props.attrs.get("table:tab-color") if props is not None else None
        self._visible = props is None or props.attrs.get("table:display", "true") != "false"

    def _require_owner(self):
        if self._sheet is None:
            raise RuntimeError(f"style {self.name!r} has no owning Sheet and cannot be written to")

    @property
    def tab_color(self):
        return self._tab_color

    @tab_color.setter
    def tab_color(self, value):
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_table_style(), "style:table-properties")
        if value is None:
            props.attrs.pop("table:tab-color", None)
        else:
            props.attrs["table:tab-color"] = value
        self._tab_color = value

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, value):
        self._require_owner()
        props = _ensure_style_child(self._sheet._ensure_table_style(), "style:table-properties")
        props.attrs["table:display"] = "true" if value else "false"
        self._visible = bool(value)

    def __repr__(self):
        return f"TableStyle(name={self.name!r}, tab_color={self.tab_color!r})"


def _parse_user_defined_value(tag):
    """The typed Python value behind one `<meta:user-defined>` element,
    per its `meta:value-type` (`"string"` if absent, per the ODF spec)."""
    value_type = tag.get("meta:value-type", "string")
    text = tag.get_text()
    if value_type == "float":
        return float(text)
    if value_type == "boolean":
        return text == "true"
    if value_type == "date":
        return dt.date.fromisoformat(text[:10])
    return text


def _write_user_defined_value(tag, value):
    """Set `<meta:user-defined>`'s text content and `meta:value-type` from
    a Python value - the reverse of `_parse_user_defined_value`."""
    if isinstance(value, bool):
        tag.attrs["meta:value-type"] = "boolean"
        tag.string = "true" if value else "false"
    elif isinstance(value, (int, float)):
        tag.attrs["meta:value-type"] = "float"
        tag.string = str(value)
    elif isinstance(value, dt.date):
        tag.attrs["meta:value-type"] = "date"
        tag.string = value.isoformat()
    elif isinstance(value, str):
        tag.attrs.pop("meta:value-type", None)  # "string" is the implicit default
        tag.string = value
    else:
        raise TypeError(f"unsupported custom property value type: {type(value)!r}")


class DocumentProperties:
    """Structured, writable access to `meta.xml` - the document properties
    behind LibreOffice's "File > Properties" dialog: `.title`, `.subject`,
    `.description`, `.creator` (who last saved it), `.initial_creator`
    (who created it), `.keywords` (a list), plus arbitrary custom
    properties (`meta:user-defined`) via dict-style access
    (`props["Client"]`). Get it via `ODSReader.properties`, not directly.

    A custom property's Python type round-trips through ODF's own
    `meta:value-type` (`str`/`float`/`bool`/`datetime.date`) - assigning
    any other type raises `TypeError`."""

    def __init__(self, reader: "ODSReader"):
        self._reader = reader

    def _office_meta(self):
        root = self._reader.meta_data.find("office:document-meta")
        meta = root.find("office:meta")
        if meta is None:
            meta = _blank_template(self._reader.meta_data, "office:meta")
            root.append(meta)
        return meta

    def _get_text(self, tag_name):
        tag = self._office_meta().find(tag_name)
        return tag.get_text() if tag is not None else None

    def _set_text(self, tag_name, value):
        meta = self._office_meta()
        tag = meta.find(tag_name)
        if value is None:
            if tag is not None:
                tag.decompose()
            return
        if tag is None:
            tag = _blank_template(self._reader.meta_data, tag_name)
            meta.append(tag)
        tag.string = value

    @property
    def title(self):
        return self._get_text("dc:title")

    @title.setter
    def title(self, value):
        self._set_text("dc:title", value)

    @property
    def subject(self):
        return self._get_text("dc:subject")

    @subject.setter
    def subject(self, value):
        self._set_text("dc:subject", value)

    @property
    def description(self):
        return self._get_text("dc:description")

    @description.setter
    def description(self, value):
        self._set_text("dc:description", value)

    @property
    def creator(self):
        """Who last saved the document (`dc:creator`)."""
        return self._get_text("dc:creator")

    @creator.setter
    def creator(self, value):
        self._set_text("dc:creator", value)

    @property
    def initial_creator(self):
        """Who originally created the document (`meta:initial-creator`)."""
        return self._get_text("meta:initial-creator")

    @initial_creator.setter
    def initial_creator(self, value):
        self._set_text("meta:initial-creator", value)

    @property
    def keywords(self):
        """`meta:keyword` values (0+), in document order."""
        return [tag.get_text() for tag in self._office_meta().find_all("meta:keyword")]

    @keywords.setter
    def keywords(self, values):
        meta = self._office_meta()
        for tag in meta.find_all("meta:keyword"):
            tag.decompose()
        for value in values or ():
            tag = _blank_template(self._reader.meta_data, "meta:keyword")
            tag.string = value
            meta.append(tag)

    @property
    def generator(self):
        """The application that last saved this file (e.g.
        `"LibreOffice/25.8..."`) - read-only, `odsslicer` doesn't claim
        to be a spreadsheet application."""
        return self._get_text("meta:generator")

    @property
    def custom(self):
        """A dict snapshot `{name: value}` of every `meta:user-defined`
        property - use `props["name"]`/`props["name"] = value` to read or
        write a single one instead."""
        return {
            tag.get("meta:name"): _parse_user_defined_value(tag)
            for tag in self._office_meta().find_all("meta:user-defined")
        }

    def _find_custom(self, name):
        return self._office_meta().find("meta:user-defined", attrs={"meta:name": name})

    def __getitem__(self, name):
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        return _parse_user_defined_value(tag)

    def __setitem__(self, name, value):
        tag = self._find_custom(name)
        if tag is None:
            tag = _blank_template(self._reader.meta_data, "meta:user-defined")
            tag.attrs["meta:name"] = name
            self._office_meta().append(tag)
        _write_user_defined_value(tag, value)

    def __delitem__(self, name):
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        tag.decompose()

    def __contains__(self, name):
        return self._find_custom(name) is not None

    def __repr__(self):
        return f"DocumentProperties(title={self.title!r}, creator={self.creator!r})"


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
        self.styles_data = BeautifulSoup(self.styles, "xml")
        self.meta_data = BeautifulSoup(self.meta, "xml")
        self.tables = self.data.find_all("table:table")
        self.sheets_names = [table["table:name"] for table in self.tables]
        self._sheets: dict[str, Sheet | None] = {name: None for name in self.sheets_names}
        if self.verbose:
            print(f"    {repr(self)}")

    def __repr__(self):
        return f"ODSReader({self.file}, sheets={self.sheets_names})"

    @property
    def properties(self) -> "DocumentProperties":
        """Structured, writable access to `meta.xml`'s document properties
        - see `DocumentProperties`."""
        return DocumentProperties(self)

    def _find_style(self, name, family=None):
        """A `<style:style>` by name (optionally constrained to a
        `style:family`, e.g. `"table-cell"`/`"table-row"`/`"table-column"`/
        `"table"` - names are conventionally unique per family in real
        files, but nothing enforces that): automatic styles (`content.xml`)
        first, then named/common styles (`styles.xml`) - a `table:style-name`
        can point at either."""
        if not name:
            return None
        attrs = {"style:name": name}
        if family is not None:
            attrs["style:family"] = family
        return self.data.find("style:style", attrs=attrs) or self.styles_data.find(
            "style:style", attrs=attrs
        )

    def _find_number_style(self, name):
        """A `<number:*-style>` by name, wherever it lives - like cell
        styles, a number format can be defined in either file."""
        if not name:
            return None
        return self.data.find(_NUMBER_STYLE_TAGS, attrs={"style:name": name}) or self.styles_data.find(
            _NUMBER_STYLE_TAGS, attrs={"style:name": name}
        )

    def _automatic_styles(self):
        """The `<office:automatic-styles>` element in `content.xml` - the
        only place a newly created style can go and still survive `save()`
        (styles already in `styles.xml` are copied through unchanged, see
        `save()`)."""
        styles = self.data.find("office:automatic-styles")
        if styles is None:
            styles = _new_qualified_tag("office:automatic-styles")
            body = self.data.find("office:body")
            if body is not None:
                body.insert_before(styles)
            else:
                self.data.append(styles)
        return styles

    def _new_style_name(self, prefix):
        """A `style:name` not already used by any style in the document
        (either file), of the form `f"{prefix}{n}"` for the smallest
        available `n`."""
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        for doc in (self.data, self.styles_data):
            for tag in doc.find_all(attrs={"style:name": True}):
                m = pattern.match(tag.get("style:name") or "")
                if m:
                    max_n = max(max_n, int(m.group(1)))
        return f"{prefix}{max_n + 1}"

    def _new_style_tag(self, family, prefix, parent_style_name=None):
        """A brand new, empty `<style:style style:family=family>` with a
        fresh unique name, already inserted into `content.xml`'s automatic
        styles."""
        tag = _blank_template(self.data, "style:style")
        tag.attrs["style:name"] = self._new_style_name(prefix)
        tag.attrs["style:family"] = family
        if parent_style_name:
            tag.attrs["style:parent-style-name"] = parent_style_name
        self._automatic_styles().append(tag)
        return tag

    _TEMPLATE_PATH = Path(__file__).parent / "_template.ods"

    @classmethod
    def new(cls, sheet_name: str = "Sheet1", verbose: bool = False) -> "ODSReader":
        """Create a brand new, empty spreadsheet - not backed by any file on
        disk yet - with a single sheet named `sheet_name`.

        Bootstrapped from a minimal template bundled with the package (a
        valid, empty ODF document needs several non-trivial pieces - a
        `mimetype`, `META-INF/manifest.xml`, `styles.xml`... - that only a
        real spreadsheet application can produce correctly, so this reuses
        one rather than hand-assembling them). `save(path)` requires an
        explicit path (there is no source file to default to).
        """
        reader = cls(cls._TEMPLATE_PATH, verbose=verbose)
        reader._from_template = True
        if sheet_name != "Sheet1":
            reader.tables[0]["table:name"] = sheet_name
            reader.sheets_names[0] = sheet_name
            reader._sheets = {sheet_name: reader._sheets.pop("Sheet1")}
        return reader

    def save(self, path: Union[Path, str, None] = None):
        """Write the in-memory content back out as a .ods file.

        `content.xml` (sheets, cell data, automatic styles, formulas) and
        `meta.xml` (`.properties` - title, author, custom properties...)
        are regenerated from their in-memory trees; every other zip member
        (`styles.xml`, `settings.xml`, `manifest.xml`, thumbnail...) is
        copied through unchanged from the source file. Defaults to
        overwriting `self.file` - except for a document created with
        `ODSReader.new()`, which has no source file of its own and
        requires an explicit `path`.
        """
        if path is None:
            if getattr(self, "_from_template", False):
                raise ValueError(
                    "this document was created with ODSReader.new() and has no "
                    "source file of its own - pass an explicit path to save(...)"
                )
            path = self.file
        regenerated = {
            "content.xml": self.data.encode("utf-8"),
            "meta.xml": self.meta_data.encode("utf-8"),
        }
        with ZipFile(self.file) as src:
            entries = [(item, regenerated.get(item.filename, src.read(item.filename))) for item in src.infolist()]
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
                self.tables[self.sheets_names.index(name)], verbose=self.verbose, reader=self
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
        self._sheets[name] = Sheet(table, verbose=self.verbose, reader=self)
        return self._sheets[name]

    def delete_sheet(self, name: str):
        """Remove the sheet named `name` entirely.

        Raises `IndexError` for an unknown name, and `ValueError` for the
        document's last remaining sheet (an ODF spreadsheet needs at least
        one). Any `Sheet`/`Cell` object obtained before the call that
        pointed into this sheet is now backed by a decomposed, detached
        XML element - stop using it."""
        if name not in self.sheets_names:
            raise IndexError(f"No sheet named {name}")
        if len(self.sheets_names) <= 1:
            raise ValueError("cannot delete the only remaining sheet in the document")
        idx = self.sheets_names.index(name)
        self.tables[idx].decompose()
        del self.tables[idx]
        del self.sheets_names[idx]
        del self._sheets[name]
