"""Recovering a stalled Worker, unattended (SCRUM-38).

The watchdog already flags a Worker STALLED (no heartbeat past the threshold)
and, until now, could only report it and wait for the Owner. This decides what
to DO about it and does it, at the Manager's cycle boundary — the same place
Worker requests, deliveries and chores run, off the two-second poll.

**Detect → classify → act, keyed on the watchdog's `StallReport`:**

* **session ran out, work intact** — the sandbox is still there — → **restart in
  place** (`yoloai restart --resume`), keeping the Worker's claim and its
  copy-on-write workspace. Nothing it was part-way through is lost.
* **sandbox gone / work lost** — no sandbox under that name → **re-stage**:
  release the claim so the ticket returns to the board for a fresh start. The
  work is already gone with the sandbox; holding the claim would only strand it.
* **cannot tell** — the heartbeat was unreadable, or yoloai could not answer →
  do nothing this cycle. Acting on "I could not check" is how a live Worker
  gets reaped.

**It does not loop.** A per-Worker ledger counts recovery attempts; between
attempts it backs off (growing), and after `max_restarts` it stops trying and
falls back to today's behaviour — leave it STALLED for the Owner to see. A
Worker that heartbeats again has its ledger entry cleared, so a later stall
starts from zero.

The planner is a pure function over hand-built inputs; every side-effecting
dependency of the applier is injected. Nothing here runs on the hot timing
path — the supervise loop calls it as an injected step at the cycle boundary,
beside `_honour_worker_requests`, never replacing another step.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.reporting.heartbeat import StallReport

# What to do about one stalled Worker.
RESTART = "restart"  # session dead, sandbox (and work) present -> restart in place
RESTAGE = "restage"  # sandbox gone, work lost -> release the claim, re-dispatch
REPORT = "report"  # backoff exhausted or cannot classify -> leave it for the Owner

DEFAULT_MAX_RESTARTS = 3
DEFAULT_BASE_BACKOFF = 300.0  # seconds before the 2nd attempt; doubles thereafter
DEFAULT_BACKOFF_CAP = 3600.0
DEFAULT_MAX_ACTIONS = 1  # one recovery action per cycle, like one session at a time


@dataclass(frozen=True)
class LedgerEntry:
    attempts: int = 0
    first_at: float = 0.0
    last_at: float = 0.0


@dataclass(frozen=True)
class RecoveryAction:
    worker: str
    kind: str
    ticket: str = ""
    reason: str = ""


def _backoff_seconds(
    attempts: int,
    *,
    base: float = DEFAULT_BASE_BACKOFF,
    cap: float = DEFAULT_BACKOFF_CAP,
) -> float:
    """How long to wait before the next recovery attempt. Zero for the first
    (nothing has been tried yet), then `base`, `2*base`, `4*base`… to `cap`."""
    if attempts <= 0:
        return 0.0
    return min(cap, base * (2 ** (attempts - 1)))


def _plan_recovery(
    stalls: list[StallReport],
    *,
    classify,
    ledger: dict[str, LedgerEntry],
    now: float,
    max_restarts: int = DEFAULT_MAX_RESTARTS,
    base_backoff: float = DEFAULT_BASE_BACKOFF,
    backoff_cap: float = DEFAULT_BACKOFF_CAP,
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> list[RecoveryAction]:
    """What to do about each stalled Worker this cycle.

    `classify(report) -> "present" | "gone" | "unknown"` says whether the
    Worker's sandbox is still there. The most-silent Workers are considered
    first, and at most `max_actions` act per cycle so recovery never storms.
    """
    actions: list[RecoveryAction] = []
    for report in sorted(stalls, key=lambda r: r.seconds_silent, reverse=True):
        if len(actions) >= max_actions:
            break
        worker = report.worker
        if not report.known:
            # The heartbeat could not even be read — "I cannot tell", not a
            # measured silence. Never act on that.
            continue
        entry = ledger.get(worker, LedgerEntry())
        if entry.attempts >= max_restarts:
            actions.append(
                RecoveryAction(
                    worker,
                    REPORT,
                    report.ticket,
                    f"{entry.attempts} recovery attempt(s) already — leaving it "
                    "STALLED for the Owner rather than restarting in a loop",
                )
            )
            continue
        wait = _backoff_seconds(entry.attempts, base=base_backoff, cap=backoff_cap)
        if entry.attempts > 0 and (now - entry.last_at) < wait:
            # Backing off — tried recently, not yet time to try again.
            continue
        where = classify(report)
        if where == "present":
            actions.append(
                RecoveryAction(
                    worker, RESTART, report.ticket, "session stalled; sandbox present"
                )
            )
        elif where == "gone":
            actions.append(
                RecoveryAction(
                    worker, RESTAGE, report.ticket, "sandbox gone; work lost"
                )
            )
        # "unknown": yoloai could not answer — do nothing this cycle.
    return actions


# --- the per-Worker backoff ledger -----------------------------------------


def _ledger_path(root: Path) -> Path:
    return Path(root) / ".rite" / "recovery.json"


def _read_ledger(root: Path) -> dict[str, LedgerEntry]:
    """The recovery ledger, or empty when there is none or it cannot be read.

    A ledger that will not parse reads as empty: the cost is a recovery that
    forgets it had backed off (it may try once more), never a crash in the
    tick that reads it."""
    try:
        data = json.loads(_ledger_path(root).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, LedgerEntry] = {}
    for worker, raw in data.items():
        if isinstance(raw, dict):
            out[str(worker)] = LedgerEntry(
                attempts=int(raw.get("attempts", 0) or 0),
                first_at=float(raw.get("first_at", 0.0) or 0.0),
                last_at=float(raw.get("last_at", 0.0) or 0.0),
            )
    return out


def _write_ledger(root: Path, ledger: dict[str, LedgerEntry]) -> None:
    path = _ledger_path(root)
    body = {
        worker: {
            "attempts": entry.attempts,
            "first_at": entry.first_at,
            "last_at": entry.last_at,
        }
        for worker, entry in ledger.items()
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass  # best-effort: a ledger we cannot persist means a forgotten backoff,
        # never a failed recovery or a crashed tick.


def _record_attempt(
    ledger: dict[str, LedgerEntry], worker: str, now: float
) -> dict[str, LedgerEntry]:
    entry = ledger.get(worker, LedgerEntry())
    ledger[worker] = LedgerEntry(
        attempts=entry.attempts + 1,
        first_at=entry.first_at or now,
        last_at=now,
    )
    return ledger


# --- the applier: run at the Manager's cycle boundary ----------------------


def recover_stalled_workers(
    root: Path,
    manager: str,
    say,
    *,
    now: float | None = None,
    stalls_of=None,
    classify=None,
    do_restart=None,
    do_restage=None,
    max_restarts: int = DEFAULT_MAX_RESTARTS,
    max_actions: int = DEFAULT_MAX_ACTIONS,
) -> list[RecoveryAction]:
    """Read the watchdog's stalls, decide, and act — the injected cycle-boundary
    step (SCRUM-38). Returns the actions taken, for the caller to say/record.

    Every dependency is injectable and defaults to production, so a test drives
    the whole decision without a watchdog, yoloai or a claims ledger. Entirely
    best-effort: a failure in here is said, never raised into the supervise
    loop.
    """
    root = Path(root)
    now = time.time() if now is None else now
    try:
        stalls = stalls_of() if stalls_of is not None else _watchdog_stalls(root)
        classify = classify or (lambda report: _classify_sandbox(report.worker, root))
        do_restart = do_restart or (lambda report: _restart(root, report.worker))
        do_restage = do_restage or (lambda report: _restage(root, report.worker))

        ledger = _read_ledger(root)
        stalled_names = {r.worker for r in stalls}
        # A Worker that recovered on its own clears its backoff, so a later
        # stall starts from zero rather than from an exhausted budget.
        for gone in [w for w in ledger if w not in stalled_names]:
            del ledger[gone]

        by_worker = {r.worker: r for r in stalls}
        actions = _plan_recovery(
            stalls,
            classify=classify,
            ledger=ledger,
            now=now,
            max_restarts=max_restarts,
            max_actions=max_actions,
        )
        for action in actions:
            report = by_worker[action.worker]
            if action.kind == RESTART:
                ok, message = do_restart(report)
                _record_attempt(ledger, action.worker, now)
                say(
                    f"recovering stalled Worker {action.worker!r}: restarted in "
                    f"place ({message})"
                    if ok
                    else f"could not restart stalled Worker {action.worker!r}: "
                    f"{message}"
                )
            elif action.kind == RESTAGE:
                ok, message = do_restage(report)
                _record_attempt(ledger, action.worker, now)
                say(
                    f"recovering stalled Worker {action.worker!r}: its sandbox is "
                    f"gone, so {action.ticket or 'its ticket'} is re-staged "
                    f"({message})"
                    if ok
                    else f"could not re-stage stalled Worker {action.worker!r}: "
                    f"{message}"
                )
            elif action.kind == REPORT:
                say(
                    f"stalled Worker {action.worker!r} has exhausted its recovery "
                    f"budget — left STALLED for you: {action.reason}"
                )
        _write_ledger(root, ledger)
        return actions
    except Exception as e:  # noqa: BLE001 - recovery must never break the cycle
        try:
            say(f"Worker recovery skipped this cycle: {type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        return []


def _watchdog_stalls(root: Path) -> list[StallReport]:
    from rite_ai.watchdog import run_watchdog_check

    return list(run_watchdog_check(root).stalled)


def _classify_sandbox(worker: str, root: Path) -> str:
    """ "present" | "gone" | "unknown" for this Worker's sandbox."""
    from rite_ai.sandbox import worker_sandbox_status

    status = worker_sandbox_status(worker, root)
    if not status.known:
        return "unknown"
    if status.value == "not found":
        return "gone"
    return "present"


def _restart(root: Path, worker: str) -> tuple[bool, str]:
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.credentials.store import worker_environment
    from rite_ai.sandbox import restart_worker

    parsed = parse_config(Path(root) / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return False, f"config.yaml will not parse: {parsed.message}"
    env = worker_environment(parsed.credentials)
    result = restart_worker(worker, root, parsed.sandbox, env=env)
    return result.ok, result.message


def _restage(root: Path, worker: str) -> tuple[bool, str]:
    """Release the Worker's claim so its ticket returns to the board. The work
    is already gone with the sandbox; the Manager re-dispatches next cycle."""
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(Path(root) / ".rite" / "claims.json")
    try:
        claims = ledger.claims_for(worker)
    except Exception as e:  # noqa: BLE001
        return False, f"could not read the claim ledger: {e}"
    paths = sorted({p for claim in claims for p in claim.paths})
    if not paths:
        return True, "no claim to release; its ticket is already free"
    try:
        ledger.release(worker, paths)
    except Exception as e:  # noqa: BLE001
        return False, f"could not release its claim: {e}"
    return True, f"released {len(paths)} path(s); the ticket can be taken again"
