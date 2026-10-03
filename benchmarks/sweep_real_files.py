"""Sweep real-world .ods files, comparing two versions of odsslicer.

Run before a release. The test suite covers what we thought to write down;
this covers what people's files actually contain - it is what caught a
0.13.0 regression the 780 tests did not see (27 sheets showing rows that
stand a million rows below the data, and 54 files where writing past the
data landed in the wrong cell).

Four phases, each answering a different question:

1. **read** - do both versions read every file the same way? Any difference
   has to be explained by the changelog, so the sweep prints what changed
   per file and per sheet, never a value.
2. **grid** - does the grid agree with the file? For each position the grid
   claims, the cell there must be the one the XML has at that row and
   column, repeats counted. This walks `content.xml` independently of the
   library, so a positioning bug cannot hide behind its own loader.
3. **write** - after writing past the data and inside it, does saving and
   reading back leave every other cell alone? And do the parts `save()` is
   meant to copy through - `styles.xml`, `settings.xml`, a chart's own
   content, the manifest, the thumbnail - come back out of the zip byte for
   byte? That is the mechanical form of "we change values, not styling":
   parsing and serialising XML back is faithful in meaning but not to the
   byte, so a part nothing touched must never go through it.
4. **open** - does anything raise? A file both versions fail on the same way
   is fine (macOS aliases and truncated zips are common); a new failure is
   not.

**Privacy.** These are the user's own spreadsheets, and some hold pupils'
names. Nothing here ever prints a cell's content: comparisons go through
SHA-256 digests, and the report carries counts, sizes, addresses and file
paths only. Keep it that way when extending it.

Usage:

    # public corpus, quick check that the sweep itself works
    python benchmarks/sweep_real_files.py --corpus promotion/corpus/libreoffice-core --limit 40

    # the real thing, before a release: this checkout against the last tag
    python benchmarks/sweep_real_files.py --baseline v0.13.2 --limit 500 --write-limit 150

Each file is read in a subprocess per version, since the two versions are
the same package under two paths and cannot share an interpreter. The
subprocess runs without PYTHONPATH: it holds the projects directory here,
where a copy of the package would shadow the checkout under test.
"""

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAX_BYTES_DEFAULT = 3 * 1024 * 1024


# --------------------------------------------------------------------------
# collecting files
# --------------------------------------------------------------------------

SKIP_PARTS = ("/Library/", "/.Trash/", "/.claude/worktrees/", "/claude-501/", "/site-packages/")


def spotlight_files() -> list[Path]:
    """Every .ods Spotlight knows about under the home directory."""
    out = subprocess.run(
        ["mdfind", "-onlyin", str(Path.home()), 'kMDItemFSName == "*.ods"'],
        capture_output=True, text=True, check=False,
    ).stdout
    return [Path(line) for line in out.splitlines() if line]


def usable(path: Path, max_bytes: int) -> bool:
    """A file worth reading: a real local file, not too big to sweep.

    Dropbox leaves "dataless" placeholders behind (`st_blocks == 0`):
    reading one downloads it, which is not something a sweep should do to
    someone's account.
    """
    if any(part in f"/{path}" for part in SKIP_PARTS):
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_size <= max_bytes and st.st_blocks > 0


def sample(files: list[Path], limit: int, max_bytes: int, seed: int) -> list[Path]:
    """Deduplicate by (size, name), then take `limit` at random.

    The same spreadsheet lives in several folders (backups, year copies):
    keeping one of each spends the budget on variety rather than on copies.
    """
    seen: dict[tuple[int, str], Path] = {}
    for path in files:
        if not usable(path, max_bytes):
            continue
        seen.setdefault((path.stat().st_size, path.name), path)
    unique = sorted(seen.values())
    random.Random(seed).shuffle(unique)
    return sorted(unique[:limit])


# --------------------------------------------------------------------------
# the probe, run in a subprocess against one version of the package
# --------------------------------------------------------------------------


def cell_signature(tag: object) -> str:
    """What a cell is, as the file states it - name, declared type, stored
    value, displayed text. Computed identically from the library's own tag
    and from the independent XML walk, so comparing the two tests placement
    alone, not value parsing."""
    from bs4 import Tag

    assert isinstance(tag, Tag)
    attrs = {k: v for k, v in tag.attrs.items() if k.startswith("office:") or k.endswith("-value")}
    texts = [p.get_text() for p in tag.find_all("text:p", recursive=False)]
    return f"{tag.name}|{sorted(attrs.items())}|{texts}"


def digest(parts: list[str]) -> str:
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()[:16]


def xml_rows(path: Path, sheet_index: int, needed_rows: int, needed_cols: int) -> list[list[str]]:
    """Signatures of the cells `content.xml` puts at each position of one
    sheet, repeats unrolled, stopping at `needed_rows`.

    Deliberately not using anything from odsslicer: this is the oracle the
    grid is checked against.
    """
    import zipfile

    from bs4 import BeautifulSoup

    with zipfile.ZipFile(path) as zf:
        soup = BeautifulSoup(zf.read("content.xml"), "xml")
    tables = soup.find_all("table:table")
    if sheet_index >= len(tables):
        return []
    rows: list[list[str]] = []
    for row in tables[sheet_index].find_all("table:table-row"):
        repeat = int(row.attrs.get("table:number-rows-repeated", "1"))
        signatures: list[str] = []
        for cell in row.find_all(["table:table-cell", "table:covered-table-cell"]):
            crep = int(cell.attrs.get("table:number-columns-repeated", "1"))
            signatures.extend([cell_signature(cell)] * min(crep, needed_cols + 1))
        for _ in range(min(repeat, max(needed_rows - len(rows), 0) + 1)):
            rows.append(signatures)
            if len(rows) > needed_rows:
                return rows
    return rows


def probe(src: Path, path: Path, do_grid: bool, write_dir: Path | None) -> dict:
    """Read one file with the version at `src` and report what it saw.

    Returns digests and counts only - never a value (see the module
    docstring).
    """
    sys.path.insert(0, str(src))
    import warnings

    result: dict = {"file": str(path), "error": None, "sheets": [], "warnings": 0}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            from odsslicer import ODSReader

            table = ODSReader(path)
            for index, name in enumerate(table.sheets_names):
                sheet = table.sheet(name)
                values = sheet[:, :].to_list() if sheet.n_rows else []
                sheet_report = {
                    "name_digest": digest([name]),
                    "size": list(sheet.size),
                    "values": digest([repr(v) for row in values for v in row]),
                    "texts": digest([repr(c.text) for row in sheet.rows for c in row]),
                }
                if do_grid:
                    sheet_report["grid_mismatches"] = grid_mismatches(sheet, path, index)
                result["sheets"].append(sheet_report)
            if write_dir is not None:
                result["write"] = write_and_reread(table, path, write_dir)
        except Exception as exc:  # a corrupt file must not stop the sweep
            result["error"] = type(exc).__name__
        result["warnings"] = len(caught)
    return result


def grid_mismatches(sheet: object, path: Path, index: int) -> list[str]:
    """Addresses where the grid holds a cell the file puts elsewhere."""
    expected = xml_rows(path, index, sheet.n_rows, sheet.n_cols)  # type: ignore[attr-defined]
    bad: list[str] = []
    for r, row in enumerate(sheet.rows):  # type: ignore[attr-defined]
        if r >= len(expected):
            if any(not c.is_empty for c in row):
                bad.append(f"row {r} holds values the file puts elsewhere")
            break
        for c, cell in enumerate(row):
            if c >= len(expected[r]):
                # loading pads a short row up to the sheet's width, and only
                # with empty cells: anything else there is a real shift
                if not cell.is_empty:
                    bad.append(f"{cell.address}: non-empty past the row's cells")
                break
            if cell_signature(cell.cell) != expected[r][c]:
                bad.append(cell.address)
        if len(bad) > 20:
            bad.append("...")
            break
    return bad


# The package parts `save()` regenerates from their in-memory tree; every
# other member of the zip has to come back out of it unchanged.
REGENERATED_PARTS = {"content.xml", "meta.xml"}


def rewritten_parts(before: Path, after: Path) -> list[str]:
    """The members of `after` that do not match `before` byte for byte,
    `content.xml` and `meta.xml` aside - a member added or dropped counts."""
    import zipfile

    with zipfile.ZipFile(before) as src, zipfile.ZipFile(after) as dst:
        names = set(src.namelist()) | set(dst.namelist())
        return sorted(
            name
            for name in names - REGENERATED_PARTS
            if name not in src.namelist()
            or name not in dst.namelist()
            or src.read(name) != dst.read(name)
        )


def write_and_reread(table: object, path: Path, write_dir: Path) -> dict:
    """Write past the data and inside it, save, read back: only the cells
    written may differ, and only the regenerated parts of the package."""
    import datetime as dt

    sheet = table.sheets[0]  # type: ignore[attr-defined]
    if sheet.n_rows == 0 or sheet.n_cols == 0:
        return {"skipped": "empty sheet"}
    targets = {
        (sheet.n_rows + 1, 0): dt.date(2026, 3, 7),
        (sheet.n_rows + 1, min(1, sheet.n_cols - 1)): 42.5,
        (sheet.n_rows // 2, 0): "sweep",
    }
    before = {
        (r, c): repr(sheet[r, c].value)
        for r in range(sheet.n_rows)
        for c in range(sheet.n_cols)
        if (r, c) not in targets
    }
    for (r, c), value in targets.items():
        sheet[r, c].value = value
    out = write_dir / f"{path.stem}-{os.getpid()}.ods"
    table.save(out)  # type: ignore[attr-defined]
    # before reading it back, while both files are still on disk: editing
    # values must leave every other part of the package exactly as it was
    rewritten = rewritten_parts(path, out)

    from odsslicer import ODSReader

    reread = ODSReader(out).sheets[0]
    changed = sum(
        1
        for (r, c), value in before.items()
        if r < reread.n_rows and c < reread.n_cols and repr(reread[r, c].value) != value
    )
    kept = sum(1 for (r, c) in targets if repr(reread[r, c].value) != "None")
    out.unlink(missing_ok=True)
    return {
        "unrelated_changed": changed,
        "targets_written": kept,
        "targets": len(targets),
        "rewritten_parts": rewritten,
    }


# --------------------------------------------------------------------------
# driving the two versions
# --------------------------------------------------------------------------


@dataclass
class Findings:
    read_differences: list[str] = field(default_factory=list)
    grid_violations: list[str] = field(default_factory=list)
    write_failures: list[str] = field(default_factory=list)
    copied_parts_rewritten: list[str] = field(default_factory=list)
    new_errors: list[str] = field(default_factory=list)
    swept: int = 0
    unreadable: int = 0


def run_probe(src: Path, path: Path, do_grid: bool, write_dir: Path | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [sys.executable, __file__, "--probe", str(src), str(path)]
    if do_grid:
        argv.append("--grid")
    if write_dir is not None:
        argv += ["--write-dir", str(write_dir)]
    done = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    if done.returncode != 0:
        return {"file": str(path), "error": f"probe crashed: {done.stderr.strip()[-120:]}", "sheets": []}
    return json.loads(done.stdout)


def compare(current: dict, baseline: dict | None, findings: Findings, do_write: bool) -> None:
    path = current["file"]
    findings.swept += 1
    if current["error"]:
        # macOS aliases and truncated zips are a known part of any real
        # sample: only a file the other version reads is a finding
        if baseline is None or baseline["error"] == current["error"]:
            findings.unreadable += 1
        else:
            findings.new_errors.append(f"{path}: {current['error']}")
        return
    for sheet in current["sheets"]:
        bad = sheet.get("grid_mismatches") or []
        if bad:
            findings.grid_violations.append(f"{path} sheet {sheet['name_digest']}: {len(bad)} ({bad[:3]})")
    if do_write and "write" in current:
        w = current["write"]
        if w.get("unrelated_changed") or (w.get("targets_written", 0) < w.get("targets", 0)):
            findings.write_failures.append(f"{path}: {w}")
        if w.get("rewritten_parts"):
            findings.copied_parts_rewritten.append(f"{path}: {w['rewritten_parts']}")
    if baseline is None or baseline["error"]:
        return
    if len(baseline["sheets"]) != len(current["sheets"]):
        findings.read_differences.append(
            f"{path}: {len(baseline['sheets'])} -> {len(current['sheets'])} sheets"
        )
        return
    for old, new in zip(baseline["sheets"], current["sheets"], strict=True):
        if old["size"] != new["size"]:
            findings.read_differences.append(
                f"{path} sheet {new['name_digest']}: size {old['size']} -> {new['size']}"
            )
        elif old["values"] != new["values"] or old["texts"] != new["texts"]:
            findings.read_differences.append(f"{path} sheet {new['name_digest']}: values or texts differ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", nargs=2, metavar=("SRC", "FILE"))
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--write-dir")
    parser.add_argument("--corpus", help="sweep this directory instead of the home directory")
    parser.add_argument("--baseline", help="git ref to compare this checkout against")
    parser.add_argument("--baseline-src", help="a src/ directory to compare against instead")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--write-limit", type=int, default=150)
    parser.add_argument("--max-size", type=float, default=MAX_BYTES_DEFAULT / 1e6, help="MB")
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--json", help="write the full report here")
    args = parser.parse_args()

    if args.probe:
        src, path = Path(args.probe[0]), Path(args.probe[1])
        write_dir = Path(args.write_dir) if args.write_dir else None
        print(json.dumps(probe(src, path, args.grid, write_dir)))
        return 0

    max_bytes = int(args.max_size * 1e6)
    if args.corpus:
        candidates = sorted(p for p in Path(args.corpus).rglob("*.ods") if usable(p, max_bytes))
        files = sample(candidates, args.limit, max_bytes, args.seed)
    else:
        files = sample(spotlight_files(), args.limit, max_bytes, args.seed)
    if not files:
        print("no file to sweep", file=sys.stderr)
        return 2

    baseline_src, worktree = resolve_baseline(args)
    try:
        findings = Findings()
        reports = []
        with tempfile.TemporaryDirectory() as tmp:
            for n, path in enumerate(files, 1):
                write_dir = Path(tmp) if n <= args.write_limit else None
                current = run_probe(REPO / "src", path, args.grid or True, write_dir)
                base = run_probe(baseline_src, path, False, None) if baseline_src else None
                compare(current, base, findings, write_dir is not None)
                reports.append({"current": current, "baseline": base})
                print(f"\r{n}/{len(files)} files", end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)
    finally:
        if worktree:
            subprocess.run(
                ["git", "-C", str(REPO), "worktree", "remove", "--force", str(worktree)], check=False
            )

    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=1))
    return report(findings, baseline_src)


def resolve_baseline(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    """The src/ of the version to compare against, checking a ref out into a
    throwaway worktree when given one."""
    if args.baseline_src:
        return Path(args.baseline_src), None
    if not args.baseline:
        return None, None
    worktree = Path(tempfile.mkdtemp(prefix="odsslicer-baseline-")) / args.baseline.replace("/", "-")
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "add", "--detach", "-q", str(worktree), args.baseline],
        check=True,
    )
    return worktree / "src", worktree


def report(f: Findings, baseline_src: Path | None) -> int:
    print(f"\n{f.swept} files swept" + (f", against {baseline_src}" if baseline_src else ", no baseline"))
    both = " by both versions" if baseline_src else ""
    print(f"  {f.unreadable} unreadable{both} (aliases, truncated zips)")
    for label, items in (
        ("read differences", f.read_differences),
        ("grid vs XML violations", f.grid_violations),
        ("write/reread failures", f.write_failures),
        ("copied parts rewritten by a save", f.copied_parts_rewritten),
        ("new errors", f.new_errors),
    ):
        print(f"  {len(items)} {label}")
        for item in items[:15]:
            print(f"      {item}")
        if len(items) > 15:
            print(f"      ... and {len(items) - 15} more")
    bad = f.grid_violations or f.write_failures or f.copied_parts_rewritten or f.new_errors
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
