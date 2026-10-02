"""A refusal is reported once, and not as lost when the retry arrived (SCRUM-22).

🔴 **What happened** (the yoloAI dogfood, 2026-10-02): the person was told
sixteen times between 18:46 and 19:08 that "a message in rite's text form
did not reach anyone … its end line was written twice". Every relay in that
window had gone. The only doubled end line in the Owner's conversation was
from 00:04Z, and the Manager's retry had delivered it eleven seconds later.

Two causes, each pinned on its own here:

- `refused_commands` kept any transcript FILE touched since the session began
  and then reported every denial in it. A resumed conversation appends to one
  `.jsonl`, so each session re-reported the whole history. Now each denial's
  own `timestamp` bounds it, and a ledger keyed by the engine's `tool_use_id`
  reports it once whatever the timestamps say.
- The person's notice said "Nothing was sent" for a call whose retry had
  arrived. Now a later message of the Manager's own in its outbox suppresses
  it, for the relays that write the outbox.

The fixture in `test_a_failed_relay_is_said_to_the_person.py` scanned three
times with the SAME session start, which is the one case the notice's own text
dedupes; these change the session start, as production does.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.managers import supervise
from rite_ai.managers.mailbox import OUTBOX, QUESTION, read, send
from rite_ai.managers.transcripts import project_transcript_dir, refusals
from tests.test_rites_relay_form_is_permitted_and_nothing_else_is import (
    DOUBLED,
    ENGINE_DENIAL,
)
from tests.test_two_destinations import _project

HOUR = 3600.0
PLAIN_DENIAL = "This command requires approval"


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat().replace("+00:00", "Z")


class Conversation:
    """One resumed Claude conversation: a single `.jsonl` every cycle appends
    to, as a Manager's is."""

    def __init__(self, tmp_path: Path, monkeypatch, name: str = "s.jsonl"):
        self.base = tmp_path / "transcripts"
        self.dir = project_transcript_dir(tmp_path, self.base)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / name
        self.calls = 0
        monkeypatch.setattr(
            supervise.claude_login, "projects_dir", lambda root, manager: self.base
        )

    def _append(self, entry: dict) -> None:
        with self.path.open("a") as out:
            out.write(json.dumps(entry) + "\n")

    def call(
        self, command: str, *, at: float | None, denied: bool, call_id: str = ""
    ) -> str:
        self.calls += 1
        call_id = call_id or f"toolu_{self.calls:04d}"
        stamp = {"timestamp": _iso(at)} if at is not None else {}
        use = {"type": "tool_use", "id": call_id, "name": "Bash"}
        use["input"] = {"command": command}
        self._append({"message": {"content": [use]}, **stamp})
        result = {"type": "tool_result", "tool_use_id": call_id}
        if denied:
            # The engine's own shape: the doubled heredoc's denial NAMES the
            # stray end line; a plain command's names nothing.
            said = ENGINE_DENIAL if command == DOUBLED else PLAIN_DENIAL
            result.update({"is_error": True, "content": said})
        else:
            result["content"] = "reply queued from 'lead'"
        self._append({"message": {"content": [result]}, **stamp})
        return call_id


def _session(root: Path, since: float, manager: str = "lead") -> list[str]:
    said: list[str] = []
    supervise._say_refusals(root, since, said.append, engine="claude", manager=manager)
    return said


def _notices(root: Path) -> list[str]:
    return [
        m.text
        for m in read(root, "lead", OUTBOX)
        if m.kind == QUESTION and "did not reach anyone" in m.text
    ]


REPLY = (
    "/opt/rite/bin/rite reply --manager lead - <<'RITE_TEXT_aaaaaaaaaaaa'\n"
    "ok\nRITE_TEXT_aaaaaaaaaaaa"
)


class TestOncePerRefusal:
    def test_a_resumed_conversation_reports_its_old_refusal_once(
        self, tmp_path, monkeypatch
    ):
        """The live shape: one denial, then session after session appending
        successful calls to the same file, each with a later start."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 10 * HOUR
        talk.call(DOUBLED, at=t0, denied=True)

        first = _session(root, since=t0 - 60)
        assert any("RITE_TEXT_ab53c15d9459" in line for line in first)
        for n in range(1, 5):
            talk.call(REPLY, at=t0 + n * HOUR + 30, denied=False)
            later = _session(root, since=t0 + n * HOUR)
            assert later == [], f"session {n} reported an old refusal again: {later}"

        assert len(_notices(root)) == 1, _notices(root)

    def test_the_session_bound_alone_drops_an_older_denial(self, tmp_path, monkeypatch):
        """The scan, without the ledger: the entry's own time is what decides,
        though the file was touched after the session began."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - HOUR
        talk.call("curl https://example.com", at=t0, denied=True)
        talk.call(REPLY, at=t0 + 600, denied=False)  # touches the file now

        # Read through `refusals` with the transcript directory named: with no
        # Manager, `_say_refusals` reads the default directory, so asserting on
        # it here would pass whatever the bound did (it did, once).
        assert refusals(root, t0 + 300, base=talk.base) == []
        assert [r.command for r in refusals(root, t0 - 1, base=talk.base)] == [
            "curl https://example.com"
        ]

    def test_the_ledger_alone_reports_an_undated_denial_once(
        self, tmp_path, monkeypatch
    ):
        """No timestamps, so the session bound cannot decide: the id does."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        talk.call(DOUBLED, at=None, denied=True)
        start = time.time() - 60
        reported = [_session(root, since=start + n) for n in range(4)]
        assert sum(1 for said in reported if said) == 1, reported
        assert len(_notices(root)) == 1

    def test_history_copied_into_a_new_file_is_not_reported_again(
        self, tmp_path, monkeypatch
    ):
        """A resumed session carries earlier history into a new file, with the
        original timestamps and the same call ids (`_latest_event`)."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 2 * HOUR
        call = talk.call(DOUBLED, at=t0, denied=True)
        _session(root, since=t0 - 60)
        copy = Conversation(tmp_path, monkeypatch, name="resumed.jsonl")
        copy.call(DOUBLED, at=t0, denied=True, call_id=call)
        assert _session(root, since=t0 - 60) == []
        assert len(_notices(root)) == 1

    def test_a_new_refusal_after_an_old_one_is_still_reported(
        self, tmp_path, monkeypatch
    ):
        """Control: the fix must not swallow the next real refusal."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 2 * HOUR
        talk.call(DOUBLED, at=t0, denied=True)
        _session(root, since=t0 - 60)
        talk.call("curl https://example.com", at=t0 + HOUR + 10, denied=True)
        said = _session(root, since=t0 + HOUR)
        assert len(said) == 1 and "curl" in said[0]


class TestARetryThatArrived:
    def test_a_refused_reply_whose_retry_arrived_is_not_told_as_lost(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 120
        talk.call(DOUBLED, at=t0, denied=True)
        send(
            root,
            "lead",
            OUTBOX,
            "Status: KAN-31 round 2 is confirmed.",
        )

        said = _session(root, since=t0 - 60)

        assert _notices(root) == []
        assert any("sent again, and the second one arrived" in s for s in said)

    def test_control_without_a_retry_the_person_is_told(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 120
        talk.call(DOUBLED, at=t0, denied=True)

        _session(root, since=t0 - 60)

        (notice,) = _notices(root)
        assert "that call sent nothing" in notice
        assert "Nothing was sent" not in notice

    def test_a_message_from_before_the_refusal_is_not_its_retry(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        send(root, "lead", OUTBOX, "an earlier status")
        t0 = time.time() + 60  # the refusal comes AFTER that message
        talk.call(DOUBLED, at=t0, denied=True)

        _session(root, since=t0 - 60)

        assert len(_notices(root)) == 1

    def test_rites_own_notice_is_not_the_managers_retry(self, tmp_path, monkeypatch):
        """Only a message the Manager wrote shows its retry arrived."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 120
        talk.call(DOUBLED, at=t0, denied=True)
        send(
            root,
            "lead",
            OUTBOX,
            "KAN-9 · q1a2b · Worker alpha is waiting · reply in this thread\n> which?",
            kind=QUESTION,
        )

        _session(root, since=t0 - 60)

        assert len(_notices(root)) == 1

    def test_a_refused_route_is_told_even_if_a_reply_followed(
        self, tmp_path, monkeypatch
    ):
        """A route does not write the outbox, so a reply there shows nothing
        about whether the route went."""
        root = _project(tmp_path)
        talk = Conversation(tmp_path, monkeypatch)
        t0 = time.time() - 120
        route = (
            "/opt/rite/bin/rite route --ticket KAN-1 helper - "
            "<<'RITE_TEXT_bbbbbbbbbbbb'\nx\nRITE_TEXT_bbbbbbbbbbbb\n"
            "RITE_TEXT_bbbbbbbbbbbb"
        )
        talk.call(route, at=t0, denied=True)
        send(root, "lead", OUTBOX, "a status reply")

        _session(root, since=t0 - 60)

        assert len(_notices(root)) == 1
