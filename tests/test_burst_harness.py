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
    Returns how many increments THIS actor believes it made.

    Until ENOUGH has happened or the cap is reached — not "for N seconds".
    This ran for a fixed 1.5s and then required more than 200 increments to
    consider itself meaningful, which is a floor on the MACHINE rather than
    on the harness: a shared CI runner does a fraction of the work a laptop
    does in the same wall clock. Measured — 10 of 10 runs clear it here,
    and one Linux run managed 62, failing with "harness barely ran (62) —
    not a test" without ever evaluating the property.

    `_cas_actor` in the conformance suite already loops this way and says
    why in as many words. This is the same lesson, in the file whose job is
    to calibrate the harness those tests rest on.
    """
    path, use_lock, seconds, enough = args
    import contextlib

    from rite_ai.state import locked, write_atomic

    target = Path(path)
    guard = (lambda: locked(target)) if use_lock else contextlib.nullcontext
    stop = deadline(seconds)
    made = 0
    while __import__("time").time() < stop and made < enough:
        align()
        with guard():
            current = int(target.read_text() or "0")
            write_atomic(target, str(current + 1))
        made += 1
    return made


def _run(
    tmp_path: Path,
    use_lock: bool,
    actors: int = 6,
    enough_each: int = 40,
    seconds: float = 30.0,
):
    """`seconds` is a CAP, not a duration: actors stop as soon as they have
    done `enough_each`, so a fast machine finishes in well under a second
    and a slow one is given room rather than a failure. It is still bounded,
    because a harness that cannot finish is a result too — just a different
    one from a property that does not hold."""
    counter = tmp_path / "counter"
    counter.write_text("0")
    made = run_actors(
        _counter_actor, [(str(counter), use_lock, seconds, enough_each)] * actors
    )
    return sum(made), int(counter.read_text())


def test_the_harness_catches_lost_updates_without_a_lock(tmp_path):
    """SENSITIVITY. If this ever passes with lost == 0, the harness has
    stopped producing real collisions and every test built on it is void."""
    attempted, recorded = _run(tmp_path, use_lock=False)
    # Reached by construction now, so this fires only if the cap ran out —
    # a machine too loaded to finish, which is a different finding from the
    # property failing and should not read like one.
    assert attempted > 200, (
        f"the harness did not finish: {attempted} increments before the cap. "
        "That is a machine that could not run it, not a property that failed"
    )
    assert recorded < attempted, (
        f"no lost updates across {attempted} unlocked increments — the harness "
        "is not producing collisions, so it cannot catch a broken lock"
    )


def test_the_harness_reports_nothing_lost_under_a_real_lock(tmp_path):
    """SPECIFICITY. The same load, correctly locked, must lose nothing."""
    attempted, recorded = _run(tmp_path, use_lock=True, enough_each=12)
    assert attempted > 50, (
        f"the harness did not finish: {attempted} increments before the cap. "
        "That is a machine that could not run it, not a property that failed"
    )
    assert recorded == attempted, (
        f"{attempted - recorded} of {attempted} locked increments lost"
    )
