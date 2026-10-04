"""`ODSReader.save()`: writing the file, what it leaves alone, and what it keeps.

Four things are checked here. First, the write itself (issue #8): `save()`
used to truncate its target and write the new zip into it, so a program
reading the file meanwhile got a partial zip, and a save stopped midway left
the workbook unreadable - these tests stop a save at each of its steps, and
read the file on disk while the zip is being written. Second, that the parts
`save()` copies through really are copied, byte for byte, and only serialised
once something wrote to their tree - and that every write into such a tree
says so. Third, that a part which is serialised says what it said: in
particular that no run of whitespace is lost on the way. Fourth, that a
password-protected package is refused when it is opened, before a save could
write what its encrypted parts parse to - nothing - over them.
"""

import os
import pathlib
import random
import re
import stat
import zipfile

import pytest
from lxml import etree

from conftest import FIXTURES_DIR
from odsslicer import EncryptedDocumentError, ODSReader
from odsslicer.xmlutils import _encrypted_parts, _parse_xml, _root_local_name


def _edited(path):
    book = ODSReader(path)
    book.sheet("Sheet1")["A1"].value = "saved"
    return book


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def _names(folder):
    return sorted(path.name for path in folder.iterdir())


def _interrupt(monkeypatch, step, error):
    if step == "writing":
        writestr = zipfile.ZipFile.writestr
        calls = []

        def failing_writestr(self, *args, **kwargs):
            calls.append(args)
            if len(calls) == 3:
                raise error("interrupted")
            writestr(self, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "writestr", failing_writestr)
    elif step == "syncing":

        def failing_fsync(fd):
            raise error("interrupted")

        monkeypatch.setattr(os, "fsync", failing_fsync)
    else:

        def failing_replace(self, target):
            raise error("interrupted")

        monkeypatch.setattr(pathlib.Path, "replace", failing_replace)


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("step", ["writing", "syncing", "renaming"])
def test_interrupted_save_leaves_the_old_file_and_no_temporary_file(workbook, monkeypatch, step, error):
    before = workbook.read_bytes()
    book = _edited(workbook)
    _interrupt(monkeypatch, step, error)
    with pytest.raises(error):
        book.save()
    monkeypatch.undo()

    assert workbook.read_bytes() == before
    assert _names(workbook.parent) == ["workbook.ods"]
    book.save()
    assert ODSReader(workbook).sheet("Sheet1")["A1"].value == "saved"


def test_reader_during_a_save_gets_the_old_file_until_the_new_one_is_complete(workbook, monkeypatch):
    before = workbook.read_bytes()
    book = _edited(workbook)
    seen = []
    writestr = zipfile.ZipFile.writestr

    def write_then_read(self, *args, **kwargs):
        writestr(self, *args, **kwargs)
        seen.append(workbook.read_bytes())  # what another program would read now

    monkeypatch.setattr(zipfile.ZipFile, "writestr", write_then_read)
    book.save()
    monkeypatch.undo()

    with zipfile.ZipFile(workbook) as saved:
        assert len(seen) == len(saved.namelist())
    assert all(data == before for data in seen)
    assert ODSReader(workbook).sheet("Sheet1")["A1"].value == "saved"


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o664], ids=oct)
def test_save_in_place_keeps_the_file_permissions(workbook, mode):
    workbook.chmod(mode)
    expected = _mode(workbook)  # all of it on POSIX, the read-only flag on Windows
    _edited(workbook).save()
    assert _mode(workbook) == expected


def test_save_to_another_path_leaves_the_source_and_gives_default_permissions(workbook, tmp_path):
    before = workbook.read_bytes()
    folder = tmp_path / "out"
    folder.mkdir()
    _edited(workbook).save(folder / "copy.ods")

    assert workbook.read_bytes() == before
    assert ODSReader(folder / "copy.ods").sheet("Sheet1")["A1"].value == "saved"
    reference = folder / "reference"
    reference.write_bytes(b"")  # a file as open() creates it, the umask applied
    assert _mode(folder / "copy.ods") == _mode(reference)
    assert _names(folder) == ["copy.ods", "reference"]
    assert _names(tmp_path) == ["out", "workbook.ods"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need a privilege on Windows")
def test_save_through_a_symlink_replaces_the_file_it_points_to(workbook, tmp_path):
    link = tmp_path / "link.ods"
    link.symlink_to(workbook)
    _edited(link).save()

    assert link.is_symlink()
    assert link.resolve() == workbook.resolve()
    assert ODSReader(workbook).sheet("Sheet1")["A1"].value == "saved"
    assert _names(tmp_path) == ["link.ods", "workbook.ods"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root may write any file")
def test_save_over_a_read_only_file_raises_permission_error(workbook):
    before = workbook.read_bytes()
    workbook.chmod(0o444)
    book = _edited(workbook)
    with pytest.raises(PermissionError):
        book.save()

    assert workbook.read_bytes() == before
    assert _names(workbook.parent) == ["workbook.ods"]


def test_recalculation_runs_on_the_saved_file(workbook, monkeypatch):
    recalculated = []

    def recalculate(path, timeout, update_links):
        assert ODSReader(path).sheet("Sheet1")["A1"].value == "saved"
        assert _names(workbook.parent) == ["workbook.ods"]
        recalculated.append(path)

    monkeypatch.setattr("odsslicer.reader._recalculate_file", recalculate)
    _edited(workbook).save(recalculate=True)
    assert recalculated == [workbook]


# ---------------------------------------------------------------------------
# What a save leaves alone: the parts copied through byte for byte
# ---------------------------------------------------------------------------
#
# The promise of the package is that it changes values, not styling. For
# `styles.xml` and `settings.xml` that is a property of the code rather than a
# hope, because `save()` copies their bytes instead of serialising a tree of
# them - and parsing then serialising is faithful in meaning but not to the
# byte. Measured on these very fixtures: attributes come back in another order
# (1,427 start tags out of 3,110 in one `content.xml`), `&apos;` is written
# out as `'`, an empty element collapses to `<x/>`. (A whitespace-only text
# node used to be squeezed to a single space too - which in
# `<number:text>   </number:text>` was a number format losing its padding, 34
# such nodes over three of the eight fixtures below; see "what a save keeps
# as written" further down for how that is gone.)
#
# These tests fail as soon as a part nothing asked for is serialised.

REGENERATED_PARTS = {"content.xml", "meta.xml"}

COPIED_PARTS = ("styles.xml", "settings.xml")

FIXTURES = [
    FIXTURES_DIR / "TEST.ods",
    FIXTURES_DIR / "WHOLEROW.ods",
    *sorted((FIXTURES_DIR / "wild").glob("*.ods")),
]

# a number format padded with a run of spaces, which a round trip through
# BeautifulSoup shortens to one
PADDED_NUMBER_TEXT = re.compile(rb"<number:text[^>]*>[ \t]{2,}</number:text>")

# how many each fixture holds, in `styles.xml`, counted on the files
PADDED_NUMBER_TEXT_COUNTS = {
    "libreoffice26_linux_streets.ods": 8,
    "libreoffice26_windows_procurement.ods": 13,
    "libreoffice35_casinos_2015.ods": 13,
}


def _members(path):
    with zipfile.ZipFile(path) as package:
        return {name: package.read(name) for name in package.namelist()}


def _saved(book, tmp_path):
    out = tmp_path / "saved.ods"
    book.save(out)
    return _members(out)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_a_save_without_an_edit_rewrites_the_regenerated_parts_only(fixture, tmp_path):
    before = _members(fixture)

    after = _saved(ODSReader(fixture), tmp_path)

    assert set(after) == set(before)
    assert {name for name, data in before.items() if after[name] != data} <= REGENERATED_PARTS


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_editing_a_value_leaves_styles_and_settings_byte_identical(fixture, tmp_path):
    before = _members(fixture)
    book = ODSReader(fixture)
    book.sheets[0][0, 0].value = "edited by the test"

    after = _saved(book, tmp_path)

    for part in COPIED_PARTS:
        if part in before:
            assert after[part] == before[part], part


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_reading_the_settings_does_not_make_the_save_rewrite_them(fixture, tmp_path):
    before = _members(fixture)
    book = ODSReader(fixture)
    book.settings_data.find("office:settings")  # parsing is not editing

    after = _saved(book, tmp_path)

    if "settings.xml" in before:
        assert after["settings.xml"] == before["settings.xml"]
    else:
        assert "settings.xml" not in after


@pytest.mark.parametrize("name, expected", sorted(PADDED_NUMBER_TEXT_COUNTS.items()))
def test_a_number_format_padded_with_spaces_keeps_its_padding(name, expected, tmp_path):
    # real LibreOffice accounting formats: `<number:text>   </number:text>`,
    # which serialising `styles.xml` would shorten to one space
    fixture = FIXTURES_DIR / "wild" / name
    assert len(PADDED_NUMBER_TEXT.findall(_members(fixture)["styles.xml"])) == expected

    after = _saved(ODSReader(fixture), tmp_path)

    assert len(PADDED_NUMBER_TEXT.findall(after["styles.xml"])) == expected


def test_a_touched_settings_part_is_written_back(workbook, tmp_path):
    book = ODSReader(workbook)
    item = book.settings_data.find("config:config-item", attrs={"config:name": "VisibleAreaTop"})
    assert item is not None, "TEST.ods is expected to record a visible area"
    item.string = "4242"
    book._touched_part("settings.xml")

    _saved(book, tmp_path)

    reread = ODSReader(tmp_path / "saved.ods").settings_data
    written = reread.find("config:config-item", attrs={"config:name": "VisibleAreaTop"})
    assert written.get_text() == "4242"


def test_a_touched_styles_part_is_written_back(workbook, tmp_path):
    book = ODSReader(workbook)
    style = book.styles_data.find("style:style", attrs={"style:name": "Default"})
    assert style is not None, "TEST.ods is expected to carry a Default cell style"
    style["style:display-name"] = "touched by the test"
    book._touched_part("styles.xml")

    after = _saved(book, tmp_path)

    assert b'style:display-name="touched by the test"' in after["styles.xml"]


def test_a_touched_settings_part_is_created_for_a_file_that_had_none(tmp_path):
    # Excel ships no settings.xml at all: it is optional in ODF
    fixture = FIXTURES_DIR / "wild" / "excel16_uk_stats_2026.ods"
    assert "settings.xml" not in _members(fixture)
    book = ODSReader(fixture)
    settings = book.settings_data
    settings.find("office:settings").append(
        settings.new_tag("config:config-item-set", attrs={"config:name": "ooo:view-settings"})
    )
    book._touched_part("settings.xml")

    after = _saved(book, tmp_path)

    assert b'config:name="ooo:view-settings"' in after["settings.xml"]


# ---------------------------------------------------------------------------
# What a save keeps as written: whitespace, and the meaning of every part
# ---------------------------------------------------------------------------
#
# A part that is serialised - `content.xml` and `meta.xml` on every save, the
# others once touched - must say what it said. Attribute order, `&apos;` and
# the shape of an empty element are allowed to change; nothing else is, so
# the canonical form (C14N) of a part has to come back identical.
#
# BeautifulSoup squeezes a text node made of whitespace only to one character
# while parsing, unless an element it was told to preserve is open:
# `_parse_xml` names the document element, so everything under it is kept.
# The accounting formats of three fixtures, `<number:text>   </number:text>`,
# are the real-world case; the same loss reached `content.xml` on every save
# of every version before this, for a padded format that happened to live
# in its automatic styles.


def _c14n(data):
    return etree.tostring(etree.fromstring(data), method="c14n")


def _with_part_edited(fixture, tmp_path, part, edit):
    """A copy of `fixture` whose `part` went through `edit` (bytes -> bytes)."""
    path = tmp_path / f"edited-{fixture.name}"
    with zipfile.ZipFile(fixture) as src, zipfile.ZipFile(path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            dst.writestr(item, edit(data) if item.filename == part else data)
    return path


# an accounting-style number format, padded with three spaces either side
PADDED_FORMAT = (
    b'<number:number-style style:name="NPAD"><number:text>   </number:text>'
    b'<number:number number:decimal-places="2" number:min-integer-digits="1" number:grouping="true"/>'
    b"<number:text>   </number:text></number:number-style>"
)


def _padded_in_automatic_styles(content):
    assert b"<office:automatic-styles>" in content, "TEST.ods is expected to carry automatic styles"
    return content.replace(b"<office:automatic-styles>", b"<office:automatic-styles>" + PADDED_FORMAT, 1)


def test_a_padded_number_format_in_content_xml_keeps_its_padding_through_a_save(tmp_path):
    # `content.xml` is regenerated by every save: a padded format there -
    # one applied to a cell directly rather than through a named style -
    # came back as `<number:text> </number:text>`, whatever the edit
    path = _with_part_edited(FIXTURES_DIR / "TEST.ods", tmp_path, "content.xml", _padded_in_automatic_styles)
    assert len(PADDED_NUMBER_TEXT.findall(_members(path)["content.xml"])) == 2

    after = _saved(ODSReader(path), tmp_path)

    assert len(PADDED_NUMBER_TEXT.findall(after["content.xml"])) == 2
    reread = ODSReader(tmp_path / "saved.ods")
    assert [t.get_text() for t in reread._find_number_style("NPAD").find_all("number:text")] == ["   ", "   "]


@pytest.mark.parametrize("name, expected", sorted(PADDED_NUMBER_TEXT_COUNTS.items()))
def test_a_styles_part_written_back_keeps_its_padded_number_formats(name, expected, tmp_path):
    # copying `styles.xml` through hid the loss; once something writes to
    # it, the tree is serialised and has to hold the padding itself
    book = ODSReader(FIXTURES_DIR / "wild" / name)
    book._touched_part("styles.xml")

    after = _saved(book, tmp_path)

    assert len(PADDED_NUMBER_TEXT.findall(after["styles.xml"])) == expected


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_a_save_without_an_edit_changes_nothing_in_meaning(fixture, tmp_path):
    before = _members(fixture)

    after = _saved(ODSReader(fixture), tmp_path)

    for part in REGENERATED_PARTS:
        assert _c14n(after[part]) == _c14n(before[part]), part


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_a_styles_part_written_back_says_what_it_said(fixture, tmp_path):
    before = _members(fixture)
    book = ODSReader(fixture)
    book._touched_part("styles.xml")  # serialised from its tree, with nothing changed in it

    after = _saved(book, tmp_path)

    assert _c14n(after["styles.xml"]) == _c14n(before["styles.xml"])


def test_parsing_keeps_every_run_of_whitespace_whatever_the_element():
    # not only `number:text`: nothing is decided about what an element means
    markup = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<!-- a comment before the document element, as XML allows -->\n"
        b'<a:root xmlns:a="urn:a"><a:x>   </a:x><a:y><a:z>\t\n </a:z></a:y><a:w> </a:w></a:root>'
    )
    assert _root_local_name(markup) == "root"

    out = _parse_xml(markup).encode("utf-8")

    assert b"<a:x>   </a:x>" in out
    assert b"<a:z>\t\n </a:z>" in out
    assert b"<a:w> </a:w>" in out


def test_parsing_something_that_is_not_xml_still_gives_a_tree():
    assert _root_local_name(b"") is None
    assert _root_local_name(b"no markup at all") is None
    assert _parse_xml(b"").find(True) is None


# ---------------------------------------------------------------------------
# Every write into a copied part says so
# ---------------------------------------------------------------------------
#
# The parts `save()` copies through are only serialised once something records
# a write to their tree (`_touched_part`, `_touched_tag`). A write that forgot
# to would be silently lost on save: these tests hold each path that can reach
# such a tree. A chart's content has its own tests (test_odsslicer.py, "a chart
# follows..."). `settings.xml` has no writer in the library yet.


def test_a_condition_added_to_a_number_format_of_styles_xml_is_written_back(workbook, tmp_path):
    # a format a named cell style uses lives in `styles.xml` (A7 of TEST.ods
    # is one): `add_condition` writes into it where it is
    book = ODSReader(workbook)
    sheet = book.sheet("Sheet1")
    fmt, target = sheet["A7"].style.number_format, sheet["A8"].style.number_format
    assert fmt._tag.find_parent("office:document-styles") is not None, "expected a format of styles.xml"
    assert fmt.conditions == []

    fmt.add_condition("value()<0", target)
    _saved(book, tmp_path)

    reread = ODSReader(tmp_path / "saved.ods").sheet("Sheet1")["A7"].style.number_format
    assert [(condition, applied.name) for condition, applied in reread.conditions] == [
        ("value()<0", target.name)
    ]


def test_a_style_of_styles_xml_bearing_a_fork_s_name_is_forked_rather_than_written_into(tmp_path):
    # odsslicer names the styles it forks `ors1`, `ocos1`..., and writes into
    # one it finds under such a name without forking again - but only one of
    # its own, in the automatic styles of `content.xml`. A style of
    # `styles.xml` that happens to bear the name is somebody else's.
    foreign = (
        b'<style:style style:name="ors1" style:family="table-row">'
        b'<style:table-row-properties style:row-height="1cm"/></style:style>'
    )

    def add_foreign_style(styles):
        assert b"<office:styles>" in styles
        return styles.replace(b"<office:styles>", b"<office:styles>" + foreign, 1)

    path = _with_part_edited(FIXTURES_DIR / "TEST.ods", tmp_path, "styles.xml", add_foreign_style)
    before = _members(path)
    book = ODSReader(path)
    sheet = book.sheet("Sheet1")
    sheet.rows[0][0].cell.parent["table:style-name"] = "ors1"
    assert sheet.row_style(0).height == "1cm"

    sheet.row_style(0).height = "2cm"
    after = _saved(book, tmp_path)

    assert after["styles.xml"] == before["styles.xml"]
    assert ODSReader(tmp_path / "saved.ods").sheet("Sheet1").row_style(0).height == "2cm"


# ---------------------------------------------------------------------------
# A password-protected package is refused, not read as empty
# ---------------------------------------------------------------------------
#
# Its parts are ciphertext, which `_parse_xml` recovers from as from any broken
# markup: the document read as one with no sheets, and `save()` - over the
# source file, by default - wrote a `content.xml` holding the XML declaration
# alone in place of the encrypted one. LibreOffice's own test files
# (sc/qa/unit/data/ods/password*.ods) are the real-world case; the fixture
# below is TEST.ods with its manifest saying the same of `content.xml`.

ODF_MANIFEST = b"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
OPENOFFICE_MANIFEST = b"http://openoffice.org/2001/manifest"
OPENOFFICE_DOCTYPE = (
    b'<!DOCTYPE manifest:manifest PUBLIC "-//OpenOffice.org//DTD Manifest 1.0//EN" "Manifest.dtd">\n'
)
# what LibreOffice writes for a part encrypted the pre-3.4 way, checksums made up
ENCRYPTION_DATA = (
    b'<manifest:encryption-data manifest:checksum-type="SHA1/1K"'
    b' manifest:checksum="wnowfp29iYFoFfSCRvaKpQ==">'
    b'<manifest:algorithm manifest:algorithm-name="Blowfish CFB"'
    b' manifest:initialisation-vector="qOnQzN5IFMw="/>'
    b'<manifest:key-derivation manifest:key-derivation-name="PBKDF2" manifest:key-size="16"'
    b' manifest:iteration-count="1024" manifest:salt="Vl97+rK9tMG+QExYbQjkkg=="/>'
    b"</manifest:encryption-data>"
)
CONTENT_ENTRY = b'<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'


def _encrypted(tmp_path, namespace=ODF_MANIFEST, doctype=b""):
    """TEST.ods with `content.xml` declared encrypted in a manifest of
    `namespace`, and replaced by bytes as meaningless as ciphertext."""

    def mark_encrypted(manifest):
        assert CONTENT_ENTRY in manifest, "TEST.ods is expected to list content.xml in its manifest"
        entry = CONTENT_ENTRY[:-2] + b">" + ENCRYPTION_DATA + b"</manifest:file-entry>"
        manifest = manifest.replace(CONTENT_ENTRY, entry).replace(ODF_MANIFEST, namespace)
        declaration, rest = manifest.split(b"\n", 1)
        return declaration + b"\n" + doctype + rest

    path = _with_part_edited(FIXTURES_DIR / "TEST.ods", tmp_path, "META-INF/manifest.xml", mark_encrypted)
    return _with_part_edited(path, tmp_path, "content.xml", lambda _: random.Random(0).randbytes(941))


@pytest.mark.parametrize(
    "namespace, doctype",
    [(ODF_MANIFEST, b""), (OPENOFFICE_MANIFEST, OPENOFFICE_DOCTYPE)],
    ids=["odf", "openoffice"],
)
def test_opening_a_password_protected_package_raises_and_leaves_it_untouched(namespace, doctype, tmp_path):
    path = _encrypted(tmp_path, namespace, doctype)
    before = path.read_bytes()

    with pytest.raises(EncryptedDocumentError, match="password-protected"):
        ODSReader(path)

    assert path.read_bytes() == before


def test_a_password_protected_package_is_a_value_error(tmp_path):
    # the error callers already catch for a file odsslicer cannot read
    with pytest.raises(ValueError):
        ODSReader(_encrypted(tmp_path))


@pytest.mark.parametrize("manifest", [b"", b"no markup at all", b"<manifest:manifest"], ids=repr)
def test_a_manifest_that_does_not_parse_declares_nothing_encrypted(manifest):
    assert _encrypted_parts(manifest) == set()


def test_a_manifest_declares_encrypted_only_the_entries_carrying_encryption_data():
    with zipfile.ZipFile(FIXTURES_DIR / "TEST.ods") as package:
        manifest = package.read("META-INF/manifest.xml")
    assert _encrypted_parts(manifest) == set()

    entry = CONTENT_ENTRY[:-2] + b">" + ENCRYPTION_DATA + b"</manifest:file-entry>"
    assert _encrypted_parts(manifest.replace(CONTENT_ENTRY, entry)) == {"content.xml"}
