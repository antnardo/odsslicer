"""`ODSReader.save()`: writing the file, and what it leaves alone.

Two things are checked here. First, the write itself (issue #8): `save()` used
to truncate its target and write the new zip into it, so a program reading the
file meanwhile got a partial zip, and a save stopped midway left the workbook
unreadable - these tests stop a save at each of its steps, and read the file on
disk while the zip is being written. Second, that the parts `save()` copies
through really are copied, byte for byte, and only serialised once something
wrote to their tree.
"""

import os
import pathlib
import re
import stat
import zipfile

import pytest

from conftest import FIXTURES_DIR
from odsslicer import ODSReader


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
def test_interrupted_save_leaves_the_old_file_and_no_temporary_file(
    workbook, monkeypatch, step, error
):
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


def test_reader_during_a_save_gets_the_old_file_until_the_new_one_is_complete(
    workbook, monkeypatch
):
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


def test_save_to_another_path_leaves_the_source_and_gives_default_permissions(
    workbook, tmp_path
):
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


@pytest.mark.skipif(
    os.name == "nt", reason="symbolic links need a privilege on Windows"
)
def test_save_through_a_symlink_replaces_the_file_it_points_to(workbook, tmp_path):
    link = tmp_path / "link.ods"
    link.symlink_to(workbook)
    _edited(link).save()

    assert link.is_symlink()
    assert link.resolve() == workbook.resolve()
    assert ODSReader(workbook).sheet("Sheet1")["A1"].value == "saved"
    assert _names(tmp_path) == ["link.ods", "workbook.ods"]


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root may write any file"
)
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
# out as `'`, an empty element collapses to `<x/>`, and a whitespace-only text
# node is squeezed to a single space - which in `<number:text>   </number:text>`
# is a number format losing its padding, 34 such nodes over three of the eight
# fixtures below.
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
