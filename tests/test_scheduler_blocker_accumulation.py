"""Regression tests for what a real launchd agent did to the outbox over
an hour of ticking.

The scheduler tick records a stalled worker as a `blocker` message so a
human or a later session sees it on next contact, and it deduplicates so
that "a stall that persists across ticks is one standing problem, not one
per tick". The dedup compared the recorded DETAIL — and the detail
contained the elapsed seconds, which is larger on every tick. So the
comparison was never equal, and the tick queued a fresh blocker every
time.

Measured under a launchd agent at one tick a minute against one silent
worker: eleven ticks, eleven blocker files. It compounds, because the
watchdog reads the outbox back and reports each pending blocker as a
reason of its own — so tick N wrote N lines to `scheduler.log`, and the
log grew quadratically while the disk filled with duplicate records of
one fact. That is the unattended disk-filling failure a comment in
`rite_ai.scheduler` already claimed to have fixed; the doubling it
described was fixed and the accumulation underneath it was not.

Nothing removed a blocker either. `flush_outbox`'s delivery callback
handles `handover` and `handover-label` and returns False for everything
else, so a stall blocker outlived the stall that caused it: after the
worker came back, `needs_attention` stayed true and the log kept
reporting it, for the life of the project.

Neither is visible in a single mocked tick. Both need a tick that runs
again, over a condition that persists and then stops.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rite_ai.reporting.heartbeat import write_heartbeat
from rite_ai.reporting.outbox import enqueue, list_pending
from rite_ai.scheduler import run_tick


def _project(tmp_path: Path, workers: tuple[str, ...] = ("alpha",)) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "heartbeat:\n  interval_minutes: 1\n  stall_threshold: 1\n"
    )
    for worker in workers:
        manifest = tmp_path / "workers" / worker / "worker.yml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(f"worker:\n  name: {worker}\n")
    return tmp_path


def _beat(root: Path, worker: str, seconds_ago: float, ticket: str = "") -> None:
    """A heartbeat that happened `seconds_ago`. Ageing it further between
    ticks is what really happens while a worker stays silent, and is what
    made the old dedup miss."""
    write_heartbeat(root, worker, ticket=ticket)
    path = root / ".rite" / "heartbeats" / f"{worker}.json"
    record = json.loads(path.read_text())
    record["timestamp"] = time.time() - seconds_ago
    path.write_text(json.dumps(record))


def _blockers(root: Path) -> list[dict]:
    return [m.payload for m in list_pending(root) if m.kind == "blocker"]


class TestAPersistingStallIsRecordedOnce:
    def test_ticking_over_the_same_stall_queues_one_blocker(self, tmp_path: Path):
        root = _project(tmp_path)

        for seconds_silent in (300, 360, 420, 480, 540):
            _beat(root, "alpha", seconds_silent, ticket="RW-1")
            result = run_tick(root)
            assert result.ok

        blockers = _blockers(root)
        assert len(blockers) == 1, (
            f"one standing stall, {len(blockers)} records of it — the dedup "
            "key moved between ticks"
        )
        assert blockers[0]["key"] == "stall:alpha"

    def test_the_recorded_detail_does_not_move_while_the_stall_does(
        self, tmp_path: Path
    ):
        """The recorded fact is WHEN the last heartbeat was, not how long
        ago — the same information, stated so that it stops changing once
        the worker goes quiet. How long ago belongs in the log, which is
        a time series; the outbox holds standing conditions."""
        root = _project(tmp_path)

        _beat(root, "alpha", 300)
        run_tick(root)
        first = _blockers(root)[0]["detail"]

        _beat(root, "alpha", 900)
        run_tick(root)
        second = _blockers(root)[0]["detail"]

        assert first == second
        assert "since last heartbeat" not in first
        assert "last heartbeat" in first

    def test_the_log_does_not_grow_a_line_per_tick(self, tmp_path: Path):
        """The compounding half. The watchdog reads the outbox back, so
        every duplicate blocker became another reason reported on every
        subsequent tick."""
        root = _project(tmp_path)

        line_counts = []
        for seconds_silent in (300, 360, 420, 480):
            _beat(root, "alpha", seconds_silent)
            line_counts.append(len(run_tick(root).messages))

        assert len(set(line_counts)) == 1, (
            f"tick output grew across ticks: {line_counts}"
        )

    def test_two_stalled_workers_get_one_record_each(self, tmp_path: Path):
        """Keyed per worker, not per stall SET — otherwise a second
        worker going quiet changes the combined string and re-queues the
        first worker's problem alongside it."""
        root = _project(tmp_path, workers=("alpha", "beta"))

        _beat(root, "alpha", 300)
        run_tick(root)
        _beat(root, "alpha", 360)
        _beat(root, "beta", 300)
        run_tick(root)

        keys = sorted(b["key"] for b in _blockers(root))
        assert keys == ["stall:alpha", "stall:beta"]


class TestARecoveredWorkersBlockerIsRetracted:
    def test_a_blocker_does_not_outlive_the_stall_that_caused_it(self, tmp_path: Path):
        root = _project(tmp_path)
        _beat(root, "alpha", 300)
        run_tick(root)
        assert len(_blockers(root)) == 1

        write_heartbeat(root, "alpha")  # the worker came back
        result = run_tick(root)

        assert _blockers(root) == []
        assert result.needs_attention is False

    def test_recovering_one_worker_leaves_the_other_recorded(self, tmp_path: Path):
        root = _project(tmp_path, workers=("alpha", "beta"))
        _beat(root, "alpha", 300)
        _beat(root, "beta", 300)
        run_tick(root)
        assert len(_blockers(root)) == 2

        write_heartbeat(root, "alpha")
        _beat(root, "beta", 400)
        run_tick(root)

        assert [b["key"] for b in _blockers(root)] == ["stall:beta"]

    def test_the_pile_an_older_version_left_behind_is_cleaned_up(self, tmp_path: Path):
        """An upgrade meets an outbox already holding one unkeyed blocker
        per tick from however long the old scheduler ran. They are the
        shape this module wrote, they name a worker, and they are
        recognised and retracted rather than left to be reported
        forever."""
        root = _project(tmp_path)
        for _ in range(5):
            enqueue(
                root,
                "blocker",
                {
                    "detail": "watchdog: worker 'alpha' stalled — 600s since last "
                    "heartbeat (ticket RW-1)"
                },
            )
        assert len(_blockers(root)) == 5

        write_heartbeat(root, "alpha")  # not stalled now
        run_tick(root)

        assert _blockers(root) == []

    def test_a_legacy_blocker_for_a_still_stalled_worker_is_replaced_not_kept(
        self, tmp_path: Path
    ):
        """Retracted and re-recorded under a key, rather than left beside
        the keyed one — two records of one stall is the defect."""
        root = _project(tmp_path)
        enqueue(
            root,
            "blocker",
            {"detail": "watchdog: worker 'alpha' stalled — 600s since last heartbeat"},
        )

        _beat(root, "alpha", 700)
        run_tick(root)

        blockers = _blockers(root)
        assert len(blockers) == 1
        assert blockers[0]["key"] == "stall:alpha"

    def test_an_unrelated_blocker_is_never_touched(self, tmp_path: Path):
        """Retraction is scoped to stall records this module wrote. A
        blocker queued by anything else is somebody else's to clear."""
        root = _project(tmp_path)
        enqueue(root, "blocker", {"detail": "disk is full"})

        write_heartbeat(root, "alpha")
        run_tick(root)

        assert [b["detail"] for b in _blockers(root)] == ["disk is full"]
