"""Is this ticket refined? The one predicate (the note's P6 and part 3.2).

A pure function of ONE read of the board (the ticket and its comments) and the
key. It never lists the board, because list endpoints lag a new issue (DF4).
It never writes. It never trusts a label: `ready-to-work` is a view of this
answer, not an input to it (part 3.10).

**Every state that is not REFINED fails closed, and says why.** In particular:

- A comment that carries the record marker but cannot be read as a signed
  record for this ticket makes the ticket UNREADABLE. It is not skipped.
  Skipping it could promote an older record to the head: an edited or garbled
  newer record would silently hand the Worker a superseded definition of done.
  The cost is that anyone who can comment can make a ticket UNREADABLE. That is
  a visible stop that names the comment, never a way to make work start.
- Two heads are CONFLICT, never "latest wins": which is latest is the order of
  two independent writes (part 4, race 3).

The round states (ASKING, PROPOSED, PARKED) come from the round ledger, which is
TR2's. Until then a ticket with no record is NOT REFINED.
"""

from __future__ import annotations

from dataclasses import dataclass

from rite_ai.refinement import record as rec
from rite_ai.tickets.interface import Thread, Ticket

NOT_REFINED = "NOT REFINED"
REFINED = "REFINED"
STALE = "STALE"
CONFLICT = "CONFLICT"
UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class Status:
    """What the predicate answered, from ONE read of the board.

    `record` is set only when the state is REFINED or STALE, and it is the
    record from that same read. `ticket` is the ticket as that read returned
    it, whenever the read succeeded. A caller that starts work hands the
    Worker `record` and `ticket` from this object, and must not read the board
    again: a second read lets an edit land between the two, so a Worker could
    be started against text that no longer matches the state that authorised
    it (the note's part 4, race 4).
    """

    state: str
    record: rec.Record | None
    detail: str
    ticket: Ticket | None = None

    @property
    def refined(self) -> bool:
        return self.state == REFINED


def status(board, ticket_id: str) -> Status:
    """Is `ticket_id` refined on `board` (a `TicketBackend`)?

    Exactly one board read (`read_thread`), then the key, then `evaluate`.
    The board's identity comes from `board` itself, so the record is checked
    against the board that was actually read.
    """
    from rite_ai.refinement import key as refinement_key

    identity = rec.board_identity(board)
    thread = board.read_thread(ticket_id)
    if identity is None:
        return Status(
            UNREADABLE,
            None,
            f"rite cannot identify this board ({type(board).__name__}), so no "
            "record on it can be checked",
            thread.ticket if isinstance(thread, Thread) else None,
        )
    return evaluate(ticket_id, identity, thread, refinement_key.load())


def board_for(root, config, *, role: str = "workers"):
    """The board a project's config names, or the `Status` that says why there
    is none. Shared by `of` (reading) and `accept` (writing), so a record is
    written to, and checked against, a board built the same way."""
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.tickets import BackendError, create_backend_from_config

    if config is None:
        config = parse_config(root / ".rite" / "config.yaml")
    if isinstance(config, ParseError):
        return Status(UNREADABLE, None, f"config error: {config.message}")
    tb = config.ticket_backend
    if tb.type == "none":
        return Status(
            UNREADABLE,
            None,
            "no ticket backend is configured, so no ticket can be checked "
            "(ticket_backend.type in .rite/config.yaml)",
        )
    board = create_backend_from_config(
        tb, board_role=role, credentials=config.credentials
    )
    if isinstance(board, BackendError):
        return Status(UNREADABLE, None, board.message)
    return board


def of(root, config, ticket_id: str, *, role: str = "workers") -> Status:
    """Is `ticket_id` refined, from a project's root and its parsed config?

    **The one path from a ticket id to a status.** `rite refine status` calls
    it, and so does anything that starts work (TR4's `sandbox start`), so the
    composition (config, backend, one read, key, predicate) exists once and
    cannot drift between callers. `config` is the parse the caller already
    holds, so what it decided from and the board read here come from ONE
    parse; None parses `root`'s own.

    Every way of failing to get as far as a read is UNREADABLE, never NOT
    REFINED: an unconfigured or unbuildable board is not a board with no
    records on it.
    """
    board = board_for(root, config, role=role)
    if isinstance(board, Status):
        return board
    return status(board, ticket_id)


def render_for_worker(record: rec.Record) -> str:
    """The agreed definition of done as a Worker's start prompt carries it.
    One implementation, in `record`; re-exported here so a caller of `of`
    needs this module only."""
    return rec.render_for_worker(record)


def evaluate(ticket_id: str, board: dict, thread, loaded) -> Status:
    """The predicate itself, pure. `thread` is a `tickets.Thread` or a
    `tickets.BackendError`; `board` is `record.board_identity` of the board
    it was read from; `loaded` is `refinement.key.Loaded`."""
    from rite_ai.tickets import BackendError

    if isinstance(thread, BackendError):
        return Status(
            UNREADABLE, None, f"the board could not be read: {thread.message}"
        )
    ticket = thread.ticket

    def answer(state: str, detail: str, record: rec.Record | None = None) -> Status:
        return Status(state, record, detail, ticket)

    if not thread.complete:
        return answer(
            UNREADABLE,
            "the ticket's comments could not be shown complete "
            f"({thread.note or 'the board returned fewer than it counted'}), "
            "so an agreed definition of done may be among the ones not read",
        )

    claimed = [c for c in thread.comments if rec.carries_marker(c.body)]
    if not claimed:
        return answer(NOT_REFINED, "no agreed definition of done on the ticket")

    if loaded.key is None:
        return answer(
            UNREADABLE,
            loaded.problem
            or "the ticket carries refinement records, but this machine has no "
            "refinement key to check them with",
        )

    records: dict[str, tuple[rec.Record, dict]] = {}
    for comment in claimed:
        where = f"comment {comment.id}" + (
            f" by {comment.author}" if comment.author else ""
        )
        payload = rec.extract(comment.body)
        if payload is None:
            return answer(
                UNREADABLE,
                f"{where} is marked as a refinement record, but no record could "
                "be read from it. Delete the comment if it is not rite's",
            )
        if not rec.mac_verifies(payload, loaded.key):
            return answer(
                UNREADABLE,
                f"{where} is marked as a refinement record, but its signature "
                "does not verify: it was edited, or rite did not write it. "
                "Delete it, or restore rite's text",
            )
        if payload.get("ticket") != ticket_id:
            return answer(
                UNREADABLE,
                f"{where} is a record for {payload.get('ticket')!r}, not for "
                f"{ticket_id}: a record does not transfer between tickets",
            )
        if payload.get("board") != board:
            return answer(
                UNREADABLE,
                f"{where} is a record written for a different board "
                f"({payload.get('board')}); this project's board is {board}",
            )
        problem = rec.schema_problem(payload)
        if problem:
            return answer(UNREADABLE, f"{where} is signed but {problem}")
        record = rec.from_payload(payload)
        seen = records.get(record.record_id)
        if seen is not None and rec.canonical(seen[1]) != rec.canonical(payload):
            return answer(
                UNREADABLE, f"two different records share the id {record.record_id}"
            )
        records[record.record_id] = (record, payload)

    superseded = {r.supersedes for r, _ in records.values() if r.supersedes}
    heads = [r for r, _ in records.values() if r.record_id not in superseded]
    if len(heads) != 1:
        ids = ", ".join(sorted(r.record_id for r in heads)) or "none: a cycle"
        return answer(
            CONFLICT,
            f"{len(heads)} records each claim to be the current one ({ids}). A "
            "person decides which stands; rite does not pick by order",
        )
    head = heads[0]
    changed = [
        name
        for name, now, then in (
            ("title", rec.text_sha256(ticket.title), head.title_sha256),
            (
                "description",
                rec.text_sha256(ticket.description),
                head.description_sha256,
            ),
        )
        if now != then
    ]
    if changed:
        return answer(
            STALE,
            f"the ticket's {' and '.join(changed)} changed after the definition "
            f"of done was agreed (record {head.record_id})",
            head,
        )
    return answer(REFINED, f"agreed definition of done: record {head.record_id}", head)
