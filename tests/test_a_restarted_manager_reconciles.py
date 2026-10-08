"""A restarted Manager reconciles stale state instead of escalating it
(SCRUM-64).

🔴 The dogfood bug (a8, 2026-10-04): after a restart the Manager resumed a
stale claim for a Worker whose sandbox was gone and whose ticket was already
delivered, and asked the Owner to fix it every cycle. The named acceptance
test below is that exact scenario, and it must converge — the claim released,
nothing escalated. The controls are a live Worker (claim untouched) and a
sandbox rite cannot ask about (no action).
"""

from __future__ import annotations

import pytest

from rite_ai.managers import reconcile
from rite_ai.managers.reconcile import HELD, RELEASE, REPORT, Facts

# --- the planner, from facts alone -------------------------------------------------


@pytest.mark.parametrize(
    "facts, kind",
    [
        (Facts(has_claim=True, sandbox_gone=True, work_landed=True), RELEASE),
        (Facts(has_claim=True, sandbox_gone=True, work_landed=False), REPORT),
        (Facts(has_claim=True, sandbox_gone=False, work_landed=True), HELD),
        (Facts(has_claim=True, sandbox_gone=None, work_landed=True), HELD),
        (Facts(has_claim=False, sandbox_gone=True, work_landed=True), HELD),
    ],
)
def test_plan_releases_only_when_gone_and_landed(facts, kind):
    assert reconcile.plan("alpha", facts).kind == kind


def test_cannot_tell_the_sandbox_is_never_an_action():
    """The whole safety rule: 'unknown' (yoloAI unreachable) holds, it does
    not release."""
    a = reconcile.plan(
        "alpha", Facts(has_claim=True, sandbox_gone=None, work_landed=False)
    )
    assert a.kind == HELD and "could not tell" in a.reason


# --- the applier, dependencies injected --------------------------------------------


def _run(facts_by_worker, released, *, at_start=True):
    said = []
    actions = reconcile.reconcile(
        tmp_root(),
        "lead",
        said.append,
        at_start=at_start,
        now=1000.0,
        workers_of=lambda: sorted(facts_by_worker),
        facts_of=lambda w: facts_by_worker[w],
        release=lambda w: released.append(w) or "released 2 claim(s)",
    )
    return actions, said, released


def tmp_root():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp())


def test_the_dogfood_scenario_converges_without_escalating():
    """Stale claim + delivered work + dead sandbox: the claim is released,
    and nothing is asked of the Owner."""
    released = []
    facts = {"alpha": Facts(has_claim=True, sandbox_gone=True, work_landed=True)}
    actions, said, released = _run(facts, released)
    assert [a.kind for a in actions] == [RELEASE]
    assert released == ["alpha"]
    # No escalation: nothing reconcile said is a request to the Owner to run
    # a host command.
    blob = " ".join(said).lower()
    assert "rite sandbox" not in blob and "ask the owner" not in blob


def test_a_live_workers_claim_is_untouched():
    released = []
    facts = {"beta": Facts(has_claim=True, sandbox_gone=False, work_landed=True)}
    actions, said, released = _run(facts, released)
    assert actions == [] and released == []


def test_an_unknown_sandbox_gets_no_action():
    released = []
    facts = {"gamma": Facts(has_claim=True, sandbox_gone=None, work_landed=False)}
    actions, said, released = _run(facts, released)
    assert actions == [] and released == []


def test_a_gone_sandbox_with_undelivered_work_is_reported_not_released():
    released = []
    facts = {"delta": Facts(has_claim=True, sandbox_gone=True, work_landed=False)}
    actions, said, released = _run(facts, released)
    assert [a.kind for a in actions] == [REPORT]
    assert released == []
    assert "NOT" in " ".join(said) or "not been delivered" in " ".join(said)


def test_one_worker_failing_does_not_stop_the_rest():
    released = []

    def facts_of(w):
        if w == "bad":
            raise RuntimeError("yoloai blew up")
        return Facts(has_claim=True, sandbox_gone=True, work_landed=True)

    said = []
    reconcile.reconcile(
        tmp_root(),
        "lead",
        said.append,
        at_start=True,
        now=1.0,
        workers_of=lambda: ["bad", "good"],
        facts_of=facts_of,
        release=lambda w: released.append(w) or "released 1 claim(s)",
    )
    assert released == ["good"]
    assert any("could not reconcile 'bad'" in s for s in said)


# --- the per-cycle throttle --------------------------------------------------------


def test_the_per_cycle_pass_is_throttled_but_start_is_not():
    reconcile._LAST_AT.clear()
    root = tmp_root()
    facts = {"alpha": Facts(has_claim=False, sandbox_gone=True, work_landed=True)}
    calls = []

    def once(*, at_start, now):
        return reconcile.reconcile(
            root,
            "lead",
            lambda s: None,
            at_start=at_start,
            now=now,
            workers_of=lambda: calls.append(now) or list(facts),
            facts_of=lambda w: facts[w],
            release=lambda w: "",
            throttle=300.0,
        )

    once(at_start=False, now=1000.0)  # runs, sets the clock
    once(at_start=False, now=1100.0)  # within the window: skipped
    once(at_start=False, now=1400.0)  # past it: runs
    once(at_start=True, now=1450.0)  # start forces a run regardless
    assert calls == [1000.0, 1400.0, 1450.0]


# --- the real fact-gathering and release, end to end -------------------------------


def test_the_named_scenario_with_real_claims_and_handback(tmp_path, monkeypatch):
    """Not injected facts: a real claim, a real handback (work landed), and a
    sandbox yoloAI reports gone — the real `facts_for`/`_release` path
    releases the claim and forgets the owner."""
    from rite_ai import handback
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.managers import lifecycle

    (tmp_path / ".rite").mkdir()
    ledger = ClaimsLedger(tmp_path / ".rite" / "claims.json")
    # ⚠ The claim NAMES THE TICKET, as every `rite claim` does. The first
    # version of this test claimed with no ticket and still expected a
    # release, which is the loose match the follow-up removed: landing is
    # matched against the claim's own ticket now, so a ticket-less claim is
    # reported rather than released (see the ticket-less test below).
    ledger.claim(["src/a.py"], "alpha", "RT-1")
    handback.write(
        tmp_path, "alpha", ticket="RT-1", branch="RT-1", summary="done", now=1.0
    )
    lifecycle.record_owner(tmp_path, "alpha", "lead")
    _listing(monkeypatch, {})
    said = []
    actions = reconcile.reconcile(
        tmp_path,
        "lead",
        said.append,
        at_start=True,
        now=1.0,
        workers_of=lambda: ["alpha"],
    )
    assert [a.kind for a in actions] == [reconcile.RELEASE]
    assert ledger.claims_for("alpha") == []
    assert lifecycle._owner_of(tmp_path, "alpha") == ""


def test_a_live_sandbox_keeps_the_real_claim(tmp_path, monkeypatch):
    from rite_ai.claims.ledger import ClaimsLedger

    (tmp_path / ".rite").mkdir()
    ledger = ClaimsLedger(tmp_path / ".rite" / "claims.json")
    ledger.claim(["src/a.py"], "alpha", "RT-1")
    _sandbox_named(monkeypatch, "alpha", "active", tmp_path)
    actions = reconcile.reconcile(
        tmp_path,
        "lead",
        lambda s: None,
        at_start=True,
        now=1.0,
        workers_of=lambda: ["alpha"],
    )
    assert actions == []
    assert [c.paths for c in ledger.claims_for("alpha")] == [["src/a.py"]]


# --- the real sandbox-liveness mapping ---------------------------------------------


@pytest.mark.parametrize(
    "status_value, known, expected",
    [
        ("not found", True, True),  # the only "gone"
        ("stopped", True, False),  # still holds its copy — NOT gone
        ("active", True, False),
        ("idle", True, False),
        ("unknown — yoloai ls failed", False, None),  # cannot tell
    ],
)
def test_sandbox_gone_is_only_not_found(
    tmp_path, monkeypatch, status_value, known, expected
):

    if known:
        listing = (
            _listing(monkeypatch, {})
            if status_value == "not found"
            else _sandbox_named(monkeypatch, "alpha", status_value, tmp_path)
        )
    else:
        listing = _listing(monkeypatch, {}, known=False, unknown=status_value)
    assert reconcile._sandbox_gone(tmp_path, "alpha", lambda: listing) is expected


# --- a REPORT reaches the Manager's inbox, not only the terminal -------------------


def test_a_report_is_told_to_the_manager(tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root, manager, about, text: told.append(text),
    )
    facts = {"delta": Facts(has_claim=True, sandbox_gone=True, work_landed=False)}
    reconcile.reconcile(
        tmp_path,
        "lead",
        lambda s: None,
        at_start=True,
        now=1.0,
        workers_of=lambda: list(facts),
        facts_of=lambda w: facts[w],
        release=lambda w: "",
    )
    assert told and "NOT been delivered" in told[0]


def test_a_release_is_told_to_the_manager(tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root, manager, about, text: told.append(text),
    )
    facts = {"alpha": Facts(has_claim=True, sandbox_gone=True, work_landed=True)}
    reconcile.reconcile(
        tmp_path,
        "lead",
        lambda s: None,
        at_start=True,
        now=1.0,
        workers_of=lambda: list(facts),
        facts_of=lambda w: facts[w],
        release=lambda w: "released 1 claim(s)",
    )
    assert told and "stale" in told[0]


# --- the supervisor actually runs it, at start and per cycle -----------------------


def test_the_supervisor_runs_reconcile_at_start_and_per_cycle():
    import inspect

    from rite_ai.managers import supervise

    src = inspect.getsource(supervise._supervise)
    start = src.index("reconcile.reconcile(root, manager, say, at_start=True)")
    first_session = src.index("stalled: _Stalled | None = None")
    assert start < first_session, "the start pass must run before the first session"
    # And a per-cycle pass at the boundary, after deliveries, before starts.
    cycle = src.index("reconcile.reconcile(root, manager, say)")
    deliveries = src.index("honour_deliveries(root, manager, say)")
    starts = src.index("_honour_worker_requests(root, manager, broker, say)", cycle)
    assert deliveries < cycle < starts


# ===================================================================================
# The SCRUM-64 follow-up: what counts as "landed", matched exactly.
#
# 🔴 The blocking finding of the review of the SCRUM-64 commit. `_work_landed`
# matched by WORKER NAME ALONE against three signals that do not mean landed,
# and the worst of them was inverted: a WATCHED pull request was read as
# landed, when `merging` drops an entry the moment GitHub says merged — so an
# entry that is still there is a PR still OPEN, which is precisely the claim
# D-41 holds until it merges. `deliver` opens the PR and destroys the sandbox
# in one call, so the FIRST `pull_request` delivery met "sandbox gone + a
# watched PR" and released the paths to the next Worker with the work
# unmerged.
#
# Every test below is the behaviour, not the spelling: each one is red under
# the loose match and green under the exact one.
# ===================================================================================


def _project(tmp_path):
    """A root with the two records `_landed` reads, both empty."""
    (tmp_path / ".rite").mkdir(exist_ok=True)
    return tmp_path


def _claimed(tmp_path, worker="alpha", ticket="RT-1", paths=("src/a.py",)):
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(_project(tmp_path) / ".rite" / "claims.json")
    ledger.claim(list(paths), worker, ticket)
    return ledger


def _listing(monkeypatch, by_name=None, *, known=True, unknown=""):
    """Patch the ONE `yoloai ls --json` a reconcile pass makes.

    ⚠ Patched here and not `worker_sandbox_status`: that is no longer on the
    path (SCRUM-64 follow-up — it was one subprocess PER WORKER), and a test
    patching it would silently let the real `yoloai ls` run. Measured when
    this changed: the file went from under a second to 17.
    """
    from rite_ai.sandbox import SandboxListing

    listing = SandboxListing(dict(by_name or {}), known=known, unknown=unknown)
    monkeypatch.setattr("rite_ai.sandbox.list_sandboxes", lambda *a, **kw: listing)
    return listing


def _sandbox_is_gone(monkeypatch):
    """No sandbox is listed at all, so every Worker's is `not found`."""
    return _listing(monkeypatch, {})


def _sandbox_named(monkeypatch, worker, status, root):
    """`worker`'s sandbox listed with `status`."""
    from rite_ai.sandbox import sandbox_name

    return _listing(monkeypatch, {sandbox_name(worker, root): status})


def _watch_a_pr(root, worker="alpha", ticket="RT-1", number=7):
    from rite_ai.publishing import merging

    merging.watch(
        root,
        merging.Watched(
            worker=worker,
            ticket=ticket,
            module="app",
            repo="o/r",
            number=number,
            head="deadbeef",
            manager="lead",
            auto_merge_at_start=False,
        ),
    )


def _run_real(root, worker="alpha", manager="lead"):
    said = []
    actions = reconcile.reconcile(
        root,
        manager,
        said.append,
        at_start=True,
        now=1.0,
        workers_of=lambda: [worker],
    )
    return actions, said


# --- 1. the dangerous case: an open PR is a veto, not evidence ---------------------


def test_an_open_watched_pr_is_never_landed(tmp_path, monkeypatch):
    """🔴 THE BLOCKING BUG. Sandbox gone + a watched PR + a `delivered`
    event: the loose match called this landed and released the claim while
    the pull request was open. The claim must survive."""
    ledger = _claimed(tmp_path)
    _watch_a_pr(tmp_path)
    from rite_ai.reporting import events

    events.record(tmp_path, "delivered", worker="alpha", ticket="RT-1", ok=True)
    _sandbox_is_gone(monkeypatch)

    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert [c.paths for c in ledger.claims_for("alpha")] == [["src/a.py"]]
    assert "still being watched" in " ".join(said)


def test_a_delivered_event_alone_is_not_landed(tmp_path, monkeypatch):
    """A delivery whose work is NOT where it goes yet (no `landed`) leaves the
    claim held: had it landed, `deliver` would have released it itself."""
    ledger = _claimed(tmp_path)
    from rite_ai.reporting import events

    events.record(
        tmp_path, "delivered", worker="alpha", ticket="RT-1", ok=True, landed=False
    )
    _sandbox_is_gone(monkeypatch)

    actions, _ = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert ledger.claims_for("alpha") != []


def test_a_delivered_event_from_before_the_landed_field_is_not_landed(
    tmp_path, monkeypatch
):
    """An event written by an older rite carries no `landed`, and no answer is
    not "landed"."""
    ledger = _claimed(tmp_path)
    from rite_ai.reporting import events

    events.record(tmp_path, "delivered", worker="alpha", ticket="RT-1")
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [REPORT]
    assert ledger.claims_for("alpha") != []


# --- 2. the match is on the ticket, not just the worker ---------------------------


def test_a_delivery_of_an_EARLIER_ticket_does_not_release_this_one(
    tmp_path, monkeypatch
):
    """The log is never pruned, so under the loose match a Worker that
    delivered anything, ever, had every later claim released. Here RT-0
    landed and RT-1 is the claim in flight."""
    ledger = _claimed(tmp_path, ticket="RT-1")
    from rite_ai.reporting import events

    events.record(
        tmp_path, "delivered", worker="alpha", ticket="RT-0", ok=True, landed=True
    )
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-0")
    _sandbox_is_gone(monkeypatch)

    actions, _ = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert [c.paths for c in ledger.claims_for("alpha")] == [["src/a.py"]]


def test_another_workers_landing_does_not_release_this_worker(tmp_path, monkeypatch):
    ledger = _claimed(tmp_path, worker="alpha", ticket="RT-1")
    from rite_ai.reporting import events

    events.record(tmp_path, "merged", worker="beta", ticket="RT-1", landed=True)
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [REPORT]
    assert ledger.claims_for("alpha") != []


def test_a_handback_for_a_DIFFERENT_ticket_does_not_release_this_one(
    tmp_path, monkeypatch
):
    """A handback is cleared when a Worker is started on new work, but a
    Worker whose start never cleared it would otherwise have had the new
    ticket's claim released by the old ticket's handback."""
    from rite_ai import handback

    ledger = _claimed(tmp_path, ticket="RT-2")
    handback.write(
        tmp_path, "alpha", ticket="RT-1", branch="RT-1", summary="done", now=1.0
    )
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [REPORT]
    assert ledger.claims_for("alpha") != []


def test_an_exact_ticket_prefix_is_not_a_match(tmp_path, monkeypatch):
    """No prefix match: a landing on RT-10 must not release RT-1's claim, nor
    the other way round."""
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    events.record(
        tmp_path, "delivered", worker="alpha", ticket="RT-10", ok=True, landed=True
    )
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [REPORT]
    assert ledger.claims_for("alpha") != []


# --- 3. what DOES release ----------------------------------------------------------


def test_a_merged_event_releases_the_claim(tmp_path, monkeypatch):
    """GitHub's own answer, recorded by `merging._tick` at the one point rite
    observes a merge."""
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-1", number=7)
    _sandbox_is_gone(monkeypatch)

    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [RELEASE]
    assert ledger.claims_for("alpha") == []
    assert "merged" in " ".join(said)


def test_a_delivery_that_landed_releases_the_claim(tmp_path, monkeypatch):
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    events.record(
        tmp_path, "delivered", worker="alpha", ticket="RT-1", ok=True, landed=True
    )
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [RELEASE]
    assert ledger.claims_for("alpha") == []


def test_a_readable_handback_for_this_ticket_releases_the_claim(tmp_path, monkeypatch):
    from rite_ai import handback

    ledger = _claimed(tmp_path, ticket="RT-1")
    handback.write(
        tmp_path, "alpha", ticket="RT-1", branch="RT-1", summary="done", now=1.0
    )
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [RELEASE]
    assert ledger.claims_for("alpha") == []


def test_a_merge_releases_even_though_the_handback_is_unreadable(tmp_path, monkeypatch):
    """A certain positive beats a cannot-tell: the PR merged, so the claim is
    stale whatever the handback file says."""
    from rite_ai import handback
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    handback.path_for(tmp_path, "alpha").parent.mkdir(parents=True, exist_ok=True)
    handback.path_for(tmp_path, "alpha").write_text("{not json", encoding="utf-8")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-1")
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [RELEASE]
    assert ledger.claims_for("alpha") == []


# --- 4. "cannot tell" is never a release -----------------------------------------


def test_an_unreadable_handback_is_cannot_tell_not_landed(tmp_path, monkeypatch):
    """`handback.read` returns an UNREADABLE record precisely to say "cannot
    tell"; the loose match counted it as landed because it was not None."""
    from rite_ai import handback

    ledger = _claimed(tmp_path, ticket="RT-1")
    handback.path_for(tmp_path, "alpha").parent.mkdir(parents=True, exist_ok=True)
    handback.path_for(tmp_path, "alpha").write_text("{not json", encoding="utf-8")
    _sandbox_is_gone(monkeypatch)

    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert ledger.claims_for("alpha") != []
    assert "cannot tell" in " ".join(said)


def test_an_unreadable_watch_record_is_cannot_tell(tmp_path, monkeypatch):
    """A PR for this ticket may be open in it, and an open PR is the veto."""
    from rite_ai.publishing.merging import _path

    ledger = _claimed(tmp_path, ticket="RT-1")
    _path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _path(tmp_path).write_text("{not json", encoding="utf-8")
    _sandbox_is_gone(monkeypatch)

    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert ledger.claims_for("alpha") != []
    assert "cannot be read" in " ".join(said)


def test_a_ticketless_claim_is_reported_never_released(tmp_path, monkeypatch):
    """A claim that names no ticket cannot be matched to any work. It is said
    once, with the host-side door, rather than released on a guess."""
    from rite_ai import handback
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(_project(tmp_path) / ".rite" / "claims.json")
    ledger.claim(["src/a.py"], "alpha")  # no ticket
    handback.write(
        tmp_path, "alpha", ticket="RT-1", branch="RT-1", summary="done", now=1.0
    )
    _sandbox_is_gone(monkeypatch)

    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert ledger.claims_for("alpha") != []
    assert "names no ticket" in " ".join(said)


def test_an_unreadable_claim_ledger_is_reported_not_silently_no_claim(
    tmp_path, monkeypatch
):
    """The loose version collapsed "cannot read the ledger" into "no claim to
    reconcile" and said nothing at all."""
    _project(tmp_path)
    (tmp_path / ".rite" / "claims.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(
        reconcile, "_claims_of", lambda root, worker: None
    )  # the ledger raising, whatever it raises
    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert "could not read the claim ledger" in " ".join(said)


def test_one_unlanded_ticket_holds_every_claim_the_worker_has(tmp_path, monkeypatch):
    """`_release_claims` releases a Worker's claims together, so a partial
    landing must not release the lot."""
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1", paths=("src/a.py",))
    ledger.claim(["src/b.py"], "alpha", "RT-2")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-1")
    _sandbox_is_gone(monkeypatch)

    actions, _ = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    assert len(ledger.claims_for("alpha")) == 2


def test_both_tickets_landed_releases(tmp_path, monkeypatch):
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    ledger.claim(["src/b.py"], "alpha", "RT-2")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-1")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-2")
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [RELEASE]
    assert ledger.claims_for("alpha") == []


def test_a_stopped_sandbox_still_holds_a_landed_ticket(tmp_path, monkeypatch):
    """Belt and braces with the exact match: landed work plus a sandbox that
    is merely stopped is still not a release."""
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-1")
    _sandbox_named(monkeypatch, "alpha", "stopped", tmp_path)
    assert _run_real(tmp_path)[0] == []
    assert ledger.claims_for("alpha") != []


def test_no_claim_asks_yoloai_nothing(tmp_path, monkeypatch):
    """A Worker holding nothing has nothing to reconcile, so the pass costs no
    `yoloai ls` for it — the per-Worker subprocess the start path pays."""
    _project(tmp_path)
    asked = []
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes",
        lambda *a, **kw: asked.append(a) or None,
    )
    facts = reconcile.facts_for(tmp_path, "alpha")
    assert facts.has_claim is False and asked == []
    assert reconcile.plan("alpha", facts).kind == HELD


# --- 5. a REPORT is told once per reason, not every 300 seconds -------------------


def test_an_unresolvable_report_is_told_once_not_every_pass(tmp_path, monkeypatch):
    """🔴 The stricter match makes REPORT the common outcome, so repeating it
    every cycle would rebuild the per-cycle escalation loop this module
    exists to stop. `merging`'s told-once-per-reason rule."""
    reconcile._LAST_AT.clear()
    reconcile._SAID.clear()
    told = []
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root, manager, about, text: told.append(text),
    )
    facts = {"delta": Facts(has_claim=True, sandbox_gone=True, work_landed=False)}

    def once(now):
        return reconcile.reconcile(
            tmp_path,
            "lead",
            lambda s: None,
            at_start=True,
            now=now,
            workers_of=lambda: list(facts),
            facts_of=lambda w: facts[w],
            release=lambda w: "",
        )

    assert [a.kind for a in once(1.0)] == [REPORT]
    assert once(400.0) == []  # same reason: not said again
    assert once(800.0) == []
    assert len(told) == 1

    # A CHANGED reason is told. Nothing is suppressed that the Manager has
    # not already heard.
    facts["delta"] = Facts(has_claim=True, sandbox_gone=True, work_landed=None)
    assert [a.kind for a in once(1200.0)] == [REPORT]
    assert len(told) == 2


def test_a_release_lets_the_next_report_through(tmp_path, monkeypatch):
    reconcile._LAST_AT.clear()
    reconcile._SAID.clear()
    told = []
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root, manager, about, text: told.append(text),
    )
    state = {"facts": Facts(has_claim=True, sandbox_gone=True, work_landed=False)}

    def once(now):
        return reconcile.reconcile(
            tmp_path,
            "lead",
            lambda s: None,
            at_start=True,
            now=now,
            workers_of=lambda: ["delta"],
            facts_of=lambda w: state["facts"],
            release=lambda w: "released 1 claim(s)",
        )

    once(1.0)  # REPORT, remembered
    state["facts"] = Facts(has_claim=True, sandbox_gone=True, work_landed=True)
    once(2.0)  # RELEASE, forgets it
    state["facts"] = Facts(has_claim=True, sandbox_gone=True, work_landed=False)
    assert [a.kind for a in once(3.0)] == [REPORT]
    assert len(told) == 3


# --- 6. only this Manager's Workers, by the record that cannot be forged ----------


def _project_with(monkeypatch, workers):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "rite_ai.config.parse.load_project",
        lambda root: SimpleNamespace(
            workers=[SimpleNamespace(name=n, manager=m) for n, m in workers]
        ),
    )


def test_a_worker_another_manager_owns_is_not_reconciled(tmp_path, monkeypatch):
    """🔴 `worker.yml` is inside the project tree, which every Manager's
    profile grants WRITABLE; the owner record is outside it and is
    `lifecycle._may_act`'s authority. Consulting it only for the unassigned
    let Manager B release Manager A's Worker's claims by editing one config
    line."""
    from rite_ai.managers import lifecycle

    _project_with(monkeypatch, [("alpha", "lead")])
    lifecycle.record_owner(tmp_path, "alpha", "second")

    said = []
    assert reconcile._mine(tmp_path, "lead", said.append) == []
    # And it IS the owning Manager's, though `worker.yml` says "lead": the
    # record outranks the config both ways.
    assert reconcile._mine(tmp_path, "second", said.append) == ["alpha"]
    # Not said: in a fleet, a Worker belonging to a peer is the ordinary
    # state, and a line per peer per pass is the noise this module reduces.
    assert said == []


def test_no_owner_record_keeps_the_generous_rule(tmp_path, monkeypatch):
    """The dogfood case: a Worker an older rite or a person started has no
    owner record, and must still be reconciled."""
    _project_with(monkeypatch, [("alpha", "lead"), ("beta", ""), ("gamma", "second")])
    assert reconcile._mine(tmp_path, "lead", lambda s: None) == ["alpha", "beta"]


def test_a_malformed_owner_record_skips_one_worker_not_the_pass(tmp_path, monkeypatch):
    """`_owner_of` raises ValueError for a record naming no Manager, and the
    first version caught only OSError — one bad record aborted every later
    Worker in the pass, permanently."""
    from rite_ai.managers import lifecycle

    _project_with(monkeypatch, [("alpha", "lead"), ("beta", "lead")])
    lifecycle._owners_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (lifecycle._owners_dir(tmp_path) / "alpha.json").write_text("{}", encoding="utf-8")

    said = []
    assert reconcile._mine(tmp_path, "lead", said.append) == ["beta"]
    assert "cannot tell which Manager" in " ".join(said)


# --- 7. a failing release does not abort the rest of the pass ---------------------


def test_a_failing_release_does_not_lose_the_remaining_workers(tmp_path, monkeypatch):
    """The release and the telling used to sit OUTSIDE the per-Worker `try`:
    anything thrown there abandoned every Worker after it."""
    reconcile._SAID.clear()
    landed = Facts(has_claim=True, sandbox_gone=True, work_landed=True)
    done = []

    def release(worker):
        if worker == "alpha":
            raise RuntimeError("the ledger exploded")
        done.append(worker)
        return "released 1 claim(s)"

    said = []
    actions = reconcile.reconcile(
        tmp_path,
        "lead",
        said.append,
        at_start=True,
        now=1.0,
        workers_of=lambda: ["alpha", "beta"],
        facts_of=lambda w: landed,
        release=release,
    )
    assert done == ["beta"]
    assert [a.worker for a in actions] == ["beta"]
    assert any("could not reconcile 'alpha'" in s for s in said)


# --- 8. the two events reconcile depends on are actually written -----------------


def test_deliver_records_whether_the_work_landed(tmp_path):
    """`reconcile` reads `landed`, and `deliver` must write it from the same
    condition on which it releases the claims itself."""
    import inspect

    from rite_ai.publishing import deliver

    src = inspect.getsource(deliver.deliver)
    assert "landed = bool(outcomes) and all(o.ok for o in outcomes)" in src
    assert "landed=landed," in src
    # And the release uses that same name, not a second copy of the condition.
    assert "if landed:\n        released = _release_claims(root, worker)" in src


def test_merging_records_a_merge_from_githubs_own_answer(tmp_path, monkeypatch):
    """Dropping the entry is how `_tick` says "merged" — and it drops a PR
    closed WITHOUT merging too, whose claims stay held on purpose. Without a
    positive record the two are indistinguishable afterwards."""
    from rite_ai.publishing import merging
    from rite_ai.reporting import events

    _project(tmp_path)
    _watch_a_pr(tmp_path, number=11)
    monkeypatch.setattr(merging, "_release_claims", lambda root, w: "released 1")
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root, manager, about, text: None,
    )

    class _Facts:
        merged = True
        state = "closed"

    merging.tick(
        tmp_path, "lead", lambda s: None, read=lambda repo, n: _Facts(), merge=None
    )
    recorded = [e for e in events.since(tmp_path, 0.0) if e["event"] == "merged"]
    assert [(e["worker"], e["ticket"], e["number"]) for e in recorded] == [
        ("alpha", "RT-1", 11)
    ]
    # And the entry is gone, which is why the event has to exist.
    assert merging._load(tmp_path) == []


def test_a_closed_unmerged_pr_records_no_merge(tmp_path, monkeypatch):
    """The control for the test above: the claims stay held, and nothing in
    the log says the work landed."""
    from rite_ai.publishing import merging
    from rite_ai.reporting import events

    _project(tmp_path)
    _watch_a_pr(tmp_path, number=12)
    monkeypatch.setattr(
        "rite_ai.managers.telling.tell_manager",
        lambda root, manager, about, text: None,
    )

    class _Facts:
        merged = False
        state = "closed"

    merging.tick(
        tmp_path, "lead", lambda s: None, read=lambda repo, n: _Facts(), merge=None
    )
    assert [e for e in events.since(tmp_path, 0.0) if e["event"] == "merged"] == []
    # And reconcile does not release on it: the entry is gone, but nothing
    # positive was ever recorded.
    assert reconcile._landed(tmp_path, "alpha", "RT-1")[0] is False


# --- 9. the local tier's claims name their ticket ---------------------------------


def test_the_local_tiers_subtask_claims_name_the_ticket(tmp_path):
    """A whole tier claiming `""` would be a tier whose claims no
    reconciliation could ever match to work."""
    from rite_ai.local.step import _LedgerClaims

    taken = []

    class _Ledger:
        def claim(self, paths, worker, ticket="", *, manager="", **kw):
            taken.append((worker, ticket, manager))

            class _R:
                ok = True

            return _R()

    _LedgerClaims(_Ledger(), manager="lead", ticket="RT-9").take(("src/a.py",), "alpha")
    assert taken == [("alpha", "RT-9", "lead")]


# --- 10. what the mutation run found missing --------------------------------------
#
# Each of the four below was written because a mutation SURVIVED the suite above:
# a guard with no test is not a guard.


def test_a_truthy_landed_that_is_not_true_is_not_landed(tmp_path, monkeypatch):
    """`is True`, not truthiness. A JSON log is a file: `"landed": "false"` is
    a string, and every string but the empty one is truthy in Python — so a
    truthiness test reads the word "false" as "it landed"."""
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    events.record(
        tmp_path, "delivered", worker="alpha", ticket="RT-1", ok=True, landed="false"
    )
    events.record(
        tmp_path, "delivered", worker="alpha", ticket="RT-1", ok=True, landed=1
    )
    _sandbox_is_gone(monkeypatch)

    assert reconcile._landed(tmp_path, "alpha", "RT-1")[0] is False
    assert [a.kind for a in _run_real(tmp_path)[0]] == [REPORT]
    assert ledger.claims_for("alpha") != []


def test_a_handback_ticket_is_not_matched_by_PREFIX(tmp_path, monkeypatch):
    """RT-10's handback must not release RT-1's claim. The event path has its
    own prefix test; this is the handback one, and `"RT-10".startswith("RT-1")`
    is True, which is why a prefix match here is not a theoretical worry."""
    from rite_ai import handback

    ledger = _claimed(tmp_path, ticket="RT-1")
    handback.write(
        tmp_path, "alpha", ticket="RT-10", branch="RT-10", summary="done", now=1.0
    )
    _sandbox_is_gone(monkeypatch)

    assert [a.kind for a in _run_real(tmp_path)[0]] == [REPORT]
    assert ledger.claims_for("alpha") != []


def test_an_unreadable_record_is_told_as_cannot_tell_not_as_undelivered(
    tmp_path, monkeypatch
):
    """🔴 The REASON is the point, not just the verdict. Both "cannot tell"
    and "not landed" hold the claim, so a reader that collapses them still
    looks safe — and then tells the Manager its Worker's work was never
    delivered when the truth is that rite cannot read its own watch record.
    Acting on that sentence is what the a8 dogfood did."""
    from rite_ai.publishing.merging import _path
    from rite_ai.reporting import events

    ledger = _claimed(tmp_path, ticket="RT-1")
    ledger.claim(["src/b.py"], "alpha", "RT-2")
    events.record(tmp_path, "merged", worker="alpha", ticket="RT-2")
    _path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _path(tmp_path).write_text("{not json", encoding="utf-8")
    _sandbox_is_gone(monkeypatch)

    actions, said = _run_real(tmp_path)
    assert [a.kind for a in actions] == [REPORT]
    blob = " ".join(said)
    assert "cannot tell whether its work landed" in blob
    assert "has NOT been delivered" not in blob
    assert len(ledger.claims_for("alpha")) == 2


def test_the_local_tier_passes_its_ticket_to_the_ledger(tmp_path):
    """Pinned on the call itself: the adapter's `take` must hand the ledger a
    ticket, in the ledger's own ticket position."""
    import inspect

    from rite_ai.local import step

    src = inspect.getsource(step._LedgerClaims.take)
    assert "self.ticket" in src, "the local tier's claims must name their ticket"


def test_a_pass_makes_ONE_yoloai_call_however_many_workers_it_has(
    tmp_path, monkeypatch
):
    """🔴 The SCRUM-64 follow-up review's cost finding. `_sandbox_gone` was
    one `yoloai ls --json` — a subprocess with a 30-second timeout — PER
    WORKER on the `rite start` path, which is the very per-start cost the
    SCRUM-64 commit message gives as its reason for keeping the self-test
    reap off that path. One listing answers for every Worker."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.sandbox import SandboxListing

    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    ledger = ClaimsLedger(tmp_path / ".rite" / "claims.json")
    for n in range(6):
        ledger.claim([f"src/{n}.py"], f"w{n}", f"RT-{n}")

    calls = []

    def listed(*a, **kw):
        calls.append(a)
        return SandboxListing({})  # nothing listed: every sandbox is gone

    monkeypatch.setattr("rite_ai.sandbox.list_sandboxes", listed)
    said = []
    reconcile._LAST_AT.clear()
    reconcile._SAID.clear()
    actions = reconcile.reconcile(
        tmp_path,
        "lead",
        said.append,
        at_start=True,
        now=1.0,
        workers_of=lambda: [f"w{n}" for n in range(6)],
    )
    assert len(actions) == 6, actions  # all six reported, none released
    assert len(calls) == 1, f"six Workers cost {len(calls)} yoloai calls"


def test_a_pass_with_nothing_to_reconcile_makes_NO_yoloai_call(tmp_path, monkeypatch):
    """Taken lazily: a project whose Workers hold no claims — the common case
    — pays nothing at all."""
    calls = []
    monkeypatch.setattr(
        "rite_ai.sandbox.list_sandboxes", lambda *a, **kw: calls.append(a) or None
    )
    (tmp_path / ".rite").mkdir(parents=True, exist_ok=True)
    reconcile._LAST_AT.clear()
    reconcile._SAID.clear()
    reconcile.reconcile(
        tmp_path,
        "lead",
        lambda s: None,
        at_start=True,
        now=1.0,
        workers_of=lambda: ["w0", "w1"],
    )
    assert calls == []
