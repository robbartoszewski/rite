"""Level 2 — the APPROACH, produced at the front of an executing unit's turn.

`DECOMPOSER_DESIGN.md` §2.4 lists this as Level 2's one unbuilt piece: *"A step
at the front of the turn — before the unit edits: given the approved subtask and
its spec slice, produce the unit's own steps with the decomposition model."* It
was deferred because *"its Claude side waits on the `--agent claude` literal …
the local side lands with the proof runs"*. The literal is gone (OL5) and the
proof runs are in `spikes/OL1-ollama-inside-a-worker-sandbox.md`, so this is the
local side.

**Level 1 is the PLAN, Level 2 is the APPROACH.** Level 1 says which subtasks
exist and what each may touch; it is gated. Level 2 says how THIS unit intends to
carry out one already-approved subtask, with the unit's own decomposition model
(`decomposition_model_for` / `worker_decomposition_model`) — Opus for a Claude
unit, its own model for a local one, since a GPU unit has nothing to gain from
loading a second model.

⚠ **Two properties keep Level 2 outside the gates, and the design is explicit
that both are CHECKS and not conventions.**

1. **The subtask executed must be byte-identical to the approved one.** The
   approach may not change `scope`, `verify` or `cites` — those are what plan
   review approved and what the committer's allowlist and RL-7's verify are built
   from. `boundary_problem` is that check.
2. **Nothing is persisted into the `Decomposition`.** The approach is a working
   artifact that reaches the execution turn's instruction and stops there. *"If
   it is written into `Decomposition`, the gates start reading the executor's own
   words"* — so this module returns text and is given no writer.

**A failed approach is not a failed subtask.** An endpoint that was down did not
produce a bad plan; it produced nothing (RL-47's distinction). The caller runs the
turn without an approach rather than failing it, because Level 2 improves a turn
and is not a gate on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from rite_ai.local.decomposition import Subtask

# What the unit is asked for. The scope/verify/cites sentence is in the prompt as
# well as in `boundary_problem`, because telling a model not to do a thing is
# cheaper than catching it afterwards — and the check is still there, since a
# prompt is not an enforcement mechanism.
PROMPT = """You are about to carry out ONE already-approved subtask.

Subtask {sid}: {intent}

You may change only these paths: {scope}
It will be checked afterwards by running: {verify}

Relevant specification:
{spec_slice}

Write the ordered steps YOU will take to do this, as a short numbered list.

Rules:
- Do not change what the subtask is. The paths, the check and the cited
  specification are fixed and were approved by someone else.
- Do not propose extra subtasks, and do not propose touching other paths.
- Steps only. Do not make any edit now.
"""

MAX_STEPS_CHARS = 4000
"""A cap, because this text is PREPENDED to the execution turn's instruction and
a model that answers with an essay would spend the window the work needs. Cut
rather than refused: a truncated approach is still useful, and an approach is not
a gate (module docstring)."""


@dataclass(frozen=True)
class Approach:
    """The unit's own steps, or why there are none."""

    steps: str = ""
    problem: str = ""
    infrastructure_fault: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.steps.strip()) and not self.problem


def boundary_problem(approved: Subtask, executing: Subtask) -> str:
    """Why `executing` is not the subtask that was approved, or "" (DD-2.4).

    ⚠ **The check that keeps Level 2 outside the gates.** Level 2 runs between
    approval and execution, so if it could alter the subtask it would be
    rewriting what plan review passed — and `scope` is the committer's allowlist
    while `verify` is the only thing RL-7 trusts, so a change to either turns the
    gates into decoration.

    Byte-identical on the load-bearing fields, not "close enough". `status`,
    `attempts`, `branch` and `last_failure` are the harness's own bookkeeping and
    move legitimately during a turn, so they are not compared.
    """
    for field in ("id", "intent", "scope", "verify", "cites"):
        was, now = getattr(approved, field), getattr(executing, field)
        if was != now:
            return (
                f"the subtask about to run is not the one that was approved: "
                f"{field} was {was!r} and is now {now!r}. Level 2 produces an "
                "approach and may not change the subtask (DD-2.4)"
            )
    return ""


def plan_approach(
    subtask: Subtask,
    spec_slice: str,
    *,
    model: str,
    endpoint: str,
    propose=None,
    workspace: str = "",
    instruction_dir: str = "",
    path_root: str = "",
    context_limit: int = 0,
) -> Approach:
    """The unit's own steps for `subtask`, or an `Approach` saying why not.

    `propose` is injected so this is testable without a model on the machine —
    the same reason `engine_probe` injects `get` and `which`. The default is one
    Goose turn with the DECOMPOSITION model, which is a different model from the
    one the turn itself will run.
    """
    if not model or not endpoint:
        return Approach(problem="no decomposition model or endpoint for this unit")
    if propose is None:
        from rite_ai.local.decompose import GooseProposer

        propose = GooseProposer(
            model=model,
            endpoint=endpoint,
            instruction_dir=instruction_dir,
            path_root=path_root,
            context_limit=context_limit,
        ).propose

    prompt = PROMPT.format(
        sid=subtask.id,
        intent=subtask.intent,
        scope=", ".join(subtask.scope) or "(none declared)",
        verify=subtask.verify or "(nothing)",
        spec_slice=spec_slice,
    )
    try:
        proposal = propose(prompt, workspace)
    except Exception as e:  # noqa: BLE001 - a launch failure is a result
        return Approach(problem=f"the approach step could not run: {e}")
    problem = getattr(proposal, "problem", "")
    if problem:
        return Approach(
            problem=problem,
            infrastructure_fault=bool(getattr(proposal, "infrastructure_fault", False)),
        )
    raw = getattr(proposal, "bytes", b"") or b""
    steps = raw.decode("utf-8", "replace").strip()
    if not steps:
        # Not a fault: the model answered and said nothing useful.
        return Approach(problem="the approach step produced no steps")
    return Approach(steps=steps[:MAX_STEPS_CHARS])
