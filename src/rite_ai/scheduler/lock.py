"""One tick at a time, and a lock that cannot outlive the process holding it.

`scheduler-tick` runs unattended from cron or launchd on a fixed interval.
Nothing stopped two of them overlapping, so a tick that ran long — or hung,
before every subprocess call grew a timeout — was joined by the next one, and
the one after that. That is the failure class that compounds: it is invisible
while the machine is idle and worst exactly when the machine is busy.

THE DECISION, since this needed one rather than a patch:

**A lockfile holding the owner's pid, not a timeout.** "Is the previous tick
still running" is a question the operating system can answer exactly, on the
single machine Phase 1 targets. Deriving it from elapsed time instead means
picking a number that is simultaneously too short (a slow tick gets its lock
stolen while it works) and too long (a killed tick wedges the scheduler until
the number expires). `os.kill(pid, 0)` asks the real question.

**A busy tick says so, out loud, and exits 0.** Two ticks overlapping is a
normal consequence of a long tick meeting a short interval, not a fault — but
a silent skip is the same defect as the empty scheduler log this project
already fixed: a skipped tick and a scheduler that never fired must not look
alike in the log. So the skip is reported with the holder's pid and how long
it has been running, and the exit status stays 0 because nothing is broken.

**A stale lock is reclaimed, loudly.** A tick killed with SIGKILL, or lost to
a power cut, leaves its lockfile behind. If its pid is no longer running the
lock is meaningless, and the next tick takes it and says that it did. This is
the stale-claim problem one level down, and it gets the opposite answer for a
reason: a claim guards a human's in-flight edits and rite cannot tell a
crashed session from a thinking one, so claims never auto-expire. A tick lock
guards a five-minute mechanical job owned by a process on this machine, whose
liveness is directly observable — so reclaiming is safe here in a way
releasing a claim is not.

`_PID_REUSE_BACKSTOP_SECONDS` is the one time-based rule, and it exists only
because a pid can be recycled onto an unrelated long-lived process, which
would otherwise wedge the scheduler forever — the exact permanent-wedge this
module exists to prevent. It is derived, not picked: it must exceed the
longest bounded wait anything in the package can perform, so it can never
fire on a tick that is merely slow. `test_scheduler_lock.py` asserts that
relationship against the real timeouts in the source rather than restating
the number.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

LOCK_FILENAME = "scheduler.lock"

# See the module docstring: a backstop against pid reuse, not a tick timeout.
# Must stay larger than any single bounded operation in the package.
_PID_REUSE_BACKSTOP_SECONDS = 900.0


@dataclass
class LockAcquired:
    """We hold the lock. `reclaimed_from` names the pid whose abandoned lock
    we cleared, when that is what happened — the caller reports it, because
    a scheduler quietly recovering from a killed tick is something the
    operator should see in the log."""

    path: Path
    reclaimed_from: int | None = None
    reclaim_reason: str = ""


@dataclass
class LockBusy:
    """Another tick is genuinely running."""

    holder_pid: int
    held_for_seconds: float
    detail: str = ""
    """Set when the holder's identity is not the useful part of the story —
    losing a race to reclaim an abandoned lock, say, where the winner's pid
    is not the one we read."""

    @property
    def summary(self) -> str:
        if self.detail:
            return f"another scheduler-tick is already running ({self.detail})"
        return (
            f"another scheduler-tick is already running "
            f"(pid {self.holder_pid}, started {_human(self.held_for_seconds)} ago)"
        )


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
    to another user — still running, so still a live holder."""
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


def _read_holder(path: Path) -> tuple[int, float] | None:
    """`(pid, acquired_at)`, or None if the file is missing or unusable.

    An unreadable lockfile is treated as no lock rather than as a held one.
    That is the opposite of the rule for `claims.json`, deliberately: a
    corrupt claims ledger must never be read as "nothing is claimed" because
    that silently drops a safety guarantee, whereas a corrupt lockfile read
    as "held" would wedge the scheduler permanently with no way back short
    of deleting the file by hand. The cost of getting this one wrong is one
    extra tick; the cost of getting it wrong the other way is the failure
    this module exists to prevent.
    """
    try:
        data = json.loads(path.read_text())
        return int(data["pid"]), float(data["acquired_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _reclaim(path: Path) -> bool:
    """Drop an abandoned lock and take it. Returns False if another tick got
    there first — losing that race means somebody else holds a fresh lock,
    which is a perfectly good outcome: we skip."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return _publish(path)


def _publish(path: Path) -> bool:
    """Make `path` exist, fully populated, only if it does not already.

    `os.link` is the primitive rather than `O_CREAT | O_EXCL`, and the
    difference is not academic. With O_EXCL the file exists the instant it is
    created and is filled a moment later, so a concurrent tick can open it
    inside that window, read zero bytes, judge it unreadable and take a lock
    somebody else legitimately holds. That is not hypothetical: running
    twenty real ticks at once produced exactly one "previous lock
    unreadable" reclaim, which is a second tick running while the first was
    working. Linking an already-written temp file into place publishes the
    content and the name in one step, so no reader can observe a half-made
    lock.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "acquired_at": time.time()}) + "\n")
    try:
        os.link(tmp, path)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


# The lock can legitimately vanish underneath us: the holder finishes and
# releases between our failed `link` and our read of the file. That is an
# ordinary race with a correct outcome (try again), not a stale lock, and
# reporting it as one made a normal handover look like a crash recovery.
# Bounded so a pathological churn cannot spin here.
_ACQUIRE_ATTEMPTS = 3


def acquire(root: Path) -> LockAcquired | LockBusy:
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    for _ in range(_ACQUIRE_ATTEMPTS):
        if _publish(path):
            return LockAcquired(path=path)

        if not path.exists():
            continue  # released between our link and our look — just retry

        holder = _read_holder(path)
        if holder is None:
            if not _reclaim(path):
                return LockBusy(
                    holder_pid=-1,
                    held_for_seconds=0.0,
                    detail="it took the lock first",
                )
            return LockAcquired(
                path=path,
                reclaimed_from=None,
                reclaim_reason="previous lock unreadable",
            )
        return _decide(path, holder)

    return LockBusy(
        holder_pid=-1,
        held_for_seconds=0.0,
        detail=f"lock changed hands {_ACQUIRE_ATTEMPTS} times while acquiring",
    )


def _decide(path: Path, holder: tuple[int, float]) -> LockAcquired | LockBusy:
    pid, acquired_at = holder
    age = max(time.time() - acquired_at, 0.0)

    if not process_is_running(pid):
        if not _reclaim(path):
            return LockBusy(
                holder_pid=pid, held_for_seconds=age, detail="it took the lock first"
            )
        return LockAcquired(
            path=path,
            reclaimed_from=pid,
            reclaim_reason=f"pid {pid} is no longer running",
        )

    if age >= _PID_REUSE_BACKSTOP_SECONDS:
        # Alive, but far past anything a tick can legitimately spend. Either
        # the pid was recycled onto an unrelated process, or a tick is wedged
        # in a way its own timeouts did not catch. Both need the scheduler
        # back, and both are worth saying out loud.
        if not _reclaim(path):
            return LockBusy(holder_pid=pid, held_for_seconds=age)
        return LockAcquired(
            path=path,
            reclaimed_from=pid,
            reclaim_reason=(
                f"lock held by pid {pid} for {_human(age)}, beyond the "
                f"{int(_PID_REUSE_BACKSTOP_SECONDS)}s ceiling for any single tick"
            ),
        )

    return LockBusy(holder_pid=pid, held_for_seconds=age)


def release(root: Path) -> None:
    """Remove the lock if this process still owns it.

    Ownership is re-checked rather than assumed: if a later tick decided our
    lock was stale and took it, deleting the file would strip the lock off
    whoever legitimately holds it now."""
    path = lock_path(root)
    holder = _read_holder(path)
    if holder is not None and holder[0] != os.getpid():
        return
    path.unlink(missing_ok=True)


@contextmanager
def held(root: Path) -> Iterator[LockAcquired | LockBusy]:
    """Acquire for the duration of the block, always releasing what we took.

    Yields the outcome instead of raising on contention — a busy scheduler
    is an ordinary result the caller reports, not an error. Release runs in
    `finally` so a crash inside the tick still frees the lock; the pid check
    in `release` covers the case where it does not (a SIGKILL), by making the
    abandoned file reclaimable rather than permanent.
    """
    outcome = acquire(root)
    try:
        yield outcome
    finally:
        if isinstance(outcome, LockAcquired):
            release(root)
