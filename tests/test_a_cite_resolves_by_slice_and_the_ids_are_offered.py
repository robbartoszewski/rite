"""SCRUM-92: a cite resolves by derived text OR a slice, and the ids are named.

Two defects, found one behind the other on a real run.

🔴 **A cite could not resolve on a project whose spec is too small to digest.**
`_cites_problem` asked only whether `.rite/spec/units/<id>` existed, and `rite
spec index` REFUSES to derive units for a small spec — "this spec does not
decompose … reading the whole file costs less". So every cited subtask was
refused, every uncited one was refused too (RL-63 requires a cite), and the
remedy the error named was a command that declines to help. A valid two-subtask
plan was refused on both subtasks for a unit that was in the spec and readable
the whole time.

🔴 **And the decomposer was never told which ids exist.** It was instructed to
cite "a spec unit that exists" and left to infer the ids from the spec's prose.
Measured: a heading `## 978a36de0d0fbb2fba559f87 — slugify behavior` yields the
unit `978a36de0d0fbb2fba559f87-slugify-behavior`, and the model cited
`978a36de0d0fbb2fba559f87` — the refinement record id, which the same document
mentions by name. A reasonable misreading of text rite could simply have quoted.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.local.decompose import _prompt_for, available_units
from rite_ai.spec.slice import unit_text

SPEC = """# Tally Spec

## widget behaviour

The widget does the thing, and does not do the other thing.

## other section

Unrelated prose so the spec has more than one unit.
"""


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    (rite / "context").mkdir(parents=True, exist_ok=True)
    (rite / "context" / "spec.md").write_text(SPEC)
    (rite / "brief.yaml").write_text("project:\n  name: p\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "spec:\n  paths: ['.rite/context/spec.md']\n  pin_count: 8\n"
        "  slice_depth: 1\n  refuse_above: 0.25\n  extra_units: []\n"
        "coordination:\n  manager_roles: []\n"
    )
    return tmp_path.resolve()


def test_a_cite_resolves_from_the_spec_with_NO_derived_units(tmp_path):
    """🔴 The deadlock, now passing. No `.rite/spec/units/` exists at all."""
    root = _project(tmp_path)
    assert not (root / ".rite" / "spec" / "units").exists()
    text, problem = unit_text(root, "widget-behaviour")
    assert problem == "", problem
    assert "does the thing" in text, text


def test_a_cite_that_is_not_a_unit_is_still_refused(tmp_path):
    """RL-63's intent is intact: the cite must resolve to real spec text."""
    root = _project(tmp_path)
    text, problem = unit_text(root, "not-a-unit-at-all")
    assert problem, "an invented cite resolved"
    assert text == ""


def test_a_project_with_no_spec_says_so_rather_than_resolving(tmp_path):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nspec:\n  paths: []\n"
    )
    _text, problem = unit_text(tmp_path.resolve(), "anything")
    assert "registers no spec" in problem, problem


def test_derived_text_is_still_preferred_when_it_exists(tmp_path):
    """The derived unit remains the first answer — this adds a fallback, it does
    not replace the digest."""
    from rite_ai.spec.digest_files import unit_filename, units_dir

    root = _project(tmp_path)
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    (where / unit_filename("widget-behaviour")).write_text("DERIVED TEXT")
    text, problem = unit_text(root, "widget-behaviour")
    assert problem == ""
    assert text == "DERIVED TEXT", text


# ---- the ids are offered to the model -------------------------------------


def test_the_prompt_names_the_units_that_exist(tmp_path):
    root = _project(tmp_path)
    units = available_units(root)
    assert "widget-behaviour" in units, units
    prompt = _prompt_for("T-1", "do the thing", units)
    for unit in units:
        assert unit in prompt, f"{unit} is not offered to the model"


def test_the_prompt_says_a_cite_outside_the_list_is_refused(tmp_path):
    root = _project(tmp_path)
    prompt = _prompt_for("T-1", "x", available_units(root))
    assert "RL-63" in prompt
    assert "nothing else is a unit id" in prompt


def test_no_spec_is_said_rather_than_offered_as_an_empty_list(tmp_path):
    """🔴 An empty list reads as "cite anything". It must read as "you cannot"."""
    prompt = _prompt_for("T-1", "x", ())
    assert "registers no spec units" in prompt
    assert "rather than inventing a cite" in prompt


def test_available_units_never_raises_on_a_broken_spec(tmp_path):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "config.yaml").write_text("spec: [[[ not yaml")
    assert available_units(tmp_path.resolve()) == ()


def test_the_decomposer_actually_offers_them():
    """Dead wiring, by AST."""
    import ast
    import inspect

    from rite_ai.local import decompose

    tree = ast.parse(inspect.getsource(decompose.decompose_ticket))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "available_units" in called, "the prompt is built without the unit ids"


# ---- the VALIDATOR must use the resolver, not just have one available ------


def _subtask(cites):
    from rite_ai.local import decomposition as dec

    return dec.Subtask(
        id="s1",
        intent="do the widget thing",
        scope=("a.py",),
        verify="python -m pytest -q t.py",
        cites=tuple(cites),
    )


def test_the_validator_refuses_a_cite_that_does_not_resolve(tmp_path):
    """🔴 A surviving mutation showed this was untested: the validator could
    have ignored the resolver entirely and every test still passed."""
    from rite_ai.local.plan_validation import _cites_problem

    root = _project(tmp_path)
    why = _cites_problem(root, _subtask(["not-a-unit-at-all"]))
    assert why, "a subtask citing a non-existent unit was accepted"
    assert "RL-63" in why, why


def test_the_validator_accepts_a_cite_that_resolves_by_slice(tmp_path):
    """The other half, and the deadlock itself: no derived units anywhere."""
    from rite_ai.local.plan_validation import _cites_problem

    root = _project(tmp_path)
    assert not (root / ".rite" / "spec" / "units").exists()
    assert _cites_problem(root, _subtask(["widget-behaviour"])) == ""


def test_the_validator_still_refuses_a_subtask_with_no_cites(tmp_path):
    """RL-63 unchanged: an uncited subtask is the free-form run this replaces."""
    from rite_ai.local.plan_validation import _cites_problem

    root = _project(tmp_path)
    why = _cites_problem(root, _subtask([]))
    assert "cites no spec unit" in why, why
