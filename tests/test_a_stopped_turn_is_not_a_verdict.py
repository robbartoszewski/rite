"""SCRUM-103 — a turn cut off by a clock is not a judgement on the work.

🔴 **Measured, v0.7.0 gate run `smoke_mixed-20261009T063320Z`.** Both subtasks
of ticket 1 were stopped at 1200s with nothing committed, each was recorded
FAILED, and `step.next_subtask` makes FAILED terminal by design — "so a failure
stops this ticket until a person or a step review looks at it". The ticket
could not complete, and nothing had judged the work.

⚠ **The obvious fix was already considered and rejected, for a good reason.**
`goose_agent`'s own note: marking a timeout an `infrastructure_fault` "would
make a model that stalls for twenty minutes cost no attempt, and a subtask that
hangs every time would be retried for ever". That objection stands. So a stop
is a THIRD category, retried a bounded number of times (`MAX_STOPS`) and failed
at the bound.

⚠ **No real clock anywhere here.** The agent is injected and reports
`stopped=True`, which is exactly what `GooseAgent` sets on `TimeoutExpired` —
so CI guards this in milliseconds. The one test that touches the real timeout
path drives `subprocess.TimeoutExpired` through a fake launcher, never a wait.
"""

from __future__ import annotations

import subprocess

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import level2, plan_state
from rite_ai.local import stage as st
from rite_ai.local import step as stepmod
from rite_ai.local.harness import (
    AgentReport,
    Commit,
    Context,
    VerifyResult,
)
from rite_ai.local.worker_step import Placement


class _State:
    def __init__(self):
        self.values: dict = {}
        self.n = 0

    def read_state(self, key):
        from rite_ai.coordination.state_layer import Absent, Present

        if key not in self.values:
            return Absent()
        value, version = self.values[key]
        return Present(value=value, version=version)

    def write_state(self, key, value, expected_version):
        from rite_ai.coordination.state_layer import Written

        self.n += 1
        self.values[key] = (value, f"v{self.n}")
        return Written(version=f"v{self.n}")


class _StoppedAgent:
    """What `GooseAgent` returns on `TimeoutExpired` — ran, did not finish."""

    def run(self, context: Context, workspace: str) -> AgentReport:
        return AgentReport(
            claimed_success=False,
            stopped=True,
            summary=(
                "goose did not finish within 2700s and was stopped; whatever it "
                "had already changed is still there"
            ),
        )


class _FailingVerify:
    """A stopped turn's verify fails — there is nothing committed to pass it."""

    def run(self, command, workspace):
        return VerifyResult(
            passed=False,
            output=(
                "Using CPython 3.14.3\nCreating virtual environment at: .venv\n"
                "   Building tally @ file:///x\n      Built tally\nInstalled\n"
                + "noise\n" * 200
                + "AssertionError: the real reason is at the end\n"
            ),
        )


class _Claims:
    def take(self, paths, worker):
        return True

    def release(self, paths, worker):
        pass


class _Committer:
    def commit_to_branch(self, workspace, branch, message) -> Commit:
        return Commit(error="nothing to commit: the scope is unchanged")


def _plan(stops: int = 0, status: str = dec.PLANNED) -> dec.Decomposition:
    return dec.Decomposition(
        ticket="T-1",
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="do it",
                scope=("a.py",),
                verify="uv run pytest -q",
                cites=("u1",),
                status=status,
                stops=stops,
            ),
        ),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )


# --- the signal itself ---------------------------------------------------------


def test_the_agent_reports_a_timeout_as_STOPPED_not_a_fault(tmp_path):
    """Drives the real `GooseAgent` timeout branch via a fake launcher — no wait."""
    from rite_ai.local.goose_agent import GooseAgent

    agent = GooseAgent(model="m", endpoint="http://x", timeout=7)

    def _boom(argv, workspace, environment):
        raise subprocess.TimeoutExpired(cmd="goose", timeout=7)

    # The endpoint probe runs first and would report an infrastructure fault
    # before the turn, so the launcher is never reached. Both are stubbed: this
    # test is about the TIMEOUT branch, with no clock and no endpoint.
    object.__setattr__(agent, "_probe", lambda: type("P", (), {"problems": []})())
    object.__setattr__(agent, "_launch", _boom)
    report = agent.run(
        Context(subtask=_plan().subtasks[0], spec_slice="## u1", ticket="T-1"),
        str(tmp_path),
    )
    assert report.stopped is True, "a stop must be distinguishable"
    assert report.infrastructure_fault is False, (
        "NOT a fault: it ran — goose_agent's own objection, preserved"
    )
    assert "did not finish within 7s" in report.summary


def test_the_configured_timeout_is_used_not_the_constant(tmp_path):
    from rite_ai.local.goose_agent import RUN_TIMEOUT_SECONDS, GooseAgent

    assert RUN_TIMEOUT_SECONDS == 45 * 60, "the measured limit, not the old 20 min"
    seen = {}

    agent = GooseAgent(model="m", endpoint="http://x", timeout=123)

    def _capture(argv, workspace, environment):
        raise subprocess.TimeoutExpired(cmd="goose", timeout=agent.timeout)

    object.__setattr__(agent, "_probe", lambda: type("P", (), {"problems": []})())
    object.__setattr__(agent, "_launch", _capture)
    report = agent.run(
        Context(subtask=_plan().subtasks[0], spec_slice="## u1", ticket="T-1"),
        str(tmp_path),
    )
    assert "123s" in report.summary, seen


# --- what a stop does to the subtask ------------------------------------------


def _run_step(tmp_path, plan):
    state = plan_state.layer(tmp_path)
    dec.write(state, plan, dec.read(state, "T-1").version)
    # The step refuses without the Level-2 approval digests — it cannot tell the
    # subtask it is about to run is the one that was approved.
    level2.record_approval(state, "T-1", "lead", plan.subtasks)
    got = state.read_state(st.key_for("T-1"))
    state.write_state(
        st.key_for("T-1"),
        st.render(
            st.Record(
                ticket="T-1",
                stage=st.STEPPING,
                log=(st.Transition(frm="", to=st.STEPPING, at=1.0, why="x"),),
            )
        ),
        got.version,
    )
    return stepmod.take_one_step(
        tmp_path,
        "planner",
        "T-1",
        agent=_StoppedAgent(),
        state=state,
        verifier=_FailingVerify(),
        committer=_Committer(),
        claims=_Claims(),
        placement=Placement(),
        approach_for=lambda *a, **k: ("steps", ""),
    ), state


@pytest.fixture
def project(tmp_path):
    """A project whose cite actually resolves — otherwise the step refuses
    before the agent runs (RL-63) and this file would test nothing."""
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: t\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nspec:\n  paths:\n  - docs/SPEC.md\n"
        "  pin_count: 8\n  slice_depth: 1\n"
    )
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    docs.write_bytes if False else None
    (docs / "SPEC.md").write_text(
        "# T Spec\n\n## u1\n\nThe unit the subtask cites, so the slice resolves.\n"
    )
    return tmp_path


def test_a_first_stop_leaves_the_subtask_PLANNED_not_failed(project):
    """🔴 The defect: FAILED is terminal, so a clock ended the ticket."""
    _, state = _run_step(project, _plan(stops=0))
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert after.status == dec.PLANNED, (
        "a stopped turn is not a verdict; FAILED would retire it (next_subtask)"
    )
    assert after.stops == 1
    # And it is RUNNABLE again, which is the whole point.
    assert stepmod.next_subtask(dec.read(state, "T-1").plan) is not None


def test_at_the_bound_it_DOES_fail(project):
    """CONTROL, and it is goose_agent's objection honoured: a subtask that hangs
    every time must still stop the ticket."""
    _, state = _run_step(project, _plan(stops=stepmod.MAX_STOPS - 1))
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert after.stops == stepmod.MAX_STOPS
    assert after.status == dec.FAILED, "past the bound the evidence is the subtask's"
    assert stepmod.next_subtask(dec.read(state, "T-1").plan) is None


def test_the_stop_is_said_and_counted_in_the_log(project):
    step, _ = _run_step(project, _plan(stops=0))
    joined = " ".join(step.lines)
    assert "stopped on a timeout, not judged" in joined
    assert f"1 of {stepmod.MAX_STOPS} stops used" in joined


# --- defect 3: the reason, not the preamble -----------------------------------


def test_last_failure_keeps_the_agents_own_words_for_a_stop(project):
    """🔴 It kept `verify_output[:500]`, which for the gate run was uv's venv
    setup, with the reason past the cut."""
    _, state = _run_step(project, _plan(stops=0))
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert "did not finish within" in after.last_failure
    assert "Creating virtual environment" not in after.last_failure


def test_last_failure_keeps_the_TAIL_of_output_when_the_turn_ran(project):
    """For a turn that was not stopped, the output's end is the reason."""

    class _Ran(_StoppedAgent):
        def run(self, context, workspace):
            return AgentReport(claimed_success=True, summary="did it")

    state = plan_state.layer(project)
    plan = _plan()
    dec.write(state, plan, dec.read(state, "T-1").version)
    level2.record_approval(state, "T-1", "lead", plan.subtasks)
    got = state.read_state(st.key_for("T-1"))
    state.write_state(
        st.key_for("T-1"),
        st.render(
            st.Record(
                ticket="T-1",
                stage=st.STEPPING,
                log=(st.Transition(frm="", to=st.STEPPING, at=1.0, why="x"),),
            )
        ),
        got.version,
    )
    stepmod.take_one_step(
        project,
        "planner",
        "T-1",
        agent=_Ran(),
        state=state,
        verifier=_FailingVerify(),
        committer=_Committer(),
        claims=_Claims(),
        placement=Placement(),
        approach_for=lambda *a, **k: ("steps", ""),
    )
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert "the real reason is at the end" in after.last_failure
    assert "Creating virtual environment" not in after.last_failure


# --- the record survives a round trip -----------------------------------------


def test_stops_round_trip_and_an_older_plan_reads_as_zero():
    plan = _plan(stops=2)
    again = dec.parse(dec.render(plan))
    assert not isinstance(again, str), again
    assert again.subtasks[0].stops == 2
    # A plan written before this field exists must still parse.
    import json

    body = json.loads(dec.render(plan).decode())
    for s in body["subtasks"]:
        del s["stops"]
    older = dec.parse(json.dumps(body).encode())
    assert not isinstance(older, str), older
    assert older.subtasks[0].stops == 0
