"""Manager roles: engine and duties (RL-T3, design §3).

The properties here are the ones the rest of rite local rests on. Two matter
more than the rest, and both are about what must NOT change:

- a project that has one Manager and says nothing about duties keeps working
  exactly as it does today;
- a `managers:` list written by v0.4.0 — bare names, priority order — parses
  and serialises back unchanged, because thirty-nine places read that list.
"""

from __future__ import annotations

import pytest

from rite_ai.config.managers import (
    DUTIES,
    PRESETS,
    configuration_problems,
    effective_duties,
    parse_managers,
    to_yaml_entry,
)


def _roles(*entries):
    parsed = parse_managers(list(entries))
    assert not parsed.error, parsed.error
    return parsed.roles


# --- what v0.4.0 already wrote ----------------------------------------------------


def test_a_list_of_bare_names_still_parses_in_priority_order():
    """`managers: [alpha, beta]` is what v0.4.0 shipped, and priority is the
    order of the list. Thirty-nine call sites read it that way."""
    parsed = parse_managers(["alpha", "beta", "gamma"])
    assert parsed.names == ["alpha", "beta", "gamma"]
    assert [r.engine for r in parsed.roles] == ["claude"] * 3


def test_bare_names_serialise_back_as_bare_names():
    """Otherwise upgrading rewrites every existing project's config for a
    feature it is not using."""
    parsed = parse_managers(["alpha", "beta"])
    assert [to_yaml_entry(r) for r in parsed.roles] == ["alpha", "beta"]


def test_adopting_a_local_tier_makes_a_v0_4_0_project_declare_its_managers():
    """The migration this feature actually imposes, stated by a test rather
    than discovered by a user: a project already listing bare names must give
    each one a preset or duties the moment it adds a tier. The error says which
    managers, so the fix is mechanical."""
    parsed = parse_managers(
        [
            "alpha",
            "beta",
            {
                "name": "small",
                "engine": "local:small",
                "preset": "executor",
                "endpoint": "http://localhost:11434/v1",
                "model": "qwen3:8b",
                "agent": "opencode",
            },
        ]
    )
    assert parsed.error
    assert "alpha, beta" in parsed.error

    # ...and once they declare, the mixed-shape file parses.
    parsed = parse_managers(
        [
            {"name": "alpha", "preset": "lead"},
            {"name": "beta", "preset": "pm", "engine": "human"},
            {
                "name": "small",
                "engine": "local:small",
                "preset": "executor",
                "endpoint": "http://localhost:11434/v1",
                "model": "qwen3:8b",
                "agent": "opencode",
            },
        ]
    )
    assert not parsed.error, parsed.error
    assert parsed.names == ["alpha", "beta", "small"]
    assert parsed.roles[2].local_class == "small"


# --- the one-Manager project does not change --------------------------------------


def test_a_lone_manager_that_declares_nothing_holds_every_duty():
    """Today's single session doing everything. If this changes, every existing
    project silently loses abilities on upgrade."""
    (role,) = _roles("alpha")
    assert effective_duties(role, 1) == frozenset(DUTIES)


def test_a_lone_manager_is_never_told_its_configuration_is_wrong():
    """Every rule below is about a division of labour that does not exist yet."""
    assert configuration_problems(list(_roles("alpha"))) == []


def test_two_bare_managers_still_parse_because_v0_4_0_shipped_that():
    """RL-34 as written refuses this — "once a project has two, each declares".
    Taken literally it breaks every project already on v0.4.0, which shipped
    `managers:` as bare names and may list three. The rule's REASON is that
    routing cannot guess, and that only bites once there is something to route
    between; two undeclared managers are two of today's sessions."""
    parsed = parse_managers(["alpha", "beta"])
    assert not parsed.error, parsed.error
    assert parsed.names == ["alpha", "beta"]


def test_declaring_one_manager_forces_the_others_to_declare_too():
    """The moment tiers exist, an undeclared manager is a routing decision
    nobody made. The error names the managers, because the reader has to fix an
    entry rather than learn a law."""
    parsed = parse_managers(["alpha", {"name": "small", "preset": "executor"}])
    assert parsed.error
    assert "alpha" in parsed.error
    assert "preset or a duties list" in parsed.error


# --- duties are a set, which is what makes Robert's mixes expressible -------------


def test_a_manager_may_hold_any_combination_of_duties():
    (role,) = _roles(
        {"name": "mixed", "duties": ["decompose", "plan-review", "execute"]}
    )
    assert effective_duties(role, 2) == {"decompose", "plan-review", "execute"}


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_expands_to_duties_rite_enforces(preset):
    (role,) = _roles({"name": "m", "preset": preset})
    duties = effective_duties(role, 2)
    assert duties
    assert duties <= frozenset(DUTIES)


def test_explicit_duties_override_a_preset():
    """A preset is a named default, not a type (RL-2)."""
    (role,) = _roles({"name": "m", "preset": "executor", "duties": ["decompose"]})
    assert effective_duties(role, 2) == {"decompose"}


def test_a_misspelt_duty_is_refused_rather_than_dropped():
    """The vocabulary is closed because gates key on it: a dropped
    `plan-reveiw` is a Manager that silently skips plan review (RL-3)."""
    parsed = parse_managers([{"name": "m", "duties": ["plan-reveiw"]}, "alpha"])
    assert parsed.error and "plan-reveiw" in parsed.error
    assert "plan-review" in parsed.error  # the vocabulary is shown, not just refused


# --- a local engine is a configuration, not a label -------------------------------


@pytest.mark.parametrize("missing", ["endpoint", "model", "agent"])
def test_a_local_engine_must_say_what_actually_runs(missing):
    entry = {
        "name": "small",
        "engine": "local:small",
        "endpoint": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "agent": "opencode",
        "preset": "executor",
    }
    del entry[missing]
    parsed = parse_managers([entry])
    assert parsed.error and missing in parsed.error


def test_those_fields_mean_nothing_on_a_claude_engine():
    """Accepting them silently would let a project believe it had configured a
    model for a Manager that spawns `claude`."""
    parsed = parse_managers([{"name": "a", "preset": "lead", "model": "qwen3:8b"}])
    assert parsed.error and "model" in parsed.error


def test_an_unknown_engine_is_refused_with_the_shape_that_is_allowed():
    parsed = parse_managers([{"name": "a", "engine": "ollama", "preset": "lead"}])
    assert parsed.error and "local:<class>" in parsed.error


def test_a_secret_in_config_is_refused_and_the_keychain_is_named():
    """SPEC §10. The endpoint is a URL; a key lives in the keychain."""
    parsed = parse_managers(
        [
            {
                "name": "small",
                "engine": "local:small",
                "endpoint": "https://api.example/v1",
                "model": "m",
                "agent": "aider",
                "api_key": "sk-live-1234",
                "preset": "executor",
            }
        ]
    )
    assert parsed.error and "keychain" in parsed.error
    assert "sk-live-1234" not in parsed.error  # never echo the thing itself


def test_an_unknown_key_on_an_entry_is_refused():
    """A silent partial read of a Manager is a Manager that is not what the
    file says it is."""
    parsed = parse_managers([{"name": "a", "preset": "lead", "tier": "big"}])
    assert parsed.error and "tier" in parsed.error


def test_a_duplicate_name_makes_priority_ambiguous_and_is_refused():
    parsed = parse_managers(["alpha", "alpha"])
    assert parsed.error and "twice" in parsed.error


# --- the configurations doctor must report ----------------------------------------


def test_a_decomposer_reviewed_only_by_its_own_engine_is_reported():
    """RL-6: a wrong slicing passes every check the slicer wrote. The gate is
    a DIFFERENT engine, not merely a different Manager."""
    roles = _roles(
        {
            "name": "p1",
            "engine": "local:large",
            "preset": "planner",
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:70b",
            "agent": "opencode",
        },
        {
            "name": "p2",
            "engine": "local:large",
            "duties": ["plan-review"],
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "agent": "opencode",
        },
        {"name": "lead", "preset": "pm"},
    )
    problems = configuration_problems(list(roles))
    assert any("different engine" in p and "p1" in p for p in problems)


def test_a_decomposer_with_an_independent_reviewer_is_not_reported():
    roles = _roles(
        {
            "name": "planner",
            "engine": "local:large",
            "preset": "planner",
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:70b",
            "agent": "opencode",
        },
        {"name": "lead", "preset": "lead"},
    )
    assert configuration_problems(list(roles)) == []


def test_integrate_on_a_local_engine_is_reported():
    """RL-11: the harness is rite's own code and SPEC §5.1.1 forbids it a push,
    so a local integrate holder cannot do the job it was given."""
    roles = _roles(
        {
            "name": "small",
            "engine": "local:small",
            "duties": ["integrate"],
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "agent": "opencode",
        },
        {"name": "lead", "preset": "lead"},
    )
    problems = configuration_problems(list(roles))
    assert any("integrate" in p and "small" in p for p in problems)


def test_a_project_whose_only_deciders_are_people_is_reported():
    """RL-32: the Owner runs unattended, so it cannot be a person."""
    roles = _roles(
        {"name": "robert", "engine": "human", "preset": "pm"},
        {
            "name": "small",
            "engine": "local:small",
            "preset": "executor",
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "agent": "opencode",
        },
    )
    problems = configuration_problems(list(roles))
    assert any("Owner" in p for p in problems)


def test_robert_s_three_roles_are_expressible_as_one_project():
    """The shape the design is for: one Manager reviewing and integrating, one
    decomposing, one executing — and a mix in a single Manager."""
    roles = _roles(
        {"name": "lead", "preset": "lead"},
        {
            "name": "planner",
            "engine": "local:large",
            "duties": ["decompose", "step-review", "execute"],
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:70b",
            "agent": "opencode",
        },
        {
            "name": "executor",
            "engine": "local:small",
            "preset": "executor",
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "agent": "opencode",
        },
    )
    assert configuration_problems(list(roles)) == []
    held = {r.name: effective_duties(r, 3) for r in roles}
    assert "integrate" in held["lead"]
    assert "decompose" in held["planner"] and "execute" in held["planner"]
    assert held["executor"] == {"execute"}


def test_a_role_that_is_serialised_and_reparsed_is_the_same_role():
    """Round-trip at the entry level: `config_to_yaml` writes these back."""
    original = _roles(
        {
            "name": "small",
            "engine": "local:small",
            "duties": ["execute"],
            "endpoint": "http://localhost:11434/v1",
            "model": "qwen3:8b",
            "agent": "opencode",
            "credential": "local_endpoint_key",
        }
    )
    again = parse_managers([to_yaml_entry(r) for r in original])
    assert not again.error, again.error
    assert again.roles[0] == original[0]
