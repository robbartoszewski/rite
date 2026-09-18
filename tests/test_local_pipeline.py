"""The decomposition artifact and the duty router (RL-T4, RL-T5).

Two properties carry most of the weight, and both are about a gate that must
not be bypassed:

- a decomposition is not released until a plan-review holder approves it, and a
  return drops the approval rather than annotating it;
- a review is never routed to whoever produced the thing being reviewed, and
  when the author is the only holder that is a configuration fault rather than
  a queue.
"""

from __future__ import annotations

import pytest

from rite_ai.config.managers import ManagerRole
from rite_ai.coordination.state_layer import ABSENT, Conflict, Written
from rite_ai.local.decomposition import (
    ACCEPTED,
    APPROVED,
    PENDING,
    Decomposition,
    Subtask,
    parse,
    problems,
    read,
    render,
    returned_to_plan_review,
    with_subtask,
    write,
)
from rite_ai.local.duty_router import (
    Candidate,
    Queued,
    Routed,
    Task,
    Unroutable,
    route,
    targets_managers,
)


class FakeState:
    """A state layer that remembers bytes and versions. Not a backend — the
    point is that nothing above it knows which backend it is (RL-35)."""

    def __init__(self):
        self.values: dict[str, tuple[bytes, str]] = {}
        self.writes = 0

    def read_state(self, key: str):
        from rite_ai.coordination.state_layer import Absent, Present

        if key not in self.values:
            return Absent()
        value, version = self.values[key]
        return Present(value=value, version=version)

    def write_state(self, key: str, value: bytes, expected_version: str):
        current = self.values.get(key, (b"", ABSENT))[1]
        if current != expected_version:
            return Conflict(current=current)
        self.writes += 1
        version = f"v{self.writes}"
        self.values[key] = (value, version)
        return Written(version=version)

    def append_message(self, content: str):  # pragma: no cover - unused here
        raise NotImplementedError

    def read_messages(self, since=None, limit=None):  # pragma: no cover
        raise NotImplementedError


def _plan(**kw) -> Decomposition:
    base = dict(
        ticket="ABC-1",
        subtasks=(
            Subtask(
                id="s1",
                intent="add the parser",
                scope=("parser.py",),
                verify="pytest tests/test_parser.py",
            ),
            Subtask(
                id="s2",
                intent="wire it in",
                scope=("cli.py",),
                verify="pytest tests/test_cli.py",
            ),
        ),
        decomposed_by="planner",
    )
    base.update(kw)
    return Decomposition(**base)


# --- the artifact is durable, and says what a gate needs ---------------------------


def test_a_decomposition_round_trips_through_the_state_layer():
    """SPEC §2.0: a session's memory is not a record. Three gates read this."""
    state = FakeState()
    plan = _plan()
    result = write(state, plan, ABSENT)
    assert isinstance(result, Written)
    back = read(state, "ABC-1")
    assert back.plan == plan
    assert back.version == result.version


def test_two_writers_do_not_silently_overwrite_each_other():
    """A step review accepting one subtask while an executor reports another is
    the normal case, not a rare one."""
    state = FakeState()
    first = write(state, _plan(), ABSENT)
    assert isinstance(first, Written)
    stale = write(state, _plan(decomposed_by="someone-else"), ABSENT)
    assert isinstance(stale, Conflict)
    assert stale.current == first.version


def test_nothing_is_released_until_a_plan_review_holder_approves():
    plan = _plan()
    assert plan.approval == PENDING
    assert not plan.released
    approved = Decomposition(
        **{**plan.__dict__, "approval": APPROVED, "approved_by": "lead"}
    )
    assert approved.released


def test_a_return_drops_the_approval_rather_than_annotating_it():
    """RL-8: a plan that failed its parent's verify is not an approved plan
    with a note attached. Anything else lets subtasks keep running from a plan
    the gate has just rejected."""
    approved = Decomposition(
        **{**_plan().__dict__, "approval": APPROVED, "approved_by": "lead"}
    )
    returned = returned_to_plan_review(approved, "recomposition verify failed")
    assert not returned.released
    assert returned.approved_by == ""
    assert returned.returns == ("recomposition verify failed",)


def test_returns_accumulate_because_the_budget_counts_them():
    """RL-10 counts returns per parent ticket from any cause, and a count with
    no reasons is a number nobody can act on."""
    plan = _plan()
    plan = returned_to_plan_review(plan, "conflict in parser.py")
    plan = returned_to_plan_review(plan, "parent verify failed")
    assert len(plan.returns) == 2
    assert "conflict in parser.py" in plan.returns


@pytest.mark.parametrize(
    ("bad", "says"),
    [
        (b"not json at all", "not readable"),
        (b'{"format_version": 99, "ticket": "x"}', "format"),
        (
            b'{"format_version": 1, "subtasks": [{"id": "s", "status": "vibes"}]}',
            "status",
        ),
        (
            b'{"format_version": 1, "subtasks": [{"id": "s", "attempts": -1}]}',
            "attempts",
        ),
        (b'{"format_version": 1, "subtasks": [{"intent": "no id"}]}', "no id"),
    ],
)
def test_an_unreadable_decomposition_is_refused_rather_than_repaired(bad, says):
    """Every gate downstream reads this. A half-understood plan is a plan whose
    gates check something else."""
    result = parse(bad)
    assert isinstance(result, str)
    assert says in result


def test_the_bytes_are_deterministic():
    """A re-write that decided nothing new should be visible as a no-op."""
    assert render(_plan()) == render(_plan())


def test_updating_one_subtask_leaves_the_others_alone():
    plan = _plan()
    done = Subtask(
        id="s1",
        intent="add the parser",
        scope=("parser.py",),
        verify="pytest tests/test_parser.py",
        status=ACCEPTED,
        branch="task/s1",
    )
    updated = with_subtask(plan, done)
    assert updated.subtask("s1").status == ACCEPTED
    assert updated.subtask("s2") == plan.subtask("s2")


# --- what plan review is handed mechanically --------------------------------------


def test_a_verify_that_does_not_exist_is_caught_before_anything_runs():
    plan = _plan(subtasks=(Subtask(id="s1", intent="do it", scope=("a.py",)),))
    assert any("no verify" in p for p in problems(plan))


def test_two_subtasks_claiming_one_path_are_caught_before_the_merge():
    """The design's composition gate (RL-T30): two subtasks that conflict were two subtasks that should
    not have been separate. Cheaper here than at composition."""
    plan = _plan(
        subtasks=(
            Subtask(id="s1", intent="a", scope=("shared.py",), verify="t"),
            Subtask(id="s2", intent="b", scope=("shared.py",), verify="t"),
        )
    )
    found = problems(plan)
    assert any("both claim shared.py" in p for p in found)


def test_a_sound_plan_has_nothing_to_report():
    assert problems(_plan()) == []


# --- the router ------------------------------------------------------------------


LEAD = ManagerRole(name="lead", preset="lead")
PLANNER = ManagerRole(
    name="planner",
    engine="local:large",
    preset="planner",
    endpoint="http://localhost:11434/v1",
    model="qwen3:70b",
    agent="opencode",
)
EXECUTOR = ManagerRole(
    name="executor",
    engine="local:small",
    preset="executor",
    endpoint="http://localhost:11434/v1",
    model="qwen3:8b",
    agent="opencode",
)


def test_a_task_goes_to_a_manager_that_holds_the_duty_for_its_stage():
    routed = route(
        Task(id="t1", stage="decompose"),
        [Candidate(LEAD), Candidate(PLANNER), Candidate(EXECUTOR)],
    )
    assert isinstance(routed, Routed) and routed.manager == "planner"


def test_a_duty_nobody_holds_is_unroutable_rather_than_queued():
    """A task waiting for a duty nobody holds waits for ever, and a queue
    cannot tell you that."""
    result = route(
        Task(id="t1", stage="decompose"), [Candidate(LEAD), Candidate(EXECUTOR)]
    )
    assert isinstance(result, Unroutable)
    assert "no manager holds decompose" in result.reason


def test_plan_review_never_goes_to_the_engine_that_wrote_the_plan():
    """RL-6: a reviewer sharing the decomposer's engine shares the blind spots
    of the plan it is checking, and a wrong slicing passes every check it
    wrote."""
    second_planner = ManagerRole(
        name="planner2",
        engine="local:large",
        duties=("plan-review",),
        endpoint="http://localhost:11434/v1",
        model="qwen3:70b",
        agent="opencode",
    )
    result = route(
        Task(id="t1", stage="plan-review", produced_by="planner"),
        [Candidate(PLANNER), Candidate(second_planner)],
    )
    assert isinstance(result, Unroutable)
    assert "not a review" in result.reason

    with_lead = route(
        Task(id="t1", stage="plan-review", produced_by="planner"),
        [Candidate(PLANNER), Candidate(second_planner), Candidate(LEAD)],
    )
    assert isinstance(with_lead, Routed) and with_lead.manager == "lead"


def test_a_review_is_never_routed_to_its_author():
    """SPEC §7.1, applied across tiers (RL-18)."""
    other = ManagerRole(
        name="planner2",
        engine="local:large",
        duties=("step-review",),
        endpoint="http://localhost:11434/v1",
        model="qwen3:70b",
        agent="opencode",
    )
    result = route(
        Task(id="t1", stage="step-review", produced_by="planner"),
        [Candidate(PLANNER), Candidate(other)],
    )
    assert isinstance(result, Routed) and result.manager == "planner2"


def test_an_idle_eligible_manager_never_coexists_with_a_task_it_could_run():
    """The ticket's own test. A queue that holds work an idle Manager could
    take is a router that has stopped routing."""
    busy = Candidate(EXECUTOR, in_flight=1, capacity=1)
    idle = Candidate(
        ManagerRole(
            name="executor2",
            engine="local:small",
            preset="executor",
            endpoint="http://localhost:11434/v1",
            model="qwen3:8b",
            agent="opencode",
        )
    )
    result = route(Task(id="t1", stage="execute"), [busy, idle])
    assert isinstance(result, Routed) and result.manager == "executor2"


def test_a_task_every_holder_is_too_busy_for_is_queued_with_a_reason():
    result = route(
        Task(id="t1", stage="execute"),
        [Candidate(EXECUTOR, in_flight=2, capacity=2)],
    )
    assert isinstance(result, Queued)
    assert "in flight" in result.reason


def test_a_refusal_is_an_answer_and_the_task_is_not_offered_back():
    """P2-4c. Re-offering a refused task produces a loop that looks like
    progress."""
    result = route(
        Task(id="t1", stage="execute"),
        [Candidate(EXECUTOR, refused=("t1",))],
    )
    assert isinstance(result, Queued)
    assert "refused" in result.reason


def test_the_tiebreak_is_stable_so_two_machines_choose_the_same_manager():
    """A random tiebreak is unreproducible in exactly the situation someone is
    trying to explain afterwards."""
    a = Candidate(EXECUTOR)
    b = Candidate(
        ManagerRole(
            name="another",
            engine="local:small",
            preset="executor",
            endpoint="http://localhost:11434/v1",
            model="qwen3:8b",
            agent="opencode",
        )
    )
    first = route(Task(id="t1", stage="execute"), [a, b])
    second = route(Task(id="t1", stage="execute"), [b, a])
    assert isinstance(first, Routed) and isinstance(second, Routed)
    assert first.manager == second.manager == "another"


def test_an_unknown_stage_is_refused_with_the_stages_that_exist():
    result = route(Task(id="t1", stage="deploy"), [Candidate(LEAD)])
    assert isinstance(result, Unroutable) and "not a stage" in result.reason


# --- and the line that keeps Workers fungible -------------------------------------


def test_a_project_whose_managers_are_alike_does_not_route_between_them():
    """RL-4: while they are all alike there is nothing to route between, and
    SPEC §2.3's Worker labels keep working — today's behaviour, unchanged."""
    assert not targets_managers([ManagerRole(name="a"), ManagerRole(name="b")])
    assert not targets_managers([ManagerRole(name="solo")])


def test_a_project_with_tiers_targets_managers():
    """Once engines or duties differ, a Worker label would bypass routing —
    the routing design's Seam 3."""
    assert targets_managers([LEAD, EXECUTOR])
    assert targets_managers(
        [ManagerRole(name="a", preset="lead"), ManagerRole(name="b", preset="executor")]
    )
