"""SCRUM-83: a Worker goes to a Manager that can actually drive it.

Nothing gated WHO may ask for a Worker. `broker.decide` checked the request's
size, its JSON, that the Worker and the ticket exist and that a slot was free —
and no duty at all. So in a mixed fleet whichever Manager asked first owned the
Worker, and ownership is load-bearing three times:

- only the owner may gate, step, stop or inspect it (`lifecycle._may_act`);
- only the owner's cycle drives its ticket (`_local_tier_tickets`);
- only the `decompose` holder can author a plan.

A Manager that cannot author, holding a Worker whose plan rite must author, is
a deadlock that no configuration escapes. Measured 2026-10-08: a Claude Manager
holding only `plan-review` won the GPU Worker, and the ticket never left
`defined`.

⚠ **Gated on DUTY and CAPABILITY, never on a provider** (Robert, 2026-10-08:
the Manager/Worker interfaces are to be provider- and platform-agnostic). The
Worker is asked whether rite must author its plan, not whether it is local; the
Manager is asked whether it holds `decompose`, not whether it is Claude.
`test_the_gate_names_no_provider` is what keeps that true.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from rite_ai.managers import broker


def _project(tmp_path, *, roles: str, worker_engine: str) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncoordination:\n  manager_roles:\n" + roles
    )
    d = tmp_path / "workers" / "gpu1"
    d.mkdir(parents=True, exist_ok=True)
    body = "worker:\n  name: gpu1\n  manager: lead\n  modules: []\n"
    if worker_engine != "claude":
        body += (
            f"  engine: {worker_engine}\n  model: qwen3.8:latest\n"
            "  endpoint: http://localhost:11434\n  agent: goose\n"
            "  context_window: 32768\n"
        )
    (d / "worker.yml").write_text(body)
    return tmp_path.resolve()


_AUTHOR_AND_APPROVER = (
    "  - name: lead\n    engine: claude\n    duties: [plan-review, integrate]\n"
    "  - name: planner\n    engine: local:small\n    model: qwen3.8:latest\n"
    "    endpoint: http://localhost:11434\n    agent: goose\n"
    "    context_window: 32768\n    duties: [decompose, board, route]\n"
)


def test_an_owner_that_can_DELEGATE_the_authoring_duty_may_own_the_worker(tmp_path):
    """🔴 SCRUM-83 refined: HOLD **OR DELEGATE**.

    The first shape of this rule required the owner to hold `decompose`
    itself, which forced owner, Worker-starter and plan author to be one
    Manager — and excluded the only fleet shape that works, because the
    Manager measured to drive step 1 reliably cannot author (DD-2.4) and the
    one that authors reaches step 1 only sometimes.
    """
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    assert broker._may_own_worker(root, "lead", "gpu1") == "", (
        "an owner that delegates authoring to the one Manager holding the duty "
        "was refused the Worker"
    )


def test_the_delegation_does_not_reintroduce_the_deadlock(tmp_path):
    """🔴 The control Robert asked for by name.

    The deadlock was "the owner cannot author AND the author cannot touch the
    Worker". Delegation breaks the FIRST half, so it is not enough that the
    gate says yes — the authoring path must then actually produce a plan under
    the owner's own driving cycle, attributed to the delegate.
    """
    from rite_ai.config.parse import parse_config
    from rite_ai.local.decompose import author_for

    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    roles = parse_config(root / ".rite" / "config.yaml").coordination.manager_roles

    # the gate lets `lead` own it...
    assert broker._may_own_worker(root, "lead", "gpu1") == ""
    # ...and the authoring path agrees who writes the plan, rather than
    # refusing `lead` as it used to.
    author, problem = author_for(roles, "lead")
    assert problem == "", problem
    assert author == "planner", author


def test_the_gate_and_the_authoring_path_cannot_disagree(tmp_path):
    """They ask the SAME function. A gate that accepted an arrangement the
    authoring path refused is exactly how the deadlock arose."""
    import ast
    import inspect

    src = inspect.getsource(broker._may_own_worker)
    assert "author_for" in src, (
        "the gate decides ownership by its own rule instead of the one the "
        "decomposer uses"
    )
    del ast


def test_the_manager_that_authors_may_own_it(tmp_path):
    """The control that keeps the gate from refusing everything."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    assert broker._may_own_worker(root, "planner", "gpu1") == ""


def test_a_worker_that_plans_its_own_work_may_be_owned_by_anyone(tmp_path):
    """The second control, and the one that stops this becoming a rule about
    Claude Managers: a Worker rite does not author for is nobody's special
    case, and the Manager holding only `plan-review` may own it."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="claude")
    assert broker._may_own_worker(root, "lead", "gpu1") == ""


def test_an_unreadable_manifest_is_a_refusal(tmp_path):
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    (root / "workers" / "gpu1" / "worker.yml").write_text("{{{ not yaml")
    why = broker._may_own_worker(root, "planner", "gpu1")
    assert why, "an unreadable Worker was handed over anyway"


def test_an_unreadable_project_is_a_refusal(tmp_path):
    """Fail closed: a config nobody could read must not read as "no duties
    required"."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    (root / ".rite" / "config.yaml").write_text("coordination: [[[")
    assert broker._may_own_worker(root, "planner", "gpu1") != ""


def test_nobody_holding_the_duty_is_a_refusal_that_says_so(tmp_path):
    """Delegation needs somebody to delegate TO."""
    roles = (
        "  - name: lead\n    engine: claude\n    duties: [plan-review]\n"
        "  - name: other\n    engine: claude\n    duties: [board]\n"
    )
    root = _project(tmp_path, roles=roles, worker_engine="local:small")
    why = broker._may_own_worker(root, "lead", "gpu1")
    assert why, "a Worker was handed over with nobody able to author its plan"
    assert "no Manager in this project does" in why, why


def test_several_holders_is_also_a_refusal_rather_than_a_guess(tmp_path):
    """🔴 Fail closed on ambiguity. Picking one would make which model authored
    a plan depend on iteration order, and RL-6 compares the author with the
    approver — so the wrong pick silently changes what independence means."""
    roles = (
        "  - name: lead\n    engine: claude\n    duties: [plan-review]\n"
        "  - name: p1\n    engine: local:small\n    model: qwen3:8b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n    duties: [decompose]\n"
        "  - name: p2\n    engine: local:small\n    model: qwen3:14b\n"
        "    endpoint: http://localhost:11434\n    agent: goose\n"
        "    context_window: 32768\n    duties: [decompose]\n"
    )
    root = _project(tmp_path, roles=roles, worker_engine="local:small")
    why = broker._may_own_worker(root, "lead", "gpu1")
    assert why, "rite guessed which of two Managers should author"
    assert "cannot tell which" in why, why


# ---- provider agnosticism, which is the standing requirement ---------------


def test_the_gate_names_no_provider():
    """🔴 Robert's guideline, as a test.

    `may_own_worker` must decide from a DUTY and a CAPABILITY. Read by AST with
    the DOCSTRING DROPPED — it has to discuss Claude, because that is where
    this was measured, and the first version of this test failed on its own
    explanation.
    """
    tree = ast.parse(inspect.getsource(broker._may_own_worker))
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    body_nodes = fn.body[1:] if ast.get_docstring(fn) else fn.body
    body = "\n".join(ast.unparse(n) for n in body_nodes)

    # The capability is the question asked of the Worker.
    assert "needs_authored_plan" in body, "the gate does not ask the capability"
    # The duty is the question asked of the Manager.
    assert "DECOMPOSE" in body or "decompose" in body, "the gate asks no duty"
    # And no provider name decides anything.
    for provider in ("claude", "goose", "ollama", "codex", "gemini", "cursor"):
        assert provider not in body.lower(), (
            f"the gate branches on {provider!r}, so a new provider would need "
            "another special case here"
        )
    assert "is_local" not in body, (
        "the gate reads `is_local`, which is a question about an engine; ask "
        "`needs_authored_plan` so a new provider needs no change here"
    )


def test_the_capability_is_what_the_driver_asks_too():
    """The driver and the gate must ask the same question, or a Worker could be
    owned by a Manager whose cycle still would not drive it."""
    from rite_ai.cli import main

    src = inspect.getsource(main._local_worker_holds)
    assert "needs_authored_plan" in src, src


def test_the_gate_is_actually_wired_into_the_broker():
    """Dead wiring: a gate nothing calls is a comment. By AST."""
    tree = ast.parse(inspect.getsource(broker.for_project))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_may_own_worker" in called, "for_project never consults the gate"


def test_the_supervisor_tells_the_broker_which_manager_is_asking():
    from rite_ai.managers import supervise

    src = inspect.getsource(supervise)
    assert "broker(raw, manager)" in src, (
        "the broker is called without the asking Manager, so the gate cannot "
        "know whose request it is"
    )


@pytest.mark.parametrize("engine", ["local:small", "local:large", "local:anything"])
def test_every_local_class_needs_an_authored_plan(tmp_path, engine):
    """The capability must not be tied to one local CLASS either."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine=engine)
    # Either Manager may own it now: `planner` holds the duty, `lead` delegates.
    assert broker._may_own_worker(root, "planner", "gpu1") == ""
    assert broker._may_own_worker(root, "lead", "gpu1") == ""
    # ...and the capability is what decides, for every local class.
    from rite_ai.sandbox import worker_manifest

    assert worker_manifest(root, "gpu1").needs_authored_plan


def test_the_author_is_chosen_by_duty_alone():
    """Robert's standing guideline, applied to the delegation too: the choice of
    author must not look at an engine or a provider name. By AST, docstring
    dropped — the docstring necessarily discusses Claude."""
    import ast
    import inspect

    from rite_ai.local.decompose import author_for

    tree = ast.parse(inspect.getsource(author_for))
    fn = tree.body[0]
    body_nodes = fn.body[1:] if ast.get_docstring(fn) else fn.body
    body = "\n".join(ast.unparse(n) for n in body_nodes).lower()
    for provider in ("claude", "goose", "ollama", "codex", "gemini", "cursor"):
        assert provider not in body, f"the author is chosen by {provider!r}"
    assert "is_local" not in body, "the author is chosen by engine kind"


def test_a_delegated_plan_is_attributed_to_the_AUTHOR_not_the_driver(tmp_path):
    """🔴 RL-6 depends on this and a mutation proved it was unguarded.

    `decomposed_by` is the Manager RL-6 compares against the approver. Under
    delegation the driver and the author differ, so writing the driver there
    would compare the approver with a Manager that did not write the plan —
    and a plan reviewed by its own author would pass a check meant to make
    that impossible.
    """
    from rite_ai.coordination.local_backend import LocalStateLayer
    from rite_ai.local import decomposition as dec
    from rite_ai.local.decompose import Proposal, decompose_ticket

    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    # RL-63: a cite must RESOLVE, or the plan is refused for a reason that has
    # nothing to do with delegation. Written through rite's own namer so the
    # test does not encode a second spelling of where units live.
    from rite_ai.spec.digest_files import unit_filename, units_dir

    units = units_dir(root)
    units.mkdir(parents=True, exist_ok=True)
    (units / unit_filename("c1")).write_text("the agreed behaviour of the thing")

    plan = (
        '{"format_version": 1, "ticket": "T-1", "decomposed_by": "", "subtasks": ['
        '{"id": "s1", "intent": "do the first half", "scope": ["a.py"],'
        ' "verify": "python -m pytest -q t.py", "cites": ["c1"]},'
        '{"id": "s2", "intent": "do the second half", "scope": ["b.py"],'
        ' "verify": "python -m pytest -q t.py", "cites": ["c1"]}]}'
    )

    class _Stub:
        def propose(self, prompt, workspace):
            del prompt, workspace
            return Proposal(bytes=plan.encode())

    state = LocalStateLayer(tmp_path / "state")
    # `lead` DRIVES; it holds no authoring duty and delegates to `planner`.
    result = decompose_ticket(
        root, "lead", "T-1", proposer=_Stub(), state=state, ticket_text="do it"
    )
    assert result.wrote, (
        f"the delegated plan was not written: {result.problem} {result.reasons}"
    )
    written = dec.read(state, "T-1").plan
    assert written is not None
    assert written.decomposed_by == "planner", (
        f"the plan is attributed to {written.decomposed_by!r}; under delegation "
        "it must name the AUTHOR, because RL-6 compares that with the approver"
    )
    assert any("authored by 'planner'" in line for line in result.lines), result.lines
