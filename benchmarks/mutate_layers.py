"""Break each guard of the XML layer, `xmltree`, and of the code built on
it, one at a time, and check that a test notices - the proof that
`tests/test_xmltree.py` tests what the layer is for, and not merely that it
runs.

A guard is a line that is there because the obvious translation is wrong:
the tail handed over in `remove`, the namespaces `new` declares, an element
compared with `is None` where `or` would skip one with no children, a row
placed next to a known one rather than by index. Each mutation below undoes
one; the run reports any that leaves its tests passing - a guard nothing
tests, or a line that guards nothing.

Until 1.0 it broke the guards of `xmlbs` too, the bs4 layer, against the
same parity tests: the mutations that went with it are in ROADMAP.md, step 5.

    python benchmarks/mutate_layers.py          # every mutation
    python benchmarks/mutate_layers.py remove   # those whose label says so

The files are restored after each run, and on Ctrl+C. When a layer
changes, a mutation whose text no longer matches is reported as such:
update it here.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAYERS = REPO / "src" / "odsslicer"
TESTS = ["tests/test_xmltree.py"]
# the code built on the layer has its tests elsewhere: the mutations of
# those guards name the tests that hold them
POSITIONAL = ["tests/test_odsslicer.py", "-k", "never_places"]
NAMES = ["tests/test_odsslicer.py", "-k", "NamesByNamespace"]
READ_FROM_ELEMENT = ["tests/test_odsslicer.py", "-k", "holds_content or paragraphs_joined"]
COPIES = ["tests/test_odsslicer.py", "-k", "read_afresh or repeated_over_columns"]


def _by_index(reference: str, element: str, *, after: bool) -> str:
    """A placement next to `reference` written by index, as 0.15.0 wrote
    it: what lxml's `insert()` walks the parent's children to."""
    offset = " + 1" if after else ""
    return f"{reference}.getparent().insert({reference}.getparent().index({reference}){offset}, {element})"


# (label, file, text as written, text with the guard undone[, tests to run])
MUTATIONS = [
    # lxml: the tail
    ("xmltree.remove drops the text after", "xmltree.py", "    _say_before(element, element.tail)\n", ""),
    ("xmltree.clone keeps the tail", "xmltree.py", "    copied.tail = None\n", ""),
    (
        "xmltree.insert_after lands past the text",
        "xmltree.py",
        "    element.tail, reference.tail = reference.tail, None\n",
        "",
    ),
    (
        "xmltree.insert_before inserts after",
        "xmltree.py",
        "reference.addprevious(element)",
        "reference.addnext(element)",
    ),
    ("xmltree.unwrap drops the element's text", "xmltree.py", "    _say_before(element, element.text)\n", ""),
    (
        "xmltree.unwrap drops the text after the element",
        "xmltree.py",
        '        inner[-1].tail = (inner[-1].tail or "") + (element.tail or "")\n',
        "",
    ),
    (
        "xmltree.wrap_contents leaves the leading text out",
        "xmltree.py",
        "    wrapper.text, element.text = element.text, None\n",
        "",
    ),
    (
        "xmltree.set_paragraph_text writes the text after an element into the paragraph's",
        "xmltree.py",
        "                last.tail = pending\n",
        "                paragraph.text = (paragraph.text or '') + pending\n",
    ),
    # lxml: text, search, names
    (
        "xmltree.text stops at the first child",
        "xmltree.py",
        'return "".join(cast("Iterator[str]", element.itertext()))',
        'return element.text or ""',
    ),
    (
        "xmltree.set_text keeps the children",
        "xmltree.py",
        "        element.remove(child)\n",
        "        pass\n",
    ),
    ("xmltree.paragraph_text drops the encoded spaces", "xmltree.py", "parts.append(_spaces(child))", "pass"),
    (
        "xmltree.children searches the descendants",
        "xmltree.py",
        "return list(element.iterchildren(qn(name)))",
        "return list(element.iterdescendants(qn(name)))",
    ),
    (
        "xmltree.new declares no prefix",
        "xmltree.py",
        "etree.Element(qn(name), nsmap=nsmap)",
        "etree.Element(qn(name))",
    ),
    (
        "xmltree.local_name keeps the namespace",
        "xmltree.py",
        "cast(str, etree.QName(element).localname)",
        "cast(str, element.tag)",
    ),
    (
        "xmltree.name_of gives the URI form",
        "xmltree.py",
        "return prefixed(cast(str, element.tag))",
        "return cast(str, element.tag)",
    ),
    (
        "xmltree.namespace_of gives nothing",
        "xmltree.py",
        'return cast("str | None", etree.QName(element).namespace)',
        "return None",
    ),
    ("xmltree.rename renames nothing", "xmltree.py", "    element.tag = qn(name)\n", "    pass\n"),
    # lxml: what came with the switch
    (
        "xmltree.child searches the descendants",
        "xmltree.py",
        "return next(element.iterchildren(qn(name)), None)",
        "return next(element.iterdescendants(qn(name)), None)",
    ),
    (
        "xmltree.elements keeps the comments",
        "xmltree.py",
        "return list(element.iterchildren(etree.Element))",
        "return list(element)",
    ),
    (
        "xmltree.attributes keys an unnamed namespace by URI",
        "xmltree.py",
        '            if prefix is not None:\n                key = f"{prefix}:{local}"\n',
        "",
    ),
    (
        "xmltree.unset removes nothing",
        "xmltree.py",
        "        del element.attrib[name]\n",
        "        pass\n",
    ),
    (
        "xmltree.new declares no namespace for its attributes",
        "xmltree.py",
        "    for qualified in (name, *(attrs or ())):\n",
        "    for qualified in (name,):\n",
    ),
    (
        "xmltree.parse refuses a part past libxml2's size limit",
        "xmltree.py",
        "remove_blank_text=False, huge_tree=True",
        "remove_blank_text=False",
    ),
    # the code on the layer: an element with no children is false in lxml
    (
        "NumberFormat: `or` between two children",
        "numberformat.py",
        '        number_tag = child(tag, "number:number")\n        if number_tag is None:\n'
        '            number_tag = child(tag, "number:boolean")\n',
        '        number_tag = child(tag, "number:number") or child(tag, "number:boolean")\n',
        ["tests/test_odsslicer.py", "-k", "style_resolves or number_format"],
    ),
    (
        "_find_in_styles: `or` between the two parts",
        "reader.py",
        "return found if found is not None else first(self.styles_data.getroot().iter(*tags))",
        "return found or first(self.styles_data.getroot().iter(*tags))",
        ["tests/test_odsslicer.py", "tests/test_reader.py"],
    ),
    (
        "_drawings: a drawing deleted with its cells still follows",
        "reader.py",
        "        if not all(_root_of(tag) is root for tag in (*ended, *objects)):\n",
        "        if False:\n",
        ["tests/test_odsslicer.py", "-k", "chart"],
    ),
    # sheet.py: an element is its namespace and local name, not its local name alone
    (
        "_sheet_rows takes a row of another namespace for a row",
        "sheet.py",
        "    for element in parent.iterchildren(*_ROW_LEVEL):\n        if element.tag == _ROW:\n",
        "    for element in parent.iterchildren(etree.Element):\n"
        '        if etree.QName(element).localname == "table-row":\n',
        NAMES,
    ),
    (
        "_column_definitions takes a column of another namespace for a column",
        "sheet.py",
        "            if name == _COLUMN:\n",
        '            if etree.QName(element).localname == "table-column":\n',
        NAMES,
    ),
    # the grid's guards read the element, and say what a `Cell` would say
    (
        "_holds_content forgets the value type",
        "sheet.py",
        "        cell_tag.get(_VALUE_TYPE) is not None\n        or ",
        "        ",
        READ_FROM_ELEMENT,
    ),
    (
        "_holds_content forgets office:value",
        "sheet.py",
        "        or cell_tag.get(_VALUE) is not None\n",
        "",
        READ_FROM_ELEMENT,
    ),
    (
        "_holds_content forgets the formula",
        "sheet.py",
        "        or cell_tag.get(_FORMULA) is not None\n",
        "",
        READ_FROM_ELEMENT,
    ),
    (
        "_holds_content forgets the note",
        "sheet.py",
        '_PARAGRAPH_OR_NOTE = (qn("text:p"), qn("office:annotation"))',
        '_PARAGRAPH_OR_NOTE = (qn("text:p"),)',
        READ_FROM_ELEMENT,
    ),
    (
        "_holds_content looks past the cell's children",
        "sheet.py",
        "next(cell_tag.iterchildren(*_PARAGRAPH_OR_NOTE), None)",
        "next(cell_tag.iterdescendants(*_PARAGRAPH_OR_NOTE), None)",
        READ_FROM_ELEMENT,
    ),
    (
        "Cell reads a lone child as a paragraph",
        "cell.py",
        "            if only.tag != _TEXT_P:\n",
        "            if False:\n",
        READ_FROM_ELEMENT,
    ),
    (
        "load copies a repeated time value's first reading",
        "sheet.py",
        '                            if built.format != "time":\n',
        "                            if True:\n",
        COPIES,
    ),
    (
        "Cell._at keeps the first position's column",
        "cell.py",
        "        copy.row, copy.col = row, col\n",
        "        copy.row = row\n",
        COPIES,
    ),
    (
        "sort looks one column's default style up for every column",
        "sheet.py",
        "            return column_defaults[cell.col]\n",
        "            return next(iter(column_defaults.values()))\n",
        ["tests/test_odsslicer.py", "-k", "sort"],
    ),
    # sheet.py: next to a known element, never by index
    (
        "a row grown past the data placed by index",
        "sheet.py",
        "                insert_after(previous, new_row_tag)\n",
        "                " + _by_index("previous", "new_row_tag", after=True) + "\n",
        POSITIONAL,
    ),
    (
        "a chain of copies placed by index",
        "sheet.py",
        "        insert_after(reference, element)\n        reference = element\n",
        "        " + _by_index("reference", "element", after=True) + "\n        reference = element\n",
        POSITIONAL,
    ),
    (
        "inserted rows placed by index",
        "sheet.py",
        "                insert_before(before, row_tag)\n",
        "                " + _by_index("before", "row_tag", after=False) + "\n",
        POSITIONAL,
    ),
    (
        "inserted cells placed by index",
        "sheet.py",
        "                    insert_before(anchor, new_tag)\n",
        "                    " + _by_index("anchor", "new_tag", after=False) + "\n",
        POSITIONAL,
    ),
]


def main() -> None:
    wanted = sys.argv[1:]
    survivors, stale = [], []
    for label, name, before, after, *tests in MUTATIONS:
        if wanted and not any(word in label for word in wanted):
            continue
        path = LAYERS / name
        original = path.read_text()
        if original.count(before) != 1:
            stale.append(label)
            print(f"  ?  {label}: the text to mutate is not there once")
            continue
        try:
            path.write_text(original.replace(before, after))
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-x",
                    "-p",
                    "no:cacheprovider",
                    *(tests[0] if tests else TESTS),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
        finally:
            path.write_text(original)
        caught = run.returncode != 0
        if not caught:
            survivors.append(label)
        print(f"  {'ok' if caught else '!!'} {label}")
    print(f"{len(survivors)} mutation(s) no test caught, {len(stale)} out of date")
    sys.exit(1 if survivors or stale else 0)


if __name__ == "__main__":
    main()
