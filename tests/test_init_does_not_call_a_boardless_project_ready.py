"""SCRUM-7 / C3: `rite init` does not say "Ready." about a project with no board.

`setup.what_is_missing` returned rows for no Manager, no module and no Worker,
and never looked at `config.ticket_backend.type`. With no rows `run_init`
prints "Ready. Start a Dispatch session — it knows what to do from here."

Measured in the v0.7.0 dogfood: a real project finished init with
`ticket_backend.type: none` and was told it was ready — while a4's own
changelog already claimed init "no longer says 'Ready.' about a project with
nothing to work on". The check covered the WORKSPACE and stopped short of the
thing the work comes from.

🔴 **A signpost, not a writer.** init does not write backend config — `rite
credential set` does, from `Field.config_path` — so the rows name that command
and the fields it records, never a file to edit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.init import setup
from rite_ai.cli.main import cli

READY = "Ready. Start a Dispatch session"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "rite-home"))


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "main.go").write_text("package main\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "c"],
    ):
        subprocess.run(
            ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
    return root


def _init(root: Path, *args: str, input: str | None = None) -> str:
    result = CliRunner().invoke(cli, ["init", *args, str(root)], input=input)
    assert result.exit_code == 0, result.output
    return result.output


def _preset(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "preset.yaml"
    path.write_text(body)
    return ["--config", str(path)]


# Everything a project needs for the OTHER rows to be silent, so the board row
# is the only one under test: a Manager, a module (the root repo) and a Worker.
WHOLE_PROJECT = "managers:\n  add: lead\nworkers:\n  add: w1\n"


class TestTheBoardIsPartOfBeingReady:
    def test_a_project_with_no_board_is_told_so(self, tmp_path):
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *_preset(tmp_path, WHOLE_PROJECT))

        assert "no ticket board is configured" in out
        assert "`ticket_backend.type: none`" in out

    def test_and_is_not_called_ready(self, tmp_path):
        """The row is the point; the word "Ready." is what it stops."""
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *_preset(tmp_path, WHOLE_PROJECT))

        assert READY not in out

    def test_the_row_names_the_command_that_wires_it(self, tmp_path):
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *_preset(tmp_path, WHOLE_PROJECT))

        assert "rite credential set jira" in out

    def test_and_the_fields_that_command_records(self, tmp_path):
        """⚠ Named because the dogfood's actual failure was believing the site
        and the project key were enough: `rite credential set jira` recorded
        both and `type` stayed `none`, so rite read no board at all."""
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *_preset(tmp_path, WHOLE_PROJECT))

        assert "ticket_backend.type" in out
        assert "ticket_backend.site" in out
        assert "ticket_backend.projects.workers" in out

    def test_it_never_tells_anyone_to_edit_the_file(self, tmp_path):
        """🔴 init does not write backend config and must not ask for a hand
        edit of what `rite credential set` writes."""
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *_preset(tmp_path, WHOLE_PROJECT))

        assert "config.yaml" not in setup.NO_BOARD
        assert "edit" not in setup.NO_BOARD
        assert "no ticket board is configured" in out

    def test_a_project_with_a_board_gets_no_board_row(self, tmp_path):
        """⚠ **The control.** A row that is always printed is not a check.
        JIRA chosen through the questionnaire, which is the only way init
        itself sets `ticket_backend.type`."""
        root = _repo(tmp_path / "app")
        body = WHOLE_PROJECT + (
            "operations:\n  ticket_backend: jira\n  jira_site: t.atlassian.net\n"
        )

        out = _init(root, "--yes", *_preset(tmp_path, body))

        assert "no ticket board is configured" not in out


class TestARefinementRoundHasSomewhereToGo:
    """Same shape for Slack: `refinement.questions_to` defaults to `dm`, and
    with `slack.owner_user` empty a round has no delivery route. a4 added a
    `rite doctor` check for it — but init declares readiness first, and a
    project is told it is ready before anybody runs doctor."""

    def _with_a_board(self, tmp_path: Path, extra: str = "") -> list[str]:
        return _preset(
            tmp_path,
            WHOLE_PROJECT
            + "operations:\n  ticket_backend: jira\n  jira_site: t.atlassian.net\n"
            + extra,
        )

    def test_a_board_with_no_slack_owner_is_told_the_route_is_missing(self, tmp_path):
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *self._with_a_board(tmp_path))

        assert "refinement.questions_to: dm" in out
        assert "slack.owner_user" in out

    def test_and_is_not_called_ready(self, tmp_path):
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *self._with_a_board(tmp_path))

        assert READY not in out

    def test_the_row_names_both_ways_a_question_still_reaches_you(self, tmp_path):
        """The command that fixes it, and the terminal route that works
        meanwhile — a round that cannot be delivered is not a round that
        cannot be answered."""
        root = _repo(tmp_path / "app")

        out = _init(root, "--yes", *self._with_a_board(tmp_path))

        assert "rite credential set slack" in out
        assert "rite replies" in out
        assert "rite refine answer" in out

    def test_a_project_with_a_slack_owner_gets_no_row(self, tmp_path):
        """⚠ The control for this row."""
        root = _repo(tmp_path / "app")
        out = _init(root, "--yes", *self._with_a_board(tmp_path))
        assert "slack.owner_user" in out  # before

        from rite_ai.config.models import (
            ProjectConfig,
            SlackConfig,
            TicketBackendConfig,
        )

        rows = setup.what_is_missing(
            root,
            _answers(
                ProjectConfig(
                    ticket_backend=TicketBackendConfig(type="jira"),
                    slack=SlackConfig(owner_user="U123"),
                )
            ),
            "w1",
        )

        assert not [r for r in rows if "slack.owner_user" in r]

    def test_a_channel_route_is_not_judged_by_the_dm_field(self, tmp_path):
        """`questions_to: channel` does not need `slack.owner_user`, so the
        row would be a false accusation."""
        from rite_ai.config.models import (
            ProjectConfig,
            RefinementConfig,
            TicketBackendConfig,
        )

        rows = setup.what_is_missing(
            tmp_path,
            _answers(
                ProjectConfig(
                    ticket_backend=TicketBackendConfig(type="jira"),
                    refinement=RefinementConfig(questions_to="channel", channel="C1"),
                )
            ),
            "w1",
        )

        assert not [r for r in rows if "slack.owner_user" in r]

    def test_a_boardless_project_is_not_also_accused_of_this(self, tmp_path):
        """Following `rite doctor`'s own rule: a project with no board refines
        nothing, so only the board row is printed. Two rows describing one
        unconfigured project is how a row stops being read."""
        from rite_ai.config.models import ProjectConfig

        rows = setup.what_is_missing(tmp_path, _answers(ProjectConfig()), "w1")

        assert [r for r in rows if "no ticket board" in r]
        assert not [r for r in rows if "slack.owner_user" in r]


def _answers(config):
    """The two fields `what_is_missing` reads, with a module and a Manager
    already in place so only the rows under test can appear."""
    from dataclasses import dataclass, field

    from rite_ai.config.models import Module

    @dataclass
    class _A:
        config: object
        modules: list = field(default_factory=lambda: [Module(name="m", path="m/")])

    config.coordination.managers = ["lead"]
    return _A(config=config)
