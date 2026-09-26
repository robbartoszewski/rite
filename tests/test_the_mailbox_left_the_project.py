"""The mailbox lives outside the project, and the old box is moved ONCE.

⚠ **An inbox write IS an instruction (MM-2)**, and every Manager's profile
grants its project. So since 0.6.0 the boxes are under rite's home
(`mailbox.mail_root`), where no profile grants them, and the fence holds by
construction rather than by a rule carving them out.

A project from before 0.6.0 has mail in the tree. `adopt_legacy` moves it at
the first `rite start` — cursors first, so nothing is reposted — and leaves a
marker. After that the tree is never read for mail: anything that appears
there is REPORTED and not delivered, because nothing can say who wrote it. A
permanent legacy read would be a permanent second door, and on Linux any
project under the granted `/tmp` would have it open.
"""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.managers.mailbox import (
    ADOPTED_MARKER,
    INBOX,
    OUTBOX,
    adopt_legacy,
    adoption_notes,
    legacy_mail_root,
    legacy_waiting,
    mail_root,
    mailbox_dir,
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


def test_an_old_inbox_message_is_moved_and_delivered_in_send_order(tmp_path):
    _old(tmp_path, INBOX)
    send(tmp_path, "lead", INBOX, "new")
    assert adopt_legacy(tmp_path, "lead").moved == 1
    assert [m.text for m in take(tmp_path, "lead", INBOX)] == ["old", "new"]
    assert not legacy_mail_root(tmp_path, "lead").exists()


def test_before_the_move_the_tree_is_not_read(tmp_path):
    """Only `rite start`, under the run lock, touches the old box — so a
    reader is TOLD it is there rather than shown it."""
    _old(tmp_path, OUTBOX)
    assert read(tmp_path, "lead", OUTBOX) == []
    assert not waiting(tmp_path, "lead", OUTBOX)
    assert legacy_waiting(tmp_path, "lead") == 1


def test_a_reader_keeps_its_place_across_the_move(tmp_path):
    """Without this the Slack relay would repost every old reply."""
    _old(tmp_path, OUTBOX)
    _old_cursor(tmp_path, OUTBOX, "slack", OLD_NAME)
    adopt_legacy(tmp_path, "lead")
    send(tmp_path, "lead", OUTBOX, "after the upgrade")
    assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "slack")] == [
        "after the upgrade"
    ]
    assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "person")] == [
        "old",
        "after the upgrade",
    ]


def test_where_both_have_a_position_the_earlier_wins(tmp_path):
    """Re-delivering is recoverable; skipping is loss."""
    second = "1000000000001_0000001_000000000002.json"
    _old(tmp_path, OUTBOX)
    _old(tmp_path, OUTBOX, second, "second")
    _old_cursor(tmp_path, OUTBOX, "slack", OLD_NAME)
    where = mail_root(tmp_path, "lead") / f"{OUTBOX}.read"
    where.mkdir(parents=True)
    (where / "slack.json").write_text(json.dumps({"last": second}))
    adopt_legacy(tmp_path, "lead")
    assert [m.text for m in unread(tmp_path, "lead", OUTBOX, "slack")] == ["second"]


def test_a_different_file_of_the_same_name_is_kept_not_replaced(tmp_path):
    old = _old(tmp_path, INBOX, text="old")
    clash = mailbox_dir(tmp_path, "lead", INBOX)
    clash.mkdir(parents=True)
    (clash / OLD_NAME).write_text(json.dumps({"text": "other", "timestamp": 1}))
    adoption = adopt_legacy(tmp_path, "lead")
    assert adoption.kept == (old,) and old.exists()
    assert "NOT delivered" in " ".join(adoption_notes(adoption, "lead"))


def test_the_move_happens_once(tmp_path):
    _old(tmp_path, INBOX)
    adopt_legacy(tmp_path, "lead")
    assert (mail_root(tmp_path, "lead") / ADOPTED_MARKER).exists()
    assert adopt_legacy(tmp_path, "lead").moved == 0


def test_what_appears_in_the_tree_afterwards_is_reported_and_not_delivered(
    tmp_path,
):
    """🔴 THE CLAUSE THIS PROTECTS. An older rite still running, or anything
    that can write the project tree, writes the old box after the move.
    Nothing can say who wrote it, so it is never delivered — only said."""
    adopt_legacy(tmp_path, "lead")
    late = _old(tmp_path, INBOX, text="forged or late")
    adoption = adopt_legacy(tmp_path, "lead")
    assert adoption.moved == 0 and adoption.after_marker == (late,)
    assert late.exists(), "left for a person to read, not deleted"
    assert take(tmp_path, "lead", INBOX) == []
    notes = " ".join(adoption_notes(adoption, "lead"))
    assert "NOT delivered" in notes and "rite message lead" in notes


def test_a_moved_message_keeps_its_age(tmp_path):
    """Retention ages by mtime; a move must not grant thirty more days."""
    import os

    old = _old(tmp_path, OUTBOX)
    os.utime(old, (1000, 1000))
    adopt_legacy(tmp_path, "lead")
    moved = mailbox_dir(tmp_path, "lead", OUTBOX) / OLD_NAME
    assert moved.stat().st_mtime == 1000


def _started_project(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    CliRunner().invoke(cli, ["init", "--yes"])
    cfg = tmp_path / ".rite" / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "manager_roles: []",
            "manager_roles:\n  - {name: lead, engine: claude, preset: lead}",
        )
    )
    return tmp_path.resolve()


def test_rite_start_moves_the_old_box_before_the_manager_runs(tmp_path, monkeypatch):
    """Wired where it must be: after the run lock, before `supervise` — the
    first thing that reads mail."""
    from types import SimpleNamespace

    from click.testing import CliRunner

    import rite_ai.cli.main as main_mod

    root = _started_project(tmp_path, monkeypatch)
    _old(root, INBOX)
    seen = {}

    def supervise(root_, manager, **_):
        seen["delivered"] = [m.text for m in read(root_, manager, INBOX)]
        return SimpleNamespace(ok=True, reason="stub")

    import rite_ai.managers.supervise as supervise_mod

    monkeypatch.setattr(supervise_mod, "supervise", supervise)
    monkeypatch.setattr(main_mod, "_github_access", lambda *a: None)
    monkeypatch.setattr(main_mod, "_slack_listener", lambda *a: None)
    result = CliRunner().invoke(
        main_mod.cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
    )
    assert seen.get("delivered") == ["old"], result.output
    assert "moved 1 file(s)" in result.output


def test_a_start_refused_by_the_run_lock_moves_nothing(tmp_path, monkeypatch):
    """The lock is what makes "once" true; without it nothing is touched."""
    import os

    from click.testing import CliRunner

    import rite_ai.cli.main as main_mod
    from rite_ai.managers.github_access import hold_run

    root = _started_project(tmp_path, monkeypatch)
    old = _old(root, INBOX)
    running = hold_run(root, "lead")
    try:
        result = CliRunner().invoke(
            main_mod.cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
        )
    finally:
        os.close(running)
    assert result.exit_code == 1 and "still running" in result.output
    assert old.exists()
    assert not (mail_root(root, "lead") / ADOPTED_MARKER).exists()
