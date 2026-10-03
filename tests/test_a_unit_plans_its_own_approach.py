"""Level 2 — the approach, at the front of an executing unit's turn (OL6, DD-2.4).

`DECOMPOSER_DESIGN.md` §2.4 listed this as Level 2's one unbuilt piece, deferred
because *"its Claude side waits on the `--agent claude` literal … the local side
lands with the proof runs"*. The literal went in OL5 and the proof runs are in
`spikes/OL1-ollama-inside-a-worker-sandbox.md`.

⚠ Two properties keep Level 2 OUTSIDE the gates, and the design says both are
checks rather than conventions: the subtask executed is byte-identical to the
approved one, and nothing reaches the `Decomposition`.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from rite_ai.local.approach import (
    MAX_STEPS_CHARS,
    Approach,
    boundary_problem,
    plan_approach,
)
from rite_ai.local.decomposition import Subtask

SUB = Subtask(
    id="s1",
    intent="add the CSV export",
    scope=("app/export.py",),
    verify="pytest -q tests/test_export.py",
    cites=("5.2",),
)


class _Proposal:
    def __init__(self, text="", problem="", fault=False):
        self.bytes = text.encode("utf-8")
        self.problem = problem
        self.infrastructure_fault = fault


def _propose(text="1. read the spec\n2. write the export", **kw):
    return lambda prompt, workspace: _Proposal(text, **kw)


# ── the boundary ─────────────────────────────────────────────────────────────


def test_an_unchanged_subtask_passes():
    assert boundary_problem(SUB, SUB) == ""


def test_a_changed_scope_is_refused():
    # `scope` is the committer's allowlist. A Level 2 that could widen it would
    # be handing itself paths plan review never approved.
    assert "scope" in boundary_problem(SUB, replace(SUB, scope=("app/", "etc/")))


def test_a_changed_verify_is_refused():
    # `verify` is the only thing RL-7 trusts; a Level 2 that could soften it
    # turns every gate above it into decoration.
    assert "verify" in boundary_problem(SUB, replace(SUB, verify="true"))


def test_changed_cites_are_refused():
    assert "cites" in boundary_problem(SUB, replace(SUB, cites=("9.9",)))


def test_a_changed_intent_is_refused():
    assert "intent" in boundary_problem(SUB, replace(SUB, intent="something else"))


def test_the_harnesss_own_bookkeeping_is_not_a_boundary_breach():
    # status/attempts/branch/last_failure move legitimately during a turn.
    moved = replace(SUB, status="running", attempts=1, branch="b", last_failure="x")
    assert boundary_problem(SUB, moved) == ""


# ── the step itself ──────────────────────────────────────────────────────────


def test_the_unit_gets_its_own_steps():
    got = plan_approach(
        SUB, "the slice", model="qwen3:8b", endpoint="http://e", propose=_propose()
    )
    assert got.ok
    assert "write the export" in got.steps


def test_the_prompt_carries_the_subtask_and_the_slice_and_fixes_them():
    seen = {}

    def propose(prompt, workspace):
        seen["prompt"] = prompt
        return _Proposal("1. go")

    plan_approach(
        SUB,
        "SPEC 5.2 says export as CSV",
        model="m",
        endpoint="http://e",
        propose=propose,
    )
    prompt = seen["prompt"]
    assert "add the CSV export" in prompt
    assert "app/export.py" in prompt
    assert "SPEC 5.2 says export as CSV" in prompt
    # And it is told the subtask is not its to change.
    assert "fixed" in prompt or "approved by someone else" in prompt


def test_a_failed_approach_is_not_a_failed_subtask():
    # RL-47's distinction: an endpoint that was down produced no approach, not a
    # bad one. `ok` is False and there is a reason; nothing here raises.
    got = plan_approach(
        SUB,
        "s",
        model="m",
        endpoint="http://e",
        propose=_propose(problem="down", fault=True),
    )
    assert not got.ok
    assert got.infrastructure_fault
    assert "down" in got.problem


def test_an_empty_answer_is_a_problem_and_not_a_fault():
    # The model answered and said nothing useful, which is not infrastructure.
    got = plan_approach(
        SUB, "s", model="m", endpoint="http://e", propose=_propose(text="  \n ")
    )
    assert not got.ok
    assert not got.infrastructure_fault


def test_a_launch_that_raises_is_a_result_not_an_exception():
    def propose(prompt, workspace):
        raise OSError("no goose")

    got = plan_approach(SUB, "s", model="m", endpoint="http://e", propose=propose)
    assert not got.ok
    assert "no goose" in got.problem


def test_no_model_means_no_approach_rather_than_a_guess():
    assert not plan_approach(SUB, "s", model="", endpoint="http://e").ok
    assert not plan_approach(SUB, "s", model="m", endpoint="").ok


def test_a_very_long_answer_is_cut():
    # It is PREPENDED to the execution turn's instruction, so an essay would
    # spend the window the work needs.
    got = plan_approach(
        SUB, "s", model="m", endpoint="http://e", propose=_propose(text="x" * 99_000)
    )
    assert len(got.steps) == MAX_STEPS_CHARS


# ── it reaches the turn, and stops there ─────────────────────────────────────


def test_the_instruction_carries_the_approach():
    from rite_ai.local.goose_agent import _instruction
    from rite_ai.local.harness import Context

    text = _instruction(
        Context(
            subtask=SUB,
            spec_slice="slice",
            ticket="T-1",
            approach="1. first\n2. then",
        )
    )
    assert "1. first" in text
    # Placed after the approved facts, so it cannot read as part of them.
    assert text.index("Change only these paths") < text.index("1. first")


def test_an_empty_approach_leaves_the_instruction_as_it_was():
    from rite_ai.local.goose_agent import _instruction
    from rite_ai.local.harness import Context

    plain = _instruction(Context(subtask=SUB, spec_slice="slice", ticket="T-1"))
    assert "your own steps" not in plain.lower()


def test_nothing_about_the_approach_is_written_into_the_plan():
    """⚠ The property that keeps Level 2 outside the gates.

    "If it is written into `Decomposition`, the gates start reading the
    executor's own words." So the module is given no writer.

    ⚠ Checked on the module's CODE, not its text. A first version of this
    grepped the source for "Decomposition" and failed on the docstring that
    explains the rule — the same mistake `test_blast_radius` documents when it
    says it enumerates argv "because docstrings legitimately discuss pushing".
    """
    import ast
    import inspect

    from rite_ai.local import approach as mod

    tree = ast.parse(inspect.getsource(mod))

    # The SYMBOLS brought in, not the module paths they came from: this module
    # legitimately imports `Subtask` from `...local.decomposition`, so matching
    # the path would forbid reading the type it is handed.
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(a.name for a in node.names)
    # Nothing that could persist: no plan type to build, no state layer to write.
    for forbidden in ("Decomposition", "StateLayer", "write", "read"):
        assert forbidden not in imported, (forbidden, sorted(imported))
    # `Subtask` IS imported, and only as the type it is handed.
    assert "Subtask" in imported

    # And no function here accepts a state layer or a plan to write into.
    for _name, fn in inspect.getmembers(mod, inspect.isfunction):
        params = set(inspect.signature(fn).parameters)
        assert not ({"state", "plan", "writer"} & params), (_name, params)


def test_the_approach_is_not_a_field_of_the_subtask_or_the_plan():
    from dataclasses import fields

    from rite_ai.local.decomposition import Decomposition

    assert "approach" not in {f.name for f in fields(Subtask)}
    assert "approach" not in {f.name for f in fields(Decomposition)}


# ── GooseProposer parity (DD-4.5) ────────────────────────────────────────────


def test_the_proposer_can_pin_its_window():
    """Its docstring used to admit it "does not pin the window it launches
    against" — and at Ollama's unconfigured 4,096 default an agent's own system
    prompt does not fit (RL-T0 section 0)."""
    from rite_ai.local.decompose import GooseProposer

    seen = {}

    def launch(argv, workspace, environment):
        seen.update(environment)
        return MagicMock(returncode=0, stdout="1. go", stderr="")

    GooseProposer(
        model="m",
        endpoint="http://e",
        launch=launch,
        context_limit=32768,
        path_root="/sbx/rite/goose",
    ).propose("prompt", "/tmp")
    assert seen["GOOSE_CONTEXT_LIMIT"] == "32768"
    assert seen["GOOSE_PATH_ROOT"] == "/sbx/rite/goose"


def test_the_proposer_writes_its_prompt_where_it_is_told(tmp_path):
    from pathlib import Path

    from rite_ai.local.decompose import GooseProposer

    seen = {}

    def launch(argv, workspace, environment):
        seen["dir"] = Path(argv[argv.index("-i") + 1]).parent
        return MagicMock(returncode=0, stdout="1. go", stderr="")

    GooseProposer(
        model="m", endpoint="http://e", launch=launch, instruction_dir=str(tmp_path)
    ).propose("prompt", "/tmp")
    assert seen["dir"] == tmp_path


def test_the_proposer_defaults_to_no_window_and_no_root():
    # A caller that passes nothing keeps the old behaviour: a lever, not a
    # change of default.
    from rite_ai.local.decompose import GooseProposer

    seen = {}

    def launch(argv, workspace, environment):
        seen.update(environment)
        return MagicMock(returncode=0, stdout="1. go", stderr="")

    GooseProposer(model="m", endpoint="http://e", launch=launch).propose("p", "/tmp")
    assert "GOOSE_CONTEXT_LIMIT" not in seen
    assert "GOOSE_PATH_ROOT" not in seen


def test_an_approach_object_with_no_steps_is_not_ok():
    assert not Approach().ok
    assert not Approach(steps="   ").ok
