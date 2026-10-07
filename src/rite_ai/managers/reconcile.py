"""A restarted Manager reconciles its model against ground truth before it
acts (SCRUM-64), so it stops escalating stale half-state to the Owner.

**What happened** (the a8 dogfood, 2026-10-04). A Manager was stopped and
restarted. It came back believing a Worker was mid-ticket and "stalled 10h,"
and asked the Owner, every cycle, to start it, destroy another, and diagnose
a gate — when the ticket was already delivered, that sandbox intentionally
stopped, and the other sandbox had never existed. It had no way to discover
any of that, so it looped. A Manager that can only get unstuck by a human
running host commands is not unattended.

So at `rite start`, and throttled per cycle, the supervisor compares each of
this Manager's Workers against ground truth and acts only on what it can
read for certain:

**It releases a claim only when the sandbox is known GONE and the work is
delivered, merged or handed back.** "Cannot tell" is never an action: an
unknown sandbox, an unreadable claim, a Worker still running — all leave the
claim exactly as it is. A sandbox that is gone while its work has NOT landed
keeps its claim too, and is reported, not released: releasing there would
hand the ticket to the next Worker while the first Worker's commits sit
undelivered. Every outcome is told to the Manager, so a restart converges to
a true, actionable state with no human in the loop.

Shaped like `recovery`: a pure planner (`plan`) decided entirely from facts,
and an applier (`reconcile`) whose every dependency is injectable, so the
policy is tested without a sandbox, a board or a claim ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RELEASE = "release"  # sandbox gone AND work landed -> release the stale claim
REPORT = "report"  # sandbox gone AND work NOT landed -> hold, tell the Manager
HELD = "held"  # a live/unknown sandbox, or no claim -> leave it, say nothing

THROTTLE_SECONDS = 300.0
"""At most one reconcile pass this often per cycle, after the first at start."""


@dataclass(frozen=True)
class Facts:
    """What rite can read about one Worker right now. Each `None` is an
    explicit "cannot tell", distinct from True/False."""

    has_claim: bool
    sandbox_gone: bool | None  # None: yoloAI could not be asked
    work_landed: bool  # delivered, merged, or handed back — all mean "left the sandbox"


@dataclass(frozen=True)
class Action:
    worker: str
    kind: str
    reason: str = ""


def plan(worker: str, facts: Facts) -> Action:
    """What to do about one Worker, from facts alone. Every branch that is
    not a certain release holds."""
    if not facts.has_claim:
        return Action(worker, HELD, "no claim to reconcile")
    if facts.sandbox_gone is None:
        return Action(worker, HELD, "could not tell whether the sandbox is gone")
    if not facts.sandbox_gone:
        return Action(worker, HELD, "the sandbox is still there")
    if facts.work_landed:
        return Action(
            worker,
            RELEASE,
            "its sandbox is gone and its work was delivered, merged or handed "
            "back, so the claim it still holds is stale",
        )
    return Action(
        worker,
        REPORT,
        "its sandbox is gone but its work has NOT been delivered, merged or "
        "handed back; the claim is kept rather than handing the ticket on with "
        "the work undelivered",
    )


# --- gathering the facts, on the host --------------------------------------------


def _claim_paths(root: Path, worker: str) -> list[str] | None:
    """The paths `worker` claims, or None if the ledger cannot be read (which
    is "cannot tell", never "no claim")."""
    from rite_ai.claims.ledger import ClaimsLedger

    try:
        claims = ClaimsLedger(Path(root) / ".rite" / "claims.json").claims_for(worker)
    except Exception:  # noqa: BLE001 - unreadable ledger: cannot tell
        return None
    return sorted({p for c in claims for p in c.paths})


def _sandbox_gone(root: Path, worker: str) -> bool | None:
    """True only when yoloAI says the sandbox is not there; None when it
    could not be asked; False for any live state, `stopped` included (a
    stopped sandbox still holds its copy — it is not gone)."""
    from rite_ai.sandbox import worker_sandbox_status

    status = worker_sandbox_status(worker, root)
    if not status.known:
        return None
    return status.value == "not found"


def _work_landed(root: Path, worker: str) -> bool:
    """Whether `worker`'s work left its sandbox: a handback, a delivered
    event, or a pull request still watched (a PR means it was pushed)."""
    from rite_ai import handback

    hb = handback.read(Path(root), worker)
    if hb is not None:  # a handback, readable or not, means it handed off
        return True
    from rite_ai.reporting import events

    for event in events.since(Path(root), 0.0):
        if event.get("event") == "delivered" and event.get("worker") == worker:
            return True
    from rite_ai.publishing import merging

    watched = merging._load(Path(root))  # noqa: SLF001
    if isinstance(watched, list):
        return any(getattr(w, "worker", "") == worker for w in watched)
    return False


def facts_for(root: Path, worker: str) -> Facts:
    paths = _claim_paths(root, worker)
    return Facts(
        has_claim=bool(paths),
        sandbox_gone=_sandbox_gone(root, worker),
        work_landed=_work_landed(root, worker),
    )


def _mine(root: Path, manager: str) -> list[str]:
    """This Manager's Workers: the ones it owns (SCRUM-59 owner record) or
    whose `worker.yml` names it, plus — so a project that nominates nobody
    still reconciles — those that name no Manager. `worker_handbacks._mine`'s
    rule, which is the Manager that would integrate the work."""
    from rite_ai.config.parse import load_project
    from rite_ai.managers import lifecycle

    project = load_project(Path(root))
    workers = list(getattr(project, "workers", []) or [])
    named = [w.name for w in workers if getattr(w, "manager", "") == manager]
    unassigned = [w.name for w in workers if not getattr(w, "manager", "")]
    mine = set(named)
    for name in unassigned:
        try:
            owner = lifecycle._owner_of(Path(root), name)  # noqa: SLF001
        except OSError:
            owner = ""  # cannot tell: fall back to the generous rule
        if not owner or owner == manager:
            mine.add(name)
    return sorted(mine)


# --- the applier, run at the Manager's cycle boundary and at start ---------------


_LAST_AT: dict[tuple[str, str], float] = {}
"""When this process last ran a reconcile pass, per (project, Manager), for
the per-cycle throttle. A fresh process (a restart) has none, so the start
pass always runs."""


def reconcile(
    root: Path,
    manager: str,
    say,
    *,
    at_start: bool = False,
    now: float | None = None,
    workers_of=None,
    facts_of=None,
    release=None,
    throttle: float = THROTTLE_SECONDS,
) -> list[Action]:
    """Reconcile this Manager's Workers against ground truth; return the
    actions taken, for the caller to say/record.

    Throttled per cycle (`throttle`); `at_start` forces it. Entirely
    best-effort: a failure here is said, never raised into the loop. Every
    dependency defaults to production and is injectable for the tests.
    """
    import time

    root = Path(root)
    now = time.time() if now is None else now
    key = (str(root), manager)
    if not at_start and now - _LAST_AT.get(key, 0.0) < throttle:
        return []
    _LAST_AT[key] = now

    workers_of = workers_of or (lambda: _mine(root, manager))
    facts_of = facts_of or (lambda worker: facts_for(root, worker))
    release = release or (lambda worker: _release(root, worker))

    from rite_ai.managers.telling import tell_manager

    def tell(text: str) -> None:
        try:
            tell_manager(root, manager, "a Worker rite reconciled", text)
        except OSError as e:
            say(f"could not tell {manager!r} a reconcile outcome: {e}")

    actions: list[Action] = []
    try:
        for worker in workers_of():
            try:
                action = plan(worker, facts_of(worker))
            except Exception as e:  # noqa: BLE001 - one Worker must not stop the rest
                say(f"{manager!r}: could not reconcile {worker!r}: {e}")
                continue
            if action.kind == RELEASE:
                result = release(worker)
                said = f"{worker}: {action.reason} — {result}"
                say(f"{manager!r}: reconciled {said}")
                tell(said)
                actions.append(action)
            elif action.kind == REPORT:
                said = f"{worker}: {action.reason}"
                say(f"{manager!r}: {said}")
                tell(said)
                actions.append(action)
    except Exception as e:  # noqa: BLE001 - reconcile must never break the cycle
        try:
            say(f"reconcile skipped this cycle: {type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
    return actions


def _release(root: Path, worker: str) -> str:
    """Release `worker`'s claims and forget its owner — the host-side action,
    the same `deliver` and `recovery` take (SCRUM-59's path, run here in the
    supervisor rather than asked for)."""
    from rite_ai.managers.lifecycle import forget_owner
    from rite_ai.publishing.deliver import _release_claims  # noqa: PLC2701

    out = _release_claims(root, worker)
    forget_owner(root, worker)
    return out


# ⚠ **Reaping dead self-test sandboxes is NOT done here (SCRUM-64 judgment
# call).** The plan wanted reconcile to run `reap_dead_selftest_sandboxes`,
# whose help wrongly claimed a supervisor's startup did it. But that reap
# runs `yoloai ls` — a blocking subprocess — and putting it on the supervise
# hot-start path added a real yoloai call to every start and broke the
# wait/deadline bounds tests by its latency. The scheduler tick already runs
# the reap periodically (`scheduler._run_tick_locked`), which is where a
# crash-safety net belongs; reconcile stays claim reconciliation, the
# measured dogfood bug.
