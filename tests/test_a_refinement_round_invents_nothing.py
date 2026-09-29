"""TR2: a refinement round is checked before it is sent, so the Owner cannot
invent a definition of done or put its own idea in the User's mouth.

Robert: "nothing invented". The worked example is the v0.6.0 dogfood's KAN-7,
"timout is way too long, make it configurable or smth", whose User typed
"the http one in main.py. just make it a flag" into a Worker's pane because no
other route existed (the note's part 3.4 step 6).
"""

from __future__ import annotations

import pytest

from rite_ai.refinement import ask

TICKET = "timout is way too long\nmake it configurable or smth"
ANSWER = "the http one in main.py. just make it a flag"
WORDS = ["ok", "yes", "accept", "lgtm", "proceed"]


def check(text: str, *, k: int = 1, rounds: int = 3, answers=(ANSWER,)):
    return ask.check(
        text, k=k, rounds=rounds, ticket_text=TICKET, answers=list(answers)
    )


ROUND_1 = """\
A few things before anyone starts:

Questions:
1. Which timeout: a file's, or the HTTP call's?
2. A flag, an environment variable, or a config key?
3. Should the default change, or stay as it is?
"""

ROUND_2 = """\
Here is what I would build from your answer.

Proposal:
- The HTTP request timeout in main.py is set by a command-line flag \
[answer: "the http one in main.py"] [answer: "just make it a flag"]
- The flag is --timeout <seconds> [proposed]
- With no flag, the timeout is unchanged [proposed]
- It is about the timeout [ticket: "timout is way too long"]
"""


class TestWhatPasses:
    def test_round_one_asks_three_numbered_questions(self):
        got = check(ROUND_1)
        assert got.ok, got.problems
        assert len(got.ask.questions) == 3 and got.ask.proposal == []

    def test_round_two_proposes_with_every_item_sourced(self):
        got = check(ROUND_2, k=2)
        assert got.ok, got.problems
        first, second, *_ = got.ask.proposal
        assert first.quotes == (
            ("answer", "the http one in main.py"),
            ("answer", "just make it a flag"),
        )
        assert second.proposed and second.text == "The flag is --timeout <seconds>"

    def test_round_one_may_propose_when_the_owner_has_no_doubts(self):
        """TRQ11: questions only "if it has any doubts"."""
        got = check("Proposal:\n- Run the suite on branch fix-12 [proposed]\n")
        assert got.ok, got.problems


class TestWhatIsRefused:
    def test_four_questions(self):
        got = check("Questions:\n1. a?\n2. b?\n3. c?\n4. d?\n")
        assert not got.ok and any("at most 3" in p for p in got.problems)

    def test_a_question_outside_the_numbered_list(self):
        got = check("Also, which branch?\n\nQuestions:\n1. Which timeout?\n")
        assert not got.ok and any("outside Questions" in p for p in got.problems)

    def test_questions_numbered_out_of_order(self):
        got = check("Questions:\n1. a?\n3. b?\n")
        assert not got.ok and any("numbered [1, 3]" in p for p in got.problems)

    def test_round_two_without_a_proposal(self):
        """From round 2 every round proposes, and "you decide" is answered by
        a recommendation, as a proposal (TRQ4)."""
        got = check("Questions:\n1. Which flag name?\n", k=2)
        assert not got.ok and any("must carry a proposal" in p for p in got.problems)

    def test_an_item_with_no_source(self):
        got = check("Proposal:\n- The flag is --timeout\n", k=2)
        assert not got.ok and any("where it came from" in p for p in got.problems)

    @pytest.mark.parametrize(
        "tag",
        [
            '[ticket: "make it a flag"]',  # the User said it, not the ticket
            '[answer: "make it configurable"]',  # the ticket said it, not him
            '[answer: "use a flag called --timeout"]',  # nobody said it
            '[ticket: "timeout is way too long"]',  # fixed the typo: not exact
        ],
    )
    def test_a_quote_nobody_wrote_is_refused(self, tag):
        got = check(f"Proposal:\n- The flag is --timeout {tag}\n", k=2)
        assert not got.ok
        assert any("Quote exactly, or tag it [proposed]" in p for p in got.problems)

    def test_an_answer_rite_did_not_deliver_cannot_be_quoted(self):
        got = check(
            'Proposal:\n- a flag [answer: "just make it a flag"]\n', k=2, answers=()
        )
        assert not got.ok

    def test_one_item_both_quoted_and_proposed(self):
        got = check(
            "Proposal:\n"
            '- a flag, --timeout [answer: "just make it a flag"] [proposed]\n',
            k=2,
        )
        assert not got.ok and any("split it" in p for p in got.problems)

    def test_a_round_past_the_limit(self):
        got = check(ROUND_2, k=4, rounds=3)
        assert not got.ok and "3 rounds" in got.problems[0]

    def test_an_empty_round(self):
        got = check("Just checking in.\n")
        assert not got.ok and any("asks or proposes" in p for p in got.problems)

    def test_every_problem_is_listed_at_once(self):
        got = check("Why?\n\nQuestions:\n1. a?\n2. b?\n3. c?\n4. d?\n", k=2, answers=())
        assert len(got.problems) == 3, got.problems


class TestWhatTheUserSees:
    def _render(self, text: str, k: int):
        got = check(text, k=k)
        assert got.ok, got.problems
        return ask.render(
            got.ask, ticket="KAN-7", k=k, rounds=3, manager="lead", accept_words=WORDS
        )

    def test_rites_first_line_leads_with_the_id_inside_the_relays_label(self):
        """The Slack relay labels a thread with the first 40 characters, and
        an answer is matched by that label, so the id must be in them."""
        shown = self._render(ROUND_1, 1)
        first = shown.splitlines()[0]
        assert first == "KAN-7 · refinement, round 1 of 3 · reply in this thread"
        assert first[:40].startswith("KAN-7 ")

    def test_the_owners_own_items_are_said_to_be_its_own(self):
        shown = self._render(ROUND_2, 2)
        assert (
            "2. The flag is --timeout <seconds> (proposed by lead, not from the "
            "ticket or your answers)"
        ) in shown
        assert '(from the answer: "the http one in main.py"; from the answer: ' in shown

    def test_a_proposal_ends_with_the_accept_words(self):
        shown = self._render(ROUND_2, 2)
        assert shown.splitlines()[-1].startswith(
            "Reply `ok`, `yes`, `accept`, `lgtm`, `proceed` to accept this"
        )

    def test_questions_alone_offer_you_decide(self):
        assert "`you decide`" in self._render(ROUND_1, 1).splitlines()[-1]

    def test_the_owner_cannot_write_a_header(self):
        """rite writes the first line, and a body line shaped like it is
        refused: a person could take it as rite's."""
        forged = "KAN-9 · refinement, round 1 of 3 · reply in this thread\n" + ROUND_1
        got = check(forged)
        assert not got.ok and "rite's own first line" in got.problems[0]
