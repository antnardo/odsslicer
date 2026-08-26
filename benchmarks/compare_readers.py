# -*- coding: utf-8 -*-
"""Read-speed and memory comparison against other .ods readers.

Usage: python benchmarks/compare_readers.py [sizes...]   (default: 100 1000 10000 100000)

The scenario is deliberately the competitors' home turf: a purely numeric
matrix (N rows x 5 float columns, one sheet) read in full into Python values.
Contenders: odsslicer, odfdo (the other maintained full read/write library)
and python-calamine (the Rust streaming reader) - both optional, skipped with
a note when not installed.

Each measurement runs in a fresh subprocess (this script re-invokes itself
with --read) so peak RSS reflects one tool only; read time is the median of
three runs and excludes the library import. Results print as markdown tables
to paste into DOCS.md.
"""
import importlib.util
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

N_COLS = 5
TOOLS = ("odsslicer", "odfdo", "calamine")
MODULES = {"odsslicer": "odsslicer", "odfdo": "odfdo", "calamine": "python_calamine"}


def measure_one(tool, path):
    """--read mode: read the whole matrix, print import time, read time, RSS."""
    import resource

    t0 = time.perf_counter()
    if tool == "odsslicer":
        from odsslicer import ODSReader
    elif tool == "odfdo":
        from odfdo import Document
    elif tool == "calamine":
        from python_calamine import CalamineWorkbook
    t_import = time.perf_counter() - t0

    t0 = time.perf_counter()
    if tool == "odsslicer":
        values = ODSReader(path).sheets[0][:, :].to_list()
    elif tool == "odfdo":
        values = Document(path).body.tables[0].get_values()
    elif tool == "calamine":
        values = CalamineWorkbook.from_path(path).get_sheet_by_index(0).to_python()
    t_read = time.perf_counter() - t0

    # ru_maxrss is bytes on macOS, KiB on Linux
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / 1e6 if sys.platform == "darwin" else rss / 1e3
    n = len(values)
    # all three tools must agree on the data before their timings are comparable
    assert values[0][0] == 0.25 and values[n - 1][N_COLS - 1] == float((n - 1) * N_COLS + N_COLS - 1) + 0.25
    print(f"{t_import} {t_read} {rss_mb}")


def generate(path, n_rows):
    from odsslicer import ODSReader

    table = ODSReader.new("Data")
    values = [[float(i * N_COLS + j) + 0.25 for j in range(N_COLS)] for i in range(n_rows)]
    table.sheet("Data")[f"A1:E{n_rows}"].value = values
    table.save(path)


def fmt_time(seconds):
    if seconds < 0.001:
        return "< 1 ms"
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.1f} s"


def main(sizes):
    tools = [t for t in TOOLS if importlib.util.find_spec(MODULES[t]) is not None]
    for missing in set(TOOLS) - set(tools):
        print(f"({missing} not installed - skipped)")
    results = {}  # (tool, n) -> (median read time, max rss)
    with tempfile.TemporaryDirectory() as tmp:
        for n in sizes:
            path = Path(tmp) / f"matrix_{n}.ods"
            generate(path, n)
            for tool in tools:
                runs = []
                for _ in range(3):
                    out = subprocess.run(
                        [sys.executable, __file__, "--read", tool, str(path)],
                        capture_output=True, text=True, check=True,
                    ).stdout.split()
                    runs.append((float(out[1]), float(out[2])))
                results[tool, n] = (statistics.median(r[0] for r in runs), max(r[1] for r in runs))
                print(f"  {tool} {n}: {results[tool, n]}", file=sys.stderr)

    header = "| Rows (x5 float columns) | " + " | ".join(tools) + " |"
    rule = "|---" * (len(tools) + 1) + "|"
    print("\nRead time (import excluded, median of 3):\n")
    print(header + "\n" + rule)
    for n in sizes:
        print(f"| {n:,} | " + " | ".join(fmt_time(results[t, n][0]) for t in tools) + " |")
    print("\nPeak memory (RSS):\n")
    print(header + "\n" + rule)
    for n in sizes:
        print(f"| {n:,} | " + " | ".join(f"{results[t, n][1]:.0f} MB" for t in tools) + " |")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--read":
        measure_one(sys.argv[2], sys.argv[3])
    else:
        main([int(a) for a in sys.argv[1:]] or [100, 1000, 10000, 100000])
