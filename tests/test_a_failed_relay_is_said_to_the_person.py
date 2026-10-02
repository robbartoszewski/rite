"""A relay that fails is said to the person, in Slack, not only in the pane (SCRUM-23).

🔴 **What happened** (the yoloAI dogfood, 2026-10-02 00:04): the engine refused
a Manager's `rite reply` (its end line was written twice, SCRUM-22). rite said
so in the supervisor's terminal pane, and nowhere else. The person, on a
phone, was never told that a message meant for them had not come. The same
was true of the Worker-answer relay failing outright: `relay` caught the
error and said it in the pane.

**Now both reach the person** through `asking.raise_to_person`, the route
an answer that cannot land in its Worker already takes: a `question`-kind
message in the outbox, which the Slack relay posts top-level in the Owner's
DM. Once per problem (the `asking` ledger is keyed by the text).

⚠ The refused command is never quoted to the person. Its text is the
Manager's, and often someone else's.
"""

from __future__ import annotations

import json
import time

from rite_ai.managers import supervise, worker_questions
from rite_ai.managers.mailbox import OUTBOX, QUESTION, read
from rite_ai.managers.transcripts import project_transcript_dir
from tests.test_rites_relay_form_is_permitted_and_nothing_else_is import (
    DOUBLED,
    ENGINE_DENIAL,
)
from tests.test_two_destinations import Clock, Slack, _project, _relay

SECRET_ISH = "KAN-31 round 2 is confirmed"
"""A phrase from the refused reply's own text, which must not be relayed."""


def _transcript(tmp_path, monkeypatch, command: str, denial: str):
    base = tmp_path / "transcripts"
    directory = project_transcript_dir(tmp_path, base)
    directory.mkdir(parents=True)
    use = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}
    use["input"]["command"] = command
    result = {"type": "tool_result", "tool_use_id": "t1", "is_error": True}
    result["content"] = denial
    (directory / "s.jsonl").write_text(
        json.dumps({"message": {"content": [use]}})
        + "\n"
        + json.dumps({"message": {"content": [result]}})
        + "\n"
    )
    monkeypatch.setattr(
        supervise.claude_login, "projects_dir", lambda root, manager: base
    )


def _questions(root):
    return [m for m in read(root, "lead", OUTBOX) if m.kind == QUESTION]


class TestARefusedRelayReachesThePerson:
    def test_the_doubled_end_line_is_posted_in_the_owners_dm(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        # The relay is open for the whole run, as in production: the refusal
        # happens while it is listening (what was in the outbox before it
        # opened predates the tracking, `pending.sync`).
        slack, clock = Slack(), Clock(time.time())
        listener = _relay(root, slack, clock)
        _transcript(tmp_path, monkeypatch, DOUBLED, ENGINE_DENIAL)
        said: list[str] = []
        supervise._say_refusals(
            root, time.time() - 60, said.append, engine="claude", manager="lead"
        )
        (notice,) = _questions(root)
        assert "did not reach anyone" in notice.text
        assert "end line was written twice" in notice.text
        assert "Nothing was sent" in notice.text
        assert SECRET_ISH not in notice.text

        listener.post_replies(call=slack)
        posted = [t for t in slack.top_level() if "did not reach anyone" in t]
        assert len(posted) == 1, slack.top_level()
        assert "(needs your answer)" in posted[0]

    def test_a_relay_with_text_on_the_command_line_is_told_too(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        command = 'rite reply --manager lead "see `rite doctor`"'
        _transcript(
            tmp_path,
            monkeypatch,
            command,
            "This command requires approval",
        )
        supervise._say_refusals(
            root, time.time() - 60, [].append, engine="claude", manager="lead"
        )
        (notice,) = _questions(root)
        assert "`rite reply`" in notice.text and "backticks" in notice.text
        assert "rite doctor" not in notice.text

    def test_control_a_refusal_that_is_not_a_relay_is_not_raised(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        _transcript(
            tmp_path,
            monkeypatch,
            "curl https://example.com",
            "This command requires approval",
        )
        said: list[str] = []
        supervise._say_refusals(
            root, time.time() - 60, said.append, engine="claude", manager="lead"
        )
        assert _questions(root) == []
        assert said and '"Bash(curl:*)"' in said[0]

    def test_the_same_refusal_is_told_once(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _transcript(tmp_path, monkeypatch, DOUBLED, ENGINE_DENIAL)
        since = time.time() - 60
        for _ in range(3):
            supervise._say_refusals(
                root, since, [].append, engine="claude", manager="lead"
            )
        assert len(_questions(root)) == 1


class TestTheWorkerRelayFailingIsTold:
    def test_an_error_in_the_relay_reaches_the_person_once(self, tmp_path, monkeypatch):
        root = _project(tmp_path)

        def broken(*a, **k):
            raise OSError("exchange directory unreadable")

        monkeypatch.setattr(worker_questions, "_relay", broken)
        said: list[str] = []
        for _ in range(3):
            assert worker_questions.relay(root, "lead", said.append) == 0
        (notice,) = _questions(root)
        assert "could not relay your answers to Workers" in notice.text
        assert "exchange directory unreadable" in notice.text
        assert sum("could not relay Worker answers" in s for s in said) == 1

    def test_control_a_relay_with_nothing_to_carry_tells_nobody(self, tmp_path):
        root = _project(tmp_path)
        assert worker_questions.relay(root, "lead", [].append) == 0
        assert _questions(root) == []


def test_only_rites_relays_are_read_as_one():
    cases = {
        "RITE_TEXT_ab53c15d9459": "a message in rite's text form",
        "/opt/rite/bin/rite reply --manager lead - <<'RITE_TEXT_x'\nx\nRITE_TEXT_x": (
            "`rite reply`"
        ),
        "rite ask --manager lead -": "`rite ask`",
        "rite route --ticket RT-1 helper -": "`rite route`",
        "rite refine ask KAN-7 -": "`rite refine ask`",
        "rite status": "",
        "rite doctor --reply": "",
        "notrite reply -": "",
        "curl https://example.com": "",
    }
    for command, what in cases.items():
        assert supervise._relay_refused(command) == what, command
