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


# ⚠ COUNTING ARGUMENTS WAS A PROXY, and `None` satisfied it.
#
# This returned `len(node.args) + keywords named weekday` and asked whether
# that reached 3. `workers_at(schedule, minute, None)` reaches 3 — and
# `None` is exactly the value that means "ignore the day", so the guard
# passed on the one input it exists to forbid. Measured: replacing the
# weekday with `None` at the scheduler, distribution and sandbox call sites
# left 44, 30 and 25 tests green respectively.
#
# So it now reports what was PASSED, and a literal `None` is a violation
# rather than a satisfied count.
ABSENT = "absent"
EXPLICIT_NONE = "None"
SUPPLIED = "supplied"


def _weekday_of(node: ast.Call) -> str:
    """What this call passes as `weekday`: absent, a literal None, or a value."""
    arg = None
    if len(node.args) >= 3:
        arg = node.args[2]
    else:
        for k in node.keywords:
            if k.arg == "weekday":
                arg = k.value
    if arg is None:
        return ABSENT
    if isinstance(arg, ast.Constant) and arg.value is None:
        return EXPLICIT_NONE
    return SUPPLIED


def _workers_at_calls() -> list[tuple[str, int, str]]:
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
            found.append((str(path.relative_to(SRC)), node.lineno, _weekday_of(node)))
    return found


def test_every_workers_at_call_supplies_a_weekday():
    found = _workers_at_calls()
    assert found, "no `workers_at` calls found — this guard stopped guarding"
    missing = [
        f"{mod}:{line}"
        for mod, line, passed in found
        if passed == ABSENT and (mod, line) not in WEEKDAY_NOT_NEEDED
    ]
    assert not missing, (
        "these `workers_at` calls omit the weekday, so they ignore `days:` "
        "and silently answer for the wrong day: " + ", ".join(missing)
    )
    nulled = [
        f"{mod}:{line}"
        for mod, line, passed in found
        if passed == EXPLICIT_NONE and (mod, line) not in WEEKDAY_NOT_NEEDED
    ]
    assert not nulled, (
        "these `workers_at` calls pass `weekday=None`, which MEANS ignore "
        "the day — the argument is present and the `days:` config is not "
        "consulted, which is the failure this guard exists to catch and "
        "which counting arguments could not see: " + ", ".join(nulled)
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


class TestDistributionAgreesWithTheDay:
    """⚠ THIS TEST COULD NOT BE WRITTEN BEFORE, and that is the finding.

    `distribute()` took the MINUTE from the caller's `now` and the WEEKDAY
    from `current_moment(...)` with no `now` at all — two halves of one
    instant, from two clocks. Measured on a schedule open Sundays only,
    asked about a Saturday:

        capacity reported: 3      expected: 0

    So the day could not be pinned by a test, which is exactly why this
    call site had no behavioural coverage to lose: mutating its weekday to
    `None` left 30 tests green.

    Both now come from one `current_moment(schedule.timezone, now)`.
    """

    class _Backend:
        def list_tickets(self, *_a, **_k):
            return []

    def _schedule(self) -> ScheduleConfig:
        # Open on SUNDAY only, so the day is the whole of the answer.
        return ScheduleConfig(
            timezone="Europe/Warsaw",
            windows=[ScheduleWindow(hours="00:00-24:00", workers=3, days="Sun")],
        )

    def _capacity(self, tmp_path, when):
        from rite_ai.coordination.distribution import distribute

        (tmp_path / ".rite").mkdir(exist_ok=True)
        result = distribute(
            tmp_path,
            self._Backend(),
            manager="m",
            workers=["w1"],
            schedule=self._schedule(),
            now=when,
            busy=set(),
            modules=set(),
        )
        return getattr(result, "capacity", None)

    def test_a_closed_saturday_distributes_nothing(self, tmp_path):
        saturday = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
        assert self._capacity(tmp_path, saturday) == 0, (
            "distribution reported capacity on a day the schedule closed — "
            "the weekday is not coming from the moment it was asked about"
        )

    def test_the_open_day_is_unaffected(self, tmp_path):
        sunday = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        assert self._capacity(tmp_path, sunday) == 3
