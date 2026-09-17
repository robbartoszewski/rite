"""Four Managers, four processes, one git remote, no split brain.

Everything else in Phase 2 tests one piece: the CAS, the lease boundaries,
the election policy. This tests the claim the whole thing exists to make —
**at no moment do two Managers believe they are Owner** — with real
processes, the real git transport, and no mocks anywhere in the path.

**The clock is accelerated, not faked.** Every process derives its clock
from the same real `time.time()` and the same start point, multiplied by one
factor. So the clocks AGREE across processes (which is what makes the
overlap check meaningful) while a 15-minute lease expires in a few seconds
and the run sees real turnover instead of one uneventful ownership.

**Ownership runs are reconstructed, not asserted moment by moment.** A
Manager believes it is Owner from the tick that promoted it until its own
`expires`; `(manager, acquired)` identifies one run, and the test asserts
that no two runs by DIFFERENT Managers overlap. An overlap is a split brain,
which is the one thing §2.4 promises cannot happen.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path

ACTORS = 4
SECONDS = 15.0
# 15 simulated minutes per 1.5 real seconds, so leases lapse inside the run
# and a slow push costs real simulated minutes — which is the interesting
# case, not an artefact.
FACTOR = 600.0
START = 1789000000.0  # a fixed simulated epoch, shared by every process


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


def _clock(t0: float):
    from datetime import UTC, datetime

    def now():
        return datetime.fromtimestamp(
            START + (time.time() - t0) * FACTOR, tz=UTC
        )

    return now


def _manager_actor(args):
    """One Manager, ticking until the run ends, logging every moment it
    believed it held the role."""
    remote, cache, name, managers, t0, log_path, pause_at = args

    from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
    from rite_ai.coordination.git_backend import GitStateLayer
    from rite_ai.coordination.lease import OwnerLeaseHolder
    from rite_ai.coordination.monitor import ManagerMonitor
    from rite_ai.coordination.schemas import parse_timestamp

    clock = _clock(t0)
    config = CoordinationConfig(
        managers=list(managers),
        remote=remote,
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )
    holder = OwnerLeaseHolder(
        GitStateLayer(remote, cache), name, config, clock=clock
    )
    monitor = ManagerMonitor(
        holder, heartbeat=HeartbeatConfig(interval_minutes=10, stall_threshold=3)
    )

    rows = []
    stop = time.time() + SECONDS
    while time.time() < stop:
        if pause_at and pause_at[0] <= time.time() - t0 <= pause_at[1]:
            # This Manager hangs: no ticks, so no renewals. Its lease lapses
            # and somebody else must take the role — the case the whole
            # lease design exists for (D-17).
            time.sleep(0.05)
            continue
        tick = monitor.tick()
        if not tick.owner:
            continue
        current = holder.current()
        if isinstance(current, tuple):
            lease, _ = current
            if lease is not None and lease.owner == name:
                expires = parse_timestamp(lease.expires)
                rows.append(
                    {
                        "manager": name,
                        "acquired": lease.acquired,
                        "at": clock().timestamp(),
                        "expires": expires.timestamp() if expires else 0.0,
                        "action": tick.action,
                        "problems": tick.problems,
                    }
                )
    Path(log_path).write_text(json.dumps(rows))
    return len(rows)


def test_four_managers_four_processes_never_overlap(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    logs = tmp_path / "logs"
    logs.mkdir()

    t0 = time.time()
    # Two of the four hang for a while, at different times, so the run
    # contains real promotions rather than one uneventful ownership.
    pauses = {1: (3.0, 7.0), 0: (6.0, 10.0)}
    args = [
        (
            str(remote),
            str(tmp_path / f"cache-{i}"),
            f"m{i}",
            [f"m{j}" for j in range(ACTORS)],
            t0,
            str(logs / f"{i}.json"),
            pauses.get(i),
        )
        for i in range(ACTORS)
    ]
    with mp.get_context("spawn").Pool(ACTORS) as pool:
        pool.map(_manager_actor, args)

    rows = [r for log in logs.glob("*.json") for r in json.loads(log.read_text())]
    assert rows, "no Manager ever held the role — the run proved nothing"

    # Reconstruct ownership runs: (manager, acquired) is one run.
    runs: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        key = (row["manager"], row["acquired"])
        span = runs.setdefault(key, [row["at"], row["expires"]])
        span[0] = min(span[0], row["at"])
        span[1] = max(span[1], row["expires"])

    ordered = sorted(
        ((start, end, name) for (name, _), (start, end) in runs.items())
    )
    overlaps = overlapping_owners(runs)
    assert not overlaps, (
        "TWO OWNERS AT ONCE — "
        + "; ".join(
            f"{a[2]} held until {a[1]:.1f} while {b[2]} held from {b[0]:.1f}"
            for a, b in overlaps
        )
    )

    if os.environ.get("RITE_ELECTION_STATS"):
        # Not decoration: a concurrency test that passes tells you nothing
        # unless you can see what it exercised. RITE_ELECTION_STATS=1 prints
        # the ownership runs and every problem the Managers reported.
        print(f"\nrows={len(rows)} runs={len(runs)}")
        for start, end, name in ordered:
            print(f"  {name}: owner {start:.1f} -> {end:.1f}")
        probs = [p for r in rows for p in r["problems"]]
        print(f"problems={len(probs)}")
        for p in sorted(set(probs))[:5]:
            print("   ", p[:110])

    # The run has to have been a real one, or the assertion above is free.
    managers = {name for _, _, name in ordered}
    assert len(managers) >= 2, (
        f"only {managers} ever held the role; no turnover happened, so a "
        "split brain could not have been observed either"
    )


def test_the_overlap_check_can_actually_fail():
    """The calibration, run against the SAME function the real test uses —
    a calibration that exercises a copy of the logic proves nothing about
    the original. An assertion about concurrency that has never been seen
    to fail is a decoration."""
    overlapping = {("m0", "t1"): [0.0, 100.0], ("m1", "t2"): [90.0, 200.0]}
    assert overlapping_owners(overlapping), "a split brain went undetected"

    # A long run containing a later one: what a neighbours-only check misses.
    contained = {
        ("m0", "t1"): [0.0, 500.0],
        ("m0", "t2"): [10.0, 20.0],
        ("m1", "t3"): [300.0, 400.0],
    }
    assert overlapping_owners(contained), "a contained run went undetected"

    clean = {
        ("m0", "t1"): [0.0, 100.0],
        ("m1", "t2"): [101.0, 200.0],
        ("m0", "t3"): [201.0, 300.0],
    }
    assert not overlapping_owners(clean), "a clean handover reported as overlap"
