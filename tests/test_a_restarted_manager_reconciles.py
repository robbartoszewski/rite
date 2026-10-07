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
    from rite_ai.sandbox import SandboxStatus

    (tmp_path / ".rite").mkdir()
    ledger = ClaimsLedger(tmp_path / ".rite" / "claims.json")
    ledger.claim(["src/a.py"], "alpha")
    handback.write(
        tmp_path, "alpha", ticket="RT-1", branch="RT-1", summary="done", now=1.0
    )
    lifecycle.record_owner(tmp_path, "alpha", "lead")
    monkeypatch.setattr(
        "rite_ai.sandbox.worker_sandbox_status",
        lambda w, r: SandboxStatus("not found"),
    )
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
    from rite_ai.sandbox import SandboxStatus

    (tmp_path / ".rite").mkdir()
    ledger = ClaimsLedger(tmp_path / ".rite" / "claims.json")
    ledger.claim(["src/a.py"], "alpha")
    monkeypatch.setattr(
        "rite_ai.sandbox.worker_sandbox_status",
        lambda w, r: SandboxStatus("active"),
    )
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
    from rite_ai.sandbox import SandboxStatus

    monkeypatch.setattr(
        "rite_ai.sandbox.worker_sandbox_status",
        lambda w, r: SandboxStatus(status_value, known=known),
    )
    assert reconcile._sandbox_gone(tmp_path, "alpha") is expected


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
