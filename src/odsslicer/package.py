"""The package: the zip a document is, its parts as bytes and as lxml trees,
and writing them back.

`ODSReader` held this alongside its sheets, its style lookup and its drawings
until 1.0. It has a rule of its own, which is easier to keep in one class:
`save()` writes `content.xml` and `meta.xml` from their trees, and copies
every other part byte for byte unless something recorded a write to its tree
(`touch`, `touch_tag`) - that is what makes "we change values, not styling" a
property of the code. The class that holds the bytes, the trees and that
record is the only one deciding how a part goes back into the zip; the
others only say what they wrote to. It knows nothing of sheets, styles or
charts: which parts it parses beyond the four of the document (`settings.xml`
on demand, an embedded object's `content.xml` when asked for one) is decided
by whoever asks.
"""

from pathlib import Path
from typing import cast
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from lxml import etree

from odsslicer.fileutils import _replacing
from odsslicer.xmltree import parse, serialize
from odsslicer.xmlutils import _encrypted_parts

__all__ = ["EncryptedDocumentError", "MalformedDocumentError"]

# Minimal stand-ins for the optional package parts (ODF 1.2 part 3 makes
# everything but `content.xml` and the mimetype optional, and e.g. Excel
# really does omit `settings.xml`): enough structure for the rest of the
# library - style lookup, `.properties` - to work unchanged.
_BLANK_STYLES_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<office:document-styles"
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    b' office:version="1.2">'
    b"<office:styles/><office:automatic-styles/><office:master-styles/>"
    b"</office:document-styles>"
)
_BLANK_META_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<office:document-meta"
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
    b' xmlns:dc="http://purl.org/dc/elements/1.1/"'
    b' office:version="1.2">'
    b"<office:meta/>"
    b"</office:document-meta>"
)
_BLANK_SETTINGS_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<office:document-settings"
    b' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    b' xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
    b' office:version="1.2">'
    b"<office:settings/>"
    b"</office:document-settings>"
)


class EncryptedDocumentError(ValueError):
    """The package is password-protected: its content is encrypted, and
    odsslicer reads none of it rather than read it as an empty document."""


class MalformedDocumentError(ValueError):
    """A part of the package is not well-formed XML. odsslicer reads it as
    written or not at all: a parser that recovers would complete a truncated
    part with whatever it guesses, and `save()` would write that guess back
    over the original."""


def _parse_part(file: Path, name: str, markup: bytes) -> "etree._ElementTree":
    """Package part `name` of `file` as an lxml tree (`xmltree.parse`),
    `MalformedDocumentError` naming the part and the place if it is not
    well-formed."""
    try:
        return parse(markup)
    except etree.XMLSyntaxError as exc:
        raise MalformedDocumentError(f"{file}: {name} is not well-formed XML ({exc})") from exc


def _root_of(element: etree._Element) -> etree._Element:
    """The topmost ancestor of `element`, itself if it has no parent - the
    root of its part's tree, or of a detached subtree.

    Not by `getroottree()`: an element taken out of its tree still belongs
    to the same lxml document, and `getroottree()` answers that document's
    root - a write to a detached element would count as one to the part,
    and a drawing deleted with its row as one still in the document."""
    while (parent := element.getparent()) is not None:
        element = parent
    return element


class _Package:
    """The parts of the package `file`: `content.xml`, `styles.xml`,
    `meta.xml` parsed on opening, `settings.xml` and the embedded objects'
    content on demand; which of them were written to; and `write`, which
    puts them back into a zip."""

    def __init__(self, file: Path) -> None:
        self.file = file
        # http://docs.oasis-open.org/office/v1.2/
        # Only `content.xml` (and the mimetype) is guaranteed: real-world
        # generators omit the rest - Excel ships no `settings.xml` at all.
        with ZipFile(file) as zip:
            members = set(zip.namelist())

            def read_optional(name: str, fallback: bytes) -> bytes:
                return zip.read(name) if name in members else fallback

            # Before anything is parsed: an encrypted part parses as an
            # empty document, and a save would write it over the data -
            # see `_encrypted_parts`.
            if "content.xml" in _encrypted_parts(read_optional("META-INF/manifest.xml", b"")):
                raise EncryptedDocumentError(
                    f"{file} is password-protected: odsslicer cannot decrypt it."
                    " Open it in LibreOffice and save it without a password to read it here."
                )
            # Document content and automatic styles used in the content.
            self.content = zip.read("content.xml")
            # Styles used in the document content and automatic styles used in the styles themselves.
            self.styles = read_optional("styles.xml", _BLANK_STYLES_XML)
            # Document meta information, such as the author or the time of the last save action.
            self.meta = read_optional("meta.xml", _BLANK_META_XML)
            # Application-specific settings, such as the window size or printer information.
            self.settings = read_optional("settings.xml", _BLANK_SETTINGS_XML)
        # parsed as written, whitespace and attribute order kept, and
        # strictly: a part that is not well-formed XML raises
        # `MalformedDocumentError` here, rather than open repaired
        self.data = _parse_part(file, "content.xml", self.content)
        self.styles_data = _parse_part(file, "styles.xml", self.styles)
        self.meta_data = _parse_part(file, "meta.xml", self.meta)
        # `settings.xml` holds nothing a cell needs, so it is parsed only if
        # something asks for it - see `settings_data`
        self._settings_data: etree._ElementTree | None = None
        # The parts `write` regenerates only once something here wrote to
        # their tree; until then it copies their bytes through.
        self.touched: set[str] = set()
        # the embedded objects' content, parsed on demand, each with the
        # bytes it was read from; None for a name the package does not hold
        self._objects: dict[str, tuple[etree._ElementTree, bytes] | None] = {}
        self._objects_parsed = False  # see parse_objects

    @property
    def settings_data(self) -> "etree._ElementTree":
        """`settings.xml`, parsed on first access (see `ODSReader.settings_data`)."""
        if self._settings_data is None:
            self._settings_data = _parse_part(self.file, "settings.xml", self.settings)
        return self._settings_data

    def content_root(self) -> etree._Element:
        """`<office:document-content>`, the root element of `content.xml`:
        the automatic styles and the body are its children."""
        return self.data.getroot()

    def declare_on_content_root(self, prefix: str, uri: str) -> None:
        """Declare `prefix` for `uri` on the root of `content.xml`. lxml
        cannot add a declaration to an existing element: the root is made
        again, its name, attributes, declarations and content kept, and the
        content moved under it - the elements, and every `Sheet` and `Cell`
        holding one, stay the same objects. A pass over the document, paid
        once, by a document that binds no prefix to OpenFormula and is given
        a formula (see `ODSReader._formula_prefix`)."""
        old = self.content_root()
        nsmap = cast("dict[str, str]", dict(old.nsmap))
        nsmap[prefix] = uri
        root = etree.Element(old.tag, nsmap=nsmap)
        for key, value in old.attrib.items():
            root.set(key, value)
        root.text = old.text
        for element in list(old):
            root.append(element)  # its tail along
        self.data._setroot(root)

    def touch(self, name: str) -> None:
        """Record that the in-memory tree of package part `name` no longer
        matches the file, so that `write` serialises it instead of copying
        its bytes through."""
        self.touched.add(name)

    def touch_tag(self, tag: etree._Element) -> None:
        """Record a write to `tag`, wherever it lives: the part whose tree
        holds it is then serialised by `write`. For a tag of `content.xml`
        or `meta.xml`, which are always regenerated, there is nothing to
        record. The way for code that writes to a tag it found rather than
        made - a number format of `styles.xml`, say - to say so without
        knowing which part it came from.

        The element climbs to its tree's root (see `_root_of`): taken out of
        its tree, it is a root of its own, and belongs to no part."""
        top = _root_of(tag)
        for name, part in self.lazy_parts().items():
            if part.getroot() is top:
                self.touch(name)

    def lazy_parts(self) -> "dict[str, etree._ElementTree]":
        """The parsed parts `write` writes back only once touched, by the
        name they have in the package. `settings.xml` is absent until
        something parses it; so is an object's content (see `object_part`)."""
        parts: dict[str, etree._ElementTree] = {"styles.xml": self.styles_data}
        if self._settings_data is not None:
            parts["settings.xml"] = self._settings_data
        parts.update({name: part[0] for name, part in self._objects.items() if part is not None})
        return parts

    def _serialized(self, name: str, part: "etree._ElementTree") -> bytes:
        """Parsed part `name` back to bytes, by `xmltree.serialize`, under
        the XML declaration it was read with."""
        if name == "styles.xml":
            return serialize(part, self.styles)
        if name == "settings.xml":
            return serialize(part, self.settings)
        return serialize(part, cast("tuple[etree._ElementTree, bytes]", self._objects[name])[1])

    def object_parts(self, names: "list[str]") -> "dict[str, etree._ElementTree]":
        """The parts `names` - the `content.xml` of embedded objects - parsed,
        those not parsed yet read from the package in one opening of it; a
        name the package does not hold is left out. Parsing one is not
        editing it: whoever rewrites it has to `touch` it. A part that is not
        well-formed XML raises `MalformedDocumentError`."""
        missing = [name for name in names if name not in self._objects]
        if missing:
            with ZipFile(self.file) as package:
                members = set(package.namelist())
                for name in missing:
                    self._objects[name] = (
                        self._object_part(name, package.read(name)) if name in members else None
                    )
        return {name: part[0] for name in names if (part := self._objects[name]) is not None}

    def _object_part(self, name: str, markup: bytes) -> "tuple[etree._ElementTree, bytes]":
        return _parse_part(self.file, name, markup), markup

    def parse_objects(self) -> None:
        """Parse the content of every object the package embeds, if not done
        yet, so that one that is not well-formed XML raises
        `MalformedDocumentError` now, with the document as it was - before a
        structural edit, rather than once the edit is halfway through.

        Found among the package's members, not through the `<draw:object>`
        elements: looking those up now would fill the drawings' cache before
        the edit, with elements the edit may then discard. A document whose
        content names no object has nothing to read."""
        if self._objects_parsed:
            return
        if b":object" in self.content:
            with ZipFile(self.file) as package:
                for name in package.namelist():
                    if name.endswith("/content.xml") and name not in self._objects:
                        self._objects[name] = self._object_part(name, package.read(name))
        self._objects_parsed = True  # once every part has parsed: a malformed one raises again

    def write(self, path: Path) -> None:
        """Write the package to `path`: `content.xml` and `meta.xml` from
        their trees, a touched part from its tree, every other member as it
        was read - see `ODSReader.save`, which says why, and how `path` is
        replaced."""
        regenerated = {
            "content.xml": serialize(self.data, self.content),
            "meta.xml": serialize(self.meta_data, self.meta),
        }
        regenerated.update(
            (name, self._serialized(name, part))
            for name, part in self.lazy_parts().items()
            if name in self.touched
        )
        with ZipFile(self.file) as src:
            entries = [
                (item, regenerated.get(item.filename, src.read(item.filename))) for item in src.infolist()
            ]
        # a regenerated part absent from the source package (every part but
        # `content.xml` is optional in ODF, see __init__) still has to be
        # written, or the in-memory edits to it would be dropped
        present = {item.filename for item, _ in entries}
        entries += [(ZipInfo(name), data) for name, data in regenerated.items() if name not in present]
        with _replacing(path) as file, ZipFile(file, "w") as dst:
            for item, data in entries:
                # the ODF spec requires `mimetype` to be the first entry and stored uncompressed
                item.compress_type = ZIP_STORED if item.filename == "mimetype" else ZIP_DEFLATED
                dst.writestr(item, data)
