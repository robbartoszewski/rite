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


def honour_deliveries(root: Path, manager: str, say) -> None:
    """Deliver every request `manager` wrote, and tell it what happened."""
    from rite_ai.managers.telling import tell_manager
    from rite_ai.publishing.deliver import Refused, deliver

    def tell(text: str) -> None:
        try:
            tell_manager(root, manager, ABOUT, text)
        except OSError as e:
            say(f"could not tell {manager!r} what happened to a delivery: {e}")

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
            continue
        refused = _staged_pipeline_refusal(root, manager, request.ticket)
        if refused:
            say(f"{manager!r}: {refused}")
            tell(refused)
            continue
        result = deliver(root, request.worker, request.ticket, manager=manager)
        if isinstance(result, Refused):
            said = f"NOT delivered {request.worker}/{request.ticket}: {result.why}"
            say(f"{manager!r}: {said}")
            tell(said)
            continue
        for outcome in result.outcomes:
            say(f"{manager!r}: {outcome.note()}")
            tell(outcome.note())
        say(f"{manager!r}: {request.worker}: {result.sandbox}")
        tell(f"{request.worker}: {result.sandbox}")


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
