"""L-6 — the local tier advances without anybody typing a command.

`rite local decompose`, `approve` and `step` were three sound commands somebody
ran in order. This drives them, one stage per pass, inside the supervisor's own
cycle. Robert ruled it into v0.7.0 on 2026-10-03.

Two properties are tested, and they are the two that matter: the sequence runs
hands-off, and **it refuses to advance when any gate fails** rather than finding
a way round. The gates are the real ones — `approve_plan` is not stubbed in the
refusal tests, because a test that stubbed it would be testing a different
program.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from rite_ai.local import decomposition as dec
from rite_ai.local import plan_state
from rite_ai.local import stage as st
from rite_ai.local.loop import (
    APPROVED,
    DECOMPOSED,
    DEFINED,
    DELIVERY_ASKED,
    RECOMPOSED,
    STEPPED,
    Advance,
    advance_ticket,
    drive_local_tier,
    independent_reviewer,
)

TICKET = "T-1"
GPU = (
    "    endpoint: http://localhost:11434\n"
    "    agent: goose\n"
    "    context_window: 32768\n"
)


def _project(tmp_path: Path, *, author="qwen3:8b", reviewer="qwen3:32b", solo=False):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    roles = (
        "  - name: planner\n    engine: local:small\n"
        f"    model: {author}\n{GPU}"
        "    duties: [decompose, execute]\n"
    )
    if not solo:
        roles += (
            "  - name: lead\n    engine: local:large\n"
            f"    model: {reviewer}\n{GPU}"
            "    duties: [plan-review, decide, board, route, integrate]\n"
        )
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n" + roles
    )
    _spec_units(tmp_path, "5.1", "5.2")
    # ⚠ **A started Worker with a SIGNED refinement record, SCRUM-72.** The
    # pipeline's first stage is DEFINED: the definition of done pinned to the
    # ticket, which is the record the Worker was started on. Before the stage
    # machine the loop ran without one and the decomposer saw "(no ticket text
    # was supplied)", so every test here could drive the whole pipeline with no
    # definition anywhere — which is the hole §3.3a closed. A test that skipped
    # it now would be testing a program that no longer exists.
    _started_worker(tmp_path)
    return tmp_path


def _refinement_payload(ticket: str = TICKET) -> dict:
    """A real signed record's payload, not a stand-in dict: 72b feeds this to
    the decomposer and checks it against the board, so the tests want the
    shape the production path produces."""
    from rite_ai.refinement import record as rec

    return rec.build(
        ticket=ticket,
        board={"type": "none", "project": "acme"},
        title=f"{ticket}: the thing",
        description="Do the thing.",
        definition_of_done=["a.txt says the thing", "b.txt says the other thing"],
        verify=["pytest -q"],
        provenance={"kind": rec.ACCEPTED, "answered_by": {"owner_user": "robert"}},
        supersedes=None,
        key=b"k" * 32,
        scope_in=["a.txt", "b.txt"],
    ).payload()


def _started_worker(root: Path, worker: str = "alpha", manager: str = "planner"):
    """A Worker of `manager`, recorded as started on TICKET with a signed
    refinement record — the state a real local-tier pass finds."""
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record

    worker_dir = root / "workers" / worker
    worker_dir.mkdir(parents=True, exist_ok=True)
    (worker_dir / "worker.yml").write_text(
        f"worker:\n  name: {worker}\n  manager: {manager}\n  modules: []\n"
    )
    modules_yaml = root / ".rite" / "modules.yaml"
    if not modules_yaml.exists():
        modules_yaml.write_text("modules: {}\n")
    record.write(
        root,
        worker,
        TICKET,
        parse_config(root / ".rite" / "config.yaml"),
        parse_modules(modules_yaml),
        refinement=_refinement_payload(),
    )
    return worker


def _spec_units(root: Path, *ids: str) -> None:
    from rite_ai.spec.digest_files import unit_filename, units_dir

    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    for unit in ids:
        (where / unit_filename(unit)).write_text(f"Section {unit}: the rule.\n")


def _subtasks(status=dec.PLANNED):
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


def _pinned(root: Path, manager: str = "planner") -> str:
    """The refinement record this ticket's pipeline pins to (SCRUM-72d): the
    `record_id` of the signed record its Worker was STARTED on. Read from the
    real snapshot, not invented — the production path pins exactly this."""
    from rite_ai.local.gates import definition_snapshot

    got = definition_snapshot(root, manager, TICKET)
    assert not isinstance(got, str), got
    return str(got["record_id"])


def _state(root: Path):
    return plan_state.layer(root)


def _write_plan(root: Path, manager="planner", **kw):
    state = _state(root)
    read = dec.read(state, TICKET)
    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=kw.pop("subtasks", _subtasks()),
        decomposed_by=kw.pop("decomposed_by", "planner"),
        approval=kw.pop("approval", dec.PENDING),
        **kw,
    )
    dec.write(state, plan, read.version)
    # ⚠ **And the STAGE that artifact supports (SCRUM-72).** A plan on disk
    # with no stage record is a half-state no real run produces; leaving it
    # would make every test here exercise `stage.adopt` instead of the thing it
    # is about. BEST-EFFORT on purpose: a plan this test wrote deliberately
    # malformed has a SHUT gate and cannot be adopted, and that refusal is
    # exactly what such a test is checking.
    from rite_ai.local.gates import gate_for
    from rite_ai.local.loop import _stage_from_artifacts

    derived = _stage_from_artifacts(root, manager, TICKET, plan)
    if derived:
        st.adopt(
            state,
            TICKET,
            derived,
            gate=gate_for(root, manager, TICKET, state),
            definition=_pinned(root, manager),
        )
    return plan


def _review(root: Path, reviewer="lead", op=None, reason="") -> list[str]:
    """The reviewing Manager answers, through the real path (SCRUM-72 §3.3b).

    ⚠ **Nothing here stubs the approval.** The verdict is written into the
    reviewer's OWN directory, exactly as `rite plan approve` writes it, and
    honoured by the same function the supervisor calls. The reviewer's
    identity is that directory — no name is passed in a payload anywhere.
    """
    from rite_ai.local import plan_review

    plan_review.write_verdict(root, reviewer, op or plan_review.APPROVE, TICKET, reason)
    said: list[str] = []
    plan_review.honour_verdicts(root, reviewer, said.append)
    return said


def _define(root: Path, manager="planner") -> None:
    """One pass: the spec/definition stage. The first stage of every ticket,
    and it runs no model — it pins the record the Worker was started on."""
    got = advance_ticket(root, manager, TICKET)
    assert got.stage == DEFINED, got


def _stored(root: Path):
    return dec.read(_state(root), TICKET).plan


# ── the sequence, hands-off ──────────────────────────────────────────────────


class _Driver:
    """Stands in for the two INFERENCE stages only.

    Decompose and step are the two that run a model; approval and the delivery
    request are rite's own deterministic code and are NOT stubbed anywhere in
    this file.
    """

    def __init__(self, root: Path, *, recomposes=True):
        self.root = root
        self.calls: list[str] = []
        self.texts: list[str] = []
        self.recomposes = recomposes
        self.recompositions = 0

    def recompose(self, root, manager, ticket, digests, *, now=None, **kw):
        """RL-8's verify, stubbed — it is the one stage that shells out.

        ⚠ Stubbed because it RUNS A COMMAND, not because it is optional: the
        gate itself is not stubbed anywhere in this file, and the tests below
        drive both verdicts through it.
        """
        from rite_ai.local import recompose as rc

        self.recompositions += 1
        if self.recomposes:
            return rc.Result(
                ticket=ticket,
                ok=True,
                commands=("pytest -q",),
                plan=rc.fingerprint(digests),
                at=now or 0.0,
            )
        return rc.Result(
            ticket=ticket,
            ok=False,
            commands=("pytest -q",),
            failed="pytest -q",
            output="2 failed",
            plan=rc.fingerprint(digests),
            at=now or 0.0,
        )

    def decompose(self, root, manager, ticket, *, ticket_text=""):
        self.calls.append(f"decompose:{manager}")
        # ⚠ **Kept, and asserted on (SCRUM-72d).** `advance_ticket` called the
        # decomposer with NO ticket text, so it saw "(no ticket text was
        # supplied)" and sliced a ticket by its id while the agreed definition
        # of done sat in a record nothing read. A stub that silently accepted
        # `ticket_text` and dropped it would hide that coming back.
        self.texts.append(ticket_text)
        _write_plan(self.root, decomposed_by=manager)
        return type("R", (), {"wrote": True, "problem": "", "reasons": ()})()

    def step(self, root, manager, ticket):
        self.calls.append(f"step:{manager}")
        plan = _stored(self.root)
        nxt = next((s for s in plan.subtasks if s.status == dec.PLANNED), None)
        if nxt is None:
            return type("S", (), {"problem": "nothing planned", "subtask": ""})()
        done = tuple(
            replace(s, status=dec.ACCEPTED) if s.id == nxt.id else s
            for s in plan.subtasks
        )
        read = dec.read(_state(self.root), TICKET)
        dec.write(
            _state(self.root),
            dec.Decomposition(
                ticket=plan.ticket,
                subtasks=done,
                decomposed_by=plan.decomposed_by,
                approval=plan.approval,
                approved_by=plan.approved_by,
            ),
            read.version,
        )
        return type(
            "S",
            (),
            {
                "problem": "",
                "accepted": True,
                "status": "accepted",
                "subtask": nxt.id,
            },
        )()


def _advance(root: Path, driver: _Driver, manager="planner", **kw):
    kw.setdefault("recompose_with", driver.recompose)
    return advance_ticket(
        root, manager, TICKET, author_plan=driver.decompose, step=driver.step, **kw
    )


def _recomposed(root: Path, manager="planner", ok=True) -> None:
    """Record an RL-8 result for the plan approved now, the way a pass that
    ran the verify leaves it — for tests that start after that stage."""
    from rite_ai.local import level2
    from rite_ai.local import recompose as rc

    state = _state(root)
    digests = level2.approved_digests(state, TICKET)
    assert not isinstance(digests, str), digests
    written = rc.write(
        state,
        rc.Result(
            ticket=TICKET,
            ok=ok,
            commands=("pytest -q",),
            failed="" if ok else "pytest -q",
            plan=rc.fingerprint(digests),
            at=1.0,
        ),
    )
    assert type(written).__name__ == "Written", written
    # ⚠ And the STAGE, which could not be adopted until now: `recomposed`'s
    # gate wants an RL-8 result, so `_write_plan`'s own adoption was refused
    # while there was none. Adopting here is what a driven pipeline has by
    # this point, and it keeps these tests one pass from the delivery they
    # are about rather than two.
    from rite_ai.local.gates import gate_for

    st.adopt(
        state,
        TICKET,
        st.RECOMPOSED if ok else st.STEPPING,
        gate=gate_for(root, manager, TICKET, state),
        definition=_pinned(root, manager),
    )


def _level2_cleared(root: Path, manager="planner") -> None:
    """The approval digests and a persisted approach per subtask, as a driven
    pipeline leaves them by the time a delivery is asked for."""
    from rite_ai.local import level2

    state = _state(root)
    plan = _stored(root)
    recorded = level2.record_approval(
        state,
        TICKET,
        plan.approved_by or "lead",
        plan.subtasks,
        expected=level2.approval_version(state, TICKET),
    )
    assert type(recorded).__name__ == "Written", recorded
    for sub in plan.subtasks:
        written = level2.write_approach(state, TICKET, sub, "1. do it")
        assert type(written).__name__ == "Written", written


def test_it_drives_decompose_then_approve_then_step_with_no_commands(tmp_path):
    """The whole point. Nobody types anything; each pass advances one stage."""
    root = _project(tmp_path)
    d = _Driver(root)

    # ⚠ The FIRST stage is the spec/definition session (SCRUM-72 §3.3a): the
    # signed refinement record the Worker was started on, pinned to the ticket.
    # Before the stage machine this stage did not exist and the decomposer was
    # handed "(no ticket text was supplied)".
    zeroth = _advance(root, d)
    assert zeroth.stage == DEFINED, zeroth
    assert d.calls == [], "pinning the definition runs no model"

    first = _advance(root, d)
    assert first.stage == DECOMPOSED, first
    assert _stored(root).approval == dec.PENDING, "a decomposer may not approve"

    # 🔴 **The harness does not approve (§3.3b).** This pass ASKS the
    # independent reviewer and the ticket waits; before SCRUM-72 it stamped
    # APPROVED in that reviewer's name without ever asking it.
    asked = _advance(root, d)
    assert asked.stage == DECOMPOSED, asked
    assert "asked of lead" in asked.note
    assert _stored(root).approval == dec.PENDING
    waiting = _advance(root, d)
    assert not waiting.moved, "nothing moves until the reviewer answers"
    assert "waiting for lead's verdict" in waiting.blocked

    # lead answers, in its own directory, and rite honours it.
    said = _review(root)
    assert any("is approved by lead" in line for line in said), said
    assert _stored(root).approved_by == "lead"
    assert _stored(root).released

    second = _advance(root, d)
    assert second.stage == APPROVED, second

    third = _advance(root, d)
    assert third.stage == STEPPED, third
    fourth = _advance(root, d)
    assert fourth.stage == STEPPED, fourth
    assert all(s.status == dec.ACCEPTED for s in _stored(root).subtasks)

    assert d.calls == ["decompose:planner", "step:planner", "step:planner"]


def test_one_stage_per_pass_and_never_two(tmp_path):
    # The engine's accounting is in units of work per cycle; a pass that ran a
    # whole ticket would spend an unbounded amount inside one tick.
    root = _project(tmp_path)
    d = _Driver(root)
    _define(root)
    _advance(root, d)
    assert len(d.calls) == 1
    _advance(root, d)  # approval: free, deterministic, no inference
    assert len(d.calls) == 1


def test_when_every_subtask_is_accepted_it_asks_rite_to_deliver(tmp_path):
    root = _project(tmp_path)
    _write_plan(
        root,
        subtasks=_subtasks(dec.ACCEPTED),
        approval=dec.APPROVED,
        approved_by="lead",
    )
    asked = {}

    def ask(root_, manager, ticket):
        asked["args"] = (manager, ticket)
        return True, ""

    _level2_cleared(root)
    _recomposed(root)
    got = advance_ticket(root, "planner", TICKET, ask_delivery=ask)
    assert got.stage == DELIVERY_ASKED, got
    assert asked["args"] == ("planner", TICKET)


def test_the_delivery_goes_through_the_request_path(tmp_path):
    """OL8's path: rite validates and pushes. Nothing here pushes."""
    import json

    from rite_ai.publishing import requests

    root = _project(tmp_path)
    _write_plan(
        root,
        subtasks=_subtasks(dec.ACCEPTED),
        approval=dec.APPROVED,
        approved_by="lead",
    )
    # The Worker of this Manager recorded as started on the ticket is
    # `_project`'s: the pipeline cannot reach DEFINED without one, so a second
    # one written here would be a second answer to "whose work is this".
    _level2_cleared(root)
    _recomposed(root)
    got = advance_ticket(root, "planner", TICKET)
    assert got.stage == DELIVERY_ASKED, got
    written = requests.requests_dir(root, "planner") / f"{TICKET}.json"
    assert written.exists()
    assert json.loads(written.read_text()) == {"worker": "alpha", "ticket": TICKET}


def test_a_delivery_is_not_asked_for_twice(tmp_path):
    # A delivered plan stays all-accepted, so without this guard every cycle
    # would write the request again and the supervisor would honour it again.
    from rite_ai.publishing import requests

    root = _project(tmp_path)
    _write_plan(
        root,
        subtasks=_subtasks(dec.ACCEPTED),
        approval=dec.APPROVED,
        approved_by="lead",
    )
    _level2_cleared(root)
    _recomposed(root)
    where = requests.requests_dir(root, "planner")
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{TICKET}.json").write_text("{}")
    # ⚠ **Two guards now, and this test is the file one.** The stage is
    # `recomposed` here — the request was written behind rite's back, after the
    # plan — so the structural guard has nothing to say and the file check
    # answers. The structural guard is the next test: once rite has asked, the
    # stage is the end of the table and it cannot ask again whatever any file
    # says. Both are kept: one catches a request rite wrote and failed to
    # record, the other a request it recorded.
    got = advance_ticket(root, "planner", TICKET)
    assert not got.moved
    assert "already waiting" in got.blocked


def test_once_the_delivery_is_requested_the_pipeline_is_at_its_end(tmp_path):
    """`delivery requested` is the end of the table: nothing follows it, so a
    ticket cannot be driven round again even with the request file removed."""
    from rite_ai.publishing import requests

    root = _project(tmp_path)
    _write_plan(
        root,
        subtasks=_subtasks(dec.ACCEPTED),
        approval=dec.APPROVED,
        approved_by="lead",
    )
    _level2_cleared(root)
    _recomposed(root)
    asked = []
    got = advance_ticket(
        root,
        "planner",
        TICKET,
        ask_delivery=lambda *a: (asked.append(a), (True, ""))[1],
    )
    assert got.stage == DELIVERY_ASKED, got
    # The request file gone and the plan untouched: the STAGE is what refuses.
    where = requests.requests_dir(root, "planner") / f"{TICKET}.json"
    if where.exists():
        where.unlink()
    again = advance_ticket(
        root,
        "planner",
        TICKET,
        ask_delivery=lambda *a: (asked.append(a), (True, ""))[1],
    )
    assert not again.moved, again
    assert "delivery has been requested" in again.blocked
    assert len(asked) == 1, "it must not ask a second time"
    assert st.read(_state(root), TICKET).stage == DELIVERY_ASKED


# ── it refuses to advance when a gate fails ──────────────────────────────────


def test_a_same_model_reviewer_cannot_approve_so_nothing_advances(tmp_path):
    """RL-6 as Robert ruled it. Two 'local:' labels on one model are one model."""
    root = _project(tmp_path, author="qwen3.8:latest", reviewer="qwen3.8:latest")
    d = _Driver(root)
    _define(root)
    assert _advance(root, d).stage == DECOMPOSED
    blocked = _advance(root, d)
    assert not blocked.moved, blocked
    assert "independent reviewer" in blocked.blocked or "same model" in blocked.blocked
    assert _stored(root).approval == dec.PENDING
    # And it must not step from an unapproved plan.
    assert "step:planner" not in d.calls


def test_rites_own_window_pin_does_not_launder_the_same_model(tmp_path):
    root = _project(
        tmp_path, author="qwen3.8:latest", reviewer="rite-ctx32768-qwen3.8-latest"
    )
    d = _Driver(root)
    _define(root)
    _advance(root, d)
    assert not _advance(root, d).moved
    assert _stored(root).approval == dec.PENDING


def test_a_lone_decomposer_cannot_approve_its_own_plan(tmp_path):
    """DD-3.5: the gate is worthless if the thing it gates can set it."""
    root = _project(tmp_path, solo=True)
    d = _Driver(root)
    _define(root)
    _advance(root, d)
    blocked = _advance(root, d)
    assert not blocked.moved
    assert "plan-review" in blocked.blocked
    assert _stored(root).approval == dec.PENDING


def test_an_unnamed_author_fails_closed(tmp_path):
    """RL-67: rite cannot check an independence claim it cannot place."""
    root = _project(tmp_path)
    _write_plan(root, decomposed_by="")
    blocked = advance_ticket(root, "planner", TICKET)
    assert not blocked.moved
    assert "RL-67" in blocked.blocked
    assert _stored(root).approval == dec.PENDING


def test_an_unplaceable_author_fails_closed(tmp_path):
    root = _project(tmp_path)
    _write_plan(root, decomposed_by="ghost")
    blocked = advance_ticket(root, "planner", TICKET)
    assert not blocked.moved
    assert "RL-67" in blocked.blocked


def test_an_unresolvable_cite_stops_approval(tmp_path):
    """RL-63, inherited through approve_plan: an unresolvable cite would stall
    the subtask AFTER approval, which is why it is checked before."""
    root = _project(tmp_path)
    _write_plan(
        root,
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="a",
                scope=("a.txt",),
                verify="pytest -q",
                cites=("9.9",),
            ),
            dec.Subtask(
                id="s2",
                intent="b",
                scope=("b.txt",),
                verify="pytest -q",
                cites=("5.2",),
            ),
        ),
    )
    # The ask goes out; the refusal comes from `approve_plan` when the
    # reviewer's verdict is honoured, which is where the plan is re-checked.
    asked = advance_ticket(root, "planner", TICKET)
    assert asked.stage == DECOMPOSED, asked
    said = _review(root)
    assert any("was NOT approved" in line for line in said), said
    assert any("9.9" in line or "validation" in line for line in said), said
    assert _stored(root).approval == dec.PENDING


def test_a_rejected_plan_is_RE_AUTHORED_with_its_reasons(tmp_path):
    """🔴 **§3.3b's new transition, and it lands with its bound.** A REJECTED
    plan used to block for ever: `advance_ticket` re-authored only when there
    was no plan at all, so a reviewer that said no stopped the ticket rather
    than improving it. It is re-authored now, WITH the reasons as the
    decomposer's input — and bounded, because an unbounded re-author is a loop
    and not a fix.

    ⚠ The plan is re-authored BEFORE the stage moves. `decomposed`'s gate
    wants a PENDING plan and a rejected one is REJECTED, so the move is only
    legal once a new plan exists — the artifact leads and the gate is what
    makes that true.
    """
    root = _project(tmp_path)
    d = _Driver(root)
    _write_plan(root, approval=dec.REJECTED, returns=("the slicing crosses modules",))

    got = _advance(root, d)
    assert got.stage == DECOMPOSED, got
    assert "re-authored" in got.note
    assert d.calls == ["decompose:planner"]
    # The definition of done comes first and the reasons are under their own
    # heading: a plan re-authored against the reasons alone would answer the
    # review rather than the ticket.
    text = d.texts[0]
    assert text.index("Agreed definition of done") < text.index("was REJECTED")
    assert "the slicing crosses modules" in text
    assert "do not argue with them" in text
    assert _stored(root).approval == dec.PENDING


def test_a_plan_rejected_past_the_bound_is_escalated_not_re_authored(tmp_path):
    """The bound, and it is `Decomposition.returns` — the count RL-10 already
    keeps. A plan re-sliced three times that still does not pass is not a plan
    one more turn fixes."""
    from rite_ai.local.recompose import MAX_RETURNS

    root = _project(tmp_path)
    d = _Driver(root)
    _write_plan(
        root,
        approval=dec.REJECTED,
        returns=tuple(f"reason {n}" for n in range(MAX_RETURNS)),
    )
    got = _advance(root, d)
    assert not got.moved, got
    assert f"{MAX_RETURNS} is the bound" in got.blocked
    assert "somebody has to look at the ticket" in got.blocked
    assert "reason 0" in got.blocked
    assert d.calls == [], "nothing is re-authored past the bound"


def test_a_decomposer_that_produced_nothing_is_reported_not_retried_blindly(tmp_path):
    root = _project(tmp_path)

    def failed(root_, manager, ticket, *, ticket_text=""):
        return type(
            "R", (), {"wrote": False, "problem": "", "reasons": ("no cites",)}
        )()

    _define(root)
    blocked = advance_ticket(root, "planner", TICKET, author_plan=failed)
    assert not blocked.moved
    assert "no cites" in blocked.blocked


# ── choosing a reviewer ──────────────────────────────────────────────────────


def test_the_reviewer_is_a_different_model_holder(tmp_path):
    root = _project(tmp_path)
    plan = _write_plan(root)
    who, why = independent_reviewer(root, plan)
    assert who == "lead", why


def test_no_independent_holder_is_said_rather_than_guessed(tmp_path):
    root = _project(tmp_path, author="qwen3.8:latest", reviewer="qwen3.8:latest")
    plan = _write_plan(root)
    who, why = independent_reviewer(root, plan)
    assert who == ""
    assert "same model" in why


# ── the per-cycle pass ───────────────────────────────────────────────────────


def test_the_pass_stops_after_one_ticket_moves(tmp_path):
    """One ticket per pass, so a tick costs one stage and not several.

    ⚠ `advance` is injected, and the first version of this test did not inject
    it: the pass fell through to a second ticket, reached the production
    defaults, and ran a REAL decomposition for four and a half minutes. That is
    why the seam exists.
    """
    root = _project(tmp_path)
    said: list[str] = []
    seen: list[str] = []

    def advance(root_, manager, ticket):
        seen.append(ticket)
        return Advance(ticket, stage=STEPPED, note="a subtask accepted")

    moved = drive_local_tier(
        root, "planner", [TICKET, "T-2"], said.append, advance=advance
    )
    assert seen == [TICKET], seen
    assert len(moved) == 1
    assert any("local tier" in line for line in said)


def test_a_pass_tries_the_next_ticket_when_one_cannot_move(tmp_path):
    root = _project(tmp_path)
    seen: list[str] = []

    def advance(root_, manager, ticket):
        seen.append(ticket)
        if ticket == TICKET:
            return Advance(ticket, blocked="its plan was rejected")
        return Advance(ticket, stage=STEPPED)

    drive_local_tier(root, "planner", [TICKET, "T-2"], None, advance=advance)
    assert seen == [TICKET, "T-2"], seen


def test_a_ticket_that_cannot_move_is_still_reported(tmp_path):
    # Silence is indistinguishable from nothing to do, which is what the spin
    # guard exists to catch.
    root = _project(tmp_path)
    said: list[str] = []
    drive_local_tier(
        root,
        "planner",
        [TICKET],
        said.append,
        advance=lambda r, m, t: Advance(t, blocked="its plan was rejected"),
    )
    assert any("not advanced" in line for line in said), said


# ── the record of the run (SCRUM-72 §3.3a) ───────────────────────────────────


def test_the_run_leaves_a_log_naming_every_stage_once_in_order(tmp_path):
    """§3.3a's last guard test: "the end-to-end run's record must show every
    stage in order". With stub agents, driven only by rite's own code — nobody
    types a command and nothing but `stage.advance` writes the record.

    ⚠ `stepping` appears ONCE though two subtasks ran. Which subtask is where
    is the plan's business; the ticket enters `stepping` once, which is why the
    table has no self-loop.
    """
    root = _project(tmp_path)
    d = _Driver(root)
    answered = False
    for _ in range(12):
        got = _advance(root, d)
        if not got.moved:
            if answered:
                break
            # The one point a Manager is involved, and it is a real answer
            # from a real directory rather than a stamp.
            _review(root)
            answered = True
            continue

    record = st.read(_state(root), TICKET).record
    assert [t.to for t in record.log] == [
        DEFINED,
        DECOMPOSED,
        APPROVED,
        STEPPED,
        RECOMPOSED,
        DELIVERY_ASKED,
    ], [t.to for t in record.log]
    # Each entry says where it came from, so the chain is checkable rather
    # than a list of claims.
    assert [t.frm for t in record.log] == [
        "",
        DEFINED,
        DECOMPOSED,
        APPROVED,
        STEPPED,
        RECOMPOSED,
    ]
    # And the two inference stages are the only ones that ran a model.
    assert d.calls == ["decompose:planner", "step:planner", "step:planner"]


def test_the_pipeline_cannot_be_driven_round_from_the_middle(tmp_path):
    """A plan written straight to APPROVED with no stage record is adopted at
    APPROVED — and the adoption is gated: it cannot claim a stage the plan does
    not say it reached, and the log says it was adopted rather than passed."""
    root = _project(tmp_path)
    _write_plan(root, approval=dec.APPROVED, approved_by="lead")
    record = st.read(_state(root), TICKET).record
    assert record.stage == APPROVED
    assert "adopted" in record.log[0].why

    # The same plan at PENDING cannot be adopted at APPROVED, however it is
    # asked: `approve_plan` is the only writer of approved.
    other = _project(tmp_path / "second")
    _write_plan(other, approval=dec.PENDING)
    assert st.read(_state(other), TICKET).record.stage == DECOMPOSED
    from rite_ai.local.gates import gate_for

    refused = st.adopt(
        _state(other),
        TICKET,
        APPROVED,
        gate=gate_for(other, "planner", TICKET, _state(other)),
        definition=_pinned(other),
    )
    assert isinstance(refused, st.Refused)


def test_a_decomposer_that_claims_it_wrote_a_plan_but_did_not_does_not_advance(
    tmp_path,
):
    """🔴 **The artifact decides, not the report.** `DecomposeResult.wrote` is
    the decomposer's own claim; the stage only moves if the gate can find the
    plan. A loop that trusted the claim would record DECOMPOSED for a ticket
    with nothing on disk — and then every later gate would be checking a plan
    that is not there."""
    root = _project(tmp_path)
    _define(root)

    def claims_it_wrote(root_, manager, ticket, *, ticket_text=""):
        return type("R", (), {"wrote": True, "problem": "", "reasons": ()})()

    got = advance_ticket(root, "planner", TICKET, author_plan=claims_it_wrote)
    assert not got.moved, got
    assert "has no decomposition" in got.blocked
    assert st.read(_state(root), TICKET).stage == DEFINED


def test_a_plan_returned_to_review_takes_the_stage_back_with_it(tmp_path):
    """A composition conflict, a recomposition failure or a rejection after
    approval all call `decomposition.returned_to_plan_review`, which drops the
    approval. The stage FOLLOWS the artifact back to review — otherwise it
    stays at `stepping` forever, and the one thing that says what may run next
    disagrees with the one thing that says whether anything may run at all."""
    root = _project(tmp_path)
    d = _Driver(root)
    _define(root)
    _advance(root, d)  # decomposed
    _advance(root, d)  # the review is asked for
    _review(root)  # lead answers
    _advance(root, d)  # approved
    _advance(root, d)  # stepping
    assert st.read(_state(root), TICKET).stage == STEPPED

    state = _state(root)
    read = dec.read(state, TICKET)
    dec.write(
        state,
        dec.returned_to_plan_review(read.plan, "its verify fails on the ticket branch"),
        read.version,
    )
    before = len(d.calls)

    got = advance_ticket(root, "planner", TICKET, step=d.step, author_plan=d.decompose)
    assert got.stage == DECOMPOSED, got
    assert "returned to plan review" in got.note or "no longer approved" in got.note
    assert st.read(_state(root), TICKET).stage == DECOMPOSED
    assert len(d.calls) == before, "it must not run a subtask from an unapproved plan"
    # And the log keeps both: RL-10 counts returns.
    log = [t.to for t in st.read(_state(root), TICKET).record.log]
    assert log == [DEFINED, DECOMPOSED, APPROVED, STEPPED, DECOMPOSED]


def test_the_decomposer_is_given_the_agreed_definition_of_done(tmp_path):
    """🔴 SCRUM-72d. `advance_ticket` called `author_plan` with no ticket text
    at all, so the decomposer saw "(no ticket text was supplied)" — it sliced
    a ticket by its ID while the signed definition of done sat in a record
    nothing read. §3.3a's "the refinement record gates the start but is never
    an input to the work", in one line of code.

    ⚠ It is `record.render_for_worker`'s text, not a second wording: the SAME
    text the Worker's start prompt carries, so the plan is sliced against what
    the Worker will be held to rather than against a paraphrase."""
    root = _project(tmp_path)
    d = _Driver(root)
    _define(root)
    _advance(root, d)

    assert len(d.texts) == 1
    text = d.texts[0]
    assert text, "the decomposer must not be asked to slice a ticket by its id"
    assert "Agreed definition of done for T-1" in text
    assert "a.txt says the thing" in text
    assert "b.txt says the other thing" in text
    assert "In scope:" in text and "a.txt" in text
    assert "no ticket text was supplied" not in text

    # And it is exactly what the Worker's own start prompt carries.
    from rite_ai.refinement import record as rec

    assert text == rec.render_for_worker(rec.from_payload(_refinement_payload()))


def test_a_definition_that_changed_under_the_pipeline_halts_it(tmp_path):
    """🔴 SCRUM-72d, the STALE halt. The definition of done is read from the
    Worker's publish-record snapshot, and that snapshot is REWRITTEN when the
    Worker is started again — on a re-refined ticket, or on a different one.
    A plan authored against one definition must not go on being reviewed,
    stepped and delivered against another."""
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record
    from rite_ai.refinement import record as rec

    root = _project(tmp_path)
    d = _Driver(root)
    _define(root)
    _advance(root, d)  # decomposed, against the first definition
    first = st.read(_state(root), TICKET).record.definition
    assert first

    # The ticket is re-refined and the Worker started again: a NEW record.
    renewed = rec.build(
        ticket=TICKET,
        board={"type": "none", "project": "acme"},
        title=f"{TICKET}: the thing",
        description="Do the thing, but differently.",
        definition_of_done=["c.txt says something else entirely"],
        verify=["pytest -q"],
        provenance={"kind": rec.ACCEPTED, "answered_by": {"owner_user": "robert"}},
        supersedes=None,
        key=b"k" * 32,
    ).payload()
    assert renewed["record_id"] != first
    record.write(
        root,
        "alpha",
        TICKET,
        parse_config(root / ".rite" / "config.yaml"),
        parse_modules(root / ".rite" / "modules.yaml"),
        refinement=renewed,
    )

    before = st.read(_state(root), TICKET).record
    got = _advance(root, d)
    assert not got.moved, got
    assert "definition of done CHANGED" in got.blocked
    assert first in got.blocked and renewed["record_id"] in got.blocked
    # Nothing is undone and nothing is guessed: which definition it is now
    # meant to satisfy is not rite's to decide.
    assert st.read(_state(root), TICKET).record == before
    assert _stored(root).approval == dec.PENDING
    assert d.calls == ["decompose:planner"], "no further work ran"


def test_the_pinned_definition_is_carried_not_re_pinned(tmp_path):
    """Pinned once, at `defined`, and carried unchanged. A later transition
    that could re-pin it would be one that could silently re-point a plan at a
    definition nobody authored it against."""
    root = _project(tmp_path)
    d = _Driver(root)
    _define(root)
    pinned = st.read(_state(root), TICKET).record.definition
    assert pinned

    _advance(root, d)
    _advance(root, d)  # the review is asked for
    _review(root)
    _advance(root, d)  # approved
    for entry in st.read(_state(root), TICKET).record.log:
        assert entry.to in (DEFINED, DECOMPOSED, APPROVED)
    assert st.read(_state(root), TICKET).record.definition == pinned


def test_the_subtask_slice_carries_the_definition_of_done_too(tmp_path):
    """§3.3a: the snapshot is fed "to the decomposer AND the slice". Without
    it a subtask ran against spec units and its own one-line intent, while the
    signed definition of done — the thing `deliver` later holds the work
    against — sat in a record nothing in that path read."""
    from rite_ai.local.step import _slice_for

    root = _project(tmp_path)
    _spec_units(root, "5.1")
    text, problem = _slice_for(root, ("5.1",), "AGREED DEFINITION HERE")
    assert not problem
    assert text.startswith("AGREED DEFINITION HERE"), text[:80]
    assert "Section 5.1: the rule." in text
    assert "how this codebase does the thing above" in text

    # With no definition the slice is exactly what it was: the spec alone.
    plain, problem = _slice_for(root, ("5.1",))
    assert not problem
    assert plain == "Section 5.1: the rule.\n"


def test_the_executor_asks_for_the_definition_it_is_given(tmp_path):
    """Wired, not only written — `test_no_dead_wiring`'s lesson. `take_one_step`
    must pass the definition into the slice, or the paragraph above is a
    function nothing calls with its third argument."""
    import inspect

    from rite_ai.local import step as step_module

    src = inspect.getsource(step_module.take_one_step)
    wanted = (
        "_slice_for(\n"
        "        root, subtask.cites, "
        "_definition_for(root, manager, ticket)\n"
        "    )"
    )
    assert wanted in src
