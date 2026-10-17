"""Where real files tell "the children of" from "anything under": step 3 of
the lxml migration (ROADMAP.md, "Enfants ou descendants, site par site").

bs4's `find`/`find_all` search every descendant unless told otherwise, and
the code relies on that default in some fifty places. Most of them mean the
children - a row's cells, a style's properties - and some mean the
descendants. Where the two give the same answer on every file, the choice
changes nothing a reader sees; where they differ, the difference is what the
fix is about, and it has to be judged against LibreOffice. This script
counts, structure by structure, the files and the elements where the two
answers differ, walking the XML with lxml alone - nothing from odsslicer -
so that it can serve as the oracle of the step.

Privacy: these are the user's own spreadsheets, some holding pupils' names.
The report carries counts and file paths only, never a cell's content.

Usage:
    python benchmarks/children_scan.py --limit 500
    python benchmarks/children_scan.py --corpus tests/wild
"""

import argparse
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
from sweep_real_files import MAX_BYTES_DEFAULT, sample, spotlight_files, usable

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def qn(name: str) -> str:
    prefix, local = name.split(":")
    return f"{{{NS[prefix]}}}{local}"


CELLS = (qn("table:table-cell"), qn("table:covered-table-cell"))
ROW_CONTAINERS = {qn(n) for n in ("table:table-header-rows", "table:table-rows", "table:table-row-group")}
STYLE_PROPERTIES = [
    "style:table-cell-properties",
    "style:text-properties",
    "style:paragraph-properties",
    "style:table-row-properties",
    "style:table-column-properties",
    "style:table-properties",
]
NUMBER_CHILDREN = ["number:number", "number:boolean", "number:currency-symbol", "style:text-properties"]


def rows_through_containers(table: etree._Element) -> list[etree._Element]:
    """The rows of a sheet the way ODF nests them: in the table itself, or in
    header rows and row groups at any depth - never inside a cell."""
    found = []
    for child in table:
        if child.tag == qn("table:table-row"):
            found.append(child)
        elif child.tag in ROW_CONTAINERS:
            found.extend(rows_through_containers(child))
    return found


def differs(children: list, descendants: list) -> int:
    """How many elements the descendants count that the children do not."""
    return len(descendants) - len(children) if len(descendants) != len(children) else 0


def scan_part(root: etree._Element, part: str, counts: Counter, files: dict, path: Path) -> None:
    def note(site: str, n: int) -> None:
        if n:
            counts[site] += n
            files[site].add(path)

    if part == "content.xml":
        spreadsheet = root.find(f"{qn('office:body')}/{qn('office:spreadsheet')}")
        sheets = spreadsheet.findall(qn("table:table")) if spreadsheet is not None else []
        note(
            "sheets: table:table outside office:spreadsheet",
            differs(sheets, list(root.iter(qn("table:table")))),
        )
        for table in sheets:
            rows = rows_through_containers(table)
            note(
                "rows: table:table-row not reached through row containers",
                differs(rows, list(table.iter(qn("table:table-row")))),
            )
            for row in rows:
                cells = [c for c in row if c.tag in CELLS]
                note("cells: a row's cell that is not its child", differs(cells, list(row.iter(*CELLS))))
            for group in table.iter(qn("table:table-column-group")):
                direct = group.findall(qn("table:table-column"))
                if not direct and next(group.iter(qn("table:table-column")), None) is not None:
                    note("column groups: columns only in a nested group", 1)
        for annotation in root.iter(qn("office:annotation")):
            note(
                "notes: text:p not a child of the note",
                differs(annotation.findall(qn("text:p")), list(annotation.iter(qn("text:p")))),
            )
            note("notes: holding a text:list", int(annotation.find(qn("text:list")) is not None))
            for name in ("dc:creator", "dc:date"):
                note(
                    f"notes: {name} not a child",
                    differs(annotation.findall(qn(name)), list(annotation.iter(qn(name)))),
                )
        if spreadsheet is not None:
            note(
                "pivot tables: table:data-pilot-tables not a child of the spreadsheet",
                differs(
                    spreadsheet.findall(qn("table:data-pilot-tables")),
                    list(spreadsheet.iter(qn("table:data-pilot-tables"))),
                ),
            )
    for name in ("office:automatic-styles", "office:body", "office:styles"):
        first = next(root.iter(qn(name)), None)
        if first is not None and first.getparent() is not root:
            note(f"parts: first {name} not a child of the root", 1)
    for style in root.iter(qn("style:style"), qn("style:default-style")):
        for name in STYLE_PROPERTIES:
            if style.find(qn(name)) is None and next(style.iter(qn(name)), None) is not None:
                note(f"styles: {name} only below a child", 1)
            elif style.find(qn(name)) is not None and next(style.iter(qn(name))) is not style.find(qn(name)):
                note(f"styles: first {name} in document order not the child", 1)
    for container in root.iter(qn("office:automatic-styles"), qn("office:styles")):
        for number_style in container:
            if not isinstance(number_style.tag, str) or not number_style.tag.startswith(
                f"{{{NS['number']}}}"
            ):
                continue
            for name in NUMBER_CHILDREN:
                child = number_style.find(qn(name))
                first = next(number_style.iter(qn(name)), None)
                if first is not None and first is not child:
                    note(f"number styles: first {name} not a child", 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", help="scan this directory instead of the home directory")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.corpus:
        candidates = sorted(Path(args.corpus).rglob("*.ods"))
        files = candidates[: args.limit]
    else:
        files = sample(
            [p for p in spotlight_files() if usable(p, MAX_BYTES_DEFAULT)],
            args.limit,
            MAX_BYTES_DEFAULT,
            args.seed,
        )
    counts: Counter = Counter()
    where: dict = defaultdict(set)
    unreadable = 0
    for path in files:
        try:
            with zipfile.ZipFile(path) as package:
                for part in ("content.xml", "styles.xml"):
                    if part in package.namelist():
                        scan_part(etree.fromstring(package.read(part)), part, counts, where, path)
        except Exception:
            unreadable += 1
    print(f"{len(files)} files scanned, {unreadable} unreadable")
    if not counts:
        print("children and descendants agree everywhere")
    for site, n in sorted(counts.items()):
        print(f"  {site}: {n} elements in {len(where[site])} files")
        for path in sorted(where[site])[:5]:
            print(f"      {path}")


if __name__ == "__main__":
    main()
