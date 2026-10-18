"""ODSReader: its sheets - listed, added, deleted, renamed, moved - new
documents, and `save()`: writing the file, what it leaves alone, and what it
keeps.

Four things are checked of `save()`. First, the write itself (issue #8): `save()`
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

import datetime as dt
import os
import pathlib
import random
import re
import stat
import subprocess
import sys
import zipfile

import pytest
from lxml import etree

from conftest import (
    FIXTURES_DIR,
    ods_with_prefixes,
    ods_with_sheet,
    rebind_prefix,
    requires_soffice,
    table_row,
)
from odsslicer import EncryptedDocumentError, MalformedDocumentError, ODSReader
from odsslicer.xmltree import new, parse, qn, serialize, text
from odsslicer.xmlutils import _encrypted_parts


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
# BeautifulSoup shortened to one
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
    book.settings_data.find(qn("office:settings"))  # parsing is not editing

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


def _config_item(settings, name):
    """The first `<config:config-item>` of `settings` named `name`."""
    items = settings.getroot().iter(qn("config:config-item"))
    return next((item for item in items if item.get(qn("config:name")) == name), None)


def test_a_touched_settings_part_is_written_back(workbook, tmp_path):
    book = ODSReader(workbook)
    item = _config_item(book.settings_data, "VisibleAreaTop")
    assert item is not None, "TEST.ods is expected to record a visible area"
    item.text = "4242"
    book._package.touch("settings.xml")

    _saved(book, tmp_path)

    assert _config_item(ODSReader(tmp_path / "saved.ods").settings_data, "VisibleAreaTop").text == "4242"


def test_a_write_into_the_settings_said_of_its_element_is_written_back(workbook, tmp_path):
    # `_Package.touch_tag` finds the part an element belongs to by its tree's root
    book = ODSReader(workbook)
    item = _config_item(book.settings_data, "VisibleAreaTop")
    item.text = "4242"
    book._package.touch_tag(item)

    after = _saved(book, tmp_path)

    assert b">4242</config:config-item>" in after["settings.xml"]


def test_a_settings_element_taken_out_of_its_tree_belongs_to_no_part(workbook, tmp_path):
    before = _members(workbook)
    book = ODSReader(workbook)
    item = _config_item(book.settings_data, "VisibleAreaTop")
    item.getparent().remove(item)
    book._package.touch_tag(item)  # detached: its root is itself

    after = _saved(book, tmp_path)

    assert after["settings.xml"] == before["settings.xml"]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_a_settings_part_written_back_says_what_it_said(fixture, tmp_path):
    before = _members(fixture)
    book = ODSReader(fixture)
    book.settings_data  # noqa: B018 - parsed, then serialised with nothing changed in it
    book._package.touch("settings.xml")

    after = _saved(book, tmp_path)

    if "settings.xml" in before:
        assert _c14n(after["settings.xml"]) == _c14n(before["settings.xml"])
        # the declaration as the file wrote it, not lxml's
        assert after["settings.xml"].split(b"?>")[0] == before["settings.xml"].split(b"?>")[0]
    else:
        assert after["settings.xml"].startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<office:')


def test_a_settings_part_with_other_prefixes_is_read_and_written_with_its_own(workbook, tmp_path):
    # settings.xml is on lxml: found by namespace whatever the prefixes, and
    # serialised back with the file's own
    config = "urn:oasis:names:tc:opendocument:xmlns:config:1.0"
    rebound = _with_part_edited(
        workbook, tmp_path, "settings.xml", lambda data: rebind_prefix(data, config, "cfg")
    )
    assert b"<cfg:config-item " in _members(rebound)["settings.xml"]
    book = ODSReader(rebound)
    item = _config_item(book.settings_data, "VisibleAreaTop")
    assert item is not None
    item.text = "4242"
    book._package.touch_tag(item)

    after = _saved(book, tmp_path)

    assert (
        b'<cfg:config-item cfg:name="VisibleAreaTop" cfg:type="int">4242</cfg:config-item>'
        in (after["settings.xml"])
    )
    assert b"xmlns:config=" not in after["settings.xml"]


def test_a_touched_styles_part_is_written_back(workbook, tmp_path):
    book = ODSReader(workbook)
    style = book._catalog.find_style("Default")
    assert style is not None, "TEST.ods is expected to carry a Default cell style"
    style.set(qn("style:display-name"), "touched by the test")
    book._package.touch("styles.xml")

    after = _saved(book, tmp_path)

    assert b'style:display-name="touched by the test"' in after["styles.xml"]


def test_a_touched_settings_part_is_created_for_a_file_that_had_none(tmp_path):
    # Excel ships no settings.xml at all: it is optional in ODF
    fixture = FIXTURES_DIR / "wild" / "excel16_uk_stats_2026.ods"
    assert "settings.xml" not in _members(fixture)
    book = ODSReader(fixture)
    settings = book.settings_data
    settings.find(qn("office:settings")).append(
        new("config:config-item-set", {"config:name": "ooo:view-settings"})
    )
    book._package.touch("settings.xml")

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
# BeautifulSoup squeezed a text node made of whitespace only to one character
# while parsing, unless an element it was told to preserve was open: from
# 0.14.2 to 1.0, odsslicer named the document element, so that everything
# under it was kept; lxml keeps it as written. The accounting formats of
# three fixtures, `<number:text>   </number:text>`, are the real-world case;
# the same loss reached `content.xml` on every save of every version before
# 0.14.2, for a padded format that happened to live in its automatic styles.


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
    texts = reread._catalog.find_number_style("NPAD").iter(qn("number:text"))
    assert [text(element) for element in texts] == ["   ", "   "]


@pytest.mark.parametrize("name, expected", sorted(PADDED_NUMBER_TEXT_COUNTS.items()))
def test_a_styles_part_written_back_keeps_its_padded_number_formats(name, expected, tmp_path):
    # copying `styles.xml` through hid the loss; once something writes to
    # it, the tree is serialised and has to hold the padding itself
    book = ODSReader(FIXTURES_DIR / "wild" / name)
    book._package.touch("styles.xml")

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
    book._package.touch("styles.xml")  # serialised from its tree, with nothing changed in it

    after = _saved(book, tmp_path)

    assert _c14n(after["styles.xml"]) == _c14n(before["styles.xml"])


def test_parsing_keeps_every_run_of_whitespace_whatever_the_element():
    # not only `number:text`: nothing is decided about what an element means
    markup = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<!-- a comment before the document element, as XML allows -->\n"
        b'<a:root xmlns:a="urn:a"><a:x>   </a:x><a:y><a:z>\t\n </a:z></a:y><a:w> </a:w></a:root>'
    )
    out = serialize(parse(markup), markup)

    assert b"<a:x>   </a:x>" in out
    assert b"<a:z>\t\n </a:z>" in out
    assert b"<a:w> </a:w>" in out


# ---------------------------------------------------------------------------
# Every write into a copied part says so
# ---------------------------------------------------------------------------
#
# The parts `save()` copies through are only serialised once something records
# a write to their tree (`_Package.touch`, `_Package.touch_tag`). A write that forgot
# to would be silently lost on save: these tests hold each path that can reach
# such a tree. A chart's content has its own tests (test_drawings.py, "a chart
# follows..."). `settings.xml` has no writer in the library yet: the tests
# above write into it directly.


def test_a_condition_added_to_a_number_format_of_styles_xml_is_written_back(workbook, tmp_path):
    # a format a named cell style uses lives in `styles.xml` (A7 of TEST.ods
    # is one): `add_condition` writes into it where it is
    book = ODSReader(workbook)
    sheet = book.sheet("Sheet1")
    fmt, target = sheet["A7"].style.number_format, sheet["A8"].style.number_format
    styles = next(fmt._tag.iterancestors(qn("office:document-styles")), None)
    assert styles is not None, "expected a format of styles.xml"
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
    sheet.rows[0][0].cell.getparent().set(qn("table:style-name"), "ors1")
    assert sheet.row_style(0).height == "1cm"

    sheet.row_style(0).height = "2cm"
    after = _saved(book, tmp_path)

    assert after["styles.xml"] == before["styles.xml"]
    assert ODSReader(tmp_path / "saved.ods").sheet("Sheet1").row_style(0).height == "2cm"


# ---------------------------------------------------------------------------
# A malformed part is refused, not read repaired
# ---------------------------------------------------------------------------
#
# Every part is parsed strictly: a truncated one raises
# `MalformedDocumentError`, naming the part, where bs4 completed it with a
# guess that `save()` wrote back - a truncated `content.xml` read with cells
# missing, and saved that way. `content.xml`, `styles.xml` and `meta.xml`
# raise when the document is opened, `settings.xml` when something reads it,
# a chart's own content at the first structural edit (test_drawings.py).


def _truncated(data):
    return data[: len(data) // 2]


@pytest.mark.parametrize("part", ["content.xml", "styles.xml"])
def test_a_malformed_content_or_styles_part_is_refused_when_the_document_is_opened(part, workbook, tmp_path):
    path = _with_part_edited(workbook, tmp_path, part, _truncated)
    before = path.read_bytes()

    with pytest.raises(MalformedDocumentError, match=re.escape(part)):
        ODSReader(path)

    assert path.read_bytes() == before


def test_a_malformed_meta_part_is_refused_when_the_document_is_opened(workbook, tmp_path):
    path = _with_part_edited(workbook, tmp_path, "meta.xml", _truncated)

    with pytest.raises(MalformedDocumentError, match=r"meta\.xml"):
        ODSReader(path)


def test_a_malformed_settings_part_is_refused_when_something_reads_it(workbook, tmp_path):
    path = _with_part_edited(workbook, tmp_path, "settings.xml", _truncated)
    before = _members(path)
    book = ODSReader(path)  # nothing reads it yet: the document opens

    with pytest.raises(MalformedDocumentError, match=r"settings\.xml"):
        book.settings_data  # noqa: B018

    after = _saved(book, tmp_path)
    assert after["settings.xml"] == before["settings.xml"]


def test_a_malformed_document_error_is_a_value_error():
    assert issubclass(MalformedDocumentError, ValueError)


# ---------------------------------------------------------------------------
# A password-protected package is refused, not read as empty
# ---------------------------------------------------------------------------
#
# Its parts are ciphertext, which bs4 recovered from as from any broken
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


# ---------------------------------------------------------------------------
# BeautifulSoup is gone from the package
# ---------------------------------------------------------------------------


def test_the_package_never_imports_beautifulsoup(workbook, tmp_path):
    # 1.0 reads and writes with lxml alone, and no longer depends on bs4: an
    # import of it left anywhere would fail where it is not installed. The
    # tests have it, to inspect saved files: a fresh interpreter tells
    script = (
        "import sys\n"
        "from odsslicer import ODSReader\n"
        f"book = ODSReader({str(workbook)!r})\n"
        "for sheet in book.sheets:\n"
        "    sheet.to_list()\n"
        "sheet = book.sheet('Sheet1')\n"
        "sheet['A1'].value = 'x'\n"
        "sheet['A1'].comment = 'note'\n"
        "sheet['A1'].hyperlink = 'https://example.com'\n"
        "sheet['A1'].style.bold = True\n"
        "sheet.insert_rows(1, 2)\n"
        f"book.save({str(tmp_path / 'out.ods')!r})\n"
        "print(sorted(name for name in sys.modules if name.split('.')[0] == 'bs4'))\n"
    )
    # this checkout's package, whatever the environment's path says
    env = {**os.environ, "PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1] / "src")}
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, env=env, cwd=tmp_path
    )
    assert done.stdout.strip() == "[]"


def test_what_odsslicer_creates_declares_no_namespace_the_document_binds(workbook, tmp_path):
    # a new element declares its namespace for itself, and lxml drops the
    # declaration once it is inserted where the document binds it: the saved
    # content.xml declares what the original did, no more - bs4 repeated a
    # declaration on an element whose name the document had no other of
    before = _members(workbook)["content.xml"].count(b"xmlns:")
    book = ODSReader(workbook)
    sheet = book.sheet("Sheet1")
    sheet["F20"].value = dt.date(2026, 10, 17)  # past the data: rows, cells, a date style
    sheet["A1"].comment = "a note"
    sheet["A1"].comment.author = "me"
    sheet["A2"].hyperlink = "https://example.com"
    sheet["A3"].style.bold = True
    sheet.row_style(1).height = "1cm"
    sheet.insert_rows(1, 2)
    book.add_sheet("Added")["B2"].value = "x"
    book.sheet("SheetEmpty")["A1:C3"].value = [["k", "v", "n"], ["a", 1, 2], ["b", 3, 4]]
    book.sheet("SheetEmpty").create_pivot_table("A1:C3", "E1", rows=["k"], values={"v": "sum"})
    after = _saved(book, tmp_path)

    assert after["content.xml"].count(b"xmlns:") == before


def test_a_line_break_written_as_a_reference_in_an_attribute_survives_a_save(tmp_path):
    # LibreOffice writes a line break inside an attribute as `&#x0a;` - the
    # text of a string a formula computed, in `office:string-value`: bs4
    # wrote it back as a raw line break, which every XML parser reads as a
    # space, so each save turned it into one. lxml writes the reference
    xml = table_row(
        '<table:table-cell table:formula="of:=&quot;a&#x0a;b&quot;" office:value-type="string"'
        ' office:string-value="a&#x0a;b"><text:p>a</text:p><text:p>b</text:p></table:table-cell>'
    )
    path = ods_with_sheet(tmp_path / "break.ods", xml)
    ODSReader(path).save()
    cell = ODSReader(path).sheet("Sheet1")["A1"]
    assert cell.formula == 'of:="a\nb"'
    assert cell.cell.get(qn("office:string-value")) == "a\nb"


# ---------------------------------------------------------------------------
# ODSReader / Sheet: reaching the cells of TEST.ods, sheet Sheet1
# ---------------------------------------------------------------------------


def test_reader_lists_sheet_names(reader):
    assert set(reader.sheets_names) >= {
        "Sheet1",
        "Sheet2Repeat",
        "SheetEmpty",
        "SheetFusion",
    }


def test_sheets_property_returns_a_reusable_list(reader):
    # regression: used to be a one-shot generator without len()/reuse support
    sheets = reader.sheets
    assert isinstance(sheets, list)
    assert len(sheets) == len(reader.sheets_names)
    assert len(reader.sheets) == len(sheets)  # can be consumed more than once


def test_sheet_unknown_name_raises(reader):
    with pytest.raises(KeyError):
        reader.sheet("DoesNotExist")


def test_sheet_is_cached_between_calls(reader):
    assert reader.sheet("Sheet1") is reader.sheet("Sheet1")


def test_written_values_read_back_after_a_save(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "modifié"
    s["A3"].value = 42.5

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    reread_sheet = reread.sheet("Sheet1")
    assert reread_sheet["A1"].value == "modifié"
    assert reread_sheet["A3"].value == 42.5
    # untouched cells on the same sheet survive the round trip
    assert reread_sheet["A2"].value == 3.4
    assert reread_sheet["A5"].value == 6.4 and reread_sheet["A5"].is_formula


def test_save_leaves_other_sheets_untouched(writable_reader, tmp_path):
    s = writable_reader.sheet("Sheet1")
    s["A1"].value = "modifié"

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    reread = ODSReader(out)
    fusion = reread.sheet("SheetFusion")
    assert fusion.size == (9, 4)
    assert fusion["A4"].value == 5
    assert fusion["B4"].value == 7
    assert fusion["C1"].value == 3
    assert fusion["C9"].value == 1


def test_save_keeps_odf_mimetype_convention(writable_reader, tmp_path):
    import zipfile

    out = tmp_path / "out.ods"
    writable_reader.save(out)

    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype"
        info = z.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"


def test_save_without_a_path_overwrites_the_source_file(writable_reader, tmp_path):
    import shutil

    copy_path = tmp_path / "inplace.ods"
    shutil.copy(writable_reader.file, copy_path)
    r = ODSReader(copy_path)
    r.sheet("Sheet1")["A1"].value = "in place"
    r.save()  # no path given -> overwrite r.file (== copy_path)

    reread = ODSReader(copy_path)
    assert reread.sheet("Sheet1")["A1"].value == "in place"


# ---------------------------------------------------------------------------
# Writing: new sheets (ODSReader.add_sheet)
# ---------------------------------------------------------------------------


class TestAddSheet:
    """`ODSReader.add_sheet`."""

    def test_a_new_sheet_is_empty(self, writable_reader):
        before = list(writable_reader.sheets_names)
        s = writable_reader.add_sheet("NewSheet")
        assert writable_reader.sheets_names == [*before, "NewSheet"]
        assert s.size == (0, 0)
        assert writable_reader.sheet("NewSheet") is s  # cached, same object

    def test_an_empty_name_is_rejected(self, writable_reader):
        with pytest.raises(ValueError):
            writable_reader.add_sheet("")

    def test_a_name_in_use_is_rejected(self, writable_reader):
        writable_reader.add_sheet("Dup")
        with pytest.raises(ValueError):
            writable_reader.add_sheet("Dup")

    def test_a_new_sheet_is_writable_and_grows(self, writable_reader):
        s = writable_reader.add_sheet("NewSheet")
        s["A1"].value = "hello"
        s["C3"].value = 42
        assert s.size == (3, 3)
        assert s["A1"].value == "hello"
        assert s["C3"].value == 42
        assert s["B2"].value is None

    def test_the_existing_sheets_are_left_as_they_are(self, writable_reader):
        writable_reader.add_sheet("NewSheet")
        s1 = writable_reader.sheet("Sheet1")
        assert s1["A1"].value == "texte simple"
        assert s1.size == (9, 2)

    def test_an_added_sheet_reads_back_after_a_save(self, writable_reader, tmp_path):
        # regression: a brand new sheet's lone blank row (needed for it to be a
        # structurally valid ODF sheet) was physically left in the XML after
        # load() discards it from the logical view; growing the sheet then
        # appended new rows *after* that still-present phantom row without
        # removing it, so a save/reload round trip surfaced it as an extra,
        # wrongly-shaped row - see also test_save_round_trip_growing_from_empty.
        s = writable_reader.add_sheet("NewSheet")
        s["A1"].value = "hello"
        s["C3"].value = 42

        out = tmp_path / "out.ods"
        writable_reader.save(out)

        reread = ODSReader(out)
        assert reread.sheets_names[-1] == "NewSheet"
        s2 = reread.sheet("NewSheet")
        assert s2.size == (3, 3)
        assert s2["A1"].value == "hello"
        assert s2["C3"].value == 42
        assert s2["B2"].value is None
        # existing sheets are untouched, and the new table sits after them in the XML
        assert reread.sheet("Sheet1")["A1"].value == "texte simple"

        import zipfile

        with zipfile.ZipFile(out) as z:
            content = z.read("content.xml").decode("utf-8")
        idx_fusion = content.index('table:name="SheetFusion"')
        idx_new = content.index('table:name="NewSheet"')
        idx_named_expr = content.index("table:named-expressions")
        assert idx_fusion < idx_new < idx_named_expr

    def test_an_added_sheet_grown_from_empty_reads_back_after_a_save(self, writable_reader, tmp_path):
        # same phantom-row regression as above, but on a pre-existing empty sheet
        # rather than one freshly created by add_sheet.
        s = writable_reader.sheet("SheetEmpty")
        assert s.size == (0, 0)
        s["B3"].value = "from scratch"

        out = tmp_path / "out.ods"
        writable_reader.save(out)

        reread = ODSReader(out).sheet("SheetEmpty")
        assert reread.size == (3, 2)
        assert reread["B3"].value == "from scratch"
        assert reread["A1"].value is None


OPENFORMULA = "urn:oasis:names:tc:opendocument:xmlns:of:1.2"


# ---------------------------------------------------------------------------
# The prefix a document binds to OpenFormula, which a written formula takes
# ---------------------------------------------------------------------------


class TestOpenFormulaPrefix:
    """`of:` in `of:=SUM(...)` names OpenFormula only where the document
    says so: no XML parser resolves a prefix inside a value. A formula is
    written with the prefix the document binds to it, or with `of` declared
    on the root of `content.xml` (DOCS.md, "What changes in 1.0")."""

    @staticmethod
    def without_openformula(tmp_path):
        """TEST.ods with no declaration of OpenFormula at all - as 5 files of
        the corpus another tool rewrote - and no formula left to need one."""

        def drop(markup):
            markup = markup.replace(f' xmlns:of="{OPENFORMULA}"'.encode(), b"")
            return markup.replace(b"of:=", b"oooc:=")

        path = tmp_path / "unbound.ods"
        with zipfile.ZipFile(FIXTURES_DIR / "TEST.ods") as src, zipfile.ZipFile(path, "w") as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                dst.writestr(item, drop(data) if item.filename == "content.xml" else data)
        return path

    def test_a_document_binding_of_gets_of(self, writable_reader, tmp_path):
        writable_reader.sheet("Sheet1")["C1"].formula = "=A2+A3"
        writable_reader.save(tmp_path / "out.ods")
        assert ODSReader(tmp_path / "out.ods").sheet("Sheet1")["C1"].formula == "of:=[.A2]+[.A3]"

    def test_a_document_binding_another_prefix_gets_that_prefix(self, tmp_path):
        path = ods_with_prefixes(
            FIXTURES_DIR / "TEST.ods", tmp_path / "TEST.ods", {OPENFORMULA: "openformula"}
        )
        reader = ODSReader(path)
        cell = reader.sheet("Sheet1")["C1"]
        cell.formula = "=A2+A3"
        assert cell.formula == "openformula:=[.A2]+[.A3]"
        assert cell.formula_friendly == "=A2+A3"
        reader.save()
        content = zipfile.ZipFile(path).read("content.xml")
        assert b'table:formula="openformula:=[.A2]+[.A3]"' in content
        assert b"xmlns:of=" not in content

    def test_a_document_binding_none_gets_of_declared_on_its_root(self, tmp_path):
        path = self.without_openformula(tmp_path)
        reader = ODSReader(path)
        sheet = reader.sheet("Sheet1")
        cell = sheet["C1"]
        cell.formula = "=A2+A3"
        assert cell.formula == "of:=[.A2]+[.A3]"
        # the root is made again: what the sheet and its cells hold stays theirs
        sheet["C2"].value = 7
        assert sheet.table.getparent() is not None
        reader.save()
        content = zipfile.ZipFile(path).read("content.xml")
        root = etree.fromstring(content)
        assert root.nsmap["of"] == OPENFORMULA
        assert content.count(f'xmlns:of="{OPENFORMULA}"'.encode()) == 1
        reread = ODSReader(path).sheet("Sheet1")
        assert (reread["C1"].formula, reread["C2"].value) == ("of:=[.A2]+[.A3]", 7)
        assert [s.to_list() for s in ODSReader(path).sheets][1:] == [
            s.to_list() for s in ODSReader(FIXTURES_DIR / "TEST.ods").sheets
        ][1:]

    def test_a_document_binding_none_declares_of_once(self, tmp_path):
        reader = ODSReader(self.without_openformula(tmp_path))
        reader.sheet("Sheet1")["C1"].formula = "=A2"
        root = reader.data.getroot()
        reader.sheet("Sheet1")["C2"].formula = "=A3"
        assert reader.data.getroot() is root

    @requires_soffice
    def test_libreoffice_computes_a_formula_written_where_of_was_bound_by_no_one(self, tmp_path):
        path = self.without_openformula(tmp_path)
        reader = ODSReader(path)
        reader.sheet("Sheet1")["C1"].formula = "=A2+A3"
        reader.save(recalculate=True)
        assert ODSReader(path).sheet("Sheet1")["C1"].value == 6.4


# ---------------------------------------------------------------------------
# New documents (ODSReader.new())
# ---------------------------------------------------------------------------


class TestNewDocument:
    """`ODSReader.new`: a document with no file behind it."""

    def test_it_has_a_single_empty_sheet(self):
        doc = ODSReader.new()
        assert doc.sheets_names == ["Sheet1"]
        assert doc.sheet("Sheet1").size == (0, 0)

    def test_its_sheet_takes_the_name_given(self):
        doc = ODSReader.new(sheet_name="Budget")
        assert doc.sheets_names == ["Budget"]
        assert doc.sheet("Budget").size == (0, 0)

    def test_two_are_independent_of_each_other(self):
        doc1 = ODSReader.new()
        doc2 = ODSReader.new()
        doc1.sheet("Sheet1")["A1"].value = "from doc1"
        assert doc2.sheet("Sheet1")["A1"].value is None

    def test_it_supports_writing_growing_formulas_and_add_sheet(self):
        doc = ODSReader.new()
        s = doc.sheet("Sheet1")
        s["A1"].value = "Total"
        s["B1"].formula = "SUM(A2:A10)"
        assert s.size == (1, 2)
        assert s["B1"].formula_friendly == "=SUM(A2:A10)"

        s2 = doc.add_sheet("Data")
        s2["A1:A3"].value = [10, 20, 30]
        assert s2["A1:A3"].to_list() == [[10], [20], [30]]

    def test_saving_it_without_a_path_raises(self, tmp_path):
        doc = ODSReader.new()
        doc.sheet("Sheet1")["A1"].value = "x"
        with pytest.raises(ValueError):
            doc.save()

    def test_it_reads_back_after_a_save(self, tmp_path):
        doc = ODSReader.new()
        s = doc.sheet("Sheet1")
        s["A1"].value = "Total"
        s["B1"].formula = "SUM(A2:A10)"
        doc.add_sheet("Data")["A1:A3"].value = [10, 20, 30]

        out = tmp_path / "brand_new.ods"
        doc.save(out)

        reread = ODSReader(out)
        assert reread.sheets_names == ["Sheet1", "Data"]
        assert reread.sheet("Sheet1")["A1"].value == "Total"
        assert reread.sheet("Sheet1")["B1"].formula_friendly == "=SUM(A2:A10)"
        assert reread.sheet("Data")["A1:A3"].to_list() == [[10], [20], [30]]

    def test_a_file_backed_reader_still_saves_to_its_own_file_without_a_path(self, writable_reader, tmp_path):
        # regression guard: the save()-without-path guard must only trigger for
        # documents created via ODSReader.new(), not regular file-backed ones
        import shutil

        copy_path = tmp_path / "inplace.ods"
        shutil.copy(writable_reader.file, copy_path)
        r = ODSReader(copy_path)
        r.sheet("Sheet1")["A1"].value = "still works"
        r.save()  # no path given -> overwrites r.file (== copy_path), must NOT raise
        assert ODSReader(copy_path).sheet("Sheet1")["A1"].value == "still works"


class TestDeleteSheet:
    """`ODSReader.delete_sheet`."""

    def test_a_deleted_sheet_is_gone(self, writable_reader):
        r = writable_reader
        r.add_sheet("Extra")
        r.delete_sheet("Extra")
        assert "Extra" not in r.sheets_names
        with pytest.raises(KeyError):
            r.sheet("Extra")

    def test_an_unknown_sheet_raises(self, writable_reader):
        with pytest.raises(KeyError):
            writable_reader.delete_sheet("NoSuchSheet")

    def test_the_last_remaining_sheet_raises(self, writable_reader):
        r = writable_reader
        for name in list(r.sheets_names)[:-1]:
            r.delete_sheet(name)
        assert len(r.sheets_names) == 1
        with pytest.raises(ValueError):
            r.delete_sheet(r.sheets_names[0])

    def test_a_deleted_sheet_stays_deleted_after_a_save(self, writable_reader, tmp_path):
        r = writable_reader
        r.add_sheet("Extra")
        r.delete_sheet("Extra")
        out = tmp_path / "out.ods"
        r.save(out)
        reread = ODSReader(out)
        assert "Extra" not in reread.sheets_names


# ---------------------------------------------------------------------------
# ODSReader.rename_sheet / .move_sheet
# ---------------------------------------------------------------------------


class TestRenameSheet:
    """`ODSReader.rename_sheet`, and `Sheet.name` following it."""

    def test_the_sheet_names_follow(self, writable_reader):
        r = writable_reader
        r.rename_sheet("Sheet1", "Renamed")
        assert "Renamed" in r.sheets_names
        assert "Sheet1" not in r.sheets_names
        assert r.sheet("Renamed").name == "Renamed"

    def test_a_loaded_sheet_follows(self, writable_reader):
        r = writable_reader
        s = r.sheet("Sheet1")  # force construction before the rename
        r.rename_sheet("Sheet1", "Renamed")
        assert s.name == "Renamed"
        assert r.sheet("Renamed") is s

    def test_sheet_name_follows_every_renaming(self, writable_reader):
        # the name is read on the sheet's element, no copy to keep up to date
        r = writable_reader
        s = r.sheet("Sheet1")
        r.rename_sheet("Sheet1", "First")
        r.rename_sheet("First", "Second")
        assert s.name == s.table.get(qn("table:name")) == "Second"
        assert "name='Second'" in repr(s)
        assert r.sheet("Second") is s

    def test_assigning_sheet_name_raises_attribute_error_naming_rename_sheet(self, writable_reader):
        s = writable_reader.sheet("Sheet1")
        with pytest.raises(AttributeError, match=r"rename_sheet\('Sheet1', 'Other'\)"):
            s.name = "Other"
        assert s.name == "Sheet1"
        assert writable_reader.sheets_names[0] == "Sheet1"

    def test_cross_sheet_formula_references_follow(self, writable_reader):
        r = writable_reader
        s2 = r.sheet("Sheet2Repeat")
        s2["A1"].formula = "Sheet1.A2+Sheet1.A3"
        r.rename_sheet("Sheet1", "Renamed")
        assert s2["A1"].formula_friendly == "=Renamed.A2+Renamed.A3"

    def test_a_name_with_spaces_is_quoted_in_references(self, writable_reader):
        r = writable_reader
        s2 = r.sheet("Sheet2Repeat")
        s2["A1"].formula = "Sheet1.A2"
        r.rename_sheet("Sheet1", "Mon Bilan")
        assert s2["A1"].formula == "of:=['Mon Bilan'.A2]"

    def test_an_unqualified_reference_in_its_own_formulas_is_left_alone(self, writable_reader):
        r = writable_reader
        s1 = r.sheet("Sheet1")
        s1["C1"].formula = "A2+A3"
        r.rename_sheet("Sheet1", "Renamed")
        assert s1["C1"].formula_friendly == "=A2+A3"

    def test_an_unknown_sheet_raises(self, writable_reader):
        with pytest.raises(KeyError):
            writable_reader.rename_sheet("NoSuchSheet", "x")

    def test_a_name_in_use_raises(self, writable_reader):
        with pytest.raises(ValueError):
            writable_reader.rename_sheet("Sheet1", "Sheet2Repeat")

    def test_an_empty_name_raises(self, writable_reader):
        with pytest.raises(ValueError):
            writable_reader.rename_sheet("Sheet1", "")

    def test_its_own_name_is_a_no_op(self, writable_reader):
        r = writable_reader
        r.rename_sheet("Sheet1", "Sheet1")
        assert r.sheets_names.count("Sheet1") == 1

    def test_a_renamed_sheet_keeps_its_name_after_a_save(self, writable_reader, tmp_path):
        r = writable_reader
        s2 = r.sheet("Sheet2Repeat")
        s2["A1"].formula = "Sheet1.A2"
        r.rename_sheet("Sheet1", "Renamed")
        out = tmp_path / "out.ods"
        r.save(out)

        reread = ODSReader(out)
        assert "Renamed" in reread.sheets_names
        assert reread.sheet("Sheet2Repeat")["A1"].formula_friendly == "=Renamed.A2"
        assert reread.sheet("Renamed")["A1"].value == "texte simple"


class TestMoveSheet:
    """`ODSReader.move_sheet`."""

    def test_a_sheet_moved_first_reorders_the_others(self, writable_reader):
        r = writable_reader
        r.move_sheet("SheetFusion", 0)
        assert r.sheets_names == ["SheetFusion", "Sheet1", "Sheet2Repeat", "SheetEmpty"]

    def test_the_last_index_puts_it_at_the_end(self, writable_reader):
        r = writable_reader
        r.move_sheet("Sheet1", 3)
        assert r.sheets_names == ["Sheet2Repeat", "SheetEmpty", "SheetFusion", "Sheet1"]

    def test_an_inner_index_puts_it_there(self, writable_reader):
        r = writable_reader
        r.move_sheet("SheetFusion", 1)
        assert r.sheets_names == ["Sheet1", "SheetFusion", "Sheet2Repeat", "SheetEmpty"]

    def test_its_own_position_is_a_no_op(self, writable_reader):
        r = writable_reader
        before = list(r.sheets_names)
        r.move_sheet("Sheet2Repeat", 1)
        assert r.sheets_names == before

    def test_an_unknown_sheet_raises(self, writable_reader):
        with pytest.raises(KeyError):
            writable_reader.move_sheet("NoSuchSheet", 0)

    def test_an_index_out_of_range_raises(self, writable_reader):
        with pytest.raises(ValueError):
            writable_reader.move_sheet("Sheet1", 99)

    def test_a_moved_sheet_keeps_its_place_after_a_save(self, writable_reader, tmp_path):
        r = writable_reader
        r.move_sheet("SheetFusion", 0)
        out = tmp_path / "out.ods"
        r.save(out)

        reread = ODSReader(out)
        assert reread.sheets_names == ["SheetFusion", "Sheet1", "Sheet2Repeat", "SheetEmpty"]
        assert reread.sheet("Sheet1")["A1"].value == "texte simple"
