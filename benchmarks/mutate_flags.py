"""Undo, one at a time, each line that tells `save()` a copied part was
written to - and the other guards of the parts `save()` copies through -
and check that a test notices: `mutate_layers.py`'s counterpart for
`reader.py` and the code writing into `styles.xml`, `settings.xml` and the
charts' own parts.

`save()` copies a part byte for byte unless something recorded a write to
its tree (`_touched_part`, `_touched_tag`): a write that forgets to is lost
on save without a word, and nothing else would show it. Written for step 1
of the lxml migration (ROADMAP.md), which moved `settings.xml` and the
charts to lxml while `styles.xml` stayed on bs4, and `_touched_tag` had to
find the part of an element of either library; since step 5 every part is
on lxml. Run it again when a new writer appears.

    python benchmarks/mutate_flags.py              # every mutation
    python benchmarks/mutate_flags.py pre-check    # those whose label says so

The files are restored after each run, and on Ctrl+C. A mutation whose text
no longer matches is reported as out of date: update it here.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "odsslicer"
TESTS = ["tests/test_reader.py", "tests/test_odsslicer.py"]


def _chart_loop(verb: str, flag: str) -> str:
    return (
        "        for name, chart in self._charts():\n"
        f"            {flag}\n"
        "            for attr in _CHART_RANGE_ATTRS:\n"
        "                for element in _carrying(chart, attr):\n"
        f"                    element.set(attr, {verb}("
    )


# (label, file, text as written, text with the guard undone)
MUTATIONS = [
    # the flags
    (
        "flag: a condition added to a number format of styles.xml",
        "numberformat.py",
        "        self._reader._touched_tag(self._tag)\n",
        "        pass\n",
    ),
    (
        "flag: a chart's ranges remapped",
        "reader.py",
        _chart_loop("_remap_range_list", "self._touched_part(name)"),
        _chart_loop("_remap_range_list", "pass"),
    ),
    (
        "flag: a chart's ranges renamed",
        "reader.py",
        _chart_loop("_rename_range_list", "self._touched_part(name)"),
        _chart_loop("_rename_range_list", "pass"),
    ),
    # what the flags rely on
    (
        "_touched_tag: a part matched by its tree's root",
        "reader.py",
        "            if part.getroot() is top:\n",
        "            if part is top:\n",
    ),
    (
        "_touched_tag: getroottree() for an element",
        "reader.py",
        "        top = tag\n"
        "        while (parent := top.getparent()) is not None:\n"
        "            top = parent\n",
        "        top = tag.getroottree().getroot()\n",
    ),
    (
        "save: a touched part other than styles.xml is serialised",
        "reader.py",
        "            if name in self._touched\n",
        "            if name in self._touched and name == 'styles.xml'\n",
    ),
    (
        "save: a touched styles.xml is serialised",
        "reader.py",
        "            if name in self._touched\n",
        "            if name in self._touched and name != 'styles.xml'\n",
    ),
    (
        "_charts: an embedded object that is no chart is left alone",
        "reader.py",
        "return (tree, markup) if next(tree.getroot().iter(_OFFICE_CHART), None) is not None else None",
        "return (tree, markup)",
    ),
    # strict parsing, and the edits that refuse a malformed chart up front
    (
        "strict: a part parsed with recovery",
        "reader.py",
        "        return parse(markup)\n    except",
        "        return etree.ElementTree(etree.fromstring(markup, etree.XMLParser(recover=True)))\n"
        "    except",
    ),
    (
        "pre-check: insert_rows",
        "sheet.py",
        'rows)")\n        self._read_charts_first()\n        if self.n_cols == 0',
        'rows)")\n        if self.n_cols == 0',
    ),
    (
        "pre-check: insert_columns",
        "sheet.py",
        'columns)")\n        self._read_charts_first()\n        straddling',
        'columns)")\n        straddling',
    ),
    (
        "pre-check: delete_rows",
        "sheet.py",
        'rows)")\n        self._read_charts_first()\n        for row in targets:',
        'rows)")\n        for row in targets:',
    ),
    (
        "pre-check: delete_column",
        "sheet.py",
        'columns)")\n        self._read_charts_first()\n        r = 0',
        'columns)")\n        r = 0',
    ),
    (
        "pre-check: rename_sheet",
        "reader.py",
        "        self._read_objects()  # a malformed chart raises before anything is renamed\n",
        "",
    ),
    # No mutation for a pre-check through the drawings' cache any more: it
    # raised TypeError on an element the edit then discarded, which
    # `_drawings` now leaves out (master, 62ea419) - the guard became a
    # preference no test can tell from its absence.
    (
        "pre-check: a refusal forgotten",
        "reader.py",
        "        if self._objects_read:\n            return\n",
        "        if self._objects_read:\n            return\n        self._objects_read = True\n",
    ),
]


def main() -> None:
    wanted = sys.argv[1:]
    survivors, stale = [], []
    for label, name, before, after in MUTATIONS:
        if wanted and not any(word in label for word in wanted):
            continue
        path = SRC / name
        original = path.read_text()
        if original.count(before) != 1:
            stale.append(label)
            print(f"  ?  {label}: the text to mutate is not there once")
            continue
        try:
            path.write_text(original.replace(before, after))
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
        finally:
            path.write_text(original)
        caught = run.returncode != 0
        if not caught:
            survivors.append(label)
        print(f"  {'ok' if caught else '!!'} {label}", flush=True)
    print(f"{len(survivors)} mutation(s) no test caught, {len(stale)} out of date")
    sys.exit(1 if survivors or stale else 0)


if __name__ == "__main__":
    main()
