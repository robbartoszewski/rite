import time
from pathlib import Path

from rite_ai.reporting.heartbeat import (
    detect_stalls,
    read_heartbeat,
    write_heartbeat,
)


class TestWriteAndRead:
    def test_roundtrip(self, tmp_path: Path):
        write_heartbeat(tmp_path, "alpha", ticket="T-1", message="working")
        hb = read_heartbeat(tmp_path, "alpha")
        assert hb is not None
        assert hb.worker == "alpha"
        assert hb.ticket == "T-1"
        assert hb.message == "working"
        assert hb.timestamp > 0

    def test_read_missing_returns_none(self, tmp_path: Path):
        assert read_heartbeat(tmp_path, "ghost") is None

    def test_overwrite_on_repeat(self, tmp_path: Path):
        write_heartbeat(tmp_path, "alpha", message="first")
        write_heartbeat(tmp_path, "alpha", message="second")
        hb = read_heartbeat(tmp_path, "alpha")
        assert hb is not None
        assert hb.message == "second"


class TestDetectStalls:
    def test_no_heartbeat_is_stall(self, tmp_path: Path):
        stalls = detect_stalls(tmp_path, ["alpha"], threshold_seconds=60)
        assert len(stalls) == 1
        assert stalls[0].worker == "alpha"

    def test_no_heartbeat_reports_infinity_not_the_current_epoch(self, tmp_path: Path):
        """A worker that has never sent a heartbeat previously reported
        `seconds_silent=now` — the current epoch timestamp (~1.8 billion)
        misread as a duration, i.e. every never-started worker showed as
        stalled for ~56 years. Caught by running `rite watchdog` against a
        freshly-created worker by hand."""
        stalls = detect_stalls(tmp_path, ["alpha"], threshold_seconds=60)
        assert stalls[0].seconds_silent == float("inf")

    def test_recent_heartbeat_not_stalled(self, tmp_path: Path):
        write_heartbeat(tmp_path, "alpha")
        stalls = detect_stalls(tmp_path, ["alpha"], threshold_seconds=60)
        assert len(stalls) == 0

    def test_old_heartbeat_is_stall(self, tmp_path: Path, monkeypatch):
        write_heartbeat(tmp_path, "alpha", ticket="T-5")
        hb_path = tmp_path / ".rite" / "heartbeats" / "alpha.json"
        import json

        data = json.loads(hb_path.read_text())
        data["timestamp"] = time.time() - 7200
        hb_path.write_text(json.dumps(data))
        stalls = detect_stalls(tmp_path, ["alpha"], threshold_seconds=3600)
        assert len(stalls) == 1
        assert stalls[0].ticket == "T-5"

    def test_mixed_workers(self, tmp_path: Path):
        write_heartbeat(tmp_path, "alpha")
        stalls = detect_stalls(tmp_path, ["alpha", "beta"], threshold_seconds=60)
        assert len(stalls) == 1
        assert stalls[0].worker == "beta"
