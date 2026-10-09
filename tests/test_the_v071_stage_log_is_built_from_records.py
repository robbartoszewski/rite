"""`hooks._sequence` turns three of rite's records into one ordered sequence.

The gate's `pipeline_stages_in_order` judges whatever this returns, so a bug
here is a green that means nothing — the failure this whole harness exists to
make impossible. Each case below is one sequence the records could describe,
and every one of them has a control: the defect that must NOT read as a pass.

⚠ **Why `approached` and `step_reviewed` are the interesting ones.** SCRUM-72
persists six stage transitions, and the plan's eight names include those two,
which 72 enforces through other records — a Level-2 approach per subtask, and a
subtask reaching `accepted` only after rite ran its verify. `_sequence` merges
them in. The thing it must never do is supply one that is not on disk, because
that would turn a skipped Level-2 approach into a passing pipeline.
"""

from __future__ import annotations

from tools.e2e_v071.checks import STAGES
from tools.e2e_v071.hooks import _place_step_review, _sequence

# The six transitions SCRUM-72 writes, in the order a clean run writes them.
_CLEAN_LOG = [
    {"from": "", "to": "defined", "at": 10.0},
    {"from": "defined", "to": "decomposed", "at": 20.0},
    {"from": "decomposed", "to": "approved", "at": 30.0},
    {"from": "approved", "to": "stepping", "at": 50.0},
    {"from": "stepping", "to": "recomposed", "at": 60.0},
    {"from": "recomposed", "to": "delivery requested", "at": 70.0},
]


def _entry(**over):
    base = {
        "log": list(_CLEAN_LOG),
        # approved at 30, stepping at 50 — the approach lands between them.
        "approaches": [{"subtask": "s1", "at": 40.0}],
        "accepted": ["s1"],
        "unreadable": "",
    }
    base.update(over)
    return base


def test_a_clean_run_produces_exactly_the_plans_stages_in_order():
    assert _sequence(_entry()) == list(STAGES)


def test_a_skipped_level_2_approach_is_missing_from_the_sequence():
    """🔴 The control that matters most. No approach record on disk means no
    `approached` in the sequence, so the check FAILS — rather than the hook
    supplying a stage nothing persisted."""
    got = _sequence(_entry(approaches=[]))
    assert "approached" not in got
    assert got != list(STAGES)


def test_a_subtask_never_accepted_leaves_out_the_step_review():
    """RL-7: a subtask is accepted only after rite ran its verify. Nothing
    accepted means no step review happened, and the sequence says so."""
    got = _sequence(_entry(accepted=[]))
    assert "step_reviewed" not in got
    assert got != list(STAGES)


def test_the_approach_is_placed_by_its_own_recorded_time():
    """Not by assumption. An approach recorded BEFORE the approval (which
    `cleared_to_run` should make impossible) comes out in that wrong order, so
    the check fails instead of the hook tidying it away."""
    got = _sequence(_entry(approaches=[{"subtask": "s1", "at": 25.0}]))
    assert got.index("approached") < got.index("plan_reviewed")
    assert got != list(STAGES)


def test_the_earliest_approach_is_the_one_that_counts():
    entry = _entry(
        approaches=[{"subtask": "s2", "at": 45.0}, {"subtask": "s1", "at": 40.0}]
    )
    got = _sequence(entry)
    assert got == list(STAGES)


def test_a_rejection_is_kept_under_its_own_name_and_does_not_match():
    """A plan the reviewer rejected is a real transition. Mapping it to
    `plan_reviewed` would make a rejected plan indistinguishable from an
    approved one."""
    log = list(_CLEAN_LOG)
    log.insert(3, {"from": "decomposed", "to": "rejected", "at": 25.0})
    got = _sequence(_entry(log=log))
    assert "rejected" in got
    assert got != list(STAGES)


def test_an_unreadable_record_is_not_an_empty_one():
    """`stage.read` distinguishes absent from unreadable; so must this. An
    unreadable record yields no stages, which fails — it must never come back
    looking like a ticket that simply has not started."""
    assert (
        _sequence(
            {
                "log": [],
                "approaches": [],
                "accepted": [],
                "unreadable": "state.json is not readable",
            }
        )
        == []
    )


def test_equal_timestamps_keep_the_order_the_log_was_written_in():
    """Two transitions recorded in the same second are still ordered, by the
    log's own order. A sort that dropped that tie-break would shuffle them."""
    log = [
        {"from": "", "to": "defined", "at": 99.0},
        {"from": "defined", "to": "decomposed", "at": 99.0},
        {"from": "decomposed", "to": "approved", "at": 99.0},
    ]
    got = _sequence({"log": log, "approaches": [], "accepted": [], "unreadable": ""})
    assert got == ["defined", "decomposed", "plan_reviewed"]


def test_the_step_review_is_not_placed_when_nothing_was_executed():
    """It goes after `executed`. With no `executed` to follow, putting it
    anywhere would assert an order no record supports."""
    assert _place_step_review(["defined", "decomposed"]) == ["defined", "decomposed"]


def test_the_step_review_follows_the_last_execution():
    assert _place_step_review(["defined", "executed", "executed", "recomposed"]) == [
        "defined",
        "executed",
        "executed",
        "step_reviewed",
        "recomposed",
    ]
