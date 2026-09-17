"""Coordination settings that cannot work (§2.4, D-42, D-56).

Each test names a state that is ALWAYS a mistake, whatever else is true.
Nothing here asserts wording; each asserts that the state is reported at all
and that a healthy or half-finished project is left alone.
"""

from __future__ import annotations

import pytest

from rite_ai.config.models import CoordinationConfig
from rite_ai.coordination.config_check import coordination_problems


def config(**kw):
    base = dict(
        managers=["alpha", "beta"],
        remote="git@example.invalid:coord.git",
        state_branch="state",
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )
    base.update(kw)
    return CoordinationConfig(**base)


def test_a_complete_block_has_nothing_to_say():
    assert coordination_problems(config()) == []


def test_an_absent_block_is_phase_1_and_not_a_problem():
    """A single-machine project never configures this, and must not be told
    it is broken for the thing §2.4 says it does not need."""
    assert coordination_problems(CoordinationConfig()) == []


def test_a_stray_value_says_nothing_while_coordination_is_unused():
    """The check speaks only about a block somebody is USING. A leftover
    value in a project with no managers and no remote is not a fault to
    report — it coordinates with nobody either way, and `doctor` that cries
    about unused settings teaches people to skim past it.

    Caught by mutation: without this, the "not configured at all" exit is
    indistinguishable from having no exit, because a DEFAULT block produces
    no problems by any route."""
    assert (
        coordination_problems(
            CoordinationConfig(managers=[], remote="", owner_lease_minutes=0)
        )
        == []
    )


class TestHalfABlock:
    """The dangerous shape: enough to look configured, not enough to work."""

    def test_managers_without_a_remote_is_reported(self):
        assert coordination_problems(config(remote="")), "no remote, no election"

    def test_a_remote_without_managers_is_reported(self):
        assert coordination_problems(config(managers=[])), "nobody is eligible"


class TestPriorityMustBeUnambiguous:
    def test_a_duplicate_manager_is_reported(self):
        """D-56: the list IS priority. A name twice has two priorities, and
        which wins depends on where the reader stopped."""
        problems = coordination_problems(config(managers=["alpha", "beta", "alpha"]))
        assert problems and any("alpha" in p for p in problems)

    def test_a_blank_manager_name_is_reported(self):
        """A Manager with no name cannot publish a heartbeat under one or
        hold the lease — and an empty entry is what a trailing `-` in YAML
        produces, so it is easy to write by accident."""
        assert coordination_problems(config(managers=["alpha", "  "]))


class TestValuesThatCannotWork:
    @pytest.mark.parametrize("minutes", [0, -5])
    def test_a_non_positive_lease_is_reported(self, minutes):
        """A lease already expired when written means the role can never be
        held: every reader sees it as expired the moment it lands."""
        assert coordination_problems(config(owner_lease_minutes=minutes))

    def test_a_negative_skew_tolerance_is_reported(self):
        assert coordination_problems(config(skew_tolerance_seconds=-1))

    def test_zero_skew_tolerance_is_allowed(self):
        """0 is the documented risk (D-42 narrows the window, it does not
        close it), not a misconfiguration — reporting it would train people
        to ignore the check."""
        assert coordination_problems(config(skew_tolerance_seconds=0)) == []

    def test_an_empty_state_branch_is_reported(self):
        assert coordination_problems(config(state_branch=" "))


def test_every_problem_names_the_section_so_it_can_be_found():
    """`doctor` prints these among many lines; one that does not say where
    to look costs the reader the search."""
    for bad in (
        config(remote=""),
        config(managers=[]),
        config(managers=["a", "a"]),
        config(owner_lease_minutes=0),
    ):
        for problem in coordination_problems(bad):
            assert problem.startswith("coordination:")


def test_identity_is_deliberately_not_checked_here():
    """A machine cannot yet tell WHICH Manager it is (raised separately), so
    a config listing other machines' names only is not reported as wrong —
    guessing would turn an open question into a wrong answer."""
    assert coordination_problems(config(managers=["someone-else"])) == []
