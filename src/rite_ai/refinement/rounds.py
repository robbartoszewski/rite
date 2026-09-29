"""The rounds of refining one ticket with the User, and what that makes it (TR2).

`refinement.status` answers from the board whether a ticket is REFINED. It
cannot say what happens to a ticket that is not: whether a question is out,
whether the User was silent past the deadline, whether a person must restart
it. That is this ledger's, and only that: **it holds rounds, and it never
decides REFINED** (the note's part 4, race 11). The board is the truth; a
ledger lost or deleted costs the rounds' history, never a wrong state.

## Where it lives, and who writes it

In the Owner's `routing/` directory, beside the delivered-message ledger,
which the Manager's sandbox profile does not grant (`routing._ledger_dir`). A
Manager asks for a round with `rite refine ask`, which writes a request into
its own directory, and the supervisor, outside the boundary, decides and
records it: the same shape as `rite route` and `rite chore`. So a Manager
cannot mark its own question answered, move a deadline, or unpark a ticket.

One file per ticket, each written under a kernel `flock` on its own lock
file (race 11, the scheduler lock's fix): the supervisor, a person's
`rite refine reopen` and the relay can all write, from different processes.

## What a ticket's state is

`state_of` combines the board's answer (one read, `status.Status`) with this
ledger, in that order, so the board always wins:

* REFINED, CONFLICT and UNREADABLE come from the board, whatever the ledger
  says;
* NOT REFINED or STALE on the board, with an attempt here made against the
  ticket's CURRENT text, is that attempt's state: ASKING, PROPOSED,
  WAITING FOR YOU or PARKED;
* an attempt made against OTHER text is over: the ticket was edited, and a
  User who fixes the ticket himself has resumed it (part 3.4 step 7). The
  board's own state stands.

Robert's rulings this encodes (TRQ11): silence past the deadline is WAITING
FOR YOU, never PARKED, and is not asked again while he is away; being
re-presented when he is back spends no round; PARKED is only "not agreed
after N rounds", "thread unreadable" or "not started by the Manager".
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st

ASKING = "ASKING"
PROPOSED = "PROPOSED"
WAITING = "WAITING FOR YOU"
PARKED = "PARKED"

NOT_AGREED = "not agreed after N rounds"
THREAD_UNREADABLE = "thread unreadable"
NOT_STARTED = "not started by the Manager"
PARK_REASONS = (NOT_AGREED, THREAD_UNREADABLE, NOT_STARTED)

MISSES_TO_PARK = 2
"""Consecutive sessions a ticket was handed to the Owner without a round
being started, before it is PARKED (not started by the Manager). A Manager
that cannot or will not start a ticket's refinement costs two sessions, not
a session a minute (part 3.4 step 0)."""

DIRNAME = "refinement"


@dataclass
class Round:
    """One message to the User about one ticket."""

    k: int
    sent_at: float
    deadline: float
    proposal: bool
    """Whether it carries a definition of done the User can accept in a word."""
    where: str = ""
    """Where it went: the outbox message's name, which the relay maps to the
    Slack thread it posted it in."""
    read_past_deadline: bool = False
    """The thread was read after the deadline, and nothing sent before it
    answered (race 6). Only then is silence WAITING FOR YOU."""
    presented_again_at: float = 0.0
    """When it was re-presented after the User came back. Once only."""
    answered_at: float = 0.0
    """When an attributed reply answered it, by Slack's send time. 0: not."""

    @property
    def answered(self) -> bool:
        return self.answered_at > 0


@dataclass
class Attempt:
    """Refinement of one ticket's text, from its first round to its end."""

    ticket: str
    text_sha256: str
    """The ticket's title and description the attempt is refining. Another
    text is another attempt (the ticket was edited)."""
    rounds: list[Round] = field(default_factory=list)
    parked: str = ""
    misses: int = 0

    @property
    def latest(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None


@dataclass(frozen=True)
class State:
    """What a ticket is, for the Owner and the loop."""

    name: str
    detail: str
    attempt: Attempt | None = None

    @property
    def open(self) -> bool:
        """Counts against K: a round inside its deadline (TRQ11: tickets
        waiting for the User never crowd out new ones)."""
        return self.name in (ASKING, PROPOSED)

    @property
    def uses_sessions(self) -> bool:
        """WAITING FOR YOU, PARKED, CONFLICT and UNREADABLE never cause a
        session (part 3.4 step 0)."""
        return self.name in (ASKING, PROPOSED, st.NOT_REFINED, st.STALE)


def text_of(ticket) -> str:
    """The hash an attempt is keyed by: title and description together, so
    an edit to either starts a new attempt, as either makes a record STALE."""
    return rec.text_sha256(f"{ticket.title}\n{ticket.description}")


def state_of(board: st.Status, attempt: Attempt | None, *, now: float) -> State:
    """The ticket's state, from one board read and this ledger. The board
    wins: the ledger only says what an unrefined ticket is waiting for."""
    if board.state in (st.REFINED, st.CONFLICT, st.UNREADABLE):
        return State(board.state, board.detail)
    if (
        attempt is None
        or board.ticket is None
        or attempt.text_sha256 != text_of(board.ticket)
    ):
        return State(board.state, board.detail)
    if attempt.parked:
        return State(PARKED, attempt.parked, attempt)
    latest = attempt.latest
    if latest is None:
        return State(board.state, board.detail, attempt)
    of = f"round {latest.k}"
    if not latest.answered and now >= latest.deadline:
        if latest.read_past_deadline:
            return State(WAITING, f"{of}: no answer by its deadline", attempt)
        return State(
            ASKING,
            f"{of}: deadline passed, thread not yet read past it",
            attempt,
        )
    if latest.answered:
        # Answered and not yet followed by a new round: the Owner's move.
        return State(ASKING, f"{of} answered; the next round is due", attempt)
    return State(PROPOSED if latest.proposal else ASKING, of, attempt)


# --- the ledger -------------------------------------------------------------


def _dir(root: Path, owner: str) -> Path:
    from rite_ai.managers.routing import _ledger_dir

    return _ledger_dir(root, owner) / DIRNAME


def _path(root: Path, owner: str, ticket: str) -> Path:
    # A ticket id may carry '/' or '#' (`routing.ticket_problem`), so it is
    # quoted into one file name rather than trusted as a path.
    return _dir(root, owner) / f"{quote(ticket, safe='-_.')}.json"


def _from(data: dict) -> Attempt | None:
    try:
        return Attempt(
            ticket=data["ticket"],
            text_sha256=data["text_sha256"],
            rounds=[Round(**r) for r in data.get("rounds", [])],
            parked=data.get("parked", ""),
            misses=int(data.get("misses", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load(root: Path, owner: str, ticket: str) -> Attempt | None:
    """The ticket's attempt, or None. A file that does not parse is None:
    the board still decides REFINED, so a lost ledger costs history only."""
    try:
        data = json.loads(_path(root, owner, ticket).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _from(data) if isinstance(data, dict) else None


@contextmanager
def locked(root: Path, owner: str, ticket: str):
    """The ticket's attempt, under its lock, and a `save` to call before
    leaving. Yields `(attempt or None, save)`."""
    from rite_ai.state import write_atomic

    path = _path(root, owner, ticket)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)

        def save(attempt: Attempt) -> None:
            write_atomic(path, json.dumps(asdict(attempt), indent=1) + "\n")

        yield load(root, owner, ticket), save
    finally:
        os.close(fd)


PACING_FILE = "pacing.json"


def last_session(root: Path, owner: str) -> float | None:
    """When a session last started for refinement, or None if never.
    Written by the supervisor when it starts one (`record_session`)."""
    try:
        data = json.loads((_dir(root, owner) / PACING_FILE).read_text("utf-8"))
        return float(data["last_session"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def record_session(root: Path, owner: str, at: float) -> None:
    from rite_ai.state import write_atomic

    where = _dir(root, owner)
    where.mkdir(parents=True, exist_ok=True)
    write_atomic(where / PACING_FILE, json.dumps({"last_session": at}) + "\n")


@dataclass(frozen=True)
class Events:
    """What the User did since the last refinement session: the only things
    that may start another one (part 3.4 step 0)."""

    first_look: bool
    replies: int
    deadlines: int


def events_since(
    attempts: dict[str, Attempt], last: float | None, *, now: float
) -> Events:
    since = last if last is not None else float("-inf")
    replies = deadlines = 0
    for attempt in attempts.values():
        if attempt.parked:
            continue
        for r in attempt.rounds:
            if r.answered and r.answered_at > since:
                replies += 1
            elif not r.answered and since < r.deadline <= now:
                deadlines += 1
    return Events(first_look=last is None, replies=replies, deadlines=deadlines)


def all_attempts(root: Path, owner: str) -> dict[str, Attempt]:
    """Every attempt this Owner holds, by ticket."""
    found: dict[str, Attempt] = {}
    where = _dir(root, owner)
    if not where.is_dir():
        return found
    for path in sorted(where.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        attempt = _from(data) if isinstance(data, dict) else None
        if attempt is not None:
            found[attempt.ticket] = attempt
    return found
