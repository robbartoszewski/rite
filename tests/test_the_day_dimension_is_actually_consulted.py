"""`days:` parses, and every caller evaluating a real moment must USE it.

⚠ **It did not, and the defect shipped inside the release that added the
feature.** `workers_at(schedule, minute_of_day, weekday=None)` treats
`None` as "ignore the day dimension" — correct for callers written before
`days:` existed, and silently WRONG for a schedule that uses it. Four call
sites evaluating the current moment omitted it:

    loop/_capacity, scheduler's tick, scheduler's assignment ceiling,
    coordination/distribution

Measured on a schedule of `Mon-Fri 09:00-17:00 → 3` and
`Sat-Sun 00:00-23:59 → 0`, at Saturday noon:

    weekday supplied : 0      <- what the user configured
    weekday omitted  : 3      <- what loop and scheduler reported

So `rite start` refused to start a Worker (enforcement passes the weekday)
while the loop reported capacity for three. **Two true sentences that
disagree — the identical failure the advisory schedule produced before
0.5.1, reintroduced one layer up by a parameter nobody supplied.**

The dead-wiring guard cannot see this class: it asks whether a FUNCTION is
called, never whether a PARAMETER is ever passed. So the structural check
below is specific to the one function where omitting an argument silently
changes the answer instead of failing.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rite_ai.config.models import ScheduleConfig
from rite_ai.schedule import ScheduleWindow, workers_at

SRC = Path(__file__).resolve().parents[1] / "src" / "rite_ai"

# A call that deliberately asks the day-independent question. Each entry is
# a reason, not a silencer: it must say why THIS call has no weekday.
WEEKDAY_NOT_NEEDED: dict[tuple[str, int], str] = {}


def _workers_at_calls() -> list[tuple[str, int, int]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "workers_at":
                continue
            supplied = len(node.args) + len(
                [k for k in node.keywords if k.arg == "weekday"]
            )
            found.append((str(path.relative_to(SRC)), node.lineno, supplied))
    return found


def test_every_workers_at_call_supplies_a_weekday():
    found = _workers_at_calls()
    assert found, "no `workers_at` calls found — this guard stopped guarding"
    missing = [
        f"{mod}:{line}"
        for mod, line, supplied in found
        if supplied < 3 and (mod, line) not in WEEKDAY_NOT_NEEDED
    ]
    assert not missing, (
        "these `workers_at` calls omit the weekday, so they ignore `days:` "
        "and silently answer for the wrong day: " + ", ".join(missing)
    )


class TestTheAnswerActuallyDiffers:
    """The structural check above is worthless if the two answers are the
    same, so the difference it protects is asserted directly."""

    @pytest.fixture
    def weekend_off(self) -> ScheduleConfig:
        return ScheduleConfig(
            timezone="Europe/Warsaw",
            windows=[
                ScheduleWindow(hours="09:00-17:00", workers=3, days="Mon-Fri"),
                ScheduleWindow(hours="00:00-23:59", workers=0, days="Sat-Sun"),
            ],
        )

    def test_saturday_with_the_weekday_is_zero(self, weekend_off):
        assert workers_at(weekend_off, 12 * 60, 5) == 0

    def test_saturday_without_it_is_the_weekday_count(self, weekend_off):
        """Not a bug in `workers_at` — this is what `None` MEANS. It is the
        reason omitting it at a call site is the defect."""
        assert workers_at(weekend_off, 12 * 60, None) == 3

    def test_a_weekday_is_unaffected(self, weekend_off):
        assert workers_at(weekend_off, 12 * 60, 2) == 3


class TestTheLoopAgreesWithEnforcement:
    """The end the user sees: `rite loop` must not report capacity on a day
    the schedule closed, because `start_worker` will refuse it."""

    def test_loop_capacity_is_zero_on_a_closed_saturday(self, tmp_path):
        from rite_ai.loop import _capacity

        class Project:
            class config:
                schedule = ScheduleConfig(
                    timezone="Europe/Warsaw",
                    windows=[
                        ScheduleWindow(hours="09:00-17:00", workers=3, days="Mon-Fri"),
                        ScheduleWindow(hours="00:00-23:59", workers=0, days="Sat-Sun"),
                    ],
                )

        # 2026-09-19 is a Saturday.
        saturday = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
        count, _when = _capacity(Project(), saturday)
        assert count == 0, (
            "the loop reported capacity on a day the schedule closed, while "
            "`start_worker` refuses — two true sentences that disagree"
        )

    def test_loop_capacity_is_the_configured_count_on_a_weekday(self, tmp_path):
        from rite_ai.loop import _capacity

        class Project:
            class config:
                schedule = ScheduleConfig(
                    timezone="Europe/Warsaw",
                    windows=[
                        ScheduleWindow(hours="09:00-17:00", workers=3, days="Mon-Fri"),
                    ],
                )

        wednesday = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        count, _when = _capacity(Project(), wednesday)
        assert count == 3
