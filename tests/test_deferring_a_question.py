"""Deferring a question to a check-in, and the rule that makes it safe (K2).

⚠ **The failure is asymmetric.** Deferring a question that blocks idles a
Manager until the next check-in, hours away. Asking one that could have
waited costs the User thirty seconds. So each test below pins one of the
ways the design leans towards asking:

* plain `rite reply` and plain `rite ask` are immediate;
* `--defer` with no `--while` is refused, and nothing is queued;
* no check-in window means a deferral is asked at once, with the reason;
* the loop going idle with questions queued asks them at once and says the
  deferral was wrong.

And the queue is WHOLE: a deferral is asked at the first cycle boundary
inside a window, so nothing lands a queue that never delivers.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import checkins
from rite_ai.managers.mailbox import OUTBOX, read
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import supervise

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _not_today() -> str:
    """A day that is certainly not today, so a window on it is closed now."""
    return _DAYS[(datetime.datetime.now().weekday() + 3) % 7]


def _build(root: Path, checkin_windows: str) -> Path:
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


CLOSED = (
    f'checkins:\n  windows:\n    - {{days: {_not_today()}, hours: "09:00-10:00"}}\n'
)
ALWAYS_OPEN = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    def build(checkin_windows: str = CLOSED) -> Path:
        _build(tmp_path, checkin_windows)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
        return tmp_path

    return build


def _ask(*args: str):
    return CliRunner().invoke(cli, ["ask", "--manager", "lead", *args])


def _outbox(root: Path) -> list[str]:
    return [m.text for m in read(root, "lead", OUTBOX)]


# --- asking now stays the default -------------------------------------------


def test_reply_is_unchanged_and_immediate(project):
    root = project()
    result = CliRunner().invoke(cli, ["reply", "--manager", "lead", "which schema?"])
    assert result.exit_code == 0, result.output
    assert _outbox(root) == ["which schema?"]


def test_ask_without_defer_asks_now(project):
    root = project()
    result = _ask("which schema?")
    assert result.exit_code == 0, result.output
    assert _outbox(root) == ["which schema?"]
    assert checkins.queued(root, "lead") == []


def test_while_without_defer_asks_now_and_says_so(project):
    """Most likely a forgotten --defer. Asking now is the safe reading."""
    root = project()
    result = _ask("rename the flag?", "--while", "doing ticket 14")
    assert result.exit_code == 0, result.output
    assert _outbox(root) == ["rename the flag?"]
    assert "asked NOW, not deferred" in result.output


# --- a deferral that cannot name parallel work is blocking ---------------------


@pytest.mark.parametrize("meanwhile", [None, "", "   "])
def test_defer_without_while_is_refused_and_nothing_is_queued(project, meanwhile):
    root = project()
    args = ["--defer", "rename the flag?"]
    if meanwhile is not None:
        args += ["--while", meanwhile]
    result = _ask(*args)
    assert result.exit_code == 1
    assert "then it blocks you — ask now" in result.output
    assert checkins.queued(root, "lead") == []
    assert _outbox(root) == []


# --- a deferral waits for the window -----------------------------------------------


def test_a_deferred_question_is_absent_from_the_outbox_before_the_window(project):
    root = project(CLOSED)
    result = _ask("--defer", "rename the flag?", "--while", "doing ticket 14")
    assert result.exit_code == 0, result.output
    assert _outbox(root) == []
    [q] = checkins.queued(root, "lead")
    assert q.text == "rename the flag?" and q.meanwhile == "doing ticket 14"
    assert f"deferred as {q.id}" in result.output


def test_the_queue_is_under_the_managers_own_directory(project):
    """Not `.rite/user/*.json`: that name-keyed directory has already held
    two things that were not instance records (C11, C4)."""
    root = project(CLOSED)
    _ask("--defer", "rename the flag?", "--while", "doing ticket 14")
    [q] = checkins.queued(root, "lead")
    assert q.path.parent == root / ".rite" / "managers" / "lead" / "checkins" / "queue"
    user = root / ".rite" / "user"
    assert not user.exists() or not list(user.glob("*.json"))


@pytest.mark.parametrize(
    "windows",
    ["", 'checkins:\n  windows:\n    - {hours: "9-10"}\n'],
    ids=["none-configured", "all-malformed"],
)
def test_with_no_window_to_wait_for_it_is_asked_at_once_with_the_reason(
    project, windows
):
    root = project(windows)
    result = _ask("--defer", "rename the flag?", "--while", "doing ticket 14")
    assert result.exit_code == 0, result.output
    assert "asked NOW, not deferred" in result.output
    [sent] = _outbox(root)
    assert "rename the flag?" in sent
    assert "there is none to wait for" in sent
    assert checkins.queued(root, "lead") == []


# --- the supervisor ------------------------------------------------------------------


def _drive(monkeypatch, root: Path, verdict: str, cycles: int = 1):
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
    result = supervise(
        root,
        "lead",
        engine="claude",
        max_sessions=cycles,
        window_seconds=0,
        prompt="work the queue",
        verdict=lambda _r: verdict,
        note=said.append,
        starter=starter,
        poll=0,
    )
    return result, prompts, said


def test_idle_with_questions_queued_asks_them_and_says_the_deferral_was_wrong(
    tmp_path, monkeypatch
):
    root = _build(tmp_path, CLOSED)
    checkins.defer(root, "lead", "rename the flag?", "doing ticket 14")
    checkins.defer(root, "lead", "drop python 3.11?", "doing ticket 15")

    result, prompts, said = _drive(monkeypatch, root, "idle")

    assert prompts == [], "idle is still a stop; no session is started"
    [sent] = _outbox(root)
    assert "rename the flag?" in sent and "drop python 3.11?" in sent
    assert "the deferral was wrong" in sent
    assert any("the deferral was wrong" in line for line in said), said
    assert checkins.queued(root, "lead") == []


def test_idle_with_nothing_queued_sends_nothing(tmp_path, monkeypatch):
    root = _build(tmp_path, CLOSED)
    _drive(monkeypatch, root, "idle")
    assert _outbox(root) == []


def test_a_deferral_waits_at_a_boundary_outside_a_window(tmp_path, monkeypatch):
    root = _build(tmp_path, CLOSED)
    checkins.defer(root, "lead", "rename the flag?", "doing ticket 14")
    _drive(monkeypatch, root, "ready")
    assert _outbox(root) == []
    assert len(checkins.queued(root, "lead")) == 1


def test_inside_a_window_the_queue_is_asked_at_the_cycle_boundary(
    tmp_path, monkeypatch
):
    """The queue is whole: it delivers. A queue that never delivers holds
    questions a Manager believes it asked."""
    root = _build(tmp_path, ALWAYS_OPEN)
    checkins.defer(root, "lead", "rename the flag?", "doing ticket 14")
    _, prompts, said = _drive(monkeypatch, root, "ready")
    [sent] = _outbox(root)
    assert "Check-in" in sent and "rename the flag?" in sent
    assert "(meanwhile: doing ticket 14)" in sent
    assert checkins.queued(root, "lead") == []
    assert any(line.startswith("check-in: asked 1") for line in said), said
    assert prompts, "the cycle still runs after the check-in"


def test_every_cycle_is_told_the_rule_in_its_own_words(tmp_path, monkeypatch):
    root = _build(tmp_path, CLOSED)
    _, prompts, _ = _drive(monkeypatch, root, "ready")
    assert checkins.RULE in prompts[0]
    assert "--defer" in prompts[0] and "--while" in prompts[0]


def test_the_rule_is_the_one_the_plan_states():
    """The plan asks for these words; paraphrase is how a rule softens."""
    assert checkins.RULE == (
        "ask now unless the question is clearly deferrable; if you are unsure "
        "whether it blocks you, it blocks you."
    )


def test_the_instruction_says_when_there_is_no_window(tmp_path):
    root = _build(tmp_path, "")
    said = checkins.instructions(root, "lead")
    assert "asked at once anyway" in said


# --- the queue ------------------------------------------------------------------------


def test_a_corrupt_queue_file_is_still_a_question(tmp_path):
    """Skipped, it would never be asked and nothing would say so."""
    root = _build(tmp_path, CLOSED)
    where = checkins._checkins_dir(root, "lead") / checkins.QUEUE_DIRNAME
    where.mkdir(parents=True)
    (where / "1_qbroken.json").write_text("{not json: is this still asked?")
    [q] = checkins.queued(root, "lead")
    assert "is this still asked?" in q.text


def test_asking_is_recorded_so_the_filter_can_be_counted(tmp_path):
    root = _build(tmp_path, CLOSED)
    q = checkins.defer(root, "lead", "rename the flag?", "doing ticket 14")
    checkins.ask_now(root, "lead", [q], "why")
    events = [(e["event"], e["id"]) for e in checkins._ledger(root, "lead")]
    assert events == [("queued", q.id), ("asked", q.id)]
