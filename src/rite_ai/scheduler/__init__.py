"""The scheduler — what actually calls the watchdog and handles scheduled
window boundaries, unattended (SPEC §3.5, §2.7.3, §9.10's "start" setup
phase: "Start scheduled tasks ... checking the schedule for window
transitions").

Two halves, deliberately separate modules composed here:

1. **OS-level install/uninstall** (`install`/`uninstall`/`status`) — a
   cron entry or launchd agent that runs `rite scheduler-tick` in a
   project directory on a cadence. This is a STANDING, PERSISTENT,
   unattended configuration change to the user's own machine — it is
   built and fully tested here (with every OS call mocked in tests), but
   is NEVER run against a real crontab/launchd registration by anything
   in this codebase's own test suite or by any automated process; a human
   runs `rite scheduler install` themselves, the same way they would run
   `crontab -e` themselves.
2. **The tick itself** (`run_tick`) — the mechanical, no-LLM logic that
   actually runs each cycle: the watchdog check (§3.5), and the schedule
   window-boundary check (§2.7.3, D-46) that hands over any active
   Worker when the schedule drops to zero. Both are things a plain
   script can do; nothing here spawns or requires a Claude session.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.config.parse import load_project
from rite_ai.duration import format_duration as _format_duration
from rite_ai.reporting.outbox import enqueue, list_pending
from rite_ai.schedule import current_minute_of_day, workers_at
from rite_ai.scheduler import lock
from rite_ai.scheduler.logfile import KEEP, log_path, rotate_if_needed
from rite_ai.state import write_atomic
from rite_ai.watchdog import WatchdogResult, run_watchdog_check

STATE_FILENAME = "schedule-state.json"
LAST_TICK_FILENAME = "scheduler-last-tick"

# How far past the expected cadence a gap has to be before it is worth
# saying out loud. Three intervals rather than two so ordinary jitter —
# launchd coalescing timers for power, a tick that ran long — stays quiet,
# with a floor so a fast cadence does not make every hiccup an event.
GAP_FACTOR = 3
GAP_FLOOR_SECONDS = 10 * 60


def _last_tick_path(root: Path) -> Path:
    return root / ".rite" / LAST_TICK_FILENAME


def _read_last_tick(root: Path) -> float | None:
    path = _last_tick_path(root)
    if not path.is_file():
        return None
    try:
        return float(path.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_last_tick(root: Path, when: float) -> None:
    path = _last_tick_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, str(when))


def _expected_interval_seconds(root: Path) -> int:
    """The cadence the tick believes it runs at, from config.

    Best-effort by design: the OS-level interval is set at install time
    and can differ from `watchdog.interval_minutes`, and this only feeds a
    threshold for whether a gap is worth mentioning — so a wrong answer
    changes how chatty the log is, never whether the tick works."""
    from rite_ai.config.parse import parse_config

    config = parse_config(root / ".rite" / "config.yaml")
    minutes = getattr(getattr(config, "watchdog", None), "interval_minutes", 5)
    try:
        return max(int(minutes), 1) * 60
    except (TypeError, ValueError):
        return 5 * 60


def _gap_message(root: Path, now: float) -> str | None:
    """Say so when the scheduler was not running for a while.

    The morning-after question about an unattended log is "did this keep
    running all night?", and answering it by eye means diffing timestamps
    down hundreds of identical lines. The tick knows when it last ran, so
    it can just say. A sleeping laptop is the overwhelmingly common cause
    — macOS does not replay the `StartInterval` firings it missed while
    asleep, it resumes on wake — so the message names that possibility
    rather than implying something broke.
    """
    previous = _read_last_tick(root)
    if previous is None:
        return None
    gap = now - previous
    expected = _expected_interval_seconds(root)
    threshold = max(expected * GAP_FACTOR, GAP_FLOOR_SECONDS)
    if gap <= threshold:
        return None
    return (
        f"gap since previous tick: {_format_duration(gap)} "
        f"(cadence ~{_format_duration(expected)}) — no tick ran in that "
        "window; on a laptop this is normally sleep, which launchd does "
        "not replay on wake"
    )


# ---------------------------------------------------------------------------
# The tick — mechanical, no LLM, safe to run from cron/launchd unattended.
# ---------------------------------------------------------------------------


@dataclass
class TickResult:
    ok: bool
    messages: list[str] = field(default_factory=list)
    needs_attention: bool = False
    skipped: bool = False
    """True when another tick held the lock and this one did no work. Not a
    failure — `ok` stays True — but the caller must still print the message,
    since a silent skip is indistinguishable from a scheduler that never
    fired."""


def _read_last_worker_count(state_path: Path) -> int | None:
    if not state_path.is_file():
        return None
    try:
        return int(state_path.read_text().strip())
    except ValueError:
        return None


def _write_last_worker_count(state_path: Path, count: int) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(state_path, str(count))


STALL_KEY_PREFIX = "stall:"

# The shape this module wrote before blockers carried a key. Recognised
# so an upgrade can retract the pile a running scheduler already made.
_LEGACY_STALL_RE = re.compile(r"^watchdog: worker '([^']+)' stalled")


def _stall_records(result: WatchdogResult) -> list[tuple[str, str]]:
    """One `(key, detail)` per stalled worker, for the outbox.

    Two things here are deliberate and were each found the hard way.

    **A key that does not move.** The detail used to be the whole stall
    set folded into one string INCLUDING the elapsed seconds, and the
    dedup compared details — so "is this already pending" was false on
    every tick, because the number had gone up. Running under a real
    launchd agent at one tick a minute, the outbox grew by one file per
    tick for as long as the worker stayed silent, and the watchdog reads
    the outbox back and reports each pending blocker as a reason, so the
    log grew a line per blocker per tick: quadratic, unattended, on the
    exact stall the scheduler exists to survive. That is the disk-filling
    failure a comment in this file already claimed to have fixed; the
    doubling was fixed and the accumulation was not. Measured over eleven
    ticks: eleven blocker files and a log line count rising with every
    one of them.

    So the recorded detail names WHEN the last heartbeat was, not how
    long ago it was — the same fact, stated in a way that stops changing
    once the worker goes quiet. How long ago belongs in the log, which is
    a time series; the outbox holds standing conditions.

    **One record per worker**, keyed by name, rather than one per stall
    SET — otherwise a second worker stalling changes the combined string
    and re-queues the first worker's problem along with it.
    """
    records = []
    for s in result.stalled:
        ticket_note = f" (ticket {s.ticket})" if s.ticket else ""
        if s.seconds_silent == float("inf"):
            when = "no heartbeat ever recorded"
        else:
            last_seen = datetime.fromtimestamp(s.last_seen, tz=UTC)
            when = f"last heartbeat {last_seen.isoformat(timespec='seconds')}"
        records.append(
            (
                f"{STALL_KEY_PREFIX}{s.worker}",
                f"watchdog: worker '{s.worker}' stalled — {when}{ticket_note}",
            )
        )
    return records


def _stall_key_of(msg) -> str | None:
    """The stall this pending blocker records, or None if it is not one."""
    if msg.kind != "blocker":
        return None
    key = msg.payload.get("key") or ""
    if key.startswith(STALL_KEY_PREFIX):
        return key
    match = _LEGACY_STALL_RE.match(msg.payload.get("detail") or "")
    return f"{STALL_KEY_PREFIX}{match.group(1)}" if match else None


def _reconcile_stall_blockers(
    root: Path, records: list[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """Make the outbox's stall blockers match the stalls that exist NOW.

    Returns `(standing, retracted)` — the details still recorded, and the
    details that were just removed. The caller needs both, because the
    watchdog read the outbox BEFORE this ran and is still reporting a
    blocker that no longer exists.

    Nothing ever removed a blocker. `flush_outbox`'s delivery callback
    handles `handover` and `handover-label` and returns False for
    everything else, so a stall blocker outlived the stall: once a worker
    recovered, `needs_attention` stayed true and the log kept reporting
    it, forever. A standing condition that has stopped standing is not a
    record worth keeping — the log already holds the history.
    """
    wanted = dict(records)
    pending_keys: set[str] = set()
    retracted: list[str] = []
    standing: list[str] = []
    for msg in list_pending(root):
        key = _stall_key_of(msg)
        if key is None:
            continue
        keyed = bool(msg.payload.get("key"))
        # Keep exactly one KEYED record per live stall. An unkeyed one is
        # from before this fix, and there may be hundreds of them for the
        # same worker — recognising them is only useful if they are then
        # cleared, so they are dropped and re-recorded in the keyed form
        # rather than kept alongside it.
        if key in wanted and keyed and key not in pending_keys:
            pending_keys.add(key)
            # The detail AS STORED, not as recomputed this tick. The
            # caller matches these against what the watchdog read off
            # disk, so a record kept from an earlier tick has to be
            # quoted the way that tick wrote it.
            standing.append(msg.payload.get("detail") or "")
        else:
            retracted.append(msg.payload.get("detail") or "")
            msg.path.unlink(missing_ok=True)

    for key, detail in records:
        if key not in pending_keys:
            enqueue(root, "blocker", {"detail": detail, "key": key})
            standing.append(detail)

    return standing, retracted


def run_tick(root: Path) -> TickResult:
    """One invocation of the scheduler — call this every `watchdog.
    interval_minutes` (default 5) from cron/launchd. Idempotent: running
    it twice in the same minute does the same thing twice, harmlessly (the
    boundary check only ACTS on a genuine transition, not a repeated
    reading of the same state).

    Serialised against other ticks by `scheduler.lock` (see
    `rite_ai.scheduler.lock` for why a pid beats a timeout). The lock lives
    here rather than in the CLI so no caller can route around it. A tick
    that finds the lock held returns immediately with `skipped=True` and a
    message naming the holder."""
    with lock.held(root) as outcome:
        if isinstance(outcome, lock.LockBusy):
            return TickResult(
                ok=True, messages=[f"skipped: {outcome.summary}"], skipped=True
            )
        return _run_tick_locked(root, outcome)


def _run_tick_locked(root: Path, outcome: lock.LockAcquired) -> TickResult:
    messages: list[str] = []

    now = time.time()
    gap = _gap_message(root, now)
    if gap:
        messages.append(gap)
    _write_last_tick(root, now)

    if outcome.reclaimed_from is not None or outcome.reclaim_reason:
        # A lock left behind by a tick that died. Recovering silently would
        # hide that a previous run was killed mid-flight.
        messages.append(f"reclaimed stale scheduler lock: {outcome.reclaim_reason}")

    rotated = rotate_if_needed(log_path(root))
    if rotated:
        messages.append(rotated)

    watchdog_result = run_watchdog_check(root)
    needs_attention = watchdog_result.needs_attention
    if watchdog_result.needs_attention:
        # No LLM call, and nothing here can spawn a Manager session — the
        # mechanical half of "wakes the Manager" is recording durable
        # state a human or a future session sees on next contact (§2.0),
        # not literally starting one.
        #
        # Record ONLY what the outbox does not already hold. The watchdog's
        # reasons include "blocker in outbox: ..." entries read back out of
        # this very outbox (§3.5), so enqueueing the whole reason list makes
        # every tick re-report the previous tick's report: at a 5-minute
        # cadence the recorded text doubles each cycle and fills the disk
        # within hours of an unattended stall. The findings the outbox does
        # not already know are the stall reports, and even those are only
        # worth writing once — a stall already pending is the same standing
        # problem, not a new one. "Already pending" is decided on a KEY
        # that does not move, which the elapsed-seconds detail did; see
        # `_stall_records`.
        standing, retracted = _reconcile_stall_blockers(
            root, _stall_records(watchdog_result)
        )
        # The watchdog reads the outbox back, so every blocker this tick
        # holds comes round as a reason of its own. Report each stall
        # once — as a stall — not again as a note about the record of it.
        # `retracted` is suppressed for a different reason: the watchdog
        # took its snapshot BEFORE reconciliation and is still reporting a
        # blocker that has since been removed, which would keep a
        # recovered worker "needing attention" for one more tick.
        echoes = {f"blocker in outbox: {detail}" for detail in (*standing, *retracted)}
        reasons = [r for r in watchdog_result.reasons if r not in echoes]
        messages.extend(f"watchdog: {reason}" for reason in reasons)
        needs_attention = bool(reasons)

    project = load_project(root)
    if isinstance(project, list):
        # The project could not be loaded at all — a deleted directory, a
        # broken config. `ok=False`, which the CLI turns into a nonzero
        # exit, because this is the one thing a tick must not report as a
        # normal cycle.
        #
        # It is a different fact from "the tick ran and found a stall":
        # that is the tick doing its job and stays `ok=True`. This is the
        # tick unable to do its job, and launchd records only the exit
        # code. Five soak agents left registered against deleted
        # directories fired every 60s for hours reporting
        # `LastExitStatus = 0` — indistinguishable from healthy, which is
        # exactly how they went unnoticed.
        return TickResult(ok=False, messages=messages, needs_attention=True)

    schedule = project.config.schedule
    minute = current_minute_of_day(schedule.timezone)
    if minute is not None and schedule.windows:
        current_count = workers_at(schedule, minute)
        state_path = root / ".rite" / STATE_FILENAME
        last_count = _read_last_worker_count(state_path)

        if last_count is not None and last_count != 0 and current_count == 0:
            # A genuine transition INTO zero (§2.7.3, D-46) — hand over
            # every currently active Worker. Does NOT wait for a
            # checkpoint (§2.1's "commit early and often" bounds the
            # damage instead) — that's the deliberate asymmetry with
            # graceful Owner demotion.
            active_workers: list[str] = []
            claims_path = root / ".rite" / "claims.json"
            if claims_path.is_file():
                ledger = ClaimsLedger(claims_path)
                active_workers = sorted({c.worker for c in ledger.list_claims()})

            if active_workers:
                from rite_ai.lifecycle import perform_handover

                for worker in active_workers:
                    perform_handover(
                        root, worker=worker, reason="scheduled window boundary"
                    )
                    messages.append(
                        f"scheduled window boundary: handed over worker '{worker}'"
                    )
            else:
                messages.append("scheduled window boundary: 0 workers, nothing active")
        elif last_count is not None and last_count == 0 and current_count > 0:
            # The RETURN path, which said nothing at all — so the tick
            # printed "nothing to report — no stalled workers, no window
            # transition" in the same minute a window opened, and wrote
            # the new count to state silently. The log this line lands in
            # is read by a human the morning after; asserting that no
            # transition happened, at the moment one did, is the one
            # thing it must not do.
            #
            # It starts NOTHING, and that is deliberate, not an omission:
            # §2.5 is explicit that "Managers/Owner still need a human to
            # start — `rite pool fill` is that explicit action; nothing
            # spawns a session automatically", and every pooled slot runs
            # `claude` against a weekly quota that no cleanup gets back.
            # So the boundary is REPORTED and the non-action is named,
            # rather than left as a silence a reader has to interpret.
            messages.append(
                f"scheduled window boundary: window opened, {current_count} "
                "worker(s) scheduled — nothing started automatically (§2.5); "
                "`rite pool fill` is the explicit action"
            )

        if last_count != current_count:
            _write_last_worker_count(state_path, current_count)

    coordination_messages = _coordination_tick(root, project)
    messages.extend(coordination_messages)

    return TickResult(ok=True, messages=messages, needs_attention=needs_attention)


def _coordination_tick(root: Path, project) -> list[str]:
    """One coordination tick: publish liveness, keep or take the Owner role.

    This is what makes Phase 2 run at all. The machinery landed complete and
    called by nobody, which is D-14's defect shape — a function whose caller
    was never written — and the scheduler tick is where the spec already puts
    unattended periodic work (§5.1.2), on a cadence that suits it: §3.3.1
    wants a heartbeat about every 10 minutes and a lease renewal every 15,
    and the default tick is every 5.

    **Nothing here touches the ticket backend.** A Manager also distributes
    work to its Workers (P2-4b) and that is deliberately NOT wired to cron:
    it writes labels and comments on a shared board, and doing that
    unattended deserves its own decision (Q9). This tick writes only to the
    coordination repo, which is what §2.4's mechanics are made of.

    It also starts no session, so §2.5's rule is untouched: election decides
    who the Owner IS, not that anything begins working.

    Silent on a machine that is not enrolled, which is every project today.
    """
    config = project.config.coordination
    if not config.managers or not config.remote:
        return []

    from rite_ai.coordination.identity import enrolment, this_manager

    problem = enrolment(root, config)
    if problem:
        # Named, not skipped silently: a machine that thinks it is
        # coordinating and is not looks identical to one that is.
        return [problem]

    name = this_manager(root)
    try:
        from rite_ai.coordination.git_backend import GitStateLayer
        from rite_ai.coordination.lease import OwnerLeaseHolder
        from rite_ai.coordination.monitor import ManagerMonitor

        layer = GitStateLayer(
            config.remote,
            root / ".rite" / "coordination-cache.git",
            state_branch=config.state_branch,
        )
        holder = OwnerLeaseHolder(layer, name, config)
        # Read the ledger ONCE for this tick: what we publish and what we
        # decide the handover on must be the same reading, or the tick can
        # advertise itself busy and hand over in the same breath.
        workers, in_flight = _this_machines_load(root)
        monitor = ManagerMonitor(
            holder,
            root=root,
            heartbeat=project.config.heartbeat,
            status=lambda: (workers, in_flight),
            # D-43 puts the handover at an operation boundary and says only
            # the caller knows where that is. From cron, the honest answer
            # is "when this machine has nothing in flight": no claimed
            # ticket means no Worker is mid-anything, which is the closest
            # thing to a boundary a periodic job can see.
            #
            # Without this an incumbent that has been ASKED never hands over
            # and the role moves only when its lease lapses — the
            # interruption §2.4's graceful demotion exists to replace.
            hand_over_when=lambda: in_flight == 0,
        )
        tick = monitor.tick()
        duties = _owner_duties(root, layer, config, project, name, tick)
    except Exception as e:  # noqa: BLE001 - a tick must not die on coordination
        # The scheduler runs unattended from cron; an exception here would
        # take the watchdog and the window check down with it.
        return [f"coordination: the tick could not run: {e}"]

    # Written down so `rite status` can say what this machine concluded
    # without a round trip — stamped with the time, never as a live answer.
    from rite_ai.coordination import last_tick as last_tick_file

    last_tick_file.record(root, name, tick.action, tick.owner, len(tick.problems))

    lines = [f"coordination: {name} — {tick.action}"]
    if tick.detail:
        lines[0] += f" ({tick.detail})"
    lines.extend(f"coordination: {p}" for p in tick.problems)
    lines.extend(duties)
    return lines


def _owner_duties(root, layer, config, project, name: str, tick) -> list[str]:
    """What only the Owner does: act on Managers that have gone quiet.

    §2.3 gives the Owner two jobs about a stalled Manager — surface it (done
    by `doctor`) and deal with its work. D-14's SECOND trigger hands that
    work over (P2-3b) and P2-5c expires the claims it was holding, and
    NEITHER had a caller, so a stalled machine's tickets stayed assigned to
    it and its claims blocked everybody else for ever.

    **Only the Owner, deliberately.** Every Manager can see the same stall;
    if each acted, one stalled machine would get N handover comments on its
    tickets and N expiry records. The role exists to decide who acts.

    **Handover first, expiry second.** The handover needs the claims to know
    which tickets to comment on, and expiring them first would silently
    reduce it to nothing. The expiry is also what makes this idempotent: the
    next tick finds no claims and does nothing at all.
    """
    if not tick.owner:
        return []

    from datetime import UTC, datetime

    from rite_ai.coordination.claims_state import expire_offline_claims
    from rite_ai.coordination.takeover import ToldTheBoard, hand_over_stalled_manager

    heartbeat = project.config.heartbeat
    now = datetime.now(UTC)
    lines: list[str] = []

    for other in config.managers:
        if other == name:
            continue
        handed = hand_over_stalled_manager(
            root,
            layer,
            owner=name,
            stalled=other,
            now=now,
            interval_minutes=heartbeat.interval_minutes,
            stall_threshold=heartbeat.stall_threshold,
        )
        if isinstance(handed, ToldTheBoard) and handed.tickets:
            lines.append(
                f"coordination: handed over {other}'s work: "
                + ", ".join(handed.tickets)
            )
            if handed.queued:
                lines.append(
                    "coordination: board NOT updated for "
                    + ", ".join(handed.queued)
                )

    expiry = expire_offline_claims(
        layer,
        by=name,
        now=now,
        interval_minutes=heartbeat.interval_minutes,
        stall_threshold=heartbeat.stall_threshold,
        skip=frozenset({name}),
    )
    for machine, count in expiry.expired.items():
        lines.append(
            f"coordination: expired {count} claim(s) held by {machine} — its "
            "heartbeat has lapsed"
        )
    if expiry.refused:
        lines.append(f"coordination: claims were not expired: {expiry.refused}")
    return lines


def _this_machines_load(root: Path) -> tuple[list[str], int]:
    """(Workers, tickets in flight) from ONE read of the claims ledger.

    Workers are those holding claims — the definition `run_tick`'s window
    boundary already uses.

    `in_flight` counts distinct TICKETS, not claims: a Worker holding four
    paths for one ticket is doing one piece of work, and counting paths
    would make a machine look four times as busy as it is.

    It is not decoration. The Owner assigns to the least loaded Manager
    (P2-4a: `min(v.in_flight, ...)`), so a machine that always reports 0
    advertises itself as idle and the Owner sends it everything — routing
    defeated silently, with every machine looking healthy. The first cut of
    this tick hardcoded 0.

    Read once, not twice: two reads could disagree and produce a worker list
    that does not match the count beside it.
    """
    claims_path = root / ".rite" / "claims.json"
    if not claims_path.is_file():
        return [], 0
    from rite_ai.claims.ledger import ClaimsLedger

    claims = ClaimsLedger(claims_path).list_claims()
    workers = sorted({c.worker for c in claims})
    tickets = {c.ticket for c in claims if c.ticket}
    return workers, len(tickets)


# ---------------------------------------------------------------------------
# OS-level install/uninstall — cron (portable) or launchd (macOS).
# ---------------------------------------------------------------------------


@dataclass
class SchedulerResult:
    ok: bool
    message: str


def detect_backend() -> str:
    return "launchd" if sys.platform == "darwin" else "cron"


def _marker(root: Path) -> str:
    return f"# rite-scheduler:{root.resolve()}"


def _rite_binary() -> str:
    return shutil.which("rite") or "rite"


def _cron_line(root: Path, interval_minutes: int) -> str:
    binary = _rite_binary()
    log = log_path(root)  # same file the tick rotates
    return (
        f"*/{interval_minutes} * * * * cd {root.resolve()} && "
        f"{binary} scheduler-tick >> {log} 2>&1  {_marker(root)}"
    )


def _read_crontab() -> str:
    try:
        proc = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        # Same as "no crontab": callers use this to decide whether rite's
        # own entry is already present, and both answers here mean "not
        # found". A timeout must not crash `rite scheduler status`.
        return ""
    if proc.returncode != 0:
        return ""  # "no crontab for user" is not an error here
    return proc.stdout


def _write_crontab(content: str) -> SchedulerResult:
    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=content,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return SchedulerResult(False, f"`crontab -` failed: {e}")
    if proc.returncode != 0:
        return SchedulerResult(False, f"crontab write failed: {proc.stderr.strip()}")
    return SchedulerResult(True, "crontab updated")


def _install_cron(root: Path, interval_minutes: int) -> SchedulerResult:
    existing = _read_crontab()
    marker = _marker(root)
    if marker in existing:
        return SchedulerResult(False, f"already installed for {root}")
    new_line = _cron_line(root, interval_minutes)
    prefix = existing.rstrip("\n") + ("\n" if existing.strip() else "")
    return _write_crontab(prefix + new_line + "\n")


def _uninstall_cron(root: Path) -> SchedulerResult:
    existing = _read_crontab()
    marker = _marker(root)
    lines = [line for line in existing.splitlines() if marker not in line]
    if len(lines) == len(existing.splitlines()):
        return SchedulerResult(False, f"not installed for {root}")
    new_content = "\n".join(lines) + ("\n" if lines else "")
    return _write_crontab(new_content)


def _is_installed_cron(root: Path) -> bool:
    return _marker(root) in _read_crontab()


def _launchd_label(root: Path) -> str:
    slug = str(root.resolve()).strip("/").replace("/", "-")
    return f"dev.rite.scheduler.{slug}"


def _launchd_plist_path(root: Path) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_launchd_label(root)}.plist"


def _launchd_plist_content(root: Path, interval_minutes: int) -> str:
    binary = _rite_binary()
    log = log_path(root)  # same file the tick rotates
    label = _launchd_label(root)
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{binary}</string>
    <string>scheduler-tick</string>
  </array>
  <key>WorkingDirectory</key><string>{root.resolve()}</string>
  <key>StartInterval</key><integer>{interval_minutes * 60}</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _install_launchd(root: Path, interval_minutes: int) -> SchedulerResult:
    plist_path = _launchd_plist_path(root)
    if plist_path.is_file():
        return SchedulerResult(False, f"already installed for {root}")
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a partially-written plist that launchd never loaded is a
    # file `rite scheduler status` would call installed.
    write_atomic(plist_path, _launchd_plist_content(root, interval_minutes))
    try:
        proc = subprocess.run(
            ["launchctl", "load", str(plist_path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        plist_path.unlink(missing_ok=True)  # leave no half-installed agent
        return SchedulerResult(False, f"`launchctl load` failed: {e}")
    if proc.returncode != 0:
        plist_path.unlink(missing_ok=True)
        return SchedulerResult(False, f"launchctl load failed: {proc.stderr.strip()}")
    return SchedulerResult(True, f"installed {plist_path}")


def _uninstall_launchd(root: Path) -> SchedulerResult:
    plist_path = _launchd_plist_path(root)
    if not plist_path.is_file():
        return SchedulerResult(False, f"not installed for {root}")
    try:
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        # The plist is deliberately left in place: removing it while
        # launchd may still hold the job loaded is how you get an agent
        # that keeps firing with no file to unload it by.
        return SchedulerResult(False, f"`launchctl unload` failed: {e}")
    plist_path.unlink(missing_ok=True)
    return SchedulerResult(True, f"removed {plist_path}")


def install(
    root: Path, interval_minutes: int = 5, backend: str | None = None
) -> SchedulerResult:
    backend = backend or detect_backend()
    if backend == "launchd":
        return _install_launchd(root, interval_minutes)
    if backend == "cron":
        return _install_cron(root, interval_minutes)
    return SchedulerResult(False, f"unknown backend: {backend!r}")


def uninstall(root: Path, backend: str | None = None) -> SchedulerResult:
    backend = backend or detect_backend()
    if backend == "launchd":
        return _uninstall_launchd(root)
    if backend == "cron":
        return _uninstall_cron(root)
    return SchedulerResult(False, f"unknown backend: {backend!r}")


@dataclass
class SchedulerHealth:
    """Whether the scheduler is actually running — not whether it is
    registered.

    `rite scheduler status` reported "installed", which is a fact about a
    plist on disk. This project has already shipped one thing that was
    installed, correct, executable and never read: the pre-push hook git
    ignored because `core.hooksPath` pointed elsewhere. "Registered" and
    "running" are different claims, and only the second one is worth
    asking about — so this reports the last tick that actually happened,
    which is evidence the job ran rather than evidence it was set up.
    """

    backend: str
    installed: bool
    interval_seconds: int | None = None
    last_tick: float | None = None
    seconds_since_tick: float | None = None
    overdue: bool = False
    loaded: bool | None = None  # launchd only; None when not knowable
    last_exit: int | None = None
    log_bytes: int = 0
    log_files: int = 0


def _launchd_job_state(root: Path) -> tuple[bool | None, int | None]:
    (
        """`(loaded, last exit status)` from launchd itself.

    launchd is the authority on whether it holds the job, and its
    last-exit column is the only place a tick that failed *before it
    could write anything* leaves a trace.""",
    )
    label = _launchd_label(root)
    try:
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, None
    if proc.returncode != 0:
        return None, None
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].strip() == label:
            try:
                return True, int(parts[1])
            except ValueError:
                return True, None
    return False, None


def _plist_interval(root: Path) -> int | None:
    path = _launchd_plist_path(root)
    if not path.is_file():
        return None
    text = path.read_text()
    marker = "<key>StartInterval</key><integer>"
    idx = text.find(marker)
    if idx < 0:
        return None
    rest = text[idx + len(marker) :]
    digits = rest.split("<", 1)[0].strip()
    try:
        return int(digits)
    except ValueError:
        return None


def health(root: Path, backend: str | None = None, now: float | None = None):
    """What `rite scheduler status` should actually answer."""
    moment = now if now is not None else time.time()
    resolved = backend or detect_backend()
    state = SchedulerHealth(
        backend=resolved, installed=is_installed(root, backend=resolved)
    )

    if resolved == "launchd":
        state.interval_seconds = _plist_interval(root)
        state.loaded, state.last_exit = _launchd_job_state(root)

    state.last_tick = _read_last_tick(root)
    if state.last_tick is not None:
        state.seconds_since_tick = max(moment - state.last_tick, 0.0)
        expected = state.interval_seconds or _expected_interval_seconds(root)
        threshold = max(expected * GAP_FACTOR, GAP_FLOOR_SECONDS)
        state.overdue = state.seconds_since_tick > threshold

    log = log_path(root)
    if log.is_file():
        state.log_bytes = log.stat().st_size
        state.log_files = 1
    for i in range(1, KEEP + 1):
        archive = log.with_suffix(log.suffix + f".{i}")
        if archive.is_file():
            state.log_bytes += archive.stat().st_size
            state.log_files += 1
    return state


def format_health(state) -> list[str]:
    lines = [
        f"{state.backend}: "
        + ("installed" if state.installed else "not installed")
        + (
            f", every {_format_duration(state.interval_seconds)}"
            if state.interval_seconds
            else ""
        )
    ]
    # Only meaningful when there IS a plist. Saying "not installed" and
    # "the plist exists" in consecutive lines is worse than saying neither.
    if state.installed and state.loaded is False:
        lines.append(
            "  NOT LOADED — the plist exists but launchd does not hold the "
            "job; `rite scheduler uninstall` then `install` to re-register"
        )
    if state.last_exit:
        lines.append(f"  last run exited {state.last_exit} (non-zero)")

    if state.last_tick is None:
        if state.installed:
            lines.append(
                "  no tick has run yet — registered, but nothing has "
                "confirmed it fires. Wait one interval, or run "
                "`rite scheduler-tick` by hand."
            )
    else:
        ago = _format_duration(state.seconds_since_tick or 0)
        when = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(state.last_tick))
        verdict = " — OVERDUE" if state.overdue else ""
        lines.append(f"  last tick: {when} ({ago} ago){verdict}")
    if state.log_files:
        lines.append(
            f"  log: {state.log_bytes:,} bytes across {state.log_files} file(s)"
        )
    return lines


def is_installed(root: Path, backend: str | None = None) -> bool:
    backend = backend or detect_backend()
    if backend == "launchd":
        return _launchd_plist_path(root).is_file()
    if backend == "cron":
        return _is_installed_cron(root)
    return False
