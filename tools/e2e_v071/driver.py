"""What the harness does DURING the run: sample state, play the Owner, induce failures.

Nothing here rescues the fleet. The only run-phase actions are:
- `rite message <owner> "<qid> <answer>"`: the Owner answering, which is the User's
  role (rite counts it as the User speaking), not a host rescue;
- `yoloai stop <sandbox>` and `yoloai destroy`: the failures being induced;
- stopping and restarting the Owner's supervisor: the restart being induced.
`checks.no_forbidden_host_commands` audits that this stayed true.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from tools.e2e_v071.checks import came_back
from tools.e2e_v071.config import Fleet
from tools.e2e_v071.observe import Observer, delivered_prs
from tools.e2e_v071.runlog import RunDir, sh

OTHER_ANSWER = (
    "This is not specified for this ticket. Use your judgement, keep the change "
    "minimal, and say what you chose in your handback."
)


@dataclass
class DriverState:
    """Progress across polls, persisted only through the run's .jsonl logs."""

    answered: set = field(default_factory=set)
    written: set = field(default_factory=set)
    acked: set = field(default_factory=set)
    requests_seen: set = field(default_factory=set)
    plans_seen: dict = field(default_factory=dict)  # ticket key -> last snapshot
    kill_done: bool = False
    restart_done: bool = False


def sample(
    run: RunDir, obs: Observer, fleet: Fleet, st: DriverState, tickets: dict
) -> None:
    """One observation, appended: claims; every Worker start request ever seen
    (requests are consumed when honoured, so only a sampler sees them all); and each
    pipeline ticket's plan whenever it changed, which is what the demo replays as the
    ticket moving through the pipeline."""
    run.append("samples.jsonl", {"claims": obs.claims()})
    for key in fleet.pipeline_keys:
        try:
            plan = obs.decomposition(tickets[key])
        except RuntimeError as e:
            plan = {"unreadable": str(e)[-200:]}
        if plan != st.plans_seen.get(key):
            st.plans_seen[key] = plan
            run.append("plans.jsonl", {"key": key, "plan": plan})
    for m in fleet.managers:
        for req in obs.start_requests(obs.manager_dir(m["name"])):
            k = (req.get("worker"), str(req.get("ticket")))
            if k not in st.requests_seen:
                st.requests_seen.add(k)
                run.append("start_requests.jsonl", {"manager": m["name"], **req})


def _sandbox_ticket(events: list[dict]) -> dict:
    """sandbox name -> the ticket it was last started on."""
    out = {}
    for e in events:
        if e.get("event") == "sandbox-started":
            out[e.get("sandbox")] = str(e.get("ticket"))
    return out


def play_owner(
    run: RunDir,
    obs: Observer,
    fleet: Fleet,
    tickets: dict,
    st: DriverState,
    env: dict,
    answer: str,
) -> None:
    """Answer each open Worker question once; track it into the sandbox and its ack."""
    owner_dir = obs.manager_dir(fleet.owner)
    ledger = obs.worker_questions(owner_dir)
    on = _sandbox_ticket(obs.events())
    target = str(tickets[fleet.owner_key]) if fleet.owner_key else None
    for qid, sandbox in (ledger.get("_open") or {}).items():
        if qid in st.answered:
            continue
        ticket = on.get(sandbox, "")
        text = answer if ticket == target else OTHER_ANSWER
        sh(
            run,
            ["rite", "message", fleet.owner, f"{qid} {text}"],
            phase="run",
            cwd=run.project,
            env=env,
        )
        st.answered.add(qid)
        run.append(
            "owner.jsonl",
            {
                "kind": "answered",
                "qid": qid,
                "sandbox": sandbox,
                "ticket": ticket,
                "target": ticket == target,
            },
        )
    delivered = ledger.get("_delivered") or {}
    closed = ledger.get("_closed") or {}
    for qid in st.answered:
        if qid in delivered and qid not in st.written:
            st.written.add(qid)
            run.append("owner.jsonl", {"kind": "written-into-sandbox", "qid": qid})
        # An ack inside one poll removes the qid from `_delivered` before it is
        # seen there, so "closed and no longer delivered" counts on its own.
        if qid not in delivered and qid in closed and qid not in st.acked:
            st.acked.add(qid)
            run.append("owner.jsonl", {"kind": "acknowledged", "qid": qid})


def a_plan_awaits_review(obs, fleet, tickets: dict) -> bool:
    """Is there a plan written and not yet approved?

    Read through the installed rite, so it is the same plan record the reviewer
    will be asked about. Anything unreadable answers False: starting a Manager
    early costs a GPU turn and an eviction, and the next poll asks again.
    """
    for key in fleet.pipeline_keys:
        ident = str(tickets.get(key) or "")
        if not ident:
            continue
        try:
            plan = obs.decomposition(ident)
        except RuntimeError:
            continue
        if isinstance(plan, dict) and plan.get("approval") != "approved":
            return True
    return False


def maybe_kill(
    run: RunDir, obs: Observer, fleet: Fleet, tickets: dict, st: DriverState, env: dict
) -> None:
    """Kill the scenario's `kill_worker`'s sandbox once, mid-ticket: after it has
    worked a few minutes on a ticket and before that ticket is delivered."""
    if st.kill_done or not fleet.kill_worker:
        # No `kill_worker` means the scenario induces no failure (the happy-path
        # smoke). Marked done so nothing downstream waits for an induction that
        # was never going to happen.
        st.kill_done = True
        return
    victim = fleet.kill_worker
    skip = {str(tickets[k]) for k in (fleet.owner_key, fleet.decoy_key) if k}
    after = float(fleet.run.get("kill_after_minutes", 3)) * 60
    events = obs.events()
    for e in reversed(events):
        if e.get("event") != "sandbox-started" or e.get("worker") != victim:
            continue
        ticket = str(e.get("ticket"))
        if (
            ticket in skip
            or delivered_prs(events, ticket)
            or time.time() - float(e["at"]) < after
        ):
            return
        sh(run, ["yoloai", "stop", e["sandbox"]], phase="run", env=env)
        run.append(
            "inductions.jsonl",
            {
                "kind": "kill-sandbox",
                "worker": victim,
                "sandbox": e["sandbox"],
                "ticket": ticket,
                "mode": "stop",
            },
        )
        st.kill_done = True
        return


def maybe_restart_with_stale_state(
    run: RunDir,
    obs: Observer,
    fleet: Fleet,
    st: DriverState,
    env: dict,
    stop_owner,
    start_owner,
) -> None:
    """Once the kill has been recovered and some Worker has delivered while still
    holding its claim: stop the Owner's supervisor, destroy that Worker's sandbox
    while it is down (sandbox gone + work delivered = a stale claim, plan 3.2's rule),
    then start the supervisor again."""
    if st.restart_done or not st.kill_done:
        return
    # 🔴 **A scenario that induces no failure has nothing to restart from.**
    # `maybe_kill` marks `kill_done` for such a scenario so nothing downstream
    # waits for a kill that was never going to happen — and that let this
    # function straight through to `kills[0]` on an empty list. It killed a
    # mixed smoke run with an IndexError AFTER the Worker had started and the
    # pipeline had reached `defined`: the loop was working and the harness
    # crashed on top of it.
    #
    # Two guards, because they are two different facts: this scenario induces
    # nothing (so it is done), and a kill has been recorded but not yet read
    # back (so wait).
    if not fleet.kill_worker:
        st.restart_done = True
        return
    kills = [i for i in run.read("inductions.jsonl") if i["kind"] == "kill-sandbox"]
    if not kills:
        return
    events = obs.events()
    recovered = any(came_back(e, kills[0]) for e in events)
    if not recovered:
        return
    held = {x.get("worker") for x in obs.claims()}
    last_sandbox = {
        e.get("worker"): e.get("sandbox")
        for e in events
        if e.get("event") == "sandbox-started"
    }
    # Sandboxes are per Worker, not per ticket: only a Worker whose LATEST start is
    # the ticket it delivered is idle-and-delivered. One already on its next ticket
    # holds live work, and destroying that is not a stale-state scenario.
    last_ticket = {
        e.get("worker"): str(e.get("ticket"))
        for e in events
        if e.get("event") == "sandbox-started"
    }
    for e in reversed(events):
        if e.get("event") != "delivered" or e.get("worker") not in held:
            continue
        if last_ticket.get(e.get("worker")) != str(e.get("ticket")):
            continue
        worker = e["worker"]
        stop_owner()
        run.append(
            "inductions.jsonl", {"kind": "manager-stopped", "manager": fleet.owner}
        )
        sh(
            run,
            ["yoloai", "destroy", "--abandon-unapplied", last_sandbox[worker]],
            phase="run",
            env=env,
        )
        run.append(
            "inductions.jsonl",
            {
                "kind": "stale-while-down",
                "worker": worker,
                "sandbox": last_sandbox[worker],
                "ticket": str(e.get("ticket")),
            },
        )
        run.append("samples.jsonl", {"claims": obs.claims()})
        time.sleep(30)
        run.append("samples.jsonl", {"claims": obs.claims()})
        start_owner()
        run.append(
            "inductions.jsonl", {"kind": "manager-restarted", "manager": fleet.owner}
        )
        st.restart_done = True
        return
