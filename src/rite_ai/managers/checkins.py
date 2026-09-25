"""Questions deferred to a check-in, and the rule that makes deferring safe.

**What it is for** (plan § K, `docs/design/V060_CHECKINS.md`). A User wants
most of their interaction with the AI in a few windows a day. So a question
that does not block the Manager can wait for the next check-in, instead of
interrupting them now.

⚠ **THE FAILURE IS ASYMMETRIC, AND EVERYTHING HERE LEANS ONE WAY.** Deferring
a question that blocks costs a whole window of idle work, up to five hours
between 9–10 and 14–15. Asking one that could have waited costs the User
thirty seconds. So asking now is the default and deferring is a separate,
explicit act. Every case where rite cannot tell whether a deferral is safe
ends with the question being asked:

* a deferral that cannot name what the Manager will do meanwhile is refused.
  A Manager with nothing else to do is blocked, by that fact;
* no check-in windows configured, or none that parse: asked at once, and
  the reason is said;
* the loop's verdict goes idle while questions are queued: at least one was
  misjudged, so the queue is asked at once, and that is said too.

**Where the state lives: under the Manager's own directory**, NOT in
`.rite/user/*.json`. That directory is keyed by Manager name and has twice
held a file that was not an instance record (C11's designation, C4's
permission settings). A third kind of file there would be the same defect
again.

**Asked means sent to the outbox**, through the validated writer `rite reply`
uses. So `rite replies`, `rite connect` and the Slack relay all carry it, and
nothing here knows which of them exist.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import manager_dir
from rite_ai.state import write_atomic

CHECKINS_DIRNAME = "checkins"
QUEUE_DIRNAME = "queue"
LEDGER_FILENAME = "ledger.jsonl"

RULE = (
    "ask now unless the question is clearly deferrable; if you are unsure "
    "whether it blocks you, it blocks you."
)
"""⚠ **The plan asks for these words, so the Manager is told them verbatim.**
The rest of the instruction explains the rule. This sentence is the rule, and
paraphrasing it is how it would soften."""

REFUSED_WITHOUT_WHILE = "then it blocks you — ask now"
"""What a deferral with no `--while` is told. If the Manager cannot name
anything to do meanwhile, it is blocked."""


def _checkins_dir(root: Path, manager: str) -> Path:
    """`.rite/managers/<manager>/checkins/` — the Manager's own directory."""
    return manager_dir(root, manager) / CHECKINS_DIRNAME


@dataclass(frozen=True)
class Question:
    """One deferred question, waiting for a check-in."""

    id: str
    text: str
    meanwhile: str
    queued_at: float
    path: Path


def _queue_dir(root: Path, manager: str) -> Path:
    return _checkins_dir(root, manager) / QUEUE_DIRNAME


def defer(root: Path, manager: str, text: str, meanwhile: str) -> Question:
    """Queue a question for the next check-in.

    The caller has already refused an empty `meanwhile` — this does not
    decide policy, it stores. The filename starts with milliseconds so a
    sorted listing is queue order, as in the mailbox.
    """
    queued_at = time.time()
    qid = "q" + secrets.token_hex(3)
    where = _queue_dir(root, manager)
    path = where / f"{int(queued_at * 1000)}_{qid}.json"
    write_atomic(
        path,
        json.dumps(
            {"id": qid, "text": text, "meanwhile": meanwhile, "queued_at": queued_at}
        )
        + "\n",
    )
    record(root, manager, {"event": "queued", "id": qid, "at": queued_at})
    return Question(qid, text, meanwhile, queued_at, path)


def queued(root: Path, manager: str) -> list[Question]:
    """Every question waiting, in the order it was deferred.

    ⚠ **A file that will not parse is still a question somebody deferred.**
    It is returned with its raw content as the text rather than skipped. A
    question skipped here would never be asked, and rite would never say so.
    """
    where = _queue_dir(root, manager)
    if not where.is_dir():
        return []
    out: list[Question] = []
    for path in sorted(where.glob("*.json")):
        try:
            raw = path.read_text()
        except OSError:
            continue
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not a mapping")
            out.append(
                Question(
                    id=str(data.get("id") or path.stem),
                    text=str(data.get("text") or "").strip() or raw.strip(),
                    meanwhile=str(data.get("meanwhile") or ""),
                    queued_at=float(data.get("queued_at") or 0.0),
                    path=path,
                )
            )
        except (ValueError, TypeError):
            out.append(Question(path.stem, raw.strip(), "", 0.0, path))
    return out


def record(root: Path, manager: str, event: dict) -> None:
    """Append one event to the Manager's check-in ledger.

    Kept so the filter's value is COUNTED rather than believed (plan § K3):
    how many questions were queued, withdrawn before asking, and asked.
    One JSON object per line, appended in one write.
    """
    path = _checkins_dir(root, manager) / LEDGER_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def _ledger(root: Path, manager: str) -> list[dict]:
    """Every recorded event, oldest first. Unreadable lines are skipped."""
    path = _checkins_dir(root, manager) / LEDGER_FILENAME
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _question_lines(questions: list[Question]) -> list[str]:
    """The questions as a person reads them, with what the Manager is doing
    meanwhile, so the User can tell how much an answer would unblock."""
    lines: list[str] = []
    for q in questions:
        first, *rest = q.text.splitlines() or [""]
        lines.append(f"- [{q.id}] {first}")
        lines.extend(f"  {line}" for line in rest)
        if q.meanwhile:
            lines.append(f"  (meanwhile: {q.meanwhile})")
    return lines


def ask_now(
    root: Path, manager: str, questions: list[Question], why: str
) -> Path | None:
    """Send these questions to the User NOW, as one message, with `why`.

    ⚠ **Sent BEFORE the queue files are removed.** A crash between the two
    asks a question twice, which is recoverable. The other order can lose
    one, which is the failure this module exists to prevent.
    """
    if not questions:
        return None
    from rite_ai.managers.mailbox import OUTBOX, send

    text = "\n".join([why, "", *_question_lines(questions)])
    path = send(root, manager, OUTBOX, text)
    now = time.time()
    for q in questions:
        record(root, manager, {"event": "asked", "id": q.id, "at": now, "why": why})
        try:
            q.path.unlink()
        except FileNotFoundError:
            pass
    return path


def idle_with_questions_queued(root: Path, manager: str) -> str:
    """The safety net: the loop went idle while questions were queued.

    A Manager with nothing left to do was, by that fact, waiting on
    something, so at least one deferral was wrong. Everything queued is
    asked now. Returns the line to say, or "" when nothing was queued.
    """
    waiting = queued(root, manager)
    if not waiting:
        return ""
    ask_now(
        root,
        manager,
        waiting,
        f"The Manager {manager!r} had deferred these to the next check-in, "
        "then ran out of work with them still waiting. So at least one of "
        "them was blocking it after all and the deferral was wrong. Asking "
        "now instead of at the check-in:",
    )
    return (
        f"the loop went idle with {len(waiting)} deferred question(s) queued "
        f"for {manager!r} — the deferral was wrong about at least one, so they "
        "were asked now (`rite replies` shows them)"
    )


@dataclass(frozen=True)
class Windows:
    """Whether a check-in will ever come, and the line that says when.

    `usable` False covers every case where a deferred question would have
    nowhere to wait: no windows, only malformed ones, or a config that did
    not parse. Each of those asks at once, and `line` says which."""

    usable: bool
    line: str
    open_now: bool = False


def windows(root: Path) -> Windows:
    """Read this project's check-in windows, on the schedule's clock."""
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.schedule import current_moment, describe_checkins, next_checkin

    config = parse_config(root / ".rite" / "config.yaml")
    if isinstance(config, ParseError):
        return Windows(
            False,
            f"check-ins: unknown — config.yaml did not parse ({config.message}); "
            "a deferred question is asked at once",
        )
    moment = current_moment(config.schedule.timezone)
    when = next_checkin(config.checkins, moment)
    return Windows(
        when.open_now or bool(when.starts),
        describe_checkins(config.checkins, moment),
        open_now=when.open_now,
    )


def deliver_at_checkin(root: Path, manager: str) -> str:
    """At a cycle boundary inside a check-in window, ask what is queued.

    Called at EVERY cycle boundary, and a no-op outside a window or with
    nothing queued. Returns the line to say, or "".

    ⚠ **This is the delivery that makes a deferral whole.** A queue that
    never delivers holds questions a Manager believes it asked, which the
    plan calls worse than no queue. So delivery ships with the queue, not
    after it.
    """
    waiting = queued(root, manager)
    if not waiting:
        return ""
    state = windows(root)
    if not state.open_now:
        return ""
    ask_now(
        root,
        manager,
        waiting,
        f"Check-in — questions the Manager {manager!r} held for this window:",
    )
    return (
        f"check-in: asked {len(waiting)} deferred question(s) from {manager!r} "
        "(`rite replies` shows them)"
    )


def instructions(root: Path, manager: str) -> str:
    """What a Manager is told, every cycle, about deferring a question.

    Every cycle, and with the current check-in line, so the Manager knows
    how long a deferred question would wait. Composed here, where the rule
    lives, rather than in the prompt module: two modules writing a version
    of one text is how they drift.
    """
    from rite_ai import own_command

    rite = own_command()
    state = windows(root)
    lines = [
        "",
        "",
        "## Questions: ask now unless clearly deferrable",
        "",
        f"⚠ THE RULE: {RULE}",
        "",
        "Deferring a question that blocks you idles you until the next "
        "check-in, which can be hours away. Asking one that could have "
        "waited costs the User thirty seconds. So asking now is the "
        "default, and every doubt is resolved by asking now.",
        "",
        f'To ask now: `{rite} reply --manager {manager} "<question>"`.',
        "",
        "Only when a question is CLEARLY deferrable, meaning you have real "
        "work to do meanwhile that does not depend on the answer, you may "
        "defer it to the User's next check-in:",
        f'  {rite} ask --manager {manager} --defer "<question>" --while '
        '"<what you will do meanwhile>"',
        "If you cannot name that work, the question blocks you: ask now. A "
        "deferral with no --while is refused.",
        "",
    ]
    if state.usable:
        lines.append(
            f"Check-ins for this project: {state.line.removeprefix('check-ins: ')}."
        )
    else:
        lines.append(
            "This project has no check-in that will open "
            f"({state.line.removeprefix('check-ins: ')}), so a deferred "
            "question is asked at once anyway."
        )
    return "\n".join(lines) + "\n"
