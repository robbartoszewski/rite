"""A Manager is handed the board rite read outside its sandbox (SCRUM-29).

Its prompt said to work the queue with `rite loop run`. Inside a Manager's
sandbox a Jira board's credentials are withheld by design, so that command can
only answer `unknown` there (and before 0.7.0a4 it was a traceback through
`warn_if_global`, which `0f1c8fc` fixed). The supervisor reads the same board
from outside before it starts the session, so it hands over what it read: the
ready and blocked tickets, as a section of the instruction.
"""

from __future__ import annotations

import time

from rite_ai.cli.main import LoopAnswer
from rite_ai.managers.prompt import for_manager
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import _board_brief, supervise


def _answer(verdict="ready", ready=("KAN-6", "KAN-9"), blocked=()):
    answer = LoopAnswer(verdict)
    answer.basis = (verdict, tuple(ready), tuple(blocked))
    answer.read_at = time.time()
    return answer


class TestTheBrief:
    def test_it_names_what_is_ready_and_what_is_blocked(self):
        brief = _board_brief(
            _answer(blocked=(("KAN-7", "waiting on KAN-6"),)),
        )
        assert "## The board this cycle (rite)" in brief
        assert "Ready to start: KAN-6, KAN-9." in brief
        assert "Blocked: KAN-7 — waiting on KAN-6" in brief
        assert "verdict `ready`" in brief

    def test_nothing_ready_is_said_as_none(self):
        assert "Ready to start: none." in _board_brief(_answer(ready=()))

    def test_no_board_reading_means_no_brief_rather_than_an_invented_one(self):
        """A plain verdict, a refinement cycle (basis None) and a cycle mail
        started (None) have no board read behind them to report."""
        refining = LoopAnswer("refining")
        refining.basis = None
        assert _board_brief("ready") == ""
        assert _board_brief(None) == ""
        assert _board_brief(refining) == ""


def _run(tmp_path, monkeypatch, verdict):
    import rite_ai.managers.supervise as sup

    (tmp_path / ".rite").mkdir()
    seen: list[str] = []

    def starter(
        root,
        manager,
        *,
        engine,
        resume_id,
        max_sessions,
        window_seconds,
        prompt="",
        permission="",
        agent="",
    ):
        seen.append(prompt)
        return StartResult(True, "ok", session="s1", attach="a")

    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(
        sup,
        "ending",
        lambda n, human_was_present, pane="": type(
            "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
        )(),
    )
    monkeypatch.setattr(sup, "stop_session", lambda s: Stopped(True, True))
    monkeypatch.setattr(sup, "forget_instance", lambda r, m: None)
    supervise(
        tmp_path,
        "lead",
        engine="claude",
        max_sessions=1,
        window_seconds=0,
        prompt="work the queue",
        verdict=verdict,
        starter=starter,
        poll=0,
    )
    return seen


def test_the_session_is_handed_the_board_the_supervisor_read(tmp_path, monkeypatch):
    seen = _run(tmp_path, monkeypatch, lambda _r: _answer())

    assert seen, "no cycle started"
    assert "Ready to start: KAN-6, KAN-9." in seen[0], (
        "the Manager was started on a board it cannot read, and not told what "
        "rite read from it"
    )
    assert "work the queue" in seen[0], "the ordinary instruction was lost"


def test_the_prompt_no_longer_sends_a_manager_to_a_command_that_cannot_answer(
    tmp_path,
):
    text = for_manager("lead", root=tmp_path)
    assert "The board this cycle" in text, "it does not say where the board is"
    assert "answers `unknown`" in text
    assert "{rite}" not in text, "a placeholder reached the Manager unformatted"
