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


def send(root: Path, manager: str, box: str, text: str) -> Path:
    """Put one message in a box. Returns the path written."""
    where = mailbox_dir(root, manager, box)
    where.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    path = where / f"{int(ts * 1000)}_{os.getpid()}_{next(_SEQUENCE)}.json"
    write_atomic(path, json.dumps({"text": text, "timestamp": ts}) + "\n")
    return path


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
        out.append(Message(text, float(data.get("timestamp", 0.0)), path))
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
    """Instruction text telling a Manager where to put its replies."""
    out = mailbox_dir(root, manager, OUTBOX)
    return (
        "\n\n## Talking to the User\n\n"
        f"To ask the User something or tell them something, write a file to "
        f'`{out}` containing JSON `{{"text": "...", "timestamp": '
        f"<unix seconds>}}`, named `<milliseconds>_<pid>_<n>.json`. They "
        f"read it with `rite connect {manager}`. Messages they send you "
        f"arrive in your instructions at the start of a turn.\n"
    )
