"""A machine can be more than one Manager (RL-T35, RL-60).

`.rite/machine` held one line because Q8 asked "which Manager is this
machine", and the question's shape supplied the answer. rite local breaks it:
its Q1 answer is "one machine", with a lead, a planner and an executor sharing
it, each needing its own name because the Owner routes on heartbeats and a
ticket labelled `planner` is picked up by whatever publishes as `planner`.

The properties here are the ones that make the change safe rather than the
ones that make it work:

- a single-line file behaves exactly as it does today, on every path;
- the FIRST name stays the one that holds the lease and the claims, because a
  machine holds one lease and two local Managers competing for the Owner role
  on one box is nonsense;
- a file this version cannot read fully is not enrolled at all, rather than
  enrolled as its first line;
- a Manager that has never published is reported as never started, not as
  "cannot tell" — that distinction is the whole point of naming them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.identity import enrolment, hosted_managers, this_manager

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _machine(tmp_path, text: str):
    rite = tmp_path / ".rite"
    rite.mkdir(exist_ok=True)
    (rite / "machine").write_text(text)
    return tmp_path


# --- the file ---------------------------------------------------------------------


def test_one_name_reads_exactly_as_it_did(tmp_path):
    """Every enrolled project today. Nothing about it may change."""
    root = _machine(tmp_path, "mac-studio\n")
    assert this_manager(root) == "mac-studio"
    assert hosted_managers(root) == ["mac-studio"]


def test_several_names_are_all_hosted_and_the_first_is_primary(tmp_path):
    root = _machine(tmp_path, "lead\nplanner\nexecutor\n")
    assert hosted_managers(root) == ["lead", "planner", "executor"]
    # The lease, the claims and standing for Owner stay with one name.
    assert this_manager(root) == "lead"


def test_blank_lines_and_padding_are_not_names(tmp_path):
    root = _machine(tmp_path, "  lead  \n\n\tplanner\n\n")
    assert hosted_managers(root) == ["lead", "planner"]


def test_a_missing_file_is_not_enrolled(tmp_path):
    (tmp_path / ".rite").mkdir()
    assert hosted_managers(tmp_path) == [] and this_manager(tmp_path) is None


def test_a_name_that_is_not_a_usable_key_voids_the_whole_file(tmp_path):
    """Half a declaration is not a smaller declaration, it is an unknown one:
    a second line this version cannot read is evidence the file means
    something other than what it appears to."""
    root = _machine(tmp_path, "lead\nteam/planner\n")
    assert hosted_managers(root) == []
    assert this_manager(root) is None


def test_a_name_with_spaces_in_it_voids_the_file(tmp_path):
    assert hosted_managers(_machine(tmp_path, "mac studio\n")) == []


def test_the_same_manager_twice_voids_the_file(tmp_path):
    """Two of the same Manager on one machine would publish one heartbeat
    between them and take the same work twice."""
    assert hosted_managers(_machine(tmp_path, "lead\nplanner\nlead\n")) == []


# --- enrolment --------------------------------------------------------------------


def _config(**kw):
    kw.setdefault("managers", ["lead", "planner"])
    kw.setdefault("remote", "git@example.com:t/c.git")
    return CoordinationConfig(**kw)


def test_a_project_not_coordinating_at_all_is_silent(tmp_path):
    assert enrolment(_machine(tmp_path, "lead\n"), CoordinationConfig()) is None


def test_hosting_exactly_what_is_listed_is_fine(tmp_path):
    assert enrolment(_machine(tmp_path, "lead\nplanner\n"), _config()) is None


def test_a_primary_nobody_listed_is_named_as_before(tmp_path):
    problem = enrolment(_machine(tmp_path, "gamma\n"), _config())
    assert problem and "calls itself 'gamma'" in problem


def test_a_hosted_name_nobody_listed_is_its_own_report(tmp_path):
    """This machine IS enrolled and coordinating; the problem is one line
    further down a file nobody re-reads."""
    problem = enrolment(_machine(tmp_path, "lead\nexecutor\n"), _config())
    assert problem and "'executor'" in problem
    assert "nothing will ever be routed to it" in problem
    # Not the primary's message: that one would send a reader to the wrong line.
    assert "calls itself" not in problem


def test_an_unreadable_file_is_not_enrolled_rather_than_partly_enrolled(tmp_path):
    problem = enrolment(_machine(tmp_path, "lead\nbad/name\n"), _config())
    assert problem and "not enrolled" in problem


# --- never started, which is not "cannot tell" -------------------------------------


def test_a_manager_that_never_published_says_so(tmp_path):
    from rite_ai.coordination.heartbeat import liveness
    from rite_ai.coordination.local_backend import LocalStateLayer

    live = liveness(
        LocalStateLayer(tmp_path / "state"), "planner", now=NOW, interval_minutes=10
    )
    assert live.never_seen and not live.known
    assert "is anything running as it" in live.detail


def test_a_manager_that_has_published_is_not_never_seen(tmp_path):
    from rite_ai.coordination.heartbeat import liveness, publish_heartbeat
    from rite_ai.coordination.local_backend import LocalStateLayer

    layer = LocalStateLayer(tmp_path / "state")
    publish_heartbeat(
        layer, "planner", workers=[], in_flight=0, now=NOW - timedelta(seconds=5)
    )
    live = liveness(layer, "planner", now=NOW, interval_minutes=10)
    assert not live.never_seen and live.known


def test_assignment_distinguishes_never_started_from_cannot_tell(tmp_path):
    """A configuration a person fixes and a fleet problem were sharing one
    sentence."""
    from rite_ai.coordination.assignment import manager_views
    from rite_ai.coordination.local_backend import LocalStateLayer

    views = manager_views(
        LocalStateLayer(tmp_path / "state"),
        ["planner"],
        now=NOW,
        interval_minutes=10,
        stall_threshold=3,
    )
    assert views[0].why_not.startswith("never started")


def test_the_overview_reports_a_hosted_manager_nothing_runs(tmp_path):
    """The failure rite local will actually hit: three Managers declared, one
    of them running, and work labelled for the others waiting for ever."""
    from rite_ai.coordination.heartbeat import publish_heartbeat
    from rite_ai.coordination.local_backend import LocalStateLayer
    from rite_ai.coordination.overview import NEVER, read_overview

    layer = LocalStateLayer(tmp_path / "state")
    publish_heartbeat(
        layer, "lead", workers=[], in_flight=0, now=NOW - timedelta(seconds=5)
    )

    overview = read_overview(
        layer,
        _config(),
        now=NOW,
        heartbeat=HeartbeatConfig(),
        this_machine="lead",
        hosted=("lead", "planner"),
    )

    states = {m.name: m.state for m in overview.managers}
    assert states["planner"] == NEVER
    assert any("nothing has ever published as it" in p for p in overview.problems)
    assert all(m.is_this_machine for m in overview.managers)


def test_a_manager_on_somebody_elses_machine_is_not_this_machines_problem(tmp_path):
    """A name belonging to a box nobody has set up yet is reported as never
    started and NOT as a problem here — putting it in front of the one person
    who cannot act on it is how a problem list stops being read."""
    from rite_ai.coordination.local_backend import LocalStateLayer
    from rite_ai.coordination.overview import NEVER, read_overview

    overview = read_overview(
        LocalStateLayer(tmp_path / "state"),
        _config(),
        now=NOW,
        heartbeat=HeartbeatConfig(),
        this_machine="lead",
        hosted=("lead",),
    )

    states = {m.name: m.state for m in overview.managers}
    assert states["planner"] == NEVER
    assert not any("planner" in p for p in overview.problems)
