"""The blind window between dispatching and being able to see it (L-3).

The plan asked for "a dispatch record written before any spawn". The Owner
running the live dogfood showed why a record of RUNNING sessions would be the
wrong thing: it answers "both workers free" and "neither is free" correctly,
repeatedly, keeping no record at all. It observes — claims, dirty checkouts,
live heartbeats — and observation cannot drift, because it reads the thing
rather than a note about it.

So what is recorded is only what cannot be observed: the seconds between
`start_worker` returning and the sandbox becoming visible. These tests are
about that record staying small, staying honest, and never quietly holding a
Worker back.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.loop.intents import (
    BLIND_SECONDS,
    clear_intent,
    read_intents,
    reconcile,
    record_intent,
)

NOW = 1_759_000_000.0


def _root(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir(parents=True)
    return tmp_path


def _alive(_pid):
    return True


def _dead(_pid):
    return False


def test_an_intent_is_written_before_anything_could_see_it(tmp_path):
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)

    (intent,) = read_intents(root)
    assert intent.worker == "alpha" and intent.ticket == "BEN-1"
    assert intent.pid  # so a crash between record and spawn is detectable


def test_a_young_invisible_dispatch_holds_the_worker(tmp_path):
    """The whole point: observation says free about a Worker a session is
    booting into, and a 120s cycle would dispatch it again."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)

    settled = reconcile(root, observably_busy=set(), now=NOW + 5, is_running=_alive)

    assert [i.worker for i in settled.holding] == ["alpha"]
    assert not settled.lost


def test_observation_catching_up_retires_the_record(tmp_path):
    """Observation is the better witness; this file must never outlive it."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)

    settled = reconcile(
        root, observably_busy={"alpha"}, now=NOW + 20, is_running=_alive
    )

    assert [i.worker for i in settled.confirmed] == ["alpha"]
    assert read_intents(root) == []


def test_an_old_invisible_dispatch_is_a_problem_not_a_quiet_subtraction(tmp_path):
    """A leaked intent that silently held a Worker back would be exactly the
    drift this design refused a ledger to avoid."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)

    settled = reconcile(
        root, observably_busy=set(), now=NOW + BLIND_SECONDS + 1, is_running=_alive
    )

    assert [i.worker for i in settled.lost] == ["alpha"]
    assert settled.problems and "nothing ever appeared" in settled.problems[0]
    assert not settled.holding
    assert read_intents(root) == []


def test_a_crash_between_recording_and_spawning_is_caught(tmp_path):
    """The case the record exists for: the process that meant to spawn is
    gone and nothing appeared."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)

    settled = reconcile(root, observably_busy=set(), now=NOW + 5, is_running=_dead)

    assert [i.worker for i in settled.lost] == ["alpha"]


def test_the_problem_is_aged_against_the_reconciling_clock(tmp_path):
    """Not against whenever somebody reads it — a report whose number moves
    while being read is not evidence."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)

    settled = reconcile(root, observably_busy=set(), now=NOW + 600, is_running=_alive)

    assert "10m ago" in settled.problems[0]


def test_a_refused_spawn_releases_the_worker_at_once(tmp_path):
    """A `yoloai new` that errors immediately is not a blind window. Leaving
    the intent would hold a Worker back for three minutes over nothing."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)
    clear_intent(root, "alpha")

    assert read_intents(root) == []


def test_one_intent_per_worker(tmp_path):
    """A second dispatch to the same Worker replaces the first rather than
    stacking — two entries would hold it back twice as long."""
    root = _root(tmp_path)
    record_intent(root, "alpha", "BEN-1", now=NOW)
    record_intent(root, "alpha", "BEN-2", now=NOW + 1)

    (intent,) = read_intents(root)
    assert intent.ticket == "BEN-2"


def test_an_unreadable_record_reads_as_empty_rather_than_stopping_the_fleet(tmp_path):
    """The unusual direction for this codebase, and deliberate: this file only
    ever REMOVES capacity, so failing closed would stop a fleet over a corrupt
    scratch file. The claims ledger, which decides whether two Workers touch
    one path, refuses instead."""
    root = _root(tmp_path)
    (root / ".rite" / "dispatch-intents.json").write_text("{ truncated")

    assert read_intents(root) == []


def test_nothing_recorded_is_not_a_problem(tmp_path):
    root = _root(tmp_path)
    settled = reconcile(root, observably_busy=set(), now=NOW, is_running=_alive)
    assert settled.problems == [] and settled.holding == []
