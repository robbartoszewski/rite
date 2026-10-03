"""Tell the Manager when a Worker has handed back (dogfood, 2026-10-03).

`rite_ai.handback` is the record a Worker writes when it finishes. This is
the half that reads it on the host and makes the Manager learn: the
supervisor runs outside every boundary, so it can write a Manager's inbox,
which the Worker itself cannot (`enclosure._manager_separation`, and the
`PermissionError` that started this — see `rite_ai.handback`).

**The same shape as `worker_questions`, for the same reasons.** That module
carries a Worker's question out of its sandbox; this carries a Worker's
completion. Both are read by the supervisor on a timer, both are written by
rite and not by a model, and both are told once. The difference is who is
told: a question needs the PERSON, so `worker_questions` raises it to them.
A completion needs the MANAGER — it holds the board, the capacity and the
integrate step — so this goes to its inbox and nowhere else. Sending every
finished ticket to the Owner's DM would fill the one channel RP1 exists to
keep scannable with work nobody has to act on.

⚠ **rite does not move the ticket on the board here.** The Manager holds
the board credential and the integrate capability; rite writing a status
behind it would skip the step that checks the work. So the note says
"ready to integrate" and the Manager integrates. What rite does do itself
is stop the Worker reading as stalled (`rite_ai.watchdog`), which is the
part no model should have to infer.

**Told once**, by a ledger in the Manager's own directory — outside the
project, so a Worker cannot mark its own handback as already told and have
it reach nobody (`rite_ai.handback`'s last note). A handback that is
cleared and re-written is a new handback and is told again: the Worker was
started on new work and finished it.
"""

from __future__ import annotations

import json
from pathlib import Path

LEDGER_FILE = "worker-handbacks.json"
"""worker -> the identity of the handback already passed on, so a second
look does not tell the Manager the same completion twice."""


def _identity(record) -> str:
    """What makes this handback a different one from the last.

    The timestamp AND the ticket, not the timestamp alone: a Worker cleared
    and restarted writes a second handback, and keying on the clock would
    have made telling the Manager about it depend on two writes landing on
    different floats.
    """
    return f"{record.timestamp!r}|{record.ticket}|{record.unreadable}"


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

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(data, indent=1, sort_keys=True) + "\n")
    except OSError:
        pass


def _for_the_manager(record) -> str:
    """The note rite puts in the Manager's next instruction."""
    from rite_ai.managers.telling import header

    about = f" on {record.ticket}" if record.ticket else ""
    if record.unreadable:
        return (
            header(f"Worker {record.worker!r} · HANDED BACK, RECORD UNREADABLE")
            + "\n"
            + f"Worker {record.worker!r} wrote a handback and rite could not read "
            f"it ({record.unreadable}). It has finished or tried to: do NOT "
            "restart it on the assumption that it is hung, and do not treat "
            "this as nothing having been said. Look at its branch and its "
            f"workspace, and at `rite status`, before deciding."
        )
    branch = (
        f"\nBranch, as the Worker reported it: `{record.branch}`"
        if record.branch
        else ""
    )
    summary = (
        "\nWhat it says it did:\n"
        + "\n".join(f"> {line}" for line in record.summary.splitlines())
        if record.summary
        else ""
    )
    return (
        header(f"Worker {record.worker!r} · HANDED BACK — READY TO INTEGRATE")
        + "\n"
        + f"Worker {record.worker!r} has finished{about} and handed its work "
        "back. It is free, and it is NOT hung: it will stop beating from now "
        "on, and that silence is expected — do not restart it, and do not "
        "force-release its claims as if it had died. Its claim is held "
        "deliberately until the work lands."
        + branch
        + summary
        + "\n\nThe branch and the summary above are the Worker's own claim, "
        "not something rite checked. Integrate it (or give that Worker the "
        "next ticket, which is what frees its slot)."
    )


def surface(root: Path, manager: str, say) -> int:
    """Tell `manager`, once, about each Worker that has handed back.

    Returns how many were told. Never raises into the supervisor: a failure
    is said and the next look tries again, said once per reason rather than
    every tick.
    """
    try:
        return _surface(root, manager, say)
    except Exception as e:  # noqa: BLE001 - a watcher must not end the run
        _say_once(root, manager, say, f"could not check Workers for a handback: {e}")
        return 0


def _say_once(root: Path, manager: str, say, line: str) -> None:
    """Say `line` unless it is what was said last time."""
    path = _ledger_path(root, manager)
    told = _load(path)
    if told.get("_last_problem") == line:
        return
    say(line)
    told["_last_problem"] = line
    _store(path, told)


def _surface(root: Path, manager: str, say) -> int:
    from rite_ai import handback
    from rite_ai.config.parse import load_project
    from rite_ai.managers.mailbox import INBOX, send

    root = Path(root)
    project = load_project(root)
    if isinstance(project, list):
        return 0
    path = _ledger_path(root, manager)
    told = _load(path)
    count = 0
    names = [w.name for w in project.workers]
    found = handback.read_all(root, names)
    # A worker whose handback has been cleared is dropped from the ledger:
    # the entry records what was told about a record that no longer exists.
    for gone in [w for w in told if w != "_last_problem" and w not in found]:
        told.pop(gone)
        _store(path, told)
    for worker, record in sorted(found.items()):
        if told.get(worker) == _identity(record):
            continue
        try:
            send(root, manager, INBOX, _for_the_manager(record))
        except OSError as e:
            _say_once(
                root,
                manager,
                say,
                f"Worker {worker!r} handed back and {manager!r} could not be "
                f"told ({e}); it will be tried again",
            )
            continue
        told[worker] = _identity(record)
        _store(path, told)
        say(f"Worker {worker!r} {record.describe()}: told {manager!r}")
        count += 1
    return count
