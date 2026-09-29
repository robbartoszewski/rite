"""`rite doctor` says when a refinement round cannot reach the person in Slack.

Measured in the v0.7.0 dogfood (`~/open-source/yoloai`): `refinement.
questions_to: dm` (the default), `slack.owner_user: ''` and no Slack bot token,
and `rite doctor` said nothing about Slack at all — `_doctor_slack` is silent
for a project without Slack, by design. But refinement is on for every project
with a board, and no Worker starts until a ticket is refined, so the gap was
the one thing between the run and its first Worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.cli.main as main
from rite_ai.cli.main import _doctor_refinement_reaches_you, cli
from rite_ai.credentials.store import namespaced, store

NS = "acme-1a2b3c"


def _project(
    tmp_path: Path,
    *,
    board: str = "jira",
    owner_user: str = "",
    questions_to: str = "dm",
    channel: str = "",
) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    rite.joinpath("brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    rite.joinpath("modules.yaml").write_text("modules: {}\n")
    rite.joinpath("config.yaml").write_text(
        f"ticket_backend:\n  type: {board}\n  site: t.atlassian.net\n"
        "  projects:\n    workers: KAN\n"
        f"credentials:\n  namespace: {NS}\n"
        "sandbox:\n  enabled: false\n"
        f"slack:\n  owner_user: '{owner_user}'\n"
        f"refinement:\n  questions_to: {questions_to}\n  channel: '{channel}'\n"
    )
    return tmp_path


def _with_slack_token() -> None:
    store(namespaced(NS, "slack_bot_token"), "xoxb-test")


def _check(root: Path, capsys) -> tuple[list[str], str]:
    problems: list[str] = []
    _doctor_refinement_reaches_you(root, problems)
    return problems, capsys.readouterr().out


def test_the_dogfood_config_is_a_problem_naming_both_gaps(tmp_path, capsys):
    problems, out = _check(_project(tmp_path), capsys)
    assert len(problems) == 1
    assert "slack.owner_user is empty" in out
    assert "no Slack bot token" in out
    # What to do, as commands and keys.
    assert "`rite credential set slack`" in out
    assert "slack.owner_user" in out and "U…" in out


def test_the_owner_without_a_token_is_still_a_problem(tmp_path, capsys):
    problems, out = _check(_project(tmp_path, owner_user="U123"), capsys)
    assert len(problems) == 1
    assert "no Slack bot token" in out
    assert "owner_user is empty" not in out


def test_a_token_without_the_owner_is_still_a_problem(tmp_path, capsys):
    _with_slack_token()
    problems, out = _check(_project(tmp_path), capsys)
    assert len(problems) == 1
    assert "slack.owner_user is empty" in out
    assert "bot token" not in out


def test_control_owner_and_token_set_is_not_a_problem(tmp_path, capsys):
    _with_slack_token()
    problems, out = _check(_project(tmp_path, owner_user="U123"), capsys)
    assert problems == [] and out == ""


def test_a_project_with_no_board_refines_nothing_so_is_not_checked(tmp_path, capsys):
    problems, out = _check(_project(tmp_path, board="none"), capsys)
    assert problems == [] and out == ""


def test_a_channel_needs_the_token_and_not_the_owner(tmp_path, capsys):
    root = _project(tmp_path, questions_to="channel", channel="C0123")
    problems, out = _check(root, capsys)
    assert len(problems) == 1
    assert "C0123" in out and "no Slack bot token" in out
    assert "owner_user" not in out


def test_an_unreadable_store_is_not_reported_as_a_missing_token(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr("rite_ai.credentials.store.store_is_readable", lambda: False)
    problems, out = _check(_project(tmp_path, owner_user="U123"), capsys)
    assert problems == [] and out == ""


@pytest.fixture
def no_board_probe(monkeypatch):
    """The board check reaches Jira over the network; it is not what this is
    about, so it is stubbed and everything else in doctor runs."""
    monkeypatch.setattr(main, "_doctor_board_can_create", lambda root, p: None)


def test_rite_doctor_reports_it_and_counts_it(tmp_path, monkeypatch, no_board_probe):
    """Wired into the real report, not only a function that exists."""
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["doctor"])
    assert "refinement: questions go to your Slack DM" in result.output
    assert result.exit_code == 1
