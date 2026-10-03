"""Where a local subtask RUNS: inside a Worker's sandbox (SCRUM-54, OL5).

**The piece OL5 was built for and did not have.** `in_sandbox.exec_launcher`
and `in_sandbox.instruction_dir_for` were written, measured against a real
sandbox and tested, and `sandbox.goose_path_root` with them — and all three had
no caller in `src/`, which is the defect class `tests/test_no_dead_wiring.py`
exists for. So the local tier ran at MANAGER tier (`step.take_one_step` passes
`worker=manager` and `workspace=root`, the operator's own tree), and a mixed
Claude+GPU fleet was configurable (`WorkerManifest` carries engine, endpoint,
model, agent and window since OL3) but not runnable. This module is the join.

⚠ **It is a PLACEMENT, not a second harness.** `step.take_one_step` keeps the
plan read, RL-6's approval gate, the spec slice, Level 2's boundary check and
rite's own recording; this answers one question for it — *where does this
subtask run, and what runs it* — and `run_subtask` is unchanged. A second copy
of those gates is a second copy that drifts, and the gates are the tier.

## What runs where, and why that split is the measured one

The agent runs INSIDE the sandbox. The verify and the commit run on the HOST,
against the sandbox's own copy. That is not a compromise; it is three facts.

1. **The work is in the copy, not the host tree.** yoloAI gives a Worker a COPY
   of its workdir and the turn edits that. Measured here against a fixture under
   an ungranted root, with the control that matters: a turn that wrote
   `marker.txt` inside left the original host file untouched. So a verify run in
   the host's project root would test a tree the model never touched — it would
   pass or fail on something else entirely.
2. **The host can reach the copy, while the sandbox is still running.** Measured:
   `git status` in the copy showed the turn's edits, and a host `git commit`
   onto a branch there succeeded with the sandbox active. So the verify and the
   commit need no second mechanism — `SubprocessVerifier` and `GitCommitter` are
   reused unchanged, pointed at the copy.
3. **It is how a Claude Worker already hands back.** `publishing/deliver.py`
   stops the sandbox, finds that same copy, and collects `refs/heads/<ticket>`
   from each module clone in it. A GPU Worker that commits there hands back
   through the very same path, which is what "the same way a Claude Worker
   does" has to mean if it is to mean anything.

⚠ **And it keeps RL-7 stronger than the alternative would.** "rite runs the
verify" is the one thing the tier does not delegate. Run through `yoloai exec`,
the verify would execute inside the box the agent has had unconstrained
`auto`-mode run of — its PATH, its interpreters, its installed tools. Run on the
host against the copy, the agent can still edit the test FILES (they are its
work), but it cannot reach the toolchain that judges them.

⚠ **The branch is the TICKET, not `rite-local/<ticket>/<subtask>`.** `deliver`
collects one ref and that is its name; see `harness.run_subtask`'s `branch`.

## What this refuses, and why refusing is the answer

A sandbox that is not running, a copy that cannot be found, a Worker with no
writable Goose root, a Worker whose modules are not exactly one — each is a
named refusal rather than a default. The tier's whole history is silent
misconfiguration costing a run: a window read as "enough" (S33/S34), a Manager
running a model it did not declare (`a56ecd1`), a turn refused before it started
and counted as an attempt. A placement rite is not sure of is the same shape, so
it says so and nothing runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Placement:
    """Where one subtask runs, and what runs it.

    An empty placement with no `problem` means MANAGER tier — the host path
    that existed before this module, unchanged. That is deliberately not an
    error: a project with no Worker assigned to the ticket, or with a Claude
    Worker on it, is not misconfigured.
    """

    worker: str = ""
    sandbox: str = ""
    workspace: str = ""
    """The HOST path the verify and the commit run in: the module's clone
    inside the sandbox's copy. Empty for a Manager-tier placement."""
    subdir: str = ""
    """The same directory named RELATIVE to the copy's root, which is what the
    turn inside the sandbox is given. `yoloai exec` has no `--cwd` and starts
    in the copy's root, while a Worker's modules are clones one level down."""
    branch: str = ""
    agent: object | None = None
    problem: str = ""

    @property
    def sandboxed(self) -> bool:
        return bool(self.worker) and not self.problem


def placement_for(
    root: Path, manager: str, ticket: str, *, binary: str = ""
) -> Placement:
    """Where `ticket`'s next subtask runs under `manager`.

    `Placement()` for the Manager-tier path, a sandboxed one for a local
    Worker, or one carrying `problem` when a local Worker is assigned and rite
    cannot place a turn in it.
    """
    root = Path(root)

    # ⚠ The SAME selector `loop._ask_delivery` uses, deliberately. It reads what
    # `rite sandbox start` recorded, so the Worker that executes the subtask and
    # the Worker named in the delivery request are the same by construction
    # rather than by two functions agreeing. A delivery request naming a Worker
    # whose sandbox holds no commits is the failure this avoids.
    from rite_ai.local.loop import _worker_for

    worker = _worker_for(root, manager, ticket)
    if not worker:
        return Placement()

    from rite_ai.sandbox import worker_manifest

    manifest = worker_manifest(root, worker)
    if not bool(getattr(manifest, "is_local", False)):
        # A Claude Worker, or a manifest that could not be read — `None means
        # "could not tell", never "a Claude Worker"`, and either way this is not
        # a local turn to place. The Manager-tier path is what ran before.
        return Placement()

    def no(why: str) -> Placement:
        return Placement(worker=worker, problem=f"{worker}: {why}")

    shape = _engine_problem(manifest)
    if shape:
        return no(shape)

    from rite_ai.sandbox import existing_sandbox_name, worker_sandbox_status

    sandbox = existing_sandbox_name(worker, root)
    status = worker_sandbox_status(worker, root)
    if not status.known:
        # "I could not check" and "there is no sandbox" are opposite answers;
        # only one of them is safe to act on (`worker_sandbox_status`).
        return no(f"rite could not ask yoloAI about its sandbox: {status}")
    if str(status) != "active":
        return no(
            f"its sandbox is {status}, and a turn runs inside a RUNNING one — "
            f"`rite sandbox start --worker {worker} --ticket {ticket}` starts it"
        )

    # ⚠ The module BEFORE the copy, deliberately. This is a question about what
    # the Worker declares, and a Worker whose manifest rite cannot act on should
    # be told that rather than told about a sandbox directory — the config is
    # the thing the operator can fix, and reporting the environment first sends
    # them to look at the wrong half.
    module, why = _one_module(root, worker)
    if why:
        return no(why)

    from rite_ai.sandbox import _sandbox_copy  # noqa: PLC2701 - see below

    # ⚠ Private, and imported anyway, exactly as `publishing/deliver.py` does.
    # The copy's layout is yoloAI's and not an interface; one function derives
    # it, and a second caller reaching for a second derivation is how the two
    # would come to disagree about where a Worker's work is. Sharing the
    # private one is the lesser evil, and it is the one `deliver` already took.
    copy = _sandbox_copy(sandbox, root / "workers" / worker)
    if copy is None:
        return no(f"rite could not find its sandbox's copy of workers/{worker}/")
    clone = copy / module
    if not (clone / ".git").exists():
        return no(
            f"its sandbox holds no git checkout of {module} at {clone} — "
            f"`rite prepare --worker {worker}` clones it, before the sandbox starts"
        )

    from rite_ai.sandbox import goose_path_root

    path_root = goose_path_root(sandbox, binary)
    if not path_root:
        # `goose_path_root` returns "" rather than guessing, and says the caller
        # must treat it as a refusal: Goose with no writable root does not fail
        # halfway, it panics in `session_manager.rs` before reaching the model.
        return no(
            "rite could not locate its sandbox's own writable layer, so Goose "
            "has nowhere to put its config, session store and logs — it would "
            "panic before reaching the model rather than fail a turn"
        )

    from rite_ai.local.in_sandbox import instruction_dir_for

    instruction_dir = instruction_dir_for(sandbox, binary)
    if not instruction_dir:
        # "" means the layer could not be found, and the default it falls back
        # to is the host temp root — granted to EVERY sandbox on this machine,
        # so the subtask's intent and its spec slice would be readable by every
        # other Worker for as long as the turn ran. That is the leak OL5 closed
        # by writing the file into the sandbox's own layer; falling back to it
        # silently would reopen it.
        return no(
            "rite could not locate its sandbox's exchange directory, and the "
            "fallback is the shared temp root — every other Worker on this "
            "machine could read this subtask's intent and spec slice there"
        )

    return Placement(
        worker=worker,
        sandbox=sandbox,
        workspace=str(clone),
        subdir=module,
        branch=ticket,
        agent=_sandboxed_agent(
            manifest, sandbox, module, path_root, instruction_dir, binary
        ),
    )


def _engine_problem(manifest) -> str:
    """Why this local Worker's declared engine cannot run a turn, or ""."""
    from rite_ai.config.managers import window_undeclared

    if window_undeclared(manifest):
        # S33/S34: the server's default cannot be read before the model loads,
        # and a prompt over it is cut from the front with no error — 4,096 on
        # this Mac, smaller than the agents' own prompts.
        return (
            "it declares no context_window, and a local engine run at its "
            "server's default has its prompt silently cut from the front"
        )
    if getattr(manifest, "agent", "") != "goose":
        # ⚠ The SAME refusal `step._agent_for` makes for a Manager, and the same
        # reason (S35): window enforcement is Goose-shaped today (`pin_window`
        # plus `GOOSE_CONTEXT_LIMIT`), so another agent would run at the
        # server's default. Said here as well because a Worker reaches this
        # module and never reaches that one.
        return (
            f"it declares agent {getattr(manifest, 'agent', '') or '(none)'!r}; "
            "only 'goose' has window enforcement today (S35), and an unenforced "
            "window is how a 4,096-token default silently ruins a run"
        )
    if not getattr(manifest, "model", ""):
        return "it declares no model, and the engine class is a label (OL3)"
    if not getattr(manifest, "endpoint", ""):
        return "it declares no endpoint, and the engine class is a label (OL3)"
    return ""


def _one_module(root: Path, worker: str) -> tuple[str, str]:
    """(the module whose clone the turn runs in, or "", why not).

    ⚠ **Exactly one, and more than one is REFUSED rather than chosen.** A
    `Subtask` carries `scope` — paths — and no module, and the paths are
    relative to the clone they are written against. With two clones in the
    copy, `src/x.py` names a file in both and rite would have to guess which
    repository to run the verify in and which to commit to. A wrong guess
    commits the work to the wrong module's branch, where `deliver` collects it
    into the wrong project checkout — silently, since both halves succeed.

    So this is a stated limitation and not a hidden one: a multi-module local
    Worker needs a module on the subtask, which is a decomposition-format
    change and belongs to its own ticket.
    """
    from rite_ai.config.parse import ParseError, parse_worker
    from rite_ai.workspace.manage import modules_for_worker

    manifest = parse_worker(root / "workers" / worker / "worker.yml")
    if isinstance(manifest, ParseError):
        return "", f"its worker.yml will not parse: {manifest.message}"
    modules = modules_for_worker(root, list(manifest.modules) or None)
    if not isinstance(modules, list):
        return "", str(modules)
    if len(modules) == 1:
        return modules[0].name, ""
    if not modules:
        return "", (
            "it has no modules, so there is no checkout for a turn to run in — "
            "give it one in its worker.yml and `rite prepare` it"
        )
    return "", (
        f"it has {len(modules)} modules ({', '.join(m.name for m in modules)}) "
        "and a subtask names paths without naming a module, so rite cannot "
        "tell which clone to verify in or commit to — a local Worker runs one "
        "module today, and guessing would commit the work to the wrong one"
    )


def _sandboxed_agent(
    manifest, sandbox: str, subdir: str, path_root: str, instruction_dir: str, binary
):
    """A `GooseAgent` whose turn happens inside `sandbox`.

    ⚠ **`inherit_environment=False` is load-bearing, not tidiness.** Measured:
    with `GITHUB_TOKEN` merely PRESENT in the operator's shell, a sandboxed turn
    came back `could not start goose: SecretOnArgv` — `GooseAgent.run` builds
    `dict(os.environ)`, which is right on the host, and `exec_argv` refuses to
    put a secret-shaped variable on argv, which is right in a sandbox (SB12
    measured a sandbox's environment readable from other sandboxes). Together
    they made the sandboxed path dead on every yoloAI-launched session, whose
    launch line exports every token the operator holds — and dead in the worst
    way, recorded as a failed ATTEMPT against the subtask rather than as a turn
    that never ran (RL-47).

    ⚠ Nothing is lost by not inheriting. `exec_argv` forwards `FORWARDED` and
    nothing else, and the rest of the turn's environment — `PATH` included —
    comes from the sandbox itself, because `env KEY=VAL cmd` ADDS to the
    environment it was handed rather than replacing it (`env -i` would replace
    it, and is not used).

    ⚠ **The probe is given the MANIFEST**, for the reason `step._agent_for`
    gives at length: left to itself `GooseAgent._probe` fabricates a
    `ManagerRole` with no `context_window` and then fails its own preflight on
    the window check, so a correctly configured unit can never run. A
    `WorkerManifest` answers `name`, `agent`, `endpoint` and `context_window`,
    which is every attribute `probe_engine` and `window_undeclared` read.
    """
    from rite_ai.local.engine_probe import probe_engine
    from rite_ai.local.goose_agent import GooseAgent, goose_environment
    from rite_ai.local.in_sandbox import exec_launcher

    return GooseAgent(
        model=manifest.model,
        endpoint=manifest.endpoint,
        probe=lambda m=manifest: probe_engine(m),
        launch=exec_launcher(sandbox, binary, subdir=subdir),
        instruction_dir=instruction_dir,
        inherit_environment=False,
        # The shared helper, so a Worker's turn and a Manager's launch cannot
        # come to disagree about which model runs where (`goose_environment`).
        # `GOOSE_PATH_ROOT` is set for a Worker and must NOT be for a Manager:
        # a difference measured (OL1), not chosen.
        env=goose_environment(
            manifest.endpoint,
            manifest.model,
            context_limit=getattr(manifest, "context_window", 0),
            path_root=path_root,
        ),
    )
