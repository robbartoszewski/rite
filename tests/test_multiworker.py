"""Regression tests for defects found by driving several workers at once.

The concurrency work in `test_concurrency.py` covered the shared state that
two processes MUTATE at the same time — the claims ledger, the outbox, the
pool. This file covers what that pass did not reach: state that is shared
between workers without any race being involved, and the scheduler lock
meeting real worker load for the first time.

The handover defect here needs no concurrency at all to reproduce, only
more than one worker. That is why the whole suite missed it: every existing
handover test writes one snapshot from one caller, and a single-writer test
of a single-writer file passes whatever the file is keyed by.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.handover import read_snapshot, read_snapshots, write_snapshot
from rite_ai.lifecycle import perform_handover


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    return tmp_path


class TestEveryWorkerKeepsItsOwnHandover:
    """`.rite/handover.json` was one unkeyed file, and `perform_handover`
    is called per WORKER. Three workers stopping left one snapshot:

        $ rite handover show
        ticket:     RW-303          <- carol's; alice's and bob's are gone
        progress:   handover: clean shutdown

    The record had no `worker` field either, so nothing said whose state it
    was. A fresh session reads this to reconstruct where work stands
    (§9.10.1) and saw one arbitrary worker with no sign of the others.
    """

    def test_three_workers_handing_over_keep_three_snapshots(self, tmp_path: Path):
        root = _project(tmp_path)
        for worker, ticket in (
            ("alice", "RW-101"),
            ("bob", "RW-202"),
            ("carol", "RW-303"),
        ):
            write_snapshot(root, ticket=ticket, worker=worker)

        snapshots = read_snapshots(root)

        assert {s.worker for s in snapshots} == {"alice", "bob", "carol"}
        assert {s.ticket for s in snapshots} == {"RW-101", "RW-202", "RW-303"}

    def test_a_snapshot_records_whose_it_is(self, tmp_path: Path):
        root = _project(tmp_path)
        write_snapshot(root, ticket="RW-1", worker="alice")
        assert read_snapshots(root)[0].worker == "alice"

    def test_one_workers_write_does_not_erase_another(self, tmp_path: Path):
        root = _project(tmp_path)
        write_snapshot(root, ticket="RW-1", progress="first", worker="alice")
        write_snapshot(root, ticket="RW-2", progress="second", worker="bob")

        alice = [s for s in read_snapshots(root) if s.worker == "alice"]
        assert alice and alice[0].ticket == "RW-1", "bob's snapshot overwrote alice's"

    def test_perform_handover_keys_by_the_worker_it_was_called_for(
        self, tmp_path: Path
    ):
        """The real path: `rite stop --worker X` goes through here."""
        root = _project(tmp_path)
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/a.py"], "alice", ticket="RW-101")
        ledger.claim(["src/b.py"], "bob", ticket="RW-202")

        perform_handover(root, worker="alice", reason="clean shutdown")
        perform_handover(root, worker="bob", reason="clean shutdown")

        by_worker = {s.worker: s.ticket for s in read_snapshots(root)}
        assert by_worker == {"alice": "RW-101", "bob": "RW-202"}

    def test_a_legacy_unkeyed_snapshot_is_still_read(self, tmp_path: Path):
        """An upgrade must not look like the snapshot vanished."""
        root = _project(tmp_path)
        (root / ".rite" / "handover.json").write_text(
            json.dumps({"ticket": "RW-OLD", "progress": "from an older rite"})
        )
        snapshots = read_snapshots(root)
        assert [s.ticket for s in snapshots] == ["RW-OLD"]

    def test_read_snapshot_still_answers_for_one_session(self, tmp_path: Path):
        root = _project(tmp_path)
        write_snapshot(root, ticket="RW-1", worker="alice")
        assert read_snapshot(root).ticket == "RW-1"

    def test_no_snapshots_is_empty_not_an_error(self, tmp_path: Path):
        assert read_snapshots(_project(tmp_path)) == []


# --- concurrent writes, real processes ----------------------------------


def _write_one(args: tuple[str, int]) -> int:
    root, index = args
    write_snapshot(Path(root), ticket=f"RW-{index}", worker=f"w{index}")
    return index


class TestConcurrentHandoverWrites:
    def test_twelve_workers_writing_at_once_keep_twelve_snapshots(self, tmp_path: Path):
        root = _project(tmp_path)
        n = 12
        with mp.get_context("fork").Pool(n) as pool:
            pool.map(_write_one, [(str(root), i) for i in range(n)])

        snapshots = read_snapshots(root)
        assert len(snapshots) == n, (
            f"{len(snapshots)} of {n} survived concurrent writes"
        )
        assert {s.worker for s in snapshots} == {f"w{i}" for i in range(n)}


# --- the scheduler tick lock, meeting worker load -----------------------


def _tick_once(root: str) -> int:
    from rite_ai.scheduler import run_tick

    result = run_tick(Path(root))
    return sum(m.count("handed over worker") for m in result.messages)


class TestConcurrentTicksAtAScheduleBoundary:
    """The tick's boundary branch hands over every active worker and only
    then records the new count, so ticks that all read the pre-transition
    count each believe the transition is theirs to perform. Measured with
    the tick lock removed: sixteen concurrent ticks produced 48 handover
    records for three workers — sixteen apiece. Against a real ticket
    backend that is sixteen comments and sixteen label writes per worker.

    This is the lock and multi-worker load meeting; each was covered
    separately and neither test could see this.
    """

    def _project_at_a_boundary(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        (root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\n"
            "schedule:\n  timezone: UTC\n"
            "  windows:\n    - hours: '00:00-24:00'\n      workers: 0\n"
        )
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        for w in ("alice", "bob", "carol"):
            (root / "workers" / w).mkdir(parents=True, exist_ok=True)
            (root / "workers" / w / "worker.yml").write_text(
                f'worker:\n  name: "{w}"\n  modules: []\n'
            )
            ledger.claim([f"src/{w}.py"], w, ticket=f"RW-{w}")
        # The previous tick saw three workers; the schedule now says zero.
        (root / ".rite" / "schedule-state.json").write_text("3")
        return root

    def test_each_worker_is_handed_over_exactly_once(self, tmp_path: Path):
        from rite_ai.reporting.outbox import list_pending

        root = self._project_at_a_boundary(tmp_path)
        n = 16
        with mp.get_context("fork").Pool(n) as pool:
            pool.map(_tick_once, [str(root)] * n)

        handovers = [m for m in list_pending(root) if m.kind.startswith("handover")]
        per_worker: dict[str, int] = {}
        for m in handovers:
            w = m.payload.get("worker", "?")
            per_worker[w] = per_worker.get(w, 0) + 1

        assert per_worker == {"alice": 1, "bob": 1, "carol": 1}, (
            f"{len(handovers)} handover records for three workers: {per_worker}"
        )

    def test_the_claims_are_released_once_and_stay_released(self, tmp_path: Path):
        root = self._project_at_a_boundary(tmp_path)
        with mp.get_context("fork").Pool(8) as pool:
            pool.map(_tick_once, [str(root)] * 8)
        assert ClaimsLedger(root / ".rite" / "claims.json").list_claims() == []


class TestUpgradingFromAnUnkeyedSnapshot:
    """A project written by an older rite carries `.rite/handover.json`
    with no worker field. Reading it unconditionally showed the same
    handover twice once anything keyed existed:

        $ rite handover show          # after upgrading, then `rite stop`
        worker:     legacy
        ticket:     RW-OLD
        worker:     (unnamed session)
        ticket:     RW-OLD            <- the superseded file, read again

    The unkeyed record cannot be told apart from the keyed copy of itself,
    so it is read only while nothing keyed exists.
    """

    def _legacy(self, root: Path, ticket: str = "RW-OLD") -> None:
        (root / ".rite" / "handover.json").write_text(
            json.dumps({"ticket": ticket, "progress": "from an older rite"})
        )

    def test_a_legacy_snapshot_is_read_when_nothing_keyed_exists(self, tmp_path: Path):
        root = _project(tmp_path)
        self._legacy(root)
        assert [s.ticket for s in read_snapshots(root)] == ["RW-OLD"]

    def test_it_stops_being_read_once_a_keyed_snapshot_exists(self, tmp_path: Path):
        root = _project(tmp_path)
        self._legacy(root)
        write_snapshot(root, ticket="RW-NEW", worker="legacy")

        snapshots = read_snapshots(root)

        assert [s.worker for s in snapshots] == ["legacy"], (
            f"the superseded unkeyed file was read again: {snapshots}"
        )

    def test_the_legacy_file_is_not_deleted(self, tmp_path: Path):
        """Superseding it is this module's call; deleting a user's state
        file is not."""
        root = _project(tmp_path)
        self._legacy(root)
        write_snapshot(root, ticket="RW-NEW", worker="legacy")
        assert (root / ".rite" / "handover.json").is_file()
