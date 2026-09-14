from unittest.mock import MagicMock, patch

import httpx

from rite_ai.tickets.interface import BackendError, Ticket, TicketFilter
from rite_ai.tickets.jira import JiraBackend, JiraConfig, _issue_to_ticket


def _make_config(**overrides):
    defaults = {
        "site": "test.atlassian.net",
        "email": "user@test.com",
        "token": "test-token",
        "project_key": "TEST",
    }
    defaults.update(overrides)
    return JiraConfig(**defaults)


def _make_issue(
    key="TEST-1",
    summary="Test issue",
    status="To Do",
    labels=None,
    assignee_name=None,
):
    fields = {
        "summary": summary,
        "status": {"name": status},
        "labels": labels or [],
        "description": "A test issue",
        "created": "2026-01-15T10:00:00.000+0000",
        "updated": "2026-01-15T12:00:00.000+0000",
    }
    if assignee_name:
        fields["assignee"] = {
            "displayName": assignee_name,
            "accountId": "abc123",
        }
    else:
        fields["assignee"] = None
    url = f"https://test.atlassian.net/rest/api/3/issue/{key}"
    return {"key": key, "self": url, "fields": fields}


class TestIssueToTicket:
    def test_parses_full_issue(self):
        data = _make_issue(
            key="PROJ-42",
            summary="Fix bug",
            status="In Progress",
            labels=["scheduled", "urgent"],
            assignee_name="Alice",
        )
        ticket = _issue_to_ticket(data)
        assert ticket.id == "PROJ-42"
        assert ticket.title == "Fix bug"
        assert ticket.status == "In Progress"
        assert ticket.assignee == "Alice"
        assert ticket.labels == ["scheduled", "urgent"]
        assert ticket.created_at is not None
        assert ticket.updated_at is not None

    def test_handles_no_assignee(self):
        data = _make_issue(assignee_name=None)
        ticket = _issue_to_ticket(data)
        assert ticket.assignee == ""

    def test_handles_missing_fields(self):
        ticket = _issue_to_ticket({"key": "X-1", "fields": {}})
        assert ticket.id == "X-1"
        assert ticket.title == ""
        assert ticket.status == ""

    def test_handles_empty_dict(self):
        ticket = _issue_to_ticket({})
        assert ticket.id == ""


class TestJiraBackendCreate:
    @patch.object(JiraBackend, "_request")
    def test_create_returns_ticket(self, mock_req):
        mock_req.return_value = {"key": "TEST-99"}
        backend = JiraBackend(_make_config())
        result = backend.create(
            "New feature", description="desc", labels=["enhancement"]
        )
        assert isinstance(result, Ticket)
        assert result.id == "TEST-99"
        assert result.title == "New feature"
        assert "test.atlassian.net/browse/TEST-99" in result.url

    @patch.object(JiraBackend, "_request")
    def test_create_propagates_error(self, mock_req):
        mock_req.return_value = BackendError("auth failed")
        backend = JiraBackend(_make_config())
        result = backend.create("Title")
        assert isinstance(result, BackendError)

    def test_create_requires_project_key(self):
        backend = JiraBackend(_make_config(project_key=None))
        result = backend.create("Title")
        assert isinstance(result, BackendError)
        assert "project_key" in result.message

    @patch.object(JiraBackend, "_request")
    def test_create_sends_adf_description(self, mock_req):
        mock_req.return_value = {"key": "TEST-1"}
        backend = JiraBackend(_make_config())
        backend.create("Title", description="Some text")
        call_args = mock_req.call_args
        payload = call_args[1]["json"]
        desc = payload["fields"]["description"]
        assert desc["type"] == "doc"
        assert desc["content"][0]["content"][0]["text"] == "Some text"


class TestJiraBackendRead:
    @patch.object(JiraBackend, "_request")
    def test_read_returns_ticket(self, mock_req):
        mock_req.return_value = _make_issue(key="TEST-5", summary="Read me")
        backend = JiraBackend(_make_config())
        result = backend.read("TEST-5")
        assert isinstance(result, Ticket)
        assert result.id == "TEST-5"
        assert result.title == "Read me"
        assert "browse/TEST-5" in result.url


class TestJiraBackendMove:
    @patch.object(JiraBackend, "_request")
    def test_move_finds_transition(self, mock_req):
        mock_req.side_effect = [
            {
                "transitions": [
                    {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}},
                    {"id": "41", "name": "Done", "to": {"name": "Done"}},
                ]
            },
            {},
        ]
        backend = JiraBackend(_make_config())
        result = backend.move("TEST-1", "Done")
        assert result is None
        post_call = mock_req.call_args_list[1]
        assert post_call[1]["json"]["transition"]["id"] == "41"

    @patch.object(JiraBackend, "_request")
    def test_move_no_matching_transition(self, mock_req):
        mock_req.return_value = {
            "transitions": [
                {"id": "31", "name": "In Progress", "to": {"name": "In Progress"}},
            ]
        }
        backend = JiraBackend(_make_config())
        result = backend.move("TEST-1", "Nonexistent")
        assert isinstance(result, BackendError)
        assert "no transition" in result.message

    @patch.object(JiraBackend, "_request")
    def test_move_matches_case_insensitive(self, mock_req):
        mock_req.side_effect = [
            {
                "transitions": [
                    {"id": "41", "name": "done", "to": {"name": "Done"}},
                ]
            },
            {},
        ]
        backend = JiraBackend(_make_config())
        result = backend.move("TEST-1", "Done")
        assert result is None


class TestJiraBackendList:
    @patch.object(JiraBackend, "_request")
    def test_list_with_filters(self, mock_req):
        mock_req.return_value = {
            "issues": [_make_issue(key="TEST-1"), _make_issue(key="TEST-2")]
        }
        backend = JiraBackend(_make_config())
        result = backend.list_tickets(TicketFilter(status="To Do", label="scheduled"))
        assert isinstance(result, list)
        assert len(result) == 2
        params = mock_req.call_args[1]["params"]
        assert 'status = "To Do"' in params["jql"]
        assert 'labels = "scheduled"' in params["jql"]

    @patch.object(JiraBackend, "_request")
    def test_list_no_filters(self, mock_req):
        mock_req.return_value = {"issues": []}
        backend = JiraBackend(_make_config())
        result = backend.list_tickets()
        assert isinstance(result, list)
        assert len(result) == 0


class TestJiraBackendOperations:
    @patch.object(JiraBackend, "_request")
    def test_comment(self, mock_req):
        mock_req.return_value = {}
        backend = JiraBackend(_make_config())
        result = backend.comment("TEST-1", "Hello from rite")
        assert result is None
        payload = mock_req.call_args[1]["json"]
        assert payload["body"]["type"] == "doc"

    @patch.object(JiraBackend, "_request")
    def test_assign(self, mock_req):
        mock_req.return_value = {}
        backend = JiraBackend(_make_config())
        # A real accountId — 24 hex characters — goes through untouched.
        result = backend.assign("TEST-1", "6054dede37065a0069a7c74f")
        assert result is None
        payload = mock_req.call_args[1]["json"]
        assert payload["accountId"] == "6054dede37065a0069a7c74f"

    @patch.object(JiraBackend, "_request")
    def test_label(self, mock_req):
        mock_req.return_value = {}
        backend = JiraBackend(_make_config())
        result = backend.label("TEST-1", ["scheduled", "alpha"])
        assert result is None
        payload = mock_req.call_args[1]["json"]
        assert len(payload["update"]["labels"]) == 2

    @patch.object(JiraBackend, "_request")
    def test_link(self, mock_req):
        mock_req.return_value = {}
        backend = JiraBackend(_make_config())
        result = backend.link("ABC-1", "XYZ-1", "Blocks")
        assert result is None
        method, path = mock_req.call_args[0][:2]
        assert method == "POST"
        assert path == "/issueLink"
        payload = mock_req.call_args[1]["json"]
        assert payload["type"]["name"] == "Blocks"
        # The TARGET is the inward issue. Measured against a live JIRA,
        # `POST /issueLink` reads "inwardIssue <type.outward> outwardIssue"
        # — see the regression test below for what the old order did.
        assert payload["inwardIssue"]["key"] == "XYZ-1"
        assert payload["outwardIssue"]["key"] == "ABC-1"

    @patch.object(JiraBackend, "_request")
    def test_link_propagates_error(self, mock_req):
        mock_req.return_value = BackendError("JIRA 400")
        backend = JiraBackend(_make_config())
        result = backend.link("ABC-1", "XYZ-1", "Blocks")
        assert isinstance(result, BackendError)

    @patch.object(JiraBackend, "_request")
    def test_update_title(self, mock_req):
        mock_req.return_value = {}
        backend = JiraBackend(_make_config())
        result = backend.update("TEST-1", title="New title")
        assert result is None
        payload = mock_req.call_args[1]["json"]
        assert payload["fields"]["summary"] == "New title"

    @patch.object(JiraBackend, "_request")
    def test_update_no_fields_is_noop(self, mock_req):
        backend = JiraBackend(_make_config())
        result = backend.update("TEST-1")
        assert result is None
        mock_req.assert_not_called()

    @patch.object(JiraBackend, "_request")
    def test_query_passes_jql(self, mock_req):
        mock_req.return_value = {"issues": [_make_issue(key="TEST-3")]}
        backend = JiraBackend(_make_config())
        result = backend.query('project = TEST AND labels = "scheduled"')
        assert isinstance(result, list)
        assert len(result) == 1
        params = mock_req.call_args[1]["params"]
        assert params["jql"] == 'project = TEST AND labels = "scheduled"'


class TestJiraBackendHttp:
    def test_request_handles_http_error(self):
        backend = JiraBackend(_make_config())
        backend._client = MagicMock()
        backend._client.request.side_effect = httpx.ConnectError("down")
        result = backend._request("GET", "/issue/X-1")
        assert isinstance(result, BackendError)
        assert "failed" in result.message
        # The URL and the setting that produced it: errno text alone gave no
        # way to tell a site that is down from a mistyped `ticket_backend.site`.
        assert "https://test.atlassian.net/rest/api/3/issue/X-1" in result.message
        assert "ticket_backend.site" in result.message

    def test_request_handles_4xx(self):
        backend = JiraBackend(_make_config())
        backend._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        backend._client.request.return_value = mock_resp
        result = backend._request("GET", "/issue/X-1")
        assert isinstance(result, BackendError)
        assert "401" in result.message

    def test_request_handles_204_no_content(self):
        backend = JiraBackend(_make_config())
        backend._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.content = b""
        backend._client.request.return_value = mock_resp
        result = backend._request("PUT", "/issue/X-1")
        assert result == {}
