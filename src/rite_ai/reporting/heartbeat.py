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


def read_heartbeat(root: Path, worker: str) -> HeartbeatRecord | None:
    path = root / ".rite" / "heartbeats" / f"{worker}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return HeartbeatRecord(
            worker=data.get("worker", worker),
            timestamp=data.get("timestamp", 0),
            ticket=data.get("ticket", ""),
            message=data.get("message", ""),
        )
    except (json.JSONDecodeError, KeyError):
        return None


def detect_stalls(
    root: Path,
    workers: list[str],
    threshold_seconds: float = 1800.0,
) -> list[StallReport]:
    now = time.time()
    stalls: list[StallReport] = []
    for w in workers:
        hb = read_heartbeat(root, w)
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
