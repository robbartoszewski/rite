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


LAST_PROBLEM = "_last_problem"
"""Where the once-only problem line is kept, beside `TOLD` and not inside it.

⚠ **Checked, not assumed.** The first version kept the worker entries at the
top level alongside this key and said a worker could not be called
`_last_problem` because `require_safe_name` refuses a leading underscore. It
does not — `require_safe_name("_last_problem")` is accepted — so the two
namespaces really could collide. Measured rather than reasoned, and the fix
is the nesting below rather than a correction to the sentence."""

TOLD = "told"
"""worker -> `_identity` of the handback already passed on."""


def _entries(data: dict) -> dict:
    """The worker entries, from either shape this ledger has had.

    The first version kept them at the top level beside `_last_problem`;
    they moved under `TOLD` when that collision turned out to be real. A
    ledger in the old shape is read rather than ignored — ignoring it told
    every outstanding completion to the Manager a second time on upgrade,
    which the terminating check measured — and it is rewritten in the new
    shape by the next `_store`, so the old keys do not accumulate."""
    told = data.get(TOLD)
    if isinstance(told, dict):
        return dict(told)
    return {
        k: v
        for k, v in data.items()
        if k != LAST_PROBLEM and k != TOLD and isinstance(v, str)
    }


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


def _store(path: Path, data: dict) -> str:
    """Write the ledger. Returns "" on success, or why it could not be.

    ⚠ **It used to swallow `OSError` and return None, which turned "told
    once" into "told every tick".** Measured in review with the write
    blocked: four `surface()` calls put four identical notes in the
    Manager's inbox for one completion, and nothing said why. The module
    docstring promises once; a promise kept by a file that may silently fail
    to be written is not kept. `worker_questions._store` swallows the same
    way and has the same hole — noted rather than changed here, because that
    is its module's call to make.
    """
    from rite_ai.state import write_atomic

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(data, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        return f"{path.name} could not be written: {e}"
    return ""


def _one_line(value: str) -> str:
    """Worker-authored text, flattened so it cannot forge rite's header.

    ⚠ **Belt AND braces, and both are deliberate.** `handback.write` refuses
    a ticket or branch carrying a newline, which is the mechanical guard and
    the one that matters. This is the second: a record written by an older
    rite, or by hand into `.rite/` — which every Worker's sandbox can write
    — never went through that refusal. `slack._quoted` states the rule both
    serve: a typed line must not be able to forge rite's own header, because
    the reader of this note is a model reading all of it.
    """
    return " ".join(str(value).split())


def _for_the_manager(record) -> str:
    """The note rite puts in the Manager's next instruction."""
    from rite_ai.managers.telling import header

    ticket = _one_line(record.ticket)
    about = f" on {ticket}" if ticket else ""
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
    reported = _one_line(record.branch)
    branch = f"\nBranch, as the Worker reported it: `{reported}`" if reported else ""
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


def surface(root: Path, manager: str, say, *, every_worker: bool = True) -> int:
    """Tell `manager`, once, about each Worker that has handed back.

    `every_worker` false restricts it to the Workers that name `manager` as
    theirs; true (the default) also includes Workers that name no Manager at
    all, so a project where nobody is nominated still has somebody told. See
    `_mine` for why that direction is the generous one.

    Returns how many were told. Never raises into the supervisor: a failure
    is said and the next look tries again, said once per reason rather than
    every tick.
    """
    try:
        return _surface(root, manager, say, every_worker=every_worker)
    except Exception as e:  # noqa: BLE001 - a watcher must not end the run
        _say_once(root, manager, say, f"could not check Workers for a handback: {e}")
        return 0


def _say_once(root: Path, manager: str, say, line: str) -> None:
    """Say `line` unless it is what was said last time.

    For the failures OUTSIDE `_surface`'s own ledger pass — it loads the
    ledger itself and writes its own key, so calling this in the middle of
    that pass loses one of the two writes."""
    path = _ledger_path(root, manager)
    told = _load(path)
    if told.get(LAST_PROBLEM) == line:
        return
    say(line)
    told[LAST_PROBLEM] = line
    _store(path, told)


def _mine(project, manager: str, every_worker: bool) -> list[str]:
    """The Workers whose handback `manager` is the one to be told about.

    ⚠ **A Worker's OWN Manager, not whoever holds `route`.** Review found
    the first version told the routing owner about every Worker, and the
    reason this goes to a Manager rather than to the Owner's DM is that the
    Manager holds the board, the capacity and the integrate step — all of
    which belong to the Manager that assigned the ticket. So `helper`'s
    Worker finishing told `lead`, which cannot integrate it, and left
    `helper`, which can, never told.

    `every_worker` is the fallback for a project where no Worker names a
    Manager, or where nothing identifies one Manager as the teller. It is
    deliberately generous in that direction: review measured a configuration
    (`manager_roles` with no single `route` holder) in which NOBODY was told
    while the watchdog still suppressed the stall — a Worker silent, not
    reported stalled, and nobody informed, which is strictly worse than
    before this existed. Told twice is recoverable; told to nobody is the
    defect.
    """
    named = [w.name for w in project.workers if getattr(w, "manager", "") == manager]
    if not every_worker:
        return named
    # ⚠ **ADDED, not an alternative to `named`.** The first repair returned
    # early when this Manager had Workers of its own, so on a project where
    # the routing owner owns any Worker, an unassigned one reached NOBODY —
    # which is the very case this function was rewritten to close. Measured
    # on two Workers (`lead` owns w1, w2 names no Manager): w2's handback was
    # told to neither Manager while the watchdog went on suppressing its
    # stall. Told twice is recoverable; told to nobody is the defect.
    unassigned = [w.name for w in project.workers if not getattr(w, "manager", "")]
    return named + unassigned


def _surface(root: Path, manager: str, say, *, every_worker: bool = True) -> int:
    from rite_ai import handback
    from rite_ai.config.parse import load_project
    from rite_ai.managers.mailbox import INBOX, send

    root = Path(root)
    project = load_project(root)
    if isinstance(project, list):
        return 0
    path = _ledger_path(root, manager)
    # ⚠ ONE dict, loaded once and written through. The first version called
    # `_say_once` on a failure, which re-reads the ledger and writes its own
    # `_last_problem` key, and then wrote this stale dict back over it —
    # dropping the mark, so the "could not be told" line was re-said every
    # tick, which is what the once-only rule exists to stop.
    ledger = _load(path)
    told = _entries(ledger)
    count = 0
    names = _mine(project, manager, every_worker)
    found = handback.read_all(root, names)
    # A worker whose handback has been cleared is dropped from the ledger:
    # the entry records what was told about a record that no longer exists.
    gone = [w for w in told if w not in found]
    for worker in gone:
        told.pop(worker)
    for worker, record in sorted(found.items()):
        if told.get(worker) == _identity(record):
            continue
        try:
            send(root, manager, INBOX, _for_the_manager(record))
        except OSError as e:
            line = (
                f"Worker {worker!r} handed back and {manager!r} could not be "
                f"told ({e}); it will be tried again"
            )
            if ledger.get(LAST_PROBLEM) != line:
                say(line)
                ledger[LAST_PROBLEM] = line
            continue
        told[worker] = _identity(record)
        # A problem that has stopped standing is not a record worth keeping:
        # left set, a failure that recurred after recovering was said only
        # the first time, for the life of the ledger.
        ledger.pop(LAST_PROBLEM, None)
        say(f"Worker {worker!r} {record.describe()}: told {manager!r}")
        count += 1
    if gone or count or ledger.get(LAST_PROBLEM) or TOLD not in ledger:
        ledger[TOLD] = told
        failed = _store(path, ledger)
        if failed:
            # ⚠ Said, and said as what it costs. The note HAS reached the
            # Manager; what has not been recorded is that it did, so the
            # next look will send it again.
            say(
                f"could not record that {manager!r} was told about a handback "
                f"({failed}) — it will be told again, possibly repeatedly, "
                "until this is fixed"
            )
    return count
