"""Read a live run's state from OUTSIDE: files and rite's own read functions only.

Where rite already has the function that answers "where does X live" or "what does
this record say" (`manager_dir`, `decomposition.read`), the observer asks the
INSTALLED rite — the fixed build under test — through its own interpreter, rather
than re-spelling the path here. SCRUM-72 moves plan state out of the project
(plan 3.3b); a re-spelled path would go on reading the old place and report nothing.

Everything else is a plain file rite documents: `.rite/events.jsonl`,
`.rite/claims.json`, `.rite/recovery.json`, a Manager's `worker-questions.json`
and its `journal/`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def installed_rite_python(path_env: str | None = None) -> str:
    """The interpreter the `rite` found on `path_env` runs under.

    Read from its shebang; a uv virtualenv's launcher is a `#!/bin/sh` shim, so
    for anything that is not a Python the `python3` beside the resolved script is
    used instead."""
    found = shutil.which("rite", path=path_env)
    if not found:
        raise RuntimeError("no `rite` on PATH")
    script = Path(found).resolve()
    first = script.read_text(errors="replace").splitlines()[0]
    interp = first[2:].strip().split()[0] if first.startswith("#!") else ""
    if "python" in Path(interp).name:
        return interp
    beside = script.parent / "python3"
    if beside.exists():
        return str(beside)
    raise RuntimeError(f"cannot find the interpreter {found} runs under")


@dataclass
class Observer:
    project: Path
    env: dict
    python: str = ""

    # ---- rite's own answers, through the installed build ----

    def _ask_rite(self, snippet: str) -> object:
        """Run `snippet` (which must print one JSON value) under the installed rite."""
        done = subprocess.run(
            [self.python or installed_rite_python(self.env.get("PATH")), "-c", snippet],
            cwd=self.project,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if done.returncode != 0:
            raise RuntimeError(f"rite read failed: {done.stderr.strip()[-400:]}")
        return json.loads(done.stdout)

    def manager_dir(self, manager: str) -> Path:
        return Path(
            self._ask_rite(
                "import json; from pathlib import Path; "
                "from rite_ai.managers import manager_dir; "
                f"print(json.dumps(str(manager_dir(Path('.').resolve(), {manager!r}))))"
            )
        )

    def supervisor_state(self, manager: str) -> str:
        """running | ended | died | "" — rite's own reading of supervisor.json."""
        return str(
            self._ask_rite(
                "import json; from pathlib import Path; "
                "from rite_ai.managers.routing import supervisor_state; "
                f"print(json.dumps(supervisor_state(Path('.').resolve(), {manager!r})))"
            )
        )

    def decomposition(self, ticket: str) -> dict | None:
        """The ticket's plan as rite renders it, or None.

        🔴 **SCRUM-72c moved plan state out of the project** — it is now under
        `plan_state.home(root)`, beside the mailboxes, outside every Manager's
        grant — and this read goes through `plan_state.layer`, which is the one
        spelling every local-tier consumer binds to. It used to construct
        `LocalStateLayer(<project>/.rite)` by hand: against the fixed build that
        reads a place nothing writes any more and finds no plan, which `checks`
        reports as a FAIL. Re-spelling the path here is exactly what that
        docstring warned would rot, so it is not re-spelled.
        """
        return self._ask_rite(
            "import json; from pathlib import Path; "
            "from rite_ai.local import decomposition as d, plan_state; "
            "r = d.read(plan_state.layer(Path('.').resolve()), "
            f"{ticket!r}); "
            "print(json.dumps(None if r.plan is None else "
            "json.loads(d.render(r.plan))))"
        )

    # ---- documented files ----

    def _json(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def events(self) -> list[dict]:
        p = self.project / ".rite" / "events.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def claims(self) -> list[dict]:
        data = self._json(self.project / ".rite" / "claims.json", [])
        return data if isinstance(data, list) else []

    def recovery(self) -> dict:
        data = self._json(self.project / ".rite" / "recovery.json", {})
        return data if isinstance(data, dict) else {}

    def worker_questions(self, owner_dir: Path) -> dict:
        data = self._json(owner_dir / "worker-questions.json", {})
        return data if isinstance(data, dict) else {}

    def journal(self, manager_dir: Path) -> list[dict]:
        """Every journal entry: name, mtime, text."""
        d = manager_dir / "journal"
        if not d.is_dir():
            return []
        return [
            {
                "name": p.name,
                "mtime": p.stat().st_mtime,
                "text": p.read_text(errors="replace"),
            }
            for p in sorted(d.glob("*.md"))
        ]

    def start_requests(self, manager_dir: Path) -> list[dict]:
        """Worker start requests the Manager filed (`requests/`), read or not."""
        d = manager_dir / "requests"
        out = []
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            body = self._json(p, None)
            if isinstance(body, dict):
                out.append(body)
        return out


def delivered_any(events: list[dict], ticket: str) -> list[dict]:
    """Every `delivered` event for `ticket`, PR or not.

    ⚠ `delivered_prs` requires "; PR " in the outcome text, which only
    `publish.strategy: pull_request` produces. Under `commit` the work is
    delivered as a branch into the project's checkout and there is no PR, so a
    run judged by `delivered_prs` would wait for ever for something that is
    never written. The happy-path smoke uses this.
    """
    return [
        e
        for e in events
        if e.get("event") == "delivered" and str(e.get("ticket")) == str(ticket)
    ]


def delivered_prs(events: list[dict], ticket: str) -> list[dict]:
    """[{worker, pr_url}] for each `delivered` event whose outcome says a PR exists.

    The PR URL is only in the outcome text (`deliver.py`: "...; pushed; PR <url>")."""
    found = []
    for e in events:
        if e.get("event") != "delivered" or str(e.get("ticket")) != str(ticket):
            continue
        for note in e.get("outcomes") or []:
            if (
                isinstance(note, str)
                and note.startswith("Delivered ")
                and "; PR " in note
            ):
                url = note.split("; PR ", 1)[1].split()[0].rstrip(".")
                found.append(
                    {"worker": e.get("worker"), "pr_url": url, "at": e.get("at")}
                )
    return found
