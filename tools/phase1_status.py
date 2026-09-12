#!/usr/bin/env python3
"""Per-module Phase-1 completion, measured — not recalled.

Run:  python tools/phase1_status.py [--markdown] [--json]

WHY THIS EXISTS
---------------
Percentages quoted from memory move. The same module has been called 6%,
then 2%, then 9% in three successive conversations, and "93%" once measured
45%. This script is the answer to "what is the number?" so that the number
is re-derived from committed state every time rather than remembered.

WHAT COMPLETION MEANS HERE
--------------------------
Two independent measurements, deliberately NOT averaged into one score,
because they fail in different directions and a single number hides that.

1. REACHABILITY (`--reach`). Static import graph from the real entry points
   (`rite_ai.cli.main:cli`, the `rite` / `rite-ai` console scripts, plus
   `rite_ai.gate.__main__`). A module with no import path from an entry
   point is unreachable behaviour no matter how complete its code or how
   green its tests. This repo has shipped that five times — `rite prepare`
   fully built with zero callers, `rite status` a hand-rolled copy of an
   uncalled module — so it is measured first and separately.

   Nested/function-local imports count: this codebase imports inside
   functions deliberately (CLI startup cost), so a module-level-only scan
   would report most of the package as dead.

2. DoD COVERAGE (`--dod`). The denominator is the Definition of Done for
   Phase 1, `.docs/IMPLEMENTATION_PLAN.md` §P1.16 — the project's own
   statement of what "Phase 1 is finished" means. Each bullet becomes one
   item with a mechanical probe wherever a probe can exist.

   Items are one of:
     PROBE    — asserted here, in a throwaway project, by running the real
                CLI. Pass/fail is observed, not asserted by a human.
     EXTERNAL — needs a resource this script must not use: a real JIRA
                board, a GitHub token, `yoloai`, the user's OS keychain, or
                spawning real `claude` sessions that cost real quota.
     HUMAN    — needs judgement or a live Claude session (a Dispatch slash
                command doing the work; agents actually spawning).

   Percentage = PROBE-passed / PROBE-total. EXTERNAL and HUMAN items are
   reported as a separate count and are NEVER folded into the numerator or
   silently absorbed into the denominator — that is the move that turns a
   45% into a 93%.

WHAT THIS SCRIPT CANNOT TELL YOU
--------------------------------
A passing probe is evidence that a behaviour is reachable and does the
thing the DoD names once, in a clean project. It is not evidence the module
is correct. Sixteen defects were found by hand-rehearsal on this repo that
the full suite passed at the time. Treat every percentage below as an upper
bound on completion, not an estimate of it.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "rite_ai"
PLAN = REPO / ".docs" / "IMPLEMENTATION_PLAN.md"

ENTRY_POINTS = ["rite_ai.cli.main", "rite_ai.gate.__main__"]

PROBE, EXTERNAL, HUMAN = "PROBE", "EXTERNAL", "HUMAN"


# --------------------------------------------------------------------------
# 1. Reachability
# --------------------------------------------------------------------------


def module_name(path: Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def all_modules() -> dict[str, Path]:
    out = {}
    for p in sorted(SRC.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out[module_name(p)] = p
    return out


def imports_of(path: Path, owner: str | None = None) -> set[str]:
    """Every `rite_ai.*` module this file imports, at ANY nesting depth.

    Both forms matter here and missing either one invents orphans:
      * nested/function-local imports — this package imports inside
        functions on purpose, to keep CLI startup cheap;
      * relative imports — `cli/init/__init__.py` re-exports its siblings
        with `from .questionnaire import ...`, and a scan that skipped
        those reported six live init modules as dead.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    found: set[str] = set()

    def absolutise(level: int, mod: str | None) -> str | None:
        if owner is None:
            return None
        base = owner.split(".")
        # Inside a package's __init__, `.` means the package itself; inside a
        # submodule it means the parent package.
        if (SRC.parent / Path(*base) / "__init__.py").is_file():
            pkg = base
        else:
            pkg = base[:-1]
        pkg = pkg[: len(pkg) - (level - 1)] if level > 1 else pkg
        return ".".join(pkg + ([mod] if mod else []))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("rite_ai"):
                    found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = (
                absolutise(node.level, node.module)
                if node.level
                else (
                    node.module
                    if node.module and node.module.startswith("rite_ai")
                    else None
                )
            )
            if not base:
                continue
            found.add(base)
            for a in node.names:
                found.add(f"{base}.{a.name}")
    return found


def reachable_set(modules: dict[str, Path]) -> set[str]:
    seen: set[str] = set()
    queue = [e for e in ENTRY_POINTS if e in modules]
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        for target in imports_of(modules[name], owner=name):
            # `from rite_ai.pool import fill` yields both `rite_ai.pool` and
            # `rite_ai.pool.fill`; only the former is a module.
            for candidate in (target, target.rsplit(".", 1)[0]):
                if candidate in modules and candidate not in seen:
                    queue.append(candidate)
    return seen


def top_package(name: str) -> str:
    parts = name.split(".")
    return parts[1] if len(parts) > 1 else "(root)"


# --------------------------------------------------------------------------
# 2. DoD probes
# --------------------------------------------------------------------------


@dataclass
class Item:
    id: str
    module: str
    kind: str
    text: str
    probe: str | None = None  # name of a probe_* function
    result: bool | None = None
    detail: str = ""


@dataclass
class Env:
    """A throwaway rite project the probes run against."""

    root: Path
    rite: str
    interpreter: str = ""
    env: dict[str, str] = field(default_factory=dict)

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.rite, *args],
            cwd=str(cwd or self.root),
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, **self.env},
        )

    def py(self, code: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run `code` against the SAME `rite_ai` the probes are measuring.

        ⚠ A probe that needs to look at rite's own API must come through
        here, never through a bare `import rite_ai` in this file. This
        script is run as `python3 tools/phase1_status.py`, so a top-level
        or in-probe import binds to whatever interpreter the user typed —
        on this machine homebrew's python3.14, which has no `rite_ai`
        installed at all. That is not a hypothetical: D15 did exactly this
        and reported `ModuleNotFoundError` as a FAILING DoD ITEM for
        `rite start`, which was behaving correctly the whole time. A brief
        was then written against that number.

        The failure mode is worse than a crash, because it is silent when
        it does not crash: an interpreter that HAPPENS to have some
        `rite_ai` installed would answer from that build while every other
        probe shelled out to `self.rite`, and the two halves of one probe
        would describe two different codebases. `resolve_rite_binary`
        refuses exactly that split for the CLI; this closes the same hole
        for in-process checks by reusing the interpreter it verified."""
        assert self.interpreter, "Env.interpreter unset — build Env via make_env"
        return subprocess.run(
            [self.interpreter, "-c", code],
            cwd=str(cwd or self.root),
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, **self.env},
        )


class StaleBinary(RuntimeError):
    """The `rite` about to be measured is not the `rite` in this checkout."""


def resolve_rite_binary() -> tuple[str, str]:
    """The `rite` console script that imports THIS working tree, and the
    interpreter that proves it — verified, not assumed.

    This check exists because its absence produced a wrong number. The first
    version took `shutil.which("rite")`, which on this machine resolves to
    `~/.local/bin/rite`: a uv-tool install holding a **copied, non-editable**
    `rite_ai` package. Every probe result was therefore a statement about
    whatever build was last installed, not about the commit being measured —
    and because that install happened to be recent, the numbers looked
    entirely plausible while `start`'s newly-added scheduler reporting was
    invisible to the probe that existed to check it.

    A status script that silently measures a different build than the one in
    front of you is worse than no status script. So: prefer the repo's own
    editable venv, then anything on PATH, and in both cases ASK the binary
    where it imports `rite_ai` from. Refuse rather than measure a stranger.
    """
    candidates = [REPO / ".venv" / "bin" / "rite"]
    on_path = shutil.which("rite")
    if on_path:
        candidates.append(Path(on_path))

    tried: list[str] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        # Console scripts carry their interpreter in the shebang; asking that
        # interpreter is the only way to learn which `rite_ai` actually gets
        # imported, for both venv and uv-tool layouts.
        shebang = candidate.read_text(errors="replace").splitlines()[0]
        if not shebang.startswith("#!"):
            continue
        interpreter = shebang[2:].strip()
        probe = subprocess.run(
            [
                interpreter,
                "-c",
                "import rite_ai, sys; sys.stdout.write(rite_ai.__file__)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        imported = Path(probe.stdout.strip()) if probe.stdout.strip() else None
        if imported and SRC in imported.parents:
            # The interpreter travels with the script. It is the one just
            # PROVEN to import this checkout, so `Env.py` cannot drift onto
            # a different build than `Env.run` measures.
            return str(candidate), interpreter
        tried.append(f"{candidate} -> {imported or 'import failed'}")

    raise StaleBinary(
        "no `rite` on this machine imports "
        f"{SRC}.\n  tried: " + "\n         ".join(tried or ["(none found)"]) + "\n"
        "Install this checkout editable first:  uv tool install -e .\n"
        "Refusing to measure, because a probe run against a different build "
        "reports that build's behaviour under this commit's name."
    )


def make_env(tmp: Path) -> Env:
    """A real, initialised rite project, isolated from the user's machine."""
    rite, interpreter = resolve_rite_binary()
    gitconfig = tmp / "gitconfig"
    gitconfig.write_text(
        "[init]\n\tdefaultBranch = main\n"
        "[user]\n\tname = phase1 status\n\temail = status@rite.invalid\n"
    )
    empty_transcripts = tmp / "no-transcripts"
    empty_transcripts.mkdir()
    env = {
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_SYSTEM": "/dev/null",
        # Never scan the real ~/.claude/projects: slow, and couples the
        # measurement to state nothing here controls.
        "RITE_CLAUDE_PROJECTS_DIR": str(empty_transcripts),
        "HOME": str(tmp / "home"),
    }
    (tmp / "home").mkdir()

    root = tmp / "proj"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", "."],
        cwd=root,
        env={**os.environ, **env},
        check=True,
        capture_output=True,
    )
    cfg = tmp / "init.yaml"
    cfg.write_text(
        "project:\n  name: probe\n  role: owner\n  root_branch: main\n"
        "what:\n  kind: backend\n  features: probe project\n"
        "technology:\n  platform: linux\n  languages: [python]\n"
    )
    e = Env(root=root, rite=rite, interpreter=interpreter, env=env)
    e.run("init", "--config", str(cfg), "--yes")
    return e


# -- individual probes ------------------------------------------------------


def probe_init_config(e: Env) -> tuple[bool, str]:
    ok = (e.root / ".rite" / "config.yaml").is_file()
    ok = ok and (e.root / ".rite" / "brief.yaml").is_file()
    return ok, "`.rite/` created by `rite init --config --yes`"


def probe_init_generates_claude(e: Env) -> tuple[bool, str]:
    agents = list((e.root / ".claude" / "agents").glob("*.md"))
    cmds = list((e.root / ".claude" / "commands").glob("*.md"))
    ok = (e.root / "CLAUDE.md").is_file() and len(agents) == 4 and len(cmds) == 3
    return ok, f"CLAUDE.md + {len(agents)} agents + {len(cmds)} commands"


def probe_init_detects_repos(e: Env) -> tuple[bool, str]:
    tmp = e.root.parent / "detect"
    tmp.mkdir(exist_ok=True)
    sub = tmp / "backend"
    sub.mkdir(exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "."],
        cwd=sub,
        env={**os.environ, **e.env},
        capture_output=True,
    )
    cfg = tmp / "i.yaml"
    cfg.write_text(
        "project:\n  name: d\n  role: owner\n  root_branch: main\n"
        "what:\n  kind: backend\n"
    )
    subprocess.run(
        ["git", "init", "-q", "."],
        cwd=tmp,
        env={**os.environ, **e.env},
        capture_output=True,
    )
    e.run("init", "--config", str(cfg), "--yes", cwd=tmp)
    mods = (
        (tmp / ".rite" / "modules.yaml").read_text()
        if (tmp / ".rite" / "modules.yaml").is_file()
        else ""
    )
    return "backend" in mods, "nested repo registered in modules.yaml"


def probe_claims_refuse_overlap(e: Env) -> tuple[bool, str]:
    e.run("claim", "src/api", "--worker", "alpha", "--ticket", "T-1")
    second = e.run("claim", "src/api/handler.py", "--worker", "beta", "--ticket", "T-2")
    e.run("release", "--worker", "alpha")
    return second.returncode != 0, f"overlapping claim exit={second.returncode}"


def probe_ticket_template_release_after_merge(e: Env) -> tuple[bool, str]:
    t = (REPO / "templates" / "commands" / "ticket.md").read_text()
    merge_at = t.find("Merge once reviewed")
    release_at = t.find("Release your claims after the merge")
    ok = merge_at != -1 and release_at != -1 and merge_at < release_at
    return ok, "template orders merge before release"


def probe_publish_gate_catches(e: Env) -> tuple[bool, str]:
    bad = e.root / "leak.py"
    bad.write_text('HOME = "/Users/someone/dev/thing"\nAWS = "AKIAIOSFODNN7EXAMPLE"\n')
    subprocess.run(
        ["git", "add", "-A"],
        cwd=e.root,
        env={**os.environ, **e.env},
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "planted"],
        cwd=e.root,
        env={**os.environ, **e.env},
        capture_output=True,
    )
    r = e.run("publish", "check")
    bad.unlink(missing_ok=True)
    return r.returncode == 2, f"exit={r.returncode} (2 = findings)"


def probe_kb_add_file(e: Env) -> tuple[bool, str]:
    f = e.root.parent / "standards.md"
    f.write_text("# Standards\n\nUse tabs. Just kidding.\n")
    e.run("kb", "add", str(f))
    idx = e.root / ".rite" / "kb" / "INDEX.md"
    listed = e.run("kb", "list")
    ok = idx.is_file() and "standards" in (idx.read_text() + listed.stdout)
    return ok, "authored file copied and indexed"


def probe_start_reads_handover(e: Env) -> tuple[bool, str]:
    e.run("handover", "write")
    r = e.run("start")
    return "handover snapshot" in r.stdout.lower(), "start reports the snapshot it read"


def probe_start_reports_setup_steps(e: Env) -> tuple[bool, str]:
    """DoD, as resolved by D-50: `start` REPORTS the persistent/networked/
    quota-spending steps rather than performing them.

    The previous version of this probe asserted the old DoD wording — that
    `start` registers the scheduler — and failed. That failure was the
    finding: the spec was asking for a lifecycle command that writes a cron
    entry as a side effect. This probe now checks the resolved contract, and
    checks BOTH halves, because "reports it" and "does not do it" fail
    independently: a `start` that silently installed the tick would satisfy a
    wording-only assertion.
    """
    r = e.run("start")
    out = r.stdout.lower()
    reports = "scheduler" in out and "rite scheduler install" in out
    # Through the measured build's own interpreter — see `Env.py`. This
    # line read `from rite_ai.scheduler import is_installed` and raised
    # ModuleNotFoundError under the interpreter that runs this script,
    # which the runner recorded as a failing DoD item for `start`.
    check = e.py(
        # `Env.py` runs in the project root, so cwd IS the root under test.
        "import sys, pathlib\n"
        "from rite_ai.scheduler import is_installed\n"
        "sys.stdout.write('1' if is_installed(pathlib.Path.cwd()) else '0')"
    )
    if check.returncode != 0 or check.stdout.strip() not in {"0", "1"}:
        return False, f"is_installed check failed: {check.stderr.strip()[:160]}"
    performed = check.stdout.strip() == "1"
    return (reports and not performed), (
        f"reports scheduler state={reports}, silently installed={performed}"
    )


def probe_start_validates_schedule(e: Env) -> tuple[bool, str]:
    """The health check `start` genuinely owes: the schedule it is about to
    be governed by (D-50). A gap defaults to 0 workers, which is safe but
    rarely intended, and `start` is the moment it begins to matter."""
    e.run("schedule", "set-timezone", "Europe/Warsaw")
    e.run("schedule", "set", "09:00-12:00", "2")
    out = e.run("start").stdout.lower()
    e.run("schedule", "set", "12:00-09:00", "0")
    return "schedule:" in out, "start surfaces the 21-hour coverage gap"


def probe_start_does_not_fill_pool(e: Env) -> tuple[bool, str]:
    """D-50's sharpest half, and BOTH halves of it. NEVER runs `pool fill`.

    The negative half: `start` must not fill the pool, checked by asserting
    no pool state was created. Spent quota is the one damage no cleanup
    reverses.

    The positive half: P1.16 promises the pool depth is *reported* — that
    is the behaviour D-50 put in place of the top-up it removed, not a
    consolation prize. Asserting only the negative passed happily against a
    `start` that said nothing about the pool at all, which is how that gap
    survived: a Manager told nothing concludes it has warm capacity. The
    two fail independently, so both are checked, the same way D15 checks
    the scheduler."""
    (e.root / ".rite" / "pool.json").unlink(missing_ok=True)
    out = e.run("start").stdout.lower()
    created = (e.root / ".rite" / "pool.json").exists()
    reports = "coordinator pool" in out and "rite pool fill" in out
    return (reports and not created), (
        f"reports depth={reports}, wrote pool state={created} (must be False)"
    )


def probe_stop_releases_claims(e: Env) -> tuple[bool, str]:
    e.run("claim", "src/x", "--worker", "alpha", "--ticket", "T-9")
    e.run("stop")
    r = e.run("status")
    return "src/x" not in r.stdout, "claims released by stop"


def probe_stop_offline(e: Env) -> tuple[bool, str]:
    r = e.run("stop")
    return r.returncode == 0, f"stop with no backend exit={r.returncode}"


def probe_handover_converges(e: Env) -> tuple[bool, str]:
    """One perform_handover, called by stop AND by the scheduler tick."""
    src = (SRC / "lifecycle" / "commands.py").read_text()
    sched = (SRC / "scheduler" / "__init__.py").read_text()
    ok = "def perform_handover" in src and "perform_handover" in sched
    return ok, "scheduler tick calls the same perform_handover as stop"


def probe_handover_timestamp_advances(e: Env) -> tuple[bool, str]:
    e.run("handover", "write")
    first = e.run("handover", "show").stdout
    import time as _t

    _t.sleep(1.1)
    e.run("handover", "write")
    second = e.run("handover", "show").stdout
    return first != second and bool(second.strip()), "snapshot rewritten on each write"


def probe_watchdog_exit_codes(e: Env) -> tuple[bool, str]:
    r = e.run("watchdog")
    return r.returncode in (
        0,
        1,
    ), f"watchdog exit={r.returncode} (0 clean / 1 needs judgement)"


def probe_status_sections(e: Env) -> tuple[bool, str]:
    out = e.run("status").stdout.lower()
    want = ["claim", "pool", "burn", "coordination"]
    missing = [w for w in want if w not in out]
    return not missing, (
        "all sections present" if not missing else f"missing: {missing}"
    )


def probe_status_aggregates(e: Env) -> tuple[bool, str]:
    e.run("projects", "add", "probe", str(e.root))
    outside = e.root.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    r = e.run("status", cwd=outside)
    return "probe" in r.stdout, "aggregate view lists the registered alias"


def probe_doctor_validates_schedule(e: Env) -> tuple[bool, str]:
    """§9.8's actual claim: doctor re-runs the schedule check for a config
    that was **hand-edited** rather than written through `rite schedule set`.

    The previous version of this probe set a VALID schedule and looked for
    the word "schedule" in doctor's output — which doctor only prints when
    it finds a problem, so a healthy project failed the probe. Third probe in
    this file to assert on a string that only exists on the failure path;
    the pattern is worth naming. Plant a problem `schedule set` would have
    refused, and assert doctor catches it.

    Self-contained: writes its own config and restores it, so it neither
    depends on nor disturbs the schedule other probes leave behind.
    """
    cfg = e.root / ".rite" / "config.yaml"
    original = cfg.read_text()
    try:
        cfg.write_text(
            original + "\nschedule:\n  timezone: Europe/Warsaw\n  windows:\n"
            "    - hours: '09:00-18:00'\n      workers: 500\n"
        )
        r = e.run("doctor")
        out = r.stdout.lower()
        caught = "schedule:" in out and "500" in out
        return (
            caught,
            f"doctor flags a hand-edited over-cap window (exit {r.returncode})",
        )
    finally:
        cfg.write_text(original)


def probe_schedule_upsert_splits(e: Env) -> tuple[bool, str]:
    """The specific trap: a whole-list-replace passes every other item."""
    e.run("schedule", "set-timezone", "Europe/Warsaw")
    e.run("schedule", "set", "09:00-18:00", "3")
    e.run("schedule", "set", "12:00-14:00", "1")
    out = e.run("schedule", "show").stdout
    windows = re.findall(r"(\d{2}:\d{2})-(\d{2}:\d{2})", out)
    ok = len(windows) >= 3
    return ok, f"{len(windows)} windows after overlapping set (want >= 3)"


def probe_schedule_cap_refused(e: Env) -> tuple[bool, str]:
    e.run("schedule", "set-timezone", "Europe/Warsaw")
    r = e.run("schedule", "set", "09:00-18:00", "500")
    return (
        r.returncode != 0,
        f"over-cap window exit={r.returncode} (refused, not clamped)",
    )


def probe_budget_no_recommendation(e: Env) -> tuple[bool, str]:
    out = e.run("budget").stdout.lower()
    banned = ["recommend", "suggest", "you should run", "workers:"]
    hit = [b for b in banned if b in out]
    machine_wide = "machine" in out
    return (not hit and machine_wide), (
        f"machine-wide labelled={machine_wide}, banned phrases={hit or 'none'}"
    )


def probe_budget_no_percentage(e: Env) -> tuple[bool, str]:
    out = e.run("budget").stdout
    has_pct_of_quota = re.search(r"%\s*of\s*(weekly_token_budget|quota)", out)
    return has_pct_of_quota is None, "no percentage-of-quota, per SPEC §2.6.2 amendment"


def probe_budget_no_control_path(e: Env) -> tuple[bool, str]:
    """Static: budget/ must not reach concurrency machinery at all."""
    forbidden = {
        "rite_ai.schedule",
        "rite_ai.pool",
        "rite_ai.workspace",
        "rite_ai.sandbox",
    }
    leaked = set()
    for p in (SRC / "budget").rglob("*.py"):
        leaked |= imports_of(p, owner=module_name(p)) & forbidden
    return (
        not leaked,
        f"budget imports of concurrency modules: {sorted(leaked) or 'none'}",
    )


def probe_projects_crud(e: Env) -> tuple[bool, str]:
    e.run("projects", "add", "alias1", str(e.root))
    listed = e.run("projects", "list").stdout
    e.run("projects", "remove", "alias1")
    gone = e.run("projects", "list").stdout
    return ("alias1" in listed and "alias1" not in gone), "add/list/remove round-trip"


def probe_projects_aggregate_warning(e: Env) -> tuple[bool, str]:
    from_mod = (SRC / "dispatch" / "__init__.py").read_text()
    wired = "aggregate_load_warning" in (SRC / "cli" / "main.py").read_text()
    return (
        "def aggregate_load_warning" in from_mod and wired
    ), "warning wired into `projects add`"


def probe_credential_check_exit(e: Env) -> tuple[bool, str]:
    r = e.run("credential", "check", "definitely_not_a_real_key_xyz")
    return (
        r.returncode == 1,
        f"missing credential exit={r.returncode} (guards `&&` correctly)",
    )


def _seed_stale_pool_slot(e: Env) -> None:
    """A slot whose tmux session does not exist and whose lease has lapsed.

    An empty pool cannot demonstrate the live/stale split — with zero slots
    there is nothing to call stale, so asserting on the word "stale" against
    a fresh project tests the probe's wording and not the behaviour. Seed
    one provably-dead slot instead.
    """
    import time as _t

    state = e.root / ".rite" / "pool.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    long_ago = _t.time() - 60 * 60 * 24
    state.write_text(
        json.dumps(
            {
                "slots": [
                    {
                        "name": "rite-pool-probe-definitely-not-running",
                        "created_at": long_ago,
                        "last_live_at": long_ago,
                        "worker": "probe-slot",
                        "unreachable_since": long_ago,
                    }
                ]
            }
        )
    )


def probe_pool_status_live_stale(e: Env) -> tuple[bool, str]:
    _seed_stale_pool_slot(e)
    out = e.run("pool", "status").stdout.lower()
    (e.root / ".rite" / "pool.json").unlink(missing_ok=True)
    ok = "stale" in out and ("live" in out or "0/" in out)
    return (
        ok,
        f"dead slot reported stale, not counted live ({out.strip().splitlines()[:1]})",
    )


def probe_pool_warns_below_threshold(e: Env) -> tuple[bool, str]:
    """DoD: warns *stating how many sessions are needed*, not merely that
    the pool is low."""
    out = e.run("pool", "status").stdout.lower()
    ok = "more needed" in out and "pool fill" in out
    return ok, "warning names the shortfall and the remedy"


def probe_pool_status_readonly(e: Env) -> tuple[bool, str]:
    outside = e.root.parent / "readonly-probe"
    if outside.exists():
        shutil.rmtree(outside)
    outside.mkdir()
    e.run("pool", "status", cwd=outside)
    return not (
        outside / ".rite"
    ).exists(), "`pool status` creates no .rite/ where it runs"


def probe_pool_fill_refuses_over_cap(e: Env) -> tuple[bool, str]:
    """Never actually fills — asserts the refusal path without spawning."""
    r = e.run("pool", "fill", "--count", "500")
    refused = r.returncode != 0 or "refus" in (r.stdout + r.stderr).lower()
    return refused, "a 500-session target is refused, not clamped"


def probe_review_runs_checklist(e: Env) -> tuple[bool, str]:
    """Assert on checklist CONTENT, not on the word "checklist".

    `rite review` prints the merged checklist itself; an earlier version of
    this probe looked for the literal word and reported a working command as
    broken. Match the default template's own section headings instead, which
    is what "ran the checklist" actually looks like on stdout.
    """
    r = e.run("review")
    out = r.stdout + r.stderr
    want = ["Security", "Correctness", "Verification"]
    present = [w for w in want if w in out]
    return len(present) == len(want), f"sections rendered: {present}"


def probe_prepare_reachable(e: Env) -> tuple[bool, str]:
    r = e.run("prepare", "--worker", "nosuchworker")
    answered = r.returncode != 0 and bool((r.stdout + r.stderr).strip())
    return answered, "`rite prepare` is wired and answers (was orphaned once)"


PROBES = {name: fn for name, fn in list(globals().items()) if name.startswith("probe_")}


# --------------------------------------------------------------------------
# The DoD table — one row per §P1.16 bullet
# --------------------------------------------------------------------------

ITEMS: list[Item] = [
    Item(
        "D01",
        "cli",
        PROBE,
        "`rite init` creates a working project (`--config`)",
        "probe_init_config",
    ),
    Item("D02", "cli", HUMAN, "`rite init` interactive questionnaire end-to-end"),
    Item(
        "D03",
        "cli",
        PROBE,
        "`rite init` detects existing repos, adds as modules",
        "probe_init_detects_repos",
    ),
    Item(
        "D04",
        "cli",
        PROBE,
        "`rite init` generates `CLAUDE.md` and `.claude/`",
        "probe_init_generates_claude",
    ),
    Item(
        "D05",
        "claims",
        PROBE,
        "Two workers cannot collide — claims enforce it",
        "probe_claims_refuse_overlap",
    ),
    Item(
        "D06",
        "templates",
        PROBE,
        "`/ticket` template releases after merge (D-41)",
        "probe_ticket_template_release_after_merge",
    ),
    Item(
        "D07",
        "templates",
        HUMAN,
        "`/ticket` actually claims→works→PRs→releases in a session",
    ),
    Item(
        "D08",
        "review",
        PROBE,
        "`rite review` runs checklists",
        "probe_review_runs_checklist",
    ),
    Item("D09", "review", HUMAN, "`rite review` spawns review agents"),
    Item(
        "D10",
        "gate",
        PROBE,
        "`rite publish check` catches secrets and home paths",
        "probe_publish_gate_catches",
    ),
    Item(
        "D11",
        "kb",
        PROBE,
        "`rite kb add <file>` copies and indexes authored content",
        "probe_kb_add_file",
    ),
    Item(
        "D12", "kb", EXTERNAL, "`rite kb add <url>` fetches and caches (needs network)"
    ),
    Item(
        "D13",
        "tickets",
        EXTERNAL,
        "`rite board create/move/list/query/label/link` vs a real JIRA board",
    ),
    Item(
        "D14",
        "lifecycle",
        PROBE,
        "`rite start` reads the handover snapshot first",
        "probe_start_reads_handover",
    ),
    Item(
        "D15",
        "lifecycle",
        PROBE,
        "`rite start` reports scheduler/cache, never performs them (D-50)",
        "probe_start_reports_setup_steps",
    ),
    Item(
        "D15b",
        "lifecycle",
        PROBE,
        "`rite start` validates the schedule it runs under (D-50)",
        "probe_start_validates_schedule",
    ),
    Item(
        "D15c",
        "lifecycle",
        PROBE,
        "`rite start` reports pool depth and never fills it (D-50)",
        "probe_start_does_not_fill_pool",
    ),
    Item(
        "D16",
        "lifecycle",
        PROBE,
        "`rite stop` releases claims",
        "probe_stop_releases_claims",
    ),
    Item(
        "D17",
        "lifecycle",
        EXTERNAL,
        "`rite stop`'s handover actually reaches the ticket backend",
    ),
    Item(
        "D18",
        "lifecycle",
        PROBE,
        "`stop` succeeds offline (queued locally)",
        "probe_stop_offline",
    ),
    Item(
        "D19",
        "lifecycle",
        PROBE,
        "stop / timeout / zero-window share one `perform_handover`",
        "probe_handover_converges",
    ),
    Item(
        "D20",
        "handover",
        PROBE,
        "Snapshot written every cycle; timestamp advances",
        "probe_handover_timestamp_advances",
    ),
    Item(
        "D21",
        "watchdog",
        PROBE,
        "Watchdog: exit 0 clean, exit 1 when judgement needed",
        "probe_watchdog_exit_codes",
    ),
    Item(
        "D22",
        "reporting",
        PROBE,
        "`rite status` shows claims, pool, burn-rate, counters",
        "probe_status_sections",
    ),
    Item(
        "D23",
        "dispatch",
        PROBE,
        "`rite status` outside a project aggregates registered ones",
        "probe_status_aggregates",
    ),
    Item(
        "D24",
        "cli",
        PROBE,
        "`rite doctor` validates schedule coverage and caps",
        "probe_doctor_validates_schedule",
    ),
    Item(
        "D25",
        "schedule",
        PROBE,
        "`rite schedule set` is an upsert that splits overlaps",
        "probe_schedule_upsert_splits",
    ),
    Item(
        "D26",
        "schedule",
        PROBE,
        "Out-of-cap window refused, never clamped",
        "probe_schedule_cap_refused",
    ),
    Item(
        "D27",
        "budget",
        PROBE,
        "Burn-rate is machine-wide and recommends nothing",
        "probe_budget_no_recommendation",
    ),
    Item(
        "D28",
        "budget",
        PROBE,
        "No percentage-of-quota, no exhaustion warning (§2.6.2)",
        "probe_budget_no_percentage",
    ),
    Item(
        "D29",
        "budget",
        PROBE,
        "Burn-rate has no path into concurrency control",
        "probe_budget_no_control_path",
    ),
    Item(
        "D30",
        "dispatch",
        PROBE,
        "`rite projects list/add/remove` round-trip",
        "probe_projects_crud",
    ),
    Item(
        "D31",
        "dispatch",
        PROBE,
        "`projects add` warns on aggregate scheduled load",
        "probe_projects_aggregate_warning",
    ),
    Item(
        "D32",
        "credentials",
        PROBE,
        "`rite credential check` answers in its exit code",
        "probe_credential_check_exit",
    ),
    Item(
        "D33",
        "credentials",
        EXTERNAL,
        "`credential set/rotate` write the real OS keychain",
    ),
    Item(
        "D34",
        "pool",
        PROBE,
        "`rite pool status` splits live from stale",
        "probe_pool_status_live_stale",
    ),
    Item(
        "D35",
        "pool",
        PROBE,
        "`rite pool status` is genuinely read-only",
        "probe_pool_status_readonly",
    ),
    Item(
        "D36",
        "pool",
        PROBE,
        "`pool fill` refuses an over-cap target rather than clamping",
        "probe_pool_fill_refuses_over_cap",
    ),
    Item(
        "D37",
        "pool",
        EXTERNAL,
        "`pool fill` actually tops up (spawns real `claude` sessions)",
    ),
    Item(
        "D38",
        "pool",
        PROBE,
        "Warns below `pool.warn_threshold`, stating how many are needed",
        "probe_pool_warns_below_threshold",
    ),
    Item(
        "D39",
        "workspace",
        PROBE,
        "`rite prepare` is reachable and answers",
        "probe_prepare_reachable",
    ),
    Item(
        "D40",
        "sandbox",
        EXTERNAL,
        "Scoped token provisioned without passing through chat",
    ),
    Item(
        "D41", "sandbox", EXTERNAL, "A Worker can push/PR/merge from inside its sandbox"
    ),
    Item("D42", "sandbox", EXTERNAL, "An over-scoped token is refused or flagged"),
    Item("D43", "(dogfood)", HUMAN, "Bentora configured as a rite project"),
]


def check_plan_drift() -> str:
    """The DoD table above is a transcription. Say so if the source moved."""
    if not PLAN.is_file():
        return (
            "⚠ `.docs/IMPLEMENTATION_PLAN.md` not present (gitignored) — "
            "DoD table unverified against source."
        )
    text = PLAN.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"### P1\.16\..*?(?=\n---|\n## )", text, re.S)
    if not m:
        return "⚠ §P1.16 not found in the plan — DoD table unverified against source."
    bullets = len(re.findall(r"^- ", m.group(0), re.M))
    return (
        f"Denominator: `.docs/IMPLEMENTATION_PLAN.md` §P1.16, {bullets} bullets, "
        f"decomposed here into {len(ITEMS)} separately-checkable items (several "
        f"bullets name more than one behaviour). Re-transcribe if §P1.16 changes."
    )


# --------------------------------------------------------------------------


def run_probes() -> None:
    with tempfile.TemporaryDirectory(prefix="rite-phase1-") as td:
        e = make_env(Path(td))
        for item in ITEMS:
            if item.kind != PROBE:
                continue
            fn = PROBES.get(item.probe or "")
            if fn is None:
                item.result, item.detail = False, "probe missing"
                continue
            try:
                item.result, item.detail = fn(e)
            except Exception as exc:  # a probe that errors is a probe that failed
                item.result, item.detail = (
                    False,
                    f"probe error: {type(exc).__name__}: {exc}",
                )


def summarise() -> dict:
    modules = all_modules()
    reached = reachable_set(modules)
    pkg_reach: dict[str, dict] = {}
    for name in modules:
        pkg = top_package(name)
        d = pkg_reach.setdefault(pkg, {"total": 0, "reached": 0, "orphans": []})
        d["total"] += 1
        if name in reached:
            d["reached"] += 1
        else:
            d["orphans"].append(name)

    by_module: dict[str, dict] = {}
    for item in ITEMS:
        d = by_module.setdefault(
            item.module,
            {"probe": 0, "passed": 0, "external": 0, "human": 0, "items": []},
        )
        d["items"].append(item)
        if item.kind == PROBE:
            d["probe"] += 1
            d["passed"] += 1 if item.result else 0
        elif item.kind == EXTERNAL:
            d["external"] += 1
        else:
            d["human"] += 1
    return {
        "reach": pkg_reach,
        "dod": by_module,
        "modules": modules,
        "reached": reached,
    }


def render_markdown(s: dict) -> str:
    out: list[str] = []
    out.append("## Phase-1 completion, measured\n")
    out.append(f"_{check_plan_drift()}_\n")
    out.append(
        "Percentage = probes passed / probes runnable here. EXTERNAL and HUMAN "
        "items are excluded from both sides and counted separately — never "
        "folded in.\n"
    )
    out.append("| Module | DoD % | passed/probes | external | human | files reached |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for mod in sorted(s["dod"]):
        d = s["dod"][mod]
        pct = f"{100 * d['passed'] / d['probe']:.0f}%" if d["probe"] else "—"
        r = s["reach"].get(mod)
        reach = f"{r['reached']}/{r['total']}" if r else "n/a"
        out.append(
            f"| `{mod}` | {pct} | {d['passed']}/{d['probe']} | "
            f"{d['external']} | {d['human']} | {reach} |"
        )
    tp = sum(d["probe"] for d in s["dod"].values())
    ta = sum(d["passed"] for d in s["dod"].values())
    te = sum(d["external"] for d in s["dod"].values())
    th = sum(d["human"] for d in s["dod"].values())
    out.append(
        f"| **TOTAL** | **{100 * ta / tp:.0f}%** | **{ta}/{tp}** "
        f"| **{te}** | **{th}** | |"
    )
    total_items = tp + te + th
    out.append(
        f"\n**Read the two numbers together.** {ta}/{tp} = "
        f"{100 * ta / tp:.0f}% of the DoD items that CAN be checked here pass. "
        f"But only {tp}/{total_items} = {100 * tp / total_items:.0f}% of the DoD "
        f"has any mechanical check at all — the remaining {te + th} items "
        f"({te} EXTERNAL, {th} HUMAN) are the integration-heavy ones (a real "
        f"JIRA board, a real sandbox pushing a real PR, `/ticket` end-to-end, "
        f"the dogfood). **The high percentage is partly a statement about what "
        f"is easy to probe.** A module showing 100% on a single probe is "
        f"weakly evidenced, not finished — check its `passed/probes` column."
    )

    out.append("\n### Modules with no DoD denominator\n")
    covered = set(s["dod"])
    for pkg in sorted(s["reach"]):
        if pkg in covered:
            continue
        r = s["reach"][pkg]
        out.append(
            f"- `{pkg}` — no §P1.16 bullet names it. Reachable files "
            f"{r['reached']}/{r['total']}. **No percentage is honest here.**"
        )

    out.append("\n### Unreachable modules (built, not callable)\n")
    orphans = [o for r in s["reach"].values() for o in r["orphans"]]
    if orphans:
        for o in sorted(orphans):
            out.append(f"- `{o}`")
    else:
        out.append("- none — every module is reachable from an entry point.")

    out.append("\n### Failing probes\n")
    fails = [i for i in ITEMS if i.kind == PROBE and not i.result]
    if not fails:
        out.append("- none")
    for i in fails:
        out.append(f"- **{i.id}** (`{i.module}`) {i.text} — {i.detail}")
    return "\n".join(out) + "\n"


def test_trajectory(ref: str = "origin/main", since: str = "2026-09-09 00:00") -> str:
    """Test-count trajectory, cheap enough to re-run: counts `def test_` per
    commit via `git grep`, with no checkouts.

    Two different numbers get called "the test count" and mixing them is how
    "560 → 1111" ends up describing one window:
      * DEFINITIONS — what this function counts. Stable, cheap, comparable
        across commits.
      * COLLECTED — what `pytest --collect-only -q` reports. Larger, because
        `@parametrize` expands one definition into many cases. This is the
        number pytest prints and therefore the one people quote.
    Only definitions are computed here. For collected counts at an old
    commit you need a worktree at that commit AND an editable install
    matching it — commits before the `rite` → `rite_ai` package rename
    cannot be collected against a venv built for the other name.
    """
    revs = subprocess.run(
        [
            "git",
            "log",
            ref,
            f"--since={since}",
            "--format=%H%x01%ad%x01%s",
            "--date=format:%m-%d %H:%M",
            "--reverse",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    rows, prev = [], None
    for line in revs:
        sha, date, subject = line.split("\x01")
        # `[[:space:]]` not `\s`: git's regex engine does not honour the
        # Perl shorthand, and silently matched only unindented defs — 279
        # where the real count is 964, a 71% undercount that looked
        # entirely plausible. POSIX classes are what git actually supports.
        counted = subprocess.run(
            [
                "git",
                "grep",
                "-c",
                "-E",
                r"^[[:space:]]*(async )?def test_",
                sha,
                "--",
                "tests/",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout
        n = sum(
            int(line.rsplit(":", 1)[-1])
            for line in counted.splitlines()
            if line.rsplit(":", 1)[-1].isdigit()
        )
        if n != prev:
            rows.append(f"| `{sha[:7]}` | {date} | {n} | {subject[:66]} |")
            prev = n
    head = ["| commit | when | test defs | subject |", "|---|---|---:|---|"]
    return "\n".join(
        ["### Test-count trajectory (definitions, not collected cases)", ""]
        + head
        + rows
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reach-only", action="store_true")
    ap.add_argument("--tests", action="store_true", help="test-count trajectory only")
    args = ap.parse_args()

    if args.tests:
        print(test_trajectory())
        return 0

    if not args.reach_only:
        run_probes()
    s = summarise()
    if args.json:
        print(
            json.dumps(
                {
                    "items": [
                        {
                            "id": i.id,
                            "module": i.module,
                            "kind": i.kind,
                            "text": i.text,
                            "result": i.result,
                            "detail": i.detail,
                        }
                        for i in ITEMS
                    ],
                    "reach": {
                        k: {kk: vv for kk, vv in v.items()}
                        for k, v in s["reach"].items()
                    },
                },
                indent=2,
            )
        )
    else:
        print(render_markdown(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
