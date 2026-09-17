"""What a new Owner owes the old one's work (P2-3c, D-14's THIRD trigger).

D-14 says there is ONE handover function for all three triggers — `rite
stop`, a stalled Manager's heartbeat timeout, and Owner lease expiry — and
that the first three must produce identical board state (§9.10). The third
caller was undercounted to "both" everywhere: in the plan, in the shipped
docstrings, and in D-14's own row until it was corrected. This is that
caller.

Without it, promotion is silent on the board. The outgoing Owner's tickets
keep its assignment and its labels, with no handover comment, and the next
human to look sees work apparently in progress on a machine that is gone —
the exact state `rite stop` exists to avoid producing.

**Worker names are qualified with the machine, and that is not cosmetic.**
`perform_handover` releases claims from the LOCAL ledger by worker name, and
worker names repeat across machines — `w1` is the default everywhere. Passing
the outgoing Owner's bare worker name would release OUR OWN identically-named
worker's claim: silent, local, cross-machine data loss on every promotion.
Qualified as `<machine>/<worker>` it matches nothing local (local names have
no slash) and the audit record says which machine the work came from.

**The audit record is written first.** `claims_state` established this for
cross-machine expiry and the reason is the same here: if the record cannot be
written, the board is not touched. A promotion that quietly rearranged
another machine's work with no trace is worse than one that did not happen.

⚠ **Published claims are NOT released here, and that is a boundary, not an
omission.** A lapsed lease does not prove a machine is gone — a Manager can
be alive and working while its renewal loop is wedged. Claims are released on
heartbeat evidence (`expire_offline_claims`, P2-5c), which is the signal that
actually means "gone". So this hands over the BOARD state and leaves the
claims to the mechanism that can tell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rite_ai.coordination.claims_state import CLAIMS_KEY, published_claims
from rite_ai.coordination.message_log import (
    LogMessage,
    format_message,
    promotion_event,
)
from rite_ai.coordination.state_layer import Absent, StateLayer, Unavailable


@dataclass
class ToldTheBoard:
    previous_owner: str
    reason: str
    tickets: list[str] = field(default_factory=list)
    """Tickets a handover was performed for, in the order performed."""
    board_updated: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    """Tickets whose handover could not reach the backend and was queued —
    the board still says what it said before. Named, not counted."""


@dataclass
class Refused:
    """The trigger's own precondition did not hold. Nothing was touched."""

    reason: str


@dataclass
class Unknown:
    reason: str


def hand_over_outgoing_owner(
    root: Path,
    layer: StateLayer,
    *,
    new_owner: str,
    previous_owner: str,
    promotion_reason: str = "lease-expired",
    now: datetime | None = None,
):
    """Called by the Manager that has just promoted itself (§2.4.2 step 4).

    `promotion_reason` is `message_log`'s vocabulary: `lease-expired`,
    `lease-not-credible` (D-55), `no-owner`, `handed-over`.
    """
    reason = f"owner lease expired, promoted by {new_owner}"

    return hand_over_machines_work(
        root,
        layer,
        machine=previous_owner,
        reason=reason,
        record=promotion_event(new_owner, previous_owner, promotion_reason),
    )


def hand_over_stalled_manager(
    root: Path,
    layer: StateLayer,
    *,
    owner: str,
    stalled: str,
    now: datetime,
    interval_minutes: int,
    stall_threshold: int,
):
    """D-14's SECOND trigger: the Owner, on a stalled Manager's behalf.

    The stall is re-established here rather than trusted from the caller. A
    handover performed on a Manager that is merely quiet takes work away
    from a machine that is still doing it, and the caller's idea of "stalled"
    may be several ticks old.

    "Cannot tell" is never stalled (D-54, §3.4). No heartbeat at all is the
    same answer: a Manager that has never published one may be a machine
    that was never set up, and stripping tickets off the board on that basis
    would be a guess with consequences.
    """
    from rite_ai.coordination.heartbeat import is_stalled, liveness

    live = liveness(layer, stalled, now=now, interval_minutes=interval_minutes)
    if not live.known:
        return Refused(f"cannot tell whether {stalled} is stalled: {live.detail}")
    if not is_stalled(live, stall_threshold=stall_threshold):
        return Refused(
            f"{stalled} is not stalled ({live.missed} missed, "
            f"threshold {stall_threshold})"
        )

    reason = f"manager stalled, handed over by {owner}"
    missed = live.missed
    record = LogMessage(
        kind="handover",
        subject=f"{owner} handed over {stalled}'s work ({missed} intervals missed)",
        fields={"Manager": owner, "Stalled": stalled, "Missed": str(live.missed)},
    )
    return hand_over_machines_work(
        root, layer, machine=stalled, reason=reason, record=record
    )


def hand_over_machines_work(
    root: Path,
    layer: StateLayer,
    *,
    machine: str,
    reason: str,
    record: LogMessage,
):
    """The one path both triggers use (D-14: "do not write a second handover
    path", and that applies to the cross-machine wrapper too, not only to
    `perform_handover` itself)."""
    from rite_ai.lifecycle.commands import perform_handover

    claims = _claims_of(layer, machine)
    if isinstance(claims, Unknown):
        return claims

    logged = layer.append_message(format_message(record))
    if isinstance(logged, Unavailable):
        # No record, no action. The board is left exactly as it is.
        return Unknown(f"the handover could not be recorded: {logged.reason}")

    told = ToldTheBoard(machine, reason)
    seen: set[str] = set()
    for claim in claims:
        ticket = str(claim.get("ticket") or "")
        worker = str(claim.get("worker") or "")
        if not ticket or ticket in seen:
            # One handover per ticket. A ticket with several claimed paths
            # is still one piece of work, and two comments on one ticket
            # read as two handovers.
            continue
        seen.add(ticket)
        result = perform_handover(
            root,
            worker=f"{machine}/{worker}" if worker else machine,
            reason=reason,
            ticket=ticket,
        )
        told.tickets.append(ticket)
        if result.ticket_commented:
            told.board_updated.append(ticket)
        else:
            told.queued.append(ticket)
    return told


def _claims_of(layer: StateLayer, machine: str):
    read = layer.read_state(CLAIMS_KEY)
    if isinstance(read, Unavailable):
        # Unknown is not "it held nothing" (D-54). Promoting without
        # handing over is a decision, and it must not be made by accident.
        return Unknown(f"the published claims could not be read: {read.reason}")
    if isinstance(read, Absent):
        return []
    try:
        state = json.loads(read.value.decode("utf-8", "replace"))
    except (json.JSONDecodeError, ValueError):
        return Unknown(f"{CLAIMS_KEY} is not readable JSON")
    if not isinstance(state, dict):
        return Unknown(f"{CLAIMS_KEY} is not an object")
    return published_claims(state).get(machine, [])
