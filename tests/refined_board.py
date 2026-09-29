"""A board whose ticket carries a real, signed refinement record (TR4 tests).

Nothing here mocks the predicate. The key is a real one in a temporary
directory, the record is built and signed by `refinement.record.build`, and
`refinement.status.of` checks it exactly as `rite sandbox start` does. Only
the network is faked: `create_backend_from_config` returns a GitHub or Jira
backend whose `read_thread` answers from memory and counts its reads.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.tickets import BackendError, Comment, Thread, Ticket
from rite_ai.tickets.github import GitHubBackend
from rite_ai.tickets.jira import JiraBackend


class _Memory:
    """`read_thread` from memory, counted. `read` is never TR4's path."""

    def _setup(self, ticket, comments, error):
        self.ticket = ticket
        self.comments = comments
        self.error = error
        self.reads: list[str] = []

    def read_thread(self, ticket_id):
        self.reads.append(ticket_id)
        if self.error is not None:
            return self.error
        return Thread(
            self.ticket,
            [
                Comment(id=str(i), body=b, author="rite")
                for i, b in enumerate(self.comments)
            ],
            True,
        )

    def read(self, ticket_id):
        raise AssertionError("TR4 reads the board once, through read_thread")


class FakeGitHub(_Memory, GitHubBackend):
    def __init__(self, repo, ticket, comments, error=None):
        GitHubBackend.__init__(self, repo)
        self._setup(ticket, comments, error)


class FakeJira(_Memory, JiraBackend):
    def __init__(self, site, ticket, comments, error=None):
        # No HTTP client: nothing here may reach a network.
        self.config = type("JiraConfig", (), {"site": site, "project_key": ""})()
        self._setup(ticket, comments, error)


def signed_record(ticket: Ticket, key: bytes, board: dict, **overrides):
    fields = dict(
        ticket=str(ticket.id),
        board=board,
        title=ticket.title,
        description=ticket.description,
        definition_of_done=[
            "the HTTP timeout in main.py is a --timeout flag",
            "with no flag, the timeout is unchanged",
        ],
        verify=["pytest tests/test_timeout.py"],
        provenance={"kind": rec.ACCEPTED, "message": "m1"},
        supersedes=None,
        key=key,
    )
    fields.update(overrides)
    return rec.build(**fields)


@contextmanager
def board_with(
    tmp_path,
    monkeypatch,
    ticket: Ticket,
    *,
    refined: bool = True,
    jira_site: str = "",
    signed_against: Ticket | None = None,
    error: BackendError | None = None,
):
    """Patch the project's board to one carrying `ticket`.

    `refined` puts a record signed for `signed_against` (default: `ticket`
    itself; a different text makes the ticket STALE) on it. `jira_site`
    makes it a Jira board, else a GitHub one. `error` makes the read fail.
    Yields `(board, record)`; `record` is None when not refined.
    """
    monkeypatch.setenv(refinement_key.KEY_DIR_ENV, str(tmp_path / "refinement-key"))
    key = refinement_key.ensure()
    identity = (
        {"type": "jira", "site": jira_site.lower()}
        if jira_site
        else {"type": "github", "repo": "org/repo"}
    )
    record = signed_record(signed_against or ticket, key, identity) if refined else None
    comments = [rec.render(record)] if record else []
    board = (
        FakeJira(jira_site, ticket, comments, error)
        if jira_site
        else FakeGitHub("org/repo", ticket, comments, error)
    )
    with patch("rite_ai.tickets.create_backend_from_config", return_value=board):
        yield board, record


class _AnyTicketRefined(FakeGitHub):
    """Answers every ticket id with a ticket that carries a valid record."""

    def __init__(self, key: bytes):
        FakeGitHub.__init__(self, "org/repo", None, [], None)
        self.key = key

    def read_thread(self, ticket_id):
        self.reads.append(ticket_id)
        ticket = Ticket(id=ticket_id, title="a ticket", description="its text")
        record = signed_record(ticket, self.key, {"type": "github", "repo": "org/repo"})
        return Thread(ticket, [Comment(id="1", body=rec.render(record))], True)


@contextmanager
def any_ticket_refined():
    """For tests about something else: every ticket is REFINED through the
    real predicate and a real key, from a board that the project's config
    need not even name (`board_for` is what is replaced)."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as where:
        with patch.dict(os.environ, {refinement_key.KEY_DIR_ENV: where}):
            board = _AnyTicketRefined(refinement_key.ensure())
            with patch("rite_ai.refinement.status.board_for", return_value=board):
                yield board


def refined(ticket_id: str):
    """A `refinement` check for tests about something else (TR5's route and
    assignment gates): the REAL predicate answers REFINED for `ticket_id`,
    over a record signed with a real key made for this one call."""
    from rite_ai.refinement import status as refinement_status

    with any_ticket_refined() as board:
        return refinement_status.status(board, ticket_id)
