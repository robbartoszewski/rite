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

from rite_ai.config.managers import DECOMPOSE
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


def test_a_manager_that_cannot_author_is_refused_the_worker(tmp_path):
    """🔴 The defect, in one call."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    why = broker.may_own_worker(root, "lead", "gpu1")
    assert why, "the Manager that cannot author its plan was given the Worker"
    assert DECOMPOSE in why, why
    assert "planner" in why, "the refusal does not say who CAN take it"


def test_the_manager_that_authors_may_own_it(tmp_path):
    """The control that keeps the gate from refusing everything."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    assert broker.may_own_worker(root, "planner", "gpu1") == ""


def test_a_worker_that_plans_its_own_work_may_be_owned_by_anyone(tmp_path):
    """The second control, and the one that stops this becoming a rule about
    Claude Managers: a Worker rite does not author for is nobody's special
    case, and the Manager holding only `plan-review` may own it."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="claude")
    assert broker.may_own_worker(root, "lead", "gpu1") == ""


def test_an_unreadable_manifest_is_a_refusal(tmp_path):
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    (root / "workers" / "gpu1" / "worker.yml").write_text("{{{ not yaml")
    why = broker.may_own_worker(root, "planner", "gpu1")
    assert why, "an unreadable Worker was handed over anyway"


def test_an_unreadable_project_is_a_refusal(tmp_path):
    """Fail closed: a config nobody could read must not read as "no duties
    required"."""
    root = _project(tmp_path, roles=_AUTHOR_AND_APPROVER, worker_engine="local:small")
    (root / ".rite" / "config.yaml").write_text("coordination: [[[")
    assert broker.may_own_worker(root, "planner", "gpu1") != ""


def test_nobody_holding_the_duty_is_a_refusal_that_says_so(tmp_path):
    roles = (
        "  - name: lead\n    engine: claude\n    duties: [plan-review]\n"
        "  - name: other\n    engine: claude\n    duties: [board]\n"
    )
    root = _project(tmp_path, roles=roles, worker_engine="local:small")
    why = broker.may_own_worker(root, "lead", "gpu1")
    assert "none" in why, why


# ---- provider agnosticism, which is the standing requirement ---------------


def test_the_gate_names_no_provider():
    """🔴 Robert's guideline, as a test.

    `may_own_worker` must decide from a DUTY and a CAPABILITY. Read by AST with
    the DOCSTRING DROPPED — it has to discuss Claude, because that is where
    this was measured, and the first version of this test failed on its own
    explanation.
    """
    tree = ast.parse(inspect.getsource(broker.may_own_worker))
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
    assert "may_own_worker" in called, "for_project never consults the gate"


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
    assert broker.may_own_worker(root, "lead", "gpu1") != ""
    assert broker.may_own_worker(root, "planner", "gpu1") == ""
