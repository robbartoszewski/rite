"""No Manager can read, list or plant the key that signs refinement records.

The key is what stops a Manager writing its own "agreed definition of done"
(the note's part 3.6: a terminal confirmation was measured NOT to be a
barrier). So this RUNS `sandbox-exec` against the profile rite writes, with a
HOME laid out as a real one is, the way `test_no_manager_reads_another_
managers_mail.py` does: the key where production puts it (beside the
credential root, no override), and a control for each refusal showing the same
kind of access succeeds where it should.

Measured by hand first, 2026-09-28 on macOS, against the real HOME: a file
beside the key's location could not be read, listed or written from inside a
Manager's profile, while the same file read outside it and the project's own
file read inside it. Linux (Landlock) and yoloAI Workers are not covered here;
they are still owed (TR0).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai.managers.enclosure import write_profile
from rite_ai.refinement import key as refinement_key

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
    import rite_ai.managers.github_access as ga

    home = (tmp_path / "home").resolve()
    (home / ".rite").mkdir(parents=True)
    monkeypatch.setenv("RITE_HOME_DIR", str(home / ".rite"))
    monkeypatch.delenv("RITE_MAIL_DIR", raising=False)
    monkeypatch.delenv(refinement_key.KEY_DIR_ENV, raising=False)
    isolated = ga._credential_root
    monkeypatch.setattr(
        ga, "_credential_root", lambda h=None: isolated(h if h is not None else home)
    )
    project = (tmp_path / "project").resolve()
    (project / ".rite").mkdir(parents=True)
    (project / "file.txt").write_text("project content\n")
    refinement_key.ensure()
    key = refinement_key.key_path()
    assert key.is_relative_to(home), "the key is not where production puts it"
    return {
        "project": project,
        "key": key,
        "profile": write_profile(project, "lead", home),
    }


def test_the_key_is_readable_outside_the_boundary(machine):
    """The control: the file exists and is readable, so a refusal inside is the
    profile's doing."""
    assert machine["key"].read_bytes()


def test_the_project_is_readable_inside_the_boundary(machine):
    done = _under(machine["profile"], "cat file.txt", machine["project"])
    assert done.returncode == 0, done.stderr


def test_a_manager_cannot_read_the_key(machine):
    done = _under(machine["profile"], f"cat '{machine['key']}'", machine["project"])
    assert done.returncode != 0
    assert "not permitted" in done.stderr.lower()


def test_a_manager_cannot_list_where_the_key_is(machine):
    done = _under(
        machine["profile"], f"ls '{machine['key'].parent}'", machine["project"]
    )
    assert done.returncode != 0


def test_a_manager_cannot_plant_a_key(machine):
    """A Manager able to write the key file could sign with a key it chose."""
    planted = machine["key"].parent / "planted"
    done = _under(machine["profile"], f"echo x > '{planted}'", machine["project"])
    assert done.returncode != 0
    assert not planted.exists()
