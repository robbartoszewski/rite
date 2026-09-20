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


def pane_pid(name: str) -> int:
    """The pid of the process tmux is actually running, not ours.

    ⚠ A draft recorded `os.getpid()` — the rite CLI's own pid, which is dead
    seconds later and recyclable. Measured: recorded 41437 where the pane was
    41470. `loop/session.py` asks tmux, and so does this. A recorded value
    that names a different process than the one it claims to name is worse
    than no value, because it reads as evidence.
    """
    binary = _tmux()
    if binary is None:
        return 0
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, "#{pane_pid}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if done.returncode != 0:
        return 0
    try:
        return int((done.stdout or "").strip())
    except ValueError:
        return 0


@dataclass
class StartResult:
    ok: bool
    message: str
    session: str = ""
    attach: str = ""


def _tmux() -> str | None:
    return shutil.which("tmux")


@dataclass(frozen=True)
class Liveness:
    """Whether a session is running, and whether we could find out.

    ⚠ **Three answers, not two, and the third is why this type exists.**
    A draft of this module returned a bare bool: `has-session` timing out or
    failing to run returned False, so "could not ask" read as "not running"
    — and the duplicate check then let a second PAID Manager session start.
    That is the conflation `worker_sandbox_status` already fixed with
    `known=False`, reintroduced here.

    Nothing bad happened in practice, and the reason is worth writing down
    because it is load-bearing and was nobody's intention: `session_name` is
    deterministic, so a second start collides on the name and tmux refuses
    it. **The safety property was resting on naming.** Add a disambiguating
    suffix or a retry and the protection disappears with nothing to notice.
    """

    alive: bool
    known: bool
    detail: str = ""


def liveness(name: str) -> Liveness:
    binary = _tmux()
    if binary is None:
        return Liveness(False, known=False, detail="tmux not found")
    try:
        done = subprocess.run(
            [binary, "has-session", "-t", name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Liveness(False, known=False, detail="`tmux has-session` timed out")
    except (OSError, subprocess.SubprocessError) as e:
        return Liveness(False, known=False, detail=f"`tmux has-session` failed: {e}")
    return Liveness(done.returncode == 0, known=True)


def is_alive(name: str) -> bool:
    """Alive, treating "could not ask" as not alive.

    ⚠ Safe ONLY where a false negative is cheap. The duplicate check must
    use `liveness()` instead, because there a false negative starts a second
    paid session — §5.1.1: a safety property may fail closed, never open.
    """
    return liveness(name).alive


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
    if not liveness(instance.session).alive:
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

    if max_sessions <= 0:
        return StartResult(
            False,
            f"refusing to start: a ceiling of {max_sessions} permits no "
            "sessions at all, so the Manager would start and immediately "
            "have nothing it may do. Pass a positive --sessions.",
        )

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

    # FAIL CLOSED before anything else: if we cannot establish whether a
    # session is already running, refuse. A draft of this checked `is_alive`,
    # which answers False when it cannot ask, so a timeout permitted a second
    # PAID session (D-74, §5.1.1).
    here = liveness(name)
    if not here.known:
        return StartResult(
            False,
            f"cannot tell whether Manager '{manager}' is already running "
            f"({here.detail}), so starting one could make two — refused. "
            f"Check with `tmux ls` and try again.",
        )

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
    if here.alive:
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
        # Truncated, as `loop/session.py` truncates: tmux can emit a great
        # deal on failure and a refusal nobody can read is a refusal nobody
        # acts on.
        detail = (done.stderr or done.stdout or "").strip()[:200]
        return StartResult(False, f"tmux refused to start the session: {detail}")

    if not settled_alive(name):
        return StartResult(
            False,
            f"the session started and exited immediately, so `{launch}` is "
            f"not running. `tmux new-session` reports whether a session was "
            f"CREATED, not whether the command in it survived. Run "
            f"`{launch}` directly to see why.",
        )

    record_instance(
        root,
        ManagerInstance(
            name=manager,
            session=name,
            # tmux's pane pid, not ours. See `pane_pid`.
            pid=pane_pid(name),
            # What actually ran, not what was configured: `launch` is
            # `command or engine or "claude"`, so an explicit command means
            # the configured engine is not what is running.
            engine=launch,
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
