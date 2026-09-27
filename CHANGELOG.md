# Changelog

All notable changes to `odsslicer` are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/) — while the major version stays `0`, the API can
still change between minor versions.

## [Unreleased]

### Fixed

- **What the rows below the data hold is no longer deleted when the sheet grows or rows are
  inserted** ([#10](https://github.com/antnardo/odsslicer/issues/10)). A last row where no
  cell holds a value — one with a chart or a shape anchored to a cell, a formatted cell — was
  left out of the grid, then deleted from the file as soon as the sheet grew or a row was
  inserted, anywhere; so was whatever lay below more than 1,000 empty rows. A report template
  lost the chart anchored below its table as soon as a day was added. Writing past the data
  now takes those rows back with what they hold, and inserting rows moves them down; inserting
  or deleting columns moves what they hold sideways, where it stayed put. Everything lands
  where LibreOffice puts it for the same edit.
- **A note counts as content, as in LibreOffice**: the grid reaches a note on an empty cell
  below or right of the data, and `cell.comment` reads it, where it gave `None` — `sheet.size`
  grows accordingly. A note more than 1,000 empty columns right of the data was deleted from
  the file by a plain save.
- Inserting columns into a sheet far from its maximum width no longer takes them off the
  column definitions past the data, whose columns lost their widths. A sheet padded to its
  application's full size, 1,048,576 rows by 16,384 columns (1,024 before LibreOffice 7.4),
  still gives back what an insertion adds, so as not to exceed it.
- **`cell.value` takes the values numpy and pandas hand out**
  ([#11](https://github.com/antnardo/odsslicer/issues/11)): numpy's integers, booleans,
  `datetime64` and `timedelta64` raised `TypeError`, and so did writing `df.to_numpy()` for a
  DataFrame of integers, a numpy scalar to a range, or a `decimal.Decimal`, as database
  drivers return them. They are written as the value they stand for, and `cell.value` gives
  back the Python type — a numpy `float64` used to stay one.
- **A NaN leaves the cell empty**, as pandas writes a missing value to a spreadsheet: it was
  written as `office:value="nan"`, which LibreOffice shows as 0 and counts in sums and
  averages. numpy's `NaT` does the same, and an infinite number raises `ValueError` instead
  of showing as 0.
- **Deleting the last row of a range no longer takes in the row after it**
  ([#12](https://github.com/antnardo/odsslicer/issues/12)). `delete_rows` and
  `delete_column` kept the address of a reference to a deleted cell, which then read the cell
  taking its place: a total's `SUM(A1:A6)` with row 6 deleted went on reading A6, the total
  itself, a circular reference. As in LibreOffice, a range whose first or last row is deleted
  now keeps the rows left of it, and a reference to a deleted cell, or a range deleted whole,
  becomes `#REF!`.
- **`delete_column` no longer raises `AssertionError` past the columns a sheet declares**
  ([#13](https://github.com/antnardo/odsslicer/issues/13)). LibreOffice declares only the
  columns it uses, and `ODSReader.new()` a single one: a column written past them could not
  be deleted.
- **The print titles' columns no longer shift every column's width.** LibreOffice declares
  the columns repeated on every printed page apart, in a group, which odsslicer skipped: each
  column read the width and visibility of the one after it, and inserting or deleting columns
  moved the wrong definitions. They now count where they stand.
- **References to other sheets written as LibreOffice writes them now follow insertions,
  deletions and renames** ([#14](https://github.com/antnardo/odsslicer/issues/14)): an
  absolute sheet, `$Data.A5` — what clicking a cell of another sheet gives — a name starting
  with an accented letter, `Élèves.A5`, and a range whose end is on its start's sheet,
  `$Data.A1:.A5`. None of them moved, and a renamed sheet left them pointing at a sheet that
  no longer existed, `#REF!` or `#NAME?` in LibreOffice; the end of such a range moved with
  the rows of the formula's own sheet instead. `cell.formula` takes both forms too, where
  `"$Data.A5"` was written untranslated.
- **Charts follow inserted and deleted rows and columns, and a renamed sheet**
  ([#15](https://github.com/antnardo/odsslicer/issues/15)), as LibreOffice has them follow.
  A chart's ranges stayed as they were: a day inserted in a report's table was left out of
  its chart, and a renamed sheet left the chart with no range at all in LibreOffice. The cell
  a chart's frame ends in, from which LibreOffice sizes it on open, stayed too, so that a row
  inserted above a chart made it a row shorter.
- **The text of a date or a time follows its cell's format**
  ([#16](https://github.com/antnardo/odsslicer/issues/16)), as `cell.text` reads it and as
  readers that do not recompute it show it. A format naming days or months — `NNNN, MMMM D,
  YYYY` — could not be rendered, and the text took another cell's layout: `09/27/26` for
  "Sunday, September 27, 2026". The names now come in the format's language, the document's
  if it gives none, from LibreOffice's for 67 locales; an example is taken only from a cell
  of the same format. Fractions of a second were dropped, `01:24` for `01:24.75`: they come
  as the format asks, rounded as LibreOffice rounds them.
- **A duration under a day shows as a duration.** Written into a cell with no format, it got
  the format of a time of day, `07:30:00 AM` in an en-US document; it gets `[HH]:MM:SS`, as a
  longer one does.
- **A percentage's sign goes where the document's locale puts it**
  ([#17](https://github.com/antnardo/odsslicer/issues/17)): `NumberFormat.create` wrote a
  space before it in every document, `50 %` in en-US, where LibreOffice writes `50%`. And the
  text written from any percentage or currency format follows the format's own text, where a
  space before the sign or the symbol was taken for granted.
### Added

- **`NumberFormat.create(..., elapsed=True)`** makes an elapsed-time format, LibreOffice's
  `[HH]:MM`, where 26 hours show as 26:00 rather than 02:00, and `NumberFormat.elapsed`
  reads it ([#17](https://github.com/antnardo/odsslicer/issues/17)).

### Changed

- **A duration in a format counting time in full reads as a `timedelta`, whatever its
  length** ([#20](https://github.com/antnardo/odsslicer/issues/20)): 7 h 30 in a `[HH]:MM`
  cell read as `time(7, 30)`, as any value within a day did, so that a timesheet's column of
  durations mixed `time` and `timedelta`. A `timedelta` written into a cell with no format,
  which gets `[HH]:MM:SS`, now reads back as a `timedelta`. A value within a day in any other
  format still reads as a `time`. `Sheet.copy` and `Sheet.sort` give a cell its format before
  its value, which reads back through it.
- **Writing into a merge's master cell keeps the merge**
  ([#19](https://github.com/antnardo/odsslicer/issues/19)), as typing into it does in
  LibreOffice. A value, a formula or a style written there undid it: a long date written into
  a title merged across three columns showed as `###`. Writing into a hidden cell of the merge
  still undoes it, so that the value written shows.
- **DOCS.md states the shapes of selections as they are**
  ([#21](https://github.com/antnardo/odsslicer/issues/21)), where it said they followed numpy:
  a column keeps its axis, `(n, 1)`, where numpy drops it, and an address spanning one row is
  one-dimensional, `sheet["A12:C12"]` giving `(3,)` where `sheet[11:12, 0:3]` gives `(1, 3)`.
- **DOCS.md no longer says that LibreOffice recomputes every formula on open**
  ([#18](https://github.com/antnardo/odsslicer/issues/18)). At its default setting, it
  recomputes the formulas with no cached result, the volatile ones, and those in a cell with
  no style at all: a total in bold whose inputs `odsslicer` changed shows its old result
  without `save(recalculate=True)`.
- The PyPI page lists the same keywords as the repository's GitHub topics, so a search for
  what the package does finds it.

## [0.13.2] — 2026-09-27

### Fixed

- **`recalculate()` no longer has LibreOffice copy its result over the workbook in place**
  ([#9](https://github.com/antnardo/odsslicer/issues/9)), and neither does
  `save(recalculate=True)`. LibreOffice saved the recalculated workbook to a temporary file of
  its own, then copied it over the workbook: a program reading the workbook meanwhile could get
  a partial zip (`BadZipFile`), and a copy stopped midway could leave it unreadable, as `save()`
  could until 0.13.0. LibreOffice now saves its result to a temporary file in the workbook's
  folder, which is renamed over the workbook once LibreOffice reports the save complete: a
  reader gets the old version or the new one, and a failed run leaves the workbook as it was,
  with no temporary file behind. LibreOffice still loads the workbook where it is, so that its
  links resolve and its folder is trusted as before, and saves it in its own format. The
  workbook keeps its permissions; a read-only one now raises `PermissionError` before
  LibreOffice starts, where it gave `RuntimeError`.
- **A timeout or Ctrl-C now stops LibreOffice itself**, on macOS and Linux. `recalculate()`
  killed the process it had started, which is often a wrapper — Homebrew's `soffice` is a shell
  script that runs the real one — and LibreOffice carried on: it rewrote the workbook seconds
  after `recalculate()` had raised `RuntimeError`. LibreOffice now runs in a process group of
  its own, killed as a whole, and the lock files it leaves next to the workbook are deleted.
- When the recalculation script fails inside LibreOffice, `RuntimeError` gives its traceback,
  where it said that the script apparently didn't execute.

## [0.13.1] — 2026-09-26

### Fixed

- **Writing past the columns of a run of repeated rows no longer corrupts the run**
  ([#5](https://github.com/antnardo/odsslicer/issues/5)). LibreOffice stores a run of
  identical rows, empty ones included, as one repeated element. Writing a range past the
  sheet's width inside such a run saved its first value onto every row of the run and the
  next ones further right, one column in two — `D2:D4 = [[1], [2], [3]]` gave D = 1 and F = 2
  on all of rows 2 to 6 — while the sheet read back as expected until the save. Growing the
  sheet now widens the run's element once, and writing unrolls it first, as it already did
  within the sheet's width.
- `delete_column` no longer raises `ValueError` on a run of repeated rows whose cells are
  repeated too, which is how LibreOffice writes empty ones.
- On a sheet ending in a run of repeated empty rows, writing inside the run no longer raises
  `IndexError`, and writing below it no longer lands one row too low.
- **Empty rows and columns between data no longer shift the cells after them**
  ([#6](https://github.com/antnardo/odsslicer/issues/6)). Loading left out of the grid any
  run of more than 10 empty columns, and any repeated element of more than 1,000 empty rows,
  wherever they stood: with data in A1 and Z1 alone, `sheet["B1"]` read Z1's value and
  `sheet["Z1"]` read `None`, and writing to Z1 landed in AX1. Only padding after the data is
  left out now, so `sheet.size` grows for sheets with such gaps. It shrinks for sheets with
  empty rows past the padding, a million rows down, which the grid showed just below the
  data: writing there landed at the bottom of the sheet.
- **Writing past the data no longer lands beyond the formatted columns that follow it.**
  LibreOffice writes empty cells as far as the last formatted column. Past 10 of them, the
  grid left them out but the file kept them, and new cells went after them: with 15,
  `sheet["D1"]` landed in S1. The sheet now grows into those cells, keeping their formatting.
- Writing into a merged cell whose range runs into columns left out of the grid — Excel
  merges notes across empty columns past the data — no longer raises `IndexError`; nor does
  undoing such a merge, or deleting or inserting rows or columns through it.
- **A date or time written into a cell with no number format no longer shows as a number**
  ([#7](https://github.com/antnardo/odsslicer/issues/7)). It was stored with its type but
  no format: past the sheet's declared columns, where it grows, LibreOffice showed 2022-03-07
  as 44627, and within them a duration of 128:45 as 08:45:00. Such a cell now gets the format
  LibreOffice would give the value if typed there, the standard one of the document's
  locale: `07/03/22`, `07/03/22 13:45` and `09:30:00` in a fr-FR document, `03/07/22`,
  `03/07/22 01:45 PM` and `09:30:00 AM` in en-US, `[HH]:MM:SS` for a duration everywhere. The
  formats of 67 locales are built in, taken from LibreOffice 25.8; any other locale, or a
  document with no language, gets ISO 8601. A cell that has a number format keeps it, as in
  LibreOffice, and the displayed text of a date or time now follows the cell's own format
  first, before an example taken from another cell.
- **A cell with no style of its own resolves to its column's default cell style**, where
  LibreOffice keeps the formatting of a column formatted as a whole: `cell.style` reads it,
  setting a style property on such a cell carries the column's formatting over instead of
  dropping it, background and number format included, and `Sheet.copy` and `Sheet.sort`
  carry it along.
- A time format with AM/PM showed 13:45 as `13:45 PM` in the displayed text odsslicer
  writes: it now counts the hours on a 12-hour clock, `01:45 PM`.
- The displayed text of a date in a format LibreOffice takes from the system
  (`number:format-source="language"`, as it saves a column in the system's short date) is no
  longer rendered from that format's placeholder elements — `7/3/22` where LibreOffice shows
  `07/03/2022` on a French macOS — but taken from another cell's, else written in ISO 8601.

## [0.13.0] — 2026-09-26

### Added

- **`recalculate(path, update_links=True)`**, and `save(..., recalculate=True,
  update_links=True)`, have LibreOffice update the workbook's links before recalculating: a
  formula reading another workbook, written out or built by `INDIRECT()`, computes instead of
  coming back as `Err:540`, and from that file as it is now rather than from values
  LibreOffice saved from it earlier. Opt-in, for workbooks you trust: it lets a workbook read
  any file and fetch any URL its formulas name, which is what LibreOffice's own check guards
  against. For the run, the throwaway profile trusts the workbook's folder, and the workbook's
  macros stay disabled. `save()` raises `ValueError` for `update_links=True` without
  `recalculate=True`; without `update_links`, nothing changes.

### Fixed

- **`save()` no longer truncates the workbook before rewriting it**
  ([#8](https://github.com/antnardo/odsslicer/issues/8)). It wrote the new zip straight into
  its target, the source file by default: a program reading the file meanwhile got a partial
  zip (`BadZipFile`), and a save stopped midway — an exception, Ctrl-C, a crash — left the
  workbook unreadable, its previous version gone. The new file is now written under a
  temporary name in the same folder, flushed to disk, then renamed over the target with
  `os.replace()`: a reader gets the old version or the new one, and an interrupted save
  leaves the old one as it was, with no temporary file behind. The saved file keeps the
  permissions of the one it replaces, a symbolic link is followed, and a read-only file still
  raises `PermissionError`. Saving now needs write access to the folder, and another hard link
  to the old file keeps the old content. With `recalculate=True`, LibreOffice's own save,
  which follows, still copies its result over the file in place.

### Documentation

- **Recalculating from LibreOffice's command line** (DOCS.md, section 7):
  `soffice --headless --convert-to ods`, how to force a full recalculation on load (the
  profile's *Recalculation on File Load* setting, `ODFRecalcMode`, set through a throwaway
  `-env:UserInstallation` profile), why the result has to be written elsewhere and moved back
  (converting onto the input fails, yet `soffice` exits with status 0), and when to prefer
  `recalculate()`, which also refreshes pivot tables.
- **References to other workbooks come back as `Err:540`** after a headless recalculation
  that does not update links — and `INDIRECT()` ones even after a plain conversion, since
  volatile formulas are recomputed on every load. No command-line option or configuration
  setting lifts it: `--convert-to` never allows link updates. What does, and is now
  documented: loading the workbook with `UpdateDocMode=FULL_UPDATE` from a folder the profile
  trusts, spelled as LibreOffice spells URLs, and dispatching `.uno:UpdateTableLinks` before
  recalculating — what `update_links=True` does.

## [0.12.3] — 2026-09-26

### Fixed

- **A date-time cell, or a duration of 24 hours or more, no longer makes its whole sheet
  unreadable** ([#4](https://github.com/antnardo/odsslicer/issues/4)). Dates and times were
  parsed with fixed patterns that took nothing but a bare date and a whole-second duration
  under a day, so loading a sheet holding `30/11/2023 13:00`, `128:45:00`, a negative
  duration or a fraction of a second raised `ValueError` — even for `sheet["A1"]`. A
  date-time now reads as a `datetime.datetime`, and a duration as a `datetime.time` when it
  fits in a day, fractional seconds included, or as a `datetime.timedelta` otherwise.
  Everything that read before reads the same.
- **Date-times and durations can be written**: `cell.value` accepts `datetime.datetime` and
  `datetime.timedelta`, which raised `TypeError` — so neither could `Sheet.copy` or
  `Sheet.sort` move such a cell — and stores them as LibreOffice does. A `datetime.time`
  keeps its microseconds, which were dropped. `Sheet.sort` orders a column mixing dates and
  date-times, or times and durations.
- **A value that cannot be read as its declared type falls back to its displayed text**
  instead of failing the sheet (a malformed date, a duration counted in months, an unknown
  value type): one `UserWarning` per sheet names such cells, and `cell.raw_value` keeps the
  value as written.
- Writing a date into a cell whose format shows a time no longer raises `AttributeError`
  when no other cell shows how to display it: it shows as its midnight.
- **The source distribution no longer ships the `rsc/` folder** (the wheels were never
  affected). `setuptools-scm` packs every tracked file, and that folder held development
  references: the OASIS OpenDocument 1.2 specification (two PDFs, under OASIS copyright
  rather than the package's MIT license), a blank LibreOffice document and raw XML notes —
  enough to take the 0.12.2 sdist to 4.3 MB instead of about 300 KB. The folder is no longer
  tracked; neither the package nor the tests used it.

## [0.12.2] — 2026-09-20

### Fixed

- **Reading every cell of an empty sheet no longer crashes.** A selection holding no cells at
  all (`sheet[:, :]` on a sheet with no rows) was mistaken for a single cell, so `.to_list()`
  raised `AttributeError: 'list' object has no attribute 'value'` — hit by any plain scan over
  a workbook that happens to contain one empty sheet. Such a selection is now a zero-length
  row: `.to_list()` returns `[]`, `.size` is `(0,)`, and writing to it does nothing.

## [0.12.1] — 2026-09-20

### Fixed

- **A cell holding several lines no longer reads back as its first line only.** ODF stores a
  multi-line cell (Ctrl+Enter in a spreadsheet) as one `<text:p>` per line; `cell.text` — and
  a string `cell.value` — only looked at the first one, silently dropping the rest of what the
  spreadsheet displays. All the paragraphs are now joined with `\n`, a comment's own
  paragraphs still excluded.
- Writing a value containing `\n` now writes one paragraph per line, the way applications do:
  a literal newline inside a single paragraph is plain whitespace to ODF and would not
  survive a round trip through a stricter reader. Rewriting a cell with fewer lines drops the
  paragraphs left over.

## [0.12.0] — 2026-09-14

### Added

- **`Sheet.insert_rows(row, count=1)` / `insert_row(row)` and `Sheet.insert_columns(col,
  count=1)` / `insert_column(col)`** — insert blank rows or columns, like a spreadsheet's
  "insert rows above": formula references anywhere in the document follow the cells they
  point at (a range straddling the insertion point stretches), a merge straddling it grows,
  and column widths stay with their columns. On files that declare the full application grid
  through filler rows/columns (LibreOffice, Excel), the filler is given back so the document
  never exceeds the maximum grid size.

### Changed

- The formula-reference rewriting behind `delete_rows`/`delete_column` now shares one
  implementation with insertion (no behaviour change).

## [0.11.1] — 2026-09-10

### Fixed

- **Styling a cell twice no longer loses the first properties in the spreadsheet**
  ([#1](https://github.com/antnardo/odsslicer/issues/1)). Forking a cell its own private
  style linked back to the previous one through `style:parent-style-name` — but only a
  *named* style (from `styles.xml`) is an addressable ancestor: LibreOffice ignores an
  automatic parent and falls back to `Default`, so borders, bold, alignment and the rest
  silently disappeared on screen, while `odsslicer` — resolving the chain itself — still read
  them back. Forks now carry the resolved properties (and the number format) in their own
  XML, and only link to a genuinely named ancestor. Files written by 0.9–0.11 are repaired
  cell by cell as soon as a style is written to them again.

### Added

- `benchmarks/compare_readers.py` and a "How it compares to other readers" table in DOCS.md:
  measured read-speed/memory comparison against `odfdo` and `python-calamine` on a purely
  numeric matrix — quantifying the README's advice that pure bulk reading is
  `python-calamine`'s territory, not ours.

## [0.11.0] — 2026-08-26

### Added

- **"Wild" fixture suite** (`tests/wild/` + `tests/test_wild_files.py`): six real-world
  `.ods` files written by other generators — Excel 16 (two builds), LibreOffice 3.5
  from 2012, LibreOffice 26.2 on Linux and Windows (open data), plus a Google Sheets export
  (which turns out to be converted server-side by a headless LibreOfficeDev 6.0) —
  exercised end to end: open, exact sheet
  sizes, full read, write, save round-trip, and (opt-in) reopening by a real LibreOffice.
  Person names present in the published originals were redacted before inclusion; sources and
  licenses in `tests/wild/README.md`.

### Fixed

- **Files without `settings.xml` no longer fail to open.** Excel omits it (it is optional in
  ODF, as are `styles.xml` and `meta.xml`, both now optional too with minimal stand-ins), and
  `save()` now writes the regenerated parts even when the source package lacked them.
- **Grid fillers no longer blow up `Sheet.load`**: Excel and LibreOffice declare the sheet's
  full extent with an empty cell repeated 16,384 times at the end of each row (and an
  all-empty row block repeated ~1,048,000 times), which used to materialize millions of
  `Cell` objects — opening a 50 KB file took minutes. Rows are now normalized to the sheet's
  real width at load time (only trailing *empty* runs are clamped; no data moves), bringing
  those opens down to milliseconds.
- **Ragged rows (Excel files) crashed range reads** with `IndexError`: the same
  normalization pads short rows, so every sheet is rectangular as the rest of the API
  assumes.

## [0.10.0] — 2026-08-24

### Changed

- **`ODSReader.sheet(name)`, `delete_sheet`, `rename_sheet` and `move_sheet` now raise
  `KeyError`** (instead of `IndexError`) for an unknown sheet name — the natural exception for
  a lookup by name. Out-of-range row/column indexes (`delete_row`/`delete_column`) still raise
  `IndexError`.
- **All console output goes through the standard `logging` module** (logger name
  `"odsslicer"`) instead of `print`: load-time details at `DEBUG` (`INFO` when created with
  `verbose=True`), anomalies — such as rows of inconsistent lengths — at `WARNING`. Configure
  logging to see them; nothing is printed directly anymore.

### Added

- **`Sheet.delete_rows([...])`** — remove many rows in one operation: the document-wide
  formula-reference adjustment runs once instead of once per row (10-15× faster for 100 rows,
  more on big documents). `delete_row` is now a thin wrapper over it.
- **Complete type annotations** across the whole API, checked by mypy in CI
  (`disallow_untyped_defs`), and a **`py.typed` marker** so downstream type checkers can
  verify code using the package.
- A benchmark harness (`benchmarks/bench.py`) and a measured **Performance** section in
  DOCS.md (timings and memory at 1k/10k/100k rows, practical limits, usage advice).
- This changelog.

### Performance

- Writing values of a format with no example anywhere in the document used to scan the whole
  document per cell (~33 ms each on a 10k-row sheet): the display-inference candidate lookup
  is now lazy — ×32 on the measured case, and the always-running forward scan is gone from
  every write path (range writes and `sort` got ~40-50% faster too).

### Fixed

- `export_content_xml()` crashed when the reader had been opened with a `str` path rather
  than a `Path` (`self.file` is now always normalized to a `Path`).

## [0.9.1] — 2026-08-24

### Fixed

- The source distribution no longer ships two private test fixture files inadvertently
  included in the 0.1.0 and 0.9.0 sdists (the wheels were never affected). The files were
  also purged from the repository's entire git history, and the old sdists were removed from
  PyPI.

### Changed

- The former single 3,850-line `classes.py` is split into focused modules (`addresses`,
  `constants`, `xmlutils`, `formulas`, `styles`, `cell`, `sheet`, `properties`,
  `libreoffice`, `reader`); `odsslicer.classes` remains as a compatibility shim, so every
  existing import keeps working.
- `recalculate()` hardening: the host Python's environment (`PYTHONPATH`, `PYTHONHOME`,
  `LD_LIBRARY_PATH`, and any foreign interpreter on `PATH` — e.g. an active venv) no longer
  leaks into the LibreOffice subprocess, whose embedded Python would otherwise crash on some
  builds.
- The tests and example directories are stripped down to what the suite actually needs.
- CI now also runs the LibreOffice consistency suite (real `soffice` round-trips) on Ubuntu.

## [0.9.0] — 2026-08-23

Feature-complete pre-1.0 release. 0.1.0 was a reader with basic value writing; 0.9.0 is a
full read/write toolkit, with every write verified against a real LibreOffice.

### Added

- **Structure**: `ODSReader.new()` (create a file from scratch), `add_sheet`, `rename_sheet`
  (fixes cross-sheet formula references), `move_sheet`, `delete_sheet`, `Sheet.delete_row`/
  `delete_column` (formula references throughout the document follow), `Sheet.copy`
  (value + formula + style, overlap-safe), `Sheet.sort` (stable, `None` last, formulas follow
  their row), `Sheet.merge`/`unmerge` plus `Cell.is_merged`/`merge_master`/`merge_span`/
  `merge_range`, whole-range writes, automatic sheet growth, automatic unrolling of
  repeated/merged cells on write.
- **Formulas**: `Cell.formula` written in ordinary spreadsheet syntax (translated to ODF),
  `Cell.formula_friendly` (reverse translation), `Cell.fill_formula` (fill-handle semantics),
  `{r}`/`{c}` per-cell templates on range writes, `Sheet.create_pivot_table` (pivot
  definitions), and `recalculate()` / `save(recalculate=True)` — delegate computing formulas
  and refreshing pivots to a headless local LibreOffice (throwaway profile, no `python-uno`,
  command configurable via `LIBREOFFICE_COMMAND`).
- **Styles**: `Cell.style` read *and* write (font, colors, alignment, borders, diagonals,
  rotation, wrap, shrink, protection…), private style forked on first write, style copy
  (`b.style = a.style`), `NumberFormat` read/assign/`create(...)` from scratch, conditional
  formats via `add_condition`, `Sheet.row_style`/`column_style`/`style` (height, width,
  visibility, tab color) read/write, displayed text inferred from the document's own formats.
- **Annotations & metadata**: `Cell.comment` (text, author, date, visibility),
  `Cell.hyperlink`, `ODSReader.properties` (title, subject, author, keywords, typed custom
  properties).
- Opt-in test suite round-tripping every write through a real LibreOffice; README rewritten
  as a concise overview with the full API reference moved to DOCS.md.

### Fixed

- Boolean cells always read back as `False` (`office:boolean-value` was never consulted).
- `Cell.text` returned the literal string `"None"` for empty or multi-node `text:p`.
- Growing an empty sheet (or one with a trailing empty row) could corrupt it on reload.
- A formula-only cell was wrongly `is_empty` and could be silently trimmed.
- Style-fork ownership was keyed off the style *name*, breaking once two cells legitimately
  shared a forked style.
- A cell's value `text:p` lookup wasn't scoped to direct children, so a comment's own
  paragraph could be mistaken for the cell's value.

## [0.1.0] — 2026-08-22

Initial release: `.ods` reader with numpy-style indexing (`sheet["A1"]`, slices, blocks),
typed cell values (text, number, percentage, currency, date, time, boolean), formulas read,
repeated and merged cells handled, plus basic value writing (`cell.value = ...`,
`ODSReader.save()`).

[Unreleased]: https://github.com/antnardo/odsslicer/compare/v0.13.2...HEAD
[0.13.2]: https://github.com/antnardo/odsslicer/compare/v0.13.1...v0.13.2
[0.13.1]: https://github.com/antnardo/odsslicer/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/antnardo/odsslicer/compare/v0.12.3...v0.13.0
[0.12.3]: https://github.com/antnardo/odsslicer/compare/v0.12.2...v0.12.3
[0.12.2]: https://github.com/antnardo/odsslicer/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/antnardo/odsslicer/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/antnardo/odsslicer/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/antnardo/odsslicer/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/antnardo/odsslicer/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/antnardo/odsslicer/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/antnardo/odsslicer/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/antnardo/odsslicer/compare/v0.1.0...v0.9.0
[0.1.0]: https://github.com/antnardo/odsslicer/releases/tag/v0.1.0
