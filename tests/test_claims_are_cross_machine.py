"""`rite claim` checks and publishes across machines (P2-5a/P2-5b).

The mechanism landed with both halves: a claim consults other machines'
published claims before it is granted, and publishes the grant so they can
consult it. Neither ran, because the one production caller passed no state
layer — so two machines could claim the same path and each would be told
yes.

These tests drive the real `claim` command, because the defect was in the
wiring and a test that calls the ledger directly would have passed
throughout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


def machine(tmp_path: Path, name: str, remote: str | None) -> Path:
    root = tmp_path / name
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    coordination = (
        f"coordination:\n  managers: [alpha, beta]\n  remote: '{remote}'\n"
        if remote
        else ""
    )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: s\n"
        "  projects: {workers: A, board: B}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n" + coordination
    )
    if remote:
        (rite / "machine").write_text(f"{name}\n")
    return root


def claim(root: Path, path: str, worker: str = "w1", ticket: str = "T-1"):
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["claim", path, "--worker", worker, "--ticket", ticket],
        catch_exceptions=False,
    )


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    remote = tmp_path / "coordination.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return (
        machine(tmp_path, "alpha", str(remote)),
        machine(tmp_path, "beta", str(remote)),
    )


def test_a_second_machine_cannot_claim_a_path_the_first_holds(fleet, monkeypatch):
    """§5.2 across machines. Both were told yes before this was wired, and
    two Workers edited the same file on different machines."""
    alpha, beta = fleet
    monkeypatch.chdir(alpha)
    assert claim(alpha, "src/app.py").exit_code == 0

    monkeypatch.chdir(beta)
    result = claim(beta, "src/app.py", worker="w2")
    assert result.exit_code == 4, result.output
    assert "contention" in result.output or "overlaps" in result.output


def test_different_paths_are_still_free(fleet, monkeypatch):
    alpha, beta = fleet
    monkeypatch.chdir(alpha)
    assert claim(alpha, "src/app.py").exit_code == 0
    monkeypatch.chdir(beta)
    assert claim(beta, "src/other.py", worker="w2").exit_code == 0


def test_a_project_with_no_fleet_claims_exactly_as_before(tmp_path, monkeypatch):
    """The single-machine case must not acquire a network round trip: no
    coordination configured means no layer, and `claim` stays local."""
    root = machine(tmp_path, "solo", None)
    monkeypatch.chdir(root)
    assert claim(root, "src/app.py").exit_code == 0


def test_an_unenrolled_machine_claims_locally_rather_than_failing(
    tmp_path, monkeypatch
):
    """Configured fleet, but this machine has no `.rite/machine`. It cannot
    publish under a name nobody gave it, so it behaves as it did before
    rather than refusing every claim."""
    remote = tmp_path / "coordination.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    root = machine(tmp_path, "alpha", str(remote))
    (root / ".rite" / "machine").unlink()
    monkeypatch.chdir(root)
    assert claim(root, "src/app.py").exit_code == 0


def test_an_unreachable_fleet_refuses_the_claim(tmp_path, monkeypatch):
    """Fail closed, the rule this whole layer follows: a machine that
    cannot see other machines' claims cannot safely take a shared path.
    Granting one it could not check is how two Workers end up in the same
    file."""
    root = machine(tmp_path, "alpha", str(tmp_path / "nowhere.git"))
    monkeypatch.chdir(root)
    result = claim(root, "src/app.py")
    assert result.exit_code == 4, result.output
