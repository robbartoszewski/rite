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


@dataclass(frozen=True)
class AgentReport:
    """What the agent says it did. Evidence about the agent, not about the
    work — `claimed_success` is never what decides."""

    claimed_success: bool
    summary: str = ""
    touched: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    output: str = ""


@dataclass(frozen=True)
class Commit:
    sha: str = ""
    error: str = ""


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
) -> Outcome:
    """One subtask: claim, run, verify, commit, report, release.

    Refuses to start unless the plan was approved (RL-6). Releasing happens on
    every path out, because a claim held by a finished subtask blocks the
    fleet until something notices.
    """
    outcome = Outcome(subtask_id=subtask.id, branch=branch_for(plan.ticket, subtask.id))

    if not plan.released:
        # The gate, enforced where the work would start rather than trusted to
        # whoever assigns. A subtask running from an unapproved plan is the one
        # thing plan review exists to prevent.
        outcome.status = FAILED
        outcome.notes.append(
            f"{plan.ticket} is not approved by a plan-review holder, so nothing "
            "from its decomposition may run"
        )
        return outcome

    if not claims.take(subtask.scope, worker):
        outcome.status = FAILED
        outcome.notes.append(
            f"another worker holds part of {', '.join(subtask.scope)} — not "
            "started, so nothing here counts as an attempt"
        )
        return outcome

    try:
        publish_heartbeat(state, manager, workers=[worker], in_flight=in_flight)

        report = agent.run(
            Context(subtask=subtask, spec_slice=spec_slice, ticket=plan.ticket),
            workspace,
        )
        outcome.agent_claimed = report.claimed_success

        # rite runs the verify itself. The agent's report is not consulted to
        # decide whether to run it: a verify skipped because the agent said it
        # failed is a verify the agent controls.
        result = verifier.run(subtask.verify, workspace)
        outcome.verified = result.passed
        outcome.verify_output = result.output
        outcome.claim_disagreed_with_verify = report.claimed_success != result.passed

        if not result.passed:
            outcome.status = FAILED
            if report.claimed_success:
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
        if commit.error:
            # Verified work that could not be committed is not accepted: the
            # branch is what composition later applies, and there is nothing
            # to apply.
            outcome.status = VERIFIED
            outcome.notes.append(f"verified but not committed: {commit.error}")
            return outcome

        outcome.commit = commit.sha
        outcome.status = ACCEPTED
        return outcome
    finally:
        claims.release(subtask.scope, worker)
