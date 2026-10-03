"""Low-level helpers for building/cloning namespace-qualified ODF XML elements,
and for parsing a package part without losing anything it says."""

import copy
import io
import re
from typing import cast

from bs4 import BeautifulSoup, CData, NavigableString, Tag
from lxml import etree

# Standard OASIS namespace URIs, used as a last-resort fallback to build a
# namespace-qualified tag from scratch when the document has no existing tag
# of that name to copy (e.g. a brand new sheet with no cells at all yet).
_ODF_NAMESPACES = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xlink": "http://www.w3.org/1999/xlink",
}


def _root_local_name(markup: bytes) -> "str | None":
    """The local name of the document element of `markup` - `document-content`
    for a `content.xml` - read from its first start tag only, so that the
    cost does not grow with the document; `None` if it has none."""
    try:
        for _, element in etree.iterparse(io.BytesIO(markup), events=("start",)):
            return cast(str, etree.QName(element).localname)
    except etree.XMLSyntaxError:
        return None
    return None


def _parse_xml(markup: bytes) -> BeautifulSoup:
    """`markup`, a part of the package, parsed into a tree that serialises
    back to what it says - including every run of whitespace.

    BeautifulSoup squeezes any text node made of whitespace only to a single
    character while parsing (`endData()`), unless an element named in
    `preserve_whitespace_tags` is open: for a number format padded with
    spaces, `<number:text>   </number:text>`, that is a real loss. Naming
    the elements whose whitespace matters would mean deciding what each one
    means, and a forgotten one would lose its spaces in silence; naming the
    document element instead keeps every text node under it, which is all of
    them - the tree then holds what the file holds, whether odsslicer
    understands it or not. The check is per opened element, so this costs a
    few per cent of the parse and nothing in memory."""
    root = _root_local_name(markup)
    return BeautifulSoup(markup, "xml", preserve_whitespace_tags={root} if root is not None else set())


def _new_qualified_tag(tag_name: str) -> Tag:
    """Build a detached `<tag_name/>` from scratch, with its own `xmlns:`
    declaration so the "table:"/"text:" prefix resolves correctly - safe to
    insert anywhere in an ODF document even though the declaration is then
    redundant with the one at the document root (harmless, plain valid XML).
    """
    prefix = tag_name.split(":", 1)[0]
    uri = _ODF_NAMESPACES[prefix]
    fragment = BeautifulSoup(f'<{tag_name} xmlns:{prefix}="{uri}"/>', "xml")
    return cast(Tag, fragment.find(tag_name))


def _blank_template(root: Tag, tag_name: str) -> Tag:
    """A detached, blank copy of an existing `tag_name` tag reachable from
    `root`, or a freshly built one (see `_new_qualified_tag`) if the document
    has none to copy from.

    Building a new tag from scratch (e.g. `BeautifulSoup("<table:table-row/>")`)
    without its own `xmlns:` declaration loses the "table:"/"text:" namespace
    prefix, since there is nothing in that isolated fragment for lxml/bs4 to
    resolve it against - copying an existing tag from the live document (when
    there is one) sidesteps having to hardcode the namespace URI at all.
    """
    template = (
        root.find(tag_name)
        or getattr(root, "find_previous", lambda *a: None)(tag_name)
        or getattr(root, "find_next", lambda *a: None)(tag_name)
    )
    if template is None:
        return _new_qualified_tag(tag_name)
    new_tag = cast(Tag, copy.deepcopy(template))
    new_tag.attrs.clear()
    for child in list(new_tag.children):
        child.extract()
    return new_tag


def _ensure_style_child(style_tag: Tag, tag_name: str) -> Tag:
    """The `tag_name` properties child of `style_tag` (e.g.
    `<style:table-cell-properties>` under a `<style:style>`), creating a
    blank one (see `_blank_template`) if it isn't there yet."""
    child = style_tag.find(tag_name)
    if child is None:
        new_child = _blank_template(style_tag, tag_name)
        style_tag.append(new_child)
        return new_child
    return cast(Tag, child)


def _is_forked_style_name(name: "str | None", prefix: str) -> bool:
    """True if `name` looks like one odsslicer itself generated for a
    single owner (a specific cell/row/column/sheet) via `prefix` - safe to
    mutate in place rather than fork again. Real documents don't use these
    reserved prefixes in practice."""
    if not name:
        return False
    return re.match(rf"^{re.escape(prefix)}\d+$", name) is not None


# Builds the tags `_set_paragraph_text` inserts: `new_tag` only borrows the XML
# builder from it (an empty element then serialises as `<text:s/>`), the tags
# are never attached to it - and a factory spares a walk up to the document.
_TAG_FACTORY = BeautifulSoup("", "xml")

# A run of spaces, or a tab: what `_set_paragraph_text` cannot leave as is.
_WHITESPACE_RUN = re.compile(r"( +|\t)")


def _space_count(tag: Tag) -> int:
    try:
        return max(int(cast(str, tag.attrs.get("text:c", "1"))), 1)
    except ValueError:
        return 1


def _paragraph_text(paragraph: Tag) -> str:
    """The text of a `<text:p>` as a spreadsheet application shows it.

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


def _set_paragraph_text(paragraph: Tag, text: str) -> None:
    """Make `text` the whole content of `paragraph`, a `<text:p>` or an element
    inside one, encoding its whitespace the way LibreOffice does: a run of
    spaces as one literal space followed by `<text:s text:c="N-1"/>`, all of it
    as `<text:s/>` at the start of the paragraph, a tab as `<text:tab/>`.

    Literal whitespace would read back the same in LibreOffice, but ODF lets
    any consumer collapse it - an encoded run survives every one of them. A
    newline is the caller's business: in a cell it starts a new paragraph."""
    if "  " not in text and "\t" not in text and not text.startswith(" "):
        paragraph.string = text
        return
    paragraph.clear()
    pending = ""
    for i, piece in enumerate(_WHITESPACE_RUN.split(text)):
        if i % 2 == 0:
            pending += piece
            continue
        if piece == "\t":
            element = _TAG_FACTORY.new_tag("tab", namespace=_ODF_NAMESPACES["text"], nsprefix="text")
        else:
            at_start = i == 1 and not pending
            spaces = len(piece) if at_start else len(piece) - 1
            if not at_start:
                pending += " "
            if spaces == 0:
                continue
            element = _TAG_FACTORY.new_tag("s", namespace=_ODF_NAMESPACES["text"], nsprefix="text")
            if spaces > 1:
                element.attrs["text:c"] = str(spaces)
        if pending:
            paragraph.append(NavigableString(pending))
            pending = ""
        paragraph.append(element)
    if pending:
        paragraph.append(NavigableString(pending))
