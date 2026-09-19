"""Heartbeat and stall detection (SPEC §9.8).

Workers write periodic heartbeat records. The Manager reads them and
detects stalls — a worker that has missed heartbeats gets surfaced to the
human rather than silently hanging.

Heartbeats are stored as JSON in `.rite/heartbeats/`. Each worker gets one
file, overwritten on each beat — there is no history, only "when did I last
hear from this worker".
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import write_atomic


@dataclass
class HeartbeatRecord:
    worker: str
    timestamp: float
    ticket: str = ""
    message: str = ""


@dataclass
class StallReport:
    worker: str
    last_seen: float
    seconds_silent: float
    ticket: str = ""
    known: bool = True
    """False when the heartbeat could not be READ, as opposed to never
    having been written. Both used to arrive as `seconds_silent=inf`, which
    both renderers print as "no heartbeat ever recorded" — a confident
    statement about a file nobody could open."""
    detail: str = ""


@dataclass
class HeartbeatStatus:
    """A worker's heartbeat, or why there isn't one — three states, not two.

    `read_heartbeat` answers `None` for a file that is absent, corrupt,
    malformed, or unreadable, and `detect_stalls` turns `None` into
    `seconds_silent=inf`: maximally stalled. So a heartbeat nobody can parse
    reports the worker as worse off than any real duration could, which is
    the opposite of what "I cannot tell" should cost — and it is the shape
    `worker_sandbox_status` already gets right with its own `known` flag.
    """

    record: HeartbeatRecord | None = None
    known: bool = True
    detail: str = ""

    @property
    def beat(self) -> bool:
        return self.record is not None


def write_heartbeat(
    root: Path,
    worker: str,
    ticket: str = "",
    message: str = "",
) -> None:
    hb_dir = root / ".rite" / "heartbeats"
    hb_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "worker": worker,
        "timestamp": time.time(),
        "ticket": ticket,
        "message": message,
    }
    write_atomic(hb_dir / f"{worker}.json", json.dumps(record) + "\n")


def read_heartbeat_status(root: Path, worker: str) -> HeartbeatStatus:
    """The worker's heartbeat, or WHY there is none.

    `OSError` was not caught at all: a heartbeat file that exists and cannot
    be opened — a permission, a bad mount, an I/O error — raised out of
    here, and this runs inside `rite status` and the scheduler tick, so the
    whole command died on one unreadable file. Caught, and reported as
    "cannot tell" rather than as silence.
    """
    path = root / ".rite" / "heartbeats" / f"{worker}.json"
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return HeartbeatStatus()  # never beat: known, and no record
    except OSError as e:
        return HeartbeatStatus(known=False, detail=f"{path.name} unreadable: {e}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return HeartbeatStatus(known=False, detail=f"{path.name} is not JSON: {e}")
    if not isinstance(data, dict):
        return HeartbeatStatus(
            known=False,
            detail=f"{path.name} holds {type(data).__name__}, not an object",
        )
    stamp = data.get("timestamp", 0)
    if not isinstance(stamp, int | float):
        return HeartbeatStatus(
            known=False, detail=f"{path.name} has a non-numeric timestamp"
        )
    return HeartbeatStatus(
        record=HeartbeatRecord(
            worker=data.get("worker", worker),
            timestamp=stamp,
            ticket=data.get("ticket", ""),
            message=data.get("message", ""),
        )
    )


def read_heartbeat(root: Path, worker: str) -> HeartbeatRecord | None:
    """The record, or None for anything else — the ORIGINAL contract, kept.

    Callers that only want the record keep working unchanged, and they
    inherit the `OSError` fix for free. Anything that needs to tell "never
    beat" from "cannot tell" asks `read_heartbeat_status`.
    """
    return read_heartbeat_status(root, worker).record


def _workers_holding_claims(root: Path) -> set[str] | None:
    """Who holds a claim, or None when the ledger cannot be read."""
    path = root / ".rite" / "claims.json"
    if not path.exists():
        return set()
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.state import CorruptStateError

    try:
        return {c.worker for c in ClaimsLedger(path).list_claims()}
    except CorruptStateError:
        return None


def not_started(root: Path, workers: list[str]) -> list[str]:
    """Workers with no heartbeat and no claims: registered, and nothing
    says a session has picked them up yet.

    `rite add worker` followed by `rite status` used to show the new worker
    STALLED, and `rite watchdog` exited 1 on it every cycle until a first
    heartbeat — broken, on minute one. A claim is evidence a session did
    start, so a worker holding one with no heartbeat is still a stall (it
    may be working without being told to beat). When the ledger cannot be
    read nothing counts as not started: a missed stall is worse than a
    false one.
    """
    holding = _workers_holding_claims(root)
    if holding is None:
        return []
    # `status.known and not status.beat`: a worker whose heartbeat cannot be
    # READ has not been shown to be unstarted, and treating it as unstarted
    # drops it from stall detection entirely — silence about the one worker
    # there is a question about.
    return [
        w
        for w in workers
        if w not in holding
        and (s := read_heartbeat_status(root, w)).known
        and not s.beat
    ]


def detect_stalls(
    root: Path,
    workers: list[str],
    threshold_seconds: float = 1800.0,
) -> list[StallReport]:
    now = time.time()
    stalls: list[StallReport] = []
    idle = set(not_started(root, workers))
    for w in workers:
        if w in idle:
            continue
        status = read_heartbeat_status(root, w)
        if not status.known:
            # "Cannot tell" is its own answer. It used to arrive here as
            # None and leave as `seconds_silent=inf`, which both renderers
            # print as "no heartbeat ever recorded" — the most alarming
            # thing they can say, asserted about a file nobody could read.
            stalls.append(
                StallReport(
                    worker=w,
                    last_seen=0,
                    seconds_silent=0,
                    known=False,
                    detail=status.detail,
                )
            )
            continue
        hb = status.record
        if hb is None:
            # `now` was used here previously — an absolute epoch timestamp
            # (~1.8 billion) misread as a duration, reporting every worker
            # that has simply never sent a first heartbeat as "stalled for
            # ~56 years." `float("inf")` makes "never sent one" a value
            # distinct from any real duration, for the caller to render
            # accordingly rather than print a nonsense number.
            stalls.append(
                StallReport(
                    worker=w,
                    last_seen=0,
                    seconds_silent=float("inf"),
                )
            )
            continue
        silent = now - hb.timestamp
        if silent > threshold_seconds:
            stalls.append(
                StallReport(
                    worker=w,
                    last_seen=hb.timestamp,
                    seconds_silent=silent,
                    ticket=hb.ticket,
                )
            )
    return stalls
