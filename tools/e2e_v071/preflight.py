"""Is this machine ready for the gate? Every item is probed, none assumed.

`run` refuses to start unless every item is OK: the gate is not run as a partial
fleet against half-built fixes. The items:
- the FIXED build: each probe in fleet.yaml `fixed_build.probes` answers;
- the DF16-patched yoloAI is first on PATH (version marker), and, with
  `--probe-df16`, the WRITEUP #35 escape probe is refused from inside a sandbox;
- Ollama serves the GPU model; `claude`, `gh`, `git`, `tmux` are present.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tools.e2e_v071.config import Fleet


@dataclass(frozen=True)
class Item:
    name: str
    ok: bool
    detail: str


def patched_path(fleet: Fleet, env: dict) -> dict:
    """env with the patched yoloAI directory first on PATH, and WITHOUT the active
    virtualenv: under `uv run` the rite checkout's own dev `rite` would otherwise
    shadow the installed build, and the gate must test the installed fixed build."""
    d = str(Path(os.path.expanduser(fleet.yoloai["patched_bin_dir"])))
    venv = env.get("VIRTUAL_ENV")
    parts = [
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if p and not (venv and Path(p) == Path(venv) / "bin")
    ]
    out = {k: v for k, v in env.items() if k != "VIRTUAL_ENV"}
    out["PATH"] = os.pathsep.join([d, *parts])
    return out


def _run(
    argv: list[str], env: dict, timeout: float = 60
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))


def fixed_build_items(fleet: Fleet, env: dict, rite_python: str) -> list[Item]:
    items = []
    v = _run(["rite", "--version"], env)
    items.append(
        Item("rite on PATH", v.returncode == 0, (v.stdout or v.stderr).strip()[:120])
    )
    want = fleet.fixed_build.get("min_version") or ""
    if not want:
        items.append(
            Item(
                "fixed build version pinned",
                False,
                "fleet.yaml fixed_build.min_version is empty: set it to the RC "
                "that carries the merged fixes",
            )
        )
    else:
        items.append(
            Item(
                "fixed build version",
                want in v.stdout,
                f"want {want}, have {v.stdout.strip()}",
            )
        )
    for probe in fleet.fixed_build.get("probes", []):
        fix = probe["fix"]
        if "argv" in probe:
            r = _run(["rite", *probe["argv"]], env)
            ok = r.returncode == 0 and probe["expect"] in (r.stdout + r.stderr)
            items.append(
                Item(
                    f"{fix}: rite {' '.join(probe['argv'])}",
                    ok,
                    "present" if ok else (r.stderr or r.stdout).strip()[:160],
                )
            )
        else:
            r = _run([rite_python, "-c", probe["python"]], env)
            items.append(
                Item(
                    f"{fix}: {probe['python']}",
                    r.returncode == 0,
                    "present" if r.returncode == 0 else r.stderr.strip()[-160:],
                )
            )
    return items


def yoloai_items(fleet: Fleet, env: dict) -> list[Item]:
    found = shutil.which("yoloai", path=env.get("PATH"))
    want_dir = str(Path(os.path.expanduser(fleet.yoloai["patched_bin_dir"])).resolve())
    first = bool(found) and str(Path(found).resolve().parent) == want_dir
    v = _run(["yoloai", "version"], env)
    marker = fleet.yoloai["patched_version_marker"]
    return [
        Item(
            "patched yoloAI first on PATH",
            first,
            f"{found or 'none'} (want {want_dir})",
        ),
        Item(
            "patched yoloAI version",
            marker in v.stdout,
            v.stdout.strip()[:120] or v.stderr.strip()[:120],
        ),
    ]


def df16_probe(env: dict) -> Item:
    """WRITEUP #35: from inside a throwaway sandbox, a host tmux server's socket must
    not be reachable. Control: the same probe on the HOST must succeed, or the probe
    proves nothing. Creates and destroys one sandbox named `e2e-df16-probe-<ts>`."""
    name = f"e2e-df16-probe-{int(time.time())}"
    work = Path(tempfile.mkdtemp(prefix="e2e-df16-"))
    sock_dir = Path(
        tempfile.mkdtemp(prefix="e2e-df16-sock-", dir=os.path.expanduser("~"))
    )
    sock = sock_dir / "s"
    try:
        (work / "README").write_text("df16 probe\n")
        _run(["git", "init", "-q", str(work)], env)
        _run(["git", "-C", str(work), "add", "."], env)
        _run(
            [
                "git",
                "-C",
                str(work),
                "-c",
                "user.email=e2e@local",
                "-c",
                "user.name=e2e",
                "commit",
                "-qm",
                "probe",
            ],
            env,
        )
        _run(["tmux", "-S", str(sock), "new-session", "-d", "-s", "df16"], env)
        control = _run(["tmux", "-S", str(sock), "ls"], env)
        if control.returncode != 0:
            return Item(
                "DF16 probe",
                False,
                "control failed: host tmux socket unusable "
                f"({control.stderr.strip()[:120]})",
            )
        made = _run(
            [
                "yoloai",
                "new",
                "--backend",
                "seatbelt",
                "--agent",
                "idle",
                name,
                str(work),
            ],
            env,
            timeout=300,
        )
        if made.returncode != 0:
            return Item(
                "DF16 probe",
                False,
                f"could not create the probe sandbox: {made.stderr.strip()[:160]}",
            )
        inside = _run(
            ["yoloai", "exec", name, "tmux", "-S", str(sock), "ls"], env, timeout=120
        )
        refused = inside.returncode != 0
        return Item(
            "DF16 probe (host tmux socket refused from a sandbox)",
            refused,
            "refused" if refused else f"ESCAPE OPEN: {inside.stdout.strip()[:120]}",
        )
    finally:
        _run(["yoloai", "destroy", "--abandon-unapplied", name], env, timeout=300)
        _run(["tmux", "-S", str(sock), "kill-server"], env)
        shutil.rmtree(sock_dir, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def platform_items(fleet: Fleet, env: dict) -> list[Item]:
    items = []
    for tool in ("claude", "gh", "git", "tmux", "goose"):
        items.append(
            Item(
                f"{tool} on PATH",
                bool(shutil.which(tool, path=env.get("PATH"))),
                shutil.which(tool, path=env.get("PATH")) or "missing",
            )
        )
    gh = _run(["gh", "auth", "status"], env)
    items.append(
        Item(
            "gh authenticated",
            gh.returncode == 0,
            (gh.stdout + gh.stderr).strip().splitlines()[0:1].__str__(),
        )
    )
    models = {
        w.get("model") for w in (*fleet.workers, *fleet.managers) if w.get("model")
    }
    endpoint = next(
        (w["endpoint"] for w in fleet.workers if w.get("endpoint")),
        "http://localhost:11434",
    )
    try:
        with urllib.request.urlopen(
            endpoint.rstrip("/") + "/api/tags", timeout=10
        ) as r:
            have = {m["name"] for m in json.load(r).get("models", [])}
        for m in sorted(models):
            items.append(Item(f"Ollama serves {m}", m in have, endpoint))
    except OSError as e:
        items.append(Item("Ollama reachable", False, f"{endpoint}: {e}"))
    return items


def run_all(
    fleet: Fleet, env: dict, rite_python: str, *, probe_df16: bool
) -> list[Item]:
    env = patched_path(fleet, env)
    items = [
        *fixed_build_items(fleet, env, rite_python),
        *yoloai_items(fleet, env),
        *platform_items(fleet, env),
    ]
    if probe_df16:
        items.append(df16_probe(env))
    else:
        items.append(
            Item(
                "DF16 probe",
                False,
                "not run: pass --probe-df16 "
                "(creates and destroys one throwaway sandbox)",
            )
        )
    return items
