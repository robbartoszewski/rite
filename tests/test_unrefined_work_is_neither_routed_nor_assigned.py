"""TR5: an unrefined ticket is neither routed to a Manager nor assigned to one.

`rite sandbox start` already refuses to start a Worker on a ticket that is not
REFINED (TR4). That is the last gate, not the only one: a ticket routed or
assigned before it is refined reaches a Manager that plans around it and then
cannot start it, and the Owner learns nothing. So the Owner's supervisor
checks before it delivers a route, and the unattended tick checks before it
labels a ticket with a Manager's name (Robert: scheduled and refined is
assignable; scheduled and not refined is the Owner's to refine).

Nothing here mocks the predicate. Every record is built and signed with a
real key by `refinement.record.build`, and every answer comes from
`refinement.status.status` reading a board. Only the network is faked.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from rite_ai.managers import routing
from rite_ai.managers.mailbox import INBOX, read
from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as refinement_status
from rite_ai.tickets import BackendError, Comment, Thread, Ticket
from rite_ai.tickets.github import GitHubBackend
from rite_ai.tickets.own_writes import ReadsItsOwnWrites
from tests.refined_board import refined, signed_record

OWNER, SECONDARY = "lead", "helper"
NAMES = [OWNER, SECONDARY]
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
IDENTITY = {"type": "github", "repo": "org/repo"}


@pytest.fixture
def key(tmp_path):
    with patch.dict(
        os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "refinement-key")}
    ):
        yield refinement_key.ensure()


class Backlog(GitHubBackend):
    """A GitHub board from memory: `refined` tickets carry a signed record,
    `stale` ones a record signed for text the ticket no longer has, `broken`
    ones fail to read. `label` records rather than writes."""

    def __init__(self, key, ids, *, refined=(), stale=(), broken=()):
        GitHubBackend.__init__(self, "org/repo")
        self.tickets = [
            Ticket(
                id=i, title=f"ticket {i}", description="its text", labels=["scheduled"]
            )
            for i in ids
        ]
        self.key = key
        self.refined, self.stale, self.broken = set(refined), set(stale), set(broken)
        self.labelled: list[tuple[str, list[str]]] = []
        self.reads: list[str] = []

    def list_tickets(self, _filter):
        return self.tickets

    def read_thread(self, ticket_id):
        self.reads.append(ticket_id)
        if ticket_id in self.broken:
            return BackendError(f"GitHub answered 502 for {ticket_id}")
        (ticket,) = [t for t in self.tickets if t.id == ticket_id]
        comments = []
        if ticket_id in self.refined or ticket_id in self.stale:
            signed_for = ticket
            if ticket_id in self.stale:
                signed_for = Ticket(
                    id=ticket.id, title=ticket.title, description="older text"
                )
            record = signed_record(signed_for, self.key, IDENTITY)
            comments.append(Comment(id="1", body=rec.render(record), author="rite"))
        return Thread(ticket, comments, True)

    def label(self, ticket_id, labels, remove=None):
        self.labelled.append((ticket_id, labels))
        return None


def _check(board):
    return lambda ticket: refinement_status.status(board, ticket)


def _on_board(ticket_id):
    return Ticket(id=ticket_id, title="a ticket")


def _route(root, board_check, ticket="RT-1", said=None):
    routing.request(root, OWNER, SECONDARY, "fix the timeout", ticket)
    return routing.deliver_routes(
        root,
        OWNER,
        OWNER,
        NAMES,
        (said if said is not None else []).append,
        read_ticket=_on_board,
        refinement=board_check,
    )


def _told_owner(root) -> str:
    (note,) = read(root, OWNER, INBOX)
    return note.text


# --- the route ---------------------------------------------------------------


class TestARouteNeedsARefinedTicket:
    def test_a_ticket_with_no_record_is_not_routed_and_the_owner_is_told_how(
        self, tmp_path, key
    ):
        board = Backlog(key, ["RT-1"])
        assert _route(tmp_path, _check(board)) == 0
        assert read(tmp_path, SECONDARY, INBOX) == []
        told = _told_owner(tmp_path)
        assert "NOT REFINED" in told and "rite refine accept RT-1" in told

    def test_a_stale_record_is_not_routed(self, tmp_path, key):
        board = Backlog(key, ["RT-1"], stale=["RT-1"])
        assert _route(tmp_path, _check(board)) == 0
        assert read(tmp_path, SECONDARY, INBOX) == []
        assert "STALE" in _told_owner(tmp_path)

    def test_a_board_that_cannot_be_read_is_not_a_refined_one(self, tmp_path, key):
        board = Backlog(key, ["RT-1"], broken=["RT-1"])
        assert _route(tmp_path, _check(board)) == 0
        assert "UNREADABLE" in _told_owner(tmp_path)

    def test_no_check_at_all_routes_nothing(self, tmp_path):
        said: list[str] = []
        assert _route(tmp_path, None, said=said) == 0
        assert read(tmp_path, SECONDARY, INBOX) == []
        assert any("UNREADABLE" in line for line in said), said

    def test_a_check_that_raises_routes_nothing(self, tmp_path):
        def explodes(_ticket):
            raise RuntimeError("keychain locked")

        assert _route(tmp_path, explodes) == 0
        assert "keychain locked" in _told_owner(tmp_path)

    def test_a_refined_route_carries_the_agreed_definition_of_done(self, tmp_path, key):
        board = Backlog(key, ["RT-1"], refined=["RT-1"])
        assert _route(tmp_path, _check(board)) == 1
        (msg,) = read(tmp_path, SECONDARY, INBOX)
        first, *rest = msg.text.splitlines()
        assert first.startswith("[routed by the Owner Manager 'lead' · ticket RT-1")
        # Quoted like the Owner's text, so a board's words cannot forge a
        # header, and from the one read that answered REFINED.
        assert all(line.startswith(">") for line in rest), rest
        assert "> Agreed definition of done for RT-1" in msg.text
        assert "> - [ ] with no flag, the timeout is unchanged" in msg.text
        assert board.reads == ["RT-1"]

    def test_the_refusal_is_the_one_a_refused_start_carries(self, tmp_path, key):
        """One sentence for one state, whichever gate said it."""
        from rite_ai.cli.main import refused_for_refinement

        board = Backlog(key, ["RT-1"])
        _route(tmp_path, _check(board))
        assert refused_for_refinement("NOT REFINED", "RT-1") in _told_owner(tmp_path)


def test_the_supervisor_checks_the_board_it_was_given(tmp_path, key):
    """`rite start`'s router asks the predicate about the same board the
    ticket check reads, and a board rite cannot identify routes nothing."""
    from rite_ai.cli.main import _router_for

    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "config.yaml").write_text(
        "coordination:\n  managers: [lead, helper]\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n      preset: lead\n"
        "    - name: helper\n      engine: claude\n      preset: executor\n"
    )
    anonymous = type("Board", (), {"read": staticmethod(_on_board)})()
    routing.request(tmp_path, OWNER, SECONDARY, "x", "RT-1")
    _router_for(tmp_path, OWNER, anonymous)(lambda _m: None)
    assert read(tmp_path, SECONDARY, INBOX) == []
    assert "UNREADABLE" in _told_owner(tmp_path)

    board = Backlog(key, ["RT-2"], refined=["RT-2"])
    board.read = _on_board
    routing.request(tmp_path, OWNER, SECONDARY, "y", "RT-2")
    _router_for(tmp_path, OWNER, board)(lambda _m: None)
    (msg,) = read(tmp_path, SECONDARY, INBOX)
    assert "ticket RT-2" in msg.text and board.reads == ["RT-2"]


# --- the assignment ----------------------------------------------------------


def _pool(tmp_path, managers=("alpha", "beta")):
    from datetime import timedelta

    from rite_ai.coordination.heartbeat import publish_heartbeat
    from rite_ai.coordination.local_backend import LocalStateLayer
    from tests.test_local_wiring import _project_for_pool

    layer = LocalStateLayer(tmp_path / "state")
    for manager in managers:
        publish_heartbeat(
            layer, manager, workers=[], in_flight=0, now=NOW - timedelta(seconds=5)
        )
    return layer, _project_for_pool(list(managers))


def _assign(tmp_path, board, **kw):
    from rite_ai.scheduler import _assign_the_pool

    layer, project = _pool(tmp_path)
    return _assign_the_pool(
        layer, project.config.coordination, project, "alpha", board, NOW, **kw
    )


class TestAssignmentNeedsARefinedTicket:
    def test_only_the_refined_ticket_is_assigned_and_the_rest_are_said(
        self, tmp_path, key
    ):
        board = Backlog(
            key,
            ["RT-1", "RT-2", "RT-3", "RT-4"],
            refined=["RT-2"],
            stale=["RT-3"],
            broken=["RT-4"],
        )
        lines = _assign(tmp_path, board)
        assert [t for t, _ in board.labelled] == ["RT-2"], lines
        for ticket, state in (
            ("RT-1", "NOT REFINED"),
            ("RT-3", "STALE"),
            ("RT-4", "UNREADABLE"),
        ):
            assert any(
                line.startswith(f"coordination: {ticket} not assigned — {state}")
                for line in lines
            ), (ticket, lines)

    def test_a_backlog_with_nothing_refined_assigns_nothing(self, tmp_path, key):
        board = Backlog(key, ["RT-1", "RT-2"])
        lines = _assign(tmp_path, board)
        assert board.labelled == [] and len(lines) == 2, lines

    def test_the_default_check_is_the_board_itself_through_its_wrapper(
        self, tmp_path, key
    ):
        """The unattended tick's board is built with a root, so it arrives
        wrapped (`ReadsItsOwnWrites`). A wrapper the predicate could not see
        through would make every record UNREADABLE: nothing ever assigned,
        for a reason nobody could find."""
        inner = Backlog(key, ["RT-1", "RT-2"], refined=["RT-1"])
        wrapped = ReadsItsOwnWrites(inner, Path(tmp_path / "root"), "github:org/repo")
        lines = _assign(tmp_path, wrapped)
        assert [t for t, _ in inner.labelled] == ["RT-1"], lines

    def test_an_assigned_ticket_is_not_read_again(self, tmp_path, key):
        """Rule 1 before Rule 0: a ticket a Manager already holds costs no
        board read each tick."""
        board = Backlog(key, ["RT-1"], refined=["RT-1"])
        board.tickets[0].labels.append("beta")
        assert _assign(tmp_path, board) == []
        assert board.reads == []


# --- the handout to a Worker --------------------------------------------------


def _hand_out(tmp_path, board, **kw):
    from rite_ai.config.models import ScheduleConfig, ScheduleWindow
    from rite_ai.coordination.distribution import distribute

    return distribute(
        tmp_path,
        board,
        manager="alpha",
        workers=["w1", "w2", "w3"],
        schedule=ScheduleConfig(
            timezone="UTC", windows=[ScheduleWindow(hours="00:00-24:00", workers=3)]
        ),
        now=NOW,
        busy=set(),
        **kw,
    )


class TestAHandoutNeedsARefinedTicket:
    """A Manager's name can be put on a ticket by hand, and a ticket can go
    STALE after the Owner assigned it: either one handed out labels a Worker
    with work its start then refuses."""

    def test_only_the_refined_ticket_goes_to_a_worker(self, tmp_path, key):
        board = Backlog(key, ["RT-1", "RT-2", "RT-3"], refined=["RT-2"], stale=["RT-3"])
        for ticket in board.tickets:
            ticket.labels.append("alpha")
        got = _hand_out(tmp_path, board)
        assert got.handouts == [("RT-2", "w1")]
        assert got.held_back["RT-1"].startswith("NOT REFINED")
        assert got.held_back["RT-3"].startswith("STALE")
        assert [t for t, _ in board.labelled] == ["RT-2"]

    def test_a_check_that_raises_holds_the_ticket_and_the_tick_goes_on(
        self, tmp_path, key
    ):
        board = Backlog(key, ["RT-1", "RT-2"], refined=["RT-2"])
        for ticket in board.tickets:
            ticket.labels.append("alpha")

        def flaky(ticket_id):
            if ticket_id == "RT-1":
                raise RuntimeError("connection reset")
            return refinement_status.status(board, ticket_id)

        got = _hand_out(tmp_path, board, refinement=flaky)
        assert got.handouts == [("RT-2", "w1")]
        assert got.held_back["RT-1"].startswith("UNREADABLE")

    def test_the_default_check_is_the_board_itself_through_its_wrapper(
        self, tmp_path, key
    ):
        inner = Backlog(key, ["RT-1", "RT-2"], refined=["RT-1"])
        for ticket in inner.tickets:
            ticket.labels.append("alpha")
        wrapped = ReadsItsOwnWrites(inner, Path(tmp_path / "root"), "github:org/repo")
        got = _hand_out(tmp_path, wrapped)
        assert got.handouts == [("RT-1", "w1")], got
        assert got.held_back["RT-2"].startswith("NOT REFINED")


def test_a_board_that_cannot_answer_one_ticket_does_not_stop_assignment(tmp_path, key):
    """The same fail-closed wrapper on the Owner's side: one ticket whose
    check raises is left and said, and the next is still assigned."""
    board = Backlog(key, ["RT-1", "RT-2"], refined=["RT-2"])

    def flaky(ticket_id):
        if ticket_id == "RT-1":
            raise RuntimeError("connection reset")
        return refinement_status.status(board, ticket_id)

    lines = _assign(tmp_path, board, refinement=flaky)
    assert [t for t, _ in board.labelled] == ["RT-2"], lines
    assert any("RT-1 not assigned — UNREADABLE" in line for line in lines), lines


def test_board_identity_sees_through_any_depth_of_wrapper(tmp_path, key):
    inner = Backlog(key, [])
    once = ReadsItsOwnWrites(inner, tmp_path, "github:org/repo")
    twice = ReadsItsOwnWrites(once, tmp_path, "github:org/repo")
    assert rec.board_identity(twice) == rec.board_identity(inner) == IDENTITY


def test_the_test_helper_answers_through_the_real_predicate():
    """`refined` is what the other route and assignment tests inject. It must
    be the predicate answering, not a stub that says REFINED."""
    answer = refined("RT-5")
    assert answer.refined and answer.record.ticket == "RT-5"
    assert answer.record.mac
