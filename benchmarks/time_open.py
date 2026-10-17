"""Time opening real workbooks and loading every sheet - the path a reader
of odsslicer takes most, and the one a refactoring of the XML layer must
not slow down.

`bench.py` measures synthetic workbooks, five plain columns; a real file has
styles, notes, merged cells and repeated rows, which exercise other code.
This opens each file given, loads all its sheets (`reader.sheets`, which
builds every grid), and prints the best and median of `--repeat` runs, each
in a fresh process so that one run's garbage does not land in the next's
collections. With `--src`, the package is imported from another checkout's
`src` instead - an export of the last release, say - so that two versions
are timed under the same machine load, interleaved:

    git archive v0.15.1 src | tar -x -C /tmp/base
    python benchmarks/time_open.py data/NotesReference.ods --src /tmp/base/src

Only times and sheet counts are printed, never a cell: these files can hold
pupils' names.
"""

import argparse
import os
import statistics
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# what each run does, in its own process: the import is not timed
_RUN = """
import sys, time
sys.path.insert(0, sys.argv[1])
from odsslicer import ODSReader
t0 = time.perf_counter()
reader = ODSReader(sys.argv[2])
sheets = reader.sheets
print(time.perf_counter() - t0, len(sheets), sum(s.n_rows * s.n_cols for s in sheets))
"""


def run_once(src: Path, path: Path) -> "tuple[float, int, int]":
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-c", _RUN, str(src), str(path)], capture_output=True, text=True, check=True, env=env
    ).stdout.split()
    return float(out[0]), int(out[1]), int(out[2])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--src", type=Path, help="another checkout's src, timed alongside this one")
    args = parser.parse_args()
    sources = {"this checkout": REPO / "src"}
    if args.src is not None:
        sources["--src"] = args.src.resolve()
    for path in args.files:
        times: dict[str, list[float]] = {name: [] for name in sources}
        shape = (0, 0)
        for _ in range(args.repeat):
            for name, src in sources.items():  # interleaved: the same load for both
                elapsed, n_sheets, n_cells = run_once(src, path)
                times[name].append(elapsed)
                shape = (n_sheets, n_cells)
        print(f"{path.name}: {shape[0]} sheets, {shape[1]:,} cells in the grids")
        for name, values in times.items():
            print(
                f"  {name:>14}: best {min(values) * 1000:7.1f} ms,"
                f" median {statistics.median(values) * 1000:7.1f} ms ({len(values)} runs)"
            )


if __name__ == "__main__":
    main()
