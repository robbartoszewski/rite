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

from dataclasses import dataclass, field

from rite_ai.refinement import record as rec

NOT_REFINED = "NOT REFINED"
REFINED = "REFINED"
STALE = "STALE"
CONFLICT = "CONFLICT"
UNREADABLE = "UNREADABLE"


@dataclass(frozen=True)
class Status:
    state: str
    detail: str
    head: rec.Record | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def refined(self) -> bool:
        return self.state == REFINED


def evaluate(ticket_id: str, board: dict, thread, loaded) -> Status:
    """`thread` is a `tickets.Thread` or a `tickets.BackendError`; `loaded` is
    `refinement.key.Loaded`."""
    from rite_ai.tickets import BackendError

    if isinstance(thread, BackendError):
        return Status(UNREADABLE, f"the board could not be read: {thread.message}")
    if not thread.complete:
        return Status(
            UNREADABLE,
            "the ticket's comments could not be shown complete "
            f"({thread.note or 'the board returned fewer than it counted'}), "
            "so an agreed definition of done may be among the ones not read",
        )

    claimed = [c for c in thread.comments if rec.carries_marker(c.body)]
    if not claimed:
        return Status(NOT_REFINED, "no agreed definition of done on the ticket")

    if loaded.key is None:
        return Status(
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
            return Status(
                UNREADABLE,
                f"{where} is marked as a refinement record, but no record could "
                "be read from it. Delete the comment if it is not rite's",
            )
        if not rec.mac_verifies(payload, loaded.key):
            return Status(
                UNREADABLE,
                f"{where} is marked as a refinement record, but its signature "
                "does not verify: it was edited, or rite did not write it. "
                "Delete it, or restore rite's text",
            )
        if payload.get("ticket") != ticket_id:
            return Status(
                UNREADABLE,
                f"{where} is a record for {payload.get('ticket')!r}, not for "
                f"{ticket_id}: a record does not transfer between tickets",
            )
        if payload.get("board") != board:
            return Status(
                UNREADABLE,
                f"{where} is a record written for a different board "
                f"({payload.get('board')}); this project's board is {board}",
            )
        problem = rec.schema_problem(payload)
        if problem:
            return Status(UNREADABLE, f"{where} is signed but {problem}")
        record = rec.from_payload(payload)
        seen = records.get(record.record_id)
        if seen is not None and rec.canonical(seen[1]) != rec.canonical(payload):
            return Status(
                UNREADABLE,
                f"two different records share the id {record.record_id}",
            )
        records[record.record_id] = (record, payload)

    superseded = {r.supersedes for r, _ in records.values() if r.supersedes}
    heads = [r for r, _ in records.values() if r.record_id not in superseded]
    if len(heads) != 1:
        ids = ", ".join(sorted(r.record_id for r in heads)) or "none: a cycle"
        return Status(
            CONFLICT,
            f"{len(heads)} records each claim to be the current one ({ids}). A "
            "person decides which stands; rite does not pick by order",
        )
    head = heads[0]
    title_now = rec.text_sha256(thread.ticket.title)
    description_now = rec.text_sha256(thread.ticket.description)
    changed = [
        name
        for name, now, then in (
            ("title", title_now, head.title_sha256),
            ("description", description_now, head.description_sha256),
        )
        if now != then
    ]
    if changed:
        return Status(
            STALE,
            f"the ticket's {' and '.join(changed)} changed after the definition "
            f"of done was agreed (record {head.record_id})",
            head,
        )
    return Status(REFINED, f"agreed definition of done: record {head.record_id}", head)
