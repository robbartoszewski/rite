"""TR2: a board of unrefined tickets is refinement work, then a wait on the
User, and never `idle`.

Robert's third verdict: "work exists, none of it can start, refinement is
next. Both existing words are false there." `idle` ends a run, so a board
whose `scheduled` tickets were all unrefined would have stopped the Owner
before it refined anything (the note's part 3.4 step 8). And `ready` would
start sessions that can do nothing. So:

* `refining`: the Owner has refinement it may start now: its first look, or
  the User replied, or a round's deadline passed since the last refinement
  session. A session starts.
* `waiting-on-user`: nothing can start and the User has done nothing since.
  The supervisor waits and spends nothing.
* `idle` only when nothing is `scheduled` at all.

Every ticket's state comes through the real predicate over real signed
records (`Backlog`); only the network is faked.
"""

from __future__ import annotations

from rite_ai.loop import IDLE, READY, REFINING, WAITING_ON_USER
from rite_ai.refinement import rounds
from rite_ai.refinement import status as refinement_status
from tests.test_loop_dry_run import NOW, _free, plan_cycle, project
from tests.test_unrefined_work_is_neither_routed_nor_assigned import Backlog, key

__all__ = ["key"]  # the fixture, used by name below

DAY = 86_400.0


def _cycle(root, board, refiner="lead"):
    return plan_cycle(
        root,
        board=board,
        sandbox_status=_free,
        clock=NOW,
        refiner=refiner,
        refinement=lambda t: refinement_status.status(board, t),
    )


def _ids(n):
    return [f"RT-{i}" for i in range(1, n + 1)]


def test_twenty_unrefined_tickets_are_refinement_work_not_idle(tmp_path, key):
    root = project(tmp_path)
    got = _cycle(root, Backlog(key, _ids(20)))
    assert got.verdict == REFINING, got.detail
    assert got.ready == [], "no Worker is offered an unrefined ticket"
    assert got.refinement.start == ["RT-1", "RT-2", "RT-3"]
    assert len(got.refinement.queued) == 17


def test_after_that_session_a_silent_user_is_waited_on_not_asked_again(tmp_path, key):
    root = project(tmp_path)
    rounds.record_session(root, "lead", NOW - 60)
    got = _cycle(root, Backlog(key, _ids(20)))
    assert got.verdict == WAITING_ON_USER, got.detail
    assert "after your next reply: RT-1, RT-2, RT-3" in got.detail


def test_a_reply_since_the_last_session_is_a_reason_to_refine(tmp_path, key):
    root = project(tmp_path)
    board = Backlog(key, _ids(4))
    (first,) = [t for t in board.tickets if t.id == "RT-1"]
    with rounds.locked(root, "lead", "RT-1") as (_a, save):
        save(
            rounds.Attempt(
                ticket="RT-1",
                text_sha256=rounds.text_of(first),
                rounds=[
                    rounds.Round(
                        k=1,
                        sent_at=NOW - 600,
                        deadline=NOW + DAY,
                        proposal=False,
                        answered_at=NOW - 30,
                    )
                ],
            )
        )
    rounds.record_session(root, "lead", NOW - 120)
    got = _cycle(root, board)
    assert got.verdict == REFINING and "1 reply" in got.detail, got.detail


def test_a_deadline_passing_is_a_reason_to_refine(tmp_path, key):
    """The thread must be read past it before silence is WAITING FOR YOU."""
    root = project(tmp_path)
    board = Backlog(key, _ids(1))
    with rounds.locked(root, "lead", "RT-1") as (_a, save):
        save(
            rounds.Attempt(
                ticket="RT-1",
                text_sha256=rounds.text_of(board.tickets[0]),
                rounds=[
                    rounds.Round(
                        k=1, sent_at=NOW - DAY - 60, deadline=NOW - 60, proposal=False
                    )
                ],
            )
        )
    rounds.record_session(root, "lead", NOW - 120)
    got = _cycle(root, board)
    assert got.verdict == REFINING and "deadline" in got.detail, got.detail


def test_refined_work_is_ready_whatever_else_waits(tmp_path, key):
    root = project(tmp_path)
    got = _cycle(root, Backlog(key, _ids(3), refined=["RT-2"]))
    assert got.verdict == READY and got.ready == ["RT-2"]


def test_nothing_scheduled_is_still_idle(tmp_path, key):
    root = project(tmp_path)
    assert _cycle(root, Backlog(key, [])).verdict == IDLE


def test_a_board_that_cannot_be_checked_is_a_wait_never_idle_never_work(tmp_path, key):
    """UNREADABLE is never NOT REFINED and never REFINED, and it is not an
    empty board either: it waits, and says it needs a person."""
    root = project(tmp_path)
    got = _cycle(root, Backlog(key, _ids(2), broken=_ids(2)))
    assert got.verdict == WAITING_ON_USER
    assert "RT-1 needs a person (UNREADABLE" in got.detail


def test_a_secondary_does_not_refine(tmp_path, key):
    """Refinement is the Owner's (TRQ7). A secondary sees only REFINED work,
    and with none it is idle: its work comes through its inbox."""
    root = project(tmp_path)
    (root / ".rite" / "config.yaml").write_text(
        (root / ".rite" / "config.yaml").read_text()
        + "coordination:\n  managers: [lead, helper]\n"
        "  manager_roles:\n    - name: lead\n      engine: claude\n      preset: lead\n"
        "    - name: helper\n      engine: claude\n      preset: executor\n"
    )
    board = Backlog(key, _ids(3))
    assert _cycle(root, board, refiner="helper").verdict == IDLE
    assert _cycle(root, board, refiner="lead").verdict == REFINING


def test_an_answered_round_whose_next_round_was_never_sent_is_owed(tmp_path, key):
    """Found in the TR2 live run: he answered at 10:58, the sessions meant to
    send round 2 failed, and with his reply older than the last session the
    loop would have waited on him while the Owner owed him the next round."""
    root = project(tmp_path)
    board = Backlog(key, _ids(1))
    with rounds.locked(root, "lead", "RT-1") as (_a, save):
        save(
            rounds.Attempt(
                ticket="RT-1",
                text_sha256=rounds.text_of(board.tickets[0]),
                rounds=[
                    rounds.Round(
                        k=1,
                        sent_at=NOW - 900,
                        deadline=NOW + DAY,
                        proposal=False,
                        answered_at=NOW - 800,
                    )
                ],
            )
        )
    rounds.record_session(root, "lead", NOW - 60)  # after his answer
    got = _cycle(root, board)
    assert got.verdict == REFINING and "next round" in got.detail, got.detail
    assert got.refinement.owed == ["RT-1"] and "RT-1" in got.refinement.texts
