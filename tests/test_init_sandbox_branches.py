"""`rite init`'s sandbox question — three branches, walked.

This is the on-ramp a stranger meets first and the prompt the dogfood
starts in, so each branch is exercised as a person would reach it rather
than asserted against the resolver in isolation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import rite_ai.sandbox as sb
from rite_ai.cli.init import prefs
from rite_ai.cli.init.questionnaire import _resolve_sandbox


class _Ui:
    """Records what the user was told and answers as instructed."""

    def __init__(self, answers: list[bool]):
        self.answers = list(answers)
        self.notes: list[str] = []
        self.warnings: list[str] = []
        self.questions: list[str] = []

    def note(self, message: str) -> None:
        self.notes.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def confirm(self, question: str, default: bool = True) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else default

    def said(self) -> str:
        return " ".join(self.notes + self.warnings)


def _resolve(ui):
    return _resolve_sandbox({}, True, ui)


class TestBranchOneVerifiedBackend:
    def test_it_asks_and_uses_the_verified_backend(self):
        ui = _Ui([True])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            patch.object(sb, "is_installed", return_value=True),
            patch.object(
                sb, "choose_backend", return_value=sb.BackendChoice("seatbelt")
            ),
        ):
            enabled, backend = _resolve(ui)
        assert (enabled, backend) == (True, "seatbelt")
        assert any("Run Workers in sandboxes?" in q for q in ui.questions)

    def test_no_is_respected(self):
        ui = _Ui([False])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            patch.object(sb, "is_installed", return_value=True),
            patch.object(
                sb, "choose_backend", return_value=sb.BackendChoice("seatbelt")
            ),
        ):
            enabled, _ = _resolve(ui)
        assert enabled is False


class TestBranchThreeNoVerifiedBackend:
    """Decided from the platform alone, BEFORE the install offer — with
    yoloAI absent there is nothing to ask about backends, and offering to
    install it where no verified backend can exist helps nobody."""

    def test_it_does_not_ask_and_says_why(self):
        ui = _Ui([])
        with patch.object(sb, "platform_can_sandbox", return_value=False):
            enabled, _ = _resolve(ui)
        assert enabled is False
        assert ui.questions == [], "asked a question whose Yes could not be honoured"
        assert "will not be sandboxed" in ui.said()
        assert "macOS-only" in ui.said()

    def test_it_never_offers_to_install(self):
        ui = _Ui([])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=False),
            patch.object(sb, "install_yoloai") as installer,
        ):
            _resolve(ui)
        installer.assert_not_called()

    def test_a_backend_that_becomes_unusable_later_still_stops_short(self):
        """yoloAI present, but nothing it offers is one rite has verified."""
        ui = _Ui([])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            patch.object(sb, "is_installed", return_value=True),
            patch.object(
                sb,
                "choose_backend",
                return_value=sb.BackendChoice(reason="seatbelt is unavailable here"),
            ),
        ):
            enabled, _ = _resolve(ui)
        assert enabled is False
        assert ui.questions == []


class TestBranchTwoInstallOffer:
    def _absent(self):
        return patch.object(sb, "is_installed", return_value=False)

    def test_declining_still_asks_the_sandbox_question(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        ui = _Ui([False, True])  # no to install, yes to sandboxing
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            self._absent(),
            patch.object(sb, "yoloai_install_command", return_value=["brew", "x"]),
        ):
            enabled, _ = _resolve(ui)
        assert enabled is True, "declining the install turned the feature off too"
        assert any("Run Workers in sandboxes?" in q for q in ui.questions)

    def test_a_failed_install_is_reported_and_init_continues(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        ui = _Ui([True, True])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            self._absent(),
            patch.object(sb, "yoloai_install_command", return_value=["brew", "x"]),
            patch.object(
                sb,
                "install_yoloai",
                return_value=sb.InstallOutcome(False, "install failed: no such cask"),
            ),
        ):
            enabled, _ = _resolve(ui)
        assert "no such cask" in ui.said()
        assert enabled is True
        assert any("Run Workers in sandboxes?" in q for q in ui.questions)

    def test_an_installer_that_lies_is_caught(self, tmp_path, monkeypatch):
        """Exit 0 with nothing on PATH is the failure that looks like
        success — `install_yoloai` re-resolves the binary rather than
        trusting the exit code."""
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        with (
            patch.object(sb, "yoloai_install_command", return_value=["brew", "x"]),
            patch.object(sb, "_yoloai_binary", return_value=None),
            patch.object(
                sb.subprocess,
                "run",
                return_value=type(
                    "P", (), {"returncode": 0, "stdout": "", "stderr": ""}
                )(),
            ),
        ):
            outcome = sb.install_yoloai()
        assert not outcome.ok
        assert "still not on PATH" in outcome.detail

    def test_no_installer_rite_knows_about_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        ui = _Ui([True])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            self._absent(),
            patch.object(sb, "yoloai_install_command", return_value=None),
        ):
            enabled, _ = _resolve(ui)
        assert "does not know how to install" in ui.said()
        assert enabled is True


class TestDecliningIsStickyPerMachine:
    """The install is a fact about the machine; the sandbox setting is a
    fact about the project. Only the first is remembered."""

    def test_a_decline_is_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        assert prefs.yoloai_install_declined() is False
        prefs.record_yoloai_install_declined()
        assert prefs.yoloai_install_declined() is True

    def test_a_second_project_is_not_asked_again(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            patch.object(sb, "is_installed", return_value=False),
            patch.object(sb, "yoloai_install_command", return_value=["brew", "x"]),
        ):
            first = _Ui([False, True])
            _resolve(first)
            second = _Ui([True])
            _resolve(second)
        assert any("Install it now" in q for q in first.questions)
        assert not any("Install it now" in q for q in second.questions)
        assert "declined installing it before" in second.said()

    def test_the_sandbox_question_is_still_asked_every_time(
        self, tmp_path, monkeypatch
    ):
        """Sticky suppresses the OFFER, never the setting."""
        monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
        prefs.record_yoloai_install_declined()
        ui = _Ui([True])
        with (
            patch.object(sb, "platform_can_sandbox", return_value=True),
            patch.object(sb, "is_installed", return_value=False),
            patch.object(sb, "yoloai_install_command", return_value=["brew", "x"]),
        ):
            enabled, _ = _resolve(ui)
        assert any("Run Workers in sandboxes?" in q for q in ui.questions)
        assert enabled is True

    def test_recording_never_raises_when_home_is_unwritable(
        self, tmp_path, monkeypatch
    ):
        """Failing to store a preference must not fail the init it was
        asked during."""
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory")
        monkeypatch.setenv("RITE_HOME_DIR", str(blocker / "home"))
        prefs.record_yoloai_install_declined()  # must not raise
        assert prefs.yoloai_install_declined() is False


class TestPlatformGate:
    def test_only_darwin_has_a_verified_backend(self):
        with patch.object(sb.sys, "platform", "darwin"):
            assert sb.platform_can_sandbox() is True
        with patch.object(sb.sys, "platform", "linux"):
            assert sb.platform_can_sandbox() is False

    def test_no_install_command_off_darwin(self):
        with patch.object(sb.sys, "platform", "linux"):
            assert sb.yoloai_install_command() is None

    def test_no_install_command_without_brew(self):
        with (
            patch.object(sb.sys, "platform", "darwin"),
            patch.object(sb.shutil, "which", return_value=None),
        ):
            assert sb.yoloai_install_command() is None


def test_prefs_file_lives_under_rite_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    prefs.record_yoloai_install_declined()
    assert (Path(tmp_path) / "home" / prefs.FILENAME).is_file()
