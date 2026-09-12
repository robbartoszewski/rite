from pathlib import Path
from unittest.mock import MagicMock, patch

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.scheduler import (
    detect_backend,
    install,
    is_installed,
    run_tick,
    uninstall,
)


def _project(tmp_path: Path, timezone_name: str = "", windows: str = "") -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    sched = ""
    if timezone_name:
        sched = f"schedule:\n  timezone: {timezone_name}\n{windows}"
    (rite_dir / "config.yaml").write_text(f"ticket_backend:\n  type: none\n{sched}")
    return tmp_path


class TestRunTick:
    def test_no_workers_no_blockers_no_schedule_is_a_noop(self, tmp_path: Path):
        root = _project(tmp_path)
        result = run_tick(root)
        assert result.ok
        assert not result.needs_attention

    def test_blocker_in_outbox_is_surfaced_and_needs_attention(self, tmp_path: Path):
        root = _project(tmp_path)
        from rite_ai.reporting.outbox import enqueue

        enqueue(root, "blocker", {"detail": "missing credentials"})
        result = run_tick(root)
        assert result.needs_attention
        assert any("watchdog" in m for m in result.messages)

    def test_schedule_boundary_into_zero_hands_over_active_workers(
        self, tmp_path: Path
    ):
        root = _project(
            tmp_path,
            timezone_name="UTC",
            windows=(
                "  windows:\n"
                "    - hours: '00:00-01:00'\n      workers: 3\n"
                "    - hours: '01:00-24:00'\n      workers: 0\n"
            ),
        )
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/a.ts"], "alpha", ticket="RW-1")

        state_path = root / ".rite" / "schedule-state.json"
        state_path.write_text("3")  # simulate "last tick saw 3 workers"

        with patch("rite_ai.scheduler.current_minute_of_day", return_value=720):
            result = run_tick(root)  # 720 = 12:00, inside the 0-workers window

        assert any("handed over worker 'alpha'" in m for m in result.messages)
        assert ledger.list_claims() == []

    def test_no_transition_does_not_hand_over(self, tmp_path: Path):
        root = _project(
            tmp_path,
            timezone_name="UTC",
            windows="  windows:\n    - hours: '00:00-24:00'\n      workers: 3\n",
        )
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/a.ts"], "alpha", ticket="RW-1")

        state_path = root / ".rite" / "schedule-state.json"
        state_path.write_text("3")

        with patch("rite_ai.scheduler.current_minute_of_day", return_value=600):
            result = run_tick(root)

        assert not any("handed over" in m for m in result.messages)
        assert len(ledger.list_claims()) == 1

    def test_first_ever_tick_records_state_without_acting(self, tmp_path: Path):
        """No prior state means nothing to compare against — must not be
        treated as a spurious transition into zero."""
        root = _project(
            tmp_path,
            timezone_name="UTC",
            windows="  windows:\n    - hours: '00:00-24:00'\n      workers: 0\n",
        )
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/a.ts"], "alpha", ticket="RW-1")

        result = run_tick(root)
        assert not any("handed over" in m for m in result.messages)
        assert len(ledger.list_claims()) == 1

    def test_no_timezone_skips_boundary_check_entirely(self, tmp_path: Path):
        root = _project(tmp_path)  # no schedule at all
        result = run_tick(root)
        assert result.ok


class TestOSLevelInstall:
    @patch("rite_ai.scheduler.subprocess.run")
    def test_install_cron_appends_marked_line(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="0 0 * * * echo hi\n"),  # crontab -l
            MagicMock(returncode=0, stderr=""),  # crontab -
        ]
        result = install(tmp_path, interval_minutes=5, backend="cron")
        assert result.ok
        write_call = mock_run.call_args_list[1]
        written = write_call.kwargs["input"]
        assert f"rite-scheduler:{tmp_path.resolve()}" in written
        assert "echo hi" in written  # existing crontab preserved

    @patch("rite_ai.scheduler.subprocess.run")
    def test_install_cron_refuses_if_already_installed(self, mock_run, tmp_path: Path):
        marker = f"# rite-scheduler:{tmp_path.resolve()}"
        mock_run.return_value = MagicMock(
            returncode=0, stdout=f"*/5 * * * * true  {marker}\n"
        )
        result = install(tmp_path, backend="cron")
        assert not result.ok
        assert "already installed" in result.message

    @patch("rite_ai.scheduler.subprocess.run")
    def test_install_cron_handles_no_existing_crontab(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),  # "no crontab for user"
            MagicMock(returncode=0, stderr=""),
        ]
        result = install(tmp_path, backend="cron")
        assert result.ok

    @patch("rite_ai.scheduler.subprocess.run")
    def test_uninstall_cron_removes_only_the_marked_line(
        self, mock_run, tmp_path: Path
    ):
        marker = f"# rite-scheduler:{tmp_path.resolve()}"
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=f"0 0 * * * echo hi\n*/5 * * * * true  {marker}\n",
            ),
            MagicMock(returncode=0, stderr=""),
        ]
        result = uninstall(tmp_path, backend="cron")
        assert result.ok
        written = mock_run.call_args_list[1].kwargs["input"]
        assert marker not in written
        assert "echo hi" in written

    @patch("rite_ai.scheduler.subprocess.run")
    def test_uninstall_cron_refuses_if_not_installed(self, mock_run, tmp_path: Path):
        mock_run.return_value = MagicMock(returncode=0, stdout="0 0 * * * echo hi\n")
        result = uninstall(tmp_path, backend="cron")
        assert not result.ok

    @patch("rite_ai.scheduler.subprocess.run")
    def test_is_installed_cron(self, mock_run, tmp_path: Path):
        marker = f"# rite-scheduler:{tmp_path.resolve()}"
        mock_run.return_value = MagicMock(returncode=0, stdout=f"x  {marker}\n")
        assert is_installed(tmp_path, backend="cron") is True

    @patch("rite_ai.scheduler.subprocess.run")
    def test_install_launchd_writes_plist_and_loads(self, mock_run, tmp_path: Path):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        with patch("rite_ai.scheduler.Path.home", return_value=tmp_path):
            result = install(tmp_path, interval_minutes=5, backend="launchd")
        assert result.ok
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][0] == "launchctl"

    @patch("rite_ai.scheduler.subprocess.run")
    def test_install_launchd_refuses_if_plist_exists(self, mock_run, tmp_path: Path):
        with patch("rite_ai.scheduler.Path.home", return_value=tmp_path):
            from rite_ai.scheduler import _launchd_plist_path

            plist_path = _launchd_plist_path(tmp_path)
            plist_path.parent.mkdir(parents=True)
            plist_path.write_text("existing")

            result = install(tmp_path, backend="launchd")
        assert not result.ok
        mock_run.assert_not_called()

    def test_detect_backend_matches_platform(self):
        import sys

        backend = detect_backend()
        if sys.platform == "darwin":
            assert backend == "launchd"
        else:
            assert backend == "cron"


class TestATickThatCannotRunSaysSo:
    """`run_tick` never set `ok=False`, so the CLI's `if not result.ok:
    raise SystemExit(1)` was dead code and every tick exited 0 — including
    a tick whose project had been deleted.

    launchd records the exit code and nothing else. Five soak agents left
    registered against directories that no longer existed fired every 60
    seconds for hours, each reporting `LastExitStatus = 0`, which is how
    they stayed unnoticed. Same class as `rite credential check` reporting
    a missing credential through exit 0: a check that cannot fail is not a
    check.
    """

    def test_a_missing_project_exits_nonzero(self, tmp_path: Path):
        result = run_tick(tmp_path)  # no .rite/ at all
        assert result.ok is False, "a tick with no project reported success"
        assert result.needs_attention is True

    def test_a_broken_config_exits_nonzero(self, tmp_path: Path):
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        (rite_dir / "config.yaml").write_text("ticket_backend: [not, a, mapping]\n")
        result = run_tick(tmp_path)
        assert result.ok is False
        assert any("config error" in m for m in result.messages)

    def test_a_healthy_tick_still_reports_success(self, tmp_path: Path):
        root = _project(tmp_path)
        result = run_tick(root)
        assert result.ok is True

    def test_finding_a_stall_is_not_a_failure(self, tmp_path: Path):
        """The tick did its job. Only being unable to run is a failure —
        otherwise every stalled worker would mark the agent as failing."""
        root = _project(tmp_path)
        (root / "workers" / "alpha").mkdir(parents=True)
        (root / "workers" / "alpha" / "worker.yml").write_text(
            'worker:\n  name: "alpha"\n  modules: []\n'
        )
        result = run_tick(root)
        assert result.needs_attention is True
        assert result.ok is True, "a stalled worker marked the tick itself failed"
