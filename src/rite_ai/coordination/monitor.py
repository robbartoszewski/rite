"""The Manager's coordination loop (P2-4d, §2.4.1, D-16).

One `tick()` is everything a Manager owes the fleet at one moment: publish
that it is alive, renew the lease if it holds it, promote if the role is
free and it is next in priority, and ask for the role back if it outranks
whoever has it.

**A tick never hands the role over by itself.** D-43 puts the handover
boundary at one ticket-backend write or one board-state transition, and only
the caller knows where it is in a sequence — so a request addressed to us is
REPORTED, and `hand_over_when` lets a caller that knows it is at a boundary
opt in. A timer that interrupts work mid-operation produces exactly the
inconsistency graceful demotion exists to avoid.

**A promotion immediately hands over the outgoing Owner's work** (P2-3c,
D-14's third trigger). That is the whole reason the election reports whose
lease it displaced: promoting and leaving the old Owner's tickets assigned
to a machine that is gone is the state `rite stop` exists to prevent.

**A tick also hands this Manager's work to its Workers** (P2-4b/P2-4c), when
the caller supplies a board and a schedule. Distribution that nothing calls is
the same defect D-14 records against `perform_handover`: a function whose
caller was never written, correct and inert. The Owner is a Manager too, so
this runs whatever the role turned out to be.

**Asking is done once, not every tick.** A promotion request is a file; it
does not need rewriting every ten seconds, and the churn would conflict with
every other writer on a whole-ref CAS (§2.4.2) for no gain.

Nothing here retries or sleeps on its own beyond the interval: a tick that
cannot establish the truth reports that and changes nothing, because every
alternative — assuming the role is free, assuming we still hold it — is a
split brain.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rite_ai.config.models import HeartbeatConfig, ScheduleConfig
from rite_ai.coordination.demotion import (
    Asked,
    HandedOver,
    hand_over,
    pending_request,
    request_promotion,
)
from rite_ai.coordination.demotion import Unknown as DemotionUnknown
from rite_ai.coordination.distribution import Distributed, distribute
from rite_ai.coordination.election import (
    Deferred,
    NotEligible,
    NotOwner,
    Promoted,
    StillOwner,
    Unknown,
    stand_for_owner,
)
from rite_ai.coordination.heartbeat import publish_heartbeat
from rite_ai.coordination.lease import LeaseLost, OwnerLeaseHolder, Renewed, Uncertain
from rite_ai.coordination.publish import NotPublished
from rite_ai.coordination.takeover import ToldTheBoard, hand_over_outgoing_owner


@dataclass
class Tick:
    """What this Manager did, and what it could not do. Read by a caller
    that has to tell a human; every field is named rather than counted."""

    owner: bool = False
    action: str = "nothing"
    detail: str = ""
    promoted_from: str = ""
    handed_over_to: str = ""
    asked_for_promotion: bool = False
    asked_to_hand_over: bool = False
    handover: ToldTheBoard | None = None
    handouts: list[tuple[str, str]] = field(default_factory=list)
    """(ticket, worker) given out this tick (P2-4b)."""
    refused: dict[str, str] = field(default_factory=dict)
    """Tickets returned to the pool, and why (P2-4c)."""
    held_back: dict[str, str] = field(default_factory=dict)
    """Still ours, waiting for a slot. Not a problem — a queue."""
    distribution: str = ""
    """Why distribution was not attempted, or "" when it was.

    NOT a problem, which is why it is not in `problems`: a monitor with no
    board is a configuration, not a fault. But it is not nothing either —
    "no board configured" and "nothing to hand out" produce identical ticks
    otherwise, and the first is why an overnight run can do nothing all night
    while every tick reports success."""
    problems: list[str] = field(default_factory=list)
    """Anything that could not be established. NOT fatal, and not silent:
    a tick that could not read the state must say so or a Manager looks
    healthy while coordinating with nothing."""


class ManagerMonitor:
    def __init__(
        self,
        holder: OwnerLeaseHolder,
        *,
        root: Path | None = None,
        heartbeat: HeartbeatConfig | None = None,
        status: object = None,
        hand_over_when=None,
        interval: float = 60.0,
        backend=None,
        workers: list[str] | None = None,
        schedule: ScheduleConfig | None = None,
        modules: set[str] | None = None,
        draining: str = "",
    ) -> None:
        self.holder = holder
        self.root = root
        self.heartbeat = heartbeat or HeartbeatConfig()
        # A callable returning (workers, in_flight). Without it the loop
        # does not publish liveness — and says so rather than pretending.
        self.status = status
        # A callable returning True when the caller is at an operation
        # boundary (D-43). Without it, a request is reported, never acted on.
        self.hand_over_when = hand_over_when
        self.interval = interval
        # The board half (P2-4b/P2-4c). Absent, a tick does the coordination
        # work and says nothing about distribution rather than implying none
        # was needed.
        self.backend = backend
        self.workers = workers or []
        self.schedule = schedule
        self.modules = modules
        self.draining = draining
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: Tick | None = None

    def tick(self, now: datetime | None = None) -> Tick:
        now = now or self.holder.clock()
        result = Tick()
        self._beat(now, result)

        current = self.holder.current()
        if isinstance(current, Uncertain):
            result.action = "unknown"
            result.detail = current.reason
            result.problems.append(current.reason)
            return result
        lease, _ = current
        we_hold_it = lease is not None and lease.owner == self.holder.manager

        if we_hold_it:
            self._as_owner(now, result)
        else:
            self._as_manager(now, result)
        # Whatever the role turned out to be, this machine is a Manager and
        # its assigned tickets are waiting.
        self._distribute(now, result)
        return result

    # --- the two halves ---

    def _as_owner(self, now: datetime, result: Tick) -> Tick:
        result.owner = True
        asked = pending_request(self.holder.layer, self.holder.manager)
        if isinstance(asked, DemotionUnknown):
            result.problems.append(asked.reason)
        elif isinstance(asked, Asked):
            result.asked_to_hand_over = True
            result.detail = f"{asked.request.requester} has asked for the role"
            if self.hand_over_when is not None and self.hand_over_when():
                handed = hand_over(self.holder, now=now)
                if isinstance(handed, HandedOver):
                    result.owner = False
                    result.action = "handed over"
                    result.handed_over_to = handed.to
                    return result
                result.problems.append(getattr(handed, "reason", "handover refused"))

        renewed = self.holder.renew()
        if isinstance(renewed, Renewed):
            result.action = "renewed"
        elif isinstance(renewed, LeaseLost):
            result.owner = False
            result.action = "lost the lease"
            result.detail = renewed.reason
        else:
            # Uncertain. We keep the role until our own clock says otherwise
            # (§2.4.1) — silence is not a refusal.
            result.action = "renewal uncertain"
            result.owner = self.holder.holds_role(now)
            result.problems.append(renewed.reason)
        return result

    def _as_manager(self, now: datetime, result: Tick) -> Tick:
        outcome = stand_for_owner(self.holder, heartbeat=self.heartbeat, now=now)

        if isinstance(outcome, Promoted):
            result.owner = True
            result.action = "promoted"
            result.promoted_from = outcome.previous_owner
            if outcome.displaced_not_credible:
                # D-59: somebody's clock is wrong, and this is the only
                # signal that will say so.
                result.problems.append(outcome.displaced_not_credible)
            if outcome.unseen:
                result.detail = "could not see: " + ", ".join(outcome.unseen)
            self._hand_over_their_work(outcome, result)
            return result

        if isinstance(outcome, StillOwner):
            # The lease says us but `current()` did not: a race with our own
            # renewal. Nothing to do; the next tick reads it as ours.
            result.owner = True
            result.action = "still owner"
            return result

        if isinstance(outcome, Deferred):
            result.action = "deferred"
            result.detail = f"{outcome.to} is alive and outranks us"
            return result

        if isinstance(outcome, NotOwner):
            result.action = "not owner"
            result.detail = f"{outcome.owner} holds the lease"
            if self._outranks_incumbent(outcome.owner):
                self._ask(outcome.owner, now, result)
            return result

        if isinstance(outcome, NotEligible):
            result.action = "not eligible"
            result.detail = outcome.reason
            return result

        if isinstance(outcome, Unknown):
            result.action = "unknown"
            result.detail = outcome.reason
            result.problems.append(outcome.reason)
        return result

    # --- the pieces ---

    def _distribute(self, now: datetime, result: Tick) -> None:
        missing = [
            name
            for name, value in (
                ("a board backend", self.backend),
                ("a schedule", self.schedule),
                ("a project root", self.root),
            )
            if value is None
        ]
        if missing:
            result.distribution = (
                "not attempted: this monitor was constructed without "
                f"{', '.join(missing)}"
            )
            return
        handed = distribute(
            self.root,
            self.backend,
            manager=self.holder.manager,
            workers=self.workers,
            schedule=self.schedule,
            now=now,
            modules=self.modules,
            draining=self.draining,
        )
        if not isinstance(handed, Distributed):
            result.problems.append(handed.reason)
            return
        result.handouts = handed.handouts
        result.refused = handed.refused
        result.held_back = handed.held_back
        for ticket, why in handed.could_not_refuse.items():
            # Neither ours to do nor the board's to reassign. It sits until
            # somebody is told, so somebody is told.
            result.problems.append(f"could not return {ticket}: {why}")

    def _beat(self, now: datetime, result: Tick) -> None:
        if self.status is None:
            return
        workers, in_flight = self.status()
        published = publish_heartbeat(
            self.holder.layer,
            self.holder.manager,
            workers=list(workers),
            in_flight=in_flight,
            now=now,
        )
        if isinstance(published, NotPublished):
            # A heartbeat that did not publish is not cosmetic: every other
            # Manager's election reads it, and a Manager that looks silent
            # gets its work handed over (P2-3b).
            problem = f"the heartbeat did not publish: {published.reason}"
            if published.may_have_landed:
                # The lost-ack distinction again. "It failed" and "we never
                # heard" are different things to tell a human.
                problem += " (it may have landed — re-read before deciding)"
            result.problems.append(problem)

    def _hand_over_their_work(self, outcome: Promoted, result: Tick) -> None:
        """D-14's third trigger, and §2.4.2 step 4's promotion event.

        Both, because they are one write: the event is the record that the
        handover happens under, and a promotion with nothing to hand over
        still has to be recorded. Skipping the whole call when there was no
        previous Owner — which is what this did — meant a FIRST election
        left no trace at all, so "when did this machine become Owner?" had
        no answer anywhere.
        """
        if self.root is None:
            return
        handed = hand_over_outgoing_owner(
            self.root,
            self.holder.layer,
            new_owner=self.holder.manager,
            previous_owner=outcome.previous_owner,
            promotion_reason=outcome.why,
        )
        if isinstance(handed, ToldTheBoard):
            result.handover = handed
            if handed.queued:
                # The board was NOT updated. Say which tickets, so a caller
                # can tell a human rather than reporting a clean promotion.
                result.problems.append(
                    "board not updated for " + ", ".join(handed.queued)
                )
        else:
            result.problems.append(handed.reason)

    def _ask(self, incumbent: str, now: datetime, result: Tick) -> None:
        existing = pending_request(self.holder.layer, incumbent)
        if isinstance(existing, DemotionUnknown):
            result.problems.append(existing.reason)
            return
        if isinstance(existing, Asked):
            if existing.request.requester == self.holder.manager:
                return  # already asked; asking again is churn on a shared ref
            if not self._outranks(self.holder.manager, existing.request.requester):
                return  # someone with a better claim is already asking
        asked = request_promotion(
            self.holder.layer,
            self.holder.manager,
            incumbent,
            managers=self.holder.config.managers,
            now=now,
        )
        if isinstance(asked, Asked):
            result.asked_for_promotion = True
        else:
            result.problems.append(getattr(asked, "reason", "the request failed"))

    def _outranks_incumbent(self, incumbent: str) -> bool:
        return self._outranks(self.holder.manager, incumbent)

    def _outranks(self, one: str, other: str) -> bool:
        managers = self.holder.config.managers
        if one not in managers or other not in managers:
            return False
        return managers.index(one) < managers.index(other)

    # --- running it ---

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"rite-monitor-{self.holder.manager}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.last = self.tick()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
