"""A stop on "nothing ready" says when the board was read (DF4).

The board's list is a snapshot: rite reads back its own writes exactly, but a
ticket a person created seconds before (or after) the read is not in it, on
GitHub (measured, up to 6.5 s) or wherever a board lags. The coordinator's
ruling, 2026-09-29: keep stopping, since a second read after a delay would
only narrow the window, and say what was seen AS OF when, so a person who
filed a ticket ten seconds ago can see why it was not picked up.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from rite_ai.loop import IDLE, as_of, plan_cycle

HHMMSS = re.compile(r"as of (\d\d:\d\d:\d\d)")


class EmptyBoard:
    def __init__(self):
        self.listed_at: float | None = None

    def list_tickets(self, _filter):
        self.listed_at = time.time()
        return []


def _project(tmp_path):
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
        "schedule:\n  timezone: UTC\n  windows:\n    - hours: '00:00-23:59'\n"
        "      workers: 1\n"
    )
    return root


def test_the_idle_cycle_carries_the_time_of_its_read(tmp_path):
    board = EmptyBoard()
    cycle = plan_cycle(
        _project(tmp_path), board=board, now=datetime.now(UTC).replace(hour=12)
    )
    assert cycle.verdict == IDLE
    assert board.listed_at is not None
    assert 0 <= cycle.board_read_at - board.listed_at < 1
    [said] = HHMMSS.findall(cycle.detail)
    assert said == as_of(cycle.board_read_at)
    assert "not in that read" in cycle.detail


def test_rite_start_stops_with_the_read_time_not_the_time_it_speaks():
    """Control on WHICH time: a read an hour ago must say that hour."""
    from rite_ai.cli.main import LoopAnswer
    from rite_ai.managers.supervise import _why

    answer = LoopAnswer("idle")
    answer.read_at = time.time() - 3600
    said = _why(answer, 2)
    assert f"nothing ready as of {as_of(answer.read_at)} (2 session(s))" in said
    assert as_of(time.time()) not in said
    assert "not in that read" in said


def test_a_verdict_with_no_read_does_not_invent_a_time():
    from rite_ai.managers.supervise import _why

    said = _why("idle", 0)
    assert "as of" not in said
    assert "the board listed nothing ready (0 session(s))" in said


def test_the_verdict_rite_start_receives_carries_the_read_time(tmp_path):
    """`rite start` sees the cycle only through `LoopAnswer`."""
    from rite_ai.cli.main import LoopAnswer

    cycle = plan_cycle(
        _project(tmp_path), board=EmptyBoard(), now=datetime.now(UTC).replace(hour=12)
    )
    assert LoopAnswer.of(cycle).read_at == cycle.board_read_at
