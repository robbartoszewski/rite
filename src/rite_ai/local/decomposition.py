"""The decomposition artifact (RL-T4, and the pipeline RL-6 to RL-10 gate).

A ticket broken into subtasks, each with a scope, a verify command and the
decisions it cites — plus who approved the plan and what has happened to each
subtask since. Durable from the moment it exists: SPEC §2.0 says a session's
memory is not a record, and this is the thing three gates read.

**It is evidence, never instruction** (the design's channel segregation,
RL-T26). Plan review, step review and recomposition all read this artifact,
and nothing a decomposer writes here
may tell a reviewer what to check or what to skip. That is why there is no
`notes`, `guidance` or `reviewer_hint` field: a field like that is a channel
from the tier being reviewed to the tier reviewing it, and the earlier
prototype measurably lost review depth exactly that way.

Written through the state-layer interface (D-20, RL-35) — no git call and no
backend name appears here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Conflict,
    Present,
    StateLayer,
    Unavailable,
    Written,
)

# Subtask status. A closed set: these are read by gates, and a gate cannot key
# on a string a decomposer invented.
PLANNED = "planned"
RUNNING = "running"
VERIFIED = "verified"
FAILED = "failed"
ACCEPTED = "accepted"
STATUSES = (PLANNED, RUNNING, VERIFIED, FAILED, ACCEPTED)

# Plan approval (RL-6). `PENDING` is not "approved by nobody yet and running
# anyway": nothing is released until a plan-review holder approves.
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
APPROVALS = (PENDING, APPROVED, REJECTED)

FORMAT_VERSION = 1


def key_for(ticket: str) -> str:
    """Where this ticket's decomposition lives in the state layer."""
    return f"decompositions/{ticket}.json"


@dataclass(frozen=True)
class Subtask:
    id: str
    intent: str
    # The paths this subtask may touch. Scope is what makes a conflict at
    # composition evidence about the PLAN (the design's composition gate,
    # RL-T30) rather than a chore.
    scope: tuple[str, ...] = ()
    # Mechanical, and rite runs it rather than trusting a report (RL-7).
    verify: str = ""
    cites: tuple[str, ...] = ()
    status: str = PLANNED
    # Only work counts (RL-47). An endpoint that was down is not an attempt.
    attempts: int = 0
    branch: str = ""
    last_failure: str = ""


@dataclass(frozen=True)
class Decomposition:
    ticket: str
    subtasks: tuple[Subtask, ...] = ()
    decomposed_by: str = ""
    approval: str = PENDING
    approved_by: str = ""
    # Why a plan review rejected it, or why a recomposition sent it back. Kept
    # because RL-10 counts returns per parent ticket, and a count with no
    # reasons is a number nobody can act on.
    returns: tuple[str, ...] = ()
    format_version: int = FORMAT_VERSION

    @property
    def released(self) -> bool:
        """Whether any subtask may run. The gate, in one place (RL-6)."""
        return self.approval == APPROVED

    def subtask(self, subtask_id: str) -> Subtask | None:
        return next((s for s in self.subtasks if s.id == subtask_id), None)


def problems(plan: Decomposition) -> list[str]:
    """What plan review is told to check mechanically, so it can spend its
    attention on whether the slicing is right (RL-6, RL-7).

    A verify that cannot fail is the easiest way to fake a pass, and a scope
    that overlaps another subtask's is the mis-slice composition would find
    later — both are cheaper to catch here.
    """
    found: list[str] = []
    if not plan.subtasks:
        found.append("the decomposition has no subtasks")
    seen: dict[str, str] = {}
    for sub in plan.subtasks:
        if not sub.verify.strip():
            found.append(f"{sub.id}: no verify command — nothing could say it is done")
        if not sub.scope:
            found.append(
                f"{sub.id}: no scope — composition cannot tell a conflict from a "
                "coincidence, and a mis-slice would only appear at the merge"
            )
        if not sub.intent.strip():
            found.append(
                f"{sub.id}: no intent — a reviewer has nothing to check it against"
            )
        for path in sub.scope:
            if path in seen and seen[path] != sub.id:
                found.append(
                    f"{sub.id} and {seen[path]} both claim {path} — two subtasks "
                    "editing one path were two subtasks that should not have been "
                    "separate"
                )
            seen[path] = sub.id
    ids = [s.id for s in plan.subtasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        found.append(f"duplicate subtask id(s): {', '.join(duplicates)}")
    return found


def render(plan: Decomposition) -> bytes:
    """Deterministic bytes: sorted keys, one trailing newline. Two runs that
    decided the same thing produce the same value, so a no-op write is visible
    as a no-op rather than as a change."""
    body = {
        "format_version": plan.format_version,
        "ticket": plan.ticket,
        "decomposed_by": plan.decomposed_by,
        "approval": plan.approval,
        "approved_by": plan.approved_by,
        "returns": list(plan.returns),
        "subtasks": [
            {
                "id": s.id,
                "intent": s.intent,
                "scope": list(s.scope),
                "verify": s.verify,
                "cites": list(s.cites),
                "status": s.status,
                "attempts": s.attempts,
                "branch": s.branch,
                "last_failure": s.last_failure,
            }
            for s in plan.subtasks
        ],
    }
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse(raw: bytes) -> Decomposition | str:
    """A decomposition, or why these bytes are not one.

    Refused rather than repaired: every gate downstream reads this, and a
    half-understood plan is a plan whose gates check something else.
    """
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return f"not readable as a decomposition ({e})"
    if not isinstance(body, dict):
        return "not readable as a decomposition (not an object)"
    version = body.get("format_version")
    if version != FORMAT_VERSION:
        return (
            f"written in format {version!r}, and this rite reads "
            f"{FORMAT_VERSION} — upgrade rite rather than reading it as this one"
        )
    approval = body.get("approval", PENDING)
    if approval not in APPROVALS:
        return f"approval {approval!r} is not one of {', '.join(APPROVALS)}"
    subtasks = []
    for item in body.get("subtasks", []):
        if not isinstance(item, dict) or not item.get("id"):
            return "a subtask has no id"
        status = item.get("status", PLANNED)
        if status not in STATUSES:
            return (
                f"{item['id']}: status {status!r} is not one of {', '.join(STATUSES)}"
            )
        attempts = item.get("attempts", 0)
        if type(attempts) is not int or attempts < 0:
            return f"{item['id']}: attempts must be a whole number"
        subtasks.append(
            Subtask(
                id=str(item["id"]),
                intent=str(item.get("intent", "")),
                scope=tuple(item.get("scope", ())),
                verify=str(item.get("verify", "")),
                cites=tuple(item.get("cites", ())),
                status=status,
                attempts=attempts,
                branch=str(item.get("branch", "")),
                last_failure=str(item.get("last_failure", "")),
            )
        )
    return Decomposition(
        ticket=str(body.get("ticket", "")),
        subtasks=tuple(subtasks),
        decomposed_by=str(body.get("decomposed_by", "")),
        approval=approval,
        approved_by=str(body.get("approved_by", "")),
        returns=tuple(body.get("returns", ())),
    )


@dataclass
class Read:
    plan: Decomposition | None = None
    version: str = ABSENT
    error: str = ""
    unavailable: str = ""


def read(state: StateLayer, ticket: str) -> Read:
    """The stored decomposition, distinguishing three things a caller must not
    confuse: there is none, there is one, and nobody could tell."""
    result = state.read_state(key_for(ticket))
    if isinstance(result, Unavailable):
        return Read(unavailable=result.reason)
    if isinstance(result, Absent):
        return Read(version=result.version)
    assert isinstance(result, Present)
    parsed = parse(result.value)
    if isinstance(parsed, str):
        return Read(version=result.version, error=parsed)
    return Read(plan=parsed, version=result.version)


def write(
    state: StateLayer, plan: Decomposition, expected_version: str
) -> Written | Conflict | Unavailable:
    """Compare-and-swap, always. Two Managers may touch one decomposition — a
    step review accepting a subtask while an executor reports another — and a
    last-writer-wins store would drop one of them silently."""
    return state.write_state(key_for(plan.ticket), render(plan), expected_version)


def with_subtask(plan: Decomposition, subtask: Subtask) -> Decomposition:
    """`plan` with this subtask replaced by id, or appended."""
    if plan.subtask(subtask.id) is None:
        return replace(plan, subtasks=(*plan.subtasks, subtask))
    return replace(
        plan,
        subtasks=tuple(s if s.id != subtask.id else subtask for s in plan.subtasks),
    )


def returned_to_plan_review(plan: Decomposition, reason: str) -> Decomposition:
    """Composition conflict, recomposition failure, or a rejection — all of
    them come back here (RL-8, RL-T30), and all of them count (RL-10).

    Approval drops with the return: a plan that failed its parent's verify is
    not an approved plan with a note attached, and nothing may run from it
    until a plan-review holder approves it again.
    """
    return replace(
        plan,
        approval=PENDING,
        approved_by="",
        returns=(*plan.returns, reason),
    )
