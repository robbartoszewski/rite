"""Synchronised-burst concurrency harness (P2-0c).

The fixture P2-1c and P2-2d need, built once. Modelled on
`test_blast_radius_concurrent.py`, whose calibration is the reason this exists:
workers looping on independent sleeps desynchronise within a second and never
revisit the microsecond window a race lives in — the first version of that soak
reported ZERO violations against a knowingly broken lock. So:

- **Real processes**, not threads. A flock race, a git push race and a
  read-modify-write race all live between processes.
- **Repeated synchronised bursts.** Every actor busy-waits to the same
  wall-clock instant (`align`) on every iteration, so the critical window is
  revisited thousands of times rather than once. No IPC: each process computes
  the same instants from the same clock.
- **A harness is only as good as its calibration.** `test_burst_harness.py`
  runs it against a deliberately unlocked counter and requires it to catch the
  lost updates. A concurrency test that has never been shown to fail against a
  broken implementation is not evidence of anything.

Actor functions must be module-level (picklable) and take one argument.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from collections.abc import Callable
from typing import Any

# How often actors re-synchronise. Short enough to produce thousands of
# collisions per second; the busy-wait keeps it precise without a scheduler.
BURST_PERIOD = 0.004

# Overridable, as `RITE_SOAK_SECONDS` already is for the blast-radius soak, so a
# longer run can be requested without editing the suite.
SOAK_SECONDS = float(os.environ.get("RITE_SOAK_SECONDS", "2"))


def align(period: float = BURST_PERIOD) -> None:
    """Busy-wait to the next multiple of `period` on the wall clock."""
    target = (int(time.time() / period) + 1) * period
    while time.time() < target:
        pass


def deadline(seconds: float | None = None) -> float:
    return time.time() + (SOAK_SECONDS if seconds is None else seconds)


def run_actors(actor: Callable[[Any], Any], args: list[Any], timeout_factor: int = 20):
    """Run `actor(arg)` for each arg in its own process; return the results in
    order. Each actor is expected to loop until its own deadline, calling
    `align()` before every attempt at the contested operation."""
    if not args:
        return []
    seconds = SOAK_SECONDS
    with mp.Pool(len(args)) as pool:
        handles = [pool.apply_async(actor, (a,)) for a in args]
        return [h.get(timeout=max(30.0, seconds * timeout_factor)) for h in handles]
