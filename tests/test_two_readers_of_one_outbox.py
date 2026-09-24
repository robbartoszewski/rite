"""Two permanent readers of one outbox must both see every message (A1).

WHY THIS EXISTS. `rite connect` was told *"delete one once you have relayed
it so it is not shown twice"*. That is correct for exactly one reader. From
0.6.0 there are two — the local chat and a Slack relay, both permanent by
requirement — and the rule loses messages: whoever reads first deletes, and
the other never sees it.

⚠ **Decision 1 chose a per-reader cursor over fan-out and acknowledgement
FOR A SPECIFIC REASON**, and these tests pin it: a cursor records where a
READER got to, so nothing is written into a message. Fan-out and
acknowledgement would each have needed a message to know who had seen it,
which reverses the property `TestTheSupervisorDoesNotCareWhoWrote` exists to
keep — that anything able to write a file can attach with no transport layer.
"""

from __future__ import annotations

import json

import pytest

from rite_ai.managers.mailbox import (
    OUTBOX,
    _cursor_path,
    mailbox_dir,
    mark_read,
    read,
    send,
    unread,
)
from rite_ai.names import UnsafeName


def _reply(root, text):
    return send(root, "lead", OUTBOX, text)


class TestBothReadersSeeEverything:
    def test_one_reader_consuming_does_not_take_it_from_the_other(self, tmp_path):
        """⚠ THE FAILURE A1 EXISTS FOR, stated as a test."""
        _reply(tmp_path, "the build is broken")

        first = unread(tmp_path, "lead", OUTBOX, "connect")
        assert [m.text for m in first] == ["the build is broken"]
        mark_read(tmp_path, "lead", OUTBOX, "connect", first)

        second = unread(tmp_path, "lead", OUTBOX, "slack")
        assert [m.text for m in second] == ["the build is broken"], (
            "the second reader lost the message the first one consumed — "
            "which is the delete-on-relay failure this replaces"
        )

    def test_a_reader_does_not_see_the_same_message_twice(self, tmp_path):
        _reply(tmp_path, "one")
        first = unread(tmp_path, "lead", OUTBOX, "connect")
        mark_read(tmp_path, "lead", OUTBOX, "connect", first)
        assert unread(tmp_path, "lead", OUTBOX, "connect") == []

    def test_a_later_message_is_still_delivered_after_a_cursor_exists(self, tmp_path):
        _reply(tmp_path, "one")
        mark_read(
            tmp_path,
            "lead",
            OUTBOX,
            "connect",
            unread(tmp_path, "lead", OUTBOX, "connect"),
        )
        _reply(tmp_path, "two")
        assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "connect")] == ["two"]

    def test_nothing_is_deleted_by_reading(self, tmp_path):
        """The cursor moves; the box does not shrink. A third reader added
        next year must still be able to see the history."""
        _reply(tmp_path, "one")
        _reply(tmp_path, "two")
        messages = unread(tmp_path, "lead", OUTBOX, "connect")
        mark_read(tmp_path, "lead", OUTBOX, "connect", messages)
        assert len(read(tmp_path, "lead", OUTBOX)) == 2
        assert len(list(mailbox_dir(tmp_path, "lead", OUTBOX).glob("*.json"))) == 2


class TestTheMessageStaysIdentityFree:
    """⚠ The premise Decision 1 was chosen on. If a cursor forced a sender
    field, the decision was made on a false comparison."""

    def test_a_message_gains_no_reader_fields(self, tmp_path):
        path = _reply(tmp_path, "hello")
        mark_read(
            tmp_path,
            "lead",
            OUTBOX,
            "connect",
            unread(tmp_path, "lead", OUTBOX, "connect"),
        )
        on_disk = json.loads(path.read_text())
        assert set(on_disk) == {"text", "timestamp"}, (
            f"reading wrote something into the message: {on_disk}"
        )

    def test_the_cursor_lives_outside_the_box(self, tmp_path):
        """Everything inside a box is a message — `read` globs `*.json`
        there — so a cursor kept among them would be delivered as one."""
        _reply(tmp_path, "hello")
        mark_read(
            tmp_path,
            "lead",
            OUTBOX,
            "connect",
            unread(tmp_path, "lead", OUTBOX, "connect"),
        )
        cursor = _cursor_path(tmp_path, "lead", OUTBOX, "connect")
        assert cursor.is_file()
        assert cursor.parent != mailbox_dir(tmp_path, "lead", OUTBOX)
        assert len(read(tmp_path, "lead", OUTBOX)) == 1, "the cursor was delivered"


class TestItFailsTheSafeWay:
    def test_an_unreadable_cursor_redelivers_rather_than_drops(self, tmp_path):
        """A message twice is recoverable; a message nobody sees is the
        failure this whole channel exists to prevent."""
        _reply(tmp_path, "important")
        cursor = _cursor_path(tmp_path, "lead", OUTBOX, "connect")
        cursor.parent.mkdir(parents=True, exist_ok=True)
        cursor.write_text("{not json")
        assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "connect")] == [
            "important"
        ]

    def test_a_reader_name_cannot_escape_its_directory(self, tmp_path):
        with pytest.raises(UnsafeName):
            _cursor_path(tmp_path, "lead", OUTBOX, "../../etc/passwd")

    def test_marking_nothing_creates_no_cursor(self, tmp_path):
        mark_read(tmp_path, "lead", OUTBOX, "connect", [])
        assert not _cursor_path(tmp_path, "lead", OUTBOX, "connect").exists()
