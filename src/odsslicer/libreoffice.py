# -*- coding: utf-8 -*-
"""Delegating formula recalculation and pivot refresh to a headless LibreOffice."""

import glob
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


def recalculate(*args):
    path = os.environ["ODSSLICER_RECALC_FILE"]
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
        doc.store()
    finally:
        doc.close(True)


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


def recalculate(
    path: "str | Path", timeout: int = 120, update_links: bool = False
) -> None:
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
    with tempfile.TemporaryDirectory(prefix="odsslicer-lo-") as tmp:
        profile = Path(tmp) / "profile"
        scripts = profile / "user" / "Scripts" / "python"
        scripts.mkdir(parents=True)
        (scripts / "odsslicer_recalc.py").write_text(_LIBREOFFICE_RECALC_SCRIPT, encoding="utf-8")
        base = [exe, *LIBREOFFICE_COMMAND[1:], f"-env:UserInstallation={profile.as_uri()}"]
        cmd = base + ["vnd.sun.star.script:odsslicer_recalc.py$recalculate?language=Python&location=user"]
        env = dict(
            os.environ,
            ODSSLICER_RECALC_FILE=str(path),
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
