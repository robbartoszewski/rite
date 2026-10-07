"""The v0.7.1 acceptance gate, end to end. Run from the rite repo root:

    uv run python -m tools.e2e_v071.harness plan
    uv run python -m tools.e2e_v071.harness preflight [--probe-df16]
    uv run python -m tools.e2e_v071.harness setup --offline      # dry: no network
    uv run python -m tools.e2e_v071.harness setup --create-repo  # creates the repo
    uv run python -m tools.e2e_v071.harness run <run-dir> [--probe-df16]
    uv run python -m tools.e2e_v071.harness check <run-dir>      # re-judge, offline
    uv run python -m tools.e2e_v071.harness teardown <run-dir> [--delete-repo]

README.md has the prerequisites and which checks wait on which fix.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from tools.e2e_v071 import checks, driver, hooks, preflight
from tools.e2e_v071.config import HERE, Fleet, load
from tools.e2e_v071.observe import Observer, delivered_prs, installed_rite_python
from tools.e2e_v071.runlog import RunDir, sh

DEFAULT_RUNS_ROOT = Path(os.path.expanduser("~/AI/rite-e2e-runs"))
EXIT = {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2}
REFUSED = 3


# ---------------------------------------------------------------- setup


def _preset(fleet: Fleet, backend: str) -> dict:
    return {
        "project": {"name": fleet.project_name, "root_branch": "main"},
        "what": {"kind": "library"},
        "technology": {"languages": ["python"]},
        "operations": {"ticket_backend": backend, "sandbox": True},
        "schedule": {
            "timezone": fleet.run.get("timezone", "Europe/Warsaw"),
            "workers": 2,
            "hours": "00:00-24:00",
        },
    }


def _patch_config(project: Path, fleet: Fleet, repo: str) -> None:
    """The keys `rite init` does not ask for. Edited as YAML, then validated by
    `rite doctor` at the end of setup, so a key rite does not accept is caught."""
    path = project / ".rite" / "config.yaml"
    cfg = yaml.safe_load(path.read_text()) or {}
    cfg.setdefault("heartbeat", {}).update(fleet.heartbeat)
    cfg["publish"] = {
        "strategy": "pull_request" if repo else "commit",
        "squash": False,
        "auto_merge": False,
    }
    if repo:
        cfg.setdefault("ticket_backend", {}).update({"type": "github", "repo": repo})
        cfg["github_app"] = {
            "app_id": str(fleet.github["app_id"]),
            "installation_id": str(fleet.github["installation_id"]),
            "repository": repo,
        }
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def _manager_args(m: dict) -> list[str]:
    args = ["rite", "add", "manager", m["name"], "--preset", m["preset"]]
    for key in ("engine", "endpoint", "model", "agent"):
        if m.get(key):
            args += [f"--{key}", str(m[key])]
    if m.get("context_window"):
        args += ["--context-window", str(m["context_window"])]
    return args


def _worker_args(w: dict) -> list[str]:
    args = [
        "rite",
        "add",
        "worker",
        w["name"],
        "-m",
        w["manager"],
        "--no-follow-module-docs",
    ]
    for key in ("engine", "endpoint", "model", "agent"):
        if w.get(key):
            args += [f"--{key}", str(w[key])]
    if w.get("context_window"):
        args += ["--context-window", str(w["context_window"])]
    return args


def _created_id(out: str) -> str:
    """`rite board create` prints `created <id>: <url or title>`."""
    m = re.search(r"^created (\S+):", out, re.M)
    if not m:
        raise RuntimeError(f"could not read the new ticket id from: {out!r}")
    return m.group(1)


def setup(fleet: Fleet, runs_root: Path, *, offline: bool, create_repo: bool) -> RunDir:
    if not offline and not create_repo:
        raise SystemExit(
            "setup: pass --offline (local dry setup) or --create-repo "
            "(creates a private GitHub repo)"
        )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run = RunDir(runs_root / run_id)
    run.path.mkdir(parents=True)
    env = preflight.patched_path(fleet, run.env())
    shutil.copytree(HERE / "app", run.project)

    # The Owner's answer: random per run, only its hash goes into the repo.
    answer = "Q" + secrets.token_hex(3).upper()
    (run.project / "tests" / "currency.sha256").write_text(
        __import__("hashlib").sha256(answer.encode()).hexdigest() + "\n"
    )
    secret = run.file("owner-answer.txt")
    secret.write_text(answer + "\n")
    secret.chmod(0o600)

    def p(argv, **kw):
        return sh(run, argv, phase="setup", cwd=run.project, env=env, check=True, **kw)

    p(["git", "init", "-q", "-b", "main"])
    p(["git", "add", "-A"])
    p(
        [
            "git",
            "-c",
            "user.email=e2e@rite.local",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=rite e2e",
            "commit",
            "-qm",
            "tally: the throwaway app",
        ]
    )

    repo = ""
    if create_repo:
        repo = f"{fleet.github_owner}/{fleet.repo_prefix}{run_id.lower()}"
        p(
            [
                "gh",
                "repo",
                "create",
                repo,
                "--private",
                "--source",
                ".",
                "--remote",
                "origin",
                "--push",
            ]
        )

    preset = run.file("init-preset.yaml")
    preset.write_text(yaml.safe_dump(_preset(fleet, "github" if repo else "none")))
    p(["rite", "init", ".", "--config", str(preset), "--yes"])
    _patch_config(run.project, fleet, repo)
    have = (
        yaml.safe_load((run.project / ".rite" / "config.yaml").read_text()) or {}
    ).get("coordination", {}).get("managers") or []
    for m in fleet.managers:
        if m["name"] not in have:
            p(_manager_args(m))
    for w in fleet.workers:
        p(_worker_args(w))
    p(["git", "add", "-A"])
    p(
        [
            "git",
            "-c",
            "user.email=e2e@rite.local",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=rite e2e",
            "commit",
            "-qm",
            "rite: the e2e gate's project and fleet",
        ]
    )
    if repo:
        p(["git", "push", "-q", "origin", "main"])

    tickets: dict[str, str] = {}
    if repo:
        for t in fleet.tickets:
            labels = ["-l", "scheduled"] + (
                ["-l", "ready-to-work"] if t.decoy_done else []
            )
            out = p(["rite", "board", "create", t.title, "-d", t.body, *labels]).stdout
            tid = _created_id(out)
            tickets[t.key] = tid
            accept = ["rite", "refine", "accept", tid]
            for item in t.definition_of_done:
                accept += ["--item", item]
            p(accept + ["--verify", t.verify])
            if t.decoy_done:
                p(["rite", "board", "move", tid, "done"])
    run.file("tickets.json").write_text(json.dumps(tickets, indent=1) + "\n")
    run.file("run.json").write_text(
        json.dumps({"run_id": run_id, "repo": repo, "offline": offline}, indent=1)
        + "\n"
    )

    doctor = sh(run, ["rite", "doctor"], phase="setup", cwd=run.project, env=env)
    run.file("doctor.txt").write_text(doctor.stdout + doctor.stderr)
    print(f"run dir: {run.path}")
    print(
        f"rite doctor exit {doctor.returncode} (full output: {run.file('doctor.txt')})"
    )
    print(CREDENTIALS_NOTE.format(project=run.project))
    if repo and doctor.returncode != 0:
        raise SystemExit(
            "setup: rite doctor is not clean; fix what it says before `run`"
        )
    return run


CREDENTIALS_NOTE = """\
Credentials are the operator's to set; the harness never handles a secret. In {project}:
  rite credential set github_token --stdin < <a token with repo scope for the run repo>
  rite credential set github_app_key --stdin < <the App's private key .pem>
  claude setup-token, then: rite credential set claude  (a Claude Worker needs it)
Then re-run `rite doctor` there until it is clean."""


# ---------------------------------------------------------------- run


class Supervisors:
    """The Managers' supervisors, each in a window of the run's own tmux server."""

    def __init__(self, run: RunDir, env: dict):
        self.run, self.env = run, env
        self.socket = f"rite-e2e-{run.path.name}"
        (run.path / "logs").mkdir(exist_ok=True)

    def _tmux(self, *args: str) -> subprocess.CompletedProcess:
        return sh(
            self.run, ["tmux", "-L", self.socket, *args], phase="run", env=self.env
        )

    def start(self, manager: str) -> None:
        log = self.run.path / "logs" / f"{manager}.log"
        cmd = (
            f"cd {self.run.project} && "
            f"rite start {manager} --record-issues 2>&1 | tee -a {log}"
        )
        exists = self._tmux("has-session", "-t", "e2e").returncode == 0
        if exists:
            self._tmux("new-window", "-t", "e2e", "-n", manager, cmd)
        else:
            self._tmux("new-session", "-d", "-s", "e2e", "-n", manager, cmd)

    def stop(self, manager: str, obs: Observer, wait: float = 180) -> None:
        self._tmux("send-keys", "-t", f"e2e:{manager}", "C-c", "")
        deadline = time.time() + wait
        while time.time() < deadline and obs.supervisor_state(manager) == "running":
            time.sleep(5)

    def stop_all(self, fleet: Fleet, obs: Observer) -> None:
        for m in fleet.managers:
            self.stop(m["name"], obs)
        self._tmux("kill-server")


def run_gate(run: RunDir, fleet: Fleet, *, probe_df16: bool) -> int:
    env = preflight.patched_path(fleet, run.env())
    rpy = installed_rite_python(env["PATH"])
    items = preflight.run_all(fleet, run.env(), rpy, probe_df16=probe_df16)
    run.file("preflight.json").write_text(
        json.dumps([i.__dict__ for i in items], indent=1)
    )
    bad = [i for i in items if not i.ok]
    if bad:
        for i in bad:
            print(f"  NOT READY  {i.name}: {i.detail}")
        print(
            "run: refused — the gate is not run against a partial or unpatched build."
        )
        return REFUSED
    meta = json.loads(run.file("run.json").read_text())
    if meta.get("offline"):
        print(
            "run: refused — this run dir was set up --offline (no board, no PR target)."
        )
        return REFUSED

    tickets = json.loads(run.file("tickets.json").read_text())
    obs = Observer(run.project, env, rpy)
    sup = Supervisors(run, env)
    st = driver.DriverState()
    answer = run.file("owner-answer.txt").read_text().strip()
    run.append("inductions.jsonl", {"kind": "run-started"})
    sup.start(fleet.owner)
    for m in fleet.managers:
        if m["name"] != fleet.owner and fleet.run.get("start_planner_loop", True):
            sup.start(m["name"])

    deadline = time.time() + float(fleet.run["deadline_minutes"]) * 60
    poll = float(fleet.run["poll_seconds"])
    try:
        while time.time() < deadline:
            driver.sample(run, obs, fleet, st)
            driver.play_owner(run, obs, fleet, tickets, st, env, answer)
            driver.maybe_kill(run, obs, fleet, tickets, st, env)
            driver.maybe_restart_with_stale_state(
                run,
                obs,
                fleet,
                st,
                env,
                stop_owner=lambda: sup.stop(fleet.owner, obs),
                start_owner=lambda: sup.start(fleet.owner),
            )
            events = obs.events()
            done = all(
                delivered_prs(events, tickets[t.key]) for t in fleet.work_tickets
            )
            if done and st.kill_done and st.restart_done:
                # One more stretch so reconciliation and the journal can catch up.
                for _ in range(4):
                    time.sleep(poll)
                    driver.sample(run, obs, fleet, st)
                break
            time.sleep(poll)
    finally:
        sup.stop_all(fleet, obs)
    return judge(run, fleet, obs)


# ---------------------------------------------------------------- judge


def collect(run: RunDir, fleet: Fleet, obs: Observer) -> checks.Evidence:
    tickets = json.loads(run.file("tickets.json").read_text())
    managers = [m["name"] for m in fleet.managers]
    journal = []
    for m in managers:
        for j in obs.journal(obs.manager_dir(m)):
            journal.append({"manager": m, **j})
    pr_diffs = {}
    for t in fleet.work_tickets:
        prs = delivered_prs(obs.events(), tickets[t.key])
        if prs:
            d = subprocess.run(
                ["gh", "pr", "diff", prs[-1]["pr_url"]],
                capture_output=True,
                text=True,
                env=obs.env,
            )
            pr_diffs[t.key] = d.stdout
    gpu = next(w["name"] for w in fleet.workers if w.get("engine"))
    claude = next(w["name"] for w in fleet.workers if not w.get("engine"))
    planner = next(m["name"] for m in fleet.managers if m.get("preset") == "planner")
    started = [i for i in run.read("inductions.jsonl") if i["kind"] == "run-started"]
    return checks.Evidence(
        tickets=tickets,
        gpu_worker=gpu,
        claude_worker=claude,
        owner=fleet.owner,
        planner=planner,
        events=obs.events(),
        claims_samples=[
            {"at": s["at"], "claims": s["claims"]} for s in run.read("samples.jsonl")
        ],
        start_requests=run.read("start_requests.jsonl"),
        recovery=obs.recovery(),
        decompositions={"gpu-slug": obs.decomposition(tickets["gpu-slug"])},
        owner_log=run.read("owner.jsonl"),
        inductions=run.read("inductions.jsonl"),
        commands=run.read("commands.jsonl"),
        journal=journal,
        pr_diffs=pr_diffs,
        owner_answer=run.file("owner-answer.txt").read_text().strip(),
        run_started_at=started[0]["at"] if started else 0.0,
        stage_log=hooks.stage_log(obs, tickets),
        lifecycle_requests=hooks.lifecycle_requests(obs, managers),
        reconcile_reports=hooks.reconcile_reports(obs, managers),
        plan_review_requests=hooks.plan_review_requests(obs, fleet.owner),
    )


def report(run: RunDir, results: list[checks.Result]) -> str:
    v = checks.verdict(results)
    lines = [f"# v0.7.1 acceptance gate — {run.path.name}: **{v}**", ""]
    lines += ["| Check | Status | Evidence |", "|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r.criterion or r.name} | {r.status} | {r.evidence.replace('|', '/')} |"
        )
    run.file("report.md").write_text("\n".join(lines) + "\n")
    run.file("report.json").write_text(
        json.dumps({"verdict": v, "results": [r.__dict__ for r in results]}, indent=1)
    )
    return v


def judge(run: RunDir, fleet: Fleet, obs: Observer) -> int:
    results = checks.evaluate(collect(run, fleet, obs))
    v = report(run, results)
    print(run.file("report.md").read_text())
    return EXIT[v]


# ---------------------------------------------------------------- teardown


def teardown(run: RunDir, fleet: Fleet, *, delete_repo: bool) -> None:
    """Stop this run's supervisors and destroy ONLY this run's sandboxes: the ones
    whose names carry this project's sandbox prefix. Never `--all`."""
    env = preflight.patched_path(fleet, run.env())
    sh(
        run,
        ["tmux", "-L", f"rite-e2e-{run.path.name}", "kill-server"],
        phase="teardown",
        env=env,
    )
    events = Observer(run.project, env).events() if run.project.exists() else []
    ours = sorted({e["sandbox"] for e in events if e.get("sandbox")})
    for name in ours:
        sh(
            run,
            ["yoloai", "destroy", "--abandon-unapplied", name],
            phase="teardown",
            env=env,
        )
    meta = json.loads(run.file("run.json").read_text())
    if delete_repo and meta.get("repo"):
        sh(
            run,
            ["gh", "repo", "delete", meta["repo"], "--yes"],
            phase="teardown",
            env=env,
        )
    print(f"stopped the run's supervisors; destroyed {len(ours)} sandbox(es): {ours}")


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="harness",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="print the fleet, tickets and checks")
    pf = sub.add_parser("preflight", help="is this machine ready?")
    pf.add_argument("--probe-df16", action="store_true")
    su = sub.add_parser("setup")
    su.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    su.add_argument(
        "--offline",
        action="store_true",
        help="local dry setup: no repo, no board, no network",
    )
    su.add_argument(
        "--create-repo",
        action="store_true",
        help="create the run's private GitHub repo (board + PR target)",
    )
    for name in ("run", "check", "teardown"):
        sp = sub.add_parser(name)
        sp.add_argument("run_dir", type=Path)
        if name == "run":
            sp.add_argument("--probe-df16", action="store_true")
        if name == "teardown":
            sp.add_argument("--delete-repo", action="store_true")
    a = ap.parse_args(argv)
    fleet = load()

    if a.cmd == "plan":
        print(
            f"fleet: managers={[m['name'] for m in fleet.managers]} "
            f"workers={[w['name'] for w in fleet.workers]}"
        )
        for t in fleet.tickets:
            print(f"  ticket {t.key}: {t.title}  [{t.for_worker or 'decoy'}]")
        for c in checks.ALL:
            print(f"  check {c.__name__}")
        return 0
    if a.cmd == "preflight":
        env = preflight.patched_path(fleet, dict(os.environ))
        items = preflight.run_all(
            fleet, env, installed_rite_python(env["PATH"]), probe_df16=a.probe_df16
        )
        for i in items:
            print(f"  {'OK ' if i.ok else 'NO '} {i.name}: {i.detail}")
        return 0 if all(i.ok for i in items) else REFUSED
    if a.cmd == "setup":
        setup(fleet, a.runs_root, offline=a.offline, create_repo=a.create_repo)
        return 0
    run = RunDir(a.run_dir.resolve())
    if a.cmd == "run":
        return run_gate(run, fleet, probe_df16=a.probe_df16)
    if a.cmd == "check":
        env = preflight.patched_path(fleet, run.env())
        return judge(
            run, fleet, Observer(run.project, env, installed_rite_python(env["PATH"]))
        )
    if a.cmd == "teardown":
        teardown(run, fleet, delete_repo=a.delete_repo)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
