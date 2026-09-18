"""Calibration of the synchronised-burst harness (P2-0c).

A concurrency harness that has never been shown to fail against a broken
implementation proves nothing — `test_blast_radius_concurrent.py` records that
its first version reported zero violations against a knowingly broken lock.
So the harness is accepted only if it does BOTH:

- catches lost updates on a read-modify-write counter with no lock, and
- reports none on the identical counter under `rite_ai.state.locked()`.

The first proves sensitivity; the second proves it does not cry wolf. Either
alone is not calibration.
"""

from __future__ import annotations

from pathlib import Path

from burst import align, deadline, run_actors


def _counter_actor(args):
    """Increment a shared file counter as fast as synchronised bursts allow.
    Returns how many increments THIS actor believes it made."""
    path, use_lock, seconds = args
    import contextlib

    from rite_ai.state import locked, write_atomic

    target = Path(path)
    guard = (lambda: locked(target)) if use_lock else contextlib.nullcontext
    stop = deadline(seconds)
    made = 0
    while __import__("time").time() < stop:
        align()
        with guard():
            current = int(target.read_text() or "0")
            write_atomic(target, str(current + 1))
        made += 1
    return made


def _run(tmp_path: Path, use_lock: bool, actors: int = 6, seconds: float = 1.5):
    counter = tmp_path / "counter"
    counter.write_text("0")
    made = run_actors(_counter_actor, [(str(counter), use_lock, seconds)] * actors)
    return sum(made), int(counter.read_text())


def test_the_harness_catches_lost_updates_without_a_lock(tmp_path):
    """SENSITIVITY. If this ever passes with lost == 0, the harness has
    stopped producing real collisions and every test built on it is void."""
    attempted, recorded = _run(tmp_path, use_lock=False)
    assert attempted > 200, f"harness barely ran ({attempted}) — not a test"
    assert recorded < attempted, (
        f"no lost updates across {attempted} unlocked increments — the harness "
        "is not producing collisions, so it cannot catch a broken lock"
    )


def test_the_harness_reports_nothing_lost_under_a_real_lock(tmp_path):
    """SPECIFICITY. The same load, correctly locked, must lose nothing."""
    attempted, recorded = _run(tmp_path, use_lock=True)
    assert attempted > 50, f"harness barely ran ({attempted}) — not a test"
    assert recorded == attempted, (
        f"{attempted - recorded} of {attempted} locked increments lost"
    )
