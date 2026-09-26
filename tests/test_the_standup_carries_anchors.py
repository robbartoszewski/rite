"""The standup a check-in opens with carries anchors, not prose (K4).

⚠ Three things were reported done in the last release that had never run.
A standup is otherwise the most efficient channel there is for unverified
claims, so it is composed by rite from records, and a Manager's own lines go
through the anchor floor and are labelled as the Manager's.

The central assertion is over a PRODUCED check-in, as the plan asks: no line
of the standup lacks an anchor. Asserting it over the composer would pass
for a composer that is never called.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import checkins, standup
from rite_ai.managers.mailbox import OUTBOX, read
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import supervise
from rite_ai.reporting import events

ALWAYS_OPEN = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'

# What a reader can check. Every bullet of a standup must carry one.
_ANCHOR = re.compile(
    r"commit [0-9a-f]{7,40}\b"
    r"|sandbox \S+"
    r"|ticket \S+"
    r"|session \S+"
    r"|\bq[0-9a-f]{6}\b"
    r"|`[^`]+`"
    r"|\[anchor: [^\]]*[A-Za-z0-9][^\]]*\]"
    r"|\(ledger: \S+\)"
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _build(root: Path, checkin_windows: str = ALWAYS_OPEN) -> Path:
    rite = root / ".rite"
    rite.mkdir(exist_ok=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n" + checkin_windows
    )
    _git(root, "init", "-q")
    _git(
        root,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Land the thing the standup must name",
    )
    return root


def _drive(monkeypatch, root: Path, during=None):
    import rite_ai.managers.supervise as sup

    said: list[str] = []

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
        if during is not None:
            during(prompt)
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
        root,
        "lead",
        engine="claude",
        max_sessions=1,
        window_seconds=0,
        prompt="work",
        verdict=lambda _r: "ready",
        note=said.append,
        starter=starter,
        poll=0,
    )
    return said


def _standup_lines(message: str) -> list[str]:
    """The standup's claim lines: every bullet from the standup's head down
    to, and including, the deferral counts."""
    lines = message.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Standup since"))
    end = next(
        (i for i, line in enumerate(lines) if line.startswith("Questions held")),
        len(lines),
    )
    return [line for line in lines[start:end] if line.startswith("- ")]


@pytest.fixture
def no_yoloai(monkeypatch):
    import rite_ai.sandbox as sb

    monkeypatch.setattr(
        sb,
        "list_rite_sandboxes",
        lambda: [sb.SandboxEntry(name="rite-acme-000000-alpha", status="running")],
    )


# --- the produced check-in ---------------------------------------------------------


def test_a_check_in_with_nothing_queued_still_opens_with_a_standup(
    tmp_path, monkeypatch, no_yoloai
):
    root = _build(tmp_path)
    _drive(monkeypatch, root)
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    assert "Standup since" in sent
    assert "Observed by rite:" in sent


def test_the_standup_names_the_commit_and_the_sandbox_as_observed_by_rite(
    tmp_path, monkeypatch, no_yoloai
):
    root = _build(tmp_path)
    sha = _git(root, "rev-parse", "--short", "HEAD")
    events.record(
        root,
        "sandbox-started",
        worker="alpha",
        sandbox="rite-acme-000000-alpha",
        ticket="12",
    )
    _drive(monkeypatch, root)
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    observed = sent.split("Observed by rite:")[1].split("\n\n")[0]
    assert f"- commit {sha} Land the thing the standup must name" in observed
    assert (
        "- Worker alpha started in sandbox rite-acme-000000-alpha, ticket 12"
        in observed
    )
    assert "- sandbox rite-acme-000000-alpha: running at this check-in" in observed


def test_no_line_of_a_produced_standup_lacks_an_anchor(
    tmp_path, monkeypatch, no_yoloai
):
    """⚠ Over the OUTPUT. Every kind of record at once, plus a Manager note
    and a withdrawal, so each line shape the standup can produce is here."""
    root = _build(tmp_path)
    events.record(
        root,
        "sandbox-started",
        worker="alpha",
        sandbox="rite-acme-000000-alpha",
        ticket="12",
    )
    events.record(
        root, "sandbox-stopped", worker="alpha", sandbox="rite-acme-000000-alpha"
    )
    events.record(root, "board-move", ticket="12", status="Done", by="lead")
    checkins.record(
        root,
        "lead",
        {
            "event": "cycle",
            "at": time.time(),
            "number": 1,
            "session": "abc-123",
            "started_at": time.time() - 60,
            "ending": "finished",
        },
    )
    checkins.record(
        root,
        "lead",
        {
            "event": "refusal",
            "at": time.time(),
            "number": 1,
            "session": "abc-123",
            "command": "rm -rf build",
        },
    )
    assert (
        checkins.note(root, "lead", "abc1234", "a Worker authenticated\nand pushed")
        == ""
    )
    q = checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    kept = checkins.defer(root, "lead", "drop python 3.11?", "ticket 15")
    _drive(
        monkeypatch,
        root,
        during=lambda _p: checkins.withdraw(root, "lead", q.id, "commit abc1234"),
    )
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    claims = _standup_lines(sent)
    assert len(claims) >= 8, sent
    unanchored = [line for line in claims if not _ANCHOR.search(line)]
    assert unanchored == [], unanchored
    assert kept.id in sent


def test_the_managers_lines_are_labelled_as_stated_not_observed(
    tmp_path, monkeypatch, no_yoloai
):
    root = _build(tmp_path)
    checkins.note(root, "lead", "abc1234", "a Worker authenticated")
    _drive(monkeypatch, root)
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    observed, stated = sent.split("Stated by the Manager — rite did not verify these:")
    assert "a Worker authenticated" not in observed
    assert "- a Worker authenticated [anchor: abc1234]" in stated


def test_nothing_recorded_is_said_with_what_was_looked_at(tmp_path, monkeypatch):
    root = _build(tmp_path)
    # `git log --since` is inclusive to the second, so start past the
    # fixture's own commit.
    lines = standup.digest(root, "lead", since=time.time() + 2)
    assert any(line.startswith("- nothing: `git log --all --since=@") for line in lines)


def test_the_first_check_in_says_how_far_back_it_looked(tmp_path):
    root = _build(tmp_path)
    [head, *_] = standup.digest(root, "lead", since=0.0)
    assert "no earlier check-in" in head and "last 24 hours" in head


def test_the_next_standup_starts_at_the_last_check_in(tmp_path, monkeypatch, no_yoloai):
    root = _build(tmp_path)
    events.record(root, "board-move", ticket="OLD-1", status="Done", by="")
    _drive(monkeypatch, root)
    time.sleep(0.01)
    later = checkins._last_checkin(root, "lead")
    lines = standup.digest(root, "lead", since=later)
    assert not any("OLD-1" in line for line in lines)


# --- the records, written where rite saw each thing succeed -------------------------


def test_the_supervisor_records_each_cycle_and_its_ending(
    tmp_path, monkeypatch, no_yoloai
):
    root = _build(tmp_path, "checkins:\n  windows: []\n")
    _drive(monkeypatch, root)
    [cycle] = [e for e in checkins.ledger(root, "lead") if e["event"] == "cycle"]
    assert cycle["number"] == 1 and cycle["ending"] == "quit" and cycle["session"]


def test_a_refusal_is_recorded_with_its_cycle(tmp_path, monkeypatch, no_yoloai):
    import rite_ai.managers.supervise as sup

    root = _build(tmp_path, "checkins:\n  windows: []\n")
    monkeypatch.setattr(
        sup, "refused_commands", lambda r, since, base=None: ["rm -rf build"]
    )
    _drive(monkeypatch, root)
    [refusal] = [e for e in checkins.ledger(root, "lead") if e["event"] == "refusal"]
    assert refusal["command"] == "rm -rf build" and refusal["number"] == 1


def test_a_board_move_is_recorded_where_the_ticket_actually_landed(
    tmp_path, monkeypatch
):
    import rite_ai.cli.main as main

    root = _build(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)

    class Board:
        def move(self, ticket, status):
            return "closed"  # GitHub has only open and closed

    monkeypatch.setattr(main, "_ticket_backend", lambda role: (Board(), ""))
    result = CliRunner().invoke(cli, ["board", "move", "12", "Done"])
    assert result.exit_code == 0, result.output
    [move] = [e for e in events.since(root, 0) if e["event"] == "board-move"]
    assert move["ticket"] == "12" and move["status"] == "closed"


def test_a_failed_board_move_is_not_recorded(tmp_path, monkeypatch):
    import rite_ai.cli.main as main
    from rite_ai.tickets import BackendError

    root = _build(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)

    class Board:
        def move(self, ticket, status):
            return BackendError("no such ticket")

    monkeypatch.setattr(main, "_ticket_backend", lambda role: (Board(), ""))
    CliRunner().invoke(cli, ["board", "move", "12", "Done"])
    assert events.since(root, 0) == []


def test_recording_never_breaks_what_it_records(tmp_path):
    """No `.rite/` at all: nothing to record into, and no exception."""
    events.record(tmp_path, "sandbox-started", worker="alpha", sandbox="x")
    assert events.since(tmp_path, 0) == []


# --- the Manager's note ---------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    _build(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


def _note(*args: str):
    return CliRunner().invoke(cli, ["checkin", "note", "--manager", "lead", *args])


@pytest.mark.parametrize("anchor", [None, "", "  ", "ㅤ"])
def test_a_note_with_no_anchor_is_refused(project, anchor):
    args = ["--observed", "sorted out the Worker problem"]
    if anchor is not None:
        args += ["--anchor", anchor]
    result = _note(*args)
    assert result.exit_code == 1
    assert "refusing" in result.output
    assert [e for e in checkins.ledger(project, "lead") if e["event"] == "note"] == []


def test_a_note_with_nothing_observed_is_refused(project):
    result = _note("--anchor", "abc1234")
    assert result.exit_code == 1
    assert "no --observed" in result.output


def test_an_anchored_note_is_recorded(project):
    result = _note("--anchor", "abc1234", "--observed", "a Worker authenticated")
    assert result.exit_code == 0, result.output
    [note] = [e for e in checkins.ledger(project, "lead") if e["event"] == "note"]
    assert note["anchor"] == "abc1234" and note["observed"] == "a Worker authenticated"


# --- when the check-in goes out ------------------------------------------------------


def test_a_note_made_in_the_check_in_cycle_is_in_that_check_in(
    tmp_path, monkeypatch, no_yoloai
):
    """The check-in is delivered when the cycle ENDS, so what the Manager
    noted during it, and the cycle itself, are in it — not in the next one."""
    root = _build(tmp_path)

    def manager_notes(prompt):
        assert "a standup goes to the User when this session ends" in prompt
        assert checkins.note(root, "lead", "abc1234", "a Worker authenticated") == ""

    _drive(monkeypatch, root, during=manager_notes)
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    assert "- a Worker authenticated [anchor: abc1234]" in sent
    assert "- cycle 1, session " in sent


def test_a_run_that_stops_on_its_verdict_in_a_window_still_checks_in(
    tmp_path, monkeypatch, no_yoloai
):
    """No cycle will run to deliver it — and a Manager with nothing to do is
    when the User should hear what happened."""
    import rite_ai.managers.supervise as sup

    root = _build(tmp_path)
    q = checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    said: list[str] = []
    result = supervise(
        root,
        "lead",
        engine="claude",
        max_sessions=1,
        window_seconds=0,
        prompt="work",
        verdict=lambda _r: "idle",
        note=said.append,
        starter=lambda *a, **k: pytest.fail("idle starts no session"),
        poll=0,
    )
    assert result.ok
    [sent] = [m.text for m in read(root, "lead", OUTBOX)]
    assert "Standup since" in sent and q.id in sent
    assert checkins._queued(root, "lead") == []
    assert any(line.startswith("check-in:") for line in said), said
    del sup
