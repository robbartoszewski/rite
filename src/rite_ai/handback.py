"""A Worker's "I am done, here is the work", written where the host reads it.

**What happened (dogfood, 2026-10-03).** A sandboxed Worker finished its
ticket and did what its own instructions tell it to: "Tell your Manager you
are free, in the same message that reports the work." There was no command
that could do it. A Manager's mailbox lives outside the project
(`managers.manager_dir`, DF3) and a Worker's sandbox does not grant that
path, so every route into it is refused — and the refusal is correct and
stays, because a message in a Manager's inbox is delivered as the Owner's
instruction and a Worker must not write one
(`enclosure._manager_separation`).

⚠ **Which command produced which error was measured, after review caught
this paragraph asserting it.** `rite message <manager>` catches the
`PermissionError` and prints a refusal that explains itself (exit 1, no
traceback). `rite reply --manager` did NOT catch it, and raised
`PermissionError: [Errno 13] Permission denied` out of `mailbox.send` — a
traceback, which is the shape the dogfood reported, and an agent handed a
traceback routes around it. It catches it now (see `rite reply`), said
rather than raised. Neither of those is the fix for THIS, though: the fix is
that there is a command for the thing the Worker was told to do.

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

NOT_INTEGRATED_AFTER = 12 * 3600.0
"""How long a handback may stand before rite says nobody has taken it up.

Two things at once, and it is worth saying which is which. It is useful on
its own: finished work waiting half a day is a real condition, and before
this nothing said so, because a handback is permanent until the Worker is
started again. It also bounds the forgery below — a handback is suppression
of a stall, and an unbounded suppression is one nothing ever revisits. ⚠ That
second claim is only true because the escalated reason is deliberately NOT
filtered out of the scheduler's tick (`watchdog.handed_back_reasons`): the
terminating check found the first version putting it there, where the one
reader that carries anything unattended subtracted it again, so the bound
existed only for somebody running `rite watchdog` by hand.

It does NOT reintroduce the ambiguity the permanence removes: past this age
the Worker is still reported as handed back and still not as stalled. A
different sentence is added, not a different verdict."""


class BadHandback(ValueError):
    """A field that must not be written as given."""


_FORBIDDEN = ("\n", "\r", "\x00")


def _checked_field(value: str, what: str) -> str:
    """A one-line field, refused rather than written if it is not.

    ⚠ **A NEWLINE HERE FORGES rite's OWN HEADER.** `worker_handbacks`
    interpolates the ticket and the branch into a note in a Manager's inbox,
    whose first line is `telling.header` — and `telling.is_routed_work_note`'s
    contract is that anything recognising a note recognises it by that
    header. The Manager reading the note is a model reading all of it, so a
    `branch` containing a newline and a bracketed line puts text that looks
    like rite's own, or like the Owner's instruction, into the Manager's
    instruction stream. `slack._quoted` states the rule this obeys: a typed
    line must not be able to forge the header.

    The summary is `> `-quoted per line by the reader, which is the other
    half of the same rule and is why it is not restricted here. These two
    are not quoted at their use site because they are short identifiers, so
    they are constrained at the source instead — and a Worker's `--ticket`
    and `--branch` are copied out of text somebody else wrote, which is the
    premise `stdin_text` is built on.
    """
    for bad in _FORBIDDEN:
        if bad in value:
            raise BadHandback(
                f"{what} must be one line: it contains {bad!r}, and a line "
                "break there would read as rite's own header in the note "
                "your Manager is sent"
            )
    return value


def _flattened(value: object) -> str:
    """A one-line field as READ, whatever is on disk.

    ⚠ **HERE, not at a use site, and the terminating check is why.** `write`
    refuses a newline (`_checked_field`), which covers every handback `rite
    done` makes — and covers nothing written by hand into
    `.rite/handback/<worker>.json`, which every Worker's sandbox can do. The
    first repair flattened these in the one reader review had demonstrated,
    the Manager's inbox note, and left `Handback.describe()` — "the sentence
    every view prints about this handback" — interpolating them raw. Measured
    on that version: a hand-written `branch` carrying a newline and a
    bracketed line made `rite watchdog` and `rite status` print what looks
    like rite's own note, which is the Manager's instruction stream.

    Fixing the formatter would have left the next formatter, so the value is
    safe from the moment it is read. `summary` is not flattened because every
    reader quotes it `> ` per line, which is the other half of the same rule
    (`slack._quoted`).
    """
    return " ".join(str(value or "").split())


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

    def not_integrated(self, now: float | None = None) -> bool:
        """Has this stood long enough that nobody is taking it up?

        False for an unreadable record: its timestamp is not a time anybody
        read, and `age_seconds` off a zero would say it had been waiting
        since 1970.
        """
        if self.unreadable or not self.timestamp:
            return False
        return self.age_seconds(now) >= NOT_INTEGRATED_AFTER


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

    Raises `BadHandback` for a ticket or branch that is not one line — see
    `_checked_field`, which is the one place that rule is stated.
    """
    path = path_for(root, worker)
    record = {
        "worker": worker,
        "ticket": _checked_field(ticket, "the ticket"),
        "branch": _checked_field(branch, "the branch"),
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
        ticket=_flattened(data.get("ticket")),
        branch=_flattened(data.get("branch")),
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


def clear(
    root: Path, worker: str, *, older_than: float | None = None
) -> Handback | None:
    """Remove `worker`'s handback, returning what was removed, or None.

    Called when the Worker has been started on new work: from then on its
    silence is a question again.

    ⚠ **`older_than` is what makes this safe to call AFTER the start, which
    is the only time it may be called at all.** Review of the first version
    found, and measured, that clearing before the start reopened the exact
    defect this module exists to close: `rite prepare` that fails on a dirty
    tree, or `rite sandbox start` that refuses a non-REFINED ticket, had
    already deleted the record, so a finished Worker was reported STALLED
    again by a command that did nothing. The clear therefore moved after the
    start succeeds — and once it is there, a handback the NEW Worker has
    already written could be deleted by it. Passing the moment the start
    began removes that: an older record is the previous task's, a newer one
    was written by the Worker now running and is kept.

    Omitting it removes whatever is there, which is for a caller that holds
    no such moment (a test, or a person clearing by hand).
    """
    had = read(root, worker)
    if had is None:
        return None
    if older_than is not None and had.timestamp >= older_than:
        # The Worker now running wrote this. Not ours to remove.
        return None
    try:
        path_for(root, worker).unlink()
    except (FileNotFoundError, OSError):
        pass
    return had
