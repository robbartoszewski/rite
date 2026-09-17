"""Shared machinery for the cross-process election tests.

**The clock is accelerated, not faked.** Every process derives it from the
same real `time.time()` and the same start point, multiplied by one factor,
so the clocks AGREE across processes — which is what makes an overlap check
mean anything — while a 15-minute lease lapses in about 1.5 seconds and a
run sees real turnover instead of one uneventful ownership.
"""

from __future__ import annotations

import time

FACTOR = 600.0
START = 1789000000.0  # a fixed simulated epoch, shared by every process


def clock_for(t0: float):
    from datetime import UTC, datetime

    def now():
        return datetime.fromtimestamp(START + (time.time() - t0) * FACTOR, tz=UTC)

    return now


def ownership_runs(rows: list[dict]) -> dict[tuple[str, str], list[float]]:
    """`(manager, acquired)` identifies one run: a Manager believes it is
    Owner from the tick that took the lease until that lease's own expiry.

    **A run can end EARLY, and getting that wrong invents a split brain.**
    An Owner that hands over deliberately stops believing it holds the role
    at that moment, not at the expiry it last renewed to — so a row marked
    `closed` truncates the run there. Without this, every graceful handover
    reads as an overlap: the outgoing Owner appears to hold a lease it gave
    up minutes (of simulated time) earlier. Found by this test failing on
    the graceful path while the crash path was clean.
    """
    runs: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        key = (row["manager"], row["acquired"])
        span = runs.setdefault(key, [row["at"], row["expires"]])
        span[0] = min(span[0], row["at"])
        span[1] = max(span[1], row["expires"])
    for row in rows:
        if row.get("closed"):
            span = runs.get((row["manager"], row["acquired"]))
            if span is not None:
                span[1] = min(span[1], row["at"])
    return runs


def overlapping_owners(runs: dict[tuple[str, str], list[float]]):
    """Every pair of ownership runs by DIFFERENT Managers that overlap in
    time. Compared pairwise rather than between neighbours: one long run can
    contain several later ones, and a neighbours-only check walks straight
    past that."""
    spans = [(start, end, name) for (name, _), (start, end) in runs.items()]
    found = []
    for i, (start_a, end_a, name_a) in enumerate(spans):
        for start_b, end_b, name_b in spans[i + 1 :]:
            if name_a == name_b:
                continue
            if start_a < end_b and start_b < end_a:
                found.append(((start_a, end_a, name_a), (start_b, end_b, name_b)))
    return found


def describe(runs) -> str:
    return "; ".join(
        f"{name} {start:.1f}->{end:.1f}"
        for (name, _), (start, end) in sorted(runs.items())
    )
