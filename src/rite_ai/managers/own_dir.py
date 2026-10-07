"""Every file rite touches in a Manager's own directory or outbox, through
one layer that follows no link and opens nothing but regular files.

🔴 **Why one layer (SCRUM-69 round 3, SCRUM-59 final and follow-up reviews,
all measured).** A Manager can write inside its own directory and outbox,
and rite works there outside every boundary: it reads requests, writes the
cycle's prompt, appends to ledgers, copies the journal. Point fixes kept
missing variants, because the Manager can plant a link, a FIFO or a whole
directory moved in from the project (carrying links one level down) at ANY
path rite later opens by name:
- a link at `prompt.txt` had the prompt overwrite any file the person owns;
- `checkins/` moved in with `ledger.jsonl` linked to `~/.zshrc` had rite
  append a `$(…)` line there: code execution at the person's next shell;
- a FIFO at a ledger hung the supervisor; a directory named `5.json` in the
  outbox woke the loop for ever; `routes/` linked to a sibling's took the
  sibling's work.

So nothing in rite opens a path under these two directories by name. It goes
through here, where:
- the base (the Manager's directory, or its outbox) is opened `O_NOFOLLOW`;
  its own path the Manager cannot replace (the profile denies writing it);
- every further component is opened `O_NOFOLLOW | O_DIRECTORY` through the
  descriptor before it, so no link is followed at any depth;
- a file is opened `O_NOFOLLOW | O_NONBLOCK` and used only if `fstat` says
  it is a regular file (a FIFO neither blocks nor is read);
- a write is a new temporary file (`O_EXCL`) renamed over the name through
  the directory's descriptor: atomic, and a rename replaces a planted link or
  FIFO rather than following it;
- an append or a lock opens with the same flags and checks before use.

It works the same inside the Manager's own sandbox, where the same commands
(`rite reply`, `rite ask --defer`) use it.

A path that is not what rite made raises `OSError` (`NotARegularFile` for a
link, FIFO or directory where a file belongs) for the caller to say.
"""

from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

STATE = "state"
"""The Manager's own directory (`managers.manager_dir`)."""
OUTBOX = "outbox"
"""Its mail outbox (`mailbox.mailbox_dir(…, OUTBOX)`)."""
INBOX = "inbox"
"""Its inbox, which only rite writes; read the same way for one code path."""

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

REFUSED = ".refused"
"""Suffix an entry is renamed to when it is not a regular file: out of every
`*.json` glob, so it is not met again, and kept for a person to look at."""


class NotARegularFile(OSError):
    """A link, a FIFO, a directory: anything but the regular file rite made."""


def _base(root: Path, manager: str, area: str) -> Path:
    from rite_ai.managers import manager_dir

    if area == STATE:
        return manager_dir(Path(root), manager)
    if area in (OUTBOX, INBOX):
        from rite_ai.managers import mailbox

        box = mailbox.OUTBOX if area == OUTBOX else mailbox.INBOX
        return mailbox.mailbox_dir(Path(root), manager, box)
    raise ValueError(f"no such area {area!r}")


def _parts(rel: str | Path) -> tuple[str, ...]:
    """`rel` as components, refusing anything that could leave the base."""
    parts = PurePosixPath(str(rel)).parts
    if not parts or parts[0] == "/" or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{rel!r} is not a path inside a Manager's directory")
    return parts


def _open_base(root: Path, manager: str, area: str) -> int | None:
    base = _base(root, manager, area)
    try:
        return os.open(base, _DIR_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as e:
        # A base that is a link (ELOOP) or whose name is a non-directory
        # (ENOTDIR): refused as what it is, never followed. The profile also
        # stops the Manager replacing its own directory; this is the layer's
        # own guard, independent of it.
        raise _refused(e, str(base)) from None


def _refused(e: OSError, what: str) -> OSError:
    if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR, errno.ENXIO):
        return NotARegularFile(e.errno, f"{what} is not what rite made there")
    return e


def _walk(fd: int, parts: tuple[str, ...], create: bool) -> int:
    """Descend `parts` from `fd` (which this closes), no link followed;
    creating missing directories (0700) when `create`. Returns a new fd."""
    for part in parts:
        try:
            nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            if not create:
                os.close(fd)
                raise
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as e:
                os.close(fd)
                raise _refused(e, part) from None
        except OSError as e:
            os.close(fd)
            raise _refused(e, part) from None
        os.close(fd)
        fd = nxt
    return fd


@contextmanager
def directory(
    root: Path, manager: str, rel: str = "", *, area: str = STATE, create: bool = False
):
    """The descriptor of a directory under the base, or None if absent (and
    not `create`). Raises `NotARegularFile` for a link anywhere on the way."""
    base = _open_base(root, manager, area)
    if base is None:
        if not create:
            yield None
            return
        _base(root, manager, area).mkdir(parents=True, exist_ok=True)
        base = _open_base(root, manager, area)
        if base is None:
            raise FileNotFoundError(str(_base(root, manager, area)))
    try:
        fd = _walk(base, _parts(rel), create) if rel else base
    except FileNotFoundError:
        yield None
        return
    try:
        yield fd
    finally:
        os.close(fd)


def _split(rel: str | Path) -> tuple[str, str]:
    parts = _parts(rel)
    return "/".join(parts[:-1]), parts[-1]


# --- files ----------------------------------------------------------------------


def _open_regular(dir_fd: int, name: str, flags: int, mode: int = 0o600) -> int:
    try:
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode, dir_fd=dir_fd)
    except OSError as e:
        raise _refused(e, name) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotARegularFile(errno.EINVAL, f"{name} is not a regular file")
        if flags & (os.O_WRONLY | os.O_RDWR):
            # Back to blocking for a regular file (no effect, but explicit).
            fcntl.fcntl(
                fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK
            )
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_fd(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= limit:
        chunk = os.read(fd, limit + 1 - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


DEFAULT_LIMIT = 16 * 1024 * 1024


def read_bytes(
    root: Path, manager: str, rel: str, *, area: str = STATE, limit: int = DEFAULT_LIMIT
) -> bytes:
    """The file's bytes (at most `limit` + 1, so a size check can see more).
    Raises FileNotFoundError, or `NotARegularFile`."""
    parent, name = _split(rel)
    with directory(root, manager, parent, area=area) as d:
        if d is None:
            raise FileNotFoundError(rel)
        fd = _open_regular(d, name, os.O_RDONLY)
        try:
            return _read_fd(fd, limit)
        finally:
            os.close(fd)


def read_text(
    root: Path, manager: str, rel: str, *, area: str = STATE, limit: int = DEFAULT_LIMIT
) -> str:
    return read_bytes(root, manager, rel, area=area, limit=limit).decode(
        "utf-8", "replace"
    )


def write_text(
    root: Path,
    manager: str,
    rel: str,
    text: str,
    *,
    area: str = STATE,
    mode: int = 0o600,
) -> None:
    """Replace `rel` with `text`, atomically: a new temporary file in the same
    directory, synced, renamed over the name. Creates missing directories.
    A planted link or FIFO at the name is replaced, never followed; a
    directory there is refused."""
    parent, name = _split(rel)
    with directory(root, manager, parent, area=area, create=True) as d:
        _replace(d, name, text, mode)


def create(
    root: Path,
    manager: str,
    rel: str,
    text: str,
    *,
    area: str = STATE,
    mode: int = 0o600,
) -> None:
    """Create `rel`, refusing to replace anything (`O_EXCL`); FileExistsError
    if the name is taken by anything at all."""
    parent, name = _split(rel)
    with directory(root, manager, parent, area=area, create=True) as d:
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=d
        )
        try:
            data = text.encode("utf-8")
            while data:
                data = data[os.write(fd, data) :]
        finally:
            os.close(fd)


def append_text(
    root: Path, manager: str, rel: str, text: str, *, area: str = STATE
) -> None:
    """Append to a regular file, creating it. A link or FIFO at the name is
    refused (`NotARegularFile`), never written through."""
    parent, name = _split(rel)
    with directory(root, manager, parent, area=area, create=True) as d:
        fd = _open_regular(d, name, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            data = text.encode("utf-8")
            while data:
                data = data[os.write(fd, data) :]
        finally:
            os.close(fd)


@contextmanager
def locked(root: Path, manager: str, rel: str, *, area: str = STATE):
    """Hold an exclusive lock on `<rel>.lock` (the sidecar rule of
    `state.locked`: a lock on the data file is orphaned by every atomic
    replace), opened as a regular file and never through a link."""
    parent, name = _split(rel)
    with directory(root, manager, parent, area=area, create=True) as d:
        fd = _open_regular(d, name + ".lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def exists(root: Path, manager: str, rel: str, *, area: str = STATE) -> bool:
    """Whether `rel` is there as a regular file."""
    try:
        parent, name = _split(rel)
        with directory(root, manager, parent, area=area) as d:
            if d is None:
                return False
            st = os.stat(name, dir_fd=d, follow_symlinks=False)
            return stat.S_ISREG(st.st_mode)
    except OSError:
        return False


def unlink(root: Path, manager: str, rel: str, *, area: str = STATE) -> None:
    """Remove `rel`; never raises (called after an outcome was told)."""
    try:
        parent, name = _split(rel)
        with directory(root, manager, parent, area=area) as d:
            if d is not None:
                os.unlink(name, dir_fd=d)
    except OSError:
        pass


def set_aside(root: Path, manager: str, rel: str, *, area: str = STATE) -> None:
    """Rename `rel` to `<rel>.refused` (a link, FIFO or directory where a file
    belongs), so it is not met again; never raises."""
    try:
        parent, name = _split(rel)
        with directory(root, manager, parent, area=area) as d:
            if d is not None:
                os.rename(
                    name,
                    name + REFUSED + "-" + secrets.token_hex(3),
                    src_dir_fd=d,
                    dst_dir_fd=d,
                )
    except OSError:
        pass


# --- request directories: list, take, remove -------------------------------------


def _names(fd: int, suffix: str) -> list[str]:
    return sorted(n for n in os.listdir(fd) if n.endswith(suffix))


def names(
    root: Path, manager: str, dirname: str, suffix: str, *, area: str = STATE
) -> list[str]:
    """Entries of `dirname` ending `suffix`, oldest first by name. Raises
    OSError for a directory that is a link anywhere on the way."""
    with directory(root, manager, dirname, area=area) as fd:
        return [] if fd is None else _names(fd, suffix)


def read(
    root: Path, manager: str, dirname: str, name: str, limit: int, *, area: str = STATE
) -> str:
    """A regular file's text; raises OSError (`NotARegularFile` for a link)."""
    return read_text(root, manager, f"{dirname}/{name}", area=area, limit=limit)


def take(
    root: Path,
    manager: str,
    dirname: str,
    limit: int,
    claim: str = "",
    *,
    area: str = STATE,
) -> list[tuple[str, str]]:
    """Every `*.json`, oldest first: `(name, text)`.

    With `claim`, each is first RENAMED to `<name><claim>` (only one taker
    wins a rename) and the claimed name is returned; the caller removes it
    when done. Without, each is removed as it is read.

    An entry that is not a regular file (a planted link, a FIFO, a directory)
    is set aside (`set_aside`, a unique `.refused-…` name, kept for a person)
    and returned with text "", so the caller's refusal is told; the name
    returned no longer exists, so a caller's removal of it does nothing."""
    found: list[tuple[str, str]] = []
    with directory(root, manager, dirname, area=area) as fd:
        if fd is None:
            return []
        for name in _names(fd, ".json"):
            try:
                if claim:
                    os.rename(name, name + claim, src_dir_fd=fd, dst_dir_fd=fd)
                    name += claim
                try:
                    leaf = _open_regular(fd, name, os.O_RDONLY)
                except NotARegularFile:
                    os.rename(
                        name,
                        name + REFUSED + "-" + secrets.token_hex(3),
                        src_dir_fd=fd,
                        dst_dir_fd=fd,
                    )
                    found.append((name, ""))
                    continue
                try:
                    text = _read_fd(leaf, limit).decode("utf-8", "replace")
                finally:
                    os.close(leaf)
                found.append((name, text))
                if not claim:
                    os.unlink(name, dir_fd=fd)
            except OSError:
                continue
    return found


def unlink_in(
    root: Path, manager: str, dirname: str, name: str, *, area: str = STATE
) -> None:
    unlink(root, manager, f"{dirname}/{name}", area=area)


def write_new(
    root: Path, manager: str, dirname: str, name: str, text: str, *, area: str = STATE
) -> None:
    """Create `dirname/name`, never replacing anything or following a link."""
    with directory(root, manager, dirname, area=area) as fd:
        if fd is None:
            raise FileNotFoundError(f"{dirname} is not there")
    create(root, manager, f"{dirname}/{name}", text, area=area)


# --- a file directly in a directory the Manager cannot replace -------------------
#
# rite's own ledgers sit at the top of the Manager's directory, whose path the
# Manager cannot replace (the profile denies writing it). These open that
# directory `O_NOFOLLOW` by its path and then treat the leaf as above, so a
# link, FIFO or directory planted AT the ledger's name is never followed.


@contextmanager
def _parent_of(path: Path, create: bool):
    parent = Path(path).parent
    if create:
        parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(parent, _DIR_FLAGS)
    except FileNotFoundError:
        yield None
        return
    except OSError as e:
        raise _refused(e, str(parent)) from None
    try:
        yield fd
    finally:
        os.close(fd)


def read_file(path: Path, limit: int = DEFAULT_LIMIT) -> str:
    """Raises FileNotFoundError, or `NotARegularFile`."""
    with _parent_of(path, False) as d:
        if d is None:
            raise FileNotFoundError(str(path))
        fd = _open_regular(d, Path(path).name, os.O_RDONLY)
        try:
            return _read_fd(fd, limit).decode("utf-8", "replace")
        finally:
            os.close(fd)


def write_file(path: Path, text: str, mode: int = 0o600) -> None:
    """Atomic replace, as `write_text`."""
    name = Path(path).name
    with _parent_of(path, True) as d:
        _replace(d, name, text, mode)


def _replace(d: int, name: str, text: str, mode: int) -> None:
    tmp = f".{name}.{secrets.token_hex(6)}.tmp"
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=d
    )
    try:
        try:
            data = text.encode("utf-8")
            while data:
                data = data[os.write(fd, data) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, name, src_dir_fd=d, dst_dir_fd=d)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=d)
        except OSError:
            pass
        raise


@contextmanager
def locked_file(path: Path):
    """An exclusive lock on the `<path>.lock` sidecar (see `locked`)."""
    with _parent_of(path, True) as d:
        fd = _open_regular(d, Path(path).name + ".lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def load_json(path: Path) -> dict:
    """A ledger's JSON object, or {} when it is absent, unreadable, not a
    regular file (said by nothing: a ledger rite cannot trust is empty), or
    not an object."""
    import json

    try:
        data = json.loads(read_file(path))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
