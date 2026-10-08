"""The one way rite puts a question in front of a person (Q1–Q4 part B; TR2).

**Why one.** The v0.6.0 dogfood had two agents with questions: a Worker whose
question sat in a file nobody read, and an Owner that never asked about the
lazy tickets at all. Ticket refinement (TR2) is building the Owner's side and
this dogfood fix the Worker's. Agreed through the coordinator, 2026-09-28:
both go through here, and differ only in who raises the question and where an
answer goes. Two mechanisms for "an agent has a question for a human" is what
this exists to prevent.

**What it does.** Writes the question as a `question`-kind message
(`mailbox.QUESTION`, RP1 piece 1) in the Owner's outbox. The Slack relay posts
that to the Owner's DM labelled "needs your answer", and `rite replies` shows
it on this machine.

**rite writes the first line, and it is what an answer is matched by.** The
relay labels the Slack thread it starts with the first 40 characters of the
text (`slack.py`, `reply "<40>"`), and relays a reply in that thread under a
rite-written header naming the label. So the first line leads with the
subject, then this question's id, both inside those 40 characters:

    KAN-7 · q3f9a · Worker alpha is waiting · reply in this thread

An answer is attributed by the thread it is in (or by a leading subject id in
the DM), never by a model's reading of it. Only a message the relay marks
INSTRUCTION (the Owner's DM) or one typed on this machine counts as an
answer; that rule lives in the relay and in the consumer, not here.

**Raised once.** A ledger in the Owner's own state directory, keyed by the
question's id: a hash of subject, raiser and text. A question still
outstanding the next time a caller looks (a Worker's `question.json` is seen
every tick) is not raised again. Whether the person has SEEN it is RP1's
delivery confirmation (`managers.pending`), which tracks this message like
any other that needs the person; see `outstanding`. `settle` drops it once
answered, so the same words asked later are a new question. The id is not
secret; it is an address.

⚠ **The signature is shared with TR2.** Change it only with the coordinator
told, so the refinement work is not pointed at something that moved.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

LEDGER_FILE = "asked.json"
LABEL_CHARS = 40
"""The relay's thread label length (`slack.py`); the first line must carry the
subject and the id within it."""


@dataclass(frozen=True)
class Raised:
    id: str
    """`q` plus four hex digits: in the first line, and the ledger's key."""
    new: bool
    """True when this call put it in front of the person; False when it was
    already pending from an earlier call."""


def _question_id(subject: str, raiser: str, text: str) -> str:
    raw = f"{subject}\0{raiser}\0{text}".encode()
    return "q" + hashlib.sha256(raw).hexdigest()[:4]


def _who(raiser: str) -> str:
    kind, _, name = raiser.partition(":")
    return f"{kind.capitalize()} {name}" if name else raiser


def _first_line(subject: str, qid: str, raiser: str) -> str:
    return f"{subject} · {qid} · {_who(raiser)} is waiting · reply in this thread"


_RAISED = re.compile(r" · (q[0-9a-f]{4}) · .+ is waiting · reply in this thread$")
"""`_first_line`'s shape: how a question rite raised introduces its own id."""


def own_question_id(text: str) -> str:
    """The id a question rite raised introduces in its first line
    (`_first_line`), or "" for any other message. Only that line introduces an
    id; an id anywhere else in a message is a reference back to one (SCRUM-47).
    """
    found = _RAISED.search(text.split("\n", 1)[0])
    return found.group(1) if found else ""


def _ledger_path(root: Path, owner: str) -> Path:
    from rite_ai.managers import manager_dir

    return manager_dir(root, owner) / LEDGER_FILE


def _load(path: Path) -> dict:
    # Through `own_dir`: a link or FIFO planted at the ledger is never
    # followed (SCRUM-69 round 3).
    from rite_ai.managers import own_dir

    return own_dir.load_json(path)


def _store(path: Path, data: dict) -> None:
    from rite_ai.managers import own_dir

    own_dir.write_file(path, json.dumps(data, indent=1, sort_keys=True) + "\n")


def raise_to_person(
    root: Path, owner: str, *, subject: str, raiser: str, text: str
) -> Raised:
    """Put `text` in front of the person, once, as a question from `raiser`
    about `subject`, through `owner`'s outbox.

    `raiser` is `"worker:<name>"` or `"manager:<name>"`. `subject` is a
    ticket id; with none, the raiser stands in for it, because an empty
    subject would leave the thread label nothing to match on.
    """
    from rite_ai.managers.mailbox import OUTBOX, QUESTION, send

    root = Path(root)
    subject = subject.strip() or _who(raiser)
    qid = _question_id(subject, raiser, text)
    path = _ledger_path(root, owner)
    ledger = _load(path)
    if qid in ledger:
        return Raised(qid, False)
    send(
        root,
        owner,
        OUTBOX,
        _first_line(subject, qid, raiser) + "\n" + text,
        kind=QUESTION,
    )
    ledger[qid] = {
        "subject": subject,
        "raiser": raiser,
        "raised_at": time.time(),
    }
    _store(path, ledger)
    return Raised(qid, True)


def settle(root: Path, owner: str, qid: str) -> bool:
    """The question `qid` is answered, or no longer asked: forget it, so the
    same words asked again are a new question. True if it was pending."""
    path = _ledger_path(Path(root), owner)
    ledger = _load(path)
    if qid not in ledger:
        return False
    del ledger[qid]
    _store(path, ledger)
    return True


def outstanding(root: Path, owner: str) -> dict:
    """Every question raised through `owner` and not settled, by id.

    NOT `managers.pending` (RP1 piece 2), which answers a different
    question: whether the person has SEEN a message (a reply or a reaction
    in Slack, or `rite replies`). That ledger is keyed by the outbox file and
    picks up every `question`-kind message this module writes by itself, so
    a question raised here also comes back at each check-in until it is
    confirmed. This one is keyed by the question's content, so the same
    question is raised once, and settled when it is answered."""
    return _load(_ledger_path(Path(root), owner))
