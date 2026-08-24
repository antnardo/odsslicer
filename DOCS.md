# odsslicer — complete API reference

This is the full, feature-by-feature reference for `odsslicer`, with a usage example for every
feature. For a short overview, installation, and the comparison with other packages, see the
[README](README.md).

Everything below assumes:

```python
from odsslicer import ODSReader, NumberFormat
from datetime import date, time, datetime

table = ODSReader("workbook.ods")
sheet = table.sheet("Sheet1")
```

---

## Table of contents

1. [Reading](#1-reading)
   - [Opening a file, sheets](#opening-a-file-sheets)
   - [Indexing and slicing](#indexing-and-slicing)
   - [Cells (`Cell`)](#cells-cell)
   - [Arrays (`ArrayValues`)](#arrays-arrayvalues)
   - [Iteration](#iteration)
   - [Address conversion helpers](#address-conversion-helpers)
2. [Writing values](#2-writing-values)
   - [`Cell.value` and `save()`](#cellvalue-and-save)
   - [Writing a range at once](#writing-a-range-at-once)
   - [Automatic unrolling of repeated and merged cells](#automatic-unrolling-of-repeated-and-merged-cells)
   - [Automatic sheet growth](#automatic-sheet-growth)
   - [Displayed text: how `.text` is produced on write](#displayed-text-how-text-is-produced-on-write)
3. [Files and sheets](#3-files-and-sheets)
   - [Creating a new file from scratch](#creating-a-new-file-from-scratch)
   - [Adding, renaming, reordering, deleting sheets](#adding-renaming-reordering-deleting-sheets)
4. [Rows, columns and ranges](#4-rows-columns-and-ranges)
   - [Deleting rows and columns](#deleting-rows-and-columns)
   - [Copying cells and ranges](#copying-cells-and-ranges)
   - [Sorting a range](#sorting-a-range)
   - [Merged cells](#merged-cells)
5. [Formulas](#5-formulas)
   - [Writing a formula](#writing-a-formula)
   - [Reading a formula back in ordinary syntax](#reading-a-formula-back-in-ordinary-syntax)
   - [Filling a formula across a range](#filling-a-formula-across-a-range)
   - [Formula templates with `{r}`/`{c}`](#formula-templates-with-rc)
   - [Formula references follow structural edits](#formula-references-follow-structural-edits)
6. [Pivot tables](#6-pivot-tables)
7. [Recalculating with LibreOffice](#7-recalculating-with-libreoffice)
8. [Styles](#8-styles)
   - [Reading a cell's style](#reading-a-cells-style)
   - [Writing cell styles](#writing-cell-styles)
   - [Copying a style from one cell to another](#copying-a-style-from-one-cell-to-another)
   - [Borders](#borders)
   - [Number formats](#number-formats)
   - [Conditional number formats](#conditional-number-formats)
   - [Row, column and sheet styles](#row-column-and-sheet-styles)
9. [Cell comments](#9-cell-comments)
10. [Cell hyperlinks](#10-cell-hyperlinks)
11. [Document properties](#11-document-properties)
12. [Known limitations](#12-known-limitations)
13. [Appendix: notable bug fixes](#13-appendix-notable-bug-fixes)

---

## 1. Reading

### Opening a file, sheets

```python
from odsslicer import ODSReader
from pathlib import Path

table = ODSReader(Path("workbook.ods"))   # a str path works too
table.sheets_names        # ["Sheet1", "Sheet2", ...]
table.sheets               # list of Sheet (cached, reusable)
sheet = table.sheet("Sheet1")
sheet.size                  # (n_rows, n_cols)
sheet.name                  # "Sheet1"
```

`ODSReader` parses `content.xml`, `styles.xml` and `meta.xml` (via BeautifulSoup/lxml) into
in-memory trees. `ODSReader.sheet(name)` raises `IndexError` for an unknown name.

### Indexing and slicing

The API is numpy-inspired — **row first, column second**, 0-indexed — and also accepts
spreadsheet-style addresses:

```python
sheet["A1"]                # cell A1 (a Cell)
sheet[0, 0]                 # equivalent: (row, col), 0-indexed
sheet[0]                    # entire row 1 (same as sheet["1"])
sheet[:, 0]                  # entire column A (same as sheet["A"])
sheet["A1:B3"]               # block, equivalent to sheet[0:3, 0:2]
sheet["A:B"]                 # columns A and B, all rows
sheet["1:2"]                 # rows 1 and 2, all columns

sheet["ZZZ100000"]          # outside the data: an empty cell (value=None), no error
```

An address or slice outside the data always returns empty cells (`value=None`) of the correct
shape, rather than an error — the shape follows the same conventions as numpy (a (n, 1) column
stays 2D; see `to_vector()` below to flatten it).

### Cells (`Cell`)

```python
cell = sheet["A1"]
cell.value          # typed value: str / float / bool / datetime.date / datetime.time / None
cell.text           # the text as displayed in the spreadsheet (str, or None)
str(cell)            # == cell.text (or "None")
cell.format          # "string" / "float" / "percentage" / "currency" / "date" / "time" / "boolean" / None
cell.row, cell.col   # 0-indexed position
cell.address         # spreadsheet-style address, e.g. "A1", "AZ12"
cell.is_formula      # True if the cell holds an ODF formula
cell.is_empty        # True if no value/text/format/formula is set
```

`Cell` supports the usual numeric conversions (`int()`, `float()`, `round()`, `abs()`, `-`,
`+`, `math.trunc/ceil/floor`) and comparisons (`==`, `<`, `>`, `<=`, `>=`), all operating on
`cell.value`. Comparing an empty cell (`value=None`) to a numeric cell raises `TypeError`, just
like plain Python (`None < 3.4`).

Available formats are listed in `odsslicer.FORMATS` (ODF format -> conversion callable).

### Arrays (`ArrayValues`)

Any multi-cell selection (`sheet[0]`, `sheet[:, 0]`, `sheet["A1:B3"]`, iterating over a
`Sheet`...) returns an `ArrayValues`, a wrapper around a list of `Cell` (1D) or a list of
lists of `Cell` (2D):

```python
arr = sheet["A1:B3"]
arr.dimension     # 0 (a single cell), 1 (row/column), or 2 (block)
arr.size           # numpy-style shape, e.g. (3, 2)
arr.to_list()       # raw values (list or list of list), without the Cell objects
arr.to_numpy()      # np.array of the values
arr.to_vector()     # for a (n, 1) shape: a 1D ArrayValues of size (n,)
arr[0]              # indexing into the underlying list(s) of Cell
```

Equality (`==`) between two `ArrayValues` compares the values (`to_list()`), not the identity
of the `Cell` objects. `.value` and `.formula` are also writable on a selection — see
[Writing a range at once](#writing-a-range-at-once).

### Iteration

```python
for row in sheet:              # equivalent to sheet[:]
    for cell in row:
        print(cell.address, cell.value)
```

### Address conversion helpers

`Sheet.address(string, n_rows=1)` converts a text address into a Python index/slice:

| Notation      | Result                                 |
|---------------|------------------------------------------|
| `"A1"`        | `(0, 0)` — (row, col)                     |
| `"1"`         | `0` — single row                          |
| `"A"`         | `(slice(n_rows), 0)` — entire column       |
| `"A1:B3"`     | `(slice(0, 3), slice(0, 2))`               |
| `"A:B"`       | `(slice(n_rows), slice(0, 2))`             |
| `"1:2"`       | `slice(0, 2)`                              |

A malformed address (`"1A"`, `"A:2"`, `"2:A"`, `"B:A"`...) raises `ValueError`.

`Sheet.string_address(row, col)` performs the reverse (0-indexed -> `"A1"`, `"AZ12"`...) and
`Sheet.string_to_col("AZ")` converts column letters to an index — both use the usual
spreadsheet bijective base-26 numbering (`Z` = 25, `AA` = 26, `AZ` = 51, `BA` = 52...).

```python
Sheet.string_address(0, 27)   # "AB1"
Sheet.string_to_col("AZ")      # 51
```

---

## 2. Writing values

### `Cell.value` and `save()`

```python
sheet["A1"].value = "new text"
sheet["A2"].value = 42.5
sheet["A3"].value = True
sheet["A4"].value = date(2026, 12, 31)
sheet["A5"].value = time(9, 30)
sheet["A6"].value = None              # clears the cell

table.save("modified_workbook.ods")    # or table.save() to overwrite the source file
```

Accepted types: `str`, `int`/`float`, `bool`, `datetime.date`, `datetime.time`, and `None`
(clears). Writing a number over a cell already formatted as `percentage` or `currency` keeps
that format. Writing over a cell that held a formula erases the formula (`is_formula` becomes
`False`).

`ODSReader.save(path=None)` rewrites the `.ods`: `content.xml` and `meta.xml` are regenerated
from the in-memory trees; every other zip member (`styles.xml`, `settings.xml`,
`manifest.xml`, thumbnail...) is copied through unchanged from the source file, and the ODF
convention (`mimetype` first, uncompressed) is respected. With no argument, `save()` overwrites
the source file — except for a document created with `ODSReader.new()`, which has no source
file and requires an explicit path.

### Writing a range at once

`sheet[...]` (a slice, not a single cell) is writable, for both `.value` and `.formula`:

```python
sheet["A1:A3"].value = 0                # broadcasts 0 to every cell in the range
sheet["A1:C1"].value = [1, 2, 3]        # element-wise, must match the selection's shape
sheet["A1:B2"].value = [[1, 2], [3, 4]] # 2D, same idea
sheet["A1:C1"].formula = "SUM(B1:B10)"  # broadcasts the same formula to every cell
```

See [Formula templates with `{r}`/`{c}`](#formula-templates-with-rc) for per-cell varying
formulas across a range.

### Automatic unrolling of repeated and merged cells

ODS compresses identical rows/columns into a single XML element shared between several
`Cell`s, and represents a merge via a top-left "master" cell plus hidden
`table:covered-table-cell` cells. Writing to one of these cells automatically "unrolls" the
structure involved — the compressed row/column is split into individual elements, and/or the
merge is undone — before the new value is applied:

```python
sheet["C5"].value = 42   # C5 was part of a block of 6 compressed rows: the block is split
                          # into 6 independent rows, only C5's value changes, the other
                          # cells in the block keep their original value
```

Writing to a merged cell (master or hidden) undoes the whole merge: every previously hidden
cell becomes independent again and reveals its own value — ODF already stores it internally
under `table:covered-table-cell`, exactly as LibreOffice would when manually un-merging.
`Cell` objects already obtained before the write remain valid and are automatically repointed
to their new individual XML element; `sheet.size` never changes as a result of unrolling.

### Automatic sheet growth

Writing to an address outside the current extent (`sheet.size`) grows the sheet instead of
raising — existing rows are widened with blank cells if needed, then new full-width blank
rows are appended — including growing a completely empty sheet (`size == (0, 0)`):

```python
sheet.size            # (9, 2)
sheet["E12"].value = "corner"
sheet.size            # (12, 5): rows 10-12 added, columns C-E added, everything else blank
```

A plain read (`sheet["Z1"].value` with no assignment) never grows anything — only a write
triggers growth. New rows/cells don't inherit any particular style.

### Displayed text: how `.text` is produced on write

ODF stores both a cell's value (`office:value`) and the text as displayed (`text:p`), formatted
per the document's locale. On write, `odsslicer` produces that text in three layers, first one
that applies wins:

1. **Learn from an example.** It looks for another cell of the same format in the document
   (preferring the cell's own prior content), compares its raw value to its displayed text to
   infer a pattern (decimal separator, decimal count, prefix/suffix, or a date pattern), checks
   the pattern reproduces the example exactly, and applies it:

   ```python
   sheet["A6"].text    # "200,00 %" (value 2.0)
   sheet["A6"].value = 0.5
   sheet["A6"].text    # "50,00 %" — same style as the cell's previous content

   sheet["A8"].text    # "28/02/21"
   sheet["A8"].value = date(2030, 1, 5)
   sheet["A8"].text    # "05/01/30"
   ```

   For "general" numbers (plain `float`, not percentage/currency) only the decimal separator
   is reused — never the decimal count, which would truncate precision.

2. **Read the real format.** If no usable example exists, it reads the cell's own resolved
   `NumberFormat` (see [Number formats](#number-formats)) — decimal places, grouping, currency
   symbol, or a date/time layout from `.components`:

   ```python
   table = ODSReader.new()             # a blank document: nothing anywhere to learn from
   sheet = table.sheet("Sheet1")
   fmt = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="$", grouping=True)
   sheet["A1"].style.number_format = fmt
   sheet["A1"].value = 1234.5
   sheet["A1"].text                     # "1,234.50 $" — read from the real format, not guessed
   ```

   This layer uses a plain `.`/`,` decimal/grouping convention — a `NumberFormat` doesn't
   capture the document's actual locale. Real spreadsheet applications recompute the display
   text from the format on open anyway, so this cached text mostly matters to `odsslicer`'s
   own `.text` reads.

3. **Plain Python conversion**, only if neither layer applies.

---

## 3. Files and sheets

### Creating a new file from scratch

`ODSReader.new(sheet_name="Sheet1")` creates a brand new, empty spreadsheet — not backed by
any file on disk — with a single sheet:

```python
table = ODSReader.new()                 # or ODSReader.new(sheet_name="Budget")
sheet = table.sheet("Sheet1")
sheet["A1"].value = "Total"
sheet["B1"].formula = "SUM(A2:A10)"
table.add_sheet("Data")

table.save("new_workbook.ods")          # a path is required: there's no source file to default to
```

A valid, empty ODF document needs several non-trivial pieces beyond `content.xml` — a
`mimetype`, `META-INF/manifest.xml`, `styles.xml`, `meta.xml`, `settings.xml` — so `.new()` is
bootstrapped from a minimal template bundled with the package. Everything else works exactly
as on a document opened from an existing file.

### Adding, renaming, reordering, deleting sheets

```python
summary = table.add_sheet("Summary")    # new empty sheet, appended last
summary.size                              # (0, 0)

table.rename_sheet("Sheet1", "Q4 Budget")
table.move_sheet("Q4 Budget", 0)         # make it the first tab (0-based index)
table.delete_sheet("Data")
```

- `add_sheet` raises `ValueError` for an empty name or one already in use.
- `rename_sheet` also rewrites any formula elsewhere in the document that references the
  sheet by name — `OldName.A1` becomes `NewName.A1` (quoted, `'New Name'.A1`, if needed).
  An unqualified reference within the renamed sheet's own formulas (`.A1`, meaning "this
  sheet") needs no rewrite. Raises `IndexError` for an unknown `old_name`, `ValueError` for an
  empty `new_name` or one already in use.
- `move_sheet(name, index)` raises `IndexError` for an unknown name, `ValueError` if `index`
  is out of range.
- `delete_sheet` raises `IndexError` for an unknown name and `ValueError` for the document's
  last remaining sheet (an ODF spreadsheet needs at least one).

---

## 4. Rows, columns and ranges

### Deleting rows and columns

```python
sheet.delete_row(3)        # 0-based; shifts every row below it up by one
sheet.delete_column(0)     # shifts every column to its right left by one
```

Any merge intersecting the removed row/column is undone first (see [Merged
cells](#merged-cells)) rather than left with a now-wrong span. Raise `IndexError` for an
out-of-range index. Formula references throughout the document are adjusted — see [Formula
references follow structural edits](#formula-references-follow-structural-edits).

### Copying cells and ranges

`Sheet.copy(source, dest)` copies a cell or rectangular range onto `dest` (its top-left
corner), like a spreadsheet's copy-paste — value, formula (with relative references shifted,
`$A$1` stays put), and style all come along:

```python
sheet.copy("A1", "C1")           # single cell
sheet.copy("A1:B2", "D5")        # a whole range, same shape at the new anchor
```

Grows the sheet first if needed, and is safe when `source` and `dest` overlap (every source
cell is read before any destination cell is written). A merged source cell copies whatever
value/style it individually carries — the merge itself is not replicated.

### Sorting a range

`Sheet.sort(source, by, ascending=True)` sorts the rows of `source` in place, by the values in
column `by` (an absolute column index within `source`):

```python
sheet.sort("A2:C10", by=1)                   # sort rows 2-10 by column B, ascending
sheet.sort("A2:C10", by=1, ascending=False)
```

A stable sort — rows with equal keys keep their relative order — and `None` always sorts last
regardless of `ascending`. Each row's value/formula/style moves together; a formula's
references shift by that row's own displacement, so a same-row formula like `=B2*C2` still
refers to its own, now-relocated row. Raises `ValueError` if `by` falls outside `source`'s
columns.

### Merged cells

A cell's merge state is readable without triggering any automatic un-merging:

```python
cell.is_merged           # True for either the master or one of the hidden/covered cells
cell.is_merge_master     # True only for the top-left cell of the range
cell.is_covered          # True only for a hidden table:covered-table-cell
cell.merge_master        # the top-left Cell of the range, from any cell in it — or None
cell.merge_span          # (n_rows, n_cols), or None
cell.merge_range         # "A1:C2"-style address string, or None
```

`Sheet.merge(address)` merges a rectangular selection into one cell: the top-left cell becomes
the master and keeps its value; every other cell becomes hidden — nothing is erased, its
content just stops showing, exactly as `unmerge` expects. `Sheet.unmerge(address)` undoes the
merge covering `address` (any single cell in the range, master or covered):

```python
sheet.merge("A1:C2")
sheet["A1"].merge_range     # "A1:C2"
sheet.unmerge("B2")         # any cell in the range works, not just the master
```

`merge` grows the sheet first if needed; raises `ValueError` for a single-cell range or if any
cell is already part of a merge. `unmerge` raises `ValueError` if `address` isn't a single
cell, or isn't part of any merge.

---

## 5. Formulas

### Writing a formula

`Cell.formula` accepts ordinary spreadsheet syntax — `A1`-style references, `$` for absolute
rows/columns, ranges, `,`-separated function arguments, cross-sheet references:

```python
sheet["C1"].formula = "A2+A3"           # or "=A2+A3" — the leading '=' is optional
sheet["C1"].is_formula   # True
sheet["C1"].formula      # "of:=[.A2]+[.A3]" — normalized to ODF's own syntax
sheet["C1"].value        # None: no calculation engine, nothing computes a cached result

sheet["C2"].formula = "$A$2+$A$3"           # absolute references
sheet["C3"].formula = "SUM(A1:A3)"          # ranges
sheet["C4"].formula = "IF(A1>0,1,-1)"       # comma-separated arguments
sheet["C5"].formula = "Sheet2.A1"           # cross-sheet
sheet["C6"].formula = "'My Sheet'.A1:A3"    # cross-sheet, quoted name
sheet["C7"].formula = None                  # clears the formula
```

Internally ODF uses `[.A1]` for references, `;` between arguments, and an `of:=` language
prefix. Setting `.formula` translates ordinary syntax into that form (commas inside quoted
string literals are left alone). If the formula already contains a `[` it's assumed to be
hand-written in ODF syntax and is passed through unchanged — an escape hatch for anything the
translation doesn't cover (named ranges, 3D references).

Writing a formula auto-materializes repeated/merged cells and auto-grows the sheet if needed;
writing `.value` or `.formula` clears the other.

There's no formula evaluator: `.value` reads back as `None` until a real spreadsheet
application opens the file and recalculates — this matches how ODF represents a formula with
no cached result.

### Reading a formula back in ordinary syntax

`.formula` always returns the raw ODF form. `.formula_friendly` translates it back to ordinary
syntax — the exact reverse of what `.formula = "..."` accepts:

```python
cell.formula
# 'of:=IF(OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1])=0;"";OFFSET([$Notes.$C$3];[.I$1];[.$A3]+[.$A$1]))'
cell.formula_friendly
# '=IF(OFFSET($Notes.$C$3,I$1,$A3+$A$1)=0,"",OFFSET($Notes.$C$3,I$1,$A3+$A$1))'
```

`None` if the cell has no formula. Best-effort: a construct the write-side translation doesn't
cover is passed through untranslated rather than guessed at.

### Filling a formula across a range

`Cell.fill_formula(target)` copies a cell's formula into every cell of `target`, shifting
relative references the way a spreadsheet's fill handle does — a `$`-anchored reference stays
put on whichever axis it locks:

```python
sheet["B2"].formula = "$A1+1"
sheet["B2"].fill_formula("B3:B10")
# B3 -> "=$A2+1", B4 -> "=$A3+1", ..., B10 -> "=$A9+1"
```

`target` can be an address string or a selection (`sheet["B3:B10"]`), in any direction.
Raises `ValueError` if the source cell has no formula, or if a shifted reference would fall
off the sheet.

### Formula templates with `{r}`/`{c}`

`.formula` on a range can use `{r}`/`{c}` placeholders, expanded **per cell** using that
cell's own 1-indexed row/column:

```python
sheet["A2:A10"].formula = "$A{r-1}+1"
# A2  -> "of:=[.$A1]+1"
# A3  -> "of:=[.$A2]+1"
# ...
# A10 -> "of:=[.$A9]+1"
```

A placeholder can hold a small arithmetic expression (`+`, `-`, `*`, `//`) over `r`/`c`.
`{c}` is always a plain **column number** (1-indexed), not a letter. A pattern with no `{...}`
broadcasts as-is.

Escape literal braces (e.g. an array constant `{1,2,3}`) by doubling them, like
`str.format`; the doubled content is passed through completely untouched:

```python
sheet["A1"].formula = "SUM({{1,2,3}})"   # -> "of:=SUM({1,2,3})"
```

### Formula references follow structural edits

Several operations rewrite formulas so they keep pointing at the same cells:

- `delete_row`/`delete_column`: every formula in the document that points into the affected
  sheet (its own formulas, and any other sheet's formula qualified with its name) has
  references past the removed row/column shifted:

  ```python
  sheet["C5"].formula = "A6+A7"
  sheet.delete_row(3)             # above both A6 and A7 — both shift up by one
  sheet["C4"].formula_friendly    # "=A5+A6" — C5's own content, now at C4
  ```

  A reference pointing *exactly* at the removed row/column is left as-is rather than modeled
  as a `#REF!` error (there's no error-value concept) — deleting the first row of a
  `SUM(A2:A3)` range shrinks it to `SUM(A2:A2)`.

- `rename_sheet`: explicitly qualified references (`OldName.A1`) are rewritten to the new
  name; unqualified ones within the sheet itself need no change.
- `copy`, `sort`, `fill_formula`: relative references shift by the displacement; `$`-anchored
  ones stay put.

Since there's no calculation engine, any formula whose text changes has its cached displayed
value cleared — it shows blank until a real spreadsheet application recalculates it.

---

## 6. Pivot tables

`Sheet.create_pivot_table(source, target, rows=..., columns=..., values=..., name=...)` writes
a pivot table's ODF definition ("data pilot table" in ODF terms) — same philosophy as
formulas: `odsslicer` describes what to compute, a real spreadsheet application computes it:

```python
# source data with a header row: Category | Region | Amount
sheet.create_pivot_table(
    "A1:C100",                     # source range (first row = field headers)
    "E1",                          # top-left of where the result will go
    rows=["Category"],             # row categories
    columns=["Region"],            # column categories
    values={"Amount": "sum"},      # aggregated field -> function
    name="SalesPivot",             # optional, defaults to "DataPilotTable{n}"
)
```

`source` may be sheet-qualified (`"Data.A1:C100"`) to pull from another sheet. Valid
aggregation functions: `"sum"`, `"average"`, `"count"`, `"countnums"` (numeric values only),
`"max"`, `"min"`, `"product"`, `"stdev"`, `"stdevp"`, `"var"`, `"varp"`. Raises `ValueError`
for a field not found in the source's header row, an unknown function, or a name already in
use.

**Unlike a formula, a pivot table is not recomputed automatically on open.** Every conformant
reader recalculates formulas on load; a pivot table needs an explicit refresh (Data > Pivot
Table > Refresh in LibreOffice) before its result appears at `target`. Confirmed against a
real LibreOffice: a file holding only the definition opens fine, the definition is fully
recognized and editable from the pivot UI, but the target area stays empty until refreshed.
`odsslicer` writes only the definition, never a computed grid — unless you let LibreOffice do
it for you with [`recalculate()` / `save(recalculate=True)`](#7-recalculating-with-libreoffice),
which refreshes every pivot table and materializes its output.

---

## 7. Recalculating with LibreOffice

`odsslicer` has no calculation engine of its own — formulas are written but not evaluated
(`.value` is `None`), and pivot tables are written as definitions only. `recalculate(path)`
closes that gap by delegating to a **local LibreOffice**, run headless: it opens the file,
recalculates every formula (including ones whose cached value went stale because you changed
an input cell), refreshes every pivot table (materializing its output grid), and saves the
file back in place:

```python
from odsslicer import ODSReader, recalculate

table = ODSReader("workbook.ods")
sheet = table.sheet("Sheet1")
sheet["A2"].value = 100.0                 # A5 = SUM(A2:A3) now has a stale cached value
sheet["C1"].formula = "A2*2"              # fresh formula, no value yet
sheet.create_pivot_table("A1:B50", "E1", rows=["Category"], values={"Amount": "sum"})

table.save("out.ods", recalculate=True)    # save, then let LibreOffice compute everything

computed = ODSReader("out.ods")            # reopen to read the results back
computed.sheet("Sheet1")["A5"].value        # 103.0 — recomputed, not the stale 6.4
computed.sheet("Sheet1")["C1"].value        # 200.0
computed.sheet("Sheet1")["E1"].value        # "Category" — the pivot's output grid is now real cells
```

`save(path, recalculate=True)` is a convenience for `save(path)` followed by
`recalculate(path)`; the standalone function works on any existing `.ods` file. The in-memory
document is *not* reloaded — reopen the file to read computed values. A run takes a couple of
seconds (LibreOffice start-up).

**How it works, and what it needs.** LibreOffice is started with a throwaway user profile in a
temporary directory (`-env:UserInstallation=…`), so your own LibreOffice profile is never
touched, and it runs a small script through LibreOffice's *own* embedded Python via the
scripting framework — no system-side `python-uno` is required, only the `soffice` executable.
LibreOffice re-saves the whole file in its own serialization, exactly as if you had opened it
and hit Save, so expect it to grow and be normalized.

**Configuring the command.** The command line lives in one module-level list you can edit at
the top of your script:

```python
import odsslicer
odsslicer.LIBREOFFICE_COMMAND          # ["soffice", "--headless", "--norestore", "--nologo", "--nodefault"]
odsslicer.LIBREOFFICE_COMMAND[0] = "/opt/libreoffice/program/soffice"   # a specific build
```

The first element is the executable; the rest are the flags every run gets (the throwaway
profile and the script URL are appended per call). A bare name is looked up on `PATH`, then in
the usual install locations (macOS app bundle, `/usr/bin`, `/usr/lib/libreoffice`, `/opt`,
snap, Windows `Program Files`); an explicit absolute path is taken at its word. Raises
`FileNotFoundError` if no executable can be found, and `RuntimeError` if LibreOffice fails,
times out (`timeout=120` seconds by default), or runs but doesn't rewrite the file (which is how
a silently-not-executed script shows up — e.g. when another LibreOffice instance already owns
the profile).

## 8. Styles

ODF splits a cell's formatting across two concerns: a `table:style-name` pointing to a
`<style:style>` (the cell's *visual* look — in `content.xml`'s automatic styles or
`styles.xml`'s named styles like `"Good"`), and, separately, a `style:data-style-name`
pointing to a `<number:*-style>` (the cell's real *display format*). `odsslicer` resolves
both, including inheritance via `style:parent-style-name`.

All style classes are importable from the top-level package: `from odsslicer import
CellStyle, NumberFormat, Border, RowStyle, ColumnStyle, TableStyle`.

### Reading a cell's style

```python
style = sheet["A7"].style
style.bold                            # False
style.italic, style.underline, style.strikethrough
style.font_family, style.font_size, style.font_color
style.superscript, style.subscript    # derived from style.text_position
style.background_color                # None, or e.g. "#ffdbb6"
style.horizontal_align, style.vertical_align
style.rotation                         # degrees, or None
style.wrap_text, style.shrink_to_fit
style.writing_mode                     # e.g. "lr-tb"
style.protection                       # raw style:cell-protect value, or None
style.border_top                       # None, or a Border("0.74pt solid #808080")
style.border_bottom, style.border_left, style.border_right
style.diagonal_bl_tr, style.diagonal_tl_br
style.number_format                    # None, or a NumberFormat (see below)
style.cell_properties, style.text_properties   # raw flattened attribute dicts, escape hatch
```

`Cell.style` is `None` only if the cell has no owning `ODSReader`; a cell with no style yet
returns a `CellStyle` with every property `None`/`False`, which a write turns into a real one.
A `Border` has `.width`/`.style`/`.color`; ODF's literal `"none"` resolves to `None`.

### Writing cell styles

Every property above (except `.conditions` on number formats, see below) is writable:

```python
sheet["A1"].style.bold = True
sheet["A1"].style.font_color = "#FF0000"
sheet["A1"].style.background_color = "#FFFF00"
sheet["A1"].style.horizontal_align = "center"
sheet["A1"].style.wrap_text = True
sheet["A1"].style.rotation = 45
```

The first write on a given cell forks it its own private automatic style — off the cell's
current style as parent, so every other already-resolved property keeps applying — and reuses
that same forked style for every later write on the same cell, so setting several properties
never affects any other cell that used to share the original style:

```python
sheet["A1"].attrs.get("table:style-name")   # None, or some shared style like "ce9"
sheet["A1"].style.bold = True
sheet["A1"].attrs.get("table:style-name")   # "ocs1" — a new style, private to A1
sheet["A1"].style.italic = True             # reuses "ocs1", doesn't fork again
```

### Copying a style from one cell to another

```python
sheet["B1"].style = sheet["A1"].style   # or = sheet["A1"], or = "ce9" (a raw style name)
sheet["B1"].style = None                # clears B1's style
```

This points the target at the *same* underlying style as the source — safe even if shared,
since a later individual property write forks a private copy on the spot.

### Borders

`border_top`/`border_bottom`/`border_left`/`border_right` accept a `Border`, a raw ODF
shorthand string, or `None`:

```python
sheet["A1"].style.border_top = "0.5pt solid #000000"
sheet["A1"].style.border_bottom = None        # explicitly no border on that side
```

Because the four sides resolve as one block from a single style, setting one side re-writes
the other three explicitly from whatever's currently resolved, so they're never silently
lost. `diagonal_bl_tr`/`diagonal_tl_br` resolve independently: `None` removes the override
(falls back to inherited), the string `"none"` forces no diagonal.

### Number formats

`.number_format` has `.family` (`"number"`/`"percentage"`/`"currency"`/`"date"`/`"time"`/
`"boolean"`/`"text"`), `.decimal_places`, `.grouping`, `.currency_symbol`, `.font_color`, and
for date/time styles `.components` (the ordered layout, e.g. `[("day", "long"), ("text",
"/"), ("month", "long"), ...]`).

Assign an existing one (from another cell, or by style name), or build one from scratch:

```python
sheet["B1"].style.number_format = sheet["A7"].style.number_format   # or = "N108"

pct = NumberFormat.create(table, "percentage", decimal_places=1)
eur = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="€", grouping=True)
dmy = NumberFormat.create(table, "date", components=[
    ("day", "long"), ("text", "/"), ("month", "long"), ("text", "/"), ("year", "long"),
])
hm = NumberFormat.create(table, "time", components=[("hours", "long"), ("text", "h"), ("minutes", "long")])
sheet["C1"].style.number_format = pct
```

`create(reader, family, ...)` accepts `decimal_places`/`grouping`/`min_integer_digits` for
numeric families, `currency_symbol` (required for `"currency"`), `components` (required for
`"date"`/`"time"`), and `font_color` for any family. `sheet["C1"].style.number_format = None`
removes the format.

### Conditional number formats

A number format can be conditional — e.g. negatives in red. On read,
`Cell.style.number_format` is already resolved against the cell's own value; `.conditions`
(list of `(condition, NumberFormat)`) and `.resolve(value)` on the base format expose the
mechanism:

```python
sheet["A7"].value                              # 2.0
sheet["A7"].style.number_format.name           # "N108P0" — already resolved for this value
sheet["A7"].style.number_format.font_color     # None (only the negative variant is red)
```

Write one with `.add_condition(condition, target)` — `target` applies whenever `condition`
(ODF's `"value()>=0"`-style syntax) matches:

```python
positive = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="€")
negative = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="€", font_color="#FF0000")
positive.add_condition("value()<0", negative)
sheet["A1"].style.number_format = positive
```

Only the comparison subset of ODF's condition language is understood (`value()>=0`,
`value()<100`...); an unsupported condition is never matched, so `.resolve()` falls back to the
base format rather than guessing.

### Row, column and sheet styles

`Sheet.row_style(row)` / `Sheet.column_style(col)` / `Sheet.style` resolve the little
formatting ODF attaches at those levels — all writable:

```python
sheet.row_style(0).height        # e.g. "0.452cm", or None
sheet.row_style(0).optimal_height
sheet.row_style(0).visible
sheet.column_style(0).width      # e.g. "2.258cm", or None
sheet.column_style(0).visible
sheet.style.tab_color            # the sheet tab's color, or None
sheet.style.visible

sheet.row_style(0).height = "1cm"
sheet.column_style(0).width = "5cm"
sheet.column_style(2).visible = False
sheet.style.tab_color = "#FF0000"
```

Any of the three is `None` if out of range or the `Sheet` has no owning `ODSReader`. Like
cell styles, the first write forks a private style reused on later writes — since these don't
chain via `parent-style-name`, the fork copies the current properties verbatim, so setting
only `.height` doesn't reset `.visible`.

---

## 9. Cell comments

`Cell.comment` reads a cell's note (`office:annotation`) — `None` if it has none:

```python
cell.comment                                 # None, or a Comment
cell.comment = "Follow up with finance"      # creates one (or replaces an existing one's text)
cell.comment.text                            # "Follow up with finance"
cell.comment.author = "Antonin"
cell.comment.date = datetime.now()
cell.comment.visible = True                  # pinned open, rather than only shown on hover
cell.comment = None                          # removes it
```

`.text` joins ODF's multiple `text:p` paragraphs with `\n` on read, and splits on `\n` back
into paragraphs on write, so a multi-line note round-trips. A comment never interferes with
the cell's own value: writing `.value` keeps the comment.

---

## 10. Cell hyperlinks

`Cell.hyperlink` reads a cell's link URL (a `<text:a>` wrapping the cell's whole text) —
`None` if it has none:

```python
cell.hyperlink                          # None, or a URL
cell.value = "Anthropic"
cell.hyperlink = "https://anthropic.com" # wraps the cell's current text in a link
cell.hyperlink = None                   # unwraps it, leaving the plain text in place
```

Setting a hyperlink on an empty cell gives it empty text to wrap first. Only a whole-cell
link is supported — a link on just part of the text isn't modeled. Writing a new `.value`
afterwards replaces the text, link included, as in a real spreadsheet.

---

## 11. Document properties

`ODSReader.properties` gives structured, writable access to `meta.xml` — the document
properties behind LibreOffice's "File > Properties" dialog:

```python
props = table.properties
props.title              # None, or e.g. "Q4 Budget"
props.subject
props.description
props.creator             # who last saved it (dc:creator)
props.initial_creator     # who originally created it
props.generator           # the app that last saved it, e.g. "LibreOffice/25.8..." — read-only
props.keywords            # a list, e.g. ["budget", "2026"]

props.title = "Q4 Budget"
props.keywords = ["budget", "2026"]     # replaces the whole list
props.title = None                       # clears the field
```

Arbitrary custom properties (`meta:user-defined`) are available dict-style; a custom
property's Python type round-trips through ODF's own `meta:value-type` (`str`/`float`/`bool`/
`datetime.date`, anything else raises `TypeError`):

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

---

## 12. Known limitations

- **No calculation engine of its own.** Formulas are written and translated but not
  evaluated by `odsslicer` (`.value` is `None` until a spreadsheet application recalculates),
  and pivot tables are written as definitions only. Use
  [`recalculate()` / `save(recalculate=True)`](#7-recalculating-with-libreoffice) to have a
  local LibreOffice compute both; without it, a real application recomputes formulas on open,
  but a pivot table needs an explicit refresh.
- **Named ranges and 3D references** (`Sheet1:Sheet3.A1`) aren't translated by the friendly
  formula syntax — write them in ODF's bracket syntax directly (the `[` escape hatch).
- **Displayed-text locale.** The on-write `.text` inference doesn't capture the document's
  actual locale (`number:language`/`number:country`); its layers may produce a different
  separator convention than the rest of the document. Real applications recompute display
  text on open.
- **Partial rich text** (one bold word inside a sentence, a link on part of a cell's text) is
  flattened on read and not writable.
- **Not covered:** data validation / drop-down lists, autofilters, frozen panes, sheet-level
  protection, row/column grouping, charts and embedded images, page layout/printing.
- Rows/columns repeated beyond an internal threshold (LibreOffice pads a sheet's default
  styling to 2^20 rows) are detected and discarded on load rather than materialized; a
  `[WARNING]` is printed if a row-length inconsistency remains after that cleanup.

---

## 13. Appendix: notable bug fixes

Bugs found and fixed while developing the module — all covered
by regression tests.

1. **Reading boolean cells**: the `"boolean"` format looked up `office:value` instead of
   `office:boolean-value`, and converted with `bool(s)` — `True` for the non-empty string
   `"false"`. A real ODF boolean always read back as `False`. Fixed.
2. **`Cell.text`/`str(cell)` returned the literal string `"None"`** for a cell whose
   `<text:p>` is empty or spread across several nodes (`<text:span>`), because bs4's
   `p.string` is `None` whenever there isn't exactly one text child. Fixed with
   `p.get_text()`.
3. **Growing an empty sheet (or one with a trailing empty row) could corrupt it on the next
   save/reload** — `load()` discarded such rows from memory but not from the XML, and
   `grow_to` appended after them. Fixed: stray rows are discarded first.
4. **A formula-only cell was wrongly `is_empty`**, so `load()`'s trailing-empty-row trim could
   silently drop it. Fixed: `is_empty` now checks the formula.
5. **Writing to the only sheet of a minimal document failed** — nothing anywhere to copy a
   namespace template from. Both `grow_to` and `_set_text` now fall back to building a
   correctly namespace-qualified element from scratch.
6. **Style forking keyed off the style *name*'s shape** broke as soon as two cells legitimately
   shared a forked style (via `cell.style = other.style`/`Sheet.copy`): editing one silently
   mutated the other. Fixed by tracking ownership on the `Cell` object itself.
7. **A cell's value `text:p` wasn't scoped to direct children** — once cell comments (which
   nest their own `text:p`) were added, the comment's paragraph could be mistaken for the
   cell's value. Fixed with `recursive=False` on every value `text:p` lookup.
