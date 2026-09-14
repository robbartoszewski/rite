"""The watchdog — cheap liveness checks, no LLM in the loop (SPEC §3.5,
D-37).

Checking "is anything stalled" used to mean waking a full Dispatch session,
which re-reads its entire accumulated context to answer a question that
needs no judgement — measured at ~484K tokens per wake-up, most of which
concluded nothing had changed. This module is the replacement: a plain
function (meant to be run every ~5 minutes by whatever scheduler `rite
start` sets up — cron, launchd, a loop — the mechanism is deliberately not
this module's concern) that inspects heartbeat files and the outbox and
returns a verdict. No LLM call anywhere in this file. The Manager session
is invoked only when `WatchdogResult.needs_attention` is true.

Three checks are built: a stalled worker (missed heartbeats past
`heartbeat.stall_threshold` × `heartbeat.interval_minutes`), a blocker
recorded in the outbox, and a worker that is **beating normally and
blocked** — an open blocker in its own handover snapshot.

⚠ **The third check exists because the first two could not see the
commonest real failure.** Measured 2026-09-11 against a live sandboxed
worker: it heartbeated on schedule and wrote
`--blocker "QUESTION: should DEF-9 drop the legacy column or keep it
nullable?"`, and `rite watchdog` answered `ok — nothing needs attention`
with exit 0. The stall check only sees silence, and the outbox check only
sees what `scheduler-tick` enqueues — nothing a Worker writes ever
reaches the outbox, because no command lets a Worker enqueue anything.
A Worker's question lands in its handover snapshot and nowhere else, so
that is where this module now looks.

**The two conditions are not the same fact and must not read as one.**
A missed heartbeat means *probably dead* — nobody is there, and what it
was holding may need force-releasing. An open blocker means *definitely
alive and definitely stuck* — somebody is waiting for an answer that a
human can give right now. They get different wording and different exit
codes (see `rite watchdog`'s `--help`) because a Manager scripting on
this has to tell them apart: one is an investigation, the other is a
reply.

SPEC §3.5 also names "a decision queued with no response" — there is no
decision-queue mechanism anywhere in this codebase yet (that's
Manager<->Owner territory, Phase 2), so this is narrower than the full
spec text on purpose rather than inventing a mechanism nothing else uses;
extend this module the day a decision queue exists, not before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.parse import load_project
from rite_ai.duration import format_duration
from rite_ai.reporting.heartbeat import StallReport, detect_stalls
from rite_ai.reporting.outbox import OutboxMessage, list_pending
from rite_ai.state import CorruptStateError

# Outbox kinds that represent something a human/Manager needs to act on,
# as opposed to `handover` (routine, drained by `start`'s own flush) or
# `handover-label` (a retry, same). SPEC §9.10's outbox docstring names
# "blocker" as a real kind; "stall" is included too since a stall can be
# recorded there before this module's own heartbeat check would catch it
# (e.g. a Worker that reported its own stall rather than going silent).
ATTENTION_KINDS = ("blocker", "stall")


@dataclass
class BlockedWorker:
    """A worker that is alive and waiting on an answer.

    Distinct from `StallReport` in kind, not degree: a stall is inferred
    from silence, this is *stated* by the worker itself in its handover
    snapshot. A worker can be both (it asked, then died), and the two are
    reported separately when it is."""

    worker: str
    ticket: str
    blockers: list[str]
    seconds_since: float


@dataclass
class WatchdogResult:
    needs_attention: bool
    reasons: list[str] = field(default_factory=list)
    stalled: list[StallReport] = field(default_factory=list)
    blockers: list[OutboxMessage] = field(default_factory=list)
    blocked: list[BlockedWorker] = field(default_factory=list)
    """Workers beating normally with an open blocker in their handover
    snapshot — the answerable half of "needs attention"."""
    unreadable: list[tuple[str, str, float]] = field(default_factory=list)
    """`(worker, why, seconds_silent)` for each handover snapshot that
    exists and will not parse.

    Structured, like every other category here, rather than reaching the
    caller only as prose in `reasons`. The distinction this field carries
    is the one `_load` exists to preserve — a file that cannot be READ is
    not a worker with nothing to say — and a caller that wants to act on
    it differently (or a test that wants to assert it happened) should
    not have to grep an English sentence to find out."""
    unwatched: list[str] = field(default_factory=list)
    """Workers rite holds state for but does not watch — see
    `_unwatched_workers`. Reported separately from `stalled` because the
    condition is different in kind: a stall is a worker that went quiet,
    this is a worker nothing would notice going quiet."""


def run_watchdog_check(root: Path) -> WatchdogResult:
    """The whole check, mechanical and zero-token. Returns a verdict; does
    not itself notify or start anything — that's the caller's job (the
    scheduler that runs this, and whatever it does when `needs_attention`
    is true)."""
    project = load_project(root)
    if isinstance(project, list):
        # Config errors are themselves worth surfacing — a Manager can't
        # even orient itself against a broken config, which is exactly
        # the kind of thing that needs judgement, not silence.
        return WatchdogResult(
            needs_attention=True,
            reasons=[f"config error: {e.file}: {e.message}" for e in project],
        )

    workers = [w.name for w in project.workers]
    threshold_seconds = (
        project.config.heartbeat.interval_minutes
        * 60
        * project.config.heartbeat.stall_threshold
    )
    stalled = detect_stalls(root, workers, threshold_seconds=threshold_seconds)
    unwatched = _unwatched_workers(root, workers)

    blockers = [msg for msg in list_pending(root) if msg.kind in ATTENTION_KINDS]

    reasons: list[str] = []
    for name, evidence in unwatched:
        reasons.append(
            f"worker '{name}' is not registered ({evidence}) — nothing here "
            f"watches it, so if its session dies no stall will ever be "
            f"reported. Register it with `rite add worker {name}`, or clear "
            f"the leftover state with `rite release --worker {name}`"
        )
    for s in stalled:
        ticket_note = f" (ticket {s.ticket})" if s.ticket else ""
        if s.seconds_silent == float("inf"):
            when = "no heartbeat ever recorded"
        else:
            # Words, not raw seconds. This line is read unattended, hours
            # after the fact, and "3608s" is the same fact as "1h 0m" only
            # if you are willing to do the arithmetic.
            when = f"{format_duration(s.seconds_silent)} since last heartbeat"
        reasons.append(f"worker '{s.worker}' stalled — {when}{ticket_note}")
    for b in blockers:
        detail = b.payload.get("detail") or b.payload.get("reason") or ""
        reasons.append(f"{b.kind} in outbox" + (f": {detail}" if detail else ""))
    unreadable = _unreadable_handovers(root)
    for name, why, silent in unreadable:
        reasons.append(
            f"worker '{name}' — handover snapshot UNREADABLE ({why}), last "
            f"written {format_duration(silent)} ago. Whether it is waiting "
            "on anything cannot be read; repair or remove the file"
        )

    # Last, and worded to be unmistakable against the stall lines above:
    # these workers are ALIVE. Reading "blocked" as "stalled" sends a
    # Manager to force-release the claims of a session that is sitting
    # there waiting for an answer.
    blocked = _blocked_workers(root)
    stalled_names = {s.worker for s in stalled}
    for bw in blocked:
        ticket_note = f" (ticket {bw.ticket})" if bw.ticket else ""
        also = (
            " — AND it has stopped beating, so the question may be moot"
            if bw.worker in stalled_names
            else ""
        )
        age = (
            f", asked {format_duration(bw.seconds_since)} ago"
            if bw.seconds_since > 0
            else ""
        )
        detail = "; ".join(bw.blockers)
        reasons.append(
            f"worker '{bw.worker}' is BLOCKED and waiting{ticket_note}{age}"
            f"{also}: {detail}"
        )

    return WatchdogResult(
        needs_attention=bool(reasons),
        reasons=reasons,
        stalled=stalled,
        blockers=blockers,
        blocked=blocked,
        unreadable=unreadable,
        unwatched=[name for name, _ in unwatched],
    )


def _blocked_workers(root: Path) -> list[BlockedWorker]:
    """Workers whose own handover snapshot carries an open blocker.

    Reads `.rite/handover/*.json` — one snapshot per session, overwritten
    whole on each `rite handover write`. So a blocker disappears the
    moment the worker writes a snapshot without it, which is what makes
    this safe to report every cycle: there is no separate "resolve" step
    to forget, and no queue to drain.

    A snapshot left behind by a session that died holding a blocker keeps
    reporting, deliberately — the question really is still unanswered.
    When that worker is also stalled, both lines appear and the blocked
    line says so.
    """
    import time

    from rite_ai.handover import read_snapshots

    now = time.time()
    out: list[BlockedWorker] = []
    for snap in read_snapshots(root):
        if snap.unreadable:
            # Reported separately, by `_unreadable_handovers`. NOT as a
            # blocked worker: this module's own reasoning is that
            # "blocked" must stay unmistakable, because reading it as
            # "stalled" sends a Manager to force-release a live session's
            # claims. An unreadable file does not say the worker is
            # waiting — it says nobody can tell.
            continue
        open_blockers = [b for b in snap.blockers if str(b).strip()]
        if not open_blockers:
            continue
        out.append(
            BlockedWorker(
                worker=snap.worker or "_owner",
                ticket=snap.ticket,
                blockers=open_blockers,
                seconds_since=max(0.0, now - snap.timestamp) if snap.timestamp else 0.0,
            )
        )
    return sorted(out, key=lambda b: b.worker)


def _unreadable_handovers(root: Path) -> list[tuple[str, str, float]]:
    """`(worker, why, seconds since the file was last written)` for every
    handover snapshot that cannot be parsed.

    These used to vanish. `rite_ai.handover._load` returned `None` for an
    unparseable file and `read_snapshots` dropped it, so a truncated
    `handover/alpha.json` removed that worker from this check entirely —
    the one mechanism that notices an unattended worker going quiet about
    precisely the worker whose recorded state had been damaged. It is a
    reason in its own right, not a blocker and not a stall.
    """
    import time

    from rite_ai.handover import read_snapshots

    now = time.time()
    out: list[tuple[str, str, float]] = []
    for snap in read_snapshots(root):
        if not snap.unreadable:
            continue
        out.append(
            (
                snap.worker or "_owner",
                snap.unreadable,
                max(0.0, now - snap.timestamp) if snap.timestamp else 0.0,
            )
        )
    return sorted(out)


def _pool_slot_workers(root: Path) -> set[str]:
    """Pool slots are workers too, registered somewhere else.

    A pooled coordinator claims paths under its slot name — that is what
    makes `rite pool archive` able to release what a dead session was
    holding — and no slot ever gets a `workers/<name>/worker.yml`. Its
    register is `.rite/pool.json`, and `rite pool status`/`archive` are
    what watch it, on their own liveness probe. Counting slots as
    unwatched would put a standing false alarm on every project that uses
    the pool, which is the setup §2.5 recommends."""
    from rite_ai.pool import _read_state

    try:
        return {slot.worker or slot.name for slot in _read_state(root)}
    except CorruptStateError:
        return set()


def _unwatched_workers(root: Path, registered: list[str]) -> list[tuple[str, str]]:
    """Workers that rite is holding state FOR but is not watching.

    `detect_stalls` only looks at workers with a `workers/<name>/worker.yml`
    manifest, and every other command takes `--worker <anything>` without
    comment. So a project where nobody ran `rite add worker` accepts
    claims and heartbeats for a name, reports each of them as recorded,
    and then answers "ok — nothing needs attention" however long that
    worker has been silent. Measured on a project with one claim and a
    heartbeat 100 minutes stale against a 2-minute threshold: `rite
    watchdog` exited 0 saying nothing needed attention, while `rite
    status` printed "no workers" and "claims (1): ghost" two lines apart.

    The stall check cannot simply switch to reading heartbeat files
    instead — a file left behind by a worker that was properly retired
    would then stall forever. So the manifest stays the register, and
    state belonging to nobody in it is reported as its own condition: the
    watchdog saying what it is NOT watching.
    """
    known = set(registered) | _pool_slot_workers(root)
    found: dict[str, list[str]] = {}

    hb_dir = root / ".rite" / "heartbeats"
    if hb_dir.is_dir():
        for path in sorted(hb_dir.glob("*.json")):
            name = path.stem
            if name not in known:
                found.setdefault(name, []).append("has a heartbeat")

    claims_path = root / ".rite" / "claims.json"
    if claims_path.is_file():
        try:
            claims = ClaimsLedger(claims_path).list_claims()
        except CorruptStateError:
            # A ledger that will not parse is somebody else's error to
            # report — `rite status` and `rite claim` both raise on it.
            # It must not turn this check into a crash.
            claims = []
        for claim in claims:
            if claim.worker and claim.worker not in known:
                found.setdefault(claim.worker, []).append("holds a claim")

    return [
        (name, ", ".join(dict.fromkeys(evidence)))
        for name, evidence in sorted(found.items())
    ]
