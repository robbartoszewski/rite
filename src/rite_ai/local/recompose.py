"""RL-8 — the recomposition verify, before anything is delivered (SCRUM-72f).

§3.3a's table: **"RL-8 recomposition verify: Missing. Delivery is requested
once every subtask is accepted."**

🔴 **What that means.** RL-7 runs each subtask's own verify, so every subtask
was checked in isolation — and nothing ever checked the composed work. A plan
sliced wrongly produces subtasks that each pass and a ticket that does not
work, which is the one failure decomposition itself introduces and the one
gate that would catch it. `_ask_delivery` fired on "every subtask accepted"
and rite pushed.

**So before delivery is requested, the ticket's own agreed verify runs on the
composed work**, and the result is persisted. The commands are the refinement
record's `verify` — the one the Owner agreed at refinement and the one
`deliver` already holds host-measured items against — not a command the
decomposer or the executor chose. A gate whose check the gated tier writes is
not a gate.

⚠ **Where it runs.** `worker_step.placement_for`'s `workspace`: the module's
clone inside the sandbox's copy for a placed ticket, the project root for a
Manager-tier one. That is where every subtask's commits are and where the
ticket branch is already checked out, so the verify sees the composed work
rather than a tree nothing touched — the same reason RL-7's verify runs there.

⚠ **"Nothing was agreed" is a DECLARED state, not a pass.** A refinement that
agreed no verify (`record.NONE_AGREED`) leaves nothing to run, and that is
recorded as what it is and said, rather than recorded as green. SCRUM-68
makes the same distinction for the tag-relative guards: a check that did not
run and a check that passed must not read alike.

⚠ **The result is tied to the plan it verified.** A pass is recorded with a
fingerprint of the approved subtasks, so a plan re-approved afterwards cannot
ride on an older pass — the same rule `level2`'s approval digests follow, for
the same reason.

**A failure returns the plan to plan review** (§3.3a), which spends one of
RL-10's returns — and `returns` is bounded (`MAX_RETURNS`), because an
unbounded return is a loop and not a fix. Past the bound it escalates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Present,
    StateLayer,
    Unavailable,
)

FORMAT_VERSION = 1

MAX_RETURNS = 3
"""How many times one ticket's plan may go back to review before it
escalates.

🔴 **One bound for BOTH return paths**, and it is `Decomposition.returns`
that counts them — the field RL-10 already keeps "because RL-10 counts returns
per parent ticket, and a count with no reasons is a number nobody can act
on". A rejection (§3.3b) and a failed RL-8 are the same event from the plan's
point of view: it goes back to its planner with reasons. Two budgets would be
two numbers to keep in step, and the first thing to drift."""


def key_for(ticket: str) -> str:
    return f"recompositions/{ticket}.json"


def fingerprint(digests: dict[str, str]) -> str:
    """A content address for the SET of approved subtasks.

    ⚠ Not the plan's state-layer version: that changes every time a subtask's
    status moves, which is every step, so a pass recorded against it would be
    void by the time it was read. The approved digests do not move during
    stepping and do move when a plan is re-approved, which is exactly when a
    pass must stop counting.
    """
    return hashlib.sha256(
        json.dumps(digests, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Result:
    ticket: str
    ok: bool = False
    none_agreed: bool = False
    commands: tuple[str, ...] = ()
    failed: str = ""
    """The command that failed, when one did."""
    output: str = ""
    problem: str = ""
    """Why the verify could not RUN — distinct from failing (RL-47). An
    endpoint or a workspace that was not there did not fail the work."""
    plan: str = ""
    """`fingerprint` of the subtasks this result verified."""
    at: float = 0.0

    @property
    def cleared(self) -> bool:
        """Whether this result lets the work be delivered. ⚠ `none_agreed` is
        cleared and is NOT `ok`: nothing ran, and the two must not read
        alike."""
        return (self.ok or self.none_agreed) and not self.problem

    def line(self) -> str:
        if self.problem:
            return (
                f"{self.ticket}: its recomposition verify could not run — "
                f"{self.problem}"
            )
        if self.none_agreed:
            return (
                f"{self.ticket}: no verify was agreed at refinement, so there is "
                "NOTHING to check on the composed work. Delivered on that basis, "
                "which is a declared state and not a pass"
            )
        if self.ok:
            return (
                f"{self.ticket}: the composed work passes its agreed verify "
                f"({', '.join(self.commands)})"
            )
        return (
            f"{self.ticket}: the composed work FAILS its agreed verify — "
            f"`{self.failed}` did not pass. Each subtask passed its own check "
            "and the ticket does not work, which is a plan sliced wrongly "
            "(RL-8)"
        )


MAX_OUTPUT_CHARS = 4000


def render(result: Result) -> bytes:
    body = {
        "format_version": FORMAT_VERSION,
        "ticket": result.ticket,
        "ok": result.ok,
        "none_agreed": result.none_agreed,
        "commands": list(result.commands),
        "failed": result.failed,
        "output": result.output[:MAX_OUTPUT_CHARS],
        "problem": result.problem,
        "plan": result.plan,
        "at": result.at,
    }
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse(raw: bytes) -> Result | str:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return f"not readable as a recomposition result ({e})"
    if not isinstance(body, dict) or body.get("format_version") != FORMAT_VERSION:
        return "written in a format this rite does not read"
    for field, kind in (("ok", bool), ("none_agreed", bool)):
        if not isinstance(body.get(field), kind):
            return f"its {field!r} is not a boolean"
    at = body.get("at", 0.0)
    return Result(
        ticket=str(body.get("ticket", "")),
        ok=bool(body["ok"]),
        none_agreed=bool(body["none_agreed"]),
        commands=tuple(str(c) for c in body.get("commands", ())),
        failed=str(body.get("failed", "")),
        output=str(body.get("output", "")),
        problem=str(body.get("problem", "")),
        plan=str(body.get("plan", "")),
        at=float(at)
        if isinstance(at, int | float) and not isinstance(at, bool)
        else 0.0,
    )


def write(state: StateLayer, result: Result):
    got = state.read_state(key_for(result.ticket))
    return state.write_state(
        key_for(result.ticket),
        render(result),
        got.version if not isinstance(got, Unavailable) else ABSENT,
    )


def read(state: StateLayer, ticket: str) -> Result | str | None:
    """The recorded result, None when there is none, or why it cannot be
    read. Three answers, as `level2.read_approach`: "cannot read" must not
    deliver, and must not be mistaken for "not run yet" either."""
    got = state.read_state(key_for(ticket))
    if isinstance(got, Unavailable):
        return f"its recomposition result could not be read: {got.reason}"
    if isinstance(got, Absent):
        return None
    assert isinstance(got, Present)
    parsed = parse(got.value)
    return parsed if isinstance(parsed, str) else parsed


# --- the commands, from the record the Owner agreed ------------------------------

NONE_AGREED = "none agreed"
"""`refinement.record.NONE_AGREED`, named here so a reader of this file sees
what the sentinel is. Imported rather than redefined below."""


def verify_commands(snapshot) -> tuple[str, ...] | str:
    """The agreed verify commands, `NONE_AGREED`, or why there are none.

    ⚠ **The REFINEMENT record's, never the plan's.** A subtask's verify is
    written by the decomposer, which is the tier RL-8 is checking; the ticket's
    verify was agreed with the Owner and signed. A gate whose check the gated
    tier writes is not a gate.
    """
    from rite_ai.refinement.record import NONE_AGREED as AGREED_NONE

    if isinstance(snapshot, str) or not snapshot:
        return (
            "rite cannot read the refinement record this ticket was started "
            f"on, so it does not know what to check ({snapshot or 'there is none'})"
        )
    verify = snapshot.get("verify")
    if verify == AGREED_NONE:
        return AGREED_NONE
    if isinstance(verify, str):
        return (verify,) if verify.strip() else "its agreed verify is empty"
    if isinstance(verify, list) and verify and all(isinstance(c, str) for c in verify):
        commands = tuple(c for c in verify if c.strip())
        return commands or "its agreed verify is empty"
    return 'its agreed verify is neither commands nor "none agreed"'


# --- running it -------------------------------------------------------------------


def run_recomposition(
    root,
    manager: str,
    ticket: str,
    digests: dict[str, str],
    *,
    snapshot=None,
    workspace: str = "",
    verifier=None,
    now: float | None = None,
) -> Result:
    """Run the ticket's agreed verify on the composed work, and say what
    happened. Never raises: a verify that could not run is a `problem`, which
    is not a failure (RL-47)."""
    import time
    from pathlib import Path

    at = time.time() if now is None else now
    plan = fingerprint(digests)
    if snapshot is None:
        from rite_ai.local.gates import definition_snapshot

        snapshot = definition_snapshot(Path(root), manager, ticket)
    commands = verify_commands(snapshot)
    if isinstance(commands, str):
        from rite_ai.refinement.record import NONE_AGREED as AGREED_NONE

        if commands == AGREED_NONE:
            return Result(ticket=ticket, none_agreed=True, plan=plan, at=at)
        return Result(ticket=ticket, problem=commands, plan=plan, at=at)

    if not workspace:
        from rite_ai.local.worker_step import placement_for

        placed = placement_for(Path(root), manager, ticket)
        if getattr(placed, "problem", ""):
            return Result(
                ticket=ticket,
                problem=f"rite cannot reach the composed work: {placed.problem}",
                commands=commands,
                plan=plan,
                at=at,
            )
        # ⚠ The sandbox copy's clone for a placed ticket, the project root for
        # a Manager-tier one — where the commits are and where the ticket
        # branch is already checked out.
        workspace = placed.workspace or str(root)

    if verifier is None:
        from rite_ai.local.runners import SubprocessVerifier

        verifier = SubprocessVerifier()

    for command in commands:
        try:
            verdict = verifier.run(command, workspace)
        except Exception as e:  # noqa: BLE001 - a launch failure is not a failure
            return Result(
                ticket=ticket,
                problem=f"`{command}` could not be run ({e})",
                commands=commands,
                plan=plan,
                at=at,
            )
        if not getattr(verdict, "ran", True):
            # ⚠ RL-47, and this is where the module's own promise was broken:
            # "a verify that could not run is a `problem`, which is not a
            # failure" was true only for an exception the launcher raised —
            # and the launcher catches the common one. A command that does not
            # execute is not the composed work failing, and returning the plan
            # for it spends an RL-10 return no plan change can satisfy.
            return Result(
                ticket=ticket,
                problem=(
                    f"`{command}` never ran, so the composed work is unverified "
                    f"rather than wrong: {getattr(verdict, 'output', '')}"
                ),
                commands=commands,
                plan=plan,
                at=at,
            )
        if not getattr(verdict, "passed", False):
            return Result(
                ticket=ticket,
                ok=False,
                commands=commands,
                failed=command,
                output=str(getattr(verdict, "output", ""))[:MAX_OUTPUT_CHARS],
                plan=plan,
                at=at,
            )
    return Result(ticket=ticket, ok=True, commands=commands, plan=plan, at=at)


# --- the guard before a delivery is requested ------------------------------------


@dataclass(frozen=True)
class Cleared:
    why: str = ""


@dataclass(frozen=True)
class Blocked:
    why: str


def cleared_to_deliver(state: StateLayer, ticket: str, digests: dict[str, str]):
    """Whether `ticket`'s composed work has passed RL-8, for the plan that is
    approved now."""
    got = read(state, ticket)
    if isinstance(got, str):
        return Blocked(got)
    if got is None:
        return Blocked(
            f"{ticket}'s composed work has not been verified: RL-8 runs the "
            "ticket's own agreed verify on the ticket branch before anything is "
            "delivered, and no result is recorded. Each subtask passing its own "
            "check is not the ticket working"
        )
    want = fingerprint(digests)
    if got.plan != want:
        return Blocked(
            f"{ticket}'s recorded recomposition verify was run against a "
            "different plan than the one approved now, so it says nothing about "
            "this one. It runs again"
        )
    if got.problem:
        return Blocked(f"{ticket}: {got.problem}")
    if not got.cleared:
        return Blocked(got.line())
    return Cleared(got.line())
