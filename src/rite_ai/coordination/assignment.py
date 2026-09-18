"""The Owner assigns work to a Manager (P2-3a, SPEC §2.3).

"Assigns work by setting a label with a Worker's name (or a Manager's, for the
Manager to sub-assign)" — so assignment is not state-branch data at all. §3.3.3
is explicit: active assignments are ticket labels, "already external", and
duplicating them into the state branch would give two answers to one question.

What the state layer provides is the other half: who is alive and how loaded
they are (P2-4a's heartbeats). This module joins the two — read status, choose,
label — and refuses to choose when it cannot tell.

**Why fewest-in-flight, and why config order breaks ties.** Workers are
fungible (§5.3.4), so there is nothing to match against a ticket; the only
useful signal is load, which is why the heartbeat carries a count at all. When
two Managers are equally loaded the tie goes to `coordination.managers` order,
the same list priority comes from everywhere else (D-60) — never to a
timestamp, which across machines is exactly what §2.4.1 says cannot be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rite_ai.coordination.heartbeat import Liveness, is_stalled, liveness
from rite_ai.coordination.schemas import status_from_json
from rite_ai.coordination.state_layer import Present, StateLayer


@dataclass
class ManagerView:
    """What the Owner can see of one Manager, and whether it may be given work."""

    name: str
    live: Liveness
    in_flight: int = 0
    workers: list[str] | None = None
    assignable: bool = False
    why_not: str = ""


def manager_views(
    layer: StateLayer,
    names: list[str],
    *,
    now: datetime,
    interval_minutes: int,
    stall_threshold: int,
) -> list[ManagerView]:
    """One view per configured Manager, in `coordination.managers` order."""
    from rite_ai.coordination.heartbeat import status_key

    views: list[ManagerView] = []
    for name in names:
        live = liveness(layer, name, now=now, interval_minutes=interval_minutes)
        if not live.known:
            views.append(
                ManagerView(name, live, why_not=f"cannot tell — {live.detail}")
            )
            continue
        if is_stalled(live, stall_threshold=stall_threshold):
            views.append(ManagerView(name, live, why_not=f"stalled — {live.detail}"))
            continue
        read = layer.read_state(status_key(name))
        status = (
            status_from_json(read.value.decode("utf-8", errors="replace"))
            if isinstance(read, Present)
            else None
        )
        if status is None:
            views.append(
                ManagerView(name, live, why_not="status could not be read on re-read")
            )
            continue
        views.append(
            ManagerView(
                name,
                live,
                in_flight=status.in_flight,
                workers=list(status.workers),
                assignable=True,
            )
        )
    return views


def choose_manager(
    views: list[ManagerView],
    *,
    roles: list | None = None,
    stage: str = "",
) -> ManagerView | None:
    """The assignable Manager with the fewest tasks in flight, ties going to
    the order the views came in — which is config order.

    **Duty first, then load** (RL-5, RL-T33). When the project declares what
    its Managers are FOR, a task's stage decides which of them may take it at
    all: a decomposition waiting for review is not work an executor can take,
    however idle it is. Load only breaks the tie among those that may.

    `roles` and `stage` are optional because a project whose Managers are all
    alike has nothing to route between (RL-4) — that is today's behaviour, and
    it stays exactly as it was.
    """
    assignable = [v for v in views if v.assignable]
    if not assignable:
        return None
    if roles and stage:
        from rite_ai.config.managers import effective_duties
        from rite_ai.local.duty_router import STAGE_DUTY

        duty = STAGE_DUTY.get(stage)
        if duty is not None:
            by_name = {r.name: r for r in roles}
            holders = [
                v
                for v in assignable
                if v.name in by_name
                and duty in effective_duties(by_name[v.name], len(roles))
            ]
            # No holder is NOT "fall back to anyone": giving a task to a
            # Manager that does not hold its duty is the mis-route the router
            # exists to prevent, and it would look like success.
            assignable = holders
            if not assignable:
                return None
    return min(assignable, key=lambda v: (v.in_flight, views.index(v)))


@dataclass
class Assigned:
    ticket: str
    manager: str


@dataclass
class NotAssigned:
    reason: str
    per_manager: dict[str, str] | None = None


def assign_to_manager(
    backend,
    ticket_id: str,
    views: list[ManagerView],
    *,
    roles: list | None = None,
    stage: str = "",
) -> Assigned | NotAssigned:
    """Label `ticket_id` with the chosen Manager's name (§2.3).

    The label IS the assignment, so a backend that refuses the write leaves the
    ticket unassigned — reported, never reported as assigned.

    `roles` and `stage` route by duty before load (RL-T33). Without them this
    behaves exactly as it did, which is what a single-tier project wants."""
    from rite_ai.tickets import BackendError

    chosen = choose_manager(views, roles=roles, stage=stage)
    if chosen is None and roles and stage:
        # Two different causes were about to share one sentence. "Nobody is
        # free" is a wait; "nobody holds this duty" is a configuration that
        # will never progress, and a reader who cannot tell them apart waits
        # for the second one.
        from rite_ai.config.managers import effective_duties
        from rite_ai.local.duty_router import STAGE_DUTY

        duty = STAGE_DUTY.get(stage)
        if duty is not None:
            holders = [r.name for r in roles if duty in effective_duties(r, len(roles))]
            if not holders:
                return NotAssigned(
                    f"no manager holds {duty}, which {stage} needs — "
                    f"{ticket_id} would wait for ever. Give a manager that "
                    "duty, or a preset that includes it"
                )
            return NotAssigned(
                f"the manager(s) holding {duty} ({', '.join(holders)}) cannot "
                f"be given work now",
                {v.name: v.why_not for v in views if v.name in holders} or None,
            )
    if chosen is None:
        return NotAssigned(
            "no Manager can be given work",
            {v.name: v.why_not for v in views} or None,
        )
    result = backend.label(ticket_id, [chosen.name])
    if isinstance(result, BackendError):
        return NotAssigned(f"could not label {ticket_id}: {result.message}")
    return Assigned(ticket_id, chosen.name)
