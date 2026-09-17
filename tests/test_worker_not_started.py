"""A worker that has just been added reads as not started, not STALLED.

Measured before the fix: `rite add worker w1`, then `rite status` showed
`w1 … — STALLED` and `rite watchdog` exited 1 with "stalled — no heartbeat ever
recorded" — broken, on minute one, and reported by a scheduled watchdog every
cycle until the first heartbeat.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.cli.init import run_init
from rite_ai.cli.main import cli


def _project_with_worker(tmp_path: Path, monkeypatch) -> Path:
    origin = tmp_path / "origin"
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        cwd=origin,
        check=True,
    )
    root = tmp_path / "project"
    root.mkdir()
    assert run_init(root, yes=True).status == "created"
    monkeypatch.chdir(root)
    runner = CliRunner()
    assert runner.invoke(cli, ["add", "module", "api", str(origin)]).exit_code == 0
    assert runner.invoke(cli, ["add", "worker", "w1"]).exit_code == 0
    return root


def test_status_and_watchdog_on_a_worker_just_added(tmp_path, monkeypatch):
    _project_with_worker(tmp_path, monkeypatch)
    runner = CliRunner()

    status = runner.invoke(cli, ["status", "--no-board"])
    assert "w1: modules=[api] — not started" in status.output
    assert "STALLED" not in status.output

    watchdog = runner.invoke(cli, ["watchdog"])
    assert watchdog.exit_code == 0, watchdog.output
    assert "stalled" not in watchdog.output


def test_a_worker_holding_claims_with_no_heartbeat_is_still_stalled(
    tmp_path, monkeypatch
):
    root = _project_with_worker(tmp_path, monkeypatch)
    ClaimsLedger(root / ".rite" / "claims.json").claim(["api/src"], "w1")
    runner = CliRunner()

    assert (
        "w1: modules=[api] — STALLED"
        in runner.invoke(cli, ["status", "--no-board"]).output
    )
    watchdog = runner.invoke(cli, ["watchdog"])
    assert watchdog.exit_code == 1
    assert "no heartbeat ever recorded" in watchdog.output
