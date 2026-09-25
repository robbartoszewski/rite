"""Check-in windows (plan § K1): the schedule's grammar and clock, not a second.

A check-in window is a `schedule.windows[]` entry without `workers`. It is
read by the schedule's own `_parse_hours` / `parse_days`, on the schedule's
own zone, and a malformed one is reported by `rite doctor` in the words a
malformed schedule window gets. What is pinned here is that sameness — a
second parser or a second zone rule would drift, and a 14:00 check-in would
land at 15:00 for somebody whose schedule is right.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.init.scaffold import config_to_yaml
from rite_ai.cli.main import cli
from rite_ai.config.models import (
    CheckinsConfig,
    CheckinWindow,
    ScheduleConfig,
    ScheduleWindow,
)
from rite_ai.config.parse import ParseError, parse_config
from rite_ai.schedule import (
    Moment,
    describe_checkins,
    in_checkin,
    next_checkin,
    resolve_zone,
    validate_checkins,
    validate_schedule,
)

ZONE = resolve_zone("Europe/Warsaw")
MON, FRI, SAT = 0, 4, 5


def _at(weekday: int, hh: int, mm: int = 0) -> Moment:
    return Moment(hh * 60 + mm, weekday, ZONE)


ROBERT = CheckinsConfig(
    windows=[
        CheckinWindow(days="Mon-Fri", hours="09:00-10:00"),
        CheckinWindow(days="Mon-Fri", hours="14:00-15:00"),
        CheckinWindow(days="Mon-Fri", hours="20:00-21:00"),
    ]
)


# --- when ----------------------------------------------------------------------


def test_the_next_window_is_the_next_one_today():
    assert next_checkin(ROBERT, _at(MON, 10, 30)).starts == "Mon 14:00"


def test_how_far_away_it_is_is_counted_in_minutes():
    assert next_checkin(ROBERT, _at(MON, 13, 15)).minutes_away == 45


def test_inside_a_window_it_says_when_the_window_closes():
    when = next_checkin(ROBERT, _at(MON, 14, 20))
    assert when.open_now and when.until == "Mon 15:00"


def test_the_end_of_a_window_is_not_inside_it():
    """`[start, end)`, as for the schedule: 15:00 belongs to the gap."""
    assert not in_checkin(ROBERT, _at(MON, 15, 0))
    assert in_checkin(ROBERT, _at(MON, 14, 59))


def test_days_skip_the_weekend():
    assert next_checkin(ROBERT, _at(FRI, 21, 0)).starts == "Mon 09:00"


def test_a_window_that_wraps_past_midnight_is_found_and_closes_next_day():
    late = CheckinsConfig(windows=[CheckinWindow(hours="23:30-00:30")])
    assert next_checkin(late, _at(MON, 22, 0)).starts == "Mon 23:30"
    assert in_checkin(late, _at(MON, 23, 45))
    assert in_checkin(late, _at(MON, 0, 10))
    assert next_checkin(late, _at(MON, 23, 45)).until == "Tue 00:30"


def test_a_wrapping_window_reads_days_exactly_as_the_schedule_does():
    """The schedule checks `days` against the weekday of the minute being
    asked about, so Fri 23:30-00:30 on `Mon-Fri` stops at midnight into
    Saturday. Whatever one thinks of that, a check-in must agree with it —
    two readings of one grammar is the drift this ticket rules out."""
    from rite_ai.schedule import workers_at

    sched = ScheduleConfig(
        windows=[ScheduleWindow(days="Mon-Fri", hours="23:30-00:30", workers=1)]
    )
    checks = CheckinsConfig(
        windows=[CheckinWindow(days="Mon-Fri", hours="23:30-00:30")]
    )
    for weekday in range(7):
        for minute in (0, 15, 29, 30, 23 * 60 + 29, 23 * 60 + 30, 23 * 60 + 59):
            moment = Moment(minute, weekday, ZONE)
            assert in_checkin(checks, moment) == bool(
                workers_at(sched, minute, weekday)
            ), (weekday, minute)


# --- no windows is said ----------------------------------------------------------


def test_no_windows_is_said_not_implied():
    line = describe_checkins(CheckinsConfig(), _at(MON, 9))
    assert "none configured" in line
    assert "asked at once" in line


def test_the_line_names_the_clock_it_was_read_against():
    line = describe_checkins(ROBERT, _at(MON, 10, 30))
    assert "next at Mon 14:00 (in 3h30m)" in line
    assert ZONE.describe() in line


def test_only_malformed_windows_is_not_reported_as_a_time():
    broken = CheckinsConfig(windows=[CheckinWindow(hours="9-10")])
    line = describe_checkins(broken, _at(MON, 9))
    assert "malformed" in line and "rite doctor" in line


# --- the schedule's own words ------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "days"),
    [("9-10", ""), ("25:00-26:00", ""), ("09:00-10:00", "Mon-Fry")],
)
def test_a_malformed_window_is_named_in_the_schedules_own_words(hours, days):
    [schedule_problem] = [
        p
        for p in validate_schedule(
            ScheduleConfig(
                timezone="UTC",
                windows=[
                    ScheduleWindow(hours=hours, days=days, workers=1),
                    ScheduleWindow(hours="00:00-24:00", workers=0),
                ],
            ),
            max_concurrent_workers=4,
        )
        if "covered" not in p
    ]
    [checkin_problem] = validate_checkins(
        CheckinsConfig(windows=[CheckinWindow(hours=hours, days=days)])
    )
    # The fault's own sentence is shared; only the consequence differs.
    fault = schedule_problem.split(" — ")[0]
    assert checkin_problem.startswith(fault), (schedule_problem, checkin_problem)


def test_a_good_list_has_no_problems():
    assert validate_checkins(ROBERT) == []


# --- config -------------------------------------------------------------------------


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_the_config_shape_from_the_design_parses(tmp_path):
    config = parse_config(
        _config(
            tmp_path,
            "checkins:\n  windows:\n"
            '    - {days: Mon-Fri, hours: "09:00-10:00"}\n'
            '    - {days: Mon-Fri, hours: "14:00-15:00"}\n',
        )
    )
    assert not isinstance(config, ParseError), config
    assert config.checkins.windows == ROBERT.windows[:2]


def test_workers_in_a_checkin_window_is_refused_by_name(tmp_path):
    """A check-in window is when the User is around, not a Worker count; a
    `workers:` read as nothing is a setting its author believes they made."""
    result = parse_config(
        _config(
            tmp_path,
            'checkins:\n  windows:\n    - {hours: "09:00-10:00", workers: 2}\n',
        )
    )
    assert isinstance(result, ParseError)
    assert "checkins.windows[0]" in result.message and "'workers'" in result.message


def test_the_writer_keeps_the_section(tmp_path):
    """`rite schedule set` rewrites config.yaml through `config_to_yaml`; a
    section it does not emit is a section it deletes."""
    config = parse_config(
        _config(tmp_path, 'checkins:\n  windows:\n    - {days: Mon, hours: "09:00-10:00"}\n')
    )
    assert not isinstance(config, ParseError)
    again = parse_config(_config(tmp_path, config_to_yaml(config)))
    assert not isinstance(again, ParseError)
    assert again.checkins == config.checkins


# --- through the CLI ------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    def build(checkins: str) -> Path:
        rite = tmp_path / ".rite"
        rite.mkdir(exist_ok=True)
        (rite / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\n"
            "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
        )
        (rite / "modules.yaml").write_text("modules: {}\n")
        (rite / "config.yaml").write_text(checkins)
        monkeypatch.chdir(tmp_path)
        return tmp_path

    return build


def test_status_prints_the_checkin_line(project):
    project('checkins:\n  windows:\n    - {hours: "00:00-24:00"}\n')
    result = CliRunner().invoke(cli, ["status", "--no-board"])
    assert result.exit_code == 0, result.output
    assert "check-ins: open now" in result.output


def test_status_says_when_none_are_configured(project):
    project("heartbeat:\n  interval_minutes: 10\n")
    result = CliRunner().invoke(cli, ["status", "--no-board"])
    assert "check-ins: none configured" in result.output


def test_doctor_names_a_malformed_window(project):
    project('checkins:\n  windows:\n    - {hours: "9-10"}\n')
    result = CliRunner().invoke(cli, ["doctor"])
    assert "checkins: invalid hours range: '9-10' (expected HH:MM-HH:MM)" in (
        result.output
    )
