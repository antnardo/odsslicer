"""Comment: a cell's note, `<office:annotation>` - its text, author, date and
visibility, read and written in place.

Split from `cell.py` at 1.0's audit: a note is an element of its own inside
the cell's, with its own layout - author and date before the text, the text
as paragraphs or a list - which `Cell.comment` only finds, makes or removes.
"""

import datetime as dt

from lxml import etree

from odsslicer.xmltree import (
    child,
    descendants,
    insert_before,
    new,
    paragraph_text,
    qn,
    remove,
    set_paragraph_text,
    set_text,
    text,
)

__all__ = ["Comment"]


# what a note holds as its text, after its author and date
_NOTE_TEXT = frozenset({qn("text:p"), qn("text:list")})


class Comment:
    """A cell's note (`<office:annotation>`) - `.text` (the note body,
    `\\n`-joined across ODF's own multiple `<text:p>` paragraphs),
    `.author` (`dc:creator`), `.date` (`dc:date`, a `datetime.datetime`),
    `.visible` (`office:display` - shown pinned open vs. only on hover).
    Look up via `Cell.comment`, not directly - writable: set any property
    to update it in place, once the comment itself exists (see
    `Cell.comment`'s setter to create one)."""

    def __init__(self, tag: etree._Element) -> None:
        self._tag = tag

    def _insert_before_text(self, element: etree._Element) -> None:
        # keeps dc:creator/dc:date ahead of the note's text, as ODF orders
        # them, whichever property gets set first: before its first
        # paragraph or list - a paragraph searched among all descendants
        # could be one inside a list, and put the author inside it
        first = next((c for c in self._tag.iterchildren(*_NOTE_TEXT)), None)
        if first is not None:
            insert_before(first, element)
        else:
            self._tag.append(element)

    @property
    def text(self) -> "str | None":
        # every paragraph, descendants: a note set as a bulleted list keeps
        # its paragraphs in text:list, and LibreOffice shows them as its text
        paragraphs = [paragraph_text(p) for p in descendants(self._tag, "text:p")]
        return "\n".join(paragraphs) if paragraphs else None

    @text.setter
    def text(self, value: "str | None") -> None:
        # the whole text goes, a list with it: removing its paragraphs only
        # left the list behind, empty items and all
        for element in list(self._tag.iterchildren(*_NOTE_TEXT)):
            remove(element)
        for line in (value or "").split("\n"):
            p = new("text:p")
            set_paragraph_text(p, line)
            self._tag.append(p)

    @property
    def author(self) -> "str | None":
        tag = child(self._tag, "dc:creator")
        return text(tag) if tag is not None else None

    @author.setter
    def author(self, value: "str | None") -> None:
        tag = child(self._tag, "dc:creator")
        if value is None:
            if tag is not None:
                remove(tag)
            return
        if tag is None:
            tag = new("dc:creator")
            self._insert_before_text(tag)
        set_text(tag, value)

    @property
    def date(self) -> "dt.datetime | None":
        tag = child(self._tag, "dc:date")
        if tag is None:
            return None
        try:
            return dt.datetime.fromisoformat(text(tag))
        except ValueError:
            return None

    @date.setter
    def date(self, value: "dt.datetime | None") -> None:
        tag = child(self._tag, "dc:date")
        if value is None:
            if tag is not None:
                remove(tag)
            return
        if not isinstance(value, dt.datetime):
            raise TypeError(f"Comment.date must be a datetime.datetime or None, got {type(value)!r}")
        if tag is None:
            tag = new("dc:date")
            self._insert_before_text(tag)
        set_text(tag, value.isoformat())

    @property
    def visible(self) -> bool:
        return self._tag.get(qn("office:display")) == "true"

    @visible.setter
    def visible(self, value: bool) -> None:
        self._tag.set(qn("office:display"), "true" if value else "false")

    def __repr__(self) -> str:
        return f"Comment(author={self.author!r}, text={self.text!r})"
