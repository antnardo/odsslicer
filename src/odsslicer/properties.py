"""DocumentProperties: structured, writable access to meta.xml.

The first module on lxml alone (see `xmltree`), as the feasibility study's
prototype: `meta.xml` is read by nothing else, so its tree changes library
with this module and with the two lines of `ODSReader` that parse and
serialise it - a module moves with the part it reads, not on its own.

Elements are looked for among the children of `<office:meta>`, where ODF
puts them, rather than anywhere below it as bs4's `find` did."""

import datetime as dt
from typing import TYPE_CHECKING

from lxml import etree

from .xmltree import children, new, qn, remove, set_text, text

if TYPE_CHECKING:
    from .reader import ODSReader


_META_NAME = qn("meta:name")
_META_VALUE_TYPE = qn("meta:value-type")


def _parse_user_defined_value(tag: etree._Element) -> "str | float | bool | dt.date":
    """The typed Python value behind one `<meta:user-defined>` element,
    per its `meta:value-type` (`"string"` if absent, per the ODF spec)."""
    value_type = tag.get(_META_VALUE_TYPE, "string")
    content = text(tag)
    if value_type == "float":
        return float(content)
    if value_type == "boolean":
        return content == "true"
    if value_type == "date":
        return dt.date.fromisoformat(content[:10])
    return content


def _write_user_defined_value(tag: etree._Element, value: "str | float | bool | dt.date") -> None:
    """Set `<meta:user-defined>`'s text content and `meta:value-type` from
    a Python value - the reverse of `_parse_user_defined_value`."""
    if isinstance(value, bool):
        tag.set(_META_VALUE_TYPE, "boolean")
        set_text(tag, "true" if value else "false")
    elif isinstance(value, (int, float)):
        tag.set(_META_VALUE_TYPE, "float")
        set_text(tag, str(value))
    elif isinstance(value, dt.date):
        tag.set(_META_VALUE_TYPE, "date")
        set_text(tag, value.isoformat())
    elif isinstance(value, str):
        if _META_VALUE_TYPE in tag.attrib:  # "string" is the implicit default
            del tag.attrib[_META_VALUE_TYPE]
        set_text(tag, value)
    else:
        raise TypeError(f"unsupported custom property value type: {type(value)!r}")


class DocumentProperties:
    """Structured, writable access to `meta.xml` - the document properties
    behind LibreOffice's "File > Properties" dialog: `.title`, `.subject`,
    `.description`, `.creator` (who last saved it), `.initial_creator`
    (who created it), `.keywords` (a list), plus arbitrary custom
    properties (`meta:user-defined`) via dict-style access
    (`props["Client"]`). Get it via `ODSReader.properties`, not directly.

    A custom property's Python type round-trips through ODF's own
    `meta:value-type` (`str`/`float`/`bool`/`datetime.date`) - assigning
    any other type raises `TypeError`."""

    def __init__(self, reader: "ODSReader") -> None:
        self._reader = reader

    def _office_meta(self) -> etree._Element:
        root = self._reader.meta_data.getroot()
        meta = root.find(qn("office:meta"))
        if meta is None:
            meta = new("office:meta")
            root.append(meta)
        return meta

    def _get_text(self, tag_name: str) -> "str | None":
        tag = self._office_meta().find(qn(tag_name))
        return text(tag) if tag is not None else None

    def _set_text(self, tag_name: str, value: "str | None") -> None:
        meta = self._office_meta()
        tag = meta.find(qn(tag_name))
        if value is None:
            if tag is not None:
                remove(tag)
            return
        if tag is None:
            tag = new(tag_name)
            meta.append(tag)
        set_text(tag, value)

    @property
    def title(self) -> "str | None":
        return self._get_text("dc:title")

    @title.setter
    def title(self, value: "str | None") -> None:
        self._set_text("dc:title", value)

    @property
    def subject(self) -> "str | None":
        return self._get_text("dc:subject")

    @subject.setter
    def subject(self, value: "str | None") -> None:
        self._set_text("dc:subject", value)

    @property
    def description(self) -> "str | None":
        return self._get_text("dc:description")

    @description.setter
    def description(self, value: "str | None") -> None:
        self._set_text("dc:description", value)

    @property
    def creator(self) -> "str | None":
        """Who last saved the document (`dc:creator`)."""
        return self._get_text("dc:creator")

    @creator.setter
    def creator(self, value: "str | None") -> None:
        self._set_text("dc:creator", value)

    @property
    def initial_creator(self) -> "str | None":
        """Who originally created the document (`meta:initial-creator`)."""
        return self._get_text("meta:initial-creator")

    @initial_creator.setter
    def initial_creator(self, value: "str | None") -> None:
        self._set_text("meta:initial-creator", value)

    @property
    def keywords(self) -> list[str]:
        """`meta:keyword` values (0+), in document order."""
        return [text(tag) for tag in children(self._office_meta(), "meta:keyword")]

    @keywords.setter
    def keywords(self, values: "list[str] | tuple[str, ...] | None") -> None:
        meta = self._office_meta()
        for tag in list(children(meta, "meta:keyword")):
            remove(tag)
        for value in values or ():
            tag = new("meta:keyword")
            set_text(tag, value)
            meta.append(tag)

    @property
    def generator(self) -> "str | None":
        """The application that last saved this file (e.g.
        `"LibreOffice/25.8..."`) - read-only, `odsslicer` doesn't claim
        to be a spreadsheet application."""
        return self._get_text("meta:generator")

    @property
    def custom(self) -> "dict[str | None, str | float | bool | dt.date]":
        """A dict snapshot `{name: value}` of every `meta:user-defined`
        property - use `props["name"]`/`props["name"] = value` to read or
        write a single one instead."""
        return {
            tag.get(_META_NAME): _parse_user_defined_value(tag)
            for tag in children(self._office_meta(), "meta:user-defined")
        }

    def _find_custom(self, name: str) -> "etree._Element | None":
        # not an ElementPath predicate: a name holding a quote would break it
        tags = children(self._office_meta(), "meta:user-defined")
        return next((tag for tag in tags if tag.get(_META_NAME) == name), None)

    def __getitem__(self, name: str) -> "str | float | bool | dt.date":
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        return _parse_user_defined_value(tag)

    def __setitem__(self, name: str, value: "str | float | bool | dt.date") -> None:
        tag = self._find_custom(name)
        if tag is None:
            tag = new("meta:user-defined", {"meta:name": name})
            self._office_meta().append(tag)
        _write_user_defined_value(tag, value)

    def __delitem__(self, name: str) -> None:
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        remove(tag)

    def __contains__(self, name: str) -> bool:
        return self._find_custom(name) is not None

    def __repr__(self) -> str:
        return f"DocumentProperties(title={self.title!r}, creator={self.creator!r})"
