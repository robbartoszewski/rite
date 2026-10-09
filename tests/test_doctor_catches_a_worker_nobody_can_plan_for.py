"""A Worker whose plan nobody can author must be reported, not left to hang.

🔴 **The failure mode, measured over an evening of real runs:** a local/GPU
Worker starts, claims its paths, and is never given a subtask — because no
Manager holds `decompose`, so the staged pipeline never reaches it. `rite
sandbox start` succeeds. The Worker sits at its prompt. Nothing is wrong
anywhere a person looks, and the fleet reads as healthy while advancing
nothing. The v0.7.0 gate scenario (a Claude Manager orchestrating, a GPU Worker
implementing) cannot run without a planner, and a Claude Manager cannot be one
(DD-2.4), so this is the common case rather than an exotic one.

⚠ Gated on DUTY and CAPABILITY, never an engine (SCRUM-83's line):
`needs_authored_plan` of each Worker, `decompose` of the Managers.
"""

from __future__ import annotations

from click.testing import CliRunner

from rite_ai.cli.main import cli

LOCAL = (
    "  engine: local:small\n  model: qwen3:8b\n"
    "  endpoint: http://localhost:11434\n  agent: goose\n  context_window: 32768\n"
)


def _project(
    tmp_path, *, roles: str, worker_engine: str = "local:small", sandbox: bool = True
):
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True, exist_ok=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        + ("" if sandbox else "sandbox:\n  enabled: false\n")
        + "coordination:\n  manager_roles:\n"
        + roles
    )
    d = tmp_path / "workers" / "gpu1"
    d.mkdir(parents=True, exist_ok=True)
    body = "worker:\n  name: gpu1\n  manager: lead\n  modules: []\n"
    if worker_engine != "claude":
        body += LOCAL
    (d / "worker.yml").write_text(body)
    return tmp_path.resolve()


CLAUDE_ONLY = "  - name: lead\n    engine: claude\n    duties: [plan-review, board]\n"
WITH_PLANNER = CLAUDE_ONLY + (
    "  - name: planner\n    engine: local:large\n    model: qwen3.8:latest\n"
    "    endpoint: http://localhost:11434\n    agent: goose\n"
    "    context_window: 32768\n    duties: [decompose]\n"
)


def _doctor(tmp_path, monkeypatch, root):
    monkeypatch.chdir(root)
    return CliRunner().invoke(cli, ["doctor"])


def test_a_gpu_worker_with_no_planner_is_reported(tmp_path, monkeypatch):
    """🔴 The defect: this used to be silent."""
    root = _project(tmp_path, roles=CLAUDE_ONLY)
    out = _doctor(tmp_path, monkeypatch, root).output
    assert "never be given a subtask" in out, out
    assert "NO Manager holds the `decompose` duty" in out


def test_it_gives_the_whole_command_that_fixes_it(tmp_path, monkeypatch):
    """A check that names a missing role without the line that creates it sends
    a non-power user into `.rite/config.yaml`, which is the UX defect this is
    part of fixing."""
    root = _project(tmp_path, roles=CLAUDE_ONLY)
    out = _doctor(tmp_path, monkeypatch, root).output
    assert "rite add manager planner --duties decompose" in out, out
    assert "--endpoint" in out and "--model" in out and "--context-window" in out
    # and why the model must differ, so the remedy does not create an RL-6 fault
    assert "RL-6" in out


def test_it_is_appended_to_doctors_problem_list(tmp_path):
    """🔴 Asserted on the LIST, not on doctor's exit code or its total.

    Two earlier versions of this were vacuous. `exit_code != 0` and
    "problem(s) found" are both true already, from the unconfigured board, so a
    mutation that stopped counting this passed. And comparing TOTALS does not
    work either: declaring a planner adds problems of its own (an unreachable
    endpoint, no stored login), so the total goes UP — 4 without, 5 with. The
    only honest assertion is that this check appends its own entry.
    """
    from rite_ai.cli.main import _doctor_authored_plan_holder

    root = _project(tmp_path, roles=CLAUDE_ONLY)
    problems: list[str] = []
    _doctor_authored_plan_holder(root, problems)
    assert any("can author plans for" in p for p in problems), problems

    with_planner = _project(tmp_path / "ok", roles=WITH_PLANNER)
    ok: list[str] = []
    _doctor_authored_plan_holder(with_planner, ok)
    assert ok == [], ok


def test_a_planner_present_is_reported_as_runnable(tmp_path, monkeypatch):
    """The control: with a planner it must NOT complain."""
    root = _project(tmp_path, roles=WITH_PLANNER)
    out = _doctor(tmp_path, monkeypatch, root).output
    assert "the staged pipeline can run" in out, out
    assert "never be given a subtask" not in out


def test_a_claude_only_fleet_with_no_local_worker_is_not_nagged(tmp_path, monkeypatch):
    """🔴 The second control, and the one that keeps this from being noise: a
    Worker that plans its own work needs no planner, so a Claude-only project
    must hear nothing about `decompose`."""
    root = _project(tmp_path, roles=CLAUDE_ONLY, worker_engine="claude")
    out = _doctor(tmp_path, monkeypatch, root).output
    assert "never be given a subtask" not in out
    assert "the staged pipeline can run" not in out


def test_it_fires_for_an_UNSANDBOXED_fleet_too(tmp_path, monkeypatch):
    """🔴 The placement defect, found by reading the call site rather than the
    test: the check was added INSIDE `if module_sandbox.enabled:`, beside the
    Worker token checks it was written next to.

    Nothing it asks has anything to do with the sandbox — it asks each Worker
    `needs_authored_plan` and the Managers who holds `decompose`. A project
    running its Workers unsandboxed starves identically and heard nothing,
    which is the quietest possible place for the check against silent
    starvation to be switched off.
    """
    root = _project(tmp_path, roles=CLAUDE_ONLY, sandbox=False)
    out = _doctor(tmp_path, monkeypatch, root).output
    assert "never be given a subtask" in out, out
    assert "NO Manager holds the `decompose` duty" in out


def test_the_check_is_wired_into_doctor():
    """Dead wiring, by AST."""
    import ast
    import inspect

    from rite_ai.cli import main

    tree = ast.parse(inspect.getsource(main._doctor_report))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_doctor_authored_plan_holder" in called, "doctor never runs the check"
