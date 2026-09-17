"""Start and stop commands (SPEC §9.10).

`start` assesses state and decides what to do — the orientation decision
table. `stop` performs a handover: release claims, comment on the ticket
and update its label, falling back to the local outbox when the ticket
backend is unreachable or unconfigured.

The handover logic is in `perform_handover` — a single function called by
FOUR triggers per SPEC §9.10's table: `stop` (clean shutdown), the Owner on
a stalled Manager's behalf (heartbeat timeout), the next-in-line Manager on
a stalled Owner's behalf (lease expiry, Phase 2), and a scheduled window
boundary crossing into zero Workers (§2.7.3, built — `scheduler.run_tick`
calls this function when a window drops to 0). The
first three must produce identical board state; the fourth calls the same
function for the same reason but is deliberately NOT held to that
convergence guarantee — it does not wait for in-flight work the way
graceful Owner demotion does (§2.4/§2.7.3 explain the asymmetry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.models import ProjectConfig
from rite_ai.config.parse import load_project
from rite_ai.context import check_integrity
from rite_ai.label import DOT, project_name
from rite_ai.phase import Phase, detect_phase
from rite_ai.pool import probe as probe_pool
from rite_ai.reporting.outbox import OutboxMessage, enqueue, flush_outbox
from rite_ai.state import CorruptStateError
from rite_ai.tickets import BackendError, TicketBackend, create_backend_from_config


@dataclass
class StartResult:
    ok: bool
    message: str
    actions: list[str] = field(default_factory=list)
    # Where the project is and what to do next. Set on every return,
    # including the failures: "no .rite/" and "config errors" are phases too,
    # and they are the ones a newcomer hits first.
    phase: Phase | None = None


@dataclass
class StopResult:
    ok: bool
    message: str
    released_claims: int = 0
    label_failed: bool = False
    queued: bool = False


@dataclass
class HandoverResult:
    released_claims: int = 0
    outbox_path: str = ""
    ticket_commented: bool = False
    label_failed: bool = False
    # Why the board was not written to, when it was not. Queueing is the
    # designed offline behaviour (§9.10 "stop must succeed offline"), but
    # a `stop` that queues because the config will not parse, or because
    # JIRA refused the token, and then prints the same cheerful line as a
    # delivered handover, is the one failure this project keeps finding:
    # reported success, nothing happened.
    queued_reason: str = ""
    # The ticket the handover actually landed on — resolved from the
    # released claims when the caller did not name one, so `stop` can
    # report which ticket it wrote to rather than the empty string it was
    # given.
    ticket: str = ""


def _build_ticket_backend(config: ProjectConfig) -> TicketBackend | BackendError:
    tb = config.ticket_backend
    if tb.type == "none":
        return BackendError("no ticket backend configured")
    return create_backend_from_config(
        tb, board_role="workers", credentials=config.credentials
    )


def _deliver_via_backend(
    backend: TicketBackend, root: Path, label_failures: list[str] | None = None
):
    """Build the `flush_outbox` callback for a live backend — used both to
    deliver a handover immediately and to drain anything left over from a
    prior offline run (SPEC §9.10 "stop must succeed offline"). Handles two
    message kinds: `handover` (comment + label) and `handover-label` (a
    label-only retry queued when a handover's comment succeeded but its
    label failed — see below for why the two are split apart).

    `label_failures`, if given, collects ticket IDs whose label failed on
    this call — `flush_outbox`'s callback contract is `bool`-only, so this
    is the side channel a caller uses to surface the failure to a human
    (§9.10's label IS the assignment mechanism; a silent failure there is
    not acceptable) without changing that contract."""

    def _deliver(msg: OutboxMessage) -> bool:
        if msg.kind == "handover-label":
            ticket = msg.payload.get("ticket") or ""
            labels = msg.payload.get("labels") or []
            unlabels = msg.payload.get("remove") or []
            if not ticket or not (labels or unlabels):
                return True  # malformed — nothing retryable, drop it
            result = backend.label(ticket, labels, remove=unlabels)
            return not isinstance(result, BackendError)

        if msg.kind != "handover":
            return False
        ticket = msg.payload.get("ticket") or ""
        if not ticket:
            return True  # nothing to comment/label — drop, not a delivery failure
        reason = msg.payload.get("reason", "")
        worker = msg.payload.get("worker", "")
        # A board can carry tickets from more than one project, and this
        # comment is read long after the session that queued it is gone —
        # so it names its project. `msg.project`, not the running
        # process's, because a queued message may be flushed by a later
        # run and must still say where it came from.
        origin = msg.project or project_name(root)
        text = f"{DOT} {origin} — rite handover: {reason}" + (
            f" (worker: {worker})" if worker else ""
        )
        result = backend.comment(ticket, text)
        if isinstance(result, BackendError):
            return False

        # `scheduled` is correct on every handover this function can be
        # called for, not just a stall: perform_handover only ever fires
        # for INTERRUPTED work (stop/timeout/lease-expiry/window-boundary,
        # §9.10) — a completed ticket goes through `rite board move`/merge
        # instead and never reaches this path. Returning it to `scheduled`
        # is SPEC §9.10's own label mechanism ("Reassigns or unassigns the
        # ticket through the rite label mechanism"), not an invented policy.
        #
        # The comment above is what actually counts as "delivered" for THIS
        # message — once it lands, retrying the same `handover` message on
        # the next flush would re-post a duplicate. A label failure does
        # not need to risk that: `label()` is idempotent and safely
        # retryable on its own, so it gets queued as its own
        # `handover-label` message instead of being folded back into this
        # one (an earlier version of this function claimed there was "no
        # way to tell" the two failure cases apart without more state than
        # an outbox message carries — false; this is that state).
        #
        # Returning to the pool is TWO label operations, not one. Adding
        # `scheduled` while leaving the departing worker's own label in
        # place produces a ticket that answers BOTH `labels = scheduled`
        # and `labels = <worker>` — and §9.10's orientation table reads
        # the second one first ("in-progress tickets assigned to this
        # Manager's workers → resume"), so the ticket this function just
        # handed back gets picked straight up again by the next session as
        # unfinished work of a worker that no longer exists. §9.10 step 3
        # says "reassigns or UNASSIGNS"; only the add half was built.
        unlabel = [worker] if worker else []
        label_result = backend.label(ticket, ["scheduled"], remove=unlabel)
        if isinstance(label_result, BackendError):
            enqueue(
                root,
                "handover-label",
                {"ticket": ticket, "labels": ["scheduled"], "remove": unlabel},
            )
            if label_failures is not None:
                label_failures.append(ticket)
        return True

    return _deliver


def start(root: Path) -> StartResult:
    """Bring rite up for `root`: assess state, act where acting is safe,
    report where it is not (SPEC §9.10).

    **The line between acting and reporting is what this function is about.**
    `start` performs anything idempotent, local and free — reading the
    handover snapshot, parsing config, validating the schedule, counting
    claims, probing pool liveness — and *reports* anything that would be
    persistent, networked or quota-spending: registering the scheduler,
    refreshing the KB cache, filling the coordinator pool. Each of those has
    its own command, named in the output. An earlier reading of §9.10 had
    `start` perform all three; the spec was amended (§9.10, §2.5.1) rather
    than the code, because a lifecycle command that installs cron entries
    and starts `claude` sessions as a side effect of being run is the wrong
    default, and two of the three are irreversible in the direction that
    matters.

    **The one exception is the outbox flush, and it is deliberate.** It
    writes to the ticket backend, which is neither local nor free. It stays
    because §9.10 already promises that a `stop` performed offline queues its
    handover and flushes it "on next contact" — and `start` is the next
    contact. Nothing else in the package would ever deliver it. This is the
    behaviour that makes §9.10's offline `stop` honest rather than a comment
    written to a file nobody reads.

    **Idempotent, in the precise sense.** Calling `start` twice does not do
    the work twice: the second call finds the outbox empty, the scheduler
    already registered, the same claims. It is not side-effect-free on the
    first call after an offline `stop`, and §9.10's older "is a no-op"
    wording was corrected to say so.
    """
    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return StartResult(
            False,
            "no .rite/ directory — run `rite init` first",
            phase=detect_phase(root),
        )

    actions: list[str] = []

    # SPEC §9.10.1: read the latest handover snapshot BEFORE evaluating
    # orientation — a fresh session reconstructs where the previous one
    # left off from this file, not from memory it doesn't have. A missing
    # snapshot is not an error (nothing recorded yet); proceed as normal.
    from rite_ai.handover import read_snapshot

    snapshot = read_snapshot(root)
    if snapshot is not None and snapshot.unreadable:
        # Not silence. This line used to vanish entirely for an unparseable
        # snapshot and `start` went on to print `ready` — the orientation
        # command reporting that a session is oriented, having failed to
        # read the one file §9.10.1 tells it to read first.
        actions.append(
            f"handover snapshot: UNREADABLE — {snapshot.unreadable}. "
            "The previous session's state is in that file and cannot be "
            "parsed; do not treat this as a clean start."
        )
    elif snapshot is not None:
        # With its age. A fresh session has no idea what time the previous
        # one stopped, which is the entire reason it is reading this.
        actions.append(
            f"handover snapshot: ticket={snapshot.ticket or '(none)'}, "
            f"next step: {snapshot.next_step or '(none)'} "
            f"[recorded {snapshot.describe_age()}]"
        )
        if snapshot.blockers:
            actions.append(f"open blockers: {', '.join(snapshot.blockers)}")

    project = load_project(root)
    if isinstance(project, list):
        errors = "; ".join(f"{e.file}: {e.message}" for e in project)
        return StartResult(
            False, f"config errors: {errors}", phase=detect_phase(root)
        )

    actions.append(f"project '{project.brief.name}' loaded")

    issues = check_integrity(root)
    if issues:
        for issue in issues:
            actions.append(f"context warning: {issue.detail}")

    # SPEC §9.10 setup phase, "verify `.rite/` is healthy". The parenthetical
    # "(runs `rite doctor` internally)" is not followed literally and the spec
    # no longer asks for it: `doctor` is a presentation command that echoes
    # forty-odd rows, and a `start` that reprints all of them buries its own
    # orientation output. What `start` owes the caller is the subset it is
    # about to be governed by, with a pointer to `doctor` for the rest.
    #
    # The schedule is that subset. `start` brings a project up under whatever
    # `schedule.windows` says; a schedule with a coverage gap or a window over
    # `sandbox.max_concurrent_workers` is a problem `start` should surface at
    # the moment it starts mattering, not one the user discovers when a
    # window boundary behaves unexpectedly hours later.
    from rite_ai.schedule import validate_schedule

    schedule_problems = validate_schedule(
        project.config.schedule, project.config.sandbox.max_concurrent_workers
    )
    for problem in schedule_problems:
        actions.append(f"schedule: {problem}")
    if schedule_problems:
        actions.append("run `rite doctor` for the full health check")

    claims_path = rite_dir / "claims.json"
    if claims_path.exists():
        ledger = ClaimsLedger(claims_path)
        active = ledger.list_claims()
        if active:
            actions.append(f"{len(active)} active claim(s) found")

    if project.workers:
        actions.append(f"{len(project.workers)} worker(s) registered")

    # SPEC §9.10 setup phase, "start scheduled tasks". REPORTED, NOT
    # PERFORMED, and the spec was amended to match rather than the other way
    # round. Registering the tick is a cron/launchd entry: a standing change
    # to the machine that outlives the process and survives a reboot, which
    # is why `rite_ai.scheduler`'s own contract is that a human runs
    # `scheduler install` the way they would run `crontab -e`. Performing it
    # as a side effect of `start` would also break §9.10's idempotence in the
    # direction that matters — the second `start` would silently re-register.
    #
    # What `start` genuinely owes the caller is the answer to "is the tick
    # actually going to run?", because every mechanism `start` has just
    # reported on (the watchdog, window boundaries, the handover snapshot's
    # freshness) depends on it and none of them says so when it is not.
    from rite_ai.scheduler import health as scheduler_health

    sched = scheduler_health(root)
    if not sched.installed:
        actions.append(
            f"scheduler ({sched.backend}): NOT INSTALLED — the watchdog and "
            "schedule window boundaries will not run. `rite scheduler install`"
        )
    elif sched.overdue:
        actions.append(
            f"scheduler ({sched.backend}): installed but overdue — "
            "`rite scheduler status` for why"
        )
    else:
        actions.append(f"scheduler ({sched.backend}): installed")

    # SPEC §9.10 setup phase, "populate cache". Also reported, not performed,
    # and here the spec contradicted itself rather than merely over-reaching:
    # §8.7 already said "a fresh clone gets no cache files — run `rite kb
    # refresh` after cloning to populate them", putting the refresh in the
    # user's hands, while §9.10 had `start` do it silently. `kb refresh`
    # fetches every registered URL over the network; a lifecycle command that
    # makes outbound requests as a side effect can hang, fail, or surprise a
    # user who ran `start` offline. §9.10 now defers to §8.7.
    kb_cache = rite_dir / "kb" / ".cache"
    has_cache = kb_cache.is_dir() and any(kb_cache.glob("*.md"))
    if (rite_dir / "kb").is_dir() and not has_cache:
        actions.append("kb cache empty — run `rite kb refresh`")

    # SPEC §9.10 setup phase, the third quota-spending step. Reported, not
    # performed — and the report is the half that was missing. §2.5.1 was
    # amended (D-50) so that nothing tops the pool up on bring-up, which is
    # right: every pooled slot runs `claude`, and spent quota is the one
    # damage no cleanup reverses (§5.1.1). But the DoD (P1.16) promises the
    # *replacement* for the top-up — that `start` reports coordinator-pool
    # depth and names its command — and only the "does not fill" half was
    # built. A `start` silent about the pool tells a Manager it has warm
    # capacity when it has none.
    #
    # `probe` is safe to call here on both counts §9.10's rule cares about.
    # It is free and local: a mechanical OS-level tmux liveness check, zero
    # tokens, no prompt reaching any session (§2.5.2/§2.5.3). And it does
    # not create state — it writes `pool.json` only when slots already
    # exist or the file already does, so a `start` in a project that never
    # filled a pool leaves no pool state behind. That is not incidental:
    # it is what lets this report coexist with the rule that `start` must
    # never spend quota, and it is asserted by its own probe.
    #
    # The wording is `pool status`'s own, deliberately. A second vocabulary
    # for the same shortfall is how two commands come to disagree about it.
    #
    # `probe` REFUSES an unreadable `pool.json` rather than reporting it as
    # an empty pool, and that refusal is right where it was written: `fill`
    # tops up to target from what it believes is live, so reading a corrupt
    # file as empty would start a full set of sessions on top of the ones
    # already running. It is the wrong answer here. Propagated out of
    # `start`, it aborts the bring-up before the outbox flush below — the
    # delivery §9.10 promises an offline `stop`, and the one write nothing
    # else in the package performs. An unreadable pool file is a reason to
    # lose the pool LINE, not the orientation and not the handover.
    try:
        pool_status = probe_pool(root, project.config.pool)
    except CorruptStateError as exc:
        actions.append(
            f"coordinator pool: state unreadable — {exc}. Depth unknown; "
            "`rite pool status` for the same message with recovery steps."
        )
    else:
        if pool_status.warn:
            actions.append(pool_status.message)
        if pool_status.stale:
            actions.append(
                f"pool slots not responding: {', '.join(pool_status.stale)} — "
                "`rite pool status`"
            )

    pending = root / ".rite" / "outbox"
    if pending.is_dir():
        count = len(list(pending.glob("*.json")))
        if count:
            backend = _build_ticket_backend(project.config)
            if isinstance(backend, BackendError):
                actions.append(
                    f"{count} pending outbox message(s) — "
                    f"cannot flush: {backend.message}"
                )
            else:
                label_failures: list[str] = []
                delivered = flush_outbox(
                    root, _deliver_via_backend(backend, root, label_failures)
                )
                remaining = count - delivered
                if remaining:
                    actions.append(
                        f"flushed {delivered}/{count} pending outbox "
                        f"message(s), {remaining} still queued"
                    )
                else:
                    actions.append(f"flushed {delivered} pending outbox message(s)")
                if label_failures:
                    actions.append(
                        "label update failed for "
                        + ", ".join(label_failures)
                        + " — retry queued, or fix by hand with `rite board label`"
                    )

    # §9.10's orientation table, finally evaluated: an inventory with no
    # direction told someone who had just run `init` nothing about what to do
    # next. Reported, never acted on, like everything else here.
    return StartResult(True, "ready", actions=actions, phase=detect_phase(root))


def perform_handover(
    root: Path,
    worker: str | None = None,
    reason: str = "clean shutdown",
    ticket: str = "",
) -> HandoverResult:
    """Single handover function — called by `stop` (clean shutdown), by the
    Owner on a stalled Manager's behalf (heartbeat timeout), by the
    next-in-line Manager on a stalled Owner's behalf (lease expiry, Phase 2),
    and by a scheduled window boundary crossing into zero Workers (§2.7.3,
    built — `scheduler.run_tick`) — the first three must produce identical
    board state (SPEC §9.10); the fourth is exempt from that guarantee by
    design."""
    result = HandoverResult()
    rite_dir = root / ".rite"

    about_to_release: list = []
    claims_path = rite_dir / "claims.json"
    if claims_path.exists():
        ledger = ClaimsLedger(claims_path)
        # Read the claims BEFORE releasing them — this is the only place
        # the ticket ID lives when a caller doesn't pass one explicitly
        # (§9.10 step 1: "the ACTIVE ticket"). Every production caller
        # (`stop_cmd`) resolves it this way; an explicit `ticket` argument
        # still wins when given (e.g. a future Owner acting on a stalled
        # Manager's behalf, which may already know the ticket without
        # reading that Manager's claims file).
        about_to_release = ledger.claims_for(worker) if worker else ledger.list_claims()
        # A handover releases claims, and a release that the fleet never
        # hears about leaves those paths blocked on every other machine —
        # they expire on a lapsed heartbeat, and a machine that merely
        # handed over is still beating.
        from rite_ai.coordination.identity import claims_channel

        layer, machine = claims_channel(root)
        if worker:
            result.released_claims = ledger.release(
                worker, layer=layer, machine=machine
            )
        else:
            for claim in about_to_release:
                result.released_claims += ledger.release(
                    claim.worker, layer=layer, machine=machine
                )

    if not ticket:
        for claim in about_to_release:
            if claim.ticket:
                ticket = claim.ticket
                break

    # A FINAL handover snapshot, so the continuous snapshot (§9.10.1,
    # written every watchdog cycle) and this transition record never
    # disagree at the moment of transition — the snapshot answers "what's
    # the state," this function answers "why did it change."
    from rite_ai.handover import write_snapshot

    write_snapshot(
        root, ticket=ticket, progress=f"handover: {reason}", worker=worker or ""
    )

    payload = {
        "worker": worker or "",
        "reason": reason,
        "ticket": ticket,
        "released_claims": result.released_claims,
    }

    result.ticket = ticket
    delivered = False
    if not ticket:
        # Nothing to queue. `_deliver_via_backend` DROPS a handover with
        # no ticket ("nothing to comment/label"), so enqueueing one only
        # writes a file that the next `rite start` deletes unread — and
        # `stop` then reported "board NOT updated" about a board there
        # was never anything to update.
        result.queued_reason = ""
        return result
    else:
        project = load_project(root)
        if isinstance(project, list):
            result.queued_reason = "config did not parse: " + "; ".join(
                f"{e.file}: {e.message}" for e in project
            )
        else:
            backend = _build_ticket_backend(project.config)
            if isinstance(backend, BackendError):
                result.queued_reason = backend.message
            else:
                label_failures: list[str] = []
                deliver = _deliver_via_backend(backend, root, label_failures)
                delivered = deliver(
                    OutboxMessage(
                        kind="handover", payload=payload, timestamp=0, path=Path()
                    )
                )
                result.ticket_commented = delivered
                result.label_failed = bool(label_failures)
                if not delivered:
                    result.queued_reason = (
                        f"the ticket backend refused the handover comment on {ticket}"
                    )

    if not delivered:
        # No ticket to comment on, no backend configured, or the backend
        # call failed (BackendError) — queue locally and let `start()`'s
        # outbox flush retry once the backend is reachable (SPEC §9.10
        # "stop must succeed offline").
        path = enqueue(root, "handover", payload)
        result.outbox_path = str(path)

    return result


def stop(
    root: Path,
    worker: str | None = None,
    reason: str = "clean shutdown",
    ticket: str = "",
) -> StopResult:
    rite_dir = root / ".rite"
    if not rite_dir.is_dir():
        return StopResult(False, "no .rite/ directory")

    handover = perform_handover(root, worker, reason, ticket)

    message = f"stopped ({reason}), released {handover.released_claims} claim(s)"
    if handover.ticket_commented:
        message += f", handover posted to {handover.ticket}"
    elif handover.outbox_path:
        # Say it out loud. The board was NOT updated, and the next person
        # to read it — or the next session running §9.10's orientation
        # table — sees whatever it said before this shutdown.
        message += (
            f" — board NOT updated: {handover.queued_reason or 'backend unreachable'}"
            f"; handover queued at {handover.outbox_path}, delivered on the next "
            "`rite start`"
        )
    if handover.label_failed:
        message += " (WARNING: board label update failed — retry queued)"

    return StopResult(
        True,
        message,
        released_claims=handover.released_claims,
        label_failed=handover.label_failed,
        queued=bool(handover.outbox_path),
    )
