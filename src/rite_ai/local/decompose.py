"""Level 1 — writing the PLAN (DD-1.2, DD-2.4; RL-6..RL-8, RL-68).

**This fills the one hole in a pipeline that otherwise exists.** `local/step.py`
runs an approved subtask; `decomposition.py` stores the plan the three gates
read; `duty_router.py` routes the `decompose` stage. What was missing is that
**nothing ever wrote a plan** — there was no caller of `with_subtask` and no
`Subtask` built outside its own module. This is the `decompose` analogue of
`step.py`: resolve the Manager holding DECOMPOSE, ask it, take the bytes, and —
because the model fills the hole badly as often as well — spend most of the code
on what rite does then.

The pipeline is **model → bytes → `parse` → `candidate_problems` → write
PENDING** (DD-2.2), and every arrow is a refusal point. Nothing between the model
and the APPROVED gate trusts the model's self-assessment, which is RL-7 applied
one level up, to the plan.

Three things this does NOT do, each because the alternative is a known defect
class (DD-3.3):

- **Never repairs a plan.** A plan that parses but fails validation is rejected,
  not patched. rite filling a missing verify is rite writing the plan and then
  checking its own work.
- **Never executes a partially-valid plan.** A decomposition is a claim about
  how a ticket splits; dropping the bad subtask changes the claim, and the rest
  compose into something that does not satisfy the ticket (DD-5.2).
- **Never lets a rejected plan count as a subtask's attempt** (RL-47): no subtask
  was tried. An infrastructure fault is not an attempt either, so the loop stops
  rather than spinning the budget.

On exhaustion the ticket **escalates** — it does not fall back to a free-form
run or a degraded plan. `escalated` says so; hooking it to the escalation budget
(design §8.9) is the orchestrator's, and this returns the fact rather than
inventing the mechanism.

**It never writes APPROVED.** The write is PENDING whatever the model emitted,
and a candidate that marked itself approved is refused by `candidate_problems`
(DD-3.5) before it is reached.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from rite_ai.config.managers import DECOMPOSE, ManagerRole, effective_duties
from rite_ai.local import decomposition as dec
from rite_ai.local.plan_validation import (
    DEFAULT_MAX_SUBTASKS,
    candidate_problems,
)

# Small and configurable (DD-3.4): a bounded retry assumes the model's second
# answer is informed by the first rejection, and that is unmeasured, so the loop
# is cheap and stops early when the reasons do not change (RL-69).
DEFAULT_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class Proposal:
    """What a decomposer agent produced: the raw bytes of a candidate plan, or
    the fact that the turn did not happen. `bytes` is never trusted — `parse`
    and `candidate_problems` are the boundary between them and a plan."""

    bytes: bytes = b""
    infrastructure_fault: bool = False
    problem: str = ""


class Proposer(Protocol):
    def propose(self, prompt: str, workspace: str) -> Proposal: ...


@dataclass
class DecomposeResult:
    """What one decomposition call did, in terms a Manager's output can state."""

    wrote: bool = False
    ticket: str = ""
    attempts: int = 0
    """Real attempts only — a turn that did not happen is not one (RL-47)."""
    reasons: tuple[str, ...] = ()
    """Why the last real attempt was rejected. Fed back into the retry (DD-3.3)."""
    warnings: tuple[str, ...] = ()
    escalated: bool = False
    converged_early: bool = False
    """RL-69: the retry's reasons were identical, so the loop converged on
    failure and the last attempt was not spent proving it again."""
    problem: str = ""
    """Why nothing was even attempted — a misconfiguration or an unreachable
    agent, not a rejected plan."""
    lines: list[str] = field(default_factory=list)


def decompose_ticket(
    root,
    manager: str,
    ticket: str,
    *,
    proposer: Proposer | None = None,
    state=None,
    roles: list[ManagerRole] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_subtasks: int = DEFAULT_MAX_SUBTASKS,
    ticket_text: str = "",
) -> DecomposeResult:
    """Author a plan for `ticket` with the Manager holding decompose, or say why
    none was written. Every dependency is injectable and defaults to production,
    so a test substitutes the agent without substituting the path (DD-4.3)."""
    root = Path(root)
    result = DecomposeResult(ticket=ticket)

    if roles is None:
        roles, problem = _roles_for(root)
        if problem:
            result.problem = problem
            return result
    holders = _decompose_holders(roles)
    # SCRUM-83 refined: the driving Manager authors when it holds the duty, and
    # otherwise DELEGATES to the one Manager that does.
    author, problem = author_for(roles, manager)
    if problem:
        result.problem = problem
        return result
    if author != manager:
        result.lines.append(
            f"{manager!r} holds no authoring duty, so the plan is authored by "
            f"{author!r}, which does (SCRUM-83 delegation)"
        )

    if state is None:
        from rite_ai.local.plan_state import layer

        state = layer(root)

    read = dec.read(state, ticket)
    if read.unavailable:
        result.problem = (
            f"the decomposition for {ticket} could not be read: {read.unavailable}"
        )
        return result
    if read.plan is not None and read.plan.released:
        # Overwriting an approved plan would silently drop its approval
        # (`returned_to_plan_review` is the only sanctioned way back to PENDING).
        result.problem = (
            f"{ticket} already has an approved decomposition — a decomposer does "
            "not overwrite one a plan-review holder released"
        )
        return result
    version = read.version

    if proposer is None:
        # ⚠ The AUTHOR's engine, not the driver's: a delegated plan is written
        # by the model whose role holds the duty.
        proposer, problem = _proposer_for(roles, author)
        if problem:
            result.problem = problem
            return result

    base_prompt = _prompt_for(ticket, ticket_text)
    previous: tuple[str, ...] | None = None
    addendum = ""

    while result.attempts < max_attempts:
        proposal = proposer.propose(base_prompt + addendum, str(root))
        if proposal.infrastructure_fault or (proposal.problem and not proposal.bytes):
            # The turn did not happen (RL-47): not an attempt, and spinning the
            # budget on a down endpoint proves nothing. Stop and say so.
            result.problem = (
                "the decomposer's turn did not happen (infrastructure fault), so "
                f"no attempt was made: {proposal.problem or 'see the agent output'}"
            )
            return result

        result.attempts += 1
        # SCRUM-88: the agent's stdout is a transcript; the plan is inside it.
        bytes_for_parse = plan_document(proposal.bytes)
        parsed = dec.parse(bytes_for_parse)
        if isinstance(parsed, str):
            reasons: tuple[str, ...] = (f"the bytes are not a decomposition: {parsed}",)
        else:
            # The ticket identity AND the author are rite's, never the model's.
            # The author is the Manager rite ASKED, not whoever the model named
            # (RL-6): a model that copies rite's own template emits
            # `decomposed_by: ""`, which validation rightly refuses, and a model
            # that names someone would misdirect RL-6's independence check once
            # `produced_by` is wired. Filling it here — the same way `ticket` is
            # filled — is what lets a prompt-compliant plan be written at all.
            # ⚠ `decomposed_by` is the AUTHOR, which under delegation is not
            # the driver. RL-6 compares the author with the approver, so
            # recording the driver here would compare the wrong pair.
            candidate = replace(parsed, ticket=ticket, decomposed_by=author)
            problems = candidate_problems(
                candidate,
                root=root,
                decompose_managers=tuple(holders),
                max_subtasks=max_subtasks,
                ticket_text=ticket_text,
            )
            if problems.ok:
                return _write_pending(
                    state, candidate, author, version, result, problems.warnings
                )
            reasons = problems.refusals

        result.reasons = reasons
        # 🔴 SCRUM-87: the model's own output, kept beside the refusal. Written
        # for EVERY refused attempt, not only a parse failure — a candidate
        # refused by validation is just as hard to argue with when nobody can
        # see it.
        kept = keep_rejected(
            root, manager, ticket, result.attempts, reasons, proposal.bytes
        )
        if kept:
            result.lines.append(f"the refused candidate was kept at {kept}")
        # RL-69 — identical reasons mean the loop has converged on failure.
        if previous is not None and reasons == previous:
            result.converged_early = True
            result.escalated = True
            result.lines.append(
                "the rejection reasons did not change on retry, so the loop "
                "converged on failure and stopped early (RL-69)"
            )
            return result
        previous = reasons
        addendum = _rejection_addendum(reasons)

    # Budget spent with no valid plan: escalate, never fall back (DD-3.3, RL-68).
    result.escalated = True
    result.lines.append(
        f"no valid plan after {result.attempts} attempt(s); the ticket escalates "
        "rather than running free-form or on a degraded plan (RL-68)"
    )
    return result


def _write_pending(state, candidate, author, version, result, warnings):
    """Write the valid candidate as a PENDING plan and never as APPROVED."""
    # `candidate.decomposed_by` is already the AUTHOR rite asked (set before
    # validation); pinned again here so the author recorded is never the
    # model's.
    #
    # ⚠ **The AUTHOR, not the driving Manager.** This parameter was `manager`
    # and re-pinned the driver here, which silently undid SCRUM-83's delegation
    # one line after it was applied: a plan written by the delegate was filed
    # as the driver's. RL-6 compares `decomposed_by` with the approver, so a
    # plan could then be approved by the Manager that wrote it while the check
    # meant to prevent exactly that still passed. Caught by a surviving
    # mutation, not by review.
    plan = replace(
        candidate,
        approval=dec.PENDING,
        approved_by="",
        decomposed_by=author,
    )
    written = dec.write(state, plan, version)
    if type(written).__name__ != "Written":
        # The plan was valid but the store moved under us; nothing was approved
        # and nothing partial was written.
        result.problem = (
            f"the plan for {plan.ticket} was valid but could not be written "
            f"({type(written).__name__}); nothing was approved"
        )
        return result
    result.wrote = True
    result.warnings = warnings
    return result


def _roles_for(root: Path):
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return [], f"this project's config.yaml will not parse: {parsed.message}"
    return parsed.coordination.manager_roles, ""


REJECTED_DIRNAME = "rejected-plans"
"""Where a refused candidate's own bytes are kept, under the Manager's state
directory. ⚠ **Not the journal**: `test_nothing_in_rite_reads_the_journal`
forbids any module outside `journal.py` from even locating that, and these are
not journal entries — they are an artefact a person opens when a plan would not
parse."""

MAX_KEPT_BYTES = 64 * 1024
"""Enough to see what the model did, capped so a runaway turn cannot fill a
disk. A truncated capture says so in the file."""


def plan_document(raw: bytes) -> bytes:
    """The decomposition inside an agent's output, or `raw` unchanged.

    🔴 **SCRUM-88, and it was never a model-capability problem.** `_propose`
    returns the WHOLE of the agent's stdout, and for goose that is an
    interactive transcript: an ASCII-art banner, a `▸ tree` / `▸ shell` trace
    per tool call, and every command's output. `parse` was handed all 58 KB of
    it and said `Expecting value: line 2 column 5 (char 5)` — which is exactly
    where `__( O)>` sits on line 2 of goose's banner.

    Measured 2026-10-08: a 17 GB local author emitted a completely valid
    two-subtask plan, with real verifies it had checked fail today, inside a
    ```json fence at the end of its transcript. rite threw it away and the
    ticket escalated. Nothing was wrong with the model.

    ⚠ **A fenced block is preferred, and the LAST decomposition-shaped
    candidate wins.** A transcript legitimately contains other JSON — this one
    carried `.rite/events.jsonl` lines (`{"at": ..., "event":
    "sandbox-started", "ticket": "1", ...}`) that `cat`-ing a file put there —
    so "the last balanced object" alone would pick an event. Candidates are
    filtered to ones that look like a decomposition, and the last is taken
    because an agent that revises its answer leaves the earlier attempt above.

    ⚠ **`raw` is returned unchanged when nothing qualifies**, so a genuinely
    unparseable output still produces `parse`'s own message about the real
    bytes rather than a tidier lie about a slice of them.
    """
    import json as _json
    import re as _re

    text = (raw or b"").decode("utf-8", "replace")
    candidates: list[str] = []
    # Fenced blocks first: ```json ... ``` or a bare ``` ... ```
    for m in _re.finditer(r"```(?:json)?\s*\n(.*?)```", text, _re.S):
        candidates.append(m.group(1))
    # Then every balanced top-level object, for an agent that fenced nothing.
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(text[start : i + 1])
                start = -1

    chosen = ""
    for candidate in candidates:
        body = candidate.strip()
        if not body.startswith("{"):
            continue
        try:
            parsed = _json.loads(body)
        except ValueError:
            continue
        # It must LOOK like a decomposition; a transcript's other JSON must not
        # be mistaken for one.
        if isinstance(parsed, dict) and "subtasks" in parsed:
            chosen = body
    return chosen.encode("utf-8") if chosen else (raw or b"")


def rejected_dir(root: Path, manager: str) -> Path:
    # ⚠ `manager_dir` ALREADY ends in `state/` (its docstring:
    # `<data>/rite/mail/<checkout>/<name>/state/`). Appending another put the
    # first captures under `state/state/`, which is how this was found.
    from rite_ai.managers import manager_dir

    return manager_dir(Path(root), manager) / REJECTED_DIRNAME


def keep_rejected(
    root: Path,
    manager: str,
    ticket: str,
    attempt: int,
    reasons: tuple[str, ...],
    raw: bytes,
) -> str:
    """Write a refused candidate's own output beside why it was refused.

    🔴 **SCRUM-87.** A candidate that would not parse was reported as "the
    bytes are not a decomposition" and the bytes were then dropped — captured
    in-process with `capture_output=True` and never written anywhere. So the
    one artefact needed to tell "the model wrote prose", "it fenced the JSON"
    and "it emitted a different schema" apart was the one thing not kept, and
    a format failure could only be guessed at. Measured 2026-10-08: a 17 GB
    local author failed both attempts with `Expecting value: line 2 column 5`
    and left nothing behind to read.

    Returns the path written, or "" — and NEVER raises. A capture that could
    not be written must not end a decomposition that was going to fail anyway;
    losing the diagnostic is bad, turning it into a crash is worse.
    """
    try:
        from rite_ai.names import require_safe_name

        # ⚠ `require_safe_name` RAISES or returns None — it is a validator, not
        # a sanitiser. Assigning its result named the first captures
        # `None-attempt1.txt`, losing which ticket they belonged to.
        require_safe_name(ticket, kind="ticket")
        safe = str(ticket)
        where = rejected_dir(root, manager)
        where.mkdir(parents=True, exist_ok=True)
        body = raw or b""
        clipped = body[:MAX_KEPT_BYTES]
        header = [
            f"# the candidate plan {manager!r} refused for ticket {safe}",
            f"# attempt: {attempt}",
            f"# bytes: {len(body)}"
            + (
                f" (showing the first {MAX_KEPT_BYTES})"
                if len(body) > len(clipped)
                else ""
            ),
            "# refused because:",
            *(f"#   - {r}" for r in reasons or ()),
            "#",
            "# Everything below is the MODEL'S OWN OUTPUT, verbatim and",
            "# unparsed. It is kept so a format failure can be read rather",
            "# than guessed at (SCRUM-87).",
            "",
        ]
        path = where / f"{safe}-attempt{attempt}.txt"
        with path.open("wb") as f:
            f.write(("\n".join(header)).encode("utf-8", "replace"))
            f.write(clipped)
        return str(path)
    except Exception:  # noqa: BLE001 - see the docstring
        return ""


def _decompose_holders(roles: list[ManagerRole]) -> set[str]:
    declared = len(roles)
    return {r.name for r in roles if DECOMPOSE in effective_duties(r, declared)}


def author_for(roles: list[ManagerRole], manager: str) -> tuple[str, str]:
    """(the Manager that authors a plan when `manager` drives, or a problem).

    🔴 **SCRUM-83, refined: an owner may HOLD OR DELEGATE the authoring duty.**
    The first shape of that rule required the Worker's owner to hold
    `decompose` itself, which forced the owner, the Worker's starter and the
    plan's author to be one Manager. That is satisfiable only by a Manager that
    can author — and the one measured to drive step 1 RELIABLY cannot (its
    decomposer adapter is unbuilt, DD-2.4), while the one that can author was
    measured to reach step 1 only sometimes. The rule excluded the one fleet
    shape that works.

    So an owner that holds no authoring duty may still own and drive a Worker,
    provided exactly one other Manager holds `decompose`: that Manager authors,
    and the owner drives. ⚠ **This is what keeps the original deadlock from
    returning** — the deadlock was "owner cannot author AND author cannot touch
    the Worker", and delegation breaks the first half: the plan IS authored, by
    the delegate, under the owner's own driving cycle.

    ⚠ Fail closed and never guess between candidates. Nobody holding the duty
    is a refusal; SEVERAL holding it is also a refusal, because picking one
    would make which model authored a plan depend on dict order, and RL-6
    compares the author with the approver.

    ⚠ Duty only. No engine or provider is named here, and
    `test_the_author_is_chosen_by_duty_alone` keeps it that way.
    """
    holders = _decompose_holders(roles)
    if manager in holders:
        return manager, ""
    if not holders:
        return "", (
            f"{manager!r} does not hold the decompose duty and no Manager in "
            "this project does, so no plan can be authored at all"
        )
    if len(holders) > 1:
        return "", (
            f"{manager!r} does not hold the decompose duty and {len(holders)} "
            f"Managers do ({', '.join(sorted(holders))}), so rite cannot tell "
            "which should author for it — give the duty to one, or to this "
            "Manager"
        )
    return next(iter(holders)), ""


def _rejection_addendum(reasons: tuple[str, ...]) -> str:
    """The rejection, fed back so the retry is informed rather than blind (DD-3.3)."""
    joined = "\n".join(f"- {r}" for r in reasons)
    return (
        "\n\nA previous attempt was REFUSED for these reasons. Produce a plan "
        "that does not repeat them; do not argue with them:\n" + joined
    )


def _prompt_for(ticket: str, ticket_text: str) -> str:
    """The decomposer's instruction. The model emits BYTES — a JSON object — and
    `parse` + `candidate_problems` decide whether they are a plan (DD-2.2)."""
    body = ticket_text.strip() or "(no ticket text was supplied)"
    return (
        f"Decompose ticket {ticket} into independent subtasks. Emit ONE JSON "
        "object and nothing else, of the form:\n"
        '{"format_version": '
        + str(dec.FORMAT_VERSION)
        + ', "ticket": "'
        + ticket
        + '", "decomposed_by": "", "subtasks": [\n'
        '  {"id": "s1", "intent": "<what this subtask achieves>",\n'
        '   "scope": ["<repo-relative path this subtask may touch>"],\n'
        '   "verify": "<a shell command that fails unless this subtask is done>",\n'
        '   "cites": ["<spec unit id this subtask depends on>"]}\n'
        "]}\n\n"
        "Rules: 2 to 8 subtasks; each scope path is repo-relative and inside the "
        "repo; each subtask cites at least one spec unit that exists; no two "
        "subtasks share a scope path; the verify is a real command, never `true` "
        "or `:`. Do NOT set an approval — a plan-review holder approves the "
        "plan.\n\nThe ticket:\n" + body
    )


def _proposer_for(roles: list[ManagerRole], manager: str):
    """(a production proposer, or a problem). Built from the DECOMPOSE Manager's
    declared engine — a local Manager runs the model its role names."""
    role = next((r for r in roles if r.name == manager), None)
    if role is None:
        return None, f"no Manager named {manager!r} in this project"
    if role.is_local:
        if role.agent != "goose":
            return None, (
                f"{manager!r} declares agent {role.agent!r}; only 'goose' has the "
                "local launch path today (S35)"
            )
        return GooseProposer(model=role.model, endpoint=role.endpoint), ""
    # ⚠ The Claude decomposer-agent adapter — capturing a full candidate from a
    # `claude` Manager — is not wired in this change. The local (goose) path is
    # the one the proof run B exercises; the Claude side is flagged, not faked.
    return None, (
        f"{manager!r} is a {role.engine} Manager; the Claude decomposer-agent "
        "adapter is not wired yet (DD-2.4). Inject a proposer, "
        "or author the plan with a local decompose Manager"
    )


@dataclass(frozen=True)
class GooseProposer:
    """One turn of Goose asked for a plan, returning its FULL output as bytes.

    Unlike `GooseAgent`, which runs a subtask and reports, this captures the
    whole of stdout — the candidate plan is the output, not a tail of it. The
    bytes are never trusted here; `parse` is the boundary (DD-2.2).

    ⚠ **The DD-4.5 window gap is closed here** (OL6): `context_limit` is placed
    when the caller passes one. It used to say "it does not pin the window it
    launches against", which at Ollama's unconfigured 4,096 default meant a plan
    authored in a window too small to hold the question (RL-T0 section 0). A
    caller that passes nothing still gets the old behaviour, so this is a lever
    rather than a change of default.
    """

    model: str
    endpoint: str
    binary: str = "goose"
    mode: str = "auto"
    launch: object | None = None
    instruction_dir: str = ""
    """Where the prompt file is CREATED. Empty is the system temp root, right on
    the host and wrong in a sandbox — the same asymmetry, and the same measured
    reason, as `GooseAgent.instruction_dir` (OL5): a host tempfile IS readable
    from inside a Worker, so it works while leaving the prompt readable by every
    other sandbox on the machine."""
    path_root: str = ""
    """`GOOSE_PATH_ROOT`. Empty for a host turn; required inside a sandbox, where
    `~/.local` is granted READ and not WRITE and Goose panics before it reaches
    the model (OL1)."""
    context_limit: int = 0
    """⚠ Closes DD-4.5 for this path. The docstring above used to say this
    proposer "does not pin the window it launches against", and at Ollama's
    unconfigured 4,096 default an agent's own system prompt does not fit
    (RL-T0 section 0) — so a plan was authored in a window too small to hold the
    question. 0 keeps the old behaviour for a caller that has no window to pass."""
    inherit_environment: bool = True
    """Whether the turn's environment starts from the HOST's.

    ⚠ **The same trap as `GooseAgent.inherit_environment`, and the same
    measured reason.** True is right on the host, where goose needs the
    operator's `PATH` and `HOME`. In a sandbox the launcher forwards a CLOSED
    set and REFUSES a secret-shaped name on argv (SB12), so inheriting means a
    `GITHUB_TOKEN` merely PRESENT in the operator's shell turns the turn into
    `could not start goose: SecretOnArgv`. Every yoloAI-launched session exports
    every token the operator holds.

    Carried here as well as on the agent because both halves of a placed
    subtask run inside the sandbox: Level 2 proposes the approach and the agent
    executes it. A fix on one of them only would have left the planning half
    dead on the same machines."""

    def propose(self, prompt: str, workspace: str) -> Proposal:
        from rite_ai.local.goose_agent import _infrastructure_fault, goose_environment

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".txt",
            prefix="rite-decompose-",
            delete=False,
            dir=self.instruction_dir or None,
        ) as handle_file:
            handle_file.write(prompt)
            instruction_path = handle_file.name
        argv = [self.binary, "run", "-n", "rite-decompose", "-i", instruction_path]
        # ⚠ Host only. See `inherit_environment`: a sandboxed turn that starts
        # from the operator's environment is refused by its own launcher.
        environment = dict(os.environ) if self.inherit_environment else {}
        environment.update(
            goose_environment(
                self.endpoint,
                self.model,
                context_limit=self.context_limit,
                path_root=self.path_root,
            )
        )
        environment["GOOSE_MODE"] = self.mode
        try:
            completed = self._launch(argv, workspace, environment)
        except Exception as e:  # noqa: BLE001 - a launch failure is a result
            return Proposal(
                problem=f"could not start {self.binary}: {type(e).__name__}: {e}"
            )
        finally:
            try:
                os.unlink(instruction_path)
            except OSError:
                pass
        said = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        fault = _infrastructure_fault(said)
        if fault:
            return Proposal(infrastructure_fault=True, problem=fault)
        return Proposal(bytes=(completed.stdout or "").encode("utf-8"))

    def _launch(self, argv, workspace, environment):
        if self.launch is not None:
            return self.launch(argv, workspace, environment)
        return subprocess.run(
            argv,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
