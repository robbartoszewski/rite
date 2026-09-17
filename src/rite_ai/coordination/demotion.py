"""Handing the Owner role back gracefully (P2-2e, §2.4 "Graceful demotion").

Five steps in the spec: the returning Manager asks, the incumbent finishes
ONE operation, transfers state, releases, and the requester acquires.

**"Finishes its current operation" is not enforced here, and cannot be.**
D-43 defines it precisely — one ticket-backend write or one board-state
transition, so a four-call sequence may hand over between any two of them —
but only the caller knows where it is in that sequence. `hand_over()` is the
call you make WHEN you are at a boundary. Pretending to enforce it would be
worse than saying so: the caller would trust a guarantee that isn't there.

**"Transfers state" is smaller than it sounds, per §2.4.3.** A new Owner
reconstructs the picture from the coordination repo and the ticket backend;
it needs no hand-off message. The transfer step exists only to make sure the
outgoing Owner's in-flight writes have LANDED before the successor reads. So
that is what this does: confirm we can still read the state we wrote, and
refuse to release if we cannot. Releasing while our own writes might be in
flight is the one thing this step exists to prevent.

⚠ **Two writes, not one — the interface cannot do better.** Clearing the
request and releasing the lease are separate files, and `StateLayer` writes
one key per call, so there is no atomic "clear and release". We clear first:
if we then fail to release, the requester simply asks again, whereas
releasing first and failing to clear leaves a live request addressed to an
Owner that no longer exists.

⚠ **Two gaps in the protocol as written, both raised rather than papered
over.** §2.4 says the incumbent releases and the requester then acquires:

1. A released lease is indistinguishable from an expired one, so the
   requester must still wait out `skew_tolerance` (§2.4.1) — a deliberate
   handover costs a minute of nobody being Owner.
2. In that minute, the role is free to ANY Manager, not just the one that
   asked. A third, lower-priority Manager can take it. The system still
   converges (the requester outranks it and asks again), but the handover
   does not do what it looks like it does.

Both close if the incumbent hands the lease DIRECTLY to the requester
instead of releasing it — one write, no vacancy, no window. That contradicts
the literal wording of steps 4 and 5, so it is not done here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rite_ai.coordination.lease import (
    LEASE_KEY,
    OwnerLeaseHolder,
    Released,
    Uncertain,
    stamp,
)
from rite_ai.coordination.promotion import (
    PromotionRequest,
    request_from_json,
    request_to_json,
)
from rite_ai.coordination.state_layer import (
    Absent,
    Present,
    StateLayer,
    Unavailable,
    Written,
)

REQUEST_KEY = "promotion-request.json"

_CAS_ATTEMPTS = 6


@dataclass
class Asked:
    request: PromotionRequest
    version: str


@dataclass
class HandedOver:
    """We are no longer Owner, and the request has been cleared."""

    to: str
    version: str


@dataclass
class Refused:
    """We stay Owner. The reason is for a human, not a retry."""

    reason: str


@dataclass
class Unknown:
    reason: str


def request_promotion(
    layer: StateLayer,
    requester: str,
    incumbent: str,
    *,
    managers: list[str],
    now: datetime,
):
    """Ask the current Owner for the role back. Asking, never taking."""
    if requester not in managers:
        return Refused(f"{requester!r} is not in coordination.managers")
    if incumbent in managers and managers.index(requester) >= managers.index(incumbent):
        # A Manager that does not outrank the incumbent has nothing to
        # return to. Writing the request anyway would leave the incumbent
        # to refuse it, which is a pointless round trip through the state.
        return Refused(f"{requester!r} does not outrank {incumbent!r}")
    request = PromotionRequest(
        requester=requester, requested=stamp(now), incumbent=incumbent
    )
    for _ in range(_CAS_ATTEMPTS):
        read = layer.read_state(REQUEST_KEY)
        if isinstance(read, Unavailable):
            return Unknown(f"could not read the promotion request: {read.reason}")
        result = layer.write_state(
            REQUEST_KEY, request_to_json(request).encode(), read.version
        )
        if isinstance(result, Written):
            return Asked(request, result.version)
        if isinstance(result, Unavailable):
            return Unknown(f"the request did not complete: {result.reason}")
    return Unknown("the state kept changing under us; the request never settled")


def pending_request(layer: StateLayer, lease_owner: str):
    """The request addressed to THIS Owner, if there is one.

    A request naming someone else was meant for an earlier Owner — the
    requester asked, and the role changed hands some other way before
    anyone acted. Acting on it now would be a seizure by accident, and
    recognising that needs no clock (see `promotion.py`).
    """
    read = layer.read_state(REQUEST_KEY)
    if isinstance(read, Unavailable):
        return Unknown(f"could not read the promotion request: {read.reason}")
    if isinstance(read, Absent):
        return None
    request = request_from_json(read.value.decode("utf-8", "replace"))
    if request is None:
        # Unreadable is not "no request" (D-54). Refusing the decision is
        # the point: an Owner that reads garbage as "nobody asked" would
        # ignore a returning Manager for ever.
        return Unknown("the promotion request is not readable JSON")
    if not request.requester or not request.is_addressed_to(lease_owner):
        return None
    return Asked(request, read.version)


def hand_over(holder: OwnerLeaseHolder, *, now: datetime | None = None):
    """Steps 3 and 4: confirm our writes landed, clear the request, release.

    Call this at an operation boundary (D-43), never mid-sequence.
    """
    now = now or holder.clock()
    managers = holder.config.managers

    asked = pending_request(holder.layer, holder.manager)
    if isinstance(asked, Unknown):
        return Unknown(asked.reason)
    if asked is None:
        return Refused("no promotion request is addressed to us")
    requester = asked.request.requester
    if managers and (
        requester not in managers
        or holder.manager not in managers
        or managers.index(requester) >= managers.index(holder.manager)
    ):
        # The authoritative check: config order, not the request (D-56).
        return Refused(f"{requester!r} does not outrank {holder.manager!r}")

    # Step 3, per §2.4.3: make sure what we wrote is readable before we go.
    landed = holder.layer.read_state(LEASE_KEY)
    if isinstance(landed, Unavailable):
        return Unknown(f"our own state could not be re-read: {landed.reason}")
    if isinstance(landed, Present):
        lease = _lease_owner(landed)
        if lease is not None and lease != holder.manager:
            # We are not the Owner any more; there is nothing to hand over.
            return Refused(f"the lease already names {lease!r}")

    cleared = _clear_request(holder.layer, asked.version)
    if isinstance(cleared, Unknown):
        return cleared

    released = holder.release()
    if isinstance(released, Released):
        return HandedOver(requester, released.version)
    if isinstance(released, Uncertain):
        return Unknown(f"the release did not complete: {released.reason}")
    # Already lost it some other way (expiry, a challenge). The request is
    # cleared and we are not Owner, which is the outcome we wanted anyway.
    return HandedOver(requester, "")


def _lease_owner(read: Present) -> str | None:
    from rite_ai.coordination.schemas import lease_from_json

    lease = lease_from_json(read.value.decode("utf-8", "replace"))
    return None if lease is None else lease.owner


def _clear_request(layer: StateLayer, version: str):
    """An empty request, not a deleted file: the state layer has no delete,
    and a cleared request that still round-trips unknown fields is safer
    than one that vanishes."""
    for _ in range(_CAS_ATTEMPTS):
        result = layer.write_state(
            REQUEST_KEY, request_to_json(PromotionRequest()).encode(), version
        )
        if isinstance(result, Written):
            return result
        if isinstance(result, Unavailable):
            return Unknown(f"the request could not be cleared: {result.reason}")
        read = layer.read_state(REQUEST_KEY)
        if isinstance(read, Unavailable):
            return Unknown(f"the request could not be re-read: {read.reason}")
        version = read.version
    return Unknown("the state kept changing under us; the request was never cleared")
