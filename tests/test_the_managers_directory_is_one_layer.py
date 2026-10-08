"""Every host-side access in a Manager's own directory goes through one
link-safe layer (`own_dir`), so a link, FIFO or directory the Manager plants
there is never followed, written through, or left to wake the loop.

The reviews measured several escapes through this directory (a ledger
replaced by a link so an append landed on an operator file; a journal entry
that was a link so an export copied an unreadable file into the project; a
FIFO that hung the supervisor; a directory in the outbox that woke the loop
for ever). The probes below plant the same SHAPES with benign contents and
check the layer refuses each: a plain file still works (the control), a link
and a FIFO are refused or replaced, a directory is set aside.

On macOS the sandbox cannot create a link or FIFO at the top of the
directory at all; it can only move in a directory that already contains one.
So the plants here are made directly (the host side must be safe regardless
of how the entry got there), and `test_the_profile_withholds_*` cover what
the sandbox itself refuses.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from rite_ai.managers import manager_dir, own_dir

CONFIG = (
    "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
    "  manager_roles:\n    - name: lead\n      engine: claude\n      preset: lead\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    rite = tmp_path / "proj" / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(CONFIG)
    monkeypatch.chdir(rite.parent)
    manager_dir(rite.parent, "lead").mkdir(parents=True, exist_ok=True)
    return rite.parent


@pytest.fixture
def outside(tmp_path) -> Path:
    """A file outside the Manager's directory, standing in for anything the
    operator owns that a planted link might point at."""
    path = tmp_path / "outside.txt"
    path.write_text("not the Manager's to touch\n")
    return path


# --- append never lands on a linked file (the measured code-execution shape) -------


def test_append_refuses_a_linked_ledger(project, outside):
    own = manager_dir(project, "lead")
    (own / "ledger.jsonl").symlink_to(outside)
    before = outside.read_text()
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.append_text(project, "lead", "ledger.jsonl", "new line\n")
    assert outside.read_text() == before


def test_append_refuses_a_fifo_ledger(project):
    own = manager_dir(project, "lead")
    os.mkfifo(own / "ledger.jsonl")
    # Refused at once, never opened (an open would block for ever).
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.append_text(project, "lead", "ledger.jsonl", "x\n")


def test_control_append_writes_a_plain_file(project):
    own_dir.append_text(project, "lead", "ledger.jsonl", "a\n")
    own_dir.append_text(project, "lead", "ledger.jsonl", "b\n")
    assert own_dir.read_text(project, "lead", "ledger.jsonl") == "a\nb\n"


# --- an atomic write replaces a link rather than following it ----------------------


def test_write_replaces_a_link_and_does_not_touch_its_target(project, outside):
    own = manager_dir(project, "lead")
    (own / "prompt.txt").symlink_to(outside)
    before = outside.read_text()
    own_dir.write_text(project, "lead", "prompt.txt", "the cycle's instruction")
    assert outside.read_text() == before
    assert not (own / "prompt.txt").is_symlink()
    assert own_dir.read_text(project, "lead", "prompt.txt") == "the cycle's instruction"


def test_write_replaces_a_fifo(project):
    own = manager_dir(project, "lead")
    os.mkfifo(own / "prompt.txt")
    own_dir.write_text(project, "lead", "prompt.txt", "go")
    assert own_dir.read_text(project, "lead", "prompt.txt") == "go"


# --- a read never follows a link, and reads nothing but a regular file -------------


def test_read_refuses_a_linked_file(project, outside):
    (manager_dir(project, "lead") / "slack.json").symlink_to(outside)
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.read_text(project, "lead", "slack.json")


def test_load_json_reads_a_linked_ledger_as_empty(project, outside):
    outside.write_text('{"secret": "value"}')
    (manager_dir(project, "lead") / "pending.json").symlink_to(outside)
    assert own_dir.load_json(manager_dir(project, "lead") / "pending.json") == {}


# --- a nested directory that is a link anywhere on the way is not descended ---------


def test_a_linked_subdirectory_is_not_descended(project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "1.json").write_text("{}")
    (manager_dir(project, "lead") / "requests").symlink_to(
        elsewhere, target_is_directory=True
    )
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.names(project, "lead", "requests", ".json")


def test_a_link_one_level_down_in_a_moved_in_directory_is_set_aside(project, outside):
    """The plant the macOS profile cannot stop: a directory built in the
    project with a link inside it, renamed in. The host side sets the link
    aside instead of reading through it."""
    outside.write_text('{"worker": "x", "ticket": "RT-1"}')
    staging = project / "staging"
    staging.mkdir()
    (staging / "1.json").symlink_to(outside)
    target = manager_dir(project, "lead") / "requests"
    staging.rename(target)
    taken = own_dir.take(project, "lead", "requests", 4096)
    assert taken == [("1.json", "")]
    assert outside.exists()
    assert not list(target.glob("1.json"))
    (aside,) = target.glob("1.json.refused-*")
    assert aside.is_symlink()


# --- a copy out (the journal export shape) never reads a linked entry --------------


def test_the_journal_exports_only_regular_files(project, tmp_path, outside):
    from rite_ai.managers import journal

    outside.write_text("OPERATOR ONLY\n")
    where = journal.journal_dir(project, "lead")
    where.mkdir(parents=True, exist_ok=True)
    (where / "20260101T000000Z-note.md").write_text("a real entry\n")
    (where / "20260101T000001Z-note.md").symlink_to(outside)
    destination = project / "exported"
    result = journal.export(project, "lead", destination)
    assert result.refused == ""
    assert (destination / "20260101T000000Z-note.md").read_text() == "a real entry\n"
    assert not (destination / "20260101T000001Z-note.md").exists()
    assert "OPERATOR ONLY" not in "".join(
        p.read_text() for p in destination.glob("*.md")
    )


# --- what the sandbox profile itself withholds -------------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_landlock_withholds_link_and_fifo_rights_in_the_managers_directories(project):
    from rite_ai.managers import landlock
    from rite_ai.managers.mailbox import OUTBOX, mailbox_dir

    policy = landlock.compose_policy(project, "lead", project / "home")
    owned = set(policy["manager_owned"])
    assert str(manager_dir(project, "lead")) in owned
    assert str(mailbox_dir(project, "lead", OUTBOX)) in owned


def test_landlock_walks_every_component_of_engine_tmp(project):
    from rite_ai.managers import landlock
    from rite_ai.managers.enclosure import engine_tmp

    policy = landlock.compose_policy(project, "lead", project / "home")
    granted = {p for p, _rel in policy["walked_from_project"]}
    assert str(engine_tmp(project, "lead")) in granted


def test_engine_tmp_parent_that_is_a_link_is_refused_at_start(project, tmp_path):
    from rite_ai.managers.enclosure import (
        LinkedManagerPath,
        engine_tmp,
        refuse_linked_manager_paths,
    )

    tmp = engine_tmp(project, "lead")
    # Replace an ANCESTOR of engine_tmp (its parent), the component a sibling
    # Manager can reach in the shared .rite/user tree.
    tmp.parent.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "sibling-reach"
    target.mkdir()
    tmp.parent.symlink_to(target, target_is_directory=True)
    with pytest.raises(LinkedManagerPath, match="is a link"):
        refuse_linked_manager_paths(project, "lead")


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")
@pytest.mark.parametrize("kind", ["link", "fifo"])
def test_the_profile_withholds_link_and_fifo_creation_at_the_top(project, kind):
    import subprocess

    from rite_ai.managers.enclosure import _own_subdirs, compose
    from rite_ai.managers.mailbox import INBOX, OUTBOX, mailbox_dir

    for box in (OUTBOX, INBOX):
        mailbox_dir(project, "lead", box).mkdir(parents=True, exist_ok=True)
    _own_subdirs(project, "lead")
    profile = project / "lead.sb"
    profile.write_text(compose(project, "lead"))
    own = manager_dir(project, "lead")
    out = mailbox_dir(project, "lead", OUTBOX)
    make = "ln -s /etc/hosts '{d}/x'" if kind == "link" else "mkfifo '{d}/x'"
    script = (
        f"{make.format(d=own)}; echo own=$?; "
        f"{make.format(d=out)}; echo out=$?; "
        f"echo ok > '{own}/plain'; echo plain=$?"
    )
    done = subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert "own=0" not in done.stdout and "out=0" not in done.stdout, done.stdout
    assert "plain=0" in done.stdout, done.stderr  # the control: a plain file works


# --- gaps the layer's own mutation run surfaced ------------------------------------


def test_read_refuses_a_fifo(project):
    """A FIFO opened for reading does not block (O_NONBLOCK) and is not a
    regular file: only the `S_ISREG` check on the read path stops it being
    read as a message or ledger."""
    os.mkfifo(manager_dir(project, "lead") / "slack.json")
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.read_text(project, "lead", "slack.json")


def test_the_base_directory_itself_is_not_followed_through_a_link(project, tmp_path):
    """Belt and suspenders: the profile stops the Manager replacing its own
    directory, but the layer opens the base `O_NOFOLLOW` regardless, so a
    base that is a link reaches nothing."""
    own = manager_dir(project, "lead")
    (own / "reply.md").write_text("real")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "reply.md").write_text("planted")
    import shutil

    shutil.rmtree(own)
    own.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.read_text(project, "lead", "reply.md")


def test_a_link_at_an_intermediate_component_is_not_descended(project, tmp_path):
    """A nested path (`checkins/queue`) whose FIRST component is a link: the
    walk must refuse at `checkins`, never reaching `queue`."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "queue").mkdir(parents=True)
    (elsewhere / "queue" / "1.json").write_text("{}")
    (manager_dir(project, "lead") / "checkins").symlink_to(
        elsewhere, target_is_directory=True
    )
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.names(project, "lead", "checkins/queue", ".json")


def test_a_path_based_ledger_does_not_read_or_write_through_a_linked_base(
    project, tmp_path
):
    """The ledger helpers (`read_file`/`write_file`/`load_json`) open the
    PARENT without following a link too, so a base that is a link reaches
    nothing — the `_parent_of` guard, distinct from the area-based one."""
    own = manager_dir(project, "lead")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "pending.json").write_text('{"planted": true}')
    import shutil

    shutil.rmtree(own)
    own.symlink_to(elsewhere, target_is_directory=True)
    # A read reaches nothing (load_json swallows the refusal to {}).
    assert own_dir.load_json(own / "pending.json") == {}
    # A write refuses rather than following the link into `elsewhere`.
    with pytest.raises(own_dir.NotARegularFile):
        own_dir.write_file(own / "pending.json", "{}")
    assert (elsewhere / "pending.json").read_text() == '{"planted": true}'


def test_landlock_withholds_the_link_and_fifo_making_rights(project):
    """The composed grant for the Manager's own directories must withhold the
    rights to make a link, FIFO or device, or to reparent one in — not merely
    list those directories. Pins the mask `apply` subtracts."""
    from rite_ai.managers import landlock

    withheld = landlock.MANAGER_OWNED_WITHHELD
    for right in (
        landlock.A_MAKE_SYM,
        landlock.A_MAKE_FIFO,
        landlock.A_MAKE_CHAR,
        landlock.A_MAKE_BLOCK,
        landlock.A_MAKE_SOCK,
        landlock.A_REFER,
    ):
        assert withheld & right, right


def test_a_unix_socket_in_a_request_directory_is_set_aside(project):
    """A socket named like a request is not a regular file: set aside, not
    skipped and left to be met again (Option A review NIT)."""
    import socket
    import tempfile

    own = manager_dir(project, "lead") / "requests"
    own.mkdir(parents=True)
    # AF_UNIX paths are short-limited and the Manager directory is deep, so
    # bind in a short temp dir and move the socket into place.
    short = Path(tempfile.mkdtemp()) / "s"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(str(short))
        os.rename(short, own / "1.json")
        taken = own_dir.take(project, "lead", "requests", 4096)
        assert taken == [("1.json", "")]
        assert list(own.glob("1.json.refused-*"))
    finally:
        s.close()
