# -*- coding: utf-8 -*-
"""DocumentProperties: structured, writable access to meta.xml."""

import datetime as dt
from typing import TYPE_CHECKING, Any, cast

from bs4 import Tag

from .xmlutils import _blank_template

if TYPE_CHECKING:
    from .reader import ODSReader


def _parse_user_defined_value(tag: Tag) -> "str | float | bool | dt.date":
    """The typed Python value behind one `<meta:user-defined>` element,
    per its `meta:value-type` (`"string"` if absent, per the ODF spec)."""
    value_type = tag.get("meta:value-type", "string")
    text = tag.get_text()
    if value_type == "float":
        return float(text)
    if value_type == "boolean":
        return text == "true"
    if value_type == "date":
        return dt.date.fromisoformat(text[:10])
    return text


def _write_user_defined_value(tag: Tag, value: "str | float | bool | dt.date") -> None:
    """Set `<meta:user-defined>`'s text content and `meta:value-type` from
    a Python value - the reverse of `_parse_user_defined_value`."""
    if isinstance(value, bool):
        tag.attrs["meta:value-type"] = "boolean"
        tag.string = "true" if value else "false"
    elif isinstance(value, (int, float)):
        tag.attrs["meta:value-type"] = "float"
        tag.string = str(value)
    elif isinstance(value, dt.date):
        tag.attrs["meta:value-type"] = "date"
        tag.string = value.isoformat()
    elif isinstance(value, str):
        tag.attrs.pop("meta:value-type", None)  # "string" is the implicit default
        tag.string = value
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

    def _office_meta(self) -> Tag:
        root = cast(Tag, self._reader.meta_data.find("office:document-meta"))
        meta = cast("Tag | None", root.find("office:meta"))
        if meta is None:
            meta = _blank_template(self._reader.meta_data, "office:meta")
            root.append(meta)
        return meta

    def _get_text(self, tag_name: str) -> "str | None":
        tag = self._office_meta().find(tag_name)
        return tag.get_text() if tag is not None else None

    def _set_text(self, tag_name: str, value: "str | None") -> None:
        meta = self._office_meta()
        tag = cast("Tag | None", meta.find(tag_name))
        if value is None:
            if tag is not None:
                tag.decompose()
            return
        if tag is None:
            tag = _blank_template(self._reader.meta_data, tag_name)
            meta.append(tag)
        tag.string = value

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
        return [tag.get_text() for tag in self._office_meta().find_all("meta:keyword")]

    @keywords.setter
    def keywords(self, values: "list[str] | tuple[str, ...] | None") -> None:
        meta = self._office_meta()
        for tag in meta.find_all("meta:keyword"):
            tag.decompose()
        for value in values or ():
            tag = _blank_template(self._reader.meta_data, "meta:keyword")
            tag.string = value
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
            tag.get("meta:name"): _parse_user_defined_value(tag)
            for tag in self._office_meta().find_all("meta:user-defined")
        }

    def _find_custom(self, name: str) -> "Tag | None":
        return cast(
            "Tag | None", self._office_meta().find("meta:user-defined", attrs={"meta:name": name})
        )

    def __getitem__(self, name: str) -> "str | float | bool | dt.date":
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        return _parse_user_defined_value(tag)

    def __setitem__(self, name: str, value: "str | float | bool | dt.date") -> None:
        tag = self._find_custom(name)
        if tag is None:
            tag = _blank_template(self._reader.meta_data, "meta:user-defined")
            tag.attrs["meta:name"] = name
            self._office_meta().append(tag)
        _write_user_defined_value(tag, value)

    def __delitem__(self, name: str) -> None:
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        tag.decompose()

    def __contains__(self, name: str) -> bool:
        return self._find_custom(name) is not None

    def __repr__(self) -> str:
        return f"DocumentProperties(title={self.title!r}, creator={self.creator!r})"
