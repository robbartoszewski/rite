"""One cycle of the work-seeking loop, as a decision nobody has acted on (L-2).

`rite loop run --dry-run` prints what a cycle WOULD do and does nothing. That
ordering is deliberate: the judgement this loop makes is policy, and policy is
worth arguing with before it can spend anything. The first draft of the plan
read capacity off the claims ledger, which `start_worker` never writes to — a
dry run would have shown a dispatched Worker still reading as free, and the
error would have cost nothing instead of a session.

**Every line is a claim with its evidence.** The Owner session running the live
dogfood next door reported "neither worker is free" and then showed dirty
trees, absent new commits and live heartbeats — it did not assert it. That is
the standard here: a `WorkerView` carries the observations that produced it, so
a reader can disagree with the conclusion rather than only with the word.

**Nothing in this module writes, spawns or spends.** No board write, no
session, no Anthropic call. It reads a board, a claims ledger, some git
checkouts and some heartbeat files. SPEC §9.12 is untouched, which is why this
layer needs no amendment to ship.

**The verdicts are the point.** "Nothing to do" and "plenty to do, none of it
takeable" call for opposite responses — one is a reason to stop and the other
is emphatically not — and a loop that printed "nothing dispatched" for both
would stop on the wrong one. They are separate values with separate names, and
`Cycle.is_reason_to_stop` is the single place that difference is decided.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CLOSED = "closed"
"""The schedule authorises 0 Workers in this window. §2.7.3's clean stop —
sleep to the boundary, do not exit."""

IDLE = "idle"
"""The board has nothing ready. The ONE verdict that is a reason to stop."""

SATURATED = "saturated"
"""Work is ready and every Worker is busy. A queue, not a fault."""

BLOCKED = "blocked"
"""Work is ready, a Worker is FREE, and every ready ticket was last refused on
paths somebody still holds. Measured next door: a queue short of uncontended
files rather than of work, with the holders changing. Dispatching here burns a
session to rediscover a collision rite already knows about — and stopping here
is wrong too, because the work is real and the holder will let go."""

READY = "ready"
"""Work is ready and a Worker is free. A later layer dispatches here."""

UNKNOWN = "unknown"
"""Something could not be established. Never treated as any of the above:
a loop's default on the unknown is to stop and say so."""


@dataclass
class WorkerView:
    """One Worker, and why this cycle believes what it believes about it."""

    name: str
    free: bool = False
    verdict: str = ""
    evidence: list[str] = field(default_factory=list)
    """What was observed, in the order it was observed. Never empty: a
    conclusion with no evidence is the thing this module exists not to
    print."""
    held_paths: tuple[str, ...] = ()


@dataclass
class Cycle:
    """What one pass found, and what it would do about it."""

    verdict: str = UNKNOWN
    detail: str = ""
    workers: list[WorkerView] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)
    """Ticket ids waiting on the board."""
    would_dispatch: list[tuple[str, str]] = field(default_factory=list)
    """(ticket, worker) this cycle would start, had it been allowed to."""
    contention: list[str] = field(default_factory=list)
    """Claimed paths and who holds them — the surface that decides whether a
    ready ticket is actually takeable."""
    blocked: dict[str, str] = field(default_factory=dict)
    """ticket -> why it is not takeable right now. Populated only from
    OBSERVED refusals, never from a guess about which files a ticket needs."""
    capacity: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def free_workers(self) -> list[str]:
        return [w.name for w in self.workers if w.free]

    @property
    def is_reason_to_stop(self) -> bool:
        """`--until-empty`'s question, answered in one place.

        Only `IDLE` is. `SATURATED` means the work is there and somebody else
        is holding the capacity; stopping on it turns a busy fleet into a
        stopped one, and the queue it left behind is invisible until a human
        looks. `UNKNOWN` stops the loop too, but as a failure — see the
        caller; it is not the same exit.
        """
        return self.verdict == IDLE


def plan_cycle(
    root: Path,
    *,
    now: datetime | None = None,
    board=None,
    sandbox_status=None,
    clock: float | None = None,
) -> Cycle:
    """Read everything, decide nothing that acts. Returns, never raises.

    `board` and `sandbox_status` are injected so a cycle can be planned
    without a ticket backend or yoloAI — the states worth testing are the
    ones where a machine is NOT fully set up, and a test that needed one
    would only ever cover the case nobody needs reported.
    """
    from rite_ai.config.parse import load_project

    cycle = Cycle()
    now = now or datetime.now().astimezone()
    clock = time.time() if clock is None else clock

    project = load_project(root)
    if isinstance(project, list):
        cycle.verdict = UNKNOWN
        cycle.detail = "the project did not load"
        cycle.problems.extend(f"{e.file}: {e.message}" for e in project)
        return cycle

    worktree = _worktree_problem(root)
    if worktree:
        # A tracked `.rite/` makes every git worktree its own project root,
        # with its own claims ledger and its own idea of who is busy — while
        # pointing at one board. Two loops in two worktrees would both see
        # full capacity.
        cycle.verdict = UNKNOWN
        cycle.detail = worktree
        cycle.problems.append(worktree)
        return cycle

    cycle.capacity, window = _capacity(project, now)
    cycle.workers = [
        _look_at_worker(root, w.name, clock, sandbox_status) for w in project.workers
    ]

    # The blind window: a Worker dispatched moments ago is observably free
    # and is not. Observation is the better witness everywhere else, so the
    # record is asked only about the seconds it cannot cover (L-3).
    from rite_ai.loop.intents import reconcile

    busy_now = {w.name for w in cycle.workers if not w.free}
    settled = reconcile(root, observably_busy=busy_now, now=clock)
    for intent in settled.holding:
        for worker in cycle.workers:
            if worker.name == intent.worker and worker.free:
                worker.free = False
                worker.verdict = "busy — a session was dispatched to it just now"
                worker.evidence.append(
                    f"dispatched {intent.ticket} {_age(clock - intent.timestamp)} "
                    "ago; nothing visible yet, which is normal for a few seconds"
                )
    cycle.problems.extend(settled.problems)

    ledger_claims = _claims(root)
    if ledger_claims is None:
        cycle.verdict = UNKNOWN
        cycle.detail = "the claims ledger could not be read"
        cycle.problems.append(
            "the claims ledger could not be read, so who is busy cannot be "
            "established — acting on that guess is how one path gets claimed twice"
        )
        return cycle
    cycle.contention = _contention(ledger_claims, clock)

    if window is None:
        cycle.verdict = UNKNOWN
        cycle.detail = (
            f"the schedule's timezone {project.config.schedule.timezone!r} "
            "could not be resolved"
        )
        cycle.problems.append(cycle.detail)
        return cycle

    if cycle.capacity == 0:
        cycle.verdict = CLOSED
        cycle.detail = (
            f"the schedule authorises 0 Workers at {window} — §2.7.3's clean "
            "stop, not a fault"
        )
        return cycle

    if board is None:
        cycle.verdict = UNKNOWN
        cycle.detail = "no ticket backend is configured, so there is no queue to read"
        cycle.problems.append(cycle.detail)
        return cycle

    # Counted before and after, because `cycle.problems` already carries the
    # lost-intent reports by now, and a leaked dispatch is something to say
    # out loud — not a reason to call the whole cycle unknown.
    before = len(cycle.problems)
    cycle.ready = _ready(board, cycle)
    if len(cycle.problems) > before:
        cycle.verdict = UNKNOWN
        cycle.detail = "the board could not be read"
        return cycle

    free = cycle.free_workers
    if not cycle.ready:
        cycle.verdict = IDLE
        cycle.detail = (
            f"nothing on the board is waiting; {len(free)} of "
            f"{len(cycle.workers)} Worker(s) free"
        )
        return cycle

    if not free:
        cycle.verdict = SATURATED
        cycle.detail = (
            f"{len(cycle.ready)} ticket(s) waiting and no Worker free — a "
            "queue, not a fault, and NOT a reason to stop"
        )
        return cycle

    held = {p for w in cycle.workers for p in w.held_paths}
    cycle.blocked = _blocked(root, cycle.ready, held, clock)
    takeable = [t for t in cycle.ready if t not in cycle.blocked]

    if not takeable:
        cycle.verdict = BLOCKED
        cycle.detail = (
            f"{len(cycle.ready)} ticket(s) waiting and {len(free)} Worker(s) "
            "free, but every one of them was last refused on paths somebody "
            "still holds — NOT a reason to stop, and not a reason to dispatch"
        )
        return cycle

    slots = min(len(free), max(0, cycle.capacity - _busy(cycle)))
    cycle.would_dispatch = list(zip(takeable, free[:slots], strict=False))
    if not cycle.would_dispatch:
        cycle.verdict = SATURATED
        cycle.detail = (
            f"{len(cycle.ready)} ticket(s) waiting; the schedule's {cycle.capacity} "
            f"slot(s) are already in use"
        )
        return cycle

    cycle.verdict = READY
    cycle.detail = f"would start {len(cycle.would_dispatch)} session(s)"
    return cycle


def _worktree_problem(root: Path) -> str:
    """`.rite/` is tracked, so a git worktree is its own project root."""
    git = root / ".git"
    if git.is_file():
        return (
            f"{root} is a git worktree, and `.rite/` is tracked — so it has "
            "its own claims ledger while sharing one board. Run the loop from "
            "the main checkout, or two loops will both see full capacity"
        )
    return ""


def _capacity(project, now: datetime) -> tuple[int, str | None]:
    from rite_ai.schedule import current_minute_of_day, workers_at

    schedule = project.config.schedule
    minute = current_minute_of_day(schedule.timezone, now)
    if minute is None:
        return 0, None
    return workers_at(schedule, minute), f"{minute // 60:02d}:{minute % 60:02d}"


def _claims(root: Path):
    from rite_ai.claims.ledger import ClaimsLedger

    path = root / ".rite" / "claims.json"
    if not path.is_file():
        return []
    try:
        return ClaimsLedger(path).list_claims()
    except Exception:  # noqa: BLE001 - an unreadable ledger is a result
        return None


def _busy(cycle: Cycle) -> int:
    return sum(1 for w in cycle.workers if not w.free)


def _contention(claims, clock: float) -> list[str]:
    """Who holds what, and for how long.

    The live dogfood's queue was not short of work — it was short of
    UNCONTENDED files, with four tickets blocked because other sessions held
    their paths and the holders kept changing.

    ⚠ **rite cannot tell which paths a TICKET needs**, so this cycle cannot
    say "ready but contended" about any particular ticket. A ticket carries a
    label, not a file list, and the collision is discovered by the Worker when
    its `rite claim` is refused — after a session has already started. So what
    is printed is the surface: every held path and its holder, for a reader to
    judge against the queue. Making that judgement mechanical needs a record of
    refused claims, which nothing writes yet.
    """
    lines: list[str] = []
    for claim in sorted(claims, key=lambda c: (c.worker, c.paths and c.paths[0] or "")):
        age = _age(clock - claim.timestamp)
        ticket = f" for {claim.ticket}" if claim.ticket else ""
        lines.append(f"{claim.worker} holds {', '.join(claim.paths)}{ticket} ({age})")
    return lines


def _blocked(
    root: Path, ready: list[str], held: set[str], clock: float
) -> dict[str, str]:
    """Which waiting tickets are known to be blocked, and by whom.

    **Only what was observed.** A ticket carries a label, not a file list, so
    nothing here predicts a collision — it reads the ones a Worker already hit
    and wrote down (`contention.jsonl`). A ticket nobody has tried is not
    blocked; it is untried, and those are different enough that guessing would
    park work nothing was holding.

    A refusal only still counts while the paths that caused it are STILL held.
    The holders change — that is what makes this a queue rather than a
    deadlock — and a stale refusal would retire a ticket for ever over a
    collision that cleared ten minutes ago.
    """
    from rite_ai.claims.ledger import read_contention

    blocked: dict[str, str] = {}
    for record in read_contention(root):
        if not record.ticket or record.ticket not in ready:
            continue
        still = [p for p in record.paths if p in held]
        if not still:
            continue
        holders = ", ".join(record.holders) or "another worker"
        blocked[record.ticket] = (
            f"{record.worker} was refused {', '.join(still)} "
            f"({holders} still holds it, {_age(clock - record.timestamp)} ago)"
        )
    return blocked


def _ready(board, cycle: Cycle) -> list[str]:
    from rite_ai.coordination.ticket_labels import SCHEDULED
    from rite_ai.tickets import BackendError, TicketFilter

    result = board.list_tickets(TicketFilter(label=SCHEDULED))
    if isinstance(result, BackendError):
        cycle.problems.append(f"the board could not be read: {result.message}")
        return []
    return [t.id for t in result]


def _look_at_worker(root: Path, name: str, clock: float, sandbox_status) -> WorkerView:
    """Busy or free, with the observations that decided it.

    The order matters. A claim is the strongest evidence a session is working,
    because a session took it deliberately; a dirty tree is next, because
    somebody edited something and did not finish; a heartbeat is weakest,
    because it says a session existed recently and nothing about whether it
    still has work.
    """
    view = WorkerView(name=name)

    claims = _claims(root)
    if claims is None:
        view.verdict = "cannot tell"
        view.evidence.append("the claims ledger could not be read")
        return view
    held = [c for c in claims if c.worker == name]
    if held:
        view.held_paths = tuple(p for c in held for p in c.paths)
        view.evidence.append(
            f"holds {len(view.held_paths)} path(s): {', '.join(view.held_paths[:4])}"
            + ("…" if len(view.held_paths) > 4 else "")
        )
        view.verdict = "busy — holding a claim"
        return view

    dirty = _dirty(root / "workers" / name)
    if dirty:
        view.evidence.extend(dirty)
        view.verdict = "busy — uncommitted work in its checkout"
        return view
    view.evidence.append("checkout clean, nothing uncommitted")

    from rite_ai.reporting.heartbeat import read_heartbeat

    beat = read_heartbeat(root, name)
    if beat is not None:
        view.evidence.append(
            f"heartbeat {_age(clock - beat.timestamp)} ago"
            + (f" on {beat.ticket}" if beat.ticket else "")
        )
    else:
        view.evidence.append("no heartbeat recorded")

    if sandbox_status is not None:
        status = sandbox_status(name, root)
        view.evidence.append(f"sandbox: {status}")
        if not getattr(status, "known", True):
            # "Could not ask" is not "no sandbox", and only one of them is
            # safe to dispatch onto.
            view.verdict = "cannot tell — the sandbox could not be asked"
            return view
        if str(status) not in ("not found", ""):
            view.verdict = "busy — a sandbox is running for it"
            return view

    view.free = True
    view.verdict = "free"
    return view


def _dirty(worker_dir: Path) -> list[str]:
    """Uncommitted work, per module checkout. The evidence the dogfood Owner
    showed rather than asserted."""
    from rite_ai.workspace import git_ops

    if not worker_dir.is_dir():
        return []
    found: list[str] = []
    for child in sorted(p for p in worker_dir.iterdir() if p.is_dir()):
        if not git_ops.is_git_repo(child):
            continue
        paths = git_ops.uncommitted_paths(child)
        if isinstance(paths, git_ops.GitError):
            found.append(f"{child.name}: could not be read ({paths.message})")
        elif paths:
            found.append(
                f"{child.name}: {len(paths)} uncommitted ({', '.join(paths[:3])})"
            )
    return found


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h{int((seconds % 3600) // 60):02d}m"


def format_cycle(cycle: Cycle) -> list[str]:
    """The dry run, written to be checked rather than admired.

    Conclusion last, evidence first — a reader who disagrees needs to see what
    was observed before being told what it meant.
    """
    lines = [f"schedule: {cycle.capacity} Worker(s) authorised in this window"]

    if cycle.workers:
        lines.append("")
        lines.append("workers:")
        for worker in cycle.workers:
            lines.append(f"  {worker.name}: {worker.verdict}")
            lines.extend(f"    · {e}" for e in worker.evidence)
    else:
        lines.append("workers: none registered")

    lines.append("")
    if cycle.contention:
        lines.append(f"claimed paths ({len(cycle.contention)}):")
        lines.extend(f"  · {c}" for c in cycle.contention)
        lines.append(
            "  (a ticket carries a label, not a file list, so a ticket is "
            "listed as blocked below only when a Worker ACTUALLY hit the "
            "collision — untried tickets are untried, not blocked)"
        )
    else:
        lines.append("claimed paths: none")

    lines.append("")
    takeable = [t for t in cycle.ready if t not in cycle.blocked]
    lines.append(
        f"waiting on the board: {len(cycle.ready)} "
        f"({len(takeable)} takeable, {len(cycle.blocked)} blocked on held paths)"
    )
    if takeable:
        lines.append(
            f"  takeable: {', '.join(takeable[:12])}"
            + ("…" if len(takeable) > 12 else "")
        )
    for ticket, why in cycle.blocked.items():
        lines.append(f"  blocked: {ticket} — {why}")

    for problem in cycle.problems:
        lines.append(f"problem: {problem}")

    lines.append("")
    lines.append(f"verdict: {cycle.verdict} — {cycle.detail}")
    if cycle.would_dispatch:
        lines.append("would start (DRY RUN — nothing was started):")
        lines.extend(f"  · {t} → {w}" for t, w in cycle.would_dispatch)
    lines.append(
        "a reason to stop: "
        + ("yes" if cycle.is_reason_to_stop else "no — the work is there")
    )
    return lines
