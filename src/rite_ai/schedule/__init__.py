"""Worker scheduling (SPEC §2.7, D-44–D-48).

A user-defined schedule per project — which hours, how many active Workers
— is the authoritative concurrency control (replacing an earlier, rejected
adaptive burn-rate controller, D-38). This module owns the parsing rules
and the upsert semantics; it does NOT decide anything on its own — rite
never computes the schedule, only validates and applies the one the user
set.

Internally, every window is normalised to non-wrapping minute-of-day
ranges `[start, end)` on a 0..1440 clock, specifically so midnight
wraparound ("18:00-09:00") and the upsert's split/trim logic are both
plain interval arithmetic rather than special-cased string handling. A
window is re-serialised back to an "HH:MM-HH:MM" string per non-wrapping
piece — `upsert_window` may turn one wrapping window into two stored
entries; this is a normalisation, not a semantic change, and the two
entries together cover exactly what the one did.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rite_ai.config.models import CheckinsConfig, ScheduleConfig, ScheduleWindow

MINUTES_PER_DAY = 1440

_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


@dataclass
class ScheduleError:
    message: str


_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAYS_RE = re.compile(r"^[A-Za-z]{3}(-[A-Za-z]{3})?$")
ALL_DAYS = frozenset(range(7))


def parse_days(days: str) -> frozenset[int] | ScheduleError:
    """Which weekdays a window covers, as Monday=0 .. Sunday=6.

    Empty means EVERY day — the meaning every window had before this field
    existed, so an existing schedule is unchanged by the field's arrival.

    A range wraps ("Fri-Mon" is Fri, Sat, Sun, Mon), matching `hours`
    wrapping at midnight: a week is a cycle, and refusing to wrap would
    make a user write two entries for one idea.
    """
    text = days.strip()
    if not text:
        return ALL_DAYS
    out: set[int] = set()
    for piece in text.split(","):
        piece = piece.strip()
        if not _DAYS_RE.match(piece):
            return ScheduleError(
                f"invalid days: {piece!r} (expected e.g. Mon, Mon-Fri, Sat,Sun)"
            )
        if "-" in piece:
            a, b = piece.split("-", 1)
            if a.lower() not in _DAY_NAMES or b.lower() not in _DAY_NAMES:
                return ScheduleError(f"invalid days: {piece!r} — unknown day name")
            start, end = _DAY_NAMES.index(a.lower()), _DAY_NAMES.index(b.lower())
            # Wraps, so a week is a cycle rather than a line.
            day = start
            out.add(day)
            while day != end:
                day = (day + 1) % 7
                out.add(day)
        else:
            if piece.lower() not in _DAY_NAMES:
                return ScheduleError(f"invalid days: {piece!r} — unknown day name")
            out.add(_DAY_NAMES.index(piece.lower()))
    return frozenset(out)


def machine_zone_name() -> str:
    """The machine's IANA zone name, as well as it can be established.

    There is no stdlib call for this — `zoneinfo` reads zones, it does not
    name the local one — so this reads the two places the answer actually
    lives and falls back to the abbreviation. The name is REPORTED rather
    than used for arithmetic: the arithmetic uses `astimezone()`, which is
    correct whatever this returns.

    ⚠ A container or CI runner with no zone configured resolves to UTC.
    That is the case this function exists to make visible: an operator in
    Warsaw who writes 09:00-17:00 gets a fleet running two hours off, with
    nothing saying so, and the shift is invisible precisely because every
    individual number looks right.
    """
    env = os.environ.get("TZ", "").strip()
    if env:
        return env
    try:
        parts = Path("/etc/localtime").resolve().parts
        if "zoneinfo" in parts:
            return "/".join(parts[parts.index("zoneinfo") + 1 :])
    except OSError:
        pass
    return datetime.now().astimezone().tzname() or "UTC"


@dataclass(frozen=True)
class ResolvedZone:
    """Which clock the schedule is being read against, and where it came
    from — the second half being the point, since a wrong zone is silent."""

    name: str
    machine_local: bool
    rejected: str = ""
    """What `schedule.timezone` asked for, when it could not be resolved.

    ⚠ Without this, a TYPO and an UNSET field were the same answer.
    Measured: `Europe/Lodnon` and `""` both produced "schedule in
    Europe/Warsaw (machine local)", byte for byte. The user who mistyped
    their own timezone was told the schedule was running normally, on a
    clock they did not choose.

    `resolve_zone` relaxed D-48, which used to FAIL when the field was
    absent, and its docstring says the loudness that bought "has to be
    bought back, which is what `describe()` is for". It bought it back for
    the default and not for the rejection, which is the half that is wrong
    rather than merely unconfigured."""

    def describe(self) -> str:
        if self.rejected:
            return (
                f"schedule in {self.name} (machine local — "
                f"schedule.timezone {self.rejected!r} is not a known "
                "timezone and was ignored)"
            )
        where = "machine local" if self.machine_local else "from config"
        return f"schedule in {self.name} ({where})"


def resolve_zone(tz_name: str) -> ResolvedZone:
    """`schedule.timezone` when set, otherwise the machine's own clock.

    Machine-local is the default because a schedule expresses human working
    hours: "nine to five" means the operator's day, and on a one-machine
    project requiring them to name their own timezone is ceremony.

    ⚠ This RELAXES D-48, which required the field once any window existed.
    D-48 bought loudness — a machine that had not been configured failed
    rather than guessed — and the replacement has to buy it back, which is
    what `describe()` is for and why `rite start` prints it. A default that
    is never stated is the same silent-wrong-clock D-48 was written against.
    """
    text = tz_name.strip()
    if text:
        try:
            ZoneInfo(text)
        except (ZoneInfoNotFoundError, ValueError):
            # Carry what was asked for: falling back silently is the
            # silent-wrong-clock D-48 was written against.
            return ResolvedZone(machine_zone_name(), machine_local=True, rejected=text)
        return ResolvedZone(text, machine_local=False)
    return ResolvedZone(machine_zone_name(), machine_local=True)


@dataclass(frozen=True)
class Moment:
    """Where the clock is, in the terms the schedule is written in."""

    minute_of_day: int
    weekday: int  # Monday=0 .. Sunday=6
    zone: ResolvedZone


def current_moment(tz_name: str, now: datetime | None = None) -> Moment:
    """The minute and weekday a schedule is evaluated against.

    Unlike `current_minute_of_day`, this never returns None: an unset or
    unrecognised timezone resolves to the machine's own clock rather than
    to "skip the check". Skipping was defensible while the field was
    mandatory; once it has a default, a caller that skips is a caller that
    ignores the schedule.
    """
    zone = resolve_zone(tz_name)
    try:
        info = ZoneInfo(zone.name)
    except (ZoneInfoNotFoundError, ValueError):
        info = None
    if info is not None:
        moment = now.astimezone(info) if now is not None else datetime.now(info)
    else:
        moment = now.astimezone() if now is not None else datetime.now().astimezone()
    return Moment(moment.hour * 60 + moment.minute, moment.weekday(), zone)


def _parse_hours(hours: str) -> tuple[int, int] | ScheduleError:
    m = _HOURS_RE.match(hours.strip())
    if not m:
        return ScheduleError(f"invalid hours range: {hours!r} (expected HH:MM-HH:MM)")
    sh, sm, eh, em = (int(g) for g in m.groups())
    start = sh * 60 + sm
    end = eh * 60 + em
    if start >= MINUTES_PER_DAY or end > MINUTES_PER_DAY:
        return ScheduleError(f"hour out of range in {hours!r}")
    return start, end


def _format_range(start: int, end: int) -> str:
    def fmt(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    return f"{fmt(start)}-{fmt(end)}"


def _coverage(start: int, end: int) -> list[tuple[int, int]]:
    """Decompose one possibly-wrapping range into non-wrapping [start, end)
    pieces. `"00:00-24:00"` (start=0, end=1440) is the "all day" case and
    already non-wrapping; end == start with a nonzero start denotes a
    zero-length range and is dropped."""
    if end == MINUTES_PER_DAY:
        return [(start, end)] if start < end else []
    if start < end:
        return [(start, end)]
    if start > end:
        if end > 0:
            return [(start, MINUTES_PER_DAY), (0, end)]
        return [(start, MINUTES_PER_DAY)]
    return []  # start == end, not the all-day case — zero-length, nothing


def _subtract(
    existing: tuple[int, int], cuts: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Subtract the union of `cuts` from one `existing` range, returning
    the leftover piece(s) (0, 1, or 2)."""
    pieces = [existing]
    for cut_start, cut_end in cuts:
        next_pieces: list[tuple[int, int]] = []
        for p_start, p_end in pieces:
            if cut_end <= p_start or cut_start >= p_end:
                next_pieces.append((p_start, p_end))  # no overlap
                continue
            if cut_start > p_start:
                next_pieces.append((p_start, cut_start))
            if cut_end < p_end:
                next_pieces.append((cut_end, p_end))
        pieces = next_pieces
    return pieces


def upsert_window(
    windows: list[ScheduleWindow], hours: str, workers: int
) -> list[ScheduleWindow] | ScheduleError:
    """Set `hours` to `workers`, splitting or trimming any existing window
    it overlaps, leaving every non-overlapping window byte-for-byte
    unchanged (SPEC §2.7's own example: two independent calls building the
    default two-window schedule, each untouched by the other)."""
    new_range = _parse_hours(hours)
    if isinstance(new_range, ScheduleError):
        return new_range
    new_start, new_end = new_range
    new_pieces = _coverage(new_start, new_end)

    result: list[tuple[int, int, int]] = []  # (start, end, workers)
    for w in windows:
        existing_range = _parse_hours(w.hours)
        if isinstance(existing_range, ScheduleError):
            continue  # a malformed pre-existing entry — drop it silently
        for piece in _coverage(*existing_range):
            for leftover_start, leftover_end in _subtract(piece, new_pieces):
                result.append((leftover_start, leftover_end, w.workers))

    for piece_start, piece_end in new_pieces:
        result.append((piece_start, piece_end, workers))

    result.sort(key=lambda r: r[0])
    return [ScheduleWindow(hours=_format_range(s, e), workers=w) for s, e, w in result]


def workers_at(
    schedule: ScheduleConfig, minute_of_day: int, weekday: int | None = None
) -> int:
    """The Worker count in effect at a given minute and weekday.

    **Time not covered by any window is 0**, and that is a decision rather
    than a fallthrough: a gap behaves as an intentional off-window, not as
    an inherited count and not as the flat `max_concurrent_workers`. It is
    also the value most projects hit first and never configure, which is
    why it is stated in the config documentation and not only here.

    `weekday` is Monday=0 .. Sunday=6. **None means "ignore the day
    dimension"**, which is what every caller meant before the dimension
    existed and what a window with no `days` means anyway — but a caller
    that omits it on a schedule that USES `days` silently gets the wrong
    answer, so callers evaluating a real moment pass it. `current_moment`
    returns both together for exactly that reason.
    """
    for w in schedule.windows:
        if _covers(w.hours, w.days, minute_of_day, weekday):
            return w.workers
    return 0


def _covers(hours: str, days: str, minute_of_day: int, weekday: int | None) -> bool:
    """Does a window with these `hours` and `days` cover this minute?

    One predicate for schedule windows AND check-in windows, so the two can
    never read the same `"Mon-Fri"`, `"23:00-01:00"` differently. A window
    that will not parse covers nothing — the caller's validator is what says
    so out loud."""
    parsed = _parse_hours(hours)
    if isinstance(parsed, ScheduleError):
        return False
    if weekday is not None:
        parsed_days = parse_days(days)
        if isinstance(parsed_days, ScheduleError) or weekday not in parsed_days:
            return False
    return any(start <= minute_of_day < end for start, end in _coverage(*parsed))


def check_worker_cap(count: int, max_concurrent_workers: int) -> str | None:
    """The `count > max_concurrent_workers` check §2.5.9 and §2.7.5 both
    require, implemented once and called from both places (P1.14's
    scope note): here, at schedule-design time, and from
    `rite_ai.sandbox.start_worker`, at actual spawn time. Refused, never
    silently clamped, in both callers."""
    if count > max_concurrent_workers:
        return (
            f"{count} workers, exceeding sandbox.max_concurrent_workers "
            f"({max_concurrent_workers}) — refused, not clamped"
        )
    return None


def validate_schedule(
    schedule: ScheduleConfig, max_concurrent_workers: int
) -> list[str]:
    """Returns problems, empty if none. Two checks: full 24h coverage (a
    gap is handled safely — defaults to 0 — but is still worth a warning,
    per §9.8/§2.7's "rite doctor validates full 24-hour coverage and warns
    on gaps"), and no window's `workers` exceeding the sandbox cap (§2.7.5:
    refused, never silently clamped)."""
    problems: list[str] = []

    if schedule.windows and not schedule.timezone:
        problems.append("schedule.timezone is required once any window exists (D-48)")

    # A timezone that was SET and could not be resolved is a different
    # fault from one that was never set, and it is the dangerous one: the
    # schedule runs on a clock the operator did not choose and nothing
    # else says so.
    zone = resolve_zone(schedule.timezone)
    if zone.rejected:
        problems.append(
            f"schedule.timezone {zone.rejected!r} is not a known timezone — "
            f"the schedule is being read against {zone.name}, this machine's "
            "own clock. Use an IANA name such as 'Europe/Warsaw'."
        )

    # ⚠ A MALFORMED `days:` SILENTLY MEANS ZERO WORKERS.
    #
    # `workers_at` skips a window whose `days` will not parse (`continue`),
    # and time no window covers is 0 — so `days: "Mon-Fry"` does not fail,
    # it quietly removes the window. Measured: a schedule configured for 3
    # Workers on weekdays produced 0 at Tuesday 10:00, and the only
    # complaint was a generic "960 minute(s) not covered by any window",
    # which names neither the window nor the typo.
    #
    # Skipping is the right RUNTIME behaviour — refusing to parse a day
    # must not start Workers outside the hours somebody meant — but it has
    # to be said out loud somewhere, and this is what `rite doctor` shows.
    for w in schedule.windows:
        days = parse_days(w.days)
        if isinstance(days, ScheduleError):
            problems.append(
                f"{days.message} — the window {w.hours!r} is ignored "
                "entirely, so it contributes 0 Workers rather than the "
                f"{w.workers} it names"
            )

    covered = [False] * MINUTES_PER_DAY
    for w in schedule.windows:
        parsed = _parse_hours(w.hours)
        if isinstance(parsed, ScheduleError):
            problems.append(parsed.message)
            continue
        cap_problem = check_worker_cap(w.workers, max_concurrent_workers)
        if cap_problem is not None:
            problems.append(f"window {w.hours} requests {cap_problem} (§2.7.5)")
        for start, end in _coverage(*parsed):
            for minute in range(start, end):
                covered[minute] = True

    if schedule.windows and not all(covered):
        gap_minutes = sum(1 for c in covered if not c)
        problems.append(
            f"schedule has {gap_minutes} minute(s) not covered by any window "
            "— defaults to 0 workers there, which may not be intended"
        )

    return problems


def next_open(schedule: ScheduleConfig, moment: Moment, horizon_days: int = 8) -> str:
    """When the schedule next allows at least one Worker, as "Mon 09:00".

    Searched forward a minute at a time over a bounded horizon rather than
    solved analytically: windows wrap at midnight AND at the week, may
    overlap, and the first matching window wins — reproducing that ordering
    in closed form is where an off-by-one would live, and this runs in
    milliseconds on a week.

    Returns "" when nothing in the horizon opens, which is a real answer:
    a schedule of all-zero windows never opens, and saying "next open:"
    with a guess would be worse than saying nothing.
    """
    minute, weekday = moment.minute_of_day, moment.weekday
    for _ in range(horizon_days * MINUTES_PER_DAY):
        minute += 1
        if minute >= MINUTES_PER_DAY:
            minute = 0
            weekday = (weekday + 1) % 7
        if workers_at(schedule, minute, weekday) > 0:
            day = _DAY_NAMES[weekday].capitalize()
            return f"{day} {minute // 60:02d}:{minute % 60:02d}"
    return ""


# --- check-in windows (plan § K1) ------------------------------------------
#
# The same grammar, parser and clock as the schedule above, deliberately. A
# check-in window is a schedule window without `workers`; it says when the
# User wants to be asked things, not how many Workers run.


def validate_checkins(checkins: CheckinsConfig) -> list[str]:
    """Problems with `checkins.windows`, in the schedule's own words.

    ⚠ Each message is the one `validate_schedule` gives for the same fault,
    because they come from the same `_parse_hours` / `parse_days`. What
    differs is only the consequence, which is stated: a malformed schedule
    window contributes 0 Workers, a malformed check-in window is a check-in
    that never happens — and a question deferred to it is never asked."""
    problems: list[str] = []
    for w in checkins.windows:
        parsed = _parse_hours(w.hours)
        if isinstance(parsed, ScheduleError):
            problems.append(
                f"{parsed.message} — this check-in window is ignored, so no "
                "check-in happens there"
            )
            continue
        days = parse_days(w.days)
        if isinstance(days, ScheduleError):
            problems.append(
                f"{days.message} — the check-in window {w.hours!r} is ignored "
                "entirely, so no check-in happens there"
            )
            continue
        if not _coverage(*parsed):
            problems.append(
                f"check-in window {w.hours!r} is zero-length, so no check-in "
                "happens there"
            )
    return problems


def in_checkin(checkins: CheckinsConfig, moment: Moment) -> bool:
    """Is a check-in window open at this moment?"""
    return any(
        _covers(w.hours, w.days, moment.minute_of_day, moment.weekday)
        for w in checkins.windows
    )


@dataclass(frozen=True)
class CheckinWhen:
    """Where the clock is relative to the check-ins, for a person to read.

    `open_now` with `until` is a window that is open; otherwise `starts` is
    the next opening ("Fri 20:00") and `minutes_away` how far off it is.
    `starts == ""` and not `open_now` means none opens within the horizon —
    no windows configured, or none that parse."""

    open_now: bool = False
    until: str = ""
    starts: str = ""
    minutes_away: int = 0

    def describe(self) -> str:
        if self.open_now:
            return f"open now, until {self.until}"
        if not self.starts:
            return ""
        hours, minutes = divmod(self.minutes_away, 60)
        away = f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"
        return f"next at {self.starts} (in {away})"


def _label(minute: int, weekday: int) -> str:
    return f"{_DAY_NAMES[weekday].capitalize()} {minute // 60:02d}:{minute % 60:02d}"


def next_checkin(
    checkins: CheckinsConfig, moment: Moment, horizon_days: int = 8
) -> CheckinWhen:
    """When the next check-in opens, or when the open one closes.

    Searched a minute at a time, like `next_open` and for its reason: windows
    wrap at midnight and at the week, and a closed form is where an
    off-by-one would live. A window that wraps midnight reports its end on
    the following day ("Sat 00:30"), since that is when it closes."""
    minute, weekday = moment.minute_of_day, moment.weekday
    if in_checkin(checkins, moment):
        for _ in range(horizon_days * MINUTES_PER_DAY):
            minute += 1
            if minute >= MINUTES_PER_DAY:
                minute, weekday = 0, (weekday + 1) % 7
            if not in_checkin(checkins, Moment(minute, weekday, moment.zone)):
                return CheckinWhen(open_now=True, until=_label(minute, weekday))
        return CheckinWhen(open_now=True, until="(never closes)")
    for step in range(1, horizon_days * MINUTES_PER_DAY + 1):
        minute += 1
        if minute >= MINUTES_PER_DAY:
            minute, weekday = 0, (weekday + 1) % 7
        if in_checkin(checkins, Moment(minute, weekday, moment.zone)):
            return CheckinWhen(starts=_label(minute, weekday), minutes_away=step)
    return CheckinWhen()


def minutes_open(checkins: CheckinsConfig, moment: Moment) -> int | None:
    """How many whole minutes the open check-in window has been open, or
    None when none is open.

    Bounded at a day: a window configured round the clock would otherwise
    never have "opened", and its check-in would happen once, ever. A day's
    bound gives it one a day."""
    if not in_checkin(checkins, moment):
        return None
    minute, weekday = moment.minute_of_day, moment.weekday
    for back in range(MINUTES_PER_DAY):
        minute -= 1
        if minute < 0:
            minute, weekday = MINUTES_PER_DAY - 1, (weekday - 1) % 7
        if not in_checkin(checkins, Moment(minute, weekday, moment.zone)):
            return back
    return MINUTES_PER_DAY


def describe_checkins(checkins: CheckinsConfig, moment: Moment) -> str:
    """The one line `rite status` prints about check-ins.

    ⚠ **No windows is SAID, not implied.** A deferred question has nowhere to
    wait without one, so it is asked at once — and a User who expected their
    day to be quiet should learn why it was not from here, not from the
    interruption."""
    if not checkins.windows:
        return (
            "check-ins: none configured (checkins.windows) — a deferred "
            "question is asked at once"
        )
    when = next_checkin(checkins, moment)
    text = when.describe()
    if not text:
        return (
            "check-ins: none will open — every checkins.windows entry is "
            "malformed (`rite doctor` names them); a deferred question is "
            "asked at once"
        )
    return f"check-ins: {text} ({moment.zone.describe()})"


def current_minute_of_day(tz_name: str, now: datetime | None = None) -> int:
    """The minute of day the schedule is evaluated against.

    ⚠ THIS USED TO RETURN `None` for an empty or unrecognised timezone,
    while `current_moment` — added later, for the same question — fell back
    to the machine's clock. Both policies were live at once and which one
    applied depended on which function a caller happened to reach for.

    Measured consequence on the DOCUMENTED DEFAULT (no timezone set):
    `sandbox.start_worker` enforced the schedule against the machine clock
    while `coordination.distribution` refused to assign anything at all,
    reporting "the schedule's timezone '' could not be resolved". Two
    subsystems, opposite behaviour, one config.

    `resolve_zone` decided the policy — machine-local is the default,
    because a schedule expresses the operator's working hours — so this
    delegates rather than keeping a second opinion. A rejected timezone is
    reported through `ResolvedZone.rejected`, not by refusing to answer.
    """
    return current_moment(tz_name, now).minute_of_day
