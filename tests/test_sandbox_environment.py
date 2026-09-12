"""The sandbox surface, against the real `yoloai` binary.

Two defects, both invisible to a mock because a mock has no environment
and no opinion about uncommitted changes:

- `rite sandbox start` failed for every real installation, because the
  venv rite runs from was on PATH inside the sandbox.
- It told the user to re-run with a flag it did not accept.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from rite_ai.config.models import SandboxConfig
from rite_ai.sandbox import sandbox_environment, start_worker


def _workspace(root: Path, worker: str = "alpha") -> Path:
    (root / "workers" / worker).mkdir(parents=True, exist_ok=True)
    return root


def _ok_run(captured: dict):
    def _run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=0, stdout="", stderr="")

    return _run


class TestSandboxEnvironmentStripsTheVenvItRunsFrom:
    def test_the_running_venv_bin_is_removed_from_path(self):
        """Measured against the real binary: a clean PATH starts a
        sandbox, the venv's bin on PATH does not. Inside seatbelt that
        directory is unreadable, so the first `python3` the agent resolves
        is rite's own interpreter, which cannot read its own pyvenv.cfg."""
        venv_bin = os.path.join(sys.prefix, "bin")
        base = {"PATH": os.pathsep.join([venv_bin, "/usr/bin", "/bin"])}
        with (
            patch.object(sys, "base_prefix", "/usr"),
            patch.object(sys, "prefix", sys.prefix),
        ):
            env = sandbox_environment(base)
        assert venv_bin not in env["PATH"].split(os.pathsep)
        assert "/usr/bin" in env["PATH"].split(os.pathsep)

    def test_virtual_env_is_dropped_alongside_it(self):
        venv_bin = os.path.join(sys.prefix, "bin")
        base = {"PATH": venv_bin + ":/usr/bin", "VIRTUAL_ENV": sys.prefix}
        with patch.object(sys, "base_prefix", "/usr"):
            env = sandbox_environment(base)
        assert "VIRTUAL_ENV" not in env

    def test_a_different_venv_on_path_is_left_alone(self):
        """A venv the user put there for the agent to use is not rite's to
        remove."""
        base = {"PATH": "/somebody/elses/venv/bin:/usr/bin"}
        with patch.object(sys, "base_prefix", "/usr"):
            env = sandbox_environment(base)
        assert "/somebody/elses/venv/bin" in env["PATH"].split(os.pathsep)

    def test_no_op_when_not_running_from_a_venv(self):
        base = {"PATH": "/usr/bin:/bin", "VIRTUAL_ENV": "/x"}
        with patch.object(sys, "base_prefix", sys.prefix):
            env = sandbox_environment(base)
        assert env == base

    def test_other_variables_are_preserved(self):
        venv_bin = os.path.join(sys.prefix, "bin")
        base = {"PATH": venv_bin + ":/usr/bin", "HOME": "/home/x", "LANG": "C"}
        with patch.object(sys, "base_prefix", "/usr"):
            env = sandbox_environment(base)
        assert env["HOME"] == "/home/x"
        assert env["LANG"] == "C"


class TestStartUsesTheCleanedEnvironment:
    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=0)
    def test_yoloai_is_spawned_with_a_cleaned_env(
        self, mock_count, mock_which, tmp_path: Path
    ):
        root = _workspace(tmp_path)
        captured: dict = {}
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_ok_run(captured)):
            result = start_worker(root, "alpha", SandboxConfig())
        assert result.ok, result.message
        assert captured["env"] is not None, "spawned with the inherited environment"


class TestAllowDirty:
    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=0)
    def test_the_flag_is_not_passed_by_default(
        self, mock_count, mock_which, tmp_path: Path
    ):
        """yoloAI's warning is about the Worker's own unpushed work, so
        proceeding is opt-in."""
        root = _workspace(tmp_path)
        captured: dict = {}
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_ok_run(captured)):
            start_worker(root, "alpha", SandboxConfig())
        assert "--allow-dirty" not in captured["args"]

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=0)
    def test_the_flag_is_passed_when_asked_for(
        self, mock_count, mock_which, tmp_path: Path
    ):
        root = _workspace(tmp_path)
        captured: dict = {}
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_ok_run(captured)):
            start_worker(root, "alpha", SandboxConfig(), allow_dirty=True)
        assert "--allow-dirty" in captured["args"]

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=0)
    def test_yoloais_advice_is_translated_into_a_flag_rite_accepts(
        self, mock_count, mock_which, tmp_path: Path
    ):
        """yoloAI says "Re-run with --allow-dirty", which is a flag on
        `yoloai new`. Passing that through unchanged sends someone to a
        flag this CLI rejects."""
        root = _workspace(tmp_path)

        def _dirty(args, **kwargs):
            return MagicMock(
                returncode=12,
                stdout="",
                stderr="uncommitted changes\nRe-run with --allow-dirty to proceed.",
            )

        with patch("rite_ai.sandbox.subprocess.run", side_effect=_dirty):
            result = start_worker(root, "alpha", SandboxConfig())
        assert not result.ok
        assert "rite sandbox start <worker> --allow-dirty" in result.message

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=0)
    def test_no_translation_when_the_flag_was_already_given(
        self, mock_count, mock_which, tmp_path: Path
    ):
        """Telling someone to re-run with a flag they just used is noise."""
        root = _workspace(tmp_path)

        def _fail(args, **kwargs):
            return MagicMock(
                returncode=1, stdout="", stderr="something about --allow-dirty"
            )

        with patch("rite_ai.sandbox.subprocess.run", side_effect=_fail):
            result = start_worker(root, "alpha", SandboxConfig(), allow_dirty=True)
        assert "re-run as" not in result.message
