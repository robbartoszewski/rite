"""A Worker's "I am done, here is the work", written where the host reads it.

**What happened (dogfood, 2026-10-03).** A sandboxed Worker finished its
ticket and did what its own instructions tell it to: "Tell your Manager you
are free, in the same message that reports the work." The command for that
is `rite message <manager>`, and from inside the sandbox it fails —
`PermissionError: Operation not permitted` — because a Manager's mailbox
lives outside the project (`managers.manager_dir`, DF3) and a Worker's
sandbox does not grant it. That refusal is correct and stays: a message in a
Manager's inbox is delivered as the Owner's instruction, so a Worker must
not write one (`enclosure._manager_separation`).

So the Worker fell back to writing a file nobody reads
(`rw/files/<TICKET>-handback.md`), the Manager never learned it had
finished, and the watchdog — seeing a Worker that had stopped beating —
reported it STALLED. The Manager read that and restarted a Worker that was
already done. The Worker's own instructions had named this exact failure
("A Worker that finishes and falls silent is indistinguishable from one that
died mid-ticket") without rite having a mechanism that told the two apart.

**What this module is.** The record that tells them apart:
`.rite/handback/<worker>.json`, written by `rite done` and read on the host
by the supervisor (`managers.worker_handbacks`) and the watchdog.

⚠ **NO SANDBOX PROFILE CHANGES, NO NEW GRANT, NO LAUNCH-ENV CHANGE.** This
is the directory tree `.rite/heartbeats/` already lives in, mounted `rw`
into a Worker's sandbox by `sandbox.start_worker` — so the transport is the
one a Worker's heartbeat already proves works from inside. The alternative
considered was yoloAI's own file-exchange directory, the way a Worker's
question travels (`sandbox.questions`). It was rejected for one decisive
reason: that directory dies with the sandbox, and freeing a Worker's slot
is what destroys the sandbox. A handback kept there would be deleted by the
step that acts on it — the same shape as the dogfood question `yoloai
destroy` would have taken with it (Q3).

**The one race, and how it is closed.** A handback is PERMANENT once
written: a Worker that has handed back never reads as stalled again,
however long it stays silent, because silence after a handback is the
expected state. That is what makes "done" and "hung" distinguishable with
no window between them — the record is written while the Worker is still
alive and beating, and nothing afterwards can turn it back into a question.

The cost of permanence is that a stale handback would hide a LATER stall,
so it is cleared at exactly one point: when the Worker is started on new
work (`rite sandbox start`, `rite prepare`). `clear` returns what it
removed and the caller says so when the Manager had not yet been told,
because a handback dropped silently is the failure this module exists to
remove.

⚠ **What a handback does NOT establish.** That the work is good, that it is
pushed, or that the ticket is complete. The summary and branch are the
Worker's own claim, carried as such; whether the branch exists and what is
on it is checked by whoever integrates, not here. A handback says one
thing rite can act on: this Worker has stopped working and is not hung.

⚠ **Stated, not reassuring:** `.rite/` is writable from inside a Worker's
sandbox, so a Worker can write another Worker's handback, as it can already
rewrite another Worker's heartbeat (`sandbox.start_worker`'s own note on the
`rw` mount). This adds no door that was not already open. What a Worker
cannot write is the ledger of which handbacks have been passed on, which
lives in the Manager's own directory outside the project — so a Worker
cannot mark its own handback as already told and have it never reach anyone.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.names import require_safe_name
from rite_ai.state import write_atomic

HANDBACK_DIRNAME = "handback"


@dataclass(frozen=True)
class Handback:
    """One Worker's completion record."""

    worker: str
    ticket: str = ""
    branch: str = ""
    summary: str = ""
    timestamp: float = 0.0
    unreadable: str = ""
    """Why this record could not be read, when it could not be. Never
    written to disk.

    ⚠ **A handback that cannot be parsed is a HANDBACK, not a None.** The
    same rule `handover._load` and `heartbeat.read_heartbeat_status` reached
    the hard way: dropping an unreadable record removes that Worker from
    the one check that would notice, and the file existing at all is
    evidence the Worker tried to say something. A caller must not read this
    as "done" — `done` below is False for it — and must not read it as
    "nothing was said" either."""

    @property
    def done(self) -> bool:
        """True only for a record rite could actually read."""
        return not self.unreadable

    def describe(self) -> str:
        """The sentence every view prints about this handback."""
        ticket = f" on {self.ticket}" if self.ticket else ""
        if self.unreadable:
            return (
                f"handed back{ticket}, but the record is UNREADABLE ({self.unreadable})"
            )
        branch = f", branch {self.branch}" if self.branch else ""
        return f"handed back{ticket}{branch}"

    def age_seconds(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.timestamp)


def _dir(root: Path) -> Path:
    return root / ".rite" / HANDBACK_DIRNAME


def path_for(root: Path, worker: str) -> Path:
    """Where `worker`'s handback lives. Validated rather than joined: a name
    reaching a path unchecked is what `rite_ai.names` exists for, and
    `--worker ../../IMPORTANT` is the defect `write_heartbeat` already
    carries a check against."""
    require_safe_name(worker, kind="worker name")
    return _dir(root) / f"{worker}.json"


def write(
    root: Path,
    worker: str,
    *,
    ticket: str = "",
    branch: str = "",
    summary: str = "",
    now: float | None = None,
) -> Path:
    """Record that `worker` has finished and handed its work back.

    Atomic, because the supervisor reads this directory on a timer and a
    half-written record would read as the unreadable case above — which is
    reported to a person, so a torn write would cost a false alarm on every
    successful handback.
    """
    path = path_for(root, worker)
    record = {
        "worker": worker,
        "ticket": ticket,
        "branch": branch,
        "summary": summary,
        "timestamp": time.time() if now is None else now,
    }
    write_atomic(path, json.dumps(record, indent=1) + "\n")
    return path


def read(root: Path, worker: str) -> Handback | None:
    """`worker`'s handback, or None when there is none.

    None means "this Worker has not handed back" and nothing else. A record
    that exists and cannot be read comes back as a `Handback` carrying
    `unreadable`, never as None.
    """
    try:
        path = path_for(root, worker)
    except Exception:
        # An unsafe name cannot have a handback, because nothing could have
        # written one under it. Not an error to the caller: every caller
        # here is iterating a list of configured workers.
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as e:
        return Handback(worker=worker, unreadable=f"{path.name} could not be read: {e}")
    try:
        data = json.loads(raw)
    except ValueError as e:
        return Handback(worker=worker, unreadable=f"{path.name} is not JSON: {e}")
    if not isinstance(data, dict):
        return Handback(
            worker=worker,
            unreadable=f"{path.name} holds {type(data).__name__}, not an object",
        )
    stamp = data.get("timestamp", 0)
    if not isinstance(stamp, int | float) or isinstance(stamp, bool):
        return Handback(
            worker=worker, unreadable=f"{path.name} has a non-numeric timestamp"
        )
    return Handback(
        worker=worker,
        ticket=str(data.get("ticket") or ""),
        branch=str(data.get("branch") or ""),
        summary=str(data.get("summary") or ""),
        timestamp=float(stamp),
    )


def read_all(root: Path, workers: list[str]) -> dict[str, Handback]:
    """Every handback among `workers`, by worker name.

    Keyed by the CONFIGURED workers rather than by what is in the directory:
    a file left behind by a worker that has since been removed is not a
    Worker anything watches, and reporting it would name something `rite
    status` does not list.
    """
    found = {}
    for w in workers:
        record = read(root, w)
        if record is not None:
            found[w] = record
    return found


def clear(root: Path, worker: str) -> Handback | None:
    """Remove `worker`'s handback, returning what was removed.

    Called when the Worker is started on new work: from then on its silence
    is a question again. The caller says what it removed when nothing had
    yet passed it on — see the module docstring on permanence.
    """
    had = read(root, worker)
    try:
        path_for(root, worker).unlink()
    except (FileNotFoundError, OSError):
        pass
    return had
