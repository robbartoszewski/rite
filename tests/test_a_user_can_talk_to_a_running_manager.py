"""A Manager could be watched and not talked to. This is the channel.

Attaching to a Manager's tmux pane shows what it did. It gives the User no
way to answer a question the Manager needs answered and no way to redirect
it without killing it. A mailbox of timestamped files is the smallest thing
that fixes that, and it reuses `reporting/outbox.py`'s naming rather than
inventing one.

⚠ **The supervisor does not care WHO wrote a message**, and these tests pin
that rather than leaving it true by accident: nothing records a sender,
checks one, or knows `rite connect` exists. That is all Slack needs later —
it writes the same files — and it is the only thing done for it.
"""

from __future__ import annotations

import json

from rite_ai.managers.mailbox import (
    INBOX,
    OUTBOX,
    delivery_note,
    mailbox_dir,
    read,
    send,
    take,
    waiting,
)
from rite_ai.managers.session import StartResult, Stopped
from rite_ai.managers.supervise import supervise


class TestTheMailboxItself:
    def test_a_message_sent_is_a_message_read(self, tmp_path):
        send(tmp_path, "lead", INBOX, "please look at ticket 12")
        assert [m.text for m in read(tmp_path, "lead", INBOX)] == [
            "please look at ticket 12"
        ]

    def test_order_is_send_order(self, tmp_path):
        for word in ("first", "second", "third"):
            send(tmp_path, "lead", INBOX, word)
        assert [m.text for m in read(tmp_path, "lead", INBOX)] == [
            "first",
            "second",
            "third",
        ]

    def test_two_messages_in_the_same_millisecond_both_survive(self, tmp_path):
        """The outbox measured this: one name for two writes lost the
        first. Same naming here, same reason."""
        a = send(tmp_path, "lead", INBOX, "one")
        b = send(tmp_path, "lead", INBOX, "two")
        assert a != b
        assert len(read(tmp_path, "lead", INBOX)) == 2

    def test_the_boxes_are_separate(self, tmp_path):
        send(tmp_path, "lead", INBOX, "to the manager")
        send(tmp_path, "lead", OUTBOX, "from the manager")
        assert [m.text for m in read(tmp_path, "lead", INBOX)] == ["to the manager"]
        assert [m.text for m in read(tmp_path, "lead", OUTBOX)] == ["from the manager"]

    def test_managers_do_not_share_a_mailbox(self, tmp_path):
        send(tmp_path, "lead", INBOX, "for lead")
        assert read(tmp_path, "planner", INBOX) == []

    def test_take_clears_what_it_read(self, tmp_path):
        send(tmp_path, "lead", INBOX, "once")
        assert [m.text for m in take(tmp_path, "lead", INBOX)] == ["once"]
        assert read(tmp_path, "lead", INBOX) == []

    def test_a_corrupt_file_does_not_stop_the_others(self, tmp_path):
        """⚠ Read from the supervisor's wait loop: one bad file must not
        take a run down or hide the messages beside it."""
        send(tmp_path, "lead", INBOX, "good")
        bad = mailbox_dir(tmp_path, "lead", INBOX) / "0_0_0.json"
        bad.write_text("{not json")
        assert [m.text for m in read(tmp_path, "lead", INBOX)] == ["good"]

    def test_an_empty_message_is_not_a_message(self, tmp_path):
        send(tmp_path, "lead", INBOX, "   ")
        assert read(tmp_path, "lead", INBOX) == []

    def test_waiting_is_false_before_anything_is_sent(self, tmp_path):
        assert not waiting(tmp_path, "lead", INBOX)
        send(tmp_path, "lead", INBOX, "hello")
        assert waiting(tmp_path, "lead", INBOX)


class TestTheSupervisorDoesNotCareWhoWrote:
    def test_a_file_nobody_rite_wrote_is_delivered(self, tmp_path):
        """⚠ The whole of the Slack-readiness. A message hand-written into
        the directory, with no sender and no rite involvement, is picked up
        exactly as one from `rite connect` would be."""
        where = mailbox_dir(tmp_path, "lead", INBOX)
        where.mkdir(parents=True)
        (where / "1700000000000_1_0.json").write_text(
            json.dumps({"text": "from somewhere else", "timestamp": 1700000000.0})
        )
        assert [m.text for m in read(tmp_path, "lead", INBOX)] == [
            "from somewhere else"
        ]

    def test_no_sender_field_is_required(self, tmp_path):
        where = mailbox_dir(tmp_path, "lead", INBOX)
        where.mkdir(parents=True)
        (where / "1700000000001_1_0.json").write_text(json.dumps({"text": "bare"}))
        assert [m.text for m in read(tmp_path, "lead", INBOX)] == ["bare"]


class TestTheDeliveryNote:
    def test_nothing_waiting_adds_nothing(self):
        assert delivery_note([]) == ""

    def test_the_text_survives_into_the_note(self, tmp_path):
        send(tmp_path, "lead", INBOX, "stop work on ticket 4")
        note = delivery_note(read(tmp_path, "lead", INBOX))
        assert "stop work on ticket 4" in note


class TestItReachesTheManagerAndComesBack:
    """The modest end-to-end: a message written to the mailbox arrives in
    the instruction a cycle is launched with, and the Manager's reply is
    readable by the User side. No tmux, no engine — the seam under test is
    the supervisor's composition point, not Claude Code."""

    def test_a_waiting_message_arrives_in_the_next_cycles_instruction(
        self, tmp_path, monkeypatch
    ):
        import rite_ai.managers.supervise as sup

        (tmp_path / ".rite").mkdir()
        send(tmp_path, "lead", INBOX, "please prioritise ticket 9")

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
        ):
            seen.append(prompt)
            return StartResult(True, "ok", session="s1", attach="a")

        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
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
            verdict=lambda _r: "ready",
            starter=starter,
            poll=0,
        )

        assert seen, "no cycle started"
        assert "please prioritise ticket 9" in seen[0], seen[0][:300]
        assert "work the queue" in seen[0], "the ordinary instruction was lost"

    def test_a_delivered_message_is_not_delivered_twice(self, tmp_path, monkeypatch):
        """Taken, not peeked — otherwise a Manager is told the same thing
        every cycle until it acts."""
        import rite_ai.managers.supervise as sup

        (tmp_path / ".rite").mkdir()
        send(tmp_path, "lead", INBOX, "one time only")
        assert waiting(tmp_path, "lead", INBOX)

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
        ):
            return StartResult(True, "ok", session="s1", attach="a")

        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
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
            prompt="work",
            verdict=lambda _r: "ready",
            starter=starter,
            poll=0,
        )
        assert not waiting(tmp_path, "lead", INBOX)

    def test_the_managers_reply_is_readable_by_the_user_side(self, tmp_path):
        """The return leg: whatever the Manager writes to `out/` is what
        `rite connect` reads."""
        send(tmp_path, "lead", OUTBOX, "which ticket first, 4 or 9?")
        assert [m.text for m in read(tmp_path, "lead", OUTBOX)] == [
            "which ticket first, 4 or 9?"
        ]

    def test_the_instruction_tells_the_manager_where_to_reply(
        self, tmp_path, monkeypatch
    ):
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
        ):
            seen.append(prompt)
            return StartResult(True, "ok", session="s1", attach="a")

        monkeypatch.setattr(
            sup, "liveness", lambda n: type("L", (), {"alive": False})()
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
            prompt="work",
            verdict=lambda _r: "ready",
            starter=starter,
            poll=0,
        )
        assert str(mailbox_dir(tmp_path, "lead", OUTBOX)) in seen[0]
