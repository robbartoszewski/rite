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

DECOMPOSED = "decomposed"
APPROVED = "approved"
STEPPED = "stepped"
DELIVERY_ASKED = "delivery requested"


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
    approve=None,
    step=None,
    ask_delivery=None,
) -> Advance:
    """Advance `ticket` by ONE stage, or say why it did not move.

    Every dependency is injectable and every default is the production one, so a
    test drives the whole pipeline without a model on the machine — the same
    reason `take_one_step` takes its agent that way.
    """
    root = Path(root)
    if state is None:
        from rite_ai.coordination.local_backend import LocalStateLayer

        state = LocalStateLayer(root / ".rite")

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

    # 1. No plan yet — author one. It is written PENDING; nothing here can
    #    write APPROVED, which is RL-6's gate and DD-3.5's rule.
    if read.plan is None:
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
        result = author_plan(root, manager, ticket)
        # `DecomposeResult.wrote` — the field name matters: `ok`/`written` do not
        # exist on it, and a getattr default of False would have read every
        # successful decomposition as a failure.
        if getattr(result, "wrote", False):
            return Advance(ticket, stage=DECOMPOSED, note=f"by {manager}")
        why = getattr(result, "problem", "") or "the decomposer produced no plan"
        reasons = getattr(result, "reasons", ()) or ()
        if reasons:
            why = f"{why or 'rejected'}: {reasons[-1]}"
        return Advance(ticket, blocked=f"not decomposed: {why}")

    plan = read.plan

    # 2. PENDING — have an INDEPENDENT plan-review holder approve it.
    if plan.approval == dec.PENDING:
        reviewer, why = independent_reviewer(root, plan)
        if not reviewer:
            return Advance(ticket, blocked=f"its plan cannot be approved: {why}")
        if approve is None:
            from rite_ai.local.approve import approve_plan

            approve = approve_plan
        result = approve(root, ticket, reviewer)
        if getattr(result, "why", ""):
            return Advance(ticket, blocked=f"not approved: {result.why}")
        return Advance(ticket, stage=APPROVED, note=f"by {reviewer}")

    if plan.approval == dec.REJECTED:
        return Advance(
            ticket,
            blocked="its plan was rejected, so it goes back to its decomposer"
            + (f": {plan.returns[-1]}" if plan.returns else ""),
        )

    # 3. APPROVED with work left — run the next subtask.
    from rite_ai.local.step import next_subtask

    if next_subtask(plan) is not None:
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
        return Advance(
            ticket,
            stage=STEPPED,
            note=f"{getattr(result, 'subtask', '') or 'a subtask'} {verdict}",
        )

    # 4. Every subtask accepted — ask rite to deliver (OL8's request path).
    done = [s for s in plan.subtasks if s.status == dec.ACCEPTED]
    if len(done) != len(plan.subtasks):
        unfinished = [s.id for s in plan.subtasks if s.status != dec.ACCEPTED]
        return Advance(
            ticket,
            blocked=(
                f"nothing is planned and {', '.join(unfinished)} did not reach "
                "accepted, so the ticket is neither runnable nor finished"
            ),
        )
    if ask_delivery is None:
        ask_delivery = _ask_delivery
    asked, why = ask_delivery(root, manager, ticket)
    if not asked:
        return Advance(ticket, blocked=f"delivery not requested: {why}")
    return Advance(ticket, stage=DELIVERY_ASKED, note="rite will push past its gate")


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
