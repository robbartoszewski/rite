"""`rite status` board state — SPEC §9.8's "ticket counts by column".

The command used to print "board state: not shown — requires a live
ticket-backend query, not made here". That was honest and still a gap:
the one thing a Manager reads this command to decide from was the one
thing it would not tell them.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.models import TicketBackendConfig
from rite_ai.reporting.status import (
    BoardState,
    _format_boards,
    collect_board_state,
    collect_status,
    format_status,
)
from rite_ai.tickets import BackendError, Ticket, TicketPage


def _ticket(key: str, status: str) -> Ticket:
    return Ticket(id=key, title=key, status=status)


def _project(root: Path, backend_yaml: str = "  type: none\n") -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n  role: manager\n")
    (root / ".rite" / "config.yaml").write_text("ticket_backend:\n" + backend_yaml)
    return root


class TestCountsByColumn:
    def test_groups_tickets_by_status(self):
        page = TicketPage(
            [
                _ticket("A-1", "To Do"),
                _ticket("A-2", "In Progress"),
                _ticket("A-3", "To Do"),
            ]
        )
        tb = TicketBackendConfig(type="jira", site="x", projects={"workers": "A"})
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = page
            state = collect_board_state(tb, "workers")
        assert state.ok
        assert state.counts == {"To Do": 2, "In Progress": 1}
        assert state.total == 3

    def test_columns_are_ordered_by_size(self):
        state = BoardState(
            role="workers",
            project_key="A",
            counts={"To Do": 1, "In Progress": 5, "Done": 3},
            total=9,
        )
        line = _format_boards([state])[1]
        assert (
            line.index("In Progress 5") < line.index("Done 3") < line.index("To Do 1")
        )

    def test_a_ticket_with_no_status_is_still_counted(self):
        tb = TicketBackendConfig(type="jira", site="x", projects={"workers": "A"})
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = TicketPage([_ticket("A-1", "")])
            state = collect_board_state(tb, "workers")
        assert state.counts == {"(no status)": 1}

    def test_truncation_is_surfaced(self):
        """A first page rendered as the whole board is a number framed to
        be misread."""
        page = TicketPage(
            [_ticket(f"A-{i}", "To Do") for i in range(100)], truncated=True
        )
        tb = TicketBackendConfig(type="jira", site="x", projects={"workers": "A"})
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = page
            state = collect_board_state(tb, "workers")
        assert state.truncated
        assert "first page only" in _format_boards([state])[1]

    def test_singular_and_plural(self):
        one = BoardState(role="w", counts={"To Do": 1}, total=1)
        many = BoardState(role="w", counts={"To Do": 2}, total=2)
        assert "1 ticket —" in _format_boards([one])[1]
        assert "2 tickets —" in _format_boards([many])[1]


class TestItNeverDiesOnTheBoard:
    """A status command that fails because the board was unreachable is
    worse than one that says the board was unreachable."""

    def test_a_backend_error_becomes_an_unavailable_line(self):
        tb = TicketBackendConfig(type="jira", site="x", projects={"workers": "A"})
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = BackendError("401 rejected")
            state = collect_board_state(tb, "workers")
        assert not state.ok
        assert "401 rejected" in _format_boards([state])[1]

    def test_a_backend_that_cannot_even_be_built_says_why(self):
        tb = TicketBackendConfig(type="jira", site="", projects={"workers": "A"})
        state = collect_board_state(tb, "workers")
        assert not state.ok
        assert state.error

    def test_an_exception_is_caught_not_raised(self):
        tb = TicketBackendConfig(type="jira", site="x", projects={"workers": "A"})
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.side_effect = RuntimeError("socket exploded")
            state = collect_board_state(tb, "workers")
        assert not state.ok
        assert "RuntimeError" in state.error
        assert "socket exploded" in state.error

    def test_status_still_renders_everything_else_when_the_board_fails(
        self, tmp_path: Path
    ):
        root = _project(
            tmp_path,
            '  type: jira\n  site: "nonexistent.invalid"\n'
            "  projects:\n    workers: A\n",
        )
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = BackendError("unreachable")
            text = format_status(collect_status(root, board=True))
        assert "unavailable — unreachable" in text
        assert "claims" in text  # the rest of the report survived


class TestOnlyConfiguredBoardsAreQueried:
    def test_a_role_with_no_project_key_is_not_asked_about(self, tmp_path: Path):
        root = _project(
            tmp_path,
            '  type: jira\n  site: "x"\n  projects:\n    workers: ABC\n    board: ""\n',
        )
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = TicketPage([])
            status = collect_status(root, board=True)
        assert [b.role for b in status.boards] == ["workers"]

    def test_all_three_roles_are_queried_when_all_are_configured(self, tmp_path: Path):
        root = _project(
            tmp_path,
            '  type: jira\n  site: "x"\n  projects:\n'
            "    board: P\n    workers: W\n    testing: T\n",
        )
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = TicketPage([])
            status = collect_status(root, board=True)
        assert [b.role for b in status.boards] == ["board", "workers", "testing"]

    def test_a_backend_with_no_projects_mapping_still_gets_asked(self, tmp_path: Path):
        """A single-project setup configures a backend without the
        three-role mapping; it still has a board."""
        root = _project(tmp_path, '  type: jira\n  site: "x"\n')
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = TicketPage([])
            status = collect_status(root, board=True)
        assert [b.role for b in status.boards] == ["workers"]

    def test_no_backend_configured_is_not_a_failed_query(self, tmp_path: Path):
        root = _project(tmp_path)
        status = collect_status(root, board=True)
        assert status.boards == []
        assert "no ticket backend configured" in format_status(status)


class TestOneBadPoolFileDoesNotHideEverythingElse:
    def test_an_unreadable_pool_file_still_renders_the_report(self, tmp_path: Path):
        """`probe` refuses an unreadable `pool.json` rather than reading it
        as an empty pool — right where it was written, since a pool read as
        empty is a pool reported as needing topping up, and the dead-slot
        records linking a crashed session to its claims are what an
        unreadable file must not be assumed to lack. Escaping from
        `collect_status` it took the whole command with it, and in the
        machine-wide aggregate ONE project's corrupt file hid every project
        listed after it. A status command is what a human runs to find out
        what is wrong; refusing to report is the one thing it must not do.

        Two claims were removed from this docstring, not reworded — it was
        copied from `ProjectStatus.pool_unreadable`, which see for what
        they were and how they were measured false. That is the spread the
        checklist's SPEC-citation line warns about, in miniature: the same
        wrong sentence in the field and in the test that covers it."""
        root = _project(tmp_path)
        (root / ".rite" / "pool.json").write_text('"not an object"')

        status = collect_status(root, board=True)
        rendered = format_status(status)
        assert status.pool is None
        assert "UNREADABLE" in rendered
        assert "rite pool status" in rendered
        # The rest of the report survives. Asserted on a line that only a
        # SURVIVING report can produce: `"board state" in rendered` also
        # matched the "not queried" line, so it held whether or not the
        # report had been cut short.
        assert "coordination cost" in rendered
        assert "claims" in rendered or "no active claims" in rendered


class TestNotQueriedSaysWhichKindOfNotQueried:
    """`boards is None` has three causes and used to have one message.

    The module already keeps "I did not ask" apart from "I asked and was
    refused". The third — "I never got far enough to ask", because
    `collect_status` returned early — rendered as the FIRST, telling the
    user they had passed `--no-board` when they had passed nothing. Found
    by running the real CLI against a live JIRA project, whose
    `brief.yaml` is missing; the suite was green.
    """

    def test_a_config_that_will_not_parse_says_so(self, tmp_path: Path):
        root = tmp_path
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "config.yaml").write_text(
            'ticket_backend:\n  type: jira\n  site: "x"\n'
        )
        # No brief.yaml — `load_project` returns errors and `collect_status`
        # returns before the query.
        status = collect_status(root, board=True)
        assert status.boards is None
        rendered = format_status(status)
        assert "project config did not load" in rendered
        assert "--no-board" not in rendered

    def test_a_missing_rite_dir_says_so(self, tmp_path: Path):
        status = collect_status(tmp_path, board=True)
        assert status.boards is None
        rendered = format_status(status)
        assert "no .rite/ directory" in rendered
        assert "--no-board" not in rendered

    def test_the_flag_message_survives_for_the_case_it_describes(self, tmp_path: Path):
        """The fix must not blur the other direction: a genuine opt-out
        still reads as one."""
        root = _project(tmp_path, '  type: jira\n  site: "x"\n')
        status = collect_status(root, board=False)
        assert "skipped with --no-board" in format_status(status)


class TestTheQueryIsOptIn:
    def test_off_by_default(self, tmp_path: Path):
        root = _project(tmp_path, '  type: jira\n  site: "x"\n')
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            status = collect_status(root)
            mk.assert_not_called()
        assert status.boards is None
        assert "not queried" in format_status(status)

    def test_the_aggregate_view_never_queries(self, tmp_path, monkeypatch):
        """The Dispatch view calls `collect_status` once per registered
        project. Querying there would be one network round trip per
        project on a machine that exists to run many."""
        registry = tmp_path / "dispatch"
        proj = _project(tmp_path / "proj", '  type: jira\n  site: "x"\n')
        monkeypatch.setenv("RITE_DISPATCH_DIR", str(registry))
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(cli, ["projects", "add", "p", str(proj)])
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            result = runner.invoke(cli, ["status"])
            mk.assert_not_called()
        assert result.exit_code == 0

    def test_cli_no_board_skips_it(self, tmp_path, monkeypatch):
        root = _project(tmp_path, '  type: jira\n  site: "x"\n')
        monkeypatch.chdir(root)
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            result = CliRunner().invoke(cli, ["status", "--no-board"])
            mk.assert_not_called()
        assert "not queried" in result.output

    def test_cli_queries_by_default(self, tmp_path, monkeypatch):
        root = _project(tmp_path, '  type: jira\n  site: "x"\n')
        monkeypatch.chdir(root)
        with patch("rite_ai.tickets.create_backend_from_config") as mk:
            mk.return_value.list_tickets.return_value = TicketPage(
                [_ticket("W-1", "To Do")]
            )
            result = CliRunner().invoke(cli, ["status"])
            mk.assert_called()
        assert "board state:" in result.output
        assert "To Do 1" in result.output
