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
from rite_ai.handback import Handback
from rite_ai.handback import read_all as read_handbacks
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
    handed_back: list[Handback] = field(default_factory=list)
    """Workers that finished and said so (`rite done`). NOT a fault, and
    never also in `stalled` — see `run_watchdog_check`."""
    handed_back_reasons: list[str] = field(default_factory=list)
    """The entries of `reasons` about a handback that needs nothing yet.

    ⚠ **NOT every handback reason.** A handback past `NOT_INTEGRATED_AFTER`
    is left out of this list on purpose, so the scheduler does NOT filter it
    out of the tick: finished work nobody has taken up for half a day is a
    standing condition somebody must act on, and it is treated exactly as a
    stall is — said every cycle until it stops being true. The terminating
    check caught the first version putting it in here, where the one reader
    that would have carried it unattended subtracted it again, so the
    escalation existed only for a hand-run `rite watchdog`.

    Carried rather than recomputed or matched by substring. The scheduler
    has to tell them apart from the rest to keep a standing condition out of
    its per-tick log, and deciding that by `"handed back" in reason` is the
    matcher-fires-on-prose mistake this codebase has already paid for; two
    calls to a shared formatter would compare unequal the moment one of them
    crossed the `NOT_INTEGRATED_AFTER` boundary."""


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
    # ⚠ **A WORKER THAT HANDED BACK IS NOT STALLED, and this is where the two
    # stop being the same observation.** Measured in the dogfood of
    # 2026-10-03: a Worker finished its ticket, stopped beating because it had
    # nothing left to do, and was reported "stalled — 42m since last
    # heartbeat". The Manager read that and restarted it. Silence after a
    # handback is the expected state, so the handback is subtracted from the
    # stall set rather than reported alongside it: a Worker in both lists is
    # a Worker a reader has to adjudicate, and the dogfood shows which way
    # that goes.
    #
    # Subtracted and not merely annotated, deliberately. `scheduler`'s
    # `_stall_records` turns `stalled` into standing outbox blockers and
    # `reporting.status` prints it as STALLED; both read the list, not the
    # prose beside it.
    # ⚠ An UNREADABLE handback counts here too, and that is a judgement, not
    # an oversight. The two rules this codebase already holds pull opposite
    # ways — "cannot tell is never stalled" (D-58, §3.4) against
    # `not_started`'s "a missed stall is worse than a false one" — and the
    # file existing at all is evidence the Worker reached the step where it
    # says it is done. So it suppresses the stall and is reported as its own
    # reason, loudly, saying that rite could not read it: a person looks,
    # and nothing restarts a Worker on the assumption it is hung.
    handed_back = sorted(read_handbacks(root, workers).values(), key=lambda h: h.worker)
    done = {h.worker for h in handed_back}
    # ⚠ **A LOCAL WORKER'S HEARTBEAT IS NOT A LIVENESS SIGNAL (SCRUM-72
    # §3.3), so it must not be read as one.** A local Worker never beats at
    # all: it is started IDLE and the staged pipeline places one approved
    # subtask at a time into its sandbox, so it is silent while a plan is
    # being authored, while a reviewer is being waited on, between subtasks,
    # and while the recomposition verify runs. The stall check can therefore
    # only ever produce a FALSE positive for it — and the dogfood already
    # showed what a Manager does with a line that says "stalled": it restarts
    # the Worker, which here would restart a sandbox the pipeline is placing
    # turns into.
    #
    # Subtracted the way a handback is, and for the same reason: `scheduler`
    # turns `stalled` into standing outbox blockers and `reporting.status`
    # prints it as STALLED, and both read the list rather than the prose
    # beside it. What says a local Worker is alive is its PIPELINE — a plan
    # step pending, or one running — and the supervisor's own spin guard is
    # what notices a pipeline that has stopped advancing.
    driven = _driven_by_the_staged_pipeline(root, workers)
    stalled = [
        s
        for s in detect_stalls(root, workers, threshold_seconds=threshold_seconds)
        if s.worker not in done and s.worker not in driven
    ]
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
        if not s.known:
            # Never "no heartbeat ever recorded" for a file that could not
            # be opened: that is the loudest thing this line says, and it
            # would be asserting silence nobody observed.
            when = s.detail or "heartbeat could not be read"
        elif s.seconds_silent == float("inf"):
            when = "no heartbeat ever recorded"
        else:
            # Words, not raw seconds. This line is read unattended, hours
            # after the fact, and "3608s" is the same fact as "1h 0m" only
            # if you are willing to do the arithmetic.
            when = f"{format_duration(s.seconds_silent)} since last heartbeat"
        verb = "stalled" if s.known else "cannot be checked"
        reasons.append(f"worker '{s.worker}' {verb} — {when}{ticket_note}")
    handback_reasons: list[str] = []
    for h in handed_back:
        # ⚠ The words "do NOT restart" are in the line on purpose. This text
        # is what a Manager reads on its own polling cadence, and in the
        # dogfood the Manager restarted a finished Worker because the only
        # line about it said "stalled". Saying what the state is was not
        # enough; the line says what not to do with it.
        #
        # ⚠ **And the unreadable case gets its OWN sentence, because the
        # confident one is not true of it.** Review caught the first version
        # appending "it is FREE and NOT hung … Integrate the work" to a
        # record rite had just said it could not read — three claims nobody
        # established, on the strength of a corrupt file. The stall is still
        # suppressed (the file existing is evidence the Worker reached the
        # step where it says it is done), and that is all that is claimed.
        if not h.done:
            # Not added to `handback_reasons` either: a record nobody can
            # parse is a fault, and the scheduler must carry it to whoever
            # reads the log unattended rather than filtering it as routine.
            reasons.append(
                f"worker '{h.worker}' {h.describe()} — so rite cannot say "
                "whether it finished. It is NOT being reported as stalled, "
                "because the record exists; do not restart it on the "
                "assumption it is hung, and do not treat this as nothing "
                "having been said. Read the file, and look at its branch"
            )
            continue
        waited = (
            f" It has been waiting {format_duration(h.age_seconds())} and "
            "nobody has taken it up."
            if h.not_integrated()
            else ""
        )
        line = (
            f"worker '{h.worker}' {h.describe()} — it is FREE and NOT hung, "
            "so do NOT restart it and do not force-release its claims. Its "
            f"silence from here on is expected.{waited} Integrate the work, "
            "or give that worker the next ticket"
        )
        reasons.append(line)
        if not h.not_integrated():
            handback_reasons.append(line)
    for worker, where in sorted(driven.items()):
        # Said, not silent: a reader looking for a Worker it has not heard
        # from must find the reason, and "it does not beat" is the reason.
        # Not in `needs_attention`'s sense of a fault — a pipeline that has
        # stopped advancing is the supervisor's spin guard to notice, not
        # this check's — but a line all the same.
        reasons.append(
            f"worker '{worker}' runs a local engine and is driven by the "
            f"staged pipeline ({where}) — it does NOT send heartbeats and its "
            "silence is the expected state. Do not restart it and do not "
            "force-release its claims: its Manager's supervise loop advances "
            "it one stage per cycle"
        )
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
        handed_back=handed_back,
        handed_back_reasons=handback_reasons,
    )


def _driven_by_the_staged_pipeline(root: Path, workers: list[str]) -> dict[str, str]:
    """`{worker: where its ticket is}` for every LOCAL Worker this project has
    whose ticket is in the staged pipeline (SCRUM-72 §3.3).

    A Worker is in here when all three hold: its manifest says `local:`, rite
    has a record of which ticket it was started on, and that ticket has a
    persisted pipeline stage. Anything rite cannot read leaves the Worker OUT
    — the stall check then applies as before, which is the direction that
    reports too much rather than too little (`not_started`'s rule: a missed
    stall is worse than a false one).
    """
    found: dict[str, str] = {}
    for worker in workers:
        try:
            from rite_ai.local import plan_state
            from rite_ai.local import stage as st
            from rite_ai.publishing import record
            from rite_ai.sandbox import worker_manifest

            if not bool(getattr(worker_manifest(root, worker), "is_local", False)):
                continue
            ticket = getattr(record.read(root, worker), "ticket", "")
            if not ticket:
                continue
            got = st.read(plan_state.layer(root), ticket)
            if got.unavailable or got.error or got.record is None:
                continue
            found[worker] = f"{ticket} at stage {got.stage}"
        except Exception:  # noqa: BLE001 - cannot tell: the stall check stands
            continue
    return found


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
