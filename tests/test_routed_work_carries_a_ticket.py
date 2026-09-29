"""Every route names a REFINED ticket, and carries its record (TR9, TR5).

Robert, TRQ5: "Can we just ticket all work that Workers do?". A route is
work, so it carries a ticket, and (TR5) that ticket must be REFINED, as a
Worker's must: an executor secondary works a routed ticket itself. The check
is the supervisor's, outside the boundary, because a Manager can write the
request file without the command. It is `refinement.status.of`, one read, and
anything but REFINED refuses, naming the state and what to do. A refusal
reaches the Owner's next instruction, not only the terminal.
"""

from __future__ import annotations

import json

import pytest

from rite_ai.managers import routing
from rite_ai.managers.mailbox import INBOX, read
from tests.refined_board import refined_status, unrefined_status

NAMES = ["lead", "helper"]


def _found(ticket_id):
    return refined_status(ticket_id)


def _raw(**fields):
    return json.dumps({"to": "helper", "text": "run the suite", **fields})


@pytest.mark.parametrize(
    "raw, check_ticket, why",
    [
        (_raw(), _found, "must name its ticket"),
        (_raw(ticket=""), _found, "must name its ticket"),
        (_raw(ticket="RT 1; rm"), _found, "not shaped like a ticket id"),
        (_raw(ticket="RT-1"), None, "board cannot be read"),
        (
            _raw(ticket="RT-404"),
            lambda t: unrefined_status(
                t, "UNREADABLE", "the board could not be read: no issue RT-404"
            ),
            "UNREADABLE: the board could not be read: no issue RT-404",
        ),
        (
            _raw(ticket="RT-2"),
            lambda t: unrefined_status(t),
            "NOT REFINED: this ticket has no agreed definition of done",
        ),
        (
            _raw(ticket="RT-3"),
            lambda t: unrefined_status(t, "STALE", "the ticket changed"),
            'rite refine accept RT-3 --item "…"',
        ),
        (
            _raw(ticket="RT-1"),
            lambda t: (_ for _ in ()).throw(OSError("network down")),
            "network down",
        ),
    ],
    ids=[
        "missing",
        "empty",
        "shape",
        "no board",
        "unreadable",
        "not refined",
        "stale",
        "check raised",
    ],
)
def test_a_route_without_a_refined_ticket_is_refused(raw, check_ticket, why):
    got = routing.decide(raw, owner="lead", managers=NAMES, check_ticket=check_ticket)
    assert not got.ok and why in got.reason


def test_the_check_is_one_read_of_that_ticket():
    asked: list[str] = []

    def check_ticket(ticket_id):
        asked.append(ticket_id)
        return _found(ticket_id)

    got = routing.decide(
        _raw(ticket="RT-9"), owner="lead", managers=NAMES, check_ticket=check_ticket
    )
    assert got.ok and asked == ["RT-9"]


def test_the_secondary_is_told_which_ticket_rite_checked(tmp_path):
    routing.request(tmp_path, "lead", "helper", "run the suite", "RT-9")
    routing.deliver_routes(
        tmp_path, "lead", "lead", NAMES, lambda _m: None, check_ticket=_found
    )
    (got,) = read(tmp_path, "helper", INBOX)
    assert got.text.startswith("[routed by the Owner Manager 'lead' · ticket RT-9 ·")


def test_the_secondary_gets_the_agreed_definition_of_done_after_the_quote(tmp_path):
    """TR5: the record from the same read travels with the route, in rite's
    own words, after the Owner's quoted text, so the secondary works to what
    the User agreed and not to the Owner's summary."""
    from rite_ai.refinement.status import render_for_worker

    status = _found("RT-9")
    routing.request(tmp_path, "lead", "helper", "run the suite", "RT-9")
    routing.deliver_routes(
        tmp_path, "lead", "lead", NAMES, lambda _m: None, check_ticket=lambda t: status
    )
    (got,) = read(tmp_path, "helper", INBOX)
    quoted, rites = got.text.split("\n", 1)[1].split("\n\n", 1)
    assert quoted == "> run the suite"
    assert rites.endswith(render_for_worker(status.record))
    assert "Work to it, not to the Owner's summary" in rites


def test_an_unrefined_route_reaches_nobody_and_tells_the_owner_what_to_do(tmp_path):
    routing.request(tmp_path, "lead", "helper", "run the suite", "RT-2")
    routing.deliver_routes(
        tmp_path,
        "lead",
        "lead",
        NAMES,
        lambda _m: None,
        check_ticket=lambda t: unrefined_status(t),
    )
    assert read(tmp_path, "helper", INBOX) == []
    (told,) = read(tmp_path, "lead", INBOX)
    assert "NOT REFINED" in told.text
    assert "Ask the User to agree one before you route it" in told.text
    assert 'rite refine accept RT-2 --item "…"' in told.text


def test_a_refusal_reaches_the_owners_next_instruction(tmp_path):
    """A request written straight to the file, with no ticket: the command's
    own check is bypassed, and the supervisor's still refuses."""
    where = routing._routes_dir(tmp_path, "lead")
    where.mkdir(parents=True)
    (where / "1.json").write_text(_raw())
    said: list[str] = []
    assert (
        routing.deliver_routes(
            tmp_path, "lead", "lead", NAMES, said.append, check_ticket=_found
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
    got = CliRunner().invoke(cli, ["route", "helper", "-"], input="run the suite")
    assert got.exit_code == 1 and "rite chore" in got.output
    assert not list(routing._routes_dir(tmp_path.resolve(), "lead").glob("*.json"))
