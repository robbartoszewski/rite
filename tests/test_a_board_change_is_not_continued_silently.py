"""🔴 Configuring a board did not reset a Manager's conversation, and nothing
warned.

Measured on the v0.6.0 Linux acceptance run: a Manager first started with no
board was told so; after a board was configured, a bare `rite start`
continued that conversation, and the Manager declined routed work as "the
same routing instruction a third time". A conversation keeps the
instructions it began with, so rite now records the board a conversation
BEGAN under and refuses to continue it under a different one — saying why,
with the command for each way forward. It never starts fresh by itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import designate, designated, designation_path
from rite_ai.managers.board_context import _began_under, board_now, refusal
from rite_ai.managers.session import StartResult
from rite_ai.managers.supervise import supervise

NONE = "ticket_backend:\n  type: none\n"
BOARD = "ticket_backend:\n  type: github\n  repo: acme/board\n"
OTHER = "ticket_backend:\n  type: github\n  repo: acme/elsewhere\n"


def _config(root: Path, backend: str) -> None:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    (root / ".rite" / "config.yaml").write_text(backend)


def _began(root: Path, backend: str) -> None:
    """A conversation designated while `backend` was configured."""
    _config(root, backend)
    designate(root, "lead", "sid-abc123", board=board_now(root))


class TestTheRefusal:
    def test_nothing_to_continue_is_never_refused(self, tmp_path):
        _config(tmp_path, BOARD)
        assert refusal(tmp_path, "lead", sessions=3, minutes=90) == ""

    def test_the_same_board_continues(self, tmp_path):
        _began(tmp_path, BOARD)
        assert refusal(tmp_path, "lead", sessions=3, minutes=90) == ""

    def test_a_board_configured_since_is_refused_with_both_commands(self, tmp_path):
        _began(tmp_path, NONE)
        _config(tmp_path, BOARD)
        said = refusal(tmp_path, "lead", sessions=3, minutes=90)
        assert "began when this project's board was not configured" in said
        assert "the GitHub repository acme/board" in said
        assert "rite start lead --fresh --sessions 3 --minutes 90" in said
        assert "rite start lead --keep-conversation --sessions 3 --minutes 90" in said
        assert "Nothing was started" in said
        assert "setup session" not in said, "a user should not need rite's jargon"

    def test_a_board_removed_since_is_refused(self, tmp_path):
        _began(tmp_path, BOARD)
        _config(tmp_path, NONE)
        said = refusal(tmp_path, "lead", sessions=1, minutes=5)
        assert "acme/board" in said and "now not configured" in said

    def test_a_board_pointed_elsewhere_is_refused(self, tmp_path):
        _began(tmp_path, BOARD)
        _config(tmp_path, OTHER)
        said = refusal(tmp_path, "lead", sessions=1, minutes=5)
        assert "acme/board" in said and "acme/elsewhere" in said

    def test_an_unrecorded_conversation_under_a_board_is_refused_and_says_why(
        self, tmp_path
    ):
        """A designation from a build that did not record the board: rite
        cannot tell, and the case that matters is exactly this one."""
        _config(tmp_path, BOARD)
        designation_path(tmp_path, "lead").parent.mkdir(parents=True, exist_ok=True)
        designation_path(tmp_path, "lead").write_text(
            json.dumps({"session": "sid-abc123"})
        )
        said = refusal(tmp_path, "lead", sessions=1, minutes=5)
        assert "cannot tell" in said and "earlier build" in said
        assert "--keep-conversation" in said and "--fresh" in said

    def test_an_unrecorded_conversation_with_no_board_continues(self, tmp_path):
        _config(tmp_path, NONE)
        designation_path(tmp_path, "lead").parent.mkdir(parents=True, exist_ok=True)
        designation_path(tmp_path, "lead").write_text(
            json.dumps({"session": "sid-abc123"})
        )
        assert refusal(tmp_path, "lead", sessions=1, minutes=5) == ""


class TestTheRecordFollowsTheConversation:
    def test_a_continued_designation_carries_the_board_forward(self, tmp_path):
        _began(tmp_path, NONE)
        designate(tmp_path, "lead", "sid-later")
        assert designated(tmp_path, "lead") == "sid-later"
        assert _began_under(tmp_path, "lead") == (True, {"type": "none"})

    def test_a_fresh_designation_replaces_it(self, tmp_path):
        _began(tmp_path, NONE)
        _config(tmp_path, BOARD)
        designate(tmp_path, "lead", "sid-new", board=board_now(tmp_path))
        assert _began_under(tmp_path, "lead")[1] == {
            "type": "github",
            "repo": "acme/board",
        }


def _quiet(monkeypatch, sup):
    monkeypatch.setattr(
        sup, "liveness", lambda n: type("L", (), {"alive": False, "known": True})()
    )
    monkeypatch.setattr(sup, "was_attached", lambda n: False)
    monkeypatch.setattr(sup, "stop_session", lambda n: None)
    monkeypatch.setattr(
        sup,
        "ending",
        lambda n, human_was_present, pane="": type(
            "E", (), {"kind": "quit", "resume": False, "status": 0, "detail": ""}
        )(),
    )


class TestTheSupervisorRecordsTheBoardAtLaunch:
    def test_a_session_that_configures_the_board_is_recorded_as_begun_without(
        self, tmp_path, monkeypatch
    ):
        """⚠ The case a setup session produces: it starts with no board and
        writes one before it ends. Read at designation, it would be recorded
        under the NEW board, and the next bare start would continue it."""
        import rite_ai.managers.supervise as sup

        _config(tmp_path, NONE)
        _quiet(monkeypatch, sup)

        def starter(root, manager, **kw):
            _config(root, BOARD)  # the session configures the board
            return StartResult(True, "ok", session="s1", attach="a", pane="%1")

        supervise(
            tmp_path,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            fresh=True,
            resume_id_for=lambda r, m, since: "sid-fresh1",
        )
        assert designated(tmp_path, "lead") == "sid-fresh1"
        assert _began_under(tmp_path, "lead") == (True, {"type": "none"})
        assert "not configured" in refusal(tmp_path, "lead", sessions=1, minutes=1)

    def test_a_resumed_cycle_keeps_the_board_it_began_under(
        self, tmp_path, monkeypatch
    ):
        import rite_ai.managers.supervise as sup
        from known_session import designate_known

        _config(tmp_path, NONE)
        designate_known(tmp_path, "lead", "YESTERDAY")
        designate(tmp_path, "lead", "YESTERDAY", board={"type": "none"})
        _config(tmp_path, BOARD)
        _quiet(monkeypatch, sup)
        launched: list[str] = []

        def starter(root, manager, *, resume_id, **kw):
            launched.append(resume_id)
            return StartResult(True, "ok", session="s1", attach="a", pane="%1")

        supervise(
            tmp_path,
            "lead",
            engine="claude",
            max_sessions=1,
            window_seconds=0,
            verdict=lambda _r: "ready",
            starter=starter,
            resume_id_for=lambda r, m, since: "TODAY",
        )
        assert launched == ["YESTERDAY"], "the test did not resume"
        assert _began_under(tmp_path, "lead") == (True, {"type": "none"})


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        BOARD + "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def supervised(monkeypatch) -> list[dict]:
    """What `supervise` was called with; nothing is ever started."""
    import rite_ai.cli.main as main_mod
    import rite_ai.managers.session as ses
    import rite_ai.managers.supervise as sup

    calls: list[dict] = []

    class Outcome:
        ok = True
        reason = "stopped: test"

    monkeypatch.setattr(
        main_mod, "_ticket_backend", lambda role="workers": (object(), None)
    )
    monkeypatch.setattr(
        sup, "supervise", lambda root, manager, **kw: calls.append(kw) or Outcome()
    )
    monkeypatch.setattr(ses, "exit_status_available", lambda: True)
    monkeypatch.setattr(ses, "running", lambda *a, **k: None)
    return calls


def _began_with_no_board(project: Path) -> None:
    designation_path(project, "lead").parent.mkdir(parents=True, exist_ok=True)
    designation_path(project, "lead").write_text(
        json.dumps({"session": "sid-abc123", "board": {"type": "none"}})
    )


START = ["start", "lead", "--sessions", "2", "--minutes", "30"]


class TestRiteStart:
    def test_a_bare_start_is_refused_before_anything_starts(self, project, supervised):
        _began_with_no_board(project)
        result = CliRunner().invoke(cli, START)
        assert result.exit_code == 1, result.output
        assert supervised == [], "a Manager was started on the old conversation"
        assert "rite start lead --fresh --sessions 2 --minutes 30" in result.output

    def test_keep_conversation_continues_and_records_the_board(
        self, project, supervised
    ):
        _began_with_no_board(project)
        result = CliRunner().invoke(cli, [*START, "--keep-conversation"])
        assert supervised, result.output
        assert supervised[0]["fresh"] is False
        assert _began_under(project, "lead")[1] == {
            "type": "github",
            "repo": "acme/board",
        }
        assert designated(project, "lead") == "sid-abc123", "the conversation was lost"
        # Asked once: the next bare start has nothing left to refuse.
        assert refusal(project, "lead", sessions=2, minutes=30) == ""

    def test_fresh_is_not_refused(self, project, supervised):
        _began_with_no_board(project)
        result = CliRunner().invoke(cli, [*START, "--fresh"])
        assert supervised and supervised[0]["fresh"] is True, result.output

    def test_both_flags_are_refused(self, project, supervised):
        result = CliRunner().invoke(cli, [*START, "--fresh", "--keep-conversation"])
        assert result.exit_code == 1 and supervised == []
        assert "Choose one" in result.output

    def test_the_no_board_start_names_the_command_for_afterwards(
        self, project, supervised, monkeypatch
    ):
        """It used to say "Run `rite start` again when the board is
        configured" — the bare start that meets this refusal."""
        import rite_ai.cli.main as main_mod

        _config(
            project,
            NONE + "coordination:\n  manager_roles:\n"
            "    - name: lead\n      engine: claude\n",
        )
        monkeypatch.setattr(
            main_mod, "_ticket_backend", lambda role="workers": (None, "none")
        )
        result = CliRunner().invoke(cli, START)
        assert "`rite start lead --fresh`" in result.output, result.output
