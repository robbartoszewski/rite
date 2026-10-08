"""L-6 — the local tier, driven inside the supervisor's own cycle.

Robert ruled this into v0.7.0 on 2026-10-03. `rite local decompose`, `approve`
and `step` were three sound commands somebody typed; this drives them in order,
unattended, so an Ollama or mixed fleet runs hands-off.

⚠ **No second loop.** It is a per-cycle callable like `chores` and `refine`, so
everything the cycle engine already does applies to the local tier without being
restated: the schedule's slots and a closed window waiting for `next_open`
(SCRUM-20), reply-only and edit-only counting as idle, the spin guard, the
event and hourly-heartbeat wake, and the concurrency bound. A separate
always-live loop would be a second thing to keep correct, and none of those
properties would hold inside it.

⚠ **ONE stage per pass, deliberately.** Two of the four stages are inference
turns, and the engine's accounting — its idle detection, its spin guard, its
ceiling — is in units of work per cycle. A pass that ran a whole ticket would
spend an unbounded amount inside one tick the engine believes is one step, which
is the shape §9.14.5 refuses. Approval and the delivery request are free and
deterministic, and they still take a pass each, because a cycle that did
something is the signal the engine reads.

⚠ **It drives the gates; it never reaches past them.** Approval goes through
`approve.approve_plan`, which is where RL-6's model-compare independence, DD-3.5,
RL-67's named author and RL-63's resolvable cites live. There is deliberately no
path here that writes `APPROVED`, and nothing here is permitted to run a subtask
from an unapproved plan — `run_subtask` refuses that itself, and this refuses
before it. Integration goes through the OL8 request path, so the push is rite's
own gated one (draft, operator-owned repo, default branch, gate passed on exactly
those commits, never `--force`).

**The share-one-model rule is the schedule's job, not this module's.** Two local
Workers on one model share a resident copy; on different models the daemon evicts
back and forth for the whole run (OL2: 1.1x/1.8x against 2.3x/3.6x). That is
throughput, so the lever is the schedule, and rite gains no budget concept.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local import stage as st

# ⚠ **One vocabulary, SCRUM-72.** These were this module's own labels for
# "what a pass did"; they are now the pipeline's persisted stages
# (`local.stage`), because a pass's report and the record of what has been
# passed must not be two sets of words that drift apart. `STEPPED` keeps its
# name — a pass that ran a subtask is what it reports — and is the STEPPING
# stage.
DECOMPOSED = st.DECOMPOSED
APPROVED = st.APPROVED
REJECTED = st.REJECTED
STEPPED = st.STEPPING
RECOMPOSED = st.RECOMPOSED
DELIVERY_ASKED = st.DELIVERY_REQUESTED
DEFINED = st.DEFINED


@dataclass(frozen=True)
class Advance:
    """What one pass did to one ticket, or why it could not."""

    ticket: str = ""
    stage: str = ""
    note: str = ""
    blocked: str = ""

    @property
    def moved(self) -> bool:
        return bool(self.stage) and not self.blocked

    def line(self) -> str:
        if self.blocked:
            return f"{self.ticket}: not advanced — {self.blocked}"
        return f"{self.ticket}: {self.stage}" + (f" ({self.note})" if self.note else "")


def independent_reviewer(root: Path, plan: dec.Decomposition) -> tuple[str, str]:
    """(a plan-review holder that may approve this plan, or "", why not).

    ⚠ **Chosen, then still CHECKED.** `approve_plan` re-applies every rule; this
    only avoids asking a Manager that obviously cannot. Picking a reviewer here
    and trusting it would move RL-6's gate into a chooser, which is the mistake
    the config-vs-router split already made once (OL7).
    """
    from rite_ai.config.managers import (
        PLAN_REVIEW,
        effective_duties,
        engine_identity,
    )
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return "", f"this project's config.yaml will not parse: {parsed.message}"
    roles = list(parsed.coordination.manager_roles)
    author = next((r for r in roles if r.name == plan.decomposed_by), None)
    if author is None:
        # RL-67, fail closed: an independence claim rite cannot place.
        return "", (
            f"its author {plan.decomposed_by or '(nobody)'!r} is not a Manager in "
            "this project, so independence cannot be checked (RL-67)"
        )
    holders = [
        r
        for r in roles
        if PLAN_REVIEW in effective_duties(r, len(roles)) and r.name != author.name
    ]
    for r in holders:
        if engine_identity(r) != engine_identity(author):
            return r.name, ""
    if holders:
        return "", (
            f"every plan-review holder is the same model as {author.name!r}, so "
            "none of them is an independent reviewer (RL-6). Two 'local:' classes "
            "serving one model are one model, however they are labelled"
        )
    return "", (
        f"no Manager other than {author.name!r} holds plan-review, so its own "
        "plan cannot be approved by anyone (RL-6, DD-3.5)"
    )


def advance_ticket(
    root: Path,
    manager: str,
    ticket: str,
    *,
    state=None,
    author_plan=None,
    ask_review=None,
    step=None,
    ask_delivery=None,
    recompose_with=None,
    gate=None,
    now: float | None = None,
) -> Advance:
    """Advance `ticket` by ONE stage, or say why it did not move.

    🔴 **Dispatched from the PERSISTED stage (SCRUM-72, §3.3a), not derived
    from the plan's shape.** This function used to decide what to do next by
    asking what the plan looked like now: no plan means decompose, PENDING
    means approve, a runnable subtask means step. That reads as an order and is
    not one — the conditions are independent, so anything that produced a later
    stage's SHAPE entered that stage, and nothing recorded that a stage had
    been passed, so nothing could notice one that had not been. Now every move
    goes through `stage.advance`, which refuses a transition the table does not
    hold and a stage whose own artifact does not back it (`gates.gate_for`).

    ⚠ **The artifact is written first and the stage follows.** Each branch does
    its work, and only then asks for the move; the move is refused if the work
    did not produce what the stage claims. The inverse order would make the
    persisted stage a lie after a crash.

    Every dependency is injectable and every default is the production one, so a
    test drives the whole pipeline without a model on the machine — the same
    reason `take_one_step` takes its agent that way.
    """
    root = Path(root)
    if state is None:
        from rite_ai.local.plan_state import layer

        state = layer(root)
    if gate is None:
        from rite_ai.local.gates import gate_for

        gate = gate_for(root, manager, ticket, state)

    stage_read = st.read(state, ticket)
    if stage_read.unavailable:
        return Advance(
            ticket, blocked=f"its stage could not be read: {stage_read.unavailable}"
        )
    if stage_read.error:
        # ⚠ NOT treated as unstarted. A stage record edited into something this
        # rite will not parse is the one case where restarting the pipeline
        # would re-author a plan over work already approved.
        return Advance(
            ticket, blocked=f"its stage record will not parse: {stage_read.error}"
        )

    from rite_ai.local.gates import definition_snapshot

    snapshot = definition_snapshot(root, manager, ticket)
    pinned = "" if isinstance(snapshot, str) else str(snapshot.get("record_id", ""))

    def moved_to(to: str, *, why: str, note: str = "") -> Advance:
        got = st.advance(
            state, ticket, to, why=why, gate=gate, now=now, definition=pinned
        )
        if isinstance(got, st.Refused):
            return Advance(ticket, blocked=got.why)
        return Advance(ticket, stage=to, note=note or why)

    read = dec.read(state, ticket)
    if read.unavailable:
        return Advance(
            ticket,
            blocked=f"its decomposition could not be read: {read.unavailable}",
        )
    if read.error:
        return Advance(
            ticket, blocked=f"its decomposition will not parse: {read.error}"
        )
    plan = read.plan

    # A pipeline that was in flight when the stage machine landed has artifacts
    # and no stage. Adopted once, off the artifacts, and only into a stage they
    # support — see `stage.adopt`.
    if stage_read.record is None:
        derived = _stage_from_artifacts(root, manager, ticket, plan)
        if derived:
            adopted = st.adopt(
                state, ticket, derived, gate=gate, now=now, definition=pinned
            )
            if isinstance(adopted, st.Refused):
                return Advance(ticket, blocked=adopted.why)
            return Advance(
                ticket,
                stage=derived,
                note="adopted from the artifacts already on disk",
            )

    stage = stage_read.stage

    # 0a. 🔴 **STALE (SCRUM-72d): the definition of done moved under the
    #     pipeline.** The definition is read from the Worker's publish-record
    #     snapshot, and that snapshot is rewritten when the Worker is started
    #     again — on a re-refined ticket, or on a different one. A plan
    #     authored against one definition must not go on being reviewed,
    #     stepped and delivered against another, so this HALTS and reports
    #     rather than advancing. Nothing is undone and nothing is guessed: the
    #     plan on disk is the plan that was approved, and which definition of
    #     done it is now meant to satisfy is not rite's to decide.
    if (
        stage != st.UNSTARTED
        and stage_read.definition
        and pinned != stage_read.definition
    ):
        unreadable = snapshot if isinstance(snapshot, str) else "it names no record_id"
        now_on = pinned or f"none rite can read ({unreadable})"
        return Advance(
            ticket,
            blocked=(
                f"its definition of done CHANGED since it reached {stage}: the "
                f"pipeline is pinned to refinement record "
                f"{stage_read.definition}, and its Worker is now started on "
                f"{now_on}. Nothing advances against a definition the plan was "
                "not authored for. Decide which it is: re-refine and start the "
                "Worker again to re-author the plan, or put back the record it "
                "was planned against"
            ),
        )

    # 0b. The plan may have been RETURNED to plan review from outside this loop
    #    — a composition conflict, a recomposition failure, a rejection after
    #    approval (`decomposition.returned_to_plan_review` drops the approval).
    #    The artifact leads and the stage follows it back.
    if stage in (st.APPROVED, st.STEPPING, st.RECOMPOSED) and (
        plan is None or plan.approval != dec.APPROVED
    ):
        return moved_to(
            st.DECOMPOSED,
            why="its plan was returned to plan review",
            note="its plan is no longer approved, so it goes back to review"
            + (f": {plan.returns[-1]}" if plan is not None and plan.returns else ""),
        )

    # 1. The spec/definition session's artifact: the signed refinement record
    #    this ticket's Worker was started on, pinned to the ticket. Before
    #    SCRUM-72 the decomposer was called with no ticket text at all.
    if stage == st.UNSTARTED:
        return moved_to(
            st.DEFINED,
            why="its definition of done is pinned to the record its Worker was "
            "started on",
            note="defined",
        )

    # 1b. REJECTED — back to its planner, RE-AUTHORED, and bounded.
    #
    # 🔴 **§3.3b's new transition, and it lands with its bound (SCRUM-72f).**
    # A REJECTED plan used to block for ever: `advance_ticket` re-authored
    # only when there was no plan at all, so a reviewer that said no stopped
    # the ticket rather than improving it. Re-authoring is only safe with a
    # budget and an escalation past it, because an unbounded re-author is a
    # loop and not a fix — and the budget is `Decomposition.returns`, the
    # count RL-10 already keeps, shared with a failed RL-8.
    if stage == st.REJECTED:
        from rite_ai.local import recompose

        spent = len(plan.returns) if plan is not None else 0
        if spent >= recompose.MAX_RETURNS:
            return Advance(
                ticket,
                blocked=(
                    f"its plan has gone back to review {spent} times "
                    f"({recompose.MAX_RETURNS} is the bound), so it is NOT "
                    "re-authored again — somebody has to look at the ticket. "
                    "The reasons so far: "
                    + "; ".join(plan.returns if plan is not None else ())
                ),
            )
        # ⚠ **RE-AUTHORED FIRST, then the stage moves.** `decomposed`'s gate
        # wants a PENDING plan and a rejected one is REJECTED, so the move is
        # only legal once a new plan exists. That is the rule everywhere here:
        # the artifact leads and the stage follows, and the gate is what makes
        # it true rather than a convention.
        if author_plan is None:
            from rite_ai.local.decompose import decompose_ticket

            author_plan = decompose_ticket
        again = author_plan(
            root,
            manager,
            ticket,
            ticket_text=_rejection_text(_definition_text(snapshot), plan),
        )
        if not getattr(again, "wrote", False):
            why = getattr(again, "problem", "") or "the decomposer produced no plan"
            reasons = getattr(again, "reasons", ()) or ()
            if reasons:
                why = f"{why or 'rejected'}: {reasons[-1]}"
            return Advance(
                ticket, blocked=f"not re-authored after its rejection: {why}"
            )
        return moved_to(
            st.DECOMPOSED,
            why=f"re-authored after rejection by {manager}",
            note=(
                f"re-authored with the rejection's reasons by {manager} "
                f"({spent} of {recompose.MAX_RETURNS} returns spent)"
            ),
        )

    # 2. DEFINED — author a plan. It is written PENDING; nothing here can
    #    write APPROVED, which is RL-6's gate and DD-3.5's rule. (A plan sent
    #    back is re-authored in the REJECTED branch above, which has the
    #    reasons to give it.)
    if stage == st.DEFINED:
        if author_plan is None:
            from rite_ai.local.decompose import decompose_ticket

            author_plan = decompose_ticket
        # ⚠ Named `author_plan` rather than after the command it runs.
        # `tests/test_no_dead_wiring` asks whether a function is called by
        # looking for its name followed by an open bracket, as a SUBSTRING — so
        # the obvious name for this parameter made `enclosure.compose` read as
        # newly called, the shorter name sitting inside the longer one. The
        # first draft of this very comment tripped it again by quoting the text.
        # Fixing that matcher to a word boundary is right and is NOT done here:
        # it uncovers four unrelated functions the loose match was masking, and
        # this branch must not merge on someone else's cleanup. Filed separately.
        # 🔴 **The definition of done IS the decomposer's input (SCRUM-72d).**
        # `advance_ticket` called `author_plan` with no ticket text at all, so
        # the decomposer saw "(no ticket text was supplied)" and sliced a
        # ticket by its id. The refinement record's own rendering is passed —
        # the SAME text the Worker's start prompt carries
        # (`record.render_for_worker`), so the plan and the work are judged
        # against one definition rather than two.
        result = author_plan(
            root, manager, ticket, ticket_text=_definition_text(snapshot)
        )
        # `DecomposeResult.wrote` — the field name matters: `ok`/`written` do not
        # exist on it, and a getattr default of False would have read every
        # successful decomposition as a failure.
        if getattr(result, "wrote", False):
            return moved_to(
                st.DECOMPOSED, why=f"decomposed by {manager}", note=f"by {manager}"
            )
        why = getattr(result, "problem", "") or "the decomposer produced no plan"
        reasons = getattr(result, "reasons", ()) or ()
        if reasons:
            why = f"{why or 'rejected'}: {reasons[-1]}"
        return Advance(ticket, blocked=f"not decomposed: {why}")

    if plan is None:
        return Advance(
            ticket,
            blocked=f"it is at {stage} but has no decomposition, so nothing "
            "says what that stage was reached with",
        )

    # 3. DECOMPOSED — ASK an independent plan-review holder to review it.
    #
    # 🔴 **The harness never approves (SCRUM-72 §3.3b).** This branch used to
    # pick a reviewer and immediately call `approve_plan` in its name: no
    # Manager was ever asked, nothing could ever write REJECTED, and RL-6's
    # gate checked the rules about who COULD have reviewed against a review
    # that did not happen. Now the reviewer is asked through its inbox — the
    # channel every Manager reads whatever its engine — and the ticket waits
    # here until a verdict is honoured (`plan_review.honour_verdicts`, at the cycle
    # boundary). Approval is not something a pass can produce.
    if stage == st.DECOMPOSED:
        # A verdict honoured since the last pass has already written the
        # artifact (`plan_review.honour_verdicts` -> `approve_plan`). The stage follows
        # it, as everywhere else: the artifact leads.
        if plan.approval == dec.APPROVED:
            return moved_to(
                st.APPROVED,
                why=f"approved by {plan.approved_by}",
                note=f"by {plan.approved_by}",
            )
        if plan.approval == dec.REJECTED:
            return moved_to(
                st.REJECTED,
                why="its plan was rejected by review",
                note="rejected: it goes back to its planner"
                + (f" ({plan.returns[-1]})" if plan.returns else ""),
            )
        reviewer, why = independent_reviewer(root, plan)
        if not reviewer:
            return Advance(ticket, blocked=f"its plan cannot be approved: {why}")
        if ask_review is None:
            from rite_ai.local.plan_review import ask as ask_review
        outcome = ask_review(
            root,
            manager,
            ticket,
            reviewer,
            plan.decomposed_by,
            read.version,
            now=now,
        )
        if getattr(outcome, "sent", False):
            # A request going out IS this pass's work: it is what the cycle
            # engine reads as progress, the same way the free deterministic
            # stages each take a pass.
            return Advance(ticket, stage=st.DECOMPOSED, note=outcome.note)
        return Advance(ticket, blocked=outcome.note)

    # 4. APPROVED or STEPPING with work left — run the next subtask.
    from rite_ai.local.step import next_subtask

    if stage in (st.APPROVED, st.STEPPING) and next_subtask(plan) is not None:
        if step is None:
            from rite_ai.local.step import take_one_step

            step = take_one_step
        result = step(root, manager, ticket)
        if getattr(result, "problem", ""):
            return Advance(ticket, blocked=f"no subtask ran: {result.problem}")
        verdict = (
            "accepted"
            if getattr(result, "accepted", False)
            else getattr(result, "status", "ran")
        )
        note = f"{getattr(result, 'subtask', '') or 'a subtask'} {verdict}"
        if stage == st.APPROVED:
            return moved_to(st.STEPPING, why=f"first subtask ran: {note}", note=note)
        # Already STEPPING: which subtask is where is the PLAN's business, and
        # the table has no self-loop, so this is not a transition.
        return Advance(ticket, stage=st.STEPPING, note=note)

    # 5. Nothing left to run — RECOMPOSE, then move. RL-8: the ticket's own
    #    agreed verify runs on the composed work before anything is delivered.
    if stage in (st.APPROVED, st.STEPPING):
        unfinished = [s.id for s in plan.subtasks if s.status != dec.ACCEPTED]
        if unfinished:
            return Advance(
                ticket,
                blocked=(
                    f"nothing is planned and {', '.join(unfinished)} did not reach "
                    "accepted, so the ticket is neither runnable nor finished"
                ),
            )
        from rite_ai.local import level2, recompose

        digests = level2.approved_digests(state, ticket)
        if isinstance(digests, str):
            return Advance(ticket, blocked=digests)
        got = recompose.cleared_to_deliver(state, ticket, digests)
        if isinstance(got, recompose.Blocked):
            # Not recorded for this plan yet, or recorded as a failure. Run it.
            result = (recompose_with or recompose.run_recomposition)(
                root, manager, ticket, digests, now=now
            )
            written = recompose.write(state, result)
            if not isinstance(written, dec.Written):
                return Advance(
                    ticket,
                    blocked=(
                        "its recomposition result could not be recorded, so it "
                        "is not delivered on an unrecorded verify: "
                        f"{getattr(written, 'reason', 'it changed under this write')}"
                    ),
                )
            if result.problem:
                # Could not RUN is not a failure (RL-47): nothing is returned
                # to review for an outage, and it is tried again next pass.
                return Advance(ticket, blocked=result.line())
            if not result.cleared:
                return _return_to_review(
                    root, manager, ticket, state, plan, read.version, result, gate, now
                )
        return moved_to(
            st.RECOMPOSED,
            why="the composed work passed its agreed verify (RL-8)",
            note=(
                got.why
                if isinstance(got, recompose.Cleared)
                else "the composed work passed its agreed verify (RL-8)"
            ),
        )

    # 6. RECOMPOSED — ask rite to deliver (OL8's request path).
    if stage == st.RECOMPOSED:
        if ask_delivery is None:
            ask_delivery = _ask_delivery
        asked, why = ask_delivery(root, manager, ticket)
        if not asked:
            return Advance(ticket, blocked=f"delivery not requested: {why}")
        return moved_to(
            st.DELIVERY_REQUESTED,
            why="rite was asked to deliver it",
            note="rite will push past its gate",
        )

    # 7. DELIVERY_REQUESTED — the end of the pipeline. Not a fault: the
    #    supervisor's own `honour_deliveries` is what happens next.
    return Advance(
        ticket,
        blocked="its delivery has been requested; rite honours that at the "
        "cycle boundary and the pipeline has nothing further to drive",
    )


def _rejection_text(definition: str, plan) -> str:
    """The decomposer's input when it re-authors after a rejection or a failed
    RL-8 (§3.3b: "with the reasons as decomposer input").

    ⚠ The definition of done comes FIRST and the reasons are under their own
    heading. A plan re-authored against the reasons alone would be a plan
    answering the review rather than the ticket.
    """
    if plan is None or not plan.returns:
        return definition
    reasons = "\n".join(f"- {r}" for r in plan.returns)
    return (
        f"{definition}\n\n"
        "A previous plan for this ticket was REJECTED, or failed its "
        "recomposition verify, for the reasons below. Produce a plan that does "
        "not repeat them; do not argue with them:\n" + reasons
    )


def _return_to_review(
    root: Path, manager: str, ticket: str, state, plan, version: str, result, gate, now
) -> Advance:
    """A failed RL-8 sends the plan back to plan review — bounded.

    🔴 **One budget for BOTH return paths** (`recompose.MAX_RETURNS`), counted
    on `Decomposition.returns`, which RL-10 already keeps. A rejection (§3.3b)
    and a failed recomposition are the same event from the plan's point of
    view: it goes back to its planner with reasons. Two budgets would be two
    numbers to keep in step, and the first thing to drift.

    ⚠ **Past the bound it ESCALATES rather than returning again.** A plan that
    has been re-sliced three times and still does not compose is not a plan
    one more turn fixes; it is a ticket somebody has to look at, and saying so
    is the whole point of a bound.
    """
    from rite_ai.local import recompose

    spent = len(plan.returns)
    if spent >= recompose.MAX_RETURNS:
        return Advance(
            ticket,
            blocked=(
                f"{result.line()} — and its plan has already gone back to review "
                f"{spent} times ({recompose.MAX_RETURNS} is the bound), so it is "
                "NOT sent back again. Each subtask passes its own check and the "
                "composed work does not: the slicing is wrong in a way "
                "re-slicing has not fixed, and somebody has to look at the "
                "ticket. The reasons so far: " + "; ".join(plan.returns)
            ),
        )
    reason = f"RL-8: {result.line()}" + (
        f" Output: {result.output[:500]}" if result.output else ""
    )
    written = dec.write(state, dec.returned_to_plan_review(plan, reason), version)
    if not isinstance(written, dec.Written):
        return Advance(
            ticket,
            blocked=(
                "its plan could not be returned to review, so it is neither "
                "delivered nor sent back: "
                f"{getattr(written, 'reason', 'it changed under this write')}"
            ),
        )
    got = st.advance(
        state,
        ticket,
        st.DECOMPOSED,
        why=f"RL-8 failed: {result.failed}",
        gate=gate,
        now=now,
    )
    if isinstance(got, st.Refused):
        return Advance(ticket, blocked=got.why)
    return Advance(
        ticket,
        stage=st.DECOMPOSED,
        note=(
            f"{result.line()} It goes back to plan review with the reasons "
            f"({spent + 1} of {recompose.MAX_RETURNS} returns spent)"
        ),
    )


def _definition_text(snapshot) -> str:
    """The agreed definition of done, as the decomposer is given it.

    ⚠ **`record.render_for_worker`, not a second rendering.** That function is
    deterministic and is exactly what the Worker's start prompt carries, so the
    planner slices against the text the Worker will be held to. A second
    wording here would be a second definition of done, which is the shape §3.3a
    is about.
    """
    if isinstance(snapshot, str) or not snapshot:
        return ""
    from rite_ai.refinement import record as rec

    try:
        return rec.render_for_worker(rec.from_payload(snapshot))
    except Exception:  # noqa: BLE001 - a payload that will not render is no text
        return ""


def _stage_from_artifacts(root: Path, manager: str, ticket: str, plan) -> str:
    """The stage `ticket`'s artifacts already evidence, or "" for a ticket the
    pipeline has not started.

    Only consulted for a ticket with NO stage record (`stage.adopt`), and every
    answer is re-checked by that stage's own gate before it is written. "" for
    a ticket with no plan: that one has nothing to adopt and starts normally at
    DEFINED, so the ordinary first transition is never bypassed.
    """
    if plan is None:
        return ""
    if plan.approval == dec.REJECTED:
        return st.REJECTED
    if plan.approval != dec.APPROVED:
        return st.DECOMPOSED
    if plan.subtasks and all(s.status == dec.ACCEPTED for s in plan.subtasks):
        from rite_ai.publishing import requests

        asked = requests.requests_dir(root, manager) / f"{ticket}.json"
        return st.DELIVERY_REQUESTED if asked.exists() else st.RECOMPOSED
    if any(s.status != dec.PLANNED for s in plan.subtasks):
        return st.STEPPING
    return st.APPROVED


def _ask_delivery(root: Path, manager: str, ticket: str) -> tuple[bool, str]:
    """Write the two-value delivery request a Manager writes (OL8).

    ⚠ **The request path, never a push from here.** rite validates and pushes on
    the host, under §5.1.1's bounds, and the supervisor's own
    `honour_deliveries` is what picks this up. The Manager's repo-scoped token
    could technically push; this does not use it, because the request path gates
    by construction in rite's own code rather than depending on a hook install.
    """
    import json

    from rite_ai.publishing import requests

    # ⚠ **Once, not every cycle.** A delivered plan stays all-accepted, so
    # without this every pass would write the request again and the supervisor
    # would honour it again — the second delivery refusing because the sandbox
    # is gone, which is noise that looks like a fault. A request still waiting
    # is not a reason to write another.
    pending = requests.requests_dir(root, manager) / f"{ticket}.json"
    if pending.exists():
        return False, "a delivery request for it is already waiting to be honoured"

    worker = _worker_for(root, manager, ticket)
    if not worker:
        return False, (
            f"no Worker of {manager!r} is recorded as working {ticket}, and a "
            "delivery request names the Worker whose sandbox holds the commits"
        )
    where = requests.requests_dir(root, manager)
    try:
        where.mkdir(parents=True, exist_ok=True)
        (where / f"{ticket}.json").write_text(
            json.dumps({"worker": worker, "ticket": ticket}) + "\n"
        )
    except OSError as e:
        return False, f"its delivery request could not be written: {e}"
    return True, ""


def _worker_for(root: Path, manager: str, ticket: str) -> str:
    """The Worker of `manager` recorded as working `ticket`, or "".

    Read from what `rite sandbox start` recorded, so this agrees with the
    settings `deliver` compares against rather than guessing a name.
    """
    from rite_ai.config.parse import load_project

    project = load_project(Path(root))
    if isinstance(project, list):
        return ""
    for worker in project.workers:
        if worker.manager and worker.manager != manager:
            continue
        from rite_ai.publishing import record

        # `read` returns `Record | Unreadable | None`; only the first has a
        # ticket, and `getattr` keeps the other two from being read as a match.
        settings = record.read(Path(root), worker.name)
        if getattr(settings, "ticket", "") == ticket:
            return worker.name
    return ""


def drive_local_tier(
    root: Path, manager: str, tickets, say, *, advance=None
) -> list[Advance]:
    """Advance the local tier by one stage, for the first ticket that can move.

    ⚠ **One ticket per pass, for the reason the module docstring gives about one
    STAGE per pass.** A pass that advanced every ticket would spend several
    inference turns inside one tick the cycle engine counts as one.

    Everything is reported, including a ticket that could not move: a local tier
    that silently declines to advance is indistinguishable from one with nothing
    to do, which is the shape the spin guard exists to catch.

    ⚠ `advance` is injectable, and it is not only for convenience. Without it a
    test of this function reaches `advance_ticket`'s production defaults and
    runs a REAL decomposition against a REAL endpoint — measured once at four
    and a half minutes for a single case, against an endpoint that happened to
    be there. A pass over several tickets is exactly where that compounds.
    """
    advances: list[Advance] = []
    if advance is None:
        advance = advance_ticket
    for ticket in tickets or ():
        got = advance(Path(root), manager, ticket)
        advances.append(got)
        if callable(say):
            say(f"local tier: {got.line()}")
        if got.moved:
            break
    return advances
