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
ordinary `rite loop start` twice. A kernel lock that the loop process itself
holds stops everything else: a loop started from a different terminal, from a
script, or as `rite loop run --watch` directly. It is `rite_ai.loop.lock`,
and it is the one that decides. `start`'s own check only saves a spawn: a
loop that loses to another exits at once, and `start` reports its last
words.

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

from rite_ai.loop.lock import Holder
from rite_ai.loop.lock import holder as loop_holder
from rite_ai.managers.session import Liveness
from rite_ai.state import write_atomic

DRAIN_FILENAME = "loop-drain"
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
    """The process holding the loop's kernel lock, exactly. Normally the
    same; different means another process holds this project's loop lock,
    which is worth saying rather than quietly preferring one of them."""
    uptime: float = 0.0
    draining: str = ""
    detail: str = ""
    unknown: bool = False
    """tmux could not be asked. NOT the same as `running=False`, and never
    printed as "not running" — that answer tells a reader to start another."""

    def lines(self) -> list[str]:
        if self.unknown:
            return [f"loop: ⚠ {self.detail}"]
        if not self.running:
            return [f"loop: not running — {self.detail}"]
        age = f", up {_age(self.uptime)}" if self.uptime else ""
        out = [f"loop: running as {self.session} (pid {self.pid or 'unknown'}){age}"]
        if self.lock_holder and self.pid and self.lock_holder != self.pid:
            # Two processes think they are this project's loop. A reader
            # deciding what to stop needs to know before they act, not after.
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


def describe_holder(held: Holder) -> str:
    """One phrase for `rite start`, `rite status` and `rite loop stop`.

    Three answers, because "could not tell" is not "not running": a start
    told "not running" by a reader that could not tell would make two."""
    if not held.known:
        return f"cannot tell whether a loop is running — {held.detail}"
    if held.running:
        return f"running (pid {held.pid})"
    return "not running"


# --- tmux --------------------------------------------------------------------------


def _tmux() -> str | None:
    return shutil.which("tmux")


def is_alive(name: str) -> bool:
    """True only when tmux ANSWERED that the session exists. Callers that
    must tell "not running" from "could not ask" use `liveness`."""
    return liveness(name).alive


def liveness(name: str) -> Liveness:
    """Whether the loop's session is running, and whether tmux could say.

    ⚠ **THREE ANSWERS, the fix `managers/session.py` made and this module
    never got (C15).** This returned a bare bool, so a `has-session` that
    timed out or could not run read as "no session". Measured through `rite
    doctor` with the loop's session present and the tmux server frozen
    (SIGSTOP): it printed "loop: not running (rite loop start)" — a
    confident wrong answer that tells the reader to start a second loop.
    That is the shape of C15's second named failure ("the loop session was
    started and `rite doctor` did not see it"), and the load-sensitive tests
    assert exactly there.

    tmux answering nonzero is a real "no": `has-session` says so for a
    session that is not there. Anything else — no answer inside the
    timeout, an OS error, or an answer that is not about the session — is
    `known=False`.
    """
    binary = _tmux()
    if binary is None:
        return Liveness(False, known=True, detail="tmux is not installed")
    # ⚠ `=` is tmux's EXACT-match prefix. `-t` resolves by exact match, then
    # fnmatch, then PREFIX, so without it a longer-named session answers for
    # a shorter one and `loop start` refuses — "a loop is already running" —
    # about a session that does not exist. `session_name` carries a path
    # hash, which makes a collision between two projects unlikely rather
    # than impossible; the property should not rest on a hash staying in the
    # name. Measured: `has-session -t lead` -> 0 with only `leader` up.
    try:
        done = subprocess.run(
            [binary, "has-session", "-t", f"={name}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=LIVENESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return Liveness(
            False,
            known=False,
            detail=f"tmux did not answer within {LIVENESS_TIMEOUT:g}s",
        )
    except OSError as e:
        return Liveness(False, known=False, detail=f"could not run tmux: {e}")
    if done.returncode == 0:
        return Liveness(True, known=True)
    said = (done.stderr or "").strip()
    # tmux's ways of saying NO, measured on 3.7c: "can't find session: x"
    # (server up), "no server running on …" (socket dir or server gone), and
    # "error connecting to … (No such file or directory)" (fresh socket dir,
    # no server ever). The last one looks like a fault and is not — reading it
    # as unknown would make `loop start` refuse on every machine with no tmux
    # server running. Anything else (a permission error, a foreign message)
    # is not an answer about this session.
    no_server = "error connecting to" in said and "No such file or directory" in said
    if (
        "can't find session" in said
        or "no server running" in said
        or no_server
        or not said
    ):
        return Liveness(False, known=True)
    return Liveness(False, known=False, detail=f"tmux said: {said[:200]}")


LIVENESS_TIMEOUT = 30.0


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
    here = liveness(name)
    if not here.known:
        # FAIL CLOSED, as `managers/session.start` does (D-74): a start that
        # cannot establish there is no loop could make two.
        return Refused(
            f"cannot tell whether a loop is already running for this project "
            f"({here.detail}), so starting one could make two",
            remedy="check with `tmux ls` and try again",
        )
    if here.alive:
        return Refused(
            f"a loop is already running for this project as {name}",
            remedy=f"`rite loop status`, or `tmux attach -t {name}` to watch it",
        )

    # Only a courtesy, to refuse before spawning: the lock is taken by the
    # loop process itself (`rite_ai.loop.lock`), so a loop that starts
    # between this check and that one loses there, exits at once, and the
    # "exited immediately" report below carries its words. A check that
    # cannot tell refuses, as tmux's does above.
    held = loop_holder(root)
    if not held.known:
        return Refused(
            f"cannot tell whether a loop is already running for this project "
            f"({held.detail}), so starting one could make two",
            remedy="try again; if it persists, the detail above says what is wrong",
        )
    if held.running:
        # TMUX HAS ALREADY SAID THERE IS NO SESSION, three lines up, so this
        # is a `rite loop run --watch` started directly. The pid is exact:
        # the kernel holds this lock for that process and nothing else, and
        # there is no such thing as a stale one to remove any more.
        return Refused(
            f"this project's loop is already running as pid {held.pid}, outside tmux",
            remedy="stop it where it was started, or `rite loop stop` to drain it",
        )
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
    here = liveness(name)
    if not here.known and _tmux() is not None:
        return LoopStatus(
            unknown=True,
            session=name,
            detail=f"cannot tell whether {name} is running — {here.detail}",
        )
    if not here.alive:
        if _tmux() is None:
            return LoopStatus(detail="tmux is not installed, so none was started")
        return LoopStatus(
            detail="no tmux session for this project"
            + (" (a drain was requested)" if drain else "")
        )

    # The pid comes from TMUX, not from the lock. The lock is taken by the
    # process that runs the loop, a moment after tmux starts it, so for that
    # moment the lock has no holder while the session plainly runs. tmux
    # knows what it is running; ask the thing that knows. The lock's holder
    # is reported beside it, exact, and the two differing is worth seeing.
    pid = 0
    pane = _ask_tmux(name, "#{pane_pid}")
    if pane.isdigit():
        pid = int(pane)

    held = loop_holder(root)
    holder = held.pid if held.known and held.running else 0

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
    here = liveness(name)
    if not here.known:
        return Started(
            name,
            detail=(
                f"asked it to drain, but cannot tell whether a loop is running "
                f"({here.detail}). The drain signal is recorded either way; "
                f"check with `rite loop status` once tmux answers."
            ),
        )
    if not here.alive:
        # SAY WHETHER THE LOCK IS STILL HELD. `rite loop start`'s refusal
        # used to send people here, and this branch reported "no loop is
        # running" while a lock sat in the way of starting one — two
        # commands, two true-sounding answers, and no way to reconcile them.
        # A held lock with no tmux session is the wedge; it is named here
        # because this is where somebody who has just been refused arrives.
        held = loop_holder(root)
        if not held.known:
            extra = f" But {describe_holder(held)}."
        elif held.running:
            extra = (
                f" A loop is running outside tmux as pid {held.pid}; the drain "
                "signal reaches it too, at the end of its current cycle."
            )
        else:
            extra = ""
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
