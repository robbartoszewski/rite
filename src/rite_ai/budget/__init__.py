"""Burn-rate measurement — reporting only, no adaptive control (SPEC §2.6,
D-38/D-39).

Reads real Claude Code transcripts (`~/.claude/projects/**/*.jsonl`) to
compute token throughput. No external API, no LLM turn anywhere in the
read path (verified against a real transcript on this machine, §2.6.1) —
every usage-bearing line carries a top-level `timestamp` and `sessionId`,
and a `message.usage` object with `input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, and `cache_read_input_tokens`.

**No adaptive control anywhere in this module, on purpose (D-38).** Nothing
here returns a Worker-count or concurrency recommendation, and nothing
here calls into `schedule/`, `pool/`, `workspace/`, or the sandbox
spawner. A caller (`rite status`) may surface exactly two things: the raw current
rate and the raw projected week-end total — nothing that reads as telling
the user what number to set.

**No percentage, and no early-exhaustion warning (SPEC §2.6.2, amended).**
Both were computed by dividing this module's machine-wide token total by
`budget.weekly_token_budget`, a key in one project's `.rite/config.yaml`.
Those are different scopes. The output was `used: 215126.9% of
weekly_token_budget` and a confident "projected to exhaust after 0.0
day(s)" — a warning that fires for every project on a machine that does
any real work, whatever its target.

`weekly_token_budget` remains valid configuration: it is the user's own
per-project allocation target, and the user manages quota through the
schedule (§2.7), which is the settled design. rite simply cannot measure
against it. Making it measurable would need per-project attribution, which
is not available: Anthropic's weekly quota is account-wide, a session can
be run against a project from any working directory, and per-worker
sessions have their own transcript directories. `format_burn_rate` names
the configured target and states plainly that it cannot be compared.

**The "week" here is a fixed weekly boundary (default: Monday 00:00 local
time, `budget.week_start_day`), not the account's actual billing-cycle
reset day** — rite has no way to learn that day, so this is a documented
default convention, not a discovered fact. Override `week_start_day` if
your plan resets on a different day.

**The headline total is dominated by cache reads, and the display must say
so.** On this machine's real corpus, `cache_read_input_tokens` was 98.4% of
the weekly total — re-reading already-cached context, which is a different
economic event from new input or generated output. A bare "1.8 billion
tokens" invites reading 98% of a cache-read figure as if it were
quota-relevant consumption. `format_burn_rate` therefore always splits the
total; see `BurnRateReport.cache_read_tokens`. rite cannot convert either
figure into a percentage of an Anthropic weekly quota, because nothing local
exposes that quota or the weighting it applies to cached reads — and, per
the note above, it cannot express them against a per-project target either.

**This is machine-wide, not project-scoped — found by actually running
`rite status` in a brand-new, empty project and seeing a multi-billion-
token burn rate.** `read_usage_since` scans every subdirectory under
`~/.claude/projects/`, i.e. every Claude Code session ever run on this
machine from any working directory, not only sessions run inside the
project asking for its own status. This matches how Anthropic's own
weekly quota works — it is account-wide, not per-repository — so the
number itself is the real one; what was missing was saying so. Every
caller (`rite status`, `rite budget`) must present this as the whole
machine's rate, never imply it belongs to one project alone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

HOURS_PER_WEEK = 7 * 24.0


def default_transcripts_dir() -> Path:
    """`~/.claude/projects/`, overridable via `RITE_CLAUDE_PROJECTS_DIR` —
    a function, not a module-level constant, so tests never scan this
    machine's real transcript history (the same reasoning as
    `rite_ai.dispatch.default_dispatch_dir`)."""
    override = os.environ.get("RITE_CLAUDE_PROJECTS_DIR")
    return Path(override) if override else Path.home() / ".claude" / "projects"


@dataclass
class UsageSample:
    timestamp: datetime
    session_id: str
    tokens: int  # sum of the four usage fields for this one API response
    message_id: str = ""  # `message.id` — the API response id; "" if absent
    cache_read_tokens: int = 0  # the `cache_read_input_tokens` part of `tokens`


def _parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _iter_usage_lines(path: Path):
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = data.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        ts = _parse_timestamp(data.get("timestamp", ""))
        if ts is None:
            continue
        tokens = sum(int(usage.get(f, 0) or 0) for f in USAGE_FIELDS)
        yield UsageSample(
            timestamp=ts,
            session_id=str(data.get("sessionId", "")),
            tokens=tokens,
            message_id=str(message.get("id") or ""),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        )


def read_usage_since(
    since: datetime, transcripts_dir: Path | None = None
) -> list[UsageSample]:
    """Every usage-bearing API response across every project's transcripts,
    at or after `since` — one sample per response, NOT one per line.
    `since` must be timezone-aware: transcript timestamps are UTC
    (`Z`-suffixed ISO-8601), and comparing aware to naive datetimes raises
    rather than silently misordering.

    Deduplication by `message.id` is the whole point of this function, and
    it is not defensive programming. Claude Code writes ONE JSONL record
    per content block, so a single assistant response containing fourteen
    `tool_use` blocks is written as fourteen records — each with its own
    `uuid` and `timestamp`, each repeating the SAME `message.id` and the
    SAME whole-response `usage` object. Summing lines therefore bills that
    response fourteen times. Measured on this machine's real corpus: 4,212
    of 5,924 responses in one week were multi-record, and the naive line
    sum came to 3.32 billion tokens against a true 1.84 billion — an 80%
    overcount that grew with how tool-heavy the week was, which is exactly
    the direction that makes a burn-rate reading untrustworthy.

    `message.id` is the API's own response id, so two records sharing one
    are always the same billed response; records without one (none observed,
    handled anyway) cannot be deduplicated and are kept as-is.
    """
    root = transcripts_dir if transcripts_dir is not None else default_transcripts_dir()
    samples: list[UsageSample] = []
    if not root.is_dir():
        return samples
    cutoff = since.timestamp()
    by_message: dict[str, UsageSample] = {}
    for path in root.rglob("*.jsonl"):
        if not _may_contain_entries_since(path, cutoff):
            continue
        for sample in _iter_usage_lines(path):
            if sample.timestamp < since:
                continue
            if not sample.message_id:
                samples.append(sample)  # nothing to deduplicate against
                continue
            prior = by_message.get(sample.message_id)
            if prior is None:
                by_message[sample.message_id] = sample
            else:
                # Same API response seen again. Keep the largest usage — a
                # record written mid-stream carries a partial `output_tokens`
                # that a later record for the same response supersedes — and
                # the earliest timestamp, which is when the response began.
                by_message[sample.message_id] = UsageSample(
                    timestamp=min(prior.timestamp, sample.timestamp),
                    session_id=prior.session_id,
                    tokens=max(prior.tokens, sample.tokens),
                    message_id=sample.message_id,
                    cache_read_tokens=max(
                        prior.cache_read_tokens, sample.cache_read_tokens
                    ),
                )
    samples.extend(by_message.values())
    return samples


def _may_contain_entries_since(path: Path, cutoff: float) -> bool:
    """Could this transcript hold an entry at or after `cutoff`?

    A file whose last modification predates the window cannot contain an
    entry inside it — entries are appended as they happen, so the file's
    mtime is at least as recent as its newest entry. Skipping those
    without opening them is the difference between reading the whole
    corpus and reading this week's part of it.

    Measured on this machine: 3,478 transcripts totalling 2,693 MB, of
    which 137 files and 217 MB were touched in the current week.
    `rite status` was parsing all 960,940 JSON lines of it on every run
    and spending 14 seconds there — a command a Dispatch session runs to
    orient itself, getting slower every week the machine is used, with
    nothing bounding it.

    A stat() that fails is treated as "read it": a file rite cannot
    measure is not a file rite may silently drop from a total.

    The one way this under-counts is a transcript restored with an old
    mtime preserved. That is rare, and the figure it feeds is an
    approximate machine-wide rate rather than a billing record — where
    the old behaviour's cost was paid on every single invocation.
    """
    try:
        return path.stat().st_mtime >= cutoff
    except OSError:
        return True


def current_week_start(
    now: datetime, tz_name: str, week_start_day: str = "monday"
) -> datetime | None:
    """The most recent occurrence of `week_start_day` at 00:00 local time
    in `tz_name`, at or before `now`. Returns `None` for an unrecognised
    timezone or weekday name — a caller skips the percentage/projection
    math entirely rather than guessing a boundary."""
    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return None
    day_name = week_start_day.strip().lower()
    if day_name not in _WEEKDAYS:
        return None
    target_weekday = _WEEKDAYS.index(day_name)  # Monday == 0, matching date.weekday()

    local_now = now.astimezone(zone)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_since_target = (midnight.weekday() - target_weekday) % 7
    return midnight - timedelta(days=days_since_target)


# How far back "recently" reaches. One hour is long enough that a single
# large response does not dominate it and short enough to reflect what is
# running now.
RECENT_WINDOW_HOURS = 1.0


@dataclass
class BurnRateReport:
    week_start: datetime
    tokens_this_week: int
    cache_read_tokens: int
    hours_elapsed: float
    tokens_per_hour: float
    projected_week_end_tokens: float
    weekly_token_budget: int | None
    # Tokens per hour over `RECENT_WINDOW_HOURS`, or None when the week is
    # younger than that window and the figure would be a fraction of an
    # hour extrapolated. Reported ALONGSIDE the average, never instead of
    # it — the projection is built on the average, and a reader has to be
    # able to see both numbers to understand why they differ.
    recent_tokens_per_hour: float | None = None
    # No pct_used / pct_projected / early_exhaustion_warning. See
    # `format_burn_rate` — the numerator and the denominator are different
    # scopes, so the ratio was never a quantity.


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "0.0%"


def format_burn_rate(report: BurnRateReport) -> list[str]:
    """The report's body lines, shared by `rite budget` and `rite status`.

    Both surfaces render the same numbers and used to do it with two
    hand-copied f-string blocks — the shape that let `rite status` drift
    into a duplicate of a module in the first place. Callers add their own
    heading and indentation; only the body lives here."""
    cache_read = report.cache_read_tokens
    fresh = report.tokens_this_week - cache_read
    lines = []
    if report.recent_tokens_per_hour is not None:
        lines.append(
            f"rate over the last hour: {report.recent_tokens_per_hour:,.0f} "
            "tokens/hour (all types)"
        )
    lines += [
        f"average since {report.week_start:%a %d %b %H:%M %Z}: "
        f"{report.tokens_per_hour:,.0f} tokens/hour (all types)",
        f"used this week: {report.tokens_this_week:,} tokens, of which",
        f"  cache reads: {cache_read:,} ({_pct(cache_read, report.tokens_this_week)})"
        " — re-reading cached context",
        f"  new tokens:  {fresh:,} ({_pct(fresh, report.tokens_this_week)})"
        " — input + output + cache writes",
        f"projected week-end total: {report.projected_week_end_tokens:,.0f} tokens",
    ]
    # No percentage of `weekly_token_budget`, and no early-exhaustion
    # warning derived from one.
    #
    # The figures above are machine-wide: `read_usage_since` scans every
    # transcript under `~/.claude/projects/`, i.e. every Claude Code session
    # run on this machine from any directory. `weekly_token_budget` is a key
    # in ONE project's `.rite/config.yaml`. Dividing the first by the second
    # is not a small inaccuracy, it is a category error, and it produced
    # readings like `used: 215126.9% of weekly_token_budget` — 2.15 billion
    # machine-wide tokens over a 1,000,000 per-project target — followed by
    # a confident `projected to exhaust after 0.0 day(s)`. Two projects with
    # different targets got contradictory readings off one shared number,
    # and a project that set no target got no reading at all.
    #
    # Attribution would need machinery that does not exist here and is not
    # worth building: Anthropic's weekly quota is account-wide, a session
    # can be run against a project from any working directory, and per-worker
    # sessions live in their own subdirectories. The measurable, honest thing
    # is the machine-wide figure, which is what the lines above already are.
    # A missing number is fine; a wrong one that fires a false exhaustion
    # warning is not.
    lines.append(
        "no percentage: usage is not attributable to one project, so these "
        "machine-wide figures cannot be expressed against a per-project target"
    )
    if report.weekly_token_budget:
        # Name it rather than ignore it. A configured key that silently does
        # nothing is its own defect — the user is owed the reason.
        lines.append(
            f"  weekly_token_budget is set to {report.weekly_token_budget:,} "
            "for this project — rite records it but cannot measure against it"
        )
    return lines


_MIN_HOURS_ELAPSED = 1 / 60  # one minute — avoids a divide-by-zero right at week start


def compute_burn_rate(
    samples: list[UsageSample],
    week_start: datetime,
    now: datetime,
    weekly_token_budget: int | None,
) -> BurnRateReport:
    """Reporting only — see the module docstring. `samples` should
    already be filtered to `timestamp >= week_start` (`read_usage_since`
    does this); this function does not re-filter, so a caller passing an
    unfiltered list will overcount."""
    tokens_this_week = sum(s.tokens for s in samples)
    cache_read_tokens = sum(s.cache_read_tokens for s in samples)

    # The week-to-date AVERAGE was the only rate reported, and it was
    # labelled "current rate". Measured on this machine mid-session: the
    # line read 32,431,427 tokens/hour while the preceding hour had
    # actually run at 448,977,101 — fourteen times higher. The average is
    # the right basis for the week-end projection below and the wrong
    # answer to "what is happening now", which is what a person reading a
    # line called "current" is asking.
    recent_tokens_per_hour: float | None = None
    if (now - week_start).total_seconds() / 3600 >= RECENT_WINDOW_HOURS:
        cutoff = now - timedelta(hours=RECENT_WINDOW_HOURS)
        recent = sum(s.tokens for s in samples if s.timestamp >= cutoff)
        recent_tokens_per_hour = recent / RECENT_WINDOW_HOURS
    hours_elapsed = max((now - week_start).total_seconds() / 3600, _MIN_HOURS_ELAPSED)
    tokens_per_hour = tokens_this_week / hours_elapsed
    projected_week_end_tokens = tokens_per_hour * HOURS_PER_WEEK

    # `weekly_token_budget` is carried through to be NAMED by the report,
    # not divided into anything. It is one project's allocation target;
    # `tokens_this_week` is every session on this machine. See
    # `format_burn_rate`.
    return BurnRateReport(
        week_start=week_start,
        tokens_this_week=tokens_this_week,
        cache_read_tokens=cache_read_tokens,
        recent_tokens_per_hour=recent_tokens_per_hour,
        hours_elapsed=hours_elapsed,
        tokens_per_hour=tokens_per_hour,
        projected_week_end_tokens=projected_week_end_tokens,
        weekly_token_budget=weekly_token_budget,
    )
