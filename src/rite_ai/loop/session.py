"""Starting, watching and stopping the loop (L-5).

*"I would run a CLI command and it would just run in the background."* Under
tmux, because that is the only persistence rite already has: there is no
`Popen`, no `fork`, no `nohup` anywhere in the package, and every
`subprocess.run` is bounded by a timeout. Two costs, accepted and said out
loud rather than engineered around — it dies with the terminal's tmux server,
and it does not survive a reboot. `status` after a reboot says "not running"
rather than quietly having restarted.

**Stopping does not kill.** `pool/` has no kill path at all, deliberately, and
a kill here would be worse than none: a session killed mid-dispatch leaves a
claim held by a process that no longer exists, which is the §2.6 failure —
caused by the stop command. So `stop` writes a drain signal, the loop finishes
the cycle it is in, takes no new work, and exits. `ManagerMonitor` has carried
a `draining` parameter through to `refusal.refusal_reason` ("shutting down:
…") since Phase 2 with nothing in `src/` ever setting it; this is its first
caller.

**One loop per project, enforced by two different mechanisms** — because they
fail differently. tmux refuses a duplicate session name, which stops the
ordinary `rite loop start` twice. A pid lock stops everything else: a loop
started from a different terminal, from a script, or as `rite loop run
--watch` directly.

**And a worktree is refused outright.** `.rite/` is tracked, so every git
worktree is its own project root with its own `claims.json` while sharing one
board — two loops in two worktrees would each see full capacity and dispatch
against the same queue. The refusal is in `plan_cycle`; this module inherits
it by running cycles.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import write_atomic

DRAIN_FILENAME = "loop-drain"
LOCK_FILENAME = "loop.lock"
LOG_FILENAME = "loop.log"
DEFAULT_INTERVAL = 120.0
"""A Worker session takes minutes to tens of minutes. A five-second cycle
would re-read the same board twenty-four times per useful decision, and the
board has rate limits."""


def session_name(root: Path) -> str:
    """Deterministic per project, for the reason `pool.slot_name` gives: the
    project's name first so `tmux ls` shows something a human is looking for,
    then a path hash for uniqueness."""
    from rite_ai.label import project_slug

    return f"rite-loop-{project_slug(root)}"


def _drain_path(root: Path) -> Path:
    return root / ".rite" / DRAIN_FILENAME


def lock_path(root: Path) -> Path:
    return root / ".rite" / LOCK_FILENAME


def log_path(root: Path) -> Path:
    """Where a backgrounded loop's output goes.

    Without it the only way to see what the loop said was `tmux attach`, and
    the only way to see what a loop that DIED said was nothing at all — tmux
    reaps the pane with the session, so by the time anything notices the exit
    there is nothing left to read. That is how the first version of
    `_why_it_died` came to report "it printed nothing, which usually means
    the command was not executable" about a loop that had printed nine lines
    explaining itself and exited for a perfectly good reason.
    """
    return root / ".rite" / LOG_FILENAME


@dataclass
class Started:
    session: str
    detail: str = ""


@dataclass
class Refused:
    reason: str
    remedy: str = ""


@dataclass
class LoopStatus:
    running: bool = False
    session: str = ""
    pid: int = 0
    draining: str = ""
    detail: str = ""

    def lines(self) -> list[str]:
        if not self.running:
            return [f"loop: not running — {self.detail}"]
        out = [f"loop: running as {self.session} (pid {self.pid or 'unknown'})"]
        if self.draining:
            out.append(
                f"loop: draining — {self.draining}. It will finish the cycle "
                "it is in, take no new work, and exit"
            )
        return out


# --- the drain signal --------------------------------------------------------------


def request_drain(root: Path, reason: str) -> None:
    """Ask the loop to stop after the cycle it is in.

    A reason, not a flag: it travels into `distribute`'s `draining` argument
    and out onto the board as "shutting down: <reason>" on any ticket the
    Manager hands back, where somebody reading the board a week later needs
    to know why.
    """
    write_atomic(_drain_path(root), (reason.strip() or "stop requested") + "\n")


def draining(root: Path) -> str:
    """The drain reason, or "" when none is set. Unreadable counts as SET.

    The asymmetry is deliberate and is the opposite of `read_intents`: a drain
    file that cannot be read is most likely one somebody just wrote, and
    continuing to take work on that guess is the expensive mistake. Stopping
    early costs a restart.
    """
    path = _drain_path(root)
    if not path.exists():
        return ""
    try:
        return path.read_text().strip() or "stop requested"
    except OSError:
        return "stop requested (the drain file could not be read)"


def clear_drain(root: Path) -> None:
    """Called by `start`, never by the loop itself.

    A loop that cleared its own drain signal on exit would race the next
    `start`; a `start` that clears it is a human saying "go" after having
    said "stop", which is exactly the sequence the file records.
    """
    _drain_path(root).unlink(missing_ok=True)


# --- one loop per project ----------------------------------------------------------


def hold_lock(root: Path) -> int | None:
    """Take the loop lock, or return the pid of whoever holds it.

    Deliberately NOT `scheduler.lock`: that one serialises ticks, which are
    seconds long, and a loop holding it for hours would stop every tick in
    the project. Same mechanism, separate file, different lifetime.
    """
    from rite_ai.scheduler.lock import process_is_running

    path = lock_path(root)
    try:
        holder = int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        holder = 0
    if holder and process_is_running(holder):
        return holder
    import os

    write_atomic(path, f"{os.getpid()} {time.time()}\n")
    return None


def release_lock(root: Path) -> None:
    import os

    path = lock_path(root)
    try:
        if int(path.read_text().split()[0]) == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, IndexError):
        return


# --- tmux --------------------------------------------------------------------------


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
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def rite_command() -> str | None:
    """The rite that is running THIS process, not whichever one is on PATH.

    Two reasons, and the first is why `start` used to be a facade. A tmux
    session is handed a command string; if that string is not executable in
    the session's environment the command dies instantly, tmux still reports
    success because the SESSION was created, and `rite loop start` printed
    "started" over nothing. Defaulting to the literal `rite` assumed a PATH
    the caller might not have.

    The second is that `rite` on PATH can be a different checkout entirely —
    an older `uv tool install`, another worktree — so a loop started from
    here could run a different rite than the one that started it, which is
    the hardest kind of difference to notice afterwards.
    """
    import sys

    argv0 = Path(sys.argv[0])
    if argv0.name in ("rite", "rite-ai") and argv0.exists():
        return str(argv0.resolve())
    return shutil.which("rite")


def start(root: Path, *, interval: float = DEFAULT_INTERVAL, command: str = ""):
    """Put `rite loop run --watch` in a detached tmux session.

    Refuses rather than clamping, the way `pool.fill` refuses above the worker
    cap: a second loop in one project is not a smaller version of one loop.
    """
    binary = _tmux()
    if binary is None:
        return Refused(
            "tmux is not installed, and it is the only way rite keeps anything "
            "running in the background",
            remedy="install tmux, or run `rite loop run --watch` in a terminal "
            "you leave open",
        )

    command = command or rite_command() or ""
    if not command:
        return Refused(
            "the `rite` command could not be located, so a tmux session would "
            "start it and it would die immediately",
            remedy="run `rite loop run --watch` directly, or install rite so "
            "`rite` is on PATH",
        )

    name = session_name(root)
    if is_alive(name):
        return Refused(
            f"a loop is already running for this project as {name}",
            remedy=f"`rite loop status`, or `tmux attach -t {name}` to watch it",
        )

    holder = hold_lock(root)
    if holder is not None:
        return Refused(
            f"another loop holds this project's lock (pid {holder})",
            remedy="`rite loop stop`, or wait for it to finish its cycle",
        )
    # Released immediately: the lock belongs to the process that RUNS the
    # loop, and that is the tmux child about to be started, not this command.
    release_lock(root)
    clear_drain(root)

    try:
        done = subprocess.run(
            [
                binary,
                "new-session",
                "-d",
                "-s",
                name,
                "-c",
                str(root),
                # Redirected to a file rather than left in the pane: a pane
                # dies with its session, so the output of a loop that exited
                # — exactly what somebody needs afterwards — is otherwise
                # unreadable by the time anything notices it went.
                f"{command} loop run --watch --interval {int(interval)} "
                f">>{log_path(root)} 2>&1",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return Refused(f"tmux could not start the session: {e}")
    if done.returncode != 0:
        return Refused(
            f"tmux refused to start the session: "
            f"{(done.stderr or done.stdout or '').strip()[:200]}"
        )

    # tmux exits 0 for a session it CREATED, even when the command inside it
    # died on the first line — so its exit code answers "did a session get
    # made", not "is the loop running". Asking the wrong one is how this
    # command printed "started" over nothing, and reported it for hours.
    if not _settled_alive(name):
        return Refused(
            f"the session started and exited immediately. {_why_it_died(root)}",
            remedy=f"run `{command} loop run --watch` directly to see the error",
        )

    return Started(
        name,
        detail=(
            f"a cycle every {int(interval)}s. It dies with this machine's tmux "
            "server and does not survive a reboot — that is deliberate, and "
            "`rite loop status` will say so rather than restarting itself"
        ),
    )


def _settled_alive(name: str, tries: int = 10, pause: float = 0.2) -> bool:
    """Is it STILL there after the window, not merely at some point during it.

    The first version returned True on the first successful poll, so a session
    that died at 0.3s was seen alive at 0.2s and reported as started — the
    same defect it was written to catch, one layer in. A command that fails
    fails in milliseconds; one that works outlives this. Polling rather than
    sleeping once so a dead session is reported quickly rather than after the
    whole window.
    """
    for _ in range(tries):
        time.sleep(pause)
        if not is_alive(name):
            return False
    return True


def _why_it_died(root: Path) -> str:
    """Whatever the dead loop wrote before it went.

    Read from the log rather than from the tmux pane, because the pane is
    gone by the time anybody asks.
    """
    try:
        lines = [ln for ln in log_path(root).read_text().splitlines() if ln.strip()]
    except OSError:
        return (
            "Nothing reached `.rite/loop.log`, which usually means the command "
            "could not be run at all."
        )
    if not lines:
        return (
            "`.rite/loop.log` is empty, which usually means the command could "
            "not be run at all."
        )
    return "Its last words: " + " / ".join(ln.strip()[:120] for ln in lines[-2:])


def status(root: Path) -> LoopStatus:
    name = session_name(root)
    drain = draining(root)
    if not is_alive(name):
        if _tmux() is None:
            return LoopStatus(detail="tmux is not installed, so none was started")
        return LoopStatus(
            detail="no tmux session for this project"
            + (" (a drain was requested)" if drain else "")
        )
    pid = 0
    try:
        pid = int(lock_path(root).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pid = 0
    return LoopStatus(running=True, session=name, pid=pid, draining=drain)


def stop(root: Path, reason: str = "stop requested"):
    """Ask, never kill.

    A killed loop can leave a claim held by a process that no longer exists —
    the failure §2.6 is about, caused by the stop command. Asking costs one
    cycle.
    """
    request_drain(root, reason)
    name = session_name(root)
    if not is_alive(name):
        return Started(
            name,
            detail=(
                "no loop is running; the drain signal is recorded so one "
                "started before it is cleared stops immediately"
            ),
        )
    return Started(
        name,
        detail=(
            "asked it to drain. It finishes the cycle it is in, takes no new "
            f"work, and exits — up to one cycle. Watch with `tmux attach -t {name}`"
        ),
    )
