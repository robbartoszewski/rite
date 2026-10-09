"""Level 1 — the decomposer fills the one hole: it writes a plan (RL-6..RL-8,
RL-68, DD-1.2/DD-2.4/DD-4).

Every assertion here is about what rite DOES with a plan, never about what a
model produces (DD-4.1): the proposer is injected and returns hand-built bytes,
and the test drives the REAL caller (`decompose_ticket`) and asserts on what
reached `write()` — the lesson from the local-tier branch, where the one
mutation that survived did so because the tests exercised the helpers and not
the caller (DD-4.3).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from rite_ai.config.managers import ManagerRole
from rite_ai.local import decompose as dcmp
from rite_ai.local import decomposition as dec
from rite_ai.local import plan_state
from rite_ai.spec.digest_files import unit_filename, units_dir

ROLES = [
    ManagerRole(
        name="planner",
        engine="local:small",
        endpoint="http://localhost:11434/v1",
        model="qwen3.8:latest",
        agent="goose",
        context_window=32768,
        duties=("decompose",),
    ),
    ManagerRole(name="lead", engine="claude", preset="lead"),
]


def _project(cites=("U1", "U2")):
    root = Path(tempfile.mkdtemp())
    (root / ".rite").mkdir()
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    for cite in cites:
        (where / unit_filename(cite)).write_text("spec text for " + cite)
    return root, plan_state.layer(root)


def _candidate(ticket="KAN-1", **over):
    body = {
        "format_version": 1,
        "ticket": ticket,
        "decomposed_by": "planner",
        "subtasks": [
            {
                "id": "s1",
                "intent": "add the auth route",
                "scope": ["src/a.py"],
                "verify": "pytest -q tests/a.py",
                "cites": ["U1"],
            },
            {
                "id": "s2",
                "intent": "wire the auth config",
                "scope": ["src/b.py"],
                "verify": "pytest -q tests/b.py",
                "cites": ["U2"],
            },
        ],
    }
    body.update(over)
    return json.dumps(body).encode("utf-8")


class _Proposer:
    """Returns the hand-built bytes in order. `calls` keeps every prompt it was
    given, so a test can assert the rejection was fed back (DD-3.3)."""

    def __init__(self, *proposals):
        self.queue = list(proposals)
        self.calls: list[str] = []

    def propose(self, prompt, workspace):
        self.calls.append(prompt)
        return self.queue.pop(0)


def test_a_valid_candidate_is_written_pending_never_approved():
    root, state = _project()
    result = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-1",
        proposer=_Proposer(dcmp.Proposal(bytes=_candidate())),
        state=state,
        roles=ROLES,
        ticket_text="add the auth route and config",
    )
    assert result.wrote
    assert result.attempts == 1
    stored = dec.read(state, "KAN-1").plan
    assert stored is not None
    # ⚠ The APPROVED gate is worthless if the decomposer can set it: the write
    # is PENDING whatever the model emitted (DD-3.5).
    assert stored.approval == dec.PENDING
    assert stored.approved_by == ""
    assert stored.decomposed_by == "planner"
    assert [s.id for s in stored.subtasks] == ["s1", "s2"]


def test_rite_records_the_manager_it_asked_not_the_author_the_model_claimed():
    # The production defect the review caught: `_prompt_for` hands the model a
    # template with `decomposed_by: ""`, which RL-67 refuses on the production
    # path (decompose_managers is always non-empty there) — so a model that
    # copies rite's own template escalated every ticket. The author is rite's:
    # the Manager it asked, filled before validation.
    #
    # (a) A prompt-compliant plan with an EMPTY author is written, not refused.
    root, state = _project()
    empty = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-1",
        proposer=_Proposer(dcmp.Proposal(bytes=_candidate(decomposed_by=""))),
        state=state,
        roles=ROLES,
        max_attempts=1,
    )
    assert empty.wrote
    assert empty.attempts == 1  # written on the first try, not after a retry
    assert dec.read(state, "KAN-1").plan.decomposed_by == "planner"

    # (b) A model that CLAIMS a different author is ignored: the recorded author
    # is the Manager that ran, so RL-6's independence check reads the truth.
    root, state = _project()
    claimed = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-2",
        proposer=_Proposer(
            dcmp.Proposal(bytes=_candidate(decomposed_by="someone-else"))
        ),
        state=state,
        roles=ROLES,
        max_attempts=1,
    )
    assert claimed.wrote
    assert dec.read(state, "KAN-2").plan.decomposed_by == "planner"


def test_a_candidate_that_marks_itself_approved_is_refused():
    # DD-4.2: a plan already marked APPROVED by its author must be refused — the
    # gate the APPROVED state exists to be cannot be set by the thing it gates.
    root, state = _project()
    result = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-1",
        proposer=_Proposer(dcmp.Proposal(bytes=_candidate(approval="approved"))),
        state=state,
        roles=ROLES,
        max_attempts=1,
    )
    assert not result.wrote
    assert result.escalated
    assert dec.read(state, "KAN-1").plan is None
    assert any("approved" in r for r in result.reasons)


def test_one_invalid_subtask_refuses_the_whole_plan_and_writes_nothing():
    # ⚠ The mutation that matters most (DD-4.3): partial acceptance. Four valid
    # subtasks and one with an unresolvable cite must refuse ALL FIVE and write
    # nothing — dropping the bad one changes the claim the decomposition makes.
    root, state = _project(cites=("U1", "U2", "U3", "U4"))
    body = json.loads(_candidate())
    body["subtasks"] = [
        {
            "id": f"s{i}",
            "intent": f"part {i}",
            "scope": [f"src/p{i}.py"],
            "verify": f"pytest -q tests/p{i}.py",
            "cites": [c],
        }
        for i, c in enumerate(["U1", "U2", "U3", "U4", "U404"], start=1)
    ]
    bytes_ = json.dumps(body).encode()
    result = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-7",
        proposer=_Proposer(dcmp.Proposal(bytes=bytes_)),
        state=state,
        roles=ROLES,
        max_attempts=1,
    )
    assert not result.wrote
    assert dec.read(state, "KAN-7").plan is None  # nothing ran, nothing written
    assert any("U404" in r for r in result.reasons)


def test_a_rejection_is_fed_back_and_a_corrected_retry_is_written():
    # DD-3.3: the decomposer is asked again WITH the reasons as input. A first
    # attempt citing a missing unit, then a corrected one, writes on attempt 2.
    root, state = _project()
    bad = json.loads(_candidate())
    bad["subtasks"][0]["cites"] = ["U404"]
    proposer = _Proposer(
        dcmp.Proposal(bytes=json.dumps(bad).encode()),
        dcmp.Proposal(bytes=_candidate()),
    )
    result = dcmp.decompose_ticket(
        root, "planner", "KAN-1", proposer=proposer, state=state, roles=ROLES
    )
    assert result.wrote
    assert result.attempts == 2
    # The second prompt carried the first rejection (RL-63's message).
    assert "REFUSED" in proposer.calls[1]
    assert "U404" in proposer.calls[1]


def test_identical_reasons_on_retry_stop_the_loop_early():
    # RL-69: a retry whose reasons do not change has converged on failure;
    # spending the last attempt proves nothing, so the loop stops early.
    root, state = _project()
    one = json.loads(_candidate())
    one["subtasks"] = one["subtasks"][:1]  # RL-66, deterministic reason
    same = json.dumps(one).encode()
    proposer = _Proposer(
        dcmp.Proposal(bytes=same),
        dcmp.Proposal(bytes=same),
        dcmp.Proposal(bytes=same),  # a third, which must never be asked for
    )
    result = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-2",
        proposer=proposer,
        state=state,
        roles=ROLES,
        max_attempts=3,
    )
    assert not result.wrote
    assert result.converged_early and result.escalated
    assert result.attempts == 2  # stopped after the identical retry, not 3
    assert len(proposer.calls) == 2


def test_an_infrastructure_fault_is_not_an_attempt():
    # RL-47: the turn did not happen, so no attempt was made and the loop does
    # not spin the budget on a down endpoint.
    root, state = _project()
    result = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-3",
        proposer=_Proposer(
            dcmp.Proposal(infrastructure_fault=True, problem="endpoint down")
        ),
        state=state,
        roles=ROLES,
    )
    assert result.attempts == 0
    assert not result.escalated
    assert "infrastructure fault" in result.problem
    assert dec.read(state, "KAN-3").plan is None


def test_a_plan_nobody_may_author_is_refused_by_the_real_caller():
    """SCRUM-83 refined: a driver that holds no authoring duty DELEGATES to the
    one Manager that does (`decompose.author_for`, and
    `test_a_worker_is_owned_by_a_manager_that_can_drive_it` covers the
    delegation and its attribution). What is left to refuse is a project where
    nobody holds the duty at all — asserted here, through the real caller,
    because a refusal that only `author_for` makes is a refusal the caller may
    not be asking for.

    ⚠ This test asserted the FIRST shape of SCRUM-83 — "the driver must hold
    `decompose` itself" — and kept asserting it after `author_for` was
    refined, which left it red rather than wrong-and-green. It is corrected to
    the rule that shipped, not relaxed: a plan is still never authored by a
    project with no holder."""
    root, state = _project()
    nobody_authors = [r for r in ROLES if r.name == "lead"]
    result = dcmp.decompose_ticket(
        root,
        "lead",  # holds plan-review/execute, not decompose
        "KAN-9",
        proposer=_Proposer(dcmp.Proposal(bytes=_candidate())),
        state=state,
        roles=nobody_authors,
    )
    assert not result.wrote
    assert "no Manager in this project does" in result.problem
    assert dec.read(state, "KAN-9").plan is None


def test_the_cli_command_is_wired_and_reports_a_refusal(monkeypatch):
    # `rite local decompose` reaches the caller: a project where NOBODY holds
    # the duty is refused with exit 1, no model consulted. ⚠ The fleet it used
    # to declare — a `planner` holding `decompose` beside `lead` — is now the
    # DELEGATING fleet and reaches the model, which is the gate scenario's own
    # composition, so it cannot also stand for a refusal here.
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = Path(tempfile.mkdtemp())
    (root / ".rite").mkdir()
    (root / ".rite" / "brief.yaml").write_text(
        "project:\n  name: demo\n  role: owner\n"
    )
    (root / ".rite" / "config.yaml").write_text(
        "coordination:\n  managers: [lead, second]\n  manager_roles:\n"
        "  - {name: lead, engine: claude, preset: lead}\n"
        "  - {name: second, engine: claude, preset: lead}\n"
    )
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, ["local", "decompose", "lead", "KAN-1"])
    assert result.exit_code == 1
    assert "no Manager in this project does" in result.output
    # 🔴 No model was consulted: an infrastructure fault would say so, and a
    # refusal that reached the engine is not a refusal.
    assert "infrastructure fault" not in result.output


def test_an_approved_plan_is_not_overwritten():
    # Overwriting a released plan would silently drop its approval.
    root, state = _project()
    approved = dec.Decomposition(
        ticket="KAN-1",
        subtasks=(dec.Subtask(id="s1", intent="x", scope=("src/a.py",), verify="t"),),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="lead",
    )
    dec.write(state, approved, dec.read(state, "KAN-1").version)
    result = dcmp.decompose_ticket(
        root,
        "planner",
        "KAN-1",
        proposer=_Proposer(dcmp.Proposal(bytes=_candidate())),
        state=state,
        roles=ROLES,
    )
    assert not result.wrote
    assert "already has an approved decomposition" in result.problem
    assert dec.read(state, "KAN-1").plan.approval == dec.APPROVED  # untouched
