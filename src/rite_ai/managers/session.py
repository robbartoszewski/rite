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

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from rite_ai.label import project_slug
from rite_ai.managers import (
    MANAGER_ENV,
    ManagerInstance,
    manager_dir,
    pid_alive,
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
    if not session_exists(name):
        # Or this records ANOTHER session's pid against this Manager — a
        # recorded value naming the wrong thing, which §9.14.10 exists for.
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
    pane: str = ""
    """⚠ The Manager's OWN pane, not the session's active one. A human who
    attaches and runs `tmux split-window` makes a second pane active, and
    every `-t <session>` question is then answered about THAT pane — so a
    scratch shell exiting 0 read as the Manager finishing cleanly. The id
    is stable for the pane's life and unambiguous."""


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
    """Two questions, asked in this order, because neither answers alone.

    ⚠ THE ORDER IS THE SAFETY PROPERTY. It reads like belt and braces and
    it is not; each call answers something the other gets wrong.

    `has-session` cannot answer this on its own. With `remain-on-exit on` —
    which `start` sets so the exit status survives — a session whose command
    has exited still EXISTS, so `has-session` says yes about a dead pane.

    `display-message` cannot answer it on its own either, for two reasons
    measured on tmux 3.7c, both of which made this function report that a
    session nobody had started was alive:

      1. A target it cannot resolve is NOT an error. With a server running,
         `display-message -p -t no-such-session '#{pane_dead}'` exits 0 and
         prints an empty expansion. This function read the exit code for
         absence and the text for deadness, so an empty reply fell through
         both: `"" != "1"` is True, and absent read as ALIVE AND KNOWN.
         Because `start`'s duplicate check refuses on alive, `rite start
         <manager>` then refused every Manager on any machine where a tmux
         server happened to be running — which is every machine whose owner
         uses tmux, i.e. everyone this command is for.

      2. `-t` resolves by exact match, then fnmatch, then PREFIX. With only
         `leader` running, `-t lead` answers ABOUT `leader` (measured). So a
         Manager named `lead` reads a different Manager's pane. `name_problem`
         accepts both names and `session_name` puts a project's Managers
         adjacent by design, so this is reachable by naming two of them
         sensibly.

    `=` is tmux's exact-match prefix and it is why existence is asked first:
    `has-session -t =lead` correctly fails when only `leader` exists, where
    `has-session -t lead` prefix-matches and succeeds. It works ONLY here —
    `display-message` wants a pane target and answers `-t =lead` with an
    empty expansion even for a session that exists, so putting `=` on the
    second call would reintroduce defect 1. Once existence is established
    exactly, the unprefixed `-t` on the second call cannot prefix-match
    elsewhere: tmux tries exact first, and we know an exact match exists.

    Residual, deliberately not widened here: a non-zero `has-session` is
    still read as absent, so a tmux that fails for some *other* reason reads
    absent rather than unanswerable — a fail-open in the duplicate check.
    The three shapes seen in practice (`can't find session`, `no server
    running`, `error connecting to <socket>`) are all genuinely absent, and
    whitelisting stderr wording would refuse to start on any tmux that
    phrases an ordinary miss differently. Named rather than silently kept.
    """
    binary = _tmux()
    if binary is None:
        return Liveness(False, known=False, detail="tmux not found")
    try:
        exists = subprocess.run(
            [binary, "has-session", "-t", f"={name}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Liveness(False, known=False, detail="`tmux has-session` timed out")
    except (OSError, subprocess.SubprocessError) as e:
        return Liveness(False, known=False, detail=f"`tmux has-session` failed: {e}")
    if exists.returncode != 0:
        # No session of exactly this name — a KNOWN answer, and not the same
        # as being unable to ask.
        return Liveness(False, known=True)
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, "#{pane_dead}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Liveness(False, known=False, detail="`tmux display-message` timed out")
    except (OSError, subprocess.SubprocessError) as e:
        return Liveness(
            False, known=False, detail=f"`tmux display-message` failed: {e}"
        )
    reply = (done.stdout or "").strip()
    if done.returncode != 0 or reply not in ("0", "1"):
        # The session is there and tmux will not say what its pane is doing.
        # Refuse rather than guess: a wrong "not alive" here starts a second
        # PAID Manager session (§5.1.1).
        return Liveness(
            False,
            known=False,
            detail=(
                f"the session exists but `tmux display-message` gave no "
                f"readable pane state ({reply!r})"
            ),
        )
    return Liveness(reply != "1", known=True)


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
    if instance.pid and not pid_alive(instance.pid):
        # The session is there and the process it recorded is not. That is a
        # tmux pane whose command exited leaving a shell — which `has-session`
        # reports as alive, because it is. Checking both is what distinguishes
        # "the Manager is running" from "a window with its name still exists".
        return None
    return instance


def _rejected_the_flag(done: subprocess.CompletedProcess) -> bool:
    """Whether tmux refused because it does not KNOW `-e`, not because the
    session could not start.

    Matched on tmux's own wording for an unknown option rather than on any
    nonzero exit: falling back on every failure would retry a genuine
    refusal without the identity and report success for it.
    """
    said = ((done.stderr or "") + (done.stdout or "")).lower()
    return "unknown flag" in said or "unknown option" in said or "usage:" in said


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
    problem = name_problem(manager, kind="manager name", must_be_a_tmux_target=True)
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
    # ⚠ **`cwd` stays the PROJECT ROOT and the identity travels separately.**
    # The alternative considered was launching in `manager_dir` so a process
    # could read its own name off `Path.cwd()`. That cwd is load-bearing: the
    # prompt tells the Manager to "Read `.rite/`" as a relative path, `rite`
    # walks UP from cwd for the project marker, and git resolves the worktree
    # from it. Moving it would have traded a missing identity for three silent
    # breakages, so the environment carries the name and cwd is left alone.
    #
    # `-e` needs tmux 3.2 (2021). Measured here on 3.7c and in CI on 3.4, but
    # a hard failure on an older tmux would mean the Manager does not start AT
    # ALL — losing the session to gain the name — so a refusal that names the
    # flag falls back and says the identity is missing.
    argv = [binary, "new-session", "-d", "-e", f"{MANAGER_ENV}={manager}"]
    identified = True
    # ⚠ **THE NAME WE INHERITED MUST NOT TRAVEL WITH US.** `rite start` is
    # routinely run from inside another Manager's session, where this
    # process's own environment already carries that Manager's name — and a
    # `tmux new-session` that has to START the server forks one that
    # inherits this environment, after which EVERY pane on that server
    # reads the inherited name.
    #
    # Measured on 3.7c, and the distinction matters because the obvious
    # version of this claim is wrong: a pane takes its environment from the
    # tmux SERVER, not from the client that asked. A nested `new-session`
    # against an already-running server does NOT leak (verified: the child
    # pane read empty). The leak is exactly the case where this invocation
    # is what starts the server.
    #
    # Stripped on BOTH calls. On the fallback it is the whole fix — without
    # it the new session reads the PARENT's name and files journal entries
    # under it, which is worse than having no name at all. On the `-e` call
    # the session's own value wins for its own panes, but a server this
    # call started would keep the inherited name for every LATER session on
    # it, so the poisoning outlives the command that caused it.
    uninherited = {k: v for k, v in os.environ.items() if k != MANAGER_ENV}
    try:
        done = subprocess.run(
            [*argv, "-s", name, launch],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            cwd=str(root),
            env=uninherited,
        )
        if done.returncode != 0 and _rejected_the_flag(done):
            identified = False
            done = subprocess.run(
                [binary, "new-session", "-d", "-s", name, launch],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=120,
                cwd=str(root),
                env=uninherited,
            )
    except (OSError, subprocess.SubprocessError) as e:
        return StartResult(False, f"could not start a session: {e}")
    if done.returncode != 0:
        # Truncated, as `loop/session.py` truncates: tmux can emit a great
        # deal on failure and a refusal nobody can read is a refusal nobody
        # acts on.
        detail = (done.stderr or done.stdout or "").strip()[:200]
        return StartResult(False, f"tmux refused to start the session: {detail}")

    # `remain-on-exit on` so the pane survives its command and carries
    # `#{pane_dead_status}` — without it tmux destroys the session and the
    # exit status goes with it, leaving finished, quit and crashed
    # indistinguishable. It also leaves the conversation readable after the
    # supervisor stops, which is what a human attaching afterwards wants.
    _keep_pane_after_exit(binary, name)

    if not settled_alive(name):
        return StartResult(
            False,
            f"the session started and exited immediately, so `{launch}` is "
            f"not running. `tmux new-session` reports whether a session was "
            f"CREATED, not whether the command in it survived. Run "
            f"`{launch}` directly to see why.",
        )

    pane = _pane_id(name)
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
    # Said, not swallowed. A Manager that cannot read its own name still
    # works — `--manager` is explicit on every command that needs it — but
    # the person who sees a journal entry refused for a missing default
    # should already know why.
    blind = (
        ""
        if identified
        else (
            f"\n  this tmux does not support `new-session -e` (3.2+), so "
            f"nothing inside this session can read {MANAGER_ENV}: `rite "
            f"journal observe` and `retrospective` will REFUSE there until "
            f"they are given `--manager {manager}` explicitly. Upgrading "
            f"tmux to 3.2+ is what makes the default work."
        )
    )
    return StartResult(
        True,
        f"Manager '{manager}' started as {name}.\n  reach it with: {attach}{blind}",
        session=name,
        attach=attach,
        pane=pane,
    )


FINISHED = "finished"
QUIT = "quit"
CRASHED = "crashed"
UNCLEAR = "unclear"

STATUS_READS = 30
STATUS_PAUSE = 0.1
"""How long `ending` waits for a reap: 30 reads, 0.1s apart. Named because a
test pins it, and a test that hardcodes the number drifts silently from the
code the moment the number is tuned — which is how the first version of this
wait was widened with its own regression test still asserting the old one."""

_EXIT_STATUS_ANSWER: bool | None = None
"""Cached: whether this tmux reports an exit status is a property of the
machine, not of the moment, so asking once per process is enough — and
each probe costs a session create and destroy."""


@dataclass(frozen=True)
class Ending:
    """How a session ended, and whether restarting it is right.

    ⚠ **Three outcomes, and a restart is correct for exactly one.** A draft
    of the supervisor had only "the pane is gone", which is what finishing,
    quitting and crashing all look like — so it restarted a session the
    human had deliberately exited, measured, and would have restarted a
    crash loop too.

    **`resume` is False whenever the answer is not certain.** The asymmetry
    decides the default: a supervisor that stops when unsure costs a human
    one command, and one that restarts when unsure fights them and spends
    money doing it.
    """

    kind: str
    status: int = 0
    detail: str = ""

    @property
    def resume(self) -> bool:
        return self.kind == FINISHED


def session_exists(name: str) -> bool:
    """Does a session with EXACTLY this name exist?

    ⚠ **`-t <name>` is not an exact match.** tmux falls back to fnmatch and
    then to PREFIX matching, so with only `leader` running:

        tmux display-message -p -t lead '#{session_name}'  ->  'leader'

    Every `-t` question about `lead` is then answered about `leader` — and
    for `ending` that means reading another session's exit status and
    landing on FINISHED, the one verdict that resumes. A Manager named `pm`
    beside `pm2` is enough.

    `=` is tmux's exact-match prefix and it works HERE, on `has-session`:

        only 'leader' exists:  has-session -t =lead   -> rc 1
                               has-session -t =leader -> rc 0

    ⚠ **It must NOT be put on `display-message`**, which wants a pane
    target: `-t =lead '#{pane_dead}'` expands EMPTY for a session that
    genuinely exists, so pinning it there reintroduces the bug it is meant
    to fix. Measured on tmux 3.7c. Hence two calls — existence here,
    content there — which also makes them fail independently.
    """
    binary = _tmux()
    if binary is None:
        return False
    try:
        done = subprocess.run(
            [binary, "has-session", "-t", f"={name}"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def ending(name: str, human_was_present: bool, pane: str = "") -> Ending:
    """Why a session's command stopped.

    Needs `remain-on-exit on`, which `start` sets: without it tmux destroys
    the session when its command exits and the exit status is gone with it.
    With it, the pane persists as dead and carries `#{pane_dead_status}` —
    which is also why the conversation is still there for a human to read
    after the supervisor has stopped.

    ⚠ **`#{pane_dead}` and `#{pane_dead_status}` do not arrive together.**
    tmux marks a pane dead when its file descriptor closes and fills the
    status in when it reaps the child, and nothing orders those two. So
    there is a window — short, and wider on Linux than on macOS — where the
    pane reads dead with no status yet. A draft read once and called that
    window permanently unknown, which made every CLEAN exit `unclear` on
    Linux CI while `exit 9` passed, because a crash happened to lose the
    race less often.

    That is also exactly how the capability probe came to disagree with the
    thing it was probing: the probe polled until the status appeared and
    this did not. **Waiting is the production behaviour, so the probe had
    been measuring a patience `ending` did not have.**
    """
    binary = _tmux()
    if binary is None:
        return Ending(UNCLEAR, detail="tmux not found, so the exit status is gone")
    if not session_exists(name):
        # Exactly this name, or none. Without the check `-t` prefix-matches
        # and this reads ANOTHER session's exit status — landing, for a
        # healthy neighbour, on FINISHED, which resumes.
        return Ending(UNCLEAR, detail="the session is gone, with its exit status")

    # ⚠ The MANAGER'S pane, not the session's active one. Falls back to the
    # session name when the id was unreadable, which is the old behaviour.
    target = pane or name

    def ask() -> tuple[bool, str, str, str] | None:
        """(reachable, pane_dead, pane_dead_status, pane_dead_signal)."""
        try:
            done = subprocess.run(
                [
                    binary,
                    "display-message",
                    "-p",
                    "-t",
                    target,
                    "#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return (False, "", "", "")
        raw = (done.stdout or "").strip().split("|")
        return (
            True,
            raw[0] if raw else "",
            raw[1] if len(raw) > 1 else "",
            raw[2] if len(raw) > 2 else "",
        )

    reported = ""
    signal = ""
    status: int | None = None
    # Thirty reads over three seconds. A loaded CI runner reaps later than
    # a quiet laptop and the first attempt at this waited one second, which
    # was enough for two of the three endings and not the third — the same
    # race, landing on a different test. Only the dead-WITHOUT-status window
    # waits: a live pane and a vanished session both answer immediately, so
    # no ordinary caller pays this. And if the status never arrives the
    # answer is still UNCLEAR, so waiting can only turn an unknown into a
    # known and never the other way.
    for attempt in range(STATUS_READS):
        if attempt:
            time.sleep(STATUS_PAUSE)
        answer = ask()
        if answer is None:
            return Ending(UNCLEAR, detail="could not read the exit status")
        reachable, dead, reported, signal = answer
        if not reachable:
            # The session is gone entirely — `remain-on-exit` did not hold
            # it, or something removed it. No status to read, so no restart.
            return Ending(UNCLEAR, detail="the session is gone, with its exit status")
        if dead != "1":
            return Ending(UNCLEAR, detail="the pane is not dead")
        # ⚠ AN ABSENT STATUS IS NOT ZERO. A draft parsed empty as 0, so a
        # tmux that had not yet reaped the child turned every ending into a
        # clean one — and `exit 9` read as FINISHED and would have been
        # resumed. Caught by Linux CI, where the field came back empty while
        # macOS filled it. Unreadable means keep asking, then unclear.
        try:
            status = int(reported)
        except ValueError:
            continue
        break

    if status is None and signal:
        # ⚠ tmux KNEW. `#{pane_dead_signal}` carries `kill`, `term`, `segv`
        # where `#{pane_dead_status}` is empty, and a draft read only the
        # second — so an OOM-killed or externally killed Manager was a FAULT
        # reported as "cannot tell", and `rite start` exited 0 on it because
        # `_stopped_because` treats anything but `crashed` as success.
        # The information was in the same call and was not asked for.
        return Ending(
            CRASHED,
            status=-1,
            detail=f"the command was killed by SIG{signal.upper()}",
        )
    if status is None:
        return Ending(
            UNCLEAR,
            detail=(
                "the pane is dead but tmux reported no exit status "
                f"({reported!r}) within {STATUS_READS * STATUS_PAUSE:g}s, so "
                "whether it finished or failed is unknown"
            ),
        )

    if status != 0:
        return Ending(
            CRASHED,
            status=status,
            detail=f"the command exited {status}",
        )
    # ⚠ ASKED HERE, not only sampled. `supervise` polls `was_attached` every
    # `poll` seconds and only while the session is alive, so a human who
    # attaches and types `exit` can still be attached AT THE MOMENT of the
    # ending while the sampled flag says False — measured, with a real
    # client on a real tty. The information was available and discarded.
    # OR, not replace: the sampled flag answers "was anybody EVER there",
    # which this call cannot see, and this answers "is anybody there NOW".
    if human_was_present or was_attached(name):
        # A clean exit with somebody attached at some point. `exit` typed by
        # a human and an agent finishing are the SAME exit status, so this
        # cannot be told apart — and the safe reading is that the human
        # meant it.
        return Ending(
            QUIT,
            detail=(
                "it exited cleanly while somebody was attached, which is what "
                "both a finished agent and a human typing `exit` look like"
            ),
        )
    return Ending(FINISHED, detail="the command exited cleanly, unattended")


@dataclass(frozen=True)
class Stopped:
    """What a stop actually did, because "stopped" has three meanings.

    `killed` False with `ok` True is the IDEMPOTENT case — there was
    nothing there. That is a success for a recovery command whose whole job
    is to make "no Manager is running" true, and reporting it as a failure
    would train users to ignore the failure.
    """

    ok: bool
    killed: bool
    detail: str = ""


def stop(name: str) -> Stopped:
    """End a Manager's tmux session.

    ⚠ **Idempotent, and that is a requirement rather than a nicety.** This
    is reached from two directions — a human pressing Ctrl-C, and `rite
    manager stop` cleaning up a session whose supervisor died — and both
    can arrive when the session is already gone. A teardown that only works
    on the happy path leaves exactly the phantom it exists to remove.

    ⚠ **Exact name only.** `-t` prefix-matches, so without
    `session_exists` a stop aimed at `lead` kills `leader` — and unlike a
    misread exit status, that one destroys somebody's work.
    """
    binary = _tmux()
    if binary is None:
        return Stopped(True, False, "tmux not found, so no session to stop")
    if not session_exists(name):
        return Stopped(True, False, f"no session named {name}")
    try:
        done = subprocess.run(
            [binary, "kill-session", "-t", name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Stopped(False, False, f"could not stop {name}: {e}")
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()[:200]
        return Stopped(False, False, f"tmux refused to stop {name}: {detail}")
    return Stopped(True, True, f"stopped {name}")


def was_attached(name: str) -> bool:
    """Is a client attached to this session right now?

    Polled rather than asked once, because attachment is a moment: the
    supervisor records whether anybody was EVER attached during a session,
    which is the question `ending` needs.

    ⚠ **BOTH DIRECTIONS ARE NOW MEASURED.** This shipped saying the TRUE
    one was reasoned rather than observed: every attempt to create a
    genuinely attached client returned `list-clients: (none)`, a `pty.fork`
    included, and the docstring said so rather than implying otherwise.

    That mattered because the signal is load-bearing. It is what tells a
    human typing `exit` from an agent finishing — both are exit status 0 —
    so had it never fired in practice, a human quitting would read as
    FINISHED and the supervisor would resume, the exact behaviour it was
    added to stop.

    **A tmux PANE is a real terminal**, which is what `pty.fork` never
    managed to be. Attaching from inside another tmux session produces a
    real client on a real tty — measured: `/dev/ttys015 …
    (attached,focused,UTF-8)`, and True; False before it and False after it
    detaches. `$TMUX` must be cleared in that pane or tmux refuses the
    nested attach and the client silently never appears, which is what made
    the first measurement look like another failure.
    """
    binary = _tmux()
    if binary is None:
        return False
    if not session_exists(name):
        return False
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, "#{session_attached}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode != 0:
        return False
    try:
        return int((done.stdout or "0").strip()) > 0
    except ValueError:
        return False


def _pane_id(name: str) -> str:
    """The id of the session's pane at the moment it was created.

    Empty if it cannot be read, and every caller falls back to the session
    name — which is the old behaviour, so an unreadable id degrades to the
    previous correctness rather than to a crash.
    """
    binary = _tmux()
    if binary is None:
        return ""
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, "#{pane_id}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip() if done.returncode == 0 else ""


def _keep_pane_after_exit(binary: str, name: str) -> None:
    """Set `remain-on-exit` so a pane survives its command.

    ⚠ **ONE function, used by both `start` and the probe, and that is the
    point.** They set it separately before, with `set-option`; on Linux CI
    the probe then reported the capability present while real sessions
    behaved as if it were absent. **A probe that does not use the production
    call measures something else** — and a capability check that disagrees
    with the thing it is checking is worse than no check.

    `set-window-option` because that is what `remain-on-exit` is. macOS
    accepted the session form and appeared to honour it; that is the
    platform telling you what you want to hear.
    """
    subprocess.run(
        [binary, "set-window-option", "-t", name, "remain-on-exit", "on"],
        capture_output=True,
        timeout=30,
        check=False,
    )


def exit_status_available() -> bool:
    """Can this tmux report why a session's command ended?

    ⚠ **A SAMPLE, not a law about the machine, and the difference is not
    pedantry.** Measured on tmux 3.4 under CI: a probe answered NO while a
    real session in the same run, seconds later, reported its status
    perfectly. The capability is INTERMITTENT there, not absent. A test
    that concluded "then every ending will be unclear" from one `False`
    asserted something that was never true and failed accordingly.

    So this answers "when asked, did a session report its status" — enough
    to warn a user that their supervisor may stop after one session, and
    NOT enough to claim any particular ending will be unreadable.

    ⚠ **Asked more than once, because a probe that loses its own race
    answers about itself.** This function has now got in its own way five
    times: the name collided with itself, the command died before the
    option was set, an absent status parsed as zero, the setup call
    diverged from production's, and a single `send-keys` could be swallowed
    by a shell not yet reading. Three attempts, and only a run in which
    EVERY attempt failed is reported as a No.

    The answer comes from `ending` itself. Four earlier probes imitated it
    and each diverged differently; there is nothing left to imitate.
    """
    global _EXIT_STATUS_ANSWER
    if _EXIT_STATUS_ANSWER is not None:
        return _EXIT_STATUS_ANSWER

    if _tmux() is None:
        return False
    for _ in range(3):
        if _probe_once():
            _EXIT_STATUS_ANSWER = True
            return True
    _EXIT_STATUS_ANSWER = False
    return False


def _probe_once() -> bool:
    """One sample: start a session the way `start` does, end it, ask
    `ending`. True only for the exact status sent."""
    binary = _tmux()
    if binary is None:
        return False
    # ⚠ Unique per CALL, not per process. A draft used the pid alone, so two
    # probes in one run collided on the name, `new-session` refused the
    # second with "duplicate session", and the capability read as ABSENT —
    # a probe reporting the machine cannot do something because the probe
    # got in its own way. Found by the suite, where it fires more than once.
    name = f"rite-probe-exit-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        # A LONG-LIVED command, then the option, then make it exit — the
        # order `start` uses. A draft launched `sh -c 'exit 3'`, which died
        # before `remain-on-exit` could be set, so the session was already
        # gone and the probe reported ABSENT on a machine that has it.
        made = subprocess.run(
            [binary, "new-session", "-d", "-s", name, "sh"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if made.returncode != 0:
            return False
        _keep_pane_after_exit(binary, name)
        if not settled_alive(name):
            return False
        subprocess.run(
            [binary, "send-keys", "-t", name, "exit 3", "Enter"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        for _ in range(20):
            time.sleep(0.1)
            if not liveness(name).alive:
                break
        else:
            # The shell never took the keystroke. That says nothing about
            # whether this tmux reports a status, so it is not an answer.
            return False
        # 3, not "any answer": a tmux that reports a status but the wrong
        # one is not a tmux `ending` can tell finishing from crashing on.
        how = ending(name, human_was_present=False)
        return how.kind == CRASHED and how.status == 3
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        subprocess.run(
            [binary, "kill-session", "-t", name], capture_output=True, check=False
        )
