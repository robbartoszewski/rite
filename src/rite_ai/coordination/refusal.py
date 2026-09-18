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

from rite_ai.coordination.message_log import LogMessage, format_message, parse_message
from rite_ai.coordination.ticket_labels import SCHEDULED, module_required_by
from rite_ai.tickets import BackendError

FULL = "full"
SHUTTING_DOWN = "shutting down"
REFUSAL = "refusal"
"""A message-log kind. Not in `KNOWN_KINDS`, which is §3.3.3's list — and the
log deliberately preserves a kind it does not know rather than dropping it, so
an older rite reading this branch ignores these rows instead of failing."""


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


def refuse_assignment(
    backend, ticket_id: str, *, manager: str, reason: str, layer=None
):
    """Put the ticket back in the pool, say why, and remember it.

    `layer` is the state layer, and without it the refusal is on the board and
    nowhere rite can read: the Owner's next tick sees an unassigned ticket,
    picks the least loaded Manager — still this one, because refusing cost it
    nothing — and hands it straight back, with a comment each time. Q9's second
    rule is the memory; this is where it is written.
    """
    written = backend.label(ticket_id, [SCHEDULED], remove=[manager])
    if isinstance(written, BackendError):
        # It is still ours. Saying anything on the board now would describe a
        # refusal that did not happen.
        return NotRefused(ticket_id, f"could not return it: {written.message}")
    posted = backend.comment(
        ticket_id, f"{manager} cannot take this ticket: {reason}. Returned to the pool."
    )
    if layer is not None:
        # Board state first, then the record: a remembered refusal for a ticket
        # still carrying this Manager's name would exclude the only machine
        # that has it.
        layer.append_message(format_message(_refusal_event(ticket_id, manager, reason)))
    return Refused(ticket_id, reason, not isinstance(posted, BackendError))


def _refusal_event(ticket: str, manager: str, reason: str) -> LogMessage:
    """One refusal, as a §3.3.2 message. Private: the only way to write one is
    to actually refuse, so nothing can record a refusal that did not happen."""
    return LogMessage(
        kind=REFUSAL,
        subject=f"{manager} returned {ticket} to the pool ({reason})",
        fields={"Ticket": ticket, "Manager": manager, "Reason": reason},
    )


def refusals_by_ticket(layer, *, limit: int = 100):
    """ticket -> {manager: reason} from the message log, or `Unavailable`.

    The refusal memory Q9's second rule needs. Returned rather than applied:
    "assign without the memory" and "assign nothing this tick" are both
    defensible when the log cannot be read, and only the caller knows which
    tick this is.

    `limit` is the last N events, not the whole log, for the reason
    `overview.py` measured — each message is a commit on the git backend, about
    9ms to read, so a year of them would put a five-minute tick into seconds of
    reading. The cost of the window is that a refusal older than N events is
    forgotten, which loses a ping-pong that has been quiet for a hundred
    events. That is the right thing to forget.
    """
    from rite_ai.coordination.state_layer import Unavailable

    got = layer.read_messages(limit=limit)
    if isinstance(got, Unavailable):
        return got
    found: dict[str, dict[str, str]] = {}
    for message in got.items:
        parsed = parse_message(message.content)
        if parsed is None or parsed.kind != REFUSAL:
            continue
        ticket = parsed.fields.get("Ticket", "")
        manager = parsed.fields.get("Manager", "")
        if ticket and manager:
            # Last write wins: a later refusal of the same ticket by the same
            # Manager is the current reason, and the reason decides below
            # whether it still applies.
            found.setdefault(ticket, {})[manager] = parsed.fields.get("Reason", "")
    return found


def refusal_still_applies(reason: str, *, manager_is_live: bool) -> bool:
    """Whether a recorded refusal should still exclude that Manager.

    The reasons are not the same kind of fact. *Missing the module* is true of
    the machine and stays true until somebody clones a repo — permanent, as far
    as rite can see. *Shutting down* was true of a moment, and a Manager that
    is publishing heartbeats again is not shutting down any more; keeping it
    would retire a machine from a ticket because it once restarted while
    holding it. That starvation grows one ticket at a time and is visible
    nowhere, which is why it is decided here rather than by leaving every
    refusal in place for ever.
    """
    if reason.startswith(SHUTTING_DOWN) or reason.startswith(FULL):
        return not manager_is_live
    return True
