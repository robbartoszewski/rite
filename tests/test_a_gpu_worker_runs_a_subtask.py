"""A local Worker's subtask runs INSIDE its sandbox (SCRUM-54).

`in_sandbox.exec_launcher`, `in_sandbox.instruction_dir_for` and
`sandbox.goose_path_root` were written, measured and tested with no caller in
`src/` — so the local tier ran at MANAGER tier, on the operator's own tree, and
a mixed Claude+GPU fleet was configurable but not runnable. These are the tests
for the placement that joins them.

Three properties carry the weight, and each is a state that looked like a
different, benign state:

- **a placed turn does not touch the host tree.** The agent edits the sandbox's
  COPY, so a verify run in the project root tests a tree the model never
  touched — it passes or fails on something else entirely, and both answers are
  lies about the work.
- **a placed turn commits to the TICKET.** `publishing/deliver.py` collects
  `refs/heads/<ticket>` from the sandbox's copy and nothing else, so work on a
  per-subtask branch is reported as "the Worker made no branch <ticket>" by a
  delivery that otherwise succeeded.
- **a placed turn does not inherit the host environment.** Measured: with
  `GITHUB_TOKEN` merely PRESENT in the operator's shell, the turn came back
  `could not start goose: SecretOnArgv` — and was recorded as a failed ATTEMPT,
  so RL-47 broke too. Every yoloAI-launched session exports every token the
  operator holds, which is the machine this path was built for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rite_ai.local import decomposition as dec
from rite_ai.local import plan_state
from rite_ai.local import step as st
from rite_ai.local.harness import Commit, VerifyResult
from rite_ai.local.in_sandbox import exec_argv
from rite_ai.local.worker_step import Placement, placement_for
from rite_ai.sandbox import SandboxStatus

MANAGER = "lead"
WORKER = "gpu1"
TICKET = "RT-1"

LOCAL_WORKER = """\
worker:
  name: gpu1
  manager: lead
  engine: local:small
  endpoint: http://localhost:11434
  model: qwen3.8:latest
  agent: goose
  context_window: 32768
  modules: [app]
"""


# --- the launcher's working directory ---------------------------------------
#
# `yoloai exec` has no `--cwd` (measured on 0.11.0) and starts in the copy's
# ROOT, while a Worker's modules are clones one level down. A turn left in the
# root would run the model against the directory ABOVE the repository.


def test_the_turn_runs_in_the_modules_clone():
    argv = exec_argv(
        "box", ["goose", "run"], {"GOOSE_MODEL": "q"}, "yoloai", subdir="app"
    )
    assert argv[:5] == ["yoloai", "exec", "box", "--", "env"]
    assert argv[5:7] == ["-C", "app"]
    # Measured inside a real seatbelt sandbox: macOS's own `env` honours `-C`,
    # so this needs no coreutils — and no shell, which is what keeps
    # `runners.py`'s "no shell" property true of the half that runs a model.
    assert "sh" not in argv


def test_a_subdir_travels_even_with_nothing_to_place():
    # `env` is reached for because of the directory, not only the variables.
    argv = exec_argv("box", ["goose"], {}, "yoloai", subdir="app")
    assert argv == ["yoloai", "exec", "box", "--", "env", "-C", "app", "goose"]


def test_no_subdir_still_means_no_env_at_all():
    assert exec_argv("box", ["goose"], {}, "yoloai") == [
        "yoloai",
        "exec",
        "box",
        "--",
        "goose",
    ]


def test_an_absolute_subdir_is_refused():
    # An absolute path here is a HOST path, and `exec_launcher`'s docstring
    # measured what that costs: it either fails inside, or — if the path happens
    # to exist on the host — runs the turn OUTSIDE the sandbox against the
    # operator's real tree. Relative cannot express that.
    with pytest.raises(ValueError, match="absolute"):
        exec_argv("box", ["goose"], {}, "yoloai", subdir="/abs/proj/app")


# --- the environment the sandboxed turn is built with -----------------------


def _ok_probe():
    return type("P", (), {"problems": []})()


def _context():
    sub = dec.Subtask(id="s1", intent="do it", scope=("a.py",), verify="true")
    from rite_ai.local.harness import Context

    return Context(ticket=TICKET, subtask=sub, spec_slice="the slice")


def test_a_secret_in_the_operators_shell_killed_the_turn(monkeypatch):
    """The regression, stated as the defect it was.

    ⚠ Not a hypothetical: `GooseAgent.run` builds `dict(os.environ)` and
    `exec_argv` refuses a secret-shaped name on argv. Both are right on their
    own, and together they made the sandboxed path dead.
    """
    from rite_ai.local.goose_agent import GooseAgent
    from rite_ai.local.in_sandbox import exec_launcher

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_whatever")
    inheriting = GooseAgent(
        model="q",
        endpoint="http://localhost:11434",
        probe=_ok_probe,
        launch=exec_launcher("box", binary="/bin/echo"),
    )
    report = inheriting.run(_context(), "/host/workspace")
    assert report.claimed_success is False
    assert "SecretOnArgv" in report.summary
    # ⚠ And the half that made it worse, now closed: a launch that RAISED is by
    # definition a turn that did not happen, and `GooseAgent` reported it with
    # `infrastructure_fault` false — so RL-47 broke too and the subtask spent an
    # attempt proving that a tool could not be started.
    assert report.infrastructure_fault is True


def test_a_sandboxed_turn_does_not_inherit_the_shell(monkeypatch):
    from rite_ai.local.goose_agent import GooseAgent, goose_environment
    from rite_ai.local.in_sandbox import exec_launcher

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_whatever")
    monkeypatch.setenv("JIRA_API_TOKEN", "also-secret")
    placed = GooseAgent(
        model="q",
        endpoint="http://localhost:11434",
        probe=_ok_probe,
        launch=exec_launcher("box", binary="/bin/echo", subdir="app"),
        inherit_environment=False,
        env=goose_environment(
            "http://localhost:11434",
            "q",
            context_limit=32768,
            path_root="/sbx/rite/goose",
        ),
    )
    report = placed.run(_context(), "/host/workspace")
    assert report.claimed_success is True
    echoed = report.summary
    assert "GOOSE_CONTEXT_LIMIT=32768" in echoed
    assert "GOOSE_PATH_ROOT=/sbx/rite/goose" in echoed
    assert "-C app" in echoed
    # ⚠ Nothing is lost by not inheriting: `env KEY=VAL cmd` ADDS to the
    # environment it was handed, so the sandbox's own PATH is still the turn's.
    assert "GITHUB_TOKEN" not in echoed and "JIRA_API_TOKEN" not in echoed


# --- the sandbox vanishing mid-turn -----------------------------------------
#
# ⚠ A race rite must not tolerate, and the thing that causes it is rite itself:
# `publishing/deliver.py` stops a Worker's sandbox FIRST, by design, so a
# supervisor cycle that steps a subtask while a delivery is honoured is the
# ordinary case. Measured against the real yoloai on a real stopped sandbox,
# with the control below.


def _completed(returncode: int, out: str = "", err: str = ""):
    from subprocess import CompletedProcess

    return CompletedProcess(
        args=["yoloai"], returncode=returncode, stdout=out, stderr=err
    )


def test_a_sandbox_stopped_mid_turn_raises_rather_than_looking_like_a_turn(
    monkeypatch,
):
    from rite_ai.local.in_sandbox import SandboxGone, exec_launcher

    monkeypatch.setattr(
        "rite_ai.local.in_sandbox.subprocess.run",
        lambda *a, **k: _completed(1, err='sandbox "box": container is not running'),
    )
    monkeypatch.setattr(
        "rite_ai.sandbox.sandbox_status_named",
        lambda n, b="": SandboxStatus("stopped"),
    )
    with pytest.raises(SandboxGone, match="did not run"):
        exec_launcher("box", binary="/bin/yoloai")(["goose"], "/ws", {})


def test_a_failing_command_in_a_LIVE_sandbox_is_a_result(monkeypatch):
    # The control, and it is the one that matters: yoloAI passes an inner
    # command's exit code through unchanged (measured: `exit 3` arrives as 3),
    # so a code alone cannot tell "goose exited 1" from "there is no sandbox".
    # Without this, every failing turn would be misread as a race.
    from rite_ai.local.in_sandbox import exec_launcher

    monkeypatch.setattr(
        "rite_ai.local.in_sandbox.subprocess.run",
        lambda *a, **k: _completed(3, out="ran-inside"),
    )
    monkeypatch.setattr(
        "rite_ai.sandbox.sandbox_status_named",
        lambda n, b="": SandboxStatus("active"),
    )
    done = exec_launcher("box", binary="/bin/yoloai")(["goose"], "/ws", {})
    assert done.returncode == 3
    assert done.stdout == "ran-inside"


def test_a_yoloai_that_cannot_be_asked_does_not_invent_a_race(monkeypatch):
    # `known=False` is "I could not check", which is not "the sandbox is gone".
    # Guessing it was would turn an unreadable yoloAI into a turn nobody ran.
    from rite_ai.local.in_sandbox import exec_launcher

    monkeypatch.setattr(
        "rite_ai.local.in_sandbox.subprocess.run", lambda *a, **k: _completed(1)
    )
    monkeypatch.setattr(
        "rite_ai.sandbox.sandbox_status_named",
        lambda n, b="": SandboxStatus("unknown", known=False),
    )
    done = exec_launcher("box", binary="/bin/yoloai")(["goose"], "/ws", {})
    assert done.returncode == 1


# --- the placement ----------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A project with one local Worker recorded as working the ticket."""
    from rite_ai.spec.digest_files import unit_filename, units_dir

    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "modules.yaml").write_text("modules:\n  app:\n    path: app\n")
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n")
    # `_worker_for` reads `load_project`, which needs all three files — the same
    # selector `loop._ask_delivery` uses, so execution and delivery name the
    # same Worker by construction.
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "sandbox:\n  enabled: true\n  backend: seatbelt\n"
    )
    where = units_dir(root)
    where.mkdir(parents=True, exist_ok=True)
    (where / unit_filename("5.3")).write_text("## 5.3 the cited unit\n")
    worker = root / "workers" / WORKER
    worker.mkdir(parents=True)
    (worker / "worker.yml").write_text(LOCAL_WORKER)
    return root


def _recorded(root: Path, worker: str = WORKER, ticket: str = TICKET):
    """What `rite sandbox start` wrote — the selector the placement reads."""
    from rite_ai.publishing import record

    path = record._path(root, worker)  # noqa: SLF001 - the test writes what it reads
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": record.RECORD_VERSION,
                "worker": worker,
                "ticket": ticket,
                "started_at": "2026-10-03T00:00:00Z",
                # `record.settings`' three keys: a module's settings are
                # rejected as malformed without all of them, and an Unreadable
                # record reads as "no Worker on this ticket".
                "modules": {
                    "app": {
                        "strategy": "commit",
                        "squash": False,
                        "auto_merge": False,
                    }
                },
            }
        )
    )


def test_no_worker_on_the_ticket_is_the_manager_tier(project):
    # Not an error: a project with nothing assigned is not misconfigured, and
    # the host path is what ran before this module existed.
    placement = placement_for(project, MANAGER, TICKET)
    assert placement == Placement()
    assert placement.sandboxed is False
    assert placement.problem == ""


def test_a_claude_worker_is_the_manager_tier(project):
    (project / "workers" / WORKER / "worker.yml").write_text(
        "worker:\n  name: gpu1\n  manager: lead\n  modules: [app]\n"
    )
    _recorded(project)
    assert placement_for(project, MANAGER, TICKET) == Placement()


def test_a_stopped_sandbox_is_a_named_refusal(project, monkeypatch):
    _recorded(project)
    _sandbox(monkeypatch, status="stopped")
    placement = placement_for(project, MANAGER, TICKET)
    assert placement.sandboxed is False
    assert "stopped" in placement.problem
    assert "rite sandbox start" in placement.problem


def test_a_yoloai_that_cannot_be_asked_is_not_no_sandbox(project, monkeypatch):
    # "I could not check" and "there is no sandbox" are opposite answers; only
    # one of them is safe to act on.
    _recorded(project)
    _sandbox(monkeypatch, status="yoloai not found", known=False)
    placement = placement_for(project, MANAGER, TICKET)
    assert "could not ask yoloAI" in placement.problem


def test_an_agent_without_window_enforcement_is_refused(project, monkeypatch):
    _recorded(project)
    _sandbox(monkeypatch)
    (project / "workers" / WORKER / "worker.yml").write_text(
        LOCAL_WORKER.replace("agent: goose", "agent: aider")
    )
    placement = placement_for(project, MANAGER, TICKET)
    # The same refusal `step._agent_for` makes for a Manager, same reason (S35):
    # an unenforced window runs at the server's 4,096-token default.
    assert "only 'goose'" in placement.problem
    assert "S35" in placement.problem


def test_an_undeclared_window_is_refused(project, monkeypatch):
    _recorded(project)
    _sandbox(monkeypatch)
    (project / "workers" / WORKER / "worker.yml").write_text(
        LOCAL_WORKER.replace("  context_window: 32768\n", "")
    )
    placement = placement_for(project, MANAGER, TICKET)
    assert "context_window" in placement.problem


def test_two_modules_are_refused_rather_than_guessed(project, monkeypatch):
    _recorded(project)
    _sandbox(monkeypatch)
    (project / ".rite" / "modules.yaml").write_text(
        "modules:\n  app:\n    path: app\n  lib:\n    path: lib\n"
    )
    (project / "workers" / WORKER / "worker.yml").write_text(
        LOCAL_WORKER.replace("modules: [app]", "modules: [app, lib]")
    )
    placement = placement_for(project, MANAGER, TICKET)
    # A `Subtask` names paths and no module, so `src/a.py` names a file in both
    # clones. A wrong guess commits the work to the wrong module's branch, where
    # `deliver` collects it into the wrong checkout — silently, both halves
    # having succeeded.
    assert "2 modules" in placement.problem
    assert "guessing" in placement.problem


def _sandbox(monkeypatch, status: str = "active", known: bool = True):
    """Stand in for yoloAI, so the placement's own rules are what is measured.

    ⚠ The REAL `SandboxStatus`, not a fake with a `known` attribute: the
    question the placement asks is `container_is_down`, and a stand-in that
    answered it directly would be testing the stand-in.
    """
    from rite_ai import sandbox as sb

    asked: list[str] = []

    def named(names, binary=""):
        asked.append(names if isinstance(names, str) else sorted(names)[0])
        return sb.SandboxStatus(status, known)

    monkeypatch.setattr(sb, "sandbox_status_named", named)
    monkeypatch.setattr(sb, "existing_sandbox_name", lambda w, r=None: f"rite-p-{w}")
    monkeypatch.setattr(sb, "goose_path_root", lambda n, b="": "/sbx/rite/goose")
    monkeypatch.setattr(
        "rite_ai.local.in_sandbox.instruction_dir_for",
        lambda n, b="": "/sbx/files",
    )
    return asked


def test_the_happy_placement_points_at_the_sandboxes_copy(
    project, monkeypatch, tmp_path
):
    _recorded(project)
    _sandbox(monkeypatch)
    copy = tmp_path / "copy"
    (copy / "app" / ".git").mkdir(parents=True)
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)

    placement = placement_for(project, MANAGER, TICKET)
    assert placement.problem == ""
    assert placement.sandboxed is True
    assert placement.worker == WORKER
    # The verify and the commit run HERE — the module's clone inside the copy —
    # because that is where the work is and where `deliver` comes to collect it.
    assert placement.workspace == str(copy / "app")
    # And the turn inside is given the same place, named relatively.
    assert placement.subdir == "app"
    # `deliver` collects `refs/heads/<ticket>` and nothing else.
    assert placement.branch == TICKET
    agent = placement.agent
    assert agent.model == "qwen3.8:latest"
    assert agent.inherit_environment is False
    assert agent.instruction_dir == "/sbx/files"
    assert agent.env["GOOSE_PATH_ROOT"] == "/sbx/rite/goose"
    assert agent.env["GOOSE_CONTEXT_LIMIT"] == "32768"


@pytest.mark.parametrize("word", ["active", "idle", "done", "failed", "a-new-word"])
def test_a_RUNNING_sandbox_is_accepted_whatever_the_agent_is_doing(
    project, monkeypatch, tmp_path, word
):
    """⚠ The gate asks whether the CONTAINER is up, never `== "active"`.

    Three of yoloAI's five status words describe a container that is perfectly
    running — `activity.py` records its vocabulary from 0.11.0: "active=working,
    idle=waiting at prompt, done=finished, failed=error" — and a word yoloAI
    adds later is printed as itself rather than mapped to the nearest one rite
    knows. A positive test would refuse a live sandbox for sitting still, and
    `exec_launcher` would read every genuine `goose` failure on an `idle` box as
    a sandbox that had vanished.

    Measured on 0.11.0: a local Worker's sandbox runs the `idle` NO-OP agent and
    reports `active`, so nothing was refused today. This is the difference
    between working and happening to work.
    """
    _recorded(project)
    _sandbox(monkeypatch, status=word)
    copy = tmp_path / "copy"
    (copy / "app" / ".git").mkdir(parents=True)
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)
    assert placement_for(project, MANAGER, TICKET).sandboxed is True


@pytest.mark.parametrize("word", ["stopped", "not found"])
def test_only_a_DOWN_container_is_refused(project, monkeypatch, word):
    _recorded(project)
    _sandbox(monkeypatch, status=word)
    assert word in placement_for(project, MANAGER, TICKET).problem


def test_one_sandbox_name_answers_both_questions(project, monkeypatch, tmp_path):
    """⚠ `existing_sandbox_name` prefers the scoped name and falls back to the
    legacy one; `worker_sandbox_status` answers for EITHER. Asked separately,
    the gate could pass on one sandbox while the turn ran in another."""
    _recorded(project)
    asked = _sandbox(monkeypatch, status="active")
    copy = tmp_path / "copy"
    (copy / "app" / ".git").mkdir(parents=True)
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)
    placement = placement_for(project, MANAGER, TICKET)
    assert asked == [placement.sandbox]


def test_the_placement_builds_level_2_on_the_workers_model(
    project, monkeypatch, tmp_path
):
    _recorded(project)
    _sandbox(monkeypatch)
    copy = tmp_path / "copy"
    (copy / "app" / ".git").mkdir(parents=True)
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)

    seen: dict = {}

    def fake_plan_approach(subtask, spec_slice, **kw):
        seen.update(kw)
        return type("A", (), {"ok": True, "steps": "x", "problem": ""})()

    monkeypatch.setattr("rite_ai.local.approach.plan_approach", fake_plan_approach)
    placement = placement_for(project, MANAGER, TICKET)
    assert placement.approach is not None
    placement.approach(_subtask_for_level2(), "the slice")
    # `worker_decomposition_model`: a local Worker plans with its OWN model —
    # one GPU, nothing to gain from loading a second set of weights.
    assert seen["model"] == "qwen3.8:latest"
    assert seen["endpoint"] == "http://localhost:11434"
    assert seen["context_limit"] == 32768
    # And against the tree the turn will edit, not the operator's project root.
    assert seen["workspace"] == str(copy / "app")


def _subtask_for_level2():
    return dec.Subtask(id="s1", intent="x", scope=("greet.py",), verify="true")


def test_a_missing_checkout_names_the_fix(project, monkeypatch, tmp_path):
    _recorded(project)
    _sandbox(monkeypatch)
    copy = tmp_path / "copy"
    copy.mkdir()
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)
    placement = placement_for(project, MANAGER, TICKET)
    assert "no git checkout of app" in placement.problem
    assert "rite prepare" in placement.problem


def test_a_sandbox_with_no_writable_layer_is_refused(project, monkeypatch, tmp_path):
    _recorded(project)
    _sandbox(monkeypatch)
    copy = tmp_path / "copy"
    (copy / "app" / ".git").mkdir(parents=True)
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)
    monkeypatch.setattr(sb, "goose_path_root", lambda n, b="": "")
    placement = placement_for(project, MANAGER, TICKET)
    # Goose with no writable root does not fail halfway — it panics in
    # `session_manager.rs` before reaching the model (OL1).
    assert "panic before reaching the model" in placement.problem


def test_no_exchange_directory_is_refused_rather_than_leaked(
    project, monkeypatch, tmp_path
):
    _recorded(project)
    _sandbox(monkeypatch)
    copy = tmp_path / "copy"
    (copy / "app" / ".git").mkdir(parents=True)
    from rite_ai import sandbox as sb

    monkeypatch.setattr(sb, "_sandbox_copy", lambda n, w: copy)
    monkeypatch.setattr(
        "rite_ai.local.in_sandbox.instruction_dir_for", lambda n, b="": ""
    )
    placement = placement_for(project, MANAGER, TICKET)
    # The fallback is the host temp root, granted to EVERY sandbox here, so the
    # subtask's intent and spec slice would be readable by every other Worker
    # for as long as the turn ran. That is the leak OL5 closed.
    assert "shared temp root" in placement.problem


# --- what `take_one_step` does with a placement -----------------------------


class _Agent:
    def __init__(self) -> None:
        self.workspaces: list[str] = []

    def run(self, context, workspace):
        from rite_ai.local.harness import AgentReport

        self.workspaces.append(workspace)
        return AgentReport(claimed_success=True, summary="did it")


class _Verifier:
    def __init__(self) -> None:
        self.workspaces: list[str] = []

    def run(self, command, workspace):
        self.workspaces.append(workspace)
        return VerifyResult(True, "ok")


class _Committer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def commit_to_branch(self, workspace, branch, message):
        self.calls.append((workspace, branch))
        return Commit(sha="c0ffee1234")


class _Claims:
    def __init__(self) -> None:
        self.taken: list[tuple[tuple, str]] = []

    def take(self, paths, worker):
        self.taken.append((paths, worker))
        return True

    def release(self, paths, worker):
        pass


def _approved(root: Path, *, with_approach: bool = True):

    plan = dec.Decomposition(
        ticket=TICKET,
        subtasks=(
            dec.Subtask(
                id="s1",
                intent="make it do the thing",
                scope=("src/a.py",),
                verify="true",
                cites=("5.3",),
            ),
        ),
        decomposed_by="planner",
        approval=dec.APPROVED,
        approved_by="reviewer",
    )
    state = plan_state.layer(root)
    written = dec.write(state, plan, dec.read(state, TICKET).version)
    assert type(written).__name__ == "Written", written
    # The approval digests always: a step with none is refused before anything
    # else, which would make every test here a test of that one refusal.
    # The persisted APPROACH only when the test is not about producing it.
    _clear_level2(state, plan, approach=with_approach)
    return state


def _clear_level2(state, plan, steps="1. do the thing", approach=True) -> None:
    """Record what approval recorded, and persist an approach per subtask —
    what the real pipeline leaves behind by the time a step runs (SCRUM-72e).

    ⚠ **Not a stub of the guard, a set-up of its inputs.** Level 2 is required
    and persisted now, and `cleared_to_run` refuses a step without it; these
    tests are about the EXECUTOR, so they arrive at it the way a driven
    pipeline does. The guard's own refusals are tested where they belong.
    """
    from rite_ai.local import level2

    recorded = level2.record_approval(
        state,
        plan.ticket,
        plan.approved_by or "reviewer",
        plan.subtasks,
        expected=level2.approval_version(state, plan.ticket),
    )
    assert type(recorded).__name__ == "Written", recorded
    if not approach:
        return
    for sub in plan.subtasks:
        written = level2.write_approach(state, plan.ticket, sub, steps)
        assert type(written).__name__ == "Written", written


class TestAPlacedTurnNeverTouchesTheHostTree:
    """⚠ **The invariant this wiring exists for.**

    The agent edits the sandbox's copy. A verify or a commit that ran in the
    project root would be testing, and committing, a tree the model never
    touched — and both would look like ordinary results.
    """

    def _run(self, project, placement):
        state = _approved(project)
        agent, verifier, committer, claims = (
            _Agent(),
            _Verifier(),
            _Committer(),
            _Claims(),
        )
        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            state=state,
            verifier=verifier,
            committer=committer,
            claims=claims,
            placement=Placement(**{**placement, "agent": agent}),
        )
        return step, agent, verifier, committer, claims

    PLACED = {
        "worker": WORKER,
        "sandbox": "rite-p-gpu1",
        "workspace": "/sbx/copy/app",
        "subdir": "app",
        "branch": TICKET,
    }

    def test_the_verify_runs_in_the_sandboxes_copy(self, project):
        step, _, verifier, _, _ = self._run(project, self.PLACED)
        assert step.ran is True
        assert verifier.workspaces == ["/sbx/copy/app"]
        assert str(project) not in verifier.workspaces

    def test_the_commit_goes_to_the_copy_on_the_ticket_branch(self, project):
        step, _, _, committer, _ = self._run(project, self.PLACED)
        # `deliver` collects `refs/heads/<ticket>` from the copy and nothing
        # else, so a per-subtask branch here is work it reports as missing.
        assert committer.calls == [("/sbx/copy/app", TICKET)]
        assert step.branch == TICKET

    def test_the_claim_is_taken_in_the_workers_name(self, project):
        # Two Workers of one Manager would otherwise both claim as that
        # Manager, and the ledger — whose one job is exclusion — would read
        # their overlapping scopes as one holder re-claiming its own paths.
        _, _, _, _, claims = self._run(project, self.PLACED)
        assert claims.taken == [(("src/a.py",), WORKER)]

    def test_the_step_says_where_it_ran(self, project):
        step, _, _, _, _ = self._run(project, self.PLACED)
        assert step.worker == WORKER
        assert step.sandbox == "rite-p-gpu1"
        assert any("rite-p-gpu1" in line for line in step.lines)

    def test_a_turn_that_never_RAN_does_not_spend_an_attempt(self, project):
        """⚠ RL-47, through the property that decides it.

        `Outcome.counts_as_attempt` had NO reader in `src/`: `step._record`
        incremented unconditionally, so the rule it states — "work counts, and
        a turn that never happened does not" — was not in force anywhere. The
        cases that reach it are real and measured: a sandbox a delivery had
        just stopped, and a launch rite's own leak guard refused.
        """
        from rite_ai.local.harness import AgentReport

        class _CouldNotRun:
            def run(self, context, workspace):
                return AgentReport(
                    claimed_success=False,
                    summary="sandbox rite-p-gpu1 is stopped, so the turn did not run",
                    infrastructure_fault=True,
                )

        state = _approved(project)
        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            state=state,
            verifier=_Verifier(),
            committer=_Committer(),
            claims=_Claims(),
            placement=Placement(**{**self.PLACED, "agent": _CouldNotRun()}),
        )
        assert step.ran is True
        plan = dec.read(state, TICKET).plan
        # The subtask is still untried, so a person or a retry gets to it.
        assert plan.subtasks[0].attempts == 0

    def test_work_that_FAILED_does_spend_one(self, project):
        # The control: without it, the assertion above passes on a counter that
        # never increments at all.
        state = _approved(project)

        class _Failing:
            def run(self, command, workspace):
                return VerifyResult(False, "the verify said no")

        st.take_one_step(
            project,
            MANAGER,
            TICKET,
            state=state,
            verifier=_Failing(),
            committer=_Committer(),
            claims=_Claims(),
            placement=Placement(**{**self.PLACED, "agent": _Agent()}),
        )
        assert dec.read(state, TICKET).plan.subtasks[0].attempts == 1

    def test_level_2_plans_INSIDE_the_sandbox_with_the_workers_own_model(self, project):
        """⚠ Reading the Manager's role for a placed turn was wrong three ways.

        It ran a write-capable auto-mode model turn on the HOST in the
        operator's real project tree — the containment the placement exists to
        provide, skipped by the planning half. It planned against a tree where
        the subtask's module-relative scope paths do not exist and the earlier
        subtasks' commits are not present. And it ignored OL3's model split, so
        a Claude Manager driving a GPU Worker produced no approach at all —
        which is the headline mixed-fleet configuration.
        """
        state = _approved(project, with_approach=False)
        asked: dict = {}

        def approach(subtask, spec_slice):
            asked["subtask"] = subtask.id
            asked["slice"] = spec_slice
            return type(
                "A", (), {"ok": True, "steps": "1. do the thing", "problem": ""}
            )()

        agent = _Agent()
        st.take_one_step(
            project,
            MANAGER,
            TICKET,
            state=state,
            verifier=_Verifier(),
            committer=_Committer(),
            claims=_Claims(),
            placement=Placement(
                **{**self.PLACED, "agent": agent, "approach": approach}
            ),
        )
        assert asked["subtask"] == "s1"
        assert "5.3" in asked["slice"] or asked["slice"]
        # SCRUM-72e: and it is PERSISTED, so the stage can be shown to have
        # happened rather than inferred from a turn that is over.
        from rite_ai.local import level2

        stored = level2.read_approach(state, TICKET, "s1")
        assert not isinstance(stored, str) and stored is not None, stored
        assert stored.steps == "1. do the thing"
        assert stored.digest == level2.digest(
            dec.read(state, TICKET).plan.subtask("s1")
        )

    def test_a_level_2_that_could_not_run_DOES_stop_the_turn(self, project):
        """🔴 **Inverted by SCRUM-72e** (Robert's §5.5: override DD-2.4).

        This asserted the opposite — "advisory, and fail-open by design: a
        Level 2 that failed is REPORTED and the subtask runs exactly as it
        would have without one". That is what made a stage §3.3a names as
        mandatory one any endpoint hiccup removed, so "the model cannot skip a
        stage" was untrue of this one.

        ⚠ **And it is still not a FAILED subtask** (RL-47). An endpoint that
        was down did not produce a bad approach, it produced none: the step is
        refused and reported, the subtask keeps its status, and no attempt is
        burned. A refusal that spent an attempt would retire a subtask for an
        outage.
        """
        state = _approved(project, with_approach=False)
        before = dec.read(state, TICKET).plan.subtask("s1")

        def approach(subtask, spec_slice):
            return type(
                "A", (), {"ok": False, "steps": "", "problem": "the endpoint was down"}
            )()

        agent = _Agent()
        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            state=state,
            verifier=_Verifier(),
            committer=_Committer(),
            claims=_Claims(),
            placement=Placement(
                **{**self.PLACED, "agent": agent, "approach": approach}
            ),
        )
        assert step.ran is False
        assert step.accepted is False
        assert "no persisted Level-2 approach" in step.problem
        assert "the endpoint was down" in step.problem
        # The subtask is untouched: same status, same attempts.
        after = dec.read(state, TICKET).plan.subtask("s1")
        assert (after.status, after.attempts) == (before.status, before.attempts)
        # And no commit was asked for, so nothing was edited.
        assert step.commit == "" and step.verify_output == ""

    def test_a_placement_problem_stops_the_subtask(self, project):
        state = _approved(project)
        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            state=state,
            placement=Placement(worker=WORKER, problem="gpu1: its sandbox is stopped"),
        )
        assert step.ran is False
        assert "its sandbox is stopped" in step.problem

    def test_the_manager_tier_still_runs_on_the_host(self, project):
        # The path that existed before this module, unchanged: no Worker, so
        # the workspace is the project root.
        state = _approved(project)
        agent, verifier, committer, claims = (
            _Agent(),
            _Verifier(),
            _Committer(),
            _Claims(),
        )
        step = st.take_one_step(
            project,
            MANAGER,
            TICKET,
            agent=agent,
            state=state,
            verifier=verifier,
            committer=committer,
            claims=claims,
        )
        assert verifier.workspaces == [str(project)]
        assert committer.calls == [(str(project), f"rite-local/{TICKET}/s1")]
        assert claims.taken == [(("src/a.py",), MANAGER)]
        assert step.worker == ""
