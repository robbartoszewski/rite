"""What needs the person is apart from what is for reading (RP1, piece 1).

Every outbox message records the command that wrote it: `rite ask` and a
deferred question rite asks are QUESTIONS, the check-in is a CHECKIN, `rite
reply` is a REPLY. Only a reply is reading. A message with no kind, or one
this rite does not know, needs the person: nothing says it is only reading.

`rite reply` refuses what reads as a question, a blocker or a decision and
redirects it to `rite ask`, erring toward refusing too much (the
coordinator, 2026-09-28: "prefer redirecting too much to `rite ask` over
letting one question through into the reading pile").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import checkins
from rite_ai.managers.mailbox import (
    CHECKIN,
    INBOX,
    OUTBOX,
    QUESTION,
    REPLY,
    _needs_action,
    how_to_reply,
    put_back,
    read,
    send,
    take,
)
from rite_ai.managers.reads_as_action import sign_of_action

ALWAYS_OPEN = 'checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n'


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n  managers:\n    - lead\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n" + ALWAYS_OPEN
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


def _kinds(root: Path) -> list[str]:
    return [m.kind for m in read(root, "lead", OUTBOX)]


# --- what reads as a question ------------------------------------------------

ASKS = [
    "which schema should ticket 12 use?",
    "Should I merge RT-14",
    "shall we skip the flaky test",
    "can you add the API key",
    "Could you look at the failing run",
    "do you want the old flag kept",
    "want me to open a PR for it",
    "please review PR 61",
    "let me know which one",
    "tell me the schema",
    "which of the two options is right",
    "any objections to dropping 3.10",
    "need a decision on the retry policy",
    "this needs your approval before it merges",
    "I need to confirm the release date with you",
    "no permission to push to main",
    "blocked on the missing token",
    "RT-14 is the blocker",
    "stuck on the Linux build",
    "cannot proceed without credentials",
    "awaiting the Owner's call",
    "waiting for you on RT-9",
    "needs you: the schema",
    "your call on the naming",
    "ok to delete the old branch",
    "is that ok with the release plan",
    "unsure whether to keep both",
    "not sure the flag is still used",
]

STATEMENTS = [
    "RT-14 merged; CI green on a1b2c3d",
    "ran the suite: 562 passed, 21 skipped",
    "the flag was renamed to --out in 44e294b",
    "see https://github.com/o/r/pull/61?tab=checks for the run",
    "unblocked RT-9 by pinning the dependency",
    "confirmed green on main after the merge",
]

OVER_CAUGHT = [
    "the decisions log is in docs/decisions.md",
    "please-review label removed from RT-9",
    "the test checks whether the lock is held",
]
"""Statements it refuses, ON PURPOSE: the price of not letting a question
through. Pinned so a change that narrows the signs is a visible decision."""


@pytest.mark.parametrize("text", ASKS)
def test_what_asks_the_person_is_found(text):
    assert sign_of_action(text), text


@pytest.mark.parametrize("text", STATEMENTS)
def test_a_plain_statement_is_not(text):
    """Not a promise that statements pass; only that these do, so the rule
    is not "refuse everything"."""
    assert sign_of_action(text) == "", text


@pytest.mark.parametrize("text", OVER_CAUGHT)
def test_some_statements_are_refused_on_purpose(text):
    assert sign_of_action(text), text


# --- rite reply refuses it; rite ask takes it -------------------------------


@pytest.mark.parametrize("text", ASKS)
def test_rite_reply_refuses_it_writes_nothing_and_names_ask(project, text):
    result = CliRunner().invoke(cli, ["reply", "--manager", "lead", text])
    assert result.exit_code == 1, result.output
    assert 'rite ask --manager lead "<the same text>"' in result.output
    assert read(project, "lead", OUTBOX) == []


def test_rite_ask_takes_the_same_text_as_a_question(project):
    result = CliRunner().invoke(cli, ["ask", "--manager", "lead", ASKS[0]])
    assert result.exit_code == 0, result.output
    assert _kinds(project) == [QUESTION]


def test_rite_reply_files_a_statement_as_reading(project):
    result = CliRunner().invoke(cli, ["reply", "--manager", "lead", STATEMENTS[0]])
    assert result.exit_code == 0, result.output
    [message] = read(project, "lead", OUTBOX)
    assert message.kind == REPLY and not _needs_action(message)


# --- what rite itself sends is filed by what it is --------------------------


def test_a_deferred_question_asked_by_rite_is_a_question(project):
    q = checkins.defer(project, "lead", "rename the flag?", "doing ticket 14")
    checkins.ask_now(project, "lead", [q], "asked early:", how="idle")
    assert _kinds(project) == [QUESTION]


def test_the_checkin_is_a_checkin(project):
    checkins.defer(project, "lead", "rename the flag?", "doing ticket 14")
    checkins._deliver_checkin(project, "lead")
    assert _kinds(project) == [CHECKIN]


# --- what nothing classed needs the person ----------------------------------


def test_no_kind_needs_the_person(project):
    send(project, "lead", OUTBOX, "written by an older rite")
    [message] = read(project, "lead", OUTBOX)
    assert message.kind == "" and _needs_action(message)


def test_an_unknown_kind_on_disk_needs_the_person(project):
    path = send(project, "lead", OUTBOX, "x", kind=REPLY)
    path.write_text(json.dumps({"text": "x", "timestamp": 1.0, "kind": "fyi"}))
    [message] = read(project, "lead", OUTBOX)
    assert message.kind == "" and _needs_action(message)


def test_an_unknown_kind_is_refused_rather_than_written(project):
    with pytest.raises(ValueError, match="unknown message kind"):
        send(project, "lead", OUTBOX, "x", kind="fyi")
    assert read(project, "lead", OUTBOX) == []


def test_putting_a_message_back_keeps_its_kind(project):
    send(project, "lead", INBOX, "x", kind=QUESTION)
    taken = take(project, "lead", INBOX)
    assert put_back(taken) == 1
    assert [m.kind for m in read(project, "lead", INBOX)] == [QUESTION]


# --- the person sees which is which -----------------------------------------


def test_rite_replies_marks_a_question_and_not_a_reply(project):
    send(project, "lead", OUTBOX, "which schema", kind=QUESTION)
    send(project, "lead", OUTBOX, "RT-14 merged", kind=REPLY)
    shown = CliRunner().invoke(cli, ["replies", "lead"]).output
    assert "[needs your answer]\nwhich schema" in shown
    assert "]\nRT-14 merged" not in shown and "RT-14 merged" in shown


# --- the Manager is told which command is which ------------------------------


def test_the_manager_is_told_to_ask_with_ask(project):
    said = how_to_reply(project, "lead") + checkins.instructions(project, "lead")
    assert ' ask --manager lead "<your question>"' in said
    assert "To ask now: `" in said and ' ask --manager lead "<question>"`' in said
    assert "To ask the User something or tell them something" not in said
    assert ' reply --manager lead "<question>"' not in said
