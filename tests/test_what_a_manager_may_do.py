"""The permission flag rite passes, every cycle, for every Manager.

**Robert's decision, reversing `acceptEdits`: always
`--dangerously-skip-permissions`.** The first decision was made on a bare
`claude -p` probe, where `acceptEdits` wrote a file with the exact contents
asked for and ran a shell command. The first real run through `rite start`
measured what the probe had not — a Manager that could reach neither `rite`
nor `gh`, three cycles, no ticket ever read, no artifact. The decision
changed because the measurement did.

⚠ **Nothing carries a permission mode into `-p`.** Resuming from a terminal
restores the mode a session was in, and that restoration excludes `-p`. So
a cycle launched without the flag is a Manager that silently cannot act AND
still exits 0 — read by `ending` as a clean finish, and resumed. That is
why these tests care about EVERY cycle rather than the first.
"""

from __future__ import annotations

from rite_ai.managers.permissions import PERMISSION_FLAG, announcement
from rite_ai.managers.supervise import launch_command


class TestTheFlagIsOnEveryCycle:
    def test_the_first_cycle_carries_it(self):
        assert PERMISSION_FLAG in launch_command(
            "claude", "", "/p.txt", PERMISSION_FLAG
        )

    def test_a_RESUMED_cycle_carries_it_too(self):
        """⚠ The one that matters: `-p` does not restore a session's mode."""
        built = launch_command("claude", "abc-123", "/p.txt", PERMISSION_FLAG)
        assert PERMISSION_FLAG in built
        assert "--resume abc-123" in built

    def test_it_is_claude_codes_own_flag_not_a_mode_value(self):
        """`--permission-mode dangerously-skip-permissions` would be a
        different and wrong thing."""
        assert PERMISSION_FLAG == "--dangerously-skip-permissions"
        assert "--permission-mode" not in PERMISSION_FLAG


class TestTheGrantIsAnnounced:
    def test_it_names_the_flag(self):
        assert PERMISSION_FLAG in announcement("lead")

    def test_it_says_the_manager_will_not_ask(self):
        """One line a user can read and know what they have agreed to."""
        said = announcement("lead").lower()
        assert "not ask" in said

    def test_it_says_where_that_power_reaches(self):
        """⚠ A Manager is unsandboxed — unlike a Worker, which yoloAI
        bounds. The announcement is the only thing standing between a user
        and a surprise, so it must say so rather than name a flag."""
        said = announcement("lead").lower()
        assert "unsandboxed" in said
        assert "machine" in said

    def test_it_names_the_manager(self):
        assert "planner" in announcement("planner")


class TestThereIsNoInertConfigLeftBehind:
    def test_no_per_manager_permission_file_is_read(self, tmp_path):
        """⚠ The per-Manager opt-in was REMOVED, not left unreachable. With
        one level, a key a user could set that changed nothing would read
        like a control and be none — which this codebase has enough of."""
        import rite_ai.managers.permissions as perms

        assert not hasattr(perms, "permission_mode")
        assert not hasattr(perms, "mode_path")
        assert not hasattr(perms, "ACCEPT_EDITS")

    def test_a_stale_opt_in_file_changes_nothing(self, tmp_path):
        """Somebody who wrote the old file gets the same launch as somebody
        who did not — the flag does not depend on it."""
        (tmp_path / ".rite" / "user").mkdir(parents=True)
        (tmp_path / ".rite" / "user" / "lead.yaml").write_text(
            "permission_mode: acceptEdits\n"
        )
        assert PERMISSION_FLAG in launch_command(
            "claude", "", "/p.txt", PERMISSION_FLAG
        )
