"""Durable-state primitives: atomic writes, and corruption that refuses to
look like emptiness.

Two failure modes this module exists to remove, both found by a blast-radius
review ahead of driving real work on a live commercial repo.

**Every `.rite/` state file was written with `Path.write_text`**, which
truncates the file and then writes into it. A process killed between those
two steps — Ctrl-C, a laptop sleeping, a power cut, an OOM kill — leaves a
half-written file on disk. `write_atomic` writes a sibling temporary file,
flushes it to the platter, and `os.replace`s it into position, which is
atomic on POSIX: a reader sees either the whole old file or the whole new
one, never a prefix.

**Every reader then treated a corrupt file as an EMPTY one.** That is the
more dangerous half, and it is not theoretical: with a truncated
`claims.json`, `rite status` reported "no active claims" and a second worker
was allowed to claim a file the first worker still held — the exclusion
guarantee the tool exists to provide, silently gone, with a zero exit code
and no message. An empty file is a real state that means "nothing is
claimed". A corrupt file means "I do not know what is claimed", and the two
must never render the same. `read_json_state` raises `CorruptStateError` for
the second so callers have to decide, and the decision for anything holding
a safety property is to refuse, not to guess.

A missing file is still simply absent — that genuinely does mean "nothing
here yet", and is returned as the caller's default.

⚠ **"Every `.rite` state file" was not every state file.** The sweep that
introduced this module converted the claims ledger, the outbox,
heartbeats, the handover snapshot, the pool and the scheduler's window
state — and left `modules.yaml`, `credentials.json`, the Dispatch
registry, and both `INDEX.md` files still calling `write_text`, none of
them locked. They were found later by looking for the ledger's shape
rather than by reading this docstring, which had asserted the job was
finished. `tests/test_shared_state_locking.py` now fails if a new one
appears, because a claim of completeness in prose is not one.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class CorruptStateError(Exception):
    """A state file exists but could not be parsed. Carries the path so the
    message can tell a human exactly which file to look at — recovering
    means inspecting or deleting a specific file, and an error that does not
    name it turns a 30-second fix into a hunt."""

    def __init__(self, path: Path, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path} is unreadable ({detail})")


def write_atomic(path: Path, content: str) -> None:
    """Replace `path`'s contents with `content`, atomically.

    The temporary file is created in the SAME directory as the target, not
    in `/tmp`: `os.replace` is only atomic within one filesystem, and
    `.rite/` may well sit on a different mount than the system temp dir.
    `flush` + `fsync` before the replace so a crash immediately afterwards
    cannot leave the rename pointing at data the kernel had not yet written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Includes KeyboardInterrupt: a Ctrl-C mid-write must not leave the
        # scratch file behind, and must never leave `path` itself touched.
        tmp_path.unlink(missing_ok=True)
        raise


def read_json_state(path: Path, default: Any) -> Any:
    """Parse `path` as JSON. Returns `default` if it does not exist; raises
    `CorruptStateError` if it exists and cannot be read or parsed.

    The asymmetry is the whole point — see the module docstring. "Absent"
    and "unparseable" are different answers and only one of them is safe to
    treat as "nothing is going on".
    """
    if not path.exists():
        return default
    try:
        raw = path.read_text()
    except OSError as e:
        raise CorruptStateError(path, f"unreadable: {e}") from e
    if not raw.strip():
        # A zero-byte file is "not written yet", not "written badly". The
        # claims ledger's own lock creates the file (`open(path, "a+")`)
        # before anything has been written into it, and a first-ever `rite
        # claim` would otherwise refuse against the empty file it just made.
        #
        # This is only safe BECAUSE writes go through `write_atomic`. With
        # the old truncate-then-write, a process killed in the gap left a
        # genuinely zero-byte file, and reading that as "nothing claimed" is
        # exactly the silent-exclusion-loss this module exists to stop.
        # `os.replace` never truncates the target, so an empty file can no
        # longer be the wreckage of a lost write.
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CorruptStateError(path, f"invalid JSON: {e}") from e


class ExclusionUnavailable(Exception):
    """`flock` does not exclude on the filesystem holding this path."""


def exclusion_holds(directory: Path) -> bool:
    """Does `flock` actually exclude on the filesystem under `directory`?

    Every safety property in this package rests on the answer being yes.
    It is yes on a local disk and it is NOT guaranteed on a network mount
    (NFS, SMB) or inside some VM shared folders, where `flock` can be a
    silent no-op — and a no-op `flock` returns rite to the defect this
    module's lock was written to remove, with no signal at all: claims
    granted to two workers at once, each told "claimed", each exiting 0.

    So the property is MEASURED rather than inferred from a filesystem
    name. A name-based allowlist would have to be right about every
    filesystem anyone mounts; this opens two descriptions of one file and
    asks the kernel whether the second lock is refused, which is the
    question. `flock` locks live on the open file description, so two
    `open()` calls conflict inside one process exactly as they do across
    two.

    A probe that cannot run at all (no permission, read-only directory)
    returns False: this answers "can rite rely on exclusion here", and
    "I could not find out" is not a yes.
    """
    # A probe file UNIQUE TO THIS CALL. It used to be one fixed name,
    # `.rite-flock-probe`, deleted in the `finally` below — so two probes of
    # the same directory at once raced: one deleted the file while the other
    # was mid-probe, the other's second `open()` then created a NEW inode,
    # its lock was granted, and it reported that flock does not exclude on a
    # disk where it does. Measured: eight concurrent probers produced a
    # false "does not exclude" in two trials of three. It fires on ordinary
    # use — several workers running `rite claim` at once each construct a
    # ledger and probe `.rite/` — and printed "run one worker at a time".
    probe = directory / (
        f".rite-flock-probe-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        first = open(probe, "a+")  # noqa: SIM115
    except OSError:
        return False
    try:
        try:
            fcntl.flock(first.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Something else holds it — which is itself proof that
            # locking works here.
            return True
        second = open(probe, "a+")  # noqa: SIM115
        try:
            fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True  # refused: exclusion holds
        except OSError:
            return False
        else:
            # Granted while we already hold it. On this filesystem the
            # lock is decoration.
            fcntl.flock(second.fileno(), fcntl.LOCK_UN)
            return False
        finally:
            second.close()
    finally:
        try:
            fcntl.flock(first.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        first.close()
        probe.unlink(missing_ok=True)


def lock_path_for(path: Path) -> Path:
    """The sidecar this module locks on behalf of `path`."""
    return path.parent / f"{path.name}.lock"


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock covering `path` for the block's duration.

    **The lock is NOT taken on `path`.** It is taken on a sidecar,
    `<path>.lock`, and the reason is `write_atomic` above: `os.replace`
    swaps a NEW inode into the path on every write, and an `flock` lives
    on the inode a process opened, not on the name. Lock the data file
    and the first successful write orphans the lock — the next process to
    open the path gets a different inode and its `flock` is granted
    immediately, so two processes sit in the critical section at once,
    both having read the same state, and the second write silently
    discards the first.

    That was not hypothetical. The claims ledger locked `claims.json`
    itself; measured with twelve concurrent claimants on non-overlapping
    paths, ten of twenty rounds lost at least one claim and the worst
    lost five of twelve — every one of them reported "claimed" and
    exited 0. A claim that vanishes is not a bookkeeping error: the next
    worker to ask for that path is granted it.

    The sidecar is created on first use and thereafter only opened —
    never written, replaced, or unlinked. Deleting a lock file while it
    is held is the failure mode sidecars are warned about (the next
    acquirer creates a fresh inode and both proceed); nothing here ever
    deletes it.

    Read-modify-write over `write_atomic` is safe INSIDE this block and
    unsafe outside it. `write_atomic` makes a write all-or-nothing
    against a crash; it does nothing whatsoever about two writers.
    """
    lock_file = lock_path_for(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_file, "a+")  # noqa: SIM115
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
