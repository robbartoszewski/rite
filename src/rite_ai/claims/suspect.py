"""Claims whose holder looks dead — named, never released.

**Measured, not assumed.** A claim 9.3 days old, whose holder never wrote a
heartbeat and has no sandbox, is still held; `detect_stalls` reports
`seconds_silent=inf` about it; and a live worker asking for the same path is
refused. rite detects the death and does nothing about it — a working detector
whose only consumer is a human reading `rite status`.

For a project meant to run continuously that is the failure that stops
everything while looking fine: the run does not break, it narrows, silently,
until nothing can be claimed.

**This module only reports, and that is the whole fix.** Two review rounds
killed the automatic release:

- "the sandbox is gone" is not evidence of death. `"not found"` with
  `known=True` is the NORMAL state for any Worker not launched by
  `start_worker`, so a guard built on it fires on live, working Workers;
- and nothing writes a heartbeat on a Worker's behalf, so a Worker heads-down
  for thirty minutes is indistinguishable from a dead one by that signal
  alone.

A report is correct on inputs where a release would be catastrophic, and the
catastrophe is specific: two sessions editing one path, which is the single
thing the ledger exists to prevent. So rite says what it sees and hands the
decision to a person, with the command already written out.

**It walks the LEDGER, not the roster.** `rite status` asks `detect_stalls`
about `project.workers` — registered Workers only — and the motivating case is
a claim held by a name nobody registered. A report that cannot see the example
that motivated it is not a report.

**Minimum age, because `detect_stalls` has none on its never-beaten branch.**
A holder with no heartbeat file is appended unconditionally
(`reporting/heartbeat.py`), so a Worker that claimed a second ago and has not
sent its first beat reports as stalled immediately. Without an age gate this
line fires from day one on every healthy project, and a line people learn to
scroll past is how this whole class of defect survives.

**"Cannot be checked" is not "never beat".** An ABSENT heartbeat says the
holder never started; one that cannot be READ says nothing about the holder at
all, and calling a claim abandoned on that basis is a guess wearing evidence's
clothes. `read_heartbeat_status` carries that third answer (EXC-4), so an
unreadable heartbeat suspends judgement, names the file, and offers a remedy
that fixes the file rather than releasing somebody's path.

⚠ **One limit left.** A claim whose stored `timestamp` is missing or zero is
reset to "now" when the ledger is read (`Claim.__post_init__`), so a corrupted
timestamp reads as brand-new and is never suspected. That fails quiet rather
than loud — the safe direction — but it means this report cannot see a claim
whose age was lost.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

MIN_AGE_MULTIPLIER = 1.0
"""How many stall thresholds old a CLAIM must be before its holder's silence
counts. One, so the claim and the heartbeat are judged against the same
window — a second number here would be a second definition of "too long", and
the first time the two disagreed there would be no way to say which was
right."""


@dataclass(frozen=True)
class Suspect:
    """One claim whose holder has gone quiet, and what to do about it."""

    worker: str
    paths: tuple[str, ...]
    claim_age: float
    silent_for: float
    """Seconds since the holder's last heartbeat, or `inf` for never."""
    registered: bool
    """False when no Worker of this name is configured — the shape the
    motivating case had, and a hint that the claim outlived its project."""
    unreadable: str = ""
    """Why the holder's heartbeat could not be read, when it could not be.
    Set means this row is a report of UNCERTAINTY rather than of abandonment,
    and the two must not read alike."""

    @property
    def never_beat(self) -> bool:
        return self.silent_for == float("inf")

    def describe(self) -> str:
        if self.unreadable:
            # Uncertainty, and it must not read like abandonment. A heartbeat
            # that is ABSENT says the holder never started; one that cannot be
            # READ says nothing about the holder at all.
            return (
                f"{', '.join(self.paths)} held by {self.worker} for "
                f"{_age(self.claim_age)} — its heartbeat CANNOT BE READ "
                f"({self.unreadable}), so whether the holder is alive is "
                "unknown rather than decided"
            )
        silence = (
            "no heartbeat ever"
            if self.never_beat
            else f"silent {_age(self.silent_for)}"
        )
        unknown = "" if self.registered else ", and no such Worker is configured"
        return (
            f"{', '.join(self.paths)} held by {self.worker} for "
            f"{_age(self.claim_age)} — {silence}{unknown}"
        )

    @property
    def remedy(self) -> str:
        if self.unreadable:
            # Not a release. The problem is a file on this machine, and
            # releasing on the strength of a heartbeat nobody could read is
            # precisely the guess this module exists not to make.
            return f"fix or remove .rite/heartbeats/{self.worker}.json, then look again"
        return f'rite release --worker {self.worker} --force --reason "holder gone"'


def _age(seconds: float) -> str:
    """Days once it is days. `format_duration` tops out at hours, and "223h"
    is not a number anybody reads — the whole value of this line is that a
    person glances at it and sees that something has been wrong for over a
    week."""
    if seconds == float("inf"):
        return "ever"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.1f}d"


def suspect_claims(
    root: Path,
    *,
    registered: list[str] | None = None,
    threshold_seconds: float = 1800.0,
    now: float | None = None,
) -> list[Suspect]:
    """Every claim whose holder looks dead. Returns, never raises, never writes.

    An unreadable ledger returns nothing rather than guessing: this report
    exists to point at a specific claim, and it has none to point at.
    """
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.reporting.heartbeat import read_heartbeat_status

    now = time.time() if now is None else now
    path = root / ".rite" / "claims.json"
    if not path.is_file():
        return []
    try:
        claims = ClaimsLedger(path).list_claims()
    except Exception:  # noqa: BLE001 - an unreadable ledger is an answer
        return []

    known = set(registered or [])
    found: list[Suspect] = []
    for claim in claims:
        age = max(0.0, now - claim.timestamp)
        if age < threshold_seconds * MIN_AGE_MULTIPLIER:
            # Still inside the window a healthy Worker is allowed to be quiet
            # for. Reporting here would fire on every project on day one.
            continue
        beat = read_heartbeat_status(root, claim.worker)
        if not beat.known:
            # Reported, because a claim whose holder cannot be checked is
            # still one somebody may need to act on — but reported as
            # uncertainty, with a remedy that fixes the file rather than
            # releasing the path.
            found.append(
                Suspect(
                    worker=claim.worker,
                    paths=tuple(claim.paths),
                    claim_age=age,
                    silent_for=float("inf"),
                    registered=claim.worker in known,
                    unreadable=beat.detail or "reason unavailable",
                )
            )
            continue
        silent = (
            float("inf")
            if beat.record is None
            else max(0.0, now - beat.record.timestamp)
        )
        if silent <= threshold_seconds:
            continue
        found.append(
            Suspect(
                worker=claim.worker,
                paths=tuple(claim.paths),
                claim_age=age,
                silent_for=silent,
                registered=claim.worker in known,
            )
        )
    return sorted(found, key=lambda s: -s.claim_age)


def lines(suspects: list[Suspect]) -> list[str]:
    """What a human reads. The remedy is printed, not implied — the whole
    point is to turn an invisible permanent failure into a one-line fix."""
    out: list[str] = []
    for suspect in suspects:
        out.append(f"claims: {suspect.describe()}")
        out.append(f"claims:   → {suspect.remedy}")
    return out
