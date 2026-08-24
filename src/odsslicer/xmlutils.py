# -*- coding: utf-8 -*-
"""Low-level helpers for building/cloning namespace-qualified ODF XML elements."""

import copy
import re
from typing import cast

from bs4 import BeautifulSoup, Tag


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
