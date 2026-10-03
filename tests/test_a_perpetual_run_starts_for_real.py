"""A run with no bound starts a REAL session (the a7 hotfix).

🔴 **Shipped broken in v0.7.0a7.** `rite start lead` with no bound (D-115, the
headline of #190) printed its banner and died:

    File ".../managers/session.py", line 344, in start
        if max_sessions <= 0:
    TypeError: '<=' not supported between instances of 'NoneType' and 'int'

A perpetual run passes `max_sessions=None` and `window_seconds=None` all the
way down, and `session.start` still assumed an int.

⚠ **Why #190's tests missed it, which is the part worth keeping.** Every
perpetual test drove `supervise` with a fake `starter`, so `None` never reached
the real `_default_starter` → `session.start`, the only path the CLI takes. The
same lesson as `test_status_sees_a_manager_the_cli_started`: a test that
supplies its own producer cannot see what the real one is handed. So these
start a real tmux session through the real starter, with no bound, the way
the CLI does. The engine is `sh`, so nothing is launched that spends.

⚠ **No stand-in session ends on a timer** (no tolerated races). A session that
ran `sleep N` raced `session.start`'s wall-clock settle window: `sleep 2`
against a 2.00 s window failed on the macOS runner, and a longer sleep only
narrows the same race. So each session here runs until the TEST ends it: the
starter's loops until it is killed, and the supervised one waits for a file
the test writes only after `session.start` has returned "started", which is
after the settle window by construction. The one timer left is a safety kill
that turns a hang into a failure, and the test asserts it never fired.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_HAS_TMUX = shutil.which("tmux") is not None

if os.environ.get("CI") == "true" and not _HAS_TMUX:
    raise RuntimeError(
        "tmux is missing in CI. These tests start a real session; skipping "
        "them in CI would retire the check silently."
    )

needs_tmux = pytest.mark.skipif(not _HAS_TMUX, reason="tmux is not installed")

RUNS_UNTIL_KILLED = "while :; do sleep 1; done"
"""A session that never ends by itself, so no settle window can outlast it."""

SAFETY_SECONDS = 120.0
"""Only on the FAILURE path: a session that was never released is killed, so
a broken test fails instead of hanging. Never part of a pass."""


def _kill(session: str) -> None:
    if session:
        subprocess.run(
            ["tmux", "kill-session", "-t", f"={session}"], capture_output=True
        )


def _project() -> tuple[Path, str]:
    root = Path(tempfile.mkdtemp(prefix="perpetual-real-"))
    (root / ".rite").mkdir()
    return root, f"lead{uuid.uuid4().hex[:6]}"


@needs_tmux
def test_the_real_starter_starts_a_session_with_no_bound():
    from rite_ai.managers import read_instance
    from rite_ai.managers.supervise import _default_starter

    root, manager = _project()
    session = ""
    try:
        result = _default_starter(
            root,
            manager,
            engine="sh",
            agent="",
            resume_id="",
            prompt=RUNS_UNTIL_KILLED,
            permission="",
            max_sessions=None,
            window_seconds=None,
        )
        assert result.ok, result.message
        session = result.session
        assert (
            subprocess.run(
                ["tmux", "has-session", "-t", f"={session}"], capture_output=True
            ).returncode
            == 0
        ), "no session is running, so this proves nothing"
        recorded = read_instance(root, manager)
        assert recorded is not None, "the run was not recorded"
        assert recorded.max_sessions is None and recorded.window_seconds is None
    finally:
        _kill(session)


@needs_tmux
def test_control_a_ceiling_of_zero_is_still_refused():
    """`None` is "no bound", not a free pass for any number: a bounded run
    whose ceiling permits no session is refused as before."""
    from rite_ai.managers.session import start

    root, manager = _project()
    result = start(root, manager, command=RUNS_UNTIL_KILLED, max_sessions=0)
    assert not result.ok
    assert "permits no sessions" in result.message
    _kill(getattr(result, "session", ""))


@needs_tmux
def test_a_perpetual_supervise_starts_its_first_session_for_real(monkeypatch):
    """The whole path the CLI takes: `supervise` with no bound and no fake
    starter, through `_default_starter` and `session.start` into tmux. The
    board has work, so the first pass starts a session. The session waits
    for a release file, which the test writes once the real starter has
    returned "started"; it then ends having changed nothing, the run waits,
    and the operator's Ctrl-C stops it."""
    import rite_ai.managers.supervise as sup
    from rite_ai.cli.main import LoopAnswer

    root, manager = _project()
    started: list[str] = []
    released = root / "release"
    safety = {"fired": False}
    real = sup._default_starter

    def recording(*a, **kw):
        result = real(*a, **kw)
        if result.ok:
            started.append(result.session)
            # AFTER the start returned "started", so after its settle window:
            # the session ends because of this, never because time passed.
            released.write_text("")

            def kill_if_stuck():
                safety["fired"] = True
                _kill(result.session)

            timer = threading.Timer(SAFETY_SECONDS, kill_if_stuck)
            timer.daemon = True
            timer.start()
            timers.append(timer)
        return result

    timers: list[threading.Timer] = []

    def operator_presses_ctrl_c(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(sup, "_default_starter", recording)
    monkeypatch.setattr(sup, "_sleep", operator_presses_ctrl_c)
    ready = LoopAnswer.of(SimpleNamespace(verdict="ready", ready=["KAN-1"], blocked={}))
    # `supervise` gives the default engine Claude's permission flags, which
    # `sh` refuses, so the stand-in takes any arguments, as `claude` does,
    # and runs the prompt from stdin.
    engine = root / "engine"
    engine.write_text("#!/bin/sh\nexec /bin/sh\n")
    engine.chmod(0o755)
    try:
        result = sup.supervise(
            root,
            manager,
            engine=str(engine),
            # Ends when released, not after a time: see the module docstring.
            prompt=f"while [ ! -e '{released}' ]; do sleep 0.1; done",
            verdict=lambda r: ready,
            note=[].append,
            poll=0.2,
        )
    finally:
        for timer in timers:
            timer.cancel()
        for session in started:
            _kill(session)
    assert started, f"no session was started: {result.reason}"
    assert not safety["fired"], "the session was never released; it was killed"
    assert result.reason.startswith("stopped"), result.reason
    assert "TypeError" not in result.reason
