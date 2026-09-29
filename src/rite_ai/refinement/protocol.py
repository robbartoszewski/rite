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
MESSAGE = "message-"
SUBJECT_OF_A_MESSAGE = "your message"
"""The key of an attempt about a chat instruction that is not a ticket yet:
`message-<id>`, the id shown beside it in the Owner's instruction."""
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
    """Check the Owner's round, put it in front of the User, record it.

    `ticket_id` is a ticket, or `message-<id>` for a chat instruction that is
    not a ticket yet (TRQ11): its text is then his words as rite delivered
    them (`delivered`), and nothing is posted on a board."""
    from rite_ai.managers import asking

    if ticket_id.startswith(MESSAGE):
        words, why = instruction_words(root, owner, ticket_id)
        if why:
            return Sent(False, why)
        subject_text, current, on_board = words, rec.text_sha256(words), False
    else:
        answer = st.checked(lambda t: st.status(board, t), ticket_id)
        if answer.state == st.REFINED:
            return Sent(
                False, f"{ticket_id} is already REFINED; there is nothing to ask"
            )
        if answer.state not in (st.NOT_REFINED, st.STALE) or answer.ticket is None:
            return Sent(
                False,
                f"{ticket_id} is {answer.state} ({answer.detail}); a round is "
                "only sent about a ticket rite can read",
            )
        ticket = answer.ticket
        subject_text = f"{ticket.title}\n{ticket.description}"
        current, on_board = rounds.text_of(ticket), True
    with rounds.locked(root, owner, ticket_id) as (attempt, save):
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
            ticket_text=subject_text,
            answers=[str(a.get("words", "")) for a in attempt.answers],
        )
        if not checked.ok:
            return Sent(
                False,
                f"round {k} for {ticket_id} was not sent:\n"
                + "\n".join(f"- {p}" for p in checked.problems),
            )
        same, since = _unchanged_for(attempt, checked.ask)
        body = ask.render(
            checked.ask,
            ticket=ticket_id,
            k=k,
            manager=owner,
            accept_words=limits.accept_words,
            unchanged=ask.unchanged_line(same, since) if same > 1 else "",
        )
        # The subject leads the thread label, which the relay cuts at 40
        # characters, and an answer is matched by the question id after it.
        # A message id is long enough to push that id out, so a round about
        # his message is labelled "your message".
        raised = asking.raise_to_person(
            root,
            owner,
            subject=SUBJECT_OF_A_MESSAGE if on_board is False else ticket_id,
            raiser=f"manager:{owner}",
            text=body,
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
                body=body,
            )
        )
        attempt.misses = 0
        save(attempt)
    posted = board.comment(ticket_id, body) if on_board else None
    shown = (
        ""
        if posted is None
        else f" It could not be posted on the ticket: {posted.message}."
    )
    return Sent(
        True,
        f"{ticket_id}: round {k} is in front of the User as "
        f"question {raised.id}.{shown}",
        raised.id,
    )


def _unchanged_for(attempt, current) -> tuple[int, int]:
    """(how many rounds in a row, this one included, carry the same proposal,
    the round it first appeared in). (1, k) when it changed or there is none.

    The safeguard for uncapped rounds: no progress is made visible, never
    enforced. A proposal that does not change is a PROPERTY of the rounds,
    not a guess at whether the talk is going anywhere."""
    k = len(attempt.rounds) + 1
    if not current.proposal:
        return 1, k
    now = ask.normalised_items(i.text for i in current.proposal)
    same, since = 1, k
    for r in reversed(attempt.rounds):
        if not r.proposal or ask.normalised_items(r.items) != now:
            break
        same, since = same + 1, r.k
    return same, since


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
            attempt.unanswered = 0
            attempt.accepted = {
                "id": reply.message,
                "at": reply.sent_at,
                "k": latest.k,
            }
            latest.answered_at = reply.sent_at
            save(attempt)
            if attempt.ticket.startswith(MESSAGE):
                notes.extend(_file(root, owner, board, attempt, save, agreed=True))
            else:
                notes.extend(_write(root, owner, board, attempt, save))
            asking.settle(root, owner, latest.where)
            return notes
        # An answer, or a correction: kept, so the next round may quote it.
        # Any reply resets the unanswered count, a qualified accept and an
        # answer that raises new questions included: the cap is on nudging
        # without a reply, not on a discussion (Robert, correcting TRQ2).
        attempt.unanswered = 0
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
        f"{ticket} is parked: rite asked about it {limits.unanswered} times in a "
        "row with no reply, so it has stopped asking, and no work starts on it. "
        f"To bring it back, reply in your DM starting with `{ticket}`, or run "
        f"`rite refine reopen {ticket}`, or edit the ticket."
    )
    send(root, owner, OUTBOX, text, kind=QUESTION)
    board.comment(ticket, text)


# --- a chat instruction becomes a chore -----------------------------------------


def instruction_words(root: Path, owner: str, key: str) -> tuple[str, str]:
    """(his words, "") for `message-<id>`, or ("", why not). Only a message
    rite delivered as the User's (`delivered`) has words to refine."""
    from rite_ai.managers import delivered

    entries, why = delivered.lookup(root, owner, [key[len(MESSAGE) :]])
    if why:
        return "", why
    return str(entries[0]["words"]), ""


def _file(root: Path, owner: str, board, attempt, save, *, agreed: bool) -> list[str]:
    """File a chat instruction as a chore with exactly his words, and move
    its refinement onto it (TRQ11; the note's part 3.14). Under the
    attempt's lock. `agreed`: he accepted a proposal, so the record is
    written on the new ticket at once; otherwise it is visibly unrefined.

    Exactly once: `filing` is saved before the board is asked. A crash in
    between leaves it set with no ticket, and that is reported and waits for
    a person, never filed again, because a silent duplicate is worse than a
    question.
    """
    from rite_ai.managers import chores, delivered
    from rite_ai.tickets import BackendError, Ticket

    key = attempt.ticket
    if attempt.filing:
        return [
            f"a chore for your message {key[len(MESSAGE) :]} may already exist "
            f"(rite began filing it at {_when(attempt.filing)} and did not hear "
            "back). Check the board, then `rite refine reopen "
            f"{key}` to try again"
        ]
    entries, why = delivered.lookup(root, owner, [key[len(MESSAGE) :]])
    if why:
        return [f"{key}: not filed: {why}"]
    if board is None:
        return [f"{key}: not filed: this supervisor has no board"]
    attempt.filing = time.time()
    save(attempt)
    title = chores._title_of(entries[0]["words"])  # noqa: SLF001
    description = chores._description_of(  # noqa: SLF001
        entries, attempt.filing, agreed=agreed
    )
    try:
        made = board.create(title, description, labels=list(chores.LABELS))
    except Exception as e:  # noqa: BLE001 - said, never raised
        made = BackendError(str(e))
    if isinstance(made, BackendError) or not getattr(made, "id", ""):
        said = made.message if isinstance(made, BackendError) else "no id came back"
        return [
            f"{key}: the board did not confirm the chore ({said}). It may exist "
            f"without its labels: check the board, then `rite refine reopen {key}`"
        ]
    ticket_id = str(made.id)
    # The text as the BOARD keeps it, read back: a board that trims or
    # re-wraps what it was given would otherwise make the moved attempt's
    # hash differ from the ticket's, and his accept be refused as an edit.
    kept = st.checked(lambda t: st.status(board, t), ticket_id).ticket or Ticket(
        id=ticket_id, title=title, description=description
    )
    moved = rounds.Attempt(
        ticket=ticket_id,
        text_sha256=rounds.text_of(kept),
        rounds=attempt.rounds,
        answers=attempt.answers,
        accepted=attempt.accepted,
    )
    rounds.move(root, owner, key, moved)
    if not agreed:
        _tell_user(
            root,
            owner,
            f"So your message is not lost, rite filed it as {ticket_id}, with "
            "exactly your words, unrefined: no work starts on it until you "
            "agree what done means. The question above still stands.",
        )
        return [
            f"{ticket_id}: filed from the User's message, unrefined, because "
            "he did not reply in time. Refinement continues on it"
        ]
    with rounds.locked(root, owner, ticket_id) as (current, save_moved):
        return [f"{ticket_id}: filed from the User's message. "] + _write(
            root, owner, board, current, save_moved
        )


def file_unanswered(root: Path, owner: str, board, *, limits, now: float) -> list[str]:
    """Every chat instruction whose latest round he has not answered after
    `chore_after_minutes` is filed as an unrefined chore with exactly his
    words (TRQ11: "create a chore with what it's got and then refine
    later"). Nothing is lost, nothing is blocked, nothing is invented."""
    lines: list[str] = []
    due = limits.chore_after_minutes * 60
    for key, attempt in rounds.all_attempts(root, owner).items():
        latest = attempt.latest
        if (
            not key.startswith(MESSAGE)
            or attempt.parked
            or attempt.accepted
            or latest is None
            or latest.answered
            or now - latest.sent_at < due
        ):
            continue
        with rounds.locked(root, owner, key) as (current, save):
            if current is None or current.latest is None or current.latest.answered:
                continue
            lines.extend(_file(root, owner, board, current, save, agreed=False))
    return lines


# --- he is back -----------------------------------------------------------------


def nudge(
    root: Path, owner: str, *, at: float, limits, checkins=None, zone: str = ""
) -> list[str]:
    """He is back: every question that reached its deadline unanswered goes
    in front of him again, unchanged, in the same round, with a new
    deadline (part 3.4 step 7). "Back" is an event rite observed (a message
    from him was delivered), never a model's guess, so he is never nudged
    while he is away. Each such message counts toward `unanswered` when it
    too goes unanswered (`rounds.read_past_deadline`), and
    `park_unanswered` stops at the cap. Returns lines for the Owner."""
    from rite_ai.managers import asking

    lines: list[str] = []
    for ticket, attempt in rounds.all_attempts(root, owner).items():
        latest = attempt.latest
        if (
            attempt.parked
            or latest is None
            or latest.answered
            or not latest.read_past_deadline
            or attempt.unanswered >= limits.unanswered
            or not latest.body
        ):
            continue
        with rounds.locked(root, owner, ticket) as (current, save):
            r = current.latest if current is not None else None
            if r is None or r.where != latest.where or not r.read_past_deadline:
                continue
            # Settled first: `asking` raises a question once, and this is the
            # same question, deliberately raised again.
            asking.settle(root, owner, r.where)
            raised = asking.raise_to_person(
                root,
                owner,
                subject=SUBJECT_OF_A_MESSAGE if ticket.startswith(MESSAGE) else ticket,
                raiser=f"manager:{owner}",
                text=r.body,
            )
            r.where = raised.id
            r.presented_again_at = at
            r.read_past_deadline = False
            r.deadline = deadline(at, limits, checkins, zone)
            save(current)
            count = current.unanswered
        lines.append(
            f"{ticket}: round {latest.k} had no reply by its deadline ({count} "
            f"of {limits.unanswered} unanswered); he is back, so rite put the "
            "same question in front of him again. Nothing new was asked"
        )
    return lines


def park_unanswered(root: Path, owner: str, board, *, limits) -> list[str]:
    """A ticket whose last `refinement.unanswered` messages all went
    unanswered is PARKED: rite stops asking (Robert, correcting TRQ2)."""
    lines: list[str] = []
    for ticket, attempt in rounds.all_attempts(root, owner).items():
        if attempt.parked or attempt.unanswered < limits.unanswered:
            continue
        with rounds.locked(root, owner, ticket) as (current, save):
            if current is None or current.parked:
                continue
            current.parked = rounds.NOT_ANSWERED
            save(current)
        if board is not None and not ticket.startswith(MESSAGE):
            _park_notice(root, owner, board, ticket, limits)
        lines.append(
            f"{ticket} is PARKED: {limits.unanswered} messages in a row went "
            "unanswered. rite told the User how to bring it back"
        )
    return lines


# --- one pass, from the supervisor ---------------------------------------------


def step(root: Path, manager: str, board, say, *, messages=(), now=None) -> list[str]:
    """One pass of refinement for `manager`'s supervisor.

    Sends the rounds it asked for, retries accepts whose record is not yet
    written, and attributes `messages` (just delivered to it) to rounds.
    Returns lines for THIS cycle's instruction: what his replies did. What
    happened to a round sent is a note in its NEXT instruction, since the
    turn that asked has usually ended by then.

    Only the Manager that refines does any of this (TRQ7): the one holding
    `route`, or a lone Manager. Another's requests are discarded, and said.
    """
    from rite_ai.config.managers import routing_owner
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.managers import delivered
    from rite_ai.managers.telling import tell_manager

    config = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(config, ParseError):
        say(f"refinement: config.yaml did not parse ({config.message})")
        return []
    roles = list(config.coordination.manager_roles)
    owner = routing_owner(roles) if roles else manager
    asks = take(root, manager)
    if manager != owner:
        if asks:
            say(
                f"{manager!r} asked for {len(asks)} refinement round(s) and does "
                "not refine (the Owner does); discarded, nothing was sent"
            )
        return []

    def tell(text: str) -> None:
        say(f"refinement: {text}")
        try:
            tell_manager(root, owner, "refinement", text)
        except OSError as e:
            say(f"could not tell {owner!r} about refinement: {e}")

    now = time.time() if now is None else now
    for ask_ in asks:
        if "problem" in ask_:
            tell(ask_["problem"])
            continue
        if board is None:
            tell(
                f"round for {ask_['ticket']} not sent: this supervisor has no "
                "board to check the ticket on"
            )
            continue
        sent = send(
            root,
            owner,
            board,
            ticket_id=ask_["ticket"],
            text=ask_["text"],
            limits=config.refinement,
            now=now,
            checkins=config.checkins,
            zone=config.schedule.timezone,
        )
        tell(sent.message)
    if board is not None:
        for line in retry_accepted(root, owner, board):
            tell(line)
        for line in file_unanswered(
            root, owner, board, limits=config.refinement, now=now
        ):
            # Said, and not a note in the Owner's inbox: a note is mail, and
            # mail starts a session, which filing a chore does not need.
            say(f"refinement: {line}")
    lines: list[str] = []
    for message in messages:
        reply = attribute(
            root,
            owner,
            message.text,
            message=delivered.message_id(message.path),
            sent_at=message.timestamp,
        )
        if reply is None:
            continue
        if board is None:
            lines.append(
                f"{reply.ticket}: a reply to round {reply.k} arrived, and this "
                "supervisor has no board to record it on"
            )
            continue
        lines.extend(handle(root, owner, board, reply, limits=config.refinement))
    lines.extend(park_unanswered(root, owner, board, limits=config.refinement))
    # After attributing: his message may itself have answered a waiting round.
    if any(delivered.classify(m.text).users for m in messages):
        lines.extend(
            nudge(
                root,
                owner,
                at=now,
                limits=config.refinement,
                checkins=config.checkins,
                zone=config.schedule.timezone,
            )
        )
    for line in lines:
        say(f"refinement: {line}")
    return lines
