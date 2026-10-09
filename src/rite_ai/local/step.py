"""One subtask of an approved decomposition, run from the Manager path (RL-T6).

**This is the caller `harness.run_subtask` was built for and did not have.**
`decomposition.py`, `harness.py` and `runners.py` were complete, tested and
reachable from nothing — 720 lines with no production caller — which is the
defect class `tests/test_no_dead_wiring.py` exists for. This module is the
join, and it exists because of what two real runs measured.

**What the two runs showed, and why this is the fix rather than a feature.**
A `qwen3:8b` Manager has been run twice against a correctly pinned window:

- v0.6.0 dogfood, macOS, 32k: it "filled its 32k window after 45 minutes of
  detours and ended without replying".
- SB11 acceptance, Linux, 32k pinned: it "repeated `git status` and `ls` three
  times, then summarised in the pane rather than with `rite reply`. The outbox
  is empty. That is the model; the run ended cleanly at the window."

Everything rite owned was right in the second run — TMPDIR, the sandbox
grants, the pinned window, a clean exit. What failed is that the model never
reported through rite, so nothing left the Manager. Both failures are the
same shape: a free-form session, a whole ticket, and a small model given room
to wander until its window is gone.

So this does not ask the model to be better. It takes the three things the
model was failing at away from it:

1. **Scope.** The agent gets ONE subtask and its spec SLICE, never the ticket
   and never a pointer into the spec (`harness.Context`: a pointer "assumes a
   context window large enough to go and read the file, and a small model's
   is not").
2. **The verify.** rite runs it (`runners.SubprocessVerifier`), and runs it
   whatever the agent claimed — "a verify skipped because the agent said it
   failed is a verify the agent controls".
3. **The report.** rite writes the outcome into the decomposition and commits
   the branch itself. Nothing depends on the model calling `rite reply`, which
   is precisely what it did not do.

⚠ **The agent's claim of success is recorded and never trusted.**
`Outcome.accepted` is derived from the verify alone, and a claim that
disagrees with it is KEPT, because that disagreement is evidence about the
agent (RL-7, RL-T0).

⚠ **Nothing here pushes.** The committer protocol has no push verb and this
module has no git call of its own; a local engine commits to a local task
branch and stops (RL-11, SPEC §5.1.1). Composition is somebody else's.

⚠ **AND NOTHING HERE PRODUCES A DECOMPOSITION.** A plan arrives approved or
nothing runs. `run_subtask` enforces that itself (`plan.released`), and this
module does not paper over it: there is no decomposer in rite today — no
caller of `decomposition.with_subtask`, no `Subtask` built outside its own
module — so the planning half of the tier is unbuilt and a plan is authored
by hand for now. Said here rather than discovered later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local.harness import Outcome, run_subtask


@dataclass
class Step:
    """What one call did, in terms a Manager's output can state."""

    ran: bool = False
    ticket: str = ""
    subtask: str = ""
    status: str = ""
    accepted: bool = False
    branch: str = ""
    commit: str = ""
    claim_disagreed: bool = False
    worker: str = ""
    """Which Worker ran it, when one did. Empty means this ran at MANAGER tier,
    on the host, which is what happened before SCRUM-54 and still happens when
    no Worker is assigned to the ticket."""
    sandbox: str = ""
    verify_output: str = ""
    lines: list[str] = field(default_factory=list)
    problem: str = ""
    """Why nothing ran. Empty when something did — including when it FAILED,
    which is a result and not a problem with asking."""


def _definition_for(root: Path, manager: str, ticket: str) -> str:
    """The ticket's agreed definition of done, as the slice carries it, or "".

    ⚠ **"" rather than a refusal, and that is not a hole.** The stage machine
    will not let a ticket reach a step without having passed `defined`
    (`stage.TRANSITIONS`, `gates.gate_for`), so a step with no definition can
    only be one driven by hand through `rite local step`. Refusing here would
    put a second copy of that gate in the executor, where it would be the one
    somebody later changes; the pipeline's guard is the guard.
    """
    from rite_ai.local.gates import definition_snapshot
    from rite_ai.local.loop import _definition_text

    return _definition_text(definition_snapshot(Path(root), manager, ticket))


def _slice_for(
    root: Path, cites: tuple[str, ...], definition: str = ""
) -> tuple[str, str]:
    """(the cited spec text, or a problem). Never a silent empty slice.

    The slice IS the context the model gets instead of the spec, so sending
    nothing would quietly reproduce the free-form run this replaces. A cite
    rite cannot resolve is a refusal, not a shrug.

    🔴 **The agreed definition of done comes FIRST (SCRUM-72d, §3.3a: the
    snapshot is fed "to the decomposer and the slice").** Without it a subtask
    ran against spec units and its own one-line intent, with the signed
    definition of done — the thing `deliver` later holds the work against —
    sitting in a record nothing in this path read. The ticket's definition and
    the subtask's spec are different kinds of context and the model needs
    both: the spec says how this codebase does things, the definition says
    what done means for this ticket.
    """
    from rite_ai.spec.slice import unit_text

    if not cites:
        return "", "the subtask cites no spec unit, so there is no slice to give"
    # 🔴 **SCRUM-92: the SAME resolver the plan validator uses.** This read the
    # derived unit file only, and the validator did too — so relaxing one
    # without the other would accept a plan here and refuse its subtask at
    # execution, which is the gate/authoring divergence SCRUM-83 was bitten by.
    # `unit_text` resolves a cite from the derived text or from a slice of the
    # spec source, and both callers ask it.
    parts: list[str] = []
    problems: list[str] = []
    for cite in cites:
        got, problem = unit_text(Path(root), cite)
        if problem:
            problems.append(problem)
        else:
            parts.append(got)
    if problems:
        return "", (
            f"{problems[0]} — a subtask run without its slice is the free-form "
            "run this path exists to replace"
        )
    text = "\n\n".join(parts)
    if definition:
        text = (
            f"{definition}\n\n"
            "The spec units this subtask cites, which are how this codebase "
            "does the thing above:\n\n" + text
        )
    return text, ""


@dataclass
class _LedgerClaims:
    """`harness.Claims` over rite's own ledger.

    An adapter rather than a second ledger: exclusion is the one property the
    claims ledger exists for, and a local tier with its own would be a second
    answer to "who holds this path". `manager=` is passed so a force-release
    cannot reach across Managers (MM3).

    ⚠ `ticket=` is passed for the same reason every other claim carries one:
    a claim that names no ticket cannot be matched to the work it was taken
    for, so nothing can ever tell whether it went stale. `reconcile` releases
    only on an exact ticket match, and a whole tier claiming `""` would have
    been a tier whose claims no reconciliation could ever reason about
    (SCRUM-64 follow-up). `harness.Claims.take` carries no ticket — a subtask
    claim is for the ticket the plan is for, which is fixed for the life of
    this adapter, so it is held here rather than threaded through the
    protocol.
    """

    ledger: object
    manager: str = ""
    ticket: str = ""

    def take(self, paths: tuple[str, ...], worker: str) -> bool:
        return bool(
            self.ledger.claim(list(paths), worker, self.ticket, manager=self.manager).ok
        )

    def release(self, paths: tuple[str, ...], worker: str) -> None:
        self.ledger.release(worker, list(paths))


FAILURE_CHARS = 500


def _why_it_stopped(outcome) -> str:
    """What to record as a subtask's last failure, in `FAILURE_CHARS`.

    🔴 **The TAIL, and the agent's own words first (SCRUM-103).** This kept
    `verify_output[:500]`, which truncates from the START. For the gate run's
    stopped turn the first 500 characters were `uv`'s venv setup — "Using
    CPython 3.14.3 / Creating virtual environment / Building tally / Installed"
    — and the reason was past the cut. Read from the plan state alone, a
    stopped turn looked like a failing assertion.

    The signal in command output is at the END, and when the agent said
    something about the turn itself — "did not finish within …s and was
    stopped" — that outranks any output, because it explains why the output is
    incomplete."""
    said = " | ".join(n for n in (outcome.notes or []) if n.strip())
    output = (outcome.verify_output or "").strip()
    if getattr(outcome, "stopped", False) and said:
        return said[:FAILURE_CHARS]
    if not output:
        return said[:FAILURE_CHARS]
    tail = output[-FAILURE_CHARS:]
    return tail if len(output) <= FAILURE_CHARS else "…" + tail[1:]


MAX_STOPS = 2
# A scope held by a neighbour frees itself the moment that Worker is DELIVERED,
# so the skip that matters is transient and a small bound clears it. Higher
# than MAX_STOPS because a stop costs a whole turn and a skip costs nothing —
# but bounded, so a scope held for good still stops the ticket.
MAX_SKIPS = 3
"""How many of a subtask's turns may be STOPPED on a timeout before it fails.

🔴 **The bound that makes a stop retryable without being free (SCRUM-103).**
`goose_agent` rejected treating a timeout as an infrastructure fault because
"a subtask that hangs every time would be retried for ever" — correctly. But
the alternative it had was to record the subtask FAILED, and `next_subtask`
below makes that terminal, so a clock became a verdict on work nobody judged.

Two, not one: a single stop is what the gate run hit on a leg that had
completed in nine minutes the run before, so one stop says more about the hour
than the subtask. Not more than two, because past that the evidence is about
the subtask."""


def next_subtask(plan: dec.Decomposition) -> dec.Subtask | None:
    """The subtask to run now: the first still planned, in the plan's order.

    ⚠ One at a time, deliberately. `harness.Context` says it: "if this ever
    carries more than one subtask, the tier has stopped being what it was
    measured as". A FAILED subtask is NOT retried here — a retry loop against
    a small model is how 45 minutes of detours happened — so a failure stops
    this ticket until a person or a step review looks at it.
    """
    return next((s for s in plan.subtasks if s.status == dec.PLANNED), None)


def take_one_step(
    root: Path,
    manager: str,
    ticket: str,
    *,
    agent=None,
    state=None,
    verifier=None,
    committer=None,
    claims=None,
    placement=None,
    approach_for=None,
) -> Step:
    """Run the next planned subtask of `ticket`, or say why nothing ran.

    Every dependency is injectable and every default is the production one, so
    a test substitutes the agent without substituting the path.

    `placement` is where the subtask runs (SCRUM-54), and it is injectable for
    the same reason the agent is: the sandboxed path's workspace, branch and
    claim identity all come from it, so a test that could only substitute the
    agent could not reach them. Left unset — and with no agent passed either —
    it is resolved from the project, which is the production path.

    `approach_for` is Level 2's producer (SCRUM-72e), injectable for the same
    reason: the approach is REQUIRED now, so a test that could not substitute
    it could only ever test the refusal.
    """
    root = Path(root)
    step = Step(ticket=ticket)

    if state is None:
        from rite_ai.local.plan_state import layer

        state = layer(root)

    read = dec.read(state, ticket)
    if read.unavailable:
        step.problem = (
            f"the decomposition for {ticket} could not be read: {read.unavailable}"
        )
        return step
    if read.error:
        step.problem = f"the decomposition for {ticket} will not parse: {read.error}"
        return step
    if read.plan is None:
        step.problem = (
            f"{ticket} has no decomposition, so there is nothing to run one "
            "subtask of. ⚠ rite has no decomposer yet: a plan is authored by "
            "hand and approved by a plan-review holder"
        )
        return step

    plan = read.plan
    if not plan.released:
        # Said here as well as enforced in `run_subtask`, because the reason a
        # Manager did nothing should not require reading the harness.
        step.problem = (
            f"{ticket}'s decomposition is {plan.approval}, not approved, so "
            "nothing from it may run (RL-6)"
        )
        return step

    subtask = next_subtask(plan)
    if subtask is None:
        done = sum(1 for s in plan.subtasks if s.status == dec.ACCEPTED)
        step.problem = (
            f"{ticket} has no subtask still planned ({done} of "
            f"{len(plan.subtasks)} accepted)"
        )
        return step

    spec_slice, slice_problem = _slice_for(
        root, subtask.cites, _definition_for(root, manager, ticket)
    )
    if slice_problem:
        step.problem = f"{ticket} {subtask.id}: {slice_problem}"
        return step

    # WHERE this subtask runs (SCRUM-54). A local Worker assigned to the ticket
    # runs it inside its own sandbox, against that sandbox's copy of its
    # checkout; with no such Worker this is empty and everything below is the
    # Manager-tier path exactly as it was.
    #
    # ⚠ Asked BEFORE the agent is defaulted, and the agent is defaulted FROM it.
    # A placement whose agent was overwritten by `_agent_for`'s host-run Goose
    # would run the turn on the operator's own tree while every other part of
    # this call — the workspace, the branch, the verify — pointed at a sandbox.
    if placement is None and agent is None:
        from rite_ai.local.worker_step import placement_for

        placement = placement_for(root, manager, ticket)
    if placement is not None and placement.problem:
        step.problem = f"{ticket} {subtask.id}: {placement.problem}"
        return step
    placed = bool(placement and placement.sandboxed)
    if placed:
        step.worker = placement.worker
        step.sandbox = placement.sandbox

    if agent is None:
        if placed and placement.agent is not None:
            agent = placement.agent
        elif placed:
            step.problem = (
                f"{ticket} {subtask.id}: {placement.worker} was placed in "
                f"sandbox {placement.sandbox} but no agent was built for it"
            )
            return step
        else:
            agent, agent_problem = _agent_for(root, manager)
            if agent_problem:
                step.problem = agent_problem
                return step

    # ⚠ The workspace is the SANDBOX's copy for a placed turn, and it has to be
    # all three of these together: the agent edits the copy, so a verify in the
    # host root would test a tree the model never touched, and a commit there
    # would put the work where `deliver` does not look for it.
    workspace = placement.workspace if placed else str(root)

    if verifier is None:
        from rite_ai.local.runners import SubprocessVerifier

        verifier = SubprocessVerifier()
    if committer is None:
        from rite_ai.local.runners import GitCommitter

        committer = GitCommitter(scope=subtask.scope)
    if claims is None:
        from rite_ai.claims.ledger import ClaimsLedger

        claims = _LedgerClaims(
            ClaimsLedger(root / ".rite" / "claims.json"),
            manager=manager,
            ticket=ticket,
        )

    # Level 2 (SCRUM-72e, overriding DD-2.4's fail-open per Robert's §5.5):
    # the unit's own steps, with its DECOMPOSITION model, before it edits
    # anything — REQUIRED and PERSISTED. Written here if it is not stored yet,
    # then the guard decides whether this subtask may run at all.
    from rite_ai.local import level2

    approach_note = ""
    stored = level2.read_approach(state, ticket, subtask.id)
    if stored is None or (
        not isinstance(stored, str) and stored.digest != level2.digest(subtask)
    ):
        # None yet, or written for a subtask this one is no longer — either
        # way this turn produces one. ⚠ A failure here is NOT a failed
        # subtask (RL-47): the guard below refuses the step and the caller
        # reports it, keeping the subtask's status and burning no attempt. An
        # endpoint that was down did not produce a bad approach.
        produce = approach_for or _approach_for
        produced, approach_note = produce(
            root, manager, subtask, spec_slice, placement if placed else None
        )
        if produced:
            written = level2.write_approach(state, ticket, subtask, produced)
            if not isinstance(written, dec.Written):
                approach_note = (
                    f"its Level-2 approach could not be persisted: "
                    f"{getattr(written, 'reason', 'it changed under this write')}"
                )

    # ⚠ **The boundary, against APPROVAL TIME.** The old check was
    # `boundary_problem(plan.subtask(subtask.id) or subtask, subtask)` — both
    # sides from the one read, so it could only ever pass, and what it exists
    # for is a subtask edited between approval and execution. `cleared_to_run`
    # compares the executing subtask against the digest `approve_plan`
    # recorded, and also refuses a step with no persisted approach. `scope` is
    # the committer's allowlist and `verify` is the only thing RL-7 trusts, so
    # a change to either turns the gates into decoration.
    verdict = level2.cleared_to_run(state, ticket, subtask)
    if isinstance(verdict, level2.Blocked):
        step.problem = f"{ticket} {subtask.id}: {verdict.why}" + (
            f" ({approach_note})" if approach_note else ""
        )
        return step
    approach = verdict.steps

    outcome = run_subtask(
        state=state,
        manager=manager,
        # ⚠ The CLAIM is taken in the Worker's name for a placed turn, not the
        # Manager's. Two Workers of one Manager working two tickets would
        # otherwise both claim as that Manager, and the claims ledger — whose
        # one job is exclusion — would read their overlapping scopes as one
        # holder re-claiming its own paths and let both through.
        worker=placement.worker if placed else manager,
        plan=plan,
        subtask=subtask,
        spec_slice=spec_slice,
        workspace=workspace,
        agent=agent,
        verifier=verifier,
        committer=committer,
        claims=claims,
        approach=approach,
        # The ticket for a placed turn: `deliver` collects `refs/heads/<ticket>`
        # from the sandbox and nothing else. See `run_subtask`'s own note.
        branch=placement.branch if placed else "",
    )
    step.ran = True
    _record(state, plan, subtask, outcome, read.version, step)
    if placed:
        # ⚠ AFTER `_record`, which ASSIGNS `step.lines` from the outcome — the
        # same trap `_approach_for`'s note below describes, and appending before
        # it would silently drop the line.
        # ⚠ Only says "committed" when there is a commit. A failed verify makes
        # no branch, and a line claiming one sends whoever reads it looking in a
        # sandbox for a ref that was never created.
        landed = (
            f"committed to {outcome.branch} ({outcome.commit[:8]})"
            if outcome.commit
            else f"nothing committed: {outcome.status}"
        )
        step.lines.append(
            f"ran inside {placement.worker}'s sandbox {placement.sandbox}, "
            f"in {placement.subdir}/, {landed}"
        )
    if approach_note:
        # ⚠ AFTER `_record`, which assigns `step.lines` from the outcome — a note
        # appended before it was silently dropped, which is how a Level-2 failure
        # would have gone unreported while looking like it never happened.
        step.lines.append(approach_note)
    return step


def _approach_for(root: Path, manager: str, subtask, spec_slice: str, placement=None):
    """(the approach, a note to record) — Level 2 for this unit (DD-2.4, OL6).

    ⚠ **A PLACED turn plans inside its own sandbox, with the Worker's own
    model**, and the placement carries that callable ready-made
    (`worker_step.Placement.approach`). Reading the Manager's role for a placed
    subtask was wrong three ways: it ran a write-capable auto-mode model turn on
    the HOST in the operator's real project tree — the containment the placement
    exists to provide, skipped by the planning half; it planned against a tree
    where the subtask's module-relative scope paths do not exist and the earlier
    subtasks' commits are not present; and it ignored OL3's model split, so a
    Claude Manager driving a GPU Worker produced no approach at all.

    ⚠ **Never raises and never refuses the turn.** An approach improves a turn;
    it is not a gate on one. So every failure here returns `("", why)` and the
    subtask runs without it — which is also RL-47's distinction: an endpoint that
    was down did not produce a bad approach, it produced none.

    The model is the unit's own Level-2 model (`decomposition_model_for`), which
    is Opus for a Claude unit and the unit's OWN model for a local one — a GPU
    unit has nothing to gain from loading a second set of weights beside the one
    it is about to implement with.
    """
    if placement is not None:
        # ⚠ A placed turn gets the PLACEMENT's Level 2 or none at all — never
        # the Manager's, which runs on the host in the operator's project root.
        # Falling through would have put the thing this just fixed back behind
        # a `None`, and a placement built without an approach (a test's, or a
        # future caller's) is exactly where that would happen silently.
        if placement.approach is None:
            return "", ""
        result = placement.approach(subtask, spec_slice)
        if result.ok:
            return result.steps, ""
        return "", f"no Level-2 approach for {subtask.id}: {result.problem}"

    from rite_ai.config.managers import decomposition_model_for
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.local.approach import plan_approach

    parsed = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return "", ""
    role = next(
        (r for r in parsed.coordination.manager_roles if r.name == manager), None
    )
    if role is None or not role.is_local:
        # Level 2's Claude side is a separate piece (DD-2.4); this is the local
        # one, which is what the proof runs unblocked.
        return "", ""
    result = plan_approach(
        subtask,
        spec_slice,
        model=decomposition_model_for(role),
        endpoint=role.endpoint,
        workspace=str(root),
        context_limit=getattr(role, "context_window", 0),
    )
    if result.ok:
        return result.steps, ""
    return "", f"no Level-2 approach for {subtask.id}: {result.problem}"


def _agent_for(root: Path, manager: str):
    """(the agent, or a problem). The Manager's declared engine, never a
    default: a local Manager runs the model its role names (`a56ecd1`)."""
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return None, f"this project's config.yaml will not parse: {parsed.message}"
    role = next(
        (r for r in parsed.coordination.manager_roles if r.name == manager), None
    )
    if role is None:
        return None, f"no Manager named {manager!r} in this project"
    if not role.is_local:
        return None, (
            f"{manager!r} is not a local Manager ({role.engine}), and this path "
            "runs a local agent against a declared endpoint"
        )
    if role.agent != "goose":
        # ⚠ S35: window enforcement is Goose-shaped today (`pin_window` plus
        # `GOOSE_CONTEXT_LIMIT`), so another agent would run at the server's
        # default — 4,096 on an unconfigured Ollama, smaller than the agents'
        # own prompts (RL-T0). Refused rather than run unenforced.
        return None, (
            f"{manager!r} declares agent {role.agent!r}; only 'goose' has "
            "window enforcement today (S35), and an unenforced window is how "
            "a 4,096-token default silently ruins a run"
        )
    from rite_ai.local.engine_probe import probe_engine
    from rite_ai.local.goose_agent import GooseAgent

    # ⚠ **THE PROBE IS INJECTED FROM THE REAL ROLE, and it has to be.**
    # `GooseAgent._probe` otherwise FABRICATES a `ManagerRole` — name "local",
    # engine "local:agent", and no `context_window` — and then probes it. The
    # window check refuses a local role that declares none, so the agent can
    # never pass its own preflight on the default path: measured here, the
    # first real run returned in 0s with "manager local: declares no
    # context_window" for a Manager whose role declares 32768.
    #
    # The caller is the one that knows the role, so the caller supplies it.
    # Fabricating a role to probe was the bug; passing the one the project
    # actually configured is the fix.
    return (
        GooseAgent(
            model=role.model,
            endpoint=role.endpoint,
            probe=lambda r=role: probe_engine(r),
        ),
        "",
    )


def _record(state, plan, subtask, outcome: Outcome, version: str, step: Step) -> None:
    """Write the outcome into the decomposition, and fill `step`.

    ⚠ **rite writes this, not the model.** The reporting half of both failed
    runs was the model's job and it did not do it; here the status, the branch,
    the commit and the attempt count are rite's own record of what rite
    observed.
    """
    step.subtask = outcome.subtask_id
    step.status = outcome.status
    step.accepted = outcome.accepted
    step.branch = outcome.branch
    step.commit = outcome.commit
    step.claim_disagreed = outcome.claim_disagreed_with_verify
    step.verify_output = outcome.verify_output
    step.lines = list(outcome.notes)

    # ⚠ **A STOPPED turn does not record the subtask as FAILED** (SCRUM-103).
    # `next_subtask` makes FAILED terminal on purpose, so a turn cut off by a
    # clock would end the subtask without anything having judged the work. A
    # stop leaves it PLANNED, up to `MAX_STOPS`, and fails it at the bound —
    # which is what keeps a genuinely hanging subtask from being retried for
    # ever, the objection `goose_agent` raised against the obvious fix.
    stopped = bool(getattr(outcome, "stopped", False)) and not outcome.accepted
    stops = subtask.stops + (1 if stopped else 0)
    status = outcome.status
    if stopped and stops < MAX_STOPS:
        status = dec.PLANNED
        step.lines.append(
            f"the turn was stopped on a timeout, not judged: {subtask.id} stays "
            f"planned ({stops} of {MAX_STOPS} stops used)"
        )
    elif stopped:
        step.lines.append(
            f"{subtask.id} has now been stopped {stops} time(s), the limit, so "
            f"it is recorded as failed — the work was never judged, and a "
            f"person or a step review decides what happens to it"
        )

    # ⚠ **A subtask THAT NEVER STARTED is not a failed one either.** The same
    # argument as the stop above, and it had the same hole: `next_subtask`
    # makes FAILED terminal, so a subtask refused before the agent ran — a
    # neighbour holding part of its scope — was retired without ever being
    # tried. `infrastructure_fault` kept it from spending an attempt and
    # stopped there; nothing carried it to the status.
    #
    # Measured on gate run smoke_mixed-20261009T113241Z: s1 held
    # `attempts=0` and `status=failed` together, the implementing subtask
    # never ran, and the ticket could not reach `recomposed`. The claim it
    # waited on was released three minutes later, by the delivery of the very
    # Worker that held it.
    never_started = bool(getattr(outcome, "never_started", False)) and not (
        outcome.accepted
    )
    skips = subtask.skips + (1 if never_started else 0)
    if never_started and skips < MAX_SKIPS:
        status = dec.PLANNED
        step.lines.append(
            f"nothing ran, so this is not a verdict: {subtask.id} stays planned "
            f"({skips} of {MAX_SKIPS} skips used)"
        )
    elif never_started:
        step.lines.append(
            f"{subtask.id} could not start {skips} time(s), the limit, so it is "
            f"recorded as failed — nothing ever ran, and a person or a step "
            f"review decides what happens to it"
        )

    updated = dec.with_subtask(
        plan,
        replace(
            subtask,
            stops=stops,
            skips=skips,
            status=status,
            # ⚠ **RL-47, through the property that decides it.**
            # `Outcome.counts_as_attempt` says "work counts, and a turn that
            # never happened does not" — and this incremented
            # unconditionally, so the property had no reader in `src/` at all
            # and the rule it states was not in force anywhere. An endpoint
            # that was down, a sandbox a delivery had just stopped, a launch
            # rite's own leak guard refused: each spent one of the subtask's
            # attempts, and its own docstring says why that is wrong — "it is
            # a machine that was not ready, and counting it would retire a
            # subtask nobody tried".
            attempts=subtask.attempts + (1 if outcome.counts_as_attempt else 0),
            branch=outcome.branch,
            last_failure=("" if outcome.accepted else _why_it_stopped(outcome)),
        ),
    )
    written = dec.write(state, updated, version)
    kind = type(written).__name__
    if kind != "Written":
        # Said, never raised: the subtask ran and its branch exists whatever
        # the bookkeeping did, and losing that fact would be worse than a
        # stale status.
        step.lines.append(
            f"the outcome could not be recorded in {plan.ticket}'s "
            f"decomposition ({kind}); the branch {outcome.branch} is still there"
        )
