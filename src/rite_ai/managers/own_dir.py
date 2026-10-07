"""Reading a Manager's own request directories from the HOST, following no
link the Manager planted (SCRUM-59 review).

The supervisor reads, renames, removes and writes files in a Manager's
`requests/`, `deliveries/`, `chores/` and `lifecycle/`, outside every
boundary. The Manager can write inside its own directory, so it can replace
any of those with a link (measured: it did so from inside its seatbelt
profile). Through a path, the supervisor would then rename and delete
`*.json` files wherever the link pointed. Everything here goes through one
descriptor, opened `O_NOFOLLOW` at the Manager's own directory (which its
profile no longer lets it replace, SCRUM-69) and at the subdirectory; every
entry is opened `O_NOFOLLOW` and read only if it is a regular file.

A directory that is a link raises `OSError` (ELOOP, or ENOTDIR on Linux) for
the caller to SAY: a Manager that did that is confused or compromised.
"""

from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_dir(root: Path, manager: str, dirname: str) -> int | None:
    """The Manager's `dirname` (`a` or `a/b`), opened as described above, no
    link followed at any component; None when it (or the Manager's
    directory) does not exist yet."""
    from rite_ai.managers import manager_dir

    try:
        fd = os.open(manager_dir(Path(root), manager), _DIR_FLAGS)
    except FileNotFoundError:
        return None
    for part in Path(dirname).parts:
        try:
            nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
        fd = nxt
    return fd


@contextmanager
def opened(root: Path, manager: str, dirname: str):
    fd = _open_dir(root, manager, dirname)
    try:
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


def names(root: Path, manager: str, dirname: str, suffix: str) -> list[str]:
    """Entries ending `suffix`, oldest first by name. Raises OSError for a
    directory that is a link, like `take`, for the caller to say."""
    with opened(root, manager, dirname) as fd:
        return [] if fd is None else _names(fd, suffix)


def _names(fd: int, suffix: str) -> list[str]:
    return sorted(n for n in os.listdir(fd) if n.endswith(suffix))


REFUSED = ".refused"
"""Suffix an entry is renamed to when it is not a regular file (a link, a
directory, a fifo): out of every `*.json` glob, so it is not met again, and
kept, not deleted, for a person to look at."""


class NotARegularFile(OSError):
    """An entry that is a link, a directory or anything but a regular file."""


def _read(fd: int, name: str, limit: int) -> str:
    """A regular file's text, at most `limit` + 1 bytes (so a caller's size
    check still sees it is too large). Raises `NotARegularFile` for anything
    else, a link included (never followed)."""
    try:
        leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO):
            raise NotARegularFile(e.errno, f"{name} is not a regular file") from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(leaf).st_mode):
            raise NotARegularFile(errno.EINVAL, f"{name} is not a regular file")
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            chunk = os.read(leaf, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks).decode("utf-8", "replace")
    finally:
        os.close(leaf)


def read(root: Path, manager: str, dirname: str, name: str, limit: int) -> str:
    """A regular file's text; raises OSError (`NotARegularFile` for a link)."""
    with opened(root, manager, dirname) as fd:
        if fd is None:
            raise FileNotFoundError(name)
        return _read(fd, name, limit)


def take(
    root: Path, manager: str, dirname: str, limit: int, claim: str = ""
) -> list[tuple[str, str]]:
    """Every `*.json`, oldest first: `(name, text)`.

    With `claim`, each is first RENAMED to `<name><claim>` (only one taker
    wins a rename) and the claimed name is returned; the caller removes it
    when done. Without, each is removed as it is read.

    An entry that is not a regular file (a planted link, a directory) is
    renamed to `<name>.refused`, never followed, and returned with text ""
    so the caller's refusal ("not JSON") is told: left in place it would be
    met, and wake the loop, every cycle (SCRUM-59 final review)."""
    found: list[tuple[str, str]] = []
    with opened(root, manager, dirname) as fd:
        if fd is None:
            return []
        for name in _names(fd, ".json"):
            try:
                if claim:
                    os.rename(name, name + claim, src_dir_fd=fd, dst_dir_fd=fd)
                    name += claim
                try:
                    text = _read(fd, name, limit)
                except NotARegularFile:
                    os.rename(name, name + REFUSED, src_dir_fd=fd, dst_dir_fd=fd)
                    found.append((name + REFUSED, ""))
                    continue
                found.append((name, text))
                if not claim:
                    os.unlink(name, dir_fd=fd)
            except OSError:
                continue
    return found


def unlink(root: Path, manager: str, dirname: str, name: str) -> None:
    """Remove `name`; never raises. Called after an outcome was told, where
    a directory swapped for a link meanwhile must not escape into the cycle
    (review): the claim is left, and reported as interrupted next time."""
    try:
        with opened(root, manager, dirname) as fd:
            if fd is not None:
                os.unlink(name, dir_fd=fd)
    except OSError:
        pass


def write_new(root: Path, manager: str, dirname: str, name: str, text: str) -> None:
    """Create `name` with `text`, refusing to replace anything or follow a
    link. Raises OSError, which the caller says."""
    with opened(root, manager, dirname) as fd:
        if fd is None:
            raise FileNotFoundError(f"{dirname} is not there")
        leaf = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=fd,
        )
        try:
            os.write(leaf, text.encode("utf-8"))
        finally:
            os.close(leaf)
