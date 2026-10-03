"""Count the places `src/odsslicer` touches BeautifulSoup, by the operation
each one needs - the inventory behind the lxml feasibility study
(`benchmarks/LXML_STUDY.md`).

A grep for `.append(` or `.get(` cannot tell a `Tag` from a list or a dict,
and the code holds plenty of both. mypy can: this runs it over `src` with a
small plugin that records every method call, attribute access and copy whose
receiver mypy types as a bs4 class, with its line. What mypy types as `Any`
escapes it - an element taken from a `find_all()` result, `Cell.attrs` (the
element's own attribute dict under another name) - so a second pass adds,
from the source text, the members only bs4 has (`find_all`, `decompose`,
`.attrs`...). A member that lists and dicts have too (`append`, `get`,
`name`) is counted only where mypy saw a bs4 receiver: for those the table
is a floor. Type mentions (`Tag` in annotations and casts) are counted from
the source text, since they are names, not calls. A site is a (line, member)
pair: two `.attrs` on one line count once.

    python benchmarks/lxml_inventory.py            # the table by family
    python benchmarks/lxml_inventory.py --sites    # every site, file:line
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "odsslicer"  # --src changes it, to count another checkout

# What each bs4 member is used for, by the operation lxml has to provide
# instead. A member missing here shows up under "other", so that a new use
# cannot slip past the table.
FAMILIES = {
    "search, descendants or children by name": {
        "find",
        "find_all",
        "__call__",
        "select",
        "select_one",
    },
    "search, in document order or upwards": {
        "find_parent",
        "find_parents",
        "find_next",
        "find_previous",
        "find_all_next",
        "find_all_previous",
        "find_next_sibling",
        "find_previous_sibling",
        "find_next_siblings",
        "find_previous_siblings",
        "previous_elements",
        "next_elements",
    },
    "attributes": {"attrs", "get", "__getitem__", "__setitem__", "__delitem__", "__contains__", "has_attr"},
    "text of an element and its descendants": {"get_text", "string", "text", "strings", "stripped_strings"},
    "clone": {"copy.deepcopy", "copy.copy", "__copy__", "__deepcopy__"},
    "insert before, after, at, at the end": {"insert_before", "insert_after", "insert", "append", "extend"},
    "remove": {"decompose", "extract", "clear", "replace_with", "unwrap"},
    "parent, siblings, children": {
        "parent",
        "children",
        "contents",
        "descendants",
        "next_sibling",
        "previous_sibling",
        "next_siblings",
        "previous_siblings",
        "next_element",
        "previous_element",
    },
    "qualified name of an element": {"name", "prefix", "namespace"},
    "parse, build, serialise": {"BeautifulSoup", "new_tag", "new_string", "encode", "decode", "prettify"},
}

PLUGIN = r"""
import json, os
from mypy.plugin import Plugin

OUT = os.environ["LXML_INVENTORY_OUT"]
SEEN = set()


def record(ctx, fullname):
    path = getattr(ctx.api, "path", "?")
    key = (path, ctx.context.line, ctx.context.column, fullname)
    if key in SEEN:
        return
    SEEN.add(key)
    with open(OUT, "a") as f:
        f.write(json.dumps(key) + "\n")


def is_bs4(t):
    return "bs4." in str(getattr(t, "type", ""))


class Inventory(Plugin):
    def get_method_hook(self, fullname):
        if fullname.startswith("bs4."):
            def hook(ctx):
                record(ctx, fullname)
                return ctx.default_return_type
            return hook
        return None

    def get_attribute_hook(self, fullname):
        if fullname.startswith("bs4."):
            def hook(ctx):
                record(ctx, fullname)
                return ctx.default_attr_type
            return hook
        return None

    def get_function_hook(self, fullname):
        if fullname in ("copy.deepcopy", "copy.copy"):
            def hook(ctx):
                if ctx.arg_types and ctx.arg_types[0] and "bs4" in str(ctx.arg_types[0][0]):
                    record(ctx, fullname)
                return ctx.default_return_type
            return hook
        if fullname.startswith("bs4."):
            def hook(ctx):
                record(ctx, fullname)
                return ctx.default_return_type
            return hook
        return None


def plugin(version):
    return Inventory
"""


def run_mypy() -> list[tuple[str, int, str]]:
    """Every bs4 member used under `src`, as (module, line, member)."""
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "inventory_plugin.py"
        plugin.write_text(PLUGIN)
        config = Path(tmp) / "mypy.ini"
        config.write_text(f"[mypy]\nplugins = {plugin}\nignore_missing_imports = True\n")
        out = Path(tmp) / "sites.jsonl"
        out.touch()
        env = {**os.environ, "LXML_INVENTORY_OUT": str(out)}
        subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(config),
                "--no-incremental",
                "--cache-dir",
                str(Path(tmp) / "cache"),
                str(SRC),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO,
        )
        sites = set()
        for line in out.read_text().splitlines():
            path, lineno, _, fullname = json.loads(line)
            if (REPO / path).resolve().parent != SRC and Path(path).resolve().parent != SRC:
                continue
            member = fullname if fullname.startswith("copy.") else fullname.rsplit(".", 1)[-1]
            if fullname.endswith(".BeautifulSoup") or fullname == "bs4.BeautifulSoup":
                member = "BeautifulSoup"
            sites.add((Path(path).stem, lineno, member))
    return sorted(sites)


# Members no list, dict or str has: wherever they appear, they are bs4's.
UNAMBIGUOUS = re.compile(
    r"\.(attrs|find|find_all|find_parent|find_parents|find_next|find_previous|find_all_next"
    r"|find_all_previous|find_next_sibling|find_previous_sibling|find_next_siblings"
    r"|find_previous_siblings|previous_elements|next_elements|next_siblings|previous_siblings"
    r"|decompose|extract|insert_before|insert_after|replace_with|unwrap|get_text|prettify"
    r"|has_attr)\b"
)


def text_sites() -> set[tuple[str, int, str]]:
    """The members of `UNAMBIGUOUS` used in the code under `src` - comments
    and docstrings aside - as (module, line, member). A module that does not
    import bs4 is left out: its `find` is lxml's."""
    sites = set()
    for path in sorted(SRC.glob("*.py")):
        source = path.read_text()
        if not re.search(r"^(from bs4 |import bs4)", source, re.MULTILINE):
            continue
        prose = {
            line
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.end_lineno
            for line in range(node.lineno, node.end_lineno + 1)
            if node.end_lineno > node.lineno  # docstrings; one-line strings hold no call
        }
        for lineno, line in enumerate(source.splitlines(), 1):
            if lineno in prose:
                continue
            for match in UNAMBIGUOUS.finditer(line.split("#", 1)[0]):
                sites.add((path.stem, lineno, match.group(1)))
    return sites


def type_mentions() -> Counter[str]:
    """`Tag`/`BeautifulSoup` named as a type - annotations and casts - by
    module, imports aside."""
    counts: Counter[str] = Counter()
    for path in sorted(SRC.glob("*.py")):
        if not re.search(r"^(from bs4 |import bs4)", path.read_text(), re.MULTILINE):
            continue
        for line in path.read_text().splitlines():
            if line.lstrip().startswith(("from ", "import ")):
                continue
            counts[path.stem] += len(re.findall(r"\b(?:Tag|BeautifulSoup)\b(?!\()", line))
    return counts


def family_of(member: str) -> str:
    for family, members in FAMILIES.items():
        if member in members:
            return family
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sites", action="store_true", help="list every site")
    parser.add_argument("--src", type=Path, help="the package directory to count, src/odsslicer by default")
    args = parser.parse_args()
    if args.src:
        global SRC
        SRC = args.src.resolve()

    # iterating over a find_all() result is that search again, not an
    # operation of its own
    sites = sorted({site for site in run_mypy() if site[2] != "__iter__"} | text_sites())
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    members: dict[str, Counter[str]] = defaultdict(Counter)
    for module, _, member in sites:
        family = family_of(member)
        by_family[family][module] += 1
        members[family][member] += 1
    mentions = type_mentions()

    print(f"{'family':<42} {'sites':>5}  modules")
    for family in [*FAMILIES, "other"]:
        if family not in by_family:
            continue
        modules = ", ".join(f"{m} {n}" for m, n in by_family[family].most_common())
        print(f"{family:<42} {sum(by_family[family].values()):>5}  {modules}")
        print(f"{'':<42} {'':>5}  ({', '.join(f'{m} {n}' for m, n in members[family].most_common())})")
    print(f"{'calls and accesses, total':<42} {len(sites):>5}")
    print(
        f"{'type mentions (Tag, BeautifulSoup)':<42} {sum(mentions.values()):>5}  "
        + ", ".join(f"{m} {n}" for m, n in mentions.most_common() if n)
    )
    lines = sum(len(p.read_text().splitlines()) for p in SRC.glob("*.py"))
    print(f"{'lines under src/odsslicer':<42} {lines:>5}")
    if args.sites:
        for module, lineno, member in sites:
            print(f"{module}.py:{lineno}  {member}  [{family_of(member)}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
