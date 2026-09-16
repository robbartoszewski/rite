"""How often a spec slice was not enough: the insufficiency rate.

A slice is the unit a ticket needs, its depth-1 references and the pinned hubs —
deliberately NOT everything it transitively depends on, because on rite's own
spec that is 70% of the document at the median. So a slice misses real
dependencies by design. Worse, a spec with few explicit cross-references has a
sparse graph and produces small slices that look like success while the
dependencies are real and simply unwritten. Neither failure is visible in the
slice. Both show up only when a Worker has to go and read the whole document,
and that is invisible unless it is recorded.

So every retrieval is recorded, every fallback is recorded, and the rate is
fallbacks over retrievals within a window. It is what tunes the depth: a rising
rate is the evidence for depth-2 or for pinning another hub, and without it the
depth is a guess defended by argument.

**No data is not good news.** No retrievals and no fallbacks is `NO_DATA`, never
a rate of 0%: a feature nobody used and a feature that always worked are
opposite readings, and only one of them justifies leaving the depth alone. A
fallback with no retrieval at all is its own status — a Worker skipped the slice
entirely. A line that cannot be read is counted and reported, never skipped, so
a damaged log cannot pass as a quiet one.

Runtime state, local to the machine that wrote it: `.rite/spec-telemetry.jsonl`.
Deliberately not under `.rite/spec/`, which holds the committed digest.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.state import locked

TELEMETRY_FILE = Path(".rite") / "spec-telemetry.jsonl"

RETRIEVAL = "retrieval"
FALLBACK = "fallback"

NO_DATA = "no-data"
MEASURED = "measured"
FALLBACKS_ONLY = "fallbacks-only"
UNREADABLE = "unreadable"

DEFAULT_WINDOW_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class Event:
    kind: str  # RETRIEVAL | FALLBACK
    unit: str
    at: float
    worker: str = ""
    slice_ratio: float | None = None  # retrievals only: slice lines / spec lines


def telemetry_path(root: Path) -> Path:
    return root / TELEMETRY_FILE


def _append(root: Path, event: Event) -> Event:
    record = {"kind": event.kind, "unit": event.unit, "at": event.at}
    if event.worker:
        record["worker"] = event.worker
    if event.slice_ratio is not None:
        record["slice_ratio"] = event.slice_ratio
    path = telemetry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One line per event, appended under the sidecar lock: several Workers
    # record at once, and a torn line would be counted as unreadable.
    with locked(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return event


def record_retrieval(
    root: Path,
    unit: str,
    *,
    worker: str = "",
    slice_ratio: float | None = None,
    now: float | None = None,
) -> Event:
    """A slice was retrieved for `unit`."""
    at = time.time() if now is None else now
    return _append(root, Event(RETRIEVAL, unit, at, worker, slice_ratio))


def record_fallback(
    root: Path, unit: str, *, worker: str = "", now: float | None = None
) -> Event:
    """The slice for `unit` was not enough and the whole spec was read."""
    at = time.time() if now is None else now
    return _append(root, Event(FALLBACK, unit, at, worker))


@dataclass
class EventLog:
    events: list[Event] = field(default_factory=list)
    unreadable_lines: int = 0
    error: str = ""  # the log exists and could not be read at all


def read_events(root: Path) -> EventLog:
    path = telemetry_path(root)
    if not path.exists():
        return EventLog()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return EventLog(error=f"{path}: {e}")
    log = EventLog()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            kind, unit, at = record["kind"], record["unit"], float(record["at"])
            if (
                kind not in (RETRIEVAL, FALLBACK)
                or not isinstance(unit, str)
                or not unit
            ):
                raise ValueError(kind)
            ratio = record.get("slice_ratio")
            log.events.append(
                Event(
                    kind,
                    unit,
                    at,
                    str(record.get("worker", "")),
                    float(ratio) if ratio is not None else None,
                )
            )
        except (ValueError, KeyError, TypeError):
            log.unreadable_lines += 1
    return log


@dataclass(frozen=True)
class Rate:
    status: str  # NO_DATA | MEASURED | FALLBACKS_ONLY | UNREADABLE
    retrievals: int
    fallbacks: int
    since: float
    until: float
    unreadable_lines: int = 0
    error: str = ""

    @property
    def value(self) -> float | None:
        """Fallbacks per retrieval, or None whenever that is not a real number."""
        if self.status != MEASURED:
            return None
        return self.fallbacks / self.retrievals

    def describe(self) -> str:
        days = round((self.until - self.since) / 86400, 1)
        span = f"the last {days:g} day{'s' if days != 1 else ''}"
        if self.status == UNREADABLE:
            text = (
                "insufficiency rate unknown — the telemetry log cannot be read "
                f"({self.error})"
            )
        elif self.status == NO_DATA:
            text = (
                f"no spec slices retrieved in {span} — no evidence either way, "
                "which is not the same as the slices being enough"
            )
        elif self.status == FALLBACKS_ONLY:
            text = (
                f"{self.fallbacks} fallback(s) to the whole spec and no slice "
                f"retrieved in {span} — the slices are being skipped, not measured"
            )
        else:
            text = (
                f"{self.fallbacks} of {self.retrievals} slice retrievals fell back "
                f"to the whole spec in {span} ({self.value:.1%})"
            )
        if self.unreadable_lines:
            text += (
                f"; {self.unreadable_lines} unreadable line(s) in the log "
                "were not counted"
            )
        return text


def insufficiency_rate(
    root: Path,
    *,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    now: float | None = None,
) -> Rate:
    until = time.time() if now is None else now
    since = until - window_seconds
    log = read_events(root)
    if log.error:
        return Rate(UNREADABLE, 0, 0, since, until, error=log.error)
    window = [e for e in log.events if since <= e.at <= until]
    retrievals = sum(1 for e in window if e.kind == RETRIEVAL)
    fallbacks = sum(1 for e in window if e.kind == FALLBACK)
    if retrievals:
        status = MEASURED
    elif fallbacks:
        status = FALLBACKS_ONLY
    else:
        status = NO_DATA
    return Rate(status, retrievals, fallbacks, since, until, log.unreadable_lines)
