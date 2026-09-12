"""Regression tests for the defects found by running rite's shared state
under real concurrent processes.

Multi-worker concurrent operation is what rite is FOR — several Claude
sessions on one machine, coordinating through files in `.rite/`. Nothing
had ever driven that state from more than one process at a time. Three
things broke, all of them silently, all of them reported success:

- a granted claim disappearing from the ledger,
- a queued outbox message being overwritten by another,
- a pool update being overwritten by a writer working from a stale read.

The single cause is one line of reasoning that does not hold:
`write_atomic` makes a write survive a crash, and says nothing at all
about two writers. Where the two got conflated, an `flock` was taken on
the very file that `os.replace` swaps out from under it.

These tests spawn real processes. They are probabilistic by nature —
against the FIXED code they pass deterministically (the lock either
excludes or it does not), and against the code as it was each one failed
in the large majority of rounds. The round counts below are set so that
a broken build passing all of them is a one-in-a-million event, not a
coin flip.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from dataclasses import replace
from pathlib import Path

import pytest

from rite_ai import pool as rite_pool
from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.reporting.outbox import enqueue, list_pending
from rite_ai.state import lock_path_for, locked, write_atomic

# Enough concurrent processes to interleave reliably on a laptop, few
# enough that the whole module stays inside a couple of seconds.
WORKERS = 12
ROUNDS = 10


# `mp.Pool` pickles the callable by qualified name, so these have to be
# module-level functions rather than closures over the test's tmp_path.
def _claim_distinct_path(args: tuple[str, int]) -> tuple[int, bool]:
    ledger_path, index = args
    result = ClaimsLedger(Path(ledger_path)).claim(
        [f"src/module{index}"], f"worker{index}", ticket=f"T-{index}"
    )
    return index, result.ok


def _claim_the_same_path(args: tuple[str, int]) -> tuple[int, bool]:
    ledger_path, index = args
    result = ClaimsLedger(Path(ledger_path)).claim(["src/shared"], f"worker{index}")
    return index, result.ok


def _enqueue_one(args: tuple[str, int]) -> int:
    root, index = args
    enqueue(Path(root), "blocker", {"n": index})
    return index


def _touch_one_pool_slot(args: tuple[str, int]) -> int:
    """What `probe` does per call: read every slot, decide, write them
    all back. The sleep stands in for the work that really sits in that
    window — `probe` runs one `tmux has-session` subprocess PER SLOT, so
    the real gap between read and write is far wider than this."""
    root, index = args
    with rite_pool._locked_state(Path(root)):
        slots = rite_pool._read_state(Path(root))
        time.sleep(0.002)
        slots[index] = replace(slots[index], last_live_at=1000.0 + index)
        rite_pool._write_state(Path(root), slots)
    return index


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    return tmp_path


class TestClaimsLedgerUnderConcurrentProcesses:
    """The claims ledger IS the exclusion guarantee (§5.2). A claim that
    is granted and then silently dropped is not a bookkeeping error: the
    next worker to ask for that path is handed it."""

    def test_every_granted_claim_survives(self, tmp_path: Path):
        """Twelve workers claiming twelve DISTINCT paths at once. Nothing
        contends, so all twelve must be granted and all twelve must still
        be in the ledger.

        Measured before the fix, twenty rounds: ten lost at least one
        claim, and the worst kept seven of twelve. The `flock` was held
        on `claims.json`, and `write_atomic`'s `os.replace` puts a NEW
        inode at that path — so the first successful write orphaned the
        lock and the next process to open the path locked a different
        file and was let straight in."""
        for round_number in range(ROUNDS):
            ledger_path = tmp_path / f"round{round_number}" / "claims.json"
            with mp.Pool(WORKERS) as pool:
                results = pool.map(
                    _claim_distinct_path,
                    [(str(ledger_path), i) for i in range(WORKERS)],
                )

            granted = {f"worker{i}" for i, ok in results if ok}
            assert granted == {f"worker{i}" for i in range(WORKERS)}, (
                f"round {round_number}: a non-overlapping claim was refused"
            )

            recorded = {c.worker for c in ClaimsLedger(ledger_path).list_claims()}
            assert recorded == granted, (
                f"round {round_number}: claims granted but missing from the "
                f"ledger: {sorted(granted - recorded)}"
            )

    def test_only_one_worker_wins_a_contested_path(self, tmp_path: Path):
        """The other half of the guarantee: when everyone wants the same
        path, exactly one may be told yes, and the ledger must agree with
        whoever that was."""
        for round_number in range(ROUNDS):
            ledger_path = tmp_path / f"round{round_number}" / "claims.json"
            with mp.Pool(WORKERS) as pool:
                results = pool.map(
                    _claim_the_same_path,
                    [(str(ledger_path), i) for i in range(WORKERS)],
                )

            winners = [i for i, ok in results if ok]
            assert len(winners) == 1, f"round {round_number}: winners={winners}"

            claims = ClaimsLedger(ledger_path).list_claims()
            holders = {c.worker for c in claims if "src/shared" in c.paths}
            assert holders == {f"worker{winners[0]}"}


class TestOutboxUnderConcurrentProcesses:
    def test_no_queued_message_overwrites_another(self, tmp_path: Path):
        """The outbox is what makes "`stop` must succeed offline" (§9.10)
        true. A lost message is a handover that is never delivered AND
        never retried, because nothing survives to retry from.

        The filename was `{milliseconds}_{kind}.json`. A millisecond is a
        long time for one process and no time at all for twelve: measured
        before the fix, fifteen rounds of twelve concurrent enqueues lost
        messages in FIFTEEN of them, keeping as few as five."""
        for round_number in range(ROUNDS):
            root = _project(tmp_path / f"round{round_number}")
            with mp.Pool(WORKERS) as pool:
                pool.map(_enqueue_one, [(str(root), i) for i in range(WORKERS)])

            pending = list_pending(root)
            assert len(pending) == WORKERS, (
                f"round {round_number}: enqueued {WORKERS}, {len(pending)} on disk"
            )
            assert {m.payload["n"] for m in pending} == set(range(WORKERS))

    def test_delivery_order_still_follows_enqueue_order(self, tmp_path: Path):
        """The discriminators added to the filename go AFTER the
        millisecond, so `sorted(glob(...))` still drains oldest-first.
        Losing that would turn a queue into a bag."""
        root = _project(tmp_path)
        for i in range(5):
            enqueue(root, "handover", {"n": i})
        assert [m.payload["n"] for m in list_pending(root)] == [0, 1, 2, 3, 4]


class TestTheLockPrimitiveItself:
    def test_the_lock_is_not_held_on_the_file_it_protects(self, tmp_path: Path):
        """The whole defect in one assertion. `locked(path)` must lock
        something `write_atomic(path)` does not replace — otherwise the
        first write inside the critical section quietly ends the mutual
        exclusion for everyone who opens the path afterwards."""
        target = tmp_path / "state.json"
        write_atomic(target, "{}")
        before = target.stat().st_ino

        with locked(target):
            write_atomic(target, '{"changed": true}')

        assert target.stat().st_ino != before, (
            "write_atomic is expected to replace the inode — if it stopped "
            "doing so, this test no longer proves anything"
        )
        assert lock_path_for(target) != target
        assert lock_path_for(target).is_file()

    def test_the_lock_file_survives_writes_to_the_data_file(self, tmp_path: Path):
        """A sidecar only excludes while every process opens the SAME
        inode. Writing the data file must never disturb it, and neither
        this module nor anything else may delete it."""
        target = tmp_path / "state.json"
        with locked(target):
            pass
        lock_inode = lock_path_for(target).stat().st_ino

        for i in range(5):
            with locked(target):
                write_atomic(target, json.dumps({"i": i}))

        assert lock_path_for(target).stat().st_ino == lock_inode

    def test_locking_is_reentrant_across_sequential_blocks(self, tmp_path: Path):
        """A released lock must be re-acquirable in the same process —
        the ledger takes it once per operation, many times per run."""
        target = tmp_path / "state.json"
        for _ in range(3):
            with locked(target):
                write_atomic(target, "{}")


@pytest.mark.parametrize("kind", ["claims", "outbox"])
def test_shared_state_is_not_left_locked_after_an_error(tmp_path: Path, kind: str):
    """An exception inside a critical section must still release the
    lock, or one failed `rite claim` wedges every session on the machine
    until the process dies."""
    target = tmp_path / f"{kind}.json"
    with pytest.raises(RuntimeError):
        with locked(target):
            raise RuntimeError("boom")
    # Re-acquiring proves the lock came back. A leaked lock would block
    # here forever rather than fail, so this is a hang-vs-pass test and
    # deliberately has no timeout of its own — pytest's own run bounds it.
    with locked(target):
        write_atomic(target, "{}")


class TestPoolStateUnderConcurrentProcesses:
    def test_no_slot_update_overwrites_another(self, tmp_path: Path):
        """`fill`, `probe` and `archive` are all read-modify-write over
        one shared file, and none of them was serialised. Measured with
        eight concurrent updaters, ten of ten rounds kept two of eight.

        `fill` is the one that costs real money rather than accuracy: its
        critical section spans `tmux new-session` calls, so two fills
        that each read an empty pool each start a full complement, and
        spent quota is not recoverable."""
        slot_count = 8
        for round_number in range(5):
            root = tmp_path / f"round{round_number}"
            (root / ".rite").mkdir(parents=True)
            rite_pool._write_state(
                root,
                [
                    rite_pool.PoolSlot(name=f"s{i}", created_at=1.0, last_live_at=None)
                    for i in range(slot_count)
                ],
            )

            with mp.Pool(slot_count) as workers:
                workers.map(
                    _touch_one_pool_slot,
                    [(str(root), i) for i in range(slot_count)],
                )

            slots = rite_pool._read_state(root)
            updated = [s for s in slots if s.last_live_at is not None]
            assert len(updated) == slot_count, (
                f"round {round_number}: {len(updated)}/{slot_count} updates "
                "survived — a writer overwrote another's read"
            )

    def test_probing_a_bare_directory_still_creates_nothing(self, tmp_path: Path):
        """Serialising `probe` must not be what finally makes it write.
        A stray `.rite/` in a directory that is not a rite project makes
        `rite init` refuse to initialise it, so the lock is skipped
        entirely when there is no `.rite/` — with no state file there is
        nothing for two probes to race over anyway."""
        with rite_pool._maybe_locked_state(tmp_path):
            pass
        assert not (tmp_path / ".rite").exists()
