"""An all-Ollama fleet integrates its own work (OL8, Robert, 2026-10-02).

RL-11 said local engines commit to a local branch and stop, because *"SPEC
§5.1.1 forbids rite's code a push; the harness is rite's code"*. That reason was
written 2026-09-19 and PB1 gave `rite deliver` a push on 2026-09-29, which
§5.1.1 now states and `test_blast_radius` allows by name. The premise expired
before anyone noticed.

**What is NOT re-tested here.** The push's own properties — draft only, gate
passed on exactly those commits, never `--force`, operator-owned repo, default
branch — belong to `publishing/deliver.py` and are pinned by
`test_rite_publishes_only_past_the_gate.py` and `test_blast_radius.py`. A local
Manager does not get a second push path; it reaches the SAME one. So what these
tests prove is the reaching, plus the independence rule that makes a local
verdict worth acting on.

⚠ **A local engine still never runs `git push` itself.** It posts the same
two-field request a Claude Manager posts, and the harness validates and pushes.
The Manager's own repo-scoped token could technically push (C6/C26) and that
route is deliberately NOT used: it is gated only by the `pre-push` hook, which a
global `core.hooksPath` stops git reading.
"""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.config.managers import configuration_problems, parse_managers
from rite_ai.publishing import requests
from rite_ai.publishing.deliver import Delivered
from tests.test_rite_delivers_a_finished_task import TICKET, Project

GPU = {"endpoint": "http://localhost:11434", "agent": "goose", "context_window": 32768}


def _problems(entries):
    parsed = parse_managers(entries)
    assert not parsed.error, parsed.error
    return configuration_problems(parsed.roles)


# ── the lift ─────────────────────────────────────────────────────────────────


def test_a_local_manager_may_hold_integrate():
    problems = _problems(
        [
            {"name": "gpu", "engine": "local:large", "model": "qwen3:32b",
             "duties": ["integrate", "decide", "board", "route"], **GPU},
        ]
    )
    assert not any("integrate" in p for p in problems), problems


def test_an_all_local_fleet_with_every_duty_configures():
    # decompose and plan-review on DIFFERENT models, so RL-6 is satisfied
    # without a Claude Manager anywhere in the fleet.
    problems = _problems(
        [
            {"name": "lead", "engine": "local:large", "model": "qwen3:32b",
             "duties": ["decide", "board", "route", "integrate", "plan-review"], **GPU},
            {"name": "planner", "engine": "local:small", "model": "qwen3:8b",
             "duties": ["decompose", "step-review", "execute"], **GPU},
        ]
    )
    assert problems == [], problems


# ── the independence that makes a local verdict worth acting on ───────────────


def test_a_same_model_reviewer_cannot_be_the_independent_one():
    problems = _problems(
        [
            {"name": "lead", "engine": "local:large", "model": "qwen3.8:latest",
             "duties": ["decide", "board", "route", "plan-review", "integrate"], **GPU},
            {"name": "planner", "engine": "local:small", "model": "qwen3.8:latest",
             "duties": ["decompose"], **GPU},
        ]
    )
    assert any("different model" in p for p in problems), problems


def test_rites_own_window_pin_does_not_launder_the_same_model():
    # `rite-ctx32768-qwen3.8-latest` IS `qwen3.8:latest` with a bigger window.
    problems = _problems(
        [
            {"name": "lead", "engine": "local:large",
             "model": "rite-ctx32768-qwen3.8-latest",
             "duties": ["decide", "board", "route", "plan-review", "integrate"], **GPU},
            {"name": "planner", "engine": "local:small", "model": "qwen3.8:latest",
             "duties": ["decompose"], **GPU},
        ]
    )
    assert any("different model" in p for p in problems), problems


# ── a local Manager reaches the request path ─────────────────────────────────


def _ask(root: Path, manager: str, worker: str, ticket: str) -> None:
    where = requests.requests_dir(root, manager)
    where.mkdir(parents=True, exist_ok=True)
    (where / "1.json").write_text(json.dumps({"worker": worker, "ticket": ticket}))


def _honour_as(p: Project, manager: str) -> list[str]:
    """`honour_deliveries` as the supervisor calls it, for `manager`.

    The sandbox is stood in for exactly as the existing delivery tests do: the
    point here is which MANAGER asked, not yoloAI.
    """
    from unittest.mock import patch

    from rite_ai.sandbox import SandboxResult, SandboxStatus

    said: list[str] = []
    with (
        patch(
            "rite_ai.sandbox.worker_sandbox_status",
            return_value=SandboxStatus("stopped"),
        ),
        patch(
            "rite_ai.sandbox.stop_worker", return_value=SandboxResult(True, "stopped")
        ),
        patch("rite_ai.sandbox.existing_sandbox_name", return_value="rite-x-alpha"),
        patch("rite_ai.sandbox._sandbox_copy", return_value=p.copy),
        patch(
            "rite_ai.sandbox.destroy_worker", return_value=SandboxResult(True, "gone")
        ),
    ):
        requests.honour_deliveries(p.root, manager, said.append)
    return said


def _origin_main(p: Project) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "refs/heads/main"],
        cwd=p.origin,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_a_local_managers_request_is_honoured_and_the_work_reaches_origin(tmp_path):
    """The whole point: no human and no Claude anywhere in this path.

    ⚠ Under `publish.strategy: push`, §5.1.1 says rite "pushes the collected
    ticket branch ONTO THE MODULE'S BRANCH" — so `main` advances and no
    `<ticket>` ref appears on the remote. Asserting a ticket ref was this test's
    own first mistake, and the delivery it called a failure had worked.
    """
    p = Project(tmp_path, "push")
    p.start()
    before = _origin_main(p)
    p.work()
    _ask(p.root, "gpu", "alpha", TICKET)
    said = _honour_as(p, "gpu")
    after = _origin_main(p)
    assert after != before, f"origin/main did not move: {said}"
    # And it is the Worker's work that arrived, not an empty fast-forward.
    import subprocess

    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "refs/heads/main"],
        cwd=p.origin,
        capture_output=True,
        text=True,
    ).stdout
    assert "f0.txt" in tree, tree


def test_the_manager_that_asked_is_told_the_outcome(tmp_path):
    from rite_ai.managers.mailbox import INBOX, delivery_note
    from rite_ai.managers.mailbox import take as take_mail

    p = Project(tmp_path, "push")
    p.start()
    p.work()
    _ask(p.root, "gpu", "alpha", TICKET)
    _honour_as(p, "gpu")
    told = delivery_note(take_mail(p.root, "gpu", INBOX))
    assert TICKET in told, told


def test_a_local_manager_never_runs_the_push_itself():
    # Blast radius parity: lifting the refusal must not add a push site. The
    # push stays in publishing/ only, which test_blast_radius asserts by
    # enumerating argv; this pins the allowance itself so a widening is loud.
    from tests.test_blast_radius import ALLOWED_GIT

    assert {verb for verb, _path in ALLOWED_GIT} == {"push"}
    assert len(ALLOWED_GIT) == 1


def test_delivered_is_what_a_local_request_produces(tmp_path):
    # Not a Refused that happened to say something encouraging.
    p = Project(tmp_path, "push")
    p.start()
    p.work()
    _ask(p.root, "gpu", "alpha", TICKET)
    said = _honour_as(p, "gpu")
    assert not any("NOT delivered" in line for line in said), said
    assert Delivered is not None


# ── DD-3.5 survives self-integrate ───────────────────────────────────────────
# Lifting RL-11 must not loosen the gate that makes a verdict mean anything. An
# all-local fleet now approves its OWN plans, so "a decomposer may never approve
# its own plan" is doing more work than it was, not less.


def test_a_local_decomposer_still_cannot_approve_its_own_plan(tmp_path):
    from rite_ai.local import decomposition as dec
    from rite_ai.local.plan_validation import candidate_problems

    plan = dec.Decomposition(
        ticket="T-1",
        subtasks=(
            dec.Subtask(id="s1", intent="first", scope=("a.txt",), verify="true"),
            dec.Subtask(id="s2", intent="second", scope=("b.txt",), verify="true"),
        ),
        approval=dec.APPROVED,
    )
    problems = candidate_problems(plan, root=tmp_path)
    assert any("already marked approved" in r for r in problems.refusals), problems
    assert any("DD-3.5" in r for r in problems.refusals), problems


def test_a_pending_plan_is_not_refused_for_that_reason(tmp_path):
    # The control: the refusal above must be about the APPROVED marking and not
    # something every candidate trips.
    from rite_ai.local import decomposition as dec
    from rite_ai.local.plan_validation import candidate_problems

    plan = dec.Decomposition(
        ticket="T-1",
        subtasks=(
            dec.Subtask(id="s1", intent="first", scope=("a.txt",), verify="true"),
            dec.Subtask(id="s2", intent="second", scope=("b.txt",), verify="true"),
        ),
        approval=dec.PENDING,
    )
    problems = candidate_problems(plan, root=tmp_path)
    assert not any("already marked approved" in r for r in problems.refusals), problems
