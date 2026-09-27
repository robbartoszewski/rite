"""A run that ends on its ceiling must be CONTINUED by the next bare start.

WHY THIS EXISTS. A bound leaves the Manager's pane where it is, held by
`remain-on-exit`, so every run that reaches `--sessions` leaves one. The next
`rite start` tried to resume, `start` refused the held session as "left over
… no record of it" — a session this project's own record named — and
`supervise` read that refusal as the conversation being gone: it said "could
not be continued" and started FRESH. Measured on macOS against real Claude,
twice in a row, with the designated transcript intact on disk.

Real tmux, twice, because every other test of a resume injects `starter` and
so never meets a session a previous run left held.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from known_session import designate_known
from rite_ai.managers import designated
from rite_ai.managers.session import session_exists, session_name
from rite_ai.managers.supervise import supervise

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux is not installed"
)

SID = "11111111-1111-4111-8111-111111111111"


def _run(root: Path, engine: Path, manager: str, said: list[str]):
    return supervise(
        root,
        manager,
        engine=str(engine),
        prompt="go",
        max_sessions=1,
        window_seconds=20,
        verdict=lambda _r: "ready",
        resume_id_for=lambda *_a: SID,
        note=said.append,
    )


def test_the_next_start_continues_a_run_that_ended_on_its_ceiling():
    work = Path(tempfile.mkdtemp(prefix="bounded-"))
    root = work / "proj"
    (root / ".rite").mkdir(parents=True)
    log = root / "launches.log"
    engine = root / "engine"
    # Behaves like `claude -p` that knows the conversation: reads its prompt,
    # works for a moment, finishes cleanly.
    engine.write_text(f'#!/bin/sh\necho "$*" >> {log}\ncat > /dev/null\nsleep 4\n')
    engine.chmod(0o755)
    manager = f"lead{uuid.uuid4().hex[:6]}"
    designate_known(root, manager, SID)
    first: list[str] = []
    second: list[str] = []
    try:
        one = _run(root, engine, manager, first)
        assert one.ok and "ceiling reached" in one.reason, one.reason
        # The precondition this test exists for: the bound left the
        # finished session held under the Manager's name.
        assert session_exists(session_name(root, manager)), first

        two = _run(root, engine, manager, second)
    finally:
        listed = subprocess.run(
            ["tmux", "ls", "-F", "#{session_name}"], capture_output=True, text=True
        ).stdout
        for name in listed.split():
            if name.endswith(manager):
                subprocess.run(["tmux", "kill-session", "-t", f"={name}"])
    launches = log.read_text().splitlines()
    assert "could not be continued" not in " ".join(second), second
    assert two.ok and "ceiling reached" in two.reason, two.reason
    assert len(launches) == 2, launches
    assert f"--resume {SID}" in launches[1], launches
    assert designated(root, manager) == SID


def test_a_start_refused_before_launch_is_not_read_as_a_gone_conversation():
    """A resume refused before the engine ran says nothing about the
    conversation, so the run ends in its own words and does NOT start
    fresh."""
    from rite_ai.managers.session import StartResult

    root = Path(tempfile.mkdtemp(prefix="refused-")) / "proj"
    (root / ".rite").mkdir(parents=True)
    designate_known(root, "lead", SID)
    asked: list[str] = []
    said: list[str] = []

    def starter(r, m, *, engine, resume_id, **kw):
        asked.append(resume_id)
        return StartResult(False, "Manager 'lead' is already running as x")

    outcome = supervise(
        root,
        "lead",
        prompt="go",
        max_sessions=2,
        window_seconds=20,
        verdict=lambda _r: "ready",
        starter=starter,
        resume_id_for=lambda *_a: "",
        note=said.append,
    )
    assert asked == [SID], "a fresh start followed a refusal that was not the engine's"
    assert not outcome.ok and "already running" in outcome.reason
    assert "could not be continued" not in " ".join(said), said
    assert designated(root, "lead") == SID
