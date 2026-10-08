"""Level 2, required and persisted — the APPROACH as a stage (SCRUM-72e).

§3.3a's table: the Level-2 approach was **"Optional. Fail-open by DD-2.4 and
never persisted. The §2.4 boundary check compares the plan with itself, so it
cannot catch drift."** Robert's §5.5 decision overrides DD-2.4: "cannot skip a
stage" requires it.

Three things were wrong and they are separate:

🔴 **1. Fail-open.** `_approach_for` returned `("", why)` for every failure and
the subtask ran anyway, so a stage the definition of done names as mandatory
was one any endpoint hiccup removed. It is a refusal now — the step does not
run without an approach. ⚠ **But a missing approach is still NOT a failed
subtask** (RL-47): the step is refused and reported, the subtask keeps its
status, and no attempt is burned. An endpoint that was down did not produce a
bad approach, it produced none.

🔴 **2. Never persisted**, so nothing afterwards could show the stage had
happened — and §3.3a's whole premise is that each stage is "a persisted state
with a guard".

⚠ **Persisted HERE, deliberately NOT in the `Decomposition`.** §3.3a says
"persisted" and does not say where; `approach.py`'s own docstring says why it
must not be the plan: *"If it is written into `Decomposition`, the gates start
reading the executor's own words"* — RL-T26's channel segregation, and the
earlier prototype measurably lost review depth exactly that way. So the
approach gets its own state key. Plan review, step review and recomposition
read the `Decomposition` and see nothing an executor wrote; the guard reads
these keys. Both properties hold, which a literal "persist it into the plan"
would have traded one for the other.

🔴 **3. The boundary check compared the plan with itself.** `step` read the
plan once and then asked `boundary_problem(plan.subtask(id), subtask)` — both
sides of the comparison came from the same read, so it could only ever pass.
What it is FOR is catching a subtask edited between approval and execution,
and for that the comparison needs a value from approval time. So
`approve_plan` records a digest per subtask, here, and the guard compares the
executing subtask against THAT.

⚠ **The digests live where the plan does: outside every Manager's grant**
(`plan_state`, SCRUM-72c). A digest a Manager could rewrite is a digest that
proves nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Present,
    StateLayer,
    Unavailable,
    Written,
)
from rite_ai.local.decomposition import Subtask

FORMAT_VERSION = 1

BOUND_FIELDS = ("id", "intent", "scope", "verify", "cites")
"""The load-bearing fields, and the ones `approach.boundary_problem` already
names: `scope` is the committer's allowlist and `verify` is the only thing
RL-7 trusts, so a change to either turns the gates into decoration. `status`,
`attempts`, `branch` and `last_failure` are the harness's own bookkeeping and
move legitimately during a turn, so they are not part of the digest."""


def digest(subtask: Subtask) -> str:
    """A content address for the part of `subtask` that was approved.

    Canonical JSON so two equal subtasks digest equally whatever order a
    writer happened to use, and sha256 so a digest cannot be worked backwards
    into a subtask that is not the one approved.
    """
    body = {
        field: list(value) if isinstance(value, tuple) else value
        for field, value in ((f, getattr(subtask, f)) for f in BOUND_FIELDS)
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def approvals_key(ticket: str) -> str:
    return f"approvals/{ticket}.json"


def approach_key(ticket: str, subtask_id: str) -> str:
    return f"approaches/{ticket}/{subtask_id}.json"


# --- what approval recorded ------------------------------------------------------


@dataclass(frozen=True)
class Approved:
    ticket: str
    by: str
    digests: dict[str, str]


def record_approval(
    state: StateLayer, ticket: str, by: str, subtasks, *, expected: str = ABSENT
) -> Written | Unavailable | object:
    """Record what was approved, at the moment it is approved.

    Called by `approve.approve_plan` after the plan write succeeds. ⚠ Two
    writes, and the ORDER matters: the plan first, then this. If this one fails
    the plan is APPROVED with no digests recorded, and every step is then
    REFUSED — fail-closed, and `approve_plan` says so. The other order would
    leave digests for an approval that did not happen.
    """
    body = {
        "format_version": FORMAT_VERSION,
        "ticket": ticket,
        "approved_by": by,
        "digests": {s.id: digest(s) for s in subtasks},
    }
    return state.write_state(
        approvals_key(ticket),
        (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        expected,
    )


def approved_digests(state: StateLayer, ticket: str) -> dict[str, str] | str:
    """`{subtask id: digest}` as approval recorded it, or why there is none.

    A string is always a refusal, never an empty result: a step must not run
    because rite could not read what was approved.
    """
    got = state.read_state(approvals_key(ticket))
    if isinstance(got, Unavailable):
        return f"what {ticket}'s approval recorded could not be read: {got.reason}"
    if isinstance(got, Absent):
        return (
            f"{ticket}'s approval recorded no subtask digests, so rite cannot "
            "tell whether the subtask about to run is the one that was "
            "approved. Have the plan reviewed again"
        )
    assert isinstance(got, Present)
    try:
        body = json.loads(got.value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return f"what {ticket}'s approval recorded is not readable ({e})"
    if not isinstance(body, dict) or body.get("format_version") != FORMAT_VERSION:
        return (
            f"what {ticket}'s approval recorded is in format "
            f"{body.get('format_version') if isinstance(body, dict) else '?'!r}, "
            f"and this rite reads {FORMAT_VERSION}"
        )
    digests = body.get("digests")
    if not isinstance(digests, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in digests.items()
    ):
        return f"what {ticket}'s approval recorded is not a map of digests"
    return dict(digests)


def approval_version(state: StateLayer, ticket: str) -> str:
    """The version to compare-and-swap the approval record against."""
    got = state.read_state(approvals_key(ticket))
    return got.version if not isinstance(got, Unavailable) else ABSENT


# --- the approach itself ---------------------------------------------------------


@dataclass(frozen=True)
class Stored:
    ticket: str
    subtask: str
    steps: str
    digest: str
    at: float = 0.0


def write_approach(
    state: StateLayer,
    ticket: str,
    subtask: Subtask,
    steps: str,
    *,
    now: float | None = None,
) -> Written | Unavailable | object:
    """Persist the approach for exactly this subtask, with its digest.

    The digest is stored WITH the steps so a later read can tell whether the
    subtask has changed since: steps written for one subtask are not an
    approach for a different one wearing the same id.
    """
    import time

    body = {
        "format_version": FORMAT_VERSION,
        "ticket": ticket,
        "subtask": subtask.id,
        "steps": steps,
        "digest": digest(subtask),
        "at": time.time() if now is None else now,
    }
    got = state.read_state(approach_key(ticket, subtask.id))
    return state.write_state(
        approach_key(ticket, subtask.id),
        (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        got.version if not isinstance(got, Unavailable) else ABSENT,
    )


def read_approach(
    state: StateLayer, ticket: str, subtask_id: str
) -> Stored | str | None:
    """The persisted approach, None when there is none, or why it cannot be
    read. ⚠ Three answers, not two: "cannot read" must not run a turn, and it
    must not be mistaken for "none yet" either, which would overwrite it."""
    got = state.read_state(approach_key(ticket, subtask_id))
    if isinstance(got, Unavailable):
        return f"its persisted approach could not be read: {got.reason}"
    if isinstance(got, Absent):
        return None
    assert isinstance(got, Present)
    try:
        body = json.loads(got.value.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return f"its persisted approach is not readable ({e})"
    if not isinstance(body, dict) or body.get("format_version") != FORMAT_VERSION:
        return "its persisted approach is in a format this rite does not read"
    steps = body.get("steps")
    stored_digest = body.get("digest")
    if not isinstance(steps, str) or not isinstance(stored_digest, str):
        return "its persisted approach carries no steps or no digest"
    at = body.get("at", 0.0)
    return Stored(
        ticket=str(body.get("ticket", "")),
        subtask=str(body.get("subtask", "")),
        steps=steps,
        digest=stored_digest,
        at=float(at)
        if isinstance(at, int | float) and not isinstance(at, bool)
        else 0.0,
    )


# --- the guard the executor asks ------------------------------------------------


@dataclass(frozen=True)
class Cleared:
    """The approach to prepend to this turn's instruction."""

    steps: str


@dataclass(frozen=True)
class Blocked:
    """Why this subtask may not run. ⚠ NOT a failed subtask: the caller
    reports it, keeps the subtask's status and burns no attempt (RL-47)."""

    why: str


def cleared_to_run(
    state: StateLayer, ticket: str, executing: Subtask
) -> Cleared | Blocked:
    """Whether `executing` may run, and with which approach.

    The whole Level-2 guard, in one place and in this order:

    1. what approval recorded must be readable, and must name this subtask;
    2. the subtask about to run must digest to what was approved — the
       boundary check, against APPROVAL TIME rather than against the plan
       rite just read;
    3. a persisted approach must exist for it, and must have been written for
       this same digest.
    """
    approved = approved_digests(state, ticket)
    if isinstance(approved, str):
        return Blocked(approved)
    want = approved.get(executing.id)
    if want is None:
        known = ", ".join(sorted(approved)) or "none"
        return Blocked(
            f"{executing.id} is not a subtask {ticket}'s approval recorded "
            f"(it recorded: {known}), so it was never approved"
        )
    now = digest(executing)
    if now != want:
        return Blocked(
            f"the subtask about to run is not the one that was approved: it "
            f"digests to {now[:12]} and approval recorded {want[:12]}. Level 2 "
            "produces an approach and may not change the subtask, and nothing "
            "else may change it either (DD-2.4, and SCRUM-72e's approval-time "
            "comparison — the old check compared the plan with itself)"
        )
    stored = read_approach(state, ticket, executing.id)
    if isinstance(stored, str):
        return Blocked(stored)
    if stored is None:
        return Blocked(
            f"{executing.id} has no persisted Level-2 approach, and a step may "
            "not run without one (§3.3a, overriding DD-2.4's fail-open). rite "
            "writes it at the front of the turn; this pass could not, and says "
            "so rather than running the turn without it"
        )
    if stored.digest != want:
        return Blocked(
            f"{executing.id}'s persisted approach was written for a different "
            f"subtask ({stored.digest[:12]}, approval recorded {want[:12]}): "
            "steps written for one subtask are not an approach for another"
        )
    if not stored.steps.strip():
        return Blocked(
            f"{executing.id}'s persisted approach has no steps in it, which is "
            "not an approach"
        )
    return Cleared(stored.steps)
