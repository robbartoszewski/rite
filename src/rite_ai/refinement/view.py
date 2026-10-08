"""The `ready-to-work` label: a view of the record, never the authority (TR7).

The note's part 3.10. A ticket carries `ready-to-work` exactly when rite's
latest read of it shows `scheduled`, REFINED, and no Manager's name: queued,
with an agreed definition of done, and not yet assigned. It lets the Owner and
the User filter "ready to be assigned" from "needs refinement" on the board
itself, without rite.

**Nothing reads it to decide anything.** Every gate reads `scheduled` and the
signed record (`status`), so a label added by hand starts nothing, and a label
removed by hand blocks nothing. rite is the only intended writer, and what it
writes is computed from one read of the ticket; the label is output, never
input. `tests/test_the_ready_label_is_a_view.py` pins both halves.

**Reconciled, never assumed correct.** The Owner's supervisor reconciles at
the start of every cycle, over `list(scheduled)` and `list(ready-to-work)`,
each member read once. A project's board already folds DF4's ledger of rite's
own recent writes into both lists (`tickets.own_writes`), so a ticket rite
labelled seconds ago is in them even while the board's list lags. The second
list finds a label left on a ticket that lost `scheduled`, or was assigned,
while no rite ran. A record write re-labels its one ticket after its read-back
(`settle`), and `rite refine sync` reconciles on demand, on the host.

**What a wrong label costs.** A hand-added one is removed, with a comment on
the ticket once per record state (found in the ticket's own thread, so there is
no ledger to lose) and a line in the Owner's instruction every time. A
hand-removed one on a REFINED ticket is put back, with no comment, because
nothing is wrong with the ticket.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rite_ai.coordination.ticket_labels import READY_TO_WORK as READY
from rite_ai.coordination.ticket_labels import SCHEDULED
from rite_ai.refinement import status as st

DESCRIPTION = (
    "Set by rite: scheduled, with an agreed definition of done. Adding it by "
    "hand does nothing."
)
"""What a person browsing the repository's labels reads (GitHub only: Jira
labels have no description). Written once, when rite first needs the label.

⚠ **At most 100 characters.** GitHub refuses a longer one with 422
("description is too long (maximum is 100 characters)": measured 2026-09-30,
101 refused and 100 accepted). The note's part 3.10 wording is 119, and the
first live run lost the description to exactly that."""

DESCRIPTION_MAX = 100

COLOUR = "0e8a16"

_TOKEN = "rite-ready-view"
"""The token a removal comment carries, with the record state it was posted
for, so the next read can tell it already said so (once per record state)."""


@dataclass(frozen=True)
class Change:
    ticket: str
    added: bool
    state: str
    """The predicate's state, from the same read that decided the change."""


@dataclass
class Reconciled:
    """One reconciliation: what changed, what could not be done, and whether
    it saw the whole board."""

    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    complete: bool = True
    statuses: dict[str, st.Status] = field(default_factory=dict)
    """Every status read, by ticket id, so a caller that needs them (the
    refinement brief) does not read the board a second time."""
    scheduled: list | None = None
    """`list(scheduled)` as this pass got it, or None when it could not, for
    the same reason."""

    @property
    def removed(self) -> list[Change]:
        return [c for c in self.changes if not c.added]

    @property
    def added(self) -> list[Change]:
        return [c for c in self.changes if c.added]

    def line(self) -> str:
        """The start line: how many labels were corrected, and whether the
        pass saw everything. "" when nothing needs saying."""
        parts = []
        if self.changes:
            parts.append(
                f"{READY}: corrected {len(self.changes)} label(s) "
                f"({len(self.added)} added, {len(self.removed)} removed)"
            )
        if not self.complete:
            parts.append(
                f"{READY}: the board's list was cut short, so this pass did not "
                "see every ticket and the label may be wrong on the rest"
            )
        parts += [f"{READY}: {p}" for p in self.problems]
        return "\n".join(parts)

    def instruction(self) -> str:
        """The lines the Owner's instruction carries: every removal, every
        time, because a Manager adding the label is worth knowing about."""
        if not self.removed:
            return ""
        lines = [
            f"- **{c.ticket}**: rite removed `{READY}`. The ticket is {c.state}, "
            "and the label is rite's view of a REFINED ticket, so adding it "
            "changes nothing. Do not add it."
            for c in self.removed
        ]
        return f"\n\n## `{READY}` labels corrected (rite)\n\n" + "\n".join(lines) + "\n"


def wanted(ticket, status: st.Status, managers=()) -> bool:
    """Whether `ticket`, as read, should carry the label: `scheduled`,
    REFINED, no Manager's name on it (not yet assigned), and **its work not
    already over** (SCRUM-73).

    ⚠ The status is the fourth condition, and the one the a9 run was missing.
    KAN-28 was Done and merged, and every other condition still held — so
    rite kept the label on finished work and its own brief offered the ticket
    again, twice. `wanted` is False here, which means `reconcile_one` TAKES
    THE LABEL OFF: the root cause said "nothing in rite removes it either",
    and this is the line that does."""
    from rite_ai.tickets.statuses import is_terminal

    if is_terminal(ticket):
        return False
    labels = set(ticket.labels or [])
    return status.refined and SCHEDULED in labels and not labels & set(managers)


def _said_already(thread, state: str) -> bool:
    marker = f"{_TOKEN}: {state}"
    return any(marker in (c.body or "") for c in thread.comments)


def _removal_comment(state: str, detail: str) -> str:
    return (
        f"rite removed `{READY}`: this ticket has no valid refinement record "
        f"(state: {state}; {detail}). The label is rite's view of an agreed "
        "definition of done, and adding it by hand starts nothing.\n\n"
        f"`{_TOKEN}: {state}`"
    )


def _describe(board) -> str:
    """Create the label with its description where the board has them, once.
    Returns "" or why it could not; either way the label write goes ahead."""
    describe = getattr(board, "describe_label", None)
    if not callable(describe):
        return ""
    done = describe(READY, DESCRIPTION, COLOUR)
    return "" if done is None else f"could not describe the label: {done.message}"


def reconcile_one(board, ticket_id: str, into: Reconciled, managers=()) -> None:
    """Read `ticket_id` once and set its label to what that read says."""
    from rite_ai.tickets import BackendError, Thread

    status, thread = st.status_and_thread(board, ticket_id)
    if not isinstance(thread, Thread):
        # Unread is left as it is: a view rite cannot recompute is not changed
        # on a guess, in either direction.
        into.problems.append(
            f"{ticket_id} could not be read ({thread.message}); label unchanged"
        )
        return
    into.statuses[ticket_id] = status
    if status.state == st.UNREADABLE:
        # Not "not refined": the record may be there and unreadable from here
        # (no key inside a sandbox, a thread shown incomplete). Stripping the
        # label on that would be a guess, so it is left, and said.
        into.problems.append(
            f"{ticket_id} is UNREADABLE ({status.detail}); label unchanged"
        )
        return
    ticket = thread.ticket
    want = wanted(ticket, status, managers)
    has = READY in (ticket.labels or [])
    if want == has:
        return
    if want:
        problem = _describe(board)
        if problem:
            into.problems.append(problem)
        written = board.label(ticket_id, [READY])
    else:
        written = board.label(ticket_id, [], remove=[READY])
    if isinstance(written, BackendError):
        into.problems.append(f"{ticket_id}: the label write failed ({written.message})")
        return
    into.changes.append(Change(ticket_id, want, status.state))
    if want or status.refined:
        # Put back, or taken off a REFINED ticket that is assigned or no longer
        # queued: nothing is wrong with the ticket, so nothing is said on it.
        return
    if _said_already(thread, status.state):
        return
    said = board.comment(ticket_id, _removal_comment(status.state, status.detail))
    if isinstance(said, BackendError):
        into.problems.append(
            f"{ticket_id}: the label was removed, but the comment saying why "
            f"could not be posted ({said.message})"
        )


def reconcile(board, managers=(), *, only=None) -> Reconciled:
    """Reconcile the label over the board, or over the ids in `only`.

    With `only` None: every ticket in `list(scheduled)` and in
    `list(ready-to-work)`, each read once. A list the board could not give,
    or gave cut short, makes the pass incomplete, and `line()` says so.
    """
    from rite_ai.tickets import BackendError, TicketFilter

    out = Reconciled()
    if only is not None:
        ids = list(dict.fromkeys(only))
    else:
        ids = []
        for label in (SCHEDULED, READY):
            listed = board.list_tickets(TicketFilter(label=label))
            if isinstance(listed, BackendError):
                out.complete = False
                out.problems.append(
                    f"the board could not list `{label}` ({listed.message})"
                )
                continue
            if getattr(listed, "truncated", False):
                out.complete = False
            if label == SCHEDULED:
                out.scheduled = list(listed)
            ids += [t.id for t in listed]
        ids = list(dict.fromkeys(ids))
    for ticket_id in ids:
        try:
            reconcile_one(board, ticket_id, out, managers)
        except Exception as e:  # noqa: BLE001 - one ticket never ends the pass
            out.problems.append(f"{ticket_id}: {type(e).__name__}: {e}")
    return out


def settle(board, ticket_id: str, managers=()) -> str:
    """Re-label one ticket after rite wrote its record or reopened it.
    Returns "" or what went wrong, for the caller to say; the record itself
    is already written and read back, so this never undoes it."""
    try:
        done = reconcile(board, managers, only=[ticket_id])
    except Exception as e:  # noqa: BLE001 - the view never fails a record write
        return f"{READY}: {type(e).__name__}: {e}"
    return done.line() if done.problems else ""


def managers_at(root) -> list[str]:
    """`managers_of` a project's own config; [] when it cannot be parsed,
    which only ever errs towards showing the label (fixed at the next cycle,
    whose config does parse)."""
    from rite_ai.config.parse import ParseError, parse_config

    config = parse_config(root / ".rite" / "config.yaml")
    return [] if isinstance(config, ParseError) else managers_of(config)


def managers_of(config) -> list[str]:
    """The Manager names in a project's config: a ticket carrying one is
    assigned, so it is not `ready-to-work`."""
    names = list(getattr(config.coordination, "managers", []) or [])
    names += [r.name for r in getattr(config.coordination, "manager_roles", []) or []]
    return list(dict.fromkeys(n for n in names if n))


@dataclass(frozen=True)
class Truth:
    """One queued ticket, from a fresh read: whether it is ready to be
    assigned, and the state that says why not."""

    ticket: object
    status: st.Status
    ready: bool


def truth(board, managers=()) -> tuple[list[Truth], str]:
    """Every `scheduled` ticket, each read once, with whether it is ready to
    be assigned: what `rite board list --ready` and `--needs-refinement`
    print. Never reads `ready-to-work`, and never writes. Returns the rows and
    "" or what makes them incomplete."""
    from rite_ai.tickets import BackendError, Thread, TicketFilter

    listed = board.list_tickets(TicketFilter(label=SCHEDULED))
    if isinstance(listed, BackendError):
        return [], f"the board could not list `{SCHEDULED}` ({listed.message})"
    rows: list[Truth] = []
    for listed_ticket in listed:
        status, thread = st.status_and_thread(board, listed_ticket.id)
        ticket = thread.ticket if isinstance(thread, Thread) else listed_ticket
        rows.append(Truth(ticket, status, wanted(ticket, status, managers)))
    cut = (
        f"the board's list of `{SCHEDULED}` was cut short; more are queued"
        if getattr(listed, "truncated", False)
        else ""
    )
    return rows, cut
