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


def record_every_write(remote) -> None:
    """Turn on the bare remote's reflog, so every update of the state ref is
    kept in order. Each state write force-pushes a parentless commit, so
    without this the remote holds only the latest lease and the sequence
    that actually landed cannot be reconstructed."""
    import subprocess

    subprocess.run(
        ["git", "-C", str(remote), "config", "core.logAllRefUpdates", "always"],
        check=True,
    )


def lease_history(remote) -> list[dict]:
    """Every lease value that LANDED on the remote, oldest first.

    ⚠ **This is the ground truth the ownership rows are not.** The rows are
    written by each process at tick boundaries, from its own reading of
    events; this is the order of the writes themselves, from the one place
    both processes write to. It needs `record_every_write` before the run."""
    import json
    import subprocess

    listed = subprocess.run(
        ["git", "-C", str(remote), "reflog", "show", "--format=%H", "refs/heads/state"],
        capture_output=True,
        text=True,
    )
    history = []
    for sha in reversed(listed.stdout.split()):
        shown = subprocess.run(
            ["git", "-C", str(remote), "show", f"{sha}:owner-lease.json"],
            capture_output=True,
            text=True,
        )
        if shown.returncode == 0 and shown.stdout.strip():
            history.append(json.loads(shown.stdout))
    return history


def promotions_over_valid_leases(history: list[dict], skew_seconds: float):
    """Every write that named a NEW owner while the previous owner's lease,
    plus the skew margin, had not expired by the new lease's own `acquired`
    stamp. Both stamps come from the same accelerated clock. An empty list
    means no process ever took the role from a lease that still held it."""
    from rite_ai.coordination.schemas import parse_timestamp

    def ts(value):
        parsed = parse_timestamp(value) if value else None
        return parsed.timestamp() if parsed else 0.0

    found = []
    for before, after in zip(history, history[1:]):
        if after.get("owner") == before.get("owner"):
            continue
        early = ts(before.get("expires")) + skew_seconds - ts(after.get("acquired"))
        if early > 0:
            found.append(
                f"{after.get('owner')} acquired at {after.get('acquired')} while "
                f"{before.get('owner')}'s lease ran to {before.get('expires')} "
                f"(+{skew_seconds:.0f}s skew): {early:.1f}s early"
            )
    return found


def open_layer(spec):
    """Build a StateLayer in a CHILD process from a picklable description.

    (module, attribute, args) — the same shape the conformance suite uses,
    for the same reason: a child cannot be handed a live object, and naming
    a backend here would tie the election's proof to one store.
    """
    import importlib

    module, attribute, args = spec
    return getattr(importlib.import_module(module), attribute)(*args)
