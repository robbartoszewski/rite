"""`rite refine accept` (TRQ10, decided "Allow it"): a session running as the
person attests a definition of done, and rite says so honestly.

A fake board whose `comment` really appends to the thread `read_thread`
returns, so the read-back that decides success is a real read of what was
written, not a stub that agrees.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rite_ai.refinement import accept as acc
from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st
from rite_ai.tickets import BackendError, Comment, Thread, Ticket
from rite_ai.tickets.github import GitHubBackend

WHEN = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


class Board(GitHubBackend):
    def __init__(self, description="timeout is too long", mangle=None, fail=None):
        super().__init__("org/repo")
        self.ticket = Ticket(id="7", title="Timeout", description=description)
        self.comments: list[Comment] = []
        self.mangle = mangle
        self.fail = fail

    def read_thread(self, ticket_id):
        return Thread(self.ticket, list(self.comments), True)

    def comment(self, ticket_id, text):
        if self.fail:
            return BackendError(self.fail)
        body = self.mangle(text) if self.mangle else text
        self.comments.append(Comment(str(len(self.comments)), body, "me"))
        return None


@pytest.fixture
def board(monkeypatch, tmp_path):
    monkeypatch.setenv(refinement_key.KEY_DIR_ENV, str(tmp_path / "refinement"))
    b = Board()
    monkeypatch.setattr(st, "board_for", lambda root, config, role="workers": b)
    return b


def _accept(**kw):
    kw.setdefault("items", ["the timeout is a flag"])
    kw.setdefault("verify", [])
    return acc.accept(None, None, "7", now=WHEN, **kw)


def test_accept_writes_a_record_and_confirms_it_by_reading_back(board):
    out = _accept()
    assert out.ok, out.message
    after = st.status(board, "7")
    assert after.state == st.REFINED
    assert after.record.record_id == out.record.record_id
    assert after.record.provenance["kind"] == rec.ATTESTED
    assert after.record.verify == rec.NONE_AGREED


def test_an_attested_record_is_findable_by_its_token(board):
    _accept()
    assert rec.ATTESTED_TOKEN in board.comments[-1].body
    assert "not confirmed" in board.comments[-1].body


def test_a_second_acceptance_supersedes_the_first_rather_than_conflicting(board):
    first = _accept()
    second = _accept(items=["the timeout is a --timeout flag"])
    assert second.ok, second.message
    assert second.record.supersedes == first.record.record_id
    assert st.status(board, "7").record.record_id == second.record.record_id


def test_accept_writes_nothing_on_an_unreadable_ticket(board):
    board.comments.append(Comment("x", f"```{rec.MARKER}\nnot json\n```", "p"))
    out = _accept()
    assert not out.ok and "Nothing was written" in out.message
    assert len(board.comments) == 1


def test_as_written_takes_the_tickets_own_items(board):
    board.ticket.description = (
        "Some context.\n\n## Definition of done\n- [ ] flag exists\n"
        "- default unchanged\n\n## Notes\n- not an item\n"
    )
    out = _accept(items=[], use_ticket_text=True)
    assert out.ok, out.message
    assert out.record.definition_of_done == ("flag exists", "default unchanged")
    assert out.record.provenance["as_written"] is True


def test_as_written_with_no_section_invents_nothing(board):
    out = _accept(items=[], use_ticket_text=True)
    assert not out.ok and "nothing to accept as written" in out.message
    assert board.comments == []


def test_no_key_means_nothing_is_signed(board, monkeypatch):
    def denied():
        raise OSError("Operation not permitted")

    monkeypatch.setattr(refinement_key, "ensure", denied)
    out = _accept()
    assert not out.ok and "cannot sign here" in out.message
    assert board.comments == []


def test_a_board_that_alters_the_record_is_a_failure_not_a_success(board):
    """Jira's comment round trip is unmeasured (TR0). If the board changes the
    record on the way, the read-back fails and it is reported as NOT refined."""
    board.mangle = lambda text: text.replace("the timeout is a flag", "altered")
    out = _accept()
    assert not out.ok and "NOT refined" in out.message


def test_a_post_that_fails_is_reported(board):
    board.fail = "gh exited 1: rate limited"
    out = _accept()
    assert not out.ok and "rate limited" in out.message


def test_the_status_line_never_shows_attested_as_confirmed():
    from rite_ai.cli.main import _provenance_line

    line = _provenance_line({"kind": rec.ATTESTED, "at": "t"})
    assert rec.ATTESTED_TOKEN in line and "not confirmed" in line
    assert "accepted by the User" in _provenance_line({"kind": rec.ACCEPTED})


def test_the_record_publishes_nothing_about_this_machine(board):
    """The record is posted to a board that may be public. The first real
    round trip posted the machine's hostname; nothing identifying the machine
    may be in it."""
    import getpass
    import socket

    out = _accept()
    names = {socket.gethostname(), socket.gethostname().split(".")[0]}
    # Short names ("mac") are ordinary words, so the provenance's VALUES are
    # checked for them; the body is checked for the full name and the user.
    for value in out.record.provenance.values():
        assert str(value) not in names, f"the provenance names this machine: {value}"
    body = board.comments[-1].body
    assert socket.gethostname() not in body
    assert getpass.getuser() not in body
