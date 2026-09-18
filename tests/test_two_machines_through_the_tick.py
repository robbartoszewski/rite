"""Two machines, one remote, driven through `run_tick` (§2.4, §5.1.2).

Everything else tests the coordination stack by calling it. This tests it the
way it will actually run: two project roots on one coordination remote, each
ticking from its own scheduler, with nothing injected — the entry point a
cron job invokes, doing what a second machine joining a fleet does.

It is the difference between "the election works" and "the election runs".
The stack passed every test it had while being called by nobody at all.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.git_backend import GitStateLayer
from rite_ai.coordination.overview import ALIVE, read_overview
from rite_ai.scheduler import run_tick

MANAGERS = ["alpha", "beta"]


def machine(tmp_path: Path, name: str, remote: str) -> Path:
    """A project root configured as one machine of a two-machine fleet."""
    root = tmp_path / name
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: test.atlassian.net\n"
        "  projects: {workers: ABC, board: XYZ}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        f"coordination:\n  managers: [{', '.join(MANAGERS)}]\n"
        f"  remote: '{remote}'\n"
    )
    # The one piece of per-machine state: which Manager this machine is.
    (rite / "machine").write_text(f"{name}\n")
    return root


def lines(result) -> list[str]:
    return [m for m in result.messages if m.startswith("coordination")]


@pytest.fixture
def fleet(tmp_path):
    remote = tmp_path / "coordination.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    alpha = machine(tmp_path, "alpha", str(remote))
    beta = machine(tmp_path, "beta", str(remote))
    return alpha, beta, str(remote)


def observe(remote: str, tmp_path: Path):
    """A third reader, as a human running `doctor` on any machine would."""
    return read_overview(
        GitStateLayer(remote, tmp_path / f"observer-{datetime.now().timestamp()}.git"),
        CoordinationConfig(managers=MANAGERS, remote=remote),
        now=datetime.now(UTC),
        heartbeat=HeartbeatConfig(interval_minutes=10, stall_threshold=3),
    )


def test_the_first_machine_to_tick_becomes_owner(fleet, tmp_path):
    alpha, _, remote = fleet
    assert any("promoted" in line for line in lines(run_tick(alpha)))
    assert observe(remote, tmp_path).owner == "alpha"


def test_a_lower_priority_machine_joins_without_taking_the_role(fleet, tmp_path):
    """§2.4: exactly one Owner. A machine joining a fleet that already has
    one publishes its liveness and leaves the role alone."""
    alpha, beta, remote = fleet
    run_tick(alpha)
    beta_lines = lines(run_tick(beta))
    assert not any("promoted" in line for line in beta_lines), beta_lines

    overview = observe(remote, tmp_path)
    assert overview.owner == "alpha" and overview.has_owner
    assert {m.name: m.state for m in overview.managers}["beta"] == ALIVE, (
        "beta joined the fleet without announcing itself"
    )


def test_a_HIGHER_priority_machine_asks_rather_than_seizing(fleet, tmp_path):
    """The case that actually isolates "no seizure" (§2.4).

    The test above does not: the joining machine outranks nobody, so it
    declines for TWO reasons at once — the lease is held AND a
    higher-priority Manager is alive — and deferral alone would carry it.

    Here the second machine to tick OUTRANKS the holder, so deferral cannot
    explain the outcome. §2.4 says it asks through a promotion request; it
    does not take.

    ⚠ **Two independent checks enforce this, and neither is redundant.**
    Measured by mutation, because it is not obvious from reading: breaking
    the election's "a live lease is not challengeable" leaves this green,
    and so does breaking the lease's own equivalent check — each covers for
    the other. Only breaking BOTH produces a seizure and fails this test.
    Anyone tempted to delete one as duplication should know it is the second
    half of a belt and braces, not a leftover."""
    alpha, beta, remote = fleet
    assert any("promoted" in line for line in lines(run_tick(beta))), "beta first"

    alpha_lines = lines(run_tick(alpha))
    assert not any("promoted" in line for line in alpha_lines), alpha_lines
    assert observe(remote, tmp_path).owner == "beta", "the role was seized"

    # And it asked, which is what makes the handover happen at beta's own
    # operation boundary rather than mid-write (§2.4 "Graceful demotion").
    from rite_ai.coordination.demotion import pending_request

    layer = GitStateLayer(remote, tmp_path / "ask-check.git")
    asked = pending_request(layer, "beta")
    assert asked is not None and asked.request.requester == "alpha", asked


def test_both_machines_are_visible_to_a_human_after_one_round(fleet, tmp_path):
    """What `doctor` shows on either machine: the role, and both Managers
    alive. Neither is inferred — each published its own heartbeat from its
    own tick."""
    alpha, beta, remote = fleet
    run_tick(alpha)
    run_tick(beta)
    states = {m.name: m.state for m in observe(remote, tmp_path).managers}
    assert states == {"alpha": ALIVE, "beta": ALIVE}, states


def test_the_owner_keeps_the_role_across_its_own_ticks(fleet, tmp_path):
    """Renewal through the real entry point: the second tick must not
    re-elect, and the role must not flap between two machines that are both
    ticking normally — the failure a human would see as churn on the board."""
    alpha, beta, remote = fleet
    run_tick(alpha)
    for _ in range(3):
        run_tick(beta)
        assert any("renewed" in line for line in lines(run_tick(alpha)))
    assert observe(remote, tmp_path).owner == "alpha"


def test_a_machine_removed_from_the_list_stops_standing(fleet, tmp_path):
    """Priority is the config list (D-60). Taking a machine out of it must
    be enough to stop it competing, without touching that machine's own
    files — which is how an operator retires one."""
    alpha, beta, remote = fleet
    run_tick(alpha)
    config = (beta / ".rite" / "config.yaml").read_text()
    (beta / ".rite" / "config.yaml").write_text(
        config.replace("managers: [alpha, beta]", "managers: [alpha]")
    )
    beta_lines = lines(run_tick(beta))
    assert beta_lines and all("promoted" not in line for line in beta_lines)
    assert observe(remote, tmp_path).owner == "alpha"
