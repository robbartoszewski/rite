"""Holding, renewing and giving up the Owner lease (P2-2a, §2.4.1, D-16/D-17).

This module does the lease MECHANICS and nothing else. It does not decide who
ought to be Owner: priority is the order of `coordination.managers` (D-60) and
promotion is the election's business. A Manager asks this module "can I take
it, and do I still have it"; the answers are honest about the third case.

**Three answers, not two.** Every operation can also come back `Uncertain` —
the state could not be read or the write was never acknowledged. Uncertain is
NOT "no": an Owner that treats a failed renewal as "I still hold it" produces
two Owners, and one that treats it as "someone took it" abandons a role nobody
else has claimed. The caller must stop acting as Owner and retry, which is why
this is a distinct type rather than an exception or a falsy value.

**`holds_role()` is the question Owner-only work must ask**, and it is answered
from the clock, not from the last call's result. A Manager that renewed
successfully ten minutes ago does not hold a 15-minute lease for ever; if
renewal has been failing, the role lapses on its own while the process is still
running. That is the whole point of a lease over a heartbeat (D-17).

**Renewal runs at a third of the lease.** Not stated in §2.4.1, which says only
"before the current one lapses". A third is derived rather than picked: it
leaves room for two failed renewals before expiry, so a single network blip
never costs the role, and it is the longest interval for which that is true.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from rite_ai.config.models import CoordinationConfig
from rite_ai.coordination.schemas import (
    LeaseVerdict,
    OwnerLease,
    lease_from_json,
    lease_to_json,
    parse_timestamp,
)
from rite_ai.coordination.state_layer import (
    Absent,
    StateLayer,
    Unavailable,
    Written,
)

LEASE_KEY = "owner-lease.json"

# A whole-ref compare-and-swap (D-16) conflicts on ANY concurrent write, not
# just a competing lease write — a Manager updating its own status file is
# enough. So a conflict is the normal case under load and must be retried
# from a fresh read, never reported as losing the role.
_CAS_ATTEMPTS = 6


@dataclass
class Acquired:
    """The lease is ours. `expires` is OUR deadline, on OUR clock."""

    lease: OwnerLease
    version: str
    # Set when the lease we displaced was rejected as not credible (D-59).
    # §2.4.1 requires this to be logged distinctly: it means somebody's
    # clock is wrong, and this is the only signal that will say so.
    displaced_not_credible: str | None = None


@dataclass
class HeldByOther:
    owner: str
    lease: OwnerLease


@dataclass
class Renewed:
    lease: OwnerLease
    version: str


@dataclass
class LeaseLost:
    """We are not the Owner any more, and we know it."""

    reason: str
    owner: str = ""


@dataclass
class Released:
    version: str


@dataclass
class Uncertain:
    """We could not establish the truth. NOT the same as "no"."""

    reason: str


def _now() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class OwnerLeaseHolder:
    def __init__(
        self,
        layer: StateLayer,
        manager: str,
        config: CoordinationConfig,
        clock=_now,
    ) -> None:
        self.layer = layer
        self.manager = manager
        self.config = config
        self.clock = clock
        # What WE last wrote, so `holds_role()` never has to trust the
        # network to answer "am I still Owner".
        self._our_expiry: datetime | None = None

    # --- reading ---

    def current(self):
        """(lease, version) | Uncertain. An absent lease reads as (None, version)."""
        result = self.layer.read_state(LEASE_KEY)
        if isinstance(result, Unavailable):
            return Uncertain(f"could not read the lease: {result.reason}")
        if isinstance(result, Absent):
            return None, result.version
        lease = lease_from_json(result.value.decode("utf-8", "replace"))
        if lease is None:
            # Unreadable is not absent (§2.4.2 step 2 treats absent as the
            # first-write case, and this is emphatically not that).
            return Uncertain("the lease file is not readable JSON")
        return lease, result.version

    def verdict(self, lease: OwnerLease, now: datetime) -> str:
        return lease.verdict(
            now=now,
            owner_lease_minutes=self.config.owner_lease_minutes,
            skew_tolerance_seconds=self.config.skew_tolerance_seconds,
        )

    def holds_role(self, now: datetime | None = None) -> bool:
        """Whether Owner-only work may proceed RIGHT NOW.

        Answered from our own last written expiry against our own clock, so
        a hung or disconnected Owner stands down by itself. We stand down at
        our own `expires`; a challenger may only take over at
        `expires + skew_tolerance`, and that margin is the gap between the
        two — it is the reason a challenger's clock has to be more than a
        minute fast before anything overlaps.
        """
        if self._our_expiry is None:
            return False
        return (now or self.clock()) < self._our_expiry

    # --- writing ---

    def acquire(self):
        """Take the lease if it is free, expired or not credible."""
        for _ in range(_CAS_ATTEMPTS):
            now = self.clock()
            current = self.current()
            if isinstance(current, Uncertain):
                return current
            lease, version = current
            not_credible = None
            if lease is not None:
                verdict = self.verdict(lease, now)
                if verdict == LeaseVerdict.HELD:
                    if lease.owner == self.manager:
                        # Already ours (a restart inside the lease window).
                        self._our_expiry = _parse_our(lease)
                        return Acquired(lease, version)
                    return HeldByOther(lease.owner, lease)
                if verdict == LeaseVerdict.NOT_CREDIBLE:
                    not_credible = (
                        f"the lease held by {lease.owner or '<unnamed>'} expires at "
                        f"{lease.expires!r}, which no honest writer could have set "
                        f"(ceiling: {self.config.owner_lease_minutes}m + "
                        f"{self.config.skew_tolerance_seconds}s) — a clock is wrong"
                    )
            fresh = self._build(now, acquired=None if lease is None else lease)
            result = self.layer.write_state(
                LEASE_KEY, lease_to_json(fresh).encode(), version
            )
            if isinstance(result, Written):
                self._our_expiry = _parse_our(fresh)
                return Acquired(fresh, result.version, not_credible)
            if isinstance(result, Unavailable):
                return Uncertain(f"the lease write did not complete: {result.reason}")
            # Conflict: somebody wrote something. Re-read and decide again —
            # they may have taken the lease, or merely updated their status.
        return Uncertain("the state kept changing under us; no attempt settled")

    def renew(self):
        """Extend our own lease. Renewing is not re-acquiring."""
        for _ in range(_CAS_ATTEMPTS):
            now = self.clock()
            current = self.current()
            if isinstance(current, Uncertain):
                return current
            lease, version = current
            if lease is None:
                return LeaseLost("the lease file is gone")
            if lease.owner != self.manager:
                return LeaseLost("another Manager holds the lease", lease.owner)
            if self.verdict(lease, now) != LeaseVerdict.HELD:
                # Our own lease lapsed. This is NOT a renewal any more: the
                # role is up for election and we must stop acting as Owner
                # before deciding to stand again. Silently extending it here
                # would be the hung-Owner-holds-for-ever failure D-17 exists
                # to remove.
                self._our_expiry = None
                return LeaseLost("our own lease has lapsed; re-elect rather than renew")
            fresh = self._build(now, acquired=lease)
            result = self.layer.write_state(
                LEASE_KEY, lease_to_json(fresh).encode(), version
            )
            if isinstance(result, Written):
                self._our_expiry = _parse_our(fresh)
                return Renewed(fresh, result.version)
            if isinstance(result, Unavailable):
                return Uncertain(f"the renewal did not complete: {result.reason}")
        return Uncertain("the state kept changing under us; no renewal settled")

    def release(self):
        """Stand down deliberately: expire the lease now, in place.

        Not a delete. The file keeps saying who held it and when, which is
        what makes a promotion reconstructable afterwards, and the state
        layer has no delete precisely because history is the point.

        **The successor still waits out the skew margin**, because the file
        cannot say WHY the lease expired: a Manager reading `expires` in the
        past cannot tell "the holder finished" from "the holder's clock is
        ahead of mine", and §2.4.1's margin exists for the second. A clean
        handover therefore costs `skew_tolerance` of dead time. Removing
        that would need an explicit "released" marker, which §2.4.1 does not
        define — raised rather than invented here.
        """
        for _ in range(_CAS_ATTEMPTS):
            now = self.clock()
            current = self.current()
            if isinstance(current, Uncertain):
                return current
            lease, version = current
            if lease is None or lease.owner != self.manager:
                self._our_expiry = None
                return LeaseLost("we no longer hold the lease")
            stood_down = OwnerLease(
                owner=lease.owner,
                acquired=lease.acquired,
                expires=stamp(now),
                priority=lease.priority,
                extra=lease.extra,
            )
            result = self.layer.write_state(
                LEASE_KEY, lease_to_json(stood_down).encode(), version
            )
            if isinstance(result, Written):
                self._our_expiry = None
                return Released(result.version)
            if isinstance(result, Unavailable):
                # We have stopped acting as Owner either way; the lease will
                # expire on its own. Say so rather than claiming a clean exit.
                self._our_expiry = None
                return Uncertain(f"the release did not complete: {result.reason}")
        return Uncertain("the state kept changing under us; no release settled")

    def _build(self, now: datetime, acquired: OwnerLease | None) -> OwnerLease:
        ours = acquired is not None and acquired.owner == self.manager
        keep_acquired = (
            acquired.acquired if ours and acquired.acquired else stamp(now)
        )
        return OwnerLease(
            owner=self.manager,
            acquired=keep_acquired,
            expires=stamp(now + timedelta(minutes=self.config.owner_lease_minutes)),
            # Written for audit, never read for a decision (D-60).
            priority=_priority_of(self.manager, self.config),
            extra=acquired.extra if ours else {},
        )


def _priority_of(manager: str, config: CoordinationConfig) -> int:
    try:
        return config.managers.index(manager)
    except ValueError:
        # Not in the list at all. Recorded as such rather than as 0, which
        # would read as "highest priority" in the audit trail.
        return -1


def _parse_our(lease: OwnerLease) -> datetime | None:
    return parse_timestamp(lease.expires)


def renewal_interval(config: CoordinationConfig) -> float:
    """A third of the lease: room for two failed renewals before expiry."""
    return max(30.0, config.owner_lease_minutes * 60 / 3)


class RenewalLoop:
    """Renews in the background until the lease is lost or we stop.

    The loop OWNS nothing: it calls `renew()` and reports. When the lease is
    lost it stops and tells the caller once — it never re-acquires on its
    own, because standing for election again is a decision about priority,
    not about timing.
    """

    def __init__(self, holder: OwnerLeaseHolder, on_lost=None, interval=None) -> None:
        self.holder = holder
        self.on_lost = on_lost
        self.interval = interval or renewal_interval(holder.config)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last = None

    def tick(self):
        """One renewal. Separated from the thread so it is testable without
        one — every property that matters is a property of this."""
        result = self.holder.renew()
        self.last = result
        if isinstance(result, LeaseLost):
            self._stop.set()
            if self.on_lost:
                self.on_lost(result)
        return result

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"rite-lease-{self.holder.manager}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
