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

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rite_ai.config.models import ScheduleConfig, ScheduleWindow

MINUTES_PER_DAY = 1440

_HOURS_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def current_minute_of_day(tz_name: str, now: datetime | None = None) -> int | None:
    """The schedule's hours are LOCAL to `schedule.timezone` (D-48) — a
    window meant as "09:00-18:00 in Warsaw" is wrong if evaluated against
    the machine's own local clock or against UTC. Returns `None` when
    `tz_name` is empty or unrecognised, so a caller (the scheduler tick)
    can skip the boundary check entirely rather than silently evaluating
    against the wrong clock. `now`, if given, overrides the real current
    time — for tests; production callers omit it."""
    if not tz_name:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return None
    moment = now.astimezone(zone) if now is not None else datetime.now(zone)
    return moment.hour * 60 + moment.minute


@dataclass
class ScheduleError:
    message: str


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


def workers_at(schedule: ScheduleConfig, minute_of_day: int) -> int:
    """The Worker count in effect at a given minute-of-day. Hours not
    covered by any window default to 0 (§2.7's own fail-safe: a gap
    behaves like an intentional off-window, not an inherited or undefined
    count)."""
    for w in schedule.windows:
        parsed = _parse_hours(w.hours)
        if isinstance(parsed, ScheduleError):
            continue
        for start, end in _coverage(*parsed):
            if start <= minute_of_day < end:
                return w.workers
    return 0


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
