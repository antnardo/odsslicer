# -*- coding: utf-8 -*-
"""Delegating formula recalculation and pivot refresh to a headless LibreOffice."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


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
# system-side python-uno needed. It gets the target file through the
# environment, since scripting-framework macros launched from the command
# line can't take arguments.
_LIBREOFFICE_RECALC_SCRIPT = '''\
import os
import uno
from com.sun.star.beans import PropertyValue


def recalculate(*args):
    path = os.environ["ODSSLICER_RECALC_FILE"]
    url = uno.systemPathToFileUrl(path)
    ctx = XSCRIPTCONTEXT.getComponentContext()
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    doc = desktop.loadComponentFromURL(url, "_blank", 0, (PropertyValue(Name="Hidden", Value=True),))
    try:
        doc.calculateAll()
        sheets = doc.getSheets()
        for i in range(sheets.getCount()):
            pilots = sheets.getByIndex(i).getDataPilotTables()
            for j in range(pilots.getCount()):
                pilots.getByIndex(j).refresh()
        doc.store()
    finally:
        doc.close(True)


g_exportedScripts = (recalculate,)
'''


def _find_libreoffice():
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


def recalculate(path, timeout=120):
    """Have a local LibreOffice open the `.ods` at `path`, recalculate every
    formula (`calculateAll()` - including ones whose cached value is stale),
    refresh every pivot table (materializing its output), and save the file
    back in place.

    This is the one thing odsslicer deliberately doesn't do itself: it has no
    calculation engine (see the README), so it delegates to the real
    application. LibreOffice is run headless with a throwaway user profile in
    a temporary directory, so nothing touches your own LibreOffice profile;
    the script it executes is LibreOffice's own embedded Python (no
    system-side `python-uno` needed). Note that LibreOffice re-saves the
    whole file in its own serialization - exactly as if you'd opened it and
    hit Save - so expect it to grow and be normalized.

    Requires `soffice` on PATH (or `LIBREOFFICE_COMMAND[0]` set to its full
    path); raises `FileNotFoundError` if it can't be found, and
    `RuntimeError` if LibreOffice fails or times out (`timeout` seconds).
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    exe = _find_libreoffice()
    with tempfile.TemporaryDirectory(prefix="odsslicer-lo-") as tmp:
        profile = Path(tmp) / "profile"
        scripts = profile / "user" / "Scripts" / "python"
        scripts.mkdir(parents=True)
        (scripts / "odsslicer_recalc.py").write_text(_LIBREOFFICE_RECALC_SCRIPT, encoding="utf-8")
        cmd = [
            exe,
            *LIBREOFFICE_COMMAND[1:],
            f"-env:UserInstallation={profile.as_uri()}",
            "vnd.sun.star.script:odsslicer_recalc.py$recalculate?language=Python&location=user",
        ]
        env = dict(os.environ, ODSSLICER_RECALC_FILE=str(path))
        mtime_before = path.stat().st_mtime_ns
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"LibreOffice timed out after {timeout}s recalculating {path}") from e
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice exited with {result.returncode} recalculating {path}:\n{result.stderr}"
            )
        if path.stat().st_mtime_ns == mtime_before:
            # soffice returns 0 even when the script silently didn't run (e.g.
            # another instance already owns the profile) - the file not being
            # rewritten is the reliable signal that nothing happened.
            raise RuntimeError(
                f"LibreOffice ran but did not rewrite {path} - the recalculation script "
                f"apparently didn't execute.\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )


_recalculate_file = recalculate  # alias for ODSReader.save, whose `recalculate` parameter shadows the name
