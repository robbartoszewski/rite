"""A ticket whose work is over is never offered as ready (SCRUM-73).

**The run this is written against.** In the a9 dogfood KAN-28 was Done and
its pull request merged, and it still carried `scheduled` and
`ready-to-work`. rite's board brief listed it under "Ready to start", so a
fresh Manager asked for a Worker on it twice — at the 03:51 restart and
again at 12:49 — re-running finished work and holding a slot each time.

**Why the labels cannot be the fix.** `rite board` only ever ADDS a label
and nothing in rite removes one, so `scheduled` and `ready-to-work` outlive
the work by construction. Readiness has to read the STATUS.

Two gates, because the replay went through two doors: the list a Manager is
handed (`loop._ready`, which the board brief prints) and the request a
Manager files for a Worker (`broker.decide`). Each has its control here —
the same ticket in `To Do`, still offered, still started — because a gate
that refuses everything would hide the same run just as effectively.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rite_ai.loop import IDLE, READY, UNKNOWN
from rite_ai.loop import plan_cycle as _plan_cycle
from rite_ai.managers.broker import decide
from rite_ai.tickets import BackendError, Ticket
from rite_ai.tickets.statuses import TERMINAL_NAMES, is_terminal, is_terminal_name
from tests.refined_board import refined

NOW = 1_759_000_000.0
LABELS = ["scheduled", "ready-to-work"]


class Board:
    """The board the a9 run had: one ticket, its labels, its status."""

    def __init__(self, *tickets: Ticket, error: str = ""):
        self.tickets = list(tickets)
        self.error = error

    def list_tickets(self, _filter=None):
        if self.error:
            return BackendError(self.error)
        return self.tickets


def done(ticket_id: str = "KAN-28", status: str = "Done", **fields) -> Ticket:
    return Ticket(
        id=ticket_id,
        title="the ticket that was replayed",
        status=status,
        labels=list(LABELS),
        **fields,
    )


def todo(ticket_id: str = "KAN-28") -> Ticket:
    """The control: the same ticket, same labels, before it was finished."""
    return Ticket(
        id=ticket_id,
        title="the ticket that was replayed",
        status="To Do",
        labels=list(LABELS),
    )


def plan_cycle(root: Path, **kwargs):
    """Both clocks pinned, and every ticket REFINED through the real
    predicate — this file is about statuses, and an unrefined board would
    answer `refining` whatever the status said."""
    kwargs.setdefault("clock", NOW)
    kwargs.setdefault("now", datetime.fromtimestamp(kwargs["clock"], UTC))
    kwargs.setdefault("refinement", refined)
    kwargs.setdefault("sandbox_status", _free)
    return _plan_cycle(root, **kwargs)


def _free(_name, _root):
    class S:
        known = True

        def __str__(self):
            return "not found"

    return S()


def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\nwhat:\n  kind: app\n"
        "technology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
        "schedule:\n  timezone: UTC\n  windows:\n    - hours: '00:00-23:59'\n"
        "      workers: 2\n"
    )
    worker = root / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: []\n"
    )
    return root


class TestTheBriefOmitsIt:
    """The list a Manager is handed. This is the sentence the brief prints:
    "Ready to start: …"."""

    def test_a_done_ticket_still_labelled_ready_is_not_ready(self, tmp_path):
        cycle = plan_cycle(project(tmp_path), board=Board(done()))

        assert cycle.ready == []
        assert cycle.verdict == IDLE

    def test_the_control_in_to_do_is_still_offered(self, tmp_path):
        """⚠ Without this the fix could be "offer nothing" and pass."""
        cycle = plan_cycle(project(tmp_path), board=Board(todo()))

        assert cycle.ready == ["KAN-28"]
        assert cycle.verdict == READY

    def test_it_is_said_out_loud_and_names_the_status(self, tmp_path):
        """A dropped ticket the Owner is not told about is a board that
        looks empty for a reason nobody can see."""
        cycle = plan_cycle(project(tmp_path), board=Board(done()))

        assert len(cycle.finished) == 1
        assert "KAN-28" in cycle.finished[0]
        assert "Done" in cycle.finished[0]

    def test_a_stale_label_is_not_an_unreadable_board(self, tmp_path):
        """⚠ The trap this nearly fell into: `plan_cycle` answers `unknown`
        for anything `_ready` adds to `cycle.problems`, and `unknown` is
        what stops a run. A tidy-up note must not stop one."""
        cycle = plan_cycle(project(tmp_path), board=Board(done()))

        assert cycle.verdict != UNKNOWN
        assert cycle.problems == []

    def test_a_finished_ticket_is_not_counted_as_work_to_refine(self, tmp_path):
        """`cycle.scheduled` is what makes an unready board `refining`
        rather than `idle`. A Done ticket must not put the fleet into a
        refinement round for work that is over."""
        cycle = plan_cycle(project(tmp_path), board=Board(done()))

        assert cycle.scheduled == 0

    def test_the_finished_one_goes_and_the_live_one_stays(self, tmp_path):
        cycle = plan_cycle(
            project(tmp_path), board=Board(done("KAN-28"), todo("KAN-31"))
        )

        assert cycle.ready == ["KAN-31"]
        assert cycle.scheduled == 1

    def test_the_idle_reason_does_not_contradict_the_read(self, tmp_path):
        """⚠ "the board listed nothing waiting" is false when the board
        listed a Done ticket. The verdict is right; the sentence under it has
        to be about the read that was actually made."""
        cycle = plan_cycle(project(tmp_path), board=Board(done()))

        assert cycle.verdict == IDLE
        assert "whose status says the work is over" in cycle.detail

    def test_an_empty_board_still_says_nothing_was_waiting(self, tmp_path):
        cycle = plan_cycle(project(tmp_path), board=Board())

        assert cycle.detail.startswith("the board listed nothing waiting as of")

    def test_format_cycle_prints_it(self, tmp_path):
        from rite_ai.loop import format_cycle

        lines = format_cycle(plan_cycle(project(tmp_path), board=Board(done())))

        assert any(line.startswith("finished: KAN-28") for line in lines)


def _known(worker: str) -> bool:
    return worker == "alpha"


def _ask(**fields) -> str:
    return json.dumps(fields)


class TestNoWorkerIsStartedOnIt:
    """The second door: the request a Manager files. The brief is advice;
    this is the gate."""

    def _reader(self, *tickets: Ticket):
        by_id = {t.id: t for t in tickets}
        return lambda wanted: by_id.get(wanted.lstrip("#"))

    def test_a_request_for_a_done_ticket_is_refused_with_a_reason(self):
        decision = decide(
            _ask(worker="alpha", ticket="KAN-28"),
            _known,
            lambda _t: True,
            read_ticket=self._reader(done()),
        )

        assert not decision.ok
        assert "KAN-28" in decision.reason
        assert "Done" in decision.reason
        # Not "come back when a slot frees": there is no later moment at
        # which finished work becomes startable.
        assert not decision.full

    def test_the_control_in_to_do_still_starts(self):
        decision = decide(
            _ask(worker="alpha", ticket="KAN-28"),
            _known,
            lambda _t: True,
            read_ticket=self._reader(todo()),
        )

        assert decision.ok

    def test_a_renamed_done_column_is_still_refused(self):
        """The board's own category, not its column name: JIRA carries
        `statusCategory.key == "done"` whatever the column is called."""
        shipped = done(
            status="Shipped",
            metadata={"fields": {"status": {"statusCategory": {"key": "done"}}}},
        )
        decision = decide(
            _ask(worker="alpha", ticket="KAN-28"),
            _known,
            lambda _t: True,
            read_ticket=self._reader(shipped),
        )

        assert not decision.ok
        assert "Shipped" in decision.reason

    def test_no_reader_refuses_rather_than_assuming_not_done(self):
        """⚠ The module's own rule: every branch refuses. A caller that can
        say a ticket exists but not what its status is has answered half the
        question, and the half it answered is the one that replayed KAN-28."""
        decision = decide(
            _ask(worker="alpha", ticket="KAN-28"), _known, lambda _t: True
        )

        assert not decision.ok
        assert "status" in decision.reason

    def test_a_ticket_that_reads_back_as_nothing_is_refused(self):
        decision = decide(
            _ask(worker="alpha", ticket="KAN-28"),
            _known,
            lambda _t: True,
            read_ticket=lambda _wanted: None,
        )

        assert not decision.ok


class TestTheWiringSuppliesTheStatus:
    """⚠ `decide` refusing is worth nothing if the live path never asks it
    to. This is the composed callable the supervisor actually holds."""

    def test_the_supervisors_own_broker_refuses_a_done_ticket(self, tmp_path):
        from rite_ai.managers.broker import for_project

        root = project(tmp_path)
        handle = for_project(root, board=Board(done()), capacity=0)
        started, said = handle(_ask(worker="alpha", ticket="KAN-28"))

        assert started is False
        assert "Done" in said

    def test_the_control_reaches_the_launch(self, tmp_path, monkeypatch):
        """The same wiring, the same ticket in `To Do`: it gets as far as
        `honour`, which is where this file's interest ends."""
        from rite_ai.managers import broker

        root = project(tmp_path)
        seen = []

        def honour(_root, request, **_kw):
            seen.append(request)
            return True, "started"

        monkeypatch.setattr(broker, "honour", honour)
        handle = broker.for_project(root, board=Board(todo()), capacity=0)
        started, _said = handle(_ask(worker="alpha", ticket="KAN-28"))

        assert started is True
        assert [r.ticket for r in seen] == ["KAN-28"]

    def test_the_board_is_read_once_for_a_request(self, tmp_path):
        """Existence and status are one question asked once. Two reads could
        disagree, and a board read per predicate is a board call per
        predicate."""
        from rite_ai.managers.broker import for_project

        board = Board(done())
        reads = []
        lister = board.list_tickets
        board.list_tickets = lambda f=None: reads.append(1) or lister(f)

        for_project(project(tmp_path), board=board, capacity=0)(
            _ask(worker="alpha", ticket="KAN-28")
        )

        assert len(reads) == 1


class TestWhatCountsAsFinished:
    @pytest.mark.parametrize(
        "status", ["Done", "done", "Closed", "closed", "Cancelled", "Won't Do"]
    )
    def test_the_names_that_end_work(self, status):
        assert is_terminal(Ticket(id="K-1", title="t", status=status))

    @pytest.mark.parametrize("status", ["To Do", "In Progress", "In Review", ""])
    def test_the_names_that_do_not(self, status):
        assert not is_terminal(Ticket(id="K-1", title="t", status=status))

    def test_an_unknown_status_is_not_terminal(self):
        """⚠ Deliberately fail-OPEN here, and the one place in this file
        that is. This gate decides whether work may START, so reading an
        unfamiliar word as "finished" would park a live ticket for ever with
        nothing saying why. A board whose terminal column rite cannot name
        keeps the old behaviour."""
        assert not is_terminal(Ticket(id="K-1", title="t", status="Icebox"))

    @pytest.mark.parametrize("status", ["Resolved", "Cancelled", "Duplicate"])
    def test_a_live_column_with_a_finished_sounding_name_is_not_finished(self, status):
        """⚠ The category DECIDES when it is there, both ways. A board may
        have a column literally called "Resolved" sitting in JIRA's
        `indeterminate` category, meaning work in progress; reading the name
        as a second opinion would park that ticket for ever."""
        assert not is_terminal(
            Ticket(
                id="K-1",
                title="t",
                status=status,
                metadata={
                    "fields": {"status": {"statusCategory": {"key": "indeterminate"}}}
                },
            )
        )
        # The control: the same name, from a backend that reports no
        # category at all, is still read as finished.
        assert is_terminal(Ticket(id="K-1", title="t", status=status))

    def test_the_backends_category_beats_its_column_name(self):
        assert is_terminal(
            Ticket(
                id="K-1",
                title="t",
                status="Shipped",
                metadata={"fields": {"status": {"statusCategory": {"key": "done"}}}},
            )
        )

    @pytest.mark.parametrize(
        "metadata",
        [
            None,
            {},
            {"fields": None},
            {"fields": {"status": "Done"}},
            {"fields": {"status": {"statusCategory": None}}},
            {"fields": {"status": {"statusCategory": {"key": None}}}},
            {"fields": {"status": {"statusCategory": {"key": ""}}}},
        ],
    )
    def test_a_metadata_shape_it_does_not_recognise_falls_back_to_the_name(
        self, metadata
    ):
        """`metadata` is whatever the backend kept, so every step down is
        checked. None of these may raise, and none of them may read as
        finished off a live status name."""
        assert not is_terminal(
            Ticket(id="K-1", title="t", status="To Do", metadata=metadata)
        )
        assert is_terminal(
            Ticket(id="K-1", title="t", status="Done", metadata=metadata)
        )

    def test_nothing_at_all_is_not_finished(self):
        assert not is_terminal(None)
        assert not is_terminal_name("")
        assert not is_terminal_name(None)

    def test_it_still_contains_the_github_backends_own_vocabulary(self):
        """⚠ The drift this catches. `GitHubBackend._CLOSE_STATUSES` is the
        list `move` and `list_tickets` already share, and it is left alone
        deliberately — it doubles as the names `move` accepts as a
        destination. This set starts from it, so a name added there and not
        here would be a ticket GitHub closes and rite still offers."""
        from rite_ai.tickets.github import GitHubBackend

        assert GitHubBackend._CLOSE_STATUSES <= TERMINAL_NAMES


class TestTheOwnerDoesNotAssignIt:
    """The door before a Worker start: the Owner labelling a waiting ticket
    with a MANAGER's name. KAN-28 reached a Manager this way — the ticket
    was Done, `scheduled` was still on it, and nothing read the status."""

    def _pool(self, tmp_path, *tickets):
        from tests.test_local_wiring import _pool_setup, _project_for_pool

        layer, board = _pool_setup(tmp_path, [], {"alpha": 0, "beta": 2})
        board.tickets = list(tickets)
        return layer, board, _project_for_pool(["alpha", "beta"])

    def _assign(self, tmp_path, *tickets):
        from rite_ai.scheduler import _assign_the_pool
        from tests.test_local_wiring import NOW as TICK

        layer, board, proj = self._pool(tmp_path, *tickets)
        lines = _assign_the_pool(
            layer, proj.config.coordination, proj, "alpha", board, TICK, refined
        )
        return board, lines

    def test_a_done_ticket_is_assigned_to_nobody(self, tmp_path):
        board, lines = self._assign(tmp_path, done("KAN-28"))

        assert board.labelled == []
        assert any("KAN-28" in line and "Done" in line for line in lines)

    def test_the_control_is_still_assigned(self, tmp_path):
        board, lines = self._assign(tmp_path, todo("KAN-28"))

        assert board.labelled == [("KAN-28", ["alpha"])], lines

    def test_the_live_one_is_assigned_while_the_finished_one_is_named(self, tmp_path):
        board, lines = self._assign(tmp_path, done("KAN-28"), todo("KAN-31"))

        assert board.labelled == [("KAN-31", ["alpha"])], lines
        assert any("KAN-28" in line for line in lines)


class TestAManagerDoesNotHandItToAWorker:
    """And the door after assignment: a Manager sub-assigning its own
    tickets. A Manager's name can be put on a ticket by hand, and a ticket
    can be finished after it was assigned, so the label cannot say."""

    @pytest.fixture
    def key(self, tmp_path):
        import os
        from unittest.mock import patch

        from rite_ai.refinement import key as refinement_key

        with patch.dict(
            os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "refinement-key")}
        ):
            yield refinement_key.ensure()

    def _hand_out(self, board):
        from rite_ai.config.models import ScheduleConfig, ScheduleWindow
        from rite_ai.coordination.distribution import distribute

        return distribute(
            None,
            board,
            manager="beta",
            workers=["w1"],
            schedule=ScheduleConfig(
                timezone="UTC",
                windows=[ScheduleWindow(hours="00:00-23:59", workers=1)],
            ),
            now=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
            busy=set(),
        )

    def _board(self, key, status: str):
        """A real in-memory board: a REFINED ticket, assigned to `beta`,
        carrying `ready-to-work` — everything the a9 run had."""
        from tests.test_the_ready_label_is_a_view import Board, refine
        from tests.test_the_ready_label_is_a_view import ticket as a_ticket

        board = Board(a_ticket("KAN-28", "beta", "scheduled", "ready-to-work"))
        board.tickets["KAN-28"].status = status
        refine(board, "KAN-28", key)
        return board

    def test_a_done_ticket_is_handed_to_no_worker(self, key):
        board = self._board(key, "Done")

        got = self._hand_out(board)

        assert got.handouts == []
        assert board.writes == [], "the board is not written at all"
        assert "Done" in got.held_back["KAN-28"]

    def test_the_control_is_handed_out(self, key):
        board = self._board(key, "In Progress")

        got = self._hand_out(board)

        assert got.handouts == [("KAN-28", "w1")]


class TestRiteTakesTheLabelBackOff:
    """⚠ The half of the root cause the gates above do not touch: "`rite
    board` can only add labels, so nothing in rite removes it either". The
    view reconciler is the one place that removes `ready-to-work`, and it
    removes it from finished work now."""

    @pytest.fixture
    def key(self, tmp_path):
        import os
        from unittest.mock import patch

        from rite_ai.refinement import key as refinement_key

        with patch.dict(
            os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "refinement-key")}
        ):
            yield refinement_key.ensure()

    def _reconciled(self, key, status: str):
        from rite_ai.refinement import view
        from tests.test_the_ready_label_is_a_view import Board, labelled, refine
        from tests.test_the_ready_label_is_a_view import ticket as a_ticket

        board = Board(a_ticket("KAN-28", "scheduled", "ready-to-work"))
        board.tickets["KAN-28"].status = status
        refine(board, "KAN-28", key)
        view.reconcile(board)
        return labelled(board, "KAN-28")

    def test_a_finished_ticket_loses_ready_to_work(self, key):
        assert not self._reconciled(key, "Done")

    def test_the_control_keeps_it(self, key):
        assert self._reconciled(key, "In Progress")

    def test_the_predicate_itself_says_no(self, key):
        """`wanted` is what `rite board list --ready` reports from too."""
        from rite_ai.refinement import view
        from tests.refined_board import refined as refined_status

        status = refined_status("KAN-28")
        assert status.refined, "the fixture must be REFINED, or this proves nothing"
        assert not view.wanted(done("KAN-28"), status)
        assert view.wanted(todo("KAN-28"), status)


class TestTheWorkerStartItselfRefuses:
    """⚠ The last door, and the only one that actually spends anything:
    `rite sandbox start --ticket`. TR4 gates it on REFINED, which is a
    different question — KAN-28 was Done, merged AND REFINED. The broker
    above runs this very command (`broker.launch_argv`), so a gate only
    there would be a gate a person at the host walks straight past."""

    def _ticket(self, status: str):
        return Ticket(
            id="7",
            title="timout is way too long",
            status=status,
            description="make it configurable or smth",
        )

    def test_a_done_ticket_starts_no_worker_and_leaves_no_copy(
        self, tmp_path, monkeypatch
    ):
        from rite_ai.sandbox.delivery import DELIVERY_FILE
        from tests.refined_board import board_with
        from tests.test_a_worker_starts_only_on_its_agreed_record import (
            _project,
            _start,
        )

        worker = _project(tmp_path, monkeypatch)
        (worker / DELIVERY_FILE).write_text("# Ticket 6, an old one\n")
        with board_with(tmp_path, monkeypatch, self._ticket("Done")):
            result, seen = _start("--ticket", "7")

        assert result.exit_code == 1
        assert "Done" in result.output
        last = result.output.strip().splitlines()[-1]
        assert last.startswith("7 is Done"), last
        assert "new" not in seen, "no sandbox was created"
        assert not (worker / DELIVERY_FILE).exists(), "nothing stale is left behind"

    def test_the_control_starts(self, tmp_path, monkeypatch):
        from tests.refined_board import board_with
        from tests.test_a_worker_starts_only_on_its_agreed_record import (
            _project,
            _start,
        )

        _project(tmp_path, monkeypatch)
        with board_with(tmp_path, monkeypatch, self._ticket("In Progress")) as (
            board,
            _record,
        ):
            result, seen = _start("--ticket", "7")

        assert result.exit_code == 0, result.output
        assert board.reads == ["7"], "still one read of the board, and only one"
        assert seen.get("new"), "the sandbox was created"


@pytest.fixture
def refining_project(tmp_path, monkeypatch):
    """A project whose `lead` holds `route`, which is who refines (TRQ7)."""
    from tests.test_the_owner_routes_to_other_managers import CONFIG

    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RITE_PROJECT_ROOT", raising=False)
    return tmp_path


class TestNorIsItRefinementWork:
    """A round posted on a Done ticket asks the User to define done for work
    that is finished and merged. `scheduled` is only ever added, so without
    a status gate the brief carries it for ever."""

    @pytest.fixture
    def key(self, tmp_path):
        import os
        from unittest.mock import patch

        from rite_ai.refinement import key as refinement_key

        with patch.dict(
            os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "refinement-key")}
        ):
            yield refinement_key.ensure()

    def _brief(self, where, status: str) -> str:
        from rite_ai.config.parse import parse_config
        from rite_ai.refinement import instructions as ri
        from tests.test_the_ready_label_is_a_view import Board
        from tests.test_the_ready_label_is_a_view import ticket as a_ticket

        board = Board(a_ticket("KAN-28", "scheduled"))
        board.tickets["KAN-28"].status = status
        return ri.brief(
            where, "lead", board, parse_config(where / ".rite" / "config.yaml")
        )

    def test_a_done_ticket_is_not_offered_to_refine(self, key, refining_project):
        got = self._brief(refining_project, "Done")

        assert "start refining it now" not in got
        assert "KAN-28 is Done" in got, "and the Owner is told why, not left guessing"

    def test_the_control_is_offered(self, key, refining_project):
        got = self._brief(refining_project, "To Do")

        assert "KAN-28: start refining it now" in got


class TestEveryViewSaysTheSameThing:
    """⚠ `wanted` answering False put finished tickets into the
    `--needs-refinement` bucket, where a REFINED one prints `[assigned]`.
    Two statements about one ticket that cannot both be true."""

    def test_board_list_does_not_call_a_finished_ticket_assigned(self):
        from unittest.mock import patch

        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        row = type(
            "Row",
            (),
            {"ticket": done("KAN-28"), "status": _Refined(), "ready": False},
        )()
        with (
            patch("rite_ai.cli.main._ticket_backend", return_value=(object(), None)),
            patch("rite_ai.refinement.view.truth", return_value=([row], "")),
        ):
            out = CliRunner().invoke(cli, ["board", "list", "--needs-refinement"])

        assert out.exit_code == 0, out.output
        assert "[assigned]" not in out.output
        assert "[Done, not work]" in out.output


class _Refined:
    """A REFINED status, which is what a delivered ticket's record is."""

    refined = True
    state = "REFINED"
