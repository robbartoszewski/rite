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

**The answer comes back the same way (S30, v0.7.0a4).** This paragraph used
to say the opposite — "it does not deliver an answer back: yoloAI 0.11.0 has
no way to send input to a running agent" — and the last dogfood run paid for
it: the operator had to `yoloai attach` the Worker to answer a question rite
had already carried all the way to Slack.

⚠ **The claim was wrong about the protocol.** yoloAI's injected `CLAUDE.md`
tells the agent to write `question.json` and to POLL `answer.json`. The
return path is a file the Worker is already watching, in the directory this
module already reads the question from. `relay` writes it
(`sandbox.questions.deliver_answer`).

**The OWNER relays, which is Robert's decision and also the only shape that
works.** The Owner already supervises the Worker and holds its sandbox
handle; it is outside every boundary, so it can write the exchange file,
and the answer arrives in its inbox because rite addressed the question from
it. A Worker cannot be messaged directly — a sandboxed Worker never appears
in Claude Code's session list (`sandbox.worker_pane`).

⚠ **Nothing here can turn a Worker's sandbox off.** No flag, no grant, no
permission change: an answer is a file in the exchange directory yoloAI made
for it, written by the host. Typing into the live session — the
`tmux -S … send-keys` route `worker_pane` names — is deliberately NOT what
this does.

⚠ **An answer that cannot land is reported, never dropped.** If the Worker
stopped before the answer arrived, the person who wrote it is told, in the
same thread, that it did not reach anyone — because they are otherwise
waiting on a reply that will never come. `Undeliverable` carries the reason
and whether the Worker is gone for good.

**Told once per question,** by `asking`'s ledger (keyed by subject, raiser
and text). A Worker that rewrites its question (the dogfood's did, twice)
asks a new one, and the old one is settled; so is a question once it is
answered or its sandbox is gone.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rite_ai.duration import format_duration

LEDGER_FILE = "worker-questions.json"
"""sandbox -> the id (`asking._question_id`) of the question raised for it,
so it can be settled when the Worker's question is answered or gone. The
once-only rule itself is `asking`'s ledger, shared with refinement.

Beside it, under keys that start with `_`: `OPEN_KEY` (every id still open,
not only the latest per sandbox), `CLOSED_KEY` (ids that were Worker
questions and are not any more) and `RELAYED_KEY` (messages already
carried)."""

DELIVERED_KEY = "_delivered"
"""qid -> {"worker", "at"} for an answer written into a sandbox (SCRUM-61).

Kept so that "written where it is polled for" and "the Worker says it read
it" can be told apart. An entry is removed when the ack arrives, so what is
left is exactly the set of answers nobody has acknowledged — and past
`read_ack.UNREAD_AFTER` that set is reported to the person who wrote them,
because they are otherwise waiting on a Worker that may never have woken."""

OPEN_KEY = "_open"
"""qid -> sandbox, for EVERY Worker question raised and not yet answered or
gone (SCRUM-52). The per-sandbox entry above holds only the LATEST id, so a
reply to an earlier question that was still unanswered matched nothing and
was dropped without a word."""

CLOSED_KEY = "_closed"
"""qid -> sandbox, for Worker questions that are no longer open, newest last
and bounded by `CLOSED_KEPT`. A reply to one of these is told back to the
person as matching no open question, instead of being ignored; an id that was
never a Worker question is somebody else's mail and is left alone."""

CLOSED_KEPT = 200


def _open_of(ledger: dict) -> dict:
    """Every open Worker question, qid -> sandbox: `OPEN_KEY`, plus the
    per-sandbox latest ids, which a ledger written before SCRUM-52 holds
    alone."""
    found = {
        k: v
        for k, v in (ledger.get(OPEN_KEY) or {}).items()
        if isinstance(k, str) and isinstance(v, str)
    }
    for sandbox, qid in ledger.items():
        if not sandbox.startswith("_") and isinstance(qid, str):
            found.setdefault(qid, sandbox)
    return found


def _close(ledger: dict, qid: str) -> None:
    """Move `qid` from open to closed, in place."""
    open_ = dict(ledger.get(OPEN_KEY) or {})
    sandbox = open_.pop(qid, None) or next(
        (s for s, v in ledger.items() if v == qid and not s.startswith("_")), ""
    )
    ledger[OPEN_KEY] = open_
    closed = dict(ledger.get(CLOSED_KEY) or {})
    closed.pop(qid, None)
    closed[qid] = sandbox
    ledger[CLOSED_KEY] = dict(list(closed.items())[-CLOSED_KEPT:])


def _close_sandbox(ledger: dict, sandbox: str) -> list[str]:
    """Close every open question of `sandbox`; return their ids."""
    gone = [qid for qid, s in _open_of(ledger).items() if s == sandbox]
    for qid in gone:
        _close(ledger, qid)
    return gone


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
        "**Reply in this thread and the answer goes to the Worker** — the "
        "Owner relays it into the sandbox the Worker polls (S30). If it has "
        "stopped by then, rite says so here rather than leaving you waiting."
    )


def _for_the_manager(worker: str, sandbox: str, ticket: str, question) -> str:
    from rite_ai.managers.telling import header

    about = f" on {ticket}" if ticket else ""
    return (
        header(f"Worker {worker!r} · WAITING ON A QUESTION")
        + "\n"
        + f"Worker {worker!r}{about} asked at {question.since()} and is blocked "
        "until a person answers. rite has sent the question to the User as one "
        "that needs an answer, and rite itself relays the reply into the "
        "Worker (S30) — you do not carry it, and you cannot answer it from "
        "inside your boundary: do not guess an answer for it, and do not "
        f"start another Worker on the same ticket. `rite sandbox status "
        f"{worker}` shows it. The question:\n"
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
            # Answered, or gone: the person is no longer being asked it, nor
            # any earlier question of this sandbox still open (SCRUM-52).
            earlier = raised.pop(sandbox, None)
            closed = _close_sandbox(raised, sandbox)
            if isinstance(earlier, str):
                settle(root, manager, earlier)
            if isinstance(earlier, str) or closed:
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
            # A rewritten question replaces the one it rewrote as the one the
            # person is asked. ⚠ It stays OPEN for an answer (SCRUM-52): a
            # reply to it can still arrive, and dropping that reply silently
            # is the defect.
            settle(root, manager, earlier)
        opened = dict(raised.get(OPEN_KEY) or {})
        if earlier != result.id or opened.get(result.id) != sandbox:
            raised[sandbox] = result.id
            if isinstance(earlier, str):
                opened.setdefault(earlier, sandbox)
            opened[result.id] = sandbox
            raised[OPEN_KEY] = opened
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


RELAYED_KEY = "_relayed"
"""Message filenames already relayed, in the same ledger. Keyed by the
message, not by the question: one question can be answered twice, and the
second answer is a new message that should reach the Worker too."""


def _qid_in(text: str) -> str:
    """The question id `asking` wrote into the first line rite sent, found in
    a reply. The SAME rule the Slack relay uses to match a refinement round
    (`slack._QUESTION_IN_LABEL`), imported rather than restated so a change
    to the id's shape cannot match in one place and miss in the other."""
    from rite_ai.managers.slack import _QUESTION_IN_LABEL  # noqa: PLC2701

    found = _QUESTION_IN_LABEL.search(text)
    return found.group(0) if found else ""


# ⚠ `_answer_text` USED TO LIVE HERE AND WAS WRONG TWICE OVER (found in
# review of #164). It stripped any line carrying a question id and handed the
# rest to the Worker, which (a) left the relay's `> ` quote markers in the
# answer — the Worker received `"> 30 seconds\n> and it is fine"` — and (b)
# said nothing about WHOSE words they were. `delivered.classify` answers both,
# and is the same function the Manager's own instruction path is held to, so
# the two cannot come to disagree about what counts as the User speaking.


def relay(root: Path, manager: str, say, messages=None) -> int:
    """Deliver each answered Worker question into the Worker (S30).

    Returns how many answers were carried. Never raises into the supervisor,
    for `surface`'s reason: a watcher that ends the run is worse than one
    that misses a tick.

    `messages`, when given, are the ones the cycle boundary has just TAKEN
    from the inbox (SCRUM-52), relayed instead of reading the inbox: by then
    they are no longer in it, and the relay must still see them.
    """
    try:
        return _relay(root, manager, say, messages)
    except Exception as e:  # noqa: BLE001 - a watcher must not end the run
        problem = f"{type(e).__name__}: {e}"
        _say_once(root, manager, say, f"could not relay Worker answers: {problem}")
        _tell_the_person_relaying_failed(root, manager, problem, say)
        return 0


def _tell_the_person_relaying_failed(root: Path, manager: str, problem: str, say):
    """🔴 SCRUM-23. The relay failing as a whole was said only in the
    supervisor's pane, so a person waiting on a Worker was told nothing.
    Raised once per problem (`asking`'s ledger is keyed by the text), in
    the Owner's DM like an answer that could not land. If even that fails,
    the pane says so."""
    from rite_ai.managers.asking import raise_to_person

    try:
        raise_to_person(
            root,
            manager,
            subject="",
            raiser=f"manager:{manager}",
            text=(
                f"rite could not relay your answers to Workers: {problem}. "
                "Nothing was written into a Worker. rite tries again at its "
                "next look; if this does not clear, the Workers are still "
                "waiting on you."
            ),
        )
    except Exception as e:  # noqa: BLE001 - said, never raised into the run
        _say_once(root, manager, say, f"could not tell the User either: {e}")


def _relay(root: Path, manager: str, say, messages=None) -> int:
    """⚠ Reads the inbox WITHOUT consuming it (`mailbox.read`, not
    `take_mail`). The answer is the Owner's mail as well as the Worker's: the
    Owner is told what its Worker was told, and taking the message here would
    silently remove it from the Manager's next prompt.

    🔴 **And the cycle boundary hands it what it takes (SCRUM-52).** This ran
    only from a watcher throttled to once every 30 s, and a reply that woke a
    Manager cycle was taken by that cycle (`take_mail`) before the relay's
    next turn: it reached the Manager's prompt and never the Worker's
    `answer.json`, while the Owner believed it delivered. Measured in the
    dogfood: two replies taken, neither ever relayed. So the supervisor passes
    `messages`, exactly what it took, and nothing taken can slip past. Each
    message is carried once, whichever path saw it first (`RELAYED_KEY`)."""
    from rite_ai.config.parse import load_project
    from rite_ai.managers import delivered
    from rite_ai.managers.asking import raise_to_person, settle
    from rite_ai.managers.mailbox import INBOX, read
    from rite_ai.sandbox import existing_sandbox_name, worker_sandbox_status
    from rite_ai.sandbox.questions import Undeliverable, deliver_answer

    root = Path(root)
    # Cheap first, because this runs at the poll rate now (SCRUM-52): no
    # Worker question was ever raised, so nothing here can be an answer.
    path = _ledger_path(root, manager)
    ledger = _load(path)
    by_qid = _open_of(ledger)
    closed = {
        k: v for k, v in (ledger.get(CLOSED_KEY) or {}).items() if isinstance(k, str)
    }
    if not by_qid and not closed:
        return 0
    project = load_project(root)
    if isinstance(project, list) or not project.config.sandbox.enabled:
        return 0
    raised = {k: v for k, v in ledger.items() if not k.startswith("_")}
    done = set(ledger.get(RELAYED_KEY) or [])
    worker_of = {existing_sandbox_name(w.name, root): w.name for w in project.workers}

    carried = 0
    for message in read(root, manager, INBOX) if messages is None else messages:
        name = message.path.name
        if name in done:
            continue
        qid = _qid_in(message.text.split("\n", 1)[0])
        sandbox = by_qid.get(qid)
        if qid and sandbox is None and qid in closed:
            # 🔴 SCRUM-52: a reply to a Worker question that is no longer open
            # (answered already, or its Worker stopped asking). Said and told,
            # never dropped: the person answered and is otherwise waiting.
            done.add(name)
            ledger[RELAYED_KEY] = sorted(done)
            _store(path, ledger)
            gone_from = closed.get(qid) or ""
            who = worker_of.get(gone_from, gone_from) or "a Worker"
            say(
                f"a reply to {qid} matched no open question of Worker {who!r}: "
                "nothing was written into a Worker; told the User"
            )
            raise_to_person(
                root,
                manager,
                subject=_ticket_of(root, gone_from) if gone_from else "",
                raiser=f"worker:{who}",
                text=(
                    f"Your reply to {qid} matched no open question of Worker "
                    f"{who!r}: that question was already answered, or the "
                    "Worker stopped asking it. Nothing was written into a "
                    "Worker. If it is still waiting, answer its current "
                    f"question: `rite sandbox status {who}` shows it."
                ),
            )
            continue
        if not qid or sandbox is None:
            # Not an answer to a Worker's question. Left alone: every other
            # kind of mail is somebody else's to read.
            continue
        # ⚠ **WHOSE WORDS, BEFORE ANYTHING IS WRITTEN (D-95).** Matching the
        # question id is not authority: the id travels in the header of EVERY
        # relayed reply in that thread, because the thread's label is the
        # first 40 characters of what rite posted and the id is in them. So a
        # reply in the BROADCAST channel — which the relay marks `context`,
        # and which is where Worker questions go when no `slack.owner_user`
        # is configured — matched, and was written into the Worker as its
        # answer. Anyone in the workspace could steer a sandboxed Worker
        # running with permissions relaxed. Found in review of #164.
        #
        # `delivered.classify` is the SAME gate the Manager's own instruction
        # path uses: the Owner's DM marked INSTRUCTION, the refinement
        # channel likewise, or this machine — and nothing else. Reused rather
        # than restated so authority cannot mean one thing here and another
        # there.
        heard = delivered.classify(message.text)
        if not heard.users:
            done.add(name)
            ledger[RELAYED_KEY] = sorted(done)
            _store(path, ledger)
            # Said, not carried, and the question stays outstanding so the
            # person is still asked. Not raised to the person either: a
            # bystander's comment is not a failure to report to them.
            why = heard.why_not or "it is not the User's instruction"
            say(
                f"not relaying to Worker {worker_of.get(sandbox, sandbox)!r} "
                f"({qid}): {why} [{heard.where}]"
            )
            continue
        worker = worker_of.get(sandbox, "")
        status = worker_sandbox_status(worker, root) if worker else None
        words = heard.words
        latest = raised.get(sandbox)
        if isinstance(latest, str) and latest != qid:
            # An answer to an EARLIER question of this Worker, which has asked
            # another since (SCRUM-52). Delivered, and labelled, so the Worker
            # does not read it as the answer to the question it asked last.
            words = (
                f"(This answers your earlier question, {qid}, not your latest "
                f"one, {latest}.)\n{words}"
            )
        outcome = deliver_answer(sandbox, words, status=status, question=qid)
        done.add(name)
        ledger[RELAYED_KEY] = sorted(done)
        _store(path, ledger)
        if isinstance(outcome, Undeliverable):
            # ⚠ BACK TO THE PERSON, always. They answered and are now waiting
            # on a Worker that will never read it; silence here is the defect
            # this whole path exists to remove.
            gone = (
                " The Worker is gone, so this answer has nowhere to land: the "
                "work needs starting again."
                if outcome.stopped
                else " Nothing was written into the sandbox."
            )
            raise_to_person(
                root,
                manager,
                subject=_ticket_of(root, sandbox),
                raiser=f"worker:{worker or sandbox}",
                text=(
                    f"Your answer to {qid} did NOT reach Worker "
                    f"{worker or sandbox!r}: {outcome.reason}.{gone}"
                ),
            )
            say(
                f"answer for Worker {worker or sandbox!r} ({qid}) could not be "
                f"delivered: {outcome.reason}; told the User"
            )
            continue
        # Recorded BEFORE settling, because settling is what makes the
        # question stop being open: the delivered-but-unread set has to
        # outlive it (SCRUM-61).
        delivered_at = dict(ledger.get(DELIVERED_KEY) or {})
        delivered_at[qid] = {"worker": worker or sandbox, "at": time.time()}
        ledger[DELIVERED_KEY] = delivered_at
        settle(root, manager, qid)
        _close(ledger, qid)
        if raised.get(sandbox) == qid:
            raised.pop(sandbox, None)
            ledger.pop(sandbox, None)
        _store(path, ledger)
        by_qid.pop(qid, None)
        closed[qid] = sandbox
        say(f"relayed the User's answer to Worker {worker or sandbox!r} ({qid})")
        carried += 1
    return carried


def report_unread(root: Path, manager: str, say) -> int:
    """Tell the person about each answer delivered and never acknowledged.

    SCRUM-61's other half. `deliver_answer` establishes only that the answer
    was written where the Worker polls for it, and until a Worker says it
    read it (`rite ack`) nobody knows it arrived. Past
    `read_ack.UNREAD_AFTER` that is worth saying: the person answered and is
    waiting on a Worker that may never have woken up.

    ⚠ **Told ONCE, through `asking`'s ledger**, which is the same once-only
    the `Undeliverable` path above uses. A per-tick report of a standing
    condition is the failure `scheduler._stall_records` records, and this
    runs on the Worker watcher's cadence.

    ⚠ **An ack whose record cannot be READ is not "unread".** `read_ack`
    answers `unreadable` for that, and treating it as an absent ack would
    tell somebody their answer never arrived on the strength of a file
    nobody could parse. It is left for the next look, and the relay's own
    problem line says the record could not be read.

    Never raises into the supervisor: a failure is said and the next look
    tries again.
    """
    try:
        return _report_unread(root, manager, say)
    except Exception as e:  # noqa: BLE001 - a watcher must not end the run
        _say_once(root, manager, say, f"could not check for unread answers: {e}")
        return 0


def _report_unread(root: Path, manager: str, say) -> int:
    from rite_ai import read_ack
    from rite_ai.managers.asking import raise_to_person

    root = Path(root)
    path = _ledger_path(root, manager)
    ledger = _load(path)
    delivered = dict(ledger.get(DELIVERED_KEY) or {})
    if not delivered:
        return 0
    now = time.time()
    told = 0
    changed = False
    for qid in sorted(delivered):
        entry = delivered[qid]
        if not isinstance(entry, dict):
            delivered.pop(qid)
            changed = True
            continue
        worker = str(entry.get("worker") or "")
        at = entry.get("at")
        if not worker or not isinstance(at, int | float):
            delivered.pop(qid)
            changed = True
            continue
        acks = read_ack.read(root, worker)
        if acks is not None and not acks.known:
            # Cannot tell. Not reported as unread, and not dropped.
            continue
        if acks is not None and acks.read_at(qid) is not None:
            # Acknowledged: the ✅ is the relay's job, and this set holds
            # only what is still unacknowledged.
            delivered.pop(qid)
            changed = True
            continue
        if now - float(at) < read_ack.UNREAD_AFTER:
            continue
        waited = format_duration(now - float(at))
        raise_to_person(
            root,
            manager,
            subject=_ticket_of(root, _open_of(ledger).get(qid, "")),
            raiser=f"worker:{worker}",
            text=(
                f"Your answer to {qid} reached Worker {worker!r}'s sandbox "
                f"{waited} ago and it has not said it read it. rite wrote the "
                "answer where the Worker polls for it; whether it was opened "
                "is not something rite can see. Look at the Worker before "
                "assuming it has the answer."
            ),
        )
        say(
            f"answer to {qid} is UNREAD by Worker {worker!r} after {waited}: "
            "told the User"
        )
        told += 1
    if changed or told:
        ledger[DELIVERED_KEY] = delivered
        _store(path, ledger)
    return told


def _teller(config) -> str:
    """The Manager whose supervisor tells the person and relays the answer,
    or "" when none is declared. The same choice `_worker_question_watch`
    makes: the one holding `route`, or with no roles the first declared
    Manager (`rite start`'s priority order, §2.4)."""
    from rite_ai.config.managers import routing_owner

    roles = list(config.coordination.manager_roles)
    if roles:
        return routing_owner(roles)
    managers = list(config.coordination.managers)
    return str(managers[0]) if managers else ""


def raised_id(root: Path, sandbox: str) -> str:
    """The id rite raised this sandbox's question under (`q3f9a`), or "" when
    it has not been raised or nothing records it. A message that refers back
    to the question names it by this id, which the Slack relay renders as a
    link to the post (SCRUM-47)."""
    from rite_ai.config.parse import load_project

    project = load_project(Path(root))
    teller = "" if isinstance(project, list) else _teller(project.config)
    if not teller:
        return ""
    qid = _load(_ledger_path(Path(root), teller)).get(sandbox)
    return qid if isinstance(qid, str) else ""


def how_to_answer(root: Path, worker: str, sandbox: str, stopped: bool) -> str:
    """How a person answers this Worker's question, in one sentence, for
    `rite sandbox status` (SCRUM-25).

    ⚠ **It said "answer it by attaching", which stopped being the way in
    S30.** The relay above carries an answer into the file the Worker polls;
    sending people to `yoloai attach` sent them to type into a live session
    for a question rite had already posted to Slack. Said per state, because
    each needs a different thing from the person: a stopped Worker cannot
    be answered at all, and a question no Manager has passed on yet has no
    thread to reply in."""
    from rite_ai.config.parse import load_project

    if stopped:
        return (
            "the Worker has stopped, so no answer can reach it: start it again "
            "on its ticket once the question is settled"
        )
    project = load_project(Path(root))
    teller = "" if isinstance(project, list) else _teller(project.config)
    if not teller:
        return (
            "no Manager is declared to relay an answer, so the only way to "
            f"answer it is to type into the session: `yoloai attach {sandbox}`"
        )
    qid = _load(_ledger_path(Path(root), teller)).get(sandbox)
    if not isinstance(qid, str):
        return (
            f"it has not been passed on yet: Manager '{teller}' sends it to you "
            f"while `rite start {teller}` runs, and its id is what an answer "
            "is matched by"
        )
    return (
        f"reply in its Slack thread, or here with `rite message {teller} "
        f'"{qid} <your answer>"`; while `rite start {teller}` runs, it writes '
        f"the answer into the file Worker '{worker}' is polling"
    )
