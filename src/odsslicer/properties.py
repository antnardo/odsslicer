# -*- coding: utf-8 -*-
"""DocumentProperties: structured, writable access to meta.xml."""

import datetime as dt
from typing import TYPE_CHECKING

from .xmlutils import _blank_template

if TYPE_CHECKING:
    from .reader import ODSReader


def _parse_user_defined_value(tag):
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


def _write_user_defined_value(tag, value):
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

    def __init__(self, reader: "ODSReader"):
        self._reader = reader

    def _office_meta(self):
        root = self._reader.meta_data.find("office:document-meta")
        meta = root.find("office:meta")
        if meta is None:
            meta = _blank_template(self._reader.meta_data, "office:meta")
            root.append(meta)
        return meta

    def _get_text(self, tag_name):
        tag = self._office_meta().find(tag_name)
        return tag.get_text() if tag is not None else None

    def _set_text(self, tag_name, value):
        meta = self._office_meta()
        tag = meta.find(tag_name)
        if value is None:
            if tag is not None:
                tag.decompose()
            return
        if tag is None:
            tag = _blank_template(self._reader.meta_data, tag_name)
            meta.append(tag)
        tag.string = value

    @property
    def title(self):
        return self._get_text("dc:title")

    @title.setter
    def title(self, value):
        self._set_text("dc:title", value)

    @property
    def subject(self):
        return self._get_text("dc:subject")

    @subject.setter
    def subject(self, value):
        self._set_text("dc:subject", value)

    @property
    def description(self):
        return self._get_text("dc:description")

    @description.setter
    def description(self, value):
        self._set_text("dc:description", value)

    @property
    def creator(self):
        """Who last saved the document (`dc:creator`)."""
        return self._get_text("dc:creator")

    @creator.setter
    def creator(self, value):
        self._set_text("dc:creator", value)

    @property
    def initial_creator(self):
        """Who originally created the document (`meta:initial-creator`)."""
        return self._get_text("meta:initial-creator")

    @initial_creator.setter
    def initial_creator(self, value):
        self._set_text("meta:initial-creator", value)

    @property
    def keywords(self):
        """`meta:keyword` values (0+), in document order."""
        return [tag.get_text() for tag in self._office_meta().find_all("meta:keyword")]

    @keywords.setter
    def keywords(self, values):
        meta = self._office_meta()
        for tag in meta.find_all("meta:keyword"):
            tag.decompose()
        for value in values or ():
            tag = _blank_template(self._reader.meta_data, "meta:keyword")
            tag.string = value
            meta.append(tag)

    @property
    def generator(self):
        """The application that last saved this file (e.g.
        `"LibreOffice/25.8..."`) - read-only, `odsslicer` doesn't claim
        to be a spreadsheet application."""
        return self._get_text("meta:generator")

    @property
    def custom(self):
        """A dict snapshot `{name: value}` of every `meta:user-defined`
        property - use `props["name"]`/`props["name"] = value` to read or
        write a single one instead."""
        return {
            tag.get("meta:name"): _parse_user_defined_value(tag)
            for tag in self._office_meta().find_all("meta:user-defined")
        }

    def _find_custom(self, name):
        return self._office_meta().find("meta:user-defined", attrs={"meta:name": name})

    def __getitem__(self, name):
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        return _parse_user_defined_value(tag)

    def __setitem__(self, name, value):
        tag = self._find_custom(name)
        if tag is None:
            tag = _blank_template(self._reader.meta_data, "meta:user-defined")
            tag.attrs["meta:name"] = name
            self._office_meta().append(tag)
        _write_user_defined_value(tag, value)

    def __delitem__(self, name):
        tag = self._find_custom(name)
        if tag is None:
            raise KeyError(name)
        tag.decompose()

    def __contains__(self, name):
        return self._find_custom(name) is not None

    def __repr__(self):
        return f"DocumentProperties(title={self.title!r}, creator={self.creator!r})"
