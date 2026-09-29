"""A board read straight after rite writes a ticket sees the write (DF4).

**Measured** 2026-09-29 on `rite-dogfood-board`, gh 2.98.0: after an issue is
created and labelled `scheduled`, `gh issue list --label scheduled` missed it
for 1.6 to 6.5 s (5 of 5) while `gh issue view` returned it at once; after
`scheduled` was removed, the list still returned it for up to 1.9 s (3 of
3). Jira documents the same for `/search/jql`. So `rite start` right after a
ticket was filed read "nothing ready" and stopped.

**Pre-registered (plan DF4):** a `rite start` issued immediately after
`rite board` creates and schedules an issue sees it on its FIRST read, with
no sleep or retry anywhere in the path. The live half of that is measured
against real GitHub and recorded in the PR; this file holds the property
against a board that behaves as measured: a list that serves an old
snapshot, and a single read that is current. Each case has a control
showing the same board, read without rite's ledger, gets it wrong.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rite_ai.tickets import BackendError, Ticket, TicketFilter
from rite_ai.tickets.interface import TicketBackend, TicketPage
from rite_ai.tickets.own_writes import ReadsItsOwnWrites, ledger_path, written
from tests.refined_board import refined

T0 = datetime(2026, 9, 29, 3, 13, tzinfo=UTC)


class LaggingBoard(TicketBackend):
    """GitHub as measured: writes land at once for `read`; `list_tickets`
    answers from `index`, which only moves when `catch_up()` is called."""

    def __init__(self):
        self.issues: dict[str, Ticket] = {}
        self.index: dict[str, Ticket] = {}
        self.clock = T0
        self.reads = 0

    def _touch(self, t: Ticket) -> None:
        self.clock += timedelta(seconds=1)
        t.updated_at = self.clock

    def catch_up(self) -> None:
        self.index = copy.deepcopy(self.issues)

    def create(self, title, description="", labels=None):
        n = str(len(self.issues) + 1)
        t = Ticket(id=n, title=title, status="OPEN", labels=list(labels or []))
        self._touch(t)
        self.issues[n] = t
        return copy.deepcopy(t)

    def read(self, ticket_id):
        self.reads += 1
        if ticket_id not in self.issues:
            return BackendError(
                "gh exited 1: GraphQL: Could not resolve to an issue or pull "
                f"request with the number of {ticket_id}."
            )
        return copy.deepcopy(self.issues[ticket_id])

    def label(self, ticket_id, labels, remove=None):
        t = self.issues[ticket_id]
        t.labels = [x for x in t.labels if x not in (remove or [])]
        t.labels += [x for x in labels if x not in t.labels]
        self._touch(t)

    def move(self, ticket_id, status):
        t = self.issues[ticket_id]
        t.status = "OPEN" if status.lower() == "open" else "CLOSED"
        self._touch(t)

    def matches(self, ticket, filters):
        f = filters or TicketFilter()
        state = (f.status or "open").upper()
        if state != "ALL" and ticket.status != state:
            return False
        return not f.label or f.label in ticket.labels

    def missing(self, error):
        return "Could not resolve to an issue" in error.message

    def list_tickets(self, filters=None):
        return TicketPage(
            [copy.deepcopy(t) for t in self.index.values() if self.matches(t, filters)]
        )

    def assign(self, ticket_id, worker):
        return None

    def update(self, ticket_id, **fields):
        return None

    def comment(self, ticket_id, text):
        return None

    def query(self, raw_query):
        return []

    def link(self, ticket_id, target_id, link_type):
        return None


SCHEDULED = TicketFilter(label="scheduled")


@pytest.fixture
def board():
    return LaggingBoard()


def _rite(board, root: Path) -> ReadsItsOwnWrites:
    (root / ".rite").mkdir(exist_ok=True)
    return ReadsItsOwnWrites(board, root, "github:o/r")


def _ids(result) -> list[str]:
    assert not isinstance(result, BackendError), result
    return sorted(t.id for t in result)


class TestAWriteIsReadBack:
    def test_a_ticket_rite_created_and_scheduled_is_listed_at_once(
        self, board, tmp_path
    ):
        rite = _rite(board, tmp_path)
        made = rite.create("retry on fail", labels=["scheduled"])
        # Control: the board's own list, right now, does not have it.
        assert _ids(board.list_tickets(SCHEDULED)) == []
        assert _ids(rite.list_tickets(SCHEDULED)) == [made.id]

    def test_scheduling_an_existing_ticket_is_listed_at_once(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        made = rite.create("add tests")
        board.catch_up()
        rite.label(made.id, ["scheduled"])
        assert _ids(board.list_tickets(SCHEDULED)) == []  # control
        assert _ids(rite.list_tickets(SCHEDULED)) == [made.id]

    def test_a_ticket_rite_took_scheduled_off_is_not_offered_again(
        self, board, tmp_path
    ):
        """The reverse race: a stale list offering a ticket just dispatched."""
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        board.catch_up()
        rite.label(made.id, ["alpha"], remove=["scheduled"])
        assert _ids(board.list_tickets(SCHEDULED)) == [made.id]  # control
        assert _ids(rite.list_tickets(SCHEDULED)) == []

    def test_a_ticket_rite_closed_is_not_offered_again(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        board.catch_up()
        rite.move(made.id, "closed")
        assert _ids(board.list_tickets(SCHEDULED)) == [made.id]  # control
        assert _ids(rite.list_tickets(SCHEDULED)) == []

    def test_another_rite_process_reads_it_back_too(self, board, tmp_path):
        """`rite board create` is one process, `rite start` another: the
        ledger, not the object, carries the write."""
        made = _rite(board, tmp_path).create("t", labels=["scheduled"])
        fresh = ReadsItsOwnWrites(board, tmp_path, "github:o/r")
        assert _ids(fresh.list_tickets(SCHEDULED)) == [made.id]

    def test_another_board_is_not_asked(self, board, tmp_path):
        _rite(board, tmp_path).create("t", labels=["scheduled"])
        other = ReadsItsOwnWrites(board, tmp_path, "github:o/other")
        assert _ids(other.list_tickets(SCHEDULED)) == []
        assert board.reads == 0


class TestTheLedgerEmptiesOnlyWhenTheListHasCaughtUp:
    def test_kept_while_the_list_lags_dropped_once_it_agrees(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        rite.list_tickets(SCHEDULED)
        assert [w.ticket for w in written(tmp_path, "github:o/r")] == [made.id]
        board.catch_up()
        assert _ids(rite.list_tickets(SCHEDULED)) == [made.id]
        assert written(tmp_path, "github:o/r") == []

    def test_a_ticket_that_no_longer_matches_is_settled_by_its_own_state(
        self, board, tmp_path
    ):
        """Unlabelled and closed: no readiness list will ever show it, so it
        is checked with a list of its current state, the same call."""
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        board.catch_up()
        rite.move(made.id, "closed")
        rite.list_tickets(SCHEDULED)
        assert written(tmp_path, "github:o/r") != []
        board.catch_up()
        rite.list_tickets(SCHEDULED)
        assert written(tmp_path, "github:o/r") == []

    def test_an_older_matching_version_does_not_settle_it(self, board, tmp_path):
        """Index at v1 (scheduled), current v3 (scheduled again after an
        unlabel): same labels, different `updated`, so not caught up."""
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        board.catch_up()
        rite.label(made.id, [], remove=["scheduled"])
        rite.label(made.id, ["scheduled"])
        rite.list_tickets(SCHEDULED)
        assert [w.ticket for w in written(tmp_path, "github:o/r")] == [made.id]

    def test_a_write_after_the_read_is_not_forgotten(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        board.catch_up()
        real_read = board.read

        def read_then_someone_writes(ticket_id):
            got = real_read(ticket_id)
            rite.label(made.id, ["again"])  # recorded mid-list
            board.read = real_read
            return got

        board.read = read_then_someone_writes
        rite.list_tickets(SCHEDULED)
        assert [w.ticket for w in written(tmp_path, "github:o/r")] == [made.id]


class TestWhatCannotBeToldIsSaid:
    def test_a_ticket_rite_wrote_that_cannot_be_read_back_is_an_error(
        self, board, tmp_path
    ):
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        board.read = lambda ticket_id: BackendError("gh exited 1: rate limited")
        got = rite.list_tickets(SCHEDULED)
        assert isinstance(got, BackendError)
        assert made.id in got.message and "cannot be told" in got.message

    def test_a_deleted_ticket_is_dropped_not_an_error_for_ever(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        made = rite.create("t", labels=["scheduled"])
        del board.issues[made.id]
        assert _ids(rite.list_tickets(SCHEDULED)) == []
        assert written(tmp_path, "github:o/r") == []

    def test_an_unreadable_ledger_is_an_error_not_an_empty_one(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        ledger_path(tmp_path).write_text("{not json")
        assert isinstance(rite.list_tickets(SCHEDULED), BackendError)

    def test_a_failed_write_is_not_recorded(self, board, tmp_path):
        rite = _rite(board, tmp_path)
        board.label = lambda *a, **k: BackendError("gh exited 1: no")
        rite.label("999", ["scheduled"])
        assert written(tmp_path, "github:o/r") == []

    def test_a_person_created_ticket_is_still_subject_to_the_lag(self, board, tmp_path):
        """Nothing to key on: said by the idle verdict, not solved here."""
        rite = _rite(board, tmp_path)
        board.create("filed on the web page", labels=["scheduled"])
        assert _ids(rite.list_tickets(SCHEDULED)) == []


class TestTheLoopSeesIt:
    def test_a_cycle_right_after_rite_filed_a_ticket_is_ready_not_idle(
        self, board, tmp_path
    ):
        from rite_ai.loop import IDLE, READY, plan_cycle

        root = tmp_path / "proj"
        rite_dir = root / ".rite"
        rite_dir.mkdir(parents=True)
        (rite_dir / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\nwhat:\n  kind: app\n"
            "technology:\n  languages:\n    - python\n"
        )
        (rite_dir / "modules.yaml").write_text("modules: {}\n")
        (rite_dir / "config.yaml").write_text(
            "ticket_backend:\n  type: none\n"
            "schedule:\n  timezone: UTC\n  windows:\n    - hours: '00:00-23:59'\n"
            "      workers: 1\n"
        )
        w = root / "workers" / "alpha"
        w.mkdir(parents=True)
        (w / "worker.yml").write_text(
            "worker:\n  name: alpha\n  manager: ''\n  modules: []\n"
        )
        rite = ReadsItsOwnWrites(board, root, "github:o/r")
        rite.create("KAN-7 timeout", labels=["scheduled"])
        now = datetime.now(UTC).replace(hour=12)
        # Control: the same cycle on the board's own list is idle.
        # The ticket is REFINED: this is about the list, not refinement (TR2).
        assert (
            plan_cycle(root, board=board, now=now, refinement=refined).verdict == IDLE
        )
        assert (
            plan_cycle(root, board=rite, now=now, refinement=refined).verdict == READY
        )


class TestEachBoardsOwnFilter:
    def test_github_lists_open_issues_unless_told_otherwise(self):
        from rite_ai.tickets.github import GitHubBackend

        gh = GitHubBackend("o/r")
        closed = Ticket(id="1", title="t", status="CLOSED", labels=["scheduled"])
        assert gh.matches(closed, SCHEDULED) is False
        assert gh.matches(closed, TicketFilter(status="closed")) is True
        opened = Ticket(id="2", title="t", status="OPEN", labels=["Scheduled"])
        assert gh.matches(opened, SCHEDULED) is True  # GitHub labels ignore case

    def test_github_names_a_missing_issue_as_measured(self):
        from rite_ai.tickets.github import GitHubBackend

        said = BackendError(
            "gh exited 1: GraphQL: Could not resolve to an issue or pull request "
            "with the number of 999999. (repository.issue)"
        )
        assert GitHubBackend("o/r").missing(said)
        assert not GitHubBackend("o/r").missing(BackendError("gh exited 1: 502"))

    def test_jira_filters_by_project_status_and_label_and_admits_assignee(self):
        from rite_ai.tickets.jira import JiraBackend, JiraConfig

        config = JiraConfig(site="x", email="e", token="t", project_key="KAN")
        jira = JiraBackend(config)
        t = Ticket(id="KAN-7", title="t", status="To Do", labels=["scheduled"])
        assert jira.matches(t, SCHEDULED) is True
        assert jira.matches(t, TicketFilter(status="to do")) is True
        assert jira.matches(Ticket(id="RT-1", title="t"), None) is False
        assert jira.matches(t, TicketFilter(assignee="alpha")) is None


class TestTheWrapperHidesNothing:
    def test_every_backend_method_is_forwarded(self):
        """A method the base class implements (a fail-closed default) is found
        on the wrapper before `__getattr__` is ever asked, so a wrapper that
        does not define it answers with the base's default instead of the
        board's. Found when TR1 added `read_thread` while this was in review:
        every wrapped board said it could not read comments."""
        import inspect

        missing = [
            name
            for name, _ in inspect.getmembers(TicketBackend, inspect.isfunction)
            if not name.startswith("_") and name not in ReadsItsOwnWrites.__dict__
        ]
        assert missing == []

    def test_a_wrapped_board_reads_its_threads(self, board, tmp_path):
        from rite_ai.tickets.interface import Thread

        thread = Thread(ticket=Ticket(id="1", title="t"), comments=[], complete=True)
        board.read_thread = lambda ticket_id: thread
        assert _rite(board, tmp_path).read_thread("1") is thread
