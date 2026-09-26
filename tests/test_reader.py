"""`ODSReader.save()` through a temporary file and a rename (issue #8).

`save()` used to truncate its target and write the new zip into it: a program
reading the file meanwhile got a partial zip, and a save stopped midway left the
workbook unreadable. These tests stop a save at each of its steps, and read the
file on disk while the zip is being written.
"""

import os
import pathlib
import stat
import zipfile

import pytest

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
