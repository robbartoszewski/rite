"""Can the scheduler log be read the morning after?

Established by running a real launchd agent overnight. The cadence was
never the problem — launchd fires precisely. What the log could not
answer was *when* anything happened: hundreds of byte-identical lines,
no timestamps, and so no way to tell a scheduler that ran all night from
one that stopped at 03:00 and resumed at 07:00.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.duration import format_duration as _format_duration
from rite_ai.scheduler import (
    _gap_message,
    _read_last_tick,
    _write_last_tick,
    run_tick,
)

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}\s")


def _project(root: Path, interval: int = 5) -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n  role: manager\n")
    (root / ".rite" / "config.yaml").write_text(
        f"ticket_backend:\n  type: none\nwatchdog:\n  interval_minutes: {interval}\n"
    )
    return root


class TestEveryLineIsTimestamped:
    def test_the_routine_line_carries_a_timestamp(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["scheduler-tick"])
        assert result.exit_code == 0, result.output
        for line in result.output.strip().split("\n"):
            assert STAMP.match(line), f"unstamped: {line!r}"

    def test_the_timestamp_carries_its_offset(self, tmp_path, monkeypatch):
        """Without the offset a log spanning a DST change silently shifts
        an hour mid-file."""
        root = _project(tmp_path)
        monkeypatch.chdir(root)
        out = CliRunner().invoke(cli, ["scheduler-tick"]).output.strip()
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}", out)

    def test_multi_line_messages_are_stamped_on_every_line(self, tmp_path, monkeypatch):
        """The log is append-only across runs, so a line without its own
        timestamp cannot be placed once anything is interleaved."""
        root = tmp_path / "brokenproj"
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text("project:\n  name: [unclosed\n")
        (root / ".rite" / "config.yaml").write_text("ticket_backend:\n  type: none\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["scheduler-tick"])
        lines = result.output.strip().split("\n")
        assert len(lines) > 1, "expected a multi-line parse error"
        for line in lines:
            assert STAMP.match(line), f"unstamped: {line!r}"


class TestGapsAreReportedNotInferred:
    """Answering "did it run all night?" by diffing timestamps down
    hundreds of identical lines is work the tick can just do."""

    def test_a_long_gap_is_announced(self, tmp_path):
        root = _project(tmp_path, interval=5)
        _write_last_tick(root, time.time() - 4 * 3600)
        result = run_tick(root)
        gaps = [m for m in result.messages if m.startswith("gap since previous tick")]
        assert len(gaps) == 1
        assert "4h" in gaps[0]
        assert "cadence ~5m" in gaps[0]

    def test_it_names_sleep_rather_than_implying_a_fault(self, tmp_path):
        root = _project(tmp_path)
        _write_last_tick(root, time.time() - 6 * 3600)
        message = _gap_message(root, time.time())
        assert "sleep" in message
        assert "launchd does not replay" in message

    def test_no_gap_reported_on_the_very_first_tick(self, tmp_path):
        root = _project(tmp_path)
        assert _gap_message(root, time.time()) is None
        result = run_tick(root)
        assert not [m for m in result.messages if m.startswith("gap since")]

    def test_normal_cadence_is_quiet(self, tmp_path):
        root = _project(tmp_path, interval=5)
        _write_last_tick(root, time.time() - 5 * 60)
        assert _gap_message(root, time.time()) is None

    def test_ordinary_jitter_is_quiet(self, tmp_path):
        """launchd coalesces timers for power; a tick landing late is not
        an event."""
        root = _project(tmp_path, interval=5)
        _write_last_tick(root, time.time() - 12 * 60)
        assert _gap_message(root, time.time()) is None

    def test_a_fast_cadence_still_has_a_floor(self, tmp_path):
        """At a one-minute cadence, three intervals would make every
        four-minute hiccup an event."""
        root = _project(tmp_path, interval=1)
        _write_last_tick(root, time.time() - 5 * 60)
        assert _gap_message(root, time.time()) is None
        _write_last_tick(root, time.time() - 30 * 60)
        assert _gap_message(root, time.time()) is not None

    def test_every_tick_records_when_it_ran(self, tmp_path):
        root = _project(tmp_path)
        before = time.time()
        run_tick(root)
        recorded = _read_last_tick(root)
        assert recorded is not None
        assert recorded >= before

    def test_a_corrupt_last_tick_file_is_survivable(self, tmp_path):
        root = _project(tmp_path)
        (root / ".rite" / "scheduler-last-tick").write_text("not a number")
        assert _read_last_tick(root) is None
        assert run_tick(root).ok

    def test_a_missing_config_does_not_stop_the_gap_check(self, tmp_path):
        """The threshold is best-effort; a broken config changes how
        chatty the log is, never whether the tick works."""
        root = tmp_path / "p"
        (root / ".rite").mkdir(parents=True)
        _write_last_tick(root, time.time() - 6 * 3600)
        assert _gap_message(root, time.time()) is not None


class TestDurationReadsLikeAHuman:
    def test_formats(self):
        assert _format_duration(45) == "45s"
        assert _format_duration(60) == "1m"
        assert _format_duration(90) == "1m 30s"
        assert _format_duration(3600) == "1h"
        assert _format_duration(4 * 3600 + 12 * 60) == "4h 12m"

    def test_negative_is_clamped(self):
        """Clock adjustments can put the previous tick in the future."""
        assert _format_duration(-10) == "0s"


class TestStallAgeReadsAsWords:
    """The watchdog's stall line is read unattended, hours after the
    fact. `3608s` is the same fact as `1h 0m` only if you are willing to
    do the arithmetic."""

    def _stalled_project(self, root: Path, age_seconds: float) -> Path:
        import json

        (root / ".rite" / "heartbeats").mkdir(parents=True, exist_ok=True)
        (root / "workers" / "alpha").mkdir(parents=True, exist_ok=True)
        (root / ".rite" / "brief.yaml").write_text(
            "project:\n  name: p\n  role: manager\n"
        )
        (root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\n"
            "heartbeat:\n  interval_minutes: 1\n  stall_threshold: 2\n"
        )
        (root / "workers" / "alpha" / "worker.yml").write_text(
            "worker:\n  name: alpha\n  modules: []\n"
        )
        (root / ".rite" / "heartbeats" / "alpha.json").write_text(
            json.dumps(
                {
                    "worker": "alpha",
                    "timestamp": time.time() - age_seconds,
                    "ticket": "DEF-42",
                }
            )
        )
        return root

    def test_an_hour_reads_as_an_hour(self, tmp_path: Path):
        from rite_ai.watchdog import run_watchdog_check

        root = self._stalled_project(tmp_path / "p", 3608)
        reasons = run_watchdog_check(root).reasons
        stall = [r for r in reasons if "stalled" in r]
        assert stall, reasons
        assert "1h" in stall[0]
        assert "3608s" not in stall[0]

    def test_a_worker_never_seen_is_said_in_words_too(self, tmp_path: Path):
        from rite_ai.watchdog import run_watchdog_check

        root = self._stalled_project(tmp_path / "p", 60)
        (root / ".rite" / "heartbeats" / "alpha.json").unlink()
        from rite_ai.claims.ledger import ClaimsLedger

        ClaimsLedger(root / ".rite" / "claims.json").claim(["src"], "alpha")
        reasons = run_watchdog_check(root).reasons
        assert any("no heartbeat ever recorded" in r for r in reasons), reasons
