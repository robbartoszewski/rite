"""What a Manager may DO: the allowlist, and what it must not silently drop.

**Robert's decision, 2026-09-24: the allowlist REPLACES
`--dangerously-skip-permissions` as the default**, with a condition —

> *"just make sure the allowlist is generous and covers everything a worker
> needs under normal circumstances."*

⚠ **The failure this file is really guarding is not a wrong command being
allowed. It is a needed command being missing**, because that does not fail
loudly: it produces a Manager stalled on an approval nobody is there to
give. So the tests below care most about coverage and about
`--permission-prompts none`, which is what turns a stall into a refusal.

⚠ **Nothing carries a permission decision into `-p`.** Resuming from a
terminal restores the mode a session was in, and that restoration excludes
`-p`. So a cycle launched without these arguments is a Manager that cannot
act AND still exits 0 — read by `ending` as a clean finish, and resumed.
That is why these tests care about EVERY cycle rather than the first.
"""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.managers.permissions import (
    BYPASS_FLAG,
    DEFAULT_ALLOW,
    GENEROUS_ALLOW,
    NOT_ALLOWED,
    OBSERVED_ALLOW,
    announcement,
    launch_arguments,
    settings_document,
    settings_path,
    write_settings,
)
from rite_ai.managers.supervise import launch_command

CORPUS = Path(__file__).parent / "data" / "observed_commands.json"


def _observed() -> dict[str, int]:
    return json.loads(CORPUS.read_text())["observed"]


class TestTheListCoversWhatWasObserved:
    """⚠ The condition, made mechanical. A guessed allowlist is the same
    instrument as a guessed threshold."""

    def test_every_observed_command_is_decided_one_way_or_the_other(self):
        """Allowed, or refused with a stated reason — never just absent.

        This is the test that stops a later edit from narrowing the list
        quietly. Removing an entry is fine; removing it without saying why
        in `NOT_ALLOWED` is not.
        """
        allow = {entry[len("Bash(") : -len(":*)")] for entry in DEFAULT_ALLOW}
        undecided = sorted(
            command
            for command in _observed()
            if command not in allow and command not in NOT_ALLOWED
        )
        assert undecided == [], (
            f"observed in real runs but neither allowed nor refused on "
            f"purpose: {undecided}. A command a Manager was measured using, "
            f"left out silently, is a stall waiting to happen."
        )

    def test_the_commands_the_acceptEdits_run_actually_died_on_are_allowed(self):
        """⚠ The v0.5.1 acceptance run, verbatim: *'Both `rite loop status`
        and a direct GitHub check (`gh issue list …`) are blocked pending
        approval.'* Three cycles, no ticket read, no artifact."""
        assert "Bash(rite:*)" in DEFAULT_ALLOW
        assert "Bash(gh:*)" in DEFAULT_ALLOW

    def test_the_busiest_observed_tools_are_allowed(self):
        for tool in ("git", "python3", "uv", "grep"):
            assert f"Bash({tool}:*)" in DEFAULT_ALLOW, tool

    def test_observed_and_generous_entries_are_kept_apart(self):
        """⚠ So nobody later reads the guesses as evidence."""
        assert set(OBSERVED_ALLOW) & set(GENEROUS_ALLOW) == set()
        assert set(DEFAULT_ALLOW) == set(OBSERVED_ALLOW) | set(GENEROUS_ALLOW)

    def test_a_shell_is_not_allowed_because_it_would_void_the_list(self):
        """`bash -c "curl …"` is one hop around every other row."""
        for shell in ("bash", "sh", "zsh"):
            assert shell in NOT_ALLOWED
            assert f"Bash({shell}:*)" not in DEFAULT_ALLOW

    def test_the_route_around_that_actually_happened_is_closed(self):
        """⚠ Told nothing about starting Workers, a Manager improvised a
        bare `claude` and they died on launch. `rite sandbox start` is the
        supported route, and it is allowed."""
        assert "Bash(claude:*)" not in DEFAULT_ALLOW
        assert "claude" in NOT_ALLOWED
        assert "Bash(rite:*)" in DEFAULT_ALLOW


class TestEveryCycleCarriesThePermissionDecision:
    def test_the_first_cycle_carries_it(self, tmp_path):
        arguments = launch_arguments(write_settings(tmp_path))
        assert "--settings" in launch_command("claude", "", "/p.txt", arguments)

    def test_a_RESUMED_cycle_carries_it_too(self, tmp_path):
        """⚠ The one that matters: `-p` does not restore a session's mode."""
        arguments = launch_arguments(write_settings(tmp_path))
        built = launch_command("claude", "abc-123", "/p.txt", arguments)
        assert "--settings" in built
        assert "--permission-prompts none" in built
        assert "--resume abc-123" in built

    def test_a_prompt_with_nobody_to_answer_it_is_DENIED_not_queued(self):
        """⚠ Defect class 15: a prompt is not an exception, it is the absence
        of an answer. Under `-p` the default prompt target is the SDK host,
        and a Manager in a tmux pane has none — so without this the run
        hangs instead of reporting."""
        assert "--permission-prompts none" in launch_arguments(Path("/s.json"))

    def test_the_bypass_flag_is_no_longer_passed(self, tmp_path):
        """The reversal itself. 0.5.1 passed this on every cycle."""
        arguments = launch_arguments(write_settings(tmp_path))
        assert BYPASS_FLAG not in arguments
        assert BYPASS_FLAG not in launch_command("claude", "", "/p.txt", arguments)

    def test_the_settings_path_is_quoted_for_the_shell(self, tmp_path):
        """This string is handed to `tmux new-session`, which runs it through
        `sh -c` — the same reason the resume id is checked."""
        spaced = tmp_path / "a dir"
        spaced.mkdir()
        assert "'" in launch_arguments(spaced / "permissions.json")


class TestTheSettingsFileTheEngineReads:
    def test_it_is_written_where_per_machine_state_lives(self, tmp_path):
        assert write_settings(tmp_path) == settings_path(tmp_path)
        assert settings_path(tmp_path).parent.name == "user"

    def test_it_is_valid_json_in_the_engines_own_shape(self, tmp_path):
        """⚠ Measured from `claude --help`: under `-p`, a settings file that
        fails validation is SILENTLY IGNORED. An ignored allowlist denies
        everything, so a malformed write is a stalled Manager with no
        message — which is why rite writes the whole document rather than
        merging into one."""
        written = json.loads(write_settings(tmp_path).read_text())
        assert written == settings_document()
        assert isinstance(written["permissions"]["allow"], list)
        assert all(isinstance(entry, str) for entry in written["permissions"]["allow"])

    def test_it_carries_nothing_but_permissions(self, tmp_path):
        """`--settings` loads ADDITIONAL settings over the user's own, so
        every key rite writes is one it takes away from them."""
        assert list(json.loads(write_settings(tmp_path).read_text())) == ["permissions"]

    def test_rite_rewrites_its_own_file_so_the_list_cannot_drift(self, tmp_path):
        """⚠ A write-once-if-absent file would pin every existing project to
        the list that shipped the day it was created."""
        path = write_settings(tmp_path)
        path.write_text('{"permissions": {"allow": ["Bash(echo:*)"]}}')
        assert json.loads(write_settings(tmp_path).read_text()) == settings_document()

    def test_it_does_not_touch_the_users_own_settings(self, tmp_path):
        """The only file a user edits is theirs, and rite never writes it."""
        theirs = tmp_path / ".claude" / "settings.json"
        theirs.parent.mkdir(parents=True)
        theirs.write_text('{"permissions": {"deny": ["Bash(git push:*)"]}}')
        write_settings(tmp_path)
        assert json.loads(theirs.read_text())["permissions"]["deny"]


class TestTheGrantIsAnnounced:
    def test_it_no_longer_claims_the_manager_will_not_ask(self):
        """⚠ That sentence was true of the bypass flag and is false now.
        Leaving it would be the doc-describes-reality defect C19 exists
        for."""
        assert "not ask" not in announcement("lead").lower()

    def test_it_says_a_refused_command_is_refused_rather_than_queued(self):
        assert "refused" in announcement("lead").lower()

    def test_it_says_where_that_power_still_reaches(self):
        """⚠ The allowlist is a speed bump, not a sandbox. A Manager is
        still unsandboxed — unlike a Worker, which yoloAI bounds."""
        said = announcement("lead").lower()
        assert "unsandboxed" in said
        assert "machine" in said

    def test_it_names_the_manager(self):
        assert "planner" in announcement("planner")


class TestThereIsNoInertConfigLeftBehind:
    def test_no_per_manager_permission_file_is_read(self):
        """⚠ The per-Manager opt-in was REMOVED, not left unreachable."""
        import rite_ai.managers.permissions as perms

        assert not hasattr(perms, "permission_mode")
        assert not hasattr(perms, "mode_path")
        assert not hasattr(perms, "ACCEPT_EDITS")

    def test_a_stale_opt_in_file_changes_nothing(self, tmp_path):
        (tmp_path / ".rite" / "user").mkdir(parents=True)
        (tmp_path / ".rite" / "user" / "lead.yaml").write_text(
            "permission_mode: acceptEdits\n"
        )
        arguments = launch_arguments(write_settings(tmp_path))
        assert "--settings" in launch_command("claude", "", "/p.txt", arguments)
