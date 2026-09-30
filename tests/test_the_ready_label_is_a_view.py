"""TR7: `ready-to-work` is a view of the refinement record, never the authority.

The note's part 3.10. The label is on a ticket exactly when rite's latest read
shows `scheduled`, REFINED and no Manager's name. Every test here asserts what
the BOARD holds after rite acted, over real signed records and a real key; only
the network is faked. A reconciler that reported corrections while writing
nothing would pass a looser suite.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from rite_ai.config.models import ScheduleConfig, ScheduleWindow
from rite_ai.coordination.distribution import distribute
from rite_ai.refinement import accept, view
from rite_ai.refinement import instructions as ri
from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st
from rite_ai.tickets import BackendError, Comment, Thread, Ticket, TicketFilter
from rite_ai.tickets.github import GitHubBackend
from rite_ai.tickets.interface import TicketPage
from tests.refined_board import signed_record
from tests.test_the_owner_routes_to_other_managers import project

__all__ = ["project"]  # a fixture, used by name below

READY = view.READY
IDENTITY = {"type": "github", "repo": "org/repo"}
NOON = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class Board(GitHubBackend):
    """A GitHub board in memory: labels, comments and lists behave as the
    board's do, and every write is kept."""

    def __init__(self, *tickets: Ticket):
        GitHubBackend.__init__(self, "org/repo")
        self.tickets = {t.id: t for t in tickets}
        self.comments: dict[str, list[str]] = {t.id: [] for t in tickets}
        self.writes: list[tuple[str, list[str], list[str]]] = []
        self.described: list[tuple[str, str]] = []
        self.truncate = False

    def read_thread(self, ticket_id):
        if ticket_id not in self.tickets:
            return BackendError(f"{ticket_id}: not found")
        return Thread(
            self.tickets[ticket_id],
            [
                Comment(id=str(i), body=b, author="rite")
                for i, b in enumerate(self.comments[ticket_id])
            ],
            True,
        )

    def comment(self, ticket_id, text):
        self.comments[ticket_id].append(text)
        return None

    def label(self, ticket_id, labels, remove=None):
        self.writes.append((ticket_id, list(labels), list(remove or [])))
        t = self.tickets[ticket_id]
        now = [x for x in t.labels if x not in (remove or [])]
        t.labels = now + [x for x in labels if x not in now]
        return None

    def describe_label(self, name, description, color):
        self.described.append((name, description))
        return None

    def list_tickets(self, filters=None):
        f = filters or TicketFilter()
        rows = [t for t in self.tickets.values() if not f.label or f.label in t.labels]
        return TicketPage(rows, truncated=self.truncate)

    # --- what a person does to the board by hand ---------------------------

    def by_hand(self, ticket_id, add=(), remove=()):
        t = self.tickets[ticket_id]
        t.labels = [x for x in t.labels if x not in remove] + list(add)

    def removal_comments(self, ticket_id):
        return [c for c in self.comments[ticket_id] if f"removed `{READY}`" in c]


@pytest.fixture
def key(tmp_path):
    with patch.dict(
        os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "refinement-key")}
    ):
        yield refinement_key.ensure()


def ticket(tid, *labels, description="the timeout is a flag"):
    return Ticket(
        id=tid, title=f"ticket {tid}", description=description, labels=list(labels)
    )


def refine(board: Board, tid: str, key: bytes) -> None:
    """Put a real signed record for the ticket's current text on it."""
    board.comments[tid].append(
        rec.render(signed_record(board.tickets[tid], key, IDENTITY))
    )


def labelled(board: Board, tid: str) -> bool:
    return READY in board.tickets[tid].labels


class TestTheLabelFollowsTheRecord:
    def test_accepting_a_record_puts_the_label_on_at_once(self, key, tmp_path):
        board = Board(ticket("7", "scheduled"))
        with patch("rite_ai.refinement.status.board_for", return_value=board):
            done = accept.accept(tmp_path, None, "7", items=["it works"], verify=[])
        assert done.ok, done.message
        assert labelled(board, "7")
        assert board.described == [(READY, view.DESCRIPTION)]

    def test_his_ok_in_his_channel_puts_the_label_on_at_once(self, key, tmp_path):
        """The User's accept word (TR2's path), not only `rite refine accept`:
        both write through `accept.write`, and both re-label at once."""
        from tests import test_the_owner_refines_with_the_user as tr2

        board = tr2.Board(tr2.kan7())
        board.tickets["KAN-7"].labels = ["scheduled"]
        tr2._to_round_two(tmp_path, board)
        _, notes = tr2.reply_to_latest(
            tmp_path, board, "ok", sent_at=tr2.NOW + 3 * 3600
        )
        assert any(n.startswith("KAN-7 refined: record ") for n in notes), notes
        assert READY in board.labels["KAN-7"]

    def test_an_unscheduled_ticket_gets_no_label_when_accepted(self, key, tmp_path):
        board = Board(ticket("7"))
        with patch("rite_ai.refinement.status.board_for", return_value=board):
            assert accept.accept(tmp_path, None, "7", items=["it works"], verify=[]).ok
        assert not labelled(board, "7")

    def test_editing_the_description_takes_it_off_at_the_next_read(self, key):
        board = Board(ticket("7", "scheduled"))
        refine(board, "7", key)
        view.reconcile(board)
        assert labelled(board, "7")
        board.tickets["7"].description = "something else entirely"
        done = view.reconcile(board)
        assert not labelled(board, "7")
        assert [(c.ticket, c.added, c.state) for c in done.changes] == [
            ("7", False, st.STALE)
        ]
        assert len(board.removal_comments("7")) == 1

    def test_a_ticket_with_a_managers_name_is_not_ready(self, key):
        board = Board(ticket("7", "scheduled", "beta", READY))
        refine(board, "7", key)
        view.reconcile(board, ["beta"])
        assert not labelled(board, "7")
        assert board.removal_comments("7") == [], "nothing is wrong with the ticket"


class TestAssignmentTakesItOff:
    def test_in_the_same_write_that_takes_scheduled_off(self, key):
        board = Board(ticket("7", "beta", "scheduled", READY))
        refine(board, "7", key)
        distribute(
            None,
            board,
            manager="beta",
            workers=["w1"],
            schedule=ScheduleConfig(
                timezone="UTC", windows=[ScheduleWindow(hours="09:00-18:00", workers=1)]
            ),
            now=NOON,
            busy=set(),
        )
        assert board.writes == [("7", ["w1"], ["beta", "scheduled", READY])]
        assert not labelled(board, "7")

    def test_a_ticket_without_it_is_not_asked_to_lose_it(self, key):
        """gh refuses to remove a label the repository lacks (measured), so
        removing one the ticket does not carry would fail the assignment."""
        board = Board(ticket("7", "beta", "scheduled"))
        refine(board, "7", key)
        distribute(
            None,
            board,
            manager="beta",
            workers=["w1"],
            schedule=ScheduleConfig(
                timezone="UTC", windows=[ScheduleWindow(hours="09:00-18:00", workers=1)]
            ),
            now=NOON,
            busy=set(),
        )
        assert board.writes == [("7", ["w1"], ["beta", "scheduled"])]


class TestAHandAddedLabel:
    def test_starts_nothing(self, key):
        """The gate reads the record: an unrefined ticket carrying the label
        is held back, and the board is not written."""
        board = Board(ticket("7", "beta", "scheduled", READY))
        got = distribute(
            None,
            board,
            manager="beta",
            workers=["w1"],
            schedule=ScheduleConfig(
                timezone="UTC", windows=[ScheduleWindow(hours="09:00-18:00", workers=1)]
            ),
            now=NOON,
            busy=set(),
        )
        assert got.handouts == [] and board.writes == []
        assert "NOT REFINED" in got.held_back["7"]

    def test_is_removed_with_one_comment_however_often_it_is_re_added(self, key):
        board = Board(ticket("7", "scheduled"))
        told = []
        for _ in range(3):
            board.by_hand("7", add=[READY])
            done = view.reconcile(board)
            assert not labelled(board, "7")
            told.append(done.instruction())
        assert len(board.removal_comments("7")) == 1
        assert "state: NOT REFINED" in board.removal_comments("7")[0]
        # The Owner is told every time: a Manager adding it is worth knowing.
        assert all(f"**7**: rite removed `{READY}`" in t for t in told)

    def test_a_new_record_state_gets_its_own_comment(self, key):
        board = Board(ticket("7", "scheduled"))
        board.by_hand("7", add=[READY])
        view.reconcile(board)
        refine(board, "7", key)
        board.tickets["7"].description = "edited after the record"
        board.by_hand("7", add=[READY])
        view.reconcile(board)
        said = board.removal_comments("7")
        assert len(said) == 2
        assert "NOT REFINED" in said[0] and "STALE" in said[1]


class TestAHandRemovedLabel:
    def test_blocks_nothing_and_is_back_at_the_next_read(self, key):
        board = Board(ticket("7", "scheduled"))
        refine(board, "7", key)
        view.reconcile(board)
        board.by_hand("7", remove=[READY])
        done = view.reconcile(board)
        assert labelled(board, "7")
        assert [(c.ticket, c.added) for c in done.changes] == [("7", True)]
        assert board.removal_comments("7") == []
        assert done.instruction() == ""


class TestALabelLeftWhileNoRiteRan:
    def test_is_found_through_its_own_list_and_counted(self, key):
        """`scheduled` taken off by hand while nothing ran: only the second
        list finds it."""
        board = Board(ticket("7", READY), ticket("8", "scheduled"))
        refine(board, "8", key)
        done = view.reconcile(board)
        assert not labelled(board, "7") and labelled(board, "8")
        assert "corrected 2 label(s) (1 added, 1 removed)" in done.line()

    def test_a_list_cut_short_is_said_never_called_reconciled(self, key):
        board = Board(ticket("7", "scheduled"))
        board.truncate = True
        done = view.reconcile(board)
        assert not done.complete
        assert "did not see every ticket" in done.line()

    def test_an_unreadable_ticket_keeps_its_label_and_is_said(self, key, tmp_path):
        """No key (as inside a sandbox): a REFINED ticket reads UNREADABLE,
        and stripping its label on that would be a guess."""
        board = Board(ticket("7", "scheduled", READY))
        refine(board, "7", key)
        with patch.dict(
            os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "none")}
        ):
            done = view.reconcile(board)
        assert labelled(board, "7") and board.writes == []
        assert "UNREADABLE" in done.line()


class TestTheOwnersCycle:
    def test_the_brief_reconciles_says_the_count_and_tells_the_owner(
        self, key, project
    ):
        from rite_ai.config.parse import parse_config

        board = Board(ticket("7", "scheduled", READY), ticket("8", "scheduled"))
        refine(board, "8", key)
        said = []
        config = parse_config(project / ".rite" / "config.yaml")
        got = ri.brief(project, "lead", board, config, say=said.append)
        assert not labelled(board, "7") and labelled(board, "8")
        assert any("corrected 2 label(s)" in s for s in said)
        assert f"**7**: rite removed `{READY}`" in got

    def test_a_manager_that_does_not_refine_writes_no_label(self, key, project):
        from rite_ai.config.parse import parse_config

        board = Board(ticket("7", "scheduled", READY))
        config = parse_config(project / ".rite" / "config.yaml")
        assert ri.brief(project, "helper", board, config) == ""
        assert board.writes == []


class TestRiteBoardListReady:
    def test_computes_from_a_fresh_read_not_from_the_label(self, key):
        board = Board(
            ticket("7", "scheduled", READY),  # hand-added, not refined
            ticket("8", "scheduled"),  # refined, label missing
            ticket("9", "scheduled", "beta"),  # refined, assigned
        )
        refine(board, "8", key)
        refine(board, "9", key)
        rows, cut = view.truth(board, ["beta"])
        assert cut == ""
        assert {r.ticket.id: r.ready for r in rows} == {
            "7": False,
            "8": True,
            "9": False,
        }
        assert board.writes == [] and board.comments["7"] == []


class TestTheGitHubLabelIsDescribedOnce:
    def _backend(self, answers):
        calls = []
        backend = GitHubBackend("org/repo")

        def gh(args):
            calls.append(args)
            return answers.pop(0)

        backend._gh = gh
        return backend, calls

    def test_created_with_its_description_when_missing(self):
        backend, calls = self._backend(
            [BackendError("gh exited 1: gh: Not Found (HTTP 404)"), "{}"]
        )
        assert backend.describe_label(READY, view.DESCRIPTION, view.COLOUR) is None
        assert calls[1][:4] == ["api", "-X", "POST", "repos/org/repo/labels"]
        assert f"description={view.DESCRIPTION}" in calls[1]
        # Once per backend.
        assert backend.describe_label(READY, view.DESCRIPTION, view.COLOUR) is None
        assert len(calls) == 2

    def test_an_existing_label_is_left_as_the_person_made_it(self):
        backend, calls = self._backend(['{"name": "ready-to-work", "color": "ff0000"}'])
        assert backend.describe_label(READY, view.DESCRIPTION, view.COLOUR) is None
        assert len(calls) == 1 and "POST" not in calls[0]
