"""The mailbox lives outside the project, and a project mid-flight loses nothing.

⚠ **An inbox write IS an instruction (MM-2)**, and every Manager's profile
grants its project. So since 0.6.0 the boxes are under rite's home
(`mailbox.mail_root`), where no profile grants them, and the fence holds by
construction rather than by a rule carving them out.

The move copies nothing. Every reader reads the old in-tree box too, merged by
filename, and a reader's old cursor counts until it writes a new one — so a
message written before the upgrade is delivered, and one already relayed is
not relayed again. These are the properties that make "nothing is copied" safe,
and they are platform-independent, so they run everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.managers.mailbox import (
    INBOX,
    OUTBOX,
    legacy_mail_root,
    mail_root,
    mailbox_dir,
    mark_read,
    prune,
    read,
    send,
    take,
    unread,
    waiting,
)

OLD_NAME = "1000000000000_0000001_000000000001.json"


def _old(root: Path, box: str, name: str = OLD_NAME, text: str = "old") -> Path:
    where = legacy_mail_root(root, "lead") / box
    where.mkdir(parents=True, exist_ok=True)
    path = where / name
    path.write_text(json.dumps({"text": text, "timestamp": 1.0}) + "\n")
    return path


def _old_cursor(root: Path, box: str, reader: str, last: str) -> None:
    where = legacy_mail_root(root, "lead") / f"{box}.read"
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{reader}.json").write_text(json.dumps({"last": last}) + "\n")


def test_the_mailbox_is_not_in_the_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert not mailbox_dir(root, "lead", INBOX).resolve().is_relative_to(root.resolve())
    assert send(root, "lead", INBOX, "hi").parent == mailbox_dir(root, "lead", INBOX)
    assert not (root / ".rite" / "managers" / "lead" / "mail").exists()


def test_two_checkouts_of_one_project_do_not_share_an_inbox(tmp_path):
    """The credential namespace is committed and every checkout shares it. A
    mailbox keyed by it would let one checkout's `lead` take the other's."""
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "config.yaml").write_text(
            "credentials:\n  namespace: acme-1234abcd\n"
        )
    send(a, "lead", INBOX, "for a")
    assert mail_root(a, "lead") != mail_root(b, "lead")
    assert read(b, "lead", INBOX) == []


def test_the_checkout_is_named_beside_its_mail(tmp_path):
    """For the person reading `~/.rite/managers/` — which project is this?"""
    root = tmp_path / "proj"
    root.mkdir()
    send(root, "lead", INBOX, "hi")
    marker = mail_root(root, "lead").parent.parent / "project"
    assert marker.read_text().strip() == str(root.resolve())


def test_an_old_inbox_message_is_delivered_in_send_order_and_taken(tmp_path):
    _old(tmp_path, INBOX)
    send(tmp_path, "lead", INBOX, "new")
    assert waiting(tmp_path, "lead", INBOX)
    assert [m.text for m in take(tmp_path, "lead", INBOX)] == ["old", "new"]
    assert not waiting(tmp_path, "lead", INBOX)
    assert list((legacy_mail_root(tmp_path, "lead") / INBOX).iterdir()) == []


def test_waiting_sees_an_old_message_alone(tmp_path):
    """The supervisor's 2-second poll must wake for a message only the old
    box holds — the case of an older `rite message` after the upgrade."""
    _old(tmp_path, INBOX)
    assert waiting(tmp_path, "lead", INBOX)


def test_a_reader_keeps_its_place_across_the_move(tmp_path):
    """Without this the Slack relay would repost every old reply."""
    _old(tmp_path, OUTBOX)
    _old_cursor(tmp_path, OUTBOX, "slack", OLD_NAME)
    send(tmp_path, "lead", OUTBOX, "after the upgrade")
    assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "slack")] == [
        "after the upgrade"
    ]


def test_a_reader_with_no_old_cursor_sees_the_old_messages(tmp_path):
    _old(tmp_path, OUTBOX)
    assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "person")] == ["old"]


def test_once_a_reader_has_a_new_cursor_the_old_one_no_longer_counts(tmp_path):
    """Otherwise the old position would pull the reader back forever."""
    _old(tmp_path, OUTBOX)
    _old_cursor(tmp_path, OUTBOX, "slack", "")
    seen = unread(tmp_path, "lead", OUTBOX, "slack")
    assert [m.text for m in seen] == ["old"]
    mark_read(tmp_path, "lead", OUTBOX, "slack", seen)
    assert unread(tmp_path, "lead", OUTBOX, "slack") == []


def test_retention_drains_the_old_outbox_only_once_it_is_processed(tmp_path):
    old = _old(tmp_path, OUTBOX)
    assert prune(tmp_path, "lead", OUTBOX, now=10**12, max_age=0).removed == ()
    assert old.exists(), "an unread old message was removed"
    mark_read(tmp_path, "lead", OUTBOX, "person", read(tmp_path, "lead", OUTBOX))
    assert old in prune(tmp_path, "lead", OUTBOX, now=10**12, max_age=0).removed
