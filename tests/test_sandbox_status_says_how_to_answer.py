"""`rite sandbox status` says how to answer a Worker's question the way that
works (SCRUM-25).

It said "answer it by attaching". Since S30 (0.7.0a4) the answer goes back
through the question's Slack thread, or `rite message <owner> "<qid> …"`, and
the Owner's supervisor writes it into the file the Worker polls
(`worker_questions.relay`). Attaching sent people to type into a live session
for a question rite had already posted. Each state the person can be in gets
the instruction that fits it, and the id an answer is matched by.
"""

from __future__ import annotations

import time
from pathlib import Path

from click.testing import CliRunner

import rite_ai.sandbox as sandbox_mod
from rite_ai.cli.main import cli
from rite_ai.managers import worker_questions as wq
from rite_ai.sandbox import SandboxStatus
from rite_ai.sandbox import questions as q

SANDBOX = "rite-p-alpha"


def _project(tmp_path: Path, monkeypatch, managers: list[str]) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: p\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    # Declared as `rite add manager` declares them: in both keys, the first
    # holding `route` (preset lead), so it is the Owner that relays.
    listed = "".join(f"    - {m}\n" for m in managers)
    presets = ["lead", *["executor"] * len(managers)][: len(managers)]
    roles = "".join(
        f"    - {{name: {m}, engine: claude, preset: {p}}}\n"
        for m, p in zip(managers, presets, strict=True)
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
        + (
            "coordination:\n  managers:\n" + listed + "  manager_roles:\n" + roles
            if managers
            else ""
        )
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _status(monkeypatch, status: str) -> str:
    asked = q.WorkerQuestion(
        sandbox=SANDBOX,
        question="WHICH timeout?",
        context="",
        raised_at=time.time(),
        path=Path("/x/rw/files/question.json"),
    )
    monkeypatch.setattr(
        sandbox_mod, "worker_sandbox_status", lambda w, r=None: SandboxStatus(status)
    )
    monkeypatch.setattr(q, "worker_question", lambda w, r=None: asked)
    result = CliRunner().invoke(cli, ["sandbox", "status", "alpha"])
    assert result.exit_code == 0, result.output
    return result.output


def _raised(root: Path, teller: str, qid: str) -> None:
    """What `worker_questions.surface` records once it has told the person."""
    wq._store(wq._ledger_path(root, teller), {SANDBOX: qid})


def test_a_raised_question_names_its_thread_and_the_command_with_its_id(
    tmp_path, monkeypatch
):
    root = _project(tmp_path, monkeypatch, ["lead", "helper"])
    _raised(root, "lead", "q3f9a")

    said = _status(monkeypatch, "idle")

    assert "attaching" not in said, "it still sends the person to attach"
    assert "reply in its Slack thread" in said
    assert 'rite message lead "q3f9a <your answer>"' in said
    assert "rite start lead" in said, "it does not say the relay needs it running"


def test_a_question_not_yet_passed_on_says_so_rather_than_naming_a_thread(
    tmp_path, monkeypatch
):
    _project(tmp_path, monkeypatch, ["lead"])

    said = _status(monkeypatch, "idle")

    assert "has not been passed on yet" in said
    assert "rite start lead" in said
    assert "rite message" not in said, "it names an id nobody has been given"


def test_a_stopped_worker_cannot_be_answered_and_is_said_so(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch, ["lead"])
    _raised(root, "lead", "q3f9a")

    said = _status(monkeypatch, "stopped")

    assert "has stopped, so no answer can reach it" in said
    assert "rite message" not in said


def test_with_no_manager_to_relay_attaching_is_the_way_and_is_named(
    tmp_path, monkeypatch
):
    """The one state where attaching IS the route: nobody relays."""
    _project(tmp_path, monkeypatch, [])

    said = _status(monkeypatch, "idle")

    assert "no Manager is declared to relay" in said
    assert f"yoloai attach {SANDBOX}" in said


def test_the_command_it_names_carries_the_answer_into_the_worker(tmp_path, monkeypatch):
    """The printed route, run: `rite message lead "q3f9a …"` on this machine,
    then the Owner's relay. Only the last step, the write into the sandbox's
    exchange directory, is captured; everything before it is rite's own."""
    root = _project(tmp_path, monkeypatch, ["lead"])
    worker = root / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: lead\n  modules: []\n"
    )
    _raised(root, "lead", "q3f9a")
    monkeypatch.setattr(sandbox_mod, "existing_sandbox_name", lambda w, r=None: SANDBOX)
    monkeypatch.setattr(
        sandbox_mod, "worker_sandbox_status", lambda w, r=None: SandboxStatus("idle")
    )
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        q, "deliver_answer", lambda sb, words, status=None: written.append((sb, words))
    )

    sent = CliRunner().invoke(cli, ["message", "lead", "q3f9a the http one, as a flag"])
    assert sent.exit_code == 0, sent.output
    said: list[str] = []
    carried = wq.relay(root, "lead", said.append)

    assert carried == 1, said
    assert written == [(SANDBOX, "q3f9a the http one, as a flag")]
