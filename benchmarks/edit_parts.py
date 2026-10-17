"""Compare, part by part, what two versions of odsslicer save after the same
structural edit - the check the sweep cannot make, since it edits values
only, and a value edit never rewrites a chart.

Written for step 1 of the lxml migration (ROADMAP.md), which moved
`settings.xml` and the charts' own parts from bs4 to lxml: the bytes of a
rewritten part change with the library, its meaning must not. For every
file and every edit, both versions save, and each package member is
compared across them:

- **same bytes** - nothing to say;
- **same canonical form** (C14N) but other bytes - the expected cost of a
  part serialised by another library (`&apos;` written out, `<x></x>` as
  `<x/>`, the XML declaration);
- **other meaning** - a finding: the two versions do not save the same
  document. Settle it against LibreOffice, not against odsslicer.

And within each version, every member but `content.xml`, `meta.xml` and
the parts the edit has a reason to rewrite (the charts, for a structural
edit; `settings.xml`, for the `touch-settings` edit) must come out byte for
byte as it went in.

The edits, on the first sheet: rows inserted and deleted, columns inserted
and deleted, the sheet renamed, and `settings.xml` parsed and marked
touched with nothing changed in it.

**Privacy.** Run on the user's own files too, which hold pupils' names:
nothing here prints a cell or a part's content - digests, counts, part
names and file paths only. Keep it that way.

    python benchmarks/edit_parts.py --baseline-src ../baseline/src FILE_OR_DIR...
    python benchmarks/edit_parts.py --baseline-src ../baseline/src --charts-only DIR

Each file is edited in a subprocess per version, as in `sweep_real_files.py`.
`--timeout` gives each up to that many seconds: a sheet holding a cell a
million rows down costs a million rows of memory to load (DOCS.md, Known
limitations), LibreOffice's own `too-many-cols-rows.ods` is one, and took
both versions past half an hour. A file timed out is counted, not compared.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

EDITS = ("insert_rows", "delete_rows", "insert_columns", "delete_column", "rename_sheet", "touch_settings")


def _apply(reader: object, edit: str) -> bool:
    """Make `edit` on `reader`'s first sheet; False if the sheet is too
    small for it."""
    sheet = reader.sheets[0]  # type: ignore[attr-defined]
    if edit == "insert_rows":
        sheet.insert_rows(min(1, sheet.n_rows), 2)
    elif edit == "delete_rows":
        if sheet.n_rows < 2:
            return False
        sheet.delete_rows([1])
    elif edit == "insert_columns":
        sheet.insert_columns(min(1, sheet.n_cols), 1)
    elif edit == "delete_column":
        if sheet.n_cols < 2:
            return False
        sheet.delete_column(1)
    elif edit == "rename_sheet":
        reader.rename_sheet(sheet.name, sheet.name + " renamed")  # type: ignore[attr-defined]
    elif edit == "touch_settings":
        reader.settings_data  # type: ignore[attr-defined]  # noqa: B018
        reader._touched_part("settings.xml")  # type: ignore[attr-defined]
    return True


def _c14n(data: bytes) -> "str | None":
    from lxml import etree

    try:
        root = etree.fromstring(
            data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
        )
    except etree.XMLSyntaxError:
        return None
    return hashlib.sha256(etree.tostring(root, method="c14n")).hexdigest()[:16]


def probe(src: Path, path: Path, out_dir: Path) -> dict:
    """Every edit on `path` with the version at `src`: per edit, each saved
    member's digest, its canonical form's, and whether it is byte for byte
    the original's."""
    sys.path.insert(0, str(src))
    from odsslicer import ODSReader

    with zipfile.ZipFile(path) as package:
        original = {name: package.read(name) for name in package.namelist()}
    result: dict = {"file": str(path), "edits": {}}
    for edit in EDITS:
        out = out_dir / f"{os.getpid()}-{edit}.ods"
        try:
            reader = ODSReader(path)
            if not _apply(reader, edit):
                result["edits"][edit] = {"skipped": True}
                continue
            reader.save(out)
        except Exception as exc:  # a corrupt file must not stop the run
            result["edits"][edit] = {"error": type(exc).__name__}
            continue
        with zipfile.ZipFile(out) as package:
            saved = {name: package.read(name) for name in package.namelist()}
        out.unlink()
        result["edits"][edit] = {
            name: {
                "bytes": hashlib.sha256(data).hexdigest()[:16],
                "c14n": _c14n(data) if name.endswith(".xml") else None,
                "copied": original.get(name) == data,
            }
            for name, data in saved.items()
        }
    return result


def run_probe(src: Path, path: Path, out_dir: Path, timeout: "float | None" = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [sys.executable, __file__, "--probe", str(src), str(path), str(out_dir)]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, env=env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"file": str(path), "timed out": True, "edits": {}}
    if done.returncode != 0:
        return {"file": str(path), "crashed": done.stderr.strip()[-200:], "edits": {}}
    return json.loads(done.stdout)


def _has_chart(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as package:
            return any(
                name.startswith("Object") and name.endswith("/content.xml") for name in package.namelist()
            )
    except (OSError, zipfile.BadZipFile):
        return False


def _expected_rewrites(edit: str, name: str) -> bool:
    """Whether `edit` has a reason to rewrite member `name`."""
    if name in ("content.xml", "meta.xml"):
        return True
    if edit == "touch_settings":
        return name == "settings.xml"
    return name.startswith("Object") and name.endswith("/content.xml")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--probe", nargs=3, metavar=("SRC", "FILE", "OUT_DIR"), help=argparse.SUPPRESS)
    parser.add_argument("--baseline-src", help="the src/ directory of the version to compare with")
    parser.add_argument("--charts-only", action="store_true", help="only files holding an embedded object")
    parser.add_argument("--timeout", type=float, help="seconds a version may take over one file")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    if args.probe:
        src, path, out_dir = args.probe
        print(json.dumps(probe(Path(src), Path(path), Path(out_dir))))
        return 0
    if not args.baseline_src:
        parser.error("--baseline-src is required")
    files = sorted(
        {f for p in args.paths for f in ([p] if p.is_file() else p.rglob("*.ods")) if f.suffix == ".ods"}
    )
    if args.charts_only:
        files = [f for f in files if _has_chart(f)]
    counts: Counter[str] = Counter()
    findings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        for path in files:
            current = run_probe(REPO / "src", path, out_dir, args.timeout)
            baseline = run_probe(Path(args.baseline_src), path, out_dir, args.timeout)
            counts["files"] += 1
            if "timed out" in current or "timed out" in baseline:
                counts["timed out, not compared"] += 1
                print(f"  timed out: {path}", file=sys.stderr, flush=True)
                continue
            if "crashed" in current or "crashed" in baseline:
                findings.append(
                    f"{path}: probe crashed ({'current' if 'crashed' in current else 'baseline'})"
                )
                continue
            for edit in EDITS:
                new, old = current["edits"].get(edit, {}), baseline["edits"].get(edit, {})
                if new.get("skipped") and old.get("skipped"):
                    continue
                if "error" in new or "error" in old:
                    if new.get("error") != old.get("error"):
                        findings.append(f"{path} {edit}: error {old.get('error')} -> {new.get('error')}")
                    else:
                        counts["same error"] += 1
                    continue
                counts["edits"] += 1
                if set(new) != set(old):
                    findings.append(f"{path} {edit}: members differ {sorted(set(new) ^ set(old))}")
                for name in sorted(set(new) & set(old)):
                    a, b = new[name], old[name]
                    if not a["copied"] and not _expected_rewrites(edit, name):
                        findings.append(f"{path} {edit}: {name} rewritten by this version")
                    if a["bytes"] == b["bytes"]:
                        counts["same bytes"] += 1
                    elif a["c14n"] is not None and a["c14n"] == b["c14n"]:
                        counts[
                            f"same C14N, other bytes: {'a chart' if name.startswith('Object') else name}"
                        ] += 1
                    else:
                        findings.append(f"{path} {edit}: {name} says something else")
            print(f"  {counts['files']}/{len(files)} {path.name}", file=sys.stderr, flush=True)
    for key, value in sorted(counts.items()):
        print(f"{key}: {value}")
    print(f"{len(findings)} finding(s)")
    for line in findings:
        print("  " + line)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
