"""Each stage's own gate: the artifact that must already say so (SCRUM-72,
§3.3a).

`stage.TRANSITIONS` says what may FOLLOW what — order. These say what must
already be TRUE — substance. Both have to hold, and they answer different
questions, which is why they are not one structure:

- the table stops a ticket entering `STEPPING` from `DEFINED`, because the
  review has not happened;
- the gate stops it entering `APPROVED` at all unless a plan exists whose
  `approval` is APPROVED and which names who approved it.

🔴 **The stage never leads the artifact.** Every caller writes the artifact
first and then asks for the move, and the gate refuses the move if the artifact
does not back it. So a stage record can never be the only evidence that
something happened: it is a claim rite re-checks against the thing itself. The
inverse arrangement — advance the stage, then do the work — is the one that
makes a persisted stage a lie after a crash.

⚠ **These read; they never write and never run a model.** A gate that could
author the thing it gates would make "cannot skip a stage" depend on the order
two functions happen to be called in.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local import stage as st


def definition_snapshot(root: Path, manager: str, ticket: str) -> dict | str:
    """The signed refinement record `ticket`'s Worker was STARTED on, or why
    there is none.

    ⚠ **The snapshot, not the board now.** `publishing.record` already keeps
    the record a Worker was started on, which is the definition of done the
    work is being done to; reading the board instead would let a ticket edited
    mid-flight change what the plan is judged against. `deliver` compares
    against this same snapshot (`_host_measurement_hold`), so the decomposer
    and the delivery gate are fed one definition rather than two.
    """
    from rite_ai.local.loop import _worker_for
    from rite_ai.publishing import record

    worker = _worker_for(Path(root), manager, ticket)
    if not worker:
        return (
            f"no Worker of {manager!r} is recorded as started on {ticket}, so "
            "there is no definition of done pinned to it — the spec/definition "
            "session happens at the Worker's start"
        )
    settings = record.read(Path(root), worker)
    payload = getattr(settings, "refinement", None)
    if not isinstance(payload, dict) or not payload:
        return (
            f"{worker} was started on {ticket} without a signed refinement "
            "record, so nothing says what done means. Refine it and start the "
            "Worker again"
        )
    return payload


def _plan(root: Path, state, ticket: str):
    """(the plan, "") or (None, why it cannot be read as one)."""
    read = dec.read(state, ticket)
    if read.unavailable:
        return None, f"its decomposition could not be read: {read.unavailable}"
    if read.error:
        return None, f"its decomposition will not parse: {read.error}"
    if read.plan is None:
        return None, "it has no decomposition"
    return read.plan, ""


_GATED = (
    st.DEFINED,
    st.DECOMPOSED,
    st.APPROVED,
    st.REJECTED,
    st.STEPPING,
    st.RECOMPOSED,
    st.DELIVERY_REQUESTED,
)
"""The stages `gate_for` below answers for. ⚠ It must be `stage.STAGES`, and
the test that says so is not ceremony: a stage added to the pipeline without a
gate here would be one nothing checks, and `gate_for` is the whole of what
"cannot skip a stage" rests on."""


def gate_for(root: Path, manager: str, ticket: str, state):
    """A callable `(stage) -> "" | why it is shut`, for `stage.advance`.

    Composed per ticket rather than passed four arguments at each call, so the
    guard takes one thing and a test can hand it a dictionary.
    """
    root = Path(root)

    def shut(target: str) -> str:
        # ⚠ **Refused FIRST, and not as a side effect of a missing plan.** This
        # is not a default-open: a stage with no gate below is a stage nothing
        # checks, and this function is what "cannot skip a stage" rests on. It
        # answers before the artifacts are read so an unknown stage is refused
        # on its own terms rather than for whatever happens to be absent.
        if target not in _GATED:
            return f"{target!r} has no gate, so rite will not enter it"

        if target == st.DEFINED:
            got = definition_snapshot(root, manager, ticket)
            return got if isinstance(got, str) else ""

        plan, why = _plan(root, state, ticket)
        if plan is None:
            return why

        if target == st.DECOMPOSED:
            if plan.approval != dec.PENDING:
                return (
                    f"its plan is {plan.approval}, not pending — a plan entering "
                    "decomposed is one waiting to be reviewed"
                )
            if not plan.decomposed_by:
                return (
                    "its plan names no author, so RL-67 cannot place one and no "
                    "independence claim about it can be checked"
                )
            return ""

        if target == st.APPROVED:
            if plan.approval != dec.APPROVED:
                return (
                    f"its plan's approval is {plan.approval!r}; only "
                    "`approve.approve_plan` writes approved, and it applies RL-6, "
                    "DD-3.5 and RL-67 before it does"
                )
            if not plan.approved_by:
                return "its plan is approved by nobody, which is not an approval"
            return ""

        if target == st.REJECTED:
            if plan.approval != dec.REJECTED:
                return f"its plan's approval is {plan.approval!r}, not rejected"
            if not plan.returns:
                return (
                    "its plan was rejected with no reasons, and a rejection with "
                    "nothing to act on cannot be re-authored against (RL-10)"
                )
            return ""

        if target == st.STEPPING:
            if not plan.released:
                return (
                    f"its plan is {plan.approval}, so no subtask may run (RL-6) "
                    "— the gate `run_subtask` enforces, asked before the work"
                )
            return ""

        if target in (st.RECOMPOSED, st.DELIVERY_REQUESTED):
            if not plan.subtasks:
                return "its plan has no subtasks, so nothing was composed"
            unfinished = [s.id for s in plan.subtasks if s.status != dec.ACCEPTED]
            if unfinished:
                return (
                    f"{', '.join(unfinished)} did not reach accepted, so the work "
                    "is not composed"
                )
            # 🔴 RL-8 (SCRUM-72f): every subtask passing its OWN check is not
            # the ticket working. A plan sliced wrongly produces subtasks that
            # each pass and a ticket that does not — the one failure
            # decomposition itself introduces — so the ticket's agreed verify
            # runs on the composed work before anything is delivered.
            from rite_ai.local import level2, recompose

            digests = level2.approved_digests(state, ticket)
            if isinstance(digests, str):
                return digests
            verdict = recompose.cleared_to_deliver(state, ticket, digests)
            if isinstance(verdict, recompose.Blocked):
                return verdict.why
            return ""

        raise AssertionError(f"{target!r} is in _GATED with no branch above")

    return shut
