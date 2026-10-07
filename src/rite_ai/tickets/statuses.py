"""Whether a ticket's STATUS says the work is over (SCRUM-73).

**The failure this exists to prevent.** In the live a9 dogfood KAN-28 was
Done and its pull request merged, but it still carried the `ready-to-work`
label. rite's board brief listed it under "Ready to start", so a fresh
Manager requested a Worker on it twice — at the 03:51 restart and again at
12:49 — re-running finished work and holding a slot each time.

**Why a label could not have caught it.** `ready-to-work` is a VIEW rite
writes (`coordination.ticket_labels.READY_TO_WORK`), and `rite board` can
only ADD labels — nothing in rite removes one. So the label outlives the
work by construction, and readiness that reads only labels offers finished
tickets for ever. The status is the board's own record of being finished,
and it is the thing to read.

**Two sources, in order of authority.**

1. **The backend's own category, where it has one.** JIRA's status carries a
   `statusCategory` whose `key` is `new`, `indeterminate` or `done` —
   workflow-independent, so a board that renames "Done" to "Shipped" is
   still read correctly. `_issue_to_ticket` keeps the whole issue as
   `Ticket.metadata`, so the category is already on every ticket rite reads;
   measured against the live board 2026-10-07, under
   `metadata["fields"]["status"]["statusCategory"]["key"]`.
2. **The status name**, for backends with no category. GitHub issues have
   only open/closed, and `GitHubBackend._CLOSE_STATUSES` already names the
   statuses it reads as closed. `TERMINAL_NAMES` starts from that set and a
   test asserts it still contains it, so the two cannot drift apart
   silently. The backend's own set is left alone deliberately: it is also
   the list of names `move` accepts as a destination, and widening it here
   would quietly make `rite board move <t> duplicate` close a ticket.

⚠ **An unrecognised status is NOT terminal.** This gate decides whether work
may START, so guessing "terminal" from an unfamiliar word would park a live
ticket for ever — the opposite failure, and a worse one, because nothing
would say why. A board whose terminal column is named something rite does
not know keeps the old behaviour, and `rite doctor` is where that belongs.
A configured list of terminal statuses is a follow-up; nothing here reads
config, so nothing here is a setting somebody believes is having an effect.
"""

from __future__ import annotations

DONE_CATEGORY = "done"
"""JIRA's `statusCategory.key` for the right-hand column, whatever it is
called on a given board."""

TERMINAL_NAMES = frozenset(
    {
        "done",
        "closed",
        "close",
        "cancelled",
        "canceled",
        "resolved",
        "wont do",
        "won't do",
        "wontdo",
        "duplicate",
    }
)
"""Status NAMES that mean the work is over, lowercased.

The first five are `GitHubBackend._CLOSE_STATUSES`, which `move` and
`list_tickets` already share (and which a test holds this set against); the
rest are the JIRA defaults a board reaches for when a ticket ends without
being delivered. All of them end work, which
is the only question asked here — `duplicate` and `won't do` are not
successes, and offering either as ready is the same defect as offering
`Done`."""


def _category_of(ticket) -> str:
    """The backend's own status category for this ticket, lowercased, or "".

    Read defensively: `metadata` is whatever the backend kept, so every step
    down is checked rather than assumed. A shape this does not recognise
    answers "" and the name decides.
    """
    data = getattr(ticket, "metadata", None)
    if not isinstance(data, dict):
        return ""
    fields = data.get("fields")
    if not isinstance(fields, dict):
        return ""
    status = fields.get("status")
    if not isinstance(status, dict):
        return ""
    category = status.get("statusCategory")
    if not isinstance(category, dict):
        return ""
    key = category.get("key")
    return key.strip().lower() if isinstance(key, str) else ""


def is_terminal(ticket) -> bool:
    """Whether this ticket's status says its work is finished.

    Takes the ticket rather than the status string so the backend's category
    can be read: a name alone cannot tell a renamed Done column from a live
    one. A `None` ticket, or one with no status at all, is not terminal —
    absence of a status is not a record of being finished.
    """
    if ticket is None:
        return False
    if _category_of(ticket) == DONE_CATEGORY:
        return True
    return is_terminal_name(getattr(ticket, "status", "") or "")


def is_terminal_name(status: str) -> bool:
    """Whether a status NAME is one of the names that end work.

    Separate from `is_terminal` for the callers that hold a status and no
    ticket — a request naming a ticket id, read back as a string.
    """
    if not isinstance(status, str):
        return False
    return status.strip().lower() in TERMINAL_NAMES
