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


def _age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.1f}d"


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
    """The process tmux is running, from tmux. What a person would kill."""
    lock_holder: int = 0
    """The pid in the lock file. Normally the same; different means something
    else holds this project's loop lock, which is worth saying rather than
    quietly preferring one of them."""
    uptime: float = 0.0
    draining: str = ""
    detail: str = ""

    def lines(self) -> list[str]:
        if not self.running:
            return [f"loop: not running — {self.detail}"]
        age = f", up {_age(self.uptime)}" if self.uptime else ""
        out = [f"loop: running as {self.session} (pid {self.pid or 'unknown'}){age}"]
        if self.lock_holder and self.pid and self.lock_holder != self.pid:
            # Two processes think they are this project's loop, or one left a
            # lock behind. Either way a reader deciding what to stop needs to
            # know before they act, not after.
            out.append(
                f"loop: ⚠ the loop lock is held by pid {self.lock_holder}, not "
                f"by the session's {self.pid} — check for a second loop"
            )
        out.append(f"loop:   watch it:  tmux attach -t {self.session}")
        out.append("loop:   stop it:  rite loop stop")
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


def running_pid(root: Path) -> int:
    """The pid of the loop running for `root`, or 0. NEVER spawns a process.

    Three callers needed this answer and two of them had written their own
    copy of the lock-read — `hold_lock` here, and `_loop_line` in the status
    report. A third (`rite start`) made it worth naming: three readings of one
    file drift apart one bug at a time, and the first symptom is two commands
    disagreeing about whether the loop is up.

    The no-subprocess property is load-bearing rather than incidental.
    `collect_status` is the read-only path and is pinned by a test that
    patches `subprocess.run` and asserts nothing calls it; asking tmux here
    once put a process spawn into the command people run most often. A signal
    to a pid is a syscall, not a process.

    So this is the cheap answer and `rite loop status` is the authoritative
    one — tmux knows what it is running and a lock file only knows what was
    written. They disagree for a few seconds at startup, while `start` has
    released its own lock and the loop has not yet taken it, and during that
    window this says "not running" and `loop status` says the truth. That is
    the right way round.

    THE LIVENESS CHECK IS INSIDE THE `try`, and that is not tidiness.
    `os.kill` raises `OverflowError` — not `OSError`, not `ValueError` — when
    the pid parses as an int but is too large for a C int, so a lock file
    reading `99999999999` made this raise rather than answer. Every caller is
    a command whose job is to tell you what is wrong with a project:
    `rite status`, `rite start`, and `hold_lock` under `rite loop start`. A
    corrupt file is precisely when they must still work, and a traceback is
    the one output that helps nobody. Found in review, pre-existing, and only
    dangerous once `start` began depending on it.
    """
    from rite_ai.scheduler.lock import process_is_running

    try:
        pid = int(lock_path(root).read_text().split()[0])
        return pid if pid and process_is_running(pid) else 0
    except (OSError, ValueError, IndexError, OverflowError):
        return 0


def hold_lock(root: Path) -> int | None:
    """Take the loop lock, or return the pid of whoever holds it.

    Deliberately NOT `scheduler.lock`: that one serialises ticks, which are
    seconds long, and a loop holding it for hours would stop every tick in
    the project. Same mechanism, separate file, different lifetime.

    ⚠ TWO LIMITS, BOTH REPORTED RATHER THAN CLAIMED AWAY, because the module
    docstring above overstated this lock and the overstatement is the kind
    that gets a defect filed as impossible:

    - **It is not atomic.** Read-then-`write_atomic` is `mkstemp` +
      `os.replace`, which overwrites unconditionally, so two loops started in
      the same instant both read "no holder" and both proceed.
      `scheduler/lock.py` solved the identical race with `os.link` after
      measuring it; this one has not been.
    - **A pid is not an identity.** Nothing distinguishes the loop from an
      unrelated process the OS gave the same number after a reboot, and this
      file survives one. `scheduler/lock.py` carries a reuse backstop; a
      simple age ceiling cannot work here, because a loop is meant to run for
      days. Until that is designed, `start` and `stop` name the stale lock
      and print the command that clears it, rather than wedging silently.
    """
    path = lock_path(root)
    holder = running_pid(root)
    if holder:
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
        # TMUX HAS ALREADY SAID THERE IS NO SESSION, three lines up. So this
        # is one of exactly two things, and the old remedy — "`rite loop
        # stop`" — was right for neither: `stop` writes a drain file and
        # never touches the lock, so it answers "no loop is running" and
        # leaves the refusal in place. A user who rebooted with a loop
        # running could not start another one again, ever, and nothing
        # printed the one command that would fix it.
        #
        # Naming both cases rather than guessing between them: rite cannot
        # tell a legitimate `rite loop run --watch` from a pid the OS
        # recycled onto something unrelated after a reboot, and guessing
        # wrong in one direction kills a running loop's lock while it works.
        # Reporting with the escape is correct on both inputs.
        return Refused(
            f"this project's loop lock is held by pid {holder}, and tmux has "
            "no session for it",
            remedy=(
                f"if that is a `rite loop run --watch` you started directly, "
                f"stop it there. If pid {holder} is unrelated — a reboot can "
                f"leave the lock behind and the number can be reused — the "
                f"lock is stale: `rm {lock_path(root)}`"
            ),
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


def _ask_tmux(name: str, fmt: str) -> str:
    """One `display-message` format string, or "" when it cannot be asked."""
    binary = _tmux()
    if binary is None:
        return ""
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, fmt],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or "").strip() if done.returncode == 0 else ""


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

    # The pid comes from TMUX, not from the lock file. `start` releases its
    # own lock before spawning — the lock belongs to the process that runs
    # the loop — so between the spawn and that process taking it there is a
    # window where the lock says nothing, and `status` used to answer "(pid
    # unknown)" for a loop that was plainly running. tmux knows what it is
    # running; ask the thing that knows.
    pid = 0
    pane = _ask_tmux(name, "#{pane_pid}")
    if pane.isdigit():
        pid = int(pane)

    holder = 0
    try:
        holder = int(lock_path(root).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        holder = 0

    started = _ask_tmux(name, "#{session_created}")
    uptime = 0.0
    if started.isdigit():
        uptime = max(0.0, time.time() - int(started))

    return LoopStatus(
        running=True,
        session=name,
        pid=pid,
        lock_holder=holder,
        uptime=uptime,
        draining=drain,
    )


def stop(root: Path, reason: str = "stop requested"):
    """Ask, never kill.

    A killed loop can leave a claim held by a process that no longer exists —
    the failure §2.6 is about, caused by the stop command. Asking costs one
    cycle.
    """
    request_drain(root, reason)
    name = session_name(root)
    if not is_alive(name):
        # SAY WHETHER THE LOCK IS STILL HELD. `rite loop start`'s refusal
        # used to send people here, and this branch reported "no loop is
        # running" while a lock sat in the way of starting one — two
        # commands, two true-sounding answers, and no way to reconcile them.
        # A held lock with no tmux session is the wedge; it is named here
        # because this is where somebody who has just been refused arrives.
        holder = running_pid(root)
        extra = (
            f" A lock is still held by pid {holder}, which will refuse a new "
            f"loop — if that process is not a `rite loop run --watch` you "
            f"started, the lock is stale: `rm {lock_path(root)}`."
            if holder
            else ""
        )
        return Started(
            name,
            detail=(
                "no loop is running; the drain signal is recorded so one "
                "started before it is cleared stops immediately." + extra
            ),
        )
    return Started(
        name,
        detail=(
            "asked it to drain. It finishes the cycle it is in, takes no new "
            f"work, and exits — up to one cycle. Watch with `tmux attach -t {name}`"
        ),
    )
