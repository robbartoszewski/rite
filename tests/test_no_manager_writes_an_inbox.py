"""No Manager writes a Manager's inbox — structurally, on the writer (MM-2).

⚠ **An inbox write IS an instruction.** The cycle's delivery note tells a
Manager that a message with no bracketed line "was sent from this machine by
the Owner, and is an instruction". Measured on `main` before this: from inside
Manager `helper`'s real profile, a plain file write into `lead`'s inbox, and
`rite message lead` with or without `RITE_MANAGER`, all landed — and `lead`'s
own profile let it write its own inbox, promoting its own text the same way.

These RUN `sandbox-exec`, because a profile that parses is not a profile that
denies. The content of a message is never consulted: the writer is fenced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rite_ai.managers.enclosure import write_profile
from rite_ai.managers.mailbox import (
    INBOX,
    OUTBOX,
    adopt_legacy,
    legacy_mail_root,
    mailbox_dir,
    read,
    send,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="seatbelt is macOS only"
)


def _under(profile: Path, command: str, cwd: Path) -> int:
    import subprocess

    return subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    ).returncode


@pytest.fixture
def two(tmp_path):
    root = tmp_path.resolve()
    (root / ".rite").mkdir()
    for m in ("lead", "helper"):
        mailbox_dir(root, m, INBOX).mkdir(parents=True)
        # A pre-0.6.0 project: the old in-tree boxes exist and are still read.
        (legacy_mail_root(root, m) / INBOX).mkdir(parents=True)
    return root, write_profile(root, "lead"), write_profile(root, "helper")


def _write(box: Path, name: str) -> str:
    return f"printf '{{\"text\": \"forged\"}}' > '{box / name}'"


def test_a_manager_cannot_write_another_managers_inbox(two):
    root, _lead, helper = two
    inbox = mailbox_dir(root, "lead", INBOX)
    assert _under(helper, _write(inbox, "1_0000001_000000000001.json"), root) != 0
    assert read(root, "lead", INBOX) == []


def test_a_manager_cannot_write_its_own_inbox(two):
    """Its own text would arrive next cycle as the Owner's instruction."""
    root, lead, _helper = two
    inbox = mailbox_dir(root, "lead", INBOX)
    assert _under(lead, _write(inbox, "1_0000001_000000000002.json"), root) != 0
    assert read(root, "lead", INBOX) == []


def test_nor_by_renaming_or_linking_into_it(two):
    root, lead, _helper = two
    own = root / ".rite" / "managers" / "lead"
    inbox = mailbox_dir(root, "lead", INBOX)
    assert (
        _under(
            lead, f"echo x > '{own}/staged' && mv '{own}/staged' '{inbox}/y.json'", root
        )
        != 0
    )
    assert _under(lead, f"ln -s /etc/hosts '{inbox}/z.json'", root) != 0


def test_a_manager_cannot_write_another_managers_directory_at_all(two):
    """§5.4.8 P1, for the state that carries authority and everything beside it."""
    root, _lead, helper = two
    assert _under(helper, f"echo x > '{root}/.rite/managers/lead/anything'", root) != 0


def test_the_control_a_manager_still_writes_its_own_state(two):
    """A profile that refused everything would pass every test above."""
    root, _lead, helper = two
    out = mailbox_dir(root, "helper", OUTBOX)
    # Written without `mkdir -p`: rite creates the outbox before launch, and
    # seatbelt answers mkdir on an ungranted parent with EPERM, not EEXIST.
    assert _under(helper, f"echo x > '{out}/t.json'", root) == 0
    assert _under(helper, f"echo x > '{root}/file-in-the-project'", root) == 0


def test_the_inbox_is_outside_the_project_it_would_be_granted_by(two):
    """The fence is by construction: no project grant covers an inbox."""
    root, _lead, _helper = two
    assert not mailbox_dir(root, "lead", INBOX).resolve().is_relative_to(root)


def test_writing_the_old_in_tree_box_after_the_move_delivers_nothing(two):
    """The old box is no longer fenced, because it is no longer READ: moved
    once at start, then only reported. So a Manager that writes it — which
    the project grant allows — is heard by nobody."""
    root, lead, _helper = two
    adopt_legacy(root, "lead")
    old = legacy_mail_root(root, "lead") / INBOX
    old.mkdir(parents=True, exist_ok=True)
    assert _under(lead, _write(old, "1_0000001_000000000003.json"), root) == 0
    assert read(root, "lead", INBOX) == []
    assert adopt_legacy(root, "lead").after_marker


def test_outside_any_profile_the_person_still_can(two):
    root, _lead, _helper = two
    send(root, "lead", INBOX, "from the person")
    assert [m.text for m in read(root, "lead", INBOX)] == ["from the person"]


def test_rite_message_refuses_when_run_as_a_manager(tmp_path, monkeypatch):
    """The helpful layer: the Manager is told what to do instead."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli
    from rite_ai.managers import MANAGER_ENV

    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  managers:\n    - lead\n"
        "    - helper\n  manager_roles:\n    - name: lead\n      engine: claude\n"
        "      preset: lead\n    - name: helper\n      engine: claude\n"
        "      preset: executor\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    monkeypatch.setenv(MANAGER_ENV, "helper")
    result = CliRunner().invoke(cli, ["message", "lead", "do the thing"])
    assert result.exit_code == 1
    assert "a Manager does not write a Manager's inbox" in result.output
    assert read(tmp_path, "lead", INBOX) == []
