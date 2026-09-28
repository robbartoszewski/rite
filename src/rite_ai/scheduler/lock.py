"""One tick at a time, held by the kernel rather than inferred from a pid.

`scheduler-tick` runs unattended from cron or launchd on a fixed interval.
Nothing stopped two of them overlapping, so a tick that ran long — or hung,
before every subprocess call grew a timeout — was joined by the next one, and
the one after that. That is the failure class that compounds: it is invisible
while the machine is idle and worst exactly when the machine is busy.

THE DECISION, and why the first one was replaced:

**0.1.0–0.6.0 published a lockfile holding the owner's pid, and decided from
outside whether that owner was gone.** It did not exclude. Measured with 8
processes contending for 20 s: 1,352–1,617 overlapping holders per run in a
Linux container, 0–1 on macOS, and 85–500 false reclaims on both. The defect
was structural, not a missing check. "Is the holder gone" was answered from
proxies — the file missing between two looks, a file that read as unusable,
`kill(pid, 0)` — and every "gone" was followed by deleting whatever file was
at the path by then, which could be another tick's fresh lock. A holder can
also look alive when it is not (`kill(pid, 0)` succeeds on a zombie and on a
recycled pid). No ordering of those steps closes that: the decision and the
delete are two system calls, and the lock can change hands between them.

**Now: `flock(LOCK_EX | LOCK_NB)` on a file that is never deleted.** Nothing
here decides whether a holder is gone. The kernel holds the lock for as long
as the holder's open file description exists and drops it when the process
exits, however it exits, including SIGKILL. So there is no stale lock, no
reclaim, no pid check and no timeout to get wrong, and a tick either gets the
lock from the kernel or is refused by it. The file is only opened: never
unlinked, never replaced. A lock file that is deleted while held lets the next
opener lock a fresh inode, which is the defect this replaces in another form,
so the rule is absolute (the claims ledger's sidecars in `state.locked` rest on
the same one).

Measured with the harness that produced the numbers above (8 processes,
20 s, each recording the interval it held the lock; three runs each):

- Ubuntu 24.04 VM (Parallels, aarch64, kernel 7.0, 4 vCPU, Python 3.12.3),
  guest idle when the runs started (load 0.00), on a Mac host that was not
  quiet: the old lock 618–665 overlapping holders and 612–692 false
  reclaims per run. This one 0 and 0. The old code was re-measured here so
  this pair is comparable. It is NOT like-for-like with the 1,352–1,617
  above: that came from a Docker container, a different machine and core
  count, and its load was not recorded.
- macOS (M-series, 14 cores, Python 3.14), on a LOADED machine: load 3.7
  before the runs and 8.5 during them, with three other working sessions
  running. The old lock 0–1 overlaps and 85–95 false reclaims; this one 0
  and 0. Taken under that load, so not comparable to a quiet-machine figure.
- Controls, on both: with `flock` made a no-op and the self-test below
  removed, the harness counted 56,275 (Linux) and 57,473 (macOS)
  overlaps, so its zero is not blindness. With the self-test kept, every
  tick was refused and none ran.

`flock`, not `fcntl`/`lockf`, and the difference matters on both platforms:
POSIX record locks belong to the PROCESS and are dropped when the process
closes ANY descriptor for the file, so one stray `open`/`close` of the lock
file anywhere in a tick would silently release it. `flock` locks belong to the
open file description, on Linux and on macOS alike, so two `open`s conflict
even inside one process, and closing one never releases the other.

**Where the kernel's answer cannot be trusted, the tick refuses (fails
closed).** `flock` can be a silent no-op on a network or VM-shared filesystem
(SPEC §5.3), which would grant every tick the lock with no signal. So every
acquisition proves exclusion on the lock itself: it opens the file a second
time and asks for the lock again, and that request must be REFUSED. A second
grant means this filesystem's `flock` is decoration, and the tick does not run.
So does a lock file that cannot be opened, an `flock` that errors, and a path
that no longer names the file that was locked. Each outcome says which.

**A busy tick says so, out loud, and exits 0.** Two ticks overlapping is a
normal consequence of a long tick meeting a short interval, not a fault. But a
silent skip is the same defect as the empty scheduler log this project already
fixed: a skipped tick and a scheduler that never fired must not look alike in
the log. The holder records its pid and start time in the file after it has
the lock, and clears the record before it lets go. That record is only for
the message. No decision reads it.

**A held lock is never taken away, however long it is held.** 0.6.0 reclaimed
a live holder's lock after 900 s, in case its pid had been recycled. With the
kernel holding the lock, pid reuse cannot matter. A tick that really is
wedged past every bounded wait in the package keeps the lock, and every
skipped tick says how long it has been held and that this is past any
legitimate tick. Taking the lock from a live holder would be two ticks
running at once, which this module exists to prevent.

⚠ **Not covered: a 0.6.0 tick running at the same moment as this one.** The
old protocol used `scheduler.lock` and deleted it on release. This one uses a
different file, `LOCK_FILENAME`, so an old tick can never delete a lock that
a new tick holds. But the two do not see each other. That only matters while
two rite versions run ticks against one project, and 0.6.0 ticks never
excluded each other in the first place.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.kernel_lock import exclusion_problem as _exclusion_problem
from rite_ai.kernel_lock import flock_failed as _flock_failed
from rite_ai.kernel_lock import open_lock_file as _open
from rite_ai.kernel_lock import read_record as _read_record
from rite_ai.kernel_lock import refused as _refused
from rite_ai.kernel_lock import same_file as _same_file
from rite_ai.kernel_lock import write_record as _write_record

LOCK_FILENAME = "scheduler-tick.lock"

# See the module docstring: this changes the wording of a skip, never whether
# the lock is taken. It must stay larger than any single bounded operation in
# the package, so a merely slow tick is never described as wedged.
_HELD_TOO_LONG_SECONDS = 900.0


@dataclass
class LockAcquired:
    """We hold the lock for as long as `fd` stays open. `release` closes it;
    so does the process exiting, which is what makes a killed tick's lock
    free itself."""

    path: Path
    fd: int
    warnings: list[str] = field(default_factory=list)
    """Filled by `release` when something went wrong while this tick held
    the lock that the caller must report (see `release`)."""


@dataclass
class LockBusy:
    """Another tick holds the lock: the kernel refused ours."""

    holder_pid: int | None
    """What the holder recorded, or None when there was no usable record.
    The holder writes it just after taking the lock, so a tick refused in
    that instant finds nothing, and "not recorded" is reported as that.

    ⚠ Display only, and it can be one holder out of date: a tick that was
    killed never cleared its record, so a tick refused in the instant after
    the next holder took the lock, before it wrote its own, reads the dead
    tick's. Nothing decides anything from it, which is why that is safe."""
    held_for_seconds: float | None

    @property
    def summary(self) -> str:
        if self.holder_pid is None or self.held_for_seconds is None:
            return (
                "another scheduler-tick is already running (it holds the lock "
                "and has not recorded its pid yet)"
            )
        text = (
            f"another scheduler-tick is already running "
            f"(pid {self.holder_pid}, started {_human(self.held_for_seconds)} ago)"
        )
        if self.held_for_seconds >= _HELD_TOO_LONG_SECONDS:
            text += (
                f". That is longer than any tick should take "
                f"({int(_HELD_TOO_LONG_SECONDS)}s), so it may be stuck. The "
                f"lock is not taken from a running tick. It frees itself when "
                f"that process exits. Check what pid {self.holder_pid} is "
                f"before stopping it"
            )
        return text


@dataclass
class LockUnavailable:
    """Mutual exclusion could not be established, so the tick must not run.

    Distinct from `LockBusy`: busy is the kernel saying "someone else has
    it", which is normal. This is "rite cannot tell whether someone else has
    it", and running anyway would be the race this module exists to remove.
    """

    reason: str

    @property
    def summary(self) -> str:
        return f"scheduler-tick did not run: {self.reason}"


def _human(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def lock_path(root: Path) -> Path:
    return root / ".rite" / LOCK_FILENAME


def process_is_running(pid: int) -> bool:
    """Signal 0 performs the permission and existence checks without
    delivering anything. `PermissionError` means the pid exists but belongs
    to another user — still running, so still a live holder.

    ⚠ No lock uses this any more: it cannot tell a zombie or a recycled pid
    from the process that wrote the number. Its one caller is the dispatch
    intents (`loop/intents.py`), where a wrong "running" only delays calling
    an intent lost until its age says so."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire(root: Path) -> LockAcquired | LockBusy | LockUnavailable:
    path = lock_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = _open(path)
    except OSError as e:
        return LockUnavailable(f"cannot open the lock file {path}: {e}")

    try:
        if _refused(fd):
            record = _read_record(fd)
            os.close(fd)
            if record is None:
                return LockBusy(holder_pid=None, held_for_seconds=None)
            pid, acquired_at = record
            return LockBusy(
                holder_pid=pid, held_for_seconds=max(time.time() - acquired_at, 0.0)
            )
    except OSError as e:
        os.close(fd)
        return LockUnavailable(_flock_failed(path, e))
    except BaseException:
        os.close(fd)
        raise

    # We hold a lock on the file we opened. Two things must also be true
    # before that means anything, and each is checked, not assumed.
    try:
        problem = _exclusion_problem(path, fd)
    except BaseException:
        os.close(fd)
        raise
    if problem:
        os.close(fd)
        return LockUnavailable(problem)

    try:
        _write_record(fd)
    except OSError:
        # The record only feeds other ticks' skip messages, which already
        # say "not recorded" when it is missing. Not a reason to skip work.
        pass
    return LockAcquired(path=path, fd=fd)


def release(outcome: LockAcquired) -> None:
    """Clear the record and close the descriptor, which frees the lock.

    Nothing is deleted. Before letting go, the holder checks that the path
    still names the file it held. If it does not, something removed or
    replaced the lock file during the tick, and a tick that started in that
    time may have run alongside this one. That cannot be undone here, but
    it is added to `outcome.warnings` so the caller reports it.
    """
    try:
        try:
            current = os.stat(outcome.path, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is None or not _same_file(os.fstat(outcome.fd), current):
            outcome.warnings.append(
                f"the scheduler lock file {outcome.path} was deleted or "
                f"replaced while this tick held it. Another tick may have run "
                f"at the same time. Something other than rite removed it"
            )
        try:
            os.ftruncate(outcome.fd, 0)
        except OSError:
            pass
    finally:
        os.close(outcome.fd)


@contextmanager
def held(root: Path) -> Iterator[LockAcquired | LockBusy | LockUnavailable]:
    """Acquire for the duration of the block, always releasing what we took.

    Yields the outcome instead of raising on contention. A busy scheduler is
    an ordinary result the caller reports, not an error. Release runs in
    `finally` so a tick that raises still frees the lock. A tick that is
    killed cannot run it, and does not need to: the kernel frees the lock
    when the process goes.
    """
    outcome = acquire(root)
    try:
        yield outcome
    finally:
        if isinstance(outcome, LockAcquired):
            release(outcome)
