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

**Answering (S30, v0.7.0a4).** The paragraph above said "answering stays a
person's job for now", and the reason given elsewhere was that "yoloAI 0.11.0
has no way to send input to a running agent". ⚠ **That was wrong about this
protocol, and `deliver_answer` is the correction.** yoloAI's own injected
`CLAUDE.md` tells the agent to write `question.json` and then to POLL
`answer.json` — so the return path is a file the Worker is already watching,
reached through the same `yoloai files <name> path` this module already uses
to read the question. Writing it is not typing into a live session and not
reaching into yoloAI's private layout; it is the other half of the exchange
yoloAI documents.

⚠ **Nothing here weakens a Worker's sandbox.** No permission changes, no
flag, no new grant: the answer is a file in the exchange directory yoloAI
created for exactly this, written by the host, read by the agent because its
own runtime instructions tell it to. See `deliver_answer` for what it
refuses to do and what it cannot tell.
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


@dataclass(frozen=True)
class Delivered:
    """An answer written where the Worker polls for it."""

    sandbox: str
    path: Path


@dataclass(frozen=True)
class Undeliverable:
    """An answer that was NOT delivered, and why.

    ⚠ **Never a silent drop.** S30's whole point: an answer that cannot reach
    the Worker must come back to the person who wrote it, because they are
    now waiting on something that will never arrive. Every caller reports
    this; none may treat it as "nothing to do".
    """

    sandbox: str
    reason: str
    stopped: bool = False
    """True when the Worker is gone rather than merely unreachable — a
    different sentence for the person: nothing will make this land, so the
    answer needs somewhere else to go."""


def deliver_answer(
    sandbox: str,
    text: str,
    *,
    status=None,
    question: str = "",
) -> Delivered | Undeliverable:
    """Write `text` where the Worker in `sandbox` polls for its answer (S30).

    The return path yoloAI documents: its injected `CLAUDE.md` tells the
    agent to write `question.json` and poll `answer.json`, and this writes
    the second in the directory `yoloai files <name> path` names. No session
    is typed into and no sandbox rule is touched.

    `question`, when given, is the id the Worker names back when it
    acknowledges reading this (`rite ack`, SCRUM-61). Omitted, the answer is
    delivered exactly as before and nothing asks for an ack — so an older
    caller keeps working and simply gets no ✅.

    `status` is the `SandboxStatus` the caller already has, passed in rather
    than fetched here so this function shells out for one thing only (the
    path) and a test can state liveness without a yoloAI. Omitted means the
    caller has not established it, which is NOT read as alive: see below.

    **It refuses, rather than writing, when:**

    - the sandbox is gone (`files_dir` -> None) — the Worker has stopped;
    - yoloAI could not be asked — `Unknown` is not "no sandbox" (§ the
      module docstring), so it is reported, never guessed past;
    - `status` says `not found`, or is not `known`, or was not given;
    - nothing is waiting: no `question.json`, or one already answered. An
      answer to a question nobody asked would sit in the exchange directory
      and be read against the NEXT question the Worker asks, which is worse
      than not delivering it.

    ⚠ **What it cannot tell, said rather than papered over.** A sandbox whose
    agent has exited while the sandbox itself lingers still has its exchange
    directory and may still have a pending `question.json`. This writes the
    answer and reports `Delivered`; nothing here can observe that the agent
    is no longer polling. `status` is what narrows that window, and it is the
    caller's to obtain — which is why a missing one is refused rather than
    assumed. The honest claim is "written where it is polled for", not "read".
    """
    from rite_ai.state import write_atomic

    if status is None:
        return Undeliverable(
            sandbox,
            "rite did not establish whether the Worker is still running, and "
            "an answer is not written on an assumption",
        )
    if not getattr(status, "known", False):
        return Undeliverable(
            sandbox, f"rite could not ask yoloAI about the sandbox: {status}"
        )
    if str(status) == "not found":
        return Undeliverable(
            sandbox,
            "the Worker's sandbox is gone, so it stopped before this answer arrived",
            stopped=True,
        )

    where = files_dir(sandbox)
    if where is None:
        return Undeliverable(
            sandbox,
            "there is no such sandbox, so the Worker has stopped",
            stopped=True,
        )
    if isinstance(where, Unknown):
        return Undeliverable(sandbox, where.reason)

    asked = question_in(where, sandbox)
    if isinstance(asked, Unknown):
        return Undeliverable(sandbox, asked.reason)
    if asked is None:
        return Undeliverable(
            sandbox,
            "nothing is waiting on an answer in that sandbox — the question "
            "was already answered, or withdrawn",
        )

    path = where / ANSWER_FILE
    payload = {"answer": text, "answered_at": time.time()}
    if question:
        # ⚠ **The id travels WITH the answer, because the Worker has to name
        # it back (SCRUM-61).** rite now asks a Worker to acknowledge that it
        # read an answer, and an ack has to say WHICH answer — one Worker is
        # answered more than once, and an ack that named nothing would tick
        # whichever question happened to be open. The Worker cannot look the
        # id up: it holds no ledger and no board credential. So it is here,
        # in the file the Worker is already reading, under a key beside the
        # text rather than inside it, so no parsing of prose is involved.
        payload["question"] = question
        payload["acknowledge_with"] = (
            f"rite ack --worker <your name> --question {question}"
        )
    try:
        write_atomic(path, json.dumps(payload, indent=1) + "\n")
    except OSError as e:
        return Undeliverable(sandbox, f"{path} could not be written: {e}")

    # ⚠ Verified, not assumed. `question_in`'s own rule is that an answer
    # counts only when its mtime is at or after the question's; a clock or a
    # filesystem that does not honour that would leave the Worker waiting on
    # an answer rite had reported as delivered.
    if question_in(where, sandbox) is not None:
        return Undeliverable(
            sandbox,
            f"{path} was written but the question still reads as unanswered, "
            "so the Worker would not see it",
        )
    return Delivered(sandbox, path)
