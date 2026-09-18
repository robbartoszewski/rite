"""The wiring that makes rite orchestrate itself (RL-T32, RL-T33).

Phase 2's set produced nine defects of one shape: a mechanism complete,
correct, tested, and called by nobody. These are the tests for the calls.

Three silences are closed here, and each was a state that looked exactly like
a different, benign state:

- a monitor with no board distributed nothing and reported the same tick as a
  monitor that had nothing to distribute;
- assignment could not tell "nobody is free" from "nobody holds this duty",
  which are a wait and a configuration that never progresses;
- `rite claim` printed one success line whether the claim was checked across
  the fleet or only on this machine.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rite_ai.config.managers import ManagerRole
from rite_ai.coordination.assignment import (
    Assigned,
    ManagerView,
    NotAssigned,
    assign_to_manager,
    choose_manager,
)
from rite_ai.coordination.heartbeat import Liveness

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

LEAD = ManagerRole(name="lead", preset="lead")
PLANNER = ManagerRole(
    name="planner",
    engine="local:large",
    preset="planner",
    endpoint="http://localhost:11434/v1",
    model="qwen3:70b",
    agent="opencode",
)
EXECUTOR = ManagerRole(
    name="executor",
    engine="local:small",
    preset="executor",
    endpoint="http://localhost:11434/v1",
    model="qwen3:8b",
    agent="opencode",
)


def _view(name: str, in_flight: int = 0, assignable: bool = True) -> ManagerView:
    return ManagerView(
        name,
        Liveness(missed=0, detail="alive"),
        in_flight=in_flight,
        workers=[],
        assignable=assignable,
        why_not="" if assignable else "stalled",
    )


class FakeBackend:
    def __init__(self):
        self.labelled: list[tuple[str, list[str]]] = []

    def label(self, ticket_id: str, labels: list[str]):
        self.labelled.append((ticket_id, labels))
        return None


# --- RL-T33: assignment consults the duty router ----------------------------------


def test_a_task_goes_to_a_manager_that_holds_its_stage_s_duty():
    """Duty first, then load (RL-5). The executor is idle and still wrong for
    a decomposition."""
    views = [
        _view("lead", in_flight=3),
        _view("planner", in_flight=2),
        _view("executor"),
    ]
    chosen = choose_manager(views, roles=[LEAD, PLANNER, EXECUTOR], stage="decompose")
    assert chosen is not None and chosen.name == "planner"


def test_load_still_breaks_the_tie_among_managers_that_may_take_it():
    second = ManagerRole(
        name="executor2",
        engine="local:small",
        preset="executor",
        endpoint="http://localhost:11434/v1",
        model="qwen3:8b",
        agent="opencode",
    )
    views = [_view("executor", in_flight=2), _view("executor2", in_flight=1)]
    chosen = choose_manager(views, roles=[EXECUTOR, second], stage="execute")
    assert chosen is not None and chosen.name == "executor2"


def test_no_duty_holder_is_not_a_fallback_to_anybody():
    """Handing a task to a Manager that does not hold its duty is the
    mis-route the router exists to prevent, and it would look like success."""
    views = [_view("lead"), _view("executor")]
    assert choose_manager(views, roles=[LEAD, EXECUTOR], stage="decompose") is None


def test_a_project_that_declares_nothing_routes_exactly_as_it_did():
    """RL-4: while Managers are alike there is nothing to route between. No
    roles, no stage — the old behaviour, which is most projects."""
    views = [_view("a", in_flight=2), _view("b", in_flight=1)]
    chosen = choose_manager(views)
    assert chosen is not None and chosen.name == "b"


def test_a_duty_nobody_holds_says_so_rather_than_saying_nobody_is_free():
    """A wait and a configuration that never progresses were sharing one
    sentence, and a reader who cannot tell them apart waits for the second."""
    result = assign_to_manager(
        FakeBackend(),
        "ABC-1",
        [_view("lead"), _view("executor")],
        roles=[LEAD, EXECUTOR],
        stage="decompose",
    )
    assert isinstance(result, NotAssigned)
    assert "no manager holds decompose" in result.reason
    assert "wait for ever" in result.reason


def test_a_busy_duty_holder_says_it_is_busy_not_that_it_is_missing():
    result = assign_to_manager(
        FakeBackend(),
        "ABC-1",
        [_view("lead"), _view("planner", assignable=False)],
        roles=[LEAD, PLANNER],
        stage="decompose",
    )
    assert isinstance(result, NotAssigned)
    assert "planner" in result.reason and "cannot be given work now" in result.reason
    assert result.per_manager and "planner" in result.per_manager


def test_assignment_labels_the_manager_the_duty_chose():
    backend = FakeBackend()
    result = assign_to_manager(
        backend,
        "ABC-1",
        [_view("lead"), _view("planner"), _view("executor")],
        roles=[LEAD, PLANNER, EXECUTOR],
        stage="plan-review",
    )
    assert isinstance(result, Assigned) and result.manager == "lead"
    assert backend.labelled == [("ABC-1", ["lead"])]


# --- RL-T32: the monitor says when it never tried ---------------------------------


def test_a_monitor_without_a_board_says_it_did_not_try():
    """ "No board configured" and "nothing to hand out" produced identical
    ticks. The first is why an overnight run can do nothing all night while
    every tick reports success."""
    from rite_ai.coordination.monitor import ManagerMonitor

    class Holder:
        manager = "alpha"
        layer = None
        config = None

    monitor = ManagerMonitor.__new__(ManagerMonitor)
    monitor.backend = None
    monitor.schedule = None
    monitor.root = None
    from rite_ai.coordination.monitor import Tick

    tick = Tick()
    monitor._distribute(NOW, tick)
    assert tick.distribution.startswith("not attempted")
    assert "board backend" in tick.distribution
    # Not a problem: a monitor with no board is a configuration, not a fault.
    assert not any("distribut" in p for p in tick.problems)


def test_a_monitor_that_has_everything_reports_no_skip():
    """The field is empty when distribution was actually attempted, so a
    reader can trust its presence to mean something."""
    from rite_ai.coordination.monitor import Tick

    tick = Tick()
    assert tick.distribution == ""


# --- claims say which mode they are in --------------------------------------------


@pytest.mark.parametrize(
    ("fleet", "expected"),
    [(False, "on this machine only"), (True, "across the fleet")],
)
def test_claim_says_whether_it_checked_the_fleet(
    tmp_path, monkeypatch, fleet, expected
):
    """`claims_channel` returns nothing unless `managers` AND `remote` are both
    set, so a half-configured fleet claims locally while printing what a
    fully-configured one prints. Correct for one machine, wrong the day a
    second joins, and the day it becomes wrong is the day nobody re-reads it."""
    import subprocess

    from click.testing import CliRunner

    import rite_ai.sandbox as sb
    from rite_ai.cli.init import run_init
    from rite_ai.cli.main import cli

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    run_init(root, yes=True)

    if fleet:
        # A REAL state layer, not a stand-in: with a layer, a claim is checked
        # against other machines' published claims and refuses when it cannot
        # read them (fail-closed, P2-5b). A fake that cannot answer would test
        # the refusal path instead of the mode line.
        from rite_ai.coordination.local_backend import LocalStateLayer

        layer = LocalStateLayer(tmp_path / "fleet-state")
        monkeypatch.setattr(
            "rite_ai.coordination.identity.claims_channel",
            lambda _root: (layer, "this-machine"),
        )

    result = CliRunner().invoke(cli, ["claim", "a.py", "--worker", "alpha"])
    assert expected in result.output, result.output
