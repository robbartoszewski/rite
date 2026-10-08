"""The decomposition pipeline, wired and running one subtask (RL-T6, 1a).

`decomposition.py`, `harness.py` and `runners.py` were complete and reachable
from nothing — 720 lines, no production caller. `local/step.py` is the join,
and `rite local step` is the entry point.

**Why it is a fix and not a feature.** A `qwen3:8b` Manager has been run twice
against a correctly pinned 32k window and failed the same way both times: the
v0.6.0 dogfood one "filled its 32k window after 45 minutes of detours and
ended without replying", and SB11's "repeated `git status` and `ls` three
times, then summarised in the pane rather than with `rite reply`. The outbox is
empty. That is the model." Everything rite owned was right in the second run.
What failed was scope (a whole ticket), and reporting (the model's job).

So the three things it failed at are taken away from it: ONE subtask with its
spec slice, rite runs the verify, rite records the outcome. These tests are
about that, and the one below is the heart of it — **the agent's claim never
decides anything.**
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import plan_state
from rite_ai.local import step as st
from rite_ai.local.harness import AgentReport, Commit, VerifyResult

TICKET = "KAN-7"
MANAGER = "small"


class _Agent:
    """Stands in for Goose. `claims` is what it REPORTS, never what happened."""

    def __init__(self, claims: bool, touched=("src/a.py",)) -> None:
        self.claims, self.touched, self.calls = claims, touched, []

    def run(self, context, workspace):
        self.calls.append(context)
        return AgentReport(
            claimed_success=self.claims, summary="did the thing", touched=self.touched
        )


class _Verifier:
    def __init__(self, passed: bool) -> None:
        self.passed, self.calls = passed, []

    def run(self, command, workspace):
        self.calls.append(command)
        return VerifyResult(self.passed, output="verify output here")


class _Committer:
    def __init__(self, error: str = "") -> None:
        self.error, self.calls = error, []

    def commit_to_branch(self, workspace, branch, message):
        self.calls.append((branch, message))
        return Commit(error=self.error) if self.error else Commit(sha="c0ffee1234")


class _Claims:
    def __init__(self, granted: bool = True) -> None:
        self.granted, self.taken, self.released = granted, [], []

    def take(self, paths, worker):
        self.taken.append((paths, worker))
        return self.granted

    def release(self, paths, worker):
        self.released.append((paths, worker))


def _subtask(sid="s1", status=dec.PLANNED, cites=("5.3",)):
    return dec.Subtask(
        id=sid,
        intent="make the thing do the thing",
        scope=("src/a.py",),
        verify="true",
        cites=cites,
        status=status,
    )


@pytest.fixture
def project(tmp_path):
    """A project with a spec digest for 5.3, so a slice can be built."""
    from rite_ai.spec.digest_files import unit_filename, units_dir

    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    # A real project root: `_require_project_root` looks for these, so without
    # one the CLI tests would measure the marker check and not the wiring.
    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n")
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    (where / unit_filename("5.3")).write_text("## 5.3 the unit the subtask cites\n")
    return root


def _seed(root: Path, plan: dec.Decomposition):
    state = plan_state.layer(root)
    read = dec.read(state, plan.ticket)
    written = dec.write(state, plan, read.version)
    assert type(written).__name__ == "Written", written
    if plan.approval == dec.APPROVED:
        _clear_level2(state, plan)
    return state


def _clear_level2(state, plan, steps="1. do the thing") -> None:
    """Record what approval recorded, and persist an approach per subtask —
    what the real pipeline leaves behind by the time a step runs (SCRUM-72e).

    ⚠ **Not a stub of the guard, a set-up of its inputs.** Level 2 is required
    and persisted now, and `cleared_to_run` refuses a step without it; these
    tests are about the EXECUTOR, so they arrive at it the way a driven
    pipeline does. The guard's own refusals are tested where they belong.
    """
    from rite_ai.local import level2

    recorded = level2.record_approval(
        state,
        plan.ticket,
        plan.approved_by or "reviewer",
        plan.subtasks,
        expected=level2.approval_version(state, plan.ticket),
    )
    assert type(recorded).__name__ == "Written", recorded
    for sub in plan.subtasks:
        written = level2.write_approach(state, plan.ticket, sub, steps)
        assert type(written).__name__ == "Written", written


def _approved(subtasks=None) -> dec.Decomposition:
    return dec.Decomposition(
        ticket=TICKET,
        subtasks=tuple(subtasks or [_subtask()]),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="reviewer",
    )


def _run(root, state, *, claim, verify, commit_error="", claims=None):
    agent = _Agent(claim)
    verifier = _Verifier(verify)
    committer = _Committer(commit_error)
    step = st.take_one_step(
        root,
        MANAGER,
        TICKET,
        agent=agent,
        state=state,
        verifier=verifier,
        committer=committer,
        claims=claims or _Claims(),
    )
    return step, agent, verifier, committer


class TestTheAgentsClaimNeverDecides:
    """⚠ **The invariant, and the reason this pipeline exists.**

    Across every combination of what the agent CLAIMS, what the verify FINDS
    and whether the commit lands, the outcome is derived from the verify and
    the commit alone. The agent's claim changes exactly one thing — whether a
    disagreement is recorded — and never the status.

    The middles are the point. `claim=True, verify=False` is the dogfood
    failure shape: a model that says it finished when it did not. `claim=False,
    verify=True` is the opposite and must still be accepted, because the work
    is what counts and a pessimistic model is not a failed subtask.
    """

    @pytest.mark.parametrize(
        ("claim", "verify", "commit_error"),
        list(
            itertools.product([True, False], [True, False], ["", "nothing to commit"])
        ),
    )
    def test_status_follows_the_verify_and_the_commit_only(
        self, project, claim, verify, commit_error
    ):
        state = _seed(project, _approved())

        step, _agent, verifier, _c = _run(
            project, state, claim=claim, verify=verify, commit_error=commit_error
        )

        assert step.ran, step.problem
        if not verify:
            expected = dec.FAILED
        elif commit_error:
            expected = dec.VERIFIED
        else:
            expected = dec.ACCEPTED
        assert step.status == expected, (
            f"claim={claim} verify={verify} commit_error={commit_error!r} "
            f"gave {step.status}"
        )
        assert step.accepted is (expected == dec.ACCEPTED)
        # The verify ran whatever the agent said: a verify skipped because the
        # agent claimed failure is a verify the agent controls.
        assert verifier.calls == ["true"]
        assert step.claim_disagreed is (claim != verify)

    @pytest.mark.parametrize("claim", [True, False])
    def test_the_disagreement_is_kept_as_evidence(self, project, claim):
        """A claim that disagrees with the verify is recorded rather than
        discarded — RL-T0 measures it and a step review needs to see it."""
        state = _seed(project, _approved())

        step, _a, _v, _c = _run(project, state, claim=claim, verify=not claim)

        assert step.claim_disagreed
        if claim:
            assert any("disagreed" in line for line in step.lines), step.lines


class TestTheAgentGetsOneSubtaskAndItsSlice:
    def test_exactly_one_subtask_is_handed_over(self, project):
        """⚠ `harness.Context`: "if this ever carries more than one subtask,
        the tier has stopped being what it was measured as"."""
        state = _seed(project, _approved([_subtask("s1"), _subtask("s2")]))

        _step, agent, _v, _c = _run(project, state, claim=True, verify=True)

        assert len(agent.calls) == 1
        assert agent.calls[0].subtask.id == "s1"

    def test_the_slice_is_the_text_and_not_a_pointer(self, project):
        """A pointer assumes a window big enough to go and read the file, and
        a small model's is not."""
        state = _seed(project, _approved())

        _step, agent, _v, _c = _run(project, state, claim=True, verify=True)

        assert "the unit the subtask cites" in agent.calls[0].spec_slice

    def test_a_missing_slice_refuses_rather_than_sending_nothing(self, project):
        """⚠ An empty slice would quietly reproduce the free-form run this
        replaces, so an unresolvable cite stops the step."""
        state = _seed(project, _approved([_subtask(cites=("9.9",))]))
        agent = _Agent(True)

        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            agent=agent,
            state=state,
            verifier=_Verifier(True),
            committer=_Committer(),
            claims=_Claims(),
        )

        assert not step.ran and "rite spec index" in step.problem
        assert agent.calls == [], "the agent ran without its slice"

    def test_a_subtask_citing_nothing_is_refused(self, project):
        state = _seed(project, _approved([_subtask(cites=())]))
        agent = _Agent(True)

        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            agent=agent,
            state=state,
            verifier=_Verifier(True),
            committer=_Committer(),
            claims=_Claims(),
        )

        assert not step.ran and "cites no spec unit" in step.problem
        assert agent.calls == []


class TestNothingRunsFromAnUnapprovedPlan:
    @pytest.mark.parametrize("approval", [dec.PENDING, dec.REJECTED])
    def test_the_gate_holds(self, project, approval):
        plan = replace(_approved(), approval=approval, approved_by="")
        state = _seed(project, plan)
        agent = _Agent(True)

        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            agent=agent,
            state=state,
            verifier=_Verifier(True),
            committer=_Committer(),
            claims=_Claims(),
        )

        assert not step.ran
        assert approval in step.problem and agent.calls == []

    def test_no_decomposition_says_the_decomposer_does_not_exist(self, project):
        """Honest about the hole rather than silent: rite cannot produce a
        plan yet, so a project with none is told that."""
        state = plan_state.layer(project)

        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            agent=_Agent(True),
            state=state,
            verifier=_Verifier(True),
            committer=_Committer(),
            claims=_Claims(),
        )

        assert not step.ran and "no decomposer" in step.problem


class TestItRecordsTheOutcomeItself:
    """The half the model failed at in both real runs: rite writes what rite
    observed, so nothing depends on the agent reporting."""

    def test_an_accepted_subtask_is_recorded_accepted(self, project):
        state = _seed(project, _approved())

        _step, _a, _v, _c = _run(project, state, claim=False, verify=True)

        plan = dec.read(state, TICKET).plan
        assert plan.subtask("s1").status == dec.ACCEPTED
        assert plan.subtask("s1").attempts == 1

    def test_a_failure_is_recorded_with_its_reason(self, project):
        state = _seed(project, _approved())

        _step, _a, _v, _c = _run(project, state, claim=True, verify=False)

        got = dec.read(state, TICKET).plan.subtask("s1")
        assert got.status == dec.FAILED and got.last_failure

    def test_a_failed_subtask_is_not_retried(self, project):
        """⚠ No retry loop. Retrying a small model is how 45 minutes of
        detours happened; a failure stops the ticket until somebody looks."""
        state = _seed(project, _approved())
        _run(project, state, claim=True, verify=False)

        again = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            agent=_Agent(True),
            state=state,
            verifier=_Verifier(True),
            committer=_Committer(),
            claims=_Claims(),
        )

        assert not again.ran and "no subtask still planned" in again.problem


class TestTheClaimIsTakenAndAlwaysReleased:
    def test_a_held_scope_stops_the_step(self, project):
        state = _seed(project, _approved())
        claims = _Claims(granted=False)

        step, agent, _v, _c = _run(
            project, state, claim=True, verify=True, claims=claims
        )

        assert step.status == dec.FAILED and agent.calls == []
        assert "another worker holds" in " ".join(step.lines)

    @pytest.mark.parametrize(
        ("verify", "commit_error"), [(True, ""), (False, ""), (True, "no")]
    )
    def test_the_claim_is_released_on_every_path(self, project, verify, commit_error):
        """A claim held by a finished subtask blocks the fleet until something
        notices, so release is in a `finally`."""
        state = _seed(project, _approved())
        claims = _Claims()

        _run(
            project,
            state,
            claim=True,
            verify=verify,
            commit_error=commit_error,
            claims=claims,
        )

        assert claims.released == [(("src/a.py",), MANAGER)]


class TestTheCliEntryPointIsWired:
    """⚠ `test_no_dead_wiring`'s lesson: this repository has shipped nine
    public functions that were complete and called by nothing, and this
    pipeline was 720 more lines of it. The join is asserted, not assumed."""

    def test_rite_local_step_runs_the_pipeline(self, project, monkeypatch):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        _seed(project, _approved())
        monkeypatch.chdir(project)
        seen = {}

        def fake(root, manager, ticket, **kw):
            seen.update(root=root, manager=manager, ticket=ticket)
            return st.Step(ran=True, ticket=ticket, subtask="s1", status=dec.ACCEPTED)

        monkeypatch.setattr("rite_ai.local.step.take_one_step", fake)
        result = CliRunner().invoke(cli, ["local", "step", MANAGER, TICKET])

        assert result.exit_code == 0, result.output
        assert seen["manager"] == MANAGER and seen["ticket"] == TICKET
        assert "accepted" in result.output

    def test_it_exits_nonzero_and_says_why_when_nothing_ran(self, project, monkeypatch):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        monkeypatch.chdir(project)
        result = CliRunner().invoke(cli, ["local", "step", MANAGER, TICKET])

        assert result.exit_code == 1
        assert "nothing ran" in result.output and "no decomposer" in result.output
