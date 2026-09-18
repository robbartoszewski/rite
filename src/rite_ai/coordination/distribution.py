"""A Manager hands its assigned tickets to its Workers (P2-4b, §2.7, D-44).

The Owner labels a ticket with a MANAGER's name (P2-3a, §2.3: "or a Manager's,
for the Manager to sub-assign"). This is the sub-assignment: the Manager reads
the tickets carrying its own name and re-labels them with one of its Workers.

**How many is not rite's decision (D-44).** The count comes from the user's
schedule for the hour it is now — `workers_at()`, the same function the
scheduler uses for window boundaries. Coordination cost is real and how much
of it a project can absorb is the user's call, informed by data (§2.7.1,
§2.7.2), never computed here. A window of 0 Workers is therefore not an error
and not a stall: it is §2.7.3's clean stop, reported as the schedule's own
answer.

**Busy Workers are the ones holding claims**, which is how the scheduler
already defines an active Worker (`run_tick`'s window-boundary handling). Using
a second definition here would give two answers to one question the first time
they disagreed.

**WHICH Worker does not matter, and saying so is the point.** Workers are
fungible (§5.3.4) — every one gets every project credential and there is
nothing to match against a ticket — so the free Workers are taken in
configured order. A pick that LOOKED clever here would be inventing a routing
rule the spec does not have.

**Three labels change together, because the label IS the assignment.** The
Worker's name goes on; the Manager's name and `scheduled` come off. Leaving
either behind produces a ticket that answers two questions at once — the
failure §9.10 already warns about in the other direction, where a handover
that only ADDS `scheduled` leaves the departing Worker's label in place.

A backend that refuses the write leaves the ticket where it was and the Worker
free for the next one. Reporting an assignment that did not happen is worse
than reporting none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rite_ai.config.models import ScheduleConfig
from rite_ai.coordination.refusal import Refused, refusal_reason, refuse_assignment
from rite_ai.coordination.ticket_labels import SCHEDULED, module_required_by
from rite_ai.schedule import current_minute_of_day, workers_at
from rite_ai.tickets import BackendError, TicketFilter


@dataclass
class Distributed:
    """What went where, and what did not go anywhere."""

    handouts: list[tuple[str, str]] = field(default_factory=list)
    """(ticket, worker), in the order performed."""
    capacity: int = 0
    """Workers the SCHEDULE allows right now — not a computed number."""
    free_workers: list[str] = field(default_factory=list)
    held_back: dict[str, str] = field(default_factory=dict)
    """ticket -> why it stayed put. Named, never counted: a Manager that
    reports "3 of 5 distributed" tells a human nothing they can act on."""
    refused: dict[str, str] = field(default_factory=dict)
    """ticket -> why it went BACK to the pool (P2-4c). Separate from
    `held_back` because they mean opposite things to the Owner: held work is
    still this Manager's, refused work is the board's again."""
    could_not_refuse: dict[str, str] = field(default_factory=dict)
    """A refusal the backend would not take. The ticket is still ours and
    still undoable — the one state a caller must not mistake for either."""


@dataclass
class NotDistributed:
    reason: str


def distribute(
    root: Path,
    backend,
    *,
    manager: str,
    workers: list[str],
    schedule: ScheduleConfig,
    now: datetime | None = None,
    busy: set[str] | None = None,
    modules: set[str] | None = None,
    draining: str = "",
):
    """Hand this Manager's assigned tickets to its free Workers.

    `modules` is what this machine actually has (P2-4c); `draining` is its own
    shutdown reason. Work it cannot do goes back to the pool rather than
    sitting here — but being merely FULL is not that, and holds instead.
    """
    minute = current_minute_of_day(schedule.timezone, now)
    if minute is None:
        # A schedule whose timezone cannot be resolved must not silently
        # become "all hours" or "no hours" — both are wrong and neither is
        # visible. §2.7 calls a timezone-less schedule a trap for exactly
        # this reason.
        return NotDistributed(
            f"the schedule's timezone {schedule.timezone!r} could not be resolved"
        )
    capacity = workers_at(schedule, minute)

    if busy is None:
        busy = _busy_workers(root)
    free = [w for w in workers if w not in busy]
    slots = max(0, capacity - len(busy))

    result = Distributed(capacity=capacity, free_workers=list(free))

    tickets = backend.list_tickets(TicketFilter(label=manager))
    if isinstance(tickets, BackendError):
        return NotDistributed(
            f"could not read this Manager's tickets: {tickets.message}"
        )
    mine = [t for t in tickets if manager in (t.labels or [])]
    if not mine:
        return result

    if capacity == 0 and not draining and not _modules_matter(mine):
        # §2.7.3: the user scheduled zero Workers for this hour. Not a
        # failure, and not something to work around. Work this machine could
        # never do still goes back below, because that is not about capacity.
        for ticket in mine:
            result.held_back[ticket.id] = (
                "the schedule has 0 Workers in this window"
            )
        return result

    for ticket in mine:
        why = refusal_reason(ticket, modules=modules or set(), draining=draining)
        if why:
            # P2-4c: not ours to do. Back to the pool, with the reason on the
            # ticket, before any question of capacity arises.
            outcome = refuse_assignment(backend, ticket.id, manager=manager, reason=why)
            if isinstance(outcome, Refused):
                result.refused[ticket.id] = why
            else:
                result.could_not_refuse[ticket.id] = outcome.reason
            continue
        if not free or slots <= 0:
            result.held_back[ticket.id] = (
                f"no free Worker: {len(busy)} of {capacity} scheduled slots in use"
                if slots <= 0
                else f"every Worker of {manager} is busy"
            )
            continue
        worker = free[0]
        written = backend.label(ticket.id, [worker], remove=[manager, SCHEDULED])
        if isinstance(written, BackendError):
            # The Worker stays free and the ticket stays put. An assignment
            # that did not land must not be reported as one.
            result.held_back[ticket.id] = (
                f"the backend refused the label: {written.message}"
            )
            continue
        free.pop(0)
        slots -= 1
        result.handouts.append((ticket.id, worker))
    result.free_workers = list(free)
    return result


def _busy_workers(root: Path) -> set[str]:
    """Workers holding a claim — the scheduler's own definition of active."""
    from rite_ai.claims.ledger import ClaimsLedger

    claims_path = root / ".rite" / "claims.json"
    if not claims_path.exists():
        return set()
    return {claim.worker for claim in ClaimsLedger(claims_path).list_claims()}


def _modules_matter(tickets) -> bool:
    """Whether any of these tickets names a module at all — so an off-window
    Manager still returns work it could never do, rather than holding it
    until a window it will refuse it in anyway."""
    return any(module_required_by(t) for t in tickets)
