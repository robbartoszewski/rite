"""Starting a Manager session, and refusing a second one.

**A NEW tmux starter following `loop/session.py`'s pattern, not a call into
it.** That module is hard-wired to the loop — `session_name` builds
`rite-loop-<slug>` and `start` shells `{command} loop run --watch` — so
reusing it would mean parameterising a working thing to serve a second
caller. What IS reused is its shape, which was arrived at by fixing the
facade twice: exit 0 from `tmux new-session` means a session was CREATED,
not that the command in it runs, so a settle-and-verify is mandatory.

**The refusal fails CLOSED (D-74).** `tmux has-session` answers "does a
session exist", not "is the command inside running", and it returns false
when tmux is missing or the call times out — so under a refusal rule an
unanswerable check would permit two PAID Manager sessions. §5.1.1: a safety
property may fail closed, never open.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.label import project_slug
from rite_ai.managers import (
    ManagerInstance,
    manager_dir,
    read_instance,
    record_instance,
)
from rite_ai.names import name_problem

SETTLE_TRIES = 10
SETTLE_PAUSE = 0.2


def session_name(root: Path, manager: str) -> str:
    """`rite-mgr-<project>-<manager>`. Project first so `tmux ls` sorts into
    something a human is scanning for, manager last so two Managers in one
    project are adjacent and distinguishable."""
    return f"rite-mgr-{project_slug(root)}-{manager}"


@dataclass
class StartResult:
    ok: bool
    message: str
    session: str = ""
    attach: str = ""


def _tmux() -> str | None:
    return shutil.which("tmux")


def is_alive(name: str) -> bool:
    binary = _tmux()
    if binary is None:
        return False
    try:
        done = subprocess.run(
            [binary, "has-session", "-t", name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def settled_alive(name: str, tries: int = SETTLE_TRIES, pause: float = SETTLE_PAUSE):
    """Still there after the window, not merely at some point during it.

    Required at EVERY poll rather than any: a first version of the loop's
    equivalent returned True on the first successful poll, so a session that
    died at 0.3s was seen alive at 0.2s and reported as started — the defect
    it was written to catch, one layer in.
    """
    for _ in range(tries):
        time.sleep(pause)
        if not is_alive(name):
            return False
    return True


def running(root: Path, manager: str) -> ManagerInstance | None:
    """The live instance for this Manager, or None.

    Both halves are required and neither is sufficient. A recorded instance
    whose session is gone is stale — the ordinary morning state, since this
    design's usual ending is an ungraceful terminal close. A live session
    with no record is somebody else's tmux session that happens to match a
    name, which is not a Manager this project started.
    """
    instance = read_instance(root, manager)
    if instance is None:
        return None
    if not is_alive(instance.session):
        return None
    return instance


def start(
    root: Path,
    manager: str,
    *,
    engine: str = "",
    command: str = "",
    max_sessions: int = 0,
    window_seconds: float = 0.0,
) -> StartResult:
    """Start one Manager session, or refuse and say why.

    `max_sessions` is mandatory at the caller (D-68) and is a COUNT of
    session starts, not spend — §2.6.1 says rite cannot read the quota and
    D-38 forbids the path from measurement back to control (D-69).
    """
    problem = name_problem(manager, kind="manager name")
    if problem:
        return StartResult(False, f"refusing to start: {problem}")

    binary = _tmux()
    if binary is None:
        # FAIL CLOSED: without tmux the duplicate check cannot run, and a
        # check that cannot run must refuse rather than permit (D-74).
        return StartResult(
            False,
            "tmux not found, so a running Manager cannot be detected and a "
            "second one could be started by accident — refusing. Install "
            "tmux, or start the session yourself and record it.",
        )

    name = session_name(root, manager)
    existing = running(root, manager)
    if existing is not None:
        return StartResult(
            False,
            f"Manager '{manager}' is already running as {existing.session} "
            f"(pid {existing.pid}). Reach it with: tmux attach -t "
            f"{existing.session}",
            session=existing.session,
            attach=f"tmux attach -t {existing.session}",
        )
    if is_alive(name):
        # A session under our name with no usable record. Refuse rather than
        # adopt: recording it would claim this project started something it
        # did not, and killing it would destroy work nobody asked about.
        return StartResult(
            False,
            f"a tmux session named {name} already exists but this project "
            f"has no record of it. Look at it (`tmux attach -t {name}`) and "
            f"either use it or remove it — rite will not adopt or kill it.",
        )

    manager_dir(root, manager).mkdir(parents=True, exist_ok=True)
    launch = command or engine or "claude"
    try:
        done = subprocess.run(
            [binary, "new-session", "-d", "-s", name, launch],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            cwd=str(root),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return StartResult(False, f"could not start a session: {e}")
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        return StartResult(False, f"tmux refused to start the session: {detail}")

    if not settled_alive(name):
        return StartResult(
            False,
            f"the session started and exited immediately, so `{launch}` is "
            f"not running. `tmux new-session` reports whether a session was "
            f"CREATED, not whether the command in it survived. Run "
            f"`{launch}` directly to see why.",
        )

    import os

    record_instance(
        root,
        ManagerInstance(
            name=manager,
            session=name,
            pid=os.getpid(),
            engine=engine,
            max_sessions=max_sessions,
            window_seconds=window_seconds,
        ),
    )
    attach = f"tmux attach -t {name}"
    return StartResult(
        True,
        f"Manager '{manager}' started as {name}.\n  reach it with: {attach}",
        session=name,
        attach=attach,
    )
