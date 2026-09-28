"""Locks the kernel holds, shared by the scheduler tick and the loop.

Both used to be pid lockfiles, and neither excluded. A pid lockfile decides
from outside whether its holder is gone, and it decides from proxies: a file
missing between two looks, a file that reads as unusable, `kill(pid, 0)`,
which succeeds on a zombie and on an unrelated process that was given the
same number. Measured: the tick lock admitted 618–665 overlapping holders per
20 s on Linux, and the loop lock about 51,000 on macOS. A zombie or a live
`sleep` whose pid sat in the loop's file wedged the project.

So both are now `flock(LOCK_EX | LOCK_NB)` on a file that is never deleted.
The kernel holds the lock while the holder's open file description exists and
drops it when the process exits, however it exits. Nothing here decides
whether a holder is gone.

`flock`, not `fcntl`/`lockf`: POSIX record locks belong to the PROCESS and are
dropped when it closes ANY descriptor for the file, so one stray `open`/`close`
would silently release them. `flock` locks belong to the open file
description, on Linux and on macOS alike, so two `open`s conflict even inside
one process, and closing one never releases the other.

What each primitive here refuses, it refuses by saying why. A lock that
cannot be shown to exclude is not taken (`exclusion_problem`).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# A record is a few dozen bytes. Anything longer is not one of ours.
RECORD_MAX_BYTES = 4096


class GateUnavailable(Exception):
    """The gate could not be taken, so nothing behind it may be decided."""


def open_lock_file(path: Path) -> int:
    """Open (creating) a lock file. Never follows a symlink, never inherited.

    O_NOFOLLOW: a symlink planted at the path would move the lock onto a file
    somebody else chose. O_CLOEXEC: a subprocess the holder starts must not
    inherit the descriptor and keep the lock alive after the holder is gone.
    """
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o644)


def same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def refused(fd: int) -> bool:
    """Try `fd` for the lock without waiting. True: refused, someone holds
    it. False: granted. Any other error is raised, never read as either."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    return False


def flock_failed(path: Path, e: OSError) -> str:
    if e.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK):
        return (
            f"the filesystem holding {path} does not support flock ({e}). "
            f"Keep the project on a local disk"
        )
    return f"flock on {path} failed: {e}"


def exclusion_problem(path: Path, fd: int) -> str:
    """Why holding `fd`'s lock does not exclude anyone else, or "".

    1. The path must still name the file we locked. Only something outside
       rite deletes or replaces these files, and if it has, the next opener
       would lock a different file.
    2. The kernel must refuse a second lock on it. Asked on a second open
       file description of the same file, which conflicts with ours even in
       one process, so a grant means `flock` does not exclude on this
       filesystem at all.
    """
    try:
        held = os.fstat(fd)
        if not same_file(held, os.stat(path, follow_symlinks=False)):
            return (
                f"the lock file {path} was replaced while it was being taken, "
                f"so holding it would not keep anyone out. Something other "
                f"than rite is deleting or replacing it"
            )
        probe = open_lock_file(path)
    except OSError as e:
        return f"could not check the lock file {path}: {e}"
    try:
        if not same_file(held, os.fstat(probe)):
            return (
                f"the lock file {path} was replaced while it was being taken. "
                f"Something other than rite is deleting or replacing it"
            )
        if not refused(probe):
            fcntl.flock(probe, fcntl.LOCK_UN)
            return (
                f"flock does not exclude on the filesystem holding {path}: a "
                f"second lock on a held file was granted. This happens on some "
                f"network and VM-shared filesystems. Keep the project on a "
                f"local disk"
            )
        return ""
    except OSError as e:
        return flock_failed(path, e)
    finally:
        os.close(probe)


def read_record(fd: int) -> tuple[int, float] | None:
    """The holder's `(pid, acquired_at)` from the lock file, or None."""
    try:
        data = json.loads(os.pread(fd, RECORD_MAX_BYTES, 0))
        return int(data["pid"]), float(data["acquired_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_record(fd: int) -> None:
    payload = json.dumps({"pid": os.getpid(), "acquired_at": time.time()}) + "\n"
    os.ftruncate(fd, 0)
    os.pwrite(fd, payload.encode(), 0)


@contextmanager
def gate(path: Path, wait_seconds: float) -> Iterator[None]:
    """Hold `path` exclusively for a few system calls, waiting up to
    `wait_seconds` for it.

    A gate serialises everyone who LOOKS AT or TAKES a long-held lock, so a
    look can never collide with a take (see `rite_ai.loop.lock`). Whoever
    holds it does a handful of system calls and lets go, so a wait longer
    than `wait_seconds` means a holder that has stopped, a process suspended
    with Ctrl-Z say. That raises `GateUnavailable` rather than waiting for
    ever or proceeding without it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = open_lock_file(path)
    except OSError as e:
        raise GateUnavailable(f"cannot open {path}: {e}") from e
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                if not refused(fd):
                    break
            except OSError as e:
                raise GateUnavailable(flock_failed(path, e)) from e
            if time.monotonic() >= deadline:
                raise GateUnavailable(
                    f"{path} has been held for over {wait_seconds:g}s by a "
                    f"process that should hold it for microseconds. It may be "
                    f"stopped (Ctrl-Z) or hung"
                )
            time.sleep(0.001)
        problem = exclusion_problem(path, fd)
        if problem:
            raise GateUnavailable(problem)
        yield
    finally:
        os.close(fd)
