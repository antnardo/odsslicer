# -*- coding: utf-8 -*-
"""Delegating formula recalculation and pivot refresh to a headless LibreOffice."""

import contextlib
import glob
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from .fileutils import _replacement

# ---------------------------------------------------------------------------
# LibreOffice integration (see `recalculate()` / `ODSReader.save(recalculate=True)`)
#
# odsslicer itself has no calculation engine: it writes formulas and pivot
# table *definitions* and leaves computing them to a real spreadsheet
# application. `recalculate()` delegates exactly that to a local LibreOffice,
# run headless. Override this list if `soffice` isn't on your PATH (or you
# want a specific build), e.g.:
#
#     import odsslicer
#     odsslicer.LIBREOFFICE_COMMAND[0] = "/opt/libreoffice/program/soffice"
#
# The first element is the executable; the rest are the flags every headless
# run gets. A throwaway user profile and the script URL are appended per call.
# ---------------------------------------------------------------------------
LIBREOFFICE_COMMAND = ["soffice", "--headless", "--norestore", "--nologo", "--nodefault"]

# Where to look for the executable when LIBREOFFICE_COMMAND[0] is a bare name
# that isn't on PATH - the usual install locations per platform.
_LIBREOFFICE_FALLBACKS = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]

# The script LibreOffice's own embedded Python runs (via the scripting
# framework, `vnd.sun.star.script:...?language=Python&location=user`) - no
# system-side python-uno needed. It gets its paths through the environment,
# since scripting-framework macros launched from the command line can't take
# arguments: the workbook, the file to save the result to, and one to report
# into.
#
# The result goes to a temporary file next to the workbook, which Python
# renames over it (issue #9): `doc.store()` saved to a temporary file of
# LibreOffice's own, then copied that over the workbook in place, where a
# reader could catch it half written. `storeToURL()` saves a copy and leaves
# the document at the URL it was loaded from, so that its links resolve and
# its folder is trusted as before - loading a copy of the workbook instead
# would also have `CELL("filename")` compute the copy's name. The filter is
# the one the workbook was loaded with, as `store()` would use.
#
# `soffice` exits with status 0 even when the script raises, and a result
# stopped midway is on disk all the same: the script reports "ok" once
# `storeToURL()` has returned, or else its traceback, and only a result
# reported complete replaces the workbook.
#
# With `update_links=True`, the script updates links to other files before
# recalculating, which takes three things:
# - a load that asks for it, UpdateDocMode = FULL_UPDATE: a hidden load that
#   doesn't gets NO_UPDATE, which no profile setting overrides;
# - the workbook's folder as a trusted location (SecureURL), without which
#   FULL_UPDATE falls back to asking - and headless, nobody answers. Trusting
#   the folders of the files the workbook reads is not enough, and the Low
#   macro security level would trust every location. The script trusts the
#   folder of the very URL it loads: LibreOffice compares the two as strings,
#   and leaves unescaped some characters that `Path.as_uri()` escapes
#   (' & + ( ) among them), so a folder URL written into the profile from
#   here would miss folders named with those;
# - a `.uno:UpdateTableLinks` dispatch, which reloads the other files where
#   LibreOffice would otherwise reuse the values from them it saved in the
#   workbook, stale or not.
# MacroExecutionMode = NEVER_EXECUTE is a hidden load's default already,
# spelled out because the folder is trusted for its links, not its macros.
_LIBREOFFICE_RECALC_SCRIPT = '''\
import os
import traceback

import uno
from com.sun.star.beans import PropertyValue


def trust_folder_of(ctx, url):
    provider = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.configuration.ConfigurationProvider", ctx
    )
    node = PropertyValue(
        Name="nodepath", Value="/org.openoffice.Office.Common/Security/Scripting"
    )
    scripting = provider.createInstanceWithArguments(
        "com.sun.star.configuration.ConfigurationUpdateAccess", (node,)
    )
    folder = url[: url.rindex("/") + 1]
    # typed: a bare tuple would go as []any, which the setting rejects
    secure_urls = uno.Any("[]string", (folder,))
    uno.invoke(scripting, "setPropertyValue", ("SecureURL", secure_urls))
    scripting.commitChanges()


def recalculate_into(path, output):
    update_links = os.environ.get("ODSSLICER_RECALC_UPDATE_LINKS") == "1"
    url = uno.systemPathToFileUrl(path)
    ctx = XSCRIPTCONTEXT.getComponentContext()
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    props = [PropertyValue(Name="Hidden", Value=True)]
    if update_links:
        trust_folder_of(ctx, url)
        props += [
            PropertyValue(Name="UpdateDocMode", Value=3),  # FULL_UPDATE
            PropertyValue(Name="MacroExecutionMode", Value=0),  # NEVER_EXECUTE
        ]
    doc = desktop.loadComponentFromURL(url, "_blank", 0, tuple(props))
    try:
        if update_links:
            helper = "com.sun.star.frame.DispatchHelper"
            dispatcher = ctx.ServiceManager.createInstanceWithContext(helper, ctx)
            frame = doc.getCurrentController().getFrame()
            dispatcher.executeDispatch(frame, ".uno:UpdateTableLinks", "", 0, ())
        doc.calculateAll()
        sheets = doc.getSheets()
        for i in range(sheets.getCount()):
            pilots = sheets.getByIndex(i).getDataPilotTables()
            for j in range(pilots.getCount()):
                pilots.getByIndex(j).refresh()
        loaded = {arg.Name: arg.Value for arg in doc.getArgs()}
        stored = (
            PropertyValue(Name="FilterName", Value=loaded.get("FilterName", "calc8")),
            PropertyValue(Name="Overwrite", Value=True),
        )
        doc.storeToURL(uno.systemPathToFileUrl(output), stored)
    finally:
        doc.close(True)


def recalculate(*args):
    try:
        recalculate_into(
            os.environ["ODSSLICER_RECALC_FILE"], os.environ["ODSSLICER_RECALC_OUTPUT"]
        )
        report = "ok"
    except Exception:
        report = traceback.format_exc()
    with open(os.environ["ODSSLICER_RECALC_REPORT"], "w", encoding="utf-8") as file:
        file.write(report)


g_exportedScripts = (recalculate,)
'''


def _find_libreoffice() -> str:
    """The LibreOffice executable to run: `LIBREOFFICE_COMMAND[0]` as-is if
    it's a path that exists or a name found on PATH, else the first usual
    install location that exists. Raises `FileNotFoundError` otherwise."""
    exe = LIBREOFFICE_COMMAND[0]
    if os.path.isabs(exe):
        # an explicit path is taken at its word - no silent fallback elsewhere
        if os.path.exists(exe):
            return exe
        raise FileNotFoundError(f"LibreOffice executable {exe!r} (odsslicer.LIBREOFFICE_COMMAND[0]) does not exist")
    found = shutil.which(exe)
    if found:
        return found
    for candidate in _LIBREOFFICE_FALLBACKS:
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        f"LibreOffice executable {exe!r} not found on PATH nor in the usual install "
        "locations - set odsslicer.LIBREOFFICE_COMMAND[0] to its full path"
    )


_SYSTEM_BIN_DIRS = ("/usr/local/bin", "/usr/bin", "/bin")


def _path_without_foreign_pythons(path_value: str) -> str:
    """`path_value` with every directory that ships a `python`/`python3`
    executable removed, and (on POSIX) the standard system directories
    guaranteed present at the end - so the only interpreter LibreOffice's
    prefix discovery can find is the system one its own build links
    against. See the environment note in `recalculate()`."""
    keep = [
        d
        for d in path_value.split(os.pathsep)
        if d
        and not glob.glob(os.path.join(d, "python3*"))
        and not glob.glob(os.path.join(d, "python.exe"))
    ]
    if os.name == "posix":
        keep.extend(d for d in _SYSTEM_BIN_DIRS if d not in keep and os.path.isdir(d))
    return os.pathsep.join(keep)


def _run(
    cmd: list[str], env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run(cmd, ...)`, except that on POSIX a timeout or an
    interruption kills LibreOffice itself, not only the process started.

    That process may only start LibreOffice: Homebrew's `soffice` is a shell
    script running the real one, and Linux's execs `oosplash`, which runs
    `soffice.bin`. `subprocess.run()` kills the process it started and no
    other, so LibreOffice carried on after a timeout, and rewrote the
    workbook seconds after `recalculate()` had raised (issue #9). Started in
    a session of its own, LibreOffice and its wrappers form a process group,
    killed as a whole.
    """
    with subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # ignored on Windows
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:  # the timeout, Ctrl-C
            if os.name == "posix":
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            raise
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


def _remove_locks(run: str, *documents: Path) -> None:
    """Delete the lock files that the LibreOffice whose profile folder is
    named `run` left next to `documents`.

    Killed with a document open, LibreOffice leaves its lock file behind,
    `.~lock.<name>#`, as if the document were still open. A lock file names
    the profile of the LibreOffice that wrote it, and `run` is random: a
    lock naming it is this run's, any other one someone else's.
    """
    for document in documents:
        lock = document.with_name(f".~lock.{document.name}#")
        with contextlib.suppress(OSError):
            if run in lock.read_text(encoding="utf-8", errors="replace"):
                lock.unlink()


def _signature(path: Path) -> tuple[int, int]:
    """What writing `path` changes: its modification time, which some file
    systems keep to the second or two only, and its size."""
    status = path.stat()
    return status.st_mtime_ns, status.st_size


def recalculate(
    path: "str | Path", timeout: int = 120, update_links: bool = False
) -> None:
    """Have a local LibreOffice open the `.ods` at `path`, recalculate every
    formula (`calculateAll()` - including ones whose cached value is stale),
    refresh every pivot table (materializing its output), and replace the
    file with the result.

    This is the one thing odsslicer deliberately doesn't do itself: it has no
    calculation engine (see the README), so it delegates to the real
    application. LibreOffice is run headless with a throwaway user profile in
    a temporary directory, so nothing touches your own LibreOffice profile;
    the script it executes is LibreOffice's own embedded Python (no
    system-side `python-uno` needed). Note that LibreOffice re-saves the
    whole file in its own serialization - exactly as if you'd opened it and
    hit Save - so expect it to grow and be normalized.

    LibreOffice saves its result to a temporary file in the same folder
    (`.<name>.<random>.tmp`), renamed over `path` once LibreOffice reports it
    complete, as `ODSReader.save()` does: a program reading the file
    meanwhile gets the old version or the new one, and a recalculation that
    fails, times out or is interrupted leaves the old version as it was,
    with no temporary file behind. The file keeps its permissions, a
    symbolic link is followed, and a read-only file raises `PermissionError`.

    A formula that reads another workbook (a reference to another file,
    written out or built by `INDIRECT()`) comes back as `Err:540`:
    LibreOffice reads other files only once link updates are allowed.
    `update_links=True` allows them, and updates every link before the
    recalculation, so that such formulas compute from the other files as
    they are now, not from values LibreOffice saved from them earlier. See
    "References to other workbooks" in DOCS.md.

    `update_links` is off by default because it lets the workbook decide
    what LibreOffice reads - any file it names, and any URL, through
    `WEBSERVICE()` or a reference to a remote file. That is exactly what
    LibreOffice's own check guards against: use it only on workbooks you
    trust. For that run, the throwaway profile trusts the workbook's folder,
    as LibreOffice updates links without asking only for a document in a
    trusted location, and the workbook's macros are never executed.

    Requires `soffice` on PATH (or `LIBREOFFICE_COMMAND[0]` set to its full
    path); raises `FileNotFoundError` if it can't be found, and
    `RuntimeError` if LibreOffice fails or times out (`timeout` seconds).
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    exe = _find_libreoffice()
    with (
        tempfile.TemporaryDirectory(prefix="odsslicer-lo-") as tmp,
        _replacement(path) as output,
    ):
        profile = Path(tmp) / "profile"
        report = Path(tmp) / "report"
        scripts = profile / "user" / "Scripts" / "python"
        scripts.mkdir(parents=True)
        (scripts / "odsslicer_recalc.py").write_text(_LIBREOFFICE_RECALC_SCRIPT, encoding="utf-8")
        base = [exe, *LIBREOFFICE_COMMAND[1:], f"-env:UserInstallation={profile.as_uri()}"]
        cmd = base + ["vnd.sun.star.script:odsslicer_recalc.py$recalculate?language=Python&location=user"]
        env = dict(
            os.environ,
            ODSSLICER_RECALC_FILE=str(path),
            ODSSLICER_RECALC_OUTPUT=str(output),
            ODSSLICER_RECALC_REPORT=str(report),
            # set either way, so that it is never inherited from the caller
            ODSSLICER_RECALC_UPDATE_LINKS="1" if update_links else "0",
        )
        # LibreOffice runs the script through its own Python, and the calling
        # process's Python environment must not leak into it: PYTHONPATH/
        # PYTHONHOME/LD_LIBRARY_PATH would poison the embedded interpreter,
        # and (verified on Ubuntu builds) that interpreter derives its prefix
        # by locating `python3` on PATH - a foreign interpreter first on PATH
        # (an active venv, GitHub's setup-python toolcache) makes it load an
        # incompatible stdlib and crash outright with std::bad_alloc.
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"):
            env.pop(var, None)
        env["PATH"] = _path_without_foreign_pythons(env.get("PATH", ""))
        unwritten = _signature(output)
        try:
            result = _run(cmd, env, timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"LibreOffice timed out after {timeout}s recalculating {path}") from e
        finally:
            _remove_locks(Path(tmp).name, path, output)
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice exited with {result.returncode} recalculating {path}:\n{result.stderr}"
            )
        outcome = None
        if report.exists():
            outcome = report.read_text(encoding="utf-8", errors="replace")
        if outcome not in (None, "ok"):
            raise RuntimeError(f"LibreOffice failed recalculating {path}:\n{outcome}")
        if outcome is None or _signature(output) == unwritten:
            # soffice returns 0 even when the script silently didn't run (e.g.
            # another instance already owns the profile) - the result not being
            # written, or not reported complete, is the reliable signal.
            raise RuntimeError(
                f"LibreOffice ran but did not rewrite {path} - the recalculation script "
                f"apparently didn't execute.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )


_recalculate_file = recalculate  # alias for ODSReader.save, whose `recalculate` parameter shadows the name
