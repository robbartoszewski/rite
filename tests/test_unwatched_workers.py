"""Regression tests for the gap found by running the scheduler under real
launchd: rite records state for workers it does not watch.

Every command takes `--worker <anything>` and reports success. The
watchdog's stall check reads `workers/<name>/worker.yml` manifests. On a
project where nobody ran `rite add worker`, those two facts combine into
the failure this project keeps finding — accepted, reported fine, does
nothing.

Measured on a live project driven by a launchd-scheduled tick: a claim on
`src` for ticket X-1 and a heartbeat 100 minutes stale against a
two-minute threshold, and `rite watchdog` answered "ok — nothing needs
attention" with exit 0, every minute, for as long as it ran. `rite
status` printed "no workers" and "claims (1): ghost" two lines apart and
remarked on neither.

The stall check itself is right to key on the manifest: switching it to
"every heartbeat file on disk" would make a file left behind by a
properly retired worker stall forever. So the register stays, and state
belonging to nobody in it becomes its own reported condition.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from click.testing import CliRunner

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.cli.main import cli
from rite_ai.pool import PoolSlot, _write_state
from rite_ai.reporting.heartbeat import write_heartbeat
from rite_ai.watchdog import run_watchdog_check


def _project(tmp_path: Path, *, stall_minutes: int = 1) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        f"heartbeat:\n  interval_minutes: {stall_minutes}\n  stall_threshold: 1\n"
    )
    return tmp_path


def _register(root: Path, worker: str) -> None:
    manifest = root / "workers" / worker / "worker.yml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(f"worker:\n  name: {worker}\n")


def _age_heartbeat(root: Path, worker: str, seconds: float) -> None:
    path = root / ".rite" / "heartbeats" / f"{worker}.json"
    record = json.loads(path.read_text())
    record["timestamp"] = time.time() - seconds
    path.write_text(json.dumps(record))


class TestWatchdogReportsWhatItIsNotWatching:
    def test_an_unregistered_worker_holding_a_claim_needs_attention(
        self, tmp_path: Path
    ):
        """The original symptom: a silent claim by a worker nothing
        watches. Before the fix this returned needs_attention=False."""
        root = _project(tmp_path)
        ClaimsLedger(root / ".rite" / "claims.json").claim(["src"], "ghost", "X-1")

        result = run_watchdog_check(root)

        assert result.needs_attention is True
        assert result.unwatched == ["ghost"]
        assert any("not registered" in r for r in result.reasons)
        assert any("holds a claim" in r for r in result.reasons)

    def test_an_unregistered_worker_with_a_stale_heartbeat_needs_attention(
        self, tmp_path: Path
    ):
        root = _project(tmp_path)
        write_heartbeat(root, "ghost", ticket="X-1")
        _age_heartbeat(root, "ghost", seconds=6000)

        result = run_watchdog_check(root)

        assert result.needs_attention is True
        assert result.unwatched == ["ghost"]
        # Not a STALL — nothing was watching it to go silent on. The two
        # conditions are different in kind and are reported separately.
        assert result.stalled == []

    def test_both_kinds_of_evidence_are_named_once_each(self, tmp_path: Path):
        root = _project(tmp_path)
        ClaimsLedger(root / ".rite" / "claims.json").claim(["src"], "ghost", "X-1")
        write_heartbeat(root, "ghost")

        reasons = run_watchdog_check(root).reasons
        assert len(reasons) == 1
        assert "has a heartbeat, holds a claim" in reasons[0]

    def test_the_message_says_how_to_resolve_it_either_way(self, tmp_path: Path):
        """Two legitimate answers — register the worker, or clear state
        that belongs to nobody. Naming only one of them turns a
        leftover-state warning into a nag."""
        root = _project(tmp_path)
        write_heartbeat(root, "ghost")

        reason = run_watchdog_check(root).reasons[0]
        assert "rite add worker ghost" in reason
        assert "rite release --worker ghost" in reason

    def test_a_registered_worker_is_watched_and_silent(self, tmp_path: Path):
        root = _project(tmp_path)
        _register(root, "alpha")
        write_heartbeat(root, "alpha")

        result = run_watchdog_check(root)

        assert result.unwatched == []
        assert result.needs_attention is False

    def test_a_registered_worker_still_stalls_normally(self, tmp_path: Path):
        """The fix must not shadow the check it sits beside."""
        root = _project(tmp_path)
        _register(root, "alpha")
        write_heartbeat(root, "alpha", ticket="X-1")
        _age_heartbeat(root, "alpha", seconds=6000)

        result = run_watchdog_check(root)

        assert result.unwatched == []
        assert [s.worker for s in result.stalled] == ["alpha"]
        assert any("stalled" in r for r in result.reasons)

    def test_a_ledger_that_will_not_parse_does_not_crash_the_check(
        self, tmp_path: Path
    ):
        """`rite claim` and `rite status` both raise on a corrupt ledger,
        which is right — they act on it. The watchdog runs unattended
        from launchd every few minutes and must degrade to reporting what
        it can still see."""
        root = _project(tmp_path)
        (root / ".rite" / "claims.json").write_text("{not json")
        write_heartbeat(root, "ghost")

        result = run_watchdog_check(root)

        assert result.unwatched == ["ghost"]


class TestTheWarningLandsWhereTheMistakeIsMade:
    def test_claim_warns_about_an_unregistered_worker(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["claim", "src", "--worker", "ghost"])

        assert result.exit_code == 0  # the claim is real and is kept
        assert "claimed 1 path(s) for ghost" in result.output
        assert "not registered" in result.output
        assert "rite add worker ghost" in result.output

    def test_heartbeat_warns_about_an_unregistered_worker(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["heartbeat", "--worker", "ghost"])

        assert result.exit_code == 0
        assert "heartbeat recorded for 'ghost'" in result.output
        assert "not registered" in result.output

    def test_a_registered_worker_draws_no_warning(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        _register(root, "alpha")
        monkeypatch.chdir(root)

        commands = (
            ["claim", "src", "--worker", "alpha"],
            ["heartbeat", "-w", "alpha"],
        )
        for argv in commands:
            result = CliRunner().invoke(cli, argv)
            assert result.exit_code == 0
            assert "not registered" not in result.output


class TestPoolSlotsAreRegisteredSomewhereElse:
    """A pooled coordinator claims paths under its SLOT name — that is
    what lets `rite pool archive` release what a dead session was
    holding — and no slot ever gets a `workers/<name>/worker.yml`. Its
    register is `.rite/pool.json` and `rite pool status` is what watches
    it, on its own liveness probe.

    Found by driving `rite pool` against real tmux right after the
    unwatched-worker check went in: every slot in a filled pool was
    reported as an unregistered worker, with a suggestion to `rite add
    worker rite-pool-<the whole project path>-0`. A standing false alarm
    on the setup §2.5 recommends is worse than the gap it was closing."""

    def _slot(self, root: Path, name: str) -> None:
        _write_state(
            root,
            [PoolSlot(name=name, created_at=1.0, last_live_at=1.0, worker=name)],
        )

    def test_a_slot_holding_a_claim_is_not_reported_as_unwatched(self, tmp_path: Path):
        root = _project(tmp_path)
        self._slot(root, "rite-pool-acme-0")
        ClaimsLedger(root / ".rite" / "claims.json").claim(
            ["src"], "rite-pool-acme-0", "P-1"
        )

        result = run_watchdog_check(root)

        assert result.unwatched == []
        assert result.needs_attention is False

    def test_claiming_as_a_slot_draws_no_warning(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        self._slot(root, "rite-pool-acme-0")
        monkeypatch.chdir(root)

        result = CliRunner().invoke(
            cli, ["claim", "src", "--worker", "rite-pool-acme-0"]
        )

        assert result.exit_code == 0
        assert "not registered" not in result.output

    def test_a_name_that_is_neither_a_slot_nor_a_manifest_is_still_reported(
        self, tmp_path: Path
    ):
        root = _project(tmp_path)
        self._slot(root, "rite-pool-acme-0")
        write_heartbeat(root, "ghost")

        assert run_watchdog_check(root).unwatched == ["ghost"]

    def test_a_pool_state_that_will_not_parse_does_not_crash_the_check(
        self, tmp_path: Path
    ):
        root = _project(tmp_path)
        (root / ".rite" / "pool.json").write_text("{not json")
        write_heartbeat(root, "ghost")

        assert run_watchdog_check(root).unwatched == ["ghost"]
