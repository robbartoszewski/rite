"""`rite refine accept`: a person attests a definition of done at the host.

**TRQ10, decided 2026-09-29: "Allow it."** Any session running as the person,
outside every boundary, may run this, and the record it writes says
`attested`. It never says "confirmed by the User": rite cannot tell the
person from a model running as the person (the note's part 3.6; a terminal
prompt was measured NOT to be a barrier, so there is none). The Slack DM
stays the strong path, and its records say `accepted` (TR2).

**What stops a Manager or a Worker running this is the key** (`key.py`): a
sandbox cannot read it, so `ensure` fails there, and nothing is written.

**Written, then read back, and only then reported.** The record is signed
against the title and description from ONE read, posted as a comment, and
the ticket is read again: success is reported only when that read says
REFINED with this record at the head. A board that altered the record on
the way (Jira's comment round trip is unmeasured, TR0) is reported as a
failure, never as success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st

_DOD_HEADING = re.compile(r"^\s*(#+\s*|\*\*)?\s*definition of done\b", re.I)
_HEADING = re.compile(r"^\s*(#+\s+\S|\*\*[^*]+\*\*\s*$|h[1-6]\.\s)")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*\S)\s*$")


def as_written(description: str) -> list[str]:
    """The items under the ticket's own "Definition of done" heading, or []
    when it has none. Used by `--as-written` (TRQ6): the person accepts what
    the ticket already says, word for word, and rite invents nothing."""
    items: list[str] = []
    inside = False
    for line in description.splitlines():
        if _DOD_HEADING.match(line):
            inside = True
            continue
        if inside and _HEADING.match(line):
            break
        if inside:
            m = _ITEM.match(line)
            if m:
                items.append(m.group(1))
    return items


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    record: rec.Record | None = None


def accept(
    root,
    config,
    ticket_id: str,
    *,
    items: list[str],
    verify: list[str],
    scope_in: list[str] | None = None,
    scope_out: list[str] | None = None,
    use_ticket_text: bool = False,
    role: str = "workers",
    now: datetime | None = None,
) -> Outcome:
    board = st.board_for(root, config, role=role)
    if isinstance(board, st.Status):
        return Outcome(False, f"{ticket_id}: {board.detail}")
    before = st.status(board, ticket_id)
    if before.state in (st.UNREADABLE, st.CONFLICT) or before.ticket is None:
        return Outcome(
            False,
            f"{ticket_id}: {before.state} — {before.detail}. Nothing was written: "
            "a record is only added to a ticket whose records can be read",
        )
    if use_ticket_text:
        if items:
            return Outcome(False, "give --as-written or --item, not both")
        items = as_written(before.ticket.description)
        if not items:
            return Outcome(
                False,
                f"{ticket_id}'s description has no 'Definition of done' section "
                "with list items, so there is nothing to accept as written. "
                "Pass each item with --item",
            )
    if not items:
        return Outcome(False, "no definition of done: pass --item, or --as-written")
    try:
        key = refinement_key.ensure()
    except OSError as e:
        return Outcome(
            False,
            f"cannot sign here: {e}. A record can only be written outside every "
            "sandbox, where rite's refinement key is readable",
        )
    when = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    # ⚠ No hostname, and nothing else about this machine. The record is
    # posted to the board, which may be public; a machine's name is the same
    # kind of leak the publish gate refuses a home path for. Found when the
    # first real round trip (dogfood board issue #44) posted `mac.home`. The
    # time is enough to find an attested approval later (TRQ10).
    provenance = {
        "kind": rec.ATTESTED,
        "at": when,
        "as_written": use_ticket_text,
    }
    try:
        record = rec.build(
            ticket=ticket_id,
            board=rec.board_identity(board),
            title=before.ticket.title,
            description=before.ticket.description,
            definition_of_done=items,
            verify=verify or rec.NONE_AGREED,
            provenance=provenance,
            supersedes=before.record.record_id if before.record else None,
            key=key,
            scope_in=scope_in,
            scope_out=scope_out,
        )
    except ValueError as e:
        return Outcome(False, f"not a valid definition of done: {e}")
    posted = board.comment(ticket_id, rec.render(record))
    if posted is not None:
        return Outcome(
            False, f"{ticket_id}: the record could not be posted: {posted.message}"
        )
    after = st.status(board, ticket_id)
    if after.state != st.REFINED or after.record is None:
        return Outcome(
            False,
            f"{ticket_id}: the record was posted, but reading it back says "
            f"{after.state} — {after.detail}. It is NOT refined",
            record,
        )
    if after.record.record_id != record.record_id:
        return Outcome(
            False,
            f"{ticket_id}: reading back found record {after.record.record_id} at "
            f"the head, not the one just written ({record.record_id}). It is "
            "refined, but not by this acceptance: check the ticket",
            after.record,
        )
    return Outcome(
        True,
        f"{ticket_id}: REFINED — record {record.record_id}, attested by a session "
        "running as you (not confirmed through your channel)",
        record,
    )
