"""A JIRA project with no project key is told how to record one.

`rite init` does not ask for the key, so `rite init --yes` with JIRA writes
`projects: {}` and every board command then refused with "no project configured
for role 'workers'" — the key's name, not its shape, and no mention that
`rite credential set jira` asks for it and writes it. Both places now say.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.tickets import BackendError, create_backend


def _preset(tmp_path: Path, backend: str) -> Path:
    preset = tmp_path / "preset.yaml"
    preset.write_text(
        f"operations:\n  ticket_backend: {backend}\n"
        "  jira_site: acme.atlassian.net\n"
        "  github_repo: acme/news\n"
    )
    return preset


def test_init_with_jira_ends_by_naming_the_command(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    result = CliRunner().invoke(
        cli, ["init", str(project), "--config", str(_preset(tmp_path, "jira")), "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "JIRA: run `rite credential set jira`" in result.output
    assert "project key" in result.output


def test_init_without_jira_says_nothing_about_it(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    result = CliRunner().invoke(
        cli,
        ["init", str(project), "--config", str(_preset(tmp_path, "github")), "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "credential set jira" not in result.output


def _refusal(monkeypatch, role: str) -> str:
    monkeypatch.setenv("RITE_JIRA_EMAIL", "me@acme.com")
    monkeypatch.setenv("RITE_JIRA_TOKEN", "placeholder")
    result = create_backend(
        "jira", site="acme.atlassian.net", projects={}, board_role=role
    )
    assert isinstance(result, BackendError)
    return result.message


def test_the_board_refusal_names_the_command_and_the_shape(monkeypatch):
    message = _refusal(monkeypatch, "workers")
    assert "rite credential set jira" in message
    assert "projects:\n    workers: XYZ" in message


def test_a_role_the_command_does_not_write_is_not_sent_to_it(monkeypatch):
    message = _refusal(monkeypatch, "testing")
    assert "rite credential set jira" not in message
    assert "projects:\n    testing: XYZ" in message
