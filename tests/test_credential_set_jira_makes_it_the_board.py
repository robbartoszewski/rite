"""`rite credential set jira` on a project with no board makes Jira the board.

Measured in the v0.7.0 dogfood (`~/open-source/yoloai`): `rite init` answered
the ticket backend `none`, then `rite credential set jira` recorded the site
and `projects.workers: KAN` in config.yaml, and `type` stayed `none`. rite read
no board: `rite doctor` said no ticket backend was configured, with the site
and the project key sitting in the same file. SPEC §10.5 says the one command
"sets up a working integration"; it did not.

Driven through the real CLI in the order the dogfood ran it: `rite init --yes`
(which answers `none`), then `rite credential set jira`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli
from rite_ai.config.parse import parse_config

# site, email, token (+confirm), project key
JIRA_ANSWERS = "team.atlassian.net\nsomeone@example.com\nSEKRIT\nSEKRIT\nKAN\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)


def _initialised(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    return root


def _config(root: Path):
    return parse_config(root / ".rite" / "config.yaml")


def test_init_with_no_board_then_setting_jira_makes_jira_the_board(
    tmp_path: Path, monkeypatch
):
    root = _initialised(tmp_path, monkeypatch)
    # The precondition the dogfood had, so this cannot pass by init already
    # having chosen Jira.
    assert _config(root).ticket_backend.type == "none"

    result = CliRunner().invoke(cli, ["credential", "set", "jira"], input=JIRA_ANSWERS)
    assert result.exit_code == 0, result.output

    tb = _config(root).ticket_backend
    assert (tb.type, tb.site, tb.projects.get("workers")) == (
        "jira",
        "team.atlassian.net",
        "KAN",
    )
    # And it says so, with the other config it recorded.
    assert "ticket_backend.type" in result.output


def test_the_board_is_then_read_as_jira(tmp_path: Path, monkeypatch):
    """The consequence, not only the key: the board rite builds is Jira's, and
    a Manager's board is no longer ABSENT (the state that sends it to set a
    board up)."""
    from rite_ai.cli.main import _board_for_manager
    from rite_ai.tickets.jira import JiraBackend

    root = _initialised(tmp_path, monkeypatch)
    CliRunner().invoke(cli, ["credential", "set", "jira"], input=JIRA_ANSWERS)

    board, state, _problem, _record = _board_for_manager(root)
    assert state == "ok"
    from rite_ai.tickets.scope import unwrapped

    assert isinstance(unwrapped(board), JiraBackend)


def test_a_project_already_on_github_keeps_its_board_and_is_told(
    tmp_path: Path, monkeypatch
):
    root = _initialised(tmp_path, monkeypatch)
    path = root / ".rite" / "config.yaml"
    path.write_text(path.read_text().replace("  type: none\n", "  type: github\n", 1))
    assert _config(root).ticket_backend.type == "github"

    result = CliRunner().invoke(cli, ["credential", "set", "jira"], input=JIRA_ANSWERS)
    assert result.exit_code == 0, result.output

    assert _config(root).ticket_backend.type == "github"
    assert "ticket_backend.type is 'github'" in result.output
