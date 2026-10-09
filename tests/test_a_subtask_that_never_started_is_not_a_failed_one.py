"""A subtask refused before the agent ran is not a failed subtask.

🔴 **Measured, v0.7.0 gate run `smoke_mixed-20261009T113241Z`.** Both gate
tickets touch `src/tally/__init__.py`. The GPU Worker's `s1` — the subtask
that implements the feature — tried to start while `alpha` still held that
file, so `run_subtask` refused it and recorded:

    s1: status='failed' attempts=0 stops=0
    last_failure='another worker holds part of src/tally/__init__.py — not
                  started, so nothing here counts as an attempt'

`attempts=0` and `status=failed` together, in the same record: rite knew
nothing had run and retired the subtask anyway, because `next_subtask` only
returns PLANNED ones. The implementation was never written, the ticket never
reached `recomposed`, and three of the gate's four checks failed. The claim it
waited on was released three minutes later — by the delivery of the very
Worker that held it, since claims are released when the MANAGER delivers.

⚠ **The bug was described in a comment above the line that caused it**, which
is why this file exists: `harness.run_subtask` already says the claim conflict
"still spent one, which is how a subtask blocked by a neighbour's claim could
be retired without ever having been tried". The attempt side was fixed and the
status side was not.

⚠ **Same shape as SCRUM-103, deliberately** — see
`test_a_stopped_turn_is_not_a_verdict`. A third category, retried a bounded
number of times (`MAX_SKIPS`) and failed at the bound, so a scope that is held
for good still stops the ticket rather than spinning for ever.

⚠ **No real claim ledger, no sandbox, no clock.** The claims object is
injected and simply refuses, which is exactly what `claims.take` returning
False means. The agent is injected too, and asserts it is never called — that
is the proof that nothing ran.
"""

from __future__ import annotations

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import level2, plan_state
from rite_ai.local import stage as st
from rite_ai.local import step as stepmod
from rite_ai.local.harness import AgentReport, Commit, VerifyResult
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


class _NeverRuns:
    """The agent must not be reached. Being reached IS the failure."""

    def __init__(self):
        self.calls = 0

    def run(self, context, workspace) -> AgentReport:
        self.calls += 1
        raise AssertionError(
            "the agent ran although a neighbour held the scope — the refusal "
            "is what makes this a skip rather than a judgement"
        )


class _ScopeHeldByANeighbour:
    """`claims.take` returning False: another worker holds part of the scope."""

    def __init__(self):
        self.released: list = []

    def take(self, paths, worker):
        return False

    def release(self, paths, worker):
        self.released.append((tuple(paths), worker))


class _ScopeFree(_ScopeHeldByANeighbour):
    def take(self, paths, worker):
        return True


class _Verify:
    def run(self, command, workspace):
        return VerifyResult(passed=True, output="1 passed")


class _Committer:
    def commit_to_branch(self, workspace, branch, message) -> Commit:
        return Commit(sha="deadbee")


def _plan(skips: int = 0, status: str = dec.PLANNED) -> dec.Decomposition:
    return dec.Decomposition(
        ticket="T-1",
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="implement the thing",
                scope=("src/tally/__init__.py",),
                verify="uv run pytest -q",
                cites=("u1",),
                status=status,
                skips=skips,
            ),
        ),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )


@pytest.fixture
def project(tmp_path):
    """A project whose cite resolves — otherwise the step refuses on RL-63
    before reaching the claim at all, and this file would test nothing."""
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
    (docs / "SPEC.md").write_text(
        "# T Spec\n\n## u1\n\nThe unit the subtask cites, so the slice resolves.\n"
    )
    return tmp_path


def _run_step(root, plan, claims, agent=None):
    state = plan_state.layer(root)
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
    step = stepmod.take_one_step(
        root,
        "planner",
        "T-1",
        agent=agent or _NeverRuns(),
        state=state,
        verifier=_Verify(),
        committer=_Committer(),
        claims=claims,
        placement=Placement(),
        approach_for=lambda *a, **k: ("steps", ""),
    )
    return step, state


# --- the defect itself ---------------------------------------------------------


def test_a_held_scope_leaves_the_subtask_PLANNED_not_failed(project):
    """🔴 The gate run's s1: attempts=0 and status=failed in one record."""
    _, state = _run_step(project, _plan(), _ScopeHeldByANeighbour())
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert after.status == dec.PLANNED, (
        "nothing ran, so there is no verdict to record; FAILED retires it "
        "(next_subtask returns only PLANNED)"
    )
    assert after.skips == 1
    assert after.attempts == 0, "RL-47: a run that never happened is not an attempt"


def test_and_it_is_REACHABLE_again_which_is_the_whole_point(project):
    """The assertion the gate run would have needed: `next_subtask` finds it."""
    _, state = _run_step(project, _plan(), _ScopeHeldByANeighbour())
    nxt = stepmod.next_subtask(dec.read(state, "T-1").plan)
    assert nxt is not None, "a subtask nobody tried must still be runnable"
    assert nxt.id == "s1"


def test_at_the_bound_it_DOES_fail(project):
    """CONTROL: a scope held for good still stops the ticket."""
    _, state = _run_step(
        project, _plan(skips=stepmod.MAX_SKIPS - 1), _ScopeHeldByANeighbour()
    )
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert after.skips == stepmod.MAX_SKIPS
    assert after.status == dec.FAILED, "past the bound it is the subtask's problem"
    assert stepmod.next_subtask(dec.read(state, "T-1").plan) is None


def test_a_free_scope_accumulates_NO_skip(project):
    """CONTROL the other way: the counter moves only when nothing ran."""

    class _Ran:
        def run(self, context, workspace):
            return AgentReport(claimed_success=True, summary="did it")

    _, state = _run_step(project, _plan(), _ScopeFree(), agent=_Ran())
    after = dec.read(state, "T-1").plan.subtasks[0]
    assert after.skips == 0, "a turn that ran is not a skip"
    assert after.attempts == 1, "and it IS an attempt"


def test_the_skip_is_said_and_counted_in_the_log(project):
    step, _ = _run_step(project, _plan(), _ScopeHeldByANeighbour())
    joined = " ".join(step.lines)
    assert "nothing ran, so this is not a verdict" in joined
    assert f"1 of {stepmod.MAX_SKIPS} skips used" in joined


def test_the_outcome_marks_it_never_started(project):
    """The signal exists on the Outcome, not only in prose."""
    from rite_ai.local.harness import Outcome

    assert "never_started" in Outcome.__dataclass_fields__
    step, _ = _run_step(project, _plan(), _ScopeHeldByANeighbour())
    assert any("another worker holds part of" in line for line in step.lines)


# --- the record survives a round trip -----------------------------------------


def test_skips_round_trip_and_an_older_plan_reads_as_zero():
    plan = _plan(skips=2)
    again = dec.parse(dec.render(plan))
    assert not isinstance(again, str), again
    assert again.subtasks[0].skips == 2
    import json

    body = json.loads(dec.render(plan).decode())
    for s in body["subtasks"]:
        del s["skips"]
    older = dec.parse(json.dumps(body).encode())
    assert not isinstance(older, str), older
    assert older.subtasks[0].skips == 0


def test_a_negative_skips_is_refused_not_repaired():
    import json

    body = json.loads(dec.render(_plan()).decode())
    body["subtasks"][0]["skips"] = -1
    why = dec.parse(json.dumps(body).encode())
    assert isinstance(why, str) and "skips must be a whole number" in why
