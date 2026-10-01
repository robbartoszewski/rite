"""One loop per project, held by the kernel, and an exact answer to "is it up".

THE DEFECT THIS REPLACES. The loop lock was a pid file, `.rite/loop.lock`:
read it, and if `kill(pid, 0)` said nothing was there, overwrite it. Two
failures, both measured on macOS against main `4f7e1d2`:

- **It did not exclude.** Reading and then `write_atomic` are two steps, and
  `os.replace` overwrites unconditionally, so every loop that read "nobody"
  in the same instant proceeded. 8 processes for 20 s: 51,120–52,154
  overlapping holders out of about 58,000 acquisitions per run.
- **A pid that exists is not a pid that is yours.** A zombie (`ps` stat
  `Z`) or a live, unrelated `sleep` whose pid was in the file read as a
  running loop, and every start was refused until someone ran `rm`.

THE DESIGN. Two files, both only ever opened, never deleted or replaced:

- `loop-run.lock`, the LOCK. The loop process takes `flock(LOCK_EX |
  LOCK_NB)` and holds it for its whole life. The kernel drops it when the
  process exits, however it exits, so there is no stale lock and nothing
  decides whether a holder is gone. After taking it, the holder writes its
  pid and start time into the same file. Before letting go, it clears them.
- `loop-run.gate`, the GATE, held for a few system calls by anyone who takes
  the lock, lets go of it, or asks whether it is held.

WHY THE GATE EXISTS. `flock` has no "is this held?" query that works on both
Linux and macOS, so a reader can only find out by trying to take the lock.
Without the gate, a reader's try at the instant a loop was starting made the
loop's own try fail, and the loop refused to start, reporting "another loop
holds this project" about a project nobody held. That race belongs to the
readers, not the lock, and the gate removes it rather than making it rarer:
every take, release and look happens under the gate, so no look can overlap
a take. It also makes the record exact. It is written before the gate is let
go and cleared before the lock is, so under the gate "the lock is held" and
"the record names the holder" are the same fact. A killed holder cannot
clear its record, but its lock is free, and a reader that finds the lock
free ignores the record.

WHERE IT CANNOT KNOW, IT SAYS SO. `holder()` answers running, not running or
unknown, never a guess. Unknown covers a gate held too long (a process
stopped with Ctrl-Z), a filesystem where `flock` does not exclude, and a lock
file that cannot be opened. `acquire` refuses in the same cases. A start that
cannot establish there is no loop could make two.

⚠ A 0.6.0 loop uses `.rite/loop.lock` and this one does not see it, as with
the scheduler tick's lock. Two rite versions running loops against one project
do not exclude each other. 0.6.0's loops never excluded each other either.
"""

from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.kernel_lock import (
    GateUnavailable,
    exclusion_problem,
    flock_failed,
    gate,
    open_lock_file,
    read_record,
    refused,
    write_record,
)

LOCK_FILENAME = "loop-run.lock"
GATE_FILENAME = "loop-run.gate"

# The gate is held for a few system calls. Anything waiting longer than this
# is behind a holder that has stopped, and gets "unknown" rather than a guess.
GATE_WAIT_SECONDS = 5.0


@dataclass
class LockAcquired:
    """This process runs the loop for as long as `fd` stays open."""

    path: Path
    fd: int


@dataclass
class LockBusy:
    """Another loop holds this project. `holder_pid` is exact: read under the
    gate, where a held lock always has its holder's record."""

    holder_pid: int
    running_for_seconds: float


@dataclass
class LockUnavailable:
    """Whether another loop holds this project could not be established, so
    none may start."""

    reason: str


@dataclass
class Holder:
    """What `holder` found. `known` False means it could not tell, and then
    `running` means nothing."""

    known: bool
    running: bool = False
    pid: int = 0
    running_for_seconds: float = 0.0
    detail: str = ""


def lock_path(root: Path) -> Path:
    return root / ".rite" / LOCK_FILENAME


def gate_path(root: Path) -> Path:
    return root / ".rite" / GATE_FILENAME


def _busy(path: Path, fd: int) -> LockBusy | LockUnavailable:
    record = read_record(fd)
    if record is None:
        # Cannot happen through this module: a holder writes its record
        # before it lets go of the gate. Something else wrote this file.
        return LockUnavailable(
            f"the loop lock {path} is held but names no holder. Something "
            f"other than rite wrote to it"
        )
    pid, since = record
    return LockBusy(holder_pid=pid, running_for_seconds=max(time.time() - since, 0.0))


def acquire(root: Path) -> LockAcquired | LockBusy | LockUnavailable:
    path = lock_path(root)
    try:
        with gate(gate_path(root), GATE_WAIT_SECONDS):
            try:
                fd = open_lock_file(path)
            except OSError as e:
                return LockUnavailable(f"cannot open the loop lock {path}: {e}")
            try:
                if refused(fd):
                    outcome = _busy(path, fd)
                    os.close(fd)
                    return outcome
                problem = exclusion_problem(path, fd)
                if problem:
                    os.close(fd)
                    return LockUnavailable(problem)
                # Written under the gate, so no reader ever finds the lock held
                # without its holder named. A holder that cannot say who it is
                # does not hold.
                write_record(fd)
            except OSError as e:
                os.close(fd)
                return LockUnavailable(flock_failed(path, e))
            except BaseException:
                os.close(fd)
                raise
            return LockAcquired(path=path, fd=fd)
    except GateUnavailable as e:
        return LockUnavailable(str(e))


def release(outcome: LockAcquired) -> None:
    """Clear the record, then let the kernel release the lock, under the gate.

    If the gate cannot be had, the descriptor is closed anyway: holding a
    loop lock after the loop has finished would refuse every later start.
    The record is then left behind, and it is harmless, because a reader
    that finds the lock free ignores it.
    """
    try:
        with gate(outcome.path.parent / GATE_FILENAME, GATE_WAIT_SECONDS):
            try:
                os.ftruncate(outcome.fd, 0)
            except OSError:
                pass
            os.close(outcome.fd)
            return
    except GateUnavailable:
        pass
    os.close(outcome.fd)


def holder(root: Path) -> Holder:
    """Is a loop running for `root`, and which pid, exactly. NEVER spawns.

    Takes the gate, then asks the kernel for the lock on a descriptor of its
    own, and gives it straight back if granted. Under the gate no loop can be
    starting or stopping, so a grant means "not running" and a refusal means
    running, with the holder's record intact.
    """
    path = lock_path(root)
    if not path.exists():
        # A loop creates the file before it locks it, and never deletes it.
        return Holder(known=True, running=False)
    try:
        with gate(gate_path(root), GATE_WAIT_SECONDS):
            try:
                fd = open_lock_file(path)
            except OSError as e:
                return Holder(known=False, detail=f"cannot open {path}: {e}")
            try:
                if refused(fd):
                    busy = _busy(path, fd)
                    if isinstance(busy, LockUnavailable):
                        return Holder(known=False, detail=busy.reason)
                    return Holder(
                        known=True,
                        running=True,
                        pid=busy.holder_pid,
                        running_for_seconds=busy.running_for_seconds,
                    )
                # Granted, so nobody holds it, provided flock excludes here
                # at all. Checked while this descriptor holds it.
                problem = exclusion_problem(path, fd)
                fcntl.flock(fd, fcntl.LOCK_UN)
                if problem:
                    return Holder(known=False, detail=problem)
                return Holder(known=True, running=False)
            except OSError as e:
                return Holder(known=False, detail=flock_failed(path, e))
            finally:
                os.close(fd)
    except GateUnavailable as e:
        return Holder(known=False, detail=str(e))
