"""A ticket a Worker starts on reads as in progress on the board (SCRUM-27).

KAN-28 and KAN-29 read "To Do" while Workers were working them: nothing moved
a ticket when its Worker started, and a sandboxed Worker cannot move one
itself, holding no board credential (§5.3.4). `rite sandbox start` is the
host-side moment a Worker takes a ticket, so it moves it there, through the
backend's own `move`, and never fails a start that already happened.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from rite_ai.cli.main import STARTED_STATUS, cli
from rite_ai.tickets import BackendError
from rite_ai.tickets.interface import Ticket
from tests.refined_board import board_with

SITE = "example.atlassian.net"
TICKET = Ticket(
    id="KAN-28",
    title="retry on fail",
    status="To Do",
    labels=["scheduled"],
    description="retry three times",
    url=f"https://{SITE}/browse/KAN-28",
)


class _Board:
    """The workers board `_mark_started` moves the ticket on. `lands` is what
    `move` returns: None for "exactly where asked", a status for "somewhere
    else", a BackendError for a refusal."""

    def __init__(self, lands=None):
        self.lands = lands
        self.moves: list[tuple[str, str]] = []

    def read(self, ticket_id):
        return TICKET

    def move(self, ticket_id, status):
        self.moves.append((ticket_id, status))
        return self.lands


def _project(tmp_path: Path, monkeypatch) -> None:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text(
        "modules:\n  app:\n    path: app/\n    branch: main\n"
    )
    (rite / "config.yaml").write_text(
        f"ticket_backend:\n  type: jira\n  site: {SITE}\n"
        "  projects:\n    workers: KAN\nsandbox:\n  enabled: true\n"
        "  backend: seatbelt\n"
    )
    worker = tmp_path / "workers" / "alpha"
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: ''\n  modules: [app]\n"
    )
    monkeypatch.chdir(tmp_path)


def _start(board: _Board, *, new_fails: bool = False):
    def run(args, *a, **kw):
        if "new" in args and new_fails:
            return MagicMock(returncode=1, stdout="", stderr="no space left")
        stdout = '{"sandboxes": []}' if "ls" in args else ""
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    with (
        patch("keyring.get_password", return_value=None),
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/bin/yoloai"),
        patch("rite_ai.sandbox.subprocess.run", side_effect=run),
        patch("rite_ai.cli.main._ticket_backend", return_value=(board, None)),
        patch("rite_ai.cli.main._worker_cannot_deliver", return_value=None),
    ):
        return CliRunner().invoke(
            cli, ["sandbox", "start", "alpha", "--allow-dirty", "--ticket", "KAN-28"]
        )


def test_a_started_ticket_is_moved_to_in_progress(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    board = _Board()
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE):
        result = _start(board)

    assert result.exit_code == 0, result.output
    assert board.moves == [("KAN-28", STARTED_STATUS)], (
        "the Worker started and the board still says what it said before"
    )
    assert f"board: KAN-28 -> {STARTED_STATUS}" in result.output


def test_a_start_that_failed_moves_nothing(tmp_path, monkeypatch):
    """The board says in progress only once a Worker is on the ticket."""
    _project(tmp_path, monkeypatch)
    board = _Board()
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE):
        result = _start(board, new_fails=True)

    assert result.exit_code != 0
    assert board.moves == []


def test_a_board_that_refuses_the_move_is_said_and_the_start_stands(
    tmp_path, monkeypatch
):
    """Exit 0: the Worker IS running, and the broker reads a non-zero exit as
    a start that did not happen. The refusal is said, with the board's words
    and that the ticket still shows its old status."""
    _project(tmp_path, monkeypatch)
    board = _Board(lands=BackendError("no transition to 'In Progress' found"))
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE):
        result = _start(board)

    assert result.exit_code == 0, result.output
    assert "board NOT moved: KAN-28 still shows its old status" in result.output
    assert "no transition to 'In Progress' found" in result.output
    assert "board: KAN-28 ->" not in result.output


def test_a_board_that_raises_does_not_turn_a_started_worker_into_a_failure(
    tmp_path, monkeypatch
):
    _project(tmp_path, monkeypatch)

    class Raises(_Board):
        def move(self, ticket_id, status):
            raise ConnectionError("board unreachable")

    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE):
        result = _start(Raises())

    assert result.exit_code == 0, result.output
    assert "board NOT moved" in result.output
    assert "board unreachable" in result.output


def test_where_the_ticket_actually_landed_is_said(tmp_path, monkeypatch):
    """GitHub has no such column; its `move` says it mapped to open. The
    start reports the board's answer, not the status it asked for."""
    _project(tmp_path, monkeypatch)
    board = _Board(lands="open")
    with board_with(tmp_path, monkeypatch, TICKET, jira_site=SITE):
        result = _start(board)

    assert result.exit_code == 0, result.output
    assert "board: KAN-28 -> open" in result.output
    assert f"board: KAN-28 -> {STARTED_STATUS}" not in result.output
