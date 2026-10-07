"""The failures the supervisor records for the Owner itself (SCRUM-71).

**What was wrong.** The `--record-issues` journal held 6 entries after the
a9 dogfood, all from the a7/a8 runs, and nothing at all from either a9
start. Across the whole cycle it never captured the failures that mattered:
the replay of a finished ticket (SCRUM-73), the Owner-answer relay race, the
misrouted Owner replies, the restart of a finished Worker. Every one of them
was found from OUTSIDE the run.

For a perpetual unattended Manager the journal is the Owner's primary
feedback channel — it is how you learn what went wrong while you were not
watching. **A journal that silently omits the biggest failures is worse than
no journal**, because it is read as a clean bill of health.

**Why the model cannot be the one to write these.** §9.15.2's trigger asks
the Manager to notice a document or a tool claiming one thing while the
system did another. A Manager cannot notice its own Worker being restarted
behind its back, a delivery refused on the host, or its own message never
reaching the person: all of those happen OUTSIDE its boundary, in the
supervisor, and the Manager only ever sees the result. So the supervisor
records them, from facts it holds, and the model is not asked.

## The four rules this module is built on

1. **A CLOSED vocabulary.** `EVENTS` is the whole set, and a class outside
   it is refused rather than written. An open `record(kind, text)` would
   become the dumping ground §9.15.0 exists to prevent, one call site at a
   time, and nothing would ever be able to say what the journal covers.

2. **Exactly one entry per event, for the life of the run.** Every one of
   these is reported from a loop that runs every cycle, for as long as the
   condition lasts — a Worker that stays stalled, a delivery that stays
   refused. Without a ledger the journal fills with one failure repeated
   four hundred times, which hides the other three. The ledger is a file in
   the Manager's own state directory — **never in the journal**, whose
   write-only rule `_ledger_path` explains — so a restarted Manager does
   not re-record what the previous process already filed.

3. **Off is off (§9.15.1).** No flag, no ledger, no directory, no writes.
   `recorder_for` hands back a callable that does nothing, so no call site
   needs to know.

4. **It never raises.** A journal that cannot be written must not end a run
   — the run is the thing producing the feedback. Every failure to record
   is said once and the cycle goes on.

⚠ **Two classes the ticket names are NOT here, deliberately.**
*Reconciliation actions* need SCRUM-64, which builds reconciliation; there is
nothing to record until it exists, and a declared-but-unwired class is a
claim about coverage that nothing honours. *A slot held by an idle sandbox*
is SCRUM-70's, for the same reason. Both are named in `NOT_YET` so the gap is
visible here rather than discovered by someone reading the journal and
finding neither.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

RELAY_FAILED = "relay-failed"
"""A message from or to the Manager did not reach the person it was for."""

DELIVERY_REFUSED = "delivery-refused"
"""A delivery the Manager asked for was refused on the host."""

GATE_REFUSED = "gate-refused"
"""The publish gate, or a governance check on it, held a delivery."""

WORKER_START_REFUSED = "worker-start-refused"
"""The broker refused a Manager's request to start a Worker."""

RECOVERY_ACTED = "recovery-acted"
"""A stalled Worker was restarted in place, or its ticket re-staged."""

RECOVERY_EXHAUSTED = "recovery-exhausted"
"""A stalled Worker ran out of recovery budget and was left STALLED."""

ROUTING_ANOMALY = "routing-anomaly"
"""A route or a reply went somewhere it should not have, or nowhere."""

RECONCILED = "reconciled"
"""A restarted Manager found its own state disagreeing with the ground truth
and acted on it, or could not (SCRUM-64's reconciliation).

⚠ Every one of these is a failure that ALREADY HAPPENED and went unnoticed:
a claim outliving the work it was taken for, or a sandbox gone with the work
still in it. The reconciler is the thing that notices, which is exactly why
the Owner should hear about it — a fleet that silently needs reconciling
every cycle is a fleet with a leak somewhere upstream."""

EVENTS = (
    RELAY_FAILED,
    DELIVERY_REFUSED,
    GATE_REFUSED,
    WORKER_START_REFUSED,
    RECOVERY_ACTED,
    RECOVERY_EXHAUSTED,
    ROUTING_ANOMALY,
    RECONCILED,
)
"""The whole set. See rule 1: a class outside this is refused."""

NOT_YET = {
    "idle-slot-held": "SCRUM-70 fixes the held slot; nothing reports one yet",
}
"""Classes the ticket names that nothing can record yet, and why.

⚠ Here rather than in `EVENTS`. A class declared and never written is a
claim about what the journal covers that nothing honours, and the whole
subject of this ticket is a channel claiming coverage it did not have.

`reconciliation` was here until SCRUM-64 landed and gave it something to
record; it is `RECONCILED` now. An entry leaving this dict is the shape to
want — it means the gap closed rather than the claim being quietly
widened."""

LEDGER_FILE = "recorded-issues.json"
MAX_REMEMBERED = 2000
"""How many event keys the ledger keeps. A bound, because the file is read
and written every cycle for the life of a perpetual run. The oldest are
dropped, so a condition that returns after 2000 other events is recorded
again — which is the right way round: re-recording an old failure costs a
duplicate entry, and forgetting to bound it costs the run."""

FORGET_AFTER_SECONDS = 7 * 24 * 3600
"""How long a recorded event stays suppressed.

⚠ **Without this the ledger never expires, and "once per run" quietly
becomes "once per project, ever"** (found by review, 2026-10-07). The keys
of some of these are deliberately low-cardinality — a broker refusal whose
subject is the Manager itself — so a failure fixed in October and
reintroduced in December would go unrecorded, and the journal would say
nothing about it.

Seven days is the trade between the two failures either side of it: dedup
that is too short re-files the whole backlog on every restart, which is the
noise rule 2 exists to prevent, and dedup that never expires is silence. A
week is longer than any run between restarts seen so far and far shorter
than a project."""


@dataclass(frozen=True)
class Event:
    """One recordable failure, as facts the supervisor holds.

    `subject` is what it happened to — a Worker, a ticket, a message id —
    and it is part of the identity, so the same failure on two Workers is
    two entries. `expected` says what should have happened, because an
    `observed` with nothing to compare it against is the shape §9.15.2
    refuses.
    """

    kind: str
    subject: str
    observed: str
    expected: str
    anchor: str = ""

    def key(self) -> str:
        """What makes this event the same event as another.

        The observed text is DIGESTED into the key, not compared whole: two
        refusals of the same delivery for different reasons are two
        failures, and a reason that carries a timestamp or a byte count
        would otherwise re-record every cycle.
        """
        digest = hashlib.sha256(self.observed.encode("utf-8", "replace")).hexdigest()
        return f"{self.kind}:{self.subject}:{digest[:16]}"


def _ledger_path(root: Path, manager: str) -> Path:
    """Where the "already recorded" keys live: the Manager's own state
    directory, beside `pending`'s ledger — **not** the journal directory.

    ⚠ **The journal stays WRITE-ONLY, and that is §9.15.5 rather than
    tidiness.** `test_nothing_in_rite_reads_the_journal` holds a stronger
    rule than "nothing parses an entry": no module outside `journal.py` may
    even LOCATE the directory, because "a module that can locate it is one
    line from reading it". The first version of this put the ledger inside
    the journal and read it every cycle, which broke that rule for a file
    that is not a journal entry at all.

    It is also better on its own terms: the journal directory is what a
    person COPIES to share entries, and `rite journal export` brings it into
    the project. The recorder's bookkeeping has no business travelling with
    the notebook.
    """
    from rite_ai.managers import manager_dir

    return manager_dir(Path(root), manager) / LEDGER_FILE


def _read(path: Path) -> dict[str, float]:
    """`{event key: when it was recorded}`.

    Unreadable is treated as EMPTY, which can only ever cause a duplicate
    entry. The other way round — treating it as "everything already
    recorded" — would silently stop recording, which is this ticket's own
    defect. A key whose time cannot be read is kept with time 0, so it
    expires at the next read rather than suppressing for ever.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    recorded = loaded.get("recorded")
    if isinstance(recorded, list):
        # The first shape, a bare list. Read rather than discarded so an
        # upgrade mid-run does not re-file everything already filed.
        return {k: 0.0 for k in recorded if isinstance(k, str)}
    if not isinstance(recorded, dict):
        return {}
    out: dict[str, float] = {}
    for key, at in recorded.items():
        if isinstance(key, str):
            out[key] = float(at) if isinstance(at, (int, float)) else 0.0
    return out


def _live(keys: dict[str, float], now: float) -> dict[str, float]:
    """The keys still inside `FORGET_AFTER_SECONDS`, newest last."""
    fresh = {k: at for k, at in keys.items() if now - at < FORGET_AFTER_SECONDS}
    ordered = sorted(fresh.items(), key=lambda item: item[1])
    return dict(ordered[-MAX_REMEMBERED:])


def _write(path: Path, keys: dict[str, float], now: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"recorded": _live(keys, now)}) + "\n"
    path.write_text(body, encoding="utf-8")


def recorder_for(root: Path, manager: str, *, enabled: bool, say=None):
    """A `record(Event) -> bool` for this run, or a no-op when off.

    True means an entry was written; False means it was a repeat, refused,
    or could not be written — and every one of those is said, once, through
    `say` when there is one.

    ⚠ The returned callable swallows everything. It is called from the
    supervise loop's own boundary steps, and a journal write is not a reason
    for a run to end.
    """
    if not enabled:
        return lambda _event: False

    root = Path(root)

    def record(event: Event) -> bool:
        try:
            return _record(root, manager, event, say)
        except Exception as e:  # noqa: BLE001 - see the docstring
            _complain(say, f"could not record a {event.kind!r} entry: {e!r}")
            return False

    return record


def _complain(say, text: str) -> None:
    if callable(say):
        try:
            say(f"journal: {text}")
        except Exception:  # noqa: BLE001 - nothing is worth raising from here
            pass


def _record(root: Path, manager: str, event: Event, say) -> bool:
    from rite_ai.managers import journal

    if event.kind not in EVENTS:
        # Refused, not written. See rule 1: an open vocabulary is how this
        # becomes a dumping ground one call site at a time.
        _complain(
            say,
            f"{event.kind!r} is not one of the recordable events "
            f"({', '.join(EVENTS)}); nothing was recorded",
        )
        return False

    import time

    now = time.time()
    path = _ledger_path(root, manager)
    keys = _live(_read(path), now)
    key = event.key()
    if key in keys:
        return False

    anchor = event.anchor.strip() or f"{event.kind} on {event.subject}"
    written = journal.write_observation(
        root,
        manager=manager,
        anchor=anchor,
        observed=event.observed,
        expected=event.expected,
        # ⚠ NOTHING INFERRED, ever, from here. §9.15.4's inverted-metric
        # rule: the supervisor records what it saw and what it expected, and
        # draws no conclusion. An `inferred` written by rite would be rite
        # grading its own run.
        inferred="",
    )
    if not written.ok:
        _complain(say, written.message)
        return False

    # Remembered only after the entry is on disk. The other order loses an
    # entry on a failed write and never tries again — the shape `journal._write`
    # exists to prevent for its own files.
    _write(path, {**keys, key: now}, now)
    return True


__all__ = [
    "DELIVERY_REFUSED",
    "FORGET_AFTER_SECONDS",
    "RECONCILED",
    "EVENTS",
    "GATE_REFUSED",
    "LEDGER_FILE",
    "MAX_REMEMBERED",
    "NOT_YET",
    "RECOVERY_ACTED",
    "RECOVERY_EXHAUSTED",
    "RELAY_FAILED",
    "ROUTING_ANOMALY",
    "WORKER_START_REFUSED",
    "Event",
    "recorder_for",
]
