"""A graceful handover between two real processes over a real git remote.

The expiry path is proven elsewhere (`test_election_across_processes`): an
Owner hangs, its lease lapses, somebody else takes the role. This is the
path that is supposed to happen when NOTHING is wrong — a higher-priority
Manager comes back, asks, and is handed the role without waiting for
anything to expire and without interrupting work.

It is worth its own cross-process test because the handover is TWO writes
(clear the request, release the lease) against a shared ref that the other
process is also writing, and because the thing being handed over is the
role itself: if both Managers act as Owner for even a moment, everything
above this is moot.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path

from election_harness import clock_for, describe, overlapping_owners, ownership_runs

SECONDS = 12.0


def _manager(args):
    """One Manager. `joins_after` holds it back so the lower-priority one
    takes the role first; `yields_role` makes it hand over when asked."""
    remote, cache, name, managers, t0, log_path, joins_after, yields_role = args

    from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
    from rite_ai.coordination.git_backend import GitStateLayer
    from rite_ai.coordination.lease import OwnerLeaseHolder
    from rite_ai.coordination.monitor import ManagerMonitor
    from rite_ai.coordination.schemas import parse_timestamp

    clock = clock_for(t0)
    config = CoordinationConfig(
        managers=list(managers),
        remote=remote,
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )
    holder = OwnerLeaseHolder(GitStateLayer(remote, cache), name, config, clock=clock)
    monitor = ManagerMonitor(
        holder,
        heartbeat=HeartbeatConfig(interval_minutes=10, stall_threshold=3),
        hand_over_when=(lambda: True) if yields_role else None,
    )

    rows: list[dict] = []
    events: list[str] = []
    held: str = ""  # the `acquired` of the lease we currently believe is ours
    # Relative to this child's own start: see the note in the election test.
    own_start = time.time()
    stop = own_start + SECONDS
    while time.time() < stop:
        if time.time() - own_start < joins_after:
            time.sleep(0.05)
            continue
        tick = monitor.tick()
        if tick.action not in ("nothing", "renewed", "not owner"):
            events.append(tick.action)
        if tick.asked_for_promotion:
            events.append("asked")
        if tick.asked_to_hand_over:
            events.append("was asked")
        if tick.action == "handed over" and held:
            # Belief ends HERE, not at the expiry we last renewed to.
            moment = clock().timestamp()
            rows.append(
                {
                    "manager": name,
                    "acquired": held,
                    "at": moment,
                    "expires": moment,
                    "closed": True,
                }
            )
            held = ""
        if not tick.owner:
            continue
        current = holder.current()
        if isinstance(current, tuple):
            lease, _ = current
            if lease is not None and lease.owner == name:
                expires = parse_timestamp(lease.expires)
                held = lease.acquired
                rows.append(
                    {
                        "manager": name,
                        "acquired": lease.acquired,
                        "at": clock().timestamp(),
                        "expires": expires.timestamp() if expires else 0.0,
                    }
                )
    Path(log_path).write_text(json.dumps({"rows": rows, "events": events}))
    return name


def test_a_returning_manager_is_handed_the_role_without_a_lapse(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    logs = tmp_path / "logs"
    logs.mkdir()

    t0 = time.time()
    managers = ["alpha", "beta"]
    args = [
        # beta is lower priority but starts first, so it is Owner by default
        # (§2.4, first bullet) and yields when asked.
        (
            str(remote),
            str(tmp_path / "cb"),
            "beta",
            managers,
            t0,
            str(logs / "beta.json"),
            0.0,
            True,
        ),
        # alpha returns two seconds later and outranks it.
        (
            str(remote),
            str(tmp_path / "ca"),
            "alpha",
            managers,
            t0,
            str(logs / "alpha.json"),
            2.0,
            False,
        ),
    ]
    with mp.get_context("spawn").Pool(2) as pool:
        pool.map(_manager, args)

    loaded = {p.stem: json.loads(p.read_text()) for p in logs.glob("*.json")}
    rows = [r for v in loaded.values() for r in v["rows"]]
    runs = ownership_runs(rows)

    if os.environ.get("RITE_ELECTION_STATS"):
        print("\n" + describe(runs))
        for name, v in loaded.items():
            print(f"  {name}: {v['events']}")

    assert not overlapping_owners(runs), (
        "TWO OWNERS DURING A GRACEFUL HANDOVER — " + describe(runs)
    )

    # The protocol actually ran: beta held it, was asked, and gave it up;
    # alpha asked rather than seizing, and ended up Owner.
    assert "handed over" in loaded["beta"]["events"], loaded["beta"]["events"]
    assert "was asked" in loaded["beta"]["events"]
    assert "asked" in loaded["alpha"]["events"], loaded["alpha"]["events"]
    assert "promoted" in loaded["alpha"]["events"]

    owners = [name for (name, _) in runs]
    assert owners.count("beta") >= 1 and owners.count("alpha") >= 1, owners

    # And it ended in the right place, with the request cleared so no later
    # Owner acts on it again.
    from rite_ai.coordination.demotion import REQUEST_KEY
    from rite_ai.coordination.git_backend import GitStateLayer
    from rite_ai.coordination.lease import LEASE_KEY
    from rite_ai.coordination.promotion import request_from_json
    from rite_ai.coordination.schemas import lease_from_json

    final = GitStateLayer(str(remote), tmp_path / "check")
    assert lease_from_json(final.read_state(LEASE_KEY).value.decode()).owner == "alpha"
    assert not request_from_json(final.read_state(REQUEST_KEY).value.decode()).requester
