"""`recalculate()` through a temporary file and a rename (issue #9).

LibreOffice saved the recalculated workbook by copying its result over it in
place: a program reading the file meanwhile could get a partial zip. And a
timeout killed only the process started, often a wrapper script, while
LibreOffice carried on and rewrote the workbook after `recalculate()` had
raised. These tests read the workbook while LibreOffice saves it, and stop
LibreOffice while it has the workbook open. Shell scripts standing in for
LibreOffice, which need no LibreOffice, stop it while it saves, or have it save
nothing.
"""

import contextlib
import os
import shlex
import signal
import stat
import subprocess
import threading
import time
import zipfile

import pytest

from conftest import requires_soffice
from odsslicer import ODSReader, libreoffice, recalculate

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="shell scripts, and a process group killed as a whole"
)


def _names(folder):
    return sorted(path.name for path in folder.iterdir())


def _stops(pid):
    """Whether process `pid` ends within five seconds - dead, it can linger
    as a zombie until its parent reaps it."""
    for _ in range(50):
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture()
def fake_libreoffice(tmp_path_factory, monkeypatch):
    """Stand in for LibreOffice with the shell script `body`, run the way
    Homebrew's `soffice` runs the real LibreOffice: as the child of a
    wrapper script. It gets the environment the recalculation script would.
    """
    folder = tmp_path_factory.mktemp("libreoffice")

    def install(body):
        script = folder / "libreoffice.sh"
        script.write_text(body)
        wrapper = folder / "soffice"
        wrapper.write_text(f'#!/bin/sh\n/bin/sh {shlex.quote(str(script))} "$@"\n')
        wrapper.chmod(0o755)
        command = [str(wrapper), *libreoffice.LIBREOFFICE_COMMAND[1:]]
        monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", command)

    return install


@requires_soffice
def test_recalculate_replaces_the_workbook_through_a_rename(workbook):
    workbook.chmod(0o640)
    before = workbook.stat()
    recalculate(workbook)

    after = workbook.stat()
    assert after.st_ino != before.st_ino
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert _names(workbook.parent) == ["workbook.ods"]


@requires_soffice
def test_reader_during_a_recalculation_gets_the_old_file_or_the_new_one(workbook):
    done = threading.Event()
    reads = []
    failures = []

    def read():
        while not done.is_set():
            try:
                with zipfile.ZipFile(workbook) as package:
                    package.read("content.xml")
            except Exception as error:  # BadZipFile, while the file is partial
                failures.append(error)
            reads.append(None)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        recalculate(workbook)
    finally:
        done.set()
        reader.join()

    assert reads
    assert failures == []


@requires_soffice
def test_recalculate_computes_the_workbook_s_own_name(tmp_path):
    # LibreOffice loads the workbook itself: a copy, recalculated then renamed
    # over it, would have CELL("filename") give the copy's name
    path = tmp_path / "workbook.ods"
    table = ODSReader.new()
    table.sheet("Sheet1")["A1"].formula = 'CELL("filename")'
    table.save(path)
    recalculate(path)

    name = ODSReader(path).sheet("Sheet1")["A1"].value
    assert name.endswith("/workbook.ods'#$Sheet1")


@requires_soffice
def test_recalculate_keeps_the_workbook_s_format(workbook, libreoffice_export):
    # as store() did: a Flat ODF workbook stays plain XML, not a zip
    flat = libreoffice_export(workbook, "fods")
    recalculate(flat)

    assert flat.read_bytes().startswith(b"<?xml")
    assert _names(flat.parent) == ["workbook.fods", "workbook.ods"]


@requires_soffice
def test_script_failing_in_libreoffice_raises_its_traceback(workbook, monkeypatch):
    script = libreoffice._LIBREOFFICE_RECALC_SCRIPT.replace(
        "        doc.calculateAll()\n",
        "        raise ValueError('no recalculation today')\n",
    )
    monkeypatch.setattr(libreoffice, "_LIBREOFFICE_RECALC_SCRIPT", script)
    before = workbook.read_bytes()
    with pytest.raises(RuntimeError, match="ValueError: no recalculation today"):
        recalculate(workbook)

    assert workbook.read_bytes() == before
    assert _names(workbook.parent) == ["workbook.ods"]


@requires_soffice
@posix_only
def test_interrupted_recalculation_stops_libreoffice_and_leaves_no_file_behind(
    workbook, tmp_path_factory, monkeypatch
):
    # once LibreOffice has the workbook open - its lock file next to it - the
    # script gives its process id, and waits
    loaded = tmp_path_factory.mktemp("loaded") / "pid"
    script = libreoffice._LIBREOFFICE_RECALC_SCRIPT.replace(
        "        doc.calculateAll()\n",
        "        with open(os.environ['ODSSLICER_TEST_LOADED'], 'w') as loaded:\n"
        "            loaded.write(str(os.getpid()))\n"
        "        __import__('time').sleep(60)\n"
        "        doc.calculateAll()\n",
    )
    monkeypatch.setattr(libreoffice, "_LIBREOFFICE_RECALC_SCRIPT", script)
    monkeypatch.setenv("ODSSLICER_TEST_LOADED", str(loaded))
    main = threading.main_thread().ident
    stop = threading.Event()

    def interrupt_once_loaded():
        # a signal sent to the process could reach another thread, and leave
        # the main one blocked on LibreOffice's output
        while not stop.wait(0.05):
            if loaded.exists() and loaded.read_text():
                signal.pthread_kill(main, signal.SIGINT)
                return

    before = workbook.read_bytes()
    interrupter = threading.Thread(target=interrupt_once_loaded)
    interrupter.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            recalculate(workbook)
    finally:
        stop.set()
        interrupter.join()

    pid = int(loaded.read_text())
    try:
        assert _names(workbook.parent) == ["workbook.ods"]  # no lock file either
        assert workbook.read_bytes() == before
        assert _stops(pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


@posix_only
def test_timeout_stops_libreoffice_at_once(
    workbook, fake_libreoffice, tmp_path_factory
):
    # still busy at the timeout, then saving where LibreOffice saves: into
    # the file it is given, or into the workbook itself before issue #9
    pid = tmp_path_factory.mktemp("pid") / "pid"
    fake_libreoffice(
        f"echo $$ > {shlex.quote(str(pid))}\nsleep 10\n"
        'printf x >> "${ODSSLICER_RECALC_OUTPUT:-$ODSSLICER_RECALC_FILE}"\n'
    )
    before = workbook.read_bytes()
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="timed out"):
        recalculate(workbook, timeout=1)

    try:
        assert time.monotonic() - start < 5
        assert _stops(int(pid.read_text()))
        assert workbook.read_bytes() == before
        assert _names(workbook.parent) == ["workbook.ods"]
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid.read_text()), signal.SIGKILL)


@posix_only
def test_failed_run_removes_the_lock_files_libreoffice_left(workbook, fake_libreoffice):
    # LibreOffice dying with the workbook open leaves its lock file, which
    # names its profile
    fake_libreoffice(
        "for arg; do case $arg in -env:UserInstallation=*) "
        "profile=${arg#-env:UserInstallation=};; esac; done\n"
        "printf ',me,here,today,%s;' \"$profile\" "
        '> "${ODSSLICER_RECALC_FILE%/*}/.~lock.${ODSSLICER_RECALC_FILE##*/}#"\n'
        "exit 1\n"
    )
    with pytest.raises(RuntimeError, match="exited with 1"):
        recalculate(workbook)

    assert _names(workbook.parent) == ["workbook.ods"]


@posix_only
def test_failed_run_keeps_the_lock_file_of_another_libreoffice(
    workbook, fake_libreoffice
):
    fake_libreoffice("exit 1\n")
    lock = workbook.with_name(".~lock.workbook.ods#")
    theirs = ",Someone,elsewhere,26.09.2026 23:45,file:///home/someone/.config/libreoffice/4;"
    lock.write_text(theirs)
    with pytest.raises(RuntimeError, match="exited with 1"):
        recalculate(workbook)

    assert lock.read_text() == theirs


@posix_only
def test_libreoffice_stopping_while_saving_leaves_the_workbook_as_it_was(
    workbook, fake_libreoffice
):
    # the first bytes of a zip, and no report: soffice exits with status 0
    # even when the script fails midway
    fake_libreoffice(
        r"""printf 'PK\003\004' > "${ODSSLICER_RECALC_OUTPUT:-$ODSSLICER_RECALC_FILE}"
"""
    )
    before = workbook.read_bytes()
    with pytest.raises(RuntimeError, match="did not rewrite"):
        recalculate(workbook)

    assert workbook.read_bytes() == before
    assert _names(workbook.parent) == ["workbook.ods"]


@posix_only
@pytest.mark.parametrize(
    "body",
    ["exit 0\n", 'printf ok > "$ODSSLICER_RECALC_REPORT"\n'],
    ids=["script-not-run", "reported-without-saving"],
)
def test_libreoffice_saving_nothing_raises_and_leaves_the_workbook_as_it_was(
    workbook, fake_libreoffice, body
):
    fake_libreoffice(body)
    before = workbook.read_bytes()
    with pytest.raises(RuntimeError, match="did not rewrite"):
        recalculate(workbook)

    assert workbook.read_bytes() == before
    assert _names(workbook.parent) == ["workbook.ods"]


@posix_only
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root may write any file"
)
def test_read_only_workbook_raises_permission_error_before_libreoffice_starts(
    workbook, fake_libreoffice, tmp_path_factory
):
    started = tmp_path_factory.mktemp("started") / "started"
    fake_libreoffice(f": > {shlex.quote(str(started))}\n")
    before = workbook.read_bytes()
    workbook.chmod(0o444)
    with pytest.raises(PermissionError):
        recalculate(workbook)

    assert not started.exists()
    assert workbook.read_bytes() == before
    assert _names(workbook.parent) == ["workbook.ods"]
