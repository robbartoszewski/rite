"""Regression tests for the sandbox worker-cap fail-open.

`count_active_sandboxes()` returned 0 whenever `yoloai ls` could not be
read — a broken daemon, a JSON format it did not recognise, a non-zero
exit. `start_worker` compared that 0 against
`sandbox.max_concurrent_workers` and let every start through, reporting
success each time. The cap silently stopped capping while the operator
still believed they had one.

It needs yoloai present-and-broken to trigger, which is why it never
showed up in testing — and why two tests in test_sandbox.py were passing
*because* of it: they mocked `subprocess.run` to answer every call with
blank output, so the `ls` call looked like "zero sandboxes running".

Each test below reproduces the original symptom.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.models import SandboxConfig
from rite_ai.sandbox import (
    CountUnavailable,
    count_active_sandboxes,
    start_worker,
    worker_sandbox_status,
)

YOLOAI = "/usr/local/bin/yoloai"

# The ways `yoloai ls` can fail to answer, each of which used to be
# indistinguishable from "nothing is running".
BROKEN_LS = {
    "non-zero exit": MagicMock(
        returncode=1, stdout="", stderr="could not connect to yoloai daemon"
    ),
    "empty output": MagicMock(returncode=0, stdout="", stderr=""),
    "not json": MagicMock(returncode=0, stdout="NAME    STATUS\nrite-a  up\n"),
    "json but not an object": MagicMock(returncode=0, stdout="[]"),
}


class TestCountRefusesToInventAZero:
    @pytest.mark.parametrize("label", sorted(BROKEN_LS))
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_unreadable_ls_is_unavailable_not_zero(self, mock_which, label: str):
        with patch("rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS[label]):
            result = count_active_sandboxes()

        assert isinstance(result, CountUnavailable), (
            f"{label}: reported {result!r} — a number the cap check will act on"
        )
        assert result.reason, "no reason given for a count that could not be taken"

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_genuine_zero_is_still_zero(self, mock_which):
        """The fix must not turn an empty machine into an error."""
        with patch(
            "rite_ai.sandbox.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='{"sandboxes": []}'),
        ):
            assert count_active_sandboxes() == 0


class TestStartRefusesWhenTheCapCannotBeEnforced:
    """The symptom in full: a cap of 1, a broken `ls`, and three sandboxes
    starting in a row with every one reporting success."""

    @pytest.mark.parametrize("label", sorted(BROKEN_LS))
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_start_refuses_and_never_shells_out(
        self, mock_which, label: str, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        calls = []

        def run(args, *a, **kw):
            calls.append(args)
            if "ls" in args:
                return BROKEN_LS[label]
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=1)
            )

        assert not result.ok, f"{label}: started a sandbox with the cap unenforceable"
        assert not any("new" in args for args in calls), (
            f"{label}: shelled out to `yoloai new` anyway"
        )

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_refusal_names_the_cap_and_the_underlying_failure(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS["non-zero exit"]
        ):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=3)
            )

        assert not result.ok
        assert "max_concurrent_workers" in result.message
        assert "3" in result.message
        assert "yoloai ls" in result.message, (
            "the operator is told the cap failed but not what to fix"
        )
        assert "yoloai daemon" in result.message  # the real stderr, carried through

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_the_original_symptom_three_starts_under_a_cap_of_one(
        self, mock_which, tmp_path: Path
    ):
        for name in ("alpha", "beta", "gamma"):
            (tmp_path / "workers" / name).mkdir(parents=True)

        def run(args, *a, **kw):
            if "ls" in args:
                return BROKEN_LS["non-zero exit"]
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            results = [
                start_worker(tmp_path, name, SandboxConfig(max_concurrent_workers=1))
                for name in ("alpha", "beta", "gamma")
            ]

        started = [r for r in results if r.ok]
        assert started == [], (
            f"{len(started)} sandboxes started under a cap of 1 with no way to "
            "count what was already running"
        )

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_a_readable_count_still_starts_normally(self, mock_which, tmp_path: Path):
        """Refusing on an unreadable count must not refuse on a readable
        one — the cap still has to permit what it always permitted."""
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        running = json.dumps({"sandboxes": [{"environment": {"name": "rite-beta"}}]})

        def run(args, *a, **kw):
            if "ls" in args:
                return MagicMock(returncode=0, stdout=running, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=5)
            )

        assert result.ok, result.message

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_the_cap_itself_still_refuses_over_the_limit(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        running = json.dumps(
            {"sandboxes": [{"environment": {"name": f"rite-w{i}"}} for i in range(5)]}
        )

        def run(args, *a, **kw):
            if "ls" in args:
                return MagicMock(returncode=0, stdout=running, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=5)
            )

        assert not result.ok
        assert "refused, not clamped" in result.message


class TestStatusDistinguishesAbsentFromUncheckable:
    """`worker_sandbox_status` collapsed the same two facts: a `yoloai ls`
    that failed reported `not found`, exactly as it would for a worker
    that genuinely has no sandbox."""

    @pytest.mark.parametrize("label", sorted(BROKEN_LS))
    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_unreadable_ls_is_not_reported_as_not_found(self, mock_which, label: str):
        with patch("rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS[label]):
            status = worker_sandbox_status("alpha")

        assert status.value != "not found", (
            f"{label}: a broken yoloai looked exactly like an absent sandbox"
        )
        assert not status.known

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_cli_exits_non_zero_when_the_status_is_unknown(self, mock_which):
        with patch(
            "rite_ai.sandbox.subprocess.run", return_value=BROKEN_LS["non-zero exit"]
        ):
            result = CliRunner().invoke(cli, ["sandbox", "status", "alpha"])

        assert result.exit_code == 1, (
            "an undeterminable status was reported as a successful answer"
        )
        assert "unknown" in result.output

    @patch("rite_ai.sandbox.shutil.which", return_value=YOLOAI)
    def test_cli_exits_zero_for_a_genuine_absence(self, mock_which):
        with patch(
            "rite_ai.sandbox.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='{"sandboxes": []}'),
        ):
            result = CliRunner().invoke(cli, ["sandbox", "status", "alpha"])

        assert result.exit_code == 0
        assert "not found" in result.output


class TestSandboxEnabledDocstringDescribesWhatItGoverns:
    """`sandbox.enabled` does not gate these commands — `rite sandbox
    start` works regardless. The group's help said "Optional
    (`sandbox.enabled` in config.yaml) — a single-project setup that
    doesn't opt in needs none of this", which reads as though it does."""

    def test_help_does_not_imply_the_key_gates_these_commands(self):
        help_text = cli.commands["sandbox"].help or ""
        assert "sandbox.enabled" in help_text
        assert "needs none of this" not in help_text

    def test_help_names_what_the_key_actually_governs(self):
        help_text = cli.commands["sandbox"].help or ""
        assert "rite add worker" in help_text  # scoped-token provisioning
        assert "rite doctor" in help_text  # yoloai-missing severity
