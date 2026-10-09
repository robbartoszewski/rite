"""SCRUM-101 — a re-authored plan keeps the rejections it answers.

🔴 **A bound that did not bind.** `approve` records a rejection by appending
to the REJECTED plan's `returns`; the re-author then replaced that plan with
`replace(candidate, …)` over fresh model output, which carries `returns=()`.
So every re-author reset the history — and RL-10's bound is COUNTED from
`plan.returns` (`plan_review`), so `MAX_RETURNS` was never reached and the
reject → re-author cycle was unbounded.

What held it back was RL-69 alone: identical reasons converge and stop early.
That fails the moment the reviewer raises a NEW objection each round, which is
what a competent reviewer looking at a changed plan does.

⚠ **No existing test rejected twice with DIFFERENT reasons**, which is exactly
why the defect survived: with identical reasons RL-69 stops the loop before the
bound is ever consulted, so the suite passed with the bound defeated. That case
is the first test here.

Measured on the v0.7.0 gate run `smoke_mixed-20261009T034619Z`: the stage log
records `rejected`, and the approved plan's `returns` is `[]`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local import plan_state
from rite_ai.local.decompose import _write_pending


class _Result:
    def __init__(self):
        self.lines: list = []
        self.warnings: tuple = ()


def _subtasks():
    return (
        dec.Subtask(id="s1", intent="a", scope=("a.py",), verify="v", cites=("u1",)),
        dec.Subtask(id="s2", intent="b", scope=("b.py",), verify="v", cites=("u1",)),
    )


def _state():
    root = Path(tempfile.mkdtemp())
    (root / ".rite").mkdir(parents=True)
    return plan_state.layer(root)


def _reject(state, reason: str) -> None:
    """What `approve` does on a rejection: append to the standing plan."""
    standing = dec.read(state, "T-1")
    plan = standing.plan
    dec.write(
        state,
        dec.Decomposition(
            ticket="T-1",
            subtasks=plan.subtasks,
            decomposed_by=plan.decomposed_by,
            approval=dec.REJECTED,
            approved_by="",
            returns=(*plan.returns, reason),
        ),
        standing.version,
    )


def _reauthor(state) -> None:
    """What `decompose_ticket` does: write a fresh candidate as PENDING."""
    fresh = dec.Decomposition(
        ticket="T-1", subtasks=_subtasks(), decomposed_by="planner"
    )
    _write_pending(
        state, fresh, "planner", dec.read(state, "T-1").version, _Result(), ()
    )


def test_two_rejections_with_DIFFERENT_reasons_both_count():
    """🔴 The case nothing covered. With identical reasons RL-69 stops the loop
    before the bound is read, so the suite passed while the bound was dead."""
    state = _state()
    dec.write(
        state,
        dec.Decomposition(ticket="T-1", subtasks=_subtasks(), decomposed_by="planner"),
        dec.read(state, "T-1").version,
    )

    _reject(state, "lead: s1 names no rounding mode")
    _reauthor(state)
    assert len(dec.read(state, "T-1").plan.returns) == 1

    _reject(state, "lead: s2's verify quotes the wrong path")
    _reauthor(state)
    after = dec.read(state, "T-1").plan
    assert len(after.returns) == 2, (
        "the count must accumulate across re-authors, or RL-10's bound never "
        f"binds: {after.returns}"
    )
    assert "rounding mode" in after.returns[0]
    assert "wrong path" in after.returns[1], "and in the order they happened"


def test_the_bound_is_reachable_at_all():
    """The property RL-10 exists for: enough rejections and the count reaches
    `MAX_RETURNS`. Before this fix it could not, however many there were."""
    from rite_ai.local.recompose import MAX_RETURNS

    state = _state()
    dec.write(
        state,
        dec.Decomposition(ticket="T-1", subtasks=_subtasks(), decomposed_by="planner"),
        dec.read(state, "T-1").version,
    )
    for n in range(MAX_RETURNS):
        _reject(state, f"lead: objection number {n}")
        _reauthor(state)
    assert len(dec.read(state, "T-1").plan.returns) == MAX_RETURNS


def test_a_first_authoring_starts_from_no_returns():
    """CONTROL: nothing is invented for a plan that was never rejected."""
    state = _state()
    dec.write(
        state,
        dec.Decomposition(ticket="T-1", subtasks=_subtasks(), decomposed_by="planner"),
        dec.read(state, "T-1").version,
    )
    _reauthor(state)
    assert dec.read(state, "T-1").plan.returns == ()


def test_the_re_author_still_resets_the_APPROVAL():
    """CONTROL on the other side: carrying `returns` must not carry approval.
    A re-authored plan is PENDING and unapproved, whatever preceded it."""
    state = _state()
    dec.write(
        state,
        dec.Decomposition(
            ticket="T-1",
            subtasks=_subtasks(),
            decomposed_by="planner",
            approval=dec.APPROVED,
            approved_by="lead",
            returns=("lead: an earlier objection",),
        ),
        dec.read(state, "T-1").version,
    )
    _reauthor(state)
    after = dec.read(state, "T-1").plan
    assert after.approval == dec.PENDING
    assert after.approved_by == ""
    assert after.returns == ("lead: an earlier objection",), (
        "history kept, approval not"
    )
