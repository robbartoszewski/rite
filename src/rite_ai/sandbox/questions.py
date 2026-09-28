"""A sandboxed Worker's unanswered question, read from the host (dogfood Q1–Q4).

**What happened.** In the v0.6.0 dogfood a Worker (alpha, ticket KAN-7) read
its one-line ticket, named the three things it would have to invent, refused
to guess, and asked. It asked the way yoloAI tells it to: yoloAI 0.11.0
injects its own `CLAUDE.md` into the agent (`rw/agent-runtime/CLAUDE.md`),
which says to write the question to `files/question.json`, poll
`files/answer.json`, and that "the question will be seen and answered by an
external agent or user". Nothing in rite read that file. Every view pointed
away from it: `rite status` said "not started", `rite sandbox status` said
"idle", `rite loop run` said "busy — a sandbox is running", `rite doctor`
listed idle sandboxes with "`yoloai destroy` frees it", and `rite sandbox
destroy`, which refuses only on unapplied code, would have deleted the
question with the sandbox. The Worker sat for eight hours with three
questions and no answer.

**What this module does, and only this:** it tells whether a Worker's
sandbox holds a question nobody has answered. It reads the file where yoloAI
keeps it, found through yoloAI's own `files <name> path`, so rite does not
re-spell yoloAI's layout. Telling a person is `raise_question`'s job
elsewhere; answering stays a person's job for now (attach, or `rite sandbox
pane`).

**Pending means:** `question.json` exists and there is no `answer.json`
written at or after it. A later question overwrites the file (alpha
rewrote it twice), so the newest question is the one reported.

**"Could not check" is not "no question".** As with `SandboxStatus`, a
yoloAI that cannot be asked is said as such. Every caller that would act on
"no question", `destroy` above all, must not act on "could not check".
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

QUESTION_FILE = "question.json"
ANSWER_FILE = "answer.json"


@dataclass(frozen=True)
class WorkerQuestion:
    """The newest unanswered question in one sandbox."""

    sandbox: str
    question: str
    context: str
    raised_at: float
    """The question file's mtime: when the Worker last wrote it."""
    path: Path

    def since(self) -> str:
        """`HH:MM` in local time, for a status line."""
        return time.strftime("%H:%M", time.localtime(self.raised_at))

    def headline(self, width: int = 160) -> str:
        """The question on one line, shortened to `width`."""
        text = " ".join(self.question.split())
        return text if len(text) <= width else text[: width - 1] + "…"


@dataclass(frozen=True)
class Unknown:
    """The sandbox could not be asked. NOT the same as no question."""

    sandbox: str
    reason: str


def files_dir(sandbox: str) -> Path | Unknown | None:
    """Where yoloAI keeps `sandbox`'s exchange files, from yoloAI itself.

    None: there is no such sandbox, so there can be no question.
    """
    from rite_ai.sandbox import _yoloai_binary

    binary = _yoloai_binary()
    if binary is None:
        return Unknown(sandbox, "yoloai not found")
    try:
        proc = subprocess.run(
            [binary, "files", sandbox, "path"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Unknown(sandbox, f"`yoloai files {sandbox} path` failed: {e}")
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout or "").strip()
        # Measured on 0.11.0: an unknown name exits 1 with "sandbox not found".
        if "not found" in said:
            return None
        return Unknown(
            sandbox,
            f"`yoloai files {sandbox} path` exited {proc.returncode}: {said[:200]}",
        )
    where = proc.stdout.strip()
    if not where:
        return Unknown(sandbox, f"`yoloai files {sandbox} path` printed nothing")
    return Path(where)


def question_in(where: Path, sandbox: str = "") -> WorkerQuestion | Unknown | None:
    """The pending question in exchange directory `where`, or None."""
    question_path = where / QUESTION_FILE
    try:
        asked = question_path.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError as e:
        return Unknown(sandbox, f"{question_path} could not be read: {e}")
    try:
        answered = (where / ANSWER_FILE).stat().st_mtime
    except FileNotFoundError:
        answered = None
    except OSError as e:
        return Unknown(sandbox, f"{where / ANSWER_FILE} could not be read: {e}")
    if answered is not None and answered >= asked:
        return None
    try:
        raw = question_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return Unknown(sandbox, f"{question_path} could not be read: {e}")
    try:
        data = json.loads(raw)
    except ValueError:
        # A question that is not the JSON yoloAI asks for is still a
        # question: reported as text rather than dropped.
        data = {"question": raw}
    if not isinstance(data, dict):
        data = {"question": raw}
    return WorkerQuestion(
        sandbox=sandbox,
        question=str(data.get("question") or raw).strip(),
        context=str(data.get("context") or "").strip(),
        raised_at=asked,
        path=question_path,
    )


def pending_question(sandbox: str) -> WorkerQuestion | Unknown | None:
    """The unanswered question in `sandbox`, None if there is none, or
    `Unknown` if that could not be established."""
    where = files_dir(sandbox)
    if where is None or isinstance(where, Unknown):
        return where
    return question_in(where, sandbox)


def worker_question(
    worker: str, root: str | os.PathLike[str] | None = None
) -> WorkerQuestion | Unknown | None:
    """`pending_question` for the sandbox `worker` is actually running under."""
    from rite_ai.sandbox import existing_sandbox_name

    return pending_question(existing_sandbox_name(worker, root))
