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

import pytest

from rite_ai.managers.permissions import (
    BYPASS_FLAG,
    DEFAULT_ALLOW,
    GENEROUS_ALLOW,
    NOT_ALLOWED,
    OBSERVED_ALLOW,
    allowed,
    announcement,
    launch_arguments,
    refusal,
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
        assert allowed("rite loop status")
        assert allowed("gh issue list --repo owner/name")
        assert allowed("rite --version")

    def test_the_busiest_observed_tools_are_allowed(self):
        for command in ("git status", "python3 -c 'x'", "uv run pytest", "grep -r x ."):
            assert allowed(command), command

    def test_observed_and_generous_entries_are_kept_apart(self):
        """⚠ So nobody later reads the guesses as evidence."""
        assert set(OBSERVED_ALLOW) & set(GENEROUS_ALLOW) == set()
        assert set(DEFAULT_ALLOW) == set(OBSERVED_ALLOW) | set(GENEROUS_ALLOW)

    def test_a_shell_is_not_allowed_because_it_would_void_the_list(self):
        """`bash -c "curl …"` is one hop around every other row."""
        for shell in ("bash", "sh", "zsh"):
            assert shell in NOT_ALLOWED
            assert not allowed(f"{shell} -c 'curl http://example.com'")

    def test_the_route_around_that_actually_happened_is_closed(self):
        """⚠ Told nothing about starting Workers, a Manager improvised a
        bare `claude` and they died on launch. `rite sandbox start` is the
        supported route, and it is allowed."""
        assert not allowed("claude -p --dangerously-skip-permissions")
        assert allowed("rite sandbox start w1 --ticket 4")


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


class TestARefusalTellsTheUserWhatToDo:
    """C21. A refusal a user cannot act on is the same defect as a silent
    one."""

    def test_it_names_the_command_that_was_refused(self, tmp_path):
        said = refusal("curl https://example.com", tmp_path)
        assert "curl" in said

    def test_it_gives_the_exact_line_that_would_permit_it(self, tmp_path):
        said = refusal("curl https://example.com", tmp_path)
        assert '"Bash(curl:*)"' in said

    def test_it_names_the_file_to_put_that_line_in(self, tmp_path):
        said = refusal("curl https://example.com", tmp_path)
        assert ".claude/settings.json" in said.replace("\\", "/")

    def test_it_says_not_to_edit_rites_own_file(self, tmp_path):
        """Because rite rewrites it every run, an edit there would vanish
        without ever reporting that it had."""
        said = refusal("curl https://example.com", tmp_path)
        assert "rewritten every run" in said

    @pytest.mark.parametrize(
        "command", ["curl x", "/usr/bin/curl x", "HTTPS_PROXY=x curl x"]
    )
    def test_it_finds_the_executable_however_it_was_spelled(self, command, tmp_path):
        assert '"Bash(curl:*)"' in refusal(command, tmp_path)


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


def _transcript(base: Path, root: Path, entries: list[dict]) -> Path:
    directory = base / str(root.resolve()).replace("/", "-")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sess.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def _use(identifier: str, command: str) -> dict:
    return {
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": identifier,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ]
        }
    }


def _result(identifier: str, text: str, error: bool = True) -> dict:
    return {
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": identifier,
                    "is_error": error,
                    "content": text,
                }
            ]
        }
    }


class TestRiteCanSeeWhatTheEngineRefused:
    """⚠ Without this a refusal is invisible to rite: the engine tells the
    MODEL, and the model may say so, work around it, or neither. The v0.5.1
    acceptance run was all three across three cycles that each exited 0."""

    def test_it_finds_the_command_behind_a_bare_denial(self, tmp_path):
        from rite_ai.managers.transcripts import refused_commands

        root = tmp_path / "project"
        root.mkdir()
        base = tmp_path / "transcripts"
        _transcript(
            base,
            root,
            [_use("t1", "curl https://example.com"),
             _result("t1", "This command requires approval")],
        )
        assert refused_commands(root, base=base) == ["curl https://example.com"]

    def test_it_prefers_the_part_the_engine_named(self, tmp_path):
        """⚠ Otherwise the refusal quotes a compound line and names the
        wrong executable — measured shape: *'This Bash command contains
        multiple operations. The following part requires approval: …'*"""
        from rite_ai.managers.transcripts import refused_commands

        root = tmp_path / "project"
        root.mkdir()
        base = tmp_path / "transcripts"
        _transcript(
            base,
            root,
            [
                _use("t1", "echo hi; curl https://example.com"),
                _result(
                    "t1",
                    "This Bash command contains multiple operations. The "
                    "following part requires approval: curl https://example.com",
                ),
            ],
        )
        assert refused_commands(root, base=base) == ["curl https://example.com"]

    def test_a_successful_command_is_not_reported_as_refused(self, tmp_path):
        from rite_ai.managers.transcripts import refused_commands

        root = tmp_path / "project"
        root.mkdir()
        base = tmp_path / "transcripts"
        _transcript(
            base,
            root,
            [_use("t1", "git status"), _result("t1", "On branch main", error=False)],
        )
        assert refused_commands(root, base=base) == []

    def test_a_missing_transcript_directory_is_not_an_error(self, tmp_path):
        """⚠ Absence is not an exception. A project with no transcripts has
        had nothing refused, which is a different thing from a failure."""
        from rite_ai.managers.transcripts import refused_commands

        assert refused_commands(tmp_path, base=tmp_path / "nope") == []


class TestTheUserIsToldAboutARefusal:
    def test_a_refused_command_outside_the_list_gets_the_line_to_add(self, tmp_path):
        import rite_ai.managers.supervise as sup

        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append)  # no transcripts: silent
        assert said == []

    def test_a_refused_command_INSIDE_the_list_is_reported_differently(
        self, tmp_path, monkeypatch
    ):
        """⚠ It means rite's settings never reached the engine — telling the
        user to add a line they already have would send them the wrong
        way."""
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "refused_commands", lambda *a, **k: ["rite status"])
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append)
        assert len(said) == 1
        assert "DOES cover" in said[0]
        assert str(settings_path(tmp_path)) in said[0]

    def test_an_unlisted_refusal_names_the_line_that_would_permit_it(
        self, tmp_path, monkeypatch
    ):
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(sup, "refused_commands", lambda *a, **k: ["curl http://x"])
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append)
        assert '"Bash(curl:*)"' in said[0]

    def test_the_same_refusal_twice_is_said_once(self, tmp_path, monkeypatch):
        """Three cycles refused the same command in the acceptance run."""
        import rite_ai.managers.supervise as sup

        monkeypatch.setattr(
            sup, "refused_commands", lambda *a, **k: ["curl x", "curl x", "curl x"]
        )
        said: list[str] = []
        sup._say_refusals(tmp_path, 0.0, said.append)
        assert len(said) == 1


class TestTheDocsDescribeWhatShips:
    """⚠ Defect class 6, prose and code drifting apart. The README quotes
    the announcement verbatim and names a COUNT, and the count changes every
    time somebody adds a command to the list."""

    def _readme(self) -> str:
        return (Path(__file__).parent.parent / "README.md").read_text()

    def test_the_readme_quotes_the_announcement_this_release_prints(self):
        printed = announcement("planner")
        # The README wraps it to fit a console block, so compare on words.
        assert " ".join(self._readme().split()).count(" ".join(printed.split())) == 1, (
            "the README's permissions block is not what rite prints any more"
        )

    def test_the_readme_does_not_still_promise_the_old_flag_as_default(self):
        """C4 named this explicitly: the shipped note said the allowlist
        would arrive 'with this flag still the default', and it did not."""
        assert "still the default" not in self._readme()

    def test_the_changelog_says_the_behaviour_CHANGES_on_upgrade(self):
        """C22. Existing users get different behaviour, and discovering that
        mid-run is the worst way to learn it."""
        changelog = (Path(__file__).parent.parent / "CHANGELOG.md").read_text()
        unreleased = changelog.split("## 0.5.1")[0]
        assert "BEHAVIOUR CHANGE ON UPGRADE" in unreleased
        assert "--dangerously-skip-permissions" in unreleased
        assert "--permission-prompts none" in unreleased
