"""The queue is a draft, re-read by the Manager before it is asked (K3).

A question queued at 10:00 is re-evaluated at 14:00 and dropped if the
Manager has since answered it itself. Robert's observation: an orchestrator
asks, retracts, then finds it never needed deciding. Deferral doubles as a
filter, and the filter's value is COUNTED in every check-in rather than
believed.

A withdrawal needs an anchor, through the journal's anchor floor: "I worked
it out" is not an answer anybody can check.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import checkins
from rite_ai.managers.mailbox import OUTBOX, read
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import supervise

ALWAYS_OPEN = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'


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
    return root


def _outbox(root: Path) -> list[str]:
    return [m.text for m in read(root, "lead", OUTBOX)]


def _drive(monkeypatch, root: Path, during=None, ending_kind: str = "quit"):
    """One supervised cycle. `during(prompt)` runs while the session is
    'running' — where a real Manager would run `rite question withdraw`."""
    import rite_ai.managers.supervise as sup

    prompts: list[str] = []
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
        prompts.append(prompt)
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
            "E", (), {"kind": ending_kind, "resume": False, "status": 0, "detail": ""}
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
        prompt="work the queue",
        verdict=lambda _r: "ready",
        note=said.append,
        starter=starter,
        poll=0,
    )
    return prompts, said


# --- the draft is re-read, and only survivors are asked -------------------------


def test_two_queued_one_withdrawn_one_asked(tmp_path, monkeypatch):
    """The plan's done-when, at the seam: two queued before the window, the
    re-evaluation cycle withdraws one with an anchor, only the other is
    asked, and the check-in reports 2 queued, 1 withdrawn, 1 asked."""
    root = _build(tmp_path)
    a = checkins.defer(root, "lead", "Postgres or SQLite for ticket 7?", "ticket 8")
    b = checkins.defer(root, "lead", "group changelog fixes by module?", "ticket 9")

    def manager_rereads(prompt):
        assert a.id in prompt and b.id in prompt, "the queue is not in the instruction"
        assert "WITHDRAW ANY YOU CAN NOW ANSWER" in prompt
        assert (
            checkins.withdraw(root, "lead", a.id, "docs/adr/0004.md:12 chooses SQLite")
            == ""
        )

    prompts, said = _drive(monkeypatch, root, during=manager_rereads)

    [sent] = _outbox(root)
    assert "group changelog fixes by module?" in sent
    assert "Postgres or SQLite" not in sent.split("Questions held")[-1]
    assert "2 queued, 1 withdrawn by the Manager before asking, 1 asked now" in sent
    assert f"withdrawn {a.id}: answered by docs/adr/0004.md:12 chooses SQLite" in sent
    assert checkins._queued(root, "lead") == []
    assert not checkins._reevaluating(root, "lead")
    assert any("2 queued, 1 withdrawn" in line for line in said), said


def test_nothing_is_asked_before_the_re_evaluation_cycle_has_run(tmp_path, monkeypatch):
    root = _build(tmp_path)
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    seen_outbox_during: list[list[str]] = []
    _drive(
        monkeypatch, root, during=lambda _p: seen_outbox_during.append(_outbox(root))
    )
    assert seen_outbox_during == [[]], "asked before the Manager could withdraw it"


def test_all_withdrawn_is_still_a_check_in(tmp_path, monkeypatch):
    """The count is the evidence the filter filtered."""
    root = _build(tmp_path)
    q = checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    _drive(
        monkeypatch,
        root,
        during=lambda _p: checkins.withdraw(
            root, "lead", q.id, "commit abc1234 renamed it"
        ),
    )
    [sent] = _outbox(root)
    assert "1 queued, 1 withdrawn by the Manager before asking, 0 asked now" in sent
    assert "Questions held for this check-in" not in sent


@pytest.mark.parametrize("kind", ["finished", "quit", "crashed"])
def test_survivors_are_asked_whatever_the_cycle_ending(tmp_path, monkeypatch, kind):
    """A crash is not a reason to leave the User unasked."""
    root = _build(tmp_path)
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    _drive(monkeypatch, root, ending_kind=kind)
    [sent] = _outbox(root)
    assert "rename the flag?" in sent


def test_a_re_evaluation_left_undelivered_is_delivered_at_the_next_boundary(
    tmp_path, monkeypatch
):
    """A run killed mid-cycle leaves the marker. The Manager had its chance
    to withdraw, so the next boundary asks rather than re-reads again."""
    root = _build(tmp_path)
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    boundary = checkins.at_boundary(root, "lead")
    assert boundary.instruction and checkins._reevaluating(root, "lead")
    # ... the run dies here ...
    prompts, _ = _drive(monkeypatch, root)
    [sent] = _outbox(root)
    assert "rename the flag?" in sent
    assert "re-read the questions you deferred" not in prompts[0]


def test_outside_a_window_nothing_is_re_evaluated(tmp_path, monkeypatch):
    root = _build(tmp_path, "checkins:\n  windows: []\n")
    checkins.defer(root, "lead", "rename the flag?", "ticket 14")
    prompts, _ = _drive(monkeypatch, root)
    assert "re-read the questions you deferred" not in prompts[0]
    assert len(checkins._queued(root, "lead")) == 1


# --- the counts -----------------------------------------------------------------------


def test_early_asks_are_counted_and_named(tmp_path, monkeypatch):
    root = _build(tmp_path)
    early = checkins.defer(root, "lead", "idle one", "ticket 1")
    checkins.ask_now(root, "lead", [early], "idle", how="idle")
    checkins.defer(root, "lead", "held one", "ticket 2")
    _drive(monkeypatch, root)
    sent = _outbox(root)[-1]
    assert "2 queued, 0 withdrawn by the Manager before asking, 1 asked now" in sent
    assert "1 asked early" in sent


def _window_opened_at(monkeypatch, opened_at: float) -> None:
    """Control when the open window opened, so a second window can be made
    to begin after the first check-in without waiting for a clock."""
    monkeypatch.setattr(
        checkins,
        "windows",
        lambda _root: checkins.Windows(
            True, "check-ins: open now", open_now=True, opened_at=opened_at
        ),
    )


def test_one_check_in_per_window(tmp_path, monkeypatch):
    """The second boundary in the same window does not check in again."""
    root = _build(tmp_path)
    _window_opened_at(monkeypatch, time.time() - 60)
    checkins.defer(root, "lead", "first", "ticket 1")
    _drive(monkeypatch, root)
    _drive(monkeypatch, root)
    assert len(_outbox(root)) == 1


def test_counts_start_again_at_the_next_window(tmp_path, monkeypatch):
    root = _build(tmp_path)
    _window_opened_at(monkeypatch, time.time() - 60)
    checkins.defer(root, "lead", "first", "ticket 1")
    _drive(monkeypatch, root)
    time.sleep(0.01)
    _window_opened_at(monkeypatch, time.time())  # the next window opens
    checkins.defer(root, "lead", "second", "ticket 2")
    _drive(monkeypatch, root)
    sent = _outbox(root)
    assert len(sent) == 2
    assert "1 queued, 0 withdrawn by the Manager before asking, 1 asked now" in sent[1]
    assert "second" in sent[1] and "first" not in sent[1]


# --- withdrawing ----------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    _build(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


def _withdraw(*args: str):
    return CliRunner().invoke(cli, ["question", "withdraw", *args, "--manager", "lead"])


@pytest.mark.parametrize(
    "anchor", ["", "   ", "​", "ㅤ"], ids=["empty", "spaces", "zwsp", "hangul-filler"]
)
def test_a_withdrawal_with_no_anchor_is_refused_and_the_question_stays(project, anchor):
    q = checkins.defer(project, "lead", "rename the flag?", "ticket 14")
    result = _withdraw(q.id, "--answered-by", anchor)
    assert result.exit_code == 1
    assert "refusing" in result.output
    assert [x.id for x in checkins._queued(project, "lead")] == [q.id]


def test_the_refusal_is_the_journals_floor_not_a_copy(project):
    """One floor: the same sentence shape the journal gives, named for this."""
    q = checkins.defer(project, "lead", "rename the flag?", "ticket 14")
    result = _withdraw(q.id)
    assert "refusing to withdraw a question with no anchor" in result.output
    assert "an anchor is a commit SHA" in result.output


def test_a_withdrawal_with_an_anchor_removes_it_and_records_why(project):
    q = checkins.defer(project, "lead", "rename the flag?", "ticket 14")
    result = _withdraw(q.id, "--answered-by", "commit abc1234")
    assert result.exit_code == 0, result.output
    assert checkins._queued(project, "lead") == []
    [event] = [e for e in checkins.ledger(project, "lead") if e["event"] == "withdrawn"]
    assert event["answered_by"] == "commit abc1234"


def test_withdrawing_an_unknown_id_is_refused_by_name(project):
    checkins.defer(project, "lead", "rename the flag?", "ticket 14")
    result = _withdraw("qnothere", "--answered-by", "commit abc1234")
    assert result.exit_code == 1
    assert "no queued question 'qnothere'" in result.output
