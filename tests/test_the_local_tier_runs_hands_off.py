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

from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.local import decomposition as dec
from rite_ai.local.loop import (
    APPROVED,
    DECOMPOSED,
    DELIVERY_ASKED,
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
    return tmp_path


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


def _state(root: Path):
    return LocalStateLayer(root / ".rite")


def _write_plan(root: Path, **kw):
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
    return plan


def _stored(root: Path):
    return dec.read(_state(root), TICKET).plan


# ── the sequence, hands-off ──────────────────────────────────────────────────


class _Driver:
    """Stands in for the two INFERENCE stages only.

    Decompose and step are the two that run a model; approval and the delivery
    request are rite's own deterministic code and are NOT stubbed anywhere in
    this file.
    """

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[str] = []

    def decompose(self, root, manager, ticket):
        self.calls.append(f"decompose:{manager}")
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
    return advance_ticket(
        root, manager, TICKET, author_plan=driver.decompose, step=driver.step, **kw
    )


def test_it_drives_decompose_then_approve_then_step_with_no_commands(tmp_path):
    """The whole point. Nobody types anything; each pass advances one stage."""
    root = _project(tmp_path)
    d = _Driver(root)

    first = _advance(root, d)
    assert first.stage == DECOMPOSED, first
    assert _stored(root).approval == dec.PENDING, "a decomposer may not approve"

    second = _advance(root, d)
    assert second.stage == APPROVED, second
    # Approved by the OTHER Manager, through the real approve_plan.
    assert _stored(root).approved_by == "lead"
    assert _stored(root).released

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
    # A Worker of this Manager, recorded as working the ticket.
    worker_dir = root / "workers" / "alpha"
    worker_dir.mkdir(parents=True)
    (worker_dir / "worker.yml").write_text(
        "worker:\n  name: alpha\n  manager: planner\n  modules: []\n"
    )
    from rite_ai.config.parse import parse_config, parse_modules
    from rite_ai.publishing import record

    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    record.write(
        root,
        "alpha",
        TICKET,
        parse_config(root / ".rite" / "config.yaml"),
        parse_modules(root / ".rite" / "modules.yaml"),
    )
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
    where = requests.requests_dir(root, "planner")
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{TICKET}.json").write_text("{}")
    got = advance_ticket(root, "planner", TICKET)
    assert not got.moved
    assert "already waiting" in got.blocked


# ── it refuses to advance when a gate fails ──────────────────────────────────


def test_a_same_model_reviewer_cannot_approve_so_nothing_advances(tmp_path):
    """RL-6 as Robert ruled it. Two 'local:' labels on one model are one model."""
    root = _project(tmp_path, author="qwen3.8:latest", reviewer="qwen3.8:latest")
    d = _Driver(root)
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
    _advance(root, d)
    assert not _advance(root, d).moved
    assert _stored(root).approval == dec.PENDING


def test_a_lone_decomposer_cannot_approve_its_own_plan(tmp_path):
    """DD-3.5: the gate is worthless if the thing it gates can set it."""
    root = _project(tmp_path, solo=True)
    d = _Driver(root)
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
    blocked = advance_ticket(root, "planner", TICKET)
    assert not blocked.moved
    assert "9.9" in blocked.blocked or "validation" in blocked.blocked
    assert _stored(root).approval == dec.PENDING


def test_a_rejected_plan_goes_back_rather_than_round(tmp_path):
    root = _project(tmp_path)
    _write_plan(root, approval=dec.REJECTED, returns=("the slicing crosses modules",))
    blocked = advance_ticket(root, "planner", TICKET)
    assert not blocked.moved
    assert "rejected" in blocked.blocked
    assert "crosses modules" in blocked.blocked


def test_a_decomposer_that_produced_nothing_is_reported_not_retried_blindly(tmp_path):
    root = _project(tmp_path)

    def failed(root_, manager, ticket):
        return type(
            "R", (), {"wrote": False, "problem": "", "reasons": ("no cites",)}
        )()

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
