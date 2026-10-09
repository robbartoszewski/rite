"""One run's directory, and the one door every command the harness runs goes through.

Every command is appended to `commands.jsonl` before it runs. The gate's "no host
command run by a person" check (`checks.no_forbidden_host_commands`) audits that
log: it proves the HARNESS ran none of the commands a person would have had to run
to rescue the fleet. It cannot see a shell the harness did not open, and says so.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunDir:
    path: Path

    @property
    def project(self) -> Path:
        """The throwaway app's checkout, which is also the rite project root."""
        return self.path / "app"

    @property
    def rite_data(self) -> Path:
        """Where this run's mail, publish records and refinement key live
        (RITE_MAIL_DIR is `<rite_data>/mail`), so nothing is shared with another
        project's Managers."""
        return self.path / "rite-data"

    @property
    def rite_home(self) -> Path:
        return self.path / "rite-home"

    def file(self, name: str) -> Path:
        return self.path / name

    def env(self, base: dict | None = None) -> dict:
        """The environment every rite command of this run gets. Isolation only:
        credentials stay the operator's (`RITE_CREDENTIALS_FILE` is not moved), and
        PATH is set by the caller (preflight puts the patched yoloAI first)."""
        env = dict(os.environ if base is None else base)
        env["RITE_MAIL_DIR"] = str(self.rite_data / "mail")
        env["RITE_HOME_DIR"] = str(self.rite_home)
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def append(self, name: str, record: dict) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        with self.file(name).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), **record}, sort_keys=True) + "\n")

    def read(self, name: str) -> list[dict]:
        p = self.file(name)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def sh(
    run: RunDir,
    argv: list[str],
    *,
    phase: str,
    cwd: Path | None = None,
    env: dict | None = None,
    check: bool = False,
    timeout: float | None = 600,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    """Run argv, logging it first. `phase` is setup|run|teardown|preflight."""
    run.append("commands.jsonl", {"phase": phase, "argv": argv, "cwd": str(cwd or "")})
    done = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    run.append(
        "commands.jsonl",
        {"phase": phase, "result": done.returncode, "argv0": argv[:3]},
    )
    if check and done.returncode != 0:
        raise RuntimeError(
            f"{' '.join(argv)} exited {done.returncode}:\n{done.stdout}\n{done.stderr}"
        )
    return done
