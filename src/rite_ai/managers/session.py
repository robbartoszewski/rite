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
import re
import shutil
import signal
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

    warning: str = ""
    """Something the caller must SAY though the start succeeded.

    ⚠ Added for the one case that was silent and cost a release: tmux would
    not say which pane the Manager is in, so `ending` addresses the session
    instead and a clean finish reads as `unclear`. `ok` is still True — the
    Manager is running — but the run is degraded and the operator has to be
    told, because the symptom appears later and three steps away."""


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


_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*=)(\S+)")


def _what_tmux_said(done: subprocess.CompletedProcess) -> str:
    """tmux's own words for a refusal, safe to put in front of a person.

    ⚠ **Redacted by STRUCTURE (C16).** Three refusals put
    tmux's stderr into a message a user reads and pastes. tmux echoes what
    it was given — measured: `stop` on a target shaped `s:TOKEN=<value>`
    produced `can't find window: TOKEN=<value>` — so anything that ever puts
    a `NAME=value` on tmux's argv (the `-e` trap `_on_tmux_argv` guards) would
    surface it here. Every assignment's value is replaced whatever its name:
    a list of secret names or values loses to the one nobody listed, and
    rite never holds the token to search for anyway.
    """
    said = (done.stderr or done.stdout or "").strip()
    return _ASSIGNMENT.sub(lambda m: m.group(1) + "[redacted]", said)[:200]


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
    prompt: str = "",
    max_sessions: int = 0,
    window_seconds: float = 0.0,
    pane_env: dict[str, str] | None = None,
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
    if session_exists(name):
        # ⚠ **THE SAME SHAPE, AND IT NEEDS THE OPPOSITE ADVICE.** Not alive
        # and still there means the agent exited and `remain-on-exit` — which
        # `start` itself sets, so the exit status survives for `ending` — is
        # holding the session open under a deterministic name.
        #
        # Both guards above pass on it, each correctly: `liveness` says not
        # alive because nothing is running, and `running` says None because
        # there is no record. So this fell through to `tmux new-session` and
        # the operator was handed tmux's own words — "duplicate session:
        # rite-mgr-…" — for a situation one command clears.
        #
        # ⚠ **Reached without any crash, by the likeliest first run there
        # is.** An engine that exits at once (no Claude login, a missing
        # binary) fails `settled_alive`, and that path returns BEFORE
        # anything is recorded while the session stays. So the operator saw
        # "the session started and exited immediately", fixed the login, ran
        # `rite start` again and got "duplicate session" — two errors in a
        # row, the second explaining neither itself nor the first.
        #
        # The advice is deliberately NOT the sentence above it. A live
        # leftover is somebody's work and rite must not adopt or kill it; a
        # dead one is a finished session held for its exit status, and
        # saying which of the two this is what lets the operator act rather
        # than guess.
        return StartResult(
            False,
            f"a tmux session named {name} is left over from an earlier run: "
            f"its command has finished and the session is held open "
            f"so its exit status could be read, but this project has no "
            f"record of it. Nothing is running in it, so it is safe to "
            f"clear — `rite manager stop {manager}` does it. Its "
            f"conversation is readable first with `tmux attach -t {name}`.",
        )

    manager_dir(root, manager).mkdir(parents=True, exist_ok=True)
    launch = command or engine or "claude"
    if not command and token_is_absent() and (engine or "claude") == "claude":
        # ⚠ **Refused BEFORE a session is spent, and said truthfully.**
        # Since the engine is launched with `-p` it cannot fall back to an
        # interactive login, and the keychain path is measured to fail
        # there — so no token means a session that starts, dies, and costs
        # a `rite manager stop` to clean up.
        #
        # This is deliberately NOT `_why_the_engine_died`'s message. That
        # one exists for a real ambiguity — rite cannot tell a bad
        # credential from an unread one — and it is right there. Here the
        # variable is simply unset, there is no ambiguity, and telling
        # somebody their credential might be invalid would send them to
        # re-authenticate a login that is fine.
        #
        # An explicit `command` is never second-guessed: that caller is not
        # launching Claude Code.
        return StartResult(
            False,
            f"refusing to start Manager {manager!r}: an unattended run "
            f"needs {CLAUDE_OAUTH_ENV} in the environment and it is not "
            f"set.\n"
            f"  rite runs the engine non-interactively so a session ends "
            f"when its turn does, and a non-interactive engine cannot ask "
            f"you to log in.\n"
            f"  Mint one with `claude setup-token`, then export it in the "
            f"shell you run `rite start` from — rite reads it from the "
            f"environment and never stores or logs it.",
        )
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
    argv = [binary, "new-session", "-d", *_on_tmux_argv(MANAGER_ENV, manager)]
    # ⚠ The engine's permission mode, when the engine keeps it in the
    # environment rather than on its command line (Goose's `GOOSE_MODE`).
    # It goes through the same allowlist as the Manager name: a mode name is
    # not a secret, but the check is what stops the next variable being
    # added here by pattern-matching on this line.
    for key, value in sorted((pane_env or {}).items()):
        argv.extend(_on_tmux_argv(key, value))
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
    # The cycle's instruction, read by the launch command off stdin. Set
    # even when empty so a stale value from the caller's shell cannot leak
    # into a session that was meant to get none.
    # Written before the session starts, because the launch redirects its
    # stdin from this path. `0600` and rewritten each cycle: the engine is
    # given one instruction, not a history.
    prompt_path = manager_dir(root, manager) / PROMPT_FILE
    try:
        prompt_path.write_text(prompt)
        prompt_path.chmod(0o600)
    except OSError as e:
        return StartResult(False, f"could not write the cycle's prompt: {e}")
    try:
        done = subprocess.run(
            [*argv, "-s", name, launch, *_remain_on_exit(name)],
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
                [
                    binary,
                    "new-session",
                    "-d",
                    "-s",
                    name,
                    launch,
                    *_remain_on_exit(name),
                ],
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
        detail = _what_tmux_said(done)
        return StartResult(False, f"tmux refused to start the session: {detail}")

    # `remain-on-exit on` was set here, in a SECOND tmux call, and it is now
    # chained into `new-session` itself — see `_remain_on_exit` for the race.

    if not settled_alive(name):
        return StartResult(
            False,
            _why_the_engine_died(name, launch, manager),
        )

    pane, why_no_pane = _pane_id_or_why(name)
    # ⚠ CARRIED OUT, not swallowed. Without the id, `ending` addresses the
    # session instead of the pane, and a clean finish reads as `unclear` —
    # measured. The start still succeeds, because a running Manager beats
    # refusing one; but the caller is told so it does not surface later as an
    # unexplained refusal to resume.
    degraded = (
        (
            f"⚠ tmux would not say which pane Manager {manager!r} is in "
            f"({why_no_pane}). Its exit status will be read from the session "
            "instead, which has been measured to report a clean finish as "
            "'unclear', so this run may not resume when it should."
        )
        if why_no_pane
        else ""
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
        warning=degraded,
    )


FINISHED = "finished"
QUIT = "quit"
CRASHED = "crashed"
UNCLEAR = "unclear"

STATUS_DEADLINE = 30.0
STATUS_FIRST_PAUSE = 0.1
STATUS_MAX_PAUSE = 1.0
"""How long `ending` waits for a reap: a TIME budget with backoff, not a count.

⚠ **A FIXED READ COUNT WAS THE DEFECT, AND A BIGGER COUNT WOULD BE THE SAME
DEFECT.** It was 30 reads 0.1s apart. Measured 2026-09-26 on a 4-core Ubuntu
VM running two Managers and an 8B local model: a Manager that exited CLEANLY —
`pane_dead=1`, `pane_dead_status=0`, `pane_dead_signal=` empty, and no OOM kill
anywhere in dmesg or the journal — was reported as `unclear`, because the
status was not observed inside that window. On the same box IDLE the status
appears about 1.0s after the command exits and the same run reports correctly.
So the wait was long enough for an unloaded machine and not for a loaded one,
and any other constant would be long enough for some machines and not others.

The property is a deadline: keep asking until a stated number of SECONDS has
passed, backing off so a slow box is not hammered with subprocess round-trips
while it is the thing under load. A fast ending still answers on the first read.

⚠ **Widening it cannot turn a known answer into a wrong one.** Only the
dead-WITHOUT-status window waits; a live pane and a vanished session answer at
once. And when the deadline does expire the answer is still UNCLEAR, so this
changes WHEN the budget is reached, never what reaching it means."""

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

    ⚠ **And `=` was still not exact for every name, so this no longer asks
    by target at all (C12).** A target is parsed before it is matched:
    `has-session -t =eu:west` reads session `eu`, window `west`. tmux WILL
    create a session named `eu:west` (measured, 3.7c), and against one this
    returned False — a confident "not there" about a session that is.

    So existence is now answered by listing session NAMES and comparing
    strings, which involves no target grammar and is exact for any name.
    rite's own names never contain `:` (`name_problem` refuses it), which is
    why this cost nothing before — but the function reads as a general
    predicate, and the next caller asking about a name from outside rite
    would not know it was not one.

    ⚠ **Only THIS function became general.** Every other `-t` in this module
    still addresses by target, so it is still only sound for a name rite
    validated. Knowing a stranger's `eu:west` exists does not make it
    addressable.
    """
    binary = _tmux()
    if binary is None:
        return False
    try:
        done = subprocess.run(
            [binary, "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # Nonzero with no server running: nothing exists, which is the answer.
    if done.returncode != 0:
        return False
    return name in done.stdout.splitlines()


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

    def ask() -> tuple[bool, str, str, str, str] | None:
        """(reachable, pane_dead, pane_dead_status, pane_dead_signal, server pid)."""
        try:
            done = subprocess.run(
                [
                    binary,
                    "display-message",
                    "-p",
                    "-t",
                    target,
                    "#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}|#{pid}",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return (False, "", "", "", "")
        raw = (done.stdout or "").strip().split("|")
        return (
            True,
            raw[0] if raw else "",
            raw[1] if len(raw) > 1 else "",
            raw[2] if len(raw) > 2 else "",
            raw[3] if len(raw) > 3 else "",
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
    started = time.monotonic()
    pause = STATUS_FIRST_PAUSE
    reads = 0
    while True:
        reads += 1
        answer = ask()
        if answer is None:
            return Ending(UNCLEAR, detail="could not read the exit status")
        reachable, dead, reported, signal, server = answer
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
            pass
        else:
            break
        # ⚠ The deadline is checked AFTER a read, so the budget always buys at
        # least one observation however tight it is set.
        if time.monotonic() - started >= STATUS_DEADLINE:
            break
        # Dead with no status: the child is very likely an unreaped zombie
        # whose SIGCHLD tmux has not acted on. See `_nudge_reap`.
        _nudge_reap(server)
        time.sleep(pause)
        pause = min(pause * 2, STATUS_MAX_PAUSE)

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
            detail=f"the command was killed by {_signal_name(signal)}",
        )
    if status is None:
        return Ending(
            UNCLEAR,
            detail=(
                "the pane is dead but tmux reported no exit status "
                # ⚠ BOTH, because each alone has misled a reader. The
                # earlier message said "within 3s" from
                # `STATUS_READS * STATUS_PAUSE`, which counted the sleeping
                # and omitted 30 subprocess round-trips — a wait measured at
                # 4.4s against an instant tmux and 10.9s at 0.2s per call.
                # Reads alone say nothing about how long a loaded box was
                # given. Now the loop is time-bounded, so the seconds are
                # real and the reads say how often it looked.
                f"({reported!r}) across {reads} read(s) in "
                f"{time.monotonic() - started:.1f}s, so "
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
    here = attachment(name)
    if not here.known and not human_was_present:
        # ⚠ **NOT "nobody was there".** The sampled flag says nobody was
        # seen, and the live probe could not answer — so the one signal
        # separating a human typing `exit` from an agent finishing is
        # missing, and both produce this exact exit status. FINISHED would
        # resume; refusing costs one command. §5.1.1: a safety property may
        # fail closed, never open.
        return Ending(
            UNCLEAR,
            detail=(
                "it exited cleanly, but whether anybody was attached could "
                f"not be determined ({here.detail}) — and a human typing "
                "`exit` looks exactly like this"
            ),
        )
    if human_was_present or here.attached:
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
        detail = _what_tmux_said(done)
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
    return attachment(name).attached


@dataclass(frozen=True)
class Attachment:
    """Whether a client is attached, and whether we could find out.

    ⚠ **Three answers, for `Liveness`'s reason.** `was_attached` returned a
    bare bool and answered False for *every* way of failing — tmux missing,
    the session gone, a timeout, a refusal, an unparseable reply. In
    `ending` that False is not neutral: it is the difference between QUIT
    and FINISHED, and FINISHED is the one verdict that RESUMES. So a probe
    that merely could not run restarted a session a human had quit, which
    is the behaviour `was_attached` was added to prevent, reached through
    its error path instead of its answer.

    That is the same conflation as the original `liveness` bug — an
    unanswerable question read as a confident negative — and `Ending`'s own
    docstring already forbids it: **`resume` is False whenever the answer
    is not certain.**
    """

    attached: bool
    known: bool
    detail: str = ""


def attachment(name: str) -> Attachment:
    """Is a client attached right now, and could we tell?

    Every failure is `known=False` and carries why. The only real negative
    is tmux answering with a number that is not positive.
    """
    binary = _tmux()
    if binary is None:
        return Attachment(False, known=False, detail="tmux not found")
    if not session_exists(name):
        # Not "nobody is attached". `ending` establishes that the session
        # exists before it asks, so a session that has since vanished means
        # something changed underneath — and whether anybody was there when
        # it did is exactly what cannot now be observed.
        return Attachment(
            False, known=False, detail=f"no session named {name} to ask about"
        )
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, "#{session_attached}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return Attachment(False, known=False, detail=f"could not ask tmux: {e}")
    if done.returncode != 0:
        detail = _what_tmux_said(done)
        return Attachment(False, known=False, detail=f"tmux refused: {detail}")
    raw = (done.stdout or "").strip()
    try:
        return Attachment(int(raw) > 0, known=True)
    except ValueError:
        # ⚠ An empty or unreadable expansion is NOT zero. That parse is the
        # `#{pane_dead_status}` defect in the other field of the same call.
        return Attachment(
            False,
            known=False,
            detail=f"tmux answered {raw!r}, which is not a client count",
        )


ALLOWED_ON_TMUX_ARGV = frozenset(
    {
        MANAGER_ENV,
        "GOOSE_MODE",
        "TMPDIR",
        "GOOSE_PROVIDER",
        "GOOSE_MODEL",
        "OLLAMA_HOST",
        # C6/C26 (`github_access`): a PATH, or a helper's NAME. None is a
        # secret. The token itself is in a 0600 file.
        "GH_CONFIG_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
        # A PATH: where a Claude Manager's own login and transcripts are
        # (`claude_login`). The token is in a 0600 file there, never here.
        "CLAUDE_CONFIG_DIR",
    }
)
"""The ONLY variables that may be passed to a pane with `tmux -e` (C6).

A positive list on purpose. `-e NAME=value` puts the value on tmux's argv,
where `ps -ww` shows it to every local account for the life of the call —
harmless for a Manager's name, a leak for anything secret. The trap is that
the line doing it for the name reads as an established pattern, so "pass the
token the same way" looks like consistency. A list of names to REFUSE would
miss the next credential (Slack brings a second one); a list of names to
ALLOW cannot.

⚠ `GOOSE_MODE` is here because Goose keeps its permission mode in the
environment rather than on a flag, and a mode name (`auto`, `approve`) is
not a secret — it is the same class of value as a Manager's name. It is
listed explicitly rather than admitted by a rule like "anything ending in
_MODE", because the next variable an engine wants here might well be a
token.

⚠ `TMPDIR` is here for the Manager sandbox (B9), and the reason is the same
shape: it is a PATH under the project's own `.rite/user/`, not a secret —
`ps` showing it tells a local account where a scratch directory is, which
the profile beside it already names. It is needed because the engine is
given its own temp directory rather than the system one: Goose panics
without somewhere writable to put `.tmpXXXX` while loading extensions, and
granting the per-user temp root instead was measured to expose every other
process's scratch on the machine.

⚠ `GOOSE_PROVIDER`, `GOOSE_MODEL` and `OLLAMA_HOST` say WHICH model a local
Manager runs, and where. Without them Goose silently used the operator's
global config (measured 2026-09-25: declared `qwen3:8b`, ran
`qwen3-vl:8b-instruct`). A provider name, a model name and an endpoint URL
are configuration and not secrets; a secret an endpoint needs goes in the
role's `credential`, which never travels this way. ⚠ **A URL CAN carry one**,
`http://user:pass@host`, so `supervise` refuses an endpoint with userinfo
rather than put it here.

⚠ The GitHub names (C6/C26) are here for the reason `TMPDIR` is. They say
WHERE the credential is (`GH_CONFIG_DIR`), or which helper `git` should ask
(`GIT_CONFIG_*`: reset, then `gh auth git-credential`). **None of them IS a
credential.** The token is in a 0600 file, in a directory only this
Manager's profile can read."""


def _on_tmux_argv(name: str, value: str) -> list[str]:
    """`["-e", "NAME=value"]`, or raise for a name not cleared to be there.

    Raises rather than dropping it: a variable silently left out is a Manager
    started without something it was meant to have, and the person adding
    one should meet the reason at the moment they add it. See
    `ALLOWED_ON_TMUX_ARGV` and `CLAUDE_OAUTH_ENV`.
    """
    if name not in ALLOWED_ON_TMUX_ARGV:
        raise ValueError(
            f"refusing to pass {name} with `tmux -e`: the value would be on "
            f"tmux's argv, readable by every local account via `ps`. Only "
            f"{sorted(ALLOWED_ON_TMUX_ARGV)} may travel that way — see "
            f"ALLOWED_ON_TMUX_ARGV for why this is an allowlist."
        )
    return ["-e", f"{name}={value}"]


PROMPT_FILE = "prompt.txt"
"""Where this cycle's instruction is written for the engine to read.

⚠ **A FILE, because the environment does not reach the pane.** The prompt
must not be an argument — `tmux new-session <cmd>` puts its command on
tmux's argv, where `ps` shows it to every local account, and a prompt
quotes ticket text, paths and internal names. The obvious alternative was
an inherited variable, and it does not work: **a pane takes its
environment from the tmux SERVER, not from the client that asked**
(measured — a nested `new-session` against an already-running server reads
the server's value, not the caller's). That is exactly why `MANAGER_ENV`
needs `-e`, and `-e` is the argv exposure being avoided.

So the instruction is written to `0600` under the Manager's own directory
and the launch redirects stdin from it. The PATH is on the command line,
which is harmless; the CONTENT never is.
"""

CLAUDE_OAUTH_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
"""The credential an unattended Claude Code run needs, read from the
environment and NEVER handled by rite.

⚠ **It must never be an argument.** The obvious mechanism is the one
`MANAGER_ENV` already uses — `tmux new-session -e VAR=value` — and it is the
wrong one here: that value lands on tmux's own argv, where `ps` shows it to
every local account on the machine. `_on_tmux_argv` refuses it, and a test
inspects every argv a real start executes.

⚠ **CORRECTED (C6): inheritance does NOT reliably deliver it.** This said a
pane "inherits the environment of the process that asked for it". It
inherits the tmux SERVER's — which `PROMPT_FILE` below had already measured.
So the token arrives only when this `rite start` is what starts the server.
Measured through `rite start` with a stub engine recording what it received:

    token exported, no tmux server running        ->  engine has the token
    token exported, operator's tmux already up    ->  engine has NONE

The second is anyone who runs `rite start` from inside tmux. It is OPEN,
not fixed here: every way to deliver the value to an existing server
without argv (`update-environment`, a dedicated rite socket) is a decision
about the operator's tmux and about credential handling, and the one that
needs no decision — `-e` — is the leak this guard exists to stop.

§9.14.7 states the same rule as a compliance constraint: consume this
variable from the environment only; never read, persist, log or transmit
it.
"""


def token_is_absent() -> bool:
    """Is there no usable Claude Code token in this environment?

    Blank counts as absent — an exported-but-empty variable is how a
    sourced env file fails, and treating it as present would send the user
    to the credential-ambiguity message for a case with no ambiguity.
    """
    return not os.environ.get(CLAUDE_OAUTH_ENV, "").strip()


# Shapes an engine prints when it cannot authenticate. Matched to DETECT,
# never to relay: see `_authentication_looks_broken`.
_AUTH_SHAPES = (
    "failed to authenticate",
    "oauth",
    "not authenticated",
    "please run /login",
    "invalid api key",
    "authentication_error",
    "401",
)


def _why_the_engine_died(name: str, launch: str, manager: str = "") -> str:
    """The message when the engine did not survive its settle window.

    ⚠ **rite does not know whether the credential is bad or the engine
    could not read one, and it must not assert either.** Measured on this
    project's own machine: `claude` started interactively in a tmux pane
    authenticates and runs, while `claude -p` in the SAME pane, with the
    same keychain item and a TTY, dies saying "OAuth session expired and
    could not be refreshed" — with the credential valid and eleven days
    from its last write, against a one-year lifetime. The engine's message
    named a refusal for what was a read it never completed.

    Relaying that verbatim would make rite say the same false thing, in the
    first place a new user ever sees something go wrong — and a dogfood
    operator told her credential expired would re-authenticate, fail again,
    and have no way to know why.

    So this says what rite OBSERVED, says what rite CANNOT TELL, and gives
    the one command that discriminates: the engine, run on its own. If it
    starts, the credential is fine and the failure is in how rite invoked
    it.
    """
    if _authentication_looks_broken(_pane_text(name)):
        return (
            f"the session started and exited immediately, and `{launch}` "
            f"printed something about authentication before it died.\n"
            f"  ⚠ rite cannot tell whether your credential is invalid or "
            f"the engine failed to read one — from here those look "
            f"identical, and the engine's own wording does not "
            f"distinguish them.\n"
            f"  The command that tells you apart: run `claude` on its own, "
            f"interactively. If it starts, your credential is FINE and the "
            f"problem is the way it was launched, not your login.\n"
            f"  ⚠ The most likely way it was launched wrong, and it reads "
            f"exactly like an auth failure: **a tmux server was already "
            f"running when you ran `rite start`.** A pane inherits the tmux "
            f"SERVER's environment, not the environment of the command that "
            f"asked for the pane — so a {CLAUDE_OAUTH_ENV} you exported "
            f"reaches the engine only when this `rite start` is what started "
            f"the server. Measured; see CLAUDE_OAUTH_ENV. Run `tmux "
            f"kill-server` (or start from a shell with no tmux running) and "
            f"try again.\n"
            f"  (rite deliberately does not repeat the engine's message "
            f"here: it has been measured saying 'expired' about a "
            f"credential with a year left on it.)\n"
            f"  ⚠ The tmux session is still there holding the name — "
            f"`remain-on-exit` keeps it so the exit status can be read. "
            f"Clear it with `rite manager stop {manager or '<manager>'}` "
            f"before you try again, or the next `rite start` will refuse "
            f"on the left-over session rather than on this."
        )
    return (
        f"the session started and exited immediately, so `{launch}` is "
        f"not running. `tmux new-session` reports whether a session was "
        f"CREATED, not whether the command in it survived. Run "
        f"`{launch}` directly to see why."
    )


def _signal_name(raw: str) -> str:
    """A signal's name, from whatever this tmux spells it as.

    ⚠ **PLATFORM VOCABULARY, and it turned CI red for nine commits.**
    `#{pane_dead_signal}` is `kill` on macOS and `9` on Linux for the same
    death, so a message built from the raw value read "killed by SIGKILL"
    on one machine and "killed by SIG9" on the other — and a test asserting
    the macOS spelling failed on Linux from the moment it landed.

    This is the same split that produced three defects earlier in this
    release. The lesson each time: normalise at the boundary where the
    platform's word arrives, never compare the platform's word downstream.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        try:
            return signal.Signals(int(raw)).name
        except ValueError:
            return f"SIG{raw}"
    upper = raw.upper()
    return upper if upper.startswith("SIG") else f"SIG{upper}"


def _authentication_looks_broken(pane_text: str) -> bool:
    """Did the engine die complaining about credentials?

    ⚠ **Matched to DETECT, and the pane is never relayed.** Two reasons,
    and the second is the one that matters. A pane can carry secrets — a
    launch line with an injected token is the measured case elsewhere in
    this project — so echoing it back is a leak waiting for the right
    engine. And rite must not repeat the engine's CLAIM as if it were
    rite's own finding; what rite knows is that the engine said something
    about authentication and then died, which is a fact about the output,
    not about the credential.
    """
    low = pane_text.lower()
    return any(shape in low for shape in _AUTH_SHAPES)


_APPROVAL_SHAPES = (
    "tool approval required",
    "require an interactive terminal",
    "modes require an interactive",
)


def approval_blocked(pane: str) -> bool:
    """Did the engine die because it wanted an approval nobody could give?

    ⚠ **This is what a refusal looks like for an engine whose permission mode
    is WHOLE-SESSION.** Claude refuses one command and carries on; Goose in
    a non-interactive run refuses the whole session on the first tool call.
    Measured 2026-09-24, verbatim:

        Error: Tool approval required in non-interactive mode with
        GooseMode::approve. This is an invalid configuration —
        Approve/SmartApprove modes require an interactive terminal. Use
        GooseMode::Auto for headless sessions.

    ⚠ **Matched to DETECT, and the pane is never relayed** — the same rule
    as `_authentication_looks_broken` beside it, and for the same reason: a
    pane can carry secrets, so echoing it back is a leak waiting for the
    right launch line.

    rite sets the mode itself, so a Manager it started should not reach this.
    It is detected anyway because the one case that produces it is a mode
    arriving from somewhere rite does not control — a managed goose config,
    or an operator's environment on a path that bypasses the launch — and
    that is exactly the case with nothing else to explain it.
    """
    low = _pane_text(pane).lower() if pane else ""
    return any(shape in low for shape in _APPROVAL_SHAPES)


def pane_text_for_detection(name: str) -> str:
    """What the pane shows, for DETECTION only — named so a caller outside
    this module cannot mistake it for something to relay.

    ⚠ **A pane can carry secrets** (a launch line with an injected token is
    the measured case elsewhere in this project), so every caller matches
    against this and returns its own words.
    """
    return _pane_text(name)


def _pane_text(name: str) -> str:
    """What the pane shows, for DETECTION only. Never returned to a user."""
    binary = _tmux()
    if binary is None:
        return ""
    try:
        done = subprocess.run(
            [binary, "capture-pane", "-p", "-t", name],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "") if done.returncode == 0 else ""


def _nudge_reap(server_pid: str) -> None:
    """Send the tmux server a SIGCHLD, so it reaps a pane it has not reaped.

    🔴 **ON LINUX THE LAST EXIT IS NEVER RECORDED — tmux RUNS ONE EXIT
    BEHIND.** Measured 2026-09-26 on Ubuntu 24.04, tmux 3.4, libevent 2.1,
    kernel 7.0, with no rite, no Landlock and no load: panes `a`, `b`, `c`
    running `exit 3`, `exit 5`, `exit 7` one after another read `a:[]`, then
    `a:[3] b:[]`, then `b:[5] c:[]`. Each status arrives only when the NEXT
    child of the server exits. The pane's process sits as a zombie of the
    tmux server, whose SIGCHLD is caught, not blocked and not ignored.

    So a Manager that was the last thing to end waited out the whole
    deadline and was reported `unclear` — a clean exit, not resumed. That
    was blamed on load and then on an unread pane id; both were
    coincidental. Load only changed whether some other child happened to
    exit inside the window. Measured on a real Manager: pane `%4` dead,
    `status=[]` after 30.6s; one `kill -CHLD` to the server and it read
    `status=[0]` at once.

    This is that `kill -CHLD`. tmux's handler only runs `waitpid(WNOHANG)`
    over its own children, so a live pane is untouched and a signal with
    nothing to reap does nothing. The pid is tmux's own `#{pid}` for the
    server `ending` is already talking to. A pid that cannot be read, or a
    signal that cannot be sent, leaves `ending` exactly where it was: still
    waiting, and still `unclear` if nothing arrives.
    """
    try:
        pid = int(server_pid)
    except ValueError:
        return
    if pid <= 1:
        return
    try:
        os.kill(pid, signal.SIGCHLD)
    except OSError:
        pass


def _pane_id_or_why(name: str) -> tuple[str, str]:
    """`(pane_id, why_not)` — the session's pane id, or the reason there isn't one.

    ⚠ **THIS USED TO RETURN `""` FOR EVERY FAILURE, AND THAT WAS THE DEFECT.**
    An empty string is a value meaning "no answer" that every caller then used
    as if it were an answer: `ending` does `target = pane or name` and falls
    back to addressing the SESSION, which the fallback's own comment called
    "the old behaviour". Measured 2026-09-26 on a 4-core Ubuntu VM, two
    Managers and an 8B model: the recorded pane was EMPTY for both, the panes
    that died were `%4` and `%0` each holding `status=0`, and `ending`
    reported "no exit status across 34 read(s) in 30.6s" — so a Manager that
    finished cleanly was `unclear`, nothing resumed, and a routed message had
    nowhere to land.

    Nothing about that was slow: tmux answered in 1ms idle and 4-5ms under
    load, worst 9ms, against a 30s budget. The id was never captured and the
    failure was swallowed, so there was nothing to read in the record and no
    message saying why.

    So the reason comes back with the value, and `start_session` says it out
    loud. A caller may still fall back, but it does so knowing.
    """
    binary = _tmux()
    if binary is None:
        return "", "tmux is not on PATH"
    try:
        done = subprocess.run(
            [binary, "display-message", "-p", "-t", name, "#{pane_id}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"asking tmux failed: {type(exc).__name__}: {exc}"
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()
        return "", f"tmux exited {done.returncode}: {said or 'no output'}"
    got = (done.stdout or "").strip()
    if not got:
        return "", "tmux exited 0 and printed nothing"
    if not got.startswith("%"):
        # A pane id is `%N`. Anything else is a format that did not expand,
        # which would be stored and later addressed as if it were a pane.
        return "", f"tmux printed {got!r}, which is not a pane id"
    return got, ""


def _remain_on_exit(name: str) -> list[str]:
    """The tail that makes `new-session` keep its pane after the command ends.

    `remain-on-exit on` so the pane survives its command and carries
    `#{pane_dead_status}` — without it tmux destroys the session and the exit
    status goes with it, leaving finished, quit and crashed
    indistinguishable. It also leaves the conversation readable after the
    supervisor stops.

    ⚠ **CHAINED INTO THE SAME tmux COMMAND, because a second call RACES the
    command it is protecting (C15).** This was a separate `set-window-option`
    run after `new-session` returned, with `check=False`. A command that
    exits before that call arrives takes its window with it, and the call
    failed with "no such window" — swallowed. Measured on tmux 3.7c with an
    instant-exit command, 50 launches each:

        separate call:                        session held  4/50
        `new-session … ; set-window-option`:  session held 50/50

    One client's queued commands run before the server reaps the child, so
    the option is in place before the exit can be processed. Without this,
    an engine that dies at once — a bad login, a `--resume` the provider has
    forgotten — lost its exit status, and under load a slower command lost it
    too: the named cause-candidate for the real-tmux tests that failed only
    under full-suite load ("remain-on-exit did not hold the session").

    ⚠ **ONE function, used by both `start` and the probe, and that is the
    point.** They set it separately before, with `set-option`; on Linux CI
    the probe then reported the capability present while real sessions
    behaved as if it were absent. **A probe that does not use the production
    call measures something else.**

    `set-window-option` because that is what `remain-on-exit` is. macOS
    accepted the session form and appeared to honour it; that is the
    platform telling you what you want to hear.
    """
    return [";", "set-window-option", "-t", name, "remain-on-exit", "on"]


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
            [binary, "new-session", "-d", "-s", name, "sh", *_remain_on_exit(name)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if made.returncode != 0:
            return False
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
