# odsslicer — complete API reference

This is the full, feature-by-feature reference for `odsslicer`, with a usage example for every
feature. For a short overview, installation, and the comparison with other packages, see the
[README](README.md).

Everything below assumes:

```python
from odsslicer import ODSReader, NumberFormat
from datetime import date, time, datetime, timedelta

table = ODSReader("workbook.ods")
sheet = table.sheet("Sheet1")
```

---

## Table of contents

1. [Reading](#1-reading)
   - [Opening a file, sheets](#opening-a-file-sheets)
   - [Indexing and slicing](#indexing-and-slicing)
   - [Cells (`Cell`)](#cells-cell)
   - [Dates, times and durations](#dates-times-and-durations)
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
   - [Inserting rows and columns](#inserting-rows-and-columns)
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
   - [From the command line](#from-the-command-line)
   - [Which one to use](#which-one-to-use)
   - [References to other workbooks](#references-to-other-workbooks)
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
12. [Performance](#12-performance)
    - [How it compares to other readers](#how-it-compares-to-other-readers)
13. [Known limitations](#13-known-limitations)
14. [Appendix: notable bug fixes](#14-appendix-notable-bug-fixes)

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
in-memory trees. `ODSReader.sheet(name)` raises `KeyError` for an unknown name.

`sheet.size` covers the data, empty rows and columns between it included. A note counts as
data, as in LibreOffice: the grid reaches a note on an empty cell, however far. What
applications write after the data, to declare the sheet's full size or to format rows and
columns past it, is left out: a last row holding no data, more than 1,000 repeated empty rows
at the bottom, more than 10 empty columns at the right. It stays in the file, with the charts
and shapes anchored there, whatever the edit: writing past the data reuses it (see [Automatic
sheet growth](#automatic-sheet-growth)), and inserting or deleting rows and columns moves it
as LibreOffice would.

Progress and warnings go through the standard `logging` module (logger name `"odsslicer"`):
load-time details are logged at `DEBUG` (`INFO` when a reader/sheet is created with
`verbose=True`), and anomalies — like rows of inconsistent lengths — at `WARNING`. Configure
logging (`logging.basicConfig(level=logging.INFO)`) to see them; nothing is ever printed
directly. The package also ships a `py.typed` marker: the whole public API is type-annotated
and mypy-checked, so your own type checker can verify code that uses it.

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
shape, rather than an error. Shapes follow numpy's but for columns:

| Selection | Shape | numpy's |
| --- | --- | --- |
| `sheet[0]`, `sheet[0, 0:3]`, `sheet["A1:C1"]`, `sheet["1"]` — one row | `(3,)` | `(3,)` |
| `sheet[0:3, 0]`, `sheet[:, 0]`, `sheet["A1:A3"]`, `sheet["A"]` — one column | `(3, 1)` | `(3,)` |
| `sheet[0:3, 0:2]`, `sheet["A1:B3"]`, `sheet[0:3]`, `sheet["A:B"]` | `(3, 2)` | `(3, 2)` |
| `sheet[0:1, 0:3]` — a slice of one row | `(1, 3)` | `(1, 3)` |

An integer row index drops the row axis, as numpy does, and so does an address spanning one
row: `sheet["A12:C12"]` is `(3,)` where `sheet[11:12, 0:3]` is `(1, 3)`. An integer column
index keeps its axis, `(n, 1)`, where numpy drops it, and so does an address spanning one
column; `to_vector()` (below) flattens it to `(n,)`.

### Cells (`Cell`)

```python
cell = sheet["A1"]
cell.value          # typed value: str / float / bool / date / datetime / time / timedelta / None
cell.text           # the text as displayed in the spreadsheet (str, or None)
str(cell)            # == cell.text (or "None"); a multi-line cell reads back
                     # as "line 1\nline 2…", one ODF paragraph per line
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

A cell holding several lines (Ctrl+Enter in a spreadsheet) is one `<text:p>` paragraph per
line in ODF — `cell.text` and a string `cell.value` join them with `\n`, and writing a value
containing `\n` writes one paragraph per line in return (a literal newline *inside* a
paragraph is plain whitespace to ODF, so it would not survive).

Available formats are listed in `odsslicer.FORMATS` (ODF format -> conversion callable).

### Dates, times and durations

ODF stores a date cell as a date or a date-time, and a time cell as a *duration*, which only
reads as a time of day when it is shorter than one and its format shows a time of day.
`cell.value` follows the value and the cell's format:

| Stored in the file | `cell.value` |
| --- | --- |
| `office:date-value="2023-11-30"` | `date(2023, 11, 30)` |
| `office:date-value="2023-11-30T13:00:00"` | `datetime(2023, 11, 30, 13, 0)` |
| `office:time-value="PT09H30M00S"` | `time(9, 30)` |
| `office:time-value="PT07H30M00S"`, shown `07:30` in `[HH]:MM` | `timedelta(hours=7, minutes=30)` |
| `office:time-value="PT12H30M15.5S"` | `time(12, 30, 15, 500000)` |
| `office:time-value="PT128H45M00S"`, shown `128:45:00` | `timedelta(hours=128, minutes=45)` |
| `office:time-value="-PT01H30M00S"`, shown `-01:30:00` | `timedelta(hours=-1, minutes=-30)` |

- A duration reads as a `timedelta` in a format counting time in full — `[HH]:MM:SS`, as
  LibreOffice writes an elapsed time, and as odsslicer formats a `timedelta` written into a
  cell with no format — and whenever it is 24 hours or more, or negative; as a `time` when it
  lies within a day, in any other format. A `timedelta` written reads back as one, and a
  new format — `cell.style.number_format`, `cell.style` — reads the value again.
- One column can therefore mix `time` and `timedelta`, and a column of date-times can mix
  `datetime` and `date`: LibreOffice saves a date-time falling on midnight as a bare date.
  `Sheet.sort` orders either mix.
- LibreOffice saves a date-time displayed with a time-only format as the duration since
  30 December 1899 (`PT1086253H00M00S`): it reads as a large `timedelta`, and
  `value % timedelta(days=1)` gives back the time of day.
- A UTC offset, which spreadsheet files rarely carry, is applied and dropped: the value
  reads in UTC, as LibreOffice reads it.
- A value that cannot be read as its declared type (a malformed date, a duration counted in
  years or months) does not make the sheet unreadable: the cell reads as its displayed text,
  `cell.raw_value` keeps the value as written, and loading the sheet emits one `UserWarning`
  naming such cells.

pandas still fails here: as of pandas 3.0.5, `read_excel(..., engine="odf")` raises
`hour must be in 0..23` on a sheet holding a duration of 24 hours or more
([Stack Overflow question 71646358](https://stackoverflow.com/questions/71646358)).

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
sheet["A6"].value = datetime(2026, 12, 31, 18, 30)
sheet["A7"].value = timedelta(hours=128, minutes=45)   # stored as PT128H45M00S
sheet["A8"].value = None              # clears the cell

table.save("modified_workbook.ods")    # or table.save() to overwrite the source file
```

Accepted types: `str`, `int`/`float`, `bool`, `datetime.date`, `datetime.datetime`,
`datetime.time`, `datetime.timedelta`, and `None` (clears). Writing a number over a cell
already formatted as `percentage` or `currency` keeps that format. Writing over a cell that
held a formula erases the formula (`is_formula` becomes `False`).

What stands for one of those types goes too, and reads back as it: numpy's scalars — its
integers, floats, booleans, `datetime64` and `timedelta64`, what pandas hands out, and a numpy
array for a range (`sheet["A2:C9"].value = df.to_numpy()`) — a `decimal.Decimal`, as database
drivers return `NUMERIC` columns, or any other number. NaN, and numpy's `NaT`, are missing
values: they leave the cell empty, as pandas writes them to a spreadsheet — LibreOffice would
show a NaN as 0, and count it in sums and averages. An infinite number raises `ValueError`,
as does a `datetime64` past the year 9999.

A date or time value reads back at once the way a reload would read it (see
[Dates, times and durations](#dates-times-and-durations)): a `timedelta` within a day comes
back as a `time`, and an aware `datetime` is written in UTC and comes back without its offset.

A date or time written into a cell with no number format gets the one LibreOffice would give
it if typed there: the standard format of the document's locale, its default language in
`styles.xml`. Without one, LibreOffice shows a date past the declared columns as a serial
number (44627), and wraps a duration of 128 hours around the clock (08:45:00).

| Written | fr-FR document | en-US document | no language |
| --- | --- | --- | --- |
| `date(2022, 3, 7)` | `07/03/22` | `03/07/22` | `2022-03-07` |
| `datetime(2022, 3, 7, 13, 45, 30)` | `07/03/22 13:45` | `03/07/22 01:45 PM` | `2022-03-07 13:45:30` |
| `time(9, 30)` | `09:30:00` | `09:30:00 AM` | `09:30:00` |
| `timedelta(hours=128, minutes=45)` | `128:45:00` | `128:45:00` | `128:45:00` |
| `timedelta(hours=7, minutes=30)` | `07:30:00` | `07:30:00` | `07:30:00` |

The formats of 67 locales are built in, as LibreOffice 25.8 defines them; any other locale
gets ISO 8601, as does a document with no language. A cell that already has a number format
keeps it, whatever its kind, as in LibreOffice — a date format of its own, its column's, or
even a currency one. Every cell given a format shares one number format and one cell style,
rather than getting a style of its own; `ODSReader.new()` documents are in French (fr-FR).

`ODSReader.save(path=None)` rewrites the `.ods`: `content.xml` and `meta.xml` are regenerated
from the in-memory trees; every other zip member (`styles.xml`, `settings.xml`,
`manifest.xml`, thumbnail...) is copied through unchanged from the source file, and the ODF
convention (`mimetype` first, uncompressed) is respected. With no argument, `save()` overwrites
the source file — except for a document created with `ODSReader.new()`, which has no source
file and requires an explicit path.

`save()` never writes into the workbook itself: it writes the new file under a temporary name
in the same folder (`.name.ods.<random>.tmp`), has it reach the disk, then renames it over the
target in one step. A program reading the workbook meanwhile gets the old version or the new
one, never a partial file, and a save that fails or is interrupted (an exception, Ctrl-C)
leaves the old version as it was, with no temporary file behind — a crash or a power cut can
leave one, to delete. As the saved file is a new file:

- it keeps the permissions of the one it replaces, and a file saved to a new path gets the
  default permissions of any new file;
- a symbolic link is followed and stays as it was, but another hard link to the old file
  keeps the old content;
- saving needs write access to the folder, not just to the file, and a read-only file raises
  `PermissionError`;
- on Windows, a file another program keeps open cannot be replaced: `save()` raises
  `PermissionError` and leaves the file as it was.

With `recalculate=True`, LibreOffice's result replaces the workbook the same way, through a
temporary file and a rename — see
[Recalculating with LibreOffice](#7-recalculating-with-libreoffice).

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
structure involved — the compressed row/column is split into individual elements, and/or, for
a hidden cell, the merge is undone — before the new value is applied:

```python
sheet["C5"].value = 42   # C5 was part of a block of 6 compressed rows: the block is split
                          # into 6 independent rows, only C5's value changes, the other
                          # cells in the block keep their original value
```

Writing to a merge's master — a value, a formula, a style — keeps the merge, as typing into the
merged cell does in LibreOffice: a long date written into a title merged across three columns
still spans them. Writing to a hidden cell undoes the whole merge, since the value written would
stay hidden otherwise: every previously hidden cell becomes independent again and reveals its
own value — ODF already stores it internally under `table:covered-table-cell`, exactly as
LibreOffice would when manually un-merging. `Cell` objects already obtained before the write
remain valid and are automatically repointed to their new individual XML element;
`sheet.size` never changes as a result of unrolling.

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

Growing first takes back what the file holds past `sheet.size`: with columns D to R empty
but formatted, writing to D1 lands in D1 and keeps D1's formatting. So with the rows below
the data: a chart anchored in row 16, past the grid, stays there when a footer is written in
row 18. A run of repeated rows is widened once for all its rows, and writing into it then
unrolls it, as above.

### Displayed text: how `.text` is produced on write

ODF stores both a cell's value (`office:value`) and the text as displayed (`text:p`), formatted
per the document's locale. On write, `odsslicer` produces that text in three layers, first one
that applies wins — except for dates and times, which try the second one first:

1. **Learn from an example.** It looks for another cell of the same format in the document
   (preferring the cell's own prior content), compares its raw value to its displayed text to
   infer a pattern (decimal separator, decimal count, prefix/suffix, or a date pattern), checks
   the pattern reproduces the example exactly, and applies it:

   ```python
   sheet["A6"].text    # "200,00 %" (value 2.0)
   sheet["A6"].value = 0.5
   sheet["A6"].text    # "50,00 %" — same style as the cell's previous content
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

   A date or time goes to this layer first: its format gives its layout in full, and every
   date written into a cell with no format gets one (see [`Cell.value` and
   `save()`](#cellvalue-and-save)), while an example could be a cell formatted otherwise.

   ```python
   sheet["A8"].text    # "28/02/21", in a DD/MM/YY format
   sheet["A8"].value = date(2030, 1, 5)
   sheet["A8"].text    # "05/01/30"
   ```

   A duration is shown as LibreOffice shows it: wrapped around the clock by a time format
   (`128:45` shows as `08:45:00`), counted in full by an elapsed-time `[HH]:MM:SS` one. A
   format with AM/PM counts the hours on a 12-hour clock (`01:45 PM`). Days of the week and
   months named in full or abbreviated come in the format's language, else the document's —
   `Sunday, September 27, 2026`, `dim. 27 sept.`, and declined next to a day in the languages
   that decline them, Polish `27 września` — from LibreOffice's own names for 67 locales.
   Fractions of a second come as the format asks, in the locale's separator, and rounded as
   LibreOffice rounds them: never carried into the seconds by a clock format (59.996 s shows
   as `00:59.99`), carried by an elapsed-time one (`00:01:00.00`); without decimals, seconds
   are cut. A format LibreOffice writes with `number:format-source="language"` shows as the
   system's regional settings say, not as its elements do, and a format naming days or months
   in a language outside those 67 cannot be rendered either: its text comes from an example
   instead — a cell shown with the very same format, not merely holding a date — else from
   the third layer.

3. **Plain Python conversion**, only if neither layer applies — ISO for dates and date-times,
   hours in full for a duration (`128:45:00`). For a date or time, that now means a cell
   formatted for another kind of value, such as a currency, which keeps its format.

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
  sheet") needs no rewrite. Raises `KeyError` for an unknown `old_name`, `ValueError` for an
  empty `new_name` or one already in use.
- `move_sheet(name, index)` raises `KeyError` for an unknown name, `ValueError` if `index`
  is out of range.
- `delete_sheet` raises `KeyError` for an unknown name and `ValueError` for the document's
  last remaining sheet (an ODF spreadsheet needs at least one).

---

## 4. Rows, columns and ranges

### Inserting rows and columns

```python
sheet.insert_row(2)              # one blank row before row 2 (0-based): row 2 moves to 3
sheet.insert_rows(2, 5)          # five blank rows at once
sheet.insert_rows(sheet.n_rows)  # the sheet's height as position: below the data
sheet.insert_column(1)           # one blank column before column B
sheet.insert_columns(1, 3)
```

It behaves like a spreadsheet's "insert rows above" / "insert columns before":

- **Formula references follow the cells**, anywhere in the document — see [Formula references
  follow structural edits](#formula-references-follow-structural-edits). A range straddling
  the insertion point stretches (`SUM(A2:A10)` with rows inserted before row 5 becomes
  `SUM(A2:A12)`); a range starting at or below the insertion point moves whole.
- **A merge straddling the insertion point grows** to include the new rows/columns; merges
  above or below simply move.
- **Column widths stay with their columns**: the column definitions shift too, print titles'
  included, the new columns get the default width.
- **What the file holds past the grid moves too**: a chart or a shape anchored below the
  data, a formatted cell there, moves down with the rows after the insertion point, and
  sideways with the columns.
- The new rows/columns are **blank** — no values, no styles. Use [`copy`](#copying-cells-and-ranges)
  to bring formatting onto them.

Raises `IndexError` for a position outside `0..n_rows` (or `0..n_cols`), `ValueError` for a
`count` below 1. Files written by LibreOffice or Excel declare the whole 16,384 × 1,048,576
grid through trailing filler rows and columns (1,024 columns before LibreOffice 7.4); on such
a file, insertions give that filler back, so the document never exceeds the application's
maximum, which would make it drop data on open. A smaller sheet grows.

### Deleting rows and columns

```python
sheet.delete_row(3)              # 0-based; shifts every row below it up by one
sheet.delete_rows([3, 7, 20])    # several at once - indexes as they currently are
sheet.delete_column(0)           # shifts every column to its right left by one
```

When removing many rows, prefer one `delete_rows` call: the document-wide formula-reference
adjustment (below) runs once instead of once per row — 10-15× faster for 100 rows, more on
big documents. `delete_rows` validates every index before removing anything (atomic on
`IndexError`), ignores duplicates, and treats indexes as pre-deletion positions.

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
cell is already part of another merge. A range already merged the same way is left as it is:
code written for 0.13, where writing into a merge's master undid the merge, merges it again
after the write. `unmerge` raises `ValueError` if `address` isn't a single cell, or isn't part
of any merge.

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
sheet["C7"].formula = "$Élèves.A1"          # an absolute sheet, as LibreOffice makes them
sheet["C8"].formula = None                  # clears the formula
```

Internally ODF uses `[.A1]` for references, `;` between arguments, and an `of:=` language
prefix. Setting `.formula` translates ordinary syntax into that form (commas inside quoted
string literals are left alone). If the formula already contains a `[` it's assumed to be
hand-written in ODF syntax and is passed through unchanged — an escape hatch for anything the
translation doesn't cover (named ranges, 3D references).

Function names are written as given, so they must be the names ODF stores: a function taken
from Excel is usually stored with a `COM.MICROSOFT.` prefix — `COM.MICROSOFT.CONCAT(A1,B1)`,
where a plain `CONCAT(A1,B1)` shows as `#NAME?` in LibreOffice.

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

Only what you write is expanded: a formula read from the file keeps its braces, text or an
inline array, when an edit rewrites its references — structural edits, `copy`, `sort`,
`fill_formula`, `rename_sheet`.

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

  As in LibreOffice, a range whose first or last row is removed keeps the rows left of it —
  deleting row 3 turns `SUM(A2:A3)` into `SUM(A2:A2)` — and a reference to a removed cell, or
  a range removed whole, becomes `#REF!` (`[#REF!]` in the file), which LibreOffice shows as
  such.

- `insert_rows`/`insert_columns`: references at or past the insertion point move forward, `$`
  locks included (the referenced cell moved, the formula wasn't filled):

  ```python
  sheet["C1"].formula = "SUM(A2:A3)+$A$7"
  sheet.insert_rows(2, 3)         # inside A2:A3, above A7
  sheet["C1"].formula_friendly    # "=SUM(A2:A6)+$A$10"
  ```

- `rename_sheet`: explicitly qualified references (`OldName.A1`) are rewritten to the new
  name; unqualified ones within the sheet itself need no change.

All of them take references to other sheets in the forms LibreOffice writes: an absolute
sheet (`$Sheet2.A1`, what clicking a cell of another sheet gives), a name starting with an
accented letter, unquoted (`Élèves.A1`), and a range whose end, with no sheet name of its own,
is on its start's sheet (`$Sheet2.A1:.A5`).

Charts follow the same edits, as LibreOffice has them follow: their ranges stretch, shrink
and move with the rows and columns, a range deleted whole keeping its address — a chart has
no `#REF!` to show — and the cell a chart's frame ends in, from which LibreOffice sizes it
on open, moves too. A renamed sheet is renamed in them.
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
an input cell), refreshes every pivot table (materializing its output grid), and replaces the
file with the result:

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
The subprocess environment is shielded automatically: your Python's `PYTHONPATH`/`PYTHONHOME`/
`LD_LIBRARY_PATH` — and any foreign interpreter on `PATH`, e.g. an active venv — are kept away
from LibreOffice's embedded interpreter, which would otherwise crash on some builds.
LibreOffice re-saves the whole file in its own serialization, exactly as if you had opened it
and hit Save, so expect it to grow and be normalized.

**Replacing the file.** LibreOffice loads the workbook where it is, then saves its result to a
temporary file next to it (`.name.ods.<random>.tmp`), in the workbook's own format, and
`recalculate()` renames that file over the workbook once LibreOffice reports the save complete
— as `save()` does, with the same consequences (see [`Cell.value` and
`save()`](#cellvalue-and-save)). A program reading the workbook meanwhile gets the old version
or the recalculated one, never a partial file, and a run that fails, times out or is
interrupted (Ctrl-C) leaves the workbook as it was, with no temporary file behind. On macOS and
Linux, a timeout or Ctrl-C also stops LibreOffice itself, whatever wrapper script started it —
Homebrew's `soffice` is one — and deletes the lock files it leaves next to the workbook.

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
`FileNotFoundError` if no executable can be found, `PermissionError` for a read-only workbook,
before LibreOffice starts, and `RuntimeError` if LibreOffice fails, times out (`timeout=120`
seconds by default), or runs but doesn't save the result (which is how a silently-not-executed
script shows up — e.g. when another LibreOffice instance already owns the profile). When the
script itself raised, the `RuntimeError` gives its traceback.

### From the command line

LibreOffice's own command line does a similar round trip without Python: `--convert-to` loads
each file and saves it again, and an `.ods` converted to `.ods` comes out with the results of
its formulas cached in it:

```bash
soffice --headless --convert-to ods --outdir recalculated/ workbook.ods
```

`soffice` is `/Applications/LibreOffice.app/Contents/MacOS/soffice` on macOS, `soffice` or
`libreoffice` on Linux; `--convert-to` has been there since the LibreOffice 3.x series. What
follows was verified with LibreOffice 25.8.4 on macOS, and against its source code:

- **It never overwrites its input.** The result goes to `--outdir` (the current directory by
  default) under the input's name, and pointing `--outdir` at the input's own folder fails with
  `Error: Please verify input parameters... (SfxBaseModel::impl_store <…> failed:
  0x4c0c(Error Area:Sfx Class:Write Code:12))` — while `soffice` still exits with status 0.
  Convert into another folder, check that the output exists, then move it over the original.
- **What gets recalculated on load.** Formulas without a cached result — every formula whose
  text `odsslicer` rewrote — and volatile functions (`NOW()`, `RAND()`, `INDIRECT()`,
  `OFFSET()`…) always are, and so are formulas in a cell with no style at all, of its own or of
  its column: LibreOffice computes them to find them a number format. The others keep their
  cached results — stale, if `odsslicer` changed a value they read: a total in bold, a formula
  in a column formatted as a whole — unless *Tools > Options > LibreOffice Calc > Formula >
  Recalculation on File Load > ODF spreadsheet (not saved by LibreOffice)* says otherwise; it
  defaults to *Never recalculate*. *Always recalculate* (`ODFRecalcMode` = `0` in the profile)
  forces a full recalculation of every ODF file on load — files saved by LibreOffice included,
  whatever the label says: only *Prompt user* looks at which program saved the file. No
  command-line switch sets it; it lives in the user profile.
- **A profile of its own.** `-env:UserInstallation=file:///…` points LibreOffice at a separate
  profile: that is how to pass the setting above without changing your own, and it keeps the
  run independent of a LibreOffice already open on your desktop.
- **Pivot tables are not refreshed**, *Always recalculate* or not: their output area stays
  empty. Only `recalculate()` refreshes them.
- **References to other workbooks come out as `Err:540`**, whatever the options: only
  `recalculate(update_links=True)` reads them — see
  [References to other workbooks](#references-to-other-workbooks).

A complete run, with a throwaway profile set to *Always recalculate* and the result moved back
in place:

```bash
profile=$(mktemp -d)
mkdir -p "$profile/user"
cat > "$profile/user/registrymodifications.xcu" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
EOF
out=$(mktemp -d)
soffice "-env:UserInstallation=file://$profile" --headless --convert-to ods --outdir "$out" workbook.ods
test -s "$out/workbook.ods" && mv "$out/workbook.ods" workbook.ods
rm -rf "$profile" "$out"
```

### Which one to use

| | `recalculate()` / `save(recalculate=True)` | `soffice --convert-to ods` |
| --- | --- | --- |
| Runs from | Python, right after `odsslicer` writes | any shell, Makefile or CI job |
| Formulas | all recalculated (`calculateAll()`) | all, guaranteed only with *Always recalculate* in the profile |
| Pivot tables | refreshed | left empty |
| Output | the file itself, replaced in one step | a new file in `--outdir`, to move back yourself |
| Failure | raises `RuntimeError` or `FileNotFoundError`, the file left as it was | printed, exit status 0 even when nothing was written |
| Profile | throwaway, set up for you | your own, unless you pass `-env:UserInstallation` |
| References to other workbooks | read with `update_links=True`, for trusted workbooks; `Err:540` otherwise | `Err:540` |

Use `recalculate()` whenever the file comes out of Python code anyway. The command line suits
shell pipelines, batches (`--convert-to` takes several files in one LibreOffice start) and
files `odsslicer` never touched.

### References to other workbooks

A workbook can pull cells from another file, either written out in a formula
(`='file:///…/students.ods'#$Students.B2`) or built at run time, as in `OFFSET(INDIRECT($B$1);…)`
with the address in `B1`. Recalculated headless, those cells come out as **`Err:540`**
("External content disabled") — always with `soffice --convert-to`, and with `recalculate()`
unless you pass `update_links=True` — and `odsslicer` then reads the string `"Err:540"`. This
happens even to a file that displayed them correctly when it was last saved from LibreOffice:

- LibreOffice reads another file for a formula only once link updates are allowed for the
  document — in the application, by the *Allow updating* button of the "Automatic update of
  external links has been disabled." bar. Until then, a formula that needs the other file
  evaluates to `Err:540`. Headless, updates are allowed only to a load that asks for them.
- `INDIRECT()` and `OFFSET()` are volatile, recomputed on every load: a plain conversion, with
  no recalculation option at all, already replaces their cached results with `Err:540`.
- When at least one formula writes the reference out, LibreOffice also saves the values it read
  from that file into the workbook — a hidden sheet named after the file, which `sheets_names`
  lists — and reuses them instead of reading the file, stale or not. A workbook that reaches
  the other file only through `INDIRECT()` saves no such copy.

No command-line switch changes this, and no configuration does either: `--convert-to` loads
files without an `UpdateDocMode`, which Calc treats as "never update links" whatever the
profile says. *Update links when opening: Always (from trusted locations)*, the *Low* macro
security level and a trusted file location, all set together, still gave `Err:540` with
`--convert-to`: they only matter to a load that asks for link updates.

`recalculate()` asks for them when you pass **`update_links=True`**, and so does `save()` with
`recalculate=True` (without it, `save()` raises `ValueError`):

```python
from odsslicer import recalculate

recalculate("workbook.ods", update_links=True)                   # an existing file
table.save("workbook.ods", recalculate=True, update_links=True)  # right after writing it
```

LibreOffice then updates every link of the workbook before recalculating, as *Allow updating*
would: formulas reading another workbook compute from that file as it is now, whether they
write the reference out or build it with `INDIRECT()`, and a saved copy of its values is read
again rather than reused.

**It is off by default, and meant for workbooks you trust**, because it lets the workbook
decide what LibreOffice reads: any file its formulas name, and any URL — `WEBSERVICE()` fetches
one, a reference to a remote workbook downloads it. One formula can read a local file and send
what it read to a server, which is precisely what LibreOffice's check guards against. For its
run, `recalculate()` makes the workbook's folder a trusted location in its throwaway profile —
your own LibreOffice settings stay untouched — and loads the workbook with macros disabled.

Once a run has updated the links, the workbook holds LibreOffice's copy of what it read (see
above), and a later run without `update_links=True` may fall back on that copy, stale, rather
than give `Err:540`: keep passing it for such a workbook.

**What it takes**, should you drive LibreOffice from your own script — this is what
`recalculate()` does, verified with LibreOffice 25.8.4 and against its source:

1. `UpdateDocMode` = `3` (`FULL_UPDATE`) among the load arguments;
2. the workbook's own folder among the profile's trusted file locations — `SecureURL` under
   `/org.openoffice.Office.Common/Security/Scripting`, a list of folder URLs. LibreOffice
   updates links without asking only for a document in a trusted location (or at the *Low*
   macro security level, which trusts everything); anywhere else `FULL_UPDATE` falls back to
   asking, and headless nobody answers. Trusting the other file's folder is not enough. The
   folder URL must be spelled the way LibreOffice spells the document's own URL, which leaves
   characters such as `'`, `&`, `+`, `(` and `)` unescaped where Python's `Path.as_uri()`
   escapes them: written into the profile from Python, it misses folders named with those.
   `recalculate()` sets it from inside LibreOffice, from the URL it loads;
3. a `.uno:UpdateTableLinks` dispatch before `calculateAll()`, so that a saved copy of the
   other file's values (see above) is read again rather than reused. Without such a copy, the
   first two are enough.

The whole script is `_LIBREOFFICE_RECALC_SCRIPT`, in `odsslicer/libreoffice.py`.

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

`Cell.style` is `None` only if the cell has no owning `ODSReader`. A cell with no style of
its own resolves to its column's default cell style, where LibreOffice keeps the formatting of
a column formatted as a whole; with neither, it returns a `CellStyle` with every property
`None`/`False`, which a write turns into a real one — carrying the column's formatting over.
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
"/"), ("month", "long"), ...]`) and `.elapsed`, true for a time counted in full, `[HH]:MM`,
rather than around the clock.

Assign an existing one (from another cell, or by style name), or build one from scratch:

```python
sheet["B1"].style.number_format = sheet["A7"].style.number_format   # or = "N108"

pct = NumberFormat.create(table, "percentage", decimal_places=1)
eur = NumberFormat.create(table, "currency", decimal_places=2, currency_symbol="€", grouping=True)
dmy = NumberFormat.create(table, "date", components=[
    ("day", "long"), ("text", "/"), ("month", "long"), ("text", "/"), ("year", "long"),
])
hm = NumberFormat.create(table, "time", components=[("hours", "long"), ("text", "h"), ("minutes", "long")])
hours = NumberFormat.create(table, "time", components=[("hours", "long"), ("text", ":"),
                                                       ("minutes", "long")], elapsed=True)
sheet["C1"].style.number_format = pct
```

`create(reader, family, ...)` accepts `decimal_places`/`grouping`/`min_integer_digits` for
numeric families, `currency_symbol` (required for `"currency"`), `components` (required for
`"date"`/`"time"`), `elapsed=True` for a `"time"` format counting in full — `[HH]:MM`, where
26 hours show as 26:00 rather than 02:00 — and `font_color` for any family. A percentage's
sign goes where LibreOffice's standard format for the document's locale puts it: `50%` in
en-US, `50 %` in fr-FR, a no-break space before it in de-DE, `%50` in tr-TR, and bare after
the number for a locale LibreOffice does not know. `sheet["C1"].style.number_format = None`
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

Any of the three is `None` if out of range or the `Sheet` has no owning `ODSReader`; a column
is out of range past the columns the sheet declares, which LibreOffice limits to those it
uses. The print titles' columns count where they stand, though LibreOffice declares them
apart, in a group of their own. Like cell styles, the first write forks a private style reused on later writes — since these don't
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

## 12. Performance

Measured with `benchmarks/bench.py` (kept in the repo — run it to re-measure on your own
machine): synthetic workbooks of N rows × 5 columns (ids, text, floats, ratios, one formula
column), Apple Silicon laptop, Python 3.12.

| operation | 1,000 rows | 10,000 rows | 100,000 rows |
|---|---|---|---|
| generate + save (bulk writes) | 0.40 s | 4.1 s | 42 s |
| file size | 0.04 MB | 0.29 MB | 2.9 MB |
| open (XML parse) | 79 ms | 0.96 s | 11 s |
| first sheet access (`load()`) | 37 ms | 0.37 s | 5.3 s |
| full read (`to_numpy`) | < 1 ms | 4 ms | 75 ms |
| read / write one cell | < 1 ms | < 1 ms | ~1 ms |
| write a 1,000-cell range | 13 ms | 13 ms | 14 ms |
| `sort` 1,000 rows | 51 ms | 50 ms | 51 ms |
| `delete_row` (single) | 9 ms | 88 ms | 0.9 s |
| delete 10 rows, one by one | 81 ms | 0.86 s | 8.5 s |
| `delete_rows` (10 at once) | 8 ms | 86 ms | 1.3 s |
| `copy` a 1,000×2 block into new columns | 108 ms | 0.59 s | 5.3 s |
| `save` | 58 ms | 0.45 s | 4.1 s |
| peak memory (RSS) | 68 MB | 318 MB | 2.0 GB |

What the numbers mean in practice:

- **Time scales linearly** with document size for whole-document operations (open, save,
  full read), and the per-cell constants are small. **Memory is the real limit**: every cell
  is materialized (a bs4 XML element plus a `Cell`), costing ~4.5 KB per cell — a
  100,000-row × 5-column sheet peaks around 2.3 GB of RSS. Beyond that scale, `odsslicer` is
  the wrong tool; use a streaming reader like `python-calamine` for pure reading.
- **Local operations don't scale with document size**: reading or writing a cell, writing a
  range, sorting a range — their cost depends on the operation's own size only.
- **Deleting many rows: batch it.** Each `delete_row`/`delete_column` call runs one
  document-wide formula-reference adjustment pass, so a loop of N deletions pays N passes —
  `delete_rows([...])` pays exactly one (10-15× faster for 100 rows at 10k, and growing with
  document size).
- **Copying into new columns** pays for growing every existing row first (`grow_to`) — the
  cost is proportional to the sheet's height, not just the copied block.
- A subtle one, fixed in 0.10: writing values of a format with **no example anywhere in the
  document** (e.g. the first dates into a numbers-only sheet) used to trigger a full-document
  scan per cell (~33 ms each on a 10k-row sheet). The display-inference lookups are now lazy;
  the same writes cost ~1 ms each.

### How it compares to other readers

Measured with `benchmarks/compare_readers.py` on the competitors' home turf: a purely
numeric matrix (N rows × 5 float columns, one sheet) read in full into Python values.
Contenders: [`odfdo`](https://pypi.org/project/odfdo/) 3.24 (the other maintained full
read/write library) and [`python-calamine`](https://pypi.org/project/python-calamine/) 0.8
(the Rust streaming reader). Fresh subprocess per measurement, median of 3 runs, library
import excluded; same Apple Silicon laptop as above.

Read time:

| Rows (×5 float columns) | odsslicer 0.11 | odfdo | python-calamine |
|---|---|---|---|
| 100 | 25 ms | 4 ms | < 1 ms |
| 1,000 | 150 ms | 30 ms | 2 ms |
| 10,000 | 1.6 s | 0.30 s | 13 ms |
| 100,000 | 25 s | 9.2 s | 0.27 s |

Peak memory (RSS):

| Rows (×5 float columns) | odsslicer 0.11 | odfdo | python-calamine |
|---|---|---|---|
| 1,000 | 68 MB | 43 MB | 17 MB |
| 10,000 | 304 MB | 120 MB | 25 MB |
| 100,000 | 2.1 GB | 644 MB | 109 MB |

The honest conclusion: **for reading values in bulk, odsslicer is the slowest of the
three** — about 5-6× odfdo and ~100× python-calamine, which streams from compiled Rust
without building any DOM and stays nearly flat on memory. That is the structural price of
odsslicer's editable model (a full XML tree plus a `Cell` object per cell carrying formats,
styles, formulas and write support), not an accident — and it is why the README says to use
`python-calamine` when all you need is to read values fast. odsslicer's sweet spot is
reading *and rewriting* documents whose formatting must survive.

## 13. Known limitations

- **No calculation engine of its own.** Formulas are written and translated but not
  evaluated by `odsslicer` (`.value` is `None` until a spreadsheet application recalculates),
  and pivot tables are written as definitions only. Use
  [`recalculate()` / `save(recalculate=True)`](#7-recalculating-with-libreoffice) to have a
  local LibreOffice compute both. Without it, LibreOffice recomputes on open only the formulas
  with no cached result — those `odsslicer` wrote or rewrote — the volatile ones, and those in
  a cell with no style at all, its own or its column's: a styled total whose inputs changed
  shows its old result, unless LibreOffice is set to recalculate on load (see [From the
  command line](#from-the-command-line)). A pivot table needs an explicit refresh. Formulas that read another workbook come back
  as `Err:540` from a headless recalculation, unless `recalculate()` updates links
  (`update_links=True`, for workbooks you trust) — see
  [References to other workbooks](#references-to-other-workbooks).
- **Named ranges and 3D references** (`Sheet1:Sheet3.A1`) aren't translated by the friendly
  formula syntax — write them in ODF's bracket syntax directly (the `[` escape hatch).
- **Displayed-text locale, for numbers.** A date or time written into a cell with no number
  format does get the standard format of the document's locale (see [Displayed
  text](#displayed-text-how-text-is-produced-on-write)), but the layer that reads a
  `NumberFormat` renders numbers with a fixed `.`/`,` convention: it doesn't read the
  format's own `number:language`/`number:country`, so a number's separators may differ from
  the rest of the document. Real applications recompute display text on open.
- **Structural edits rewrite formulas and charts only.** Inserting or deleting rows/columns,
  or renaming a sheet, has formulas and charts follow the cells — a chart's ranges, and the
  cell its frame ends in — but leaves pivot-table source ranges, named ranges, and
  conditional-format or validation ranges as they were.
- **Partial rich text** (one bold word inside a sentence, a link on part of a cell's text) is
  flattened on read and not writable.
- **Not covered:** data validation / drop-down lists, autofilters, frozen panes, sheet-level
  protection, row/column grouping, charts and embedded images — which structural edits keep
  where they belong, see above — page layout/printing.
- **Padding after the data is not loaded.** Empty rows repeated more than 1,000 times at the
  bottom of a sheet (LibreOffice and Excel declare its full 1,048,576-row height that way) and
  more than 10 empty columns at its right stay out of the grid, of `sheet.size` and of full
  reads, though not out of the file — so does a last row holding no data: `cell.style` does
  not read the formatting there. Empty rows and
  columns between data are always loaded, however many: a sheet with a value or a note in row
  1,000,000 costs a million rows of memory. A warning is logged if a row-length inconsistency
  remains after that cleanup.

---

## 14. Appendix: notable bug fixes

Bugs found and fixed while developing the module, before the first release — all covered by
regression tests. Those found since are in the
[CHANGELOG](https://github.com/antnardo/odsslicer/blob/master/CHANGELOG.md), each with the
issue it was reported in.

1. **Reading boolean cells**: the `"boolean"` format looked up `office:value` instead of
   `office:boolean-value`, and converted with `bool(s)` — `True` for the non-empty string
   `"false"`. A real ODF boolean always read back as `False`. Fixed.
2. **`Cell.text`/`str(cell)` returned the literal string `"None"`** for a cell whose
   `<text:p>` is empty or spread across several nodes (`<text:span>`), because bs4's
   `p.string` is `None` whenever there isn't exactly one text child. Fixed with
   `p.get_text()`.
3. **Growing an empty sheet (or one with a trailing empty row) could corrupt it on the next
   save/reload** — `load()` discarded such rows from memory but not from the XML, and
   `grow_to` appended after them. Fixed: stray rows were discarded first — and are now taken
   back into the grid instead, since they can hold charts or formatting
   ([#10](https://github.com/antnardo/odsslicer/issues/10)).
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
