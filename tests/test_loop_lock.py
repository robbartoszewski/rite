"""One loop per project, held by the kernel: the lock's own properties.

The pid file this replaced admitted 51,120–52,154 overlapping holders per
20 s (8 processes, macOS, `4f7e1d2`), and read a zombie or an unrelated
live pid as a running loop. See `rite_ai.loop.lock` for the design, the
gate included, and why it exists.

The two measurements that argued for it are here in short form: sustained
contention may produce no overlap, and readers asking "is it up" may never
make a lone loop's start fail. Both were checked against a control that
breaks the property (the harness counted 40,606 overlaps with `flock` a
no-op, and 220,511–222,523 false refusals with the gate removed), so a pass
here is the property holding, not the harness seeing nothing.
"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path

from rite_ai import kernel_lock
from rite_ai.loop import lock


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _hold_in_another_process(root: Path, what: str = "lock") -> subprocess.Popen:
    """A real second process holding the loop lock (or only the gate) until
    killed. It prints once it holds it, so the caller never races startup."""
    body = (
        "o = lock.acquire(root)\nprint(type(o).__name__, flush=True)\n"
        if what == "lock"
        else "import fcntl, os\n"
        "fd = os.open(lock.gate_path(root), os.O_RDWR | os.O_CREAT)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('LockAcquired', flush=True)\n"
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\nfrom pathlib import Path\n"
            "from rite_ai.loop import lock\nroot = Path(sys.argv[1])\n"
            + body
            + "time.sleep(600)\n",
            str(root),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "LockAcquired"
    return proc


class TestOneLoopPerProject:
    def test_a_second_loop_is_refused_and_told_the_exact_holder(self, tmp_path):
        root = _project(tmp_path)
        loop = _hold_in_another_process(root)
        try:
            outcome = lock.acquire(root)
            seen = lock.holder(root)
        finally:
            loop.kill()
            loop.wait()

        assert isinstance(outcome, lock.LockBusy)
        assert outcome.holder_pid == loop.pid
        assert (seen.known, seen.running, seen.pid) == (True, True, loop.pid)

    def test_a_killed_loop_frees_the_project(self, tmp_path):
        root = _project(tmp_path)
        loop = _hold_in_another_process(root)
        loop.kill()
        loop.wait()

        assert lock.holder(root) == lock.Holder(known=True, running=False)
        held = lock.acquire(root)
        assert isinstance(held, lock.LockAcquired)
        lock.release(held)

    def test_releasing_leaves_the_files_and_clears_the_record(self, tmp_path):
        """Never deleted: a lock file deleted while held lets the next opener
        lock a fresh inode, the defect in another form."""
        root = _project(tmp_path)
        held = lock.acquire(root)
        assert isinstance(held, lock.LockAcquired)
        inode = lock.lock_path(root).stat().st_ino
        lock.release(held)

        assert lock.lock_path(root).stat().st_ino == inode
        assert lock.lock_path(root).read_text() == ""
        assert lock.gate_path(root).exists()

    def test_a_subprocess_of_the_loop_does_not_inherit_the_lock(self, tmp_path):
        """The loop starts Worker sessions. One that inherited the descriptor
        would keep the project locked after the loop ended. `close_fds=False`
        so the descriptor's own close-on-exec flag is what is tested."""
        root = _project(tmp_path)
        held = lock.acquire(root)
        assert isinstance(held, lock.LockAcquired)
        child = subprocess.Popen(["sleep", "30"], close_fds=False)
        lock.release(held)
        try:
            assert lock.holder(root) == lock.Holder(known=True, running=False)
        finally:
            child.kill()
            child.wait()


class TestWhereItCannotKnowItSaysSo:
    def test_a_no_op_flock_is_refused_and_reported_as_unknown(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        lock.lock_path(root).write_text("")
        monkeypatch.setattr(kernel_lock.fcntl, "flock", lambda fd, op: None)

        outcome = lock.acquire(root)
        seen = lock.holder(root)

        assert isinstance(outcome, lock.LockUnavailable)
        assert "does not exclude" in outcome.reason
        assert not seen.known

    def test_a_gate_held_by_a_stopped_process_is_unknown_not_a_guess(
        self, tmp_path, monkeypatch
    ):
        """A `rite status` suspended with Ctrl-Z mid-look holds the gate. The
        answer is "cannot tell", after a bounded wait, never "not running"."""
        root = _project(tmp_path)
        lock.lock_path(root).write_text("")
        monkeypatch.setattr(lock, "GATE_WAIT_SECONDS", 0.2)
        stuck = _hold_in_another_process(root, what="gate")
        try:
            outcome = lock.acquire(root)
            seen = lock.holder(root)
        finally:
            stuck.kill()
            stuck.wait()

        assert isinstance(outcome, lock.LockUnavailable)
        assert "held for over" in outcome.reason
        assert not seen.known

    def test_a_symlink_at_the_lock_path_is_refused(self, tmp_path):
        root = _project(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.write_text("")
        lock.lock_path(root).symlink_to(elsewhere)

        assert isinstance(lock.acquire(root), lock.LockUnavailable)
        assert not lock.holder(root).known


# --- the two measurements, short ---------------------------------------------------


def _contend(args) -> tuple[list, int]:
    root, seconds = Path(args[0]), args[1]
    held, unavailable = [], 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        outcome = lock.acquire(root)
        if isinstance(outcome, lock.LockUnavailable):
            unavailable += 1
        elif isinstance(outcome, lock.LockAcquired):
            start = time.monotonic_ns()
            time.sleep(0.0005)
            held.append((start, time.monotonic_ns()))
            lock.release(outcome)
    return held, unavailable


def test_sustained_contention_never_overlaps_two_loops(tmp_path):
    root = _project(tmp_path)
    with mp.Pool(4) as pool:
        runs = pool.map(_contend, [(str(root), 2.0)] * 4)

    assert sum(u for _, u in runs) == 0
    intervals = sorted(i for held, _ in runs for i in held)
    assert len(intervals) > 100, "too few acquisitions to mean anything"
    overlaps = [(a, b) for a, b in zip(intervals, intervals[1:]) if b[0] < a[1]]
    assert not overlaps, f"{len(overlaps)} overlapping loops"


def _lone_loop(args) -> tuple[int, int, int]:
    root, seconds = Path(args[0]), args[1]
    took = refused = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        outcome = lock.acquire(root)
        if isinstance(outcome, lock.LockAcquired):
            took += 1
            lock.release(outcome)
        else:
            refused += 1
    return os.getpid(), took, refused


def _look(args) -> tuple[int, int, set]:
    root, seconds = Path(args[0]), args[1]
    unknown = running = 0
    pids: set[int] = set()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        seen = lock.holder(root)
        if not seen.known:
            unknown += 1
        elif seen.running:
            running += 1
            pids.add(seen.pid)
    return unknown, running, pids


def test_readers_never_make_a_lone_loop_fail_to_start(tmp_path):
    """One loop, starting and stopping as fast as it can, while readers ask
    whether it is up. With a single loop, ANY refusal is false, and every
    "running" answer must name that loop."""
    root = _project(tmp_path)
    with mp.Pool(5) as pool:
        loop = pool.apply_async(_lone_loop, ((str(root), 2.0),))
        readers = [pool.apply_async(_look, ((str(root), 2.0),)) for _ in range(4)]
        loop_pid, took, refused = loop.get()
        looks = [r.get() for r in readers]

    assert took > 100, "too few starts to mean anything"
    assert refused == 0, f"a lone loop was refused {refused} times"
    assert sum(u for u, _, _ in looks) == 0
    assert sum(r for _, r, _ in looks) > 0, "no reader ever saw it running"
    assert set().union(*(p for _, _, p in looks)) == {loop_pid}


def test_without_the_gate_readers_do_make_it_fail(tmp_path, monkeypatch):
    """The control, in-process so the patch reaches both sides: a reader's
    probe at the instant of a take makes the take fail. Interleaved by hand
    rather than raced, so it fails every time the gate is missing, and shows
    what the gate is for."""
    root = _project(tmp_path)
    lock.lock_path(root).write_text("")
    monkeypatch.setattr(lock, "gate", lambda path, wait: contextlib.nullcontext())
    reader_fd = kernel_lock.open_lock_file(lock.lock_path(root))
    try:
        assert not kernel_lock.refused(reader_fd)  # a reader's probe, mid-look
        outcome = lock.acquire(root)  # a loop starting at that instant
    finally:
        os.close(reader_fd)

    assert isinstance(outcome, lock.LockUnavailable | lock.LockBusy)
