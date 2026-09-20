from datetime import UTC, datetime

from rite_ai.config.models import ScheduleConfig, ScheduleWindow
from rite_ai.schedule import (
    ScheduleError,
    check_worker_cap,
    current_minute_of_day,
    current_moment,
    resolve_zone,
    upsert_window,
    validate_schedule,
    workers_at,
)


class TestUpsertWindow:
    def test_spec_two_call_example_produces_two_independent_windows(self):
        """SPEC §2.7's own example: two independent calls, each untouched
        by the other."""
        windows: list[ScheduleWindow] = []
        result = upsert_window(windows, "09:00-18:00", 3)
        assert not isinstance(result, ScheduleError)
        result = upsert_window(result, "18:00-09:00", 0)
        assert not isinstance(result, ScheduleError)

        assert workers_from(result, "10:00") == 3
        assert workers_from(result, "20:00") == 0
        assert workers_from(result, "02:00") == 0  # through-midnight piece

    def test_setting_an_overlapping_range_splits_the_existing_window(self):
        windows = [ScheduleWindow(hours="09:00-18:00", workers=3)]
        result = upsert_window(windows, "12:00-14:00", 1)
        assert not isinstance(result, ScheduleError)

        assert workers_from(result, "10:00") == 3
        assert workers_from(result, "13:00") == 1
        assert workers_from(result, "17:00") == 3

    def test_non_overlapping_window_is_left_byte_for_byte_unchanged(self):
        windows = [ScheduleWindow(hours="09:00-18:00", workers=3)]
        result = upsert_window(windows, "20:00-22:00", 2)
        assert not isinstance(result, ScheduleError)
        untouched = [w for w in result if w.hours == "09:00-18:00"]
        assert len(untouched) == 1
        assert untouched[0].workers == 3

    def test_exact_replacement_of_an_existing_window(self):
        windows = [ScheduleWindow(hours="09:00-18:00", workers=3)]
        result = upsert_window(windows, "09:00-18:00", 5)
        assert not isinstance(result, ScheduleError)
        assert workers_from(result, "12:00") == 5

    def test_setting_a_range_that_swallows_an_existing_window_removes_it(self):
        windows = [ScheduleWindow(hours="12:00-14:00", workers=1)]
        result = upsert_window(windows, "09:00-18:00", 3)
        assert not isinstance(result, ScheduleError)
        assert workers_from(result, "13:00") == 3

    def test_invalid_hours_string_returns_an_error(self):
        result = upsert_window([], "not-a-range", 3)
        assert isinstance(result, ScheduleError)

    def test_wraparound_upsert_splits_a_non_wrapping_existing_window(self):
        windows = [ScheduleWindow(hours="00:00-24:00", workers=3)]
        result = upsert_window(windows, "22:00-06:00", 0)
        assert not isinstance(result, ScheduleError)
        assert workers_from(result, "23:00") == 0
        assert workers_from(result, "03:00") == 0
        assert workers_from(result, "12:00") == 3


class TestWorkersAt:
    def test_gap_defaults_to_zero(self):
        schedule = ScheduleConfig(
            timezone="UTC", windows=[ScheduleWindow(hours="09:00-18:00", workers=3)]
        )
        assert workers_at(schedule, _minutes("20:00")) == 0

    def test_all_day_constant(self):
        schedule = ScheduleConfig(
            timezone="UTC", windows=[ScheduleWindow(hours="00:00-24:00", workers=2)]
        )
        assert workers_at(schedule, _minutes("00:00")) == 2
        assert workers_at(schedule, _minutes("23:59")) == 2

    def test_midnight_wraparound_window(self):
        schedule = ScheduleConfig(
            timezone="UTC", windows=[ScheduleWindow(hours="18:00-09:00", workers=1)]
        )
        assert workers_at(schedule, _minutes("23:00")) == 1
        assert workers_at(schedule, _minutes("05:00")) == 1
        assert workers_at(schedule, _minutes("12:00")) == 0


class TestValidateSchedule:
    def test_no_windows_is_valid(self):
        assert validate_schedule(ScheduleConfig(), max_concurrent_workers=5) == []

    def test_full_coverage_no_cap_violation_is_valid(self):
        schedule = ScheduleConfig(
            timezone="UTC",
            windows=[
                ScheduleWindow(hours="09:00-18:00", workers=3),
                ScheduleWindow(hours="18:00-09:00", workers=0),
            ],
        )
        assert validate_schedule(schedule, max_concurrent_workers=5) == []

    def test_gap_in_coverage_is_reported(self):
        schedule = ScheduleConfig(
            timezone="UTC", windows=[ScheduleWindow(hours="09:00-18:00", workers=3)]
        )
        problems = validate_schedule(schedule, max_concurrent_workers=5)
        assert any("not covered" in p for p in problems)

    def test_window_over_cap_is_refused_not_clamped(self):
        schedule = ScheduleConfig(
            timezone="UTC", windows=[ScheduleWindow(hours="00:00-24:00", workers=10)]
        )
        problems = validate_schedule(schedule, max_concurrent_workers=5)
        assert any("exceeding" in p for p in problems)

    def test_missing_timezone_with_windows_is_reported(self):
        schedule = ScheduleConfig(
            timezone="", windows=[ScheduleWindow(hours="00:00-24:00", workers=1)]
        )
        problems = validate_schedule(schedule, max_concurrent_workers=5)
        assert any("timezone" in p for p in problems)


class TestCheckWorkerCap:
    def test_within_cap_is_none(self):
        assert check_worker_cap(3, 5) is None

    def test_at_cap_exactly_is_none(self):
        assert check_worker_cap(5, 5) is None

    def test_over_cap_returns_a_message_never_clamps(self):
        result = check_worker_cap(6, 5)
        assert result is not None
        assert "exceeding" in result
        assert "refused, not clamped" in result


class TestCurrentMinuteOfDay:
    """⚠ THIS CONTRACT CHANGED IN 0.5.1 (D-48 relaxed).

    These asserted `None` for an empty or unresolvable zone, so a caller
    could "skip the check rather than evaluate against the wrong clock".
    That was one of TWO live policies: `current_moment`, written later for
    the same question, fell back to the machine's clock. Which applied
    depended on which function a caller reached for, so on the documented
    default of an unset timezone `sandbox.start_worker` enforced the
    schedule normally while `coordination.distribution` assigned nothing.

    Machine-local won, and this delegates rather than holding a second
    opinion. An unusable zone is reported through `ResolvedZone.rejected`
    and `rite doctor`, not by refusing to answer what time it is.
    """

    def test_an_empty_timezone_reads_the_machine_clock(self):
        assert current_minute_of_day("") == current_moment("").minute_of_day

    def test_an_unknown_timezone_reads_the_machine_clock(self):
        assert (
            current_minute_of_day("Not/A_Real_Zone")
            == current_moment("Not/A_Real_Zone").minute_of_day
        )

    def test_an_unknown_timezone_is_still_reported_as_rejected(self):
        """The fallback is not silent — that distinction is the whole of
        finding B, and it is what makes returning a number safe here."""
        zone = resolve_zone("Not/A_Real_Zone")
        assert zone.rejected == "Not/A_Real_Zone"
        assert zone.describe() != resolve_zone("").describe()

    def test_utc_reads_directly(self):
        now = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
        assert current_minute_of_day("UTC", now=now) == 14 * 60 + 30

    def test_converts_across_a_real_offset(self):
        # 2026-01-01 14:30 UTC is 15:30 in Europe/Warsaw (UTC+1 in January).
        now = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
        assert current_minute_of_day("Europe/Warsaw", now=now) == 15 * 60 + 30


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def workers_from(windows: list[ScheduleWindow], hhmm: str) -> int:
    return workers_at(ScheduleConfig(timezone="UTC", windows=windows), _minutes(hhmm))
