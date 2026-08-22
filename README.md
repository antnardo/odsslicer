# odsslicer

[![CI](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml/badge.svg)](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml)

Python reader for `.ods` files (OpenDocument Spreadsheet, LibreOffice/OpenOffice Calc), with a
numpy-inspired indexing API: `sheet["A1"]`, `sheet[0, 0]`, `sheet["A1:B3"]`, plain Python
slices, etc.

The module parses `content.xml` directly (via BeautifulSoup) and handles ODF cell types
(text, number, percentage, currency, date, time, boolean), formulas, as well as repeated and
merged rows/columns.

Write support: `cell.value = ...` then `reader.save(...)`. Repeated or merged cells are
automatically unrolled/unmerged in the background on first write access, and writing beyond a
sheet's current extent grows it automatically (new rows/columns) — see
[Writing](#writing-experimental) below for details and remaining limitations.

## Installation

Not published on PyPI yet. Install directly from GitHub in the meantime:

```bash
pip install git+https://github.com/antnardo/odsslicer.git
```

Or clone it and install it editable (for local development):

```bash
git clone https://github.com/antnardo/odsslicer.git
cd odsslicer
pip install -e ".[test]"
```

### Dependencies

- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) + `lxml` (XML parser)
- [`numpy`](https://pypi.org/project/numpy/)

Installed automatically as dependencies of the package above.

## Quick usage

```python
from odsslicer import ODSReader
from pathlib import Path

table = ODSReader(Path("workbook.ods"))
table.sheets_names        # ["Sheet1", "Sheet2", ...]
table.sheets               # list of Sheet (cached)
sheet = table.sheet("Sheet1")

sheet["A1"]                # cell A1 (Cell)
sheet[0, 0]                 # equivalent: (row, col), 0-indexed
sheet[0]                    # entire row 1 (same as sheet["1"])
sheet[:, 0]                  # entire column A (same as sheet["A"])
sheet["A1:B3"]               # block, equivalent to sheet[0:3, 0:2]

sheet["ZZZ100000"]          # outside the data: returns an empty cell (value=None), no error
```

An address or slice outside the data always returns empty cells (`value=None`) of the correct
shape, rather than an error — the shape follows the same conventions as numpy (a (n, 1) column
stays 2D, see `to_vector()` below to flatten it).

### Cells (`Cell`)

```python
cell = sheet["A1"]
cell.value          # typed value (str / float / bool / datetime.date / datetime.time / None)
cell.text           # text as displayed in the spreadsheet (always a str, or None)
str(cell)            # == cell.text (or "None")
cell.format          # "string" / "float" / "percentage" / "currency" / "date" / "time" / "boolean" / None
cell.row, cell.col   # 0-indexed position
cell.address         # spreadsheet-style address, e.g. "A1", "AZ12"
cell.is_formula      # True if the cell holds an ODF formula
cell.is_empty        # True if no value/text/format is set
```

`Cell` supports the usual numeric conversions (`int()`, `float()`, `round()`, `abs()`, `-`,
`+`, `math.trunc/ceil/floor`) and comparisons (`==`, `<`, `>`, `<=`, `>=`), all of which operate
on `cell.value`. Note: comparing an empty cell (`value=None`) to a numeric cell raises a
`TypeError`, just like plain Python (`None < 3.4`).

Available formats are listed in `odsslicer.FORMATS` (ODF format -> conversion callable).

### Arrays (`ArrayValues`)

Any multi-cell selection (`sheet[0]`, `sheet[:, 0]`, `sheet["A1:B3"]`, iterating over a
`Sheet`...) returns an `ArrayValues` object, a wrapper around a list of `Cell` (1D) or a list
of lists of `Cell` (2D):

```python
arr = sheet["A1:B3"]
arr.dimension     # 0 (a single cell), 1 (row/column), or 2 (block)
arr.size           # numpy-style shape, e.g. (3, 2)
arr.to_list()       # raw values (list or list of list), without the Cell objects
arr.to_numpy()      # np.array of the values
arr.to_vector()     # for a (n, 1) shape: returns a 1D ArrayValues of size (n,)
```

Equality (`==`) between two `ArrayValues` compares the values (`to_list()`), not the identity
of the `Cell` objects.

`.value` and `.formula` are also writable on a multi-cell selection — see
[Writing the same pattern across several cells](#writing-the-same-pattern-across-several-cells)
below.

### Iteration

```python
for row in sheet:              # equivalent to sheet[:]
    for cell in row:
        print(cell.address, cell.value)
```

## Writing (experimental)

`Cell.value` is writable — the new value replaces the underlying XML content directly in
memory:

```python
from odsslicer import ODSReader

table = ODSReader("workbook.ods")
sheet = table.sheet("Sheet1")

sheet["A1"].value = "new text"
sheet["A2"].value = 42.5
sheet["A3"].value = None              # clears the cell

table.save("modified_workbook.ods")    # or table.save() to overwrite the source file
```

Accepted types for writing: `str`, `int`/`float`, `bool`, `datetime.date`, `datetime.time`,
and `None` (clears the cell). Writing a number over a cell already formatted as `percentage`
or `currency` keeps that format. Writing over a cell that held a formula erases the formula
(`is_formula` becomes `False` again).

`ODSReader.save(path=None)` rewrites the `.ods`: `content.xml` is regenerated from the
in-memory tree, every other zip member (`styles.xml`, `meta.xml`, `settings.xml`,
`manifest.xml`, thumbnail...) is copied through unchanged from the source file, and the ODF
convention (`mimetype` first, uncompressed) is respected. With no argument, `save()`
overwrites the source file.

### Automatic unrolling of repeated and merged cells

ODS compresses identical rows/columns into a single XML element shared between several
`Cell`s, and represents a merge via a top-left "master" cell (carrying the
`table:number-*-spanned` attributes) plus hidden `table:covered-table-cell` cells. Writing to
one of these cells automatically triggers, in the background, the "unrolling" of the
structure involved — the compressed row/column is split into individual XML elements, and/or
the merge is undone — before the new value is applied:

```python
sheet["C5"].value = 42   # C5 was part of a block of 6 compressed rows: the block is split
                          # into 6 independent rows, only C5's value changes, the other 35
                          # cells in the block keep their original value
```

Writing to a merged cell (master or hidden) undoes the whole merge: every previously hidden
cell becomes independent again and reveals its own value — ODF already stores it internally
under `table:covered-table-cell`, exactly as LibreOffice would when manually un-merging.
`Cell` objects already obtained before the write remain valid and are automatically repointed
to their new individual XML element; `sheet.size` never changes as a result of unrolling (the
logical row/column count was already that value).

### Automatic sheet growth

Writing to an address outside the current extent (`sheet.size`) grows the sheet instead of
raising an error: existing rows are widened with blank cells if the requested column exceeds
the current width, then new (full-width, blank) rows are appended if the requested row
exceeds the current height — including growing a completely empty sheet
(`sheet.size == (0, 0)`) from scratch:

```python
sheet.size            # (9, 2)
sheet["E12"].value = "corner"
sheet.size            # (12, 5): rows 10-12 added, columns C-E added, everything else blank
```

`sheet.size`/`n_rows`/`n_cols` immediately reflect the new extent, and a plain read
(`sheet.get_row(50)`, `sheet["Z1"].value` with no assignment) never grows anything — only a
write (`.value = ...`) triggers growth. New rows/cells don't inherit any particular style
(default formatting).

### Creating new sheets

`ODSReader.add_sheet(name)` creates a new, empty sheet and appends it after the last existing
one:

```python
sheet = table.add_sheet("Summary")
sheet.size            # (0, 0)
sheet["A1"].value = "Total"     # grows it like any other sheet, see above
```

Raises a `ValueError` for an empty name or one that's already used by another sheet in the
document. Like grown rows/cells, the new sheet carries no particular style.

### Writing formulas

`Cell.formula` is writable, just like `Cell.value`, and accepts ordinary spreadsheet syntax —
`A1`-style references, `$` for absolute rows/columns, ranges, `,`-separated function
arguments:

```python
sheet["C1"].formula = "A2+A3"           # or "=A2+A3" - leading '=' is optional
sheet["C1"].is_formula   # True
sheet["C1"].formula      # "of:=[.A2]+[.A3]" - normalized to ODF's own syntax
sheet["C1"].value        # None: no calculation engine, nothing computes a cached result

sheet["C2"].formula = "$A$2+$A$3"        # absolute references
sheet["C3"].formula = "SUM(A1:A3)"       # ranges
sheet["C4"].formula = "IF(A1>0,1,-1)"    # comma-separated arguments
```

Internally, ODF formulas don't use this syntax at all: a cell reference is `[.A1]` (not
`A1`), `;` separates function arguments (not `,`), and the whole expression is prefixed with
the formula language it's written in — `of:=...` for the default "OpenFormula" language.
Setting `.formula` translates ordinary syntax into that internal form: bare and `$`-anchored
references and ranges become `[.A1]`/`[.$A$2]`/`[.A1:.B3]`, and `,` argument separators
become `;` (commas inside quoted string literals, e.g. `"a,b"`, are left alone). Cross-sheet
references also work — `Sheet2.A1` or `'My Sheet'.A1:A3` become `[Sheet2.A1]`/
`['My Sheet'.A1:.A3]`. If the formula already contains a `[` it's assumed to already be
hand-written in ODF's own syntax (an escape hatch for anything the translation doesn't
cover) and is passed through unchanged, still with the `of:=` prefix added.

Assigning `None` clears the formula. Like `.value`, writing a formula auto-materializes
repeated/merged cells and auto-grows the sheet if needed, and writing either one clears the
other (a formula has no literal value, and vice versa).

There's no formula evaluator: the cell's `.value` reads back as `None` until a real
spreadsheet application (LibreOffice, etc.) opens the file and recalculates it — this
matches how ODF itself represents a formula with no cached result.

#### Writing the same pattern across several cells

`sheet[...]` (a slice, not a single cell) is also writable, for both `.value` and `.formula`:

```python
sheet["A1:A3"].value = 0                # broadcasts 0 to every cell in the range
sheet["A1:C1"].value = [1, 2, 3]        # element-wise, must match the selection's shape

sheet["A1:C1"].formula = "SUM(B1:B10)"  # broadcasts the exact same formula to every cell
```

`.formula` on a range can also use `{r}`/`{c}` placeholders, expanded **per cell** using that
cell's own 1-indexed row/column — so the same pattern produces a different (correctly
shifted) formula in each cell, the way dragging a formula's fill handle down a column does in
a real spreadsheet:

```python
sheet["A2:A10"].formula = "$A{r-1}+1"
# A2  -> "of:=[.$A1]+1"
# A3  -> "of:=[.$A2]+1"
# ...
# A10 -> "of:=[.$A9]+1"
```

A placeholder can hold a small arithmetic expression (`+`, `-`, `*`, `//`) over `r`/`c` —
`{r-1}`, `{c+2}`, `{r*10}`... — evaluated per cell before the usual ODF-syntax translation
runs; a pattern with no `{...}` is unaffected (so a single fixed formula broadcasts as-is,
same as the range example above). `{c}` is always a plain **column number** (1-indexed), not
a letter — write the column letter literally if it's fixed (as in the example above), or use
`{r}`/`{c}` only for the parts that actually vary from cell to cell.

Formula syntax that itself uses literal `{`/`}` — e.g. an array-constant literal like
`{1,2,3}` — would otherwise be read as a (invalid) placeholder. Escape it by doubling the
braces, exactly like `str.format`: the doubled braces become literal ones, and their content
is passed through completely untouched (no placeholder evaluation, no `,`-to-`;` translation
— write it in whichever syntax the target application expects):

```python
sheet["A1"].formula = "SUM({{1,2,3}})"   # -> "of:=SUM({1,2,3})"
```

The two features compose: a `{r}`/`{c}` placeholder and a `{{...}}`-escaped literal can appear
in the same pattern (the escaped part is set aside before templating runs, and restored
untouched afterwards) —

```python
sheet["A2"].formula = "$A{r-1}+{{1,2}}"   # A2 -> "of:=[.$A1]+{1,2}"
```

(a contrived example purely to demonstrate that the two mechanisms don't interfere with each
other — adding a cell to a 2-element array constant isn't a formula anyone would write for
real).

### Displayed text: learned from an example rather than a raw conversion

ODF doesn't just store a cell's value (`office:value`): it also stores the text as displayed
(`text:p`), typically formatted according to the document's locale (decimal separator,
`%`/`€` suffix, date format...). Rather than imposing an arbitrary format on write,
`odsslicer` looks for **another cell of the same format** in the document (preferring the
cell's own prior content if it already had a value), compares its raw value to its displayed
text to infer a pattern (decimal separator, decimal count, prefix/suffix, or a date pattern
like `%d/%m/%y` etc.), checks that the pattern reproduces the example exactly, then applies it
to the new value:

```python
sheet["A6"].text    # "200.00 %" (value 2.0)
sheet["A6"].value = 0.5
sheet["A6"].text    # "50.00 %" — same style as the cell's previous content

sheet["A8"].text    # "28/02/21" (day/month/2-digit-year format)
sheet["A8"].value = date(2030, 1, 5)
sheet["A8"].text    # "05/01/30"
```

If no example is found, or if the inferred pattern doesn't reproduce the example's text
exactly (and is therefore deemed unreliable), `odsslicer` falls back to a plain Python
conversion rather than producing incoherent text. For "general" numbers (plain `float`
format, not percentage/currency), only the decimal separator is reused — never the decimal
count, which would truncate the new value's precision.

### What is **not** supported

- No formula evaluation (see above). Named ranges and 3D references (a range spanning several
  sheets) aren't translated by the friendly formula syntax either — write them in ODF's own
  bracket syntax directly (the `[` escape hatch, see above).
- No real ODF formatting engine (resolving `styles.xml`, the document's locale, the actual
  currency): the inference above is a learn-by-example heuristic, not a read of the cell's
  style — it can silently fail (falling back to a plain conversion) for a format no other
  cell in the document already illustrates.

## Cell addressing

`Sheet.address(string, n_rows=1)` converts a text address into a Python index/slice:

| Notation      | Result                                 |
|---------------|------------------------------------------|
| `"A1"`        | `(0, 0)` — (row, col)                     |
| `"1"`         | `0` — single row                          |
| `"A"`         | `(slice(n_rows), 0)` — entire column       |
| `"A1:B3"`     | `(slice(0, 3), slice(0, 2))`               |
| `"A:B"`       | `(slice(n_rows), slice(0, 2))`             |
| `"1:2"`       | `slice(0, 2)`                              |

A malformed address (`"1A"`, `"A:2"`, `"2:A"`, `"B:A"`...) raises a `ValueError`.

`Sheet.string_address(row, col)` performs the reverse conversion (0-indexed index -> `"A1"`,
`"AZ12"`...) and `Sheet.string_to_col("AZ")` converts column letters to an index — both use
the usual spreadsheet bijective base-26 numbering (`Z` = 25, `AA` = 26, `AZ` = 51, `BA` =
52...).

## Known limitations

- **Writing**: see the detailed limitations in [Writing (experimental)](#writing-experimental)
  above.
- **Formulas**: the value cached by the spreadsheet is read (`office:value`), the formula
  itself is not re-evaluated (and writing obviously doesn't recompute anything either).
- Sheets/rows/columns beyond `MAX_REPEAT_ROWS` / `MAX_REPEAT_COLS` (see `src/odsslicer/classes.py`) are
  detected and discarded to avoid materializing rows or columns of size `2**20`/`2**10`
  created by LibreOffice for a sheet's default styling — a `[WARNING]` is printed if a row
  length inconsistency is detected after this cleanup.

## Tests

```bash
git clone https://github.com/antnardo/odsslicer.git
cd odsslicer
pip install -e ".[test]"
pytest
```

The suite (`tests/test_odsslicer.py`) covers addressing (`Sheet.address`,
`Sheet.string_address`/`string_to_col`), cell types, repeated and merged rows/columns, empty
sheets, `ArrayValues`, writing (`Cell.value = ...`, `ODSReader.save()` and its safeguards), as
well as regression tests for the fixed bugs (see below). It runs on every push/PR via
[GitHub Actions](.github/workflows/ci.yml) across Python 3.10 to 3.13.

## Are there already equivalent PyPI modules?

| Package | Read/Write | Latest release | Status |
|---|---|---|---|
| `odfpy` | Low-level R/W | Jan. 2020 | Nearly abandoned (82 open issues), but still the brick pandas uses internally |
| `pyexcel-ods(3)` | R/W | > 1 year | Inactive |
| `ezodf` | R/W | Dec. 2015 | Abandoned for 10 years |
| `pandas` (`engine="odf"`) | Read (delegates to odfpy) | follows pandas | Convenient but loses formulas/fine-grained formats |
| `python-calamine` | Read-only (Rust), fast | active | The most actively maintained option for pure reading |
| `odfdo` (modern fork of odfpy) | Full R/W | recent, regular | Actively maintained, DOM-like API |
| `pandas-ods-reader` | Read-only -> DataFrame | May 2025 | Maintained, limited scope |

None of these packages offer a numpy-style API (`sheet["A1"]`, slicing by cell address) or the
same granularity on cell formats (currency/percentage/date/time) and merged/repeated cells —
that's the main argument for publishing this module rather than simply recommending `odfdo`
or `python-calamine`.

## Versions

Version numbers are derived automatically from git tags (nothing to bump by hand in the
source) and follow [Semantic Versioning](https://semver.org/) — while the major version stays
`0`, the API can still change between minor versions. See the
[Releases](https://github.com/antnardo/odsslicer/releases) page for the changelog of each
version.

## License

[MIT](LICENSE) — reuse with essentially no restriction, just keep the copyright notice.

## Project name

Chosen name: **`odsslicer`** (available on PyPI as of 2026-07-29), to reflect the module's
real differentiator — numpy-style indexing/slicing by cell address — rather than a generic
"ods reader".

## Notable bug fixes

The following bugs were found and fixed in `src/odsslicer/classes.py` while developing this
module (most of them while rereading the original internal version ahead of its first
release):

1. **`Sheet.string_address`** produced a wrong address for most multi-letter columns (e.g.
   column 27 → `"BB1"` instead of `"AB1"`, column 51 → `"ZZ1"` instead of `"AZ1"`) due to a
   poorly implemented base-26 numbering. Fixed with the standard bijective numbering
   algorithm.
2. **`Sheet.get_col`** compared the requested column index to `self.n_rows` instead of
   `self.n_cols` to detect an out-of-range access: on any sheet with more rows than columns,
   requesting an out-of-range column raised an `IndexError` instead of returning an empty
   column.
3. The `[WARNING]` for rows of differing lengths **never** fired, even in the presence of a
   genuine inconsistency: `rows_len` was a `map` iterator already exhausted once by `max()`,
   hence empty on the second read used to compute the warning.
4. **`Sheet.empty_row` / `Sheet.empty_col`**, when explicitly passed the `slice` argument,
   returned one fewer element than expected (an element count was recomputed and then
   mistakenly reused as a `range` stop bound).
5. **`ODSReader.sheets`** was a single-use generator property (couldn't `len()` it or iterate
   it twice), with dead code after the `yield` that could never execute. Replaced with a
   plain, reusable list.
6. `Cell.__floot__` (a typo for `__floor__`) was fixed — with no observed functional impact
   (Python fell back to `__float__` for `math.floor()`), but it kept a misleading name.
7. **Reading boolean cells**: the `"boolean"` format looked up the value in `office:value`
   (like numbers) instead of the actual ODF attribute `office:boolean-value`, and converted
   it with `bool(s)` — which returns `True` for the non-empty string `"false"`. A real ODF
   boolean cell therefore always read back as `False`. Fixed (reading and writing are now
   symmetric for this format).
8. **`Cell.text`/`str(cell)` returned the literal string `"None"`** instead of the actual
   text, in two common cases: a cell whose `<text:p>` is empty (typically a formula whose
   cached result is an empty string) and a cell whose text is spread across several nodes
   (`<text:span>` for partial formatting, e.g. "1st" with "st" as superscript). In both
   cases, `text:p.string` (bs4) is `None` whenever there isn't *exactly* one text child, and
   the old code did `str(p.string)`, turning that `None` into the string `"None"`. Fixed by
   using `p.get_text()`, which correctly concatenates all descendant text (and returns `""`
   for a genuinely empty cell).
9. **Growing an empty sheet (or one whose XML has a trailing empty row) could corrupt it on
   the next save/reload.** `load()` discards some rows from its in-memory view (a lone blank
   row on an "empty" sheet, a trailing empty row) but never removes the corresponding
   `<table:table-row>` from the underlying XML. `Sheet.grow_to` appended new rows after
   whatever was physically there without accounting for this, so those still-present rows
   would resurface as an extra, wrongly-shaped row once the file was saved and re-parsed.
   Fixed: any such stray row is now discarded first.
10. **A cell holding only a formula (no cached value/text) was wrongly treated as
    `is_empty`.** `Cell.is_empty` never accounted for `.formula`, only for value/text/format.
    If such a cell ended up as a sheet's last row, `load()`'s "trim a trailing empty row"
    cleanup silently dropped it — a formula written near the edge of a sheet could vanish on
    the next save/reload. Fixed: `is_empty` now also checks the formula.

All of these cases are covered by regression tests in `tests/test_odsslicer.py`.
