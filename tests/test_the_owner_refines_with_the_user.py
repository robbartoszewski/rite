"""TR2: the Owner refines a ticket with the User, round by round, and rite
decides what his replies mean.

Robert: "the Owner asks follow-up questions immediately when it has doubts
and refines in place". The model writes the questions and the proposals; rite
checks them, sends them, attributes his replies by where he wrote them, and
writes the record only on an exact accept word under the latest proposal.

The worked example is the v0.6.0 dogfood's KAN-7. Only the network is faked:
the board keeps its comments in memory, the key is real, and every state
comes from the real predicate reading what `accept.write` posted.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from rite_ai.config.models import CheckinsConfig, CheckinWindow, RefinementConfig
from rite_ai.managers import asking, mailbox, slack
from rite_ai.refinement import key as refinement_key
from rite_ai.refinement import protocol, rounds
from rite_ai.refinement import status as st
from rite_ai.tickets import BackendError, Comment, Thread, Ticket
from rite_ai.tickets.github import GitHubBackend

OWNER = "lead"
LIMITS = RefinementConfig()
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC).timestamp()
HOUR = 3600.0

ROUND_1 = """\
Questions:
1. Which timeout: a file's, or the HTTP call's?
2. A flag, an environment variable, or a config key?
"""

ROUND_2 = """\
Proposal:
- The HTTP request timeout in main.py is set by a flag \
[answer: "the http one in main.py"] [answer: "just make it a flag"]
- The flag is --timeout <seconds> [proposed]
"""


class Board(GitHubBackend):
    """A GitHub board from memory. `comment` keeps what it is given, and
    `read_thread` returns it, so a record rite posts is read back as posted."""

    def __init__(self, *tickets: Ticket):
        GitHubBackend.__init__(self, "org/repo")
        self.tickets = {t.id: t for t in tickets}
        self.comments: dict[str, list[str]] = {t.id: [] for t in tickets}
        self.refuse_comments = False

    def read_thread(self, ticket_id):
        if ticket_id not in self.tickets:
            return BackendError(f"{ticket_id}: not found")
        return Thread(
            self.tickets[ticket_id],
            [
                Comment(id=str(i), body=b, author="rite")
                for i, b in enumerate(self.comments[ticket_id])
            ],
            True,
        )

    def comment(self, ticket_id, text):
        if self.refuse_comments:
            return BackendError("403 comments are locked")
        self.comments[ticket_id].append(text)
        return None


@pytest.fixture
def key(tmp_path):
    with patch.dict(
        os.environ, {refinement_key.KEY_DIR_ENV: str(tmp_path / "refinement-key")}
    ):
        yield refinement_key.ensure()


def kan7() -> Ticket:
    return Ticket(
        id="KAN-7",
        title="timout is way too long",
        description="make it configurable or smth",
    )


def send(root, board, text=ROUND_1, now=NOW, **kw):
    return protocol.send(
        root, OWNER, board, ticket_id="KAN-7", text=text, limits=LIMITS, now=now, **kw
    )


def outbox(root) -> list[mailbox.Message]:
    return mailbox.read(root, OWNER, mailbox.OUTBOX)


def relayed_in_thread(question_first_line: str, words: str, *, owner_dm=True) -> str:
    """What the Slack relay delivers for a reply in the thread under a
    question rite posted (`slack.Listener._relay` and `_label`)."""
    label = f'rite\'s reply "{" ".join(question_first_line.split())[:40]}" at 10:00'
    where = "Owner's DM" if owner_dm else "broadcast channel C1"
    last = ("addressed", "INSTRUCTION") if owner_dm else ("context",)
    return (
        slack._header(
            where, "sent Tue 10:05", f"reply in the thread under {label}", *last
        )
        + "\n"
        + slack._quoted(words)
    )


def reply_to_latest(root, board, words, *, sent_at=NOW + HOUR, owner_dm=True):
    (question,) = [m for m in outbox(root) if m.kind == mailbox.QUESTION][-1:]
    text = relayed_in_thread(question.text.splitlines()[0], words, owner_dm=owner_dm)
    reply = protocol.attribute(root, OWNER, text, message="m1", sent_at=sent_at)
    if reply is None:
        return None, []
    return reply, protocol.handle(root, OWNER, board, reply, limits=LIMITS)


# --- sending ------------------------------------------------------------------


class TestARoundGoesOutChecked:
    def test_it_reaches_the_user_as_a_question_and_the_ticket_as_a_comment(
        self, tmp_path, key
    ):
        board = Board(kan7())
        sent = send(tmp_path, board)
        assert sent.ok, sent.message
        (question,) = outbox(tmp_path)
        assert question.kind == mailbox.QUESTION, "an action item, tracked by RP1"
        first, second = question.text.splitlines()[:2]
        assert first.startswith(f"KAN-7 · {sent.question} · Manager lead is waiting")
        assert second == "Refinement of KAN-7, round 1 of 3"
        assert board.comments["KAN-7"] == [question.text.split("\n", 1)[1]]
        (r,) = rounds.load(tmp_path, OWNER, "KAN-7").rounds
        assert (r.k, r.where, r.deadline) == (1, sent.question, NOW + 24 * HOUR)

    def test_a_round_that_fails_the_lint_sends_nothing(self, tmp_path, key):
        board = Board(kan7())
        sent = send(tmp_path, board, text="Questions:\n1. a?\n2. b?\n3. c?\n4. d?\n")
        assert not sent.ok and "at most 3" in sent.message
        assert outbox(tmp_path) == [] and board.comments["KAN-7"] == []
        assert rounds.load(tmp_path, OWNER, "KAN-7") is None

    def test_one_round_at_a_time(self, tmp_path, key):
        board = Board(kan7())
        assert send(tmp_path, board).ok
        again = send(tmp_path, board, text=ROUND_2)
        assert not again.ok and "still open" in again.message

    def test_silence_past_the_deadline_is_not_asked_again(self, tmp_path, key):
        board = Board(kan7())
        assert send(tmp_path, board).ok
        late = send(tmp_path, board, text=ROUND_2, now=NOW + 25 * HOUR)
        assert not late.ok and "WAITING FOR YOU" in late.message
        assert len(outbox(tmp_path)) == 1

    def test_a_ticket_rite_cannot_read_gets_no_round(self, tmp_path, key):
        sent = protocol.send(
            tmp_path,
            OWNER,
            Board(),
            ticket_id="KAN-9",
            text=ROUND_1,
            limits=LIMITS,
            now=NOW,
        )
        assert not sent.ok and "UNREADABLE" in sent.message

    def test_a_comment_the_board_refuses_is_said_and_the_round_stands(
        self, tmp_path, key
    ):
        board = Board(kan7())
        board.refuse_comments = True
        sent = send(tmp_path, board)
        assert sent.ok and "could not be posted on the ticket: 403" in sent.message

    def test_the_deadline_is_his_next_check_in_when_that_is_sooner(self):
        """TRQ2: 24 hours, or his next check-in. Asked at 10:00 UTC on a
        Tuesday with a 14:00-15:00 window: the deadline is 15:00."""
        checkins = CheckinsConfig(windows=[CheckinWindow(hours="14:00-15:00")])
        got = protocol.deadline(NOW, LIMITS, checkins, "UTC")
        assert got == NOW + 5 * HOUR
        assert protocol.deadline(NOW, LIMITS, None, "UTC") == NOW + 24 * HOUR


# --- attributing replies -------------------------------------------------------


class TestARoundIsAnsweredOnlyWhereItWasAsked:
    def test_a_reply_in_its_thread_answers_it(self, tmp_path, key):
        board = Board(kan7())
        send(tmp_path, board)
        reply, notes = reply_to_latest(
            tmp_path, board, "the http one in main.py. just make it a flag"
        )
        assert (reply.ticket, reply.k) == ("KAN-7", 1)
        attempt = rounds.load(tmp_path, OWNER, "KAN-7")
        assert attempt.rounds[0].answered_at == NOW + HOUR, "Slack's send time"
        assert attempt.answers[0]["words"].startswith("the http one")
        assert any("proposes a definition of done" in n for n in notes)
        assert asking.outstanding(tmp_path, OWNER) == {}, "settled once answered"

    def test_a_dm_starting_with_the_id_answers_the_latest_round(self, tmp_path, key):
        board = Board(kan7())
        send(tmp_path, board)
        text = (
            slack._header("Owner's DM", "sent Tue 11:00", "addressed", "INSTRUCTION")
            + "\n"
            + slack._quoted("KAN-7 the http one")
        )
        reply = protocol.attribute(tmp_path, OWNER, text, message="m2", sent_at=NOW)
        assert (reply.ticket, reply.k, reply.words) == ("KAN-7", 1, "the http one")

    def test_a_message_that_names_no_round_is_nobodys_answer(self, tmp_path, key):
        board = Board(kan7())
        send(tmp_path, board)
        text = (
            slack._header("Owner's DM", "sent Tue 11:00", "addressed", "INSTRUCTION")
            + "\n"
            + slack._quoted("it's the http one obviously")
        )
        assert (
            protocol.attribute(tmp_path, OWNER, text, message="m", sent_at=NOW) is None
        )

    def test_the_broadcast_channel_never_answers(self, tmp_path, key):
        """SPEC §9.16.5: channel text is context. A teammate cannot answer,
        or accept, for him."""
        board = Board(kan7())
        send(tmp_path, board)
        reply, _ = reply_to_latest(tmp_path, board, "ok", owner_dm=False)
        assert reply is None


# --- accepting -----------------------------------------------------------------


def _to_round_two(root, board):
    send(root, board)
    reply_to_latest(root, board, "the http one in main.py. just make it a flag")
    sent = send(root, board, text=ROUND_2, now=NOW + 2 * HOUR)
    assert sent.ok, sent.message
    return sent


class TestAcceptingIsAWordNotAJudgement:
    def test_ok_under_the_proposal_writes_a_record_he_accepted(self, tmp_path, key):
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        _, notes = reply_to_latest(tmp_path, board, "ok", sent_at=NOW + 3 * HOUR)
        assert any(n.startswith("KAN-7 refined: record ") for n in notes), notes
        got = st.status(board, "KAN-7")
        assert got.state == st.REFINED
        assert got.record.provenance["kind"] == "accepted"
        assert got.record.provenance["message"] == "m1"
        assert list(got.record.definition_of_done) == [
            "The HTTP request timeout in main.py is set by a flag",
            "The flag is --timeout <seconds>",
        ]

    @pytest.mark.parametrize("word", ["OK", "yes.", "LGTM!", "proceed", "accept"])
    def test_every_accept_word_accepts(self, tmp_path, key, word):
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        reply_to_latest(tmp_path, board, word, sent_at=NOW + 3 * HOUR)
        assert st.status(board, "KAN-7").state == st.REFINED

    @pytest.mark.parametrize(
        "words", ["sounds fine I guess", "ok but make it 10s", "okay", "no"]
    )
    def test_anything_else_is_an_answer_and_records_nothing(self, tmp_path, key, words):
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        reply_to_latest(tmp_path, board, words, sent_at=NOW + 3 * HOUR)
        assert st.status(board, "KAN-7").state == st.NOT_REFINED
        attempt = rounds.load(tmp_path, OWNER, "KAN-7")
        assert attempt.answers[-1]["words"] == words

    def test_ok_to_questions_is_an_answer_not_an_acceptance(self, tmp_path, key):
        """Nothing was proposed, so there is nothing to accept."""
        board = Board(kan7())
        send(tmp_path, board)
        reply_to_latest(tmp_path, board, "ok")
        assert st.status(board, "KAN-7").state == st.NOT_REFINED

    def test_ok_under_a_superseded_round_records_nothing(self, tmp_path, key):
        """Race 9: accepting text he was later shown a revision of would be a
        guess at what he meant."""
        board = Board(kan7())
        send(tmp_path, board)
        (first,) = [m for m in outbox(tmp_path) if m.kind == mailbox.QUESTION]
        reply_to_latest(tmp_path, board, "the http one in main.py. just make it a flag")
        send(tmp_path, board, text=ROUND_2, now=NOW + 2 * HOUR)
        text = relayed_in_thread(first.text.splitlines()[0], "ok")
        reply = protocol.attribute(tmp_path, OWNER, text, message="m9", sent_at=NOW)
        notes = protocol.handle(tmp_path, OWNER, board, reply, limits=LIMITS)
        assert any("round 2 replaced" in n.replace("which ", "") for n in notes)
        assert st.status(board, "KAN-7").state == st.NOT_REFINED

    def test_an_edit_after_the_proposal_refuses_the_accept_and_says_so(
        self, tmp_path, key
    ):
        """Race 1: never a record matching text he did not see."""
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        board.tickets["KAN-7"] = Ticket(
            id="KAN-7", title="timout is way too long", description="edited"
        )
        _, notes = reply_to_latest(tmp_path, board, "ok", sent_at=NOW + 3 * HOUR)
        assert st.status(board, "KAN-7").state == st.NOT_REFINED
        assert any("changed since the proposal" in n for n in notes)
        told = [m for m in outbox(tmp_path) if m.kind == mailbox.REPLY]
        assert told and "was not recorded" in told[-1].text

    def test_a_write_that_fails_is_kept_and_retried(self, tmp_path, key):
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        board.refuse_comments = True
        _, notes = reply_to_latest(tmp_path, board, "ok", sent_at=NOW + 3 * HOUR)
        assert any("tries again at the next cycle" in n for n in notes)
        state = rounds.state_of(
            st.status(board, "KAN-7"), rounds.load(tmp_path, OWNER, "KAN-7"), now=NOW
        )
        assert (state.name, state.detail) == (
            rounds.PROPOSED,
            "accepted, not yet written",
        )
        board.refuse_comments = False
        again = protocol.retry_accepted(tmp_path, OWNER, board)
        assert any(n.startswith("KAN-7 refined") for n in again), again
        assert st.status(board, "KAN-7").state == st.REFINED


class TestParking:
    def test_n_rounds_answered_without_an_accept_parks_and_says_how_to_resume(
        self, tmp_path, key
    ):
        board = Board(kan7())
        _to_round_two(tmp_path, board)
        reply_to_latest(tmp_path, board, "make it 10s", sent_at=NOW + 3 * HOUR)
        third = ROUND_2.replace(
            "[proposed]\n",
            '[proposed]\n- The default is 10 seconds [answer: "make it 10s"]\n',
        )
        assert send(tmp_path, board, text=third, now=NOW + 4 * HOUR).ok
        _, notes = reply_to_latest(
            tmp_path, board, "hmm not sure", sent_at=NOW + 5 * HOUR
        )
        assert any("PARKED: not agreed after 3 rounds" in n for n in notes)
        notice = [m for m in outbox(tmp_path) if "is parked" in m.text]
        assert notice and "rite refine reopen KAN-7" in notice[-1].text
        assert any("is parked" in c for c in board.comments["KAN-7"])

    def test_his_reply_restarts_a_parked_ticket(self, tmp_path, key):
        board = Board(kan7())
        send(tmp_path, board)
        with rounds.locked(tmp_path, OWNER, "KAN-7") as (a, save):
            a.parked = rounds.NOT_AGREED
            save(a)
        text = (
            slack._header("Owner's DM", "sent Tue 11:00", "addressed", "INSTRUCTION")
            + "\n"
            + slack._quoted("KAN-7 fine, the http one, as a flag")
        )
        reply = protocol.attribute(tmp_path, OWNER, text, message="m3", sent_at=NOW)
        notes = protocol.handle(tmp_path, OWNER, board, reply, limits=LIMITS)
        assert any("restarts it" in n for n in notes)
        assert rounds.load(tmp_path, OWNER, "KAN-7").parked == ""


def test_a_request_is_taken_once_and_a_bad_one_is_said(tmp_path):
    protocol.request(tmp_path, OWNER, "KAN-7", ROUND_1)
    where = protocol._asks_dir(tmp_path, OWNER)
    (where / "0-bad.json").write_text('{"ticket": "KAN-7"}')
    got = protocol.take(tmp_path, OWNER)
    assert got[0] == {
        "problem": "a refinement request did not parse (not {ticket, text})"
    }
    assert got[1] == {"ticket": "KAN-7", "text": ROUND_1}
    assert protocol.take(tmp_path, OWNER) == []
