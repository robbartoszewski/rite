"""Standing for the Owner role (P2-2b, §2.4 "Promotion", D-42, D-56).

The lease (P2-2a) answers "can I take it". This answers "should I" — the
policy §2.4 states as an org chart: Managers are listed in priority order and
the first ACTIVE one is Owner.

Three things this deliberately does not do:

**It never seizes a live lease.** A higher-priority Manager coming back asks,
through the promotion request (§2.4 "Graceful demotion"); it does not take. An
interrupted assignment produces the same inconsistency a crash does.

**It never promotes on the bare expiry boundary** (D-42). Expiry is judged
against `expires + skew_tolerance`, because the incumbent's own clock may not
yet have prompted it to renew. NTP-synced clocks remain a documented
precondition — the margin narrows the window, it does not close it.

**It does not decide priority from the lease** (D-56). Only the order of
`coordination.managers` is consulted.

⚠ **Deferring to a Manager we cannot see would be worse than promoting.**
§2.4 says the highest-priority ACTIVE Manager promotes, and leaves "active"
to §3.4's heartbeats — which have a third answer, "cannot tell" (no status
file, unreadable bytes, an unparseable timestamp). The house rule is to fail
closed on unknown, and here that rule inverts: a Manager that has never
published a heartbeat — a machine listed in config that has not been set up
yet, or was retired — would wedge the role PERMANENTLY, because it is always
"not known to be inactive". Nothing in the protocol corrects a vacancy,
whereas an out-of-order promotion is corrected by design: the higher-priority
Manager comes back, publishes a promotion request, and is handed the role.

So we defer only to a higher-priority Manager we can SEE is alive, and every
Manager we could not see is named in the outcome. A vacancy is the failure
mode with no recovery path; an early promotion has one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.heartbeat import is_stalled, liveness
from rite_ai.coordination.lease import (
    Acquired,
    HeldByOther,
    OwnerLeaseHolder,
    Uncertain,
)
from rite_ai.coordination.schemas import LeaseVerdict


@dataclass
class Promoted:
    """We are Owner. `lease` is ours until its `expires`."""

    lease: object
    version: str
    # Named rather than merely counted: §2.4.1 requires a lease rejected as
    # not credible to be logged distinctly, because it means a clock is wrong.
    displaced_not_credible: str | None = None
    # Higher-priority Managers we could not see. Not an error — a promotion
    # that happened BECAUSE nobody could vouch for them is worth reading.
    unseen: list[str] = field(default_factory=list)
    # Whose lease we displaced, empty for a first election. D-14's third
    # trigger needs this: you cannot hand over the outgoing Owner's work
    # without knowing who the outgoing Owner was.
    previous_owner: str = ""
    # `message_log`'s vocabulary for why, so the promotion event says it.
    why: str = "no-owner"


@dataclass
class Deferred:
    """A higher-priority Manager is alive. It promotes, not us."""

    to: str
    detail: str = ""


@dataclass
class StillOwner:
    owner: str = ""


@dataclass
class NotOwner:
    """Someone else holds a live lease. Ask, do not seize (§2.4)."""

    owner: str
    higher_priority_than_us: bool = False


@dataclass
class NotEligible:
    reason: str


@dataclass
class Unknown:
    """The truth could not be established. Not "the role is free"."""

    reason: str


def stand_for_owner(
    holder: OwnerLeaseHolder,
    *,
    heartbeat: HeartbeatConfig | None = None,
    now: datetime | None = None,
):
    config: CoordinationConfig = holder.config
    heartbeat = heartbeat or HeartbeatConfig()
    now = now or holder.clock()

    if config.managers and holder.manager not in config.managers:
        # The list is the org chart. A machine outside it promoting would be
        # invisible to every other Manager's priority reasoning — they would
        # defer to names in the list while an unlisted one held the role.
        return NotEligible(
            f"{holder.manager!r} is not in coordination.managers "
            f"({', '.join(config.managers)})"
        )

    current = holder.current()
    if isinstance(current, Uncertain):
        return Unknown(current.reason)
    lease, _version = current

    if lease is not None:
        verdict = holder.verdict(lease, now)
        if verdict == LeaseVerdict.HELD:
            if lease.owner == holder.manager:
                return StillOwner(lease.owner)
            return NotOwner(lease.owner, _outranks(lease.owner, holder, config))

    # The role is free, expired past the margin, or not credible.
    unseen: list[str] = []
    for name in _above(holder.manager, config):
        live = liveness(
            holder.layer, name, now=now, interval_minutes=heartbeat.interval_minutes
        )
        if not live.known:
            unseen.append(name)
            continue
        if not is_stalled(live, stall_threshold=heartbeat.stall_threshold):
            return Deferred(name, live.detail)

    previous = "" if lease is None else lease.owner
    why = "no-owner"
    if lease is not None:
        why = (
            "lease-not-credible"
            if holder.verdict(lease, now) == LeaseVerdict.NOT_CREDIBLE
            else "lease-expired"
        )

    got = holder.acquire()
    if isinstance(got, Acquired):
        return Promoted(
            got.lease,
            got.version,
            got.displaced_not_credible,
            unseen,
            previous,
            why,
        )
    if isinstance(got, HeldByOther):
        # Lost the race, which is the CAS working (§2.4.2). The winner's
        # name comes from the lease we just re-read, not from a guess.
        return NotOwner(got.owner, _outranks(got.owner, holder, config))
    return Unknown(got.reason)


def _above(manager: str, config: CoordinationConfig) -> list[str]:
    if manager not in config.managers:
        return []
    return config.managers[: config.managers.index(manager)]


def _outranks(other: str, holder: OwnerLeaseHolder, config: CoordinationConfig) -> bool:
    if other not in config.managers or holder.manager not in config.managers:
        return False
    return config.managers.index(other) < config.managers.index(holder.manager)
