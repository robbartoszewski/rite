"""The schedule expresses a working week, and something enforces it.

Two gaps closed together, because either alone leaves a contradiction.

**It could not express a week.** `ScheduleWindow` was hours plus a count,
and `workers_at` took a minute-of-day with no date, so every window applied
to all seven days. "3 workers 09:00-17:00 Monday to Friday, 0 at the
weekend" — the ordinary shape of a working week — had no expression.

**And nothing enforced it.** `start_worker` checked the flat
`sandbox.max_concurrent_workers` and never called `workers_at`, so a project
configured for zero Workers at the weekend started one anyway whenever
anything asked, while `rite loop status` reported `closed` about a Worker
that was running. Two true sentences that disagreed — the signature of a
value that is computed and never read.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rite_ai.config.models import ScheduleConfig, ScheduleWindow
from rite_ai.schedule import (
    ALL_DAYS,
    Moment,
    ResolvedZone,
    next_open,
    parse_days,
    resolve_zone,
    workers_at,
)

MON, TUE, FRI, SAT, SUN = 0, 1, 4, 5, 6


def roberts_example() -> ScheduleConfig:
    """Mon-Fri 3 workers 09:00-17:00, 1 outside those hours, 0 at weekends."""
    return ScheduleConfig(
        timezone="Europe/Warsaw",
        windows=[
            ScheduleWindow(days="Mon-Fri", hours="09:00-17:00", workers=3),
            ScheduleWindow(days="Mon-Fri", hours="17:00-09:00", workers=1),
            ScheduleWindow(days="Sat-Sun", hours="00:00-23:59", workers=0),
        ],
    )


@pytest.mark.parametrize(
    "label,weekday,minute,expected",
    [
        ("Tue 10:00 — inside working hours", TUE, 10 * 60, 3),
        ("Tue 08:59 — a minute before", TUE, 8 * 60 + 59, 1),
        ("Tue 17:00 — the boundary itself", TUE, 17 * 60, 1),
        ("Tue 03:00 — through midnight", TUE, 3 * 60, 1),
        ("Fri 16:59 — last working minute of the week", FRI, 16 * 60 + 59, 3),
        ("Sat 10:00 — weekend", SAT, 10 * 60, 0),
        ("Sun 14:00 — weekend", SUN, 14 * 60, 0),
        ("Mon 00:30 — the week resuming", MON, 30, 1),
    ],
)
def test_the_working_week_is_expressible(label, weekday, minute, expected):
    assert workers_at(roberts_example(), minute, weekday) == expected, label


def test_a_window_with_no_days_still_means_every_day():
    """Every window written before `days` existed meant all seven, so an
    existing schedule must keep its exact meaning with no migration."""
    old = ScheduleConfig(
        timezone="Europe/Warsaw",
        windows=[ScheduleWindow(hours="09:00-17:00", workers=2)],
    )
    for weekday in range(7):
        assert workers_at(old, 10 * 60, weekday) == 2
    assert parse_days("") == ALL_DAYS


def test_a_day_range_wraps_like_the_hours_do():
    """A week is a cycle. "Fri-Mon" is four days, not an error."""
    days = parse_days("Fri-Mon")
    assert days == frozenset({FRI, SAT, SUN, MON})


def test_uncovered_time_is_zero_not_the_flat_cap():
    """The value most projects meet first and never configure. Stated as a
    decision: a gap is an intentional off-window, never an inherited count."""
    sparse = ScheduleConfig(
        timezone="Europe/Warsaw",
        windows=[ScheduleWindow(days="Mon", hours="09:00-10:00", workers=5)],
    )
    assert workers_at(sparse, 9 * 60 + 30, MON) == 5
    assert workers_at(sparse, 11 * 60, MON) == 0
    assert workers_at(sparse, 9 * 60 + 30, TUE) == 0


def test_next_open_names_when_to_come_back():
    """A refusal that says when the window opens is a different thing from
    one that only says no."""
    zone = ResolvedZone("Europe/Warsaw", machine_local=False)
    assert next_open(roberts_example(), Moment(10 * 60, SAT, zone)) == "Mon 00:00"


def test_a_schedule_that_never_opens_says_nothing_rather_than_guessing():
    shut = ScheduleConfig(
        timezone="Europe/Warsaw",
        windows=[ScheduleWindow(hours="00:00-23:59", workers=0)],
    )
    zone = ResolvedZone("Europe/Warsaw", machine_local=False)
    assert next_open(shut, Moment(0, MON, zone)) == ""


class TestTheResolvedClockIsReported:
    """A wrong timezone shifts every hour and looks correct doing it."""

    def test_a_configured_zone_is_used_and_named_as_configured(self):
        zone = resolve_zone("Europe/Warsaw")
        assert zone.name == "Europe/Warsaw"
        assert not zone.machine_local
        assert zone.describe() == "schedule in Europe/Warsaw (from config)"

    def test_an_empty_zone_falls_back_to_the_machine_and_SAYS_SO(self):
        zone = resolve_zone("")
        assert zone.machine_local
        assert "machine local" in zone.describe()

    def test_an_unrecognised_zone_does_not_silently_become_utc(self):
        """It falls back to the machine, and the description says machine
        local rather than naming the zone that was asked for — the operator
        needs to see that what they wrote is not what is being used."""
        zone = resolve_zone("Mars/Olympus_Mons")
        assert zone.machine_local
        assert "Mars/Olympus_Mons" not in zone.describe()


class TestStartWorkerRefusesOutsideTheWindow:
    """The half that makes the schedule real. Before 0.5.0 `workers_at` was
    read by the loop's verdict and by nothing that could stop a start."""

    def _project(self, windows: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="sched-"))
        (root / ".rite").mkdir()
        (root / ".rite" / "brief.yaml").write_text(
            "project:\n  name: t\n  role: owner\n"
        )
        (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
        (root / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\n"
            "sandbox:\n  max_concurrent_workers: 3\n" + windows
        )
        return root

    def test_a_closed_window_refuses_and_names_when_it_opens(self, monkeypatch):
        import rite_ai.schedule as sched
        from rite_ai import sandbox
        from rite_ai.schedule import Moment, ResolvedZone

        # ⚠ THE WEEKDAY WINDOW COMES FIRST, and the order is the test.
        #
        # This previously listed `Sat-Sun / 00:00-23:59 / 0` first, so at
        # 10:00 on a Saturday the first window matched on HOURS alone and
        # returned 0 whether or not the day was consulted. The test asserted
        # a refusal and got one for the wrong reason: measured, `workers_at`
        # returned 0 for weekday=SAT and 0 for weekday=None.
        #
        # With `Mon-Fri / 09:00-17:00 / 3` first, the day decides: 0 when
        # Saturday is known, 3 when it is ignored. The test now fails if the
        # enforcement point stops consulting the day.
        root = self._project(
            "schedule:\n  timezone: Europe/Warsaw\n  windows:\n"
            '    - {days: "Mon-Fri", hours: "09:00-17:00", workers: 3}\n'
            '    - {days: "Sat-Sun", hours: "00:00-23:59", workers: 0}\n'
        )
        zone = ResolvedZone("Europe/Warsaw", machine_local=False)
        monkeypatch.setattr(
            sched, "current_moment", lambda _tz, now=None: Moment(10 * 60, SAT, zone)
        )

        refusal = sandbox._schedule_refusal(root)
        assert refusal is not None, (
            "a Saturday start was permitted under a schedule allowing zero "
            "Workers at the weekend — the schedule is advisory again"
        )
        assert "0 Workers" in refusal
        assert "Mon 09:00" in refusal or "Next open" in refusal
        assert "Europe/Warsaw" in refusal

    def test_an_open_window_does_not_refuse(self, monkeypatch):
        import rite_ai.schedule as sched
        from rite_ai import sandbox
        from rite_ai.schedule import Moment, ResolvedZone

        root = self._project(
            "schedule:\n  timezone: Europe/Warsaw\n  windows:\n"
            '    - {days: "Mon-Fri", hours: "09:00-17:00", workers: 3}\n'
        )
        zone = ResolvedZone("Europe/Warsaw", machine_local=False)
        monkeypatch.setattr(
            sched, "current_moment", lambda _tz, now=None: Moment(10 * 60, TUE, zone)
        )
        assert sandbox._schedule_refusal(root) is None

    def test_a_project_with_no_schedule_is_unaffected(self):
        from rite_ai import sandbox

        root = self._project("")
        assert sandbox._schedule_refusal(root) is None, (
            "a project that never configured a schedule must not start "
            "refusing every Worker"
        )
