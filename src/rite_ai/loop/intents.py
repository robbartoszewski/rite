"""The few seconds where a dispatched session is invisible (L-3).

**This is deliberately not a ledger of running sessions.** The plan called for
"a dispatch record written before any spawn", and the Owner running the live
dogfood showed why that would be the wrong thing: it reports "both workers
free" and "neither is free" correctly, repeatedly, and keeps no record at all.
It observes — claims held, dirty checkouts, live heartbeats — and observation
cannot drift, because it reads the thing itself rather than a note about it.

A parallel ledger of who is running can drift, and drifts in the direction
that stops work: an entry for a session that ended holds capacity down until
somebody deletes a file. That is this plan's own falsifier, written before the
code.

**So what remains is the blind window, and only that.** `start_worker` returns
once yoloAI has been asked; the sandbox becomes visible to `yoloai ls` a moment
later, and the Worker's first `rite claim` is later still. In between,
observation says "free" about a Worker that a session is booting into — and a
120-second cycle dispatches it again. The window is seconds. The record covers
seconds.

**An intent is a question, not an answer.** Every cycle asks whether the thing
it intended actually happened:

- the Worker is observably busy now → the spawn worked, drop the intent;
- still invisible, and young → the window is open, hold it;
- still invisible, and old → nothing came of it. That is a PROBLEM, reported
  by name, never a quiet subtraction from capacity. A leaked intent that
  silently held a Worker back would be the drift this design refused.
- the process that wrote it is gone and nothing appeared → a crash between the
  record and the spawn, which is exactly what the record is for.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.state import write_atomic

BLIND_SECONDS = 180.0
"""How long a dispatch may stay invisible before it is treated as lost.

Generous on purpose: a sandbox that is slow to appear is ordinary, and
retiring an intent early re-dispatches a session that IS starting — the exact
double-spend the record exists to prevent. Being slow to notice a leak costs
one Worker's capacity for three minutes and says so out loud; being quick
costs a session.
"""


@dataclass
class Intent:
    """A dispatch this machine decided on, before anything could see it."""

    worker: str
    ticket: str
    timestamp: float
    pid: int = 0

    def age(self, now: float) -> float:
        return max(0.0, now - self.timestamp)


@dataclass
class Reconciliation:
    """What became of the intents, once observation had its say."""

    holding: list[Intent] = field(default_factory=list)
    """Young, still invisible. These Workers are NOT free."""
    confirmed: list[Intent] = field(default_factory=list)
    """Observation caught up; the record has done its job and is dropped."""
    lost: list[Intent] = field(default_factory=list)
    """Old and still invisible, or written by a process that is gone. A
    problem to report, never a silent subtraction."""
    at: float = 0.0
    """The clock this reconciliation used. Carried so `problems` reports an
    age against the same moment the decision was made rather than against
    whenever somebody happened to read it."""

    @property
    def problems(self) -> list[str]:
        when = self.at or time.time()
        return [
            f"a session was dispatched for {i.ticket} to {i.worker} "
            f"{int(i.age(when) // 60)}m ago and nothing ever appeared — "
            "either it failed to start, or it is running and rite cannot see "
            "it. Check `yoloai ls` before dispatching that Worker again"
            for i in self.lost
        ]


def _path(root: Path) -> Path:
    return root / ".rite" / "dispatch-intents.json"


def read_intents(root: Path) -> list[Intent]:
    """Every outstanding intent. Empty when the file is missing OR unreadable.

    Unreadable reads as empty here, and that is the unusual direction for this
    codebase — everywhere else an unreadable file refuses. The asymmetry is
    deliberate: this file only ever REMOVES capacity, so failing closed on it
    would stop a fleet over a corrupt scratch file, and the thing it protects
    against (a double dispatch inside a three-minute window) is bounded and
    visible. The claims ledger, which decides whether two Workers touch one
    path, refuses instead.
    """
    try:
        data = json.loads(_path(root).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    found: list[Intent] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        worker = str(entry.get("worker", ""))
        if not worker:
            continue
        found.append(
            Intent(
                worker=worker,
                ticket=str(entry.get("ticket", "")),
                timestamp=float(entry.get("timestamp", 0.0)),
                pid=int(entry.get("pid", 0) or 0),
            )
        )
    return found


def _write(root: Path, intents: list[Intent]) -> None:
    write_atomic(
        _path(root),
        json.dumps(
            [
                {
                    "worker": i.worker,
                    "ticket": i.ticket,
                    "timestamp": i.timestamp,
                    "pid": i.pid,
                }
                for i in intents
            ],
            indent=2,
        )
        + "\n",
    )


def record_intent(root: Path, worker: str, ticket: str, *, now: float | None = None):
    """Write BEFORE the spawn. Returns the intent.

    Before, not after, is the whole design: a crash between deciding and
    spawning leaves a record of a session nothing else knows about, and a
    crash between spawning and recording leaves a session nothing knows about
    at all. Only one of those is recoverable.
    """
    intent = Intent(
        worker=worker,
        ticket=ticket,
        timestamp=time.time() if now is None else now,
        pid=os.getpid(),
    )
    kept = [i for i in read_intents(root) if i.worker != worker]
    _write(root, [*kept, intent])
    return intent


def clear_intent(root: Path, worker: str) -> None:
    """Drop one, when the caller knows the spawn failed outright.

    A refused spawn is not a blind window — nothing is starting — and leaving
    the intent would hold a Worker back for three minutes over a `yoloai new`
    that returned an error immediately.
    """
    intents = read_intents(root)
    remaining = [i for i in intents if i.worker != worker]
    if len(remaining) != len(intents):
        _write(root, remaining)


def reconcile(
    root: Path,
    *,
    observably_busy: set[str],
    now: float | None = None,
    is_running=None,
) -> Reconciliation:
    """Ask, of each intent, whether the thing it intended happened.

    `observably_busy` is the answer from looking at the world — a claim, a
    dirty checkout, a live sandbox. When it says yes, the record has done its
    job and gets out of the way; observation is the better witness and this
    file must never outlive it.
    """
    now = time.time() if now is None else now
    if is_running is None:
        from rite_ai.scheduler.lock import process_is_running

        is_running = process_is_running

    result = Reconciliation(at=now)
    for intent in read_intents(root):
        if intent.worker in observably_busy:
            result.confirmed.append(intent)
        elif intent.age(now) > BLIND_SECONDS:
            result.lost.append(intent)
        elif intent.pid and not is_running(intent.pid):
            # The process that meant to spawn is gone and nothing appeared.
            # Exactly what the record exists to catch.
            result.lost.append(intent)
        else:
            result.holding.append(intent)

    if result.confirmed or result.lost:
        _write(root, result.holding)
    return result
