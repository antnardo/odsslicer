# Changelog

All notable changes to `odsslicer` are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project follows
[Semantic Versioning](https://semver.org/) — while the major version stays `0`, the API can
still change between minor versions.

## [Unreleased]

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

[0.11.0]: https://github.com/antnardo/odsslicer/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/antnardo/odsslicer/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/antnardo/odsslicer/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/antnardo/odsslicer/compare/v0.1.0...v0.9.0
[0.1.0]: https://github.com/antnardo/odsslicer/releases/tag/v0.1.0
