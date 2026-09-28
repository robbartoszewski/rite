"""`rite claim` works where `workers/` cannot be read: inside a Worker's sandbox.

Measured 2026-09-28 (pingr Worker proof): inside a Worker's sandbox the
project's `workers/` is deliberately unreadable, and `rite claim` died with
`PermissionError: … /workers` — raised from `claims_channel` via
`load_project`, before the ledger was written. The Worker carried on without
a claim, so the guarantee that two Workers cannot take the same path was not
in force. `rite release` failed the same way. Reproduced on main with Python
3.13 and 3.14 in a Worker-shaped yoloAI sandbox; with this fix, a claim made
inside lands in the project's ledger and a second Worker's claim on the same
path is refused.

Here a `workers/` with mode 000 stands in for the sandbox, which denies it
the same way to the same user.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-000 directory anyway"
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: []\n"
    )
    monkeypatch.setenv("RITE_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    workers = tmp_path / "workers"
    workers.chmod(0)
    try:
        with pytest.raises(PermissionError):
            list(workers.iterdir())  # the control: it really is unreadable
        yield tmp_path
    finally:
        workers.chmod(0o755)


def _ledger(root: Path) -> list:
    return json.loads((root / ".rite" / "claims.json").read_text())


def test_a_claim_is_recorded_without_reading_workers(project: Path):
    result = CliRunner().invoke(
        cli, ["claim", "README.md", "--worker", "alpha", "--ticket", "KAN-10"]
    )

    assert result.exit_code == 0, result.output
    assert [c["worker"] for c in _ledger(project)] == ["alpha"]


def test_could_not_look_is_not_reported_as_unregistered(project: Path):
    result = CliRunner().invoke(
        cli, ["claim", "README.md", "--worker", "alpha", "--ticket", "KAN-10"]
    )

    assert "not registered" not in result.output


def test_a_second_worker_is_still_refused_the_path(project: Path):
    runner = CliRunner()
    assert (
        runner.invoke(cli, ["claim", "README.md", "--worker", "alpha"]).exit_code == 0
    )

    second = runner.invoke(cli, ["claim", "README.md", "--worker", "beta"])

    assert second.exit_code == 4, second.output
    assert "held by alpha" in second.output


def test_release_works_without_reading_workers(project: Path):
    runner = CliRunner()
    assert (
        runner.invoke(cli, ["claim", "README.md", "--worker", "alpha"]).exit_code == 0
    )

    released = runner.invoke(cli, ["release", "--worker", "alpha"])

    assert released.exit_code == 0, released.output
    assert _ledger(project) == []
