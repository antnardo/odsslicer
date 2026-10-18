"""Find, in real files, the elements a comparison by local name would take
for an ODF one and a comparison by namespace URI would not - the check
behind 1.0's decision to compare element names by URI (ROADMAP.md, step 6).

Until 1.0, odsslicer recognised a row, a group of rows or columns, a column,
a covered cell, and the parts of a number format by their local name alone
(`etree.QName(element).localname`): an element of any namespace named
`table-row` was a row. Since 1.0 the table elements, and the parts of a
number format, are compared by `{URI}local`. This counts, in `content.xml`
and `styles.xml`:

- **table**: an element whose local name is one of the table structure's
  but whose namespace is not the table namespace, anywhere;
- **number format**: a child of a number format (`number:*-style`) whose
  local name is one of the parts `styles.py` reads but whose namespace is
  not the number namespace.

It found no table element in 2,112 of the author's files and 611 of
LibreOffice's test files, and `<loext:text>` in the number formats of 307
and 27 of them: LibreOffice writes it before a fill character, and shows it
as text. Number formats kept comparing local names for it until the audit
before 1.0, which compares them by URI too, to `number:text` and
`loext:text` both (`styles._FORMAT_PARTS`).

    python benchmarks/namespace_scan.py                       # the home directory, by Spotlight
    python benchmarks/namespace_scan.py promotion/corpus/libreoffice-core

**Privacy.** It prints counts, names and file paths, never content.
"""

import argparse
import subprocess
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
NUMBER = "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"
TABLE_NAMES = frozenset(
    {
        "table-row",
        "table-rows",
        "table-header-rows",
        "table-row-group",
        "table-column",
        "table-columns",
        "table-header-columns",
        "table-column-group",
        "table-cell",
        "covered-table-cell",
    }
)
# what `styles.py` reads among a number format's children
NUMBER_PARTS = frozenset(
    {
        "number",
        "text",
        "currency-symbol",
        "boolean",
        "day",
        "month",
        "year",
        "day-of-week",
        "week-of-year",
        "quarter",
        "era",
        "hours",
        "minutes",
        "seconds",
        "am-pm",
    }
)
SKIP_PARTS = ("/Library/", "/.Trash/", "/.claude/worktrees/", "/claude-501/", "/site-packages/")


def files(where: "Path | None") -> list[Path]:
    if where is not None:
        return sorted(where.rglob("*.ods"))
    out = subprocess.run(
        ["mdfind", "-onlyin", str(Path.home()), 'kMDItemFSName == "*.ods"'],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return sorted(Path(line) for line in out.splitlines() if line and not any(s in line for s in SKIP_PARTS))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("where", nargs="?", type=Path, help="a directory; the home directory if left out")
    args = parser.parse_args()
    found: Counter[tuple[str, str, str]] = Counter()
    where: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    parser_ = etree.XMLParser(huge_tree=True)
    read = unreadable = 0
    for path in files(args.where):
        try:
            with zipfile.ZipFile(path) as package:
                names = package.namelist()
                roots = [
                    etree.fromstring(package.read(part), parser_)
                    for part in ("content.xml", "styles.xml")
                    if part in names
                ]
        except Exception:  # an alias, a truncated zip, a malformed part
            unreadable += 1
            continue
        read += 1
        for root in roots:
            for element in root.iter(etree.Element):
                name = etree.QName(element)
                key = None
                if name.localname in TABLE_NAMES and name.namespace != TABLE:
                    key = ("table", str(name.namespace), name.localname)
                parent = element.getparent()
                if (
                    key is None
                    and parent is not None
                    and name.localname in NUMBER_PARTS
                    and name.namespace != NUMBER
                ):
                    of = etree.QName(parent)
                    if of.namespace == NUMBER and of.localname.endswith("-style"):
                        key = (f"number format ({of.localname})", str(name.namespace), name.localname)
                if key is not None:
                    found[key] += 1
                    where[key].add(str(path))
    print(f"{read} files read, {unreadable} unreadable")
    if not found:
        print("no element a comparison by local name would take for another")
    for key, count in found.most_common():
        print(
            f"{count} {key[0]}: {{{key[1]}}}{key[2]} in {len(where[key])} files, e.g. {sorted(where[key])[0]}"
        )


if __name__ == "__main__":
    main()
