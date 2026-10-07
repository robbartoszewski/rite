"""The staged pipeline, enforced by rite's own code (SCRUM-72, §3.3a).

**What this is for.** The definition of done Robert set for SCRUM-72: rite's
**deterministic harness code** — not the model, not the Manager's good
intentions, not a line in a `CLAUDE.md` — drives a ticket through every stage,
in order. A spec/definition session, then the refinement record, then the plan,
then its review, then the approach, then the work, then the recomposition
verify. The model fills each stage and **cannot skip or reorder one**.

That property needs three things, and a convention gives none of them:

1. the stage is **persisted**, so a restart resumes where the work is rather
   than where a session remembers being (SPEC §2.0: a session's memory is not
   a record);
2. every move goes through **one guard** — `advance` below — so there is a
   single place to read and a single place a skip would have to get past;
3. each stage's **own artifact must already exist, in the state that stage
   claims**, before the stage may be entered. The stage never leads the
   artifact. `APPROVED` is unreachable without a plan whose `approval` is
   APPROVED, and only `approve.approve_plan` writes that.

🔴 **Why a transition table and not a chain of `if`s.** `loop.advance_ticket`
DERIVED the stage every pass from the plan's current shape: no plan means
decompose, PENDING means approve, a runnable subtask means step. That reads as
an order but is not one — it is a set of independent conditions, so anything
that produced the shape of a later stage entered it, whatever had happened
before. There was no record that a stage had been passed, so nothing could
notice one that had not been. A table says what may follow what, once.

**The log is append-only.** Every accepted move appends one entry and nothing
here ever rewrites or shortens the list, because "the record shows every stage
in order" is one of the things SCRUM-72 has to be able to show after the fact.
A stored record whose log does not end at its own stage is refused rather than
repaired: it has been edited by something that is not this module.

⚠ **This is the ticket's stage, not a subtask's.** Running subtasks all happen
inside `STEPPING`; which subtask is where is the plan artifact's business
(`decomposition.Subtask.status`). Entering `STEPPING` twice is not a
transition, which is why the table has no self-loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Conflict,
    Present,
    StateLayer,
    Unavailable,
    Written,
)

UNSTARTED = ""
"""No record yet. Not a stored value: it is what `read` reports for a ticket
the pipeline has never touched, and the only stage `DEFINED` may follow."""

DEFINED = "defined"
"""The definition of done is pinned to this ticket: the signed refinement
record the Worker was started on, snapshotted. Before this the decomposer was
called with no ticket text at all and saw "(no ticket text was supplied)"."""

DECOMPOSED = "decomposed"
"""A plan exists and is PENDING. Authored by a Manager holding `decompose`."""

APPROVED = "approved"
"""An independent Manager approved the plan (RL-6, DD-3.5, RL-67)."""

REJECTED = "rejected"
"""An independent Manager rejected it, with reasons. It goes back to its
planner, which re-authors — the one way out, and it is bounded."""

STEPPING = "stepping"
"""Subtasks are being run. Which one is where lives in the plan."""

RECOMPOSED = "recomposed"
"""Every subtask is accepted and the recomposition gate has passed (RL-8)."""

DELIVERY_REQUESTED = "delivery requested"
"""rite has been asked to deliver, through its own gated request path."""

STAGES = (
    DEFINED,
    DECOMPOSED,
    APPROVED,
    REJECTED,
    STEPPING,
    RECOMPOSED,
    DELIVERY_REQUESTED,
)
"""A CLOSED set. A gate cannot key on a stage something invented, which is the
same rule `decomposition.STATUSES` and `APPROVALS` are closed for."""

TRANSITIONS: dict[str, tuple[str, ...]] = {
    UNSTARTED: (DEFINED,),
    DEFINED: (DECOMPOSED,),
    DECOMPOSED: (APPROVED, REJECTED),
    REJECTED: (DECOMPOSED,),
    # Back to DECOMPOSED from anywhere the plan can be returned to review:
    # a rejection after the fact, a composition conflict, a failed RL-8
    # (`decomposition.returned_to_plan_review` drops the approval, so the
    # artifact agrees with the stage).
    APPROVED: (STEPPING, DECOMPOSED),
    STEPPING: (RECOMPOSED, DECOMPOSED),
    RECOMPOSED: (DELIVERY_REQUESTED, DECOMPOSED),
    DELIVERY_REQUESTED: (),
}
"""What may follow what. ⚠ The absence of an edge is the whole mechanism: a
step before approval is not refused by a check somewhere, it is refused
because `DEFINED -> STEPPING` is not in this table."""

FORMAT_VERSION = 1


def key_for(ticket: str) -> str:
    return f"stages/{ticket}.json"


@dataclass(frozen=True)
class Transition:
    """One move, as it is kept. `at` is a wall clock for a human reading the
    log; nothing decides anything from it."""

    frm: str
    to: str
    at: float = 0.0
    why: str = ""


@dataclass(frozen=True)
class Record:
    ticket: str
    stage: str = UNSTARTED
    log: tuple[Transition, ...] = ()
    format_version: int = FORMAT_VERSION


def render(record: Record) -> bytes:
    """Deterministic bytes, `decomposition.render`'s rule: two runs that
    decided the same thing produce the same value."""
    body = {
        "format_version": record.format_version,
        "ticket": record.ticket,
        "stage": record.stage,
        "log": [
            {"from": t.frm, "to": t.to, "at": t.at, "why": t.why} for t in record.log
        ],
    }
    return (json.dumps(body, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse(raw: bytes) -> Record | str:
    """A stage record, or why these bytes are not one.

    Refused rather than repaired, for `decomposition.parse`'s reason: the guard
    reads this, and a half-understood record is a guard checking something
    else. The two refusals that are not about syntax are the ones that matter —
    a stage outside the closed set, and a log that does not end at the stage it
    is stored beside.
    """
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return f"not readable as a stage record ({e})"
    if not isinstance(body, dict):
        return "not readable as a stage record (not an object)"
    if body.get("format_version") != FORMAT_VERSION:
        return (
            f"written in format {body.get('format_version')!r}, and this rite "
            f"reads {FORMAT_VERSION} — upgrade rite rather than reading it as "
            "this one"
        )
    stage = body.get("stage", UNSTARTED)
    if stage not in STAGES:
        return (
            f"stage {stage!r} is not one of {', '.join(STAGES)} — a stage "
            "outside the closed set is not a stage rite can gate on"
        )
    raw_log = body.get("log")
    if not isinstance(raw_log, list) or not raw_log:
        return (
            "a stage record carries no transition log, so nothing says how it got here"
        )
    log: list[Transition] = []
    for item in raw_log:
        if not isinstance(item, dict):
            return "a transition is not an object"
        frm, to = item.get("from", UNSTARTED), item.get("to")
        if to not in STAGES or (frm not in STAGES and frm != UNSTARTED):
            return f"a transition names a stage rite does not know: {frm!r} -> {to!r}"
        at = item.get("at", 0.0)
        if not isinstance(at, int | float) or isinstance(at, bool):
            return "a transition has a non-numeric timestamp"
        log.append(
            Transition(
                frm=str(frm), to=str(to), at=float(at), why=str(item.get("why", ""))
            )
        )
    # ⚠ The one consistency rule, and it is what makes the log evidence rather
    # than decoration: the stage IS the end of its own history. A record whose
    # log stops short of its stage has had one or the other edited by something
    # that is not `advance`, and then neither can be trusted to say what has
    # been passed.
    if log[-1].to != stage:
        return (
            f"its log ends at {log[-1].to!r} but it is stored at stage {stage!r}: "
            "the stage and its history disagree, so neither says what this "
            "ticket has passed"
        )
    return Record(ticket=str(body.get("ticket", "")), stage=stage, log=tuple(log))


@dataclass
class Read:
    record: Record | None = None
    version: str = ABSENT
    error: str = ""
    unavailable: str = ""

    @property
    def stage(self) -> str:
        """The stage, or UNSTARTED. ⚠ Only meaningful once `error` and
        `unavailable` are known empty: an unreadable record is NOT UNSTARTED,
        and a caller that treats it as one restarts a pipeline mid-flight."""
        return self.record.stage if self.record is not None else UNSTARTED


def read(state: StateLayer, ticket: str) -> Read:
    """The stored stage, distinguishing the three things a caller must not
    confuse: there is none, there is one, and nobody could tell."""
    result = state.read_state(key_for(ticket))
    if isinstance(result, Unavailable):
        return Read(unavailable=result.reason)
    if isinstance(result, Absent):
        return Read(version=result.version)
    assert isinstance(result, Present)
    parsed = parse(result.value)
    if isinstance(parsed, str):
        return Read(version=result.version, error=parsed)
    return Read(record=parsed, version=result.version)


@dataclass(frozen=True)
class Advanced:
    ticket: str
    frm: str
    to: str

    def note(self) -> str:
        return f"{self.ticket}: {self.frm or 'unstarted'} -> {self.to}"


@dataclass(frozen=True)
class Refused:
    why: str


def may_follow(frm: str, to: str) -> str:
    """Why `to` may not follow `frm`, or "". The table, asked as a question —
    so a caller that wants to know before trying does not re-read the table."""
    if to not in STAGES:
        return f"{to!r} is not a stage ({', '.join(STAGES)})"
    allowed = TRANSITIONS.get(frm)
    if allowed is None:
        return f"{frm!r} is not a stage rite knows, so nothing may follow it"
    if to not in allowed:
        if not allowed:
            return f"{frm!r} is the end of the pipeline; nothing follows it"
        return (
            f"a ticket at {frm or 'unstarted'!r} cannot go to {to!r}: only "
            f"{', '.join(repr(a) for a in allowed)} may follow it. The stage "
            "before it has not been passed"
        )
    return ""


def advance(
    state: StateLayer,
    ticket: str,
    to: str,
    *,
    why: str = "",
    gate=None,
    now: float | None = None,
) -> Advanced | Refused:
    """Move `ticket` to `to`, or say why it may not — **the one guard**.

    Three refusals, in this order, and every one of them leaves the stored
    record BYTE-UNCHANGED:

    1. the record cannot be read (unavailable, or edited into something this
       module will not parse). Never treated as unstarted;
    2. `to` may not follow the stored stage (`may_follow`);
    3. `to`'s own gate is shut — its artifact does not exist, or does not say
       what this stage would claim. `gate` is a callable `(stage) -> str`
       returning "" when open, injected by the caller that knows where the
       artifacts live (`gates.gate_for` in production).

    Then a compare-and-swap, so two Managers moving one ticket cannot both
    think they did.
    """
    import time

    now = time.time() if now is None else now
    got = read(state, ticket)
    if got.unavailable:
        return Refused(f"{ticket}'s stage could not be read: {got.unavailable}")
    if got.error:
        return Refused(f"{ticket}'s stage record will not parse: {got.error}")
    frm = got.stage
    problem = may_follow(frm, to)
    if problem:
        return Refused(f"{ticket} stays at {frm or 'unstarted'}: {problem}")
    if gate is not None:
        shut = gate(to)
        if shut:
            return Refused(
                f"{ticket} stays at {frm or 'unstarted'}: it cannot enter {to} — {shut}"
            )
    existing = got.record.log if got.record is not None else ()
    record = Record(
        ticket=ticket,
        stage=to,
        log=(*existing, Transition(frm=frm, to=to, at=now, why=why)),
    )
    written = state.write_state(key_for(ticket), render(record), got.version)
    if isinstance(written, Written):
        return Advanced(ticket=ticket, frm=frm, to=to)
    if isinstance(written, Unavailable):
        return Refused(
            f"{ticket} stays at {frm or 'unstarted'}: its stage could not be "
            f"written ({written.reason})"
        )
    assert isinstance(written, Conflict)
    return Refused(
        f"{ticket} stays at {frm or 'unstarted'}: its stage changed while this "
        "ran, so the move would have been decided from a stage nobody read"
    )


def adopt(
    state: StateLayer, ticket: str, stage: str, *, gate=None, now: float | None = None
) -> Advanced | Refused:
    """Write a first stage record for a ticket whose artifacts already exist.

    ⚠ **Only when there is NO record**, and only to a stage whose own gate is
    open. A pipeline in flight when this landed has artifacts and no stage, and
    the alternative was stranding it — so the stage is read back OFF the
    artifacts once, with one `adopted` entry saying so.

    It is not a door past the guard, and the test that says so is the one that
    matters: adopting `APPROVED` needs a plan whose `approval` is APPROVED, and
    `approve.approve_plan` is the only thing that writes that. Every stage's
    gate applies here exactly as in `advance`; what is skipped is the TABLE,
    which is about order, and order is what the artifacts already evidence.
    """
    import time

    now = time.time() if now is None else now
    got = read(state, ticket)
    if got.unavailable:
        return Refused(f"{ticket}'s stage could not be read: {got.unavailable}")
    if got.error:
        return Refused(f"{ticket}'s stage record will not parse: {got.error}")
    if got.record is not None:
        return Refused(
            f"{ticket} already has a stage record (at {got.record.stage}), so it "
            "is not a candidate for adoption — move it with `advance`"
        )
    if stage not in STAGES:
        return Refused(f"{stage!r} is not a stage ({', '.join(STAGES)})")
    if gate is not None:
        shut = gate(stage)
        if shut:
            return Refused(
                f"{ticket} cannot be adopted at {stage}: its artifacts do not "
                f"say it reached there — {shut}"
            )
    record = Record(
        ticket=ticket,
        stage=stage,
        log=(
            Transition(
                frm=UNSTARTED,
                to=stage,
                at=now,
                why="adopted from the artifacts already on disk",
            ),
        ),
    )
    written = state.write_state(key_for(ticket), render(record), got.version)
    if isinstance(written, Written):
        return Advanced(ticket=ticket, frm=UNSTARTED, to=stage)
    if isinstance(written, Unavailable):
        return Refused(f"{ticket}'s stage could not be written ({written.reason})")
    return Refused(
        f"{ticket} grew a stage record while this ran, so it was not adopted"
    )
