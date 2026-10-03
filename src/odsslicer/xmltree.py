"""The operations odsslicer needs from an XML tree, on lxml alone - the
prototype of the access layer that is to replace BeautifulSoup at 1.0 (see
`ROADMAP.md`, and `benchmarks/LXML_STUDY.md` for the inventory behind it).

This is not a façade over lxml: code keeps using lxml elements directly -
`el.get(name)`, `el.set(name, value)`, `el.getparent()`, `el.iterchildren()`.
What lives here is only what lxml does differently enough from bs4 that a
hand translation gets it wrong:

- **Names.** bs4 matches `"table:table-cell"` against the prefix as the file
  writes it, so a file binding the table namespace to `tab:` reads as having
  no sheet at all - and LibreOffice reads such a file without a blink. lxml
  names an element by its namespace URI, `{urn:...:table:1.0}table-cell`,
  whatever the prefix: `qn()` turns the code's own spelling into that form.
  The code's prefixes are a vocabulary of its own, fixed by the ODF
  specification (`NAMESPACES`); the document's prefixes do not enter into
  reading at all. They matter in two places only: a new element should come
  out with the document's prefix (lxml does that by itself, see `new()`),
  and a prefix *inside a value* - a formula's `of:=` - can only be resolved
  against the document's declarations (`prefix_of()`).
- **Text.** In lxml the text after an element is that element's `tail`, not
  a node of its parent's. Removing an element removes its tail with it,
  moving it moves the text along, copying it copies the text, inserting
  after it lands past the text: in mixed content (`<text:p>`, `<text:span>`,
  `<text:a>`) each of these loses or duplicates what the cell says.
  `remove()`, `clone()`, `insert_after()` and `unwrap()` keep the text where
  it was, as bs4 does. Writing mixed content is writing tails:
  `set_paragraph_text()` puts the text after each `<text:s/>` in its tail.
- **Search.** bs4's `find_all` searches descendants unless told
  `recursive=False`, and never returns the element it starts from; lxml's
  `el.iter(name)` includes `el` itself, `el.findall(name)` looks at children
  only. `children()` and `descendants()` say which they mean. A search in
  document order from an element, which bs4 offers as `previous_elements`
  and lxml not at all, is `preceding()`/`following()`.
- **Parsing.** bs4 drives lxml with `recover=True`, which repairs anything:
  an encrypted part parses as an empty document, which `save()` then writes
  over the data. `parse()` is strict and says so.
"""

import copy
import re
from collections.abc import Iterator
from typing import cast

from lxml import etree

__all__ = [
    "NAMESPACES",
    "children",
    "clone",
    "descendants",
    "following",
    "insert_after",
    "new",
    "parse",
    "preceding",
    "prefix_of",
    "prefixed",
    "qn",
    "remove",
    "serialize",
    "set_paragraph_text",
    "set_text",
    "text",
    "unwrap",
]

# The prefixes the ODF specification writes, which are the ones odsslicer's
# code spells names with - not necessarily the document's, see `qn`.
NAMESPACES = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "chart": "urn:oasis:names:tc:opendocument:xmlns:chart:1.0",
    "dr3d": "urn:oasis:names:tc:opendocument:xmlns:dr3d:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "of": "urn:oasis:names:tc:opendocument:xmlns:of:1.2",
    "calcext": "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}
_PREFIXES = {uri: prefix for prefix, uri in NAMESPACES.items()}
# qn() runs once per attribute read on the hot path - every cell of a sheet
# being loaded - so a name is split and looked up once, then cached
_QUALIFIED: dict[str, str] = {}


def qn(name: str) -> str:
    """`"table:table-cell"` -> `"{urn:oasis:...:table:1.0}table-cell"`: the
    name lxml knows an element or attribute by, whatever prefix the document
    binds its namespace to. An unknown prefix is a mistake in the code, and
    raises `KeyError`."""
    try:
        return _QUALIFIED[name]
    except KeyError:
        prefix, local = name.split(":", 1)
        _QUALIFIED[name] = qualified = f"{{{NAMESPACES[prefix]}}}{local}"
        return qualified


def prefixed(name: str) -> str:
    """The reverse of `qn`, for names handed to the caller - the keys of
    `CellStyle.cell_properties`, say: `{URI}local` -> `prefix:local`, with
    the specification's prefix rather than the document's, so that a key
    does not depend on who wrote the file. A namespace outside `NAMESPACES`
    keeps its `{URI}local` form, which is still unambiguous."""
    if not name.startswith("{"):
        return name
    uri, local = name[1:].split("}", 1)
    prefix = _PREFIXES.get(uri)
    return f"{prefix}:{local}" if prefix is not None else name


def prefix_of(element: etree._Element, uri: str) -> "str | None":
    """The prefix `element`'s document binds `uri` to where `element`
    stands, `None` if it binds none - what a prefix written *inside a
    value* has to be: `of:=SUM(...)` in a formula only means OpenFormula if
    the document says `of` does. No XML parser resolves those, since they
    are text to it."""
    for prefix, bound in element.nsmap.items():
        if bound == uri and prefix is not None:
            return cast(str, prefix)
    return None


def parse(markup: bytes) -> "etree._ElementTree":
    """`markup`, a part of the package, as an lxml tree - whitespace,
    attribute order and character references kept as written, which bs4
    needed `preserve_whitespace_tags` for.

    Strict: a part that is not well-formed XML raises
    `lxml.etree.XMLSyntaxError`. bs4 recovers instead, and on 492 real
    files the only parts recovery ever had to deal with were those of two
    encrypted files - which then opened as documents with no sheet, and
    saved as an empty `content.xml`. No network access and no entity
    expansion: a spreadsheet has no use for either."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)
    return etree.ElementTree(etree.fromstring(markup, parser))


def serialize(tree: "etree._ElementTree", original: bytes) -> bytes:
    """`tree` back to the bytes of a package part, under the XML declaration
    of `original`, the part as read: lxml cannot tell a declaration without
    `standalone` from one saying `standalone="no"`, writes it with single
    quotes, which no producer does, and Excel follows it with CRLF."""
    end = original.find(b"?>") + 2 if original.startswith(b"<?xml") else 0
    while original[end : end + 1] in (b"\r", b"\n"):
        end += 1
    return original[:end] + etree.tostring(tree, encoding="UTF-8", xml_declaration=False)


def children(element: etree._Element, name: str) -> "Iterator[etree._Element]":
    """The children of `element` named `name` - bs4's
    `find_all(name, recursive=False)`. A cell's own paragraphs are its
    children; the paragraphs of its note are further down, and a search of
    the descendants took them for the cell's value once."""
    return element.iterchildren(qn(name))


def descendants(element: etree._Element, *names: str) -> "Iterator[etree._Element]":
    """The elements below `element` named any of `names`, in document
    order, `element` itself excluded - bs4's `find_all(names)`. lxml's own
    `element.iter(name)` would include `element` if it bore the name."""
    return element.iterdescendants(*(qn(name) for name in names))


def preceding(element: etree._Element, *names: str) -> "Iterator[etree._Element]":
    """The elements named any of `names` before `element` in the document,
    nearest first, its ancestors included - bs4's `previous_elements`,
    filtered. Lazy, as bs4's is: a caller stopping at the first match pays
    for the part of the document it walked, which is what keeps looking for
    a neighbouring cell from scanning the whole document."""
    wanted = {qn(name) for name in names}
    node: etree._Element | None = element
    while node is not None:
        for sibling in node.itersiblings(preceding=True):
            # a sibling's subtree, last element first
            yield from reversed(list(sibling.iter(*wanted)))
        node = node.getparent()
        if node is not None and node.tag in wanted:
            yield node


def following(element: etree._Element, *names: str) -> "Iterator[etree._Element]":
    """The elements named any of `names` after `element` in the document,
    nearest first, its own descendants included - bs4's `next_elements`,
    filtered, and as lazy."""
    yield from element.iterdescendants(*(qn(name) for name in names))
    wanted = [qn(name) for name in names]
    node: etree._Element | None = element
    while node is not None:
        for sibling in node.itersiblings():
            yield from sibling.iter(*wanted)
        node = node.getparent()


def text(element: etree._Element) -> str:
    """Everything `element` says, its descendants' text included and
    comments left out - bs4's `get_text()`. Not `element.text`, which stops
    at the first child: `a<text:s/>b` would read `a`."""
    return "".join(cast("Iterator[str]", element.itertext()))


def set_text(element: etree._Element, value: str) -> None:
    """Make `value` all `element` says, its children gone - bs4's
    `tag.string = value`. The children go with their tails, which is right
    here: their text is part of what `value` replaces."""
    for child in list(element):
        element.remove(child)
    element.text = value


def new(name: str, attrs: "dict[str, str] | None" = None) -> etree._Element:
    """A new, detached element named `name`, its attributes named the same
    way (`{"table:name": "Sheet1"}`).

    bs4 could not do this without the namespace's URI at hand, hence the
    copies of existing elements `xmlutils._blank_template` makes. lxml needs
    nothing of the document: the element declares the specification's
    prefix for itself, and once inserted where the document already binds
    that namespace, lxml drops the declaration and takes the document's
    prefix - `tab:table-row` under a document that says `tab:`."""
    uri = NAMESPACES[name.split(":", 1)[0]]
    element = etree.Element(qn(name), nsmap={_PREFIXES[uri]: uri})
    for key, value in (attrs or {}).items():
        element.set(qn(key), value)
    return element


def _say_before(element: etree._Element, value: "str | None") -> None:
    """Append `value` to the text standing right before `element`: the tail
    of its previous sibling, or its parent's text if it is the first child."""
    if not value:
        return
    previous = element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + value
    else:
        parent = cast(etree._Element, element.getparent())
        parent.text = (parent.text or "") + value


def remove(element: etree._Element) -> etree._Element:
    """Take `element` out of its tree, leaving the text that followed it in
    place, and return it detached - bs4's `extract()`, and its
    `decompose()` once nothing holds the element any more.

    `parent.remove(element)` alone would take that text out too: removing
    the span of `Total<text:span>:</text:span> 42 €` leaves `Total`."""
    parent = element.getparent()
    if parent is None:
        return element
    _say_before(element, element.tail)
    element.tail = None
    parent.remove(element)
    return element


def clone(element: etree._Element) -> etree._Element:
    """A detached deep copy of `element`, without the text that follows it -
    `copy.deepcopy` copies the tail, so inserting the copy next to the
    original would say that text twice."""
    copied = copy.deepcopy(element)
    copied.tail = None
    return copied


def insert_after(reference: etree._Element, element: etree._Element) -> None:
    """Put `element` right after `reference`, before the text that followed
    it - bs4's `insert_after`. lxml's `reference.addnext(element)` puts it
    past that text, and carries along `element`'s own tail if it has one:
    `element` should come from `new`, `clone` or `remove`, which leave none."""
    element.tail, reference.tail = reference.tail, None
    reference.addnext(element)


def unwrap(element: etree._Element) -> None:
    """Replace `element` by what it holds - text, children and the text
    after it, in order - bs4's `unwrap()`, which lxml has no counterpart
    for: unlinking `Voir <text:a>ici</text:a> pour le détail` must leave
    `Voir ici pour le détail`, not `Voir ici`."""
    if element.getparent() is None:
        return
    _say_before(element, element.text)
    inner = list(element)
    for child in inner:
        element.addprevious(child)  # right before `element`, its tail along
    if inner:
        inner[-1].tail = (inner[-1].tail or "") + (element.tail or "")
        element.tail = None
    remove(element)


_S, _TAB, _LINE_BREAK, _C = (qn(name) for name in ("text:s", "text:tab", "text:line-break", "text:c"))
# a run of spaces, or a tab: what `set_paragraph_text` cannot leave as is
_WHITESPACE_RUN = re.compile(r"( +|\t)")


def _spaces(element: etree._Element) -> str:
    try:
        return " " * max(int(element.get(_C, "1")), 1)
    except ValueError:
        return " "


def paragraph_text(paragraph: etree._Element) -> str:
    """The text of a `<text:p>` as a spreadsheet application shows it: `text`
    with the whitespace ODF writes as elements put back - `<text:s text:c="N"/>`
    as N spaces, `<text:tab/>` as a tab, `<text:line-break/>` as a newline.
    `xmlutils._paragraph_text` on lxml: see it for why."""
    if len(paragraph) == 0:
        return paragraph.text or ""  # one text node, the common case
    parts: list[str] = []

    def walk(element: etree._Element) -> None:
        if element.text:
            parts.append(element.text)
        for child in element:
            if isinstance(child.tag, str):  # not a comment, whose text says nothing
                if child.tag == _S:
                    parts.append(_spaces(child))
                elif child.tag == _TAB:
                    parts.append("\t")
                elif child.tag == _LINE_BREAK:
                    parts.append("\n")
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(paragraph)
    return "".join(parts)


def set_paragraph_text(paragraph: etree._Element, value: str) -> None:
    """Make `value` the whole content of `paragraph`, a run of spaces written
    as one space and `<text:s text:c="N-1"/>` (all of it as `<text:s/>` at the
    start), a tab as `<text:tab/>` - as LibreOffice writes them, and as
    `xmlutils._set_paragraph_text` does on bs4. In lxml the text after each
    element is that element's tail, which is where it goes."""
    set_text(paragraph, "")
    paragraph.text = None
    if "  " not in value and "\t" not in value and not value.startswith(" "):
        paragraph.text = value
        return
    last: etree._Element | None = None
    pending = ""
    for i, piece in enumerate(_WHITESPACE_RUN.split(value)):
        if i % 2 == 0:
            pending += piece
            continue
        if piece == "\t":
            element = new("text:tab")
        else:
            at_start = i == 1 and not pending
            spaces = len(piece) if at_start else len(piece) - 1
            if not at_start:
                pending += " "
            if spaces == 0:
                continue
            element = new("text:s")
            if spaces > 1:
                element.set(_C, str(spaces))
        if pending:
            if last is None:
                paragraph.text = pending
            else:
                last.tail = pending
            pending = ""
        paragraph.append(element)
        last = element
    if pending:
        if last is None:
            paragraph.text = pending
        else:
            last.tail = pending
