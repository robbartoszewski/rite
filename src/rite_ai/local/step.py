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
    verify_output: str = ""
    lines: list[str] = field(default_factory=list)
    problem: str = ""
    """Why nothing ran. Empty when something did — including when it FAILED,
    which is a result and not a problem with asking."""


def _slice_for(root: Path, cites: tuple[str, ...]) -> tuple[str, str]:
    """(the cited spec text, or a problem). Never a silent empty slice.

    The slice IS the context the model gets instead of the spec, so sending
    nothing would quietly reproduce the free-form run this replaces. A cite
    rite cannot resolve is a refusal, not a shrug.
    """
    from rite_ai.spec.digest_files import unit_filename, units_dir

    if not cites:
        return "", "the subtask cites no spec unit, so there is no slice to give"
    where = units_dir(Path(root))
    parts: list[str] = []
    missing: list[str] = []
    for cite in cites:
        path = where / unit_filename(cite)
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            missing.append(cite)
    if missing:
        return "", (
            f"the derived spec text for {', '.join(missing)} is not in "
            f"{where} — run `rite spec index` so the slice can be built, "
            "because a subtask run without its slice is the free-form run "
            "this path exists to replace"
        )
    return "\n\n".join(parts), ""


@dataclass
class _LedgerClaims:
    """`harness.Claims` over rite's own ledger.

    An adapter rather than a second ledger: exclusion is the one property the
    claims ledger exists for, and a local tier with its own would be a second
    answer to "who holds this path". `manager=` is passed so a force-release
    cannot reach across Managers (MM3).
    """

    ledger: object
    manager: str = ""

    def take(self, paths: tuple[str, ...], worker: str) -> bool:
        return bool(self.ledger.claim(list(paths), worker, manager=self.manager).ok)

    def release(self, paths: tuple[str, ...], worker: str) -> None:
        self.ledger.release(worker, list(paths))


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
) -> Step:
    """Run the next planned subtask of `ticket`, or say why nothing ran.

    Every dependency is injectable and every default is the production one, so
    a test substitutes the agent without substituting the path.
    """
    root = Path(root)
    step = Step(ticket=ticket)

    if state is None:
        from rite_ai.coordination.local_backend import LocalStateLayer

        state = LocalStateLayer(root / ".rite")

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

    spec_slice, slice_problem = _slice_for(root, subtask.cites)
    if slice_problem:
        step.problem = f"{ticket} {subtask.id}: {slice_problem}"
        return step

    if agent is None:
        agent, agent_problem = _agent_for(root, manager)
        if agent_problem:
            step.problem = agent_problem
            return step

    if verifier is None:
        from rite_ai.local.runners import SubprocessVerifier

        verifier = SubprocessVerifier()
    if committer is None:
        from rite_ai.local.runners import GitCommitter

        committer = GitCommitter(scope=subtask.scope)
    if claims is None:
        from rite_ai.claims.ledger import ClaimsLedger

        claims = _LedgerClaims(
            ClaimsLedger(root / ".rite" / "claims.json"), manager=manager
        )

    # Level 2 (DD-2.4, OL6): the unit's own steps, with its DECOMPOSITION model,
    # before it edits anything. Advisory by design — a unit with no decomposition
    # model, or a Level-2 turn that could not run, executes exactly as before.
    approach, approach_note = _approach_for(root, manager, subtask, spec_slice)

    # ⚠ The boundary, checked rather than trusted (DD-2.4). Level 2 runs between
    # approval and execution, so a subtask it altered would be rewriting what
    # plan review passed — and `scope` is the committer's allowlist while
    # `verify` is the only thing RL-7 trusts. `_approach_for` is given no way to
    # return a subtask, so this is belt and braces; the design asks for a check
    # and not a convention, and a structural guarantee somebody can refactor
    # away is a convention.
    from rite_ai.local.approach import boundary_problem

    drifted = boundary_problem(plan.subtask(subtask.id) or subtask, subtask)
    if drifted:
        step.problem = f"{ticket} {subtask.id}: {drifted}"
        return step

    outcome = run_subtask(
        state=state,
        manager=manager,
        worker=manager,
        plan=plan,
        subtask=subtask,
        spec_slice=spec_slice,
        workspace=str(root),
        agent=agent,
        verifier=verifier,
        committer=committer,
        claims=claims,
        approach=approach,
    )
    step.ran = True
    _record(state, plan, subtask, outcome, read.version, step)
    if approach_note:
        # ⚠ AFTER `_record`, which assigns `step.lines` from the outcome — a note
        # appended before it was silently dropped, which is how a Level-2 failure
        # would have gone unreported while looking like it never happened.
        step.lines.append(approach_note)
    return step


def _approach_for(root: Path, manager: str, subtask, spec_slice: str):
    """(the approach, a note to record) — Level 2 for this unit (DD-2.4, OL6).

    ⚠ **Never raises and never refuses the turn.** An approach improves a turn;
    it is not a gate on one. So every failure here returns `("", why)` and the
    subtask runs without it — which is also RL-47's distinction: an endpoint that
    was down did not produce a bad approach, it produced none.

    The model is the unit's own Level-2 model (`decomposition_model_for`), which
    is Opus for a Claude unit and the unit's OWN model for a local one — a GPU
    unit has nothing to gain from loading a second set of weights beside the one
    it is about to implement with.
    """
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

    updated = dec.with_subtask(
        plan,
        replace(
            subtask,
            status=outcome.status,
            attempts=subtask.attempts + 1,
            branch=outcome.branch,
            last_failure=(
                "" if outcome.accepted else (outcome.verify_output or "")[:500]
            ),
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
