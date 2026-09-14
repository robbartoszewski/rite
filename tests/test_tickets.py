import json
from unittest.mock import MagicMock, patch

from rite_ai.config.models import TicketBackendConfig
from rite_ai.tickets import create_backend, create_backend_from_config
from rite_ai.tickets.github import GitHubBackend, _issue_to_ticket, _parse_iso
from rite_ai.tickets.interface import BackendError, Ticket, TicketFilter
from rite_ai.tickets.jira import JiraBackend


def _mock_proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    proc.stderr = stderr
    return proc


class TestIssueToTicket:
    def test_parses_gh_json(self):
        data = {
            "number": 42,
            "title": "Fix the bug",
            "state": "open",
            "body": "description here",
            "labels": [{"name": "bug"}, {"name": "urgent"}],
            "assignees": [{"login": "alice"}],
            "url": "https://github.com/org/repo/issues/42",
            "createdAt": "2026-01-15T10:00:00Z",
            "updatedAt": "2026-01-16T12:00:00Z",
        }
        ticket = _issue_to_ticket(data)
        assert ticket.id == "42"
        assert ticket.title == "Fix the bug"
        assert ticket.status == "open"
        assert ticket.assignee == "alice"
        assert ticket.labels == ["bug", "urgent"]
        assert ticket.created_at is not None

    def test_handles_empty_assignees(self):
        data = {"number": 1, "title": "x", "assignees": [], "labels": []}
        ticket = _issue_to_ticket(data)
        assert ticket.assignee == ""

    def test_handles_missing_fields(self):
        ticket = _issue_to_ticket({})
        assert ticket.id == ""
        assert ticket.title == ""


class TestParseIso:
    def test_parses_z_suffix(self):
        dt = _parse_iso("2026-01-15T10:00:00Z")
        assert dt is not None
        assert dt.year == 2026

    def test_returns_none_for_empty(self):
        assert _parse_iso("") is None

    def test_returns_none_for_invalid(self):
        assert _parse_iso("not a date") is None


class TestGitHubBackendCreate:
    @patch("rite_ai.tickets.github._run_gh")
    def test_create_returns_ticket(self, mock_gh):
        mock_gh.return_value = "https://github.com/org/repo/issues/99\n"
        backend = GitHubBackend("org/repo")
        result = backend.create(
            "New feature", description="desc", labels=["enhancement"]
        )
        assert isinstance(result, Ticket)
        assert result.id == "99"
        assert result.title == "New feature"

    @patch("rite_ai.tickets.github._run_gh")
    def test_create_propagates_error(self, mock_gh):
        mock_gh.return_value = BackendError("auth failed")
        backend = GitHubBackend("org/repo")
        result = backend.create("Fail")
        assert isinstance(result, BackendError)


class TestGitHubBackendRead:
    @patch("rite_ai.tickets.github._run_gh")
    def test_read_parses_json(self, mock_gh):
        data = {
            "number": 42,
            "title": "A ticket",
            "state": "open",
            "body": "stuff",
            "labels": [],
            "assignees": [],
            "url": "https://github.com/org/repo/issues/42",
            "createdAt": "",
            "updatedAt": "",
        }
        mock_gh.return_value = json.dumps(data)
        backend = GitHubBackend("org/repo")
        result = backend.read("42")
        assert isinstance(result, Ticket)
        assert result.id == "42"


class TestGitHubBackendList:
    @patch("rite_ai.tickets.github._run_gh")
    def test_list_with_filters(self, mock_gh):
        mock_gh.return_value = json.dumps(
            [
                {
                    "number": 1,
                    "title": "a",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "assignees": [],
                    "body": "",
                    "url": "",
                    "createdAt": "",
                    "updatedAt": "",
                },
            ]
        )
        backend = GitHubBackend("org/repo")
        result = backend.list_tickets(TicketFilter(label="bug"))
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("rite_ai.tickets.github._run_gh")
    def test_list_no_filters(self, mock_gh):
        mock_gh.return_value = "[]"
        backend = GitHubBackend("org/repo")
        result = backend.list_tickets()
        assert result == []


class TestGitHubBackendOperations:
    @patch("rite_ai.tickets.github._run_gh")
    def test_comment(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.comment("42", "hello")
        assert result is None

    @patch("rite_ai.tickets.github._run_gh")
    def test_move_close(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.move("42", "closed")
        assert result is None
        args = mock_gh.call_args[0][0]
        assert args[:2] == ["issue", "close"]

    @patch("rite_ai.tickets.github._run_gh")
    def test_move_reopen(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.move("42", "open")
        assert result is None
        args = mock_gh.call_args[0][0]
        assert args[:2] == ["issue", "reopen"]

    @patch("rite_ai.tickets.github._run_gh")
    def test_move_to_rite_column_name_reopens_correctly(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.move("42", "In Progress")
        # Not None: GitHub has no "In Progress", so `move` reports the
        # state the board actually holds. See the regression test below.
        assert result == "open"
        args = mock_gh.call_args[0][0]
        assert args[:2] == ["issue", "reopen"]

    @patch("rite_ai.tickets.github._run_gh")
    def test_move_to_unrecognised_status_refuses(self, mock_gh):
        backend = GitHubBackend("org/repo")
        result = backend.move("42", "Ready for testing")
        assert isinstance(result, BackendError)
        mock_gh.assert_not_called()

    def test_link_returns_backend_error(self):
        backend = GitHubBackend("org/repo")
        result = backend.link("42", "43", "Blocks")
        assert isinstance(result, BackendError)

    @patch("rite_ai.tickets.github._run_gh")
    def test_assign(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.assign("42", "alice")
        assert result is None

    @patch("rite_ai.tickets.github._run_gh")
    def test_label(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.label("42", ["bug", "urgent"])
        assert result is None

    @patch("rite_ai.tickets.github._run_gh")
    def test_update(self, mock_gh):
        mock_gh.return_value = ""
        backend = GitHubBackend("org/repo")
        result = backend.update("42", title="New title")
        assert result is None

    @patch("rite_ai.tickets.github._run_gh")
    def test_query(self, mock_gh):
        mock_gh.return_value = "[]"
        backend = GitHubBackend("org/repo")
        result = backend.query("label:bug is:open")
        assert result == []


class TestCreateBackendProjectRouting:
    """§8.3's `projects: {board, workers, testing}` dict must route by role,
    not collapse to one project — SPEC §6.2's board structure needs a
    worker ticket created on `workers` and linked to a ticket on `board`."""

    @patch("rite_ai.credentials.store.get_scoped")
    def test_projects_dict_routes_by_board_role(self, mock_get):
        mock_get.side_effect = lambda name, credentials=None: {
            "jira_email": "user@test.com",
            "jira_token": "tok",
        }.get(name)
        backend = create_backend(
            "jira",
            site="test.atlassian.net",
            projects={"board": "XYZ", "workers": "ABC", "testing": "TEST"},
            board_role="workers",
        )
        assert isinstance(backend, JiraBackend)
        assert backend.config.project_key == "ABC"

    @patch("rite_ai.credentials.store.get_scoped")
    def test_projects_dict_board_role_selects_board_project(self, mock_get):
        mock_get.side_effect = lambda name, credentials=None: {
            "jira_email": "user@test.com",
            "jira_token": "tok",
        }.get(name)
        backend = create_backend(
            "jira",
            site="test.atlassian.net",
            projects={"board": "XYZ", "workers": "ABC", "testing": "TEST"},
            board_role="board",
        )
        assert isinstance(backend, JiraBackend)
        assert backend.config.project_key == "XYZ"

    @patch("rite_ai.credentials.store.get_scoped")
    def test_bare_project_key_still_works_without_projects_dict(self, mock_get):
        mock_get.side_effect = lambda name, credentials=None: {
            "jira_email": "user@test.com",
            "jira_token": "tok",
        }.get(name)
        backend = create_backend("jira", site="test.atlassian.net", project_key="SOLO")
        assert isinstance(backend, JiraBackend)
        assert backend.config.project_key == "SOLO"

    @patch("rite_ai.credentials.store.get_scoped")
    def test_missing_board_role_with_no_fallback_gives_a_clear_error(self, mock_get):
        """A `board_role` not present in `projects`, with no bare
        `project_key` to fall back to, must say WHICH role is missing —
        not surface JIRA's generic "project_key required" error, which
        gives no clue that the real problem is a config.yaml typo."""
        mock_get.side_effect = lambda name, credentials=None: {
            "jira_email": "user@test.com",
            "jira_token": "tok",
        }.get(name)
        result = create_backend(
            "jira",
            site="test.atlassian.net",
            projects={"board": "XYZ", "workers": "ABC"},
            board_role="testing",
        )
        assert isinstance(result, BackendError)
        assert "testing" in result.message

    @patch("rite_ai.credentials.store.get_scoped")
    def test_missing_board_role_with_empty_projects_dict_still_errors_clearly(
        self, mock_get
    ):
        """The realistic default: `ticket_backend.projects` absent from
        config.yaml parses to `{}` (TicketBackendConfig's own default) —
        this must NOT silently skip the role check and fall through to
        JIRA's generic 'project_key required' error."""
        mock_get.side_effect = lambda name, credentials=None: {
            "jira_email": "user@test.com",
            "jira_token": "tok",
        }.get(name)
        result = create_backend(
            "jira", site="test.atlassian.net", projects={}, board_role="workers"
        )
        assert isinstance(result, BackendError)
        assert "workers" in result.message


class TestCreateBackendFromConfig:
    """The single builder both `_ticket_backend` (CLI) and
    `_build_ticket_backend` (lifecycle) now share, instead of each hand-
    copying `create_backend`'s field list — closing the exact duplication
    the pre-push hook consolidation fixed elsewhere in this same change."""

    def test_github_config_builds_a_github_backend(self):
        tb = TicketBackendConfig(type="github", repo="acme/widgets")
        backend = create_backend_from_config(tb)
        assert isinstance(backend, GitHubBackend)
        assert backend.repo == "acme/widgets"

    def test_github_config_with_no_repo_gives_a_clear_error(self):
        tb = TicketBackendConfig(type="github")
        result = create_backend_from_config(tb)
        assert isinstance(result, BackendError)
        assert "repo" in result.message

    @patch("rite_ai.credentials.store.get_scoped")
    def test_jira_config_routes_by_role(self, mock_get):
        mock_get.side_effect = lambda name, credentials=None: {
            "jira_email": "user@test.com",
            "jira_token": "tok",
        }.get(name)
        tb = TicketBackendConfig(
            type="jira",
            site="test.atlassian.net",
            projects={"workers": "ABC", "board": "XYZ"},
        )
        backend = create_backend_from_config(tb, board_role="board")
        assert isinstance(backend, JiraBackend)
        assert backend.config.project_key == "XYZ"
