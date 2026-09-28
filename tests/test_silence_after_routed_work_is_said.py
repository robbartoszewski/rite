"""Routed work that ends in silence is said to the Owner (decision 3, Robert
2026-09-27).

Observed live in A6 experiment 1: the Owner told the person "Waiting on
small's report — will follow up here", the secondary finished without ever
replying, and the person heard nothing. An absent reply is indistinguishable
from work not done, so rite writes a note, in its own words, that starts the
Owner's next session:

- FINISHED WITHOUT A REPLY, noticed at the event (the session's end);
- DIED (or stopped) with the work outstanding, noticed by the sweep, or at
  once when a wait would end on it;
- still working is not a change and is never a note.

A state CHANGE is reported once; a sweep that finds nothing new costs nothing.
"""

from __future__ import annotations

import os

from rite_ai.managers import mailbox, routing

OWNER, SECONDARY = "lead", "small"
NAMES = [OWNER, SECONDARY]


def _on_board(ticket_id):
    """One single-issue read that finds the ticket (TR9: routes carry one)."""
    from rite_ai.tickets.interface import Ticket

    return Ticket(id=ticket_id, title="t")


def _setup(root):
    routing.record_supervisor(root, OWNER, os.getpid())
    routing.record_supervisor(root, SECONDARY, os.getpid())
    routing.request(root, OWNER, SECONDARY, "write notes/HELLO.txt", "RT-1")
    routing.deliver_routes(
        root, OWNER, OWNER, NAMES, lambda _m: None, read_ticket=_on_board
    )
    return [m.path.name for m in mailbox.take(root, SECONDARY, mailbox.INBOX)]


def _tick(root, **kw):
    said: list[str] = []
    routing.collect_reports(root, OWNER, NAMES, said.append, **kw)
    return said


def _notes(root):
    return [
        m.text
        for m in mailbox.read(root, OWNER, mailbox.INBOX)
        if m.text.startswith(routing.NOTE_HEADER_START)
    ]


def test_finishing_without_a_reply_is_noted_once_at_the_event(tmp_path):
    taken = _setup(tmp_path)
    assert _tick(tmp_path) == [] or not _notes(tmp_path)  # still working
    routing._record_handled(tmp_path, SECONDARY, taken)  # the session ended
    said = _tick(tmp_path)
    (note,) = _notes(tmp_path)
    assert "FINISHED WITHOUT A REPLY" in note and "check the result yourself" in note
    assert any("finished routed work without replying" in line for line in said)
    _tick(tmp_path)
    assert len(_notes(tmp_path)) == 1, "a state is reported once, not every tick"


def test_a_reply_then_the_end_is_not_silence(tmp_path):
    taken = _setup(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "done")
    _tick(tmp_path)
    routing._record_handled(tmp_path, SECONDARY, taken)
    _tick(tmp_path)
    assert _notes(tmp_path) == []


def test_a_reply_not_yet_collected_when_the_session_ends_is_not_silence(tmp_path):
    """The reply is written, then the session ends: both before one tick."""
    taken = _setup(tmp_path)
    mailbox.send(tmp_path, SECONDARY, mailbox.OUTBOX, "done")
    routing._record_handled(tmp_path, SECONDARY, taken)
    _tick(tmp_path)
    assert _notes(tmp_path) == []


def _kill(root, name):
    path = routing._ledger_dir(root, name) / routing.SUPERVISOR_FILE
    data = routing._load(path)
    data["pid"] = 2**22 + 12345
    routing._store(path, data)


def test_a_death_with_work_outstanding_is_noted_once_by_the_sweep(tmp_path):
    _setup(tmp_path)
    _tick(tmp_path)  # observed running after delivery
    _kill(tmp_path, SECONDARY)
    _tick(tmp_path, sweep_seconds=0.0)
    (note,) = _notes(tmp_path)
    assert "DIED WITH ROUTED WORK OUTSTANDING" in note
    assert "may not have happened" in note
    _tick(tmp_path, sweep_seconds=0.0)
    assert len(_notes(tmp_path)) == 1, "a Manager that stays dead is not re-announced"


def test_the_sweep_waits_its_interval(tmp_path):
    _setup(tmp_path)
    _tick(tmp_path, sweep_seconds=0.0)  # a sweep ran: nothing to say
    _kill(tmp_path, SECONDARY)
    _tick(tmp_path, sweep_seconds=3600.0)
    assert _notes(tmp_path) == [], "not due yet: the died case is a latency bound"


def test_a_death_from_before_the_work_was_handed_out_is_not_noted(tmp_path):
    """Start-order protection: killed in an earlier run, maybe about to start."""
    routing.record_supervisor(tmp_path, OWNER, os.getpid())
    routing.record_supervisor(tmp_path, SECONDARY, 2**22 + 12345)
    path = routing._ledger_dir(tmp_path, SECONDARY) / routing.SUPERVISOR_FILE
    data = routing._load(path)
    data["started_at"] = 1.0
    routing._store(path, data)
    routing.request(tmp_path, OWNER, SECONDARY, "x", "RT-1")
    routing.deliver_routes(
        tmp_path, OWNER, OWNER, NAMES, lambda _m: None, read_ticket=_on_board
    )
    _tick(tmp_path, sweep_seconds=0.0)
    assert _notes(tmp_path) == []


def test_a_note_keeps_the_owner_waiting_to_deliver_it(tmp_path):
    """Like a reply, a note in the inbox is a reason for the Owner's wait,
    or it would stop with the note unread."""
    taken = _setup(tmp_path)
    routing._record_handled(tmp_path, SECONDARY, taken)
    _tick(tmp_path)
    waiting = routing.Waiting(tmp_path, OWNER, OWNER)
    assert "waiting to be delivered" in waiting.reason()


def test_sweep_minutes_must_be_positive():
    from rite_ai.config.models import CoordinationConfig
    from rite_ai.coordination.config_check import coordination_problems

    problems = coordination_problems(
        CoordinationConfig(managers=[OWNER, SECONDARY], sweep_minutes=0)
    )
    assert any("sweep_minutes" in p for p in problems), problems


def test_a_quoted_sweep_minutes_does_not_crash_the_router(tmp_path, capsys):
    """Found by the independent verification: `sweep_minutes: "30"` crashed
    the Owner's router on every tick; only `rite doctor` checked it."""
    from rite_ai.cli.main import _router_for

    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "config.yaml").write_text(
        "coordination:\n  managers: [lead, small]\n  sweep_minutes: '30'\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n      preset: lead\n"
        "    - name: small\n      engine: claude\n      preset: executor\n"
    )
    step = _router_for(tmp_path, OWNER)
    step(lambda _m: None)  # must not raise
    assert "sweep_minutes" in capsys.readouterr().err
