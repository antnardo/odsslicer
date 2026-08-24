# odsslicer

[![CI](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml/badge.svg)](https://github.com/antnardo/odsslicer/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/odsslicer)](https://pypi.org/project/odsslicer/)

Python reader **and writer** for `.ods` files (OpenDocument Spreadsheet — LibreOffice /
OpenOffice Calc), with a numpy-inspired indexing API:

```python
from odsslicer import ODSReader

table = ODSReader("workbook.ods")
sheet = table.sheet("Sheet1")

sheet["A1"].value          # typed value: str / float / bool / date / time / None
sheet["A1:B3"].to_numpy()  # any block as a numpy array
sheet[:, 0]                 # entire column A

sheet["C1"].formula = "SUM(A1:A10)"
sheet["C1"].style.bold = True
table.save("out.ods")                     # add recalculate=True to have LibreOffice compute formulas/pivots
```

`odsslicer` works directly on the ODF XML (via BeautifulSoup/lxml), so it preserves what
other tools tend to drop — cell formats (currency, percentage, date, time), formulas, merged
and repeated cells, styles, comments — and writes them back faithfully. It has no calculation
engine of its own: like ODF itself, it describes what to compute — and can hand the file to a
local LibreOffice (`save(..., recalculate=True)`) to compute formulas and pivot tables for you.

**Full documentation with an example for every feature: [DOCS.md](https://github.com/antnardo/odsslicer/blob/master/DOCS.md).**

## Installation

```bash
pip install odsslicer
```

Requires Python ≥ 3.10. Dependencies (`beautifulsoup4`, `lxml`, `numpy`) are installed
automatically. For local development:

```bash
git clone https://github.com/antnardo/odsslicer.git
cd odsslicer
pip install -e ".[test]"
```

## What it does

Each line links to the detailed section (with examples) in [DOCS.md](https://github.com/antnardo/odsslicer/blob/master/DOCS.md).

| Area | Feature | Example |
|---|---|---|
| **Reading** | [numpy-style indexing](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#indexing-and-slicing) — addresses, slices, out-of-range returns empty cells | `sheet["A1"]`, `sheet[0, 0]`, `sheet["A1:B3"]`, `sheet[:, 0]` |
| | [Typed cells](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#cells-cell) — value, displayed text, format, address | `cell.value`, `cell.text`, `cell.format` |
| | [Arrays](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#arrays-arrayvalues) — `to_list()`, `to_numpy()`, numpy-like shapes | `sheet["A1:B3"].to_numpy()` |
| **Writing** | [Values](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#cellvalue-and-save) of every ODF type, then `save()` | `sheet["A1"].value = 42.5` |
| | [Ranges at once](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#writing-a-range-at-once) — broadcast or element-wise | `sheet["A1:C1"].value = [1, 2, 3]` |
| | [Auto-unrolling](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#automatic-unrolling-of-repeated-and-merged-cells) of repeated/merged cells on first write | transparent |
| | [Auto-growth](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#automatic-sheet-growth) when writing past the sheet's extent | `sheet["Z100"].value = 1` |
| | [Displayed text](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#displayed-text-how-text-is-produced-on-write) inferred from the document's own formats | `"50,00 %"`, `"05/01/30"` |
| **Files & sheets** | [New file from scratch](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#creating-a-new-file-from-scratch) | `ODSReader.new()` |
| | [Add / rename / reorder / delete sheets](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#adding-renaming-reordering-deleting-sheets) — renaming fixes cross-sheet formulas | `table.rename_sheet("Sheet1", "Q4")` |
| **Structure** | [Delete rows/columns](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#deleting-rows-and-columns) — formula references follow, batchable | `sheet.delete_rows([3, 7, 20])` |
| | [Copy cells/ranges](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#copying-cells-and-ranges) — value + formula + style, overlap-safe | `sheet.copy("A1:B2", "D5")` |
| | [Sort a range](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#sorting-a-range) — stable, `None` last, formulas follow their row | `sheet.sort("A2:C10", by=1)` |
| | [Merge / unmerge](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#merged-cells) + read merge state | `sheet.merge("A1:C2")`, `cell.merge_range` |
| **Formulas** | [Write in ordinary syntax](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#writing-a-formula), translated to ODF's | `cell.formula = "IF(A1>0,1,-1)"` |
| | [Read back in ordinary syntax](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#reading-a-formula-back-in-ordinary-syntax) | `cell.formula_friendly` |
| | [Fill across a range](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#filling-a-formula-across-a-range) like a fill handle | `cell.fill_formula("B3:B10")` |
| | [`{r}`/`{c}` templates](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#formula-templates-with-rc) for per-cell patterns | `sheet["A2:A10"].formula = "$A{r-1}+1"` |
| | [Pivot tables](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#6-pivot-tables) — definition written, computed by the spreadsheet | `sheet.create_pivot_table(...)` |
| | [Recalculate with LibreOffice](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#7-recalculating-with-libreoffice) — formulas + pivot refresh, headless, no UNO needed | `table.save("out.ods", recalculate=True)` |
| **Styles** | [Read](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#reading-a-cells-style) and [write](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#writing-cell-styles) cell styles — font, colors, alignment, borders, rotation, wrap… | `cell.style.bold = True` |
| | [Copy a style](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#copying-a-style-from-one-cell-to-another) in one shot | `b.style = a.style` |
| | [Number formats](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#number-formats) — read, assign, or create from scratch | `NumberFormat.create(table, "currency", ...)` |
| | [Conditional formats](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#conditional-number-formats) (e.g. negatives in red) | `fmt.add_condition("value()<0", red)` |
| | [Row / column / sheet styles](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#row-column-and-sheet-styles) — height, width, visibility, tab color | `sheet.column_style(0).width = "5cm"` |
| **Annotations** | [Comments](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#9-cell-comments) — text, author, date, visibility | `cell.comment = "Check this"` |
| | [Hyperlinks](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#10-cell-hyperlinks) | `cell.hyperlink = "https://…"` |
| **Document** | [Properties](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#11-document-properties) — title, author, keywords, typed custom properties | `table.properties.title = "Q4"` |

See [Known limitations](https://github.com/antnardo/odsslicer/blob/master/DOCS.md#13-known-limitations) for what's deliberately out of scope
(no calculation engine, no charts/images, no partial rich text…).

## Are there already equivalent PyPI modules?

| Package | Read/Write | Latest release | Notes |
|---|---|---|---|
| [`odfpy`](https://pypi.org/project/odfpy/) | Low-level R/W | 1.4.1 — Jan 2020 | Dormant, but still the brick pandas uses internally |
| [`odfdo`](https://pypi.org/project/odfdo/) | Full R/W | 3.24 — Aug 2026 | Actively maintained modern fork of odfpy; generic DOM-like API for all ODF document types |
| [`pyexcel-ods3`](https://pypi.org/project/pyexcel-ods3/) | R/W | 0.6.1 — Jan 2022 | Inactive; values only, no styles/formulas |
| [`ezodf`](https://pypi.org/project/ezodf/) | R/W | 0.3.2 — Dec 2015 | Abandoned |
| [`pandas`](https://pandas.pydata.org/) (`engine="odf"`) | Read (via odfpy) | follows pandas | Convenient for data frames; loses formulas and fine-grained formats |
| [`python-calamine`](https://pypi.org/project/python-calamine/) | Read-only (Rust), fast | 0.8 — Jul 2026 | The best option for fast pure reading |
| [`pandas-ods-reader`](https://pypi.org/project/pandas-ods-reader/) | Read-only → DataFrame | 1.0.2 — May 2025 | Maintained, limited scope |

(Versions and dates as of August 2026.)

Where `odsslicer` sits: a **spreadsheet-shaped** API (`sheet["A1:B3"]`, numpy arrays, fill
handles, copy/sort/merge) rather than a generic ODF DOM, with **read *and* write** of the
things data-oriented tools usually lose — formats, formulas (in ordinary syntax), styles,
merged cells, comments, pivot definitions — and every write verified against a real
LibreOffice. If you only need to read values fast, use `python-calamine`; if you need to
manipulate arbitrary ODF documents (text, presentations) at the XML level, use `odfdo`.

## Tests

```bash
pip install -e ".[test]"
pytest
```

The main suite (`tests/test_odsslicer.py`) covers every feature above plus regression tests
for the bugs fixed along the way; it runs on every push/PR via
[GitHub Actions](https://github.com/antnardo/odsslicer/blob/master/.github/workflows/ci.yml) across Python 3.10 to 3.13.

`tests/test_libreoffice_consistency.py` is an opt-in suite that hands files `odsslicer` wrote
to a real, local LibreOffice (`soffice --headless --convert-to fods`) and inspects what
LibreOffice itself made of them — the strongest available signal that a write is genuinely
valid ODF, not just something our own reader happens to parse back. It skips automatically if
no `soffice`/`libreoffice` binary is on `PATH`.

## Versions

Version numbers are derived automatically from git tags (via `setuptools-scm`) and follow
[Semantic Versioning](https://semver.org/) — while the major version stays `0`, the API can
still change between minor versions. See
[CHANGELOG.md](https://github.com/antnardo/odsslicer/blob/master/CHANGELOG.md) (and the
[Releases](https://github.com/antnardo/odsslicer/releases) page) for what changed in each
version.

## License

[MIT](https://github.com/antnardo/odsslicer/blob/master/LICENSE) — reuse with essentially no restriction, just keep the copyright notice.

## Project name

**`odsslicer`**, to reflect the module's real differentiator — numpy-style indexing/slicing by
cell address — rather than a generic "ods reader".
