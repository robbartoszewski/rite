"""Outbox — local queue for handover and status messages (SPEC §9.10, D-15).

When `rite stop` runs offline (JIRA unreachable), handover data is queued
here and flushed on next contact. The outbox is also the mechanism the
heartbeat timeout uses to record what happened when a session ended.

Messages are JSON files named by timestamp. Each message has a `kind`
(handover, stall, blocker) and a `payload` dict. The outbox is drained
by `flush_outbox`, which calls a callback for each message and deletes
it on success.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rite_ai.label import project_name
from rite_ai.state import write_atomic


@dataclass
class OutboxMessage:
    kind: str
    payload: dict
    timestamp: float
    path: Path
    project: str = ""
    """Which project queued this. Recorded at enqueue time rather than
    inferred from the file's location when it is read, because the whole
    point of a queued message is that it is delivered somewhere else —
    a ticket comment on a shared board, a report read hours later — where
    the directory it came from is no longer visible."""


def _outbox_dir(root: Path) -> Path:
    return root / ".rite" / "outbox"


# Distinguishes two messages queued in the same millisecond. The pid
# separates processes; the counter separates enqueues within one. See
# `enqueue` for what a collision cost.
_SEQUENCE = itertools.count()


def enqueue(
    root: Path,
    kind: str,
    payload: dict,
) -> Path:
    """Queue one message for later delivery, under a name no other
    enqueue can produce.

    The name used to be `{milliseconds}_{kind}.json`, and two enqueues
    landing in the same millisecond wrote the same path — so the second
    `write_atomic` REPLACED the first message rather than adding to the
    queue. Measured, twelve concurrent enqueues of one kind, fifteen
    rounds: every round lost messages and the worst kept five of twelve.
    A millisecond is a long time for one process and no time at all for
    several, which is the case this queue exists for — the outbox is what
    makes "`stop` must succeed offline" (§9.10) true, and a lost message
    is a handover that is never delivered and never retried, because
    nothing remains to retry from.

    The millisecond stays FIRST in the name so `sorted(glob(...))` still
    delivers in enqueue order; the discriminators only break ties.
    """
    out_dir = _outbox_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    name = f"{int(ts * 1000)}_{os.getpid()}_{next(_SEQUENCE)}_{kind}.json"
    path = out_dir / name
    data = {
        "kind": kind,
        "payload": payload,
        "timestamp": ts,
        "project": project_name(root),
    }
    write_atomic(path, json.dumps(data) + "\n")
    return path


def list_pending(root: Path) -> list[OutboxMessage]:
    """What is pending NOW, in enqueue order.

    ⚠ **A file that vanishes between the listing and the read was CONSUMED,
    and is skipped as no longer pending, not raised.** Established from the
    code, not assumed: exactly two things remove a message from this
    directory, and both are consumers. `flush_outbox` removes one it has
    delivered, and the scheduler's `_reconcile_stall_blockers` removes a
    blocker it has retracted. Both already remove with `missing_ok=True`,
    treating the other's removal as expected. Names are unique per enqueue,
    so nothing replaces a message by renaming over it, and `write_atomic`'s
    temporary files end in `.tmp` and never match the glob. So "gone" here
    can only mean "another tick took it", and whether that was a delivery
    or a retraction was decided by the remover. Raising did not keep the
    message, which was already gone; it crashed the tick that was listing,
    and every message after it in the listing went unreported (CI: four
    occurrences in `test_blast_radius_concurrent`, on Python 3.11 and 3.12).

    ⚠ **This does not make concurrent consumers safe.** Ticks no longer
    overlap (`scheduler/lock.py` is an `flock` since 0.7.0), but the tick
    lock does not guard `flush_outbox`, so two concurrent flushes (two
    `rite start`s) could still deliver one message twice. This only stops a
    consumed message from being reported as a failure of the reader.
    """
    out_dir = _outbox_dir(root)
    if not out_dir.is_dir():
        return []
    messages: list[OutboxMessage] = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            raw = path.read_text()
        except FileNotFoundError:
            continue  # consumed by another tick since the listing (above)
        try:
            data = json.loads(raw)
            messages.append(
                OutboxMessage(
                    kind=data.get("kind", ""),
                    payload=data.get("payload", {}),
                    timestamp=data.get("timestamp", 0),
                    path=path,
                    # Messages queued before this field existed still have
                    # a project — it is the one whose outbox they are in.
                    project=data.get("project") or project_name(root),
                )
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return messages


def flush_outbox(
    root: Path,
    deliver: Callable[[OutboxMessage], bool],
) -> int:
    """Deliver pending messages. `deliver` returns True on success
    (message deleted) or False on failure (message kept for retry).
    Returns count of successfully delivered messages."""
    delivered = 0
    for msg in list_pending(root):
        if deliver(msg):
            msg.path.unlink(missing_ok=True)
            delivered += 1
    return delivered
