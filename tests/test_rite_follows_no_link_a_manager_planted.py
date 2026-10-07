"""rite, outside every boundary, follows no link a Manager planted in its own
directory or outbox (SCRUM-69 round-3 review, SCRUM-59 final review).

🔴 **Measured.** A Manager can create links inside its own directory and
outbox (its profile grants them writable), and the supervisor reads,
writes, renames and removes files there outside every boundary. Through a
link it:
- overwrote any file the person owns with the cycle's prompt (`prompt.txt`);
- took another Manager's route as this one's (`routes/`);
- relayed any readable JSON file as the Manager's message (the outbox).
On Linux a linked `engine_tmp` would have been granted to the next session.

Each test plants the link, runs the real host-side function, and checks the
target is untouched; a control shows the ordinary file still works.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from rite_ai.managers import manager_dir
from rite_ai.managers.mailbox import OUTBOX, mailbox_dir, read, take

CONFIG = (
    "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
    "    - helper\n  manager_roles:\n    - name: lead\n      engine: claude\n"
    "      preset: lead\n    - name: helper\n      engine: claude\n"
    "      preset: executor\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    rite = tmp_path / "proj" / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        "modules:\n  svc:\n    path: svc/\n    url: ''\n    branch: main\n"
    )
    (rite / "config.yaml").write_text(CONFIG)
    monkeypatch.chdir(rite.parent)
    return rite.parent


@pytest.fixture
def victim(tmp_path) -> Path:
    path = tmp_path / "victim.json"
    path.write_text(
        '{"text": "the operator\'s own file", "worker": "alpha", "ticket": "RT-1"}'
    )
    return path


def test_the_prompt_is_never_written_through_a_link(project, victim):
    from rite_ai.managers.session import PROMPT_FILE, _write_prompt

    own = manager_dir(project, "lead")
    own.mkdir(parents=True)
    (own / PROMPT_FILE).symlink_to(victim)
    before = victim.read_text()
    _write_prompt(own / PROMPT_FILE, "the cycle's prompt")
    assert victim.read_text() == before
    # The link was REPLACED by an atomic rename, never written through.
    assert not (own / PROMPT_FILE).is_symlink()
    assert (own / PROMPT_FILE).read_text() == "the cycle's prompt"


def test_control_the_prompt_is_written_0600(project):
    from rite_ai.managers.session import PROMPT_FILE, _write_prompt

    own = manager_dir(project, "lead")
    own.mkdir(parents=True)
    _write_prompt(own / PROMPT_FILE, "go")
    assert (own / PROMPT_FILE).read_text() == "go"
    assert (own / PROMPT_FILE).stat().st_mode & 0o777 == 0o600


def test_routes_linked_to_another_managers_are_not_taken(project):
    from rite_ai.managers import routing

    theirs = manager_dir(project, "helper") / routing.ROUTES_DIRNAME
    theirs.mkdir(parents=True)
    (theirs / "1.json").write_text('{"to": "x", "text": "helper\'s route"}')
    own = manager_dir(project, "lead")
    own.mkdir(parents=True)
    (own / routing.ROUTES_DIRNAME).symlink_to(theirs, target_is_directory=True)
    assert routing.take(project, "lead") == []
    assert (theirs / "1.json").exists()


def test_control_a_route_is_taken(project):
    from rite_ai.managers import routing

    routing.request(project, "lead", "helper", "do it", "RT-1")
    (raw,) = routing.take(project, "lead")
    assert json.loads(raw)["text"] == "do it"


def test_a_link_in_the_outbox_is_not_relayed_and_does_not_stick(project, victim):
    box = mailbox_dir(project, "lead", OUTBOX)
    box.mkdir(parents=True)
    (box / "2.json").symlink_to(victim)
    assert read(project, "lead", OUTBOX) == []
    assert not (box / "2.json").is_symlink(), "left, it wakes the loop for ever"
    assert victim.exists()


def test_control_a_message_is_relayed(project):
    from rite_ai.managers.mailbox import send

    send(project, "lead", OUTBOX, "hello")
    assert [m.text for m in take(project, "lead", OUTBOX)] == ["hello"]


@pytest.mark.parametrize("dirname", ["refine-asks", "checkins/queue"])
def test_the_other_request_directories_are_not_followed(project, tmp_path, dirname):
    from rite_ai.managers import checkins
    from rite_ai.refinement import protocol

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "1.json").write_text('{"ticket": "RT-1", "text": "x"}')
    link = manager_dir(project, "lead") / dirname
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere, target_is_directory=True)
    if dirname == "refine-asks":
        got = protocol.take(project, "lead")
        assert all("ticket" not in g for g in got), got
    else:
        assert checkins._queued(project, "lead") == []
    assert (elsewhere / "1.json").exists()


def test_a_planted_link_in_requests_is_set_aside_and_told(project, victim):
    """Left in place it was met, and woke the loop, every cycle; and the
    Manager was never told (SCRUM-59 final review)."""
    from rite_ai.managers import broker, supervise

    where = broker.requests_dir(project, "lead")
    where.mkdir(parents=True)
    (where / "1.json").symlink_to(victim)
    told: list[str] = []
    supervise._honour_worker_requests(
        project, "lead", lambda raw: (False, "the request is not JSON"), told.append
    )
    assert any("not JSON" in line for line in told), told
    assert broker.queued(project, "lead") is False
    assert victim.exists()
    (aside,) = where.glob("1.json.refused-*")
    assert aside.is_symlink(), "kept, for a person to look at"


def test_a_linked_engine_tmp_stops_the_launch(project, tmp_path):
    from rite_ai.managers.enclosure import (
        LinkedManagerPath,
        engine_tmp,
        refuse_linked_manager_paths,
    )

    target = tmp_path / "home-ish"
    target.mkdir()
    tmp = engine_tmp(project, "lead")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.symlink_to(target, target_is_directory=True)
    with pytest.raises(LinkedManagerPath, match="is a link"):
        refuse_linked_manager_paths(project, "lead")


def test_landlock_never_grants_a_linked_manager_path(project):
    from rite_ai.managers import landlock

    policy = landlock.compose_policy(project, "lead", project / "home")
    assert str(manager_dir(project, "lead")) in policy["no_follow"]
    assert str(landlock.engine_tmp(project, "lead")) in policy["no_follow"]


def test_a_module_that_is_a_link_is_not_gated(project, tmp_path):
    """`module_path_problem` is a string check; a Manager could make `svc` a
    link to another repository on the host (SCRUM-59 final review)."""
    from rite_ai.config.parse import module_dir_problem

    other = tmp_path / "other-repo"
    (other / ".git").mkdir(parents=True)
    (project / "svc").symlink_to(other, target_is_directory=True)
    assert "is a link" in module_dir_problem(project, "svc/")
    (project / "svc").unlink()
    (project / "svc").mkdir()
    assert module_dir_problem(project, "svc/") == ""


def test_a_person_started_or_destroyed_worker_forgets_its_manager(project):
    from rite_ai.managers import lifecycle, recovery

    lifecycle.record_owner(project, "alpha", "lead")
    recovery._restage(project, "alpha")
    assert lifecycle._owner_of(project, "alpha") == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")
def test_the_profile_refuses_making_a_link_in_its_own_directory(project):
    """Defence in depth on macOS: the profile refuses creating a link in the
    Manager's own directory or outbox. (Not the whole defence: a directory
    renamed in can carry one, so rite also opens everything without
    following, as the tests above show.)"""
    import subprocess

    from rite_ai.managers.enclosure import _own_subdirs, compose
    from rite_ai.managers.mailbox import INBOX

    for box in (OUTBOX, INBOX):
        mailbox_dir(project, "lead", box).mkdir(parents=True, exist_ok=True)
    own = manager_dir(project, "lead")
    own.mkdir(parents=True, exist_ok=True)
    _own_subdirs(project, "lead")
    profile = project / "lead.sb"
    profile.write_text(compose(project, "lead"))
    out = mailbox_dir(project, "lead", OUTBOX)
    done = subprocess.run(
        [
            "sandbox-exec",
            "-f",
            str(profile),
            "/bin/sh",
            "-c",
            f"ln -s /etc/hosts '{own}/l'; echo own=$?; "
            f"ln -s /etc/hosts '{out}/l.json'; echo out=$?; "
            f"echo x > '{own}/f'; echo file=$?",
        ],
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    assert "own=0" not in done.stdout and "out=0" not in done.stdout, done.stdout
    assert "file=0" in done.stdout, done.stderr  # control
