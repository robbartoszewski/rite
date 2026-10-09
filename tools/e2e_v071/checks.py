"""The gate's pass/fail criteria (V071_DOGFOOD_FIXES.md section 4.2), one function each.

Every check returns a Result with one of three statuses:
- PASS: the evidence named in `evidence` was found.
- FAIL: it was not, or the opposite was found.
- PENDING: the check needs a record a fix has not defined yet. **PENDING is never
  a pass.** The gate passes only when every check is PASS (`verdict`).

A check reads only `Evidence`: what the observer collected during the run plus
the run's own logs. So `harness.py check <run>` re-judges a finished run offline,
and the unit tests judge fixture runs, good and broken, with the same code.

The checks are scenario-generic: which ticket is the pipeline ticket, the Owner's,
or the decoy, and whose sandbox is killed, come from the scenario (`Evidence`), so
the mixed and the all-local scenario are judged by the same functions. A scenario
lists the checks that apply to it in its fleet.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.e2e_v071.observe import delivered_prs

PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"

# A command the HARNESS must never run during the run phase: each is something a
# person would do by hand to rescue the fleet, which the gate says nobody does.
FORBIDDEN_DURING_RUN = (
    ("rite", "sandbox"),
    ("rite", "deliver"),
    ("rite", "local"),
    ("rite", "plan"),
    ("rite", "request"),
    ("rite", "release"),
    ("rite", "claim"),
    ("rite", "done"),
    ("rite", "publish"),
    ("rite", "stop"),
    ("rite", "refine"),
    ("rite", "board", "move"),
    ("rite", "board", "label"),
    ("rite", "board", "assign"),
)

# The local tier's pipeline, as plan section 3.3a names its persisted stages.
STAGES = (
    "defined",
    "decomposed",
    "plan_reviewed",
    "approached",
    "executed",
    "step_reviewed",
    "recomposed",
    "delivery_requested",
)


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    evidence: str
    criterion: str = ""


@dataclass
class Evidence:
    """Everything a check may read. Built by `harness.collect` from a run."""

    tickets: dict  # key -> board id
    owner: str
    planner: str
    gpu_workers: tuple = ()
    pipeline_keys: tuple = ()
    owner_key: str | None = None
    decoy_key: str | None = None
    kill_worker: str = ""
    engines: dict = field(default_factory=dict)  # unit name -> engine, from config
    events: list = field(default_factory=list)
    claims_samples: list = field(default_factory=list)  # [{at, claims}]
    start_requests: list = field(default_factory=list)  # every one ever seen
    recovery: dict = field(default_factory=dict)
    decompositions: dict = field(default_factory=dict)  # key -> rendered plan | None
    owner_log: list = field(default_factory=list)  # owner.jsonl
    inductions: list = field(default_factory=list)  # inductions.jsonl
    commands: list = field(default_factory=list)  # commands.jsonl
    journal: list = field(default_factory=list)  # [{manager, name, mtime, text}]
    pr_diffs: dict = field(default_factory=dict)  # key -> diff text (gh)
    owner_answer: str = ""
    run_started_at: float = 0.0
    # Hooks a fix fills in. None means "this fix has not defined its record yet".
    stage_log: dict | None = None  # SCRUM-72: key -> [stage, ...] in order
    lifecycle_requests: list | None = None  # SCRUM-59: [{op, worker, at, by}]
    reconcile_reports: list | None = None  # SCRUM-64: [{at, released, told}]
    plan_review_requests: list | None = None  # SCRUM-72 3.3b: [{ticket, to, at}]

    @property
    def work_keys(self) -> list[str]:
        return [k for k in self.tickets if k != self.decoy_key]


def every_ticket_delivered(ev: Evidence) -> Result:
    c = "every ticket reaches a delivered PR"
    missing, seen = [], []
    for key in ev.work_keys:
        prs = delivered_prs(ev.events, ev.tickets[key])
        if prs:
            seen.append(f"{key}→{prs[-1]['pr_url']}")
        else:
            missing.append(f"{key} ({ev.tickets[key]})")
    if missing:
        return Result(
            "every_ticket_delivered",
            FAIL,
            "no delivered PR for: " + ", ".join(missing),
            c,
        )
    return Result("every_ticket_delivered", PASS, "; ".join(seen), c)


def pipeline_tickets_worked_by_gpu_workers(ev: Evidence) -> Result:
    c = "each pipeline ticket is worked, and delivered, by a GPU Worker only"
    name = "pipeline_tickets_worked_by_gpu_workers"
    notes = []
    for key in ev.pipeline_keys:
        tid = str(ev.tickets[key])
        starts = [
            e
            for e in ev.events
            if e.get("event") == "sandbox-started" and str(e.get("ticket")) == tid
        ]
        prs = delivered_prs(ev.events, tid)
        who = {e.get("worker") for e in starts} | {p["worker"] for p in prs}
        if not prs:
            return Result(
                name, FAIL, f"{key} ({tid}) not delivered; started by {sorted(who)}", c
            )
        if not who or not who <= set(ev.gpu_workers):
            return Result(name, FAIL, f"{key} was worked by {sorted(who)}", c)
        notes.append(f"{key}: {', '.join(sorted(who))}")
    return Result(name, PASS, "; ".join(notes), c)


def plans_written_by_planner_approved_by_owner(ev: Evidence) -> Result:
    c = "each pipeline ticket's plan is written by planner and approved by the Owner"
    name = "plans_written_by_planner_approved_by_owner"
    notes = []
    for key in ev.pipeline_keys:
        plan = ev.decompositions.get(key)
        if plan is None:
            return Result(
                name,
                FAIL,
                f"no decomposition found for {key} (if SCRUM-72 moved plan state, "
                "update observe.decomposition)",
                c,
            )
        problems = []
        if plan.get("decomposed_by") != ev.planner:
            problems.append(f"decomposed_by={plan.get('decomposed_by')!r}")
        if plan.get("approval") != "approved" or plan.get("approved_by") != ev.owner:
            problems.append(
                f"approval={plan.get('approval')!r} by {plan.get('approved_by')!r}"
            )
        open_ = [
            s["id"] for s in plan.get("subtasks", []) if s.get("status") != "accepted"
        ]
        if open_:
            problems.append(f"subtasks not accepted: {open_}")
        if problems:
            return Result(name, FAIL, f"{key}: " + "; ".join(problems), c)
        notes.append(f"{key}: {len(plan['subtasks'])} subtasks accepted")
    return Result(
        name, PASS, "; ".join(notes) + f" (by {ev.planner}, approved by {ev.owner})", c
    )


def plans_approved_through_inbox(ev: Evidence) -> Result:
    c = "each plan's approval went through the Owner's inbox (plan 3.3b)"
    name = "plans_approved_through_inbox"
    if ev.plan_review_requests is None:
        return Result(
            name,
            PENDING,
            "STUB-PENDING SCRUM-72: plan-review request record not defined yet",
            c,
        )
    for key in ev.pipeline_keys:
        tid = str(ev.tickets[key])
        asked = [
            r
            for r in ev.plan_review_requests
            if str(r.get("ticket")) == tid and r.get("to") == ev.owner
        ]
        if not asked:
            return Result(
                name, FAIL, f"no plan-review request to {ev.owner} for {key}", c
            )
    return Result(name, PASS, f"every pipeline plan was asked of {ev.owner}", c)


def owner_answer_reached_worker(ev: Evidence) -> Result:
    c = "the Owner's answer reaches its Worker"
    name = "owner_answer_reached_worker"
    answered = [r for r in ev.owner_log if r.get("kind") == "answered"]
    if not answered:
        return Result(
            name, FAIL, "the Worker never asked the Owner (no question was answered)", c
        )
    written = [r for r in ev.owner_log if r.get("kind") == "written-into-sandbox"]
    acked = [r for r in ev.owner_log if r.get("kind") == "acknowledged"]
    diff = ev.pr_diffs.get(ev.owner_key or "", "")
    in_pr = bool(ev.owner_answer) and ev.owner_answer in diff
    qid = answered[-1].get("qid")
    if not written and not acked:
        return Result(
            name, FAIL, f"answered {qid} but rite never wrote it into the sandbox", c
        )
    if not in_pr:
        return Result(
            name,
            FAIL,
            f"the delivered {ev.owner_key} PR does not contain the Owner's answer",
            c,
        )
    return Result(
        name,
        PASS,
        f"qid {qid}: written into the sandbox, "
        f"{'acknowledged, ' if acked else ''}and in the PR",
        c,
    )


def came_back(e: dict, kill: dict) -> bool:
    """Is event `e` the killed Worker coming back on the killed ticket? A restart of
    its sandbox, or a re-stage on the SAME ticket. A start on another ticket is the
    Worker moving on, not recovering."""
    if e.get("worker") != kill["worker"] or float(e.get("at", 0)) <= kill["at"]:
        return False
    if e.get("event") == "sandbox-restarted":
        return True
    return e.get("event") == "sandbox-started" and str(e.get("ticket")) == str(
        kill["ticket"]
    )


def killed_sandbox_recovered(ev: Evidence) -> Result:
    c = "a killed Worker sandbox is recovered, with no human"
    name = "killed_sandbox_recovered"
    kills = [i for i in ev.inductions if i.get("kind") == "kill-sandbox"]
    if not kills:
        return Result(name, FAIL, "the kill was never induced", c)
    k = kills[0]
    after = [e for e in ev.events if came_back(e, k)]
    rec = ev.recovery.get(k["worker"]) or {}
    if not after and float(rec.get("last_at") or 0) <= k["at"]:
        return Result(
            name,
            FAIL,
            f"{k['worker']} ({k['sandbox']}) killed at {k['at']:.0f}; "
            "no restart or re-stage after it",
            c,
        )
    delivered = [
        p
        for p in delivered_prs(ev.events, k["ticket"])
        if float(p.get("at") or 0) > k["at"]
    ]
    if not delivered:
        return Result(
            name, FAIL, f"recovered, but {k['ticket']} was not delivered afterwards", c
        )
    how = after[0]["event"] if after else "recovery.json"
    return Result(
        name, PASS, f"{k['worker']} came back ({how}) and delivered {k['ticket']}", c
    )


def recovery_went_through_manager(ev: Evidence) -> Result:
    c = "the recovery was the Manager's, through SCRUM-59's lifecycle request"
    name = "recovery_went_through_manager"
    if ev.lifecycle_requests is None:
        return Result(
            name,
            PENDING,
            "STUB-PENDING SCRUM-59: lifecycle request record not defined yet",
            c,
        )
    kills = [i for i in ev.inductions if i.get("kind") == "kill-sandbox"]
    if not kills:
        return Result(name, FAIL, "the kill was never induced", c)
    k = kills[0]
    asked = [
        r
        for r in ev.lifecycle_requests
        if r.get("worker") == k["worker"]
        and float(r.get("at", 0)) > k["at"]
        and r.get("op") in ("restart", "destroy", "status")
    ]
    if not asked:
        return Result(
            name,
            FAIL,
            f"no Manager lifecycle request for {k['worker']} after the kill",
            c,
        )
    return Result(name, PASS, f"{asked[0]['op']} requested by {asked[0].get('by')}", c)


def restart_releases_stale_claim(ev: Evidence) -> Result:
    c = "a Manager restart mid-run reconciles stale state"
    name = "restart_releases_stale_claim"
    r = [i for i in ev.inductions if i.get("kind") == "stale-while-down"]
    restarted = [i for i in ev.inductions if i.get("kind") == "manager-restarted"]
    if not r or not restarted:
        return Result(name, FAIL, "the restart with stale state was never induced", c)
    worker, stale_at, back = r[0]["worker"], r[0]["at"], restarted[0]["at"]

    def holds(sample: dict) -> bool:
        return any(x.get("worker") == worker for x in sample["claims"])

    # The scenario must really have been stale: the claim held while the Manager
    # was down, after the sandbox was destroyed. Otherwise "released" proves nothing.
    during = [s for s in ev.claims_samples if stale_at <= s["at"] <= back]
    if not any(holds(s) for s in during):
        return Result(
            name,
            FAIL,
            f"{worker} held no claim while the Manager was down, so nothing was stale",
            c,
        )
    later = [s for s in ev.claims_samples if s["at"] > back]
    if not later:
        return Result(name, FAIL, "no claims were observed after the restart", c)
    if holds(later[-1]):
        return Result(
            name,
            FAIL,
            f"{worker}'s claim (sandbox gone, work delivered) "
            "was still held at the last sample",
            c,
        )
    return Result(name, PASS, f"{worker}'s stale claim released after the restart", c)


def restart_did_not_escalate(ev: Evidence) -> Result:
    c = "the restart reconciles WITHOUT escalating (SCRUM-64)"
    name = "restart_did_not_escalate"
    if ev.reconcile_reports is None:
        return Result(
            name,
            PENDING,
            "STUB-PENDING SCRUM-64: reconciliation report and escalation "
            "marker not defined yet",
            c,
        )
    escalated = [x for x in ev.reconcile_reports if x.get("escalated")]
    if escalated:
        return Result(name, FAIL, f"escalated: {escalated[0]}", c)
    return Result(
        name, PASS, f"{len(ev.reconcile_reports)} report(s), none escalated", c
    )


def induced_failures_journaled(ev: Evidence) -> Result:
    c = "the journal records each induced failure (SCRUM-71)"
    # The generic evidence is collected now; the verdict waits for SCRUM-71's
    # defined event set, because today's journal is model-written prose and a
    # match on a Worker's name is not proof the failure was recorded as one.
    found = []
    for i in ev.inductions:
        if i.get("kind") not in ("kill-sandbox", "stale-while-down"):
            continue
        hits = [
            j["name"]
            for j in ev.journal
            if j["mtime"] > i["at"] and (i.get("worker", "") in j["text"])
        ]
        found.append(f"{i['kind']}: {hits[:2] or 'none'}")
    return Result(
        "induced_failures_journaled",
        PENDING,
        "STUB-PENDING SCRUM-71 (event set). Generic matches: "
        + "; ".join(found or ["no inductions"]),
        c,
    )


def done_ticket_never_offered(ev: Evidence) -> Result:
    c = "no Done ticket is offered again (SCRUM-73)"
    name = "done_ticket_never_offered"
    tid = str(ev.tickets[ev.decoy_key])
    seen = []
    if any(
        str(e.get("ticket")) == tid
        for e in ev.events
        if e.get("event") in ("sandbox-started", "delivered")
    ):
        seen.append("a sandbox was started on it")
    if any(str(x.get("ticket")) == tid for s in ev.claims_samples for x in s["claims"]):
        seen.append("it was claimed")
    if any(str(r.get("ticket")) == tid for r in ev.start_requests):
        seen.append("a Worker start was requested for it")
    if seen:
        return Result(name, FAIL, f"{ev.decoy_key} ({tid}): " + ", ".join(seen), c)
    if not ev.claims_samples:
        return Result(name, FAIL, "no samples were taken, so absence proves nothing", c)
    return Result(
        name,
        PASS,
        f"never started, claimed or requested across {len(ev.claims_samples)} samples",
        c,
    )


def pipeline_stages_in_order(ev: Evidence) -> Result:
    c = "each pipeline ticket's record shows every stage, in order (plan 3.3a)"
    name = "pipeline_stages_in_order"
    if ev.stage_log is None:
        return Result(
            name,
            PENDING,
            "STUB-PENDING SCRUM-72: the transition log is not defined yet",
            c,
        )
    want = list(STAGES)
    notes = []
    for key in ev.pipeline_keys:
        got = ev.stage_log.get(key) or []
        # Per-subtask stages repeat, so compare the order each stage FIRST appears in.
        firsts = [s for i, s in enumerate(got) if s not in got[:i]]
        if firsts != want:
            return Result(name, FAIL, f"{key}: stages {got} are not {want} in order", c)
        notes.append(f"{key}: {' → '.join(firsts)}")
    return Result(name, PASS, "; ".join(notes), c)


def no_forbidden_host_commands(ev: Evidence) -> Result:
    c = "no host command run by a person (audited: the harness's own commands)"
    bad = []
    ran = [r for r in ev.commands if r.get("phase") == "run" and r.get("argv")]
    for r in ran:
        argv = r["argv"]
        words = tuple(a.rsplit("/", 1)[-1] if i == 0 else a for i, a in enumerate(argv))
        if any(words[: len(f)] == f for f in FORBIDDEN_DURING_RUN):
            bad.append(" ".join(argv[:4]))
    if bad:
        return Result(
            "no_forbidden_host_commands", FAIL, "the harness ran: " + "; ".join(bad), c
        )
    return Result(
        "no_forbidden_host_commands",
        PASS,
        f"{len(ran)} run-phase commands, none a rescue; "
        "a person's own shell is not visible to this check",
        c,
    )


def no_claude_in_the_fleet(ev: Evidence) -> Result:
    c = "the all-local fleet has no Claude unit (every Manager and Worker is local)"
    claude = sorted(n for n, e in ev.engines.items() if not str(e).startswith("local:"))
    if not ev.engines:
        return Result(
            "no_claude_in_the_fleet", FAIL, "the fleet config was not read", c
        )
    if claude:
        return Result("no_claude_in_the_fleet", FAIL, f"not local: {claude}", c)
    return Result(
        "no_claude_in_the_fleet",
        PASS,
        ", ".join(f"{n}={e}" for n, e in sorted(ev.engines.items())),
        c,
    )


ALL = (
    every_ticket_delivered,
    pipeline_tickets_worked_by_gpu_workers,
    plans_written_by_planner_approved_by_owner,
    plans_approved_through_inbox,
    owner_answer_reached_worker,
    killed_sandbox_recovered,
    recovery_went_through_manager,
    restart_releases_stale_claim,
    restart_did_not_escalate,
    induced_failures_journaled,
    done_ticket_never_offered,
    pipeline_stages_in_order,
    no_forbidden_host_commands,
    no_claude_in_the_fleet,
)
BY_NAME = {c.__name__: c for c in ALL}


def evaluate(ev: Evidence, names: tuple[str, ...] | None = None) -> list[Result]:
    """Run the named checks (a scenario's list), or all of them."""
    return [BY_NAME[n](ev) for n in names] if names else [c(ev) for c in ALL]


def verdict(results: list[Result]) -> str:
    """PASS only if every check passed; FAIL if any failed; else INCOMPLETE."""
    if any(r.status == FAIL for r in results):
        return "FAIL"
    if any(r.status == PENDING for r in results):
        return "INCOMPLETE"
    return "PASS"
