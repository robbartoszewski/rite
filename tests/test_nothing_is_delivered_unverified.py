"""SCRUM-72f — RL-8: the composed work is verified before anything is
delivered, and the bound on sending a plan back.

§3.3a's table: **"RL-8 recomposition verify: Missing. Delivery is requested
once every subtask is accepted."**

🔴 **Which means nothing ever checked the composed work.** RL-7 runs each
subtask's own verify, so every subtask was checked in isolation — and a plan
sliced wrongly produces subtasks that each pass and a ticket that does not
work. That is the one failure decomposition itself introduces, and the one
gate that would catch it. `_ask_delivery` fired on "every subtask accepted"
and rite pushed.

And the guard is in TWO places, because there are two ways to reach a
delivery: the loop's own stage machine, and a request file a Manager writes
into its own directory by hand (`rite request deliver` exists for exactly
that). A guard only on the loop is a guard on the path nobody needs to go
round.
"""

from __future__ import annotations

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import level2, plan_state
from rite_ai.local import recompose as rc
from rite_ai.local import stage as st

TICKET = "T-1"


def _state(tmp_path):
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    return plan_state.layer(tmp_path)


def _subtasks(status=dec.ACCEPTED):
    return (
        dec.Subtask(
            id="s1",
            intent="first",
            scope=("a.txt",),
            verify="pytest -q",
            cites=("5.1",),
            status=status,
        ),
        dec.Subtask(
            id="s2",
            intent="second",
            scope=("b.txt",),
            verify="pytest -q",
            cites=("5.2",),
            status=status,
        ),
    )


def _approved(tmp_path, subtasks=None):
    state = _state(tmp_path)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=tuple(subtasks or _subtasks()),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    dec.write(state, plan, dec.read(state, TICKET).version)
    level2.record_approval(state, TICKET, "lead", plan.subtasks)
    return state, plan


def _digests(state):
    got = level2.approved_digests(state, TICKET)
    assert not isinstance(got, str), got
    return got


# --- 1. the guard before a delivery is requested --------------------------------


def test_work_with_no_recorded_recomposition_is_not_cleared(tmp_path):
    state, _ = _approved(tmp_path)
    got = rc.cleared_to_deliver(state, TICKET, _digests(state))
    assert isinstance(got, rc.Blocked), got
    assert "has not been verified" in got.why
    assert "passing its own check is not the ticket working" in got.why


def test_a_recorded_pass_clears_it(tmp_path):
    """The control. Without it a guard that refused everything would pass
    every refusal test here."""
    state, _ = _approved(tmp_path)
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=True,
            commands=("pytest -q",),
            plan=rc.fingerprint(_digests(state)),
        ),
    )
    got = rc.cleared_to_deliver(state, TICKET, _digests(state))
    assert isinstance(got, rc.Cleared), got
    assert "passes its agreed verify" in got.why


def test_a_recorded_FAILURE_does_not_clear_it(tmp_path):
    state, _ = _approved(tmp_path)
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=False,
            commands=("pytest -q",),
            failed="pytest -q",
            plan=rc.fingerprint(_digests(state)),
        ),
    )
    got = rc.cleared_to_deliver(state, TICKET, _digests(state))
    assert isinstance(got, rc.Blocked), got
    assert "FAILS its agreed verify" in got.why
    assert "a plan sliced wrongly" in got.why


def test_a_pass_against_a_DIFFERENT_plan_does_not_clear_this_one(tmp_path):
    """A plan re-approved after the verify cannot ride on the older pass —
    `level2`'s rule, for its reason."""
    state, plan = _approved(tmp_path)
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=True,
            commands=("pytest -q",),
            plan=rc.fingerprint(_digests(state)),
        ),
    )
    assert isinstance(rc.cleared_to_deliver(state, TICKET, _digests(state)), rc.Cleared)

    # Re-approved with a wider scope: a different set of subtasks.
    from dataclasses import replace

    wider = (replace(plan.subtask("s1"), scope=("a.txt", "c.txt")), plan.subtask("s2"))
    dec.write(
        state,
        dec.Decomposition(
            ticket=TICKET,
            subtasks=wider,
            decomposed_by="planner",
            approval=dec.APPROVED,
            approved_by="lead",
        ),
        dec.read(state, TICKET).version,
    )
    level2.record_approval(
        state,
        TICKET,
        "lead",
        wider,
        expected=level2.approval_version(state, TICKET),
    )
    got = rc.cleared_to_deliver(state, TICKET, _digests(state))
    assert isinstance(got, rc.Blocked), got
    assert "a different plan than the one approved now" in got.why


def test_a_verify_that_could_not_RUN_is_not_a_failure(tmp_path):
    """RL-47. A workspace or an endpoint that was not there did not fail the
    work, and must not send a plan back to review for an outage."""
    state, _ = _approved(tmp_path)
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            problem="the workspace is not there",
            plan=rc.fingerprint(_digests(state)),
        ),
    )
    got = rc.cleared_to_deliver(state, TICKET, _digests(state))
    assert isinstance(got, rc.Blocked), got
    assert "the workspace is not there" in got.why


def test_an_unreadable_result_is_not_mistaken_for_none(tmp_path):
    state, _ = _approved(tmp_path)
    state.write_state(
        rc.key_for(TICKET), b"not json", state.read_state(rc.key_for(TICKET)).version
    )
    got = rc.read(state, TICKET)
    assert isinstance(got, str) and "not readable" in got
    assert isinstance(rc.cleared_to_deliver(state, TICKET, _digests(state)), rc.Blocked)


# --- 2. "nothing was agreed" is a declared state, not a pass -------------------


def test_no_agreed_verify_is_recorded_as_such_and_said(tmp_path):
    """⚠ SCRUM-68's distinction, applied here: a check that did not run and a
    check that passed must not read alike. It clears the delivery — there is
    genuinely nothing to run — and it says so in those words."""
    state, _ = _approved(tmp_path)
    result = rc.run_recomposition(
        tmp_path,
        "planner",
        TICKET,
        _digests(state),
        snapshot={"verify": "none agreed", "definition_of_done": ["x"]},
    )
    assert result.none_agreed is True
    assert result.ok is False, "nothing ran, so it is not a pass"
    assert result.cleared is True, "and there is nothing to wait for either"
    assert "NOTHING to check" in result.line()
    assert "not a pass" in result.line()


@pytest.mark.parametrize(
    "verify, expected",
    [
        (["pytest -q", "ruff check"], ("pytest -q", "ruff check")),
        ("pytest -q", ("pytest -q",)),
        ("none agreed", "none agreed"),
    ],
)
def test_the_commands_come_from_the_refinement_record(tmp_path, verify, expected):
    """⚠ **The ticket's agreed verify, never the plan's.** A subtask's verify
    is written by the decomposer, which is the tier RL-8 is checking. A gate
    whose check the gated tier writes is not a gate."""
    assert rc.verify_commands({"verify": verify}) == expected


@pytest.mark.parametrize(
    "snapshot, expected",
    [
        ({"verify": []}, "neither commands"),
        ({"verify": "   "}, "is empty"),
        ({"verify": 7}, "neither commands"),
        ({}, "cannot read the refinement record"),
        ("no Worker is recorded", "cannot read the refinement record"),
    ],
)
def test_an_unusable_agreed_verify_is_a_problem_not_a_pass(
    tmp_path, snapshot, expected
):
    got = rc.verify_commands(snapshot)
    assert isinstance(got, str) and expected in got, got


# --- 3. running it --------------------------------------------------------------


class _Verifier:
    def __init__(self, *verdicts):
        self.verdicts, self.calls = list(verdicts), []

    def run(self, command, workspace):
        self.calls.append((command, workspace))
        passed = self.verdicts.pop(0) if self.verdicts else True
        return type(
            "V", (), {"passed": passed, "output": "" if passed else "2 failed"}
        )()


def test_every_agreed_command_runs_and_the_first_failure_stops_it(tmp_path):
    state, _ = _approved(tmp_path)
    verifier = _Verifier(True, False, True)
    got = rc.run_recomposition(
        tmp_path,
        "planner",
        TICKET,
        _digests(state),
        snapshot={"verify": ["one", "two", "three"]},
        workspace="/tmp/ws",
        verifier=verifier,
    )
    assert got.ok is False
    assert got.failed == "two"
    assert got.output == "2 failed"
    assert [c for c, _ in verifier.calls] == ["one", "two"], "it stops at the failure"
    assert {w for _, w in verifier.calls} == {"/tmp/ws"}


def test_a_verifier_that_raises_is_a_problem_not_a_failure(tmp_path):
    state, _ = _approved(tmp_path)

    class _Explodes:
        def run(self, command, workspace):
            raise OSError("no such workspace")

    got = rc.run_recomposition(
        tmp_path,
        "planner",
        TICKET,
        _digests(state),
        snapshot={"verify": ["pytest -q"]},
        workspace="/tmp/ws",
        verifier=_Explodes(),
    )
    assert got.ok is False and got.problem
    assert "could not be run" in got.problem
    assert got.failed == "", "a launch failure is not a failing command"


# --- 4. a failure sends the plan back, bounded ---------------------------------


def _project(tmp_path):
    from rite_ai.spec.digest_files import unit_filename, units_dir

    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n"
        "  - name: planner\n    engine: claude\n    duties: [decompose, execute]\n"
    )
    where = units_dir(tmp_path)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ("5.1", "5.2"):
        (where / unit_filename(unit)).write_text(f"Section {unit}.\n")
    _started_worker(tmp_path)
    return tmp_path


def _started_worker(root, worker="alpha", manager="planner"):
    """A Worker recorded as started on the ticket with a signed refinement
    record: the pipeline cannot reach `defined` without one, and RL-8's
    commands come from it."""
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record
    from rite_ai.refinement import record as rec

    d = root / "workers" / worker
    d.mkdir(parents=True, exist_ok=True)
    (d / "worker.yml").write_text(
        f"worker:\n  name: {worker}\n  manager: {manager}\n  modules: []\n"
    )
    record.write(
        root,
        worker,
        TICKET,
        parse_config(root / ".rite" / "config.yaml"),
        parse_modules(root / ".rite" / "modules.yaml"),
        refinement=rec.build(
            ticket=TICKET,
            board={"type": "none", "project": "p"},
            title=f"{TICKET}: the thing",
            description="Do it.",
            definition_of_done=["a.txt says the thing"],
            verify=["pytest -q"],
            provenance={"kind": rec.ACCEPTED, "answered_by": {"owner_user": "r"}},
            supersedes=None,
            key=b"k" * 32,
        ).payload(),
    )


def _pinned(root, manager="planner") -> str:
    from rite_ai.local.gates import definition_snapshot

    got = definition_snapshot(root, manager, TICKET)
    assert not isinstance(got, str), got
    return str(got["record_id"])


def _at_recomposable(tmp_path):
    """A ticket whose subtasks are all accepted, with its stage at STEPPING —
    the pass that follows is the one that recomposes."""
    from rite_ai.local.gates import gate_for

    root = _project(tmp_path)
    state, plan = _approved(root)
    for sub in plan.subtasks:
        level2.write_approach(state, TICKET, sub, "1. do it")
    st.adopt(
        state,
        TICKET,
        st.STEPPING,
        gate=gate_for(root, "planner", TICKET, state),
        definition=_pinned(root),
    )
    return root, state, plan


def test_a_failed_recomposition_returns_the_plan_to_review(tmp_path):
    """§3.3a item 6: a failed RL-8 returns the plan to review, with no
    delivery request. Approval DROPS with the return, so nothing may run from
    it until a reviewer approves it again."""
    from rite_ai.local.loop import advance_ticket

    root, state, _ = _at_recomposable(tmp_path)
    asked = []

    def failing(root_, manager, ticket, digests, *, now=None, **kw):
        return rc.Result(
            ticket=ticket,
            ok=False,
            commands=("pytest -q",),
            failed="pytest -q",
            output="2 failed",
            plan=rc.fingerprint(digests),
            at=1.0,
        )

    got = advance_ticket(
        root,
        "planner",
        TICKET,
        state=state,
        recompose_with=failing,
        ask_delivery=lambda *a: (asked.append(a), (True, ""))[1],
    )
    assert got.stage == st.DECOMPOSED, got
    assert "goes back to plan review" in got.note
    assert asked == [], "no delivery is requested"
    plan = dec.read(state, TICKET).plan
    assert plan.approval == dec.PENDING
    assert plan.approved_by == ""
    assert plan.returns and "RL-8" in plan.returns[-1]
    assert "2 failed" in plan.returns[-1]
    assert st.read(state, TICKET).record.log[-1].to == st.DECOMPOSED


def test_a_recomposition_that_could_not_RUN_returns_nothing(tmp_path):
    """RL-47 again, and this is where it bites: a plan sent back to review for
    an outage would spend one of RL-10's returns on nothing."""
    from rite_ai.local.loop import advance_ticket

    root, state, _ = _at_recomposable(tmp_path)

    def cannot(root_, manager, ticket, digests, *, now=None, **kw):
        return rc.Result(
            ticket=ticket,
            problem="the sandbox is stopped",
            plan=rc.fingerprint(digests),
            at=1.0,
        )

    got = advance_ticket(root, "planner", TICKET, state=state, recompose_with=cannot)
    assert not got.moved, got
    assert "could not run" in got.blocked
    plan = dec.read(state, TICKET).plan
    assert plan.approval == dec.APPROVED, "it is NOT sent back"
    assert plan.returns == ()
    assert st.read(state, TICKET).stage == st.STEPPING


def test_past_the_bound_it_escalates_instead_of_returning_again(tmp_path):
    """🔴 One bound for both return paths, counted on `Decomposition.returns`
    — the field RL-10 already keeps. A plan re-sliced to the bound that still
    does not compose is not a plan one more turn fixes."""
    from rite_ai.local.loop import advance_ticket

    root = _project(tmp_path)
    state = _state(root)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=_subtasks(),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
        returns=tuple(f"reason {n}" for n in range(rc.MAX_RETURNS)),
    )
    dec.write(state, plan, dec.read(state, TICKET).version)
    level2.record_approval(state, TICKET, "lead", plan.subtasks)
    for sub in plan.subtasks:
        level2.write_approach(state, TICKET, sub, "1. do it")
    from rite_ai.local.gates import gate_for

    st.adopt(
        state,
        TICKET,
        st.STEPPING,
        gate=gate_for(root, "planner", TICKET, state),
        definition=_pinned(root),
    )

    def failing(root_, manager, ticket, digests, *, now=None, **kw):
        return rc.Result(
            ticket=ticket,
            ok=False,
            commands=("pytest -q",),
            failed="pytest -q",
            plan=rc.fingerprint(digests),
            at=1.0,
        )

    got = advance_ticket(root, "planner", TICKET, state=state, recompose_with=failing)
    assert not got.moved, got
    assert f"{rc.MAX_RETURNS} is the bound" in got.blocked
    assert "somebody has to look at the ticket" in got.blocked
    assert "reason 0" in got.blocked
    # Not sent back a fourth time: the plan is left exactly as it was.
    after = dec.read(state, TICKET).plan
    assert after.approval == dec.APPROVED
    assert len(after.returns) == rc.MAX_RETURNS
    assert st.read(state, TICKET).stage == st.STEPPING


# --- 5. the hand-written request file (§3.3a guard 5, the other half) ----------


def _request(root, manager, ticket, worker="alpha"):
    import json

    from rite_ai.publishing import requests

    where = requests.requests_dir(root, manager)
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{ticket}.json").write_text(
        json.dumps({"worker": worker, "ticket": ticket}) + "\n"
    )


@pytest.mark.parametrize(
    "stage",
    [
        st.DEFINED,
        st.DECOMPOSED,
        st.APPROVED,
        st.REJECTED,
        st.STEPPING,
        st.RECOMPOSED,
    ],
)
def test_a_hand_written_request_before_delivery_requested_is_refused(tmp_path, stage):
    """🔴 The other half of §3.3a guard 5. This directory is the Manager's
    OWN, so a Manager can write a request file into it directly — and rite
    would then push work that never passed RL-8. A guard only on the loop is a
    guard on the path nobody needs to go round."""
    from rite_ai.publishing.requests import _staged_pipeline_refusal

    root = _project(tmp_path)
    state = _state(root)
    state.write_state(
        st.key_for(TICKET),
        st.render(
            st.Record(
                ticket=TICKET,
                stage=stage,
                definition="rec-pinned",
                log=(st.Transition(frm=st.UNSTARTED, to=stage, at=1.0),),
            )
        ),
        state.read_state(st.key_for(TICKET)).version,
    )
    refused = _staged_pipeline_refusal(root, "planner", TICKET)
    assert refused, f"a request at {stage} must be refused"
    assert f"stage {stage!r}" in refused
    assert "recomposition verify (RL-8)" in refused
    assert "does not replace the gate" in refused


def test_a_request_at_delivery_requested_goes_through(tmp_path):
    """The control for the test above."""
    from rite_ai.publishing.requests import _staged_pipeline_refusal

    root = _project(tmp_path)
    state = _state(root)
    state.write_state(
        st.key_for(TICKET),
        st.render(
            st.Record(
                ticket=TICKET,
                stage=st.DELIVERY_REQUESTED,
                definition="rec-pinned",
                log=(
                    st.Transition(frm=st.UNSTARTED, to=st.DELIVERY_REQUESTED, at=1.0),
                ),
            )
        ),
        state.read_state(st.key_for(TICKET)).version,
    )
    assert _staged_pipeline_refusal(root, "planner", TICKET) == ""


def test_a_ticket_with_no_stage_record_is_unaffected(tmp_path):
    """⚠ The Claude path. A Claude Worker's ticket has no staged pipeline, so
    inventing a stage for it here would refuse every delivery rite has ever
    made."""
    from rite_ai.publishing.requests import _staged_pipeline_refusal

    root = _project(tmp_path)
    assert _staged_pipeline_refusal(root, "planner", TICKET) == ""


def test_an_unreadable_stage_record_refuses_the_delivery(tmp_path):
    """Fails closed. A stage record that will not parse is not a reason to
    push."""
    from rite_ai.publishing.requests import _staged_pipeline_refusal

    root = _project(tmp_path)
    state = _state(root)
    state.write_state(
        st.key_for(TICKET),
        b'{"stage": "whatever"}',
        state.read_state(st.key_for(TICKET)).version,
    )
    refused = _staged_pipeline_refusal(root, "planner", TICKET)
    assert "could not be read" in refused
    assert "cannot tell has passed its gates" in refused


def test_honour_deliveries_consults_the_guard(tmp_path):
    """Wired, not only written — `test_no_dead_wiring`'s lesson."""
    import inspect

    from rite_ai.publishing import requests

    src = inspect.getsource(requests.honour_deliveries)
    assert "_staged_pipeline_refusal(root, manager, request.ticket)" in src
    # And BEFORE the deliver call, or it would refuse work already pushed.
    assert src.index("_staged_pipeline_refusal") < src.index("result = deliver(")


# --- 6. RL-70: a verify that cannot fail ---------------------------------------


@pytest.mark.parametrize("command", ["true", ":", "exit 0", "/bin/true", "true --x"])
def test_a_verify_that_is_just_a_success_is_refused(command):
    """🔴 §3.3a listed RL-70 as **"Open. A bare `true`, `:` or `exit 0`
    passes."** `cannot_fail` caught `pytest || true` and `pytest | tail` and
    waved through the simplest fake of all — the one a model reaches for when
    it cannot think of a check. `decomposition.problems` has called that
    function on every subtask's verify since RL-T4 while this form went
    straight past it."""
    from rite_ai.verdicts import cannot_fail

    why = cannot_fail(command)
    assert why, f"{command!r} cannot fail and was accepted"
    assert "exits 0" in why or "nothing behind it" in why


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "pytest -k true",
        "truecolor-check",
        "exit 1",
        "make test",
        "true | pytest -q",
    ],
)
def test_a_real_check_is_not_refused(command):
    """The control. `pytest -k true` and `truecolor-check` merely contain the
    word, and the status of `true | pytest` is pytest's."""
    from rite_ai.verdicts import cannot_fail

    assert cannot_fail(command) == "", command


def test_a_plan_whose_verify_cannot_fail_does_not_pass_validation():
    """Where it bites: three gates read this artifact, and a verify that
    always passes makes all three decorative."""
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=(
            dec.Subtask(
                id="s1", intent="a", scope=("a.txt",), verify="true", cites=("5.1",)
            ),
            dec.Subtask(
                id="s2",
                intent="b",
                scope=("b.txt",),
                verify="pytest -q",
                cites=("5.2",),
            ),
        ),
        decomposed_by="planner",
    )
    found = dec.problems(plan)
    assert any("s1" in p and "cannot fail" in p for p in found), found
    assert not any("s2" in p for p in found), found


# --- 7. what the mutation run found missing ------------------------------------


@pytest.mark.parametrize("target", [st.RECOMPOSED, st.DELIVERY_REQUESTED])
def test_the_GATE_itself_requires_an_RL_8_pass(tmp_path, target):
    """🔴 Not only the loop. The loop runs RL-8 before it moves, so the loop's
    own path was covered — but `gates.gate_for` is what `stage.advance` AND
    `stage.adopt` consult, so a gate that waved it through would let a
    pipeline be adopted straight into `recomposed` with nothing verified."""
    from rite_ai.local.gates import gate_for

    root = _project(tmp_path)
    state, _ = _approved(root)
    gate = gate_for(root, "planner", TICKET, state)

    shut = gate(target)
    assert shut, f"{target} must not be enterable with no RL-8 result"
    assert "has not been verified" in shut

    # And it opens on a recorded pass — the control.
    rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=True,
            commands=("pytest -q",),
            plan=rc.fingerprint(_digests(state)),
        ),
    )
    assert gate_for(root, "planner", TICKET, state)(target) == ""


def test_adoption_cannot_reach_recomposed_with_nothing_verified(tmp_path):
    """The door the gate closes, exercised: `adopt` reads the stage off the
    artifacts, and an unverified ticket's artifacts do not say `recomposed`."""
    from rite_ai.local.gates import gate_for

    root = _project(tmp_path)
    state, _ = _approved(root)
    got = st.adopt(
        state,
        TICKET,
        st.RECOMPOSED,
        gate=gate_for(root, "planner", TICKET, state),
        definition=_pinned(root),
    )
    assert isinstance(got, st.Refused), got
    assert "has not been verified" in got.why


@pytest.mark.parametrize(
    "result, cleared",
    [
        (rc.Result(ticket=TICKET, ok=True), True),
        (rc.Result(ticket=TICKET, none_agreed=True), True),
        (rc.Result(ticket=TICKET), False),
        # ⚠ A problem beats everything else in the record. A result carrying
        # both a pass and a problem is one nothing rite writes — and a reader
        # that trusted the pass would deliver on a verify that did not run.
        (rc.Result(ticket=TICKET, ok=True, problem="the sandbox is stopped"), False),
        (
            rc.Result(ticket=TICKET, none_agreed=True, problem="unreadable"),
            False,
        ),
    ],
)
def test_cleared_is_a_pass_or_a_declared_nothing_and_never_a_problem(result, cleared):
    assert result.cleared is cleared, result


def test_honour_deliveries_does_not_deliver_a_ticket_before_its_stage(
    tmp_path, monkeypatch
):
    """🔴 Behaviourally, not by reading the source. The source check next to
    this one survives a mutation that computes the refusal and ignores it —
    which is the whole failure mode: a guard whose answer nobody reads."""
    from rite_ai.publishing import deliver as deliver_module
    from rite_ai.publishing import requests

    root = _project(tmp_path)
    state = _state(root)
    state.write_state(
        st.key_for(TICKET),
        st.render(
            st.Record(
                ticket=TICKET,
                stage=st.STEPPING,
                definition=_pinned(root),
                log=(st.Transition(frm=st.UNSTARTED, to=st.STEPPING, at=1.0),),
            )
        ),
        state.read_state(st.key_for(TICKET)).version,
    )
    _request(root, "planner", TICKET)

    delivered = []
    monkeypatch.setattr(
        deliver_module, "deliver", lambda *a, **kw: delivered.append((a, kw))
    )
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root_, manager, about, text: None,
    )
    said = []
    requests.honour_deliveries(root, "planner", said.append)

    assert delivered == [], "it must not deliver work that never passed RL-8"
    assert any("recomposition verify (RL-8)" in line for line in said), said
    assert any("stage 'stepping'" in line for line in said), said


def test_honour_deliveries_DOES_deliver_once_the_stage_says_so(tmp_path, monkeypatch):
    """The control for the test above, and for the whole guard: a ticket at
    `delivery requested` goes through, or the guard would be refusing the
    pipeline it exists to protect."""
    from rite_ai.publishing import deliver as deliver_module
    from rite_ai.publishing import requests

    root = _project(tmp_path)
    state = _state(root)
    state.write_state(
        st.key_for(TICKET),
        st.render(
            st.Record(
                ticket=TICKET,
                stage=st.DELIVERY_REQUESTED,
                definition=_pinned(root),
                log=(
                    st.Transition(frm=st.UNSTARTED, to=st.DELIVERY_REQUESTED, at=1.0),
                ),
            )
        ),
        state.read_state(st.key_for(TICKET)).version,
    )
    _request(root, "planner", TICKET)

    delivered = []
    monkeypatch.setattr(
        deliver_module,
        "deliver",
        lambda *a, **kw: (
            delivered.append((a, kw))
            or type("D", (), {"outcomes": [], "sandbox": "kept"})()
        ),
    )
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root_, manager, about, text: None,
    )
    requests.honour_deliveries(root, "planner", lambda s: None)
    assert len(delivered) == 1, delivered
