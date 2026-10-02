"""The Manager's helper sessions: a small, on-demand pool (D-115).

**What a helper is for** (Robert, 2026-10-02). The Manager is a coordinator:
no heavy implementation (the lead implements nothing; a secondary may do only
chores, ticket breakdowns and similar trivial work, through a branch and a
pull request, `prompt.ROUTED_TICKET_WORK`), and it keeps no helper sessions
running. A helper is a
session spawned ON DEMAND for work connected to the coordinator role
(answering a question, a review, a verification, a spec) and only for one of
two reasons:

- **fresh eyes**: the work must be independent of the interactive thread, so a
  helper is given the claim and the workspace and not the conversation (rite's
  reply verifier, `verifier.verify`, is one);
- **not blocking**: the work is offloaded so the Manager stays responsive to
  the User's DMs and `rite connect`.

**How they are bounded: concurrency, never a rate.** At any moment a Manager
runs its one interactive session plus at most `MANAGER_HELPER_POOL` helpers;
its Workers are bounded separately, by the schedule window's count. A helper
that would exceed the pool WAITS for a slot (`helper_slot`), it is never
refused or dropped, and nothing here counts helpers per hour.

Today the only helper is rite's own reply verifier, which runs one at a time,
so the pool is never full in practice. It is enforced anyway, at the one place
every helper session goes through, so that whatever adds a second kind of
helper inherits the bound instead of having to remember it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

MANAGER_HELPER_POOL = 2
"""Helper sessions one Manager may run at once, beside its interactive one.

Two: one fresh-eyes helper and one non-blocking one at the same moment. Small
on purpose: a helper is coordinator work, not a way to run implementation in
parallel. ⚠ The number is rite's choice, recorded in SPEC §9.14.5; Robert set
the shape ("a small fixed allowance") and not the figure."""

_slots = threading.BoundedSemaphore(MANAGER_HELPER_POOL)


@contextmanager
def helper_slot():
    """Hold one of this Manager's helper slots for the length of a helper
    session, waiting for one when the pool is full.

    One supervisor process runs one Manager, so a per-process pool is a
    per-Manager pool."""
    _slots.acquire()
    try:
        yield
    finally:
        _slots.release()
