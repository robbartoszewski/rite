"""A board read that sees what rite itself just wrote (DF4).

**What happened.** Measured 2026-09-27 and again 2026-09-29 on
`rite-dogfood-board` (gh 2.98.0): after rite creates an issue and labels it
`scheduled`, `gh issue list --label scheduled` does not return it for 1.6 to
6.5 s, while `gh issue view` returns it, open and labelled, on the first try
(5 of 5). The reverse holds too: after `scheduled` is taken off, the list
still returned the issue as scheduled in 3 of 3 trials, up to 1.9 s later.
Jira documents the same property for `/search/jql`: "Recent updates might
not be immediately visible in the returned search results". So a `rite
start` straight after a ticket is filed read "nothing ready" and stopped,
and a cycle straight after a dispatch could offer the ticket it had just
handed out. Both are decided by the timing of two unrelated things.

**What this does.** Every ticket rite creates, labels, moves or assigns is
recorded in `.rite/board-writes.json`. Every list then asks the board's
consistent single-ticket read about each recorded ticket, and that read
decides whether it belongs in the result: added when the list has not caught
up, removed when the list still shows a state rite has already changed. No
wait and no retry anywhere: the answer is the board's own consistent read.

**When a ticket leaves the ledger.** Only when the board's list view (the
eventually consistent one) shows the same state as the consistent read,
including its `updated` time: then every later list will too. Not after a
time, and not after a count.

**What it cannot do.** A ticket created by a PERSON, on the board's web page,
is not in the ledger: neither GitHub nor Jira offers a way to ask "has the
list caught up with writes I did not make". A list that returned nothing is
therefore "the board listed nothing", never "the board is empty", and the
loop says so.

**The ledger is a hint, not a source.** Anything in it is re-read from the
board before it is believed, so a wrong entry (written by anything that can
write `.rite/`) can only make rite ask about a ticket, never invent one.

⚠ GitHub's `updatedAt` has one-second resolution. A ticket written three
times by rite within one second, whose first and last states match and
whose middle one differs, could leave the ledger while the list still has
the middle state to show. rite does not write a ticket that way (a dispatch
is one label call; a create is create-then-label, whose two states differ),
and this is said rather than hidden.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.state import CorruptStateError, locked, read_json_state, write_atomic
from rite_ai.tickets.interface import (
    BackendError,
    Ticket,
    TicketBackend,
    TicketFilter,
    TicketPage,
)

LEDGER = "board-writes.json"


def board_identity(backend: TicketBackend) -> str:
    """Which board a write was on: entries for one board are never asked of
    another (a project's `workers` and `board` roles can be two Jira
    projects)."""
    repo = getattr(backend, "repo", "")
    if repo:
        return f"github:{repo}"
    config = getattr(backend, "config", None)
    if config is not None:
        site = getattr(config, "site", "")
        return f"jira:{site}/{getattr(config, 'project_key', '')}"
    return type(backend).__name__


def ledger_path(root: Path) -> Path:
    return Path(root) / ".rite" / LEDGER


@dataclass(frozen=True)
class Written:
    ticket: str
    at: float
    """When rite recorded the write. Compared, never measured against: an
    entry is dropped only if it is still the one that was confirmed."""


def record(root: Path, board: str, ticket: str) -> str:
    """Note that rite just wrote `ticket` on `board`. Returns "" or why the
    note could not be written; the write itself has already happened."""
    path = ledger_path(root)
    try:
        with locked(path):
            data = read_json_state(path, {})
            if not isinstance(data, dict):
                data = {}
            entries = data.get(board)
            entries = entries if isinstance(entries, dict) else {}
            entries[str(ticket)] = time.time()
            data[board] = entries
            write_atomic(path, _dumps(data))
    except (OSError, CorruptStateError) as e:
        return f"rite could not note this write in {path} ({e})"
    return ""


def written(root: Path, board: str) -> list[Written] | BackendError:
    path = ledger_path(root)
    try:
        data = read_json_state(path, {})
    except CorruptStateError as e:
        return BackendError(
            f"{path} could not be read ({e}), so tickets rite just wrote "
            "cannot be told apart from the board's list lagging behind them"
        )
    entries = data.get(board) if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return []
    return [
        Written(str(k), float(v))
        for k, v in entries.items()
        if isinstance(v, (int, float))
    ]


def settle(root: Path, board: str, done: list[Written]) -> None:
    """Forget entries the list has caught up with, unless rite wrote the
    ticket again since they were read."""
    if not done:
        return
    path = ledger_path(root)
    try:
        with locked(path):
            data = read_json_state(path, {})
            entries = data.get(board) if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                return
            for w in done:
                if entries.get(w.ticket) == w.at:
                    del entries[w.ticket]
            write_atomic(path, _dumps(data))
    except (OSError, CorruptStateError):
        # Kept, not lost: an entry that stays costs one read next time.
        return


def _dumps(data: dict) -> str:
    import json

    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def _same(listed: Ticket, current: Ticket) -> bool:
    """The list shows exactly what the consistent read shows, including when
    it was last changed, so it has caught up with every write so far."""
    return (
        listed.updated_at is not None
        and listed.updated_at == current.updated_at
        and (listed.status or "").casefold() == (current.status or "").casefold()
        and sorted(listed.labels or []) == sorted(current.labels or [])
        and (listed.assignee or "") == (current.assignee or "")
    )


@dataclass
class ReadsItsOwnWrites(TicketBackend):
    """Any backend, with rite's own writes read back exactly (module doc)."""

    inner: TicketBackend
    root: Path
    board: str
    notes: list[str] = field(default_factory=list)
    """Writes that could not be recorded, for the caller to say."""

    def _noted(self, ticket_id: str) -> None:
        problem = record(self.root, self.board, ticket_id)
        if problem:
            self.notes.append(problem)

    # --- writes: done, then recorded ----------------------------------------

    def create(self, title, description="", labels=None):
        made = self.inner.create(title, description=description, labels=labels)
        if isinstance(made, Ticket) and made.id:
            self._noted(made.id)
        return made

    # Recorded only when the write succeeded. A failed write may still have
    # changed something (GitHub adds labels, then removes), and that case is
    # not covered; recording a failure instead would put an id that may not
    # exist in the ledger, and every later list would fail on reading it.

    def label(self, ticket_id, labels, remove=None):
        done = self.inner.label(ticket_id, labels, remove=remove)
        if not isinstance(done, BackendError):
            self._noted(ticket_id)
        return done

    def move(self, ticket_id, status):
        done = self.inner.move(ticket_id, status)
        if not isinstance(done, BackendError):
            self._noted(ticket_id)
        return done

    def assign(self, ticket_id, worker):
        done = self.inner.assign(ticket_id, worker)
        if not isinstance(done, BackendError):
            self._noted(ticket_id)
        return done

    # --- the read this exists for -------------------------------------------

    def list_tickets(self, filters: TicketFilter | None = None):
        listed = self.inner.list_tickets(filters)
        if isinstance(listed, BackendError):
            return listed
        mine = written(self.root, self.board)
        if isinstance(mine, BackendError):
            return mine
        if not mine:
            return listed
        ours = {w.ticket for w in mine}
        by_id = {t.id: t for t in listed}
        rows = [t for t in listed if t.id not in ours]
        current: dict[str, Ticket] = {}
        gone: list[Written] = []
        for w in mine:
            now = self.inner.read(w.ticket)
            if isinstance(now, BackendError) and self.inner.missing(now):
                # Deleted since rite wrote it: in no list, and not asked again.
                gone.append(w)
                continue
            if isinstance(now, BackendError):
                return BackendError(
                    f"rite wrote {w.ticket} and could not read it back "
                    f"({now.message}), so whether it belongs in this list "
                    "cannot be told"
                )
            current[w.ticket] = now
            fits = self.inner.matches(now, filters)
            if fits is None:
                # This backend cannot decide the filter from a ticket, so
                # the list's own answer stands, uncorrected.
                if w.ticket in by_id:
                    rows.append(by_id[w.ticket])
            elif fits:
                rows.append(now)
        live = [w for w in mine if w not in gone]
        settle(self.root, self.board, gone + self._caught_up(live, current, by_id))
        return TicketPage(rows, truncated=getattr(listed, "truncated", False))

    def _caught_up(
        self, mine: list[Written], current: dict[str, Ticket], by_id: dict
    ) -> list[Written]:
        """Entries the list view now shows exactly as the consistent read
        does. Asked of the SAME list call the readiness read uses, filtered
        by the ticket's current state, never of some other view that might
        be fresher than the one being corrected."""
        done = [
            w
            for w in mine
            if w.ticket in by_id and _same(by_id[w.ticket], current[w.ticket])
        ]
        pending = [w for w in mine if w not in done]
        probes: dict[tuple, list[Written]] = {}
        for w in pending:
            now = current[w.ticket]
            key = (now.status or "", sorted(now.labels or [])[:1])
            probes.setdefault((key[0], tuple(key[1])), []).append(w)
        for (status, label), group in probes.items():
            view = self.inner.list_tickets(
                TicketFilter(status=status or None, label=label[0] if label else None)
            )
            if isinstance(view, BackendError):
                continue
            seen = {t.id: t for t in view}
            done += [
                w
                for w in group
                if w.ticket in seen and _same(seen[w.ticket], current[w.ticket])
            ]
        return done

    # --- everything else, unchanged -----------------------------------------

    def read(self, ticket_id):
        return self.inner.read(ticket_id)

    def update(self, ticket_id, **fields):
        return self.inner.update(ticket_id, **fields)

    def comment(self, ticket_id, text):
        return self.inner.comment(ticket_id, text)

    def read_thread(self, ticket_id):
        return self.inner.read_thread(ticket_id)

    def query(self, raw_query):
        return self.inner.query(raw_query)

    def link(self, ticket_id, target_id, link_type):
        return self.inner.link(ticket_id, target_id, link_type)

    def can_create(self):
        return self.inner.can_create()

    def matches(self, ticket, filters):
        return self.inner.matches(ticket, filters)

    def missing(self, error):
        return self.inner.missing(error)

    def __getattr__(self, name):
        # Backend-specific helpers (`repo`, `config`, …) callers already use.
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)
