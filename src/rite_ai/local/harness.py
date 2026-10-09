"""Harness core: one subtask, start to finish (RL-T6).

**Orchestration, not an agent** (RL-13). The riskiest thing this design could
build is a reliable tool-using loop for a small model, and two already exist —
so rite pulls the assignment, prepares the workspace, hands the agent exactly
one subtask and its spec slice, runs the verify ITSELF, commits to a local
branch, reports, and releases. The agent supplies file editing and the model's
own loop, and nothing else.

Three properties are structural here rather than conventional:

- **The agent's claim of success is recorded and never trusted.** `accepted`
  is derived from the verify alone. A claim that disagrees with the verify is
  kept, because that disagreement is the honesty signal RL-T0 measures and the
  thing a step review most needs to see.
- **Nothing here pushes, and nothing here invokes `claude`.** The harness is
  rite's own code, and SPEC §5.1.1 forbids rite's code a remote-writing verb;
  local engines commit to a local task branch and stop (RL-11). `integrate`
  takes it from there.
- **Every coordination read and write goes through the state-layer interface**
  (RL-35). No git call and no backend name appears in this module; git is used
  only to commit to the local task branch, through an injected committer, so a
  test can assert what was asked of it.

The agent, the verify runner and the committer are injected rather than
chosen. RL-T0 has not reported yet, so which agent is adopted is still open —
and a harness that can only be tested with a live model is a harness nobody
can test. Failure paths are RL-T28 deliberately: this is the path where things
work, and a verify that fails is part of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from rite_ai.coordination.heartbeat import publish_heartbeat
from rite_ai.coordination.state_layer import StateLayer
from rite_ai.local.decomposition import (
    ACCEPTED,
    FAILED,
    RUNNING,
    VERIFIED,
    Decomposition,
    Subtask,
)


@dataclass(frozen=True)
class Context:
    """Exactly what the agent is given (RL-17).

    The spec SLICE, never the spec and never a pointer into it: D-52's pointer
    assumes a context window large enough to go and read the file, and a small
    model's is not. If this ever carries more than one subtask, the tier has
    stopped being what it was measured as.
    """

    subtask: Subtask
    spec_slice: str
    ticket: str
    approach: str = ""
    """Level 2 (DD-2.4): the unit's OWN steps for this subtask, produced at the
    front of the turn with its decomposition model.

    ⚠ **A working artifact, and it stops here.** It is never written into the
    `Decomposition` — "if it is written into `Decomposition`, the gates start
    reading the executor's own words", which is what keeps Level 2 outside the
    gates. Empty is the ordinary case: a unit with no decomposition model, or a
    Level-2 step that could not run, executes exactly as before, because an
    approach improves a turn and is not a gate on it."""


@dataclass(frozen=True)
class AgentReport:
    """What the agent says it did. Evidence about the agent, not about the
    work — `claimed_success` is never what decides."""

    claimed_success: bool
    summary: str = ""
    touched: tuple[str, ...] = ()
    infrastructure_fault: bool = False
    """The turn did not happen: an endpoint that was down, a model that is not
    there, a binary that is missing.

    ⚠ **A FIELD, because this was carried in the summary's wording.** RL-47 is
    that only work counts — "an endpoint that was down is not an attempt" —
    and `goose_agent`'s own docstring says "infrastructure faults are not
    attempts". But the only trace of it was the prefix "infrastructure fault
    before the turn:" on a free-text summary, so the one caller that has to
    act on it would have had to match a sentence. Measured: the first real run
    of the wired pipeline was refused before the turn and still counted as an
    attempt."""


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    output: str = ""
    ran: bool = True
    """Whether the command EXECUTED at all, as distinct from passing (RL-47).

    🔴 **It was missing, and the distinction was being made and then lost.**
    `runners.SubprocessVerifier` catches `FileNotFoundError` deliberately —
    "the tool is not installed on this machine… saying the verify failed would
    spend an attempt proving a machine was set up wrong" — and then had
    nowhere to put that fact: the result was `(passed=False, output=<prose>)`,
    so every consumer saw a failing verify. RL-7 spent an attempt against
    `MAX_ATTEMPTS`, and RL-8 reported "the composed work FAILS its agreed
    verify" and returned the plan to review, spending an RL-10 return that no
    plan change can satisfy.

    Measured on the v0.7.0 gate run (SCRUM-95): a definition of done agreed as
    `python -m pytest -q tests/test_format.py` cannot run at all on a
    uv-managed host, where there is no bare `python`. That is the natural
    phrasing, not an exotic one.

    ⚠ A TIMEOUT is `ran=True`. It executed and was stopped; treating it as a
    non-attempt would retry a hanging verify without bound.
    """


@dataclass(frozen=True)
class Commit:
    sha: str = ""
    error: str = ""
    nothing_to_commit: bool = False
    """There was no commit to make, as distinct from a commit that FAILED.

    🔴 **SCRUM-96: this was the difference between a delivered ticket and a
    wedged one.** `run_subtask` reads `if commit.error:` and leaves the
    subtask at `VERIFIED`, which has no successor — the local tier then
    reports "neither runnable nor finished" every cycle, for ever. Measured on
    the v0.7.0 gate run: s2 asked for a test that s1's work had already made
    pass, so the agent correctly changed nothing, the verify passed, and the
    ticket could never finish.

    A subtask whose required outcome already holds is a PASS. RL-7's rule is
    that the verify alone decides, and the verify said yes."""
    note: str = ""
    """Something a reader must know about HOW this commit was made, when the
    commit itself succeeded. Carried onto the outcome's notes.

    It exists for one thing (SCRUM-64 follow-up review): a local-tier commit
    runs with the project's hooks DISABLED, so a `pre-commit` the project
    relies on did not run — and that was silent. A difference between how
    rite commits and how a person commits is a difference a reader has to be
    told about, whether or not rite thinks it is the right difference."""


class Agent(Protocol):
    def run(self, context: Context, workspace: str) -> AgentReport: ...


class Verifier(Protocol):
    def run(self, command: str, workspace: str) -> VerifyResult: ...


class Committer(Protocol):
    """Commits to a LOCAL branch. There is no push in this protocol, and that
    is the point: a harness cannot call what it was never given."""

    def commit_to_branch(self, workspace: str, branch: str, message: str) -> Commit: ...


class Claims(Protocol):
    def take(self, paths: tuple[str, ...], worker: str) -> bool: ...

    def release(self, paths: tuple[str, ...], worker: str) -> None: ...


@dataclass
class Outcome:
    subtask_id: str
    status: str = RUNNING
    agent_claimed: bool = False
    verified: bool = False
    # Kept because it is the honesty signal: the agent said it was done and the
    # verify disagreed. RL-T0 measures this, and a step review needs to see it.
    claim_disagreed_with_verify: bool = False
    branch: str = ""
    commit: str = ""
    verify_output: str = ""
    notes: list[str] = field(default_factory=list)
    infrastructure_fault: bool = False
    """Carried from the agent's report, for `counts_as_attempt`."""

    @property
    def counts_as_attempt(self) -> bool:
        """Whether this run is an ATTEMPT at the subtask (RL-47).

        The rule lives here rather than in the caller, so every caller gets
        the same answer: work counts, and a turn that never happened does
        not. A `FAILED` subtask whose agent never ran is not a failing
        subtask — it is a machine that was not ready, and counting it would
        retire a subtask nobody tried.
        """
        return not self.infrastructure_fault

    @property
    def accepted(self) -> bool:
        """Derived from the verify ALONE (RL-7). Not from the agent, and not
        from the two agreeing."""
        return self.status == ACCEPTED


def branch_for(ticket: str, subtask_id: str) -> str:
    return f"rite-local/{ticket}/{subtask_id}"


def run_subtask(
    *,
    state: StateLayer,
    manager: str,
    worker: str,
    plan: Decomposition,
    subtask: Subtask,
    spec_slice: str,
    workspace: str,
    agent: Agent,
    verifier: Verifier,
    committer: Committer,
    claims: Claims,
    in_flight: int = 1,
    approach: str = "",
    branch: str = "",
) -> Outcome:
    """One subtask: claim, run, verify, commit, report, release.

    Refuses to start unless the plan was approved (RL-6). Releasing happens on
    every path out, because a claim held by a finished subtask blocks the
    fleet until something notices.

    `branch` overrides `branch_for`, and exactly one caller needs it. A
    Manager-tier subtask commits to `rite-local/<ticket>/<subtask>`, one branch
    per subtask, and something later composes them (RL-T30, unbuilt). A subtask
    run in a WORKER's sandbox has a harder constraint: `publishing/deliver.py`
    collects `refs/heads/<ticket>` from that sandbox's copy and nothing else, so
    a per-subtask branch there is work `deliver` reports as "the Worker made no
    branch <ticket>". `worker_step` passes the ticket, and the subtasks of one
    ticket — run one at a time, in plan order, in one copy — compose by being
    committed in sequence onto it. ⚠ That is not a shortcut around RL-T30: it is
    why the Worker path does not NEED it, and the Manager path still does.
    """
    outcome = Outcome(
        subtask_id=subtask.id, branch=branch or branch_for(plan.ticket, subtask.id)
    )

    if not plan.released:
        # The gate, enforced where the work would start rather than trusted to
        # whoever assigns. A subtask running from an unapproved plan is the one
        # thing plan review exists to prevent.
        outcome.status = FAILED
        # ⚠ Not an attempt: nothing ran. `counts_as_attempt` is the one place
        # RL-47 is decided, and these two early returns are the harness's OWN
        # "nothing ran" paths — the claim conflict below says so in its note
        # and still spent one, which is how a subtask blocked by a neighbour's
        # claim could be retired without ever having been tried.
        outcome.infrastructure_fault = True
        outcome.notes.append(
            f"{plan.ticket} is not approved by a plan-review holder, so nothing "
            "from its decomposition may run"
        )
        return outcome

    if not claims.take(subtask.scope, worker):
        outcome.status = FAILED
        # The note already said this; now it is true. See above.
        outcome.infrastructure_fault = True
        outcome.notes.append(
            f"another worker holds part of {', '.join(subtask.scope)} — not "
            "started, so nothing here counts as an attempt"
        )
        return outcome

    try:
        publish_heartbeat(state, manager, workers=[worker], in_flight=in_flight)

        report = agent.run(
            Context(
                subtask=subtask,
                spec_slice=spec_slice,
                ticket=plan.ticket,
                approach=approach,
            ),
            workspace,
        )
        outcome.agent_claimed = report.claimed_success
        outcome.infrastructure_fault = report.infrastructure_fault
        if report.summary:
            # ⚠ KEPT, because discarding it hid the first real failure. The
            # agent refused before its turn and said why in `summary`; the
            # outcome carried only `claimed_success`, so the operator saw a
            # verify failing on a missing file and nothing about the turn
            # never having happened. The agent's words are evidence about the
            # agent — the same reason the disagreement below is kept.
            outcome.notes.append(f"the agent said: {report.summary}")

        # rite runs the verify itself. The agent's report is not consulted to
        # decide whether to run it: a verify skipped because the agent said it
        # failed is a verify the agent controls.
        result = verifier.run(subtask.verify, workspace)
        outcome.verified = result.passed
        outcome.verify_output = result.output
        # ⚠ A verify that never RAN contradicts nobody. The agent is not shown
        # to have been wrong by a command that did not execute, so the honesty
        # signal is not raised — and the run is not an attempt (RL-47), which
        # is what keeps `MAX_ATTEMPTS` from retiring a subtask over a missing
        # interpreter. `or`, not `=`: the agent's own fault still stands.
        ran = getattr(result, "ran", True)
        outcome.claim_disagreed_with_verify = ran and (
            report.claimed_success != result.passed
        )
        if not ran:
            outcome.infrastructure_fault = True
            outcome.notes.append(f"the verify never ran: {result.output}")

        if not result.passed:
            outcome.status = FAILED
            if report.claimed_success and ran:
                outcome.notes.append(
                    "the agent reported success and the verify disagreed — kept "
                    "as evidence about the agent, not about the work"
                )
            return outcome

        commit = committer.commit_to_branch(
            workspace,
            outcome.branch,
            f"{plan.ticket} {subtask.id}: {subtask.intent}",
        )
        if commit.error and getattr(commit, "nothing_to_commit", False):
            # ⚠ ACCEPTED WITH NO COMMIT, and said in those words (SCRUM-96).
            # There is nothing for composition to apply because the required
            # state already held — which is not the same as work that could
            # not be committed, and the two were indistinguishable here.
            #
            # The guard this does NOT weaken: an ACCEPTED subtask carrying an
            # empty sha used to mean "the commit was made and rite lost the
            # reference", which composition would apply blindly. That case
            # still fails below, because it arrives with `error` set and
            # `nothing_to_commit` false.
            outcome.status = ACCEPTED
            outcome.notes.append(
                f"accepted with no commit: {commit.error}. The verify passed, "
                "so the subtask's required state holds; there is nothing for "
                "composition to apply"
            )
            return outcome
        if commit.error:
            # Verified work that could not be committed is not accepted: the
            # branch is what composition later applies, and there is nothing
            # to apply.
            outcome.status = VERIFIED
            outcome.notes.append(f"verified but not committed: {commit.error}")
            return outcome

        outcome.commit = commit.sha
        if commit.note:
            outcome.notes.append(commit.note)
        outcome.status = ACCEPTED
        return outcome
    finally:
        claims.release(subtask.scope, worker)
