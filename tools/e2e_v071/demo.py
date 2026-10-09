"""The run as something to WATCH: a live dashboard, a replay, and a demo page.

Robert's release go is seeing both scenarios run, so a run's output is a
demonstration, not only a verdict. Everything here is derived from the run's
own records, so a finished run can be replayed exactly as it happened:

- `timeline(run)`: one ordered list of moments — rite's events, the harness's
  inductions, the Owner's answers, each pipeline ticket's plan as it changed,
  supervisor starts and stops.
- `pipeline(run)`: per ticket, when it passed each stage of the deterministic
  pipeline (spec → refine → plan → review → approach → execute → recompose →
  delivered). A stage whose record SCRUM-72 has not defined yet is shown as
  "awaiting SCRUM-72", never as passed.
- `watch` (live, in the run's tmux), `replay` (terminal, time-scaled), and
  `demo.html` (self-contained page with a play/scrub control).
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

from tools.e2e_v071.config import Fleet
from tools.e2e_v071.observe import delivered_prs
from tools.e2e_v071.runlog import RunDir

# The pipeline columns the demo shows, and which record proves each one.
COLUMNS = (
    ("spec", "Spec", "the definition snapshot (SCRUM-72 'defined')"),
    ("refine", "Refine", "the agreed definition of done (rite refine accept)"),
    ("plan", "Plan", "the decomposition, written by the planner"),
    ("review", "Review", "RL-6: the plan approved by an independent Manager"),
    ("approach", "Approach", "Level 2, per subtask (SCRUM-72 'approached')"),
    ("execute", "Execute", "RL-7: every subtask's verify run by rite, accepted"),
    ("recompose", "Recompose", "RL-8: the ticket's verify on the whole branch"),
    ("delivered", "Delivered", "a PR opened by rite's delivery"),
)
AWAITING = "awaiting SCRUM-72"


def _events(run: RunDir) -> list[dict]:
    p = run.project / ".rite" / "events.jsonl"
    out = []
    for line in p.read_text().splitlines() if p.exists() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _key_of(tickets: dict) -> dict:
    return {str(v): k for k, v in tickets.items()}


def timeline(run: RunDir) -> list[dict]:
    """[{at, lane, kind, text}] in time order."""
    tickets = _read_json(run.file("tickets.json"), {})
    key = _key_of(tickets)
    out: list[dict] = []

    def add(at, lane, kind, text):
        out.append({"at": float(at), "lane": lane, "kind": kind, "text": text})

    for e in _events(run):
        ev, w, t = e.get("event"), e.get("worker", ""), str(e.get("ticket", ""))
        name = key.get(t, t)
        if ev == "sandbox-started":
            add(e["at"], f"worker:{w}", "start", f"{w} starts on {name}")
        elif ev in ("sandbox-restarted", "sandbox-stopped", "sandbox-destroyed"):
            add(e["at"], f"worker:{w}", "lifecycle", f"{w}: {ev.split('-', 1)[1]}")
        elif ev == "delivered":
            prs = delivered_prs([e], t)
            text = (
                f"{name} delivered → {prs[-1]['pr_url']}"
                if prs
                else (
                    f"{name}: delivery NOT done — {'; '.join(e.get('outcomes') or [])}"
                )
            )
            add(e["at"], f"ticket:{name}", "delivered" if prs else "refused", text)
        elif ev == "board-move":
            add(e["at"], f"ticket:{name}", "board", f"{name} → {e.get('status')}")
    for i in run.read("inductions.jsonl"):
        k = i["kind"]
        if k == "run-started":
            add(i["at"], "harness", "harness", "run started; supervisors launched")
        elif k == "kill-sandbox":
            add(
                i["at"], "harness", "induce", f"INDUCED: killed {i['worker']}'s sandbox"
            )
        elif k == "manager-stopped":
            add(i["at"], "harness", "induce", f"INDUCED: stopped {i['manager']}")
        elif k == "stale-while-down":
            add(
                i["at"],
                "harness",
                "induce",
                f"INDUCED: destroyed {i['worker']}'s sandbox while the Manager was "
                "down (stale claim)",
            )
        elif k == "manager-restarted":
            add(i["at"], "harness", "induce", f"INDUCED: restarted {i['manager']}")
    for o in run.read("owner.jsonl"):
        text = {
            "answered": f"Owner answers {o.get('qid')}"
            + (" (the secret code)" if o.get("target") else ""),
            "written-into-sandbox": f"answer {o.get('qid')} written into the sandbox",
            "acknowledged": f"Worker acknowledged {o.get('qid')}",
        }.get(o["kind"], o["kind"])
        add(o["at"], "owner", "owner", text)
    for snap in run.read("plans.jsonl"):
        plan, name = snap.get("plan"), snap["key"]
        if not isinstance(plan, dict) or "unreadable" in plan:
            continue
        subs = plan.get("subtasks") or []
        done = sum(s.get("status") == "accepted" for s in subs)
        add(
            snap["at"],
            f"ticket:{name}",
            "plan",
            f"{name} plan: {plan.get('approval')} "
            f"(by {plan.get('decomposed_by')}"
            + (
                f", approved by {plan.get('approved_by')}"
                if plan.get("approved_by")
                else ""
            )
            + f"), {done}/{len(subs)} subtasks accepted: "
            + ", ".join(f"{s['id']}={s.get('status')}" for s in subs),
        )
    return sorted(out, key=lambda x: x["at"])


def pipeline(run: RunDir, fleet: Fleet, stage_log: dict | None = None) -> dict:
    """{ticket key: {column: {"at": t} | {"state": AWAITING} | {}}}."""
    tickets = _read_json(run.file("tickets.json"), {})
    events = _events(run)
    commands = run.read("commands.jsonl")
    plans = run.read("plans.jsonl")
    out = {}
    for t in fleet.work_tickets:
        tid = str(tickets.get(t.key, ""))
        row: dict = {c: {} for c, _, _ in COLUMNS}
        for c in commands:
            argv = c.get("argv") or []
            if argv[1:4] == ["refine", "accept", tid]:
                row["refine"] = {"at": c["at"]}
                break
        if t.key in fleet.pipeline_keys:
            for snap in plans:
                plan = snap.get("plan")
                if snap["key"] != t.key or not isinstance(plan, dict):
                    continue
                if plan.get("subtasks") and not row["plan"]:
                    row["plan"] = {"at": snap["at"], "by": plan.get("decomposed_by")}
                if plan.get("approval") == "approved" and not row["review"]:
                    row["review"] = {"at": snap["at"], "by": plan.get("approved_by")}
                subs = plan.get("subtasks") or []
                if subs and all(s.get("status") == "accepted" for s in subs):
                    row["execute"] = row["execute"] or {"at": snap["at"]}
            logged = (stage_log or {}).get(t.key)
            for col, stage in (
                ("spec", "defined"),
                ("approach", "approached"),
                ("recompose", "recomposed"),
            ):
                if logged is None:
                    row[col] = {"state": AWAITING}
                elif stage in logged:
                    row[col] = {"at": None, "state": "logged"}
        else:
            for col in ("spec", "plan", "review", "approach", "execute", "recompose"):
                row[col] = {"state": "n/a (Claude Worker)"}
        prs = delivered_prs(events, tid)
        if prs:
            row["delivered"] = {"at": prs[-1]["at"], "pr": prs[-1]["pr_url"]}
        out[t.key] = row
    return out


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------ terminal views


def _cell(c: dict) -> str:
    if c.get("at") or c.get("state") == "logged":
        return "✔"
    if c.get("state", "").startswith("awaiting"):
        return "…72"
    if c.get("state", "").startswith("n/a"):
        return "–"
    return "·"


def render_board(run: RunDir, fleet: Fleet, upto: float | None = None) -> str:
    rows = pipeline(run, fleet)
    head = f"{'ticket':16}" + "".join(f"{label:>10}" for _, label, _ in COLUMNS)
    lines = [head, "-" * len(head)]
    for key, row in rows.items():
        cells = []
        for col, _, _ in COLUMNS:
            c = row[col]
            if upto is not None and c.get("at") and c["at"] > upto:
                c = {}
            cells.append(f"{_cell(c):>10}")
        lines.append(f"{key:16}" + "".join(cells))
    return "\n".join(lines)


def _fmt(entry: dict, t0: float) -> str:
    mark = {"induce": "⚡", "delivered": "✅", "refused": "⛔", "owner": "💬"}.get(
        entry["kind"], " "
    )
    return f"+{(entry['at'] - t0) / 60:6.1f}m {mark} [{entry['lane']}] {entry['text']}"


def watch(run: RunDir, fleet: Fleet, every: float = 10.0) -> None:
    """Live: redraw the pipeline board and the latest moments until interrupted."""
    while True:
        tl = timeline(run)
        t0 = tl[0]["at"] if tl else time.time()
        report = _read_json(run.file("report.json"), None)
        print("\x1b[2J\x1b[H", end="")
        print(f"{fleet.title}   run {run.path.name}")
        print(f"verdict: {report['verdict'] if report else 'running…'}\n")
        print(render_board(run, fleet))
        print("\nlatest:")
        for e in tl[-18:]:
            print(_fmt(e, t0))
        time.sleep(every)


def replay(run: RunDir, fleet: Fleet, speed: float = 60.0) -> None:
    """Play the run back in the terminal, `speed` times faster than it happened."""
    tl = timeline(run)
    if not tl:
        print("nothing recorded in this run")
        return
    t0 = prev = tl[0]["at"]
    print(f"{fleet.title}   run {run.path.name}   (replay ×{speed:g})\n")
    for e in tl:
        time.sleep(min(max(e["at"] - prev, 0) / speed, 3.0))
        prev = e["at"]
        print(_fmt(e, t0))
    print("\n" + render_board(run, fleet))
    report = _read_json(run.file("report.json"), None)
    if report:
        print(f"\nverdict: {report['verdict']}")
        for r in report["results"]:
            print(f"  {r['status']:8} {r['criterion'] or r['name']}")


# ------------------------------------------------------------ the demo page


def write_demo(run: RunDir, fleet: Fleet, stage_log: dict | None = None) -> Path:
    """`<run>/demo.html`: self-contained, no network, a play/scrub control."""
    report = _read_json(
        run.file("report.json"), {"verdict": "not judged", "results": []}
    )
    data = {
        "title": fleet.title,
        "run": run.path.name,
        "verdict": report["verdict"],
        "results": report["results"],
        "columns": [
            {"id": c, "label": label, "proof": proof} for c, label, proof in COLUMNS
        ],
        "pipeline": pipeline(run, fleet, stage_log),
        "timeline": timeline(run),
        "fleet": {
            "managers": [
                {
                    "name": m["name"],
                    "engine": m.get("engine", "claude"),
                    "model": m.get("model", ""),
                }
                for m in fleet.managers
            ],
            "workers": [
                {
                    "name": w["name"],
                    "engine": w.get("engine", "claude"),
                    "model": w.get("model", ""),
                }
                for w in fleet.workers
            ],
        },
    }
    page = DEMO_HTML.replace(
        "__TITLE__", html.escape(f"{fleet.title} — {run.path.name}")
    )
    page = page.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    out = run.file("demo.html")
    out.write_text(page)
    return out


def write_gate_index(runs: list[tuple[RunDir, Fleet]], out: Path) -> Path:
    """One page linking each scenario's demo with its verdict: the gate view."""
    rows = []
    for run, fleet in runs:
        rep = _read_json(run.file("report.json"), {"verdict": "not judged"})
        link = html.escape(str(run.file("demo.html")))
        rows.append(
            f"<tr><td>{html.escape(fleet.title)}</td><td class='v {rep['verdict']}'>"
            f"{rep['verdict']}</td><td><a href='{link}'>watch the run</a></td></tr>"
        )
    page = GATE_HTML.replace("__ROWS__", "\n".join(rows))
    out.write_text(page)
    return out


TEMPLATES = Path(__file__).resolve().parent / "templates"
DEMO_HTML = (TEMPLATES / "demo.html").read_text()
GATE_HTML = (TEMPLATES / "gate.html").read_text()
