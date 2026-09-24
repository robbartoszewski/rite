"""A per-Manager mailbox on disk: messages in, messages out.

**What it is for.** A Manager could be watched and not talked to. Attaching
to its tmux pane shows what it did; it gives the User no way to answer a
question the Manager needs answered, and no way to change what it is doing
without killing it. This is the missing channel, in the smallest form that
works: two directories of timestamped files.

⚠ **THE SUPERVISOR DOES NOT CARE WHO WROTE A MESSAGE**, and that is the
whole of the design's future-proofing. Nothing here records a sender,
checks one, or knows that `rite connect` exists. A file in `in/` is
delivered because it is there. So anything that can write a file — a Slack
adapter, a Discord bot, a cron job, `echo` — attaches by writing the same
files, with no transport layer to build first.

That is deliberately ALL that is done for it. No adapter interface, no
fan-out, no subscriber list: those are the parts that would have to be
guessed at now and rewritten later (`docs/design/V080_RELAY_CHANNELS.md`).

⚠ **READING IS PER-READER, AND THAT IS WHY THE INVARIANT ABOVE SURVIVES.**
The outbox has more than one reader — `rite connect` and, from 0.6.0, a Slack
relay — and the old rule was "delete one once you have relayed it so it is
not shown twice". With two readers that rule loses messages: whoever reads
first deletes, and the other never sees it.

Decision 1 chose a **per-reader cursor**, and the reason it was chosen over
fan-out and acknowledgement is precisely that it does not touch a message.
A cursor records where a READER got to, in a file of its own, keyed by a name
the reader supplies. **Messages stay identity-free**: nothing is written into
them, nothing is copied per subscriber, and a file written by `echo` is still
delivered because it is there. The other two options would each have required
a message to know who had seen it, which reverses the property this module
exists to keep.

**Naming follows `reporting/outbox.py`** — milliseconds first so
`sorted(glob(...))` reads in send order, then pid and a counter to break
ties, because two writers in one millisecond otherwise produce one path
and the second write replaces the first. That was measured on the outbox,
not reasoned about, and this queue has the same shape.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.managers import manager_dir
from rite_ai.names import UnsafeName, require_safe_name
from rite_ai.state import write_atomic

INBOX = "in"
OUTBOX = "out"

_SEQUENCE = itertools.count()


@dataclass(frozen=True)
class Message:
    """One message, either direction."""

    text: str
    timestamp: float
    path: Path


def mailbox_dir(root: Path, manager: str, box: str) -> Path:
    """`.rite/managers/<manager>/mail/<box>/`.

    Under the Manager's own directory, which is where its per-Manager state
    already lives, and inside `.rite/` so it is gitignored: a message to a
    Manager on this machine is not something to commit.
    """
    return manager_dir(root, manager) / "mail" / box


def _cursor_path(root: Path, manager: str, box: str, reader: str) -> Path:
    """Where one reader's position in one box is remembered.

    ⚠ Private deliberately. Nothing outside this module needs a cursor's
    PATH — a reader uses `unread` and `mark_read` — and a public name with
    no caller outside its own file is what `test_no_dead_wiring` exists to
    catch. Exempting it would have been a standing claim that something
    will call it later; nothing will.

    `.rite/managers/<manager>/mail/<box>.read/<reader>.json` — a sibling of
    the box rather than a file inside it, because everything inside a box is
    a message and `read` globs `*.json` there. A cursor living among the
    messages would be delivered as one.
    """
    require_safe_name(reader, kind="mailbox reader")
    return manager_dir(root, manager) / "mail" / f"{box}.read" / f"{reader}.json"


def _cursor(root: Path, manager: str, box: str, reader: str) -> str:
    """The last filename this reader has seen, or "" — never raises.

    A cursor that cannot be read is treated as "has seen nothing", which
    re-delivers rather than drops. A message twice is recoverable; a message
    nobody ever sees is the failure this channel exists to prevent.
    """
    try:
        data = json.loads(_cursor_path(root, manager, box, reader).read_text())
    except (OSError, ValueError, UnsafeName):
        return ""
    return str(data.get("last", "")) if isinstance(data, dict) else ""


def unread(root: Path, manager: str, box: str, reader: str) -> list[Message]:
    """Everything this reader has not seen yet, in send order.

    ⚠ **Reads only. Nothing is deleted and nothing is marked** — call
    `mark_read` once the messages have actually reached the person, so a
    reader that crashes mid-relay re-delivers instead of losing them.
    """
    seen = _cursor(root, manager, box, reader)
    return [m for m in read(root, manager, box) if m.path.name > seen]


def mark_read(root: Path, manager: str, box: str, reader: str, messages) -> None:
    """Advance this reader's cursor past `messages`. Never raises.

    ⚠ **Advances to the LAST message given, not to "now".** A reader that
    was handed three and relayed three moves past three; a message that
    arrives between the read and this call is still unread, which is the
    same window `take` handles the same way and for the same reason.
    """
    if not messages:
        return
    last = max(m.path.name for m in messages)
    if last <= _cursor(root, manager, box, reader):
        return
    try:
        path = _cursor_path(root, manager, box, reader)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps({"last": last}) + "\n")
    except (OSError, UnsafeName):
        # A cursor that cannot be written means the reader sees the same
        # messages again. Annoying and safe; the alternative is losing them.
        pass


def send(root: Path, manager: str, box: str, text: str) -> Path:
    """Put one message in a box. Returns the path written."""
    where = mailbox_dir(root, manager, box)
    where.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    path = where / f"{int(ts * 1000)}_{os.getpid()}_{next(_SEQUENCE)}.json"
    write_atomic(path, json.dumps({"text": text, "timestamp": ts}) + "\n")
    return path


def _as_time(value: object) -> float:
    """A timestamp, or 0.0 when the value is not one.

    ⚠ **`float()` USED TO SIT OUTSIDE THE `try`**, so a `timestamp` that
    was a string, a list or `null` raised out of `read` — which promises it
    never raises — and out of the supervisor's own loop, ending the run. A
    message whose text is readable is worth delivering with a wrong
    ordering key; it is not worth killing the Manager the sender was trying
    to reach.

    0.0 sorts it first, which is harmless: `read` orders by FILENAME, and
    the timestamp is carried for the reader rather than used to sort.
    """
    try:
        when = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return when if when == when else 0.0  # NaN is not a time


def read(root: Path, manager: str, box: str) -> list[Message]:
    """Everything waiting in a box, in send order. Never raises.

    A message that cannot be parsed is SKIPPED rather than failing the
    read: this is called from the supervisor's wait loop, and one bad file
    must not stop a Manager from receiving the others or take the run down.
    """
    where = mailbox_dir(root, manager, box)
    if not where.is_dir():
        return []
    out: list[Message] = []
    for path in sorted(where.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        text = str(data.get("text", "") or "")
        if not text.strip():
            continue
        out.append(Message(text, _as_time(data.get("timestamp")), path))
    return out


def take(root: Path, manager: str, box: str) -> list[Message]:
    """Read a box and clear what was read.

    ⚠ **Deletes only the files it actually read.** A message that arrives
    between the read and the delete is left for the next take rather than
    removed unseen — the window is small and losing a message in it would
    be silent, which is the failure this whole channel exists to avoid.
    """
    messages = read(root, manager, box)
    for message in messages:
        try:
            message.path.unlink()
        except OSError:
            # Left behind rather than fatal: it will be re-delivered, and a
            # message twice is recoverable where a crashed supervisor is not.
            pass
    return messages


def waiting(root: Path, manager: str, box: str) -> bool:
    """Is anything in this box? Cheap enough for a 2-second poll."""
    where = mailbox_dir(root, manager, box)
    return where.is_dir() and any(where.glob("*.json"))


def delivery_note(messages: list[Message]) -> str:
    """The messages, as text to append to a cycle's instruction.

    ⚠ **Appended to the instruction rather than typed into the pane.** The
    engine runs with `-p` and has already read its stdin by the time
    anything could type; the instruction composed for each cycle is the one
    place a Manager reliably reads, so it is the only hook this uses.
    """
    if not messages:
        return ""
    lines = [
        "",
        "---",
        "",
        "## Messages from the User",
        "",
        "These arrived while you were working. Answer them as part of this "
        "turn, and write your reply to the mailbox (see below) so they can "
        "read it.",
        "",
    ]
    lines.extend(f"- {m.text.strip()}" for m in messages)
    return "\n".join(lines)


def how_to_reply(root: Path, manager: str) -> str:
    """Instruction text telling a Manager how to reply.

    ⚠ **A COMMAND, not a format (C5).** This used to hand the Manager a JSON
    shape and a filename pattern to reproduce by hand, and `read` skips a
    file it cannot use — so a reply with a wrong key was written, never
    shown, and nobody told. `--manager` is spelled out for the reason
    `journal.instructions` spells it: a Manager on a tmux without `-e` has no
    `RITE_MANAGER` to default from.
    """
    return (
        "\n\n## Talking to the User\n\n"
        f"To ask the User something or tell them something, run:\n"
        f'  rite reply --manager {manager} "<your message>"\n'
        f"Do not write files into the mailbox yourself. They read your replies "
        f"with `rite connect {manager}`. Messages they send you arrive in your "
        f"instructions at the start of a turn.\n"
    )
