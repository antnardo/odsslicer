"""The operations of `xmltree`, under its names and with its meaning, done
by BeautifulSoup - the bs4 side of the move to lxml (see `ROADMAP.md`,
"Plan d'engagement", step 2).

`xmltree` holds what lxml does differently enough from bs4 that a hand
translation gets it wrong: the text after an element is its `tail`, a new
element needs no copy of an existing one, a name is a namespace URI. The
code that is to move to lxml calls these functions instead of bs4's own
methods, so that every one of those differences is settled here, once,
against the same tests as `xmltree` (`tests/test_xmlbs.py` runs each case
through both) - and the switch, when it comes, changes the bodies of these
functions rather than the sites that call them: each module's
`from .xmlbs import ...` becomes `from .xmltree import ...`, and this file
goes.

A module of its own rather than more of `xmlutils`, for that reason: what
is here is exactly what `xmltree` replaces, name for name, and nothing
else. `xmlutils` keeps what does not have that one-to-one counterpart -
parsing a part, reading the manifest, the style helpers built on top of
these functions.

Most bodies are a single bs4 call: bs4 keeps the text after an element as
a node of its parent, so removing, copying or moving an element has no
tail to look after - the pitfalls are lxml's, and `xmltree` handles them.
Two operations needed more. `new`: bs4 cannot know whether the document a
new element is for binds its namespace, which `near` stands in for (see
there). And removing comes in two kinds, `remove` for an element put
elsewhere and `discard` for one dropped, because only bs4 needs telling
them apart (see `discard`).

Names are compared by local name where the code compared local names
(`local_name(child) == "table-row"`), as it did on bs4: moving those to a
namespace check is a change of behaviour, however unlikely to show, and
not this step's to make.
"""

import copy
import re
from typing import cast

from bs4 import BeautifulSoup, CData, NavigableString, Tag

from .xmltree import NAMESPACES

__all__ = [
    "children",
    "clone",
    "discard",
    "insert_after",
    "insert_before",
    "local_name",
    "name_of",
    "namespace_of",
    "new",
    "paragraph_text",
    "remove",
    "rename",
    "set_paragraph_text",
    "set_text",
    "text",
    "unwrap",
    "wrap_contents",
]

# Builds the elements `new` makes: they only borrow its XML builder - an
# empty element then serialises as `<text:s/>`, not `<text:s></text:s>` -
# and are never attached to it, which spares a walk up to a document.
_FACTORY = BeautifulSoup("", "xml")


def _named_like(root: Tag, name: str) -> "Tag | None":
    """An element named `name` in the document `root` stands in - below
    `root`, before it or after it - the copy `_blank_template` made a new
    element from, before `new` existed."""
    return cast(
        "Tag | None",
        root.find(name)
        or getattr(root, "find_previous", lambda *a: None)(name)
        or getattr(root, "find_next", lambda *a: None)(name),
    )


def new(name: str, attrs: "dict[str, str] | None" = None, *, near: "Tag | None" = None) -> Tag:
    """A new, detached element named `name` (`"table:table-row"`), its
    attributes named the same way - `xmltree.new`.

    The element is written with the specification's prefix. lxml decides
    on insertion whether it has to declare it, and declares it only where
    the document does not bind the namespace; bs4 never declares anything
    by itself. `near`, an element of the document the new one is for,
    stands in for that decision, as the code has always taken it: when the
    document holds no element named `name` yet, the new one declares its
    namespace for itself (`xmlns:table="..."`) - redundant where the root
    binds it, as LibreOffice's always does, and what keeps the file valid
    where it does not. Elsewhere, and without `near`, nothing is declared.
    That search was the cost of copying an existing element as a template,
    which is how new elements were made before (`_blank_template`): it is
    kept, so that a file is written exactly as it was. `xmltree.new`
    accepts `near` and ignores it."""
    prefix, _, local = name.partition(":")
    uri = NAMESPACES[prefix]
    declared = {f"xmlns:{prefix}": uri} if near is not None and _named_like(near, name) is None else {}
    return _FACTORY.new_tag(local, namespace=uri, nsprefix=prefix, attrs={**declared, **(attrs or {})})


def text(element: Tag) -> str:
    """Everything `element` says, its descendants' text included -
    `get_text()`, `xmltree.text`."""
    return element.get_text()


def set_text(element: Tag, value: str) -> None:
    """Make `value` all `element` says, its children gone - `tag.string =
    value`, `xmltree.set_text`."""
    element.string = value


def remove(element: Tag) -> Tag:
    """Take `element` out of its tree and return it, to be put elsewhere -
    `extract()`, `xmltree.remove`. The text that followed it stays where it
    was: in bs4 it is a node of the parent."""
    return cast(Tag, element.extract())


def discard(element: Tag) -> None:
    """Take `element` out of its tree for good - `decompose()`,
    `xmltree.discard`.

    Not `remove`, which would write the same tree: bs4's elements point at
    one another, so a subtree merely taken out waits for the garbage
    collector, where `decompose()` empties it at once - and a `Cell` still
    holding the element, one of a deleted sheet say, sees it emptied, as it
    always did. lxml frees an element nothing holds: there, `discard` is
    `remove`."""
    element.decompose()


def clone(element: Tag) -> Tag:
    """A detached deep copy of `element` - `copy.deepcopy`, `xmltree.clone`.
    In bs4 the copy carries no text from around the original."""
    return copy.deepcopy(element)


def insert_after(reference: Tag, element: Tag) -> None:
    """Put `element` right after `reference`, before the text that followed
    it - `xmltree.insert_after`.

    bs4 finds `reference`'s position by walking its siblings from the
    first, on every call: a loop of these down a long parent is quadratic.
    `sheet.py` inserts rows and cells by index instead, found once, where
    that matters (see `_position_after`); lxml's `addnext` costs nothing,
    and those sites become `insert_after` with the switch."""
    reference.insert_after(element)


def insert_before(reference: Tag, element: Tag) -> None:
    """Put `element` right before `reference`, after the text that stood
    before it - `xmltree.insert_before`. Same cost as `insert_after`."""
    reference.insert_before(element)


def unwrap(element: Tag) -> None:
    """Replace `element` by what it holds - text, children and the text
    after it, in order - `xmltree.unwrap`."""
    element.unwrap()


def wrap_contents(element: Tag, wrapper: Tag) -> None:
    """Move everything `element` holds - its text, its children - into
    `wrapper`, in order, and make `wrapper` its only child:
    `xmltree.wrap_contents`. What `cell.hyperlink = url` does to a
    paragraph. In lxml the text before the first child is no child but
    `element.text`, which moving the children alone would leave out of
    the link."""
    for child in list(element.children):
        wrapper.append(child.extract())
    element.append(wrapper)


def children(element: Tag, name: str) -> list[Tag]:
    """The children of `element` named `name` ("text:p"), in order -
    `find_all(name, recursive=False)`, `xmltree.children`, without what
    `find_all` costs.

    bs4 builds a filter object for every `find`/`find_all` call before
    walking anything, and on the paths that run once per cell - reading a
    cell's paragraphs, looking for its note - that construction outweighed
    the walk: profiling a test suite that reads wide sheets put the filter
    at the top, with two searches per cell. A cell has a handful of
    children, so a plain loop is all it takes. The name matches as bs4
    matches it: prefix and local name, or the whole name on an element whose
    prefix the parser could not resolve.

    A list, and in both layers: those paths test it for emptiness, and a
    generator cost them a tenth of `Sheet.load`."""
    prefix, _, local = name.partition(":")
    return [
        child
        for child in element.contents
        if isinstance(child, Tag) and (child.name == name or (child.name == local and child.prefix == prefix))
    ]


def local_name(element: Tag) -> str:
    """`element`'s name without its prefix - `"table-row"` -
    `xmltree.local_name`."""
    return cast(str, element.name)


def name_of(element: Tag) -> str:
    """`element`'s name, prefixed - `"table:table-row"` -
    `xmltree.name_of`. bs4 gives the prefix the document writes, lxml the
    specification's: the same for every file but one that rebinds a
    prefix, which bs4 does not read anyway."""
    return f"{element.prefix}:{element.name}" if element.prefix else cast(str, element.name)


def namespace_of(element: Tag) -> "str | None":
    """The URI of `element`'s namespace - `xmltree.namespace_of`."""
    return cast("str | None", element.namespace)


def rename(element: Tag, name: str) -> None:
    """Give `element` the name `name` (`"table:covered-table-cell"`), its
    attributes and content kept - `xmltree.rename`."""
    prefix, _, local = name.partition(":")
    element.name, element.prefix, element.namespace = local, prefix, NAMESPACES[prefix]


# A run of spaces, or a tab: what `set_paragraph_text` cannot leave as is.
_WHITESPACE_RUN = re.compile(r"( +|\t)")


def _space_count(tag: Tag) -> int:
    try:
        return max(int(cast(str, tag.attrs.get("text:c", "1"))), 1)
    except ValueError:
        return 1


def paragraph_text(paragraph: Tag) -> str:
    """The text of a `<text:p>` as a spreadsheet application shows it -
    `xmltree.paragraph_text`.

    ODF lets a consumer collapse the whitespace a paragraph holds literally,
    so a producer writes what has to survive as elements: `<text:s text:c="N"/>`
    for N spaces, `<text:tab/>`, `<text:line-break/>`. LibreOffice does it for
    every run of two spaces or more, every leading space, every tab and every
    Shift+Enter. BeautifulSoup's `get_text()` concatenates the text nodes only,
    which dropped all of them (issue #27): "a<text:s text:c="2"/>b" read "ab".

    Literal whitespace is kept as written rather than collapsed: LibreOffice
    shows it that way, which is what the reader of a cell expects to get."""
    only = paragraph.string
    if only is not None and type(only) is NavigableString:
        # one text node and nothing else, the common case, decided without a walk
        return str(only)
    parts: list[str] = []
    for node in paragraph.descendants:
        if isinstance(node, Tag):
            if node.prefix != "text":
                continue
            if node.name == "s":
                parts.append(" " * _space_count(node))
            elif node.name == "tab":
                parts.append("\t")
            elif node.name == "line-break":
                parts.append("\n")
        elif type(node) is NavigableString or isinstance(node, CData):
            parts.append(str(node))
    return "".join(parts)


def set_paragraph_text(paragraph: Tag, value: str) -> None:
    """Make `value` the whole content of `paragraph`, a `<text:p>` or an
    element inside one, encoding its whitespace the way LibreOffice does: a
    run of spaces as one literal space followed by `<text:s text:c="N-1"/>`,
    all of it as `<text:s/>` at the start of the paragraph, a tab as
    `<text:tab/>` - `xmltree.set_paragraph_text`.

    Literal whitespace would read back the same in LibreOffice, but ODF lets
    any consumer collapse it - an encoded run survives every one of them. A
    newline is the caller's business: in a cell it starts a new paragraph."""
    if "  " not in value and "\t" not in value and not value.startswith(" "):
        paragraph.string = value
        return
    paragraph.clear()
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
                element.attrs["text:c"] = str(spaces)
        if pending:
            paragraph.append(NavigableString(pending))
            pending = ""
        paragraph.append(element)
    if pending:
        paragraph.append(NavigableString(pending))
