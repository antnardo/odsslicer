# -*- coding: utf-8 -*-
"""Benchmark harness for odsslicer - run manually, not part of the test suite.

Usage: python benchmarks/bench.py [sizes...]   (default: 1000 10000 100000)

Generates synthetic workbooks (N rows x 5 columns: id, name, amount, ratio,
formula) and times every representative operation. Results are printed as a
markdown table so they can be pasted into DOCS.md.
"""
import gc
import resource
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from odsslicer import ODSReader  # noqa: E402

N_COLS = 5


def clock(fn):
    gc.collect()
    t0 = time.perf_counter()
    result = fn()
    return time.perf_counter() - t0, result


def peak_rss_mb():
    # ru_maxrss is bytes on macOS, KiB on Linux
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1e6 if sys.platform == "darwin" else v / 1e3


def generate(path, n_rows):
    """Build the synthetic workbook via odsslicer's own bulk write path."""
    table = ODSReader.new()
    sheet = table.sheet("Sheet1")
    header = ["id", "name", "amount", "ratio", "total"]
    sheet[0, 0:N_COLS].value = header
    ids = [[float(i)] for i in range(1, n_rows + 1)]
    names = [[f"item-{i}"] for i in range(1, n_rows + 1)]
    amounts = [[i * 1.5] for i in range(1, n_rows + 1)]
    ratios = [[(i % 100) / 100] for i in range(1, n_rows + 1)]
    sheet[1 : n_rows + 1, 0].value = ids
    sheet[1 : n_rows + 1, 1].value = names
    sheet[1 : n_rows + 1, 2].value = amounts
    sheet[1 : n_rows + 1, 3].value = ratios
    sheet[1 : n_rows + 1, 4].formula = "C{r}*D{r}"
    table.save(path)


def bench(n_rows):
    tmp = Path(tempfile.mkdtemp(prefix="odsslicer-bench-"))
    path = tmp / f"bench_{n_rows}.ods"
    out = {}

    out["generate+save"], _ = clock(lambda: generate(path, n_rows))
    out["file size (MB)"] = path.stat().st_size / 1e6

    out["open (parse)"], reader = clock(lambda: ODSReader(path))
    out["sheet load"], sheet = clock(lambda: reader.sheet("Sheet1"))
    out["read to_numpy"], _ = clock(lambda: sheet.to_numpy())
    out["read 1 cell"], _ = clock(lambda: sheet["C500"].value if n_rows >= 500 else sheet["C2"].value)

    out["write 1 cell"], _ = clock(lambda: setattr(sheet["B2"], "value", "changed"))
    def write_range():
        sheet[1:1001, 2].value = [[float(i)] for i in range(1000)]
    out["write 1000 cells"], _ = clock(write_range)

    out["sort 1000 rows"], _ = clock(lambda: sheet.sort("A2:E1001", by=2, ascending=False))
    out["delete 1 row"], _ = clock(lambda: sheet.delete_row(5))
    def delete_10():
        for _ in range(10):
            sheet.delete_row(5)
    out["delete 10 rows (loop)"], _ = clock(delete_10)
    out["delete 10 rows (batch)"], _ = clock(lambda: sheet.delete_rows(range(5, 15)))
    out["copy 1000x2 block"], _ = clock(lambda: sheet.copy("B2:C1001", "G2"))
    out["save"], _ = clock(lambda: reader.save(tmp / "out.ods"))
    out["peak RSS (MB)"] = peak_rss_mb()
    return out


def main():
    sizes = [int(a) for a in sys.argv[1:]] or [1000, 10000, 100000]
    results = {n: bench(n) for n in sizes}
    keys = list(next(iter(results.values())))
    header = "| operation | " + " | ".join(f"{n:,} rows" for n in sizes) + " |"
    print(header)
    print("|" + "---|" * (len(sizes) + 1))
    for k in keys:
        cells = []
        for n in sizes:
            v = results[n][k]
            cells.append(f"{v:,.2f}" if "MB" in k else f"{v*1000:,.0f} ms" if v < 100 else f"{v:,.1f} s")
        print(f"| {k} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
