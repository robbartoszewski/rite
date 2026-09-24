""" "Could not ask tmux" is not "no loop is running" (C15).

`loop/session.is_alive` returned a bare bool, so a `has-session` that timed
out read as "no session". Measured through `rite doctor` with the loop's
session present and the tmux server frozen: `loop: not running (rite loop
start)` — telling the reader to start a second loop — and `rite loop start`
got past its duplicate check. `managers/session.py` fixed this shape with a
three-state `Liveness`; the loop path never got it.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

import pytest

import rite_ai.loop.session as loop

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="no tmux")


@pytest.fixture
def frozen_loop(monkeypatch):
    """The loop's session exists on a tmux server that does not answer.

    Its OWN server, so freezing it cannot stall the suite's shared one."""
    monkeypatch.setenv("TMUX_TMPDIR", tempfile.mkdtemp(prefix="fz", dir="/tmp"))
    monkeypatch.setattr(loop, "LIVENESS_TIMEOUT", 1.0)
    root = Path(tempfile.mkdtemp(prefix="frozen-"))
    (root / ".rite").mkdir()
    name = loop.session_name(root)
    subprocess.run(["tmux", "new-session", "-d", "-s", name, "sleep 60"], check=True)
    pid = int(
        subprocess.run(
            ["tmux", "display", "-p", "#{pid}"], capture_output=True, text=True
        ).stdout
    )
    os.kill(pid, signal.SIGSTOP)
    try:
        yield root
    finally:
        os.kill(pid, signal.SIGCONT)
        subprocess.run(["tmux", "kill-server"], capture_output=True)


def test_status_says_it_cannot_tell(frozen_loop):
    got = loop.status(frozen_loop)
    assert got.unknown and not got.running
    assert "cannot tell" in " ".join(got.lines())
    assert "not running" not in " ".join(got.lines())


def test_start_refuses_rather_than_make_a_second(frozen_loop):
    got = loop.start(frozen_loop, command="sleep 60")
    assert isinstance(got, loop.Refused), got
    assert "could make two" in got.reason


@pytest.mark.parametrize(
    "stderr",
    [
        "can't find session: x",
        "no server running on /tmp/tmux-501/default",
        "error connecting to /tmp/x/tmux-501/default (No such file or directory)",
    ],
    ids=["no-session", "no-server", "fresh-socket-dir"],
)
def test_tmuxs_ways_of_saying_no_are_still_no(monkeypatch, stderr):
    """Measured wordings. The third looks like a fault and is not: reading it
    as unknown would make `loop start` refuse on every machine with no tmux
    server running."""
    done = subprocess.CompletedProcess([], 1, stdout="", stderr=stderr + "\n")
    monkeypatch.setattr(loop.subprocess, "run", lambda *a, **k: done)
    got = loop.liveness("rite-loop-x")
    assert got.known and not got.alive
