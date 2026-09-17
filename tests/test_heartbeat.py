import time
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.reporting.heartbeat import (
    detect_stalls,
    not_started,
    read_heartbeat,
    write_heartbeat,
)


def _claim(root: Path, worker: str) -> None:
    ClaimsLedger(root / ".rite" / "claims.json").claim([f"src/{worker}"], worker)


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
    def test_no_heartbeat_while_holding_claims_is_stall(self, tmp_path: Path):
        _claim(tmp_path, "alpha")
        stalls = detect_stalls(tmp_path, ["alpha"], threshold_seconds=60)
        assert len(stalls) == 1
        assert stalls[0].worker == "alpha"

    def test_no_heartbeat_reports_infinity_not_the_current_epoch(self, tmp_path: Path):
        """A worker that has never sent a heartbeat previously reported
        `seconds_silent=now` — the current epoch timestamp (~1.8 billion)
        misread as a duration, i.e. every never-started worker showed as
        stalled for ~56 years. Caught by running `rite watchdog` against a
        freshly-created worker by hand."""
        _claim(tmp_path, "alpha")
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
        _claim(tmp_path, "beta")
        stalls = detect_stalls(tmp_path, ["alpha", "beta"], threshold_seconds=60)
        assert len(stalls) == 1
        assert stalls[0].worker == "beta"


class TestNotStarted:
    """A worker nobody has picked up is not a stall. `rite add worker` then
    `rite status` showed it STALLED, and the watchdog exited 1 on it every
    cycle until a first heartbeat."""

    def test_no_heartbeat_and_no_claims_is_not_started_not_stalled(self, tmp_path):
        assert detect_stalls(tmp_path, ["alpha"], threshold_seconds=60) == []
        assert not_started(tmp_path, ["alpha"]) == ["alpha"]

    def test_a_claim_is_evidence_it_started(self, tmp_path):
        _claim(tmp_path, "alpha")
        assert not_started(tmp_path, ["alpha"]) == []
        assert [s.worker for s in detect_stalls(tmp_path, ["alpha"], 60)] == ["alpha"]

    def test_a_heartbeat_is_evidence_it_started(self, tmp_path):
        write_heartbeat(tmp_path, "alpha")
        assert not_started(tmp_path, ["alpha"]) == []

    def test_an_unreadable_ledger_counts_nobody_as_not_started(self, tmp_path):
        """A missed stall is worse than a false one."""
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "claims.json").write_text("{not json")
        assert not_started(tmp_path, ["alpha"]) == []
        assert [s.worker for s in detect_stalls(tmp_path, ["alpha"], 60)] == ["alpha"]
