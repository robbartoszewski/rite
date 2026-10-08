"""The happy-path smoke: does the local/GPU loop actually work, end to end?

Robert's scope for tonight (2026-10-08), narrower than the full gate on
purpose: ONE trivial ticket, no induced failures, and four transitions
asserted, in both fleet shapes —

    A) a Claude Manager + a GPU Worker   (the a9 gap SCRUM-72/79 fixed)
    B) a local Manager + a GPU Worker    (pure-local)

    1. the ticket was dispatched to the GPU WORKER
    2. it went through the review gates (RL-6 plan review, RL-7 step verify,
       RL-8 recomposition verify)
    3. it was delivered and handed back
    4. the Manager picked it up

**Nothing here is mocked, because mocks are what hid the last two bugs.** A
real board, a real model through a real Ollama endpoint, a real sandbox, a real
commit, and rite's own verify deciding it.

⚠ **What this deliberately does NOT prove**, so the bundle cannot be read as
more than it is:
- the PULL REQUEST leg. The smoke's app has no git remote, which is what lets a
  Worker's sandbox start without a GitHub token for the Worker
  (`sandbox.start_problem`: "if not remotes: return None" — a NON-GitHub remote
  is refused outright, so remote-less is the only tokenless shape). Delivery
  therefore lands as a branch in the project's own checkout, which is what
  `publish.strategy: commit` does. The host is what collects a Worker's branch
  in either case (OL5b §1.3), so what is skipped is the push and the PR, not
  the handover.
- everything the full 13-check gate covers: recovery, stale claims, the
  Owner's answer, the decoy, journalling. Deferred to v0.7.1 by Robert.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools.e2e_v071 import hooks
from tools.e2e_v071.config import Fleet

PASS = "PASS"
FAIL = "FAIL"

# RL-6 is the plan review by a different model; RL-7 is rite running each
# subtask's own verify before accepting it; RL-8 is the recomposition verify.
_GATES = (("plan_reviewed", "RL-6"), ("step_reviewed", "RL-7"), ("recomposed", "RL-8"))

CHECK_NAMES = (
    "dispatched_to_the_gpu_worker",
    "went_through_the_review_gates",
    "delivered_and_handed_back",
    "the_manager_picked_it_up",
)
"""The four transitions, in the order `judge` appends them. Exported so a
scenario naming a check that does not exist is caught by a test rather than at
the end of a two-hour run."""


@dataclass
class Result:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == PASS


def _git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _handbacks(obs, workers: list[str]) -> dict:
    """Each Worker's completion record, read through the installed rite."""
    snippet = (
        "import json\n"
        "from pathlib import Path\n"
        "from rite_ai import handback\n"
        "root = Path('.').resolve()\n"
        "out = {}\n"
        f"for w in {list(workers)!r}:\n"
        "    got = handback.read(root, w)\n"
        "    if got is None:\n"
        "        continue\n"
        "    out[w] = {'ticket': got.ticket, 'branch': got.branch,\n"
        "              'summary': got.summary, 'at': got.timestamp,\n"
        "              'unreadable': got.unreadable}\n"
        "print(json.dumps(out))\n"
    )
    try:
        got = obs._ask_rite(snippet)  # noqa: SLF001
    except RuntimeError:
        return {}
    return got if isinstance(got, dict) else {}


def judge(run, fleet: Fleet, obs) -> tuple[list[Result], dict]:
    """The four transitions, each from a record, plus the bundle's facts."""
    tickets = json.loads(run.file("tickets.json").read_text())
    key = fleet.pipeline_keys[0]
    board_id = str(tickets[key])
    gpu = set(fleet.gpu_workers)
    events = obs.events()
    stages = hooks.stage_log(obs, {key: board_id}) or {}
    sequence = stages.get(key) or []
    timeline = (hooks.stage_timeline(obs, {key: board_id}) or {}).get(key) or []
    handbacks = _handbacks(obs, [w["name"] for w in fleet.workers])

    results: list[Result] = []

    # 1. dispatched to the GPU Worker -------------------------------------
    started = [
        e
        for e in events
        if e.get("event") == "sandbox-started" and str(e.get("ticket")) == board_id
    ]
    on_gpu = [e for e in started if e.get("worker") in gpu]
    others = [e for e in started if e.get("worker") not in gpu]
    if not started:
        results.append(
            Result(
                "dispatched_to_the_gpu_worker",
                FAIL,
                f"no Worker was ever started on {board_id}",
            )
        )
    elif not on_gpu:
        results.append(
            Result(
                "dispatched_to_the_gpu_worker",
                FAIL,
                f"{board_id} went to {sorted({str(e.get('worker')) for e in others})}, "
                f"none of them a GPU Worker ({sorted(gpu)})",
            )
        )
    else:
        detail = (
            f"{board_id} started on {sorted({str(e.get('worker')) for e in on_gpu})}"
        )
        if others:
            # Not a failure on its own — a re-stage after a fault is legitimate —
            # but it is said, because "a GPU Worker did it" would otherwise hide
            # that a Claude Worker had it first.
            detail += (
                f"; also started on non-GPU "
                f"{sorted({str(e.get('worker')) for e in others})}"
            )
        results.append(Result("dispatched_to_the_gpu_worker", PASS, detail))

    # 2. through the review gates, in order --------------------------------
    missing = [f"{name} ({rl})" for name, rl in _GATES if name not in sequence]
    if not sequence:
        results.append(
            Result(
                "went_through_the_review_gates",
                FAIL,
                "the persisted stage log is empty — no stage was ever recorded",
            )
        )
    elif missing:
        results.append(
            Result(
                "went_through_the_review_gates",
                FAIL,
                f"never reached {', '.join(missing)}; the log says "
                f"{' → '.join(sequence)}",
            )
        )
    else:
        order = [sequence.index(name) for name, _ in _GATES]
        if order != sorted(order):
            results.append(
                Result(
                    "went_through_the_review_gates",
                    FAIL,
                    f"the gates are out of order: {' → '.join(sequence)}",
                )
            )
        else:
            results.append(
                Result(
                    "went_through_the_review_gates",
                    PASS,
                    f"RL-6/7/8 all present and in order: {' → '.join(sequence)}",
                )
            )

    # 3. delivered and handed back -----------------------------------------
    delivered = [
        e
        for e in events
        if e.get("event") == "delivered" and str(e.get("ticket")) == board_id
    ]
    hb = {w: h for w, h in handbacks.items() if str(h.get("ticket")) == board_id}
    if not delivered:
        results.append(
            Result(
                "delivered_and_handed_back",
                FAIL,
                f"no `delivered` event for {board_id}",
            )
        )
    elif not hb:
        results.append(
            Result(
                "delivered_and_handed_back",
                FAIL,
                f"{board_id} was delivered but no Worker's handback record names "
                "it, so nothing was handed back",
            )
        )
    else:
        worker, record = next(iter(hb.items()))
        results.append(
            Result(
                "delivered_and_handed_back",
                PASS,
                f"delivered, and {worker} handed back branch "
                f"{record.get('branch') or '(none named)'}",
            )
        )

    # 4. the Manager picked it up ------------------------------------------
    # The concrete form of "picked up": the Worker's branch, with its commit, is
    # in the PROJECT's own checkout — the Manager's tree, not the sandbox copy.
    listed = _git(run.project, "branch", "--list", "--format=%(refname:short)")
    branches = [b.strip() for b in listed.splitlines() if b.strip()]
    named = [b for b in branches if board_id in b or key in b]
    pickup = {}
    if named:
        branch = named[0]
        pickup = {
            "branch": branch,
            "sha": _git(run.project, "rev-parse", branch),
            "subject": _git(run.project, "log", "-1", "--format=%s", branch),
            "files": _git(run.project, "diff", "--name-only", f"main...{branch}"),
            "diff": _git(run.project, "diff", f"main...{branch}"),
        }
    if not named:
        results.append(
            Result(
                "the_manager_picked_it_up",
                FAIL,
                f"no branch for {board_id} in the project's own checkout; it has "
                f"{branches or 'none'}. The work never left the sandbox.",
            )
        )
    elif not pickup.get("files"):
        results.append(
            Result(
                "the_manager_picked_it_up",
                FAIL,
                f"branch {pickup['branch']} is in the checkout but changes no file "
                "against main — the Manager picked up an empty delivery",
            )
        )
    else:
        results.append(
            Result(
                "the_manager_picked_it_up",
                PASS,
                f"{pickup['branch']} at {pickup['sha'][:12]} changes "
                f"{len(pickup['files'].splitlines())} file(s): "
                f"{pickup['files'].replace(chr(10), ', ')}",
            )
        )

    facts = {
        "scenario": fleet.name,
        "ticket_key": key,
        "board_id": board_id,
        "gpu_workers": sorted(gpu),
        "stages": sequence,
        "timeline": timeline,
        "handbacks": handbacks,
        "delivered_events": delivered,
        "started_events": started,
        "pickup": pickup,
    }
    return results, facts


def bundle(run, fleet: Fleet, results: list[Result], facts: dict) -> str:
    """The four artefacts Robert asked to see, as one readable page."""
    ticket = fleet.ticket(facts["ticket_key"])
    verdict = "GREEN" if all(r.ok for r in results) else "NOT GREEN"
    lines = [
        f"# {fleet.title}",
        "",
        f"**{verdict}** — happy-path smoke, {len(results)} checks. "
        f"Run `{run.path.name}`.",
        "",
        "Real board, real local model through Ollama, real sandbox, real commit, "
        "rite's own verify deciding it. No mocks anywhere.",
        "",
        "## The four transitions",
        "",
        "| | check | verdict | evidence |",
        "|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | `{r.name}` | **{r.status}** | {r.detail} |")

    lines += [
        "",
        "## 1. The ticket",
        "",
        f"- **{facts['board_id']}** — {ticket.title}",
        f"- definition of done: {'; '.join(ticket.definition_of_done)}",
        f"- agreed verify (rite runs this, RL-7): `{ticket.verify}`",
        "",
        "## 2. The persisted stage transitions",
        "",
    ]
    if facts["stages"]:
        lines += [f"{i}. `{s}`" for i, s in enumerate(facts["stages"], 1)]
    else:
        lines.append("_none recorded_")
    rows = facts.get("timeline") or []
    if rows:
        lines += [
            "",
            "### How long each stage took",
            "",
            "From the transition log's own timestamps, not a stopwatch.",
            "",
            "| transition | spent in the previous stage |",
            "|---|---|",
        ]
        for row in rows:
            spent = row.get("seconds_in_previous")
            shown = "—" if spent is None else f"{spent / 60:.1f} min"
            lines.append(f"| → `{row.get('stage')}` | {shown} |")

    pickup = facts.get("pickup") or {}
    lines += [
        "",
        "## 3. The Worker's committed change",
        "",
    ]
    if pickup.get("diff"):
        lines += [
            f"`{pickup['branch']}` at `{pickup['sha'][:12]}` — "
            f"{pickup.get('subject') or '(no subject)'}",
            "",
            "```diff",
            pickup["diff"][:8000],
            "```",
        ]
    else:
        lines.append("_no branch with a change was collected_")

    lines += [
        "",
        "## 4. The Manager's pickup state",
        "",
    ]
    if facts.get("handbacks"):
        for worker, rec in facts["handbacks"].items():
            lines.append(
                f"- **{worker}** handed back ticket `{rec.get('ticket')}` on branch "
                f"`{rec.get('branch')}` — {rec.get('summary') or '(no summary)'}"
            )
    else:
        lines.append("_no handback record_")
    if pickup.get("sha"):
        lines.append(
            f"- the branch is in the project's own checkout at `{pickup['sha'][:12]}`"
        )

    lines += [
        "",
        "## What this does not prove",
        "",
        "- the pull-request leg: the smoke's app has no git remote (which is what "
        "lets a Worker's sandbox start without a Worker GitHub token), so delivery "
        "lands as a branch in the project's checkout. The host collects a Worker's "
        "branch either way, so the push and the PR are what is skipped, not the "
        "handover.",
        "- recovery, stale claims, the Owner's answer, the decoy, journalling — the "
        "full gate's other checks, deferred to v0.7.1.",
        "",
    ]
    text = "\n".join(lines)
    run.file("smoke-bundle.md").write_text(text)
    run.file("smoke-facts.json").write_text(json.dumps(facts, indent=2, default=str))
    return text
