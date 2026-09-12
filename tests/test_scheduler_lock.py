"""Two ticks must not overlap, and the log must not grow forever.

Both are "compounds over time" defects: invisible on an idle machine, worst
on a busy one, and only observable after a long unattended run — which is
exactly the run nobody watches.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from rite_ai.scheduler import lock, run_tick
from rite_ai.scheduler.logfile import (
    KEEP,
    MAX_BYTES,
    log_path,
    rotate_if_needed,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "rite_ai"


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestOnlyOneTickAtATime:
    def test_a_second_tick_skips_while_the_first_holds_the_lock(self, tmp_path):
        root = _project(tmp_path)
        with lock.held(root) as first:
            assert isinstance(first, lock.LockAcquired)
            result = run_tick(root)

        assert result.skipped is True
        assert result.ok is True  # contention is not a fault

    def test_a_skipped_tick_says_so_and_names_the_holder(self, tmp_path):
        """A silent skip is the same defect as the empty scheduler log: from
        the log alone, a skipped tick and a scheduler that never fired must
        not look alike."""
        root = _project(tmp_path)
        with lock.held(root):
            result = run_tick(root)

        assert result.messages, "a skipped tick must still report something"
        joined = " ".join(result.messages)
        assert "skipped" in joined
        assert str(os.getpid()) in joined

    def test_the_lock_is_released_when_the_tick_finishes(self, tmp_path):
        root = _project(tmp_path)
        run_tick(root)
        assert not lock.lock_path(root).exists()
        assert run_tick(root).skipped is False

    def test_the_lock_is_released_even_if_the_tick_raises(self, tmp_path):
        root = _project(tmp_path)
        try:
            with lock.held(root):
                raise RuntimeError("tick blew up")
        except RuntimeError:
            pass
        assert not lock.lock_path(root).exists()


class TestAStaleLockNeverWedgesTheScheduler:
    """A tick killed with SIGKILL leaves its lockfile behind. If that could
    hold the scheduler forever it would be the same permanent wedge the lock
    exists to prevent."""

    def test_a_lock_from_a_dead_process_is_reclaimed(self, tmp_path):
        root = _project(tmp_path)
        dead = _a_definitely_dead_pid()
        lock.lock_path(root).write_text(
            json.dumps({"pid": dead, "acquired_at": time.time()})
        )

        outcome = lock.acquire(root)

        assert isinstance(outcome, lock.LockAcquired)
        assert outcome.reclaimed_from == dead

    def test_reclaiming_is_reported_not_silent(self, tmp_path):
        root = _project(tmp_path)
        lock.lock_path(root).write_text(
            json.dumps({"pid": _a_definitely_dead_pid(), "acquired_at": time.time()})
        )
        result = run_tick(root)
        assert any("reclaimed stale scheduler lock" in m for m in result.messages)

    def test_a_live_process_keeps_its_lock(self, tmp_path):
        """The inverse: liveness must actually be checked, or the lock is
        decorative."""
        root = _project(tmp_path)
        lock.lock_path(root).write_text(
            json.dumps({"pid": os.getpid(), "acquired_at": time.time()})
        )
        assert isinstance(lock.acquire(root), lock.LockBusy)

    def test_an_unreadable_lock_is_reclaimed_rather_than_wedging(self, tmp_path):
        """Opposite rule to claims.json on purpose — see `_read_holder`. A
        corrupt lock read as "held" needs a human with `rm` to recover."""
        root = _project(tmp_path)
        lock.lock_path(root).write_text("{ not json")
        assert isinstance(lock.acquire(root), lock.LockAcquired)

    def test_release_does_not_steal_a_lock_another_tick_now_owns(self, tmp_path):
        root = _project(tmp_path)
        lock.lock_path(root).write_text(
            json.dumps({"pid": _a_definitely_dead_pid(), "acquired_at": time.time()})
        )
        lock.release(root)
        assert lock.lock_path(root).exists(), (
            "release must only remove a lock this process owns"
        )

    def test_the_pid_reuse_backstop_exceeds_every_bounded_wait_in_the_package(self):
        """The one time-based rule, asserted as a relationship rather than
        restated as a number. It must never be able to fire on a tick that is
        merely slow, so it has to sit above the longest thing any code here
        can legitimately wait for."""
        timeouts = [
            int(m)
            for p in SRC.rglob("*.py")
            for m in re.findall(r"timeout=(\d+)", p.read_text())
        ]
        assert timeouts, "found no timeouts to compare against — test is not testing"
        assert lock._PID_REUSE_BACKSTOP_SECONDS > max(timeouts)


def _a_definitely_dead_pid() -> int:
    """A pid that has certainly exited — spawned, waited on, and reaped, so
    the kernel is not holding it as a zombie that `kill(pid, 0)` would still
    find. Real rather than a made-up high number, which a busy machine could
    legitimately have assigned to something."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    assert not lock.process_is_running(proc.pid)
    return proc.pid


class TestTheSchedulerLogIsBounded:
    def test_an_oversized_log_is_rotated(self, tmp_path):
        root = _project(tmp_path)
        path = log_path(root)
        path.write_text("x" * (MAX_BYTES + 1))

        message = rotate_if_needed(path)

        assert message is not None
        assert path.stat().st_size == 0
        assert path.with_suffix(".log.1").stat().st_size == MAX_BYTES + 1

    def test_a_small_log_is_left_alone(self, tmp_path):
        path = log_path(_project(tmp_path))
        path.write_text("one line\n")
        assert rotate_if_needed(path) is None
        assert path.read_text() == "one line\n"

    def test_rotation_keeps_a_bounded_history_and_drops_the_oldest(self, tmp_path):
        path = log_path(_project(tmp_path))
        for generation in range(KEEP + 2):
            path.write_text(f"generation {generation} " + "x" * MAX_BYTES)
            rotate_if_needed(path)

        archives = sorted(p.name for p in path.parent.glob("scheduler.log.*"))
        assert archives == [f"scheduler.log.{i}" for i in range(1, KEEP + 1)]
        # Newest archive holds the most recent pre-rotation content.
        assert "generation 4" in path.with_suffix(".log.1").read_text()

    def test_rotation_truncates_in_place_so_an_open_writer_keeps_working(
        self, tmp_path
    ):
        """cron appends via `>>` and launchd holds StandardOutPath open.
        Renaming the file would leave both writing to the rotated inode — a
        log that looks rotated while growing forever somewhere else."""
        path = log_path(_project(tmp_path))
        path.write_text("x" * (MAX_BYTES + 1))
        inode_before = path.stat().st_ino

        with open(path, "a") as writer:
            rotate_if_needed(path)
            writer.write("written after rotation\n")
            writer.flush()

        assert path.stat().st_ino == inode_before
        assert "written after rotation" in path.read_text()

    def test_rotation_never_raises_on_a_broken_path(self, tmp_path):
        """A scheduler must not die because it could not tidy its own log."""
        assert rotate_if_needed(tmp_path / "nope" / "scheduler.log") is None


class TestTheLockIsNeverObservedHalfMade:
    """Found by running twenty real ticks at once, not by the suite: with
    `O_CREAT | O_EXCL` the lockfile exists before its contents are written,
    so a concurrent tick could read zero bytes, call it unreadable and take
    a lock that was legitimately held."""

    def test_a_concurrent_reader_never_sees_an_empty_lockfile(self, tmp_path):
        root = _project(tmp_path)
        outcome = lock.acquire(root)
        assert isinstance(outcome, lock.LockAcquired)
        # The instant the path exists it must already identify its owner.
        assert lock._read_holder(lock.lock_path(root)) is not None

    def test_no_temp_files_are_left_behind(self, tmp_path):
        root = _project(tmp_path)
        lock.acquire(root)
        lock.release(root)
        assert list((root / ".rite").glob(".scheduler.lock*")) == []

    def test_twenty_concurrent_acquires_yield_exactly_one_holder(self, tmp_path):
        """The property the real-world run demonstrated: contention may not
        produce two winners."""
        import multiprocessing

        root = _project(tmp_path)

        with multiprocessing.Pool(8) as pool:
            outcomes = pool.map(_try_acquire, [str(root)] * 20)

        assert sum(1 for got in outcomes if got) == 1, (
            f"expected exactly one holder, got {sum(1 for g in outcomes if g)}"
        )


def _try_acquire(root_str: str) -> bool:
    """Acquire without releasing — every caller that wins keeps it, so the
    count of winners is the count of simultaneous holders."""
    return isinstance(lock.acquire(Path(root_str)), lock.LockAcquired)


class TestBothSchedulerBackendsWriteToTheRotatedFile:
    """Rotation that covers cron but not launchd (or the reverse) leaves the
    log unbounded in whichever configuration is actually in use, while
    looking fixed. launchd is the backend on macOS, cron everywhere else, so
    a one-sided fix is invisible to whoever did it."""

    def test_the_cron_redirect_targets_the_rotated_file(self, tmp_path):
        from rite_ai.scheduler import _cron_line

        assert str(log_path(tmp_path)) in _cron_line(tmp_path, 5)

    def test_both_launchd_stream_paths_target_the_rotated_file(self, tmp_path):
        from rite_ai.scheduler import _launchd_plist_content

        plist = _launchd_plist_content(tmp_path, 5)
        streams = re.findall(
            r"<key>Standard(?:Out|Error)Path</key><string>([^<]+)</string>", plist
        )
        assert len(streams) == 2, "expected both stdout and stderr paths"
        assert set(streams) == {str(log_path(tmp_path))}
