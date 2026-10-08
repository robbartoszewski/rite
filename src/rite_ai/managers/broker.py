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

NO_SLOT = None
"""The third outcome of `for_project(...)(raw)`, beside True (started) and
False (refused): the request is valid and no Worker slot is free right now.
It is queued, not dropped (`supervise._honour_worker_requests`), because a
discarded request is a lost instruction. Falsy, so a caller that only asks
"did it start?" still reads it as not started."""
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
    full: bool = False
    """Refused only because the project is at capacity: valid, and able to
    start later unchanged."""


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
    *,
    read_ticket=None,
) -> Decision:
    """Whether this request may be honoured.

    ⚠ **Every branch here refuses. That is the shape to keep** — a validator
    written as "accept unless something looks wrong" fails open on the input
    nobody thought of, and this one is fed by a process that may be
    compromised.

    `known_worker` and `ticket_exists` are predicates rather than lookups so
    the policy can be tested without a board or a project on disk. The
    wiring supplies the real ones.

    `read_ticket(id)` returns the board's ticket, so its STATUS can be read
    (SCRUM-73). **None refuses every request**, the stance
    `routing.decide` takes on the same value and for the same reason: a
    Worker started on a ticket whose status nobody checked is how KAN-28 was
    worked twice after it was Done. Existence alone cannot answer it — the
    label a ticket carries is only ever added, so a finished ticket still
    reads as scheduled for ever.
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

    # ⚠ SCRUM-73. The ticket exists; whether its work is OVER is a separate
    # question, and the one the a9 dogfood got wrong. Checked before the
    # capacity bound so that a finished ticket is refused outright rather
    # than queued for the next free slot.
    if read_ticket is None:
        return Decision(
            False,
            f"rite could not read the status of ticket {ticket!r}, so it "
            "cannot tell whether that work is already finished. An "
            "unreadable status is not a status that says 'not yet done'.",
        )
    found = read_ticket(ticket)
    if found is None:
        return Decision(
            False,
            f"ticket {ticket!r} could not be read back from the board, so "
            "its status is unknown. Refused rather than started.",
        )
    from rite_ai.tickets.statuses import is_terminal

    if is_terminal(found):
        status = getattr(found, "status", "") or "a terminal status"
        return Decision(
            False,
            f"ticket {ticket!r} is {status}: that work is finished, so no "
            "Worker is started on it. A `ready-to-work` or `scheduled` "
            "label does not say otherwise — rite only ever adds those, so a "
            "delivered ticket keeps them.",
        )

    # ⚠ The bound is APPLIED here, not merely reported. A request that
    # bypasses the sandbox count is a request for more capacity than the
    # operator agreed to.
    if capacity and running >= capacity:
        # Queued, not refused: the supervisor holds it and asks again when a
        # slot frees, deciding afresh each time (so a queued request is never
        # trusted more than a new one).
        return Decision(
            False,
            f"{running} Worker(s) already running and this project allows {capacity}",
            full=True,
        )
    return Decision(True, request=Request(worker=worker, ticket=ticket))


def launch_argv(root: Path, request: Request) -> list[str]:
    """The exact command the supervisor runs.

    ⚠ **A list, never a string, and never through a shell.** The two values
    are validated above, and this keeps them arguments even if a later
    change to those rules lets something through.
    """
    from rite_ai import own_command

    # ⚠ THE RITE THAT IS RUNNING, not the first `rite` on PATH (as
    # `own_command` says for the Manager's instructions, `c0e4097`). Measured
    # 2026-09-26: PATH gave an installed 0.5.1, which has no event log, so a
    # Worker started here was never recorded and K4's standup missed it.
    return [
        own_command(),
        "sandbox",
        "start",
        request.worker,
        "--ticket",
        request.ticket,
    ]


def honour(root: Path, request: Request, timeout: int = 600) -> tuple[bool | None, str]:
    """Run the launch on the host and say what happened: True, False, or
    `NO_SLOT` when `rite sandbox start` refused only for want of a slot."""
    from rite_ai.sandbox import EXIT_NO_SLOT

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
        said = tail[-1][:200] if tail else f"exit {done.returncode}"
        return (NO_SLOT if done.returncode == EXIT_NO_SLOT else False), said
    return True, f"started Worker {request.worker!r} on ticket {request.ticket}"


def requeue(root: Path, manager: str, path: Path, raw: str) -> None:
    """Put a request back where `take_requests` found it, under the same
    name, so it keeps its place in the queue. Raises OSError, which the
    caller says: a request that could not be put back is a lost one.

    Through `own_dir`, creating and never replacing, following no link."""
    from rite_ai.managers import own_dir

    own_dir.write_new(root, manager, REQUESTS_DIRNAME, path.name, raw)


def queued(root: Path, manager: str) -> bool:
    """Whether any request is waiting, without taking it."""
    from rite_ai.managers import own_dir

    try:
        return bool(own_dir.names(root, manager, REQUESTS_DIRNAME, ".json"))
    except OSError:
        return False


def slot_free(root: Path) -> bool:
    """Whether one more of this project's Workers could start now, by the
    same three counts `start_worker` refuses on (the project cap, the
    schedule window, and an uncountable answer counting as no). Cheap enough
    for a wait's periodic check; the start itself decides again."""
    from rite_ai.sandbox import (
        CountUnavailable,
        _configured_workers,
        _schedule_refusal,
        count_active_sandboxes,
    )

    bound = project_capacity(Path(root))
    running = count_active_sandboxes(root, _configured_workers(root))
    if bound < 0 or isinstance(running, CountUnavailable):
        return False
    if bound and running >= bound:
        return False
    return _schedule_refusal(root, int(running)) is None


def take_requests(root: Path, manager: str) -> list[tuple[Path, str]]:
    """Every pending request, oldest first, removed as it is read.

    Removed rather than marked: a request that stays after being acted on is
    a Worker started twice the next time the loop comes round. Read through
    `own_dir`, which follows no link the Manager planted (SCRUM-59 review);
    a `requests` that is one raises OSError for the caller to say.
    """
    from rite_ai.managers import own_dir

    where = requests_dir(root, manager)
    return [
        (where / name, text)
        for name, text in own_dir.take(
            root, manager, REQUESTS_DIRNAME, MAX_REQUEST_BYTES
        )
    ]


def instructions(root: Path, manager: str) -> str:
    """What the Manager is told to do instead of `rite sandbox start`."""
    from rite_ai import own_command

    return (
        f"To start a Worker, run `{own_command()} request start <name> --ticket "
        "<id>`: rite writes the request into your own directory, so nothing "
        "reaches the shell (SCRUM-59).\n"
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


def may_own_worker(root: Path, manager: str, worker: str) -> str:
    """Why `manager` may not be given `worker`, or "" when it may.

    🔴 **SCRUM-83.** Nothing used to gate WHO may ask for a Worker: `decide`
    checked the request's size, its JSON, that the Worker and ticket exist and
    that a slot was free, and no duty at all. So in a mixed fleet the Manager
    that got there first owned the Worker — and ownership is load-bearing three
    times over: only the owner may gate or step it (`lifecycle._may_act`), only
    the owner's cycle drives its ticket (`_local_tier_tickets`), and only the
    `decompose` holder can author a plan. A Manager that cannot author, holding
    a Worker whose plan must be authored, is a deadlock with no configuration
    that escapes it. Measured 2026-10-08: a Claude Manager holding only
    `plan-review` won the GPU Worker and the ticket never left `defined`.

    ⚠ **Gated on DUTY and CAPABILITY, never on a provider.** The question asked
    of the Worker is "does rite have to author your plan"
    (`needs_authored_plan`), not "are you local"; the question asked of the
    Manager is "do you hold `decompose`", not "are you Claude". A Worker that
    plans its own work may be owned by any Manager, as before. Adding a
    provider needs no change here.

    ⚠ **Fail closed.** Anything that cannot be read is a refusal, which is this
    module's rule: a Worker handed to a Manager that cannot drive it is a fleet
    that looks healthy and advances nothing, and that is worse than a refusal
    somebody has to read.
    """
    from rite_ai.config.parse import parse_config

    try:
        from rite_ai.sandbox import worker_manifest

        manifest = worker_manifest(Path(root), worker)
    except Exception as e:  # noqa: BLE001 - cannot tell is a refusal
        return (
            f"rite could not read {worker!r}'s manifest, so it cannot tell "
            f"whether {manager!r} is able to drive it ({type(e).__name__})"
        )
    if manifest is None:
        # 🔴 **`worker_manifest` returns None for a manifest it could not
        # read**, and a bare `getattr(manifest, ..., False)` turned that into
        # "this Worker plans its own work" and allowed the request — fail-OPEN,
        # in the one function whose whole job is to fail closed. Caught by
        # `test_an_unreadable_manifest_is_a_refusal`.
        return (
            f"rite could not read {worker!r}'s manifest, so it cannot tell "
            f"whether {manager!r} is able to drive it"
        )
    if not manifest.needs_authored_plan:
        # It plans its own work. Who owns it is not this gate's business.
        return ""

    try:
        roles = parse_config(
            Path(root) / ".rite" / "config.yaml"
        ).coordination.manager_roles
    except Exception as e:  # noqa: BLE001
        return (
            f"rite could not read this project's Managers, so it cannot tell "
            f"whether {manager!r} may author {worker!r}'s plan "
            f"({type(e).__name__})"
        )
    # 🔴 SCRUM-83 refined: HOLD **OR DELEGATE**. An owner with no authoring
    # duty may still own and drive this Worker, provided exactly one other
    # Manager holds `decompose` and authors for it. Asked of the same function
    # the decomposer uses, so the gate cannot accept an arrangement the
    # authoring path would then refuse — which is how the deadlock this
    # replaces came about.
    from rite_ai.local.decompose import author_for

    author, problem = author_for(roles, manager)
    if problem:
        return (
            f"{worker!r} needs rite to author its plan, and {problem}. Only a "
            "Worker's owner may gate or step it, and only its owner's cycle "
            "drives its ticket, so the Manager that owns this one must hold "
            "the authoring duty or be able to delegate it."
        )
    del author
    return ""


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

    def _read_from_board(ticket: str):
        """The board's own entry for this id, or None.

        ⚠ **One read answers both questions** — whether the ticket exists
        and what its status is (SCRUM-73). Two reads could disagree, and the
        pair "it exists" / "its status could not be read" is the shape that
        would start a Worker on finished work.

        ⚠ **Read with no filter, which means different things per backend,
        and both of them refuse.** JIRA returns every status, so a Done
        ticket is found here and refused *as finished*, by name. GitHub's
        `issue list` defaults to open issues (measured: `list_tickets` adds
        `--state` only for a filter), so a closed issue is not returned at
        all and is refused as absent. A status filter is deliberately NOT
        added to get a uniform answer: "all" is GitHub-only vocabulary, and
        JIRA would turn it into `status = "all"` and fail the whole read.
        """
        if board is None:
            return None
        lister = getattr(board, "list_tickets", None)
        if lister is None:
            return None
        try:
            found = lister()
        except Exception:
            # An error reaching the board is not an absent ticket. Refusing
            # here reports it; returning a ticket would start a Worker on
            # one nobody has confirmed exists.
            return None
        wanted = ticket.lstrip("#")
        for entry in found or ():
            given = getattr(entry, "id", None) or getattr(entry, "key", None)
            if given is not None and str(given).lstrip("#") == wanted:
                return entry
        return None

    def _one_read():
        """A reader whose answer is taken ONCE per request, and the
        existence predicate that shares it.

        Both questions `decide` asks — is it real, is it finished — are
        answered from the same read. Built fresh for each request rather
        than cached in this closure, which outlives many cycles: a board
        answer held across cycles is a board answer that has gone stale.
        """
        seen: dict[str, object] = {}

        def reader(ticket: str):
            if ticket not in seen:
                seen[ticket] = _read_from_board(ticket)
            return seen[ticket]

        return reader, lambda ticket: reader(ticket) is not None

    def running() -> int:
        """THIS project's running Workers, or -1 when they cannot be counted.

        🔴 SCRUM-36. This was `len(list_rite_sandboxes())`: every `rite-`
        sandbox on the machine, so another project's Workers and leftover
        test probes filled this project's capacity, and the broker refused
        Workers for a project that had none running (seen in the live
        dogfood; the fix there was destroying sandboxes by hand). The cap is
        per project (SPEC §2.5.9), so the count is the project-scoped one
        `start_worker` already uses."""
        from rite_ai.sandbox import (
            CountUnavailable,
            _configured_workers,
            count_active_sandboxes,
        )

        counted = count_active_sandboxes(root, _configured_workers(root))
        return -1 if isinstance(counted, CountUnavailable) else int(counted)

    def handle(raw: str, manager: str = "") -> tuple[bool | None, str]:
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
        reader, ticket_exists = _one_read()
        decision = decide(
            raw,
            known_worker,
            ticket_exists,
            live,
            bound,
            read_ticket=reader,
        )
        if decision.full:
            return NO_SLOT, decision.reason
        if not decision.ok or decision.request is None:
            return False, f"refusing to start a Worker: {decision.reason}"
        # 🔴 SCRUM-83, AFTER `decide` has validated the request (so the Worker
        # name is a real one) and BEFORE `honour` starts anything. Here rather
        # than inside `decide` so the policy function stays testable without a
        # project on disk, which is what its own docstring asks for.
        why = may_own_worker(Path(root), manager, decision.request.worker)
        if why:
            return False, f"refusing to start a Worker: {why}"
        return honour(Path(root), decision.request)

    return handle
