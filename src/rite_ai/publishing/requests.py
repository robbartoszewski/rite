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
    behind would be delivered twice."""
    where = requests_dir(root, manager)
    if not where.is_dir():
        return []
    found: list[str] = []
    for path in sorted(where.glob("*.json")):
        try:
            found.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        try:
            path.unlink()
        except OSError:
            pass
    return found


def honour(root: Path, manager: str, say) -> None:
    """Deliver every request `manager` wrote, and tell it what happened."""
    from rite_ai.managers.telling import tell_manager
    from rite_ai.publishing.deliver import Refused, deliver

    def tell(text: str) -> None:
        try:
            tell_manager(root, manager, ABOUT, text)
        except OSError as e:
            say(f"could not tell {manager!r} what happened to a delivery: {e}")

    for raw in take(root, manager):
        request = decide(raw)
        if isinstance(request, str):
            said = f"NOT delivered: {request}. Write the request again as shown"
            say(f"{manager!r}: {said}")
            tell(said)
            continue
        result = deliver(root, request.worker, request.ticket)
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


def instructions(root: Path, manager: str) -> str:
    """What the Manager is told about delivering, with the promise that it
    hears every outcome."""
    where = requests_dir(root, manager)
    return (
        "\n\n## Delivering a Worker's finished ticket — you ASK, rite does it\n\n"
        "When a Worker reports its ticket finished and committed, ask rite to "
        "deliver it:\n\n"
        f"    mkdir -p {where}\n"
        '    echo \'{"worker":"<name>","ticket":"<ID>"}\' > '
        f"{where}/$(date +%s).json\n\n"
        "rite brings the Worker's commits out of its sandbox into the "
        "project under the project's publish strategy, which you do not "
        "choose, and removes the sandbox once everything is delivered, so the "
        "Worker can start its next ticket. **rite tells you every outcome in "
        "your next instruction**, one line per module, each with what to do "
        "if it was not delivered. Do not deliver by hand, and never merge a "
        "pull request: that is the User's, or rite's own gated step.\n"
    )
