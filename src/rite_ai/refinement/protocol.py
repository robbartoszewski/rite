"""The round protocol, run by the Owner's supervisor outside the boundary (TR2).

Robert: "the Owner asks follow-up questions immediately when it has doubts
and refines in place". This is the part rite runs, so that the part a model
runs can be wrong without a ticket being worked on a guess:

* **`send`**: the Owner's round, from `rite refine ask`, is checked again
  here against the ticket as the board has it and the answers rite
  delivered (`ask.check`; the command's own check ran inside the boundary,
  on a snapshot the Manager can write, so it is advice). It goes to the
  User through `managers.asking.raise_to_person`, the one way rite puts a
  question in front of a person, is posted on the ticket as a comment, and
  is recorded as a round with its deadline.
* **`attribute`**: a delivered message answers a round only by where it was
  written (the thread under the round, whose label carries the question's
  id) or by a leading ticket id in the DM. Never by a model's reading of it.
  Only the User's words count: the Owner's DM marked INSTRUCTION, or a
  message typed on this machine (`delivered.classify`).
* **`handle`**: an accept word under the latest proposal writes the record,
  after re-reading the ticket; anything else is an answer, kept so the next
  proposal may quote it. N rounds answered and nothing accepted is PARKED
  (not agreed after N rounds), with a notice to the User and on the ticket.

Every outcome the Owner needs to act on reaches its next instruction as a
note from rite (`telling`), and the terminal hears it too.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.refinement import ask, rounds
from rite_ai.refinement import record as rec
from rite_ai.refinement import status as st

ASKS_DIRNAME = "refine-asks"
_THREAD = re.compile(r'reply in the thread under rite\'s [^"]*"([^"]*)"')
"""The relay's header part naming the thread a reply was written in
(`slack._relay`): the root's label, whose quoted part is the first 40
characters of what rite posted. For a round that is `asking`'s first line,
`<ID> · q1a2b · Manager lead is waiting …`. Matched in the header's own
line, not by splitting it at ` · `: the label carries that separator too."""
_QUESTION = re.compile(r"\bq[0-9a-f]{4}\b")


# --- the request, written inside the boundary --------------------------------


def _asks_dir(root: Path, manager: str) -> Path:
    from rite_ai.managers import manager_dir

    return manager_dir(root, manager) / ASKS_DIRNAME


def request(root: Path, manager: str, ticket: str, text: str) -> Path:
    """`rite refine ask`'s request, in the Manager's own directory, for its
    supervisor to decide. The shape `rite route` and `rite chore` use."""
    from rite_ai.state import write_atomic

    where = _asks_dir(root, manager)
    where.mkdir(parents=True, exist_ok=True)
    path = where / f"{time.time_ns()}-{os.getpid()}.json"
    write_atomic(path, json.dumps({"ticket": ticket, "text": text}) + "\n")
    return path


def take(root: Path, manager: str) -> list[dict]:
    """Every pending request, oldest first, removed as it is read: a request
    left behind would be sent twice. One that does not parse is returned as
    `{"problem": …}` so it is said, not dropped."""
    where = _asks_dir(root, manager)
    if not where.is_dir():
        return []
    found: list[dict] = []
    for path in sorted(where.glob("*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
            path.unlink()
        except OSError:
            continue
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"ticket", "text"}:
                raise ValueError("not {ticket, text}")
            found.append({"ticket": str(data["ticket"]), "text": str(data["text"])})
        except ValueError as e:
            found.append({"problem": f"a refinement request did not parse ({e})"})
    return found


# --- sending a round ----------------------------------------------------------


@dataclass(frozen=True)
class Sent:
    ok: bool
    message: str
    question: str = ""


def deadline(sent_at: float, limits, checkins=None, zone: str = "") -> float:
    """24 hours, or the close of the User's next check-in if sooner (TRQ2):
    the window open now, or else the next one to open. It only moves the
    ticket from asking to WAITING FOR YOU, so an early one costs nothing:
    his answer still counts whenever it arrives."""
    by_hours = sent_at + limits.deadline_hours * 3600
    if checkins is None or not checkins.windows:
        return by_hours
    from rite_ai.schedule import MINUTES_PER_DAY, Moment, current_moment, in_checkin

    moment = current_moment(zone, datetime.fromtimestamp(sent_at, UTC))
    minute, weekday = moment.minute_of_day, moment.weekday
    seen_open = in_checkin(checkins, moment)
    for step in range(1, 8 * MINUTES_PER_DAY + 1):
        minute += 1
        if minute >= MINUTES_PER_DAY:
            minute, weekday = 0, (weekday + 1) % 7
        open_now = in_checkin(checkins, Moment(minute, weekday, moment.zone))
        if seen_open and not open_now:
            return min(by_hours, sent_at + step * 60)
        seen_open = seen_open or open_now
    return by_hours


def send(
    root: Path,
    owner: str,
    board,
    *,
    ticket_id: str,
    text: str,
    limits,
    now: float,
    checkins=None,
    zone: str = "",
) -> Sent:
    """Check the Owner's round, put it in front of the User, record it."""
    from rite_ai.managers import asking

    answer = st.checked(lambda t: st.status(board, t), ticket_id)
    if answer.state == st.REFINED:
        return Sent(False, f"{ticket_id} is already REFINED; there is nothing to ask")
    if answer.state not in (st.NOT_REFINED, st.STALE) or answer.ticket is None:
        return Sent(
            False,
            f"{ticket_id} is {answer.state} ({answer.detail}); a round is only "
            "sent about a ticket rite can read",
        )
    ticket = answer.ticket
    with rounds.locked(root, owner, ticket_id) as (attempt, save):
        current = rounds.text_of(ticket)
        if attempt is None or attempt.text_sha256 != current:
            attempt = rounds.Attempt(ticket=ticket_id, text_sha256=current)
        if attempt.parked:
            return Sent(
                False,
                f"{ticket_id} is PARKED ({attempt.parked}); only a person restarts "
                f"it: a reply about it, `rite refine reopen {ticket_id}`, or an "
                "edit to the ticket",
            )
        if attempt.accepted:
            return Sent(False, f"{ticket_id} was accepted; rite is writing its record")
        latest = attempt.latest
        if latest is not None and not latest.answered:
            if now < latest.deadline:
                return Sent(
                    False,
                    f"round {latest.k} for {ticket_id} is still open until "
                    f"{_when(latest.deadline)}: wait for the User's answer. One "
                    "round at a time, so he answers one message, not several",
                )
            return Sent(
                False,
                f"round {latest.k} for {ticket_id} had no answer by its deadline. "
                "It is WAITING FOR YOU: rite puts the same question in front of "
                "him again when he is next active, and nothing new is asked "
                "while he is away",
            )
        k = len(attempt.rounds) + 1
        checked = ask.check(
            text,
            k=k,
            rounds=limits.rounds,
            ticket_text=f"{ticket.title}\n{ticket.description}",
            answers=[str(a.get("words", "")) for a in attempt.answers],
        )
        if not checked.ok:
            return Sent(
                False,
                f"round {k} for {ticket_id} was not sent:\n"
                + "\n".join(f"- {p}" for p in checked.problems),
            )
        body = ask.render(
            checked.ask,
            ticket=ticket_id,
            k=k,
            rounds=limits.rounds,
            manager=owner,
            accept_words=limits.accept_words,
        )
        raised = asking.raise_to_person(
            root, owner, subject=ticket_id, raiser=f"manager:{owner}", text=body
        )
        if not raised.new:
            return Sent(
                False,
                f"this exact round is already in front of the User (question "
                f"{raised.id}); it was not sent twice",
            )
        attempt.rounds.append(
            rounds.Round(
                k=k,
                sent_at=now,
                deadline=deadline(now, limits, checkins, zone),
                proposal=bool(checked.ask.proposal),
                where=raised.id,
                items=[item.text for item in checked.ask.proposal],
            )
        )
        attempt.misses = 0
        save(attempt)
    posted = board.comment(ticket_id, body)
    shown = (
        ""
        if posted is None
        else f" It could not be posted on the ticket: {posted.message}."
    )
    return Sent(
        True,
        f"{ticket_id}: round {k} of {limits.rounds} is in front of the User as "
        f"question {raised.id}.{shown}",
        raised.id,
    )


def _when(at: float) -> str:
    return time.strftime("%a %H:%M", time.localtime(at))


# --- attributing a reply -------------------------------------------------------


@dataclass(frozen=True)
class Reply:
    """A delivered message that answers a round."""

    ticket: str
    k: int
    words: str
    message: str
    sent_at: float


def attribute(
    root: Path, owner: str, text: str, *, message: str, sent_at: float
) -> Reply | None:
    """Which round `text` answers, or None. Decided from rite's header and
    the ledger, never from what the words seem to mean (part 3.4 step 3)."""
    from rite_ai.managers import delivered

    heard = delivered.classify(text)
    if not heard.users:
        return None
    attempts = rounds.all_attempts(root, owner)
    label = _thread_label(text)
    if label is not None:
        # The question's id when the label kept it; a long ticket id can push
        # it past the 40 characters, and then the ticket id that leads the
        # label names the attempt, and its latest round.
        question = _QUESTION.search(label)
        for attempt in attempts.values():
            for r in attempt.rounds:
                if question and r.where == question.group(0):
                    return Reply(attempt.ticket, r.k, heard.words, message, sent_at)
        leading = label.split(" · ", 1)[0].strip()
        attempt = attempts.get(leading)
        if attempt is not None and attempt.rounds and not question:
            return Reply(leading, attempt.latest.k, heard.words, message, sent_at)
        return None
    first, _, rest = heard.words.strip().partition(" ")
    ticket = first.rstrip(":,.;")
    attempt = attempts.get(ticket)
    if attempt is None or not attempt.rounds or not rest.strip():
        return None
    return Reply(ticket, attempt.latest.k, rest.strip(), message, sent_at)


def _thread_label(text: str) -> str | None:
    """The quoted label of the thread `text` was written in, or None when it
    was not written in a thread under something rite posted."""
    found = _THREAD.search(text.split("\n", 1)[0])
    return found.group(1) if found else None


# --- what a reply does ---------------------------------------------------------


def is_accept(words: str, accept_words: list[str]) -> bool:
    """Exactly one accept word, whatever its case, with a trailing `.` or
    `!` allowed. "sounds fine I guess" is an answer, not a yes (step 4)."""
    word = re.sub(r"[.!]+$", "", words.strip()).casefold()
    return word in {w.casefold() for w in accept_words}


def handle(root: Path, owner: str, board, reply: Reply, *, limits) -> list[str]:
    """Record what `reply` does to its round. Returns notes for the Owner."""
    from rite_ai.managers import asking

    notes: list[str] = []
    with rounds.locked(root, owner, reply.ticket) as (attempt, save):
        if attempt is None or not attempt.rounds:
            return notes
        latest = attempt.latest
        accepting = is_accept(reply.words, limits.accept_words)
        if accepting and reply.k != latest.k:
            notes.append(
                f"{reply.ticket}: the User's `{reply.words}` was under round "
                f"{reply.k}, which round {latest.k} replaced; nothing was "
                f"recorded. Tell him: reply in round {latest.k}'s thread"
            )
            return notes
        if accepting and latest.proposal and latest.items:
            attempt.accepted = {
                "id": reply.message,
                "at": reply.sent_at,
                "k": latest.k,
            }
            latest.answered_at = reply.sent_at
            save(attempt)
            notes.extend(_write(root, owner, board, attempt, save))
            asking.settle(root, owner, latest.where)
            return notes
        # An answer, or a correction: kept, so the next round may quote it.
        if attempt.parked:
            notes.append(
                f"{reply.ticket} was PARKED ({attempt.parked}); the User's reply "
                "restarts it"
            )
            attempt.parked = ""
            attempt.misses = 0
        attempt.answers.append(
            {"id": reply.message, "words": reply.words, "at": reply.sent_at}
        )
        if reply.k == latest.k and not latest.answered:
            latest.answered_at = reply.sent_at
            asking.settle(root, owner, latest.where)
        if latest.k >= limits.rounds and latest.answered:
            attempt.parked = rounds.NOT_AGREED
            notes.append(
                f"{reply.ticket} is PARKED: not agreed after {limits.rounds} "
                "rounds. rite told the User what resumes it"
            )
            save(attempt)
            _park_notice(root, owner, board, reply.ticket, limits)
            return notes
        save(attempt)
    notes.append(
        f"{reply.ticket}: the User answered round {reply.k}. Your next round "
        "proposes a definition of done he can accept in one word, quoting his "
        "answer exactly, and asks only what is still open"
    )
    return notes


def _write(root: Path, owner: str, board, attempt, save) -> list[str]:
    """Write the accepted proposal as a record: re-read the ticket, sign,
    post, read back (step 5). Called under the attempt's lock."""
    from rite_ai.refinement import accept

    latest = attempt.latest
    before = st.checked(lambda t: st.status(board, t), attempt.ticket)
    if before.ticket is None or before.state in (st.CONFLICT, st.UNREADABLE):
        return [
            f"{attempt.ticket}: accepted, but the ticket could not be read to "
            f"write its record ({before.state}: {before.detail}). rite tries "
            "again at the next cycle"
        ]
    if rounds.text_of(before.ticket) != attempt.text_sha256:
        attempt.accepted = {}
        save(attempt)
        _tell_user(
            root,
            owner,
            f"{attempt.ticket} changed after round {latest.k} was proposed, so "
            "your accept was not recorded: it would have agreed to text you "
            "were not shown. A new round will propose against the ticket as it "
            "is now.",
        )
        return [
            f"{attempt.ticket}: the ticket changed since the proposal; the "
            "accept was not recorded and the User was told. Refine it again"
        ]
    outcome = accept.write(
        board,
        before,
        attempt.ticket,
        items=list(latest.items),
        verify=rec.NONE_AGREED,
        provenance={
            "kind": rec.ACCEPTED,
            "message": attempt.accepted["id"],
            "sent_at": datetime.fromtimestamp(attempt.accepted["at"], UTC).isoformat(
                timespec="seconds"
            ),
        },
    )
    if not outcome.ok:
        return [f"{outcome.message}. rite tries again at the next cycle"]
    attempt.accepted = {}
    save(attempt)
    return [f"{attempt.ticket} refined: record {outcome.record.record_id}"]


def retry_accepted(root: Path, owner: str, board) -> list[str]:
    """Each cycle: every accept whose record is not yet written is tried
    again, and said (step 5: PROPOSED, accepted, not yet written)."""
    notes: list[str] = []
    for ticket, attempt in rounds.all_attempts(root, owner).items():
        if not attempt.accepted:
            continue
        with rounds.locked(root, owner, ticket) as (current, save):
            if current is not None and current.accepted:
                notes.extend(_write(root, owner, board, current, save))
    return notes


def _tell_user(root: Path, owner: str, text: str) -> None:
    """A line for the User, through the Owner's outbox, for reading."""
    from rite_ai.managers.mailbox import OUTBOX, REPLY, send

    send(root, owner, OUTBOX, text, kind=REPLY)


def _park_notice(root: Path, owner: str, board, ticket: str, limits) -> None:
    """Parking posts once, to the User and on the ticket: what is missing,
    and the one thing that resumes it (part 3.4 step 7)."""
    from rite_ai.managers.mailbox import OUTBOX, QUESTION, send

    text = (
        f"{ticket} is parked: {limits.rounds} rounds did not reach a definition "
        "of done you accepted, so no work starts on it. To resume it, reply in "
        f"your DM starting with `{ticket}`, or run `rite refine reopen {ticket}`, "
        "or edit the ticket."
    )
    send(root, owner, OUTBOX, text, kind=QUESTION)
    board.comment(ticket, text)
