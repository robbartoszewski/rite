"""An engine's permission mode must reach the engine (B4b, break 3).

⚠ **THE BUG THIS CLOSES IS AN OMISSION, WHICH IS WHY NOTHING CAUGHT IT.**
`launch_command` correctly refuses to write a permission FLAG for an engine
that keeps its mode in the environment — and nothing put the value anywhere
else. So a Goose Manager launched with no permission handling at all: it took
whatever `GOOSE_MODE` the operator's shell carried, or Goose's own default
when it carried none.

Measured 2026-09-24, that default is `auto`, which ran `rm` on a file
unattended — so the Manager ran **unconstrained**, days after C4 deliberately
made the secure option the default for a `claude` Manager.
"""

from __future__ import annotations

import pytest

from rite_ai.managers.engines import permission_placement
from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV, _on_tmux_argv
from rite_ai.managers.supervise import UNATTENDED_MODE_FOR_ENV_ENGINES, launch_command


class TestTheDestinationIsCarried:
    def test_claude_takes_its_mode_on_argv(self):
        assert permission_placement("claude", "", "--settings /x.json") == (
            "argv",
            "",
            "--settings /x.json",
        )

    def test_goose_takes_its_mode_in_the_environment(self):
        assert permission_placement("local:small", "goose", "auto") == (
            "env",
            "GOOSE_MODE",
            "auto",
        )

    def test_nothing_to_place_is_not_a_placement(self):
        assert permission_placement("claude", "", "") is None
        assert permission_placement("local:small", "goose", "") is None


class TestItGoesToTheRightPlaceAndOnlyThere:
    def test_gooses_mode_is_not_written_onto_its_command_line(self):
        """Writing it there would hand goose a flag it rejects."""
        built = launch_command("local:small", "", "/p.txt", "auto", agent="goose")
        assert "auto" not in built, built
        assert built == "goose run -i /p.txt"

    def test_claudes_command_line_is_unchanged(self):
        """⚠ The control. A change that altered the one engine rite actually
        launches would be a refactor with a behaviour change inside it."""
        assert launch_command("claude", "abc", "/p.txt", "--flag") == (
            "claude -p --flag --resume abc < /p.txt"
        )

    def test_the_mode_travels_as_a_pane_environment_variable(self):
        assert _on_tmux_argv("GOOSE_MODE", "auto") == ["-e", "GOOSE_MODE=auto"]


class TestTheArgvAllowlistStillHolds:
    """⚠ C6's guard. `-e NAME=value` puts the value on tmux's argv where
    `ps` shows it to every local account — harmless for a mode name, a leak
    for a credential. The trap is that the line doing it for a safe value
    reads as an established pattern."""

    def test_a_mode_name_is_allowed_because_it_is_not_a_secret(self):
        assert "GOOSE_MODE" in ALLOWED_ON_TMUX_ARGV

    def test_a_credential_is_still_refused_by_name(self):
        with pytest.raises(ValueError, match="readable by every local account"):
            _on_tmux_argv("CLAUDE_CODE_OAUTH_TOKEN", "sk-secret")

    def test_the_allowlist_did_not_become_a_rule_anything_can_satisfy(self):
        """A rule like "anything ending in _MODE" would admit the next
        variable by accident. It is a list of names."""
        with pytest.raises(ValueError):
            _on_tmux_argv("SOME_OTHER_MODE", "auto")


class TestTheValueIsRitesChoiceNotAmbient:
    def test_the_mode_is_a_named_constant_that_can_be_found(self):
        """⚠ It is `auto`, which is what goose does anyway — so this changes
        no behaviour. What it changes is that the choice is rite's and
        greppable, instead of whatever the operator's shell carried."""
        assert UNATTENDED_MODE_FOR_ENV_ENGINES == "auto"

    def test_an_operator_shell_setting_cannot_decide_the_session(self, monkeypatch):
        """The real improvement. With `GOOSE_MODE=approve` exported, a
        Manager would fail on its first real operation (measured: exit 1)
        with nothing saying why. rite now names the mode per session."""
        monkeypatch.setenv("GOOSE_MODE", "approve")
        placed = permission_placement(
            "local:small", "goose", UNATTENDED_MODE_FOR_ENV_ENGINES
        )
        assert placed == ("env", "GOOSE_MODE", "auto"), (
            "the ambient value decided the session's mode"
        )
