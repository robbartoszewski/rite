"""A Manager refuses an assignment (P2-4c).

The Owner assigns by labelling; it does not ask first (§2.3). So a refusal is
necessarily something the Manager does AFTER the fact: it puts the ticket back
in the pool — `scheduled` on, its own name off — and says why on the ticket.

**Three reasons, and they are not the same kind of thing.**

- *Missing the module* never self-corrects. This machine does not have the
  repo; no amount of waiting changes that. Refuse.
- *Shutting down* does not self-correct either, from this ticket's point of
  view: the Manager is going away and the work must go somewhere else. Refuse.
- *Full* self-corrects in minutes. Refusing it hands the ticket back to a pool
  that will hand it straight back — with a board comment each time — so a full
  Manager HOLDS instead (P2-4b's `held_back`), and holds silently.

⚠ **RAISED: "full" cannot be refused safely until the Owner can see capacity.**
The heartbeat carries `in_flight` but not how many Workers the Manager may run,
so the Owner cannot tell a busy Manager from a full one and will keep choosing
it. Publishing capacity alongside `in_flight` would close that, and then
refusing on "full" becomes safe rather than a ping-pong with comments. The
mechanism here supports it (`refuse_assignment` takes any reason); the policy
in `distribution` does not use it, deliberately.

⚠ **PROPOSAL: how a ticket names its module.** `modules.yaml` names modules and
§2.3 says assignment is by label, but nothing in the spec maps a ticket to a
module — and a Manager cannot refuse work for a module it lacks without that
mapping. The convention here is a `module:<name>` label, matching the existing
convention that rite's labels are its assignment mechanism. A ticket with no
such label carries no module requirement, so this is additive: boards that
never use it behave exactly as before. Callers can pass their own resolver.

**The label is written before the comment, and the order is a choice.** The
board's STATE must be right: a ticket left carrying this Manager's name is
work nobody is doing. If the explanation then fails to post, the result says
so and it can be retried without changing state — whereas a comment posted
first, followed by a failed relabel, is an explanation for something that did
not happen.
"""

from __future__ import annotations

from dataclasses import dataclass

from rite_ai.coordination.ticket_labels import SCHEDULED, module_required_by
from rite_ai.tickets import BackendError

FULL = "full"
SHUTTING_DOWN = "shutting down"


@dataclass
class Refused:
    ticket: str
    reason: str
    reason_posted: bool = True
    """False when the ticket was returned but the explanation did not post.
    The board is correct either way; a human just has less to read."""


@dataclass
class NotRefused:
    """The ticket is still ours. Nothing was said on the board about it."""

    ticket: str
    reason: str


def refusal_reason(ticket, *, modules: set[str], draining: str = "") -> str | None:
    """Why this Manager cannot take this ticket, or None if it can.

    `draining` is the Manager's own shutdown reason, passed in rather than
    detected: the Manager knows it is stopping, and nothing else does.
    """
    if draining:
        return f"{SHUTTING_DOWN}: {draining}"
    needed = module_required_by(ticket)
    if needed and needed not in modules:
        # Named, with what IS here, because the usual cause is a machine that
        # was never set up for this module rather than a typo — and the
        # difference is obvious from the list.
        have = ", ".join(sorted(modules)) or "none"
        return f"this machine does not have module {needed!r} (it has: {have})"
    return None


def refuse_assignment(backend, ticket_id: str, *, manager: str, reason: str):
    """Put the ticket back in the pool and say why."""
    written = backend.label(ticket_id, [SCHEDULED], remove=[manager])
    if isinstance(written, BackendError):
        # It is still ours. Saying anything on the board now would describe a
        # refusal that did not happen.
        return NotRefused(ticket_id, f"could not return it: {written.message}")
    posted = backend.comment(
        ticket_id, f"{manager} cannot take this ticket: {reason}. Returned to the pool."
    )
    return Refused(ticket_id, reason, not isinstance(posted, BackendError))
