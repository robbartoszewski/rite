"""Harness core (RL-T6): one subtask, start to finish.

The properties worth a test are the ones a plausible implementation gets wrong
while looking right:

- the agent's claim never decides anything;
- a verify is run even when the agent says it failed, because a verify the
  agent can skip is a verify the agent controls;
- nothing runs from a plan no plan-review holder approved;
- the claim is released on every path out, including the failures.
"""

from __future__ import annotations

import pytest

from rite_ai.coordination.state_layer import Absent, Present, Written
from rite_ai.local.decomposition import (
    ACCEPTED,
    APPROVED,
    FAILED,
    VERIFIED,
    Decomposition,
    Subtask,
)
from rite_ai.local.harness import (
    AgentReport,
    Commit,
    Context,
    VerifyResult,
    branch_for,
    run_subtask,
)

SUBTASK = Subtask(
    id="s1",
    intent="add the parser",
    scope=("parser.py",),
    verify="pytest tests/test_parser.py",
)


def _plan(approved: bool = True) -> Decomposition:
    return Decomposition(
        ticket="ABC-1",
        subtasks=(SUBTASK,),
        decomposed_by="planner",
        approval=APPROVED if approved else "pending",
        approved_by="lead" if approved else "",
    )


class FakeState:
    def __init__(self):
        self.values: dict[str, tuple[bytes, str]] = {}
        self.n = 0

    def read_state(self, key: str):
        if key not in self.values:
            return Absent()
        value, version = self.values[key]
        return Present(value=value, version=version)

    def write_state(self, key: str, value: bytes, expected_version: str):
        self.n += 1
        self.values[key] = (value, f"v{self.n}")
        return Written(version=f"v{self.n}")

    def append_message(self, content: str):  # pragma: no cover
        raise NotImplementedError

    def read_messages(self, since=None, limit=None):  # pragma: no cover
        raise NotImplementedError


class FakeAgent:
    def __init__(self, claimed: bool = True):
        self.claimed = claimed
        self.contexts: list[Context] = []

    def run(self, context: Context, workspace: str) -> AgentReport:
        self.contexts.append(context)
        return AgentReport(claimed_success=self.claimed, summary="did the thing")


class FakeVerifier:
    def __init__(self, passes: bool = True):
        self.passes = passes
        self.calls: list[str] = []

    def run(self, command: str, workspace: str) -> VerifyResult:
        self.calls.append(command)
        return VerifyResult(
            passed=self.passes, output="2 passed" if self.passes else "1 failed"
        )


class FakeCommitter:
    def __init__(self, error: str = ""):
        self.error = error
        self.branches: list[str] = []

    def commit_to_branch(self, workspace: str, branch: str, message: str) -> Commit:
        self.branches.append(branch)
        return Commit(error=self.error) if self.error else Commit(sha="abc1234")


class FakeClaims:
    def __init__(self, grant: bool = True):
        self.grant = grant
        self.taken: list[tuple[str, ...]] = []
        self.released: list[tuple[str, ...]] = []

    def take(self, paths, worker):
        if self.grant:
            self.taken.append(paths)
        return self.grant

    def release(self, paths, worker):
        self.released.append(paths)


def _run(**kw):
    defaults = dict(
        state=FakeState(),
        manager="planner",
        worker="alpha",
        plan=_plan(),
        subtask=SUBTASK,
        spec_slice="## 5.3 Sandboxing\nWorkers run sandboxed.",
        workspace="/tmp/ws",
        agent=FakeAgent(),
        verifier=FakeVerifier(),
        committer=FakeCommitter(),
        claims=FakeClaims(),
    )
    defaults.update(kw)
    return defaults, run_subtask(**defaults)


# --- the verify decides, and only the verify --------------------------------------


def test_a_passing_verify_accepts_the_subtask():
    _, outcome = _run()
    assert outcome.status == ACCEPTED and outcome.accepted
    assert outcome.commit == "abc1234"
    assert outcome.branch == branch_for("ABC-1", "s1")


def test_an_agent_claiming_success_against_a_failing_verify_is_not_accepted():
    """The single most important line in the harness: `accepted` is derived
    from the verify alone. An agent that says it is done does not make it so."""
    _, outcome = _run(
        agent=FakeAgent(claimed=True), verifier=FakeVerifier(passes=False)
    )
    assert not outcome.accepted
    assert outcome.status == FAILED
    assert outcome.agent_claimed is True
    assert outcome.verified is False


def test_the_disagreement_is_kept_because_it_is_evidence_about_the_agent():
    """RL-T0 measures dishonest reporting, and a step review needs to see it.
    Discarding it would leave the tier's most important failure invisible."""
    _, outcome = _run(
        agent=FakeAgent(claimed=True), verifier=FakeVerifier(passes=False)
    )
    assert outcome.claim_disagreed_with_verify
    assert any("disagreed" in n for n in outcome.notes)


def test_an_agent_that_admits_failure_is_still_verified():
    """A verify the agent can skip is a verify the agent controls. Running it
    anyway also catches the case where the work succeeded and the agent did not
    notice."""
    args, outcome = _run(
        agent=FakeAgent(claimed=False), verifier=FakeVerifier(passes=True)
    )
    assert args["verifier"].calls == [SUBTASK.verify]
    assert outcome.accepted
    assert outcome.claim_disagreed_with_verify  # honest failure, passing verify


def test_rite_runs_the_verify_the_subtask_declared():
    args, _ = _run()
    assert args["verifier"].calls == ["pytest tests/test_parser.py"]


# --- the gate, enforced where the work would start ---------------------------------


def test_nothing_runs_from_a_plan_no_plan_review_holder_approved():
    """RL-6. Enforced here rather than trusted to whoever assigns: a subtask
    running from an unapproved plan is the one thing plan review prevents."""
    args, outcome = _run(plan=_plan(approved=False))
    assert outcome.status == FAILED
    assert args["verifier"].calls == []
    assert args["agent"].contexts == []
    assert any("not approved" in n for n in outcome.notes)


def test_an_unapproved_plan_does_not_even_take_the_claim():
    args, _ = _run(plan=_plan(approved=False))
    assert args["claims"].taken == []


# --- what the agent is given ------------------------------------------------------


def test_the_agent_gets_one_subtask_and_a_slice_never_the_spec():
    """RL-17: D-52's pointer assumes a context window big enough to go and read
    the file, and a small model's is not."""
    args, _ = _run()
    (context,) = args["agent"].contexts
    assert context.subtask == SUBTASK
    assert context.spec_slice.startswith("## 5.3")
    assert context.ticket == "ABC-1"


# --- claims and heartbeat ----------------------------------------------------------


def test_the_claim_is_released_on_every_path_out():
    for kw in (
        {},
        {"verifier": FakeVerifier(passes=False)},
        {"committer": FakeCommitter(error="nothing to commit")},
    ):
        args, _ = _run(**kw)
        assert args["claims"].released == [SUBTASK.scope], kw


def test_a_claim_that_cannot_be_taken_is_not_an_attempt():
    """RL-47: only work counts. A subtask that never started must not spend a
    budget meant for work that failed."""
    args, outcome = _run(claims=FakeClaims(grant=False))
    assert outcome.status == FAILED
    assert args["agent"].contexts == []
    assert any("not started" in n for n in outcome.notes)


def test_the_heartbeat_carries_the_in_flight_count():
    """P2-4a — which does exist: `publish_heartbeat(..., in_flight=...)`."""
    args, _ = _run(in_flight=2)
    state = args["state"]
    assert "managers/planner.json" in state.values
    assert b'"in_flight": 2' in state.values["managers/planner.json"][0]


# --- verified work that could not be committed -------------------------------------


def test_verified_but_uncommitted_is_not_accepted():
    """Composition applies branches. Work with no branch is work composition
    cannot apply, however green its verify was."""
    _, outcome = _run(committer=FakeCommitter(error="nothing to commit"))
    assert outcome.status == VERIFIED
    assert not outcome.accepted
    assert any("not committed" in n for n in outcome.notes)


# --- what the harness must not be able to do ---------------------------------------


def test_the_committer_protocol_offers_no_way_to_push():
    """SPEC §5.1.1 forbids rite's code a remote-writing verb, and RL-11 makes
    that a property of the tier: a harness cannot call what it was never
    given."""
    from rite_ai.local import harness

    methods = [m for m in dir(harness.Committer) if not m.startswith("_")]
    assert methods == ["commit_to_branch"]


@pytest.mark.parametrize("forbidden", ["push", "gh pr", "claude"])
def test_the_harness_never_names_a_push_or_the_claude_cli(forbidden):
    """RL-12: the harness has no Anthropic dimension at all, and the sanctioned
    path keeps spawning the real `claude` CLI elsewhere."""
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "src/rite_ai/local/harness.py"
    code = [
        line
        for line in source.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    body = "\n".join(code)
    # Docstrings legitimately discuss pushing; executable lines must not.
    in_docstring = False
    for line in code:
        if line.count('"""') % 2:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        assert forbidden not in line, f"{forbidden!r} in an executable line: {line}"
    assert body  # the file was actually read
