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


def relay(root: Path, manager: str, say) -> int:
    """Deliver each answered Worker question into the Worker (S30).

    Returns how many answers were carried. Never raises into the supervisor,
    for `surface`'s reason: a watcher that ends the run is worse than one
    that misses a tick.
    """
    try:
        return _relay(root, manager, say)
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


def _relay(root: Path, manager: str, say) -> int:
    """⚠ Reads the inbox WITHOUT consuming it (`mailbox.read`, not
    `take_mail`). The answer is the Owner's mail as well as the Worker's: the
    Owner is told what its Worker was told, and taking the message here would
    silently remove it from the Manager's next prompt."""
    from rite_ai.config.parse import load_project
    from rite_ai.managers import delivered
    from rite_ai.managers.asking import raise_to_person, settle
    from rite_ai.managers.mailbox import INBOX, read
    from rite_ai.sandbox import existing_sandbox_name, worker_sandbox_status
    from rite_ai.sandbox.questions import Undeliverable, deliver_answer

    root = Path(root)
    project = load_project(root)
    if isinstance(project, list) or not project.config.sandbox.enabled:
        return 0
    path = _ledger_path(root, manager)
    ledger = _load(path)
    raised = {k: v for k, v in ledger.items() if not k.startswith("_")}
    if not raised:
        return 0
    done = set(ledger.get(RELAYED_KEY) or [])
    by_qid = {qid: sandbox for sandbox, qid in raised.items() if isinstance(qid, str)}
    worker_of = {existing_sandbox_name(w.name, root): w.name for w in project.workers}

    carried = 0
    for message in read(root, manager, INBOX):
        name = message.path.name
        if name in done:
            continue
        qid = _qid_in(message.text.split("\n", 1)[0])
        sandbox = by_qid.get(qid)
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
        outcome = deliver_answer(sandbox, heard.words, status=status)
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
                    f"Your answer did NOT reach Worker {worker or sandbox!r}: "
                    f"{outcome.reason}.{gone}"
                ),
            )
            say(
                f"answer for Worker {worker or sandbox!r} ({qid}) could not be "
                f"delivered: {outcome.reason}; told the User"
            )
            continue
        settle(root, manager, qid)
        raised.pop(sandbox, None)
        ledger.pop(sandbox, None)
        _store(path, ledger)
        say(f"relayed the User's answer to Worker {worker or sandbox!r} ({qid})")
        carried += 1
    return carried
