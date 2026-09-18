"""The coordination section (P2-0a, SPEC §2.4) — Phase 2's first config.

Nothing here is live in Phase 1. The section exists because every consumer
on the critical path binds to it, and because three of its five fields were
open questions that are now answered and should stay answered.

The round-trip gate (`test_config_roundtrip_is_total`) already covers "is
every field written and read back" by walking `dataclasses.fields()`. What
it cannot cover is what the fields MEAN, which is what this file pins:
priority order, an explicit remote, and a lease that is not the pool's.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.cli.init.scaffold import config_to_yaml
from rite_ai.config.models import CoordinationConfig, ProjectConfig
from rite_ai.config.parse import ParseError, parse_config


def _parsed(tmp_path: Path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(body)
    out = parse_config(p)
    assert not isinstance(out, ParseError), out
    return out


class TestPriorityIsTheOrder:
    """§2.4: "Managers are listed in priority order"; the first active one
    is Owner. A bare list, so the order IS the priority — there is no
    separate rank field to disagree with it."""

    def test_the_list_order_is_preserved_exactly(self, tmp_path):
        cfg = _parsed(
            tmp_path,
            "coordination:\n  managers: [mac-studio, laptop, mini]\n",
        )
        assert cfg.coordination.managers == ["mac-studio", "laptop", "mini"]

    def test_order_survives_a_round_trip(self, tmp_path):
        """A serialiser that sorted or set-ified this would silently
        reorder the org chart — every promotion would then pick a
        different machine."""
        original = ProjectConfig(
            coordination=CoordinationConfig(managers=["first", "second", "third"])
        )
        back = _parsed(tmp_path, config_to_yaml(original))
        assert back.coordination.managers == ["first", "second", "third"]


class TestTheRemoteIsExplicit:
    """Never inferred from `origin`. Inferring couples coordination to
    whichever remote happens to be `origin`, which breaks on a fork — and
    the failure is a second machine coordinating through the wrong repo,
    which looks like nothing until two Owners appear."""

    def test_absent_means_empty_not_guessed(self, tmp_path):
        cfg = _parsed(tmp_path, "coordination:\n  managers: [a]\n")
        assert cfg.coordination.remote == ""

    def test_it_round_trips(self, tmp_path):
        original = ProjectConfig(
            coordination=CoordinationConfig(remote="git@example.com:team/coord.git")
        )
        back = _parsed(tmp_path, config_to_yaml(original))
        assert back.coordination.remote == "git@example.com:team/coord.git"


class TestTheOwnerLeaseIsNotThePoolLease:
    """The whole reason this key carries `owner_`. `PoolConfig.
    lease_expiry_minutes` is the LOCAL pool readiness lease (a reporting
    threshold); this one decides who runs the project. Confusing them
    would tie cross-machine election to a local probe interval."""

    def test_default_is_fifteen_minutes(self):
        assert CoordinationConfig().owner_lease_minutes == 15

    def test_it_is_a_separate_field_from_the_pool_lease(self):
        cfg = ProjectConfig()
        assert cfg.coordination.owner_lease_minutes == 15
        assert cfg.pool.lease_expiry_minutes == 15
        # Same default today, different keys on purpose: changing one must
        # not move the other.
        cfg.coordination.owner_lease_minutes = 30
        assert cfg.pool.lease_expiry_minutes == 15

    def test_a_configured_value_is_read(self, tmp_path):
        cfg = _parsed(tmp_path, "coordination:\n  owner_lease_minutes: 5\n")
        assert cfg.coordination.owner_lease_minutes == 5


class TestSkewToleranceAndStateBranch:
    def test_skew_defaults_to_sixty_seconds(self):
        """D-42's margin, as a key rather than a constant — how much clock
        drift a fleet tolerates is policy, not algorithm."""
        assert CoordinationConfig().skew_tolerance_seconds == 60

    def test_state_branch_defaults_to_state(self):
        assert CoordinationConfig().state_branch == "state"

    def test_a_blank_state_branch_falls_back_rather_than_being_empty(self, tmp_path):
        """An empty branch name would produce a refspec that pushes
        nothing, silently. §2.4.2 needs a name it can build
        `--force-with-lease=<branch>:<oid>` from."""
        cfg = _parsed(tmp_path, "coordination:\n  state_branch: ''\n")
        assert cfg.coordination.state_branch == "state"


class TestAMalformedSectionDoesNotTakeOutEveryCommand:
    def test_a_non_mapping_narrows_to_defaults(self, tmp_path):
        """`parse_config` raising would break every command that reads
        config.yaml, including the ones that would repair it."""
        cfg = _parsed(tmp_path, "coordination: 'not a mapping'\n")
        assert cfg.coordination == CoordinationConfig()

    def test_an_absent_section_is_defaults(self, tmp_path):
        cfg = _parsed(tmp_path, "ticket_backend:\n  type: none\n")
        assert cfg.coordination.managers == []
        assert cfg.coordination.owner_lease_minutes == 15
