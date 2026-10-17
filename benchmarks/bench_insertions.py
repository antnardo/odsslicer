"""Time the structural edits that place many elements at once - the ones a
port to lxml could make quadratic again (ROADMAP.md, step 5).

lxml's `parent.insert(i, el)` and `parent.index(el)` walk the parent's
children up to `i`; its `addnext`/`addprevious` relink a node in constant
time. bs4's `insert_after` looked its reference up with `parent.index()`, so
0.15.0 placed rows by an index found once - right on bs4, quadratic again if
translated word for word to lxml: 20,000 copies 50,000 rows down take 2.6 s
by index, 9 ms as a chain of `addnext` (`benchmarks/LXML_STUDY.md`). Since
1.0 every such site places its elements next to a known one. Each edit here
runs on a workbook of N rows of data:

- `write past the data`: a column of N values written below the data, which
  grows the sheet one row per cell (`grow_to`, `_take_back_rows`);
- `insert N rows`: `insert_rows(N // 2, N)`, rows placed before a known row;
- `insert 10 columns`: `insert_columns(1, 10)`, cells placed in every row;
- `write into a repeated run`: one cell in the middle of a run of N
  repeated rows below the data, which splits the run into N copies
  (`_split_repetitions`).

Each edit runs in a fresh process, best of `--repeat`, the versions
interleaved under the same machine load:

    python benchmarks/bench_insertions.py 20000 --src ../baseline/src

Only times are printed: the workbooks are synthetic.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

EDITS = ("write past the data", "insert N rows", "insert 10 columns", "write into a repeated run")

# Builds the workbooks with the version under test, then times one edit
_RUN = """
import sys, time, zipfile
sys.path.insert(0, sys.argv[1])
from odsslicer import ODSReader
edit, n, workdir = sys.argv[2], int(sys.argv[3]), sys.argv[4]
plain = f"{workdir}/plain.ods"
repeated = f"{workdir}/repeated.ods"
if edit == "build":
    reader = ODSReader.new()
    reader.sheet("Sheet1")[0:n, 0].value = [[float(i)] for i in range(n)]
    reader.save(plain)
    # a run of n repeated rows below the data, as an application writes one
    with zipfile.ZipFile(plain) as src, zipfile.ZipFile(repeated, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "content.xml":
                run = (
                    f'<table:table-row table:number-rows-repeated="{n}"><table:table-cell'
                    ' office:value-type="string"><text:p>x</text:p></table:table-cell></table:table-row>'
                )
                data = data.replace(b"</table:table>", run.encode() + b"</table:table>", 1)
            dst.writestr(item, data)
    sys.exit(0)
sheet = ODSReader(repeated if edit == "write into a repeated run" else plain).sheet("Sheet1")
t0 = time.perf_counter()
if edit == "write past the data":
    sheet[n : 2 * n, 0].value = [[float(i)] for i in range(n)]
elif edit == "insert N rows":
    sheet.insert_rows(n // 2, n)
elif edit == "insert 10 columns":
    sheet.insert_columns(1, 10)
else:
    sheet[n + n // 2, 0].value = "y"
print(time.perf_counter() - t0)
"""


def run(src: Path, edit: str, n: int, workdir: Path) -> float:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    argv = [sys.executable, "-c", _RUN, str(src), edit, str(n), str(workdir)]
    out = subprocess.run(argv, capture_output=True, text=True, check=True, env=env).stdout
    return float(out) if out.strip() else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sizes", nargs="*", type=int, default=[20_000])
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--src", type=Path, help="another checkout's src, timed alongside this one")
    args = parser.parse_args()
    sources = {"this checkout": REPO / "src"}
    if args.src is not None:
        sources["--src"] = args.src.resolve()
    for n in args.sizes:
        print(f"## {n:,} rows")
        with tempfile.TemporaryDirectory() as tmp:
            workdirs = {name: Path(tmp) / str(k) for k, name in enumerate(sources)}
            for name, src in sources.items():  # each version builds its own workbooks
                workdirs[name].mkdir()
                run(src, "build", n, workdirs[name])
            for edit in EDITS:
                times: dict[str, list[float]] = {name: [] for name in sources}
                for _ in range(args.repeat):
                    for name, src in sources.items():
                        times[name].append(run(src, edit, n, workdirs[name]))
                cells = ", ".join(f"{name} {min(values):.3f} s" for name, values in times.items())
                print(f"  {edit:<26} {cells}")


if __name__ == "__main__":
    main()
