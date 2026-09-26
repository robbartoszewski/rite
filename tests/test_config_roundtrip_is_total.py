"""`config_to_yaml` must write every field, not the ones today's caller sets.

This is a checklist line becoming a gate, which is what the template's own
opening paragraph says is supposed to happen to one: "a line that keeps firing
should become a lint rule or a gate instead". The line reads:

    A function that serialises a config object writes **every** field, not the
    ones today's caller happens to set. Anything that round-trips config
    through it silently deletes the sections it dropped.

It is there because a section was dropped once, and the cost is not a cosmetic
one: `rite schedule set` and anything else that reads-modifies-writes
`config.yaml` round-trips the WHOLE file through `parse_config` →
`config_to_yaml`, so a field the serialiser forgets is a field deleted from a
real project's config the next time anything writes it back.

The line asked a human to remember. `config_to_yaml` hand-enumerates about
thirty fields, and the test that guarded it hand-enumerated the assertions —
so a newly added field was missed by both. This walks `dataclasses.fields()`
instead, so a field added tomorrow is covered by nobody having done anything.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from rite_ai.cli.init.scaffold import config_to_yaml
from rite_ai.config.managers import ManagerRole
from rite_ai.config.models import (
    BudgetConfig,
    CheckinsConfig,
    CheckinWindow,
    CoordinationConfig,
    ExpertiseEntry,
    ProjectConfig,
    PublishGateConfig,
    ScanPattern,
    ScheduleConfig,
    ScheduleWindow,
    SpecConfig,
    TicketBackendConfig,
)
from rite_ai.config.parse import ParseError, parse_config


class Indistinguishable(Exception):
    """A field the fixture cannot give a value distinguishable from the
    default. Raised rather than skipped, because a silently skipped field is
    a field this gate claims to cover and does not."""


# Fields whose only valid values are few, so the generic "+7" would be refused
# on the way back in rather than round-tripped.
_VALID_ALTERNATES = {
    "spec.slice_depth": 2,
    # A Manager's fields constrain each other — the vocabularies are closed
    # and the endpoint/model/agent trio is local-only — so "any distinct
    # value" would build a role the parser is right to refuse. Each alternate
    # below keeps the role internally valid while still differing.
    "coordination.manager_roles[].engine": "local:large",
    "coordination.manager_roles[].preset": "planner",
    "coordination.manager_roles[].duties": ("decompose",),
    "coordination.manager_roles[].agent": "aider",
    # Schema-validated (A6): a user id and a channel name or id, because each
    # mistake is otherwise a bare `channel_not_found` at a run's first poll.
    "slack.owner_user": "U0C4HK552HF",
    "slack.broadcast_channel": "#team-status",
    # Validated (C6/C26): numeric ids, and the installation id must be set
    # whenever the App id is.
    "github_app.app_id": "123456",
    "github_app.installation_id": "7890123",
    "github_app.repository": "org/board",
}


def _distinct(path: str, value):
    """A value different from the default, so a field that is written but
    ignored on the way back in is caught as well as one that is dropped.

    Raises for anything it cannot change. An earlier version returned the
    value unchanged for lists, dicts, floats and `None` — so seven of
    twenty-four fields compared equal to themselves and the gate passed while
    `config_to_yaml` dropped them. Those seven included `expertise`,
    `scan_patterns` and `schedule.windows`: the three the serialiser builds
    by comprehension, which is to say the three most likely to be forgotten.
    """
    if path in _VALID_ALTERNATES:
        return _VALID_ALTERNATES[path]
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 7
    if isinstance(value, float):
        return value + 0.25
    if isinstance(value, str):
        return value + "-changed" if value else "changed"
    raise Indistinguishable(path)


def _populated() -> ProjectConfig:
    """A config with every collection non-empty and every Optional set.

    The gate is about fields being dropped, and an empty list round-trips as
    an empty list whether the serialiser writes it or not. Built explicitly
    rather than derived, so adding a collection field to the models makes
    `_mutate` raise `Indistinguishable` and someone has to come here — which
    is the intended failure, not an inconvenience.
    """
    return ProjectConfig(
        ticket_backend=TicketBackendConfig(
            type="jira",
            site="example.atlassian.net",
            repo="org/repo",
            projects={"workers": "ABC", "decisions": "RD"},
            credential="jira_token",
        ),
        expertise=[ExpertiseEntry(name="alice", tags=["billing", "auth"])],
        spec=SpecConfig(
            paths=["SPEC.md", "docs/adr/"],
            convention="Decisions are cited as D-<number>.",
            extra_units=[r"^- (REQ-\d+)\b"],
        ),
        publish_gate=PublishGateConfig(
            scan_patterns=[
                ScanPattern(
                    type="regex",
                    pattern="corp[.]example",
                    description="internal hostname",
                )
            ],
            gitleaks_config=".rite/gitleaks.toml",
        ),
        schedule=ScheduleConfig(
            timezone="Europe/Warsaw",
            windows=[ScheduleWindow(hours="09:00-17:00", workers=3)],
        ),
        budget=BudgetConfig(weekly_token_budget=12_000_000),
        checkins=CheckinsConfig(
            windows=[CheckinWindow(hours="09:00-10:00", days="Mon-Fri")]
        ),
        # Phase 2 (§2.4). `managers` must be non-empty here for the same
        # reason every other collection is: an empty list round-trips
        # whether or not the serialiser writes it, so an empty one would
        # let a dropped section pass the gate.
        coordination=CoordinationConfig(
            managers=["mac-studio", "laptop"],
            manager_roles=[
                # One role, fully populated and internally consistent: a local
                # engine with the three fields it must carry.
                ManagerRole(
                    name="laptop",
                    engine="local:small",
                    duties=("execute",),
                    preset="executor",
                    endpoint="http://localhost:11434/v1",
                    model="qwen3:8b",
                    agent="opencode",
                    credential="local_endpoint_key",
                ),
            ],
            remote="git@example.com:team/coord.git",
            state_branch="state",
            owner_lease_minutes=15,
            skew_tolerance_seconds=60,
        ),
    )


def _mutate(obj, prefix: str = ""):
    """A copy with every field changed, recursively — or a loud failure."""
    changes = {}
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        path = f"{prefix}{field.name}"
        if dataclasses.is_dataclass(value):
            changes[field.name] = _mutate(value, f"{path}.")
        elif isinstance(value, list):
            if not value:
                raise Indistinguishable(f"{path} (empty list)")
            changes[field.name] = [
                _mutate(v, f"{path}[].")
                if dataclasses.is_dataclass(v)
                else _distinct(path, v)
                for v in value
            ]
        elif isinstance(value, dict):
            if not value:
                raise Indistinguishable(f"{path} (empty dict)")
            changes[field.name] = {k: _distinct(path, v) for k, v in value.items()}
        elif value is None:
            raise Indistinguishable(f"{path} (left None)")
        else:
            changes[field.name] = _distinct(path, value)
    return dataclasses.replace(obj, **changes)


def _field_paths(obj, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        name = f"{prefix}{field.name}"
        if dataclasses.is_dataclass(value):
            out |= _field_paths(value, f"{name}.")
        else:
            out.add(name)
    return out


def _roundtrip(config: ProjectConfig, tmp_path: Path) -> ProjectConfig:
    path = tmp_path / "config.yaml"
    path.write_text(config_to_yaml(config))
    parsed = parse_config(path)
    assert not isinstance(parsed, ParseError), parsed
    return parsed


def test_every_declared_field_survives_the_round_trip(tmp_path):
    """The gate. Not a list of sections someone remembered to assert on —
    every field the dataclasses declare, found by walking them."""
    original = _mutate(_populated())

    parsed = _roundtrip(original, tmp_path)

    missing = []
    for path in sorted(_field_paths(original)):
        want = original
        got = parsed
        for part in path.split("."):
            want = getattr(want, part)
            got = getattr(got, part)
        if want != got:
            missing.append(f"{path}: wrote {want!r}, read back {got!r}")
    assert not missing, (
        "config_to_yaml or parse_config loses these on a round trip, so "
        "`rite schedule set` and anything else that rewrites config.yaml "
        "would delete them from a real project:\n  " + "\n  ".join(missing)
    )


def test_the_walk_actually_reaches_the_nested_sections(tmp_path):
    """Guards the guard: a `_field_paths` that returned only top-level names
    would make the test above pass while checking almost nothing."""
    paths = _field_paths(_populated())
    assert "sandbox.backend" in paths
    assert "budget.week_start_day" in paths
    assert "ticket_backend.type" in paths
    assert len(paths) > 20, sorted(paths)


def test_a_dropped_section_is_actually_caught(tmp_path):
    """Delete the code under test and confirm the check goes red — done here
    rather than left to a reader, because this file exists to replace a human
    remembering."""
    import rite_ai.cli.init.scaffold as scaffold

    original = _mutate(_populated())
    full = config_to_yaml(original)
    assert "sandbox:" in full

    real = scaffold.config_to_yaml
    try:
        # The whole block, not just its header: orphaned keys would land in
        # the section above and be refused as unknown there instead.
        def without_sandbox(c):
            kept, inside = [], False
            for line in real(c).splitlines():
                if not line.startswith(" "):
                    inside = line.startswith("sandbox:")
                if not inside:
                    kept.append(line)
            return "\n".join(kept)

        scaffold.config_to_yaml = without_sandbox
        path = tmp_path / "config.yaml"
        path.write_text(scaffold.config_to_yaml(original))
        parsed = parse_config(path)
        assert not isinstance(parsed, ParseError)
        assert parsed.sandbox.backend != original.sandbox.backend
    finally:
        scaffold.config_to_yaml = real
