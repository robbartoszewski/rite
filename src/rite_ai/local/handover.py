"""SCRUM-100 — the account a pipeline-delivered ticket hands back.

🔴 **A Worker-driven ticket hands back prose and a pipeline-driven one handed
back nothing.** `rite_ai.handback` is, in its own words, "the record a Worker
writes when it finishes": Worker-authored, from a Worker's agent session. On
the staged-pipeline path no Worker session finishes — rite drives each
subtask's turn itself and requests the delivery after RL-8 — so nothing was
there to write one.

Measured on the v0.7.0 gate run (`smoke_mixed-20261009T034619Z`): ticket 1,
driven by the Claude Worker, produced 11 KB of account — what changed, what it
substituted and why, three things it wanted filed, one open question. Ticket 2,
driven by the pipeline, produced a branch and silence. The operator could see
*that* work arrived and nothing about it.

⚠ **Composed from RECORDS, which is a different kind of claim.** Every line
here is read back from something rite persisted — the plan, its rejections, the
approach digests, each subtask's verify result, RL-8's command and outcome, the
stage log's timestamps, and git. That makes it better evidenced than an agent's
recollection and *worse* at saying what the work felt like: it cannot tell you
what the agent was unsure about. The prose says which it is, in its first
lines, so a reader never mistakes one for the other.

⚠ **Said in the prose, never as a new field.** `worker_handbacks._fields`
keeps only values where `isinstance(v, str)`, so a `by_rite: bool` on the
record would be silently dropped on the way to the Manager — a flag that looks
set and travels nowhere. The distinction is load-bearing, so it goes where it
cannot be lost.

⚠ **Never raises.** It runs inside `honour_deliveries`, on the supervisor's
cycle. A record it cannot read becomes a line saying so; a ticket whose plan it
cannot read at all returns "" and the caller writes no handback, because a
handback that says nothing is worse than the absence it replaces.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_REASON_CHARS = 600
"""How much of one rejection's text is quoted. The RL-6 reviewer writes at
length — the gate run's was 1.4 KB — and the point here is which objection was
raised, with the full text one record away."""

MAX_OUTPUT_CHARS = 800
"""How much of RL-8's output is quoted, for the same reason."""

MAX_COMMITS = 20


def _git(args: list[str], cwd: Path) -> str:
    """git's stdout, or "" — never raises, and never asks for credentials."""
    import os

    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "") if done.returncode == 0 else ""


def _landed(root: Path, branch: str) -> list[str]:
    """What the branch carries, as lines — or a line saying it could not be read.

    ⚠ Read from the PROJECT's checkout, not a sandbox: `deliver` collects the
    ticket branch into the project before it returns ("a refusal after
    collecting loses nothing: the work is already on the project"), and the
    Worker's sandbox is destroyed by then. So this is the one place the commits
    are still readable when a handback is written.
    """
    if not branch:
        return ["rite recorded no branch for this ticket, so nothing was read from git"]
    from rite_ai.config.parse import load_project

    project = load_project(Path(root))
    dirs = [Path(root)]
    if not isinstance(project, list):
        for module in project.modules:
            where = Path(root) / str(getattr(module, "path", "") or ".")
            base = str(getattr(module, "branch", "") or "main")
            if (where / ".git").exists() and _git(
                ["rev-parse", "--verify", branch], where
            ):
                return _describe(where, branch, base)
            dirs.append(where)
    for where in dirs:
        if (where / ".git").exists() and _git(["rev-parse", "--verify", branch], where):
            return _describe(where, branch, "main")
    return [
        f"branch {branch!r} is not in this project's checkout, so what it "
        "carries could not be read here"
    ]


def _describe(where: Path, branch: str, base: str) -> list[str]:
    out: list[str] = []
    subjects = [
        line
        for line in _git(
            ["log", "--format=%h %s", f"{base}..{branch}"], where
        ).splitlines()
        if line.strip()
    ]
    stat = _git(["diff", "--stat", f"{base}...{branch}"], where).strip()
    out.append(f"Branch `{branch}`, in `{where.name}`, against `{base}`:")
    out.append("")
    if subjects:
        for line in subjects[:MAX_COMMITS]:
            out.append(f"- `{line.split(' ', 1)[0]}` {line.split(' ', 1)[-1]}")
        if len(subjects) > MAX_COMMITS:
            out.append(f"- …and {len(subjects) - MAX_COMMITS} more")
    else:
        out.append("- no commit on this branch that is not already on " + base)
    if stat:
        out.append("")
        out.append("```")
        out.extend(stat.splitlines())
        out.append("```")
    return out


def _rejections(state, ticket: str) -> int:
    """How many times this ticket's plan was sent back, from the stage log.

    The log is append-only, so a `rejected` transition stays recorded however
    many plans replace each other afterwards. See the call site for why
    `plan.returns` cannot answer this.
    """
    from rite_ai.local import stage as st

    try:
        got = st.read(state, ticket)
    except Exception:  # noqa: BLE001 - never raises; see the module docstring
        return 0
    record = getattr(got, "record", None)
    if record is None:
        return 0
    return sum(
        1
        for row in (getattr(record, "log", ()) or ())
        if getattr(row, "to", "") == st.REJECTED
    )


def _stamp(at) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(at)))
    except (TypeError, ValueError):
        return "an unreadable time"


def branch_for(plan) -> str:
    """The branch a pipeline ticket's work is on: the one its subtasks name."""
    for subtask in getattr(plan, "subtasks", ()) or ():
        branch = str(getattr(subtask, "branch", "") or "")
        if branch:
            return branch
    return ""


def account_for(root, manager: str, ticket: str, *, state=None) -> str:
    """The account of a pipeline-delivered ticket, or "" when there is no plan.

    "" means "this is not a pipeline ticket, or its plan cannot be read", and
    the caller writes no handback for it — see the module note.
    """
    try:
        return _account_for(Path(root), manager, ticket, state)
    except Exception:  # noqa: BLE001 - see the module docstring
        return ""


def _account_for(root: Path, manager: str, ticket: str, state) -> str:
    from rite_ai.local import decomposition as dec
    from rite_ai.local import plan_state
    from rite_ai.local import recompose as rc
    from rite_ai.local import stage as st

    if state is None:
        state = plan_state.layer(root)
    read = dec.read(state, ticket)
    plan = getattr(read, "plan", None)
    if plan is None:
        return ""

    branch = branch_for(plan)
    lines: list[str] = [
        f"# Ticket {ticket} — composed by rite from the staged pipeline's records",
        "",
        "⚠ **This is not a Worker's account of its own work.** No Worker session "
        "finished this ticket: rite drove each subtask's turn itself and requested "
        "the delivery once the recomposition verify (RL-8) passed. Every line "
        "below is read back from a record rite persisted, so it is checkable — "
        "and it cannot tell you what the agent was unsure about, which a Worker's "
        "own handback can.",
        "",
        f"Driven by Manager `{manager}`.",
        "",
    ]

    # --- the plan, and who may be trusted to have reviewed it (RL-6) ---------
    author = str(getattr(plan, "decomposed_by", "") or "unrecorded")
    approver = str(getattr(plan, "approved_by", "") or "unrecorded")
    lines += [
        "## The plan",
        "",
        f"- authored by `{author}`",
        f"- approved by `{approver}`",
    ]
    if author and approver and author != approver:
        lines.append(
            f"- RL-6 holds: the author and the approver are different Managers "
            f"(`{author}` ≠ `{approver}`)"
        )
    elif author == approver:
        lines.append(
            f"- ⚠ RL-6: the author and the approver are recorded as the SAME "
            f"Manager (`{author}`). That is the one thing RL-6 forbids; read the "
            f"approval record before trusting this work"
        )
    # ⚠ **COUNTED FROM THE STAGE LOG, not from `plan.returns`.** A re-authored
    # plan is `replace(candidate, …)` over fresh model output, so it carries
    # `returns=()`: the rejection history is written onto the REJECTED plan and
    # then overwritten by the plan that replaces it. Reading `returns` here said
    # "approved without being sent back" about a ticket whose own timeline, two
    # sections below, showed `rejected`. The stage log is append-only and is
    # what actually survives. (The dropped history is rite's bug, not this
    # function's — see SCRUM-101; it also defeats RL-10's bound.)
    rejections = _rejections(state, ticket)
    returns = tuple(getattr(plan, "returns", ()) or ())
    if rejections:
        lines += [
            "",
            f"Sent back to its author {rejections} time(s) before approval "
            f"(RL-10 bounds this at {rc.MAX_RETURNS}), per the stage log.",
            "",
        ]
        if returns:
            for n, reason in enumerate(returns, 1):
                text = str(reason).strip()
                if len(text) > MAX_REASON_CHARS:
                    text = text[:MAX_REASON_CHARS] + " […]"
                lines += [f"{n}. {text}", ""]
        else:
            lines += [
                "⚠ What the reviewer objected to is NOT in this plan: a "
                "re-authored plan does not carry the rejection it answers "
                "(SCRUM-101). The objection is in the rejected plan that "
                "preceded it, and in the Manager's log.",
                "",
            ]
    else:
        lines += ["", "Approved without being sent back.", ""]

    # --- each subtask, and what decided it (RL-7) ----------------------------
    lines += ["## The subtasks", ""]
    for subtask in getattr(plan, "subtasks", ()) or ():
        sid = str(getattr(subtask, "id", "?"))
        status = str(getattr(subtask, "status", "") or "no state")
        attempts = getattr(subtask, "attempts", 0)
        lines.append(f"### `{sid}` — {status}, {attempts} attempt(s)")
        lines.append("")
        lines.append(f"- intent: {getattr(subtask, 'intent', '') or '(none recorded)'}")
        scope = ", ".join(f"`{p}`" for p in (getattr(subtask, "scope", ()) or ()))
        lines.append(f"- may change: {scope or '(nothing recorded)'}")
        cites = ", ".join(f"`{c}`" for c in (getattr(subtask, "cites", ()) or ()))
        lines.append(f"- cites: {cites or '(none)'}")
        lines.append(f"- checked by: `{getattr(subtask, 'verify', '') or '(none)'}`")
        got = state.read_state(f"approaches/{ticket}/{sid}.json")
        if getattr(got, "value", None):
            lines.append(
                f"- its Level-2 approach is recorded at "
                f"`approaches/{ticket}/{sid}.json` in the plan state"
            )
        failure = str(getattr(subtask, "last_failure", "") or "").strip()
        if failure and status != dec.ACCEPTED:
            short = failure[:MAX_OUTPUT_CHARS]
            lines += ["", "What its check last said:", "", "```", short, "```"]
        lines.append("")

    # --- RL-8, on the composed work -----------------------------------------
    lines += ["## RL-8 — the composed work, not each piece of it", ""]
    verified = rc.read(state, ticket)
    if verified is None or isinstance(verified, str):
        lines.append(
            "⚠ No recomposition result could be read, so nothing here says the "
            "composed work was checked. That should not be possible for a "
            "delivered ticket — read the plan state before trusting it."
        )
    else:
        commands = tuple(getattr(verified, "commands", ()) or ())
        if getattr(verified, "none_agreed", False):
            lines.append(
                "The refinement agreed NO verify for this ticket, so there was "
                "nothing to run on the composed work. That is a declared state, "
                "not a pass."
            )
        elif getattr(verified, "ok", False):
            lines.append("The composed work PASSED the verify agreed at refinement:")
            lines += [""] + [f"- `{c}`" for c in commands]
            lines.append("")
            lines.append(
                f"Recorded at {_stamp(getattr(verified, 'at', 0))}, tied to the "
                f"approved plan's fingerprint "
                f"`{str(getattr(verified, 'plan', ''))[:16]}` — so a plan "
                f"re-approved later cannot ride on this pass."
            )
        else:
            lines.append("⚠ The composed work did NOT pass its agreed verify.")
            problem = str(getattr(verified, "problem", "") or "")
            failed = str(getattr(verified, "failed", "") or "")
            if problem:
                lines.append(f"- it could not run: {problem}")
            if failed:
                lines.append(f"- the command that failed: `{failed}`")
            output = str(getattr(verified, "output", "") or "").strip()
            if output:
                lines += ["", "```", output[:MAX_OUTPUT_CHARS], "```"]
    lines.append("")

    # --- the timeline, from the stage log's own timestamps -------------------
    got = st.read(state, ticket)
    record = getattr(got, "record", None)
    if record is not None:
        lines += ["## How it got here", "", "| stage | reached |", "|---|---|"]
        for row in getattr(record, "log", ()) or ():
            lines.append(
                f"| `{getattr(row, 'to', '?')}` | {_stamp(getattr(row, 'at', 0))} |"
            )
        lines.append("")

    # --- what actually landed ------------------------------------------------
    lines += ["## What landed", ""]
    lines += _landed(root, branch)
    lines.append("")
    lines.append(
        "Nothing here was pushed by this record's writing: what a delivery does "
        "with the branch is the publish strategy's business, and it is reported "
        "separately."
    )
    return "\n".join(lines).strip() + "\n"
