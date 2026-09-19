"""`rite loop status` names a claim whose holder is gone.

`plan_cycle` already finds suspects and `format_cycle` already prints them,
so they reach `.rite/loop.log`. That is the right place for them and the
wrong place to stop: a loop meant to run for days writes a log nobody
tails, and the command a person actually types to ask "is the loop all
right?" is this one.

The claim that prompted it was 9.3 days old, its holder had never written a
heartbeat and had no sandbox, and a live worker asking for the same path
was refused — rite detected the death and reported it somewhere nobody was
reading.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.chdir(root)
    assert CliRunner().invoke(cli, ["init", "--yes", "."]).exit_code == 0
    assert CliRunner().invoke(cli, ["add", "worker", "alpha"]).exit_code == 0
    return root


def _ancient_claim(root: Path, worker: str = "alpha") -> None:
    """A claim far older than any stall threshold, whose holder never beat."""
    (root / ".rite" / "claims.json").write_text(
        json.dumps(
            [
                {
                    "paths": ["engine/thing.py"],
                    "worker": worker,
                    "ticket": "T-9",
                    "timestamp": time.time() - 9.3 * 24 * 3600,
                }
            ]
        )
    )


def test_it_names_the_claim_when_no_loop_is_running(project):
    """THE GAP. No loop is running here, which is precisely when someone
    types this — and the dead claim still has to appear."""
    _ancient_claim(project)

    out = CliRunner().invoke(cli, ["loop", "status"]).output

    assert "alpha" in out, out
    assert "engine/thing.py" in out


def test_a_healthy_project_says_nothing_extra(project):
    """Asserted as a difference: without it the test above passes on a
    build that prints the ledger unconditionally."""
    out = CliRunner().invoke(cli, ["loop", "status"]).output

    assert "engine/thing.py" not in out


def test_it_still_answers_the_question_it_was_asked(project):
    """The suspects half must never cost the session half. A project whose
    claims cannot be read still has to say whether a loop is running."""
    (project / ".rite" / "claims.json").write_text("{not json")

    result = CliRunner().invoke(cli, ["loop", "status"])

    assert result.exit_code == 0, result.output
    assert "loop:" in result.output
