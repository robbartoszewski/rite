"""No Manager reads another Manager's mail, or its instructions (DF3).

🔴 **MM-2 fenced WRITES and nothing tested READS, so a false claim survived.**
The mailbox moved out of the project to `~/.rite/managers/`, and `~/.rite` is
granted READABLE, as a tree, to every Manager. Measured on the v0.6.0 Linux
acceptance run: from inside `small`'s boundary, listing `lead`'s inbox
succeeded while a write was refused. SPEC and the `mail_root` docstring said
no profile grants an inbox; that was true for writes only. What was readable:
every Manager's inbox AND outbox, for every project on the machine — for a
firm, one client's instructions to a Manager working for another.

These RUN `sandbox-exec` against the profile rite writes, with a HOME laid
out as a real one is: rite's home at `<home>/.rite`, where the profile names
it, and the data directory where the mail now lives. Pointing rite's home
somewhere the profile never names would pass on the defect — the test would
measure an ungranted path, not the layout. On the pre-DF3 code, where
`~/.rite` was granted readable and held the mail, five of these fail. Each
refusal has a control showing the same kind of read succeeds where it
should.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai.managers.enclosure import write_profile
from rite_ai.managers.mailbox import INBOX, OUTBOX, mail_root, mailbox_dir, send

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="seatbelt is macOS only"
)


def _under(profile: Path, command: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """Two projects on one machine, each with a `lead`; project one also has
    a `helper`. Mail is where production puts it, not an override."""
    import rite_ai.managers.github_access as ga

    home = (tmp_path / "home").resolve()
    (home / ".rite").mkdir(parents=True)
    monkeypatch.setenv("RITE_HOME_DIR", str(home / ".rite"))
    monkeypatch.delenv("RITE_MAIL_DIR", raising=False)
    isolated = ga._credential_root
    monkeypatch.setattr(
        ga, "_credential_root", lambda h=None: isolated(h if h is not None else home)
    )
    one = (tmp_path / "one").resolve()
    two = (tmp_path / "two").resolve()
    for root, names in ((one, ("lead", "helper")), (two, ("lead",))):
        (root / ".rite").mkdir(parents=True)
        for name in names:
            mailbox_dir(root, name, OUTBOX).mkdir(parents=True)
            send(root, name, INBOX, f"secret for {name} in {root.name}")
            (mailbox_dir(root, name, OUTBOX) / "1_1_1.json").write_text(
                '{"text": "a reply"}'
            )
    assert not mail_root(one, "lead").is_relative_to(home / ".rite"), (
        "the mail is under rite's home again, which every Manager may read"
    )
    return {
        "home": home,
        "one": one,
        "two": two,
        "lead": write_profile(one, "lead", home),
        "helper": write_profile(one, "helper", home),
    }


def _message(root: Path, name: str, box: str) -> Path:
    return next(mailbox_dir(root, name, box).glob("*.json"))


def test_a_manager_cannot_list_or_read_a_siblings_inbox(machine):
    one, helper = machine["one"], machine["helper"]
    assert _under(helper, f"ls '{mailbox_dir(one, 'lead', INBOX)}'", one).returncode
    assert _under(helper, f"cat '{_message(one, 'lead', INBOX)}'", one).returncode


def test_nor_a_siblings_outbox(machine):
    one, helper = machine["one"], machine["helper"]
    assert _under(helper, f"cat '{_message(one, 'lead', OUTBOX)}'", one).returncode


def test_nor_another_projects_mail(machine):
    """The case that makes this a boundary between clients."""
    one, two, lead = machine["one"], machine["two"], machine["lead"]
    for box in (INBOX, OUTBOX):
        assert _under(lead, f"cat '{_message(two, 'lead', box)}'", one).returncode
    assert _under(lead, f"ls '{mail_root(two, 'lead').parent.parent}'", one).returncode


def test_nor_the_whole_mail_directory(machine):
    one, lead = machine["one"], machine["lead"]
    above = mail_root(one, "lead").parent.parent.parent
    assert _under(lead, f"ls '{above}'", one).returncode


def test_the_control_a_manager_reads_its_own_mail_and_writes_its_outbox(machine):
    """A profile that refused everything would pass every test above."""
    one, lead = machine["one"], machine["lead"]
    done = _under(lead, f"cat '{_message(one, 'lead', INBOX)}'", one)
    assert done.returncode == 0 and "secret for lead in one" in done.stdout
    assert _under(lead, f"ls '{mailbox_dir(one, 'lead', OUTBOX)}'", one).returncode == 0
    out = mailbox_dir(one, "lead", OUTBOX)
    assert _under(lead, f"echo x > '{out}/2_2_2.json'", one).returncode == 0
    # And a file in the project, under the same profile.
    (one / "readme").write_text("x")
    assert _under(lead, f"cat '{one / 'readme'}'", one).returncode == 0


def test_rite_home_is_not_readable_at_all(machine):
    """🔴 `dispatch/projects.yaml` lists every registered project's path —
    for a firm, its client list — and `credentials.json` every credential's
    name. `~/.rite` was granted readable as a tree; nothing inside a boundary
    needs it. The mail tests above hold with or without this, because the
    fixture asserts the mail is not under `~/.rite`; this one is about the
    rest of it."""
    one, lead, home = machine["one"], machine["lead"], machine["home"]
    dispatch = home / ".rite" / "dispatch"
    dispatch.mkdir()
    (dispatch / "projects.yaml").write_text("projects:\n  client-a: {path: /x}\n")
    (home / ".rite" / "credentials.json").write_text('{"client-a/jira": 1}')
    for target in (dispatch / "projects.yaml", home / ".rite" / "credentials.json"):
        done = _under(lead, f"cat '{target}'", one)
        assert done.returncode and "client-a" not in done.stdout, target
    assert _under(lead, f"ls '{home / '.rite'}'", one).returncode
    profile = machine["lead"].read_text()
    assert f'(deny file-read* file-write* (subpath "{home / ".rite"}"))' in profile


def test_a_manager_cannot_read_a_siblings_in_tree_state(machine):
    """The macOS half of the same family: `.rite/managers/<other>/` holds
    the other Manager's whole instruction (`prompt.txt`, mail included) and
    its routes. The Landlock policy never granted it."""
    one, helper = machine["one"], machine["helper"]
    theirs = one / ".rite" / "managers" / "lead"
    (theirs / "routes").mkdir(parents=True, exist_ok=True)
    (theirs / "prompt.txt").write_text("the lead's instruction")
    (theirs / "routes" / "r.json").write_text("{}")
    assert _under(helper, f"cat '{theirs / 'prompt.txt'}'", one).returncode
    assert _under(helper, f"cat '{theirs / 'routes' / 'r.json'}'", one).returncode
    assert _under(helper, f"ls '{theirs}'", one).returncode
    own = one / ".rite" / "managers" / "helper"
    own.mkdir(parents=True, exist_ok=True)
    (own / "prompt.txt").write_text("mine")
    assert _under(helper, f"cat '{own / 'prompt.txt'}'", one).returncode == 0, (
        "control: a Manager must still read its own directory"
    )


class TestABoxUnderRiteHomeIsMovedOut:
    """Development builds of 0.6.0 kept mail under `~/.rite`, readable by
    every Manager. The first start moves it out, under the run lock, and says
    what it could not move — a file left there is still exposed."""

    @pytest.fixture
    def old(self, tmp_path):
        from rite_ai.managers.mailbox import ADOPTED_MARKER, _rite_home_mail_root

        root = tmp_path / "proj"
        (root / ".rite").mkdir(parents=True)
        box = _rite_home_mail_root(root, "lead")
        for relative, text in (
            ("in/1_1_1.json", '{"text": "waiting"}'),
            ("out/1_1_2.json", '{"text": "said"}'),
            ("out.read/slack.json", '{"last": "1_1_2.json"}'),
            (ADOPTED_MARKER, '{"moved": 0}'),
        ):
            (box / relative).parent.mkdir(parents=True, exist_ok=True)
            (box / relative).write_text(text)
        (box.parent.parent / "project").write_text(str(root))
        return root, box

    def test_everything_moves_and_nothing_is_left_readable(self, old):
        from rite_ai.managers.mailbox import (
            ADOPTED_MARKER,
            adopt_legacy,
            adoption_notes,
            read,
            unread,
        )

        root, box = old
        adoption = adopt_legacy(root, "lead")
        assert adoption.from_rite_home == 4 and not adoption.kept_in_rite_home
        assert not box.parent.parent.exists(), "the old checkout directory remains"
        assert [m.text for m in read(root, "lead", INBOX)] == ["waiting"]
        assert unread(root, "lead", OUTBOX, "slack") == [], "the reader lost its place"
        assert (mail_root(root, "lead") / ADOPTED_MARKER).exists()
        assert "out of ~/.rite" in " ".join(adoption_notes(adoption, "lead"))

    def test_a_file_that_cannot_move_is_said_to_be_still_exposed(self, old):
        from rite_ai.managers.mailbox import adopt_legacy, adoption_notes

        root, box = old
        clash = mailbox_dir(root, "lead", INBOX) / "1_1_1.json"
        clash.parent.mkdir(parents=True, exist_ok=True)
        clash.write_text('{"text": "different"}')
        adoption = adopt_legacy(root, "lead")
        assert adoption.kept_in_rite_home == (box / "in" / "1_1_1.json",)
        said = " ".join(adoption_notes(adoption, "lead"))
        assert "still READABLE by every Manager" in said

    def test_another_projects_box_left_there_is_said(self, old, tmp_path):
        from rite_ai.managers.mailbox import (
            _rite_home_mail_root,
            adopt_legacy,
            still_under_rite_home,
        )

        root, _box = old
        other = tmp_path / "client-b"
        (other / ".rite").mkdir(parents=True)
        theirs = _rite_home_mail_root(other, "lead") / "in"
        theirs.mkdir(parents=True)
        (theirs / "1_1_1.json").write_text('{"text": "b"}')
        (theirs.parent.parent.parent / "project").write_text(str(other))
        adopt_legacy(root, "lead")
        said = " ".join(still_under_rite_home())
        assert "EVERY Manager" in said and str(other) in said
        assert str(root) not in said, "this project's own mail was not moved"
