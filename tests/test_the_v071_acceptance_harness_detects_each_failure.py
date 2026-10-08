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
        owner="lead",
        planner="planner",
        gpu_workers=("gpu1",),
        pipeline_keys=("gpu-slug",),
        owner_key="owner-currency",
        decoy_key="done-decoy",
        kill_worker="alpha",
        # Read only by `no_claude_in_the_fleet` (the all-local scenario's check), so
        # the fixture's units are all local; the control below puts a Claude one in.
        engines={
            "lead": "local:large",
            "planner": "local:small",
            "gpu1": "local:small",
        },
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
    "pipeline_tickets_worked_by_gpu_workers": lambda ev: ev.events.append(
        {
            "event": "sandbox-started",
            "worker": "alpha",
            "ticket": "13",
            "sandbox": "rite-x-alpha",
            "at": 160,
        }
    ),
    "plans_written_by_planner_approved_by_owner": lambda ev: ev.decompositions[
        "gpu-slug"
    ].update(approved_by="planner"),
    "plans_approved_through_inbox": lambda ev: setattr(ev, "plan_review_requests", []),
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
    "pipeline_stages_in_order": lambda ev: ev.stage_log.__setitem__(
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
    "no_claude_in_the_fleet": lambda ev: ev.engines.update(alpha="claude"),
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


# ---- the two scenarios ----


FULL_GATE = ["all_local", "mixed"]
SMOKES = ["smoke_local", "smoke_mixed"]


def test_every_scenario_is_one_of_the_two_kinds():
    """Named, not discovered: a scenario added without a guard for its kind
    would otherwise be covered by neither of the two tests below."""
    from tools.e2e_v071.config import scenario_names

    assert scenario_names() == sorted(FULL_GATE + SMOKES)


def test_the_full_gate_scenarios_name_only_real_checks():
    for name in FULL_GATE:
        fleet = load(name)
        assert set(fleet.check_names) <= set(checks.BY_NAME), name
        keys = {t.key for t in fleet.tickets}
        assert set(fleet.pipeline_keys) <= keys and fleet.decoy_key in keys
        assert fleet.kill_worker in {w["name"] for w in fleet.workers}
        assert all(
            fleet.worker(fleet.ticket(k).for_worker).get("engine")
            for k in fleet.pipeline_keys
        )


def test_the_smoke_scenarios_are_one_gpu_ticket_and_no_inductions():
    """The happy-path smoke's shape, asserted so "minimal" cannot drift into
    "accidentally not testing the GPU Worker at all"."""
    from tools.e2e_v071 import smoke

    for name in SMOKES:
        fleet = load(name)
        assert fleet.smoke, name
        assert set(fleet.check_names) == set(smoke.CHECK_NAMES), name
        assert len(fleet.pipeline_keys) == 1, name
        assert len(fleet.tickets) == 1, name
        # No induced failure, so no kill and no stale-claim restart.
        assert fleet.kill_worker == "", name
        assert fleet.decoy_key is None, name
        # The one ticket must go to a Worker with an engine — a GPU Worker.
        key = fleet.pipeline_keys[0]
        worker = fleet.worker(fleet.ticket(key).for_worker)
        assert worker.get("engine"), name
        assert worker["name"] in fleet.gpu_workers, name
        # Delivery lands as a branch, so the app must have no remote.
        assert fleet.board_only_repo, name
        assert fleet.publish_strategy == "commit", name


def test_each_smoke_can_actually_pass_its_plan_review():
    """🔴 RL-6 by rite's own identity function, for the smokes too.

    A plan must be approved by a Manager that did not write it, running a
    DIFFERENT model where both are local. Get that wrong and the review stage
    can never be reached — and the symptom is not an error, it is a run that
    sits at `decomposed` until its two-hour deadline expires and reports a
    FAIL that looks like the model's fault.
    """
    from rite_ai.config.managers import model_identity

    for name in SMOKES:
        fleet = load(name)
        roles = {m["name"]: m for m in fleet.managers}
        # ⚠ author vs APPROVER, not author vs owner. RL-6/DD-3.5 require the
        # plan's approver to differ from its author; they say nothing about who
        # owns the Worker. This compared the author with the OWNER, which only
        # looked right while the shipped presets happened to put those two
        # apart — and it is the assumption that cost three stalled runs.
        author, approver = roles[fleet.planner], roles[fleet.approver]
        assert fleet.planner != fleet.approver, name
        author_engine = str(author.get("engine") or "claude")
        approver_engine = str(approver.get("engine") or "claude")
        if author_engine.startswith("local:") and approver_engine.startswith("local:"):
            assert model_identity(author["model"]) != model_identity(
                approver["model"]
            ), f"{name}: the plan's author and approver run the same model"
        else:
            # One Claude, one local: different engines are different reviewers.
            assert author_engine != approver_engine, name


def test_the_all_local_fleet_has_no_claude_and_an_independent_reviewer():
    """DD-3.5 and RL-6, by rite's own identity function: the plan's author and its
    approver are two local Managers on two DIFFERENT models. With one local Manager
    the review stage could never pass."""
    from rite_ai.config.managers import model_identity

    fleet = load("all_local")
    units = [*fleet.managers, *fleet.workers]
    assert all(str(u.get("engine", "")).startswith("local:") for u in units)
    roles = {m["name"]: m for m in fleet.managers}
    author, approver = roles[fleet.planner], roles[fleet.owner]
    assert fleet.planner != fleet.owner
    assert model_identity(author["model"]) != model_identity(approver["model"])
    assert "no_claude_in_the_fleet" in fleet.check_names
    assert fleet.owner_key is None


def test_the_mixed_fleet_is_the_plans_fleet_with_the_owner_ticket():
    fleet = load("mixed")
    assert fleet.owner_key == "owner-currency"
    assert fleet.kill_worker == "alpha" and fleet.pipeline_keys == ("gpu-slug",)
    assert "owner_answer_reached_worker" in fleet.check_names


# ---- the demonstration ----


def _run_on_disk(tmp_path):
    """A finished mixed run's records, as the harness writes them."""
    import json

    from tools.e2e_v071.runlog import RunDir

    run = RunDir(tmp_path / "mixed-20261007T000000Z")
    (run.project / ".rite").mkdir(parents=True)
    ev = good_run()
    (run.project / ".rite" / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in ev.events)
    )
    run.file("tickets.json").write_text(json.dumps(T))
    run.file("run.json").write_text(json.dumps({"scenario": "mixed", "repo": "o/r"}))
    for i in ev.inductions:
        run.file("inductions.jsonl").open("a").write(json.dumps(i) + "\n")
    for o, at in zip(ev.owner_log, (1600, 1650, 1700)):
        run.file("owner.jsonl").open("a").write(json.dumps({"at": at, **o}) + "\n")
    plans = [
        (
            200,
            {
                "decomposed_by": "planner",
                "approval": "pending",
                "approved_by": "",
                "subtasks": [{"id": "s1", "status": "planned"}],
            },
        ),
        (
            300,
            {
                "decomposed_by": "planner",
                "approval": "approved",
                "approved_by": "lead",
                "subtasks": [{"id": "s1", "status": "planned"}],
            },
        ),
        (
            800,
            {
                "decomposed_by": "planner",
                "approval": "approved",
                "approved_by": "lead",
                "subtasks": [{"id": "s1", "status": "accepted"}],
            },
        ),
    ]
    for at, plan in plans:
        run.file("plans.jsonl").open("a").write(
            json.dumps({"at": at, "key": "gpu-slug", "plan": plan}) + "\n"
        )
    run.file("commands.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "at": 10 + n,
                    "phase": "setup",
                    "argv": ["rite", "refine", "accept", tid],
                }
            )
            + "\n"
            for n, tid in enumerate(T.values())
        )
    )
    return run


def test_the_timeline_is_in_time_order_and_shows_each_induction(tmp_path):
    from tools.e2e_v071 import demo

    tl = demo.timeline(_run_on_disk(tmp_path))
    assert [e["at"] for e in tl] == sorted(e["at"] for e in tl)
    induced = [e["text"] for e in tl if e["kind"] == "induce"]
    assert any("killed alpha" in t for t in induced)
    assert any("restarted lead" in t for t in induced)
    assert any(e["kind"] == "delivered" and "pull/2" in e["text"] for e in tl)


def test_the_pipeline_shows_what_is_proven_and_never_ticks_what_72_has_not_defined(
    tmp_path,
):
    from tools.e2e_v071 import demo

    rows = demo.pipeline(_run_on_disk(tmp_path), load("mixed"))
    gpu = rows["gpu-slug"]
    assert gpu["refine"]["at"] and gpu["plan"]["by"] == "planner"
    assert gpu["review"]["by"] == "lead" and gpu["execute"]["at"] == 800
    assert gpu["delivered"]["pr"] == URL.format(2)
    for col in ("spec", "approach", "recompose"):
        assert gpu[col] == {"state": demo.AWAITING}
    assert rows["claude-total"]["plan"]["state"].startswith("n/a")


def test_the_pipeline_ticks_72s_stages_once_its_log_exists(tmp_path):
    from tools.e2e_v071 import demo

    rows = demo.pipeline(
        _run_on_disk(tmp_path), load("mixed"), stage_log=good_run().stage_log
    )
    assert all(
        rows["gpu-slug"][c].get("state") == "logged"
        for c in ("spec", "approach", "recompose")
    )


def test_the_demo_page_is_self_contained_and_carries_the_run(tmp_path):
    import json

    from tools.e2e_v071 import demo

    run = _run_on_disk(tmp_path)
    run.file("report.json").write_text(
        json.dumps(
            {
                "verdict": "INCOMPLETE",
                "results": [
                    {
                        "name": "x",
                        "status": "PENDING",
                        "evidence": "e",
                        "criterion": "c",
                    }
                ],
            }
        )
    )
    page = demo.write_demo(run, load("mixed")).read_text()
    assert "__DATA__" not in page and "__TITLE__" not in page
    assert "killed alpha" in page and "INCOMPLETE" in page
    assert "<script src" not in page and "https://cdn" not in page
    index = demo.write_gate_index(
        [(run, load("mixed"))], tmp_path / "gate.html"
    ).read_text()
    assert "INCOMPLETE" in index and "demo.html" in index


def test_replay_prints_the_run_and_its_verdict(tmp_path, capsys):
    import json

    from tools.e2e_v071 import demo

    run = _run_on_disk(tmp_path)
    run.file("report.json").write_text(json.dumps({"verdict": "PASS", "results": []}))
    demo.replay(run, load("mixed"), speed=1e9)
    out = capsys.readouterr().out
    assert "INDUCED: killed alpha" in out and "verdict: PASS" in out
