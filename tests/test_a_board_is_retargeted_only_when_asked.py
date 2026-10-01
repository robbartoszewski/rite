"""SCRUM-8 / C5: `rite credential set` offers to move the board, and never
moves it unasked.

`credential set` set `ticket_backend.type` only when it was `none`. A project
already on another board got a note telling the user to *"Set it to 'jira' in
.rite/config.yaml"* — the one path left in setup that says "edit the file" for
something rite has a command for.

The refusal to retarget SILENTLY was right. Refusing to retarget at all was
not. So it asks, **default No** — a retarget changes which board every ticket
command reads, and a mistyped service name must not move it — and with nobody
attached it does not ask and does not move, which is S23's lesson in another
place.

⚠ **Only `github` → `jira` is reachable today**, because `github` carries no
`board_type` and no `config_path` (D1, deferred), so setting a GitHub
credential never reaches this branch. The field-clearing is asserted in both
directions at the function that does it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

import rite_ai.sandbox as sb
from rite_ai.cli.main import cli
from rite_ai.config.models import TicketBackendConfig, leave_the_old_board
from rite_ai.config.parse import parse_config

# site, email, token (+confirm), project key
JIRA = "team.atlassian.net\nsomeone@example.com\nSEKRIT\nSEKRIT\nKAN\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "rite-home"))


@pytest.fixture
def at_a_terminal(monkeypatch):
    """Somebody is there to be asked. ⚠ Needed because what is under test is
    GUARDED by whether anyone is, and a test that answers the question is by
    construction not the case the guard is for."""
    monkeypatch.setattr("rite_ai.cli.module_docs.somebody_is_there", lambda: True)


def _on_github(tmp_path: Path, monkeypatch) -> Path:
    """An initialised project whose board is GitHub, with a repo recorded."""
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    assert CliRunner().invoke(cli, ["init", "--yes"]).exit_code == 0
    path = root / ".rite" / "config.yaml"
    path.write_text(
        path.read_text()
        .replace("  type: none\n", "  type: github\n", 1)
        .replace("  repo: ''\n", "  repo: acme/app\n", 1)
    )
    tb = _config(root).ticket_backend
    assert (tb.type, tb.repo) == ("github", "acme/app")
    return root


def _config(root: Path):
    return parse_config(root / ".rite" / "config.yaml")


def _set_jira(answer: str = ""):
    return CliRunner().invoke(cli, ["credential", "set", "jira"], input=JIRA + answer)


class TestItAsksRatherThanRefusing:
    def test_the_question_names_both_boards_and_what_would_change(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        _on_github(tmp_path, monkeypatch)

        out = _set_jira("n\n").output

        assert "this project's board is 'github'" in out
        assert "Point it at 'jira' instead?" in out
        assert "Every ticket command would then read jira" in out

    def test_it_never_tells_anyone_to_edit_the_file(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """What this replaces, in its own words. (The summary of what WAS
        recorded names config.yaml too, which is a different sentence.)"""
        _on_github(tmp_path, monkeypatch)

        out = _set_jira("n\n").output

        assert "Set it to" not in out
        assert "to use jira instead" not in out

    def test_yes_moves_the_board(self, tmp_path, monkeypatch, at_a_terminal):
        root = _on_github(tmp_path, monkeypatch)

        result = _set_jira("y\n")

        assert result.exit_code == 0, result.output
        assert _config(root).ticket_backend.type == "jira"

    def test_and_says_what_it_wrote(self, tmp_path, monkeypatch, at_a_terminal):
        _on_github(tmp_path, monkeypatch)

        out = _set_jira("y\n").output

        assert "ticket_backend.type" in out
        assert "jira" in out

    def test_the_board_rite_then_builds_is_the_new_one(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """The consequence, not only the key."""
        from rite_ai.cli.main import _board_for_manager
        from rite_ai.tickets.jira import JiraBackend
        from rite_ai.tickets.scope import unwrapped

        root = _on_github(tmp_path, monkeypatch)
        _set_jira("y\n")

        board, state, _problem, _record = _board_for_manager(root)

        assert state == "ok"
        assert isinstance(unwrapped(board), JiraBackend)


class TestNoIsTheDefault:
    """🔴 A retarget changes which board every ticket command reads."""

    def test_pressing_enter_declines(self, tmp_path, monkeypatch, at_a_terminal):
        root = _on_github(tmp_path, monkeypatch)

        result = _set_jira("\n")

        assert result.exit_code == 0, result.output
        assert _config(root).ticket_backend.type == "github"

    def test_no_leaves_the_board_alone(self, tmp_path, monkeypatch, at_a_terminal):
        root = _on_github(tmp_path, monkeypatch)

        _set_jira("n\n")

        tb = _config(root).ticket_backend
        assert (tb.type, tb.repo) == ("github", "acme/app")

    def test_and_says_which_board_is_still_being_read(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        _on_github(tmp_path, monkeypatch)

        out = _set_jira("n\n").output

        assert "rite still reads the github board" in out

    def test_what_was_just_entered_is_still_recorded(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """Declining the retarget does not throw away the credential and site
        the user just typed — nothing reads them while the board is GitHub,
        and saying yes next time is then all it takes."""
        root = _on_github(tmp_path, monkeypatch)

        out = _set_jira("n\n").output

        assert _config(root).ticket_backend.site == "team.atlassian.net"
        assert "is recorded" in out


class TestWithNobodyThereItDoesNotMove:
    """🔴 `credential set` runs unattended — the fields can come down a pipe —
    and silently moving which board a project reads is not a thing to do to
    somebody who is not watching."""

    def test_it_is_not_asked(self, tmp_path, monkeypatch):
        _on_github(tmp_path, monkeypatch)

        out = _set_jira().output

        assert "Point it at" not in out

    def test_the_board_is_unchanged(self, tmp_path, monkeypatch):
        root = _on_github(tmp_path, monkeypatch)

        _set_jira()

        assert _config(root).ticket_backend.type == "github"

    def test_and_the_note_is_still_printed(self, tmp_path, monkeypatch):
        """Today's message, which is exactly right for this case."""
        _on_github(tmp_path, monkeypatch)

        out = _set_jira().output

        assert "ticket_backend.type is 'github'" in out
        assert "re-run this with a terminal" in out


class TestTheOutgoingBoardsFieldsGo:
    """A `config.yaml` describing two boards invites a reader to believe a
    `site:` that nothing uses. The INCOMING board re-prompts for everything it
    needs in the same run, so only the leftovers are stale."""

    def test_the_old_repo_is_cleared_on_the_way_to_jira(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        root = _on_github(tmp_path, monkeypatch)

        _set_jira("y\n")

        assert _config(root).ticket_backend.repo == ""

    def test_and_it_is_named_rather_than_done_quietly(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        _on_github(tmp_path, monkeypatch)

        out = _set_jira("y\n").output

        assert "ticket_backend.repo" in out
        assert "(cleared)" in out

    def test_what_the_new_board_needs_survives(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """⚠ The control. Clearing everything would pass the two above."""
        root = _on_github(tmp_path, monkeypatch)

        _set_jira("y\n")

        tb = _config(root).ticket_backend
        assert tb.site == "team.atlassian.net"
        assert tb.projects.get("workers") == "KAN"

    def test_the_scope_label_survives_a_change_of_board(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """It marks a ticket as THIS project's and is backend-agnostic
        (§6.1.1)."""
        root = _on_github(tmp_path, monkeypatch)
        before = _config(root).ticket_backend.scope_label
        assert before

        _set_jira("y\n")

        assert _config(root).ticket_backend.scope_label == before

    def test_jira_to_github_clears_the_site_and_the_project_key(self):
        """The direction `credential set` cannot reach today (D1), asserted at
        the function that does the clearing so it is right when it can."""
        backend = TicketBackendConfig(
            type="jira",
            site="team.atlassian.net",
            projects={"workers": "KAN"},
            credential="jira_token",
            scope_label="keep-me",
        )

        cleared = leave_the_old_board(backend, "github")

        assert backend.site == ""
        assert backend.projects == {}
        assert set(cleared) == {
            "ticket_backend.site",
            "ticket_backend.projects",
            "ticket_backend.credential",
        }
        assert backend.scope_label == "keep-me"

    def test_the_renamed_token_key_goes_with_the_old_board(self):
        """`credential` RENAMES the outgoing board's token key, so it is that
        board's field. Cleared rather than replaced: an empty one falls back to
        the new board's default key, and inventing a name is a separate
        question."""
        backend = TicketBackendConfig(type="github", repo="a/b", credential="gh_token")

        leave_the_old_board(backend, "jira")

        assert backend.credential == ""

    def test_a_field_already_empty_is_not_reported_as_cleared(self):
        """A summary listing what did not change is a summary nobody reads."""
        backend = TicketBackendConfig(type="github")

        assert leave_the_old_board(backend, "jira") == []


class TestTheNoBoardPathIsUntouched:
    def test_a_project_with_no_board_is_not_asked_anything(
        self, tmp_path, monkeypatch, at_a_terminal
    ):
        """⚠ The control for the whole file: `none` → set, with no question,
        is the a4 behaviour this must not disturb."""
        root = tmp_path / "fresh"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        monkeypatch.chdir(root)
        CliRunner().invoke(cli, ["init", "--yes"])

        out = _set_jira().output

        assert "Point it at" not in out
        assert _config(root).ticket_backend.type == "jira"
