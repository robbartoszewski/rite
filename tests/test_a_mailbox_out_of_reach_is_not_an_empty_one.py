"""A box this session cannot open is not a Manager that said nothing.

Reproduced on 2026-10-09 in the four-role gate run: from inside `lead`'s
boundary, `rite replies planner` ended in a bare `PermissionError` traceback
out of `pending.sync` — the ledger write, not the read. A Manager's mailbox
and its own directory are granted to that Manager alone (`enclosure`), so
this is refused by design and the only question is what rite says about it.

Two answers were wrong, and each is pinned here:

* the traceback itself, which says nothing a person can act on;
* and what the code would have said once the traceback was gone, because
  `mailbox.read` treats an unreadable box as no messages so that one bad
  file cannot end a supervised run — `nothing new from 'planner'`, which
  reads as "it has said nothing" and is never true of a box nobody opened.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import manager_dir, pending
from rite_ai.managers.mailbox import OUTBOX, mailbox_dir, out_of_reach, send

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root reads a mode-000 directory anyway"
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    rite = tmp_path / "proj" / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "coordination:\n"
        "  managers: [lead, planner]\n"
        "  manager_roles:\n"
        "  - name: lead\n"
        "    preset: lead\n"
        "  - name: planner\n"
        "    duties: [decompose]\n"
    )
    monkeypatch.setenv("RITE_PROJECT_ROOT", str(tmp_path / "proj"))
    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "mail"))
    return tmp_path / "proj"


def test_a_denied_box_is_named_and_an_absent_one_is_not(project: Path):
    """The probe tells denial from absence — the whole point of it."""
    # CONTROL: nothing sent yet, so the box does not exist. That is not a
    # problem; it is a Manager that has not spoken.
    assert out_of_reach(project, "planner", OUTBOX) == ""

    send(project, "planner", OUTBOX, "hello from the planner")
    assert out_of_reach(project, "planner", OUTBOX) == ""

    box = mailbox_dir(project, "planner", OUTBOX)
    box.chmod(0o000)
    try:
        problem = out_of_reach(project, "planner", OUTBOX)
    finally:
        box.chmod(0o755)
    assert problem, "a box that cannot be opened must not read as reachable"
    assert "planner" in problem
    assert str(box) in problem


def test_replies_refuses_a_box_it_cannot_open_rather_than_calling_it_empty(
    project: Path,
):
    send(project, "planner", OUTBOX, "a question only the person can answer")
    box = mailbox_dir(project, "planner", OUTBOX)
    box.chmod(0o000)
    try:
        result = CliRunner().invoke(cli, ["replies", "planner"])
    finally:
        box.chmod(0o755)
    assert result.exit_code == 1, result.output
    # 🔴 The regression: "nothing new" about a box nobody read.
    assert "nothing new" not in result.output
    assert "cannot read 'planner''s outbox" in result.output


def test_an_unwritable_ledger_costs_the_tracking_and_not_the_message(project: Path):
    """The reproduced traceback came from the LEDGER, which the probe above
    cannot see: the box was readable and `pending.sync` could not write.

    So the message must still be shown. A fix that refused here too would
    have swapped a traceback for a lost message, which is the one thing this
    channel exists to prevent."""
    send(project, "planner", OUTBOX, "a question only the person can answer")
    state = manager_dir(project, "planner")
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o000)
    try:
        result = CliRunner().invoke(cli, ["replies", "planner"])
    finally:
        state.chmod(0o755)
    assert result.exit_code == 0, result.output
    assert "a question only the person can answer" in result.output
    assert "delivery tracking is unavailable for 'planner'" in result.output


def test_the_ledger_writers_report_rather_than_raise(project: Path):
    send(project, "planner", OUTBOX, "a question only the person can answer")
    state = manager_dir(project, "planner")
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o000)
    try:
        note = pending.sync(project, "planner")
        confirmed = pending.confirm(
            project, "planner", "whatever.json", pending.BY_TERMINAL, at=time.time()
        )
        pending.posted(project, "planner", "whatever.json", "C1", "1.2")
    finally:
        state.chmod(0o755)
    assert "delivery tracking is unavailable" in note
    # False, not True: unconfirmed comes back at the next check-in, which is
    # the safe direction. Recording it would claim delivery on no evidence.
    assert confirmed is False
