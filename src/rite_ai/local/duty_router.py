"""Which Manager a task goes to (RL-T5, decisions RL-4, RL-5).

Three routers with three tiebreaks, and this is the first of them: **duty,
then capability and permission, then free capacity.** Duty comes first because
it decides which STAGE of the pipeline a task belongs to — a decomposition
waiting for review is not work an executor can take, however idle it is.

**Targets Managers, never Workers** once engines or duties differ (RL-4). Within
a Manager, Worker choice is "any free one" and there is no analysis: that is
what keeps SPEC §5.3.4's fungibility intact. A project whose Managers are all
alike keeps today's behaviour, including Worker labels, because there is
nothing to route between.

**Capacity is passed in, not discovered here.** P2-4a's heartbeat does carry
an in-flight count — `publish_heartbeat(..., in_flight=...)` — and the harness
supplies it from there. Taking it as an argument rather than reading it here
keeps the routing decision testable without a fleet, and keeps this module's
only dependency the duty vocabulary.

(An earlier version of this docstring said the in-flight count was not built.
That was wrong: it was grepped for in `coordination/__init__.py` alone rather
than across the package.)
"""

from __future__ import annotations

from dataclasses import dataclass

from rite_ai.config.managers import (
    DECOMPOSE,
    EXECUTE,
    INTEGRATE,
    PLAN_REVIEW,
    STEP_REVIEW,
    ManagerRole,
    effective_duties,
)

# What stage of the pipeline a task is at, and therefore the duty that may take
# it. Closed, and keyed to the duty vocabulary rather than to free strings.
STAGE_DUTY: dict[str, str] = {
    "decompose": DECOMPOSE,
    "plan-review": PLAN_REVIEW,
    "execute": EXECUTE,
    "step-review": STEP_REVIEW,
    "integrate": INTEGRATE,
}


@dataclass(frozen=True)
class Candidate:
    """A Manager as the router sees it: what it is for, and how busy it is."""

    role: ManagerRole
    in_flight: int = 0
    capacity: int = 1
    # A Manager that has refused this task already (P2-4c). It is not offered
    # the same task twice — a refusal is an answer, not a transient failure.
    refused: tuple[str, ...] = ()

    @property
    def free(self) -> int:
        return max(0, self.capacity - self.in_flight)


@dataclass(frozen=True)
class Task:
    id: str
    stage: str
    # Set for a step or plan review, and for execution of a subtask: the
    # Manager that produced the thing being reviewed or planned. Review is
    # never staffed by whoever proposed it (SPEC §7.1, RL-6, RL-18).
    produced_by: str = ""


@dataclass(frozen=True)
class Routed:
    manager: str


@dataclass(frozen=True)
class Queued:
    """Nobody may take it now. `reason` is for `rite status`, because a queued
    task with no reason is indistinguishable from a lost one."""

    reason: str


@dataclass(frozen=True)
class Unroutable:
    """No Manager in this project could EVER take it — a configuration fault,
    not a busy moment. Never queued silently: a task waiting for a duty nobody
    holds waits for ever."""

    reason: str


def _independent(candidate: Candidate, task: Task, roles: list[ManagerRole]) -> bool:
    """Whether this candidate may review what `task.produced_by` produced.

    RL-6 wants a different Manager AND a different engine for plan review: a
    reviewer sharing the decomposer's engine shares the blind spots of the plan
    it is checking. RL-18 wants a different instance for step review and for a
    planner's own work. Both are "not the author", differing only in how far
    apart the two must be.
    """
    if not task.produced_by or candidate.role.name != task.produced_by:
        if task.stage != "plan-review":
            return True
        author = next((r for r in roles if r.name == task.produced_by), None)
        if author is None:
            return True
        return candidate.role.engine != author.engine
    return False


def route(
    task: Task,
    candidates: list[Candidate],
) -> Routed | Queued | Unroutable:
    """Where this task goes, or why it goes nowhere.

    Three outcomes rather than two, deliberately. "Queued" and "nobody can ever
    take this" look identical from a queue and mean opposite things: one is a
    wait, the other is a configuration that will never progress.
    """
    stage_duty = STAGE_DUTY.get(task.stage)
    if stage_duty is None:
        return Unroutable(
            f"{task.stage!r} is not a stage rite routes — "
            f"{', '.join(sorted(STAGE_DUTY))}"
        )
    roles = [c.role for c in candidates]
    declared = len(roles)
    holders = [
        c for c in candidates if stage_duty in effective_duties(c.role, declared)
    ]
    if not holders:
        return Unroutable(
            f"no manager holds {stage_duty} — {task.id} would wait for ever. "
            "Give a manager that duty, or a preset that includes it"
        )

    eligible = [c for c in holders if _independent(c, task, roles)]
    if not eligible:
        # The author is the only holder. Queuing would wait for a reviewer who
        # cannot arrive; the project has to change, not wait.
        return Unroutable(
            f"the only manager holding {stage_duty} is {task.produced_by!r}, "
            "which produced the thing being reviewed. A review staffed by its "
            "author is not a review (SPEC §7.1)"
        )

    willing = [c for c in eligible if task.id not in c.refused]
    if not willing:
        return Queued(f"every manager holding {stage_duty} has refused {task.id}")

    free = [c for c in willing if c.free > 0]
    if not free:
        return Queued(
            f"every manager holding {stage_duty} is at capacity "
            f"({sum(c.in_flight for c in willing)} in flight)"
        )

    # Free capacity is the tiebreak (RL-5): most free first, then
    # by name so two machines reading the same state make the same choice. A
    # random tiebreak would be unreproducible in exactly the situation someone
    # is trying to explain afterwards.
    best = sorted(free, key=lambda c: (-c.free, c.role.name))[0]
    return Routed(best.role.name)


def targets_managers(roles: list[ManagerRole]) -> bool:
    """Whether this project routes to Managers at all (RL-4).

    Once engines or duties differ, assignment targets Managers and a Worker
    label would bypass routing — the routing design's Seam 3. While they are
    all alike, today's behaviour stands: there is nothing to route between, and
    SPEC §2.3's Worker labels keep working.
    """
    if len(roles) <= 1:
        return False
    engines = {r.engine for r in roles}
    duties = {effective_duties(r, len(roles)) for r in roles}
    return len(engines) > 1 or len(duties) > 1
