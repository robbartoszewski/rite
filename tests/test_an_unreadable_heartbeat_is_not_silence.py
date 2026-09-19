"""A heartbeat nobody can read is not a worker that never beat (EXC-4).

`read_heartbeat` answered `None` for four different things — absent,
corrupt, malformed, unreadable — and `detect_stalls` turned `None` into
`seconds_silent=inf`, which both renderers print as "no heartbeat ever
recorded". So a file with a permission problem produced the most alarming
statement the tool can make about a worker, asserted about something
nobody managed to open. Maximally stalled is the opposite of the safe
answer for "I cannot tell".

`worker_sandbox_status` already had the right shape — `known=False` — and
this brings the heartbeat reader to it.

Worse, an `OSError` was not caught at all. `detect_stalls` runs inside
`rite status` and inside the scheduler tick, so one unreadable file took
down a command that exists to report trouble.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.reporting.heartbeat import (
    detect_stalls,
    not_started,
    read_heartbeat,
    read_heartbeat_status,
    write_heartbeat,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite" / "heartbeats").mkdir(parents=True)
    return tmp_path


def _beat_file(root: Path, worker: str) -> Path:
    return root / ".rite" / "heartbeats" / f"{worker}.json"


def _claim(root: Path, worker: str) -> None:
    """Make the worker STARTED. Stall detection skips a worker that has
    neither beaten nor claimed anything, which is right — there is nothing
    to be late for."""
    import json

    (root / ".rite" / "claims.json").write_text(
        json.dumps(
            [{"paths": ["x"], "worker": worker, "ticket": "T-1", "timestamp": 0}]
        )
    )


class TestTheThreeStatesAreThree:
    def test_never_beat_is_known(self, project):
        status = read_heartbeat_status(project, "w1")

        assert status.known and not status.beat

    def test_a_real_beat_is_known(self, project):
        write_heartbeat(project, "w1")

        status = read_heartbeat_status(project, "w1")

        assert status.known and status.beat

    def test_corrupt_json_is_not_knowable(self, project):
        """THE DEFECT."""
        _beat_file(project, "w1").write_text("{not json at all")

        status = read_heartbeat_status(project, "w1")

        assert not status.known
        assert not status.beat
        assert "JSON" in status.detail

    def test_a_non_numeric_timestamp_is_not_knowable(self, project):
        """It would otherwise reach arithmetic: `now - hb.timestamp`."""
        _beat_file(project, "w1").write_text('{"timestamp": "yesterday"}')

        status = read_heartbeat_status(project, "w1")

        assert not status.known

    def test_an_unopenable_file_does_not_raise(self, project, monkeypatch):
        """`OSError` was uncaught, and this runs inside `rite status` and
        the scheduler tick."""
        _beat_file(project, "w1").write_text("{}")
        real = Path.read_text

        def deny(self, *a, **k):
            if self.name == "w1.json":
                raise PermissionError(13, "Permission denied")
            return real(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", deny)

        status = read_heartbeat_status(project, "w1")

        assert not status.known
        assert "unreadable" in status.detail


class TestStallDetectionSaysWhichItIs:
    def test_an_unreadable_beat_is_not_reported_as_never_beating(self, project):
        """`seconds_silent=inf` is what both renderers turn into "no
        heartbeat ever recorded", so it must mean only that."""
        _claim(project, "w1")
        _beat_file(project, "w1").write_text("{not json")

        (report,) = detect_stalls(project, ["w1"])

        assert report.known is False
        assert report.seconds_silent != float("inf")

    def test_a_worker_that_never_beat_still_says_so(self, project):
        """The difference — without it, the test above passes on a build
        that stopped reporting anything at all.

        It needs a CLAIM: a worker with neither a heartbeat nor a claim has
        not started, and skipping those is correct and long-standing."""
        _claim(project, "w1")

        (report,) = detect_stalls(project, ["w1"])

        assert report.known is True
        assert report.seconds_silent == float("inf")

    def test_an_unreadable_beat_is_not_mistaken_for_unstarted(self, project):
        """`not_started` keyed on `read_heartbeat(...) is None` too, and a
        worker counted as unstarted is skipped by stall detection — silence
        about the one worker there is a question about."""
        _beat_file(project, "w1").write_text("{not json")

        assert not_started(project, ["w1"]) == []
        assert detect_stalls(project, ["w1"]) != []


class TestTheReportDoesNotAssertSilence:
    def test_the_watchdog_says_it_could_not_read_it(self, project):
        """Through `run_watchdog_check`, which is what the scheduler tick
        calls, rather than a helper — the line a human reads at 3am is the
        thing under test."""
        from rite_ai.watchdog import run_watchdog_check

        (project / "workers" / "w1").mkdir(parents=True)
        (project / "workers" / "w1" / "worker.yml").write_text("worker:\n  name: w1\n")
        (project / ".rite" / "brief.yaml").write_text(
            "project:\n  name: t\n  role: owner\n"
        )
        (project / ".rite" / "modules.yaml").write_text("modules: {}\n")
        _beat_file(project, "w1").write_text("{not json")

        reasons = " ".join(run_watchdog_check(project).reasons)

        assert "no heartbeat ever recorded" not in reasons
        assert "could not be read" in reasons or "not JSON" in reasons


def test_the_original_contract_is_unchanged(project):
    """Two callers live in files another session holds, so the old
    signature has to keep working exactly — they inherit the OSError fix
    without being edited."""
    assert read_heartbeat(project, "w1") is None
    write_heartbeat(project, "w1")
    assert read_heartbeat(project, "w1") is not None
