"""A command that cannot fail is a check nobody is running (EXC-1, EXC-2).

Three instances on rite itself in one night, each discarding a real failure,
each caught by accident: a gate that exits 0 on its own help text and was
reported clean on that basis; `pytest | tail -3` under `set -e`, which pushed
a commit to `main` with a failing test named in the same output; and `uv run
ruff check` returning 1 without stopping the script.

The shape is the same as a claim that is silently never contended: the thing
looks fine precisely when it has stopped working, because "no complaint" is
what both a healthy run and a never-run produce.

What is worth testing is the two ways this check can itself be useless —
missing the mask, and firing on commands that are fine.
"""

from __future__ import annotations

import pytest

from rite_ai.verdicts import cannot_fail, command_problems


@pytest.mark.parametrize(
    "command",
    [
        "pytest | tail -1",
        "pytest tests/ | tail -n 20",
        "npm test | head -5",
        "cargo test | grep -v warning",
        "make test | tee build.log",
        "pytest | wc -l",
        "go test ./... | cat",
        "pytest | /usr/bin/tail -1",
    ],
)
def test_a_pipeline_ending_in_a_filter_has_lost_its_verdict(command):
    """The pipeline's status is its LAST element's. This is the exact shape
    that pushed a red commit to main."""
    assert cannot_fail(command)


@pytest.mark.parametrize(
    "command",
    ["make test || true", "pytest || :", "npm test ; true", "pytest ; exit 0"],
)
def test_an_explicitly_swallowed_failure_is_named(command):
    assert cannot_fail(command)


def test_a_backgrounded_command_reports_the_shell_not_itself():
    assert cannot_fail("pytest &")


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest tests/ -q",
        "npm run test",
        "cargo test --all-features",
        "make lint",
        "uv run pytest -q -p no:cacheprovider",
        # The filter is not LAST, so the real command's status is the one
        # that survives. Odd, but not blind.
        "cat manifest | pytest -",
        # `||` with a real command is a fallback, not a swallow.
        "pytest || pytest --lf",
        "ruff check . && ruff format --check .",
    ],
)
def test_an_ordinary_command_is_not_flagged(command):
    """A check that fires on every project is one people learn to scroll
    past — which is how this whole class survives."""
    assert cannot_fail(command) == ""


def test_nothing_recorded_is_not_a_problem():
    assert cannot_fail("") == ""
    assert cannot_fail("   ") == ""


def test_the_reason_says_what_to_look_at():
    """A bare False does not survive being read at 3am next to forty other
    lines."""
    why = cannot_fail("pytest | tail -1")
    assert "tail" in why
    assert "exit code" in why


def test_each_problem_names_where_it_lives():
    """The same sentence about the same command in two places is how a reader
    learns to skim past it."""
    lines = command_problems({"test": "pytest | tail -1"}, "module engine")
    assert lines == [
        "module engine: `test` cannot fail — the pipeline ends in `tail`, so "
        "the exit code is `tail`'s and not the command's — it reports success "
        "however the command exited"
    ]


def test_a_module_with_good_commands_reports_nothing():
    assert command_problems({"test": "pytest", "lint": "ruff check ."}, "m") == []


# --- the verify, where it is refused rather than reported --------------------------


def test_a_decomposition_verify_that_cannot_fail_is_a_problem():
    """`problems()`'s own docstring has said "a verify that cannot fail is the
    easiest way to fake a pass" since RL-T4, and only an EMPTY verify was
    refused. Three gates read this artifact; a verify that always passes makes
    all three decorative while looking green."""
    from rite_ai.local.decomposition import Decomposition, Subtask, problems

    plan = Decomposition(
        ticket="ABC-1",
        decomposed_by="planner",
        subtasks=(
            Subtask(
                id="s1",
                intent="add the parser",
                scope=("parser.py",),
                verify="pytest tests/test_parser.py | tail -1",
            ),
        ),
    )

    found = problems(plan)
    assert any("cannot fail" in p for p in found), found
    assert any("RL-7" in p for p in found)


def test_a_real_verify_still_passes_the_gate():
    from rite_ai.local.decomposition import Decomposition, Subtask, problems

    plan = Decomposition(
        ticket="ABC-1",
        decomposed_by="planner",
        subtasks=(
            Subtask(
                id="s1",
                intent="add the parser",
                scope=("parser.py",),
                verify="pytest tests/test_parser.py",
            ),
        ),
    )
    assert problems(plan) == []


def test_an_empty_verify_still_reports_the_original_problem():
    """The new check must not replace the old one: "no verify" and "a verify
    that cannot fail" are different sentences and different fixes."""
    from rite_ai.local.decomposition import Decomposition, Subtask, problems

    plan = Decomposition(
        ticket="ABC-1",
        decomposed_by="planner",
        subtasks=(Subtask(id="s1", intent="x", scope=("a.py",), verify=""),),
    )
    found = problems(plan)
    assert any("no verify command" in p for p in found)
    assert not any("cannot fail" in p for p in found)
