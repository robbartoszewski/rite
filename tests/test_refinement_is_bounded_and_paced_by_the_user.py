"""TR2: what an unrefined ticket is, and how much refinement one session may
start (the note's parts 3.1 and 3.4 step 0).

The failure being bounded is the v0.6.0 dogfood's: a supervisor spent eight
sessions in eighty seconds on tickets it could not read. And the failure being
avoided on the other side is Robert's "nothing parked" for silence: a User who
does not answer is WAITING FOR YOU, re-asked when he is back, never PARKED and
never sent a new batch of questions while he is away (TRQ11).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from rite_ai.managers.routing import _ledger_dir
from rite_ai.refinement import admit as ad
from rite_ai.refinement import rounds
from rite_ai.refinement import status as st
from rite_ai.tickets import Ticket

NOW = 1_800_000_000.0
DAY = 86_400.0
T0 = datetime(2026, 9, 1, 9, 0)


def ticket(i: int, **kw) -> Ticket:
    return Ticket(
        id=f"RT-{i}",
        title=f"ticket {i}",
        description="make it configurable or smth",
        created_at=kw.pop("created_at", T0 + timedelta(minutes=i)),
        **kw,
    )


def board(state: str, t: Ticket) -> st.Status:
    return st.Status(state, None, f"the board says {state}", t)


def attempt_on(t: Ticket, *round_list: rounds.Round, **kw) -> rounds.Attempt:
    return rounds.Attempt(
        ticket=t.id, text_sha256=rounds.text_of(t), rounds=list(round_list), **kw
    )


def sent(k=1, *, ago=0.0, proposal=False, deadline_in=DAY, **kw) -> rounds.Round:
    return rounds.Round(
        k=k,
        sent_at=NOW - ago,
        deadline=NOW - ago + deadline_in,
        proposal=proposal,
        **kw,
    )


# --- state_of: the board wins, the ledger says what an unrefined one waits for


class TestTheBoardAlwaysWins:
    @pytest.mark.parametrize("state", [st.REFINED, st.CONFLICT, st.UNREADABLE])
    def test_the_boards_answer_stands_whatever_the_ledger_says(self, state):
        t = ticket(1)
        parked = attempt_on(t, sent(), parked=rounds.NOT_AGREED)
        got = rounds.state_of(board(state, t), parked, now=NOW)
        assert got.name == state

    def test_an_attempt_on_other_text_is_over_when_the_ticket_is_edited(self):
        """A User who fixes the ticket himself has resumed it (step 7)."""
        before = ticket(1)
        parked = attempt_on(before, sent(), parked=rounds.NOT_AGREED)
        after = Ticket(id="RT-1", title="ticket 1", description="the http timeout")
        got = rounds.state_of(board(st.NOT_REFINED, after), parked, now=NOW)
        assert got.name == st.NOT_REFINED

    def test_no_ledger_is_the_boards_state(self):
        t = ticket(1)
        assert rounds.state_of(board(st.STALE, t), None, now=NOW).name == st.STALE


class TestWhatAnUnrefinedTicketWaitsFor:
    def test_a_question_out_is_asking(self):
        t = ticket(1)
        got = rounds.state_of(board(st.NOT_REFINED, t), attempt_on(t, sent()), now=NOW)
        assert got.name == rounds.ASKING and got.open and got.uses_sessions

    def test_a_proposal_out_is_proposed(self):
        t = ticket(1)
        a = attempt_on(t, sent(1, ago=DAY), sent(2, proposal=True), answered=[1])
        got = rounds.state_of(board(st.NOT_REFINED, t), a, now=NOW)
        assert got.name == rounds.PROPOSED and "round 2" in got.detail

    def test_silence_is_not_waiting_until_the_thread_was_read_past_the_deadline(
        self,
    ):
        """Race 6: a reply sent a minute before the deadline and read after
        it must still count, so the deadline alone decides nothing."""
        t = ticket(1)
        a = attempt_on(t, sent(ago=DAY + 60))
        got = rounds.state_of(board(st.NOT_REFINED, t), a, now=NOW)
        assert got.name == rounds.ASKING and "not yet read" in got.detail

    def test_silence_read_past_the_deadline_is_waiting_for_you_never_parked(self):
        t = ticket(1)
        a = attempt_on(t, sent(ago=DAY + 60, read_past_deadline=True))
        got = rounds.state_of(board(st.NOT_REFINED, t), a, now=NOW)
        assert got.name == rounds.WAITING
        assert not got.open, "waiting for the User never counts against K"
        assert not got.uses_sessions, "and never starts a session"

    def test_parked_is_said_with_its_reason(self):
        t = ticket(1)
        a = attempt_on(t, sent(), parked=rounds.THREAD_UNREADABLE)
        got = rounds.state_of(board(st.NOT_REFINED, t), a, now=NOW)
        assert (got.name, got.detail) == (rounds.PARKED, rounds.THREAD_UNREADABLE)
        assert not got.uses_sessions

    def test_silence_is_not_a_reason_to_park(self):
        """TRQ11, and Robert's "nothing parked": the three reasons are the
        only ones, and none of them is silence."""
        assert rounds.PARK_REASONS == (
            "not agreed after N rounds",
            "thread unreadable",
            "not started by the Manager",
        )


# --- admit: oldest first, K open, S per session ------------------------------


def unrefined(n: int) -> list:
    tickets = [ticket(i) for i in range(1, n + 1)]
    return [(t, rounds.State(st.NOT_REFINED, "")) for t in tickets]


class TestTheBoundsOnStarting:
    def test_twenty_unrefined_tickets_start_three_oldest_first(self):
        got = ad.admit(list(reversed(unrefined(20))), open_max=5, start_per_session=3)
        assert got.start == ["RT-1", "RT-2", "RT-3"]
        assert got.queued[:2] == ["RT-4", "RT-5"] and len(got.queued) == 17
        assert got.position("RT-4") == 1 and got.position("RT-1") == 0

    def test_the_order_is_the_boards_creation_time_then_the_id(self):
        same = T0
        items = [
            (ticket(9, created_at=same), rounds.State(st.NOT_REFINED, "")),
            (ticket(2, created_at=same), rounds.State(st.STALE, "")),
            (ticket(5, created_at=None), rounds.State(st.NOT_REFINED, "")),
            (
                ticket(7, created_at=same - timedelta(days=1)),
                rounds.State(st.STALE, ""),
            ),
        ]
        got = ad.admit(items, open_max=9, start_per_session=9)
        assert got.start == ["RT-7", "RT-2", "RT-9", "RT-5"]

    def test_open_refinements_fill_k_before_anything_new_starts(self):
        items = unrefined(6)
        for i in range(4):
            items[i] = (items[i][0], rounds.State(rounds.ASKING, "round 1"))
        got = ad.admit(items, open_max=5, start_per_session=3)
        assert got.open == ["RT-1", "RT-2", "RT-3", "RT-4"]
        assert got.start == ["RT-5"] and got.queued == ["RT-6"]

    def test_waiting_for_the_user_does_not_crowd_out_new_ones(self):
        """TRQ11: K counts only rounds still inside their deadline."""
        items = unrefined(8)
        for i in range(5):
            items[i] = (items[i][0], rounds.State(rounds.WAITING, "round 1"))
        got = ad.admit(items, open_max=5, start_per_session=3)
        assert got.waiting == ["RT-1", "RT-2", "RT-3", "RT-4", "RT-5"]
        assert got.start == ["RT-6", "RT-7", "RT-8"]

    def test_what_needs_a_person_is_said_and_never_started(self):
        t1, t2, t3, t4 = ticket(1), ticket(2), ticket(3), ticket(4)
        got = ad.admit(
            [
                (t1, rounds.State(rounds.PARKED, rounds.NOT_AGREED)),
                (t2, rounds.State(st.CONFLICT, "two heads")),
                (t3, rounds.State(st.UNREADABLE, "502")),
                (t4, rounds.State(st.REFINED, "")),
            ],
            open_max=5,
            start_per_session=3,
        )
        assert got.start == [] and not got.work
        assert set(got.needs_person) == {"RT-1", "RT-2", "RT-3"}
        assert got.ready == ["RT-4"]


class TestNewStartsArePacedByTheUser:
    """Starts are never the reason for another session: a silent User gets
    one batch of questions and no more."""

    def test_the_first_look_at_refinement_work_starts_a_session(self):
        got = ad.admit(unrefined(20), open_max=5, start_per_session=3)
        assert ad.reason_to_start(got, first_look=True, replies=0, deadlines=0)

    def test_with_no_reply_and_no_deadline_nothing_new_is_asked(self):
        got = ad.admit(unrefined(20), open_max=5, start_per_session=3)
        assert got.start, "work is admitted"
        assert ad.reason_to_start(got, first_look=False, replies=0, deadlines=0) == ""

    def test_a_reply_or_a_deadline_is_a_reason(self):
        got = ad.admit([], open_max=5, start_per_session=3)
        assert "1 reply" in ad.reason_to_start(
            got, first_look=False, replies=1, deadlines=0
        )
        assert "deadline" in ad.reason_to_start(
            got, first_look=False, replies=0, deadlines=2
        )

    def test_a_board_waiting_on_the_user_starts_no_session(self):
        items = [(ticket(1), rounds.State(rounds.WAITING, "round 1"))]
        got = ad.admit(items, open_max=5, start_per_session=3)
        assert ad.reason_to_start(got, first_look=True, replies=0, deadlines=0) == ""


# --- the ledger ---------------------------------------------------------------


def test_the_ledger_round_trips_and_quotes_an_id_into_one_file(tmp_path):
    t = Ticket(id="org/repo#12", title="x", description="y")
    a = attempt_on(t, sent(1), sent(2, proposal=True), answered=[1], misses=1)
    with rounds.locked(tmp_path, "lead", t.id) as (before, save):
        assert before is None
        save(a)
    assert rounds.load(tmp_path, "lead", t.id) == a
    assert rounds.all_attempts(tmp_path, "lead") == {t.id: a}
    files = list(_ledger_dir(tmp_path, "lead").rglob("*.json"))
    assert [f.name for f in files] == ["org%2Frepo%2312.json"]


def test_a_ledger_that_does_not_parse_is_no_attempt_never_a_state(tmp_path):
    with rounds.locked(tmp_path, "lead", "RT-1") as (_a, save):
        save(attempt_on(ticket(1), sent()))
    path = next(_ledger_dir(tmp_path, "lead").rglob("RT-1.json"))
    path.write_text("{not json")
    assert rounds.load(tmp_path, "lead", "RT-1") is None
    assert rounds.all_attempts(tmp_path, "lead") == {}


def test_the_ledger_is_outside_the_managers_boundary(tmp_path):
    """Beside the delivered-message ledger, in the directory the Manager's
    profile does not grant, so a Manager cannot answer its own question."""
    from rite_ai.managers import manager_dir

    with rounds.locked(tmp_path, "lead", "RT-1") as (_a, save):
        save(attempt_on(ticket(1), sent()))
    (path,) = _ledger_dir(tmp_path, "lead").rglob("RT-1.json")
    assert manager_dir(tmp_path, "lead") not in path.parents
    assert not path.is_relative_to(tmp_path), "and outside the project"


def test_writers_take_turns(tmp_path):
    """Race 11: the supervisor, a person's `reopen` and the relay all write.
    Each adds a round under the lock; none is lost."""
    t = ticket(1)
    with rounds.locked(tmp_path, "lead", t.id) as (_a, save):
        save(attempt_on(t))

    def add_one(k):
        with rounds.locked(tmp_path, "lead", t.id) as (a, save):
            a.rounds.append(sent(k))
            save(a)

    threads = [threading.Thread(target=add_one, args=(k,)) for k in range(1, 21)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(r.k for r in rounds.load(tmp_path, "lead", t.id).rounds) == list(
        range(1, 21)
    )
