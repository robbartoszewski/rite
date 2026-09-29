"""TR2: an instruction the User gives in chat is refined straight away, and if
he does not reply in time it is filed as a chore with exactly his words,
visibly unrefined, to be refined later. Nothing parked, nothing invented.

Robert (TRQ11): "In most cases, I think the Owner should ask follow up
questions (if it has any doubts) and refine straight away. If the User
doesn't reply within some timeout, create a chore with what it's got and then
refine later".
"""

from __future__ import annotations

from rite_ai.config.models import RefinementConfig
from rite_ai.managers import delivered, mailbox, slack
from rite_ai.refinement import protocol, rounds
from rite_ai.refinement import status as st
from tests.test_the_owner_refines_with_the_user import Board, key, relayed_in_thread
from tests.test_the_owner_routes_to_other_managers import project

__all__ = ["key", "project"]  # fixtures, used by name below

LIMITS = RefinementConfig(chore_after_minutes=60)
WORDS = "export the invoices as CSV\nthe monthly ones"
NOW = 1_800_000_000.0

PROPOSE = """\
Proposal:
- A CSV export of invoices [ticket: "export the invoices as CSV"]
- Monthly invoices only [ticket: "the monthly ones"]
- The columns are the ones the invoice page shows [proposed]
"""


def _his_instruction(root) -> str:
    """He asks for it in his DM; rite delivers it and keeps his words."""
    mailbox.send(
        root,
        "lead",
        mailbox.INBOX,
        slack._header("Owner's DM", "sent Tue 09:00", "addressed", "INSTRUCTION")
        + "\n"
        + slack._quoted(WORDS),
    )
    got = mailbox.take(root, "lead", mailbox.INBOX)
    delivered.record(root, "lead", got)
    return protocol.MESSAGE + delivered.message_id(got[0].path)


def _ask(root, board, key_, text=PROPOSE, now=NOW):
    return protocol.send(
        root, "lead", board, ticket_id=key_, text=text, limits=LIMITS, now=now
    )


def _reply(root, board, words, *, sent_at):
    (question,) = [
        m for m in mailbox.read(root, "lead", mailbox.OUTBOX) if m.kind == "question"
    ][-1:]
    text = relayed_in_thread(question.text.splitlines()[0], words)
    reply = protocol.attribute(root, "lead", text, message="m7", sent_at=sent_at)
    return protocol.handle(root, "lead", board, reply, limits=LIMITS)


def _file(root, board, now):
    return protocol.file_unanswered(root, "lead", board, limits=LIMITS, now=now)


def test_a_round_about_his_message_quotes_his_words_and_touches_no_board(project, key):
    board = Board()
    key_ = _his_instruction(project)
    sent = _ask(project, board, key_)
    assert sent.ok, sent.message
    assert getattr(board, "created", []) == [], "nothing is filed yet"
    bad = _ask(project, board, key_, text='Proposal:\n- a PDF export [ticket: "PDF"]\n')
    assert not bad.ok


def test_silence_files_it_unrefined_with_exactly_his_words_once(project, key):
    board = Board()
    key_ = _his_instruction(project)
    assert _ask(project, board, key_).ok
    early = protocol.file_unanswered(
        project, "lead", board, limits=LIMITS, now=NOW + 3599
    )
    assert early == [] and getattr(board, "created", []) == []
    lines = protocol.file_unanswered(
        project, "lead", board, limits=LIMITS, now=NOW + 3600
    )
    (chore,) = board.created
    assert any("unrefined, because he did not reply in time" in x for x in lines)
    first, rest = chore.description.split("\n\n", 1)
    assert first.startswith("**Unrefined when rite created it (")
    assert rest.startswith(WORDS + "\n\n---"), "exactly his words"
    assert chore.labels == ["chore", "scheduled"]
    told = [
        m for m in mailbox.read(project, "lead", mailbox.OUTBOX) if m.kind == "reply"
    ]
    assert told and f"rite filed it as {chore.id}" in told[-1].text
    # Refinement goes on, on the chore: same round, same thread.
    assert rounds.load(project, "lead", key_) is None
    moved = rounds.load(project, "lead", chore.id)
    assert moved.latest.k == 1 and moved.latest.items
    assert st.status(board, chore.id).state == st.NOT_REFINED
    # Exactly once.
    assert _file(project, board, NOW + 9000) == []
    assert len(board.created) == 1


def test_his_ok_later_refines_the_filed_chore(project, key):
    """The thread he was asked in still works after the chore is filed, and
    even though the board trimmed the description rite gave it."""
    board = Board()
    key_ = _his_instruction(project)
    _ask(project, board, key_)
    _file(project, board, NOW + 3600)
    (chore,) = board.created
    notes = _reply(project, board, "ok", sent_at=NOW + 7200)
    assert any(n.startswith(f"{chore.id} refined: record ") for n in notes), notes
    assert st.status(board, chore.id).state == st.REFINED


def test_his_ok_before_the_timeout_files_it_agreed(project, key):
    board = Board()
    key_ = _his_instruction(project)
    _ask(project, board, key_)
    notes = _reply(project, board, "ok", sent_at=NOW + 600)
    (chore,) = board.created
    assert chore.description.startswith("**Agreed with the User before rite created it")
    got = st.status(board, chore.id)
    assert got.state == st.REFINED and got.record.provenance["message"] == "m7"
    assert any("refined: record" in n for n in notes), notes


def test_an_answer_is_not_silence(project, key):
    board = Board()
    key_ = _his_instruction(project)
    _ask(project, board, key_, text="Questions:\n1. Which invoices?\n")
    _reply(project, board, "the monthly ones", sent_at=NOW + 60)
    assert _file(project, board, NOW + 7200) == []


def test_a_crash_while_filing_is_reported_never_filed_twice(project, key):
    board = Board()
    key_ = _his_instruction(project)
    _ask(project, board, key_)
    with rounds.locked(project, "lead", key_) as (a, save):
        a.filing = NOW + 3600  # rite began, and never heard back
        save(a)
    (line,) = protocol.file_unanswered(
        project, "lead", board, limits=LIMITS, now=NOW + 7200
    )
    assert "may already exist" in line and f"rite refine reopen {key_}" in line
    assert getattr(board, "created", []) == []


def test_a_message_that_is_not_his_cannot_be_refined(project, key):
    sent = _ask(project, Board(), protocol.MESSAGE + "1790000000000_1_000000000000")
    assert not sent.ok and "not a User's instruction" in sent.message
