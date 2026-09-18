"""A Manager refuses an assignment (P2-4c).

The Owner labels without asking (§2.3), so a refusal is a board action taken
afterwards: the ticket goes back to the pool with the reason on it. Every test
asserts what the BACKEND was told, because the label is the assignment — a
refusal that only changed a return value would leave work assigned to a
machine that cannot do it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rite_ai.config.models import ScheduleConfig, ScheduleWindow
from rite_ai.coordination.distribution import Distributed, distribute
from rite_ai.coordination.refusal import (
    NotRefused,
    Refused,
    refusal_reason,
    refuse_assignment,
)
from rite_ai.tickets import BackendError, Ticket

NOON = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
NIGHT = datetime(2026, 9, 17, 23, 0, tzinfo=UTC)


def schedule(workers=2, hours="09:00-18:00"):
    return ScheduleConfig(
        timezone="UTC", windows=[ScheduleWindow(hours=hours, workers=workers)]
    )


class FakeBackend:
    def __init__(self, tickets, refuse_label=None, refuse_comment=None):
        self.tickets = tickets
        self.refuse_label = refuse_label or set()
        self.refuse_comment = refuse_comment or set()
        self.writes: list[tuple[str, list[str], list[str]]] = []
        self.comments: list[tuple[str, str]] = []

    def list_tickets(self, filters=None):
        return list(self.tickets)

    def label(self, ticket_id, labels, remove=None):
        if ticket_id in self.refuse_label:
            return BackendError("the board rejected the label")
        self.writes.append((ticket_id, list(labels), list(remove or [])))
        return None

    def comment(self, ticket_id, text):
        if ticket_id in self.refuse_comment:
            return BackendError("comments are disabled")
        self.comments.append((ticket_id, text))
        return None


def ticket(tid, *labels):
    return Ticket(id=tid, title=tid, labels=list(labels))


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".rite").mkdir()
    return tmp_path


class TestDecidingToRefuse:
    def test_a_module_this_machine_lacks_is_a_reason(self):
        why = refusal_reason(ticket("ABC-1", "module:ios"), modules={"backend"})
        assert why and "ios" in why
        assert "backend" in why, "it must say what this machine DOES have"

    def test_a_module_this_machine_has_is_not(self):
        assert (
            refusal_reason(ticket("ABC-1", "module:ios"), modules={"ios", "backend"})
            is None
        )

    def test_a_ticket_with_no_module_label_carries_no_requirement(self):
        """Additive by construction: a board that never uses the convention
        behaves exactly as it did before it existed."""
        assert refusal_reason(ticket("ABC-1", "beta"), modules=set()) is None

    def test_shutting_down_refuses_everything_including_work_it_could_do(self):
        why = refusal_reason(
            ticket("ABC-1", "module:ios"), modules={"ios"}, draining="stop requested"
        )
        assert why and "shutting down" in why


class TestReturningItToThePool:
    def test_the_ticket_goes_back_with_scheduled_and_loses_our_name(self):
        backend = FakeBackend([])
        got = refuse_assignment(backend, "ABC-1", manager="beta", reason="no module")
        assert isinstance(got, Refused)
        assert backend.writes == [("ABC-1", ["scheduled"], ["beta"])]

    def test_the_reason_lands_on_the_ticket(self):
        backend = FakeBackend([])
        refuse_assignment(backend, "ABC-1", manager="beta", reason="no module 'ios'")
        assert len(backend.comments) == 1
        assert "beta cannot take this ticket" in backend.comments[0][1]
        assert "no module 'ios'" in backend.comments[0][1]

    def test_a_ticket_returned_without_its_explanation_is_still_returned(self):
        """The board's STATE matters more than its prose: a ticket left
        carrying this Manager's name is work nobody is doing. The missing
        explanation is reported instead of pretended."""
        backend = FakeBackend([], refuse_comment={"ABC-1"})
        got = refuse_assignment(backend, "ABC-1", manager="beta", reason="no module")
        assert isinstance(got, Refused)
        assert got.reason_posted is False
        assert backend.writes, "the ticket was not returned"

    def test_a_failed_relabel_posts_no_comment_at_all(self):
        """The opposite order's failure: a comment explaining a refusal that
        did not happen, on a ticket this Manager still holds."""
        backend = FakeBackend([], refuse_label={"ABC-1"})
        got = refuse_assignment(backend, "ABC-1", manager="beta", reason="no module")
        assert isinstance(got, NotRefused)
        assert backend.comments == []


class TestRefusalDuringDistribution:
    def test_work_for_a_missing_module_goes_back_instead_of_to_a_worker(self, root):
        backend = FakeBackend(
            [ticket("ABC-1", "beta", "module:ios"), ticket("ABC-2", "beta")]
        )
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2"],
            schedule=schedule(),
            now=NOON,
            modules={"backend"},
        )
        assert isinstance(got, Distributed)
        assert got.handouts == [("ABC-2", "w1")]
        assert "ABC-1" in got.refused
        assert ("ABC-1", ["scheduled"], ["beta"]) in backend.writes

    def test_a_shutting_down_manager_returns_everything(self, root):
        backend = FakeBackend([ticket("ABC-1", "beta"), ticket("ABC-2", "beta")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2"],
            schedule=schedule(),
            now=NOON,
            draining="stop requested",
        )
        assert got.handouts == []
        assert set(got.refused) == {"ABC-1", "ABC-2"}

    def test_being_full_HOLDS_rather_than_refusing(self, root):
        """The distinction that keeps the board quiet. "Full" self-corrects
        in minutes; returning it hands the ticket to a pool that gives it
        straight back, with a comment every time. So a full Manager holds,
        and holds SILENTLY — no board writes at all."""
        backend = FakeBackend([ticket(f"ABC-{i}", "beta") for i in range(1, 4)])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2", "w3"],
            schedule=schedule(workers=1),
            now=NOON,
        )
        assert len(got.handouts) == 1
        assert got.refused == {}
        assert set(got.held_back) == {"ABC-2", "ABC-3"}
        assert len(backend.writes) == 1, "a held ticket was written to the board"
        assert backend.comments == []

    def test_an_off_window_still_returns_work_it_could_never_do(self, root):
        """Capacity and capability are different questions. Holding a ticket
        for a module this machine does not have until a window that will
        refuse it anyway just delays the refusal by a night."""
        backend = FakeBackend([ticket("ABC-1", "beta", "module:ios")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1"],
            schedule=schedule(),
            now=NIGHT,
            modules={"backend"},
        )
        assert "ABC-1" in got.refused
        assert got.held_back == {}

    def test_a_refusal_the_board_rejects_is_neither_refused_nor_handed_out(
        self, root
    ):
        """The third state, and the one a caller must not read as either of
        the others: still ours, still undoable."""
        backend = FakeBackend(
            [ticket("ABC-1", "beta", "module:ios")], refuse_label={"ABC-1"}
        )
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1"],
            schedule=schedule(),
            now=NOON,
            modules={"backend"},
        )
        assert got.refused == {}
        assert got.handouts == []
        assert "ABC-1" in got.could_not_refuse
