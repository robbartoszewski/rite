"""The Manager boundary is chosen by platform, and says so when it cannot be.

⚠ **THE DEFECT THIS PINS.** The launch path called `enclosure.write_profile`
and `enclosure.wrap` unconditionally, so every Manager on every platform was
launched with `sandbox-exec`. On Linux that binary does not exist and the
failure arrived as a Manager that started and vanished — four of CI's nine
failures said `sandbox-exec -f … is not running` rather than "this platform
has no seatbelt".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rite_ai.managers import enclosure, landlock
from rite_ai.managers.boundaries import (
    LANDLOCK_BOUNDARY,
    SEATBELT_BOUNDARY,
    UnsupportedPlatform,
    boundary_for,
    describe,
)


class TestItPicksByPlatform:
    def test_macos_gets_seatbelt(self):
        assert boundary_for("Darwin").name == "seatbelt"

    def test_linux_gets_landlock(self):
        # Resolved by platform NAME, so this is answerable from any machine —
        # `available()` is a separate question and tested separately.
        from rite_ai.managers.boundaries import _BY_SYSTEM

        assert _BY_SYSTEM["Linux"].name == "landlock"

    def test_an_unknown_platform_is_refused_with_a_reason(self):
        """⚠ Raises rather than defaulting. A default would be either a
        `sandbox-exec` that is not there, or nothing at all — a Manager
        running unconfined while every report says it is confined."""
        with pytest.raises(UnsupportedPlatform) as raised:
            boundary_for("Plan9")
        message = str(raised.value)
        assert "Plan9" in message
        assert "Darwin" in message and "Linux" in message
        # It must say what rite will do about it, not only that it is unhappy.
        assert "unconfined" in message

    def test_describe_is_one_line_for_doctor(self):
        described = describe()
        assert "\n" not in described
        assert described.startswith(("seatbelt", "landlock", "UNAVAILABLE"))


class TestTheMacOSPathIsUnchanged:
    """⚠ **Behaviourally identical, not merely equivalent.** The seatbelt
    profile's behaviour is pinned by
    `test_the_manager_profile_denies_what_it_should.py`, and this change is
    not allowed to alter it. The cheapest way to prove that is to assert the
    seatbelt backend holds the SAME function objects rather than copies."""

    def test_it_is_enclosure_itself(self):
        assert SEATBELT_BOUNDARY.write_profile is enclosure.write_profile
        assert SEATBELT_BOUNDARY.wrap is enclosure.wrap
        assert SEATBELT_BOUNDARY.limitations is enclosure.limitations
        assert SEATBELT_BOUNDARY.engine_tmp is enclosure.engine_tmp
        assert (
            SEATBELT_BOUNDARY.refusal_looks_like_ours
            is enclosure.refusal_looks_like_ours
        )
        assert SEATBELT_BOUNDARY.why_it_was_refused is enclosure.why_it_was_refused

    def test_wrap_still_prefixes_sandbox_exec(self, tmp_path):
        wrapped = SEATBELT_BOUNDARY.wrap("sh -p < prompt.txt", tmp_path / "p.sb")
        assert wrapped.startswith("sandbox-exec -f ")
        # The redirection stays at the END, where the surrounding shell applies
        # it to the whole command.
        assert wrapped.endswith("sh -p < prompt.txt")


class TestTheLandlockPolicy:
    def test_the_project_is_writable_and_home_is_not_granted(self, tmp_path):
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        project = tmp_path / "proj"
        project.mkdir()
        policy = landlock.compose_policy(project, "lead", home)

        assert str(project.resolve()) in policy["writable"]
        # ⚠ `$HOME` is absent from BOTH lists, which is how it is denied:
        # Landlock grants nothing it is not told to grant, so unlike seatbelt
        # there is no ordering to get wrong.
        assert str(home) not in policy["writable"]
        assert str(home) not in policy["readable"]

    def test_git_identity_is_read_only(self, tmp_path):
        policy = landlock.compose_policy(tmp_path, "lead", tmp_path / "home")
        overrides = policy["readonly_overrides"]
        assert str(tmp_path / "home" / ".gitconfig") in overrides
        assert str(tmp_path / "home" / ".config/git") in overrides

    def test_wrap_runs_the_launcher_and_keeps_the_redirection_last(self, tmp_path):
        wrapped = LANDLOCK_BOUNDARY.wrap("sh -p < prompt.txt", tmp_path / "p.json")
        assert "-m rite_ai.managers.landlock" in wrapped
        assert " -- sh -p < prompt.txt" in wrapped
        assert wrapped.endswith("sh -p < prompt.txt")


class TestAccessRightsMatchTheNodeType:
    """⚠ **THE BUG THIS PINS, MEASURED.** `landlock_add_rule` rejects a
    directory right on a regular file with EINVAL, and one rejected rule fails
    the whole ruleset. The first Linux launch died on
    `landlock_add_rule(/work/repo/VERSION): Invalid argument` — `_running_rite`
    names that one FILE, and `_tool_paths` names `.claude.json` and
    `.gitconfig`. The Landlock spike never hit it because it granted only
    directories."""

    def test_a_file_keeps_only_file_rights(self, tmp_path):
        target = tmp_path / "VERSION"
        target.write_text("0.0.0\n")
        masked = landlock.READ_WRITE & landlock.FILE_ONLY
        assert masked & landlock.A_READ_FILE
        # The directory-only rights must be gone, or the kernel refuses it.
        assert not masked & landlock.A_READ_DIR
        assert not masked & landlock.A_MAKE_REG
        assert os.path.isfile(target)

    def test_a_directory_keeps_the_directory_rights(self):
        assert landlock.READ_WRITE & landlock.A_READ_DIR
        assert landlock.READ_WRITE & landlock.A_MAKE_REG


class TestItSaysWhatItDoesNotBuy:
    def test_landlock_names_the_socket_hole_every_run(self):
        """🔴 The hole this ships with, visible where somebody reads the
        code — not only in a design note. Landlock does not govern
        `connect(2)`, so a unix socket in a denied directory stays
        reachable, and rite's own tmux control socket is that shape."""
        text = " ".join(landlock.limitations()).lower()
        assert "connect(2)" in text
        assert "socket" in text
        assert "tmux" in text

    def test_seatbelt_still_names_its_own_two_closed_escapes(self):
        text = " ".join(enclosure.limitations()).lower()
        assert "tmux" in text and "signal" in text

    def test_landlock_names_the_signal_hole_when_the_kernel_is_too_old(
        self, monkeypatch
    ):
        """⚠ Dynamic, unlike seatbelt's. Signal scoping needs ABI 6 and a
        stock Ubuntu 24.04 kernel is 6.8 — ABI 4 — so on a common machine the
        signal escape is OPEN. A fixed list would claim it closed there."""
        monkeypatch.setattr(landlock, "abi", lambda: 4)
        text = " ".join(landlock.limitations()).lower()
        assert "signals cross this boundary" in text

        monkeypatch.setattr(landlock, "abi", lambda: 8)
        newer = " ".join(landlock.limitations()).lower()
        assert "signals cross this boundary" not in newer
        assert "signals do not cross the boundary" in newer


class TestTheLauncherRefusesRatherThanRunUnconfined:
    """⚠ A boundary that could not be applied must not launch anyway. That is
    how a Manager ends up unconfined while everything reports fine."""

    def test_an_unreadable_policy_refuses_and_runs_nothing(self, tmp_path, capsys):
        witness = tmp_path / "it-ran"
        rc = landlock.main(
            [
                str(tmp_path / "absent.json"),
                "--",
                "sh",
                "-c",
                f"touch {witness}",
            ]
        )
        assert rc == 1
        assert not witness.exists(), "the command ran although the policy did not load"
        assert "boundary policy" in capsys.readouterr().err

    def test_usage_is_refused_without_a_separator(self, tmp_path, capsys):
        assert landlock.main([str(tmp_path / "p.json"), "sh"]) == 2
        assert "usage:" in capsys.readouterr().err

    @pytest.mark.skipif(
        landlock.abi() > 0, reason="this kernel HAS Landlock; tested on Linux instead"
    )
    def test_a_kernel_without_landlock_refuses(self, tmp_path, capsys):
        policy = tmp_path / "p.json"
        policy.write_text('{"readable": [], "writable": []}')
        witness = tmp_path / "it-ran"
        rc = landlock.main([str(policy), "--", "sh", "-c", f"touch {witness}"])
        assert rc == 1
        assert not witness.exists()
        err = capsys.readouterr().err
        assert "could not apply the Manager boundary" in err
        assert "unconfined" in err


def test_the_policy_file_lands_under_the_user_directory(tmp_path):
    """Local facts stay local: absolute paths inside are meaningless on any
    other machine, which is why both backends write under `user_dir`."""
    written = landlock.write_profile(tmp_path, "lead", tmp_path / "home")
    assert written.exists()
    assert Path("user") in written.parents[1].parts or "user" in str(written)
