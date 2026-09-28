"""The User's instructions as rite delivered them, kept outside the boundary.

**Why this exists (TR9).** Every piece of Worker work carries a ticket, and a
User instruction that is not already a ticket becomes a chore whose text is
the User's own words. "The User's own words" has to be something rite can
produce without a model: the Manager asks for a chore by naming a delivered
message, and rite writes the ticket from what it recorded here. So the
Manager cannot pass off its own text as the User's.

⚠ **Needed because delivery deletes.** `mailbox.take` removes each inbox
file as it is delivered, so after a cycle starts nothing on disk says what
the Manager was told. This ledger is written at that same moment, by the
supervisor, in the `routing/` directory beside the Manager's mail, which its
profile does not grant (`routing._ledger_dir`).

## Which messages count as the User's

Only two, and decided from rite's own header, never from the text a person
typed:

* **the Owner's DM, marked INSTRUCTION** (`slack.Listener`): the typed text
  is every line after the header, each quoted `> ` by `slack._quoted`, so a
  typed line cannot forge the header;
* **a message with no rite header**: written from this machine (`rite
  message`, `rite connect`). A Manager cannot write an inbox, its own
  included (`enclosure._manager_separation`), so a header-less message is the
  person's.

⚠ **"INSTRUCTION" is not enough on its own.** A route is delivered to a
secondary as `[routed by the Owner Manager 'lead' · … · INSTRUCTION]`, and
that text is the Owner's, not the User's. Anything that is not exactly one
of the two shapes above is not choosable, and says why.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import write_atomic

LEDGER_FILE = "instructions.json"
KEPT_SECONDS = 14 * 24 * 3600.0
KEPT_MAX = 500
OWNERS_DM = "Owner's DM"
INSTRUCTION = "INSTRUCTION"
THIS_MACHINE = "this machine"


@dataclass(frozen=True)
class Heard:
    """One delivered message, as far as whose words it carries."""

    users: bool
    """Whether the words are the User's (see the module docstring)."""
    words: str
    """The typed text, unquoted; "" when `users` is False."""
    where: str
    """`OWNERS_DM`, `THIS_MACHINE`, or rite's header as delivered."""
    why_not: str = ""


def _header_parts(line: str) -> list[str] | None:
    """rite's bracketed header split at ` · `, or None if `line` is not one.

    Every header rite writes has at least two parts (`slack._header`,
    `routing._routed_message`, `routing._report_message`, `routing._note`),
    so a bracketed line with no ` · ` is text a person typed, like `[wip]`.
    """
    line = line.strip()
    if not (line.startswith("[") and line.endswith("]")):
        return None
    parts = line[1:-1].split(" · ")
    return parts if len(parts) >= 2 else None


def classify(text: str) -> Heard:
    """Whose words `text` carries, from rite's header alone."""
    lines = text.splitlines()
    first = lines[0] if lines else ""
    parts = _header_parts(first)
    if parts is None:
        return Heard(True, text.strip(), THIS_MACHINE)
    if parts[0] != OWNERS_DM or parts[-1] != INSTRUCTION:
        return Heard(
            False,
            "",
            first.strip(),
            "it is not the User's instruction: only a message from the "
            "Owner's DM marked INSTRUCTION, or one sent from this machine, is",
        )
    body = lines[1:]
    # Every typed line is quoted by the relay. A line that is not is not the
    # User's, and the message is refused whole rather than partly quoted.
    if not body or not all(line.startswith("> ") or line == ">" for line in body):
        return Heard(
            False,
            "",
            OWNERS_DM,
            "its text is not in the shape the Slack relay writes, so rite "
            "cannot say which part the User typed",
        )
    words = "\n".join(line[2:] for line in body).strip()
    if not words:
        return Heard(False, "", OWNERS_DM, "it is empty")
    return Heard(True, words, OWNERS_DM)


def message_id(path: Path) -> str:
    """A delivered message's id: its inbox filename without `.json`.

    Unique by construction (`mailbox.send`: milliseconds, pid, counter), and
    in send order, which is what makes it a usable name for the Manager.
    """
    return path.stem


def _path(root: Path, manager: str) -> Path:
    from rite_ai.managers.routing import _ledger_dir

    return _ledger_dir(root, manager) / LEDGER_FILE


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(root: Path, manager: str, messages, *, now: float | None = None) -> int:
    """Keep every delivered message that carries the User's words.

    Called by the supervisor with what `take` just removed, BEFORE the cycle
    starts. Returns how many were recorded. Keyed by id, so a message put
    back and delivered again (`mailbox.put_back`) is one entry, not two.

    ⚠ **One writer.** Only this Manager's supervisor writes this file, and
    the run lock (`github_access.hold_run`) makes it one process per Manager.
    """
    when = time.time() if now is None else now
    path = _path(root, manager)
    kept = _load(path)
    added = 0
    for message in messages:
        heard = classify(message.text)
        if not heard.users:
            continue
        kept[message_id(message.path)] = {
            "words": heard.words,
            "where": heard.where,
            "sent_at": message.timestamp,
            "delivered_at": when,
        }
        added += 1
    if not added:
        return 0
    fresh = {
        k: v
        for k, v in kept.items()
        if isinstance(v, dict)
        and when - float(v.get("delivered_at", 0)) <= KEPT_SECONDS
    }
    newest = dict(sorted(fresh.items())[-KEPT_MAX:])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(newest, indent=1, sort_keys=True) + "\n")
    return added


def lookup(root: Path, manager: str, ids: list[str]) -> tuple[list[dict], str]:
    """The recorded entries for `ids`, in the order given, or a refusal.

    All or nothing: a chore quoting some of what it was asked to quote is a
    ticket whose text is not what the Manager asked for.
    """
    kept = _load(_path(root, manager))
    found: list[dict] = []
    for wanted in ids:
        entry = kept.get(wanted)
        if not isinstance(entry, dict) or not str(entry.get("words", "")).strip():
            return [], (
                f"message {wanted!r} is not a User's instruction delivered to "
                f"{manager!r} in the last {int(KEPT_SECONDS // 86400)} days. "
                "Only a message from the Owner's DM marked INSTRUCTION, or one "
                "sent from this machine, can become a chore; name it by the id "
                "shown beside it in your instruction"
            )
        found.append({"id": wanted, **entry})
    return found, ""
