# odsslicer

[![CI](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml/badge.svg)](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml)

Python reader for `.ods` files (OpenDocument Spreadsheet, LibreOffice/OpenOffice Calc), with a
numpy-inspired indexing API: `sheet["A1"]`, `sheet[0, 0]`, `sheet["A1:B3"]`, plain Python
slices, etc.

The module parses `content.xml` directly (via BeautifulSoup) and handles ODF cell types
(text, number, percentage, currency, date, time, boolean), formulas, as well as repeated and
merged rows/columns.

Write support: `cell.value = ...`, `cell.formula = ...`, `cell.style.bold = ...` (and other
formatting properties), `sheet.merge(...)`/`.unmerge(...)`, new sheets, even brand new files
from scratch — then `reader.save(...)`. Repeated or merged cells are automatically
unrolled/unmerged in the background on first write access, and writing beyond a sheet's
current extent grows it automatically (new rows/columns) — see
[Writing](#writing-experimental) below for details and remaining limitations.

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

`border_top`/`border_bottom`/`border_left`/`border_right` accept a `Border`, a raw ODF
shorthand string (`"0.5pt solid #000000"`), or `None`. Because the 4 sides resolve as one
block from a single style (see above), setting just one side also re-writes the other three
explicitly from whatever's currently resolved, so they're never silently lost; `None` cancels
a side outright (writes literal `"none"`, same as ODF itself uses to override an inherited
border). `diagonal_bl_tr`/`diagonal_tl_br` resolve independently instead, so `None` there just
removes the override (falls back to whatever's inherited) — pass the string `"none"` for an
explicit "no diagonal regardless of inheritance".

`number_format` can be *assigned* an existing `NumberFormat` (or its style name, as a plain
string) already present in the document — e.g. copying the format from another cell:

```python
sheet["B1"].style.number_format = sheet["A7"].style.number_format   # or = "N108"
```

Creating a brand new number format from scratch, and writing `.conditions` (conditional
formatting), aren't supported yet — assign an existing one instead.

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
11. **Writing to a sheet that is the only one in the whole document, with nothing anywhere
    else to copy a namespace template from, failed outright.** Building `ODSReader.new()`'s
    bundled template surfaced two related bugs in the "copy an existing tag" approach used to
    create new XML elements: `Sheet.grow_to` discarded a sheet's own lone "phantom" blank row
    (see #9) *before* it could be used as a row/cell template, and `Cell._set_text` had
    nothing at all to copy a `text:p` from on such a minimal sheet. Both now fall back to
    building a correctly namespace-qualified element from scratch (using the standard OASIS
    namespace URIs directly) instead of raising `NotImplementedError`.

All of these cases are covered by regression tests in `tests/test_odsslicer.py`.
