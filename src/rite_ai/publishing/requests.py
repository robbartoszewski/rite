"""A Manager asks rite to deliver a Worker's finished task (PB1 piece 3).

A sandboxed Manager cannot reach a Worker's sandbox copy, and D10 measured
that it cannot even commit, so it ASKS, the way it asks for a Worker
(`managers/broker`), and the supervisor delivers on the host with
`deliver.deliver`, in process.

**The request carries two values and no others:** which Worker and which
ticket. Everything else (the strategy, the squash, where the work goes) is
rite's, from config the Manager's request cannot set. Unknown keys are
refused, not ignored.

⚠ **Every outcome is TOLD to the Manager that asked**, through
`telling.tell_manager`, as well as said to the terminal and recorded. The
Manager's instructions promise that (`instructions` below). A refusal that
reaches only the terminal leaves a Manager waiting on work that went nowhere
(dogfood DF13's shape), so it is a defect, not a nuisance. One note per
module, each a line that carries its own fix; nothing on this path cuts a
note short, and `tests/test_a_delivery_outcome_reaches_the_manager.py`
proves the fix arrives whole.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rite_ai.names import name_problem

DIRNAME = "deliveries"
ALLOWED_KEYS = frozenset({"worker", "ticket"})
MAX_REQUEST_BYTES = 4096
TICKET_MAX = 64
ABOUT = "work you asked rite to deliver"


@dataclass(frozen=True)
class Request:
    worker: str
    ticket: str


def requests_dir(root: Path, manager: str) -> Path:
    """The Manager's own `deliveries/`, beside its Worker `requests/`, under
    the one directory of rite's its boundary lets it write."""
    from rite_ai.managers import manager_dir

    return manager_dir(root, manager) / DIRNAME


def decide(raw: str) -> Request | str:
    """The request, or why it is refused. Every branch refuses; only the end
    allows (the broker's rule)."""
    if len(raw.encode("utf-8", "replace")) > MAX_REQUEST_BYTES:
        return "the request is larger than a request needs to be"
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return "the request is not JSON"
    if not isinstance(parsed, dict):
        return "the request is not a JSON object"
    stray = sorted(set(parsed) - ALLOWED_KEYS)
    if stray:
        return (
            f"the request carries {', '.join(map(repr, stray))}; only worker "
            "and ticket are allowed"
        )
    worker, ticket = parsed.get("worker"), parsed.get("ticket")
    if not isinstance(worker, str) or not isinstance(ticket, str):
        return 'the request needs {"worker": "<name>", "ticket": "<id>"}'
    problem = name_problem(worker, kind="worker name")
    if problem:
        return problem
    if not ticket.strip() or len(ticket) > TICKET_MAX:
        return f"a ticket id is 1 to {TICKET_MAX} characters"
    return Request(worker=worker, ticket=ticket)


def take(root: Path, manager: str) -> list[str]:
    """Every pending request, oldest first, removed as it is read: one left
    behind would be delivered twice. Read through `own_dir`, which follows
    no link the Manager planted (SCRUM-59 review); a `deliveries` that is
    one raises OSError for the caller to say."""
    from rite_ai.managers import own_dir

    return [text for _, text in own_dir.take(root, manager, DIRNAME, MAX_REQUEST_BYTES)]


def _gate_words(why: str) -> bool:
    """Whether this refusal came from the publish gate or a check on it.

    ⚠ **Matched on rite's OWN refusal text, which is the one place a
    substring match is defensible here** — these strings are written three
    functions away in `deliver.py` and `gate/`, not by a model and not by a
    person. It decides only which of two journal classes an entry gets, so
    the cost of a miss is a `delivery-refused` where a `gate-refused` was
    meant, and never a failure going unrecorded.
    """
    lowered = why.lower()
    return any(
        mark in lowered
        for mark in ("publish gate", "gate's own", "gitleaks", "secret", "suppress")
    )


def honour_deliveries(root: Path, manager: str, say, record=None) -> None:
    """Deliver every request `manager` wrote, and tell it what happened.

    `record` is SCRUM-71's journal recorder. Every refusal here is a
    delivery the Manager asked for and did not get, which is exactly what
    the a9 journal was missing: the Manager hears it in its next
    instruction and the Owner, reading the journal afterwards, heard nothing
    at all. None means the flag is off and nothing is recorded.
    """
    from rite_ai.managers import recording
    from rite_ai.managers.telling import tell_manager
    from rite_ai.publishing.deliver import Refused, deliver

    def tell(text: str) -> None:
        try:
            tell_manager(root, manager, ABOUT, text)
        except OSError as e:
            say(f"could not tell {manager!r} what happened to a delivery: {e}")

    def note(kind: str, subject: str, observed: str, anchor: str) -> None:
        if record is None:
            return
        record(
            recording.Event(
                kind,
                subject,
                observed,
                "a finished ticket is delivered, or somebody is told why not",
                anchor=anchor,
            )
        )

    try:
        pending = take(root, manager)
    except OSError as e:
        said = (
            f"NOT delivered: rite did not read your delivery requests, because "
            f"{requests_dir(root, manager)} cannot be opened as rite's own "
            f"directory ({e})"
        )
        say(f"{manager!r}: {said}")
        tell(said)
        # 🔴 SCRUM-71. **The worst delivery failure in this function, and it
        # was the one not recorded.** Every refusal below is about one
        # request rite READ; this is rite not reading them at all — so a
        # Manager's deliveries stop wholesale, for as long as the directory
        # stays unopenable, and the Owner reading the journal afterwards saw
        # nothing. Recorded with the directory in the anchor, because the
        # fix is a filesystem one and the path is the actionable part.
        note(
            recording.DELIVERY_REFUSED,
            f"{manager}: the requests directory",
            f"rite could not read {manager}'s delivery requests at all: "
            f"{requests_dir(root, manager)} cannot be opened as rite's own "
            f"directory ({e}). Every delivery it asks for stops until that "
            "is fixed, not just one",
            f"manager {manager} requests directory",
        )
        return
    for raw in pending:
        request = decide(raw)
        if isinstance(request, str):
            said = (
                f"NOT delivered: {request}. Ask again with `rite request deliver "
                "<worker> --ticket <ID>`"
            )
            say(f"{manager!r}: {said}")
            tell(said)
            note(
                recording.DELIVERY_REFUSED,
                f"{manager}: a malformed request",
                f"a delivery request from {manager} could not be read: {request}",
                f"manager {manager} delivery request",
            )
            continue
        refused = _staged_pipeline_refusal(root, manager, request.ticket)
        if refused:
            say(f"{manager!r}: {refused}")
            tell(refused)
            continue
        result = deliver(root, request.worker, request.ticket, manager=manager)
        subject = f"{request.worker}/{request.ticket}"
        if isinstance(result, Refused):
            said = f"NOT delivered {request.worker}/{request.ticket}: {result.why}"
            say(f"{manager!r}: {said}")
            tell(said)
            note(
                recording.GATE_REFUSED
                if _gate_words(result.why)
                else recording.DELIVERY_REFUSED,
                subject,
                f"the whole delivery of {subject} was refused: {result.why}",
                f"worker {request.worker} ticket {request.ticket}",
            )
            continue
        for outcome in result.outcomes:
            say(f"{manager!r}: {outcome.note()}")
            tell(outcome.note())
            if not outcome.ok:
                # ⚠ Per MODULE, because a delivery of three modules can fail
                # one and deliver two, and the Owner needs to know which.
                note(
                    recording.GATE_REFUSED
                    if _gate_words(outcome.why)
                    else recording.DELIVERY_REFUSED,
                    f"{subject} module {outcome.module}",
                    f"{outcome.module} of {subject} was not delivered: {outcome.why}",
                    f"worker {request.worker} ticket {request.ticket} "
                    f"module {outcome.module}",
                )
        say(f"{manager!r}: {request.worker}: {result.sandbox}")
        tell(f"{request.worker}: {result.sandbox}")
        for line in _hand_back_the_pipelines_account(
            root, manager, request.worker, request.ticket
        ):
            say(f"{manager!r}: {line}")


def _hand_back_the_pipelines_account(
    root: Path, manager: str, worker: str, ticket: str
) -> list[str]:
    """Write the handback a pipeline-delivered ticket otherwise never gets
    (SCRUM-100). Returns lines to say; never raises.

    🔴 **A Worker-driven ticket hands back prose; a pipeline-driven one handed
    back nothing.** `handback` is "the record a Worker writes when it
    finishes", and on this path no Worker session finishes — rite drives each
    subtask itself and asks for the delivery after RL-8. Measured on the
    v0.7.0 gate run: the Claude Worker's ticket produced 11 KB of account and
    the pipeline's produced a branch and silence.

    ⚠ **HERE, after `deliver` returned.** `deliver` collects the ticket branch
    into the project before it returns, so this is the first moment the commits
    are readable from the project — and the Worker's sandbox, where they lived,
    is gone by now.

    ⚠ **It does not overwrite a Worker's own account.** A Worker that wrote one
    for this same ticket said something rite cannot reconstruct; composing over
    it would replace testimony with a transcript. Only a missing record, or one
    about a different ticket, is written.

    ⚠ The record RELAYS to the Manager like a Worker's, by Robert's decision
    (2026-10-09, option (a)): the Manager drove the work and is told what was
    handed back under it.
    """
    from rite_ai import handback
    from rite_ai.local import decomposition as dec
    from rite_ai.local import plan_state
    from rite_ai.local.handover import account_for, branch_for

    try:
        existing = handback.read(Path(root), worker)
    except Exception:  # noqa: BLE001 - said, never raised into the cycle
        existing = None
    if existing is not None and str(getattr(existing, "ticket", "")) == str(ticket):
        return []

    # ⚠ **`compose` IS INSIDE THE TRY, although it promises never to raise.**
    # A promise in a docstring is not a guarantee at a call site that runs on
    # the supervisor's cycle: `pending.sync` carried the same promise and took
    # `rite replies` down with a traceback (see its own fix). A delivery that
    # happened must never be reported as an error because the prose about it
    # could not be written.
    try:
        summary = account_for(Path(root), manager, ticket)
        if not summary:
            return []  # not a pipeline ticket, or its plan could not be read
        state = plan_state.layer(Path(root))
        plan = getattr(dec.read(state, ticket), "plan", None)
        branch = branch_for(plan) if plan is not None else ""
        path = handback.write(
            Path(root), worker, ticket=str(ticket), branch=branch, summary=summary
        )
    except Exception as e:  # noqa: BLE001 - a missing account must not fail a delivery
        return [
            f"the work for {ticket} was delivered, but rite could not write the "
            f"account of it ({type(e).__name__}: {e}). The pipeline's records are "
            f"intact; nothing about the delivery is in doubt"
        ]
    return [
        f"wrote the pipeline's own account of {ticket} to {path} — composed from "
        f"its records, not a Worker's testimony"
    ]


def _staged_pipeline_refusal(root: Path, manager: str, ticket: str) -> str:
    """Why this delivery request must not be honoured, or "" (SCRUM-72f).

    🔴 **§3.3a guard 5: a delivery before RECOMPOSED, "through both the loop
    AND a hand-written request file".** The loop's own guard is the stage
    machine, and it is not enough on its own: this directory is the Manager's
    own, so a Manager can write a request file into it directly — `rite
    request deliver` exists for exactly that — and rite would then push work
    that never passed RL-8. A guard only on the loop is a guard on the path
    nobody needs to go round.

    ⚠ **Only for a ticket the staged pipeline drives**, which is exactly a
    ticket that HAS a stage record. A Claude Worker's ticket has none and is
    unaffected: that path has no staged pipeline, and inventing a stage for it
    here would refuse every delivery rite has ever made.

    Fails CLOSED on anything it cannot read. A stage record that will not
    parse is not a reason to push.
    """
    from rite_ai.local import plan_state
    from rite_ai.local import stage as st

    try:
        state = plan_state.layer(root)
        got = st.read(state, ticket)
    except Exception as e:  # noqa: BLE001 - said, never raised into the cycle
        return (
            f"NOT delivered {ticket}: rite could not read its pipeline stage "
            f"({type(e).__name__}: {e}), and it does not deliver work it cannot "
            "tell has passed its gates"
        )
    if got.unavailable or got.error:
        return (
            f"NOT delivered {ticket}: its pipeline stage could not be read "
            f"({got.unavailable or got.error}). rite does not deliver work it "
            "cannot tell has passed its gates"
        )
    if got.record is None:
        return ""  # not a staged-pipeline ticket
    if got.stage == st.DELIVERY_REQUESTED:
        return ""
    return (
        f"NOT delivered {ticket}: it is at stage {got.stage!r} and a delivery "
        f"is only honoured at {st.DELIVERY_REQUESTED!r}. The staged pipeline "
        "drives this ticket, and the stage before delivery is the "
        "recomposition verify (RL-8) — every subtask passing its own check is "
        "not the ticket working. A request file written by hand does not "
        "replace the gate"
    )


def instructions(root: Path, manager: str) -> str:
    """What the Manager is told about delivering, with the promise that it
    hears every outcome."""
    from rite_ai import own_command

    return (
        "\n\n## Delivering a Worker's finished ticket — you ASK, rite does it\n\n"
        "When a Worker reports its ticket finished and committed, ask rite to "
        "deliver it:\n\n"
        f"    {own_command()} request deliver <name> --ticket <ID>\n\n"
        "rite brings the Worker's commits out of its sandbox into the "
        "project under the project's publish strategy, which you do not "
        "choose, and removes the sandbox once everything is delivered, so the "
        "Worker can start its next ticket. **rite tells you every outcome in "
        "your next instruction**, one line per module, each with what to do "
        "if it was not delivered. Do not deliver by hand, and never merge a "
        "pull request: that is the User's, or rite's own gated step.\n"
    )
