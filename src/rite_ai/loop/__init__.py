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
"""The board LISTED nothing ready. The ONE verdict that is a reason to stop.
rite's own writes are read back exactly (`tickets.own_writes`); a ticket a
person created seconds ago may not be listed yet, and that is said (DF4)."""

SATURATED = "saturated"
"""Work is ready and every Worker is busy. A queue, not a fault."""

BLOCKED = "blocked"
"""Work is ready, a Worker is FREE, and every ready ticket was last refused on
paths somebody still holds. Measured next door: a queue short of uncontended
files rather than of work, with the holders changing. Dispatching here burns a
session to rediscover a collision rite already knows about — and stopping here
is wrong too, because the work is real and the holder will let go."""

DEADLOCKED = "deadlocked"
"""Ready work, a free Worker, and the paths that block it are held by holders
that look dead. `blocked` is a queue — the holder finishes and lets go.
This is not: nobody is coming back, so waiting is indefinite and the loop says
so instead of sleeping on it for ever. It still does not RELEASE anything —
rite cannot tell a crashed session from a session thinking hard, and the cost
of being wrong is two sessions on one path."""

READY = "ready"
"""Work is ready and a Worker is free. A later layer dispatches here."""

REFINING = "refining"
"""Nothing is REFINED, and the Owner has refinement work it may start now
(TR2; the note's part 3.4 step 0): a first look, a reply, or a deadline. A
session starts, as for `ready`. Not `idle`: a board of unrefined tickets has
work on it, and reading it as empty ended the run before anything could be
refined (the defect the note's part 3.4 step 8 corrects)."""

WAITING_ON_USER = "waiting-on-user"
"""Work is on the board and none of it can start: every `scheduled` ticket
is in refinement, waiting for the User, parked or unreadable, or queued
behind the cap, and the User has done nothing since the last refinement
session. Neither `idle` (there is work) nor `ready` (none of it can move).
The supervisor waits and starts no session until he answers or a round's
deadline passes (TR2; Robert's third verdict)."""

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


def as_of(moment: float | None) -> str:
    """`HH:MM:SS` local time of a board read, for "nothing ready as of"."""
    if moment is None:
        return "an unrecorded time"
    return time.strftime("%H:%M:%S", time.localtime(moment))


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
    suspects: list = field(default_factory=list)
    """Claims whose holder looks dead (`claims.suspect`). Reported, never
    released. The loop surfaces them because a claim nobody will release is
    the difference between a queue that drains and one that never does."""
    blocked: dict[str, str] = field(default_factory=dict)
    """ticket -> why it is not takeable right now. Populated only from
    OBSERVED refusals, never from a guess about which files a ticket needs."""
    capacity: int = 0
    problems: list[str] = field(default_factory=list)
    scheduled: int = 0
    """How many `scheduled` tickets the board listed, refined or not."""
    board_read_at: float | None = None
    """Wall-clock time the board's list came back. What "nothing ready" is
    true AS OF: a list is a snapshot, and a ticket created outside rite just
    before or after it is not in it (DF4). None when the board was not read."""
    refinement: object = None
    """The Owner's refinement work (`refinement.admit.Admission`), or None
    when this Manager is not the one that refines (TRQ7)."""
    refining: str = ""
    """Why a session starts for refinement, or "" (`admit.reason_to_start`)."""

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
    refiner: str | None = None,
    refinement=None,
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
    from rite_ai.claims.suspect import suspect_claims

    heartbeat = project.config.heartbeat
    cycle.suspects = suspect_claims(
        root,
        registered=[w.name for w in project.workers],
        threshold_seconds=heartbeat.interval_minutes * 60 * heartbeat.stall_threshold,
        now=clock,
    )

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
    cycle.ready = _ready(root, project, board, cycle, clock, refiner, refinement)
    cycle.board_read_at = time.time()
    if len(cycle.problems) > before:
        cycle.verdict = UNKNOWN
        cycle.detail = "the board could not be read"
        return cycle

    free = cycle.free_workers
    if not cycle.ready and cycle.refinement is not None and cycle.scheduled:
        work = cycle.refinement
        if cycle.refining:
            cycle.verdict = REFINING
            cycle.detail = (
                f"nothing is refined yet; {cycle.refining}, so a session "
                f"refines {len(work.open) + len(work.start)} ticket(s)"
            )
        else:
            cycle.verdict = WAITING_ON_USER
            cycle.detail = _waiting_detail(work)
        return cycle
    if not cycle.ready:
        cycle.verdict = IDLE
        cycle.detail = (
            # A snapshot with its time, not "the board is empty": a list lags
            # writes rite did not make itself (DF4); rite's own are read back
            # in `own_writes`.
            f"the board listed nothing waiting as of {as_of(cycle.board_read_at)} "
            f"(a ticket created outside rite shortly before then, or since, is "
            f"not in that read); {len(free)} of {len(cycle.workers)} Worker(s) free"
        )
        return cycle

    if not free:
        cycle.verdict = SATURATED
        # "A queue" only of Workers seen working. One waiting on a question,
        # or whose sandbox sits at its prompt, is not queued work (dogfood
        # S1: a Worker blocked eight hours was counted as saturation).
        stuck = [w.name for w in cycle.workers if not w.verdict.startswith("busy")]
        cycle.detail = (
            f"{len(cycle.ready)} ticket(s) waiting and no Worker free — a "
            "queue, not a fault, and NOT a reason to stop"
            if not stuck
            else f"{len(cycle.ready)} ticket(s) waiting and no Worker free, "
            f"but {', '.join(stuck)} not seen working (see workers above) — "
            "NOT a reason to stop, and not a queue that clears on its own"
        )
        return cycle

    # From the LEDGER, not from the registered Workers' views. A path held by
    # a holder nobody registered — which is what the motivating ghost claim
    # is — would otherwise read as free, and the ticket it blocks would read
    # as takeable. The same roster-versus-ledger mistake `suspect_claims`
    # exists to avoid, one layer up.
    held = {p for c in ledger_claims for p in c.paths}
    cycle.blocked, blockers = _blocked(root, cycle.ready, held, clock)
    takeable = [t for t in cycle.ready if t not in cycle.blocked]

    if not takeable:
        # Whether this is a queue or a wall depends on whether anyone is
        # coming back for THESE paths. The holders come from the contention
        # records — the sessions that actually refused these tickets — not
        # from the registered Worker list: a claim can be held by a name
        # nobody registered, and the first version asked "are all registered
        # claim-holding Workers dead?", which is wrong in both directions.
        dead_holders = {s.worker for s in cycle.suspects}
        # PER TICKET, not a union across tickets. A ticket clears only when
        # EVERY path in its refusal is free again, so one dead holder dooms
        # it whatever the others do — while a union test let one live holder
        # anywhere suppress the verdict for every ticket, which is the
        # sleeping-until-morning this exists to stop. A ticket whose holders
        # could not be identified is NOT counted doomed: unknown is "not
        # provably gone", and being wrong that way costs a cycle.
        doomed = [
            ticket
            for ticket in cycle.blocked
            if blockers.get(ticket) and (blockers[ticket] & dead_holders)
        ]
        if doomed and len(doomed) == len(cycle.blocked):
            cycle.verdict = DEADLOCKED
            cycle.detail = (
                f"{len(cycle.ready)} ticket(s) waiting and {len(free)} "
                "Worker(s) free, and every path blocking them is held by a "
                "holder that looks dead — this will not clear on its own"
            )
            return cycle
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
    from rite_ai.schedule import current_minute_of_day, current_moment, workers_at

    schedule = project.config.schedule
    minute = current_minute_of_day(schedule.timezone, now)
    if minute is None:
        return 0, None
    # THE WEEKDAY TOO. Omitting it silently ignores `days:`, so a Saturday
    # configured for 0 Workers reported the weekday window's count — while
    # `start_worker` correctly refused. Two true sentences that disagree,
    # which is the same failure the advisory schedule produced before 0.5.1.
    weekday = current_moment(schedule.timezone, now).weekday
    return (
        workers_at(schedule, minute, weekday),
        f"{minute // 60:02d}:{minute % 60:02d}",
    )


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
) -> tuple[dict[str, str], dict[str, set[str]]]:
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
    from rite_ai.claims.ledger import paths_overlap, read_contention

    blocked: dict[str, str] = {}
    blockers: dict[str, set[str]] = {}
    for record in read_contention(root):
        if not record.ticket or record.ticket not in ready:
            continue
        # `paths_overlap`, not string equality: claims nest. A claim on
        # `engine/` blocks a request for `engine/parser.py`, which is how
        # `ClaimsLedger.claim` decided to refuse it in the first place —
        # comparing the strings instead silently reports the ticket takeable
        # and, worse, lets it suppress a real deadlock by looking available.
        still = [p for p in record.paths if any(paths_overlap(p, h) for h in held)]
        if not still:
            continue
        holders = ", ".join(record.holders) or "another worker"
        blocked[record.ticket] = (
            f"{record.worker} was refused {', '.join(still)} "
            f"({holders} still holds it, {_age(clock - record.timestamp)} ago)"
        )
        blockers[record.ticket] = set(record.holders)
    return blocked, blockers


def _ready(
    root: Path, project, board, cycle: Cycle, clock: float, refiner, refinement=None
) -> list[str]:
    """The `scheduled` tickets a Worker may start: the REFINED ones (TR2).

    ⚠ **Not every `scheduled` ticket any more.** Under Robert's semantics
    scheduled + not refined is the Owner's to refine, and scheduled + refined
    is work. So each listed ticket is read once through the refinement
    predicate, and the Owner's unrefined ones become `cycle.refinement`.
    Never gated alone: without the `refining` and `waiting-on-user` verdicts
    a board of unrefined tickets would read `idle` and end the run.

    `refinement` is injectable for tests; None asks the predicate about
    `board` itself, one read per ticket.
    """
    from rite_ai.coordination.ticket_labels import SCHEDULED
    from rite_ai.refinement import admit, rounds
    from rite_ai.refinement import status as refinement_status
    from rite_ai.tickets import BackendError, TicketFilter

    result = board.list_tickets(TicketFilter(label=SCHEDULED))
    if isinstance(result, BackendError):
        cycle.problems.append(f"the board could not be read: {result.message}")
        return []
    cycle.scheduled = len(result)
    owner = _refiner_of(project, refiner)
    attempts = rounds.all_attempts(root, owner) if owner else {}
    states = []
    if refinement is None:

        def refinement(ticket_id: str):
            return refinement_status.status(board, ticket_id)

    for ticket in result:
        answer = refinement_status.checked(refinement, ticket.id)
        if answer.ticket is None:
            answer = refinement_status.Status(
                answer.state, answer.record, answer.detail, ticket
            )
        states.append(
            (ticket, rounds.state_of(answer, attempts.get(ticket.id), now=clock))
        )
    limits = project.config.refinement
    work = admit.admit(
        states, open_max=limits.open_max, start_per_session=limits.start_per_session
    )
    if refiner is None or owner == refiner:
        cycle.refinement = work
        last = rounds.last_session(root, owner) if owner else None
        events = rounds.events_since(attempts, last, now=clock)
        cycle.refining = admit.reason_to_start(
            work,
            first_look=events.first_look,
            replies=events.replies,
            deadlines=events.deadlines,
            owed=events.owed,
        )
    return work.ready


def _refiner_of(project, refiner: str | None) -> str:
    """Who refines in this project: the Manager holding `route` (TRQ7), or a
    lone Manager itself. "" when that cannot be named (a `rite loop` with no
    Manager roles): then there is no ledger to read, and nothing is assumed
    about rounds."""
    from rite_ai.config.managers import routing_owner

    roles = list(project.config.coordination.manager_roles)
    if roles:
        return routing_owner(roles) or ""
    return refiner or ""


def _waiting_detail(work) -> str:
    """Each ticket, and what it waits for: the line the wait repeats."""
    parts = []
    if work.open:
        parts.append(f"in refinement, waiting for your answer: {', '.join(work.open)}")
    if work.waiting:
        parts.append(f"waiting for you since the deadline: {', '.join(work.waiting)}")
    if work.queued:
        parts.append(f"queued for refinement: {', '.join(work.queued)}")
    if work.start:
        parts.append(f"ready to refine, after your next reply: {', '.join(work.start)}")
    for ticket, why in work.needs_person.items():
        parts.append(f"{ticket} needs a person ({why})")
    return (
        "nothing can start: " + "; ".join(parts)
        if parts
        else "nothing can start, and nothing is in refinement"
    )


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
        from rite_ai.sandbox.activity import observe
        from rite_ai.sandbox.questions import WorkerQuestion

        # The same sentence `rite status` and `rite sandbox status` print
        # (dogfood S1: these three views said "busy", "not started" and
        # "idle" about one Worker at one moment).
        seen = observe(name, root, sandbox_status)
        view.evidence.append(seen.describe())
        if seen.exists is None:
            # "Could not ask" is not "no sandbox", and only one of them is
            # safe to dispatch onto.
            view.verdict = "cannot tell — the sandbox could not be asked"
            return view
        if seen.exists:
            # ⚠ A Worker waiting on a question is not "busy" in any sense a
            # reader can act on (dogfood Q2). Still not free: it holds its
            # ticket.
            asked = seen.question
            if isinstance(asked, WorkerQuestion):
                view.evidence.append(f"question: {asked.headline(120)}")
                view.verdict = (
                    f"blocked — waiting on a question since {asked.since()}, "
                    f"unanswered (`rite sandbox status {name}`)"
                )
                return view
            # Not free while any sandbox exists for it: start refuses a
            # second one. The verdict is that decision; the state is the
            # shared sentence in the evidence, not a word of the loop's own
            # ("busy" said working of an agent waiting at its prompt).
            view.verdict = (
                "busy — its sandbox's agent is working"
                if str(seen.status) == "active"
                else "not free — a sandbox exists for it"
            )
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


def watch(
    root: Path,
    *,
    interval: float = 120.0,
    emit=print,
    sleep=None,
    limit: int | None = None,
    **cycle_kwargs,
) -> str:
    """Cycle until told to stop. Returns why it stopped.

    Four ways out, and each says which:

    - the drain signal, from `rite loop stop`. The only one a human asked for;
    - `idle` — the board is empty and nothing is in flight. The one verdict
      that IS a reason to stop (the plan's stop conditions; a bare section
      number here would be read as a SPEC citation by the citation gate,
      which is CGT-1);
    - `unknown` — something could not be established. A loop's default on the
      unknown is to stop, not to keep going on a guess;
    - `limit` cycles, for tests and for a caller that wants a bounded run.

    `saturated`, `blocked` and `closed` all sleep and go round. That is the
    whole reason those verdicts exist as separate words: each one means the
    work is real and somebody else is holding the capacity, the files, or the
    clock, and stopping would turn a busy fleet into a stopped one with a
    queue nobody can see.
    """
    import time as _time

    from rite_ai.loop import lock as loop_lock
    from rite_ai.loop.session import draining

    sleep = _time.sleep if sleep is None else sleep

    # THIS process holds the lock for as long as it runs the loop: the kernel
    # frees it when the process exits, however it exits (`rite_ai.loop.lock`).
    held = loop_lock.acquire(root)
    if isinstance(held, loop_lock.LockBusy):
        emit(
            f"loop: another loop holds this project (pid {held.holder_pid}) "
            "— not starting"
        )
        return "locked"
    if isinstance(held, loop_lock.LockUnavailable):
        emit(f"loop: not starting — {held.reason}")
        return "locked"

    try:
        cycles = 0
        while True:
            reason = draining(root)
            if reason:
                emit(f"loop: draining — {reason}. Taking no new work.")
                return "drained"

            # Before the cycle writes, not after: this is the one command
            # meant to run for days, its output goes to a file through a
            # shell redirect, and nothing trimmed it. The scheduler solved
            # exactly this — copy-and-truncate so the inode the redirect
            # holds open stays valid — so this reuses it rather than
            # inventing a second answer.
            from rite_ai.loop.session import log_path
            from rite_ai.scheduler.logfile import rotate_if_needed

            rotated = rotate_if_needed(log_path(root))
            if rotated:
                emit(f"loop: {rotated}")

            cycle = plan_cycle(root, **cycle_kwargs)
            for line in format_cycle(cycle):
                emit(line)

            if cycle.verdict == UNKNOWN:
                emit("loop: stopping — something could not be established")
                return UNKNOWN
            if cycle.verdict == DEADLOCKED:
                # Not a wait. Sleeping here prints the same thing every two
                # minutes until morning while nothing moves — the silent
                # narrowing this whole layer exists to make audible. The
                # remedy is a human command and it is already on screen.
                emit(
                    "loop: stopping — the work is blocked by holders that "
                    "look dead, and that will not clear on its own. Release "
                    "the claims above, then `rite loop start` again; nothing "
                    "resumes on its own, deliberately."
                )
                return DEADLOCKED
            if cycle.is_reason_to_stop:
                emit("loop: stopping — the queue is empty")
                return IDLE

            cycles += 1
            if limit is not None and cycles >= limit:
                return "limit"
            emit(f"loop: {cycle.verdict}; sleeping {int(interval)}s")
            sleep(interval)
    finally:
        loop_lock.release(held)


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

    if cycle.suspects:
        from rite_ai.claims.suspect import lines as suspect_lines

        lines.append("")
        lines.extend(suspect_lines(cycle.suspects))

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
