"""How much of opening a workbook is BeautifulSoup's, and how much is
odsslicer's own - the number that decides whether dropping bs4 for lxml
alone is worth a rewrite (see `benchmarks/LXML_STUDY.md`).

lxml parses ten times faster than bs4 on a real `content.xml`, but parsing
is only part of opening a sheet: odsslicer then walks the tree, unrolls
repeats and builds a `Cell` per cell, and lxml speeds none of that up by
itself. This splits the time of `ODSReader(path)` ("open") and of
`reader.sheet(name)` ("load", the first access, which builds the grid) in
two ways:

1. **Under bs4, or not.** Every bs4 method odsslicer calls is wrapped in
   a clock (`timed_inside_bs4`), the garbage collector timed apart; and,
   as a check, `Cell()` is timed against the bs4 calls it makes
   (`cell_split`). cProfile would not do: it charges every Python call a
   fixed overhead, which inflates the share of code made of many small
   calls - bs4's and odsslicer's both - and it counts bs4's generators
   twice when a built-in (`list()`, `next()`) consumes them.
2. **Timed, without profiler.** The parse alone, with bs4 as odsslicer
   calls it and with lxml; and the navigation `Sheet.load` does - every
   row, every cell, their repeats, value attributes and paragraphs - done
   once over each tree, with no `Cell` built. The difference between the
   two walks is what bs4's navigation costs over lxml's.
3. **Projected.** Opening and loading as a port to lxml would do it
   (`projected_open_and_load`: the same passes, a cell object of `Cell`'s
   size per cell, the same value conversion), against the real thing, each
   in a fresh process, best of three: the time and the peak memory the
   rewrite can expect, collector included. A projection, not the port - it
   leaves out what `Sheet.load` does for the fillers and trailing empty
   rows these workbooks do not have.

    python benchmarks/lxml_profile_open.py 1000 10000 100000 --workdir /tmp/bench

The synthetic workbooks are the ones `bench.py` writes, built once and kept
in `--workdir` (100,000 rows take minutes to generate).
"""

import argparse
import gc
import resource
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import generate
from lxml import etree

from odsslicer import ODSReader
from odsslicer.constants import FORMATS
from odsslicer.xmlutils import _parse_xml

ODSSLICER = f"{Path(__file__).resolve().parents[1] / 'src' / 'odsslicer'}"


def workbook(workdir: Path, n_rows: int) -> Path:
    path = workdir / f"bench_{n_rows}.ods"
    if not path.exists():
        generate(path, n_rows)
    return path


# bs4's members odsslicer calls that do real work - parsing, searching,
# gathering text, moving elements - whose time is bs4's. Reading an
# attribute (`tag["x"]`, `tag.get`) is a dict lookup, as cheap as lxml's
# `el.get`, and bs4 calls those members millions of times itself while
# parsing: wrapping them would put the wrapper's cost inside bs4's time.
_BS4_ENTRIES = (
    "find",
    "find_all",
    "get_text",
    "find_parent",
    "find_previous",
    "find_next",
    "find_previous_siblings",
    "decompose",
    "extract",
    "insert_before",
    "insert_after",
    "encode",
)


def timed_inside_bs4(fn: Callable[..., object], *args: object) -> tuple[dict[str, float], object]:
    """Run `fn(*args)` with every bs4 method odsslicer calls wrapped in a
    clock, and split its wall time, in seconds: under bs4 (lxml's parsing
    included, which runs under `BeautifulSoup()`), in the garbage collector
    while under bs4 or not, and the rest - odsslicer's own code, the
    built-ins and the standard library it calls.

    The wrappers cost time of their own, measured on a trivial method and
    taken off. The collector is timed through `gc.callbacks`: its cost grows
    with the number of live Python objects, of which a bs4 tree holds
    millions and an lxml tree none (its nodes are C structures).

    A sampling profiler was tried first, and set aside: it put bs4's share
    of `Sheet.load` at 30 to 46 %, where this and a plain measurement of
    `Cell()` against its own bs4 calls (`cell_split`) agree on about 80 %."""
    from bs4 import BeautifulSoup
    from bs4.element import PageElement, Tag

    state = {"depth": 0, "inside": 0.0, "calls": 0, "gc_inside": 0.0, "gc_outside": 0.0, "gc_t0": 0.0}

    def wrap(original: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*a: object, **k: object) -> object:
            if state["depth"]:
                return original(*a, **k)
            state["depth"] = 1
            t0 = time.perf_counter()
            try:
                return original(*a, **k)
            finally:
                state["inside"] += time.perf_counter() - t0
                state["calls"] += 1
                state["depth"] = 0

        return wrapper

    def collecting(phase: str, info: dict[str, int]) -> None:
        if phase == "start":
            state["gc_t0"] = time.perf_counter()
        else:
            key = "gc_inside" if state["depth"] else "gc_outside"
            state[key] += time.perf_counter() - state["gc_t0"]

    saved = [(BeautifulSoup, "__init__", BeautifulSoup.__dict__["__init__"])]
    BeautifulSoup.__init__ = wrap(BeautifulSoup.__init__)  # type: ignore[method-assign]
    for cls in (Tag, PageElement):
        for name in _BS4_ENTRIES:
            if name in cls.__dict__:
                saved.append((cls, name, cls.__dict__[name]))
                setattr(cls, name, wrap(cls.__dict__[name]))
    gc.collect()
    gc.callbacks.append(collecting)
    t0 = time.perf_counter()
    try:
        result = fn(*args)
    finally:
        wall = time.perf_counter() - t0
        gc.callbacks.remove(collecting)
        for cls, name, original in saved:
            setattr(cls, name, original)
    measured = dict(state)  # before `_wrapper_cost` runs its own calls through `wrap`
    overhead = measured["calls"] * _wrapper_cost(wrap)
    return {
        "bs4": measured["inside"] - measured["gc_inside"],
        "gc, under bs4": measured["gc_inside"],
        "gc, elsewhere": measured["gc_outside"],
        "odsslicer and stdlib": wall - overhead - measured["inside"] - measured["gc_outside"],
    }, result


def _wrapper_cost(wrap: Callable[[Callable[..., object]], Callable[..., object]]) -> float:
    """What one call through `wrap` costs over a plain call, in seconds."""

    def plain() -> None:
        return None

    wrapped, n = wrap(plain), 200_000
    t0 = time.perf_counter()
    for _ in range(n):
        wrapped()
    through = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n):
        plain()
    return max(through - (time.perf_counter() - t0), 0.0) / n


def profiled(path: Path) -> dict[str, dict[str, float]]:
    opening, reader = timed_inside_bs4(ODSReader, path)
    loading, _ = timed_inside_bs4(reader.sheet, "Sheet1")  # type: ignore[attr-defined]
    both = {key: opening[key] + loading[key] for key in opening}
    return {"open": opening, "load": loading, "open + load": both}


def cell_split(path: Path) -> dict[str, float]:
    """`Cell()` built for every cell element of the sheet, against the bs4
    calls it makes - its paragraphs, and their text - alone: the
    difference is odsslicer's own work per cell. The collector is off, and
    each is the best of three."""
    from odsslicer.cell import Cell

    reader = ODSReader(path)
    cells = reader.data.find_all("table:table-cell")
    gc.collect()
    gc.disable()
    try:
        best = {}
        for label, fn in (
            ("Cell(), all of it", lambda: [Cell(tag) for tag in cells]),
            (
                "its bs4 calls alone",
                lambda: [[p.get_text() for p in tag.find_all("text:p", recursive=False)] for tag in cells],
            ),
        ):
            best[label] = min(clock(fn)[0] for _ in range(3))
    finally:
        gc.enable()
    best["cells"] = len(cells)
    return best


# --- the navigation Sheet.load does, over each tree --------------------------

TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
VALUE_ATTRS = ("value-type", "value", "date-value", "time-value", "boolean-value")


def walk_lxml(root: etree._Element) -> int:
    row_tag = f"{{{TABLE}}}table-row"
    cell_tags = (f"{{{TABLE}}}table-cell", f"{{{TABLE}}}covered-table-cell")
    repeat_r, repeat_c = f"{{{TABLE}}}number-rows-repeated", f"{{{TABLE}}}number-columns-repeated"
    attrs = [f"{{{OFFICE}}}{a}" for a in VALUE_ATTRS] + [f"{{{TABLE}}}formula", f"{{{TABLE}}}style-name"]
    p_tag, note = f"{{{TEXT}}}p", f"{{{OFFICE}}}annotation"
    seen = 0
    for table in root.iter(f"{{{TABLE}}}table"):
        for row in table.iter(row_tag):
            seen += int(row.get(repeat_r, "1"))
            for cell in row.iterchildren(*cell_tags):
                int(cell.get(repeat_c, "1"))
                for a in attrs:
                    cell.get(a)
                ["".join(p.itertext()) for p in cell.iterchildren(p_tag)]
                cell.find(note)
    return seen


def walk_bs4(soup: object) -> int:
    attrs = [f"office:{a}" for a in VALUE_ATTRS] + ["table:formula", "table:style-name"]
    seen = 0
    for table in soup.find_all("table:table"):  # type: ignore[attr-defined]
        for row in table.find_all("table:table-row"):
            seen += int(row.attrs.get("table:number-rows-repeated", "1"))
            for cell in row.find_all(["table:table-cell", "table:covered-table-cell"], recursive=False):
                int(cell.attrs.get("table:number-columns-repeated", "1"))
                for a in attrs:
                    cell.attrs.get(a)
                [p.get_text() for p in cell.find_all("text:p", recursive=False)]
                cell.find("office:annotation", recursive=False)
    return seen


def clock(fn: object) -> tuple[float, object]:
    gc.collect()
    t0 = time.perf_counter()
    result = fn()  # type: ignore[operator]
    return time.perf_counter() - t0, result


class _ProjectedCell:
    """What `Cell.__init__` reads and keeps, taken from an lxml element - the
    work per cell a port keeps, on the tree it would have. Same attributes,
    so the same memory; no slots, as `Cell` has none."""

    def __init__(self, element: etree._Element, row: int, col: int) -> None:
        self.row, self.col, self.cell, self.sheet = row, col, element, None
        self.attrs = element.attrib
        get = element.get
        self.format = get(_VALUE_TYPE)
        attr = _RAW_VALUE.get(self.format, _VALUE)
        self.raw_value = get(attr)
        paragraphs = list(element.iterchildren(_P))
        self.text = "\n".join("".join(p.itertext()) for p in paragraphs) if paragraphs else None
        self._unreadable = None
        if self.format == "string":
            self._value = self.text
        else:
            try:
                self._value = FORMATS[self.format](self.raw_value)
            except (KeyError, TypeError, ValueError):
                self._value = self.text
        self._formula = get(_FORMULA)
        self.is_formula = self._formula is not None
        self.is_empty = (
            self.raw_value is None
            and self._value is None
            and self.format is None
            and self.text is None
            and self._formula is None
        )
        self._own_style_name = None


_VALUE_TYPE, _VALUE = f"{{{OFFICE}}}value-type", f"{{{OFFICE}}}value"
_RAW_VALUE = {kind: f"{{{OFFICE}}}{kind}-value" for kind in ("date", "time", "boolean")}
_P, _FORMULA = f"{{{TEXT}}}p", f"{{{TABLE}}}formula"
_ROW, _CELLS = f"{{{TABLE}}}table-row", (f"{{{TABLE}}}table-cell", f"{{{TABLE}}}covered-table-cell")
_ANNOTATION = f"{{{OFFICE}}}annotation"
_ROWS_REPEATED, _COLS_REPEATED = f"{{{TABLE}}}number-rows-repeated", f"{{{TABLE}}}number-columns-repeated"


def projected_open_and_load(path: Path) -> list[list[_ProjectedCell]]:
    """`ODSReader(path).sheet("Sheet1")` as it would run on lxml: the three
    parts parsed, then `Sheet.load`'s passes over the first sheet - each
    row's cells read for the sheet's width, its last cell checked for
    content, then a cell object per cell, repeats unrolled. The fillers and
    clean-ups `Sheet.load` also does find nothing to do in these workbooks,
    as they find nothing on the bs4 side."""
    with zipfile.ZipFile(path) as package:
        content = etree.fromstring(package.read("content.xml"))
        etree.fromstring(package.read("styles.xml"))
        etree.fromstring(package.read("meta.xml"))
    table = next(content.iter(f"{{{TABLE}}}table"))
    rows = list(table.iterdescendants(_ROW))
    for row in rows:
        cells = list(row.iterchildren(*_CELLS))
        sum(int(cell.get(_COLS_REPEATED, "1")) for cell in cells)
        _ProjectedCell(cells[-1], 0, 0).cell.find(_ANNOTATION)
    grid: list[list[_ProjectedCell]] = []
    for row in rows:
        cells = list(row.iterchildren(*_CELLS))
        for _ in range(int(row.get(_ROWS_REPEATED, "1"))):
            line, col = [], 0
            for cell in cells:
                for _ in range(int(cell.get(_COLS_REPEATED, "1"))):
                    line.append(_ProjectedCell(cell, len(grid), col))
                    col += 1
            grid.append(line)
    return grid


def child(mode: str, path: Path) -> None:
    """Opening and loading `path`, really (`bs4`) or as projected on lxml
    (`lxml`), in this fresh process: print its time and the process's peak
    RSS, in MB."""
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    if mode == "bs4":
        ODSReader(path).sheet("Sheet1")
    else:
        projected_open_and_load(path)
    seconds = time.perf_counter() - t0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scale = 1e6 if sys.platform == "darwin" else 1e3
    print(f"{seconds} {peak / scale} {(peak - before) / scale}")


def in_fresh_process(mode: str, path: Path) -> tuple[float, float]:
    """`child(mode, path)` in a new interpreter: (seconds, peak RSS in MB)."""
    done = subprocess.run(
        [sys.executable, __file__, "--child", mode, str(path)], capture_output=True, text=True, check=True
    )
    seconds, peak, _ = (float(x) for x in done.stdout.split())
    return seconds, peak


PEAK = """
import resource, sys, zipfile
sys.path.insert(0, {src!r})
content = zipfile.ZipFile({path!r}).read("content.xml")
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
if {lib!r} == "bs4":
    from odsslicer.xmlutils import _parse_xml
    tree = _parse_xml(content)
else:
    from lxml import etree
    tree = etree.fromstring(content)
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print((after - before) / (1e6 if sys.platform == "darwin" else 1e3))
"""


def peak_mb(path: Path, lib: str) -> float:
    """What parsing `content.xml` adds to the peak RSS of a fresh process,
    in MB - the bytes of the part already read."""
    code = PEAK.format(src=str(Path(ODSSLICER).parent), path=str(path), lib=lib)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return float(done.stdout)


def timed(path: Path) -> dict[str, float]:
    content = zipfile.ZipFile(path).read("content.xml")
    out: dict[str, float] = {"content.xml (MB)": len(content) / 1e6}
    out["parse, bs4 as odsslicer does"], soup = clock(lambda: _parse_xml(content))
    out["walk, bs4"], n_bs4 = clock(lambda: walk_bs4(soup))
    soup = None  # its memory back before lxml's turn
    out["parse, lxml alone"], root = clock(lambda: etree.fromstring(content))
    out["walk, lxml"], n_lxml = clock(lambda: walk_lxml(root))  # type: ignore[arg-type]
    assert n_bs4 == n_lxml, (n_bs4, n_lxml)
    root = None
    out["parse peak, bs4 (MB)"] = peak_mb(path, "bs4")
    out["parse peak, lxml (MB)"] = peak_mb(path, "lxml")
    out["open, unprofiled"], reader = clock(lambda: ODSReader(path))
    out["load, unprofiled"], _ = clock(lambda: reader.sheet("Sheet1"))  # type: ignore[attr-defined]
    reader = None
    real = [in_fresh_process("bs4", path) for _ in range(3)]
    projected = [in_fresh_process("lxml", path) for _ in range(3)]
    out["open + load, bs4, fresh"] = min(seconds for seconds, _ in real)
    out["open + load, lxml projected"] = min(seconds for seconds, _ in projected)
    out["peak RSS, bs4 (MB)"] = min(peak for _, peak in real)
    out["peak RSS, lxml projected (MB)"] = min(peak for _, peak in projected)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("sizes", nargs="*", type=int, default=[1000, 10000, 100000])
    parser.add_argument("--workdir", help="where the generated workbooks are kept (required)")
    parser.add_argument("--only", choices=("profile", "time"), help="one of the two measurements")
    parser.add_argument("--child", nargs=2, metavar=("MODE", "FILE"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.child[0], Path(args.child[1]))
        return 0
    if not args.workdir:
        parser.error("--workdir is required")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    for n in args.sizes:
        path = workbook(workdir, n)
        print(f"\n## {n:,} rows")
        if args.only != "time":
            for phase, split in profiled(path).items():
                total = sum(split.values())
                print(f"\n{phase}: {total:.2f} s")
                for who, seconds in split.items():
                    print(f"  {who:<28} {seconds:8.2f} s  {seconds / total:6.1%}")
            parts = cell_split(path)
            cells = parts.pop("cells")
            print(f"\nCell() over {cells:,.0f} cells, collector off")
            for label, seconds in parts.items():
                print(f"  {label:<28} {seconds:8.2f} s  {seconds / cells * 1e6:5.2f} us/cell")
            whole, bs4 = parts["Cell(), all of it"], parts["its bs4 calls alone"]
            print(f"  {'odsslicer own share':<28} {(whole - bs4) / whole:8.1%}")
        if args.only != "profile":
            print("\nunprofiled")
            for key, value in timed(path).items():
                unit = "" if "MB" in key else " s"
                print(f"  {key:<28} {value:8.2f}{unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
