"""The v0.7.1 acceptance harness (`tools/e2e_v071`) judges fixture runs correctly.

The gate itself is a live run and is not a CI test (V071_DOGFOOD_FIXES.md section 4.2).
What CI can hold is the JUDGE: each wired check passes on a run that met its
criterion and fails on one that did not — the control that proves a check can
detect the failure it exists for. A stubbed check reports PENDING, and PENDING
never makes the verdict PASS.

The second half pins the harness's reading of rite's records to rite's own code,
so a format change in rite turns this file red instead of making the gate read
nothing and report it as absence.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest
from tools.e2e_v071 import checks, harness
from tools.e2e_v071.checks import FAIL, PASS, PENDING, Evidence
from tools.e2e_v071.config import load
from tools.e2e_v071.observe import delivered_prs

T = {
    "claude-total": "11",
    "claude-format": "12",
    "gpu-slug": "13",
    "owner-currency": "14",
    "done-decoy": "15",
}
URL = "https://github.com/o/r/pull/{}"


def _delivered(ticket: str, worker: str, at: float, n: int) -> dict:
    note = f"Delivered app/{ticket}: collected into /x; pushed; PR {URL.format(n)}."
    return {
        "event": "delivered",
        "worker": worker,
        "ticket": ticket,
        "at": at,
        "outcomes": [note],
    }


def good_run() -> Evidence:
    """A run that met every wired criterion."""
    events = [
        {
            "event": "sandbox-started",
            "worker": "alpha",
            "ticket": "11",
            "sandbox": "rite-x-alpha",
            "at": 100,
        },
        {
            "event": "sandbox-restarted",
            "worker": "alpha",
            "sandbox": "rite-x-alpha",
            "at": 400,
        },
        _delivered("11", "alpha", 900, 1),
        {
            "event": "sandbox-started",
            "worker": "gpu1",
            "ticket": "13",
            "sandbox": "rite-x-gpu1",
            "at": 150,
        },
        _delivered("13", "gpu1", 950, 2),
        {
            "event": "sandbox-started",
            "worker": "alpha",
            "ticket": "12",
            "sandbox": "rite-x-alpha",
            "at": 1000,
        },
        _delivered("12", "alpha", 1400, 3),
        {
            "event": "sandbox-started",
            "worker": "alpha",
            "ticket": "14",
            "sandbox": "rite-x-alpha",
            "at": 1500,
        },
        _delivered("14", "alpha", 1900, 4),
    ]
    return Evidence(
        tickets=dict(T),
        gpu_worker="gpu1",
        claude_worker="alpha",
        owner="lead",
        planner="planner",
        events=events,
        claims_samples=[
            {"at": 1000, "claims": [{"worker": "gpu1", "ticket": "13"}]},
            {
                "at": 1050,
                "claims": [{"worker": "gpu1", "ticket": "13"}],
            },  # while lead is down
            {"at": 1300, "claims": []},  # after the restart
        ],
        start_requests=[{"worker": "alpha", "ticket": "11"}],
        recovery={"alpha": {"attempts": 1, "last_at": 400}},
        decompositions={
            "gpu-slug": {
                "decomposed_by": "planner",
                "approval": "approved",
                "approved_by": "lead",
                "subtasks": [
                    {"id": "s1", "status": "accepted"},
                    {"id": "s2", "status": "accepted"},
                ],
            }
        },
        owner_log=[
            {"kind": "answered", "qid": "qa1"},
            {"kind": "written-into-sandbox", "qid": "qa1"},
            {"kind": "acknowledged", "qid": "qa1"},
        ],
        inductions=[
            {"kind": "run-started", "at": 50},
            {
                "kind": "kill-sandbox",
                "worker": "alpha",
                "sandbox": "rite-x-alpha",
                "ticket": "11",
                "at": 300,
            },
            {"kind": "manager-stopped", "manager": "lead", "at": 1020},
            {
                "kind": "stale-while-down",
                "worker": "gpu1",
                "sandbox": "rite-x-gpu1",
                "ticket": "13",
                "at": 1030,
            },
            {"kind": "manager-restarted", "manager": "lead", "at": 1100},
        ],
        commands=[
            {"phase": "setup", "argv": ["rite", "refine", "accept", "11"]},
            {"phase": "run", "argv": ["rite", "message", "lead", "qa1 QABC123"]},
            {"phase": "run", "argv": ["yoloai", "stop", "rite-x-alpha"]},
        ],
        pr_diffs={"owner-currency": '+DEFAULT_CURRENCY = "QABC123"\n'},
        owner_answer="QABC123",
        run_started_at=50,
        stage_log={
            "gpu-slug": [
                "defined",
                "decomposed",
                "plan_reviewed",
                "approached",
                "executed",
                "step_reviewed",
                "approached",
                "executed",
                "step_reviewed",
                "recomposed",
                "delivery_requested",
            ]
        },
        lifecycle_requests=[
            {"op": "restart", "worker": "alpha", "at": 350, "by": "lead"}
        ],
        reconcile_reports=[
            {"at": 1150, "released": ["gpu1"], "told": True, "escalated": False}
        ],
        plan_review_requests=[{"ticket": "13", "to": "lead", "at": 200}],
    )


def by_name(ev: Evidence) -> dict:
    return {r.name: r for r in checks.evaluate(ev)}


def test_a_run_that_met_every_criterion_passes_every_check():
    results = by_name(good_run())
    not_passed = {
        n: (r.status, r.evidence)
        for n, r in results.items()
        if r.status != PASS and n != "induced_failures_journaled"
    }
    assert not_passed == {}
    # SCRUM-71's check stays PENDING until its event set is defined, even on a good run.
    assert results["induced_failures_journaled"].status == PENDING


def _broken(mutate) -> Evidence:
    ev = copy.deepcopy(good_run())
    mutate(ev)
    return ev


# Each control: one defect, and the check that exists to catch it must FAIL.
CONTROLS = {
    "every_ticket_delivered": lambda ev: ev.events.remove(
        next(
            e
            for e in ev.events
            if e.get("event") == "delivered" and e["ticket"] == "12"
        )
    ),
    "gpu_ticket_worked_by_gpu_worker": lambda ev: ev.events.append(
        {
            "event": "sandbox-started",
            "worker": "alpha",
            "ticket": "13",
            "sandbox": "rite-x-alpha",
            "at": 160,
        }
    ),
    "gpu_plan_by_planner_approved_by_owner": lambda ev: ev.decompositions[
        "gpu-slug"
    ].update(approved_by="planner"),
    "plan_approved_through_inbox": lambda ev: setattr(ev, "plan_review_requests", []),
    "owner_answer_reached_worker": lambda ev: setattr(
        ev, "pr_diffs", {"owner-currency": '+DEFAULT_CURRENCY = "EUR"\n'}
    ),
    "killed_sandbox_recovered": lambda ev: (
        ev.events.remove(
            next(e for e in ev.events if e["event"] == "sandbox-restarted")
        ),
        ev.recovery.clear(),
    ),
    "recovery_went_through_manager": lambda ev: setattr(ev, "lifecycle_requests", []),
    "restart_releases_stale_claim": lambda ev: ev.claims_samples.__setitem__(
        -1, {"at": 1300, "claims": [{"worker": "gpu1", "ticket": "13"}]}
    ),
    "restart_did_not_escalate": lambda ev: ev.reconcile_reports[0].update(
        escalated=True
    ),
    "done_ticket_never_offered": lambda ev: ev.claims_samples[0]["claims"].append(
        {"worker": "alpha", "ticket": "15"}
    ),
    "gpu_pipeline_stages_in_order": lambda ev: ev.stage_log.__setitem__(
        "gpu-slug",
        [
            "defined",
            "decomposed",
            "approached",
            "plan_reviewed",
            "executed",
            "step_reviewed",
            "recomposed",
            "delivery_requested",
        ],
    ),
    "no_forbidden_host_commands": lambda ev: ev.commands.append(
        {"phase": "run", "argv": ["/opt/bin/rite", "sandbox", "restart", "alpha"]}
    ),
}


@pytest.mark.parametrize("check", sorted(CONTROLS))
def test_each_check_fails_on_the_defect_it_exists_for(check):
    result = by_name(_broken(CONTROLS[check]))[check]
    assert result.status == FAIL, result.evidence


def test_every_check_but_the_pending_one_has_a_control():
    controlled = set(CONTROLS) | {"induced_failures_journaled"}
    assert controlled == {c.__name__ for c in checks.ALL}


def test_a_restart_with_nothing_stale_does_not_pass():
    """'Released after the restart' proves nothing if no claim was ever stale."""
    ev = _broken(lambda e: e.claims_samples.__setitem__(1, {"at": 1050, "claims": []}))
    assert by_name(ev)["restart_releases_stale_claim"].status == FAIL


def test_an_answer_acked_inside_one_poll_still_counts_as_delivered():
    """rite drops a qid from `_delivered` on the ack, so a fast ack is never seen
    there; the ack, plus the answer in the PR, is the evidence."""
    ev = _broken(
        lambda e: e.owner_log.remove({"kind": "written-into-sandbox", "qid": "qa1"})
    )
    assert by_name(ev)["owner_answer_reached_worker"].status == PASS


def test_an_answer_never_written_nor_acked_fails():
    ev = _broken(
        lambda e: setattr(e, "owner_log", [{"kind": "answered", "qid": "qa1"}])
    )
    assert by_name(ev)["owner_answer_reached_worker"].status == FAIL


def test_an_unread_decoy_is_not_a_pass():
    """No samples means the decoy was never looked at, which is not 'never offered'."""
    ev = _broken(lambda e: setattr(e, "claims_samples", []))
    assert by_name(ev)["done_ticket_never_offered"].status == FAIL


@pytest.mark.parametrize(
    "hook",
    ["stage_log", "lifecycle_requests", "reconcile_reports", "plan_review_requests"],
)
def test_a_fix_that_has_not_landed_is_pending_not_passed(hook):
    results = checks.evaluate(dataclasses.replace(good_run(), **{hook: None}))
    assert any(r.status == PENDING for r in results)
    assert checks.verdict(results) == "INCOMPLETE"


def test_any_failure_makes_the_verdict_fail_even_with_pendings():
    results = checks.evaluate(
        _broken(
            lambda e: (
                setattr(e, "stage_log", None)
                or e.commands.append(
                    {"phase": "run", "argv": ["rite", "deliver", "alpha"]}
                )
            )
        )
    )
    assert checks.verdict(results) == "FAIL"


def test_the_harness_running_rite_message_and_yoloai_stop_is_not_a_rescue():
    assert by_name(good_run())["no_forbidden_host_commands"].status == PASS


# ---- the harness's reading of rite, pinned to rite's own code ----


def test_the_pr_url_is_read_from_the_outcome_note_rite_writes():
    from rite_ai.publishing.deliver import Outcome

    note = Outcome("app", "13", True, f"collected; pushed; PR {URL.format(7)}").note()
    found = delivered_prs(
        [
            {
                "event": "delivered",
                "ticket": "13",
                "worker": "gpu1",
                "at": 1,
                "outcomes": [note],
            }
        ],
        "13",
    )
    assert [f["pr_url"] for f in found] == [URL.format(7)]


def test_a_not_delivered_outcome_is_not_read_as_a_pr():
    from rite_ai.publishing.deliver import Outcome

    note = Outcome("app", "13", False, f"gate refused; PR {URL.format(7)}").note()
    assert (
        delivered_prs(
            [{"event": "delivered", "ticket": "13", "outcomes": [note]}], "13"
        )
        == []
    )


def test_the_question_ledger_keys_the_owner_player_reads_are_rites():
    from rite_ai.managers import worker_questions as wq

    assert (wq.LEDGER_FILE, wq.OPEN_KEY, wq.DELIVERED_KEY, wq.CLOSED_KEY) == (
        "worker-questions.json",
        "_open",
        "_delivered",
        "_closed",
    )


def test_the_supervisor_state_the_harness_waits_on_is_spelled_as_rite_spells_it():
    from rite_ai.managers import routing

    assert routing.RUNNING == "running"
    assert callable(routing.supervisor_state)


def test_the_plan_fields_the_gpu_check_reads_are_the_ones_rite_renders():
    import json

    from rite_ai.local import decomposition as d

    plan = d.Decomposition(
        ticket="13",
        subtasks=(),
        decomposed_by="planner",
        approval=d.APPROVED,
        approved_by="lead",
    )
    body = json.loads(d.render(plan))
    assert {"decomposed_by", "approval", "approved_by", "subtasks"} <= set(body)
    assert body["approval"] == "approved"
    assert d.ACCEPTED == "accepted"


def test_the_sandbox_events_the_driver_reads_carry_worker_sandbox_and_ticket():
    import inspect

    from rite_ai import sandbox

    src = inspect.getsource(sandbox)
    assert (
        'events.record(root, "sandbox-started", '
        "worker=worker, sandbox=name, ticket=ticket)" in src
    )


def test_setup_reads_the_ticket_id_rite_board_create_prints():
    assert harness._created_id("created 42: https://github.com/o/r/issues/42\n") == "42"
    with pytest.raises(RuntimeError):
        harness._created_id("no ticket backend configured")


def test_the_fleet_is_the_plans_fleet():
    """Plan section 4.2: a Claude lead, a local planner, a Claude and a GPU Worker."""
    fleet = load()
    roles = {m["name"]: m for m in fleet.managers}
    assert roles["lead"]["preset"] == "lead" and not roles["lead"].get("engine")
    assert roles["planner"]["preset"] == "planner" and roles["planner"][
        "engine"
    ].startswith("local:")
    engines = sorted(bool(w.get("engine")) for w in fleet.workers)
    assert engines == [False, True]
    gpu = next(w for w in fleet.workers if w.get("engine"))
    assert gpu["agent"] == "goose" and gpu["context_window"] == 32768
    keys = [t.key for t in fleet.tickets]
    assert (
        sum(t.for_worker == "alpha" and not t.needs_owner_answer for t in fleet.tickets)
        == 2
    )
    assert sum(t.needs_owner_answer for t in fleet.tickets) == 1
    assert sum(t.decoy_done for t in fleet.tickets) == 1
    assert set(T) == set(keys)


def test_the_add_commands_carry_the_local_engine_flags():
    fleet = load()
    gpu = next(w for w in fleet.workers if w.get("engine"))
    argv = harness._worker_args(gpu)
    for flag in ("--engine", "--endpoint", "--model", "--agent", "--context-window"):
        assert flag in argv
    planner = next(m for m in fleet.managers if m["name"] == "planner")
    assert "--preset" in harness._manager_args(planner)
