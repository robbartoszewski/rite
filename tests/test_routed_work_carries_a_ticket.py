"""Every route names its ticket, and the supervisor checks it (TR9).

Robert, TRQ5: "Can we just ticket all work that Workers do?". A route is
work, so it carries a ticket. The check is the supervisor's, outside the
boundary, because a Manager can write the request file without the command;
it is one single-issue read, and a read that fails refuses. A refusal reaches
the Owner's next instruction, not only the terminal.
"""

from __future__ import annotations

import json

import pytest

from rite_ai.managers import routing
from rite_ai.managers.mailbox import INBOX, read
from rite_ai.tickets.interface import BackendError, Ticket

NAMES = ["lead", "helper"]


def _found(ticket_id):
    return Ticket(id=ticket_id, title="t")


def _raw(**fields):
    return json.dumps({"to": "helper", "text": "run the suite", **fields})


@pytest.mark.parametrize(
    "raw, read_ticket, why",
    [
        (_raw(), _found, "must name its ticket"),
        (_raw(ticket=""), _found, "must name its ticket"),
        (_raw(ticket="RT 1; rm"), _found, "not shaped like a ticket id"),
        (_raw(ticket="RT-1"), None, "board cannot be read"),
        (
            _raw(ticket="RT-404"),
            lambda t: BackendError("no issue RT-404"),
            "could not be read from the board (no issue RT-404)",
        ),
        (
            _raw(ticket="RT-1"),
            lambda t: (_ for _ in ()).throw(OSError("network down")),
            "network down",
        ),
    ],
    ids=["missing", "empty", "shape", "no board", "not on board", "read raised"],
)
def test_a_route_without_a_ticket_the_board_returns_is_refused(raw, read_ticket, why):
    got = routing.decide(raw, owner="lead", managers=NAMES, read_ticket=read_ticket)
    assert not got.ok and why in got.reason


def test_the_check_is_one_read_of_that_ticket():
    asked: list[str] = []

    def read_ticket(ticket_id):
        asked.append(ticket_id)
        return _found(ticket_id)

    got = routing.decide(
        _raw(ticket="RT-9"), owner="lead", managers=NAMES, read_ticket=read_ticket
    )
    assert got.ok and asked == ["RT-9"]


def test_the_secondary_is_told_which_ticket_rite_checked(tmp_path):
    routing.request(tmp_path, "lead", "helper", "run the suite", "RT-9")
    routing.deliver_routes(
        tmp_path, "lead", "lead", NAMES, lambda _m: None, read_ticket=_found
    )
    (got,) = read(tmp_path, "helper", INBOX)
    assert got.text.startswith("[routed by the Owner Manager 'lead' · ticket RT-9 ·")


def test_a_refusal_reaches_the_owners_next_instruction(tmp_path):
    """A request written straight to the file, with no ticket: the command's
    own check is bypassed, and the supervisor's still refuses."""
    where = routing._routes_dir(tmp_path, "lead")
    where.mkdir(parents=True)
    (where / "1.json").write_text(_raw())
    said: list[str] = []
    assert (
        routing.deliver_routes(
            tmp_path, "lead", "lead", NAMES, said.append, read_ticket=_found
        )
        == 0
    )
    assert read(tmp_path, "helper", INBOX) == []
    (told,) = read(tmp_path, "lead", INBOX)
    # rite's one note header (`telling`).
    assert told.text.startswith("[from rite · about a route you asked for · ")
    assert "not delivered" in told.text and "rite chore" in told.text
    assert any("must name its ticket" in line for line in said)


def test_the_command_refuses_a_route_without_a_ticket(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli
    from rite_ai.managers import MANAGER_ENV

    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite / "config.yaml").write_text(
        "coordination:\n  managers:\n    - lead\n    - helper\n  manager_roles:\n"
        "    - name: lead\n      engine: claude\n      preset: lead\n"
        "    - name: helper\n      engine: claude\n      preset: executor\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(MANAGER_ENV, "lead")
    got = CliRunner().invoke(cli, ["route", "helper", "run the suite"])
    assert got.exit_code == 1 and "rite chore" in got.output
    assert not list(routing._routes_dir(tmp_path.resolve(), "lead").glob("*.json"))
