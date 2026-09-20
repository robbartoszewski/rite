"""A schedule must say which clock it is being read against, and why.

WHY THIS EXISTS. A typo was indistinguishable from an unset field.
Measured:

    configured='Europe/Lodnon'  -> schedule in Europe/Warsaw (machine local)
    configured=''               -> schedule in Europe/Warsaw (machine local)
      identical? True

`validate_schedule` returned `[]` for the typo and nothing in `rite doctor`
looked at it. A user who mistyped their own timezone was told the schedule
was running normally, on a clock they did not choose — which is the
silent-wrong-clock D-48 was written against, reappearing in the relaxation
of D-48.

⚠ AND TWO POLICIES WERE LIVE AT ONCE. `current_minute_of_day` returned
`None` for an empty or unresolvable zone; `current_moment`, written later
for the same question, fell back to the machine's clock. Which applied
depended on which function a caller reached for, so on the DOCUMENTED
DEFAULT — no timezone set — `sandbox.start_worker` enforced the schedule
against the machine clock while `coordination.distribution` refused to
assign anything at all. Two subsystems, opposite behaviour, one config.
"""

from __future__ import annotations

from rite_ai.config.models import ScheduleConfig
from rite_ai.schedule import (
    ScheduleWindow,
    current_minute_of_day,
    current_moment,
    resolve_zone,
    validate_schedule,
)


def test_a_rejected_timezone_does_not_look_like_an_unset_one():
    typo = resolve_zone("Europe/Lodnon")
    unset = resolve_zone("")
    assert typo.describe() != unset.describe(), (
        "a mistyped timezone and an unconfigured one produce the same "
        "sentence, so the user has no way to learn their own typo"
    )


def test_the_rejected_name_is_quoted_back():
    """Naming the machine's zone is not enough: the user needs to see the
    string they wrote, or they cannot find the typo in their config."""
    zone = resolve_zone("Europe/Lodnon")
    assert zone.machine_local, "a bad zone must not be treated as usable"
    assert "Europe/Lodnon" in zone.describe(), (
        f"the rejected name is not shown: {zone.describe()!r}"
    )


def test_a_good_timezone_says_it_came_from_config():
    zone = resolve_zone("Europe/Warsaw")
    assert not zone.machine_local
    assert "from config" in zone.describe()
    assert "not a known timezone" not in zone.describe()


def test_doctor_reports_a_timezone_it_could_not_resolve():
    """`validate_schedule` is what `rite doctor` renders."""
    bad = ScheduleConfig(
        timezone="Europe/Lodnon",
        windows=[ScheduleWindow(hours="00:00-24:00", workers=2, days="Mon-Sun")],
    )
    problems = validate_schedule(bad, 5)
    assert any("Europe/Lodnon" in p for p in problems), (
        f"doctor says nothing about an unusable timezone: {problems}"
    )


def test_a_valid_timezone_produces_no_complaint():
    good = ScheduleConfig(
        timezone="Europe/Warsaw",
        windows=[ScheduleWindow(hours="00:00-24:00", workers=2, days="Mon-Sun")],
    )
    assert not any("timezone" in p for p in validate_schedule(good, 5))


class TestOnePolicyNotTwo:
    """The two functions that answer "what time is it for this schedule"
    must agree about an unresolvable zone, or two subsystems disagree."""

    def test_the_minute_is_answered_for_an_unset_timezone(self):
        assert current_minute_of_day("") == current_moment("").minute_of_day, (
            "`current_minute_of_day` and `current_moment` disagree about an "
            "unset timezone — the documented default — so enforcement and "
            "distribution behave differently on the same config"
        )

    def test_the_minute_is_answered_for_a_rejected_timezone(self):
        assert (
            current_minute_of_day("Europe/Lodnon")
            == current_moment("Europe/Lodnon").minute_of_day
        )

    def test_a_resolvable_timezone_is_unaffected(self):
        assert (
            current_minute_of_day("Europe/Warsaw")
            == current_moment("Europe/Warsaw").minute_of_day
        )


def test_a_malformed_days_string_names_itself():
    """D. `workers_at` skips a window whose `days` will not parse, and
    uncovered time is 0 — so a typo silently means zero Workers. Measured:
    a schedule configured for 3 on weekdays gave 0 at Tuesday 10:00, and
    the only complaint was a generic uncovered-minutes warning."""
    typo = ScheduleConfig(
        timezone="Europe/Warsaw",
        windows=[ScheduleWindow(hours="09:00-17:00", workers=3, days="Mon-Fry")],
    )
    problems = validate_schedule(typo, 5)
    assert any("Mon-Fry" in p for p in problems), (
        f"the typo is never named, so the user cannot find it: {problems}"
    )
    assert any("0 Workers" in p or "0 workers" in p for p in problems), (
        "the consequence — the window contributes nothing — is not stated"
    )
