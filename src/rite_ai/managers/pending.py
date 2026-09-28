"""Did this reach a human? What needs the person stays PENDING until it has
(RP1 piece 2, delivery confirmation).

Robert, 2026-09-28: "did this actually reach a human" must be an observable
property, not an assumption. The coordinator named it the part of RP1 that
matters most. What prompted it: questions that sat in a message that never
arrived, and a Worker's question written to a file nothing reads. In both,
every part of the system believed it had asked.

**What is tracked.** Every outbox message that needs the person
(`mailbox._needs_action`): a question, anything with no recorded kind, and a
check-in that holds questions. A reply is reading and is not tracked.

**What confirms one** (and nothing else does):

* the Owner's reply in the Slack thread under it (any person's, when the
  project has no Owner), read by the relay (`slack.Listener`);
* the Owner's reaction to it, **only when the app has `reactions:read`**; the
  relay says once when it does not, and then only a reply confirms;
* with no Slack relay, the message shown to a person by `rite replies` at the
  terminal. That says it was SEEN, which is what "reached a human" asks; it is
  recorded as that and never as "answered".

A message posted to Slack is NOT confirmed by being posted: a post reaches a
channel, not a person. That distinction is the point of this module.

**What an unconfirmed one does:** it comes back at every check-in
(`checkins._deliver_checkin`), with how long it has waited and where it went,
until something above confirms it.

⚠ **ON UPGRADE, what is already in the outbox is recorded as predating this
tracking, not as waiting**: nothing can say whether an older message reached
anyone, and listing a month of them at the first check-in would bury the one
that matters. `_first_sync_note` says so, once.

⚠ **Stated, not reassuring:** the ledger lives in the Manager's own
directory, which the Manager can write (MM8 grants it). A Manager can hide
its own questions from this by editing it. That removes a Manager's own
questions from its own reminder list and nothing else; a Manager that wants
to hide a question can also simply not ask it.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import write_atomic

LEDGER_FILENAME = "pending.json"
FIRST_LINE_CHARS = 120

BY_THREAD_REPLY = "a reply in its Slack thread"
BY_REACTION = "a reaction in Slack"
BY_TERMINAL = "shown by `rite replies` at the terminal"
PREDATES = "predates delivery tracking (rite 0.7.0)"


@dataclass(frozen=True)
class Item:
    name: str
    """The outbox file's name, which is the message's identity."""
    at: float
    first: str
    kind: str
    channel: str = ""
    ts: str = ""
    confirmed_how: str = ""
    confirmed_at: float = 0.0

    @property
    def confirmed(self) -> bool:
        return bool(self.confirmed_how)


def _path(root: Path, manager: str) -> Path:
    from rite_ai.managers import manager_dir

    return manager_dir(root, manager) / LEDGER_FILENAME


@contextmanager
def _locked(root: Path, manager: str):
    """One writer at a time: the relay, the check-in and `rite replies` all
    write, from different processes."""
    path = _path(root, manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield _load(path)
    finally:
        os.close(fd)


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(root: Path, manager: str, data: dict) -> None:
    write_atomic(_path(root, manager), json.dumps(data, indent=1) + "\n")


def _tracked(root: Path, manager: str, message) -> bool:
    from rite_ai.managers.mailbox import CHECKIN, _needs_action

    if not _needs_action(message):
        return False
    if message.kind == CHECKIN:
        from rite_ai.managers import checkins

        return checkins.held_questions(root, manager, message.path.name) > 0
    return True


def sync(root: Path, manager: str, *, now: float | None = None) -> str:
    """Record every message in the outbox that needs the person and is not
    recorded yet. Returns a line to say, the first time only (see the module
    note on upgrading), else ""."""
    from rite_ai.managers.mailbox import OUTBOX, read

    messages = [m for m in read(root, manager, OUTBOX) if _tracked(root, manager, m)]
    with _locked(root, manager) as data:
        first = "items" not in data
        items = data.setdefault("items", {})
        added = 0
        for m in messages:
            if m.path.name in items:
                continue
            lines = m.text.strip().splitlines() or [""]
            entry = {
                "at": m.timestamp,
                "first": lines[0][:FIRST_LINE_CHARS],
                "kind": m.kind,
            }
            if first:
                entry["confirmed"] = {"how": PREDATES, "at": now or time.time()}
            items[m.path.name] = entry
            added += 1
        # Bounded: a CONFIRMED entry goes once its message has left the
        # outbox (mailbox retention). An unconfirmed one is never dropped;
        # it is what the check-in lists.
        present = {m.path.name for m in read(root, manager, OUTBOX)}
        gone = [
            name
            for name, entry in items.items()
            if name not in present
            and isinstance(entry, dict)
            and entry.get("confirmed")
        ]
        for name in gone:
            del items[name]
        if first or added or gone:
            _save(root, manager, data)
    if first and added:
        return _first_sync_note(manager, added)
    return ""


def _first_sync_note(manager: str, count: int) -> str:
    return (
        f"delivery tracking: {count} message(s) already in {manager!r}'s outbox "
        "predate it, so nothing can say whether they reached anyone. They are "
        "recorded as that, not listed as waiting; from now on what needs you "
        "stays pending until you answer it"
    )


def posted(root: Path, manager: str, name: str, channel: str, ts: str) -> None:
    """Where the relay put it, so its thread can be read for the answer."""
    with _locked(root, manager) as data:
        entry = data.get("items", {}).get(name)
        if entry is None:
            return
        entry["where"] = {"channel": channel, "ts": ts}
        _save(root, manager, data)


def confirm(root: Path, manager: str, name: str, how: str, *, at: float) -> bool:
    """Record that `name` reached a person, and how. False when it was not
    tracked or was already confirmed (the first confirmation is kept)."""
    with _locked(root, manager) as data:
        entry = data.get("items", {}).get(name)
        if entry is None or entry.get("confirmed"):
            return False
        entry["confirmed"] = {"how": how, "at": at}
        _save(root, manager, data)
        return True


def items(root: Path, manager: str) -> list[Item]:
    data = _load(_path(root, manager))
    out: list[Item] = []
    for name, entry in (data.get("items") or {}).items():
        if not isinstance(entry, dict):
            continue
        where = entry.get("where") or {}
        confirmed = entry.get("confirmed") or {}
        out.append(
            Item(
                name=name,
                at=float(entry.get("at") or 0.0),
                first=str(entry.get("first") or ""),
                kind=str(entry.get("kind") or ""),
                channel=str(where.get("channel") or ""),
                ts=str(where.get("ts") or ""),
                confirmed_how=str(confirmed.get("how") or ""),
                confirmed_at=float(confirmed.get("at") or 0.0),
            )
        )
    return sorted(out, key=lambda i: (i.at, i.name))


def waiting(root: Path, manager: str) -> list[Item]:
    """What needs the person and has not been confirmed to reach one."""
    return [i for i in items(root, manager) if not i.confirmed]


def _age(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


def checkin_lines(root: Path, manager: str, *, now: float) -> list[str]:
    """The check-in's section: every unconfirmed item, oldest first."""
    sync(root, manager, now=now)
    left = waiting(root, manager)
    if not left:
        return []
    lines = [
        "",
        f"Still waiting for you ({len(left)}), asked and not yet answered:",
    ]
    for i in left:
        where = (
            "posted to Slack, no reply in its thread yet"
            if i.ts
            else "not posted to Slack"
        )
        lines.append(f"- {_age(now - i.at)} ago, {where}: {i.first}")
    lines.append(
        "  Answer in the message's own Slack thread, so rite can tell it was "
        "answered; a message elsewhere cannot be matched to the question."
    )
    return lines
