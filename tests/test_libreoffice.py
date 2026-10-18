"""Running LibreOffice: `recalculate()` and the public functions it starts
LibreOffice with; the tests that need a real LibreOffice are in
test_libreoffice_consistency.py.

`recalculate()` goes through a temporary file and a rename (issue #9).

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
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pytest

from conftest import requires_soffice
from odsslicer import (
    ODSReader,
    find_libreoffice,
    libreoffice,
    libreoffice_command,
    libreoffice_environment,
    recalculate,
)

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
def test_timeout_stops_libreoffice_at_once(workbook, fake_libreoffice, tmp_path_factory):
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
def test_failed_run_keeps_the_lock_file_of_another_libreoffice(workbook, fake_libreoffice):
    fake_libreoffice("exit 1\n")
    lock = workbook.with_name(".~lock.workbook.ods#")
    theirs = ",Someone,elsewhere,26.09.2026 23:45,file:///home/someone/.config/libreoffice/4;"
    lock.write_text(theirs)
    with pytest.raises(RuntimeError, match="exited with 1"):
        recalculate(workbook)

    assert lock.read_text() == theirs


@posix_only
def test_libreoffice_stopping_while_saving_leaves_the_workbook_as_it_was(workbook, fake_libreoffice):
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
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root may write any file")
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


# ---------------------------------------------------------------------------
# Running LibreOffice for something else: find_libreoffice,
# libreoffice_command, libreoffice_environment (public since 1.0)
# ---------------------------------------------------------------------------


def _executable(folder, name="soffice"):
    folder.mkdir(parents=True, exist_ok=True)
    program = folder / name
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    return program


class TestFindLibreoffice:
    def test_absolute_path_that_exists_is_returned_as_is(self, tmp_path, monkeypatch):
        program = _executable(tmp_path / "bin")
        monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", [str(program), "--headless"])
        assert find_libreoffice() == str(program)

    def test_absolute_path_that_does_not_exist_raises_rather_than_fall_back(self, tmp_path, monkeypatch):
        fallback = _executable(tmp_path / "fallback")
        monkeypatch.setattr(libreoffice, "_LIBREOFFICE_FALLBACKS", [str(fallback)])
        monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", [str(tmp_path / "missing" / "soffice")])
        with pytest.raises(FileNotFoundError, match="does not exist"):
            find_libreoffice()

    @posix_only
    def test_bare_name_is_looked_up_on_path(self, tmp_path, monkeypatch):
        program = _executable(tmp_path / "bin", "my-soffice")
        monkeypatch.setenv("PATH", str(program.parent))
        monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", ["my-soffice"])
        assert find_libreoffice() == str(program)

    def test_bare_name_off_path_falls_back_to_the_usual_locations(self, tmp_path, monkeypatch):
        fallback = _executable(tmp_path / "fallback")
        monkeypatch.setattr(libreoffice, "_LIBREOFFICE_FALLBACKS", [str(tmp_path / "none"), str(fallback)])
        monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", ["definitely-not-a-real-binary-name"])
        assert find_libreoffice() == str(fallback)

    def test_nothing_found_raises_file_not_found(self, monkeypatch):
        monkeypatch.setattr(libreoffice, "_LIBREOFFICE_FALLBACKS", [])
        monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", ["definitely-not-a-real-binary-name"])
        with pytest.raises(FileNotFoundError, match="LIBREOFFICE_COMMAND"):
            find_libreoffice()


def test_libreoffice_command_starts_headless_on_the_profile_given(tmp_path, monkeypatch):
    program = _executable(tmp_path / "bin")
    monkeypatch.setattr(libreoffice, "LIBREOFFICE_COMMAND", [str(program), "--headless", "--norestore"])
    monkeypatch.chdir(tmp_path)
    command = libreoffice_command("profile")  # a relative folder, made absolute
    assert command == [
        str(program),
        "--headless",
        "--norestore",
        f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
    ]


class TestLibreofficeEnvironment:
    def _environ(self, tmp_path):
        venv = _executable(tmp_path / "venv" / "bin", "python3").parent
        plain = _executable(tmp_path / "tools", "tool").parent
        environ = {
            "PATH": os.pathsep.join([str(venv), str(plain)]),
            "PYTHONPATH": "/somewhere",
            "PYTHONHOME": "/elsewhere",
            "LD_LIBRARY_PATH": "/lib/here",
            "DYLD_LIBRARY_PATH": "/lib/there",
            "LANG": "fr_FR.UTF-8",
        }
        return environ, venv, plain

    def test_python_variables_are_removed_and_the_rest_kept(self, tmp_path):
        environ, _, _ = self._environ(tmp_path)
        env = libreoffice_environment(environ)
        assert {"PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}.isdisjoint(env)
        assert env["LANG"] == "fr_FR.UTF-8"
        assert environ["PYTHONPATH"] == "/somewhere"  # a copy: the mapping given is left as it was

    def test_path_leaves_out_folders_holding_a_python(self, tmp_path):
        environ, venv, plain = self._environ(tmp_path)
        folders = libreoffice_environment(environ)["PATH"].split(os.pathsep)
        assert str(venv) not in folders
        assert folders[0] == str(plain)

    @posix_only
    def test_path_ends_with_the_system_folders(self, tmp_path):
        environ, _, _ = self._environ(tmp_path)
        folders = libreoffice_environment(environ)["PATH"].split(os.pathsep)
        system = [d for d in ("/usr/local/bin", "/usr/bin", "/bin") if Path(d).is_dir()]
        assert folders[-len(system) :] == system

    def test_default_is_a_copy_of_the_process_environment(self, monkeypatch):
        monkeypatch.setenv("ODSSLICER_TEST_VARIABLE", "kept")
        monkeypatch.setenv("PYTHONPATH", "/somewhere")
        env = libreoffice_environment()
        assert env["ODSSLICER_TEST_VARIABLE"] == "kept"
        assert "PYTHONPATH" not in env
        assert os.environ["PYTHONPATH"] == "/somewhere"


@requires_soffice
def test_public_api_runs_libreoffice_for_a_conversion(workbook, tmp_path):
    # DOCS.md, section 7, "Running LibreOffice yourself", as written there
    out = tmp_path / "out"
    with tempfile.TemporaryDirectory() as tmp:
        command = [
            *libreoffice_command(Path(tmp) / "profile"),
            "--convert-to",
            "csv",
            "--outdir",
            str(out),
            str(workbook),
        ]
        subprocess.run(command, env=libreoffice_environment(), check=True, capture_output=True, timeout=120)
    assert (out / f"{workbook.stem}.csv").read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# recalculate(): error paths that don't need LibreOffice installed
# ---------------------------------------------------------------------------


def test_recalculate_missing_file_raises(tmp_path):
    from odsslicer import recalculate

    with pytest.raises(FileNotFoundError):
        recalculate(tmp_path / "does_not_exist.ods")


def test_recalculate_explicit_nonexistent_executable_raises(writable_reader, tmp_path):
    # an explicit absolute path that doesn't exist must error out, not fall
    # back silently to whatever default install happens to be around
    import odsslicer
    from odsslicer import recalculate

    out = tmp_path / "out.ods"
    writable_reader.save(out)
    saved = odsslicer.LIBREOFFICE_COMMAND[0]
    odsslicer.LIBREOFFICE_COMMAND[0] = str(tmp_path / "no" / "such" / "soffice")
    try:
        with pytest.raises(FileNotFoundError):
            recalculate(out)
    finally:
        odsslicer.LIBREOFFICE_COMMAND[0] = saved


def test_recalculate_bare_name_not_found_anywhere_raises(writable_reader, tmp_path, monkeypatch):
    import odsslicer
    from odsslicer import libreoffice, recalculate

    out = tmp_path / "out.ods"
    writable_reader.save(out)
    monkeypatch.setattr(libreoffice, "_LIBREOFFICE_FALLBACKS", [])
    saved = odsslicer.LIBREOFFICE_COMMAND[0]
    odsslicer.LIBREOFFICE_COMMAND[0] = "definitely-not-a-real-binary-name"
    try:
        with pytest.raises(FileNotFoundError):
            recalculate(out)
    finally:
        odsslicer.LIBREOFFICE_COMMAND[0] = saved


def test_save_with_update_links_but_no_recalculate_raises(writable_reader, tmp_path):
    # only the LibreOffice run updates links: asking for it without one must
    # not pass silently, nor write the file first
    out = tmp_path / "out.ods"
    with pytest.raises(ValueError, match="recalculate=True"):
        writable_reader.save(out, update_links=True)
    assert not out.exists()
