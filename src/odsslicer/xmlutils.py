"""Low-level XML helpers: parsing a package part without losing anything it
says, reading the manifest, and the style helpers built on `xmlbs` - the
operations whose bs4 and lxml forms differ, which live there."""

import io
import re
from typing import cast

from bs4 import BeautifulSoup, Tag
from lxml import etree

from .xmlbs import new

# Standard OASIS namespace URIs - a subset of `xmltree.NAMESPACES`, the
# declarations odsslicer writes itself (a link's `xmlns:xlink`) and the tests
# build fragments with.
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


def _encrypted_parts(manifest: bytes) -> set[str]:
    """The paths `manifest`, a `META-INF/manifest.xml`, declares encrypted:
    those whose `file-entry` carries an `encryption-data` element.

    A password-protected package stores its parts encrypted, and nothing in
    the zip says so but the manifest: `_parse_xml` recovers from the bytes
    as from any broken markup and gives an empty tree, so the document reads
    as one with no sheets, and a save writes that emptiness over the data.
    Recognising ciphertext after the fact would be a guess; the manifest
    states it. Elements and attributes are matched by local name: files from
    OpenOffice.org 1.x and from LibreOffice saving in that format declare the
    manifest in `http://openoffice.org/2001/manifest` rather than the ODF
    namespace (LibreOffice's own `passwordWrongSHA.ods` is one), with a
    DOCTYPE that is never loaded. A manifest that does not parse declares
    nothing - it is no reason to refuse a package that is otherwise fine."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(manifest, parser)
    except etree.XMLSyntaxError:
        return set()
    encrypted = set()
    for entry in root.iter(etree.Element):
        if etree.QName(entry).localname != "file-entry":
            continue
        children = entry.iterchildren(etree.Element)
        if not any(etree.QName(child).localname == "encryption-data" for child in children):
            continue
        for name, value in entry.attrib.items():
            if etree.QName(name).localname == "full-path":
                encrypted.add(str(value))
    return encrypted


def _blank_template(root: Tag, tag_name: str) -> Tag:
    """A new `tag_name` element for the document `root` stands in: `new`,
    which used to be this, a blank copy of an existing element of the
    document. Kept for `properties.py` alone, which the 1.0 branch ports to
    lxml whole; everything else calls `new` itself."""
    return new(tag_name, near=root)


def _ensure_style_child(style_tag: Tag, tag_name: str) -> Tag:
    """The `tag_name` properties child of `style_tag` (e.g.
    `<style:table-cell-properties>` under a `<style:style>`), creating a
    blank one (see `xmlbs.new`) if it isn't there yet."""
    child = style_tag.find(tag_name)
    if child is None:
        new_child = new(tag_name, near=style_tag)
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
