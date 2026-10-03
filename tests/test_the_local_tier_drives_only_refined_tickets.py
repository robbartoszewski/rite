"""L-6 drives only REFINED tickets, and deleting that filter turns this red.

⚠ **Why this file exists separately.** The filter in `_local_tier_tickets` was
added because `test_every_path_to_work_is_enumerated` demanded an entry in its
table — and that test checks the TABLE, not the behaviour. With the filter
deleted the whole suite stayed green while L-6 would drive an UNREFINED ticket
through decomposition, approval, every subtask and out to a pushed branch. A
gate whose removal nothing notices is not a gate.

TR5 is the rule: every path that reads the backlog says what it does with a
ticket that is not refined. There are FIVE states and only one is work — a
`STALE` record no longer matches its ticket, and a looser check is exactly how
that one gets through.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rite_ai.cli.main import _local_tier_tickets
from rite_ai.refinement.status import (
    CONFLICT,
    NOT_REFINED,
    REFINED,
    STALE,
    UNREADABLE,
    Status,
)
from rite_ai.tickets.interface import BackendError, Ticket


class _Board:
    """A board holding `states` — ticket id -> refinement state."""

    def __init__(self, states: dict[str, str], error: str = ""):
        self.states = states
        self.error = error
        self.listed: list[object] = []
        self.asked: list[str] = []

    def list_tickets(self, filters=None):
        self.listed.append(filters)
        if self.error:
            return BackendError(self.error)
        return type(
            "Page",
            (),
            {"tickets": [Ticket(id=i, title=i) for i in self.states]},
        )()


def _answer(board: _Board):
    def status(asked_board, ticket_id: str) -> Status:
        board.asked.append(ticket_id)
        return Status(
            state=board.states[ticket_id], record=None, detail="", ticket=None
        )

    return patch("rite_ai.refinement.status.status", side_effect=status)


# ── the gate ─────────────────────────────────────────────────────────────────


def test_only_the_refined_ticket_is_driven():
    """The mutation test: delete the filter and this fails.

    A board with one refined and one unrefined ticket. Both are assigned to this
    Manager and both come back from `list_tickets`; only one is work.
    """
    board = _Board({"KAN-1": REFINED, "KAN-2": NOT_REFINED})
    with _answer(board):
        tickets, why = _local_tier_tickets(board, "lead")
    assert why == ""
    assert tickets == ["KAN-1"]
    # Stated as its own assertion: the unrefined one must be ABSENT, which is
    # the half that dies when the filter is removed.
    assert "KAN-2" not in tickets


@pytest.mark.parametrize("state", [NOT_REFINED, STALE, CONFLICT, UNREADABLE])
def test_no_state_but_refined_is_work(state):
    """Four ways not to be refined, and none of them is work.

    ⚠ STALE is the one a loose check lets through: the ticket HAS a refinement
    record, it just no longer matches. Driving it would work from text nobody
    approved.
    """
    board = _Board({"KAN-1": state})
    with _answer(board):
        tickets, why = _local_tier_tickets(board, "lead")
    assert tickets == [], f"{state} was treated as work"
    assert why == ""


def test_every_assigned_ticket_is_asked_about():
    # One read per ticket, as `loop._ready` does — not a guess from the label.
    board = _Board({"KAN-1": REFINED, "KAN-2": NOT_REFINED, "KAN-3": REFINED})
    with _answer(board):
        tickets, _why = _local_tier_tickets(board, "lead")
    assert board.asked == ["KAN-1", "KAN-2", "KAN-3"]
    assert tickets == ["KAN-1", "KAN-3"]


def test_it_asks_the_board_for_this_managers_tickets():
    board = _Board({"KAN-1": REFINED})
    with _answer(board):
        _local_tier_tickets(board, "lead")
    assert getattr(board.listed[0], "assignee", None) == "lead"


# ── a board that cannot answer ───────────────────────────────────────────────


def test_a_board_error_is_returned_not_raised():
    # A board that cannot be read is a cycle that advanced nothing, never a run
    # that ends.
    board = _Board({}, error="the board is down")
    tickets, why = _local_tier_tickets(board, "lead")
    assert tickets == []
    assert "the board is down" in why


def test_a_ticket_with_no_id_is_skipped_rather_than_asked_about():
    board = _Board({"KAN-1": REFINED})

    def list_tickets(filters=None):
        return type(
            "Page",
            (),
            {"tickets": [Ticket(id="", title="x"), Ticket(id="KAN-1", title="y")]},
        )()

    board.list_tickets = list_tickets
    with _answer(board):
        tickets, _why = _local_tier_tickets(board, "lead")
    assert tickets == ["KAN-1"]
    assert board.asked == ["KAN-1"]
