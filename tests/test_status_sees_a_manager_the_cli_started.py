"""`rite status` must see a Manager that `rite start` actually started.

WHY THIS EXISTS. It did not. Measured against a live tmux session:

    session really alive: True
    rite status says: recorded but not running: lead
                      — the session is gone; the record is not

The session was not gone. `rite status` reported every running Manager as
dead, on the only path the CLI takes, and the message invites the operator
to clean up the record of a live session.

⚠ FOUR CORRECT PIECES, ONE WRONG ANSWER. `start_session` records the real
pane pid. `_default_starter` then re-records the same instance three lines
later WITHOUT a pid, so it takes `ManagerInstance.pid`'s default of 0.
`pid_alive(0)` correctly returns False. `_manager_lines` correctly filters
on it. Every component behaves as written and the composition is wrong.

⚠ WHY MUTATION TESTING MISSED IT, which is the part worth keeping.
Neutering `pid_alive` to always return True turned
`test_a_dead_manager_is_not_reported_as_running` red, so the FILTERING was
covered. Nothing covered the record arriving with `pid=0`, because every
existing test constructs `ManagerInstance` with a real pid and none of them
sees what production writes. A test that builds its own input cannot
discover that the real producer builds a different one.

So this test drives `_default_starter` — the function the CLI calls — and
asks `rite status` what it sees. No mock, no hand-built record.

⚠ `claude` is never launched here: the engine is `sh`, and
`launch_command` uses the engine string as the executable name — it
also appends `-p`, which `sh` accepts and `sleep` did not, which is
why this is not `sleep 120`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from rite_ai.managers import ManagerInstance
from rite_ai.reporting.status import _manager_lines

_HAS_TMUX = shutil.which("tmux") is not None
_IN_CI = os.environ.get("CI") == "true"

if _IN_CI and not _HAS_TMUX:
    raise RuntimeError(
        "tmux is missing in CI. This test proves `rite status` against a real "
        "session; skipping it in CI would retire the check silently."
    )


def test_a_manager_instance_cannot_be_built_without_a_pid():
    """The default was the defect, not the call site that relied on it.

    A `pid` that defaults to 0 means "dead" to every reader, so omitting it
    is indistinguishable from recording a corpse. Correcting the one caller
    would leave the next one to make the same mistake.
    """
    with pytest.raises(TypeError):
        ManagerInstance(name="lead", session="s")  # type: ignore[call-arg]


def test_the_starter_cannot_be_called_without_a_permission_mode():
    """C2. The DEFAULT was the defect, exactly as it was for `pid`.

    ⚠ `permission=""` meant "launch with no permission flag", and a Manager
    launched that way starts, runs, and cannot act — it stops at the first
    operation needing approval, waiting for a human who is not there. That is
    defect class 15: a prompt is not an exception, it is the absence of an
    answer. Nothing fails, nothing is logged, and the session burns its
    window doing nothing.

    The same omission already shipped once on the neighbouring argument —
    `supervise`'s fresh fallback launched with no prompt — so the enabling
    condition is not hypothetical, it is a thing this signature has already
    done. Correcting one caller leaves the next one to make the same mistake;
    removing the default makes it unrepresentable.
    """
    from rite_ai.managers.supervise import _default_starter

    with pytest.raises(TypeError):
        _default_starter(  # type: ignore[call-arg]
            Path("/tmp"),
            "lead",
            engine="sleep 1",
            resume_id="",
            max_sessions=1,
            window_seconds=60,
            prompt="do the thing",
        )


@pytest.mark.skipif(not _HAS_TMUX, reason="tmux is not installed")
def test_status_reports_a_manager_started_the_way_the_cli_starts_one():
    from rite_ai.managers.supervise import _default_starter

    root = Path(tempfile.mkdtemp(prefix="mgr-status-"))
    (root / ".rite").mkdir()
    manager = f"lead{uuid.uuid4().hex[:6]}"
    session = ""
    try:
        result = _default_starter(
            root,
            manager,
            engine="sh",
            resume_id="",
            # The launch pipes `$RITE_PROMPT` into the engine, so the stub
            # needs something to do — this is what keeps the session alive
            # long enough for `rite status` to be asked about it.
            prompt="sleep 60",
            # Explicit, now that C2 removed the default. This engine is a
            # shell stub, which takes no permission flag, so "none" is the
            # right value — and saying so is exactly the point of the
            # change: the caller decides, rather than inheriting a default
            # that means "launch a Manager that cannot act".
            permission="",
            max_sessions=1,
            window_seconds=60,
        )
        assert result.ok, (
            f"the starter failed, so this proves nothing: {result.message}"
        )
        session = result.session
        assert (
            subprocess.run(
                ["tmux", "has-session", "-t", f"={session}"], capture_output=True
            ).returncode
            == 0
        ), "the session is not actually running, so this proves nothing"

        lines = _manager_lines(root)
        assert lines, "`rite status` said nothing about a Manager it just started"
        joined = "\n".join(lines)
        assert "recorded but not running" not in joined, (
            "`rite status` called a LIVE Manager dead, and told the operator "
            f"its session was gone:\n{joined}"
        )
        assert manager in joined and "running as" in joined, (
            f"the Manager is not reported as running:\n{joined}"
        )
    finally:
        if session:
            subprocess.run(
                ["tmux", "kill-session", "-t", f"={session}"], capture_output=True
            )
