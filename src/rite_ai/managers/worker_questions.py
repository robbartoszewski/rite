"""Tell the person when a Worker is waiting on a question (dogfood Q1–Q4, part B).

**What happened.** In the v0.6.0 dogfood a Worker asked the right three
questions about a one-line ticket and waited eight hours; nobody was told.
Part A (`rite_ai.sandbox.questions`) makes every view say so. This makes rite
SAY so, without anyone having to look.

**How, and why this way.** The board-holding Owner's supervisor, which runs
outside every boundary, looks at each of the project's Worker sandboxes. A
question it has not told anyone about becomes two messages, both written by
rite and neither by a model:

- one in the Owner's OUTBOX, of kind `question` (RP1, `mailbox.QUESTION`),
  which the Slack relay posts to the Owner's DM as "needs your answer", and
  which `rite replies` / `rite connect` show on this machine;
- one in the Owner's INBOX, marked as rite's own note and as context, so
  the Owner knows (and a Manager waiting with nothing to do wakes: F22).

⚠ **Not relayed by the Owner's model, on purpose.** The dogfood Owner told the
person "nothing needed from you right now" while its Worker was blocked; a
question that reaches the person only if a model decides to pass it on is
the failure this closes. The outbox message goes through
`asking.raise_to_person`, the one function ticket refinement (TR2) uses for
the Owner's own questions (agreed through the coordinator, 2026-09-28): rite
writes the first line, with the ticket and the question's id, and an answer
is matched by the Slack thread that line labels, not by a model.

**What it does not do.** It does not deliver an answer back: yoloAI 0.11.0
has no way to send input to a running agent, and typing into a Worker's
session, which runs with permissions bypassed, needs its own reasoning. For
now the person answers by attaching, and the message says how. A Slack reply
reaches the Owner Manager, which cannot reach the Worker; the message says
that too, so nobody answers into a void.

**Told once per question,** by `asking`'s ledger (keyed by subject, raiser
and text). A Worker that rewrites its question (the dogfood's did, twice)
asks a new one, and the old one is settled; so is a question once it is
answered or its sandbox is gone.
"""

from __future__ import annotations

import json
from pathlib import Path

LEDGER_FILE = "worker-questions.json"
"""sandbox -> the id (`asking._question_id`) of the question raised for it,
so it can be settled when the Worker's question is answered or gone. The
once-only rule itself is `asking`'s ledger, shared with refinement."""


def _ledger_path(root: Path, manager: str) -> Path:
    from rite_ai.managers import manager_dir

    return manager_dir(root, manager) / LEDGER_FILE


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(path: Path, data: dict) -> None:
    from rite_ai.state import write_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=1, sort_keys=True) + "\n")


def _ticket_of(root: Path, sandbox: str) -> str:
    """The ticket the sandbox was last started on, from rite's own event
    log (`sandbox-started`), or "" when it was not recorded."""
    from rite_ai.reporting.events import events_path

    ticket = ""
    try:
        lines = events_path(root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("event") == "sandbox-started" and entry.get("sandbox") == sandbox:
            ticket = str(entry.get("ticket") or "")
    return ticket


def _for_the_person(worker: str, sandbox: str, question) -> str:
    """The question's body. `asking.raise_to_person` writes the first line,
    which carries the ticket and the question's id."""
    context = f"\n_{question.context}_" if question.context else ""
    return (
        "\n".join(f"> {line}" for line in question.question.splitlines())
        + context
        + f"\nAsked at {question.since()}; the Worker cannot continue until "
        f"someone answers. Read it in full: `rite sandbox status {worker}`. "
        f"For now the answer has to be typed into its session: `yoloai attach "
        f"{sandbox}`. A reply in this thread reaches the Owner Manager, which "
        "cannot pass it on to the Worker."
    )


def _for_the_manager(worker: str, sandbox: str, ticket: str, question) -> str:
    from rite_ai.managers.telling import header

    about = f" on {ticket}" if ticket else ""
    return (
        header(f"Worker {worker!r} · WAITING ON A QUESTION")
        + "\n"
        + f"Worker {worker!r}{about} asked at {question.since()} and is blocked "
        "until a person answers. rite has sent the question to the User as one "
        "that needs an answer. You cannot answer it from inside your boundary: "
        "do not guess an answer for it, and do not start another Worker on the "
        f"same ticket. `rite sandbox status {worker}` shows it. The question:\n"
        + "\n".join(f"> {line}" for line in question.question.splitlines())
    )


def surface(root: Path, manager: str, say) -> int:
    """Tell the person, once, about each Worker question not yet told.
    Returns how many were told. Never raises into the supervisor: a failure
    is said, and the next look tries again. A sandbox that could not be
    asked is said once per reason, not every tick."""
    try:
        return _surface(root, manager, say)
    except Exception as e:  # noqa: BLE001 - a watcher must not end the run
        _say_once(root, manager, say, f"could not check Workers for questions: {e}")
        return 0


def _say_once(root: Path, manager: str, say, line: str) -> None:
    """Say `line` unless it is what was said last time."""
    path = _ledger_path(root, manager)
    told = _load(path)
    if told.get("_last_problem") == line:
        return
    say(line)
    told["_last_problem"] = line
    try:
        from rite_ai.state import write_atomic

        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(told, indent=1, sort_keys=True) + "\n")
    except OSError:
        pass


def _surface(root: Path, manager: str, say) -> int:
    from rite_ai.config.parse import load_project
    from rite_ai.managers.asking import raise_to_person, settle
    from rite_ai.managers.mailbox import INBOX, send
    from rite_ai.sandbox import existing_sandbox_name
    from rite_ai.sandbox.questions import Unknown, WorkerQuestion, pending_question

    root = Path(root)
    project = load_project(root)
    if isinstance(project, list) or not project.config.sandbox.enabled:
        return 0
    path = _ledger_path(root, manager)
    raised = _load(path)
    count = 0
    for worker in project.workers:
        sandbox = existing_sandbox_name(worker.name, root)
        asked = pending_question(sandbox)
        if isinstance(asked, Unknown):
            _say_once(
                root,
                manager,
                say,
                f"could not check Worker {worker.name!r} for a question: "
                f"{asked.reason}",
            )
            continue
        if not isinstance(asked, WorkerQuestion):
            # Answered, or gone: the person is no longer being asked it.
            earlier = raised.pop(sandbox, None)
            if isinstance(earlier, str):
                settle(root, manager, earlier)
                _store(path, raised)
            continue
        ticket = _ticket_of(root, sandbox)
        result = raise_to_person(
            root,
            manager,
            subject=ticket,
            raiser=f"worker:{worker.name}",
            text=_for_the_person(worker.name, sandbox, asked),
        )
        earlier = raised.get(sandbox)
        if isinstance(earlier, str) and earlier != result.id:
            # A rewritten question replaces the one it rewrote.
            settle(root, manager, earlier)
        if earlier != result.id:
            raised[sandbox] = result.id
            _store(path, raised)
        if not result.new:
            continue
        send(
            root, manager, INBOX, _for_the_manager(worker.name, sandbox, ticket, asked)
        )
        say(
            f"Worker {worker.name!r} is waiting on a question (asked "
            f"{asked.since()}, {result.id}): told the User, and {manager!r}"
        )
        count += 1
    return count
