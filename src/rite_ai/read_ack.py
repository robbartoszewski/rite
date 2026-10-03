"""A Worker saying it has READ the answer somebody sent it (SCRUM-61).

**What this is for.** `sandbox.questions.deliver_answer` writes an answer into
the file a Worker polls, and its own docstring is careful about what that
establishes: *"The honest claim is 'written where it is polled for', not
'read'."* Everything downstream then treated the write as the end of the
exchange — the question was settled, and the person who answered was told
nothing at all unless it had FAILED. So the Owner's screen said the same
thing whether the Worker read their answer in ten seconds or never woke up
to read it.

This is the other half: the Worker says it has read the answer, through the
channel `rite_ai.handback` established for the same reason (a Worker cannot
message out — see that module). `rite ack` writes it; the supervisor reads it
on the host and that, and only that, is what puts a ✅ on the Owner's reply.

⚠ **SAID AS A CLAIM, BECAUSE THAT IS WHAT IT IS.** The record is written by
the Worker's agent, so the tick means "the Worker said it read this", which
is strictly stronger than "written where it is polled for" and is still not
an observation. **Nothing on the host can observe a read**: the exchange file
is read by a process inside the sandbox, and no mtime, no poll and no yoloAI
call reports that it was opened. Every line of user-facing text about this
has to say the first thing and not imply the second.

**Keyed by question, not by Worker.** One Worker answers several questions
over its life, and an ack for the question asked yesterday must not tick the
one asked an hour ago. So the record holds `{qid: read_at}` and a view asks
about one qid.

⚠ **No ack is not an error, and it is not silence either.** A delivered
answer with no ack past `handback.NOT_INTEGRATED_AFTER`'s sibling bound is
reported as UNREAD (`managers.worker_questions`), because the person who
answered is otherwise waiting on a Worker that may never have woken up —
which is the defect this whole path exists to remove, one step further on
than where `deliver_answer` left it.

⚠ **Same trust surface as a handback, and no more.** `.rite/` is writable
from inside a Worker's sandbox, so a Worker can write another Worker's ack
exactly as it can rewrite another's heartbeat. What that buys is a ✅ on a
message and the clearing of an UNREAD report — it cannot suppress a stall
and cannot release a claim. The ledger of which acks have been acted on
lives in the Manager's own directory, outside the project, so a Worker
cannot mark its own ack as already handled.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.names import require_safe_name
from rite_ai.state import write_atomic

READ_DIRNAME = "answers-read"

UNREAD_AFTER = 2 * 3600.0
"""How long a delivered answer may sit unacked before it is reported UNREAD.

Generous on purpose. A Worker reads its answer when it next polls, which is
seconds — but a Worker that is mid-command, or whose agent is between turns,
is not in trouble, and a report that fires on a Worker which is about to
read it teaches its reader to ignore the report. Two hours is far past any
poll and still well inside the window in which somebody who answered is
still waiting for it.
"""


class BadAck(ValueError):
    """A field that must not be written as given."""


@dataclass(frozen=True)
class ReadAcks:
    """What one Worker says it has read."""

    worker: str
    read: dict[str, float]
    """question id -> when the Worker said it read the answer."""
    unreadable: str = ""
    """Why the record could not be read, when it could not be. Never written
    to disk, and never read as "nothing was acked" — the same rule
    `handback.Handback.unreadable` carries, for the same reason."""

    @property
    def known(self) -> bool:
        return not self.unreadable

    def read_at(self, question: str) -> float | None:
        """When `question`'s answer was acked, or None."""
        at = self.read.get(question)
        return float(at) if isinstance(at, int | float) else None


def _dir(root: Path) -> Path:
    return root / ".rite" / READ_DIRNAME


def path_for(root: Path, worker: str) -> Path:
    """Where `worker`'s acks live. Validated rather than joined, for the
    reason `handback.path_for` is: a name reaching a path unchecked is the
    defect `rite_ai.names` exists for."""
    require_safe_name(worker, kind="worker name")
    return _dir(root) / f"{worker}.json"


_FORBIDDEN = ("\n", "\r", "\x00")


def _checked_question(value: str) -> str:
    """A question id, refused rather than written if it is not one line.

    The id reaches a Slack API call and a line of text a Manager reads; the
    reasoning is `handback._checked_field`'s, and it is kept here rather
    than shared because the two modules have no other reason to depend on
    each other.
    """
    said = value.strip()
    if not said:
        raise BadAck("an ack needs the question id it is for")
    for bad in _FORBIDDEN:
        if bad in said:
            raise BadAck(f"a question id must be one line: it contains {bad!r}")
    return said


def read(root: Path, worker: str) -> ReadAcks | None:
    """`worker`'s acks, or None when it has made none.

    None means "this Worker has acked nothing" and nothing else; a record
    that exists and cannot be read comes back carrying `unreadable`.
    """
    try:
        path = path_for(root, worker)
    except Exception:
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as e:
        return ReadAcks(worker, {}, unreadable=f"{path.name} could not be read: {e}")
    try:
        data = json.loads(raw)
    except ValueError as e:
        return ReadAcks(worker, {}, unreadable=f"{path.name} is not JSON: {e}")
    if not isinstance(data, dict):
        return ReadAcks(
            worker,
            {},
            unreadable=f"{path.name} holds {type(data).__name__}, not an object",
        )
    said = data.get("read")
    if not isinstance(said, dict):
        return ReadAcks(worker, {}, unreadable=f"{path.name} has no 'read' object")
    return ReadAcks(
        worker,
        {
            str(q): float(at)
            for q, at in said.items()
            if isinstance(at, int | float) and not isinstance(at, bool)
        },
    )


def write(root: Path, worker: str, question: str, *, now: float | None = None) -> Path:
    """Record that `worker` has read the answer to `question`.

    ⚠ **Read-modify-write under a lock**, because a Worker acks one question
    while the record holds the ones before it, and a torn or lost update
    would silently un-ack an answer the Worker really did read — which
    reports as UNREAD and sends somebody to look at a Worker that is fine.
    """
    from rite_ai.state import locked

    qid = _checked_question(question)
    path = path_for(root, worker)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        existing = read(root, worker)
        kept = dict(existing.read) if existing is not None and existing.known else {}
        kept[qid] = time.time() if now is None else now
        write_atomic(
            path,
            json.dumps({"worker": worker, "read": kept}, indent=1, sort_keys=True)
            + "\n",
        )
    return path


def read_at(root: Path, worker: str, question: str) -> float | None:
    """When `worker` said it read `question`'s answer, or None."""
    acks = read(root, worker)
    if acks is None or not acks.known:
        return None
    return acks.read_at(question)


def clear(root: Path, worker: str) -> None:
    """Forget `worker`'s acks. For a Worker being set up fresh — the same
    point `handback.clear` is called from, and for the same reason: a record
    under this name belongs to a previous incarnation of it."""
    try:
        path_for(root, worker).unlink()
    except (FileNotFoundError, OSError):
        pass
