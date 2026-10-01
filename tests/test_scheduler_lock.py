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

from rite_ai import kernel_lock
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
        assert run_tick(root).skipped is False
        assert isinstance(lock.acquire(root), lock.LockAcquired)

    def test_the_lock_is_released_even_if_the_tick_raises(self, tmp_path):
        root = _project(tmp_path)
        try:
            with lock.held(root):
                raise RuntimeError("tick blew up")
        except RuntimeError:
            pass
        assert isinstance(lock.acquire(root), lock.LockAcquired)

    def test_the_lock_file_is_never_deleted(self, tmp_path):
        """Deleting a lock file lets the next opener lock a fresh inode while
        the old one is still held. So releasing must leave it in place."""
        root = _project(tmp_path)
        with lock.held(root) as outcome:
            assert isinstance(outcome, lock.LockAcquired)
            inode = lock.lock_path(root).stat().st_ino
        assert lock.lock_path(root).stat().st_ino == inode


class TestAKilledTickNeverWedgesTheScheduler:
    """A tick killed with SIGKILL runs no cleanup. If its lock could outlive
    it, that would be the permanent wedge the lock exists to prevent. The
    kernel drops an flock when its holder exits, so there is nothing to
    reclaim and nothing to decide."""

    def test_a_killed_holder_frees_the_lock(self, tmp_path):
        root = _project(tmp_path)
        holder = _hold_in_another_process(root)
        assert isinstance(lock.acquire(root), lock.LockBusy)

        holder.kill()
        holder.wait()

        outcome = lock.acquire(root)
        assert isinstance(outcome, lock.LockAcquired)

    def test_a_record_left_by_a_killed_holder_is_not_a_holder(self, tmp_path):
        """The pid in the file is for messages only. A leftover record, even
        one naming a live process, must not make the lock busy. That was
        the old design's proxy for "held"."""
        root = _project(tmp_path)
        lock.lock_path(root).write_text(
            json.dumps({"pid": os.getpid(), "acquired_at": time.time()})
        )
        assert isinstance(lock.acquire(root), lock.LockAcquired)

    def test_a_0_6_0_lockfile_is_ignored(self, tmp_path):
        root = _project(tmp_path)
        (root / ".rite" / "scheduler.lock").write_text(
            json.dumps({"pid": os.getpid(), "acquired_at": time.time()})
        )
        assert isinstance(lock.acquire(root), lock.LockAcquired)

    def test_a_subprocess_of_the_tick_does_not_inherit_the_lock(self, tmp_path):
        """A tick starts subprocesses. One that inherited the descriptor
        would keep the lock after the tick ended, for as long as it lived.
        `close_fds=False` so the descriptor's own close-on-exec flag is what
        is being tested."""
        root = _project(tmp_path)
        with lock.held(root) as outcome:
            assert isinstance(outcome, lock.LockAcquired)
            child = subprocess.Popen(["sleep", "30"], close_fds=False)
        try:
            assert isinstance(lock.acquire(root), lock.LockAcquired)
        finally:
            child.kill()
            child.wait()


class TestALiveHolderIsNeverRobbed:
    """0.6.0 took the lock from a live holder after 900 s in case its pid had
    been recycled. That is two ticks running at once by design."""

    def test_an_old_lock_held_by_a_live_process_stays_held(self, tmp_path):
        root = _project(tmp_path)
        holder = _hold_in_another_process(root)
        try:
            # Make the record say it has been held for a day.
            lock.lock_path(root).write_text(
                json.dumps({"pid": holder.pid, "acquired_at": time.time() - 86400})
            )
            outcome = lock.acquire(root)
            assert isinstance(outcome, lock.LockBusy)
            assert outcome.holder_pid == holder.pid
            assert "longer than any tick should take" in outcome.summary
            assert f"pid {holder.pid}" in outcome.summary
        finally:
            holder.kill()
            holder.wait()

    def test_a_holder_that_has_not_recorded_itself_is_still_a_holder(self, tmp_path):
        """The record is written just after the lock is taken. A tick refused
        in between finds no record, and must say so, not guess a pid."""
        root = _project(tmp_path)
        with lock.held(root):
            os.truncate(lock.lock_path(root), 0)
            outcome = lock.acquire(root)
        assert isinstance(outcome, lock.LockBusy)
        assert outcome.holder_pid is None
        assert "has not recorded its pid yet" in outcome.summary

    def test_the_held_too_long_notice_exceeds_every_bounded_wait_in_the_package(
        self,
    ):
        """It only changes the wording of a skip, but a merely slow tick must
        never be described as stuck, so it sits above the longest thing any
        code here can legitimately wait for. Asserted as a relationship
        rather than restated as a number."""
        timeouts = [
            int(m)
            for p in SRC.rglob("*.py")
            for m in re.findall(r"timeout=(\d+)", p.read_text())
        ]
        assert timeouts, "found no timeouts to compare against — test is not testing"
        assert lock._HELD_TOO_LONG_SECONDS > max(timeouts)


class TestNoExclusionMeansNoTick:
    """Where rite cannot establish exclusion, the tick does not run, and it
    says why. Running anyway is the race."""

    def test_a_filesystem_where_flock_is_a_no_op_is_refused(
        self, tmp_path, monkeypatch
    ):
        """What some network and VM-shared filesystems do: every flock is
        granted. The self-test on the held file must catch it."""
        root = _project(tmp_path)
        monkeypatch.setattr(kernel_lock.fcntl, "flock", lambda fd, op: None)

        outcome = lock.acquire(root)

        assert isinstance(outcome, lock.LockUnavailable)
        assert "does not exclude" in outcome.reason

    def test_a_refused_tick_does_no_work_and_is_not_ok(self, tmp_path, monkeypatch):
        from rite_ai.scheduler import LAST_TICK_FILENAME

        root = _project(tmp_path)
        monkeypatch.setattr(kernel_lock.fcntl, "flock", lambda fd, op: None)

        result = run_tick(root)

        assert result.ok is False
        assert result.skipped is True
        assert "did not run" in " ".join(result.messages)
        assert not (root / ".rite" / LAST_TICK_FILENAME).exists()

    def test_a_filesystem_without_flock_is_refused(self, tmp_path, monkeypatch):
        import errno

        def unsupported(fd, op):
            raise OSError(errno.ENOLCK, "No locks available")

        root = _project(tmp_path)
        monkeypatch.setattr(kernel_lock.fcntl, "flock", unsupported)

        outcome = lock.acquire(root)

        assert isinstance(outcome, lock.LockUnavailable)
        assert "does not support flock" in outcome.reason

    def test_a_symlink_at_the_lock_path_is_refused(self, tmp_path):
        root = _project(tmp_path)
        elsewhere = tmp_path / "elsewhere.lock"
        elsewhere.write_text("")
        lock.lock_path(root).symlink_to(elsewhere)

        assert isinstance(lock.acquire(root), lock.LockUnavailable)

    def test_a_lock_file_replaced_while_being_taken_is_refused(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        real_flock = kernel_lock.fcntl.flock

        def flock_then_replace(fd, op):
            real_flock(fd, op)
            path = lock.lock_path(root)
            if os.fstat(fd).st_ino == path.stat().st_ino:
                path.unlink()
                path.write_text("")

        monkeypatch.setattr(kernel_lock.fcntl, "flock", flock_then_replace)

        outcome = lock.acquire(root)

        assert isinstance(outcome, lock.LockUnavailable)
        assert "replaced" in outcome.reason

    def test_a_lock_file_deleted_during_a_tick_is_reported(self, tmp_path):
        """Nothing in rite deletes it, but if something else does, a tick
        that started meanwhile may have run alongside this one. That cannot
        be undone. It must not be silent."""
        root = _project(tmp_path)
        with lock.held(root) as outcome:
            lock.lock_path(root).unlink()
        assert isinstance(outcome, lock.LockAcquired)
        assert any("deleted or replaced" in w for w in outcome.warnings)


def _hold_in_another_process(root: Path) -> subprocess.Popen:
    """A real second process holding the lock until it is killed. It prints
    once it holds it, so the caller never races its startup."""
    import sys

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from rite_ai.scheduler import lock\n"
            "o = lock.acquire(Path(sys.argv[1]))\n"
            "print(type(o).__name__, flush=True)\n"
            "time.sleep(600)\n",
            str(root),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "LockAcquired"
    return proc


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


class TestContentionYieldsOneHolder:
    def test_twenty_concurrent_acquires_yield_exactly_one_holder(self, tmp_path):
        """Contention may not produce two winners."""
        import multiprocessing

        root = _project(tmp_path)

        with multiprocessing.Pool(8) as pool:
            outcomes = pool.map(_try_acquire, [str(root)] * 20)

        assert sum(1 for got in outcomes if got) == 1, (
            f"expected exactly one holder, got {sum(1 for g in outcomes if g)}"
        )

    def test_sustained_contention_never_overlaps_two_holders(self, tmp_path):
        """The measurement that found the 0.6.0 lock admitting 1,352–1,617
        overlapping holders in 20 s, shortened: processes acquire and release
        as fast as they can, each records when it held the lock, and no two
        of those intervals may intersect. A single overlap fails it."""
        import multiprocessing

        root = _project(tmp_path)
        with multiprocessing.Pool(4) as pool:
            runs = pool.map(_hold_repeatedly, [(str(root), 2.0)] * 4)

        unavailable = [reason for _, reasons in runs for reason in reasons]
        assert not unavailable, unavailable
        intervals = sorted(i for held, _ in runs for i in held)
        assert len(intervals) > 100, "too few acquisitions to mean anything"
        overlaps = [(a, b) for a, b in zip(intervals, intervals[1:]) if b[0] < a[1]]
        assert not overlaps, f"{len(overlaps)} overlapping holders"


def _hold_repeatedly(args: tuple[str, float]) -> tuple[list, list]:
    root, seconds = Path(args[0]), args[1]
    held, unavailable = [], []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with lock.held(root) as outcome:
            if isinstance(outcome, lock.LockUnavailable):
                unavailable.append(outcome.reason)
            elif isinstance(outcome, lock.LockAcquired):
                start = time.monotonic_ns()
                time.sleep(0.0005)
                held.append((start, time.monotonic_ns()))
    return held, unavailable


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


def test_the_schedule_runs_the_rite_that_installed_it(tmp_path):
    """Decided in the v0.6.0 wiring audit: an OS schedule is baked at install
    and run later by launchd or cron, whose PATH lacks ~/.local/bin, so the
    binary is absolute and is the rite that ran the install, never the first
    one on the installer's PATH (the broker's defect, f4afcd1, in another
    place)."""
    from rite_ai import own_command
    from rite_ai.scheduler import _cron_line, _launchd_plist_content, _rite_binary

    assert _rite_binary() == own_command() != "rite"
    assert f"&& {own_command()} " in _cron_line(tmp_path, 5)
    assert f"<string>{own_command()}</string>" in _launchd_plist_content(tmp_path, 5)
