"""The permission mode rite passes, every cycle.

⚠ **Nothing carries a permission mode into `-p`.** Resuming from a
terminal restores the mode a session was in, and that restoration
explicitly EXCLUDES `-p`. So there is no path where a User grants
permission once, interactively, and the unattended cycles inherit it: the
mode is a value rite composes at the launch site and passes on EVERY
invocation, exactly as `--resume <id>` is.

Measured on a bare `claude -p`, no rite involved:

  - with `--permission-mode acceptEdits`: exit 0, the file was created with
    the contents asked for;
  - with no mode at all: exit 0, NO file, "I don't have permission to write
    that file";
  - and under `acceptEdits` a shell command really executed — `date +%s`
    returned a value inside the real time window, which a model writing the
    file from memory could not produce.

The middle case is why a resumed cycle without the flag would be a Manager
that silently stops being able to work while still exiting 0.
"""

from __future__ import annotations

import pytest

from rite_ai.managers.permissions import (
    ACCEPT_EDITS,
    SKIP_ALL,
    permission_argument,
    permission_mode,
)
from rite_ai.managers.supervise import launch_command


class TestTheDefaultIsSafeAndAlwaysPassed:
    def test_a_manager_with_no_configuration_gets_acceptEdits(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        chosen = permission_mode(tmp_path, "lead")
        assert chosen.mode == ACCEPT_EDITS
        assert not chosen.explicit
        assert not chosen.problem

    def test_the_flag_is_on_the_first_cycle(self):
        assert "--permission-mode acceptEdits" in launch_command(
            "claude", "", "/p.txt", permission_argument(ACCEPT_EDITS)
        )

    def test_the_flag_is_on_a_RESUMED_cycle_too(self):
        """⚠ The one that matters: `-p` does not restore a session's mode,
        so a resumed cycle without it is a Manager that can no longer act
        and still exits 0."""
        built = launch_command(
            "claude", "abc-123", "/p.txt", permission_argument(ACCEPT_EDITS)
        )
        assert "--permission-mode acceptEdits" in built
        assert "--resume abc-123" in built


class TestTheOptInIsADeliberateAct:
    def _write(self, tmp_path, text):
        (tmp_path / ".rite" / "user").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".rite" / "user" / "lead.yaml").write_text(text)

    def test_the_opt_in_is_read_from_the_per_manager_file(self, tmp_path):
        self._write(tmp_path, "permission_mode: dangerously-skip-permissions\n")
        chosen = permission_mode(tmp_path, "lead")
        assert chosen.mode == SKIP_ALL
        assert chosen.explicit

    def test_it_becomes_the_engines_own_flag_not_a_mode_value(self):
        """`--dangerously-skip-permissions` is its own flag; passing it as a
        value to `--permission-mode` would be a different, wrong thing."""
        assert permission_argument(SKIP_ALL) == "--dangerously-skip-permissions"
        assert permission_argument(ACCEPT_EDITS) == "--permission-mode acceptEdits"

    def test_the_opt_in_is_per_manager_not_per_project(self, tmp_path):
        self._write(tmp_path, "permission_mode: dangerously-skip-permissions\n")
        assert permission_mode(tmp_path, "lead").mode == SKIP_ALL
        assert permission_mode(tmp_path, "planner").mode == ACCEPT_EDITS

    @pytest.mark.parametrize(
        "text",
        [
            "permission_mode: acceptedits\n",
            "permission_mode: skip\n",
            "permission_mode: yes\n",
            "permission_mode: true\n",
            "permission_mode: --dangerously-skip-permissions\n",
        ],
    )
    def test_an_unrecognised_value_REFUSES_rather_than_guessing(self, tmp_path, text):
        """⚠ Never silently falls back to the default. A user who wrote
        something in this file made a decision about what their Manager may
        do to their machine, and running with a different one because their
        spelling was wrong is the worst of both."""
        self._write(tmp_path, text)
        chosen = permission_mode(tmp_path, "lead")
        assert chosen.problem, f"{text!r} was accepted"
        assert "permission_mode" in chosen.problem

    def test_a_corrupt_file_refuses_too(self, tmp_path):
        self._write(tmp_path, "permission_mode: [this is not a string\n")
        assert permission_mode(tmp_path, "lead").problem

    def test_an_empty_file_is_not_an_opt_in(self, tmp_path):
        self._write(tmp_path, "")
        chosen = permission_mode(tmp_path, "lead")
        assert chosen.mode == ACCEPT_EDITS and not chosen.explicit


class TestTheModeIsStated:
    def test_the_choice_carries_what_to_say(self, tmp_path):
        """A Manager that CHOSE not to act and one that was NOT ALLOWED to
        act look identical without this."""
        (tmp_path / ".rite").mkdir()
        assert "acceptEdits" in permission_mode(tmp_path, "lead").announcement

    def test_the_opt_in_announcement_says_it_is_the_dangerous_one(self, tmp_path):
        (tmp_path / ".rite" / "user").mkdir(parents=True)
        (tmp_path / ".rite" / "user" / "lead.yaml").write_text(
            "permission_mode: dangerously-skip-permissions\n"
        )
        said = permission_mode(tmp_path, "lead").announcement
        assert "dangerously-skip-permissions" in said
        assert "lead.yaml" in said, "it must name where the choice came from"
