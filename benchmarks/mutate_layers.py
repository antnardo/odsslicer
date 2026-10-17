"""Break each guard of the two XML layers, `xmltree` (lxml) and `xmlbs`
(bs4), one at a time, and check that the parity tests notice - the proof
that `tests/test_xmlbs.py` and `tests/test_xmltree.py` test what the layers
are for, and not merely that they run.

A guard is a line that is there because the obvious translation is wrong:
the tail handed over in `remove`, the declaration `new` writes when the
document has no element of that name. Each mutation below undoes one; the
run reports any that leaves both test files passing - a guard nothing
tests, or a line that guards nothing.

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
TESTS = ["tests/test_xmlbs.py", "tests/test_xmltree.py"]

# (label, file, text as written, text with the guard undone)
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
        "etree.Element(qn(name), nsmap={_PREFIXES[uri]: uri})",
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
    # bs4: creating
    (
        "xmlbs.new never declares",
        "xmlbs.py",
        "if near is not None and _named_like(near, name) is None else {}",
        "if False else {}",
    ),
    (
        "xmlbs.new always declares",
        "xmlbs.py",
        "if near is not None and _named_like(near, name) is None else {}",
        "if True else {}",
    ),
    (
        "xmlbs.new without an XML builder",
        "xmlbs.py",
        "return _FACTORY.new_tag(local, namespace=uri, nsprefix=prefix, attrs={**declared, **(attrs or {})})",
        "return Tag(None, None, local, uri, prefix, attrs={**declared, **(attrs or {})})",
    ),
    # bs4: text, moving, search, names
    (
        "xmlbs.text reads one string only",
        "xmlbs.py",
        "    return element.get_text()\n",
        '    return element.string or ""\n',
    ),
    ("xmlbs.set_text appends", "xmlbs.py", "    element.string = value\n", "    element.append(value)\n"),
    (
        "xmlbs.remove destroys and returns nothing",
        "xmlbs.py",
        "return cast(Tag, element.extract())",
        "return element.decompose()",
    ),
    ("xmlbs.clone copies nothing", "xmlbs.py", "    return copy.deepcopy(element)\n", "    return element\n"),
    (
        "xmlbs.insert_after inserts before",
        "xmlbs.py",
        "    reference.insert_after(element)\n",
        "    reference.insert_before(element)\n",
    ),
    (
        "xmlbs.insert_before inserts after",
        "xmlbs.py",
        "    reference.insert_before(element)\n",
        "    reference.insert_after(element)\n",
    ),
    ("xmlbs.unwrap drops the content", "xmlbs.py", "    element.unwrap()\n", "    element.decompose()\n"),
    (
        "xmlbs.wrap_contents moves the elements only",
        "xmlbs.py",
        "    for child in list(element.children):\n",
        "    for child in element.find_all(True, recursive=False):\n",
    ),
    (
        "xmlbs.children ignores the prefix",
        "xmlbs.py",
        "(child.name == local and child.prefix == prefix)",
        "child.name == local",
    ),
    (
        "xmlbs.children searches the descendants",
        "xmlbs.py",
        "        for child in element.contents\n",
        "        for child in element.descendants\n",
    ),
    (
        "xmlbs.local_name gives the prefixed name",
        "xmlbs.py",
        "    return cast(str, element.name)\n",
        "    return name_of(element)\n",
    ),
    (
        "xmlbs.name_of drops the prefix",
        "xmlbs.py",
        'return f"{element.prefix}:{element.name}" if element.prefix else',
        "return",
    ),
    (
        "xmlbs.namespace_of gives the prefix",
        "xmlbs.py",
        'return cast("str | None", element.namespace)',
        'return cast("str | None", element.prefix)',
    ),
    (
        "xmlbs.rename renames nothing",
        "xmlbs.py",
        "    element.name, element.prefix, element.namespace = local, prefix, NAMESPACES[prefix]\n",
        "    pass\n",
    ),
    (
        "xmlbs.paragraph_text drops the encoded spaces",
        "xmlbs.py",
        'parts.append(" " * _space_count(node))',
        "pass",
    ),
    (
        "xmlbs.paragraph_text ignores text:c",
        "xmlbs.py",
        'parts.append(" " * _space_count(node))',
        'parts.append(" ")',
    ),
    ("xmlbs.set_paragraph_text keeps the old content", "xmlbs.py", "    paragraph.clear()\n", ""),
]


def main() -> None:
    wanted = sys.argv[1:]
    survivors, stale = [], []
    for label, name, before, after in MUTATIONS:
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
        print(f"  {'ok' if caught else '!!'} {label}")
    print(f"{len(survivors)} mutation(s) no test caught, {len(stale)} out of date")
    sys.exit(1 if survivors or stale else 0)


if __name__ == "__main__":
    main()
