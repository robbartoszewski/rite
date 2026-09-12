"""`rite scheduler status` — did it RUN, not is it registered.

The command reported "launchd: installed", which is a fact about a plist
on disk. This project has already shipped one thing that was installed,
correct, executable and never read: the pre-push hook git ignored because
`core.hooksPath` pointed elsewhere. Registration and execution are
different claims.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.scheduler import _write_last_tick, format_health, health


def _project(root: Path, interval: int = 5) -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n  role: manager\n")
    (root / ".rite" / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\nwatchdog:\n  interval_minutes: {interval}\n"
    )
    return root


class TestItReportsExecutionNotRegistration:
    def test_a_recent_tick_is_reported_with_its_age(self, tmp_path: Path):
        root = _project(tmp_path)
        _write_last_tick(root, time.time() - 90)
        text = "\n".join(format_health(health(root, backend="cron")))
        assert "last tick:" in text
        assert "1m 30s ago" in text
        assert "OVERDUE" not in text

    def test_a_long_silence_is_flagged_overdue(self, tmp_path: Path):
        root = _project(tmp_path, interval=5)
        _write_last_tick(root, time.time() - 9 * 3600)
        state = health(root, backend="cron")
        assert state.overdue
        assert "OVERDUE" in "\n".join(format_health(state))

    def test_registered_but_never_ticked_says_so(self, tmp_path: Path):
        """The exact publish-hook shape: set up correctly, never fired."""
        root = _project(tmp_path)
        with patch("rite_ai.scheduler.is_installed", return_value=True):
            text = "\n".join(format_health(health(root, backend="cron")))
        assert "no tick has run yet" in text
        assert "nothing has confirmed it fires" in text

    def test_nothing_claimed_about_ticks_when_not_installed_and_never_run(
        self, tmp_path: Path
    ):
        root = _project(tmp_path)
        text = "\n".join(format_health(health(root, backend="cron")))
        assert "not installed" in text
        assert "no tick has run yet" not in text


class TestLaunchdSpecifics:
    def test_a_plist_launchd_does_not_hold_is_called_out(self, tmp_path: Path):
        root = _project(tmp_path)
        with (
            patch("rite_ai.scheduler.is_installed", return_value=True),
            patch("rite_ai.scheduler._launchd_job_state", return_value=(False, None)),
            patch("rite_ai.scheduler._plist_interval", return_value=300),
        ):
            text = "\n".join(format_health(health(root, backend="launchd")))
        assert "NOT LOADED" in text

    def test_no_contradiction_when_there_is_no_plist(self, tmp_path: Path):
        """ "not installed" and "the plist exists" in consecutive lines is
        worse than saying neither."""
        root = _project(tmp_path)
        with (
            patch("rite_ai.scheduler.is_installed", return_value=False),
            patch("rite_ai.scheduler._launchd_job_state", return_value=(False, None)),
        ):
            text = "\n".join(format_health(health(root, backend="launchd")))
        assert "not installed" in text
        assert "NOT LOADED" not in text

    def test_a_non_zero_last_exit_is_surfaced(self, tmp_path: Path):
        """launchd's exit column is the only trace a tick leaves when it
        fails before it can write anything."""
        root = _project(tmp_path)
        with (
            patch("rite_ai.scheduler.is_installed", return_value=True),
            patch("rite_ai.scheduler._launchd_job_state", return_value=(True, 127)),
            patch("rite_ai.scheduler._plist_interval", return_value=300),
        ):
            text = "\n".join(format_health(health(root, backend="launchd")))
        assert "exited 127" in text

    def test_the_interval_comes_from_the_plist_not_config(self, tmp_path: Path):
        """The OS-level interval is set at install time and can differ
        from watchdog.interval_minutes; the plist is what actually runs."""
        root = _project(tmp_path, interval=5)
        with (
            patch("rite_ai.scheduler.is_installed", return_value=True),
            patch("rite_ai.scheduler._launchd_job_state", return_value=(True, 0)),
            patch("rite_ai.scheduler._plist_interval", return_value=60),
        ):
            text = "\n".join(format_health(health(root, backend="launchd")))
        assert "every 1m" in text

    def test_overdue_is_judged_against_the_plist_interval(self, tmp_path: Path):
        root = _project(tmp_path, interval=60)
        _write_last_tick(root, time.time() - 20 * 60)
        with (
            patch("rite_ai.scheduler.is_installed", return_value=True),
            patch("rite_ai.scheduler._launchd_job_state", return_value=(True, 0)),
            patch("rite_ai.scheduler._plist_interval", return_value=60),
        ):
            assert health(root, backend="launchd").overdue


class TestLogSize:
    def test_rotated_archives_are_counted(self, tmp_path: Path):
        root = _project(tmp_path)
        log = root / ".rite" / "scheduler.log"
        log.write_text("x" * 100)
        (root / ".rite" / "scheduler.log.1").write_text("y" * 900)
        state = health(root, backend="cron")
        assert state.log_files == 2
        assert state.log_bytes == 1000
        assert "across 2 file(s)" in "\n".join(format_health(state))

    def test_no_log_is_not_reported_as_a_zero_byte_one(self, tmp_path: Path):
        root = _project(tmp_path)
        assert health(root, backend="cron").log_files == 0
        assert "log:" not in "\n".join(format_health(health(root, backend="cron")))


class TestThroughTheCli:
    def test_status_reports_the_last_tick(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _write_last_tick(root, time.time() - 30)
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["scheduler", "status", "--backend", "cron"])
        assert result.exit_code == 0, result.output
        assert "last tick:" in result.output
        assert "30s ago" in result.output
