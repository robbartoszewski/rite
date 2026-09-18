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

import pytest

from election_harness import open_layer, overlapping_owners, ownership_runs
from kv_backend import start_store

ACTORS = 4
SECONDS = 15.0


def _manager_actor(args):
    """One Manager, ticking until the run ends, logging every moment it
    believed it held the role."""
    spec, name, managers, t0, log_path, hang_after = args

    from election_harness import clock_for
    from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
    from rite_ai.coordination.lease import OwnerLeaseHolder
    from rite_ai.coordination.monitor import ManagerMonitor
    from rite_ai.coordination.schemas import parse_timestamp

    clock = clock_for(t0)
    config = CoordinationConfig(
        managers=list(managers),
        remote="",  # the layer is already built; nothing here re-derives it
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )
    holder = OwnerLeaseHolder(open_layer(spec), name, config, clock=clock)
    monitor = ManagerMonitor(
        holder, heartbeat=HeartbeatConfig(interval_minutes=10, stall_threshold=3)
    )

    rows = []
    # The hang is triggered by PROGRESS, not by the clock. Pausing between
    # two wall-clock instants looked fine and was not: the machine decides
    # how many ticks fit in a second, and under a full suite run four
    # spawned children got so few that the window passed with nothing having
    # happened — no lapse, no turnover, and a test that reported "no
    # turnover" rather than a real failure. Hanging once this Manager has
    # actually held the role means the interesting state is reached first,
    # however slow the machine is.
    stop = time.time() + SECONDS
    while time.time() < stop:
        if len(rows) >= hang_after:
            # Having held the role, this Manager stops renewing and never
            # comes back. Its lease lapses and the next one must take over —
            # what D-17 exists for. EVERY actor does this, so the run is a
            # chain of handovers rather than one.
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


@pytest.fixture(params=["git", "kv"])
def specs(request, tmp_path):
    """One shared store, and a layer description per actor.

    Two stores, because "exactly one Owner" is the claim the whole design
    exists to make and it must not rest on one implementation. The key-value
    store shares nothing with git: separate process, socket, per-key
    compare-and-set, no refs.
    """
    if request.param == "git":
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        yield [
            (
                "rite_ai.coordination.git_backend",
                "GitStateLayer",
                (str(remote), str(tmp_path / f"cache-{i}")),
            )
            for i in range(ACTORS)
        ]
        return
    server, port = start_store()
    try:
        yield [("kv_backend", "KeyValueStateLayer", (port,)) for _ in range(ACTORS)]
    finally:
        server.kill()
        server.wait(timeout=5)


def test_four_managers_four_processes_never_overlap(specs, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()

    t0 = time.time()
    # EVERY Manager hangs once it has held the role, so the run is a chain
    # of handovers. Tying the hang to one named Manager was wrong and flaky:
    # it assumed the highest-priority one would win the first election, and
    # under load whoever STARTS first wins. When that was not m0, nobody ever
    # hung, one Manager held the role for the whole run, and the test
    # reported "no turnover" — a fragile test, not a broken election.
    args = [
        (
            specs[i],
            f"m{i}",
            [f"m{j}" for j in range(ACTORS)],
            t0,
            str(logs / f"{i}.json"),
            2,
        )
        for i in range(ACTORS)
    ]
    with mp.get_context("spawn").Pool(ACTORS) as pool:
        pool.map(_manager_actor, args)

    rows = [r for log in logs.glob("*.json") for r in json.loads(log.read_text())]
    assert rows, "no Manager ever held the role — the run proved nothing"

    runs = ownership_runs(rows)

    ordered = sorted(((start, end, name) for (name, _), (start, end) in runs.items()))
    overlaps = overlapping_owners(runs)
    assert not overlaps, "TWO OWNERS AT ONCE — " + "; ".join(
        f"{a[2]} held until {a[1]:.1f} while {b[2]} held from {b[0]:.1f}"
        for a, b in overlaps
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
