"""TR2: a refinement round's thread is read until he answers it, and his
silence is decided by reading it, not by the clock (the note's part 4, races 6
and 7).

Race 7: the relay reads at most ten threads, newest first, and drops those
older than a day. A busy Owner posting ten replies would silently stop
reading an open round's thread, and whether an answer was lost would depend
on how much else was posted. RP1 pins a question until it is SEEN, and a
reaction counts as seen; for a round that is not enough, so an open round
keeps its thread read until it is ANSWERED.

Race 6: at the deadline a ticket becomes WAITING FOR YOU only once its thread
was read past the deadline with nothing new in it, so a reply sent a minute
before the deadline and read after it still answers the round.
"""

from __future__ import annotations

from rite_ai.managers import asking
from rite_ai.managers.slack import THREADS_MAX
from rite_ai.refinement import rounds
from rite_ai.refinement import status as st
from rite_ai.tickets import Ticket
from tests.test_what_needs_you_stays_pending import (
    DAY,
    OWNER,
    Clock,
    Slack,
    _project,
    _read_threads,
    _relay,
)

TICKET = Ticket(id="KAN-7", title="timout is way too long", description="smth")


def _open_round(root, clock, *, deadline_in: float):
    """A round as `protocol.send` leaves it: asked through `asking`, and
    recorded against that question's id."""
    raised = asking.raise_to_person(
        root,
        "lead",
        subject="KAN-7",
        raiser="manager:lead",
        text="Refinement of KAN-7, round 1 of 3\n\n1. Which timeout?",
    )
    with rounds.locked(root, "lead", "KAN-7") as (_a, save):
        save(
            rounds.Attempt(
                ticket="KAN-7",
                text_sha256=rounds.text_of(TICKET),
                rounds=[
                    rounds.Round(
                        k=1,
                        sent_at=clock.now,
                        deadline=clock.now + deadline_in,
                        proposal=False,
                        where=raised.id,
                    )
                ],
            )
        )
    return raised.id


def _thread_of(listener, question):
    (root,) = [r for r in listener.roots if question in r.label]
    return root


def _state(root, now):
    board = st.Status(st.NOT_REFINED, None, "", TICKET)
    return rounds.state_of(board, rounds.load(root, "lead", "KAN-7"), now=now)


def test_a_seen_but_unanswered_round_outlives_threads_max_and_the_horizon(
    tmp_path,
):
    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    question = _open_round(root, clock, deadline_in=10 * DAY)
    listener.post_replies(call=slack)
    thread = _thread_of(listener, question)
    # He reacts: RP1 counts it SEEN and stops pinning it for that reason.
    slack.reacted[("D1", thread.ts)] = [{"name": "eyes", "users": [OWNER]}]
    _read_threads(listener, slack, clock, rounds=5)
    # More ordinary threads than the relay reads: a busy Owner's replies.
    for n in range(THREADS_MAX + 2):
        listener.remember("D1", f"{2000 + n}.0", f"rite's reply at 10:{n:02d}")
    assert sum(1 for r in listener.roots if not r.item) > THREADS_MAX
    clock.now += 3 * DAY
    assert any(r.ts == thread.ts for r in listener.roots), "the round's thread"
    slack.replies[("D1", thread.ts)] = [
        {"user": OWNER, "text": "the http one", "ts": f"{clock.now}"}
    ]
    got = _read_threads(listener, slack, clock, rounds=20)
    assert any("> the http one" in m for m in got), "his answer, days later"


def test_silence_read_past_the_deadline_is_waiting_for_you(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    _open_round(root, clock, deadline_in=600)
    listener.post_replies(call=slack)
    assert _state(root, clock.now + 700).detail.endswith("not yet read past it")
    clock.now += 700
    _read_threads(listener, slack, clock, rounds=3)
    assert _state(root, clock.now).name == rounds.WAITING
    assert any("no answer by its deadline" in line for line in listener.news())


def test_a_reply_sent_before_the_deadline_and_read_after_it_answers(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    question = _open_round(root, clock, deadline_in=600)
    listener.post_replies(call=slack)
    thread = _thread_of(listener, question)
    slack.replies[("D1", thread.ts)] = [
        {"user": OWNER, "text": "the http one", "ts": f"{clock.now + 540}"}
    ]
    clock.now += 700  # read only after the deadline
    got = _read_threads(listener, slack, clock, rounds=1)
    assert any("> the http one" in m for m in got)
    attempt = rounds.load(root, "lead", "KAN-7")
    assert not attempt.latest.read_past_deadline, "the read found his reply"


def test_before_the_deadline_nothing_is_marked(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    _open_round(root, clock, deadline_in=DAY)
    listener.post_replies(call=slack)
    _read_threads(listener, slack, clock, rounds=5)
    assert _state(root, clock.now).name == rounds.ASKING
    assert not rounds.load(root, "lead", "KAN-7").latest.read_past_deadline


def test_the_ledger_itself_refuses_to_call_silence_before_the_deadline(tmp_path):
    """Not only the relay's check: whoever marks a round read past its
    deadline, a read before the deadline marks nothing."""
    root, clock = _project(tmp_path), Clock(1000.0)
    question = _open_round(root, clock, deadline_in=600)
    assert not rounds.read_past_deadline(root, "lead", question, at=clock.now + 599)
    assert rounds.read_past_deadline(root, "lead", question, at=clock.now + 600)
    assert not rounds.read_past_deadline(root, "lead", "q0000", at=clock.now + 900)


# --- he is back ----------------------------------------------------------------


def _waiting_round(root, clock, body="Refinement of KAN-7, round 1 of 3\n\n1. Which?"):
    question = _open_round(root, clock, deadline_in=600)
    with rounds.locked(root, "lead", "KAN-7") as (a, save):
        a.latest.body = body
        save(a)
    assert rounds.read_past_deadline(root, "lead", question, at=clock.now + 700)
    return question


def _from_him(root, text):
    from rite_ai.managers import mailbox

    mailbox.send(root, "lead", mailbox.INBOX, text)
    return mailbox.take(root, "lead", mailbox.INBOX)


def _questions(root):
    from rite_ai.managers import mailbox

    return [
        m for m in mailbox.read(root, "lead", mailbox.OUTBOX) if m.kind == "question"
    ]


def test_when_he_is_back_the_waiting_question_comes_back_once(tmp_path):
    from rite_ai.refinement import protocol

    root, clock = _project(tmp_path), Clock(1000.0)
    _waiting_round(root, clock)
    assert len(_questions(root)) == 1
    lines = protocol.step(
        root, "lead", None, lambda _m: None, messages=_from_him(root, "morning")
    )
    assert any("put the same question in front of him again" in x for x in lines)
    again = _questions(root)
    assert len(again) == 2 and again[1].text.split("\n", 1)[1] == (
        "Refinement of KAN-7, round 1 of 3\n\n1. Which?"
    ), "unchanged, and the same round"
    protocol.step(
        root, "lead", None, lambda _m: None, messages=_from_him(root, "still here")
    )
    assert len(_questions(root)) == 2, "once, not a reminder"
    assert rounds.load(root, "lead", "KAN-7").latest.k == 1, "no round spent"


def test_a_message_that_is_not_his_is_not_him_being_back(tmp_path):
    from rite_ai.refinement import protocol

    root, clock = _project(tmp_path), Clock(1000.0)
    _waiting_round(root, clock)
    note = "[from rite · about something · sent Tue 10:00]\nnothing from him"
    protocol.step(root, "lead", None, lambda _m: None, messages=_from_him(root, note))
    assert len(_questions(root)) == 1


def test_a_round_still_inside_its_deadline_is_not_repeated(tmp_path):
    from rite_ai.refinement import protocol

    root, clock = _project(tmp_path), Clock(1000.0)
    _open_round(root, clock, deadline_in=DAY)
    with rounds.locked(root, "lead", "KAN-7") as (a, save):
        a.latest.body = "Refinement of KAN-7, round 1 of 3\n\n1. Which?"
        save(a)
    protocol.step(root, "lead", None, lambda _m: None, messages=_from_him(root, "hi"))
    assert len(_questions(root)) == 1


def test_silence_is_said_once_not_on_every_read(tmp_path):
    root, slack, clock = _project(tmp_path), Slack(), Clock(1000.0)
    listener = _relay(root, slack, clock)
    _open_round(root, clock, deadline_in=600)
    listener.post_replies(call=slack)
    clock.now += 700
    _read_threads(listener, slack, clock, rounds=10)
    said = [x for x in listener.news() if "no answer by its deadline" in x]
    assert len(said) == 1, said
