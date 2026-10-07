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
fungible (§5.3.4) — every one gets the same credentials and there is
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
from rite_ai.coordination.ticket_labels import (
    READY_TO_WORK,
    SCHEDULED,
    module_required_by,
)
from rite_ai.schedule import current_moment, workers_at
from rite_ai.tickets import BackendError, TicketFilter
from rite_ai.tickets.statuses import is_terminal


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
    layer=None,
    refinement=None,
):
    """Hand this Manager's assigned tickets to its free Workers.

    `modules` is what this machine actually has (P2-4c); `draining` is its own
    shutdown reason. Work it cannot do goes back to the pool rather than
    sitting here — but being merely FULL is not that, and holds instead.

    `layer` records refusals where rite can read them back (Q9 rule 2).
    Without it a refusal exists only as a board comment, and the Owner hands
    the same ticket to the same Manager on its next tick, for ever.

    ⚠ **Only a REFINED ticket is handed to a Worker (TR5).** The Owner assigns
    only REFINED tickets, but a Manager's name can be put on a ticket by hand,
    and a ticket can go STALE after it was assigned. Either one handed out
    labels a Worker with work its start then refuses (TR4). Checked with the
    one predicate, only for a ticket about to be handed out, so a full fleet
    costs no reads; anything else is held and its state said. `refinement` is
    injectable for tests; None checks `backend` itself.
    """
    # ⚠ ONE MOMENT, taken once. This read the minute from `now` and the
    # weekday from `current_moment(...)` with no `now` at all — so the two
    # halves of the same instant came from different clocks. Measured, on a
    # schedule open Sundays only, asked about a Saturday:
    #
    #     capacity reported: 3      expected: 0
    #
    # because the weekday was the real day of the week rather than the one
    # asked about. Near a day boundary that is wrong in production, and it
    # made the day dimension untestable here, which is why this call site
    # had no behavioural test to lose.
    #
    # The `minute is None` branch is gone with it: `current_moment` resolves
    # an unusable timezone to the machine's clock and reports that through
    # `ResolvedZone.rejected`, rather than refusing to answer. Refusing left
    # this subsystem assigning nothing while `start_worker` enforced the
    # schedule normally, on the documented default of an unset timezone.
    moment = current_moment(schedule.timezone, now)
    capacity = workers_at(schedule, moment.minute_of_day, moment.weekday)

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
    # ⚠ **A ticket whose STATUS says the work is over is never handed out
    # (SCRUM-73).** Before capacity, before the module refusal and before the
    # refinement check, because none of those is the question: a Done ticket
    # needs neither a Worker nor a return to the pool. A Manager's name can
    # be put on a ticket by hand and a ticket can be finished after it was
    # assigned, so the label alone cannot say. Held and said, never silent —
    # this is the condition that had KAN-28 worked twice.
    for ticket in mine:
        if is_terminal(ticket):
            result.held_back[ticket.id] = (
                f"it is {ticket.status or 'in a terminal status'}: that work "
                "is over, so no Worker is given it. Take the label off."
            )
    mine = [t for t in mine if not is_terminal(t)]
    if not mine:
        return result

    if capacity == 0 and not draining and not _modules_matter(mine):
        # §2.7.3: the user scheduled zero Workers for this hour. Not a
        # failure, and not something to work around. Work this machine could
        # never do still goes back below, because that is not about capacity.
        for ticket in mine:
            result.held_back[ticket.id] = "the schedule has 0 Workers in this window"
        return result

    for ticket in mine:
        why = refusal_reason(ticket, modules=modules or set(), draining=draining)
        if why:
            # P2-4c: not ours to do. Back to the pool, with the reason on the
            # ticket, before any question of capacity arises.
            outcome = refuse_assignment(
                backend, ticket.id, manager=manager, reason=why, layer=layer
            )
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
        answer = _refinement_of(refinement, backend, ticket.id)
        if not answer.refined:
            result.held_back[ticket.id] = (
                f"{answer.state}: no agreed definition of done to start a Worker on"
            )
            continue
        worker = free[0]
        # TR7: `ready-to-work` leaves in the same write as `scheduled`, so
        # the board never shows an assigned ticket as ready to be assigned.
        # Only when the ticket carries it: `gh issue edit --remove-label`
        # refuses a label the REPOSITORY lacks (measured, gh 2.98.0: "'…' not
        # found", exit 1), which would fail every assignment on a repository
        # rite has never labelled `ready-to-work`.
        remove = [manager, SCHEDULED]
        if READY_TO_WORK in (ticket.labels or []):
            remove.append(READY_TO_WORK)
        written = backend.label(ticket.id, [worker], remove=remove)
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


def _refinement_of(refinement, backend, ticket_id: str):
    from rite_ai.refinement import status as refinement_status

    if refinement is None:

        def refinement(ticket: str):
            return refinement_status.status(backend, ticket)

    return refinement_status.checked(refinement, ticket_id)


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
