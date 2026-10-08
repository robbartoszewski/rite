"""A plan-review holder approves a decomposition (OL7).

Until now `approval` moved from PENDING to APPROVED by **hand-editing the JSON**
in the state layer. That made RL-6's gate a convention: the thing that may not be
set by the decomposer was set by whoever held a text editor, and nothing checked
that the approver was a plan-review holder, was not the author, or was a
different model from the author.

**Every branch refuses; only the end allows** — the broker's rule, because this
is the gate `plan.released` reads and `run_subtask` enforces, and a gate that
guesses is not one.

⚠ **This matters more since OL8.** An all-Ollama fleet integrates its own work,
so the chain from plan to pushed branch can run with no Claude session and no
person in it. Independence is the whole of what makes a local verdict worth
acting on, which is why the checks here are the same rule
`config.managers.engine_identity` applies and not a second spelling of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rite_ai.local import decomposition as dec


@dataclass(frozen=True)
class Approved:
    ticket: str
    by: str
    author: str = ""

    def note(self) -> str:
        return (
            f"{self.ticket} approved by {self.by}"
            + (f", which {self.author} decomposed" if self.author else "")
            + " — its subtasks may run"
        )


@dataclass(frozen=True)
class Refused:
    why: str


@dataclass(frozen=True)
class Rejected:
    ticket: str
    by: str
    reason: str = ""

    def note(self) -> str:
        return (
            f"{self.ticket} was rejected by {self.by}"
            + (f": {self.reason}" if self.reason else "")
            + " — it goes back to its decomposer with the reasons"
        )


@dataclass(frozen=True)
class _Standing:
    """What both verdicts need once the reviewer's authority holds: the plan
    as read, the version to compare-and-swap against, and the roles already
    parsed — so neither verdict re-reads config.yaml to find what the other
    just looked up."""

    plan: dec.Decomposition
    version: str
    state: object
    roles: tuple
    author: object


def _may_review(root: Path, ticket: str, reviewer: str, state):
    """`_Standing` when `reviewer` may decide `ticket`'s plan, else `Refused`.

    🔴 **Shared by `approve_plan` and `reject_plan`, not respelled in each.**
    A rejection is as much an act of authority as an approval — it sends the
    plan back and spends one of RL-10's returns — so the two must apply the
    SAME rule. Two copies of RL-6 would be two chances for one of them to
    drift, which is the mistake the config-vs-router split already made once
    (OL7).

    ⚠ **No engine-kind condition appears here, and none may (§3.3b).**
    SCRUM-72's root cause was `if role.is_local`; the approval path must not
    reintroduce one. The only place this reads an engine is `engine_identity`,
    which decides INDEPENDENCE — the model for a local engine, the engine kind
    otherwise — and never who may approve.

    The order of the checks is deliberate: who is asking, then what they are
    asking about, then whether they may. A reviewer that does not hold the duty
    should hear that rather than a complaint about the plan's shape.
    """
    from rite_ai.config.managers import (
        PLAN_REVIEW,
        effective_duties,
        engine_identity,
    )
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(root / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return Refused(f"this project's config.yaml will not parse: {parsed.message}")
    roles = list(parsed.coordination.manager_roles)
    role = next((r for r in roles if r.name == reviewer), None)
    if role is None:
        return Refused(f"no Manager named {reviewer!r} in this project")
    if PLAN_REVIEW not in effective_duties(role, len(roles)):
        return Refused(
            f"{reviewer!r} does not hold plan-review, so it cannot approve a "
            "decomposition (RL-6). Give it the duty, or ask a Manager that holds it"
        )

    read = dec.read(state, ticket)
    if read.unavailable:
        return Refused(
            f"{ticket}'s decomposition could not be read: {read.unavailable}"
        )
    if read.error:
        return Refused(f"{ticket}'s decomposition will not parse: {read.error}")
    if read.plan is None:
        return Refused(f"{ticket} has no decomposition to approve")
    plan = read.plan

    if plan.approval == dec.APPROVED:
        # Not an error and not a silent success: somebody already did this, and
        # saying so is better than a second write that looks like the first.
        return Refused(
            f"{ticket} is already approved"
            + (f" by {plan.approved_by}" if plan.approved_by else "")
        )
    if plan.approval == dec.REJECTED:
        return Refused(
            f"{ticket}'s decomposition was rejected, so it is not a candidate for "
            "approval — it goes back to its decomposer"
            + (f": {plan.returns[-1]}" if plan.returns else "")
        )

    # DD-3.5 — the decomposer may NEVER approve its own plan. Checked here as
    # well as at write time, because this is the other door into APPROVED.
    if plan.decomposed_by and plan.decomposed_by == reviewer:
        return Refused(
            f"{reviewer!r} decomposed {ticket}, so it may not approve it "
            "(DD-3.5) — the gate is worthless if the thing it gates can set it"
        )

    # RL-6 — a different MODEL, and fail closed when the author cannot be placed.
    author = next((r for r in roles if r.name == plan.decomposed_by), None)
    if author is None:
        return Refused(
            f"{ticket} names its author as {plan.decomposed_by or '(nobody)'!r}, "
            "which is not a Manager in this project — rite cannot check an "
            "independence claim against an author it cannot place (RL-67), so "
            "the plan goes back to its decomposer rather than through"
        )
    if engine_identity(role) == engine_identity(author):
        return Refused(
            f"{reviewer!r} and {author.name!r} are the same model, so {reviewer!r} "
            "is not an independent reviewer (RL-6). Two 'local:' classes serving "
            "one model are one model, however they are labelled"
        )
    return _Standing(
        plan=plan,
        version=read.version,
        state=state,
        roles=tuple(roles),
        author=author,
    )


def reject_plan(
    root: Path | str, ticket: str, reviewer: str, reason: str, *, state=None
) -> Rejected | Refused:
    """Mark `ticket`'s decomposition REJECTED on `reviewer`'s authority, with
    reasons, or say why not.

    🔴 **A new verdict (SCRUM-72 §3.3b).** Nothing could write REJECTED
    before: plan review was a stamp that only ever wrote APPROVED, so a
    reviewer that disagreed had no way to say so and the one artifact three
    gates read could not carry a "no". The reasons are REQUIRED — RL-10 counts
    returns per parent ticket, and a count with no reasons is a number nobody
    can act on, and a rejection the planner cannot re-author against is a
    dead end rather than a review.
    """
    root = Path(root)
    if state is None:
        from rite_ai.local.plan_state import layer

        state = layer(root)
    standing = _may_review(root, ticket, reviewer, state)
    if isinstance(standing, Refused):
        return standing
    if not (reason or "").strip():
        return Refused(
            f"{ticket} was not rejected: a rejection carries its reasons, "
            "because the plan goes back to its decomposer with them and RL-10 "
            "counts a return nobody can act on as a return all the same"
        )
    plan = standing.plan
    written = dec.write(
        standing.state,
        dec.Decomposition(
            ticket=plan.ticket,
            subtasks=plan.subtasks,
            decomposed_by=plan.decomposed_by,
            approval=dec.REJECTED,
            approved_by="",
            returns=(*plan.returns, f"{reviewer}: {reason.strip()}"),
        ),
        standing.version,
    )
    if not isinstance(written, dec.Written):
        if isinstance(written, dec.Unavailable):
            return Refused(
                f"{ticket} was not rejected: the state layer could not be "
                f"written ({written.reason})"
            )
        return Refused(
            f"{ticket} was not rejected: its decomposition changed while this "
            "ran, so the rejection would have been given to a plan nobody read"
        )
    return Rejected(ticket=ticket, by=reviewer, reason=reason.strip())


def approve_plan(
    root: Path | str, ticket: str, reviewer: str, *, state=None
) -> Approved | Refused:
    """Mark `ticket`'s decomposition APPROVED on `reviewer`'s authority, or say
    why not. The only writer of APPROVED, by design and by test."""
    root = Path(root)
    if state is None:
        from rite_ai.local.plan_state import layer

        state = layer(root)
    standing = _may_review(root, ticket, reviewer, state)
    if isinstance(standing, Refused):
        return standing
    plan = standing.plan
    roles = list(standing.roles)

    # The shape, re-checked. The plan may have been edited since it was written,
    # and this is the last point before its subtasks may run.
    from rite_ai.config.managers import effective_duties
    from rite_ai.local.plan_validation import candidate_problems

    holders = {r.name for r in roles if "decompose" in effective_duties(r, len(roles))}
    problems = candidate_problems(
        dec.Decomposition(
            ticket=plan.ticket,
            subtasks=plan.subtasks,
            decomposed_by=plan.decomposed_by,
            approval=dec.PENDING,
            approved_by=plan.approved_by,
            returns=plan.returns,
        ),
        root=root,
        decompose_managers=tuple(sorted(holders)),
    )
    if problems.refusals:
        return Refused(
            f"{ticket}'s decomposition does not pass validation, so it is not "
            "approvable as it stands: " + "; ".join(problems.refusals)
        )

    written = dec.write(
        standing.state,
        dec.Decomposition(
            ticket=plan.ticket,
            subtasks=plan.subtasks,
            decomposed_by=plan.decomposed_by,
            approval=dec.APPROVED,
            approved_by=reviewer,
            returns=plan.returns,
        ),
        standing.version,
    )
    # ⚠ Compare-and-swap, and the failure is NOT reported as success. `write`
    # returns one of three things (`Written` | `Conflict` | `Unavailable`), and
    # only the first is a write — a `Conflict` means another Manager touched this
    # decomposition while the checks above ran, which is exactly the case the
    # compare-and-swap exists for.
    if not isinstance(written, dec.Written):
        if isinstance(written, dec.Unavailable):
            return Refused(
                f"{ticket} was not approved: the state layer could not be "
                f"written ({written.reason})"
            )
        return Refused(
            f"{ticket} was not approved: its decomposition changed while this "
            "ran, so the approval would have been given to a plan nobody read. "
            "Read it again and decide on what is there now"
        )
    return Approved(ticket=ticket, by=reviewer, author=standing.author.name)
