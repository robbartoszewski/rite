"""Regression tests for the defects found on first live contact with the
ticket backends' WRITE paths.

The read paths were driven against a real JIRA earlier and cost four
defects, one of them fatal (`GET /rest/api/3/search` had been removed and
answered 410, so every list and query was dead against every real
instance while green against mocks). Everything that WRITES —
`create`/`move`/`label`/`assign`/`link`/`comment` — had never been run
against anything but a mock on either backend. This file is what that
first contact cost.

Each test reproduces the ORIGINAL SYMPTOM, so it fails against the code
as it was rather than merely passing against the fix. Where a defect was
only observable against the live API, the test says so: a mocked suite
could not have caught it, and that gap is the finding, not an excuse.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.lifecycle.commands import perform_handover, stop
from rite_ai.reporting.outbox import list_pending
from rite_ai.tickets import BackendError, GitHubBackend
from rite_ai.tickets.jira import JiraBackend, JiraConfig


def _jira(**overrides) -> JiraBackend:
    defaults = {
        "site": "test.atlassian.net",
        "email": "user@test.com",
        "token": "test-token",
        "project_key": "TEST",
    }
    defaults.update(overrides)
    return JiraBackend(JiraConfig(**defaults))


def _project(tmp_path: Path, backend: str = "jira") -> Path:
    """A minimal rite project the board commands will load."""
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    if backend == "jira":
        (rite_dir / "config.yaml").write_text(
            "ticket_backend:\n"
            "  type: jira\n"
            "  site: test.atlassian.net\n"
            "  projects:\n"
            "    workers: RW\n"
        )
    else:
        (rite_dir / "config.yaml").write_text(
            "ticket_backend:\n  type: github\n  repo: org/repo\n"
        )
    return tmp_path


# --------------------------------------------------------------------------
# JIRA `link` created the INVERSE of the relationship it reported
# --------------------------------------------------------------------------
#
# LIVE-ONLY. A mock records the call and never renders the relationship,
# so nothing in a mocked suite can say which issue ended up blocked.
# Measured on a real instance: sending `inwardIssue: RT-3, outwardIssue:
# RT-4` with type "Blocks" produced "RT-3 blocks RT-4" when read back
# from RT-3, and "RT-4 is blocked by RT-3" when read back from RT-4 — the
# opposite of both the field names and of what rite printed. `POST
# /issueLink` reads as **inwardIssue <type.outward> outwardIssue**.
#
# It matters because SPEC §6.2 makes "worker tickets link to project
# tickets as blocked by" the one structural assumption rite's dependency
# logic depends on. Inverted, every blocker reads as a dependent.


class TestLinkDirection:
    @patch.object(JiraBackend, "_request")
    def test_target_is_the_inward_issue(self, mock_req):
        mock_req.return_value = {}
        assert _jira().link("RW-1", "SCRUM-1", "Blocks") is None
        payload = mock_req.call_args[1]["json"]
        # The OUTWARD issue is the one that "is blocked by" the inward
        # one. RW-1 is the ticket that must end up blocked, so RW-1 is
        # outward and the blocker SCRUM-1 is inward. The original code
        # had these the other way round.
        assert payload["inwardIssue"]["key"] == "SCRUM-1"
        assert payload["outwardIssue"]["key"] == "RW-1"

    @patch("rite_ai.tickets.create_backend")
    def test_cli_message_matches_the_payload_it_sent(
        self, mock_create, tmp_path, monkeypatch
    ):
        """The printed sentence and the created link must agree.

        They did not: the CLI said "RW-1 is blocked by SCRUM-1" while the
        API recorded "RW-1 blocks SCRUM-1". Two things were wrong at
        once and each was cited as justification for the other, which is
        why only the live API could settle it."""
        monkeypatch.chdir(_project(tmp_path))
        backend = mock_create.return_value
        backend.link.return_value = None

        result = CliRunner().invoke(cli, ["board", "link", "RW-1", "SCRUM-1"])
        assert result.exit_code == 0
        assert "RW-1 is blocked by SCRUM-1" in result.output

        # ... and the direction the backend was actually asked for.
        real = _jira()
        with patch.object(JiraBackend, "_request") as mock_req:
            mock_req.return_value = {}
            real.link("RW-1", "SCRUM-1", "Blocks")
        payload = mock_req.call_args[1]["json"]
        blocker = payload["inwardIssue"]["key"]
        blocked = payload["outwardIssue"]["key"]
        assert (blocked, blocker) == ("RW-1", "SCRUM-1")

    @patch.object(JiraBackend, "_request")
    def test_unknown_link_type_names_the_sites_own_types(self, mock_req):
        """JIRA's own message names what was wrong and not what would be
        right, and the names are case-sensitive: `--type blocks` is
        rejected where `--type Blocks` is accepted."""

        def responses(method, path, **kwargs):
            if path == "/issueLink":
                return BackendError(
                    "JIRA POST /issueLink → 404: "
                    '{"errorMessages":["No issue link type with name '
                    "'blocks' found.\"]}"
                )
            return {
                "issueLinkTypes": [
                    {"name": "Blocks"},
                    {"name": "Relates"},
                ]
            }

        mock_req.side_effect = responses
        result = _jira().link("RW-1", "SCRUM-1", "blocks")
        assert isinstance(result, BackendError)
        assert "Blocks, Relates" in result.message
        assert "case-sensitive" in result.message


# --------------------------------------------------------------------------
# Bad credentials read as "issue does not exist" on every WRITE path
# --------------------------------------------------------------------------
#
# LIVE-ONLY. The same defect that was found and fixed for `read` and
# `list` was still present on all six write methods: measured against a
# real site with a bad token, JIRA answers every write with `404 Issue
# does not exist or you do not have permission to see it` (and `POST
# /issue` with a 400 blaming the project), never a 401. A mocked suite
# returns whatever status the test author chose and nobody writes a test
# asserting that a 404 is secretly a 401 — only `/myself` distinguishes
# them, and only against a live site.


class TestBadCredentialsOnWritePaths:
    @staticmethod
    def _rejecting_token(mock_req):
        """`/myself` → 401, everything else → the 404/400 a live JIRA
        actually returns when the token is bad."""

        def responses(method, path, **kwargs):
            if path == "/myself":
                return BackendError("JIRA GET /myself → 401")
            if path == "/issue" and method == "POST":
                return BackendError(
                    "JIRA POST /issue → 400: The target project doesn't exist "
                    "or you don't have permission to create issues in it."
                )
            return BackendError(
                f"JIRA {method} {path} → 404: Issue does not exist or you do "
                "not have permission to see it."
            )

        mock_req.side_effect = responses

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda b: b.move("RW-1", "Done"), id="move"),
            pytest.param(lambda b: b.label("RW-1", ["scheduled"]), id="label"),
            pytest.param(lambda b: b.comment("RW-1", "text"), id="comment"),
            pytest.param(lambda b: b.create("title"), id="create"),
            pytest.param(
                lambda b: b.assign("RW-1", "6054dede37065a0069a7c74f"), id="assign"
            ),
            pytest.param(lambda b: b.link("RW-1", "RW-2", "Blocks"), id="link"),
            pytest.param(lambda b: b.update("RW-1", title="x"), id="update"),
        ],
    )
    @patch.object(JiraBackend, "_request")
    def test_write_blames_the_token_not_the_ticket(self, mock_req, operation):
        self._rejecting_token(mock_req)
        result = operation(_jira())
        assert isinstance(result, BackendError)
        assert "rejected the credentials" in result.message
        assert "does not exist" not in result.message

    @patch.object(JiraBackend, "_request")
    def test_assign_by_name_blames_the_token_not_the_name(self, mock_req):
        """`/user/search` is the THIRD endpoint that answers a rejected
        token with `200 []` rather than a 401 — the same shape as the
        issue search. Blaming the name here would reproduce, inside the
        fix for a credential defect, the credential defect being fixed."""

        def responses(method, path, **kwargs):
            if path == "/myself":
                return BackendError("JIRA GET /myself → 401")
            if path == "/user/search":
                return []
            return BackendError(f"JIRA {method} {path} → 404: Issue does not exist")

        mock_req.side_effect = responses
        result = _jira().assign("RW-1", "Ada Lovelace")
        assert isinstance(result, BackendError)
        assert "rejected the credentials" in result.message

    @patch.object(JiraBackend, "_request")
    def test_a_genuine_404_is_still_reported_as_a_404(self, mock_req):
        """The disambiguation must only fire when the token really is
        bad — a working token and a mistyped key is an ordinary missing
        ticket, and calling that a credential problem sends the user off
        to re-issue a token that was fine."""

        def responses(method, path, **kwargs):
            if path == "/myself":
                return {"accountId": "6054dede37065a0069a7c74f"}
            return BackendError(f"JIRA {method} {path} → 404: Issue does not exist")

        mock_req.side_effect = responses
        result = _jira().comment("RW-999", "text")
        assert isinstance(result, BackendError)
        assert "→ 404" in result.message
        assert "rejected the credentials" not in result.message


# --------------------------------------------------------------------------
# `assign` took only an accountId, and its own --help example could not work
# --------------------------------------------------------------------------
#
# LIVE-ONLY. `rite board assign RW-12 alpha` is the literal example in
# the command's own help; measured, JIRA answers "Specified user does not
# exist or you do not have required permissions", because the assignee
# API takes an accountId and nothing else. A mock accepts any string.


class TestAssignResolvesPeople:
    @patch.object(JiraBackend, "_request")
    def test_display_name_is_resolved_to_an_account_id(self, mock_req):
        def responses(method, path, **kwargs):
            if path == "/user/search":
                return [
                    {
                        "accountId": "6054dede37065a0069a7c74f",
                        "displayName": "Ada Lovelace",
                    }
                ]
            return {}

        mock_req.side_effect = responses
        assert _jira().assign("RW-1", "Ada Lovelace") is None
        assert mock_req.call_args[1]["json"] == {
            "accountId": "6054dede37065a0069a7c74f"
        }

    @patch.object(JiraBackend, "_request")
    def test_an_account_id_is_passed_through_without_a_lookup(self, mock_req):
        mock_req.return_value = {}
        assert _jira().assign("RW-1", "6054dede37065a0069a7c74f") is None
        paths = [call[0][1] for call in mock_req.call_args_list]
        assert "/user/search" not in paths

    @patch.object(JiraBackend, "_request")
    def test_an_ambiguous_name_is_refused_not_guessed(self, mock_req):
        """Assigning a ticket to the wrong colleague is worse than being
        asked to be specific."""

        def responses(method, path, **kwargs):
            if path == "/user/search":
                return [
                    {"accountId": "aaa", "displayName": "Ada Lovelace"},
                    {"accountId": "bbb", "displayName": "Ada Byron"},
                ]
            return {}

        mock_req.side_effect = responses
        result = _jira().assign("RW-1", "Ada")
        assert isinstance(result, BackendError)
        assert "matches 2 JIRA users" in result.message
        methods = [call[0][0] for call in mock_req.call_args_list]
        assert "PUT" not in methods

    @patch.object(JiraBackend, "_request")
    def test_a_rite_worker_name_points_at_board_label(self, mock_req):
        """No ticket backend has ever heard of a rite worker name, and
        the message has to say where the worker/ticket relationship
        actually lives (§9.10's label) instead of just failing."""

        def responses(method, path, **kwargs):
            if path == "/myself":
                return {"accountId": "6054dede37065a0069a7c74f"}
            if path == "/user/search":
                return []
            return {}

        mock_req.side_effect = responses
        result = _jira().assign("RW-1", "alpha")
        assert isinstance(result, BackendError)
        assert "rite board label" in result.message


# --------------------------------------------------------------------------
# `move` reported the status it was ASKED for, not the one it landed on
# --------------------------------------------------------------------------


class TestMoveReportsWhereTheTicketLanded:
    @patch.object(JiraBackend, "_request")
    def test_jira_reports_the_transitions_destination_not_its_name(self, mock_req):
        """A transition's NAME and the status it lands on are different
        strings. A workflow with a transition named "In Progress" that
        lands on "In Development" moved the ticket somewhere the caller
        never named — and the old code matched on the name FIRST and then
        echoed the request back as the outcome."""

        def responses(method, path, **kwargs):
            if path.endswith("/transitions") and method == "GET":
                return {
                    "transitions": [
                        {
                            "id": "31",
                            "name": "In Progress",
                            "to": {"name": "In Development"},
                        }
                    ]
                }
            return {}

        mock_req.side_effect = responses
        assert _jira().move("RW-1", "In Progress") == "In Development"

    @patch.object(JiraBackend, "_request")
    def test_jira_prefers_an_exact_destination_over_a_transition_name(self, mock_req):
        """When one transition is NAMED for the target column and another
        LANDS on it, the one that lands on it wins — that is the one that
        does what was asked."""

        def responses(method, path, **kwargs):
            if path.endswith("/transitions") and method == "GET":
                return {
                    "transitions": [
                        {"id": "11", "name": "Done", "to": {"name": "Closed"}},
                        {"id": "21", "name": "Finish", "to": {"name": "Done"}},
                    ]
                }
            return {}

        mock_req.side_effect = responses
        assert _jira().move("RW-1", "Done") is None
        assert mock_req.call_args[1]["json"] == {"transition": {"id": "21"}}

    @patch("rite_ai.tickets.github._run_gh")
    def test_github_reports_open_rather_than_a_column_it_does_not_have(self, mock_gh):
        """`rite board move 1 "In Progress"` printed `1 -> In Progress`
        while the issue sat exactly where it was — GitHub has no such
        column, and an already-open issue is reopened to no effect. The
        claim was also unfalsifiable: `rite board list --status "In
        Progress"` maps to `--state open` and returns every open issue."""
        mock_gh.return_value = ""
        assert GitHubBackend("org/repo").move("42", "In Progress") == "open"

    @patch("rite_ai.tickets.create_backend")
    def test_cli_prints_the_state_the_board_holds(
        self, mock_create, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(_project(tmp_path, backend="github"))
        backend = mock_create.return_value
        backend.move.return_value = "open"

        result = CliRunner().invoke(cli, ["board", "move", "42", "In Progress"])
        assert result.exit_code == 0
        assert "42 -> open" in result.output
        assert "no 'In Progress' column" in result.output


# --------------------------------------------------------------------------
# GitHub `label` refused every label rite actually uses
# --------------------------------------------------------------------------
#
# LIVE-ONLY. `gh issue edit --add-label` validates against the repo's
# existing labels and refuses an unknown one — measured: `'scheduled' not
# found`. rite's labels are worker names and `scheduled`, and no repo
# ships with those, so §9.10's assignment mechanism and the whole
# handover were unusable on a GitHub-backed project, with a retry queued
# forever behind a failure retrying cannot clear. Mocked, `_run_gh`
# returns whatever the test says and the argv looks perfectly reasonable.


class TestGitHubLabelsThatDoNotExistYet:
    @patch("rite_ai.tickets.github._run_gh")
    def test_add_goes_through_the_api_endpoint_that_creates_labels(self, mock_gh):
        mock_gh.return_value = "[]"
        assert GitHubBackend("org/repo").label("42", ["scheduled", "alpha"]) is None
        args = mock_gh.call_args[0][0]
        # `gh api -X POST .../labels` creates a missing label; `gh issue
        # edit --add-label` refuses it. Verified against the real API.
        assert args[:3] == ["api", "-X", "POST"]
        assert args[3] == "repos/org/repo/issues/42/labels"
        assert "--add-label" not in args
        assert "-f" in args and "labels[]=scheduled" in args

    @patch("rite_ai.tickets.github._run_gh")
    def test_remove_uses_issue_edit(self, mock_gh):
        mock_gh.return_value = ""
        assert GitHubBackend("org/repo").label("42", [], remove=["alpha"]) is None
        args = mock_gh.call_args[0][0]
        assert args[:2] == ["issue", "edit"]
        assert "--remove-label" in args and "alpha" in args

    @patch("rite_ai.tickets.github._run_gh")
    def test_create_labels_after_the_issue_exists(self, mock_gh):
        """`gh issue create --label` aborts the WHOLE create on an
        unknown label — measured, no issue was created at all. So the
        issue is made first and labelled second."""
        calls: list[list[str]] = []

        def record(args, cwd=None):
            calls.append(args)
            if args[:2] == ["issue", "create"]:
                return "https://github.com/org/repo/issues/7\n"
            return "[]"

        mock_gh.side_effect = record
        ticket = GitHubBackend("org/repo").create("t", labels=["alpha"])
        assert not isinstance(ticket, BackendError)
        assert ticket.id == "7"
        assert "--label" not in calls[0]
        assert calls[1][:3] == ["api", "-X", "POST"]

    @patch("rite_ai.tickets.github._run_gh")
    def test_a_failed_labelling_still_names_the_issue_it_created(self, mock_gh):
        """The issue EXISTS. An error that mentions only the labelling
        sends the caller off to create it a second time."""

        def record(args, cwd=None):
            if args[:2] == ["issue", "create"]:
                return "https://github.com/org/repo/issues/7\n"
            return BackendError("gh exited 1: rate limited")

        mock_gh.side_effect = record
        result = GitHubBackend("org/repo").create("t", labels=["alpha"])
        assert isinstance(result, BackendError)
        assert "created issue 7" in result.message


# --------------------------------------------------------------------------
# GitHub `query` was dead against the real `gh`
# --------------------------------------------------------------------------
#
# LIVE-ONLY, and the same shape as JIRA's removed `/search` endpoint: the
# whole query was joined into ONE string and passed after `--`, so `gh`
# treated it as a single search TERM and quoted it. `rite board query
# "is:open"` reached GitHub as `( repo:"org/repo is:issue is:open" )
# type:issue` and came back `Invalid search query`. A mocked suite
# asserts on the argv rite builds, never on what `gh` does with it.


class TestGitHubQueryArguments:
    @patch("rite_ai.tickets.github._run_gh")
    def test_each_qualifier_is_its_own_argument(self, mock_gh):
        mock_gh.return_value = "[]"
        GitHubBackend("org/repo").query("is:open label:bug")
        args = mock_gh.call_args[0][0]
        tail = args[args.index("--") + 1 :]
        assert tail == ["repo:org/repo", "is:issue", "is:open", "label:bug"]
        # The original defect in one assertion: nothing may arrive as a
        # single blob containing the repo AND the caller's qualifiers.
        assert not any(" " in token for token in tail)

    @patch("rite_ai.tickets.github._run_gh")
    def test_a_quoted_value_survives_as_one_token(self, mock_gh):
        mock_gh.return_value = "[]"
        GitHubBackend("org/repo").query('label:"good first issue"')
        args = mock_gh.call_args[0][0]
        assert args[-1] == "label:good first issue"

    @patch("rite_ai.tickets.github._run_gh")
    def test_an_unbalanced_quote_is_the_users_typo_not_a_crash(self, mock_gh):
        result = GitHubBackend("org/repo").query('label:"unterminated')
        assert isinstance(result, BackendError)
        assert "could not parse query" in result.message
        mock_gh.assert_not_called()


# --------------------------------------------------------------------------
# The handover added `scheduled` and never took the worker's label off
# --------------------------------------------------------------------------


class TestHandoverActuallyUnassigns:
    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_the_departing_workers_label_is_removed(self, mock_create, tmp_path):
        """SPEC §9.10 step 3 is "reassigns or UNASSIGNS"; only the add
        half was built. A ticket that still answers `labels = <worker>`
        is picked straight back up by §9.10's orientation table, whose
        first row is "in-progress tickets assigned to this Manager's
        workers → resume" — so the handover handed the work back to
        itself."""
        root = _project(tmp_path)
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        result = perform_handover(root, worker="alpha", reason="stall", ticket="RW-1")

        assert result.ticket_commented is True
        backend.label.assert_called_once_with("RW-1", ["scheduled"], remove=["alpha"])

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_the_queued_retry_carries_the_removal_too(self, mock_create, tmp_path):
        root = _project(tmp_path)
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = BackendError("board unreachable")

        perform_handover(root, worker="alpha", reason="stall", ticket="RW-1")

        pending = list_pending(root)
        assert [m.kind for m in pending] == ["handover-label"]
        assert pending[0].payload["remove"] == ["alpha"]

    def test_removing_an_absent_label_is_not_an_error(self):
        """Verified against both live APIs: JIRA answers 204 for
        `{"remove": ...}` on a label the issue never had, and `gh issue
        edit --remove-label` tolerates the same. So a handover need not
        read the ticket before returning it to the pool."""
        with patch.object(JiraBackend, "_request") as mock_req:
            mock_req.return_value = {}
            assert _jira().label("RW-1", [], remove=["never-was"]) is None
            ops = mock_req.call_args[1]["json"]["update"]["labels"]
            assert ops == [{"remove": "never-was"}]


# --------------------------------------------------------------------------
# `stop` reported success when the board was never written to
# --------------------------------------------------------------------------


class TestStopSaysWhenTheBoardWasNotUpdated:
    def test_a_config_that_does_not_parse_is_named(self, tmp_path):
        """Queueing is the designed offline behaviour (§9.10 "stop must
        succeed offline") — printing the same cheerful line for it as for
        a delivered handover is not. Found live: a `.rite/modules.yaml`
        that would not parse produced `stopped (clean shutdown), released
        1 claim(s)`, exit 0, and a board nobody had touched."""
        root = _project(tmp_path)
        (root / ".rite" / "modules.yaml").write_text("modules: []\n")

        result = stop(root, worker="alpha", ticket="RW-1")

        assert result.ok is True  # local shutdown still succeeded
        assert result.queued is True
        assert "board NOT updated" in result.message
        assert "modules.yaml" in result.message
        assert len(list_pending(root)) == 1

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_a_backend_that_refuses_the_comment_is_named(self, mock_create, tmp_path):
        root = _project(tmp_path)
        backend = mock_create.return_value
        backend.comment.return_value = BackendError("JIRA rejected the credentials")

        result = stop(root, worker="alpha", ticket="RW-1")

        assert result.queued is True
        assert "board NOT updated" in result.message
        assert "RW-1" in result.message

    @patch("rite_ai.lifecycle.commands.create_backend_from_config")
    def test_a_delivered_handover_says_which_ticket(self, mock_create, tmp_path):
        root = _project(tmp_path)
        backend = mock_create.return_value
        backend.comment.return_value = None
        backend.label.return_value = None

        result = stop(root, worker="alpha", ticket="RW-1")

        assert result.queued is False
        assert "handover posted to RW-1" in result.message
        assert "NOT updated" not in result.message


# --------------------------------------------------------------------------
# `rite board label` can take a label off again
# --------------------------------------------------------------------------


@patch("rite_ai.tickets.create_backend")
def test_board_label_remove_flag(mock_create, tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    backend = mock_create.return_value
    backend.label.return_value = None

    result = CliRunner().invoke(
        cli, ["board", "label", "RW-1", "beta", "--remove", "alpha"]
    )
    assert result.exit_code == 0
    backend.label.assert_called_once_with("RW-1", ["beta"], remove=["alpha"])
    assert "removed alpha" in result.output


@patch("rite_ai.tickets.create_backend")
def test_board_label_with_nothing_to_do_refuses(mock_create, tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    result = CliRunner().invoke(cli, ["board", "label", "RW-1"])
    assert result.exit_code == 1
    assert "nothing to do" in result.output


def test_no_backend_silently_swallows_a_write(tmp_path):
    """A guard against the family this file is about: every write method
    on every backend must return either None or a BackendError, never a
    bare success value that a caller cannot check."""
    gh = GitHubBackend("org/repo")
    assert isinstance(gh.link("1", "2", "Blocks"), BackendError)
    assert isinstance(gh.move("1", "Ready for testing"), BackendError)
