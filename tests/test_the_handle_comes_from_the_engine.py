"""Where a Manager's conversation handle comes from (B4b, break 1).

**Robert's principle: a local Manager should differ from a Claude Manager
only in the model it uses.** `_default_resume_id` broke that by asking
Claude Code's transcript directory for every engine's handle. Goose writes no
such transcript, so every resumed cycle got `resume_id=""` and started FRESH.

⚠ **And it failed silently, which `_default_resume_id`'s own docstring
already describes** — written about an earlier draft, and true again by a
different road:

    every "resume" ran a bare `claude`, and each cycle began a FRESH context
    with the ticket half-done and no memory of it — identical from outside
    to a resume that worked.

⚠ **The property is that a resumed cycle HAS the previous cycle's context**,
not that a handle was passed. A flag spelled correctly is exactly what the
old draft achieved. The end-to-end observation is recorded in the commit
message and in `ENGINE_CONTRACT.md`; these tests hold the seams that
observation runs through.
"""

from __future__ import annotations

import pytest

from rite_ai.managers.engines import CLAUDE, GOOSE, spelling_for
from rite_ai.managers.session import session_name
from rite_ai.managers.supervise import (
    _default_resume_id,
    _resume_id_source,
    launch_command,
)


class TestTheHandleSourceIsTheEnginesNotClaudes:
    """⚠ `handle_is_ours` is a SOURCE, not a format. Claude assigns an id
    rite must discover afterwards; Goose takes one rite chooses, so there is
    nothing to discover and the answer is known before the cycle runs."""

    def test_claude_still_discovers_its_id_from_the_transcript(self):
        assert _resume_id_source("claude", "") is _default_resume_id
        assert _resume_id_source("", "") is _default_resume_id

    def test_goose_does_not_go_near_the_transcript_scan(self, tmp_path):
        source = _resume_id_source("local:tier", "goose")
        assert source is not _default_resume_id
        assert source(tmp_path, "lead") == session_name(tmp_path, "lead")

    def test_the_chosen_handle_is_deterministic(self, tmp_path):
        """Cycle two has to name the same conversation cycle one created,
        and nothing is stored in between."""
        source = _resume_id_source("local:tier", "goose")
        assert source(tmp_path, "lead") == source(tmp_path, "lead")

    def test_two_managers_in_one_project_get_different_conversations(self, tmp_path):
        source = _resume_id_source("local:tier", "goose")
        assert source(tmp_path, "lead") != source(tmp_path, "planner")

    def test_the_transcript_scan_still_answers_nothing_when_there_is_nothing(
        self, tmp_path
    ):
        """⚠ Claude's side is unchanged: "" means the supervisor refuses to
        continue rather than silently starting fresh."""
        assert _default_resume_id(tmp_path, "lead") == ""


class TestANewConversationIsNAMEDWhenTheEngineTakesAName:
    """⚠ Without this, `resume` is unreachable for Goose. It resolves `-r` by
    name and fails loudly on a name it has never seen, so a first cycle with
    no `-n` creates a conversation under a name Goose chose — and the second
    cycle asks to continue one that does not exist."""

    def test_goose_names_a_fresh_conversation(self):
        built = launch_command("local:tier", "", "/p.txt", "", "goose", "rite-lead")
        assert "-n rite-lead" in built
        assert " -r" not in built

    def test_goose_resumes_that_same_name_next_cycle(self):
        built = launch_command("local:tier", "rite-lead", "/p.txt", "", "goose")
        assert "-n rite-lead -r" in built

    def test_a_resume_wins_over_a_start_handle(self):
        """They cannot both apply: naming a conversation and continuing one
        are the same argument in Goose's vocabulary."""
        built = launch_command(
            "local:tier", "rite-lead", "/p.txt", "", "goose", "rite-other"
        )
        assert "rite-other" not in built
        assert "-n rite-lead -r" in built

    def test_claudes_command_line_is_unchanged_byte_for_byte(self):
        """⚠ A registry that altered the one engine rite actually launches
        would be a refactor with a behaviour change hidden in it."""
        assert launch_command("claude", "", "/p.txt", "--flag") == (
            "claude -p --flag < /p.txt"
        )
        assert launch_command("claude", "abc-1", "/p.txt", "--flag") == (
            "claude -p --flag --resume abc-1 < /p.txt"
        )

    def test_a_start_handle_does_nothing_for_an_engine_that_assigns_its_own(self):
        """Claude has no `start` spelling, so the branch is unreachable for
        it — passing one cannot change its argv."""
        assert CLAUDE.start == ""
        assert launch_command("claude", "", "/p.txt", "--flag", "", "anything") == (
            "claude -p --flag < /p.txt"
        )

    @pytest.mark.parametrize("hostile", ["a$(touch X)", "-rf", "a b", "a`id`"])
    def test_a_hostile_handle_is_REFUSED_not_escaped(self, hostile):
        """This string is handed to `tmux new-session`, which runs it through
        `sh -c` — the same reason the resume id is checked."""
        with pytest.raises(ValueError) as raised:
            launch_command("local:tier", "", "/p.txt", "", "goose", hostile)
        assert "run by a shell" in str(raised.value)


class TestTheStarterDerivesTheHandleRatherThanBeingHandedIt:
    """⚠ This call site has silently dropped `prompt`, then `permission`,
    then `agent` — three arguments in two days, each producing a Manager that
    started and could not work. A fourth argument would be a fourth thing to
    drop."""

    def _command(self, tmp_path, resume_id, monkeypatch):
        import rite_ai.managers.supervise as sup

        seen: dict = {}

        def fake_start(root, manager, **kwargs):
            seen["command"] = kwargs.get("command", "")
            from rite_ai.managers.session import StartResult

            return StartResult(False, "not starting anything in a test")

        monkeypatch.setattr(sup, "start_session", fake_start)
        sup._default_starter(
            tmp_path,
            "lead",
            engine="local:tier",
            resume_id=resume_id,
            max_sessions=1,
            window_seconds=0,
            permission="",
            prompt="go",
            agent="goose",
        )
        return seen["command"]

    def test_a_fresh_cycle_names_the_conversation(self, tmp_path, monkeypatch):
        built = self._command(tmp_path, "", monkeypatch)
        assert f"-n {session_name(tmp_path, 'lead')}" in built
        assert " -r" not in built

    def test_a_resumed_cycle_continues_that_name(self, tmp_path, monkeypatch):
        handle = session_name(tmp_path, "lead")
        built = self._command(tmp_path, handle, monkeypatch)
        assert f"-n {handle} -r" in built


class TestTheRegistryRecordsBothDirections:
    def test_goose_declares_both_a_start_and_a_resume(self):
        assert GOOSE.start and GOOSE.resume
        assert GOOSE.handle_is_ours

    def test_claude_declares_a_resume_and_no_start(self):
        assert CLAUDE.resume and not CLAUDE.start
        assert not CLAUDE.handle_is_ours

    def test_a_local_tier_takes_its_agents_vocabulary(self):
        """`local:<class>` names a TIER, not a runtime (RL-42)."""
        assert spelling_for("local:anything", "goose") is GOOSE
