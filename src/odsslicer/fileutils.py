"""Replacing a file in one step: its new content goes to a temporary file next
to it, renamed over it once complete.

Writing a new version straight into a file truncates it first: a program
reading it meanwhile gets a partial file, and a write stopped midway leaves it
unreadable. `ODSReader.save()` did (issue #8), and so did LibreOffice's own
save in `recalculate()`, which copies its result over the file (issue #9). The
content goes to a temporary file instead, in the same folder since
`os.replace()` is atomic only within one file system, and reaches the disk
before the rename - otherwise a power cut could keep the rename but lose the
data. `save()` writes that file itself, `recalculate()` has LibreOffice write
it: the temporary file is handed out by name, and nobody holds it open while
another program writes it.
"""

import contextlib
import errno
import os
import secrets
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

__all__: list[str] = []


def _open_sibling(target: Path, mode: int) -> tuple[int, Path]:
    """A new, empty file next to `target`, open for writing: its descriptor
    and its path, hidden (`.<name>.<random>.tmp`) and matching no `*.ods`."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    while True:
        path = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        try:
            return os.open(path, flags, mode), path
        except FileExistsError:
            continue


def _sync_folder(folder: Path) -> None:
    """Have `folder`'s entries reach the disk, so that a rename in it
    survives a power cut - on POSIX only: Windows cannot open a folder."""
    if os.name != "posix":
        return
    with contextlib.suppress(OSError):  # some network file systems refuse
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextlib.contextmanager
def _replacement(path: Path) -> Iterator[Path]:
    """A new, empty file next to `path`, for its new content - written by
    this process or another one: it replaces `path` in one step when the
    `with` block ends, or is deleted, leaving `path` as it was, if the block
    raises."""
    target = path.resolve()  # replace what a symlink points to, not the link
    exists = target.exists()
    if exists and not os.access(target, os.W_OK):
        # renaming needs only the folder to be writable: without this, a
        # read-only file would be replaced where writing into it failed
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
    # tempfile creates its files 0o600, which would make every new workbook
    # private: a new file gets 0o666 less the umask, as open() gives it, and
    # one replacing a file stays private until it takes that file's mode
    fd, tmp = _open_sibling(target, 0o600 if exists else 0o666)
    os.close(fd)
    try:
        yield tmp
        with tmp.open("rb+") as file:  # Windows flushes no read-only handle
            os.fsync(file.fileno())
        if exists:
            shutil.copymode(target, tmp)
        tmp.replace(target)
    except BaseException:  # Ctrl-C included: leave no temporary file behind
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _sync_folder(target.parent)


@contextlib.contextmanager
def _replacing(path: Path) -> Iterator[BinaryIO]:
    """`_replacement(path)`, open for writing."""
    with _replacement(path) as tmp, tmp.open("wb") as file:
        yield file
