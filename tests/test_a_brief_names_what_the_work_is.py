"""SCRUM-102 — a Manager cannot take work it is only told the number of.

🔴 **Measured twice, two hours each time.** The board brief named ready
tickets by id, and a Manager's sandbox has no board credential by design. On
run `smoke_mixed-20261009T063320Z` `lead` said:

    I can't read tickets 1 or 2 (or anything else on the board) from inside my
    sandbox right now: `gh` has no auth here … the board summary I was given
    ("ready: 1, 2") is all I have: no titles, no descriptions, nothing to
    quote in a refinement round.

It asked for the content, the question went to a Slack DM this project has not
configured, and it waited. It claimed both tickets three minutes after being
told their titles by hand.

⚠ **SCRUM-80 fixed the id/count ambiguity in this same line and was not
enough.** The brief already rendered "2 ticket(s) — `1`, `2`" — unambiguous —
and the Manager still could not read what it named. SCRUM-80's own description
dismissed the Manager's journal ("rite gives a count but no identifier") as a
wrong premise; the journal was closer to right.

Three things are pinned here: the brief says WHAT each ready ticket is; it does
NOT claim a definition of done that does not exist yet; and the no-progress
hold says when an unanswered question is why nothing is moving, and when that
question can reach nobody.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rite_ai.managers.supervise import _board_brief, _unanswered_question_note


class _Answer:
    """What `LoopAnswer.of` produces, in the shape `_board_brief` reads."""

    def __init__(self, ready, titles=None, blocked=()):
        self.basis = ("ready", tuple(ready), tuple(blocked))
        self.read_at = None
        self.ready_titles = titles


# --- the brief ----------------------------------------------------------------


def test_the_brief_says_what_each_ready_ticket_IS():
    """🔴 The defect: ids only, to a Manager that cannot resolve them."""
    brief = _board_brief(
        _Answer(
            ["1", "2"],
            {
                "1": (
                    "Add tally.slugify(text) — lowercase, keep "
                    "[a-z0-9], join with dashes"
                ),
                "2": "Add tally.format_amount(value) — two places, thousands separator",
            },
        )
    )
    assert "Ready to start: 2 ticket(s)" in brief, "SCRUM-80's line is kept"
    assert "Add tally.slugify(text)" in brief
    assert "Add tally.format_amount(value)" in brief
    assert "lowercase, keep [a-z0-9]" in brief, "the body, not only the title"


def test_it_does_not_claim_a_definition_of_done_it_does_not_have():
    """⚠ The DoD is pinned at the Worker's START by the spec/definition
    session, so for a ready-and-unstarted ticket there is none. Saying "title
    + DoD" without this would invite the Manager to quote one it was never
    given."""
    brief = _board_brief(_Answer(["1"], {"1": "Add slugify — do the thing"}))
    assert "DEFINITION OF DONE is not here and does not exist yet" in brief
    assert "pinned when a Worker starts" in brief


def test_an_answer_with_no_titles_falls_back_and_invents_nothing():
    """CONTROL. An injected verdict (every existing test's) carries no titles,
    and the brief must not fabricate or crash."""
    brief = _board_brief(_Answer(["1", "2"], None))
    assert "Ready to start: 2 ticket(s) — `1`, `2`." in brief
    assert "What they are" not in brief
    assert "DEFINITION OF DONE" not in brief


def test_a_ticket_with_an_empty_title_is_not_listed_as_blank():
    brief = _board_brief(_Answer(["1", "2"], {"1": "Real title", "2": "   "}))
    assert "- `1` — Real title" in brief
    assert "- `2`" not in brief.split("What they are")[1].split("⚠")[0]


def test_the_headline_caps_the_body_but_never_the_title():
    from rite_ai.loop import HEADLINE_BODY_CHARS, _headline

    class T:
        id = "1"
        title = (
            "A title that is long but must survive intact because "
            "truncating it is how an id became ambiguous"
        )
        description = "x" * (HEADLINE_BODY_CHARS * 3)

    line = _headline(T())
    assert T.title in line, "a title is short by nature; never cut it"
    assert "[…]" in line
    assert len(line) < len(T.title) + HEADLINE_BODY_CHARS + 40


# --- the hold says when a question is the reason ------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_MAIL_DIR", str(tmp_path / "mail"))
    (tmp_path / "mail").mkdir()
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n")
    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    return root


def _ask(root: Path, slack_user: str = "") -> None:
    from rite_ai.managers import pending
    from rite_ai.managers.mailbox import OUTBOX, send

    cfg = "ticket_backend:\n  type: none\n"
    if slack_user:
        cfg += f"slack:\n  owner_user: {slack_user}\n"
    (root / ".rite" / "config.yaml").write_text(cfg)
    # The ledger first, so the question is not recorded as predating it.
    pending.sync(root, "lead", now=time.time())
    send(root, "lead", OUTBOX, "I cannot read tickets 1 or 2", kind="question")
    pending.sync(root, "lead", now=time.time())


def test_a_question_with_no_reachable_channel_says_so_loudly(project):
    """🔴 The silent-park mechanism: `questions_to: dm` with no Slack user, so
    the question reached nobody and the hold said nothing about it."""
    _ask(project)
    note = _unanswered_question_note(project, "lead")
    assert "waiting on YOU" in note
    assert "CAN REACH NOBODY" in note
    assert "THIS IS WHY NOTHING IS MOVING" in note
    assert "rite replies lead" in note, "and what to do about it"


def test_a_reachable_channel_is_not_cried_wolf_over(project):
    """CONTROL: with Slack configured the question can arrive, so the note
    says answering it is the move — not that nothing can deliver it."""
    _ask(project, slack_user="U123")
    note = _unanswered_question_note(project, "lead")
    assert "waiting on YOU" in note
    assert "CAN REACH NOBODY" not in note
    assert "Answering it is what moves this on" in note


def test_no_outstanding_question_says_nothing(project):
    """CONTROL: a Manager that simply did nothing must not be reported as
    blocked on a question. Those are the two cases the hold could not tell
    apart, and conflating them the other way is just as wrong."""
    (project / ".rite" / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    assert _unanswered_question_note(project, "lead") == ""


def test_it_never_raises_on_an_unreadable_ledger(project, monkeypatch):
    """The hold has to print its line whatever the ledger does."""
    _ask(project)
    monkeypatch.setattr(
        "rite_ai.managers.pending.items",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unreadable")),
    )
    assert _unanswered_question_note(project, "lead") == ""


# --- the WIRING, which nothing guarded ----------------------------------------


def test_plan_cycle_actually_collects_the_headlines(tmp_path):
    """🔴 Found by a surviving mutation of my own fix: with the collection
    replaced by `{}`, every test above still passed. `_board_brief` was tested
    with titles INJECTED and `_headline` was tested directly, and nothing
    asserted the wire between them — which is the dead-wiring shape rite keeps
    a whole guard for.

    So this drives the real `plan_cycle` against a real board read."""
    from datetime import UTC, datetime

    from rite_ai.loop import plan_cycle
    from rite_ai.tickets import Ticket
    from tests.refined_board import refined

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    # An open schedule, or the cycle answers `closed` before it reads a board.
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "schedule:\n  timezone: UTC\n  windows:\n    - hours: '00:00-23:59'\n"
        "      workers: 2\n"
    )

    class Board:
        def list_tickets(self, _filter=None):
            return [
                Ticket(
                    id="1",
                    title="Add tally.slugify(text)",
                    status="To Do",
                    labels=["scheduled", "ready-to-work"],
                    description="For the GPU Worker. Add slugify(text): lowercase.",
                )
            ]

    def _free(_name, _root):
        class S:
            known = True
            running = False
            busy = False
            stopped = False

        return S()

    now = 1_759_000_000.0
    cycle = plan_cycle(
        tmp_path,
        board=Board(),
        clock=now,
        now=datetime.fromtimestamp(now, UTC),
        refinement=refined,
        sandbox_status=_free,
    )
    assert "1" in cycle.ready, cycle.detail
    assert cycle.ready_titles.get("1"), "the headline must be collected here"
    assert "Add tally.slugify(text)" in cycle.ready_titles["1"]
    assert "lowercase" in cycle.ready_titles["1"], "the body too"
