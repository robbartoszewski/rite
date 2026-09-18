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
from pathlib import Path

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


def _holder():
    """The least a `ManagerMonitor` needs to be constructed. `_distribute`
    never touches the holder, and a real one needs a git remote."""

    class Holder:
        manager = "alpha"
        layer = None

    return Holder()


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
    from rite_ai.coordination.monitor import ManagerMonitor, Tick

    # Constructed, not `__new__`d: a monitor assembled attribute by attribute
    # passes this test while `__init__` forgets the field, which is the bug
    # this is about in the first place.
    monitor = ManagerMonitor(_holder())

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


# --- Q9: may an unattended tick hand work out? ------------------------------------


def _project_config(**coordination):
    from rite_ai.config.models import (
        CoordinationConfig,
        ProjectBrief,
        ProjectConfig,
        RiteProject,
        TicketBackendConfig,
        WorkerManifest,
    )

    workers = coordination.pop("_workers", [WorkerManifest(name="alpha")])
    backend_type = coordination.pop("_backend", "github")
    return RiteProject(
        root=Path("/nowhere"),
        brief=ProjectBrief(name="acme", role="owner"),
        config=ProjectConfig(
            ticket_backend=TicketBackendConfig(type=backend_type, repo="acme/acme"),
            coordination=CoordinationConfig(**coordination),
        ),
        workers=workers,
    )


def test_by_default_an_unattended_tick_may_not_put_work_on_the_board():
    """Q9's default is a decision, not an absence of one: distribution writes
    to a board other people read, from cron, with nobody watching."""
    from rite_ai.coordination.unattended import distribution_refusal

    refusal = distribution_refusal(_project_config())
    assert "assign_unattended" in refusal


def test_the_refusal_names_the_key_that_turns_it_on():
    """A reason that does not say what to change is a dead end at 3am."""
    from rite_ai.coordination.unattended import distribution_refusal

    refusal = distribution_refusal(_project_config())
    assert "config.yaml" in refusal and "true" in refusal


def test_turning_it_on_without_a_board_is_a_different_answer():
    """Somebody turned this on and expected work to move. 'Nothing happened'
    is exactly what this module exists to stop."""
    from rite_ai.coordination.unattended import distribution_refusal

    refusal = distribution_refusal(
        _project_config(assign_unattended=True, _backend="none")
    )
    assert "no board" in refusal


def test_turning_it_on_with_no_workers_says_who_is_missing():
    from rite_ai.coordination.unattended import distribution_refusal

    refusal = distribution_refusal(_project_config(assign_unattended=True, _workers=[]))
    assert "nobody to hand work to" in refusal


def test_a_project_that_has_decided_gets_no_refusal():
    from rite_ai.coordination.unattended import distribution_refusal

    assert distribution_refusal(_project_config(assign_unattended=True)) == ""


def test_policy_and_missing_wiring_are_not_the_same_sentence():
    """`distribution_off` is given, never inferred from a missing backend: a
    reader told 'no board backend' while policy is what refused goes looking
    for a bug that is not there."""
    from rite_ai.coordination.monitor import ManagerMonitor, Tick

    monitor = ManagerMonitor(
        _holder(),
        root=Path("/nowhere"),
        backend=object(),
        schedule=object(),
        distribution_off="coordination.assign_unattended is false",
    )

    tick = Tick()
    monitor._distribute(NOW, tick)
    assert "assign_unattended" in tick.distribution
    assert "board backend" not in tick.distribution
    assert not any("distribut" in p for p in tick.problems)


def test_the_role_checks_doctor_never_called_are_called():
    """`configuration_problems` held eight rules about gates that cannot
    work, complete and tested and reporting to nobody — which is the defect
    shape this whole batch is about."""
    from rite_ai.config.models import CoordinationConfig
    from rite_ai.coordination.config_check import coordination_problems

    problems = coordination_problems(
        CoordinationConfig(managers=["lead"], manager_roles=[LEAD, PLANNER])
    )
    assert any("planner" in p and "not in coordination.managers" in p for p in problems)


def test_roles_are_checked_on_a_project_with_no_remote_at_all():
    """A rite-local project can be several Managers on ONE machine. The early
    return for 'not configured' must not skip it."""
    from rite_ai.config.models import CoordinationConfig
    from rite_ai.coordination.config_check import coordination_problems

    problems = coordination_problems(
        CoordinationConfig(manager_roles=[PLANNER, EXECUTOR])
    )
    assert any("no manager can be Owner" in p for p in problems)


def test_a_project_declaring_nothing_is_still_silent():
    """Every project today. Phase 1 is not a misconfiguration."""
    from rite_ai.config.models import CoordinationConfig
    from rite_ai.coordination.config_check import coordination_problems

    assert coordination_problems(CoordinationConfig()) == []


# --- the Owner's assignment path, which Q9 was blocking ---------------------------


class RecordingBoard(FakeBackend):
    """A board with a backlog. `label` records rather than writes."""

    def __init__(self, tickets):
        super().__init__()
        self.tickets = tickets

    def list_tickets(self, _filter):
        return self.tickets


class FakeTicket:
    def __init__(self, id_, labels):
        self.id = id_
        self.labels = list(labels)


def _pool_setup(tmp_path, ticket_ids, in_flight):
    """A state layer carrying one heartbeat per Manager, and a backlog."""
    from datetime import timedelta

    from rite_ai.coordination.heartbeat import publish_heartbeat
    from rite_ai.coordination.local_backend import LocalStateLayer

    layer = LocalStateLayer(tmp_path / "state")
    for manager, count in in_flight.items():
        publish_heartbeat(
            layer, manager, workers=[], in_flight=count, now=NOW - timedelta(seconds=5)
        )
    board = RecordingBoard([FakeTicket(t, ["scheduled"]) for t in ticket_ids])
    return layer, board


def _project_for_pool(names):
    from rite_ai.config.models import (
        CoordinationConfig,
        ProjectBrief,
        ProjectConfig,
        RiteProject,
        ScheduleConfig,
        ScheduleWindow,
    )

    return RiteProject(
        root=Path("/nowhere"),
        brief=ProjectBrief(name="acme", role="owner"),
        config=ProjectConfig(
            coordination=CoordinationConfig(managers=list(names)),
            # Rule 3's ceiling. A default `ScheduleConfig` has no windows, so
            # every hour allows 0 Workers — correct, and not what these are
            # about.
            schedule=ScheduleConfig(
                timezone="UTC",
                windows=[ScheduleWindow(hours="00:00-23:59", workers=9)],
            ),
        ),
    )


def test_the_owner_assigns_waiting_tickets_to_a_manager(tmp_path):
    """P2-3a, and one of Phase 2's nine complete-and-uncalled functions. The
    exemption in `test_no_dead_wiring` said the wiring was "one call in
    `_owner_duties`" — this is that call being made."""
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(tmp_path, ["ABC-1"], {"alpha": 0, "beta": 2})
    project = _project_for_pool(["alpha", "beta"])
    lines = _assign_the_pool(
        layer, project.config.coordination, project, "alpha", board, NOW
    )
    assert board.labelled == [("ABC-1", ["alpha"])], lines
    assert any("assigned ABC-1 to alpha" in line for line in lines)


def test_a_ticket_a_manager_already_holds_is_left_alone(tmp_path):
    """Re-assigning work a Manager started moves it out from under them, and
    the label is the only record that it started."""
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(tmp_path, [], {"alpha": 0, "beta": 0})
    board.tickets = [FakeTicket("ABC-1", ["scheduled", "beta"])]
    project = _project_for_pool(["alpha", "beta"])
    lines = _assign_the_pool(
        layer, project.config.coordination, project, "alpha", board, NOW
    )
    assert board.labelled == [] and lines == []


def test_five_tickets_do_not_all_land_on_the_idlest_manager(tmp_path):
    """Heartbeats do not change until those Managers next publish, so a loop
    over one reading sends every ticket to whoever was idlest at the top of
    it — a stampede onto one machine, every tick."""
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(
        tmp_path, ["ABC-1", "ABC-2", "ABC-3", "ABC-4"], {"alpha": 0, "beta": 0}
    )
    project = _project_for_pool(["alpha", "beta"])
    _assign_the_pool(layer, project.config.coordination, project, "alpha", board, NOW)

    went_to = [labels[0] for _, labels in board.labelled]
    assert went_to.count("alpha") == 2 and went_to.count("beta") == 2, went_to


# --- Q9's three rules, which are on whether or not the switch is ------------------


def _refuse(layer, ticket, manager, reason):
    """A REAL refusal, through the code a Manager runs.

    Hand-writing the log entry would test the reader against a message the
    test wrote, and the two would agree by construction — which is exactly how
    a writer and a reader drift apart without a test noticing."""
    from rite_ai.coordination.refusal import refuse_assignment

    class Board:
        def label(self, *_args, **_kw):
            return None

        def comment(self, *_args, **_kw):
            return None

    refuse_assignment(Board(), ticket, manager=manager, reason=reason, layer=layer)


def test_rule_2_a_manager_does_not_get_back_the_ticket_it_refused(tmp_path):
    """Refusing costs the refuser nothing, so it is still the least loaded and
    gets the same ticket every five minutes for ever, with a board comment
    each time. The memory is rite's log; board comments are not read back."""
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(tmp_path, ["ABC-1"], {"alpha": 0, "beta": 5})
    _refuse(layer, "ABC-1", "alpha", "this machine does not have module 'router'")
    project = _project_for_pool(["alpha", "beta"])
    _assign_the_pool(layer, project.config.coordination, project, "alpha", board, NOW)

    assert board.labelled == [("ABC-1", ["beta"])]


def test_rule_2_forgets_a_refusal_whose_reason_has_passed(tmp_path):
    """A missing module is true of the machine; 'shutting down' was true of a
    moment. Keeping the second for ever retires a machine from a ticket
    because it once restarted while holding it."""
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(tmp_path, ["ABC-1"], {"alpha": 0, "beta": 5})
    _refuse(layer, "ABC-1", "alpha", "shutting down: reboot")
    project = _project_for_pool(["alpha", "beta"])
    _assign_the_pool(layer, project.config.coordination, project, "alpha", board, NOW)

    # alpha is publishing heartbeats again, so it is not shutting down now.
    assert board.labelled == [("ABC-1", ["alpha"])]


def test_a_ticket_everyone_refused_does_not_stop_the_next_one(tmp_path):
    """A refusal is about one ticket. Treating it as the fleet's state would
    park the whole backlog behind a ticket nobody can do."""
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(tmp_path, ["ABC-1", "ABC-2"], {"alpha": 0, "beta": 0})
    for manager in ("alpha", "beta"):
        _refuse(layer, "ABC-1", manager, "this machine does not have module 'x'")
    project = _project_for_pool(["alpha", "beta"])
    lines = _assign_the_pool(
        layer, project.config.coordination, project, "alpha", board, NOW
    )

    assert [t for t, _ in board.labelled] == ["ABC-2"]
    assert any("every manager has refused it" in line for line in lines), lines


def test_rule_3_does_not_assign_past_the_schedule(tmp_path):
    """Routing by in-flight alone puts work on a Manager already at its Worker
    limit, where it sits as an invisible backlog on one machine."""
    from rite_ai.config.models import ScheduleConfig, ScheduleWindow
    from rite_ai.scheduler import _assign_the_pool

    layer, board = _pool_setup(tmp_path, ["ABC-1"], {"alpha": 2, "beta": 3})
    project = _project_for_pool(["alpha", "beta"])
    project.config.schedule = ScheduleConfig(
        timezone="UTC", windows=[ScheduleWindow(hours="00:00-23:59", workers=2)]
    )
    lines = _assign_the_pool(
        layer, project.config.coordination, project, "alpha", board, NOW
    )

    assert board.labelled == []
    assert any("schedule's limit of 2" in line for line in lines), lines


def test_rule_3_needs_no_new_heartbeat_field(tmp_path):
    """The re-check Q9 asked for: `in_flight` exists, a published capacity does
    not — and is not needed, because `schedule:` is committed config that every
    machine reads, so the Owner computes the same ceiling the receiving machine
    will apply to itself."""
    from rite_ai.coordination.schemas import ManagerStatus

    fields = set(ManagerStatus.__dataclass_fields__)
    assert "in_flight" in fields
    assert "capacity" not in fields
