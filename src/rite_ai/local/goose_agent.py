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


@dataclass(frozen=True)
class GooseAgent:
    """One turn of Goose against a local endpoint."""

    model: str
    endpoint: str
    binary: str = "goose"
    mode: str = "auto"
    """`GOOSE_MODE`. ⚠ Goose expresses permission in the ENVIRONMENT, not on
    argv — the second of the contract's three axes. B4d is measuring what
    `auto` does with an operation that needs approval; the docs contradict
    themselves and this is not yet settled."""
    probe: Callable | None = None
    launch: Callable | None = None
    env: dict = field(default_factory=dict)

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
            return AgentReport(claimed_success=False, summary=blocked)

        instruction = _instruction(context)
        handle = session_name(context.ticket, context.subtask.id)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", prefix="rite-goose-", delete=False
        ) as handle_file:
            # ⚠ OUTSIDE the workspace on purpose. A file written into the
            # repo would be visible to the agent as part of the work, and
            # would sit in a tree the committer is about to inspect.
            handle_file.write(instruction)
            instruction_path = handle_file.name

        argv = [self.binary, "run", "-n", handle, "-i", instruction_path]
        environment = dict(os.environ)
        environment.update(
            {
                "GOOSE_PROVIDER": "ollama",
                "GOOSE_MODEL": self.model,
                "GOOSE_MODE": self.mode,
                "OLLAMA_HOST": self.endpoint.rstrip("/").removesuffix("/v1"),
            }
        )
        environment.update(self.env)
        try:
            completed = self._launch(argv, workspace, environment)
        except Exception as e:  # noqa: BLE001 - a launch failure is a result
            return AgentReport(
                claimed_success=False,
                summary=f"could not start {self.binary}: {type(e).__name__}: {e}",
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
    return (
        f"Ticket {context.ticket}, subtask {context.subtask.id}.\n\n"
        f"{context.subtask.intent}\n\n"
        f"Change only these paths: {scope}\n\n"
        f"Relevant specification:\n{context.spec_slice}\n\n"
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
