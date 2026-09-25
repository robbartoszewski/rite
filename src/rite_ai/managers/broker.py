"""Starting a Worker on a sandboxed Manager's behalf (B9, piece 2).

⚠ **A sandboxed Manager cannot start a sandboxed Worker.** Inside a seatbelt
sandbox only a semantically equivalent profile may be re-applied, and two
yoloAI sandbox profiles always differ because each scopes its paths to its
own id. Measured in `docs/design/spikes/B9-manager-sandboxing.md`. So a
Manager asks, and the supervisor — which runs on the host, outside the
boundary — does it.

## The sentence this module is written against

**The sandboxed process must not be able to obtain, BY ASKING, the
capability the sandbox removed.**

A broker that relays a request unchanged is privilege escalation with extra
steps: it hands back the exact thing the boundary took away. So the request
carries **two values and no others** — which declared Worker, and which
ticket — and every other input to the launch is chosen here, from project
state the Manager cannot write:

| what | where it comes from |
|---|---|
| which Worker | the request, **checked against `workers/<name>/worker.yml`** |
| which ticket | the request, **checked for shape and against the board** |
| the workspace directory | derived from the Worker name, never sent |
| the engine, its flags, its environment | `rite sandbox start`, never sent |
| the prompt | composed from the ticket, never sent |
| capacity | the existing bound, applied here rather than bypassed |

⚠ **The launch goes through `rite sandbox start`, deliberately, rather than
calling `sandbox.start_worker` directly.** That CLI path already validates
the workspace, resolves the Worker's token, warns about a stale `CLAUDE.md`
and applies the sandbox count bound. Reimplementing those here would mean
maintaining a second copy of a security-relevant checklist, and the second
copy is the one that falls behind.

⚠ **Refusals are recorded and reported, never silent.** A Manager that asks
for something it may not have is either confused or compromised, and both
are things an operator needs to see. Absence of a Worker is otherwise
indistinguishable from a Worker that failed to start.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rite_ai.names import name_problem

REQUESTS_DIRNAME = "requests"
ALLOWED_KEYS = frozenset({"worker", "ticket"})
MAX_REQUEST_BYTES = 4096
_TICKET_MAX = 64


@dataclass(frozen=True)
class Request:
    """The only two things a Manager may choose."""

    worker: str
    ticket: str


@dataclass(frozen=True)
class Decision:
    """Whether to start it, and why not when not."""

    ok: bool
    reason: str = ""
    request: Request | None = None


def requests_dir(root: Path, manager: str) -> Path:
    """Where a Manager writes its requests.

    A directory of its own rather than the mailbox's outbox: the outbox is
    what a human reads through `rite connect`, and mixing machine requests
    into it would put a Worker launch one careless relay away from anything
    that can write a reply.
    """
    from rite_ai.managers import manager_dir

    return manager_dir(root, manager) / REQUESTS_DIRNAME


def decide(
    raw: str,
    known_worker,
    ticket_exists,
    running: int = 0,
    capacity: int = 0,
) -> Decision:
    """Whether this request may be honoured.

    ⚠ **Every branch here refuses. That is the shape to keep** — a validator
    written as "accept unless something looks wrong" fails open on the input
    nobody thought of, and this one is fed by a process that may be
    compromised.

    `known_worker` and `ticket_exists` are predicates rather than lookups so
    the policy can be tested without a board or a project on disk. The
    wiring supplies the real ones.
    """
    if len(raw.encode("utf-8", "replace")) > MAX_REQUEST_BYTES:
        return Decision(False, "the request is larger than a request needs to be")
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return Decision(False, "the request is not JSON")
    if not isinstance(parsed, dict):
        return Decision(False, "the request is not a JSON object")

    # ⚠ **Unknown keys are REFUSED, not ignored** — the rule `config.yaml`
    # uses. An ignored key is a field somebody believes is having an effect,
    # and here it would be a field somebody is trying to have an effect
    # with.
    stray = sorted(set(parsed) - ALLOWED_KEYS)
    if stray:
        return Decision(
            False,
            f"the request carries {stray[0]!r}, which a request may not set. "
            f"Only {sorted(ALLOWED_KEYS)} are a Manager's to choose; "
            "everything else about a Worker's launch is rite's.",
        )
    missing = sorted(ALLOWED_KEYS - set(parsed))
    if missing:
        return Decision(False, f"the request has no {missing[0]!r}")

    worker = parsed["worker"]
    ticket = parsed["ticket"]
    if not isinstance(worker, str) or not isinstance(ticket, str):
        return Decision(False, "'worker' and 'ticket' must both be strings")

    # ⚠ The name becomes a PATH SEGMENT and then an argv entry. Checked with
    # the same rule every other name in rite is checked with, before it is
    # used for anything.
    problem = name_problem(worker, kind="worker name")
    if problem:
        return Decision(False, f"'worker' {problem}")
    if not known_worker(worker):
        return Decision(
            False,
            f"there is no Worker called {worker!r} in this project. A "
            "Manager may start the Workers you declared with `rite add "
            "worker`, and no others.",
        )

    ticket = ticket.strip()
    if not ticket:
        return Decision(False, "'ticket' is empty")
    if len(ticket) > _TICKET_MAX:
        return Decision(False, "'ticket' is longer than a ticket id")
    # ⚠ A ticket id reaches argv and a prompt. Restricted positively — what
    # an id is made OF — rather than by escaping, for the reason
    # `session_id_problem` gives: escaping is a claim about every
    # metacharacter of a shell nobody here chose.
    if not all(c.isalnum() or c in "-_#/." for c in ticket):
        return Decision(
            False,
            f"{ticket!r} is not shaped like a ticket id — letters, digits, "
            "'-', '_', '#', '/' and '.' only",
        )
    if not ticket_exists(ticket):
        return Decision(
            False,
            f"ticket {ticket!r} is not on this project's board. Refused "
            "rather than started: a Worker launched against a ticket nobody "
            "can see does work nobody asked for.",
        )

    # ⚠ The bound is APPLIED here, not merely reported. A request that
    # bypasses the sandbox count is a request for more capacity than the
    # operator agreed to.
    if capacity and running >= capacity:
        return Decision(
            False,
            f"{running} Worker(s) already running and this project allows "
            f"{capacity}. Refused rather than queued — nothing here would "
            "start it later, and a queued request that never runs is a "
            "Worker that silently never existed.",
        )
    return Decision(True, request=Request(worker=worker, ticket=ticket))


def launch_argv(root: Path, request: Request) -> list[str]:
    """The exact command the supervisor runs.

    ⚠ **A list, never a string, and never through a shell.** The two values
    are validated above, and this keeps them arguments even if a later
    change to those rules lets something through.
    """
    return [
        "rite",
        "sandbox",
        "start",
        request.worker,
        "--ticket",
        request.ticket,
    ]


def honour(root: Path, request: Request, timeout: int = 600) -> tuple[bool, str]:
    """Run the launch on the host and say what happened."""
    try:
        done = subprocess.run(
            launch_argv(root, request),
            cwd=str(root),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"`rite sandbox start` did not finish within {timeout}s"
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run `rite sandbox start`: {e}"
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        return False, tail[-1][:200] if tail else f"exit {done.returncode}"
    return True, f"started Worker {request.worker!r} on ticket {request.ticket}"


def take_requests(root: Path, manager: str) -> list[tuple[Path, str]]:
    """Every pending request, oldest first, removed as it is read.

    Removed rather than marked: a request that stays after being acted on is
    a Worker started twice the next time the loop comes round.
    """
    where = requests_dir(root, manager)
    if not where.is_dir():
        return []
    found: list[tuple[Path, str]] = []
    for path in sorted(where.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found.append((path, text))
        try:
            path.unlink()
        except OSError:
            pass
    return found


def instructions(root: Path, manager: str) -> str:
    """What the Manager is told to do instead of `rite sandbox start`."""
    return (
        f"To start a Worker, write a JSON file into {requests_dir(root, manager)} "
        f'containing exactly {{"worker": "<name>", "ticket": "<id>"}} — for '
        f'example `echo \'{{"worker":"alpha","ticket":"ABC-12"}}\' > '
        f"{requests_dir(root, manager)}/$(date +%s).json`.\n"
        "⚠ Do NOT run `rite sandbox start` yourself: you are inside a "
        "sandbox, and a sandbox cannot create another one. It would fail "
        "and the Worker would never start. rite starts it for you and "
        "reports the result in your next instruction."
    )


def project_capacity(root: Path) -> int:
    """How many Workers this project allows at once, or -1 if it cannot be
    read.

    ⚠ **Read here rather than passed in.** The supervisor's caller does not
    have the config in scope, and threading it through would put a
    security-relevant number on a signature that has dropped arguments
    before. -1 means "unknown", which `for_project` turns into a refusal
    rather than into "no limit".
    """
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return -1
    return int(getattr(parsed.sandbox, "max_concurrent_workers", 0) or 0)


def for_project(root: Path, board: object = None, capacity: int | None = None):
    """The decide-and-launch callable the supervisor holds.

    ⚠ **Composed here rather than in the supervisor**, so every input to the
    decision is assembled in the one file whose job is to be suspicious.
    The supervisor's part is to hand over a string and say what came back.

    ⚠ **No board means REFUSE, not "assume the ticket is real".** D-74, and
    the specific failure it prevents: an unreachable board is not an empty
    board, and treating it as one would let any string through as a ticket
    exactly when rite has lost the ability to check.
    """

    def known_worker(worker: str) -> bool:
        return (Path(root) / "workers" / worker / "worker.yml").is_file()

    def ticket_exists(ticket: str) -> bool:
        if board is None:
            return False
        lister = getattr(board, "list_tickets", None)
        if lister is None:
            return False
        try:
            found = lister()
        except Exception:
            # An error reaching the board is not an absent ticket. Refusing
            # here reports it; returning True would start a Worker on a
            # ticket nobody has confirmed exists.
            return False
        wanted = ticket.lstrip("#")
        for entry in found or ():
            given = getattr(entry, "id", None) or getattr(entry, "key", None)
            if given is not None and str(given).lstrip("#") == wanted:
                return True
        return False

    def running() -> int:
        from rite_ai.sandbox import list_rite_sandboxes

        entries = list_rite_sandboxes()
        return len(entries) if isinstance(entries, list) else -1

    def handle(raw: str) -> tuple[bool, str]:
        bound = project_capacity(Path(root)) if capacity is None else capacity
        if bound < 0:
            return False, (
                "refusing to start a Worker: this project's config could "
                "not be read, so rite cannot tell how many Workers you "
                "allow. An unreadable limit is not an absent one."
            )
        live = running() if bound else 0
        if live < 0:
            return False, (
                "refusing to start a Worker: rite could not count the "
                "sandboxes already running, so it cannot tell whether this "
                "project is at its limit. An uncountable limit is not an "
                "absent one."
            )
        decision = decide(raw, known_worker, ticket_exists, live, bound)
        if not decision.ok or decision.request is None:
            return False, f"refusing to start a Worker: {decision.reason}"
        return honour(Path(root), decision.request)

    return handle
