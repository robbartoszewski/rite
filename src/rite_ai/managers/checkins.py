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
uses, filed as a question (RP1: action, not reading). So `rite replies`, `rite
connect` and the Slack relay all carry it, and nothing here knows which of them
exist.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import manager_dir, own_dir

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
    own_dir.write_text(
        root,
        manager,
        f"{CHECKINS_DIRNAME}/{QUEUE_DIRNAME}/{path.name}",
        json.dumps(
            {"id": qid, "text": text, "meanwhile": meanwhile, "queued_at": queued_at}
        )
        + "\n",
    )
    record(root, manager, {"event": "queued", "id": qid, "at": queued_at})
    return Question(qid, text, meanwhile, queued_at, path)


def _queued(root: Path, manager: str) -> list[Question]:
    """Every question waiting, in the order it was deferred.

    ⚠ **A file that will not parse is still a question somebody deferred.**
    It is returned with its raw content as the text rather than skipped. A
    question skipped here would never be asked, and rite would never say so.
    """
    # Through `own_dir`, following no link the Manager planted (SCRUM-69
    # round-3 review): a queue linked elsewhere would be read, and its files
    # removed, wherever it pointed. A link in it is not a question.
    from rite_ai.managers import own_dir

    where = _queue_dir(root, manager)
    queue = f"{CHECKINS_DIRNAME}/{QUEUE_DIRNAME}"
    try:
        names = own_dir.names(root, manager, queue, ".json")
    except OSError:
        return []
    out: list[Question] = []
    for name in names:
        path = where / name
        try:
            raw = own_dir.read(root, manager, queue, name, 256 * 1024)
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


def _drop(root: Path, manager: str, q: Question) -> None:
    """Remove an asked or withdrawn question, through the queue's own
    descriptor: an `unlink` by path follows a linked directory above it."""
    from rite_ai.managers import own_dir

    own_dir.unlink_in(root, manager, f"{CHECKINS_DIRNAME}/{QUEUE_DIRNAME}", q.path.name)


def record(root: Path, manager: str, event: dict) -> None:
    """Append one event to the Manager's check-in ledger.

    Kept so the filter's value is COUNTED rather than believed (plan § K3):
    how many questions were queued, withdrawn before asking, and asked.
    One JSON object per line, appended in one write.
    """
    # 🔴 Through the layer (`own_dir`): measured, a `checkins/` moved in with
    # its ledger linked to `~/.zshrc` had this append a `$(…)` line there.
    own_dir.append_text(
        root,
        manager,
        f"{CHECKINS_DIRNAME}/{LEDGER_FILENAME}",
        json.dumps(event, sort_keys=True) + "\n",
    )


def ledger(root: Path, manager: str) -> list[dict]:
    """Every recorded event, oldest first. Unreadable lines are skipped."""
    try:
        lines = own_dir.read_text(
            root, manager, f"{CHECKINS_DIRNAME}/{LEDGER_FILENAME}"
        ).splitlines()
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
    root: Path, manager: str, questions: list[Question], why: str, how: str
) -> Path | None:
    """Send these questions to the User NOW, as one message, with `why`.

    `how` is recorded with each (`idle`, `no-window`), so the counts at the
    next check-in can say how many were asked early, and why.

    ⚠ **Sent BEFORE the queue files are removed.** A crash between the two
    asks a question twice, which is recoverable. The other order can lose
    one, which is the failure this module exists to prevent.
    """
    if not questions:
        return None
    from rite_ai.managers.mailbox import OUTBOX, QUESTION, send

    text = "\n".join([why, "", *_question_lines(questions)])
    path = send(root, manager, OUTBOX, text, kind=QUESTION)
    now = time.time()
    for q in questions:
        record(root, manager, {"event": "asked", "id": q.id, "at": now, "how": how})
        _drop(root, manager, q)
    return path


def _idle_with_questions_queued(root: Path, manager: str) -> str:
    """The safety net: the loop went idle while questions were queued.

    A Manager with nothing left to do was, by that fact, waiting on
    something, so at least one deferral was wrong. Everything queued is
    asked now. Returns the line to say, or "" when nothing was queued.
    """
    waiting = _queued(root, manager)
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
        how="idle",
    )
    _clear_reevaluation(root, manager)
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
    opened_at: float = 0.0
    """When the open window opened (epoch seconds), to the minute. A check-in
    is due when the last one delivered is older than this."""


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
    from datetime import datetime, timedelta

    from rite_ai.schedule import minutes_open

    now = datetime.now().astimezone()
    moment = current_moment(config.schedule.timezone, now)
    when = next_checkin(config.checkins, moment)
    opened_at = 0.0
    back = minutes_open(config.checkins, moment)
    if back is not None:
        floor = now.replace(second=0, microsecond=0)
        opened_at = (floor - timedelta(minutes=back)).timestamp()
    return Windows(
        when.open_now or bool(when.starts),
        describe_checkins(config.checkins, moment),
        open_now=when.open_now,
        opened_at=opened_at,
    )


def checkin_done_this_window(root: Path, manager: str, state: Windows) -> bool:
    """Has the open window's check-in already been delivered?"""
    return state.open_now and _last_checkin(root, manager) >= state.opened_at


REEVALUATING_FILENAME = "reevaluating.json"
LAST_CHECKIN_FILENAME = "last.json"


_REEVALUATING = f"{CHECKINS_DIRNAME}/{REEVALUATING_FILENAME}"
_LAST = f"{CHECKINS_DIRNAME}/{LAST_CHECKIN_FILENAME}"


def _reevaluating(root: Path, manager: str) -> bool:
    """Has a re-evaluation cycle been composed and not yet delivered?"""
    return own_dir.exists(root, manager, _REEVALUATING)


def _clear_reevaluation(root: Path, manager: str) -> None:
    own_dir.unlink(root, manager, _REEVALUATING)


def _last_checkin(root: Path, manager: str) -> float:
    """When the last check-in was delivered, or 0.0 if none ever was."""
    try:
        data = json.loads(own_dir.read_text(root, manager, _LAST))
        return float(data.get("at") or 0.0) if isinstance(data, dict) else 0.0
    except (OSError, ValueError, TypeError):
        return 0.0


@dataclass(frozen=True)
class Boundary:
    """What a cycle boundary does about check-ins.

    `instruction` is appended to this cycle's instruction ("" for none) and
    `said` is the line for the terminal ("" for none)."""

    instruction: str = ""
    said: str = ""


def at_boundary(root: Path, manager: str) -> Boundary:
    """Called at EVERY cycle boundary, before the cycle is launched.

    At the first boundary inside a window whose check-in is due, the
    check-in is PREPARED here and DELIVERED when this cycle ends
    (`after_cycle`), so the standup includes this cycle and whatever the
    Manager noted in it.

    ⚠ **THE QUEUE IS A DRAFT.** With questions queued, they are not asked
    yet: this cycle's instruction carries them, with one directive, withdraw
    any you can now answer. A question queued at 10:00 may have been
    answered by the Manager itself by 14:00, and an orchestrator's
    asked-then-retracted churn should never reach the User.

    A marker left by a check-in that was never delivered (a run killed
    mid-cycle) is delivered here, at once: the Manager has had its chance.
    """
    if _reevaluating(root, manager):
        return Boundary(said=_deliver_checkin(root, manager))
    state = windows(root)
    if not state.open_now or checkin_done_this_window(root, manager, state):
        return Boundary()
    waiting = _queued(root, manager)
    own_dir.write_text(
        root,
        manager,
        _REEVALUATING,
        json.dumps({"ids": [q.id for q in waiting], "at": time.time()}) + "\n",
    )
    instruction = _standup_instruction(root, manager)
    if waiting:
        instruction += _reevaluation_instruction(root, manager, waiting)
        said = (
            f"check-in: {len(waiting)} deferred question(s) go to {manager!r} "
            "to re-read first; the check-in goes out when this session ends"
        )
    else:
        said = "check-in: due in this window; it goes out when this session ends"
    return Boundary(instruction=instruction, said=said)


def before_stopping(root: Path, manager: str, verdict: str) -> list[str]:
    """The run is about to stop on the loop's verdict, at a boundary.

    ⚠ **A check-in due in this window is delivered, not skipped.** No cycle
    will run to deliver it, and a Manager with nothing to do is exactly when
    the User should hear what happened. Its standup and every queued
    question go out.

    Otherwise, THE SAFETY NET: idle with questions queued means a deferral
    was wrong, so they are asked now and that is said.
    """
    state = windows(root)
    if _reevaluating(root, manager) or (
        state.open_now and not checkin_done_this_window(root, manager, state)
    ):
        return [_deliver_checkin(root, manager)]
    if verdict == "idle":
        said = _idle_with_questions_queued(root, manager)
        return [said] if said else []
    return []


def start_line(root: Path, manager: str) -> str:
    """What `rite start` says about check-ins, before the first cycle (K6).

    ⚠ **There is no daemon** (A5, Decision 2), so a window that passes with
    no Manager running posts nothing. The queue persists on disk, and this
    line is where the User learns what is waiting and when it will be
    asked. The next check-in's standup covers everything since the last one
    actually DELIVERED, so the gap is reported rather than lost.
    """
    waiting = _queued(root, manager)
    state = windows(root)
    last = _last_checkin(root, manager)
    went = time.strftime("%a %H:%M", time.localtime(last))
    since = (
        f"; the last check-in went out {went}, and the next standup covers "
        "everything since"
        if last
        else ""
    )
    held = f"{len(waiting)} deferred question(s) waiting for {manager!r}"
    if not state.usable:
        tail = "they are asked at once" if waiting else "a deferral is asked at once"
        return f"check-ins: {held} — {state.line.removeprefix('check-ins: ')}; {tail}"
    when = state.line.removeprefix("check-ins: ")
    if state.open_now and not checkin_done_this_window(root, manager, state):
        return (
            f"check-ins: {held}; a window is {when}, so the check-in goes out "
            f"when this run's first session ends{since}"
        )
    return f"check-ins: {held}; {when}{since}"


def _standup_instruction(root: Path, manager: str) -> str:
    """What the check-in cycle is told about the standup."""
    from rite_ai import own_command

    rite = own_command()
    return "\n".join(
        [
            "",
            "",
            "## Check-in: a standup goes to the User when this session ends",
            "",
            "rite composes it from what it observed: commits, Worker sandboxes, "
            "board moves, your cycles and what the engine refused. If there is "
            "something the User should know that rite cannot see, state it "
            "with what makes it checkable:",
            f'  {rite} checkin note --manager {manager} --anchor "<SHA, '
            'file:line, ticket id, sandbox name>" --observed "<what you saw>"',
            'A note without an anchor is refused. "Landed abc1234; observed a '
            'Worker authenticate" is a note; "sorted out the Worker problem" '
            "is not. Your notes are shown as stated by you, not as observed by "
            "rite.",
            "",
        ]
    )


def after_cycle(root: Path, manager: str) -> str:
    """Called when EVERY cycle ends: deliver a re-evaluation's survivors.

    Whatever the ending — finished, quit, crashed — the questions go out.
    A crash is not a reason to leave the User unasked."""
    if not _reevaluating(root, manager):
        return ""
    return _deliver_checkin(root, manager)


def _reevaluation_instruction(root: Path, manager: str, waiting: list[Question]) -> str:
    """The directive the re-evaluation cycle carries."""
    from rite_ai import own_command

    rite = own_command()
    return "\n".join(
        [
            "",
            "",
            "## Check-in: re-read the questions you deferred, before they are asked",
            "",
            "A check-in window is open. These are the questions you deferred "
            "to it. Before they reach the User, WITHDRAW ANY YOU CAN NOW "
            "ANSWER YOURSELF, because you found the answer or it no longer "
            "needs deciding, and say where the answer came from:",
            f"  {rite} question withdraw <id> --manager {manager} "
            '--answered-by "<anchor>"',
            "An anchor is something a reader can check: a commit SHA, a file "
            'and line, a ticket id, a command and its output. "I worked it '
            'out" is not one, and a withdrawal without an anchor is refused.',
            "",
            "Withdraw only what you can answer. Every question you do not "
            "withdraw is asked when this session ends, so leave anything you "
            "are unsure about.",
            "",
            *_question_lines(waiting),
            "",
        ]
    )


def withdraw(root: Path, manager: str, qid: str, answered_by: str) -> str:
    """Withdraw a queued question the Manager has answered itself.

    Returns a refusal, or "" when it was withdrawn. The anchor goes through
    the journal's floor (`anchor_problem`): the same rule, not a copy.
    """
    from rite_ai.managers.journal import anchor_problem

    problem = anchor_problem(
        answered_by,
        "withdraw a question",
        "a withdrawal",
        "asking the question",
    )
    if problem:
        return problem
    for q in _queued(root, manager):
        if q.id == qid:
            record(
                root,
                manager,
                {
                    "event": "withdrawn",
                    "id": q.id,
                    "at": time.time(),
                    "answered_by": answered_by,
                },
            )
            _drop(root, manager, q)
            return ""
    waiting = ", ".join(q.id for q in _queued(root, manager)) or "none"
    return (
        f"no queued question {qid!r} for {manager!r} — it may already have "
        f"been asked. Queued now: {waiting}"
    )


def note(root: Path, manager: str, anchor: str, observed: str) -> str:
    """Record a line the Manager states for the next standup.

    Returns a refusal, or "". ⚠ **A note with no anchor is refused**, through
    the journal's floor: "Landed abc1234; observed a Worker authenticate"
    passes, "sorted out the Worker problem" does not. The standup labels it
    as the Manager's statement, apart from what rite observed."""
    from rite_ai.managers.journal import _is_blank, anchor_problem

    problem = anchor_problem(anchor, "add a standup note", "a standup line", "no line")
    if problem:
        return problem
    if _is_blank(observed):
        return (
            "refusing a standup note with no --observed: an anchor with "
            "nothing said about it is a pointer to nothing. Say what was "
            'SEEN there, e.g. --observed "a Worker authenticated".'
        )
    record(
        root,
        manager,
        {"event": "note", "at": time.time(), "anchor": anchor, "observed": observed},
    )
    return ""


def is_checkin(root: Path, manager: str, outbox_name: str) -> bool:
    """Was this outbox file a check-in? Read from the ledger, not the text."""
    return any(
        e.get("event") == "checkin" and e.get("outbox") == outbox_name
        for e in ledger(root, manager)
    )


def held_questions(root: Path, manager: str, outbox_name: str) -> int:
    """How many questions the check-in `outbox_name` asked (RP1: a check-in
    holding questions needs the person, one without does not). A check-in
    recorded before the count was kept is taken to hold one: unknown is
    treated as needing the person, as everywhere in RP1."""
    for e in ledger(root, manager):
        if e.get("event") == "checkin" and e.get("outbox") == outbox_name:
            try:
                return int(e.get("questions", 1))
            except (TypeError, ValueError):
                return 1
    return 0


@dataclass(frozen=True)
class Counts:
    """The filter's value, counted over one check-in period."""

    queued: int = 0
    withdrawn: int = 0
    asked: int = 0
    asked_early: int = 0

    def line(self) -> str:
        """⚠ Stated in every check-in, so "deferral filters" is measured
        rather than believed. If `withdrawn` stays near zero for weeks,
        deferral is not filtering, and that is worth knowing."""
        text = (
            f"Deferred questions since the last check-in: {self.queued} "
            f"queued, {self.withdrawn} withdrawn by the Manager before "
            f"asking, {self.asked} asked now"
        )
        if self.asked_early:
            text += (
                f"; {self.asked_early} asked early, because the loop went "
                "idle or no window was open to wait for"
            )
        return text + "."


def _counts_since(root: Path, manager: str, since: float, asking: int) -> Counts:
    """Count the ledger since `since`. `asking` is how many are being asked
    at this check-in, which have no `asked` event yet."""
    events = [e for e in ledger(root, manager) if float(e.get("at") or 0) > since]
    return Counts(
        queued=sum(1 for e in events if e.get("event") == "queued"),
        withdrawn=sum(1 for e in events if e.get("event") == "withdrawn"),
        asked=asking,
        asked_early=sum(
            1 for e in events if e.get("event") == "asked" and e.get("how") != "checkin"
        ),
    )


def _deliver_checkin(root: Path, manager: str) -> str:
    """Ask what survived the re-evaluation, as one message, with the counts.

    ONE message: the standup, the deferral counts and the questions that
    survived, so every reader of the outbox (`rite replies`, the Slack relay)
    carries the whole check-in.

    ⚠ **A check-in where every question was withdrawn is still delivered.**
    Its message is the count, and it is the evidence that the filter
    filtered.
    """
    from rite_ai.managers import standup
    from rite_ai.managers.mailbox import CHECKIN, OUTBOX, send

    survivors = _queued(root, manager)
    since = _last_checkin(root, manager)
    counts = _counts_since(root, manager, since, len(survivors))
    withdrawn = [
        e
        for e in ledger(root, manager)
        if e.get("event") == "withdrawn" and float(e.get("at") or 0) > since
    ]
    # Since MM8 the ledger is outside the project (`managers.manager_dir`), so
    # it cannot be shown relative to it. Shown from `~`, because this message
    # goes to Slack and an absolute path carries the operator's user name.
    full = _checkins_dir(root, manager) / LEDGER_FILENAME
    home = Path.home()
    ledger_path = f"~/{full.relative_to(home)}" if full.is_relative_to(home) else full
    lines = [f"Check-in — {manager}", "", *standup.digest(root, manager, since)]
    lines += ["", "Deferred questions:", f"- {counts.line()} (ledger: {ledger_path})"]
    for e in withdrawn:
        lines.append(f"- withdrawn {e.get('id')}: answered by {e.get('answered_by')}")
    if survivors:
        lines += ["", "Questions held for this check-in:", *_question_lines(survivors)]
    # RP1 piece 2: what was asked and never confirmed to reach anyone comes
    # back here, every check-in, until something confirms it.
    from rite_ai.managers import pending

    lines += pending.checkin_lines(root, manager, now=time.time())
    # TR2: an escalated refinement is in every checkpoint until he decides it
    # (Robert: "escalates it as a blocker ... in the checkpoint status updates").
    from rite_ai.refinement import protocol as refinement_protocol

    lines += refinement_protocol.checkin_lines(root, manager)
    path = send(root, manager, OUTBOX, "\n".join(lines), kind=CHECKIN)
    now = time.time()
    # Which outbox file IS a check-in, kept here rather than in the message
    # (which stays identity-free, Decision 1a): the Slack relay roots the
    # answer thread on it and mirrors it to the broadcast channel (K5).
    record(
        root,
        manager,
        {
            "event": "checkin",
            "at": now,
            "outbox": path.name,
            "questions": len(survivors),
        },
    )
    for q in survivors:
        record(
            root, manager, {"event": "asked", "id": q.id, "at": now, "how": "checkin"}
        )
        _drop(root, manager, q)
    own_dir.write_text(root, manager, _LAST, json.dumps({"at": now}) + "\n")
    _clear_reevaluation(root, manager)
    return f"check-in: {counts.line()} (`rite replies` shows the check-in)"


def instructions(root: Path, manager: str) -> str:
    """What a Manager is told, every cycle, about deferring a question.

    Every cycle, and with the current check-in line, so the Manager knows
    how long a deferred question would wait. Composed here, where the rule
    lives, rather than in the prompt module: two modules writing a version
    of one text is how they drift.
    """
    from rite_ai import own_command
    from rite_ai.managers import stdin_text

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
        "To ask now:",
        stdin_text.file_form(
            root, manager, f"{rite} ask --manager {manager}", "ask.md", "the question"
        ),
        stdin_text.FILE_RULE,
        "",
        "Only when a question is CLEARLY deferrable, meaning you have real "
        "work to do meanwhile that does not depend on the answer, you may "
        "defer it to the User's next check-in:",
        # SCRUM-33: the meanwhile is the file's first line, never a
        # double-quoted argument; it names tickets other people wrote.
        stdin_text.file_form(
            root,
            manager,
            f"{rite} ask --manager {manager} --defer --while -",
            "ask.md",
            "what you will do meanwhile on the first line, then the question",
        ),
        "The first line is what you will do meanwhile, the rest is the "
        "question. If you cannot name that work, the question blocks you: ask "
        "now. A deferral with no --while, or an empty first line, is refused.",
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
