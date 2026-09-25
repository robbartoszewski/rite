"""The sandbox profile a Manager runs inside (B9, piece 1).

⚠ **The tests that matter here RUN `sandbox-exec`**, because a profile that
parses is not a profile that denies anything. Every grant in `enclosure.py`
was added in response to a measured failure, and three of them were added
after a check that was supposed to fail passed instead.

⚠ **Read `docs/design/spikes/B9-manager-sandboxing.md` first.** A sandboxed
Manager cannot start a sandboxed Worker — the kernel refuses — which is why
`~/.yoloai` is denied here on purpose rather than by omission.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai.managers.enclosure import (
    compose,
    engine_tmp,
    limitations,
    profile_path,
    write_profile,
)

on_macos = pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS only")


def _under(profile: Path, command: str) -> int:
    done = subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return done.returncode


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".rite").mkdir()
    (tmp_path / "file.txt").write_text("project content\n")
    return tmp_path


class TestTheProfileIsDenyDefault:
    def test_nothing_is_permitted_unless_named(self, project):
        assert "(deny default)" in compose(project, "lead")

    def test_it_points_at_what_it_does_and_does_not_buy(self, project):
        """A boundary sold as more than it is would be worse than none."""
        assert "B9-manager-sandboxing" in compose(project, "lead")

    def test_it_says_the_network_is_not_confined(self, project):
        """⚠ `(allow network*)` is in the shipped WORKER profile too.
        Seatbelt has no network isolation (D-30) — a line in the file rather
        than a limitation rite might mitigate."""
        text = compose(project, "lead")
        assert "(allow network*)" in text
        assert any("network is NOT confined" in line for line in limitations())


class TestAHostilePathCannotBecomePolicy:
    """This file is parsed by something that acts on it, so the rule is the
    one `session_id_problem` uses: refuse, do not escape."""

    @pytest.mark.parametrize("bad", ['pro"ject', "pro\\ject"])
    def test_a_quote_or_backslash_in_the_root_is_refused(self, tmp_path, bad):
        root = tmp_path / bad
        root.mkdir()
        with pytest.raises(ValueError) as raised:
            compose(root, "lead")
        assert "read as policy" in str(raised.value)

    def test_a_manager_name_that_is_not_a_name_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            profile_path(tmp_path, "../escape")


class TestTheProfileIsRewrittenEveryRun:
    def test_an_edit_does_not_survive(self, project):
        """As `permissions.write_settings`: a write-once file pins a project
        to whatever shipped the day it was created."""
        path = write_profile(project, "lead")
        path.write_text("(version 1)\n(allow default)\n")
        assert "(deny default)" in write_profile(project, "lead").read_text()

    def test_the_engine_gets_its_own_tmp(self, project):
        """⚠ But NOT its own HOME: Claude's login lives there, and
        redirecting it answers "Not logged in". See
        `ENGINE_HOME_IS_THE_OPERATORS`."""
        write_profile(project, "lead")
        assert engine_tmp(project, "lead").is_dir()


@on_macos
class TestItActuallyPermitsWhatAManagerNeeds:
    """Each of these was a real failure before the grant that fixes it."""

    def test_the_project_tree_is_readable_and_writable(self, project):
        profile = write_profile(project, "lead")
        assert _under(profile, f"cat {project}/file.txt") == 0
        assert _under(profile, f"touch {project}/new.txt") == 0

    def test_rite_itself_runs(self, project):
        """⚠ Granting only `uv/tools` gave `Abort trap: 6` and
        `dyld: Library not loaded ... (file system sandbox blocked open())` —
        a crash rather than a refusal, which reads as a broken rite."""
        if not _which("rite"):
            pytest.skip("rite is not on PATH in this environment")
        profile = write_profile(project, "lead")
        assert _under(profile, "rite --version >/dev/null 2>&1") == 0

    def test_a_shell_can_redirect_to_dev_null(self, project):
        """Without a writable `/dev`, every `cmd >/dev/null` fails — most of
        what a shell does, and none of what a boundary is for."""
        profile = write_profile(project, "lead")
        assert _under(profile, "echo hello >/dev/null") == 0


@on_macos
class TestItActuallyDeniesWhatItShould:
    def test_another_project_on_this_machine_is_unreachable(self, project, tmp_path):
        """⚠ **This check passed when it should have failed**, because an
        earlier draft granted the whole per-user `$TMPDIR` to give the engine
        somewhere to write — and every other process's scratch lives there.
        The engine got its own instead."""
        other = tmp_path.parent / (tmp_path.name + "-other")
        other.mkdir()
        (other / "secret.txt").write_text("another project's file\n")
        profile = write_profile(project, "lead")
        assert _under(profile, f"cat {other}/secret.txt") != 0

    @pytest.mark.parametrize("private", ["Documents", ".ssh"])
    def test_the_operators_home_outside_the_named_paths_is_unreachable(
        self, project, private
    ):
        profile = write_profile(project, "lead")
        assert _under(profile, f"ls {Path.home() / private}") != 0

    def test_git_identity_is_readable_and_NOT_writable(self, project):
        """An agent that can rewrite git config can change what every later
        commit claims."""
        profile = write_profile(project, "lead")
        assert _under(profile, f"touch {Path.home() / '.gitconfig'}") != 0

    def test_the_yoloAI_library_is_unreachable_ON_PURPOSE(self, project):
        """⚠ Not an omission. A Manager cannot create a sandbox from inside
        one (B9), so reaching `yoloai` would fail at the one job a Manager
        exists for, after appearing to start correctly."""
        profile = write_profile(project, "lead")
        assert _under(profile, f"ls {Path.home() / '.yoloai' / 'library'}") != 0


class TestTheLimitationsAreSaidOutLoud:
    def test_there_are_some(self):
        assert limitations()

    def test_they_name_the_three_things_this_does_not_do(self):
        said = " ".join(limitations()).lower()
        assert "network" in said
        assert "rite" in said
        assert "ticket text" in said


def _which(binary: str) -> str | None:
    import shutil

    return shutil.which(binary, path=os.environ.get("PATH", ""))
