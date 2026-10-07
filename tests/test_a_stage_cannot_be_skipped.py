"""SCRUM-72 §3.3a — rite's own code drives every stage, in order, and the
model cannot skip or reorder one.

Robert's definition of done for SCRUM-72: the **deterministic harness**, not
the model and not the Manager's good intentions, enforces the pipeline. That
claim is only worth anything if a skip is REFUSED rather than merely not
attempted, so every test here tries the skip and then asserts two things:

1. the ticket did not advance, and
2. the persisted record is **byte-unchanged** — not "looks the same", the same
   bytes, because a refusal that rewrote the record would have rewritten the
   history that says what has been passed.

🔴 **What was there before.** `loop.advance_ticket` derived the stage every
pass from the plan's current shape: no plan means decompose, PENDING means
approve, a runnable subtask means step. That reads as an order and is not one
— the conditions are independent, so anything that produced a later stage's
shape entered it, and nothing recorded that a stage had been passed, so
nothing could notice one that had not been.
"""

from __future__ import annotations

import itertools

import pytest

from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.local import decomposition as dec
from rite_ai.local import stage as st

TICKET = "T-1"


def _state(tmp_path):
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return LocalStateLayer(tmp_path / ".rite")


def _bytes(tmp_path) -> bytes:
    """The whole stored state, as bytes. A refusal must not touch any of it."""
    path = tmp_path / ".rite" / "state.json"
    return path.read_bytes() if path.exists() else b""


def _put(state, ticket: str, stage: str, log=None) -> None:
    """A stage record written directly, so a test can start anywhere."""
    log = log or (st.Transition(frm=st.UNSTARTED, to=stage, at=1.0, why="set up"),)
    got = state.read_state(st.key_for(ticket))
    state.write_state(
        st.key_for(ticket),
        st.render(st.Record(ticket=ticket, stage=stage, log=tuple(log))),
        got.version,
    )


def _open_gate(_stage: str) -> str:
    """Every gate open, so these tests isolate the TABLE. The gates have their
    own tests below; mixing them would let a table hole hide behind a gate."""
    return ""


# --- 1. the table IS the mechanism ------------------------------------------------


@pytest.mark.parametrize(
    "frm, to",
    [
        (f, t)
        for f, t in itertools.product((st.UNSTARTED, *st.STAGES), st.STAGES)
        if t not in st.TRANSITIONS[f]
    ],
)
def test_every_transition_the_table_does_not_hold_is_refused(tmp_path, frm, to):
    """The cross-product, not a handful of examples: every pair the table does
    not hold must be refused. 42 of the 56 ordered pairs are skips."""
    state = _state(tmp_path)
    if frm != st.UNSTARTED:
        _put(state, TICKET, frm)
    before = _bytes(tmp_path)

    got = st.advance(state, TICKET, to, gate=_open_gate, now=2.0)
    assert isinstance(got, st.Refused), f"{frm} -> {to} was allowed: {got}"
    assert _bytes(tmp_path) == before, "a refusal rewrote the record"
    assert st.read(state, TICKET).stage == frm


@pytest.mark.parametrize(
    "frm, to",
    [(f, t) for f, ts in st.TRANSITIONS.items() for t in ts],
)
def test_every_transition_the_table_holds_is_allowed(tmp_path, frm, to):
    """The control for the test above. Without it a table of nothing but
    refusals would pass every skip test in this file."""
    state = _state(tmp_path)
    if frm != st.UNSTARTED:
        _put(state, TICKET, frm)

    got = st.advance(state, TICKET, to, gate=_open_gate, now=2.0)
    assert isinstance(got, st.Advanced), f"{frm} -> {to} was refused: {got}"
    assert st.read(state, TICKET).stage == to


def test_a_step_before_approval_is_refused(tmp_path):
    """§3.3a guard 1, by name. A plan nobody reviewed may not be run."""
    state = _state(tmp_path)
    _put(state, TICKET, st.DEFINED)
    before = _bytes(tmp_path)

    got = st.advance(state, TICKET, st.STEPPING, gate=_open_gate)
    assert isinstance(got, st.Refused)
    assert "cannot go to 'stepping'" in got.why
    assert _bytes(tmp_path) == before
    assert st.read(state, TICKET).stage == st.DEFINED


def test_a_delivery_before_recomposition_is_refused(tmp_path):
    """§3.3a guard 5, through the loop. Work that was never composed may not be
    pushed."""
    state = _state(tmp_path)
    _put(state, TICKET, st.STEPPING)
    before = _bytes(tmp_path)

    got = st.advance(state, TICKET, st.DELIVERY_REQUESTED, gate=_open_gate)
    assert isinstance(got, st.Refused)
    assert _bytes(tmp_path) == before


def test_nothing_follows_the_end_of_the_pipeline(tmp_path):
    state = _state(tmp_path)
    _put(state, TICKET, st.DELIVERY_REQUESTED)
    before = _bytes(tmp_path)

    for target in st.STAGES:
        got = st.advance(state, TICKET, target, gate=_open_gate)
        assert isinstance(got, st.Refused), target
    assert _bytes(tmp_path) == before


# --- 2. the gate is checked as well as the table ---------------------------------


def test_the_table_allowing_it_is_not_enough(tmp_path):
    """`DECOMPOSED -> APPROVED` is in the table, and is still refused while the
    plan says pending: the table is about order, the gate about substance."""
    state = _state(tmp_path)
    _put(state, TICKET, st.DECOMPOSED)
    before = _bytes(tmp_path)

    got = st.advance(
        state, TICKET, st.APPROVED, gate=lambda s: "its plan's approval is 'pending'"
    )
    assert isinstance(got, st.Refused)
    assert "pending" in got.why
    assert _bytes(tmp_path) == before


def test_a_stage_with_no_gate_is_refused_not_waved_through(tmp_path):
    """`gates.gate_for` has no default-open branch: that function is what
    "cannot skip a stage" rests on, and a stage nothing checks is a stage
    anything may claim."""
    from rite_ai.local.gates import gate_for

    gate = gate_for(tmp_path, "planner", TICKET, _state(tmp_path))
    assert "no gate" in gate("a stage nobody declared")


# --- 3. a record edited by something that is not `advance` -----------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            b'{"format_version": 1, "ticket": "T-1", "stage": "shipped", "log": []}',
            "not one of",
        ),
        (
            b'{"format_version": 1, "ticket": "T-1", "stage": "approved", "log": []}',
            "carries no transition log",
        ),
        # The log and the stage disagree: one of them was edited.
        (
            b'{"format_version": 1, "ticket": "T-1", "stage": "stepping", "log": '
            b'[{"from": "", "to": "defined", "at": 1.0, "why": ""}]}',
            "disagree",
        ),
        (
            b'{"format_version": 9, "ticket": "T-1", "stage": "defined", "log": '
            b'[{"from": "", "to": "defined", "at": 1.0, "why": ""}]}',
            "upgrade rite",
        ),
        (b"not json at all", "not readable as a stage record"),
    ],
)
def test_an_illegal_stage_write_is_refused_and_nothing_advances(
    tmp_path, value, expected
):
    """§3.3a guard 7. An unparseable record is NOT read as unstarted: that is
    the one reading under which rite would re-author a plan over approved
    work."""
    state = _state(tmp_path)
    got = state.read_state(st.key_for(TICKET))
    state.write_state(st.key_for(TICKET), value, got.version)
    before = _bytes(tmp_path)

    read = st.read(state, TICKET)
    assert expected in read.error, read.error
    assert read.record is None

    moved = st.advance(state, TICKET, st.DEFINED, gate=_open_gate)
    assert isinstance(moved, st.Refused)
    assert "will not parse" in moved.why
    assert _bytes(tmp_path) == before


def test_the_loop_refuses_an_unparseable_stage_rather_than_restarting(tmp_path):
    """Through `advance_ticket`, because that is where the damage would be: an
    unparseable record read as unstarted re-authors a plan mid-flight."""
    from rite_ai.local.loop import advance_ticket

    state = _state(tmp_path)
    got = state.read_state(st.key_for(TICKET))
    state.write_state(st.key_for(TICKET), b'{"stage": "whatever"}', got.version)
    authored = []

    advance = advance_ticket(
        tmp_path,
        "planner",
        TICKET,
        state=state,
        gate=_open_gate,
        author_plan=lambda *a: authored.append(a),
    )
    assert not advance.moved
    assert "will not parse" in advance.blocked
    assert authored == [], "it must not author a plan over an unreadable stage"


# --- 4. the log is append-only, and it is the evidence ---------------------------


def test_the_log_only_ever_grows_and_keeps_its_order(tmp_path):
    state = _state(tmp_path)
    for n, target in enumerate(
        (st.DEFINED, st.DECOMPOSED, st.APPROVED, st.STEPPING, st.RECOMPOSED),
        start=1,
    ):
        assert isinstance(
            st.advance(
                state, TICKET, target, gate=_open_gate, why=f"n={n}", now=float(n)
            ),
            st.Advanced,
        )
        record = st.read(state, TICKET).record
        assert len(record.log) == n
        assert record.log[-1].to == target
        assert record.log[-1].why == f"n={n}"

    record = st.read(state, TICKET).record
    assert [t.to for t in record.log] == [
        st.DEFINED,
        st.DECOMPOSED,
        st.APPROVED,
        st.STEPPING,
        st.RECOMPOSED,
    ]
    # Every entry names the stage it came from, so the chain is checkable.
    assert [t.frm for t in record.log] == [
        st.UNSTARTED,
        st.DEFINED,
        st.DECOMPOSED,
        st.APPROVED,
        st.STEPPING,
    ]
    assert [t.at for t in record.log] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_a_return_to_plan_review_is_recorded_and_the_log_keeps_both(tmp_path):
    """A plan sent back does not erase that it was once approved: RL-10 counts
    returns, and a count with no history is a number nobody can check."""
    state = _state(tmp_path)
    for target in (st.DEFINED, st.DECOMPOSED, st.APPROVED, st.STEPPING):
        st.advance(state, TICKET, target, gate=_open_gate)
    st.advance(state, TICKET, st.DECOMPOSED, gate=_open_gate, why="RL-8 failed")

    log = st.read(state, TICKET).record.log
    assert [t.to for t in log] == [
        st.DEFINED,
        st.DECOMPOSED,
        st.APPROVED,
        st.STEPPING,
        st.DECOMPOSED,
    ]
    assert log[-1].why == "RL-8 failed"


# --- 5. two Managers moving one ticket -------------------------------------------


def test_a_stage_that_changed_under_a_move_refuses_rather_than_overwrites(tmp_path):
    """Compare-and-swap, `decomposition.write`'s rule and for its reason: two
    Managers may touch one ticket, and a last-writer-wins store drops one."""
    state = _state(tmp_path)
    _put(state, TICKET, st.DEFINED)

    class _Racing:
        """Reads like `state`, then lets somebody else write first."""

        def __init__(self, inner):
            self.inner = inner

        def read_state(self, key):
            return self.inner.read_state(key)

        def write_state(self, key, value, expected):
            _put(self.inner, TICKET, st.DECOMPOSED)  # the other Manager, now
            return self.inner.write_state(key, value, expected)

    got = st.advance(_Racing(state), TICKET, st.DECOMPOSED, gate=_open_gate)
    assert isinstance(got, st.Refused)
    assert "changed while this ran" in got.why


# --- 6. adoption is not a door past the guard ------------------------------------


def test_adoption_cannot_claim_a_stage_the_artifacts_do_not_support(tmp_path):
    state = _state(tmp_path)
    before = _bytes(tmp_path)

    got = st.adopt(
        state,
        TICKET,
        st.APPROVED,
        gate=lambda s: "its plan's approval is 'pending'",
    )
    assert isinstance(got, st.Refused)
    assert "do not say it reached there" in got.why
    assert _bytes(tmp_path) == before


def test_adoption_only_happens_when_there_is_no_record(tmp_path):
    state = _state(tmp_path)
    _put(state, TICKET, st.DEFINED)
    before = _bytes(tmp_path)

    got = st.adopt(state, TICKET, st.RECOMPOSED, gate=_open_gate)
    assert isinstance(got, st.Refused)
    assert "already has a stage record" in got.why
    assert _bytes(tmp_path) == before


def test_an_adopted_record_says_so_in_its_log(tmp_path):
    state = _state(tmp_path)
    got = st.adopt(state, TICKET, st.APPROVED, gate=_open_gate, now=7.0)
    assert isinstance(got, st.Advanced)
    log = st.read(state, TICKET).record.log
    assert len(log) == 1
    assert log[0].frm == st.UNSTARTED and log[0].to == st.APPROVED
    assert "adopted" in log[0].why


# --- 7. the gates read the artifacts, and say which one is missing ---------------


def _plan_on_disk(tmp_path, **kw):
    state = _state(tmp_path)
    read = dec.read(state, TICKET)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=kw.pop(
            "subtasks",
            (dec.Subtask(id="s1", intent="a", scope=("a.txt",), verify="pytest -q"),),
        ),
        decomposed_by=kw.pop("decomposed_by", "planner"),
        approval=kw.pop("approval", dec.PENDING),
        **kw,
    )
    dec.write(state, plan, read.version)
    return plan


@pytest.mark.parametrize(
    "target, plan_kw, expected",
    [
        (st.DECOMPOSED, {"decomposed_by": ""}, "RL-67"),
        (st.DECOMPOSED, {"approval": dec.APPROVED}, "not pending"),
        (st.APPROVED, {}, "only `approve.approve_plan` writes approved"),
        (
            st.APPROVED,
            {"approval": dec.APPROVED, "approved_by": ""},
            "approved by nobody",
        ),
        (st.REJECTED, {}, "not rejected"),
        (st.REJECTED, {"approval": dec.REJECTED}, "rejected with no reasons"),
        (st.STEPPING, {}, "no subtask may run"),
        (
            st.RECOMPOSED,
            {"approval": dec.APPROVED, "approved_by": "lead"},
            "did not reach accepted",
        ),
    ],
)
def test_each_gate_names_the_artifact_it_is_waiting_on(
    tmp_path, target, plan_kw, expected
):
    from rite_ai.local.gates import gate_for

    _plan_on_disk(tmp_path, **plan_kw)
    gate = gate_for(tmp_path, "planner", TICKET, _state(tmp_path))
    assert expected in gate(target)


def test_a_missing_plan_shuts_every_gate_past_defined(tmp_path):
    from rite_ai.local.gates import gate_for

    gate = gate_for(tmp_path, "planner", TICKET, _state(tmp_path))
    for target in st.STAGES:
        if target == st.DEFINED:
            continue
        assert "has no decomposition" in gate(target), target


def test_the_defined_gate_needs_a_started_worker_with_a_signed_record(tmp_path):
    """The spec/definition session's artifact. Without it the decomposer was
    called with no ticket text and saw "(no ticket text was supplied)"."""
    from rite_ai.local.gates import definition_snapshot, gate_for

    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    gate = gate_for(tmp_path, "planner", TICKET, _state(tmp_path))
    assert "no definition of done pinned to it" in gate(st.DEFINED)
    assert isinstance(definition_snapshot(tmp_path, "planner", TICKET), str)


def test_every_stage_in_the_pipeline_has_a_gate(tmp_path):
    """The pairing that keeps the two lists honest: a stage added to
    `stage.STAGES` without a gate would be one nothing checks."""
    from rite_ai.local.gates import _GATED

    assert set(_GATED) == set(st.STAGES)


# --- 8. what the mutation run found missing --------------------------------------


def test_a_conflicting_write_is_not_reported_as_a_move(tmp_path):
    """Compare-and-swap's answer has to be READ. A `Conflict` reported as a
    move is worse than no CAS at all: the caller is told the ticket advanced
    and the record says it did not."""
    state = _state(tmp_path)
    _put(state, TICKET, st.DEFINED)

    class _AlwaysConflicts:
        def __init__(self, inner):
            self.inner = inner

        def read_state(self, key):
            return self.inner.read_state(key)

        def write_state(self, key, value, expected):
            from rite_ai.coordination.state_layer import Conflict

            return Conflict(current="whatever")

    got = st.advance(_AlwaysConflicts(state), TICKET, st.DECOMPOSED, gate=_open_gate)
    assert isinstance(got, st.Refused), got
    assert "changed while this ran" in got.why
    assert st.read(state, TICKET).stage == st.DEFINED


def test_an_unavailable_write_is_not_reported_as_a_move(tmp_path):
    state = _state(tmp_path)
    _put(state, TICKET, st.DEFINED)

    class _Unavailable:
        def __init__(self, inner):
            self.inner = inner

        def read_state(self, key):
            return self.inner.read_state(key)

        def write_state(self, key, value, expected):
            from rite_ai.coordination.state_layer import Unavailable

            return Unavailable(reason="the disk went away")

    got = st.advance(_Unavailable(state), TICKET, st.DECOMPOSED, gate=_open_gate)
    assert isinstance(got, st.Refused)
    assert "could not be written" in got.why
    assert st.read(state, TICKET).stage == st.DEFINED
