"""ODF formula machinery: friendly <-> ODF syntax translation, reference
shifting for fills/copies/deletions/renames, {r}/{c} templating."""

import ast
import bisect
import re
from collections.abc import Callable

from .addresses import string_address, string_to_col

_FORMULA_LANGUAGE_PREFIX = re.compile(r"^[A-Za-z][\w.-]*:=")

# A1-style cell reference, optionally with $ absolute markers on either part
# (A2, $A2, A$2, $A$2). Column letters capped at 3 (XFD-style) is generous
# enough and helps the "not a function name" heuristic below.
_CELL_REF = r"\$?[A-Za-z]{1,3}\$?[0-9]+"
# an optional leading sheet qualifier: SheetName. or 'Sheet Name'. (the quoted
# form allows spaces/special characters, doubled '' for a literal quote). As
# LibreOffice writes them, a name may start with any letter - Élèves - and
# carry a `$`, an absolute sheet, for a reference made by clicking a cell of
# another sheet: missing both skipped such references (issue #14)
_SHEET_NAME = r"(?:\$?(?:'(?:[^']|'')*'|[^\W\d]\w*))"
_FRIENDLY_REF_RE = re.compile(
    rf"(?<![A-Za-z0-9_$'.])"
    rf"(?:(?P<sheet>{_SHEET_NAME})\.)?"
    rf"(?P<start>{_CELL_REF})"
    rf"(?::(?P<end>{_CELL_REF}))?"
)
# split on quoted string literals ("..." with "" as an escaped quote), so
# translation never touches text that happens to look like a cell reference
_STRING_LITERAL_RE = re.compile(r'("(?:[^"]|"")*")')


def _translate_friendly_formula(body: str) -> str:
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

    def translate_refs(segment: str) -> str:
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


def _odf_ref_to_friendly(inner: str) -> str:
    """`.A1` -> `A1`, `.$A$1` -> `$A$1`, `Sheet2.A1` -> `Sheet2.A1` (already
    friendly) - the reverse of the single-reference half of
    `_translate_friendly_formula`."""
    return inner[1:] if inner.startswith(".") else inner


def _translate_odf_formula_to_friendly(body: str) -> str:
    """Reverse of `_translate_friendly_formula`: turn ODF bracket references
    (`[.A1]`, `[.A1:.B3]`, `[Sheet2.A1]`) back into ordinary `A1`-style
    syntax, and `;` argument separators back into `,` (outside quoted string
    literals). Best-effort - a bracket whose content isn't a plain reference
    or range (a named range, an unusual construct) is passed through as-is.
    """

    def translate_refs(segment: str) -> str:
        def replace(m: re.Match[str]) -> str:
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


def _shift_cell_address(addr: str, drow: int, dcol: int) -> str:
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
    col = string_to_col(col_letters)
    row = int(row_digits) - 1
    if not col_abs:
        col += dcol
    if not row_abs:
        row += drow
    if row < 0 or col < 0:
        raise ValueError(
            f"shifting {addr!r} by (row={drow}, col={dcol}) would move it off the sheet"
        )
    new_letters = string_address(0, col)[:-1]
    shifted = f"{col_abs}{new_letters}{row_abs}{row + 1}"
    return f".{shifted}" if dotted else shifted


def _shift_odf_reference(inner: str, drow: int, dcol: int) -> str:
    """Shift the address part(s) of one bracket's content (`.A1`, `.A1:.B3`,
    `Sheet2.A1`, `Sheet2.A1:.B3`), leaving any sheet-name prefix untouched."""

    def shift_one(part: str) -> str:
        m = _SHEET_QUALIFIED_RE.match(part)
        assert m is not None  # the pattern matches any non-empty reference part
        sheet, addr = m.group("sheet"), m.group("addr")
        shifted_addr = _shift_cell_address(addr, drow, dcol)
        return f"{sheet}.{shifted_addr}" if sheet else shifted_addr

    if ":" in inner:
        start, end = inner.split(":", 1)
        return f"{shift_one(start)}:{shift_one(end)}"
    return shift_one(inner)


def _shift_odf_formula(formula: str, drow: int, dcol: int) -> str:
    """Shift every reference in an already ODF-syntax formula (as stored by
    `Cell.formula`) by `(drow, dcol)`. Used by `Cell.fill_formula` to
    replicate a formula across a range the way a spreadsheet's fill handle
    does."""
    return _ODF_BRACKET_RE.sub(
        lambda m: f"[{_shift_odf_reference(m.group(1), drow, dcol)}]", formula
    )


def _unquote_odf_sheet_name(raw: str) -> str:
    """`'My Sheet'` -> `My Sheet` (undoing the doubled-`''` escape), or
    `Sheet2` unchanged - the reverse of the quoting `_SHEET_NAME` matches -
    and `$Sheet2`, an absolute sheet, `Sheet2`."""
    raw = raw.removeprefix("$")
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    return raw


# How a structural edit moves a referenced cell: `(row, col, edge)` -> its
# new `(row, col)` (0-based), or `None` for a cell deleted with its row or
# column. `edge` tells a single reference (`None`) from a range's first cell
# ("start") or last ("end"): a range whose first or last row is deleted
# keeps the rows left of it, as in a spreadsheet.
_Remap = Callable[[int, int, "str | None"], "tuple[int, int] | None"]


def _deletion_remap(
    deleted_rows: "list[int] | None" = None,
    deleted_cols: "list[int] | None" = None,
) -> _Remap:
    """The rule for the (sorted) `deleted_rows` (or `deleted_cols`) having
    been physically removed: an index moves back by the number of removed
    positions before it. A deleted cell is gone, unless it ends a range:
    its first cell becomes the next one left, its last the previous one.
    Leaving a deleted index unchanged, as this did, pointed the reference
    at the cell that took its place - a range losing its last row took in
    the row after it, a total just below for one (issue #12)."""

    def moved(index: int, deleted: "list[int] | None", edge: "str | None") -> "int | None":
        if not deleted:
            return index
        before = bisect.bisect_left(deleted, index)
        if before < len(deleted) and deleted[before] == index:
            if edge is None:
                return None
            return index - before - (1 if edge == "end" else 0)
        return index - before

    def remap(row: int, col: int, edge: "str | None" = None) -> "tuple[int, int] | None":
        new_row, new_col = moved(row, deleted_rows, edge), moved(col, deleted_cols, edge)
        if new_row is None or new_col is None:
            return None
        return new_row, new_col

    return remap


def _insertion_remap(
    at_row: "int | None" = None,
    at_col: "int | None" = None,
    count: int = 1,
) -> _Remap:
    """The rule for `count` rows (or columns) inserted before index `at_row`
    (or `at_col`): every index at or past the insertion point moves forward
    by `count`.

    Applied to each end of a range independently, this reproduces what a
    spreadsheet does with ranges too: inserting strictly inside `A2:A10`
    stretches it (only the end moves), inserting at or above its first row
    moves it whole, inserting just below its last row leaves it alone."""

    def remap(row: int, col: int, edge: "str | None" = None) -> "tuple[int, int] | None":
        if at_row is not None and row >= at_row:
            row += count
        if at_col is not None and col >= at_col:
            col += count
        return row, col

    return remap


def _remap_reference(
    reference: str,
    target_sheet: str,
    containing_sheet: "str | None",
    remap: _Remap,
    edge: "str | None" = None,
) -> "str | None":
    """One reference - `.A1`, `Sheet2.$A$1`, or a range, `.A1:.B3` - through
    `remap`, keeping its `$` locks, dots and sheet names; `None` if what it
    pointed at was deleted whole, a single cell or every row or column of a
    range. Only an address resolving to `target_sheet` moves - explicitly
    sheet-qualified, or bare when `containing_sheet` is `target_sheet`, or
    a range's end on its start's sheet - and anything that isn't a plain
    cell address is left as it is. Meant for structural edits, where the
    referenced cell itself moves: unlike `_shift_cell_address`'s fill/copy
    semantics, a `$` lock is irrelevant. `edge` is a single reference's
    when it stands for one: a drawing's last cell."""
    parts = reference.split(":", 1)
    edges: list[str | None] = [edge] if len(parts) == 1 else ["start", "end"]
    rewritten: list[str] = []
    before: list[tuple[int, int]] = []
    after: list[tuple[int, int]] = []
    effective_sheet = containing_sheet
    for part, edge in zip(parts, edges, strict=True):
        m = _SHEET_QUALIFIED_RE.match(part)
        assert m is not None  # the pattern matches any non-empty reference part
        ref_sheet, addr = m.group("sheet"), m.group("addr")
        # a range's end with no sheet name is on its start's sheet, as
        # LibreOffice writes one on another sheet: `[$Data.A1:.A5]`
        if ref_sheet:
            effective_sheet = _unquote_odf_sheet_name(ref_sheet)
        dotted = addr.startswith(".")
        cell = _ODF_CELL_ADDRESS_RE.match(addr[1:] if dotted else addr)
        if effective_sheet != target_sheet or cell is None:
            rewritten.append(part)
            continue
        col_abs, col_letters, row_abs, row_digits = cell.groups()
        position = (int(row_digits) - 1, string_to_col(col_letters))
        new = remap(*position, edge)
        if new is None:
            return None
        before.append(position)
        after.append(new)
        text = f"{col_abs}{string_address(0, new[1])[:-1]}{row_abs}{new[0] + 1}"
        text = f".{text}" if dotted else text
        rewritten.append(f"{ref_sheet}.{text}" if ref_sheet else text)
    ordered = len(before) == 2 and before[0][0] <= before[1][0] and before[0][1] <= before[1][1]
    if ordered and (after[0][0] > after[1][0] or after[0][1] > after[1][1]):
        return None  # every row, or every column, of the range deleted
    return ":".join(rewritten)


def _remap_range_list(
    ranges: str, target_sheet: str, remap: _Remap, edge: "str | None" = None
) -> str:
    """A list of cell ranges as charts and drawings hold them - separated by
    spaces, each sheet-qualified: `Report.A5:Report.A11 Report.B4:Report.B4`
    - through `remap` (see `_remap_reference`), as LibreOffice has a chart's
    ranges follow a structural edit (issue #15): a range shrinks with the
    rows or columns it loses, and one deleted whole keeps its address, a
    chart having no #REF! to show."""
    return " ".join(
        _remap_reference(item, target_sheet, None, remap, edge) or item for item in ranges.split()
    )


def _rename_range_list(ranges: str, old_name: str, new_name: str) -> str:
    """`_remap_range_list`'s counterpart for a sheet renamed."""
    return " ".join(_rename_odf_reference_sheet(item, old_name, new_name) for item in ranges.split())


def _remap_odf_formula_references(
    formula: str,
    target_sheet: str,
    containing_sheet: str,
    remap: _Remap,
) -> str:
    """Rewrite, through `remap` (see `_remap_reference`), every reference in
    an already ODF-syntax formula that resolves to `target_sheet`;
    references into any other sheet are left as they are. Used by
    `Sheet.delete_rows`/`insert_rows` and their column equivalents to keep
    formulas, in this sheet or any other, pointing at the same cells they
    did before the structural edit. A reference to what was deleted becomes
    `[#REF!]`, as LibreOffice writes it."""

    def remap_bracket(inner: str) -> str:
        remapped = _remap_reference(inner, target_sheet, containing_sheet, remap)
        return "#REF!" if remapped is None else remapped

    return _ODF_BRACKET_RE.sub(lambda m: f"[{remap_bracket(m.group(1))}]", formula)


_SIMPLE_SHEET_NAME_RE = re.compile(r"^[^\W\d]\w*$")


def _quote_odf_sheet_name(name: str) -> str:
    """The ODF bracket-syntax form of a sheet name: unquoted if it's a
    plain identifier, else single-quoted with any embedded `'` doubled -
    the reverse of `_unquote_odf_sheet_name`."""
    if _SIMPLE_SHEET_NAME_RE.match(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _rename_odf_reference_sheet(inner: str, old_name: str, new_name: str) -> str:
    """Rewrite the sheet-name portion of one bracket's content for a
    sheet rename - only a reference *explicitly* qualified with
    `old_name` is touched; a bare reference (`.A1`, no sheet prefix)
    means "this same sheet" regardless of what it's named, so it's
    already correct and left alone."""

    def rename_one(part: str) -> str:
        m = _SHEET_QUALIFIED_RE.match(part)
        assert m is not None  # the pattern matches any non-empty reference part
        ref_sheet, addr = m.group("sheet"), m.group("addr")
        if ref_sheet is None or _unquote_odf_sheet_name(ref_sheet) != old_name:
            return part
        absolute = "$" if ref_sheet.startswith("$") else ""  # stays absolute
        return f"{absolute}{_quote_odf_sheet_name(new_name)}.{addr}"

    if ":" in inner:
        start, end = inner.split(":", 1)
        return f"{rename_one(start)}:{rename_one(end)}"
    return rename_one(inner)


def _rename_odf_formula_sheet(formula: str, old_name: str, new_name: str) -> str:
    """Rewrite every explicitly-qualified reference to `old_name` in an
    already ODF-syntax formula to `new_name` instead - used by
    `ODSReader.rename_sheet` to keep cross-sheet formulas (in any sheet)
    pointing at the renamed sheet."""
    return _ODF_BRACKET_RE.sub(
        lambda m: f"[{_rename_odf_reference_sheet(m.group(1), old_name, new_name)}]", formula
    )


# valid table:function values for a data pilot field with orientation="data"
# (ODF 1.2 §19.643.1/19.643.3) - "auto" is deliberately excluded here, since
# it's only meaningful for row/column-orientation fields, not aggregations
_PIVOT_DATA_FUNCTIONS = frozenset(
    {"sum", "average", "count", "countnums", "max", "min", "product", "stdev", "stdevp", "var", "varp"}
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


def _eval_template_expr(expr: str, context: dict[str, int]) -> int:
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
    return eval(code, {"__builtins__": {}}, context)


_ESCAPED_BRACES_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
# NUL-delimited stash markers: guaranteed not to look like a cell reference or
# contain a comma, so they pass unscathed through the friendly-formula
# translation that runs after templating.
_ESCAPE_PLACEHOLDER = "\x00{}\x00"


def _expand_formula_template(pattern: str, row: int, col: int) -> "tuple[str, Callable[[str], str]]":
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

    def stash(m: re.Match[str]) -> str:
        escaped.append(m.group(1))
        return _ESCAPE_PLACEHOLDER.format(len(escaped) - 1)

    pattern = _ESCAPED_BRACES_RE.sub(stash, pattern)

    if "{" in pattern:
        context = {"r": row + 1, "c": col + 1}
        pattern = _TEMPLATE_TOKEN_RE.sub(
            lambda m: str(_eval_template_expr(m.group(1), context)), pattern
        )

    def restore(formula: str) -> str:
        for i, content in enumerate(escaped):
            formula = formula.replace(_ESCAPE_PLACEHOLDER.format(i), "{" + content + "}")
        return formula

    return pattern, restore


def _normalize_odf_formula(formula: str) -> str:
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


def _friendly_formula(formula: "str | None") -> "str | None":
    """The reverse of `_normalize_odf_formula`, for display: strip the
    `<language>:=` prefix and translate ODF references/separators back into
    ordinary `A1`-style syntax, e.g. `"of:=[.A2]+[.A3]"` -> `"=A2+A3"`."""
    if formula is None:
        return None
    m = _FORMULA_LANGUAGE_PREFIX.match(formula)
    body = formula[m.end() :] if m else formula
    return f"={_translate_odf_formula_to_friendly(body)}"
