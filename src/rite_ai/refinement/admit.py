"""Which unrefined tickets the Owner refines this session, and which wait (TR2).

Under Robert's semantics a `scheduled` ticket that is NOT REFINED or STALE is
refinement work for the Owner, not a refusal. Unbounded, that is the failure
the v0.6.0 dogfood already showed: a supervisor spent eight sessions in
eighty seconds on tickets it could not read. So the work is bounded four
ways, each enforced here rather than asked of the model (the note's part 3.4
step 0):

* **Order**: oldest first, by the board's `created` time, id as tie-break.
  Both come from the same read, so the order is deterministic.
* **At most K open at once** (`open_max`, 5): a round inside its deadline.
  A ticket beyond K stays NOT REFINED and is listed as "queued for
  refinement, position n". WAITING FOR YOU does not count (TRQ11).
* **At most S new starts per Owner session** (`start_per_session`, 3).
* **New starts are paced by the User.** A session is started FOR refinement
  only on the first cycle that finds refinement work, or on a cycle in which
  an attributed reply has arrived or a round's deadline has passed. Starts
  are never the reason for another session, so a silent User gets one batch
  of questions and no more, and twenty unrefined tickets mean three
  conversations, not twenty.

The fourth bound, the no-progress guard, is `rounds.MISSES_TO_PARK`: a ticket
handed over twice without a round being started is PARKED.

Pure: it reads nothing and writes nothing, so every bound is testable without
a board, and the supervisor and `rite refine status` cannot disagree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from rite_ai.refinement import rounds
from rite_ai.refinement import status as st

NEEDS_STARTING = (st.NOT_REFINED, st.STALE)


@dataclass
class Admission:
    """The Owner's refinement work for one session, and why the rest waits."""

    open: list[str] = field(default_factory=list)
    """In refinement now (ASKING, PROPOSED): listed for the Owner every time."""
    start: list[str] = field(default_factory=list)
    """NOT REFINED or STALE, admitted to start this session."""
    queued: list[str] = field(default_factory=list)
    """NOT REFINED or STALE, behind K or S, in order."""
    waiting: list[str] = field(default_factory=list)
    """WAITING FOR YOU: no sessions, no new question, not counted against K."""
    needs_person: dict[str, str] = field(default_factory=dict)
    """PARKED, CONFLICT, UNREADABLE: why, for each. No sessions."""
    ready: list[str] = field(default_factory=list)
    """REFINED: the Owner assigns these (TR5), and the loop counts them."""
    texts: dict[str, str] = field(default_factory=dict)
    """The text hash each ticket in `start` was admitted with: what the
    no-progress guard counts a miss against (`rounds.count_misses`)."""

    @property
    def work(self) -> bool:
        """Refinement a session could do now."""
        return bool(self.open or self.start)

    def position(self, ticket: str) -> int:
        """1-based place in the refinement queue, or 0."""
        return self.queued.index(ticket) + 1 if ticket in self.queued else 0


def _natural(ticket_id: str) -> tuple:
    """`RT-2` before `RT-10`: the numbers in an id compared as numbers, the
    order a person reading the board expects."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", ticket_id)
        if part
    )


def _order(item) -> tuple:
    ticket, _state = item
    created = getattr(ticket, "created_at", None)
    # A ticket whose board gave no creation time sorts after those that have
    # one, by id: deterministic, and never ahead of a ticket known older.
    return (
        created is None,
        created or datetime.min,
        _natural(str(ticket.id)),
    )


def admit(
    tickets: list[tuple[object, rounds.State]],
    *,
    open_max: int,
    start_per_session: int,
) -> Admission:
    """Sort every `scheduled` ticket into what this session does with it.

    `tickets` is each ticket as the board returned it with its state
    (`rounds.state_of`). Whether a session starts at all is `reason_to_start`.
    """
    out = Admission()
    in_order = sorted(tickets, key=_order)
    for ticket, state in in_order:
        if state.name == st.REFINED:
            out.ready.append(ticket.id)
        elif state.open:
            out.open.append(ticket.id)
        elif state.name == rounds.WAITING:
            out.waiting.append(ticket.id)
        elif state.name in NEEDS_STARTING:
            pass
        else:
            out.needs_person[ticket.id] = f"{state.name}: {state.detail}"
    room = max(0, open_max - len(out.open))
    allowed = min(room, start_per_session)
    for ticket, state in in_order:
        if state.name not in NEEDS_STARTING:
            continue
        if len(out.start) < allowed:
            out.start.append(ticket.id)
            out.texts[ticket.id] = rounds.text_of(ticket)
        else:
            out.queued.append(ticket.id)
    return out


def reason_to_start(
    admission: Admission, *, first_look: bool, replies: int, deadlines: int
) -> str:
    """Why a session starts for refinement this cycle, or "" (step 0).

    `first_look`: no refinement session has run yet in this run. `replies`
    and `deadlines`: attributed replies arrived, and round deadlines passed,
    since the last refinement session. Starting new refinements is never a
    reason on its own: that is how a silent User would be sent a new batch
    of questions every cycle.
    """
    if replies:
        return f"{replies} repl{'y' if replies == 1 else 'ies'} to refinement arrived"
    if deadlines:
        return f"{deadlines} refinement round(s) reached their deadline"
    if first_look and admission.work:
        return "refinement work is waiting and none has started in this run"
    return ""
