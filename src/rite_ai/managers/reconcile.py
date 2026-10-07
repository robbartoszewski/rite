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
read for certain. "Cannot tell" is never an action: an unknown sandbox, an
unreadable claim, a Worker still running — all leave the claim exactly as it
is. Every outcome is told to the Manager, so a restart converges to a true,
actionable state with no human in the loop.

Shaped like `recovery`: a pure planner (`plan`) decided entirely from facts,
and an applier (`reconcile`) whose every dependency is injectable, so the
policy is tested without a sandbox, a board or a claim ledger.

🔴 **What the first version of this module got wrong** (the `/code-review` of
the SCRUM-64 commit, re-run 2026-10-08; this file is its follow-up). "The
work landed" was matched **by worker name alone** against three signals, and
none of the three means what it was read to mean:

- **A watched pull request was read as landed, and it means the opposite.**
  `merging._tick` DROPS an entry the moment GitHub says the PR merged, so an
  entry that is still there is a PR still **open** — which is exactly the
  claim D-41 holds *until* the merge (`deliver`: "Under a push strategy it is
  the merge, so the claim stays held"). Under `pull_request` that fired on
  the **first** delivery: `deliver` opens the PR and destroys the sandbox in
  the same call, so the next pass saw "sandbox gone + a watched PR", called
  it landed, and handed the paths to the next Worker with the PR open and
  the first Worker's work unmerged. The dangerous reconciliation case, in
  the one branch written to prevent it.
- **A `delivered` event was read as a successful delivery.** `deliver`
  records that event on its single return — gate refusal, held host
  measurement, unpushable module and all. A refused delivery wrote one too.
- **Neither was scoped to a ticket**, and the event log is never pruned, so
  a delivery six tickets ago satisfied the claim of the ticket in flight.
- **An UNREADABLE handback counted as landed**, though `handback.read`
  returns that record precisely to say "cannot tell" — its own `done` is
  False for it.

**The rule now.** A claim is released only when **all** of these hold:

- the sandbox is known **gone** (`not found`, never merely `stopped`: a
  stopped sandbox still holds its copy);
- the claim **names a ticket**. A claim with no ticket cannot be matched to
  any work, so it is reported, never released;
- **every** ticket this Worker's claims name has landed — `_release_claims`
  releases the Worker's claims together, so one unlanded ticket holds them
  all;
- each matched **exactly**: `==` on the worker *and* on the ticket. Never a
  prefix, never a substring, never "any event this Worker ever wrote";
- and **no pull request for that ticket is still watched**. An open PR is a
  **veto** over every other signal, because that claim is being held for it
  on purpose.

**What counts as landed, and nothing else does:**

- a **readable** handback naming exactly that ticket;
- a **`merged`** event, which `merging._tick` writes from GitHub's own
  answer at the one point rite observes a merge;
- a **`delivered`** event carrying `landed: true` — the field `deliver`
  writes under exactly the condition on which it released the claims itself
  (every module ok, and nothing pushed anywhere a merge still has to
  happen).

⚠ **A `delivered` event on its own is not evidence, by construction.** Had
that delivery put the work where it goes, `deliver` would have released the
claim in the same call. So a claim still held after a delivery is held **on
purpose** — awaiting a merge, or awaiting a person's decision about a PR
that was closed without one. Releasing it is the harm, not the fix.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

RELEASE = "release"  # sandbox gone AND the work genuinely landed -> release
REPORT = "report"  # nothing to act on, but the Manager must hear it
HELD = "held"  # a live/unknown sandbox, or no claim -> leave it, say nothing

THROTTLE_SECONDS = 300.0
"""At most one reconcile pass this often per cycle, after the first at start."""


@dataclass(frozen=True)
class Facts:
    """What rite can read about one Worker right now. Each `None` is an
    explicit "cannot tell", distinct from True/False."""

    has_claim: bool | None  # None: the claim ledger could not be read
    sandbox_gone: bool | None  # None: yoloAI could not be asked
    work_landed: bool | None  # None: cannot tell whether it landed
    detail: str = ""
    """What the Manager is told besides the verdict: which signal decided
    it, or which one could not be read. Carried rather than re-derived in
    `plan`, which sees no files."""


@dataclass(frozen=True)
class Action:
    worker: str
    kind: str
    reason: str = ""
    outcome: str = ""
    """What the action's host-side step SAID, once it has been taken — for
    `RELEASE`, `_release`'s own words; "" for a `REPORT`, which takes none.

    ⚠ **Added so the journal cannot state the opposite of what happened
    (SCRUM-71).** `_release_claims` reports a failure in its return string
    ("its claims were NOT released (…)") rather than by raising, and this
    dataclass carried only the PLAN — so a recorded entry would have said a
    stale claim was released whether or not it was. That is the defect the
    same review found in `RecoveryAction`, and it is recorded verbatim here
    rather than classified: a boolean derived by matching rite's own prose
    is the dead-wiring trap one layer along."""


def plan(worker: str, facts: Facts) -> Action:
    """What to do about one Worker, from facts alone. Every branch that is
    not a certain release holds the claim."""
    tail = f" ({facts.detail})" if facts.detail else ""
    if facts.has_claim is None:
        return Action(
            worker,
            REPORT,
            "rite could not read the claim ledger, so nothing about this "
            f"Worker was reconciled{tail}",
        )
    if not facts.has_claim:
        return Action(worker, HELD, "no claim to reconcile")
    if facts.sandbox_gone is None:
        return Action(worker, HELD, "could not tell whether the sandbox is gone")
    if not facts.sandbox_gone:
        return Action(worker, HELD, "the sandbox is still there")
    if facts.work_landed is None:
        return Action(
            worker,
            REPORT,
            "its sandbox is gone, but rite cannot tell whether its work "
            f"landed, so the claim is kept rather than guessed away{tail}",
        )
    if facts.work_landed:
        return Action(
            worker,
            RELEASE,
            "its sandbox is gone and its work was delivered, merged or handed "
            f"back, so the claim it still holds is stale{tail}",
        )
    return Action(
        worker,
        REPORT,
        "its sandbox is gone but its work has NOT been delivered, merged or "
        "handed back; the claim is kept rather than handing the ticket on with "
        f"the work undelivered{tail}",
    )


# --- gathering the facts, on the host --------------------------------------------


def _claims_of(root: Path, worker: str) -> list | None:
    """The claims `worker` holds, or None if the ledger cannot be read (which
    is "cannot tell", never "no claim")."""
    from rite_ai.claims.ledger import ClaimsLedger

    try:
        return list(
            ClaimsLedger(Path(root) / ".rite" / "claims.json").claims_for(worker)
        )
    except Exception:  # noqa: BLE001 - unreadable ledger: cannot tell
        return None


def _sandbox_gone(root: Path, worker: str, listing_of=None) -> bool | None:
    """True only when yoloAI says the sandbox is not there; None when it
    could not be asked; False for any live state, `stopped` included (a
    stopped sandbox still holds its copy — it is not gone).

    ⚠ **`listing_of` is a CALLABLE returning ONE `yoloai ls --json` for the
    whole pass** (the SCRUM-64 follow-up review). Without it this is one
    subprocess with a 30-second timeout PER WORKER on the `rite start` path —
    the very per-start cost the SCRUM-64 commit cites for keeping the
    self-test reap off that path, added back by the thing that replaced it.

    A callable and not a listing, so the call happens where the DECISION to
    make it is: `facts_for` reaches here only for a Worker that holds a
    claim, so a project whose Workers hold none — the common case — pays
    nothing. Passing the listing itself would have made the caller fetch it
    before knowing whether anyone needed it, which is what the first cut of
    this did and what its test caught.
    """
    from rite_ai.sandbox import legacy_sandbox_name, list_sandboxes, sandbox_name

    listing = (listing_of or list_sandboxes)()
    status = listing.status_of(
        {sandbox_name(worker, root), legacy_sandbox_name(worker)}
    )
    if not status.known:
        return None
    return status.value == "not found"


def _landed(root: Path, worker: str, ticket: str) -> tuple[bool | None, str]:
    """Whether `worker`'s work on exactly `ticket` has landed, and the
    sentence that says which signal decided it.

    `None` is "cannot tell". Every comparison here is `==` on both the worker
    and the ticket: see the module docstring for what each signal does and
    does not establish, and what the loose version of this function released.
    """
    root = Path(root)
    if not ticket:
        return False, (
            "the claim names no ticket, so rite cannot match it to any "
            "delivered, merged or handed-back work; release it by hand if "
            "it is stale"
        )

    # 1. An open pull request VETOES every other signal, and an unreadable
    #    watch record vetoes just as hard: a PR for this ticket may be open in
    #    it, and the claim is held for exactly that.
    from rite_ai.publishing import merging

    watched = merging._load(root)  # noqa: SLF001
    if isinstance(watched, str):
        return None, f"the watched-pull-request record cannot be read ({watched})"
    for entry in watched:
        if getattr(entry, "worker", "") == worker and (
            getattr(entry, "ticket", "") == ticket
        ):
            return False, (
                f"pull request #{getattr(entry, 'number', '?')} for {ticket} is "
                "still being watched, so it is open and unmerged and D-41 holds "
                "the claim until it merges"
            )

    # 2. A merge rite saw, or a delivery that put the work where it goes.
    #    Both carry the ticket, and both are matched on it.
    from rite_ai.reporting import events

    for event in events.since(root, 0.0):
        if event.get("worker") != worker or event.get("ticket") != ticket:
            continue
        kind = event.get("event")
        if kind == "merged":
            return True, f"{ticket}'s pull request merged"
        # `is True` and not truthiness: an event written before this field
        # existed has no answer, and "no answer" is not "landed".
        if kind == "delivered" and event.get("landed") is True:
            return True, f"{ticket} was delivered and the work is where it goes"

    # 3. A handback, which must be READABLE and must name this ticket.
    from rite_ai import handback

    hb = handback.read(root, worker)
    if hb is not None and hb.unreadable:
        return None, f"its handback record cannot be read ({hb.unreadable})"
    if hb is not None and hb.done and hb.ticket == ticket:
        return True, f"it handed {ticket} back"

    return False, ""


def facts_for(root: Path, worker: str, listing_of=None) -> Facts:
    claims = _claims_of(root, worker)
    if claims is None:
        # No point asking yoloAI: `plan` cannot act without knowing the claim.
        return Facts(
            has_claim=None,
            sandbox_gone=None,
            work_landed=None,
            detail=f"{Path(root) / '.rite' / 'claims.json'} could not be read",
        )
    if not claims:
        # The common case, and it costs no `yoloai ls`: a Worker holding
        # nothing has nothing to reconcile whatever its sandbox is doing.
        return Facts(has_claim=False, sandbox_gone=None, work_landed=None)

    verdicts = [
        _landed(root, worker, ticket)
        for ticket in sorted({getattr(c, "ticket", "") for c in claims})
    ]
    if any(v is None for v, _ in verdicts):
        landed: bool | None = None
    elif all(v for v, _ in verdicts):
        landed = True
    else:
        landed = False
    return Facts(
        has_claim=True,
        sandbox_gone=_sandbox_gone(root, worker, listing_of),
        work_landed=landed,
        detail="; ".join(d for _, d in verdicts if d),
    )


def _mine(root: Path, manager: str, say=None) -> list[str]:
    """This Manager's Workers.

    🔴 **The owner record decides whenever it exists, and `worker.yml` only
    when it does not.** The record is `lifecycle._may_act`'s authority and it
    lives outside every Manager's grant; `worker.yml` is inside the project
    tree, which every Manager's profile grants WRITABLE (seatbelt's
    `enclosure.compose`, Landlock's `_fenced_project_paths`, which cannot
    deny). The first version read the record only for Workers `worker.yml`
    assigned to nobody, so one edited config line let Manager B reconcile —
    release the claims of, and `forget_owner` — a Worker rite had started for
    Manager A. Reading it for every Worker closes that both ways: it also
    gives a Worker back to the Manager it was genuinely started for when the
    config says otherwise.

    With no record, `worker_handbacks._mine`'s rule stands: the Manager
    `worker.yml` names, plus — so a project that nominates nobody still
    reconciles — those that name none. That is the dogfood case, where a
    Worker a person or an older rite started has no record at all.

    A record that exists and cannot be read is "cannot tell", so the Worker
    is not this Manager's to touch, and that is SAID: it is a fault. A Worker
    that is plainly another Manager's is not said, because in a fleet that is
    the ordinary steady state and a line per peer per pass is noise.
    """
    from rite_ai.config.parse import load_project
    from rite_ai.managers import lifecycle

    project = load_project(Path(root))
    workers = list(getattr(project, "workers", []) or [])
    mine: list[str] = []
    for worker in sorted(workers, key=lambda w: w.name):
        name = worker.name
        try:
            owner = lifecycle._owner_of(Path(root), name)  # noqa: SLF001
        except (OSError, ValueError) as e:
            # ⚠ ValueError too: `_owner_of` raises it for a record that names
            # no Manager, and the first version caught only OSError — one
            # malformed record aborted every later Worker in the pass.
            if say is not None:
                say(
                    f"{manager!r}: not reconciling {name!r} — rite cannot tell "
                    f"which Manager it belongs to ({e})"
                )
            continue
        if owner:
            if owner == manager:
                mine.append(name)
            continue
        if getattr(worker, "manager", "") in (manager, ""):
            mine.append(name)
    return mine


# --- the applier, run at the Manager's cycle boundary and at start ---------------


_LAST_AT: dict[tuple[str, str], float] = {}
"""When this process last ran a reconcile pass, per (project, Manager), for
the per-cycle throttle. A fresh process (a restart) has none, so the start
pass always runs."""

_SAID: dict[tuple[str, str, str], str] = {}
"""The last REPORT this process made about one Worker, per (project, Manager,
worker).

⚠ **Told once per reason, `merging`'s rule and for its reason.** A REPORT is
by definition something rite cannot resolve — an open PR, a ticket that never
landed, a ledger it cannot read — so it is still true on the next pass, and
the next, every 300 seconds. Saying it each time is the per-cycle escalation
loop this module exists to stop, and the stricter `_landed` above makes
REPORT the common outcome rather than the rare one. So it is said when the
reason CHANGES. A restart has an empty table and says everything once, which
is correct: a Manager that has just come up has not heard any of it.
"""


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

    workers_of = workers_of or (lambda: _mine(root, manager, say))
    if facts_of is None:
        # ⚠ **ONE `yoloai ls --json` for the whole pass**, and taken only if
        # some Worker turns out to hold a claim. Without this it was one
        # subprocess with a 30-second timeout per Worker on the `rite start`
        # path — the cost SCRUM-64's own commit message gives as its reason
        # for keeping the self-test reap off that path.
        listed: list = []

        def listing_of():
            if not listed:
                from rite_ai.sandbox import list_sandboxes

                listed.append(list_sandboxes())
            return listed[0]

        def facts_of(worker: str) -> Facts:
            return facts_for(root, worker, listing_of)

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
            said_key = (str(root), manager, worker)
            try:
                action = plan(worker, facts_of(worker))
                if action.kind == RELEASE:
                    result = release(worker)
                    told = f"{worker}: {action.reason} — {result}"
                    say(f"{manager!r}: reconciled {told}")
                    tell(told)
                    # A released claim is gone, so whatever was last reported
                    # about this Worker no longer holds: let the next REPORT
                    # through.
                    _SAID.pop(said_key, None)
                    # What HAPPENED, not what was planned (SCRUM-71): the
                    # journal records the release's own result, which is where
                    # "its claims were NOT released (…)" has to reach.
                    actions.append(replace(action, outcome=result))
                elif action.kind == REPORT:
                    told = f"{worker}: {action.reason}"
                    if _SAID.get(said_key) == told:
                        continue  # unchanged since the last pass: already told
                    _SAID[said_key] = told
                    say(f"{manager!r}: {told}")
                    tell(told)
                    actions.append(action)
            except Exception as e:  # noqa: BLE001 - one Worker must not stop the rest
                # ⚠ The release and the telling are INSIDE this, not after it:
                # a failure in either used to abort every remaining Worker and
                # lose the record of a claim already released.
                say(f"{manager!r}: could not reconcile {worker!r}: {e}")
                continue
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
