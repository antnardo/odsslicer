"""Low-level XML helpers built on `xmltree`: reading the manifest, and the
style helpers the modules share."""

import re

from lxml import etree

from .xmltree import child, new


def _encrypted_parts(manifest: bytes) -> set[str]:
    """The paths `manifest`, a `META-INF/manifest.xml`, declares encrypted:
    those whose `file-entry` carries an `encryption-data` element.

    A password-protected package stores its parts encrypted, and nothing in
    the zip says so but the manifest. A parser that recovers from broken
    markup, as BeautifulSoup's did before 1.0, gave an empty tree for the
    ciphertext, so the document read as one with no sheets, and a save
    wrote that emptiness over the data; the strict parser now refuses it,
    but not in so many words. Recognising ciphertext after the fact would be
    a guess; the manifest states it. Elements and attributes are matched by
    local name: files from OpenOffice.org 1.x and from LibreOffice saving in
    that format declare the manifest in `http://openoffice.org/2001/manifest`
    rather than the ODF namespace (LibreOffice's own `passwordWrongSHA.ods`
    is one), with a DOCTYPE that is never loaded. A manifest that does not
    parse declares nothing - it is no reason to refuse a package that is
    otherwise fine."""
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


def _ensure_style_child(style_tag: etree._Element, tag_name: str) -> etree._Element:
    """The `tag_name` properties child of `style_tag` (e.g.
    `<style:table-cell-properties>` under a `<style:style>`), creating a
    blank one if it isn't there yet."""
    found = child(style_tag, tag_name)
    if found is None:
        found = new(tag_name)
        style_tag.append(found)
    return found


def _is_forked_style_name(name: "str | None", prefix: str) -> bool:
    """True if `name` looks like one odsslicer itself generated for a
    single owner (a specific cell/row/column/sheet) via `prefix` - safe to
    mutate in place rather than fork again. Real documents don't use these
    reserved prefixes in practice."""
    if not name:
        return False
    return re.match(rf"^{re.escape(prefix)}\d+$", name) is not None
