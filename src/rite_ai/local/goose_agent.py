"""Goose behind the harness's `Agent` protocol (B4a).

**The agent supplies file editing and the model's loop. Nothing else.**
`harness.run_subtask` keeps the plan-approval gate, the claim, the heartbeat,
the verify and the commit — this only runs one turn and reports what it saw.

⚠ **IT NEVER READS THE EXIT CODE, AND THAT IS THE WHOLE POINT.** Measured
2026-09-24, twice:

    OLLAMA_HOST=http://localhost:1  goose run -n ... -i instr.txt
    exit=0   secs=109   file created: NO
    "Network error: Could not connect to localhost:1"

`goose run` returns **0** for an unreachable provider, and separately **0**
for a model that does not exist. An adapter that trusted the exit status
would report a clean success over 109 seconds of retries and no work — which
is exactly the shape `ending` spent v0.5.1 shedding, arriving in a new tier.
So `returncode` is not consulted anywhere in this file.

**What is consulted instead is the PRECONDITION.** `engine_probe` already
answers "is the endpoint answering, does it serve this model, is its context
window big enough" — so the adapter asks before it spends two minutes finding
out. That turns a silent 109-second failure into an immediate named refusal,
and it reuses a check that exists rather than pattern-matching another tool's
prose.

⚠ **TWO GAPS IN THE FIT, reported rather than designed around** (B4b's, not
this ticket's):

1. **Goose publishes no machine-readable claim.** `goose run` has no
   `--json`; opencode has `--format json` and Open Interpreter has `--json`.
   So `claimed_success` here is "a turn ran and nothing visibly failed",
   which is weaker than a real claim — and RL-T0's honesty signal
   (`claim_disagreed_with_verify`) is correspondingly noisier for this engine.
2. **`AgentReport` cannot say "I could not run at all".** RL-47 decides that
   *"infrastructure faults are not attempts"*, and `engine_probe`'s own
   docstring says the harness records them as infrastructure — but `Outcome`
   has only PLANNED / RUNNING / VERIFIED / FAILED / ACCEPTED. There is no
   representation for it, so an unreachable endpoint is currently recorded as
   a failed attempt. **This adapter names the fault in `summary` and does not
   invent a status**, because adding one is a protocol change that belongs to
   a decision rather than to a commit.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

from rite_ai.local.harness import AgentReport, Context

RUN_TIMEOUT_SECONDS = 20 * 60
"""One subtask, not a release. The benchmark's slowest task at a correct
context window was 623s, so this is comfortably above measured work and well
below an overnight stall."""

_INFRASTRUCTURE_MARKERS = (
    "network error",
    "could not connect",
    "connection refused",
    "oss setup failed",
    "pull failed",
)
"""⚠ **A SECONDARY SIGNAL, and best-effort by construction.** The probe above
catches a provider that is down BEFORE the run; these catch one that dies
DURING it, by reading another tool's prose. They will rot when Goose rewords
a message, which is why they are not the primary check and why a miss
degrades to "the verify failed" rather than to "it worked"."""


def session_name(ticket: str, subtask_id: str) -> str:
    """The handle rite CHOOSES — Goose's model, not Claude's.

    Claude generates a session id rite must discover from a transcript; Goose
    takes `-n <name>`, measured to resolve by name rather than recency and to
    fail loudly on an unknown one. So rite names it deterministically and
    stores nothing: the whole designation machinery is bypassed for this
    engine (`ENGINE_CONTRACT.md`, axis 1).
    """
    safe = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in f"{ticket}-{subtask_id}"
    )
    return f"rite-{safe.strip('-').lower()}"


def goose_environment(
    endpoint: str, model: str, *, context_limit: int = 0, path_root: str = ""
) -> dict[str, str]:
    """What tells Goose WHICH model to run, and where.

    Shared by a Worker (`GooseAgent`) and a Manager's launch
    (`supervise`), so the two cannot come to disagree. ⚠ Without these Goose
    uses the operator's GLOBAL config (`~/.config/goose/config.yaml`), and
    it does so silently. Measured 2026-09-25: a Manager declared with
    `model: qwen3:8b` ran `qwen3-vl:8b-instruct`, because only the Worker path
    set them.

    `path_root` is `GOOSE_PATH_ROOT`, and it is **empty for a Manager and set
    for a Worker running inside a sandbox** — a difference measured rather
    than chosen (OL1).

    ⚠ **A Worker MUST pass one or Goose dies before it reaches the model.**
    Inside a seatbelt Worker `~/.local` is granted READ and not WRITE — the
    read grant `sandbox.worker_home` depends on, since it is what lets rite's
    Python load at all. Goose needs to WRITE its config, its session sqlite
    and its logs, so it panics in `session_manager.rs` with
    *"Failed to secure session database directory: PermissionDenied"*. This
    one variable relocates all four paths together.

    ⚠ **A Manager must NOT pass one, and this is the trap.** A local Manager
    already has writable state: its profile grants `~/.config/goose` and
    `~/.local/share/goose` by exact path rather than redirecting `HOME`
    (`managers.enclosure.ENGINE_HOME_IS_THE_OPERATORS`, measured). That store
    holds **the conversation handle every resumed cycle names**, so moving the
    root would hand a running Manager an empty session store and lose the
    conversation it is mid-way through. The silent-fallback defect above is
    already closed for Managers by `GOOSE_MODEL`/`GOOSE_PROVIDER`, which win
    over `config.yaml`; a path root removes the fallback's SOURCE as well,
    which is worth having inside a Worker and is not worth a Manager's
    conversation.

    ⚠ **Where a Worker's root points is itself load-bearing.** It must not be
    the workspace (it would show in `yoloai diff` and in the tree the
    committer inspects, the same argument `GooseAgent.run` makes about its
    instruction file), and it must not be the temp root (granted to every
    sandbox on the machine, so one Worker's conversation would be readable by
    another — the leak `enclosure.engine_tmp` measured for a Manager). The
    sandbox's own state directory is per-sandbox and was measured isolated:
    `ol2-w1` could not read `ol2-w2`'s layer or write into it.
    """
    env = {
        "GOOSE_PROVIDER": "ollama",
        "GOOSE_MODEL": model,
        "OLLAMA_HOST": endpoint.rstrip("/").removesuffix("/v1"),
    }
    if context_limit:
        # The window the model is served with, so Goose is not left to assume
        # one. Observed: Goose then compacts at 80% of it, which cannot rescue
        # a single message larger than the window (plan, Track MS).
        env["GOOSE_CONTEXT_LIMIT"] = str(context_limit)
    if path_root:
        env["GOOSE_PATH_ROOT"] = path_root
    return env


@dataclass(frozen=True)
class GooseAgent:
    """One turn of Goose against a local endpoint."""

    model: str
    endpoint: str
    binary: str = "goose"
    mode: str = "auto"
    """`GOOSE_MODE`. ⚠ Goose expresses permission in the ENVIRONMENT, not on
    argv — the second of the contract's three axes.

    **SETTLED (Robert, 2026-10-01): `auto` is the supported posture, and it is
    supported because `approve` CANNOT WORK HEADLESS, not because `auto` is
    safe.** B4d measured both in a headless run: `auto` exits 0 and ran `rm`
    on a file unattended; `approve` exits 1 on the first tool call with
    *"Tool approval required in non-interactive mode"*. So the choice is
    between an unconstrained agent and a session that dies immediately, and
    there is no third mode — `GOOSE_MODE` is whole-session, with no
    per-command refusal to collect.

    ⚠ **Nothing here is a boundary, and reading it as one is the mistake this
    docstring exists to prevent.** `auto` means rite's containment for a local
    Manager comes from WHERE it runs (the seatbelt profile
    `managers/enclosure.py` composes, D-76 as superseded) and never from this
    value. A local Manager host-run outside that profile is an unconstrained
    agent with the operator's own file and network access, which is what B4d
    said and is still true.

    ⚠ **It is also why the value is placed rather than inherited.** Left to
    the environment, a Manager takes whatever `GOOSE_MODE` the operator's
    shell exported — an operator with `approve` exported would get a Manager
    that dies on its first tool call — so `supervise` places `auto` on the
    pane itself. `engines.GOOSE.permission_env` is the destination; a refusal
    that left the value homeless was the earlier defect."""
    probe: Callable | None = None
    launch: Callable | None = None
    env: dict = field(default_factory=dict)
    inherit_environment: bool = True
    """Whether the turn's environment starts from the HOST's.

    ⚠ **True is right on the host and FATAL in a sandbox, which is why this
    exists.** `goose` on the host needs the operator's `PATH`, `HOME` and the
    rest to run at all, so the host turn inherits. A sandboxed turn is launched
    by `in_sandbox.exec_launcher`, which forwards a CLOSED set of keys and
    REFUSES — by design, SB12 — to put a secret-shaped variable on argv, where
    every other sandbox on the machine could read it.

    Put together, inheriting meant a sandboxed turn died on the operator's own
    shell. Measured on this machine: with `GITHUB_TOKEN` merely present in the
    environment, the turn returned `could not start goose: SecretOnArgv:
    GITHUB_TOKEN was passed to a sandboxed turn` — and `AgentReport` has no way
    to say "I could not run at all" for a launch failure, so it was recorded as
    a failed ATTEMPT against the subtask. RL-47 says only work counts; a turn
    refused by rite's own leak guard is not work. ⚠ And it would have fired on
    EVERY yoloAI-launched session, whose launch line exports every token the
    operator holds — so the sandboxed path would have been dead on the machine
    it was built for while looking like a model that could not edit a file.

    False builds the environment from nothing but Goose's own keys and `env`.
    Nothing is lost: the launcher forwards only `FORWARDED` anyway, so what the
    host environment contributed to a sandboxed turn was never more than the
    chance of refusing it."""
    instruction_dir: str = ""
    """Where the instruction file is CREATED. Empty means the system temp root,
    which is right on the host and wrong in a sandbox (OL5).

    ⚠ **This exists so the file is never written to a shared place at all.** A
    sandboxed turn's instruction carries the subtask's intent and its spec
    slice — project content — and the per-user temp root is granted to every
    sandbox on this machine, so writing it there and reading it from inside
    (which was measured to work) would leave the task text readable by every
    other Worker for as long as the turn ran. Copying it in afterwards does not
    fix that; it only shortens the window. Creating it inside the sandbox's own
    layer, which was measured ISOLATED, removes the window entirely.

    The path is identical from the host and from inside, so nothing has to be
    rewritten on argv: `in_sandbox.instruction_dir_for` returns it and this
    writes there."""

    def _probe(self):
        if self.probe is not None:
            return self.probe()
        from rite_ai.config.managers import ManagerRole
        from rite_ai.local.engine_probe import probe_engine

        return probe_engine(
            ManagerRole(
                name="local",
                engine="local:agent",
                endpoint=self.endpoint,
                model=self.model,
                agent=self.binary,
            )
        )

    def run(self, context: Context, workspace: str) -> AgentReport:
        blocked = self._preflight()
        if blocked:
            # The turn did not happen, so it is not an attempt (RL-47).
            return AgentReport(
                claimed_success=False, summary=blocked, infrastructure_fault=True
            )

        instruction = _instruction(context)
        handle = session_name(context.ticket, context.subtask.id)
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".txt",
            prefix="rite-goose-",
            delete=False,
            dir=self.instruction_dir or None,
        ) as handle_file:
            # ⚠ OUTSIDE the workspace on purpose. A file written into the
            # repo would be visible to the agent as part of the work, and
            # would sit in a tree the committer is about to inspect.
            handle_file.write(instruction)
            instruction_path = handle_file.name

        argv = [self.binary, "run", "-n", handle, "-i", instruction_path]
        # ⚠ `dict(os.environ)` ONLY when inheriting. See `inherit_environment`:
        # a sandboxed turn that starts from the host environment is refused by
        # its own launcher over a variable it never wanted.
        environment = dict(os.environ) if self.inherit_environment else {}
        environment.update(goose_environment(self.endpoint, self.model))
        environment["GOOSE_MODE"] = self.mode
        environment.update(self.env)
        try:
            completed = self._launch(argv, workspace, environment)
        except Exception as e:  # noqa: BLE001 - a launch failure is a result
            # ⚠ `infrastructure_fault=True`, and it was missing. A launch that
            # RAISED is by definition a turn that did not happen — which is
            # this field's own documented meaning, "a binary that is missing" —
            # and without it RL-47 broke on every launch failure: the subtask
            # spent an attempt proving that a tool could not be started.
            #
            # Two real cases reach here, both from the sandboxed launcher and
            # both measured: `SecretOnArgv` (the operator's shell held a token,
            # so rite's own leak guard refused the argv) and `SandboxGone` (a
            # delivery stopped the Worker's sandbox between the placement check
            # and the turn). Neither is evidence about the work.
            return AgentReport(
                claimed_success=False,
                summary=f"could not start {self.binary}: {type(e).__name__}: {e}",
                infrastructure_fault=True,
            )
        finally:
            try:
                os.unlink(instruction_path)
            except OSError:
                pass

        # ⚠ `completed.returncode` is deliberately NOT read. See the module
        # docstring: goose returns 0 for an unreachable provider.
        said = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        fault = _infrastructure_fault(said)
        if fault:
            return AgentReport(
                claimed_success=False,
                summary=f"infrastructure fault during the turn: {fault}",
                infrastructure_fault=True,
            )
        return AgentReport(claimed_success=True, summary=_tail(said))

    def _preflight(self) -> str:
        """Why this turn must not be attempted, or "" to go ahead."""
        try:
            probe = self._probe()
        except Exception as e:  # noqa: BLE001
            return f"could not check the engine before running it: {e}"
        problems = list(getattr(probe, "problems", []))
        if problems:
            return "infrastructure fault before the turn: " + "; ".join(problems)
        return ""

    def _launch(self, argv, workspace, environment):
        if self.launch is not None:
            return self.launch(argv, workspace, environment)
        return subprocess.run(
            argv,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            # ⚠ A model can emit bytes that are not valid UTF-8, and
            # text=True without this raises out of the adapter — turning a
            # strange character into a dead turn. Caught by the gate, not by
            # reasoning.
            errors="replace",
            timeout=RUN_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )


def _instruction(context: Context) -> str:
    """Exactly what the agent is given (RL-17): the subtask and its spec
    SLICE, never a pointer into the spec — a small model's context window
    cannot go and read the file."""
    scope = ", ".join(context.subtask.scope) or "(none declared)"
    # Level 2's approach, when there is one (DD-2.4). Placed AFTER the subtask
    # and the spec slice and labelled as the unit's own, so a model cannot read
    # it as part of what was approved — the approach is advice this unit gave
    # itself, and the paths and the check above are not negotiable.
    approach = getattr(context, "approach", "") or ""
    own_steps = (
        f"Your own steps for this, which you wrote before starting:\n{approach}\n\n"
        if approach.strip()
        else ""
    )
    return (
        f"Ticket {context.ticket}, subtask {context.subtask.id}.\n\n"
        f"{context.subtask.intent}\n\n"
        f"Change only these paths: {scope}\n\n"
        f"Relevant specification:\n{context.spec_slice}\n\n"
        f"{own_steps}"
        "Make the change directly. Do not ask for confirmation. "
        "Say briefly what you changed when you are done."
    )


def _infrastructure_fault(said: str) -> str:
    lowered = said.lower()
    for marker in _INFRASTRUCTURE_MARKERS:
        if marker in lowered:
            return marker
    return ""


def _tail(said: str, keep: int = 2000) -> str:
    said = said.strip()
    return said if len(said) <= keep else said[-keep:]
