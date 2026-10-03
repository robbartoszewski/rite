"""`_independent` compares the MODEL and fails CLOSED (OL7).

This is the routing half of RL-6. `configuration_problems` got the model-compare
rule on 2026-10-02; the router still compared engine STRINGS and still returned
True for an author it could not find. Both were reachable, and both matter more
after OL8: an all-Ollama fleet approves its own plans, so independence is the
whole of what makes a local verdict worth acting on.

⚠ The function had no direct test before this file. It was built unwired —
`route()` has no caller in `src` — which is why a loophole and a fail-open could
sit in it unnoticed. The automation that wires it is the reason to fix it first.
"""

from __future__ import annotations

from rite_ai.config.managers import ManagerRole
from rite_ai.local.duty_router import Candidate, Task, _independent

GPU = {"endpoint": "http://localhost:11434", "agent": "goose", "context_window": 32768}


def _role(name: str, engine: str, model: str = "") -> ManagerRole:
    extra = GPU if engine.startswith("local:") else {}
    return ManagerRole(name=name, engine=engine, model=model, **extra)


def _plan_review(author: str) -> Task:
    return Task(id="t", stage="plan-review", produced_by=author)


# ── the loophole: two labels, one model ──────────────────────────────────────


def test_a_different_label_on_the_same_model_is_not_independent():
    author = _role("planner", "local:small", "qwen3.8:latest")
    reviewer = Candidate(role=_role("lead", "local:large", "qwen3.8:latest"))
    assert not _independent(reviewer, _plan_review("planner"), [author, reviewer.role])


def test_a_different_model_is_independent():
    author = _role("planner", "local:small", "qwen3:8b")
    reviewer = Candidate(role=_role("lead", "local:large", "qwen3:32b"))
    assert _independent(reviewer, _plan_review("planner"), [author, reviewer.role])


def test_rites_window_pin_cannot_pose_as_a_second_model():
    # `rite-ctx32768-qwen3.8-latest` IS `qwen3.8:latest` with a bigger window.
    author = _role("planner", "local:small", "qwen3.8:latest")
    reviewer = Candidate(
        role=_role("lead", "local:large", "rite-ctx32768-qwen3.8-latest")
    )
    assert not _independent(reviewer, _plan_review("planner"), [author, reviewer.role])


def test_a_claude_reviewer_is_independent_of_a_local_author():
    author = _role("planner", "local:small", "qwen3:8b")
    reviewer = Candidate(role=_role("lead", "claude"))
    assert _independent(reviewer, _plan_review("planner"), [author, reviewer.role])


def test_two_claude_managers_are_not_independent():
    # Unchanged: claude identity ignores the model, as it did before.
    author = _role("a", "claude", "opus")
    reviewer = Candidate(role=_role("b", "claude", "sonnet"))
    assert not _independent(reviewer, _plan_review("a"), [author, reviewer.role])


# ── the fail-open ────────────────────────────────────────────────────────────


def test_an_author_that_cannot_be_placed_is_refused():
    """⚠ Was `return True`: a plan whose decomposed_by named nothing was
    reviewable by anyone, including the engine that wrote it. DECOMPOSER_DESIGN
    called this the one thing it "would not ship without" (RL-67)."""
    reviewer = Candidate(role=_role("lead", "local:large", "qwen3:32b"))
    assert not _independent(reviewer, _plan_review("ghost"), [reviewer.role])


def test_an_unnamed_author_is_refused_too():
    """⚠ A TIGHTENING, and the one RL-67 actually asked for.

    The design names this case exactly: "A plan whose `decomposed_by` names
    nothing is reviewable by ANYONE, including the same engine that wrote it.
    That is reachable today with a hand-written plan file." So an unnamed author
    fails closed for the same reason an unplaceable one does — rite cannot check
    an independence claim it cannot place.

    ⚠ Consequence worth knowing: a hand-written plan must now name its author to
    be plan-reviewed through the router. The refusal is actionable (name a
    Manager that holds decompose), and the decomposer path already writes the
    field itself.
    """
    reviewer = Candidate(role=_role("lead", "local:large", "qwen3:32b"))
    assert not _independent(
        reviewer, Task(id="t", stage="plan-review"), [reviewer.role]
    )


def test_an_unnamed_author_is_still_fine_for_other_stages():
    # The tightening is scoped to plan review, which is the gate RL-6 is about.
    reviewer = Candidate(role=_role("lead", "local:large", "qwen3:32b"))
    assert _independent(reviewer, Task(id="t", stage="execute"), [reviewer.role])


# ── unchanged behaviour ──────────────────────────────────────────────────────


def test_the_author_may_never_review_its_own_plan():
    author = _role("planner", "local:small", "qwen3:8b")
    assert not _independent(Candidate(role=author), _plan_review("planner"), [author])


def test_other_stages_need_only_a_different_manager():
    # RL-18: step review wants a different instance, not a different model.
    author = _role("a", "local:small", "qwen3.8:latest")
    reviewer = Candidate(role=_role("b", "local:small", "qwen3.8:latest"))
    task = Task(id="t", stage="step-review", produced_by="a")
    assert _independent(reviewer, task, [author, reviewer.role])
    assert not _independent(Candidate(role=author), task, [author])
