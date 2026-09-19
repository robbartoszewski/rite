"""A SIGTERM'd probe must still destroy its sandbox.

Ctrl-C was always safe: SIGINT raises `KeyboardInterrupt`, so `verify`'s
`finally` runs. SIGTERM is the one that leaks, and SIGTERM is what every
harness sends to stop a background job — so the ordinary way of abandoning
a slow suite was the one way of ending it that left external state behind.

Observed rather than reasoned: a killed suite left the sandbox
`rite-selftest-4751-d0775d81`, where 4751 was the pid of the process that
had just been killed. The orphan counts against `machine.max_sandboxes`,
so a leaked probe can make a later `start_worker` refuse.

These run a REAL child process and send it a REAL signal. A test that
called the handler directly would prove the handler exists, not that the
signal reaches it — and "the signal never arrived" is the entire defect.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHILD = """
import os, signal, sys, time
sys.path.insert(0, {root!r})
from rite_ai.sandbox import _torn_down_on_sigterm

marker = {marker!r}

def body():
    with _torn_down_on_sigterm():
        try:
            print("READY", flush=True)
            time.sleep(30)
        finally:
            # Stands in for `verify`'s teardown: the thing that must run
            # even though the process is being terminated.
            open(marker, "w").write("torn down")

try:
    body()
except SystemExit as e:
    open(marker + ".code", "w").write(str(e.code))
"""


def _spawn(tmp_path: Path) -> tuple[subprocess.Popen, Path]:
    marker = tmp_path / "teardown"
    src = CHILD.format(root=str(ROOT / "src"), marker=str(marker))
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(src)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "READY", "child never started"
    return proc, marker


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX")
def test_sigterm_still_runs_the_teardown(tmp_path: Path):
    proc, marker = _spawn(tmp_path)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)

    assert marker.is_file(), (
        "the process was SIGTERM'd and its teardown never ran — this is the "
        "leak exactly: a `finally` does not execute when the interpreter is "
        "stopped by a signal, so the sandbox outlives the run that made it"
    )
    assert marker.read_text() == "torn down"


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX")
def test_it_exits_with_the_conventional_sigterm_code(tmp_path: Path):
    """143, what a shell reports for a SIGTERM'd child. The teardown gets to
    run; what the signal MEANS is unchanged."""
    proc, marker = _spawn(tmp_path)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30)
    code = Path(str(marker) + ".code")
    assert code.is_file(), "SystemExit never propagated"
    assert code.read_text() == str(128 + signal.SIGTERM)


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is POSIX")
def test_the_previous_handler_is_restored(tmp_path: Path):
    """The window is scoped. A library that leaves a process-wide handler
    installed has traded one surprising side effect for another."""
    src = textwrap.dedent(f"""
        import signal, sys
        sys.path.insert(0, {str(ROOT / "src")!r})
        from rite_ai.sandbox import _torn_down_on_sigterm

        def mine(signum, frame):
            pass

        signal.signal(signal.SIGTERM, mine)
        with _torn_down_on_sigterm():
            inside = signal.getsignal(signal.SIGTERM)
        after = signal.getsignal(signal.SIGTERM)
        assert inside is not mine, "the handler was never installed"
        assert after is mine, "the previous handler was not restored"
        print("OK")
    """)
    done = subprocess.run(
        [sys.executable, "-c", src], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 0, done.stderr
    assert "OK" in done.stdout


def test_off_the_main_thread_it_does_nothing_rather_than_raising():
    """`signal.signal` raises off the main thread. The probe must keep the
    behaviour it had rather than failing outright — the teardown still
    covers every ordinary exit there."""
    import threading

    from rite_ai.sandbox import _torn_down_on_sigterm

    ran = []

    def worker():
        with _torn_down_on_sigterm():
            ran.append("body")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert ran == ["body"], "the context manager broke on a worker thread"
