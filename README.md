# odsslicer

[![CI](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml/badge.svg)](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml)

Python reader for `.ods` files (OpenDocument Spreadsheet, LibreOffice/OpenOffice Calc), with a
numpy-inspired indexing API: `sheet["A1"]`, `sheet[0, 0]`, `sheet["A1:B3"]`, plain Python
slices, etc.

The module parses `content.xml` directly (via BeautifulSoup) and handles ODF cell types
(text, number, percentage, currency, date, time, boolean), formulas, as well as repeated and
merged rows/columns.

Write support: `cell.value = ...`, `cell.formula = ...`, `cell.style.bold = ...` (and other
formatting properties, including creating number formats and conditional formatting from
scratch), `cell.comment = ...`, `cell.hyperlink = ...`, `sheet.merge(...)`/`.unmerge(...)`, `sheet.copy(...)`,
`sheet.sort(...)`, `sheet.create_pivot_table(...)`, `sheet.delete_row(...)`/`.delete_column(...)`/`table.delete_sheet(...)`,
`table.rename_sheet(...)`/`.move_sheet(...)`, `table.properties` (title, author, custom
document properties), new sheets, even brand new files from scratch — then `reader.save(...)`.
Repeated or merged cells are automatically unrolled/unmerged in the
background on first write access, and writing beyond a sheet's current extent grows it
automatically (new rows/columns) — see [Writing](#writing-experimental) below for details and
remaining limitations.

## Installation

```bash
pip install odsslicer
```

Or install straight from GitHub, or clone it and install it editable (for local development):

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

### Merged cells

A cell's merge state is directly readable, without triggering the automatic un-merging above:

```python
cell.is_merged           # True for either the master or one of the hidden/covered cells
cell.is_merge_master     # True only for the top-left cell of the range
cell.is_covered          # True only for a hidden `table:covered-table-cell`
cell.merge_master        # the top-left Cell of the range, from any cell in it - or None
cell.merge_span          # (n_rows, n_cols), or None
cell.merge_range         # "A1:C2"-style address string, or None
```

`Sheet.merge(address)` merges a rectangular selection (any address `sheet[...]` accepts, e.g.
`"A1:C2"`) into one cell: the top-left cell becomes the master and keeps its value; every
other cell becomes a hidden `table:covered-table-cell` — nothing is erased, its value/
formatting just stops showing, exactly like `unmerge` (below) expects to find it. Grows the
sheet first if needed; raises `ValueError` for a single-cell range or if any cell in it is
already part of a merge.

`Sheet.unmerge(address)` undoes the merge covering `address` (any single cell in the range,
master or covered) — every cell becomes independent again and reveals whatever value ODF was
keeping hidden underneath it. Raises `ValueError` if `address` isn't a single cell, or isn't
part of any merge.

```python
sheet.merge("A1:C2")
sheet["A1"].merge_range     # "A1:C2"
sheet.unmerge("B2")         # any cell in the range works, not just the master
```

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

### Renaming and reordering sheets

```python
table.rename_sheet("Sheet1", "Q4 Budget")
table.move_sheet("Q4 Budget", 0)     # make it the first tab
```

`rename_sheet` also rewrites any formula elsewhere in the document that references the sheet
by name — `OldName.A1` becomes `NewName.A1` (quoted, `'New Name'.A1`, if the new name needs
it) — an unqualified reference within the renamed sheet's own formulas (`.A1`, meaning "this
sheet") needs no rewrite, since it already means the same thing regardless of what it's named.
Raises `IndexError` for an unknown `old_name`, and `ValueError` for an empty `new_name` or one
already used by another sheet.

`move_sheet(name, index)` moves a sheet to position `index` (0-based) among the document's
sheets, shifting the others. Raises `IndexError` for an unknown name, and `ValueError` if
`index` is out of range.

### Deleting rows, columns and sheets

```python
sheet.delete_row(3)        # shifts every row below it up by one
sheet.delete_column(0)     # shifts every column to its right left by one
table.delete_sheet("Data")
```

Any merge intersecting the removed row/column is undone first (see [Merged
cells](#merged-cells) below) rather than left with a now-wrong span — there's no general way to
"shrink" a span by one row/column instead. `delete_row`/`delete_column` raise `IndexError` for
an out-of-range index; `delete_sheet` raises `IndexError` for an unknown name and `ValueError`
for the document's last remaining sheet (an ODF spreadsheet needs at least one).

`delete_row`/`delete_column` also adjust formula references — every formula in the whole
document that points into the affected sheet (that sheet's own formulas, and any other sheet's
formula explicitly qualified with its name, e.g. `Sheet1.A6`) is rewritten so a reference past
the removed row/column still points at the same cell it did before:

```python
sheet["C5"].formula = "A6+A7"
sheet.delete_row(3)             # above both A6 and A7 - both shift up by one
sheet["C4"].formula_friendly    # "=A5+A6" - C5's own content, now at C4
```

A reference that pointed *exactly* at the removed row/column is left as-is rather than modeled
as a `#REF!`-style error — there's no error-value concept in `odsslicer` (see [Writing
formulas](#writing-formulas) below) — e.g. deleting the first row of a `SUM(A2:A3)` range
shrinks it to `SUM(A2:A2)` rather than raising or guessing. Since there's no calculation
engine, any formula whose text actually changes has its cached displayed value cleared (same
as any other write to `.formula`) — it'll show blank until the file is next opened in a real
spreadsheet application.

### Copying cells and ranges

`Sheet.copy(source, dest)` copies a cell or rectangular range onto `dest` (its top-left
corner), like a spreadsheet's copy-paste — value, formula (shifted the same way `Cell.
fill_formula` shifts it: a relative reference like `A1` moves with the copy, `$A$1` stays put),
and style all come along:

```python
sheet.copy("A1", "C1")           # single cell
sheet.copy("A1:B2", "D5")        # a whole range, same shape at the new anchor
```

Grows the sheet first if `dest` extends past its current extent, and is safe when `source` and
`dest` overlap (every source cell is read before any destination cell is written). A merged
source cell copies whatever value/style it individually carries (its own hidden value, if it's
a covered cell) — the merge itself is not replicated at the destination.

### Sorting a range

`Sheet.sort(source, by, ascending=True)` sorts the rows of `source` (a range address) in
place, by the values in column `by` (an absolute column index within `source`):

```python
sheet.sort("A2:C10", by=1)                   # sort rows 2-10 by column B, ascending
sheet.sort("A2:C10", by=1, ascending=False)
```

A stable sort — rows with equal keys keep their relative order — and `None` always sorts last
regardless of `ascending`, matching a real spreadsheet's usual treatment of blanks. Each row's
value/formula/style moves together as a unit; a formula's references shift by that row's own
displacement (same relative-reference semantics as `Cell.fill_formula`/`Sheet.copy` — a
`$`-anchored reference stays put), so a same-row formula like `=B2*C2` still refers to its own,
now-relocated row afterwards. Raises `ValueError` if `by` falls outside `source`'s columns. A
merged cell within `source` moves only its own raw content, same caveat as `Sheet.copy`.

### Creating a new file from scratch

`ODSReader.new(sheet_name="Sheet1")` creates a brand new, empty spreadsheet — not backed by
any file on disk yet — with a single sheet:

```python
from odsslicer import ODSReader

table = ODSReader.new()                 # or ODSReader.new(sheet_name="Budget")
sheet = table.sheet("Sheet1")
sheet["A1"].value = "Total"
sheet["B1"].formula = "SUM(A2:A10)"
table.add_sheet("Data")

table.save("new_workbook.ods")          # a path is required: there's no source file to default to
```

A valid, empty ODF document needs several non-trivial pieces beyond `content.xml` — a
`mimetype`, `META-INF/manifest.xml`, `styles.xml`, `meta.xml`, `settings.xml` — that only a
real spreadsheet application can produce correctly, so `.new()` is bootstrapped from a
minimal template bundled with the package rather than hand-assembled. Everything else
(writing, growing, formulas, adding sheets) works exactly the same as on a document opened
from an existing file.

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

#### Reading a formula back in ordinary syntax

`.formula` always returns the raw ODF form, `[.A1]`/`;` and all — including for formulas
already present in a file you didn't write yourself, which can get gnarly fast (a real
example, straight from a spreadsheet used for grading):

```python
cell.formula
# 'of:=IF(OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1])=0;"";OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1]))'
```

`.formula_friendly` translates that back into ordinary syntax for readability — the exact
reverse of what `.formula = "..."` accepts on write:

```python
cell.formula_friendly
# '=IF(OFFSET($Notes.$C$3,I$1,$A3+$A$1)=0,"",OFFSET($Notes.$C$3,I$1,$A3+$A$1))'
```

`None` if the cell has no formula. This is read-only and best-effort: a construct the
write-side translation doesn't cover either (a named range, an unusual reference shape) is
passed through untranslated rather than guessed at.

#### Filling a formula across a range

`Cell.fill_formula(target)` copies a cell's formula into every cell of `target`, shifting
relative references the way a spreadsheet's fill handle does when you drag a formula across
cells — a `$`-anchored (absolute) reference stays put on whichever axis it locks, regardless
of direction:

```python
sheet["B2"].formula = "$A1+1"
sheet["B2"].fill_formula("B3:B10")
# B3 -> "=$A2+1", B4 -> "=$A3+1", ..., B10 -> "=$A9+1"
```

`target` can be a sheet address string (resolved on the source cell's own sheet) or a
selection (`sheet["B3:B10"]`), and works in any direction — down, right, or across a 2D
block — since the shift for each target cell is just the difference between its own
position and the source cell's. Raises `ValueError` if the source cell has no formula, or if
a shifted reference would fall off the sheet (e.g. filling upward past row 1).

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

### Pivot tables

`Sheet.create_pivot_table(source, target, rows=..., columns=..., values=..., name=...)` writes
a pivot table's ODF definition ("data pilot table" in ODF terms) — same philosophy as formulas:
`odsslicer` describes what to compute, a real spreadsheet application computes it:

```python
# source data with a header row: Category | Region | Amount
sheet.create_pivot_table(
    "A1:C100",                     # source range (first row = field headers)
    "E1",                          # top-left of where the result will go
    rows=["Category"],             # row categories
    columns=["Region"],            # column categories
    values={"Amount": "sum"},      # aggregated field -> function
)
```

`source` may be sheet-qualified (`"Data.A1:C100"`) to pull from another sheet. Valid
aggregation functions: `"sum"`, `"average"`, `"count"`, `"countnums"` (count of numeric values
only), `"max"`, `"min"`, `"product"`, `"stdev"`, `"stdevp"`, `"var"`, `"varp"`. `name`
defaults to `"DataPilotTable{n}"`. Raises `ValueError` for a field name not found in the
source's header row, an unknown function, or a name already in use.

**Unlike a formula, a pivot table is not recomputed automatically on open.** Every conformant
reader recalculates formulas when it loads a file; a pivot table, by contrast, needs an
explicit refresh (Data > Pivot Table > Refresh in LibreOffice) before its result appears at
`target` — confirmed against a real LibreOffice: a file holding only the definition opens fine,
the definition is fully recognized and editable from the pivot UI, but the target area stays
empty until refreshed. `odsslicer` writes only the definition, never a computed grid (no
calculation engine), so expect that one refresh after opening.

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
exactly (and is therefore deemed unreliable), `odsslicer` falls back to reading the cell's own
real, resolved `NumberFormat` directly (see [Styles](#styles) above) — decimal places,
thousands grouping, currency symbol, or a date/time layout from `.components` — genuinely
reading the document's format rather than guessing at it:

```python
table = ODSReader.new()             # a blank document - nothing anywhere to learn from
sheet = table.sheet("Sheet1")
fmt = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="$", grouping=True)
sheet["A1"].style.number_format = fmt
sheet["A1"].value = 1234.5
sheet["A1"].text                     # "1,234.50 $" - read from the real format, not guessed
```

This second layer renders with a plain `.`/`,` (decimal/grouping) convention, since a
`NumberFormat` doesn't capture the document's actual locale the way a real example's text
does — real spreadsheet applications recompute the display text from the format on open
anyway, so this cached text is mostly relevant to `odsslicer`'s own `.text` reads. Only if
*neither* layer applies (no example anywhere, and the cell has no resolvable format either)
does it fall back to a plain Python conversion. For "general" numbers (plain `float` format,
not percentage/currency) the first layer only ever reuses the decimal separator — never the
decimal count, which would truncate the new value's precision.

### What is **not** supported

- No formula evaluation (see above). Named ranges and 3D references (a range spanning several
  sheets) aren't translated by the friendly formula syntax either — write them in ODF's own
  bracket syntax directly (the `[` escape hatch, see above).
- The displayed-text inference above doesn't capture the document's actual locale (see
  [Displayed text](#displayed-text-learned-from-an-example-rather-than-a-raw-conversion)) -
  its two layers can each produce a different decimal/thousands separator convention than the
  rest of the document when they apply.

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

## Styles

`Cell.style` resolves a cell's actual formatting (as opposed to `odsslicer`'s own
value/text/format detection above) and is writable — `None` only if the cell has no owning
`ODSReader` at all; a cell with no `table:style-name` yet still returns a `CellStyle` (every
property `None`/`False`) that a write turns into a real one. All the classes below are also
importable directly from the top-level package (`from odsslicer import CellStyle,
NumberFormat, Border, RowStyle, ColumnStyle, TableStyle`), e.g. for type hints:

```python
sheet["A7"].style.bold                            # False
sheet["A7"].style.background_color                # None, or e.g. "#ffdbb6"
sheet["A7"].style.border_top                       # None, or a Border("0.74pt solid #808080")
sheet["A7"].style.number_format.family            # "currency"
sheet["A7"].style.number_format.currency_symbol   # "€"
sheet["A7"].style.number_format.decimal_places    # 2
```

ODF splits a cell's formatting across two files and two concerns: a `table:style-name`
points to a `<style:style>` element (living in either `content.xml`'s per-document
"automatic styles", or `styles.xml`'s reusable named styles like `"Good"`/`"Error"`), which
carries the cell's *visual* look directly, and, separately, a `style:data-style-name`
pointing to a `<number:*-style>` element (again in either file) for the cell's real
*display format*.

`CellStyle`'s visual properties: font `.bold`/`.italic`/`.underline`/`.strikethrough`
(booleans), `.font_family`, `.font_size`, `.font_color`, `.superscript`/`.subscript`
(booleans derived from `.text_position`, e.g. `"super 58%"`); `.background_color`;
`.border_top`/`.border_bottom`/`.border_left`/`.border_right` and `.diagonal_bl_tr`/
`.diagonal_tl_br` (each a `Border` with `.width`/`.style`/`.color`, resolved from ODF's
`fo:border`/diagonal shorthand or a specific side, whichever the nearest style in the chain
defines — ODF's literal `"none"` correctly resolves to `None`, not a `Border("none")`);
`.horizontal_align`/`.vertical_align`; `.rotation` (degrees, or `None`); `.wrap_text`,
`.shrink_to_fit` (booleans); `.writing_mode` (e.g. `"lr-tb"`/`"rl-tb"`); `.protection` (raw
`style:cell-protect` value, e.g. `"protected"`, or `None`).

`.number_format` has `.family` (`"percentage"`/`"currency"`/`"date"`/`"time"`/`"number"`/
`"boolean"`/`"text"`), `.decimal_places`, `.grouping`, `.currency_symbol`, `.font_color`
(a number format can carry its own text color, e.g. red for negative currency — distinct
from `CellStyle.font_color`), and for date/time styles `.components` (the ordered layout,
e.g. `[("day", "long"), ("text", "/"), ("month", "long"), ...]`).

A number format can be **conditional** — e.g. a currency format showing negative amounts in
red via a different sub-format for positive ones:

```python
sheet["A7"].value                              # 2.0
sheet["A7"].style.number_format.name           # "N108P0" - already resolved for this value
sheet["A7"].style.number_format.font_color     # None (only the negative variant is red)
```

`Cell.style.number_format` is automatically resolved against the cell's own value. To inspect
the underlying conditions or resolve against a different hypothetical value, use
`.conditions` (a list of `(condition, NumberFormat)` pairs in document order) and
`.resolve(value)` on the base `NumberFormat` (`ODSReader._find_number_style(name)` plus
`NumberFormat(tag, reader=reader)` gets it directly). Only the common comparison subset of
ODF's condition language is understood (`"value()>=0"`, `"value()<100"`...); an unsupported
condition (a cell-content-is-text() check, a between-range...) is simply never matched, so
`.resolve()` falls back to the base format rather than guessing.

A style can inherit from another via `style:parent-style-name`; `Cell.style` walks that chain
so an inherited property still resolves (the nearest style in the chain wins for any
property more than one defines — borders/diagonals are each resolved as a whole unit from the
nearest style that defines any border info at all, rather than mixing individual sides across
levels). `.cell_properties`/`.text_properties` expose the raw, flattened attribute dicts as
an escape hatch for anything not surfaced as a named property above.

### Writing cell styles

Every property listed above except `.conditions` is writable:

```python
sheet["A1"].style.bold = True
sheet["A1"].style.font_color = "#FF0000"
sheet["A1"].style.background_color = "#FFFF00"
sheet["A1"].style.border_top = "0.5pt solid #000000"   # a Border also works
sheet["A1"].style.horizontal_align = "center"
sheet["A1"].style.wrap_text = True
```

The first write on a given cell forks it its own private automatic style — off the cell's
current style as `style:parent-style-name`, so every other already-resolved property (from
the old, possibly shared, style) keeps applying — and reuses that same forked style for every
later write on the same cell, so setting several properties one after another never affects
any other cell that used to share the original style:

```python
sheet["A1"].attrs.get("table:style-name")   # None, or some shared style like "ce9"
sheet["A1"].style.bold = True
sheet["A1"].attrs.get("table:style-name")   # "ocs1" - a new style, private to A1
sheet["A1"].style.italic = True             # reuses "ocs1", doesn't fork again
```

Copy a whole style from one cell to another in one shot by assigning `Cell.style` itself:

```python
sheet["B1"].style = sheet["A1"].style   # or = sheet["A1"], or = "ce9" (a raw style name)
sheet["B1"].style = None                # clears B1's style
```

This points the target at the *same* underlying style as the source rather than deep-copying
its properties — safe even if that style is shared with many other cells, since setting an
individual property later (`sheet["B1"].style.bold = True`) forks a private copy on the spot,
same as above, without affecting the source or anything else that still uses it.

`border_top`/`border_bottom`/`border_left`/`border_right` accept a `Border`, a raw ODF
shorthand string (`"0.5pt solid #000000"`), or `None`. Because the 4 sides resolve as one
block from a single style (see above), setting just one side also re-writes the other three
explicitly from whatever's currently resolved, so they're never silently lost; `None` cancels
a side outright (writes literal `"none"`, same as ODF itself uses to override an inherited
border). `diagonal_bl_tr`/`diagonal_tl_br` resolve independently instead, so `None` there just
removes the override (falls back to whatever's inherited) — pass the string `"none"` for an
explicit "no diagonal regardless of inheritance".

`number_format` can be *assigned* an existing `NumberFormat` already present in the document
(or its style name, as a plain string), or one built from scratch with `NumberFormat.create`:

```python
sheet["B1"].style.number_format = sheet["A7"].style.number_format   # or = "N108"

pct = NumberFormat.create(table, "percentage", decimal_places=1)
sheet["C1"].style.number_format = pct
```

`create(reader, family, ...)` supports `family="number"`/`"percentage"`/`"currency"`/`"date"`/
`"time"`/`"boolean"`: `decimal_places`/`grouping`/`min_integer_digits` for the numeric
families, `currency_symbol` (required for `"currency"`), `components` (required for `"date"`/
`"time"` — the same ordered `[(component, style_or_text), ...]` list `.components` itself
already exposes on read), and `font_color` for any family.

Conditional formatting (`.conditions`) is writable too, via `.add_condition(condition, target)`
on an existing `NumberFormat` — `target` (another `NumberFormat`, already in the document)
applies instead whenever `condition` (ODF's `"value()>=0"`-style syntax) matches, same as
`.resolve(value)` already reads. The classic red-negative-currency pattern:

```python
positive = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="€")
negative = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
positive.add_condition("value()<0", negative)
sheet["A1"].style.number_format = positive
```

### Row, column and sheet styles

`Sheet.row_style(row)` / `Sheet.column_style(col)` / `Sheet.style` resolve the same way, for
whichever little formatting ODF attaches at those levels — row/column styles don't chain via
`style:parent-style-name` in practice, so these don't walk an inheritance chain:

```python
sheet.row_style(0).height        # e.g. "0.452cm", or None
sheet.column_style(0).width      # e.g. "2.258cm", or None
sheet.style.tab_color            # the sheet tab's color, or None
```

`RowStyle`: `.height`, `.optimal_height` (bool), `.visible` — all writable. `ColumnStyle`:
`.width`, `.visible` — writable. `TableStyle`: `.tab_color`, `.visible` — writable. Any of the
three is `None` if it's out of range (rows/columns) or the `Sheet` has no owning `ODSReader`
(e.g. one built directly from a bare tag rather than via `ODSReader.sheet()`/`.add_sheet()`).

```python
sheet.row_style(0).height = "1cm"
sheet.column_style(0).width = "5cm"
sheet.style.tab_color = "#FF0000"
```

Like cell styles, the first write on a given row/column/sheet forks it a private automatic
style, reused on every later write on the same row/column/sheet — but since these don't chain
via `style:parent-style-name`, the fork instead copies the current style's properties over
verbatim, so setting only `.height` doesn't silently reset `.visible`/`.optimal_height` to
their defaults.

`odsslicer`'s own value/text heuristics (see [Writing formulas](#writing-formulas) and
[Displayed text](#displayed-text-learned-from-an-example-rather-than-a-raw-conversion) above)
don't consult `.number_format` either way — they still learn from another cell's example
rather than reading or writing the real format.

## Document properties

`ODSReader.properties` gives structured, writable access to `meta.xml` — the document
properties behind LibreOffice's "File > Properties" dialog:

```python
props = table.properties
props.title              # None, or e.g. "Q4 Budget"
props.subject
props.description
props.creator             # who last saved it (dc:creator)
props.initial_creator     # who originally created it (meta:initial-creator)
props.generator           # the app that last saved it, e.g. "LibreOffice/25.8..." - read-only
props.keywords            # a list, e.g. ["budget", "2026"]

props.title = "Q4 Budget"
props.keywords = ["budget", "2026"]     # replaces the whole list
props.title = None                       # clears the field
```

Arbitrary custom properties (`meta:user-defined`) are available dict-style:

```python
props["Client"] = "Acme Corp"
props["Amount"] = 42.5
props["Approved"] = True
props["Due"] = date(2026, 12, 31)
props["Client"]           # "Acme Corp"
"Client" in props          # True
del props["Client"]
props.custom               # a dict snapshot of every custom property
```

A custom property's Python type round-trips through ODF's own `meta:value-type` (`str`/
`float`/`bool`/`datetime.date`) — assigning any other type raises `TypeError`. Both
`content.xml` and `meta.xml` are regenerated from their in-memory trees on `save()`; every
other zip member (`styles.xml`, `settings.xml`...) is still copied through unchanged from the
source file.

## Cell comments

`Cell.comment` reads a cell's note (`office:annotation`) — `None` if it has none:

```python
cell.comment              # None, or a Comment
cell.comment = "Follow up with finance"      # creates one (or replaces an existing one's text)
cell.comment.text          # "Follow up with finance"
cell.comment = None         # removes it
```

Once a comment exists, set its other properties directly:

```python
cell.comment.author = "Antonin"
cell.comment.date = datetime.now()
cell.comment.visible = True     # pinned open, rather than only shown on hover
```

`.text` joins ODF's own multiple `text:p` paragraphs with `\n` on read, and splits on `\n`
back into separate paragraphs on write, so a multi-line note round-trips correctly.

## Cell hyperlinks

`Cell.hyperlink` reads a cell's link URL (`xlink:href` on a `<text:a>` wrapping the cell's
whole text) — `None` if it has none:

```python
cell.hyperlink                         # None, or a URL
cell.hyperlink = "https://example.com"  # wraps the cell's current text in a link
cell.hyperlink = None                   # unwraps it, leaving the plain text in place
```

Setting a hyperlink on an empty cell gives it empty text to wrap first. Only a whole-cell link
is supported — a link on just part of the text, mixed with plain text, isn't modeled. Writing
a new `.value` afterwards replaces the cell's text (link included), same as it always does —
the link isn't carried over, since it was tied to that specific text.

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

`tests/test_libreoffice_consistency.py` is a separate, opt-in suite that hands files
`odsslicer` wrote to a real, local LibreOffice (`soffice --headless --convert-to fods`) and
inspects what LibreOffice itself made of them — the strongest available signal that a write
(a style fork, a merge, a formula...) is genuinely valid ODF, not just something our own
BeautifulSoup-based reader happens to parse back. It skips automatically if no
`soffice`/`libreoffice` binary is on `PATH` (not installed in CI by default) — install
LibreOffice locally to exercise it.

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
module.

1. **Reading boolean cells**: the `"boolean"` format looked up the value in `office:value`
   (like numbers) instead of the actual ODF attribute `office:boolean-value`, and converted
   it with `bool(s)` — which returns `True` for the non-empty string `"false"`. A real ODF
   boolean cell therefore always read back as `False`. Fixed (reading and writing are now
   symmetric for this format).
2. **`Cell.text`/`str(cell)` returned the literal string `"None"`** instead of the actual
   text, in two common cases: a cell whose `<text:p>` is empty (typically a formula whose
   cached result is an empty string) and a cell whose text is spread across several nodes
   (`<text:span>` for partial formatting, e.g. "1st" with "st" as superscript). In both
   cases, `text:p.string` (bs4) is `None` whenever there isn't *exactly* one text child, and
   the old code did `str(p.string)`, turning that `None` into the string `"None"`. Fixed by
   using `p.get_text()`, which correctly concatenates all descendant text (and returns `""`
   for a genuinely empty cell).
3. **Growing an empty sheet (or one whose XML has a trailing empty row) could corrupt it on
   the next save/reload.** `load()` discards some rows from its in-memory view (a lone blank
   row on an "empty" sheet, a trailing empty row) but never removes the corresponding
   `<table:table-row>` from the underlying XML. `Sheet.grow_to` appended new rows after
   whatever was physically there without accounting for this, so those still-present rows
   would resurface as an extra, wrongly-shaped row once the file was saved and re-parsed.
   Fixed: any such stray row is now discarded first.
4. **A cell holding only a formula (no cached value/text) was wrongly treated as
    `is_empty`.** `Cell.is_empty` never accounted for `.formula`, only for value/text/format.
    If such a cell ended up as a sheet's last row, `load()`'s "trim a trailing empty row"
    cleanup silently dropped it — a formula written near the edge of a sheet could vanish on
    the next save/reload. Fixed: `is_empty` now also checks the formula.
5. **Writing to a sheet that is the only one in the whole document, with nothing anywhere
    else to copy a namespace template from, failed outright.** Building `ODSReader.new()`'s
    bundled template surfaced two related bugs in the "copy an existing tag" approach used to
    create new XML elements: `Sheet.grow_to` discarded a sheet's own lone "phantom" blank row
    (see #9) *before* it could be used as a row/cell template, and `Cell._set_text` had
    nothing at all to copy a `text:p` from on such a minimal sheet. Both now fall back to
    building a correctly namespace-qualified element from scratch (using the standard OASIS
    namespace URIs directly) instead of raising `NotImplementedError`.

All of these cases are covered by regression tests in `tests/test_odsslicer.py`.
