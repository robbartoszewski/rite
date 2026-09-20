"""A Manager hands its tickets to its Workers (P2-4b, §2.7, D-44).

The label IS the assignment, so every test here asserts what the BACKEND was
told, not what the return value says. A module that reported perfect
distribution while writing nothing would pass a looser suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.models import ScheduleConfig, ScheduleWindow
from rite_ai.coordination.distribution import (
    Distributed,
    NotDistributed,
    distribute,
)
from rite_ai.tickets import BackendError, Ticket

NOON = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
NIGHT = datetime(2026, 9, 17, 23, 0, tzinfo=UTC)


def schedule(workers=3, hours="09:00-18:00", timezone="UTC"):
    return ScheduleConfig(
        timezone=timezone, windows=[ScheduleWindow(hours=hours, workers=workers)]
    )


class FakeBackend:
    """Records every label write, and can be told to refuse one."""

    def __init__(self, tickets, refuse: set[str] | None = None, listing=None):
        self.tickets = tickets
        self.refuse = refuse or set()
        self.listing = listing
        self.writes: list[tuple[str, list[str], list[str]]] = []
        self.filters: list = []

    def list_tickets(self, filters=None):
        self.filters.append(filters)
        if self.listing is not None:
            return self.listing
        return list(self.tickets)

    def label(self, ticket_id, labels, remove=None):
        if ticket_id in self.refuse:
            return BackendError(f"{ticket_id} is locked")
        self.writes.append((ticket_id, list(labels), list(remove or [])))
        return None


def ticket(tid, *labels):
    return Ticket(id=tid, title=tid, labels=list(labels))


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".rite").mkdir()
    return tmp_path


def busy(root: Path, worker: str, ticket_id: str, path: str):
    ClaimsLedger(root / ".rite" / "claims.json").claim([path], worker, ticket_id)


class TestHandingOutWork:
    def test_an_assigned_ticket_goes_to_a_free_worker(self, root):
        backend = FakeBackend([ticket("ABC-1", "beta", "scheduled")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2"],
            schedule=schedule(),
            now=NOON,
        )
        assert isinstance(got, Distributed)
        assert got.handouts == [("ABC-1", "w1")]

    def test_the_manager_label_and_scheduled_come_off_together(self, root):
        """The label IS the assignment. A ticket left carrying both its
        Manager's name and a Worker's answers two questions at once — the
        same failure §9.10 warns about in the other direction."""
        backend = FakeBackend([ticket("ABC-1", "beta", "scheduled")])
        distribute(
            root,
            backend,
            manager="beta",
            workers=["w1"],
            schedule=schedule(),
            now=NOON,
        )
        assert backend.writes == [("ABC-1", ["w1"], ["beta", "scheduled"])]

    def test_workers_are_taken_in_configured_order_because_they_are_fungible(
        self, root
    ):
        """§5.3.4. There is nothing to match against a ticket, so a clever
        pick here would be inventing a routing rule the spec does not have."""
        backend = FakeBackend([ticket("ABC-1", "beta"), ticket("ABC-2", "beta")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2", "w3"],
            schedule=schedule(),
            now=NOON,
        )
        assert got.handouts == [("ABC-1", "w1"), ("ABC-2", "w2")]

    def test_tickets_for_other_managers_are_left_alone(self, root):
        backend = FakeBackend(
            [ticket("ABC-1", "gamma"), ticket("ABC-2", "beta")],
        )
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2"],
            schedule=schedule(),
            now=NOON,
        )
        assert got.handouts == [("ABC-2", "w1")]
        assert [w[0] for w in backend.writes] == ["ABC-2"]


class TestTheScheduleDecidesHowMany:
    def test_capacity_comes_from_the_schedule_not_from_the_worker_list(self, root):
        """D-44: how many Workers run is the user's decision, from data —
        never computed here. Four named Workers and four tickets, but the
        schedule says two."""
        backend = FakeBackend([ticket(f"ABC-{i}", "beta") for i in range(1, 5)])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2", "w3", "w4"],
            schedule=schedule(workers=2),
            now=NOON,
        )
        assert got.capacity == 2
        assert len(got.handouts) == 2
        assert set(got.held_back) == {"ABC-3", "ABC-4"}

    def test_busy_workers_use_up_scheduled_slots(self, root):
        """A Manager with three scheduled slots and two Workers already
        holding claims may hand out ONE more, whatever its worker list says."""
        busy(root, "w1", "OLD-1", "src/a.py")
        busy(root, "w2", "OLD-2", "src/b.py")
        backend = FakeBackend([ticket("ABC-1", "beta"), ticket("ABC-2", "beta")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2", "w3", "w4"],
            schedule=schedule(workers=3),
            now=NOON,
        )
        assert got.handouts == [("ABC-1", "w3")]
        assert "ABC-2" in got.held_back
        assert "2 of 3 scheduled slots in use" in got.held_back["ABC-2"]

    def test_an_off_window_hands_out_nothing_and_says_the_schedule_chose_it(self, root):
        """§2.7.3: zero Workers is a clean stop, not a stall and not an
        error to work around."""
        backend = FakeBackend([ticket("ABC-1", "beta")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1"],
            schedule=schedule(),
            now=NIGHT,
        )
        assert got.capacity == 0
        assert got.handouts == []
        assert backend.writes == []
        assert "0 Workers in this window" in got.held_back["ABC-1"]

    def test_an_unresolvable_timezone_falls_back_LOUDLY_not_silently(self, root):
        """⚠ THIS TEST'S POLICY CHANGED, deliberately, and the old one is
        worth reading before changing it back.

        It asserted `NotDistributed` — refuse rather than guess — citing
        §2.7's "a schedule with no timezone is a trap". The concern is
        right; refusing here was not, because **only this subsystem did
        it.** `resolve_zone` had already relaxed D-48 to a machine-local
        default, so on the documented default of an unset timezone
        `sandbox.start_worker` enforced the schedule against the machine
        clock while distribution assigned nothing at all. Two subsystems,
        opposite behaviour, one config — and the half that refused was the
        half nobody was watching.

        The trap is now closed by LOUDNESS rather than by refusal: a
        rejected zone is named in `ResolvedZone.describe()` and reported by
        `validate_schedule`, so `rite doctor` says which zone it could not
        use and which clock it fell back to. An unset one is still reported
        too. Nothing is silently on the wrong clock; the difference is that
        work continues while the operator is told.

        ⚠ SPEC D-48 still reads "Required field, no default" and §2.7 still
        calls a timezone-less schedule a trap, while `resolve_zone`'s
        docstring says it RELAXES D-48. The code is now consistent with
        itself; the spec has not been updated to match and that is somebody
        else's decision, recorded here rather than resolved quietly.
        """
        backend = FakeBackend([ticket("ABC-1", "beta")])
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1"],
            schedule=schedule(timezone="Mars/Olympus"),
            now=NOON,
        )
        assert not isinstance(got, NotDistributed), (
            "distribution refused on an unresolvable timezone while "
            "`start_worker` enforces the schedule against the machine clock "
            "— the same config, two behaviours"
        )

        from rite_ai.schedule import resolve_zone

        described = resolve_zone("Mars/Olympus").describe()
        assert "Mars/Olympus" in described and "machine local" in described, (
            f"the fallback is silent, which is the trap §2.7 names: {described!r}"
        )


class TestWhenAWriteFails:
    def test_a_refused_label_leaves_the_ticket_and_frees_the_worker_for_the_next(
        self, root
    ):
        """Reporting an assignment that did not happen is worse than
        reporting none — and the Worker it was meant for is still free."""
        backend = FakeBackend(
            [ticket("ABC-1", "beta"), ticket("ABC-2", "beta")], refuse={"ABC-1"}
        )
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2"],
            schedule=schedule(),
            now=NOON,
        )
        assert got.handouts == [("ABC-2", "w1")], got.handouts
        assert "refused" in got.held_back["ABC-1"]

    def test_an_unreadable_board_distributes_nothing(self, root):
        backend = FakeBackend([], listing=BackendError("the board is unreachable"))
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1"],
            schedule=schedule(),
            now=NOON,
        )
        assert isinstance(got, NotDistributed)
        assert backend.writes == []

    def test_a_backend_that_ignores_the_label_filter_is_still_filtered_here(self, root):
        """Backends differ in how faithfully they filter. Trusting the
        query would hand another Manager's ticket to our Worker — a
        cross-machine seizure caused by a backend quirk."""
        backend = FakeBackend(
            [], listing=[ticket("ABC-1", "gamma"), ticket("ABC-2", "beta")]
        )
        got = distribute(
            root,
            backend,
            manager="beta",
            workers=["w1", "w2"],
            schedule=schedule(),
            now=NOON,
        )
        assert got.handouts == [("ABC-2", "w1")]
