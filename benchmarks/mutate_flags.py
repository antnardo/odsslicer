"""Undo, one at a time, each line that tells `save()` a copied part was
written to - and the other guards of the parts `save()` copies through -
and check that a test notices: `mutate_layers.py`'s counterpart for
the package (`package.py`, `drawings.py`, `reader.py`) and the code writing
into `styles.xml`, `settings.xml` and the charts' own parts.

`save()` copies a part byte for byte unless something recorded a write to
its tree (`_Package.touch`, `_Package.touch_tag`): a write that forgets
to is lost on save without a word, and nothing else would show it. Written for step 1
of the lxml migration (ROADMAP.md), which moved `settings.xml` and the
charts to lxml while `styles.xml` stayed on bs4, and `_touched_tag` (now
`_Package.touch_tag`) had to
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
# every test but those that start LibreOffice, slow and in need of it alone
TESTS = ["tests", "--ignore=tests/test_libreoffice_consistency.py"]


# (label, file, text as written, text with the guard undone)
MUTATIONS = [
    # the flags
    (
        "flag: a condition added to a number format of styles.xml",
        "numberformat.py",
        "        self._reader._package.touch_tag(self._tag)\n",
        "        pass\n",
    ),
    (
        "flag: a chart's ranges rewritten, remapped or renamed",
        "drawings.py",
        "            self._package.touch(name)\n",
        "            pass\n",
    ),
    # what the flags rely on
    (
        "touch_tag: a part matched by its tree's root",
        "package.py",
        "            if part.getroot() is top:\n",
        "            if part is top:\n",
    ),
    (
        "touch_tag: getroottree() for an element",
        "package.py",
        "    while (parent := element.getparent()) is not None:\n"
        "        element = parent\n"
        "    return element\n",
        "    return element.getroottree().getroot()\n",
    ),
    (
        "save: a touched part other than styles.xml is serialised",
        "package.py",
        "            if name in self.touched\n",
        "            if name in self.touched and name == 'styles.xml'\n",
    ),
    (
        "save: a touched styles.xml is serialised",
        "package.py",
        "            if name in self.touched\n",
        "            if name in self.touched and name != 'styles.xml'\n",
    ),
    (
        "charts: an embedded object that is no chart is left alone",
        "drawings.py",
        "self._is_chart[name] = next(parts[name].getroot().iter(_OFFICE_CHART), None) is not None",
        "self._is_chart[name] = True",
    ),
    # strict parsing, and the edits that refuse a malformed chart up front
    (
        "strict: a part parsed with recovery",
        "package.py",
        "        return parse(markup)\n    except",
        "        return etree.ElementTree(etree.fromstring(markup, etree.XMLParser(recover=True)))\n"
        "    except",
    ),
    (
        "pre-check: insert_rows",
        "structure.py",
        'rows)")\n        self._references.read_first()\n        if sheet.n_cols == 0',
        'rows)")\n        if sheet.n_cols == 0',
    ),
    (
        "pre-check: insert_columns",
        "structure.py",
        'columns)")\n        self._references.read_first()\n        straddling',
        'columns)")\n        straddling',
    ),
    (
        "pre-check: delete_rows",
        "structure.py",
        'rows)")\n        self._references.read_first()\n        for row in targets:',
        'rows)")\n        for row in targets:',
    ),
    (
        "pre-check: delete_column",
        "structure.py",
        'columns)")\n        self._references.read_first()\n        self._unmerge_column(col)',
        'columns)")\n        self._unmerge_column(col)',
    ),
    (
        "pre-check: rename_sheet",
        "reader.py",
        "        references.read_first()  # a malformed chart raises before anything is renamed\n",
        "",
    ),
    # No mutation for a pre-check through the drawings' cache any more: it
    # raised TypeError on an element the edit then discarded, which
    # `_Drawings._elements` now leaves out (master, 62ea419) - the guard became a
    # preference no test can tell from its absence.
    (
        "pre-check: a refusal forgotten",
        "package.py",
        "        if self._objects_parsed:\n            return\n",
        "        if self._objects_parsed:\n            return\n        self._objects_parsed = True\n",
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
        # 1 is a test failing; any other code - no test collected, a file
        # not found - says nothing of the mutation
        if run.returncode not in (0, 1):
            stale.append(label)
            print(f"  ?  {label}: pytest exited {run.returncode}, its tests are not there", flush=True)
            continue
        caught = run.returncode == 1
        if not caught:
            survivors.append(label)
        print(f"  {'ok' if caught else '!!'} {label}", flush=True)
    print(f"{len(survivors)} mutation(s) no test caught, {len(stale)} out of date")
    sys.exit(1 if survivors or stale else 0)


if __name__ == "__main__":
    main()
