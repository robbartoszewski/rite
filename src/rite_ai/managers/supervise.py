"""Keeping a Manager working: start, notice it ended, bring it back, stop.

**This is the foreground process, and that is the whole compliance
argument.** §9.14.6 permits resumption only while the human's own invocation
is still live: `rite start <manager>` does not return and then resume from
somewhere else, it IS the process, so every session it starts — first or
resumed — begins inside a command a human typed and can see. When the
terminal dies, nothing brings a session back. §9.12 needs no amendment for
that, and would for anything more.

⚠ **THE ADAPTER INTERFACE IS UNSTABLE AND THIS IS NOT IT.** There is no
adapter here, no protocol, and nothing for a second engine to implement:
`launch_command` treats the engine string as **an executable name** and
appends `--resume <id>` because that is what Claude Code takes. That is a
fallback, not a contract. A local model is a request/response — no pane,
nothing to resume, and `settled_alive` would reject it for exiting
immediately, which is correct behaviour for a shape this module cannot
express.

Deliberately not generalised yet (§9.14.2, D-63): one implementation
produces an interface shaped like that implementation, and the state layer
is genuinely backend-agnostic only because git, a filesystem and a
key-value store all existed before it froze. The second shape should force
the boundary rather than be guessed at from the first.

**Two bounds, and both are checked before a session starts rather than
after.** A ceiling applied afterwards is a report. The session COUNT is the
only spend-adjacent quantity rite can enforce (§2.6.1, D-69) and it does
not bound cost — one session may run arbitrarily long — so the wall-clock
window is what actually limits duration, and §9.14.5 says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.managers import ManagerInstance, forget_instance, record_instance
from rite_ai.managers.prompt import deliver as deliver_prompt
from rite_ai.managers.session import StartResult, ending, liveness, was_attached
from rite_ai.managers.session import start as start_session
from rite_ai.managers.session import stop as stop_session

POLL_SECONDS = 2.0

# Loop verdicts that end the lifecycle (§9.14.4). `closed` is here because a
# window authorising zero Workers is the user saying "not now", and a Manager
# that kept spending through it would be ignoring them.
STOP_VERDICTS = frozenset({"idle", "deadlocked", "unknown", "closed"})
CONTINUE_VERDICTS = frozenset({"ready", "saturated", "blocked"})
"""⚠ **Stated, so that continuing is a DECISION rather than a fallthrough.**

A draft had only `STOP_VERDICTS` and `if answer in STOP_VERDICTS: return` —
so everything else launched a session, including everything that is not a
verdict at all. Measured with a captured starter: `None`, `""`, `"Idle"`
with the wrong case, and `"error: cannot read"` each started one. **A
verdict function that could not answer spent a session.**

Not reachable today, which is the argument for fixing it now rather than
later: `_loop_verdict` coerces with `or "unknown"`, wraps in `str()` and
catches everything, so production can only produce a real verdict. That is
the same shape as the duplicate guard that failed open while deterministic
session naming quietly did the work — protection nobody had recorded as
load-bearing, one refactor from live, in the code that spends money.

The asymmetry decides the default for anything in NEITHER set: an
unrecognised verdict that stops costs a restart, and one that continues
costs quota. §5.1.1 — a safety property may fail closed, never open.

Together they are exhaustive over `loop`'s seven verdicts, and a test
asserts it, so an eighth cannot be added without classifying it."""


def launch_command(engine: str, resume_id: str = "") -> str:
    """What to run in the pane.

    ⚠ **The engine string is used as an EXECUTABLE NAME.** There is no
    registry, no adapter and no way to express that an engine takes
    different flags — see this module's docstring. `--resume <id>` is
    Claude Code's spelling and is appended unconditionally, which is wrong
    for any engine that spells it differently and meaningless for one with
    no session to resume.
    """
    base = engine or "claude"
    if resume_id:
        return f"{base} --resume {resume_id}"
    return base


def _default_resume_id(root: Path, manager: str, since: float = 0.0) -> str:
    """The provider session to carry on from.

    ⚠ **A draft defaulted this to `lambda: ""`**, so `launch_command` got no
    id, every "resume" ran a bare `claude`, and each cycle began a FRESH
    context with the ticket half-done and no memory of it — identical from
    outside to a resume that worked. A default that silently means "do
    nothing" is an uncalled function wearing a different hat.

    `since` scopes it to transcripts touched after this cycle began, so the
    supervisor resumes the session IT started rather than the newest file on
    disk, which could be last week's.
    """
    from rite_ai.managers.transcripts import latest_session_id

    return latest_session_id(root, since=since)


def _stopped_because(how, started: int) -> str:
    """Say which of the three happened, because a restart is right for
    exactly one and a human needs to know which they are looking at."""
    if how.kind == "quit":
        return (
            f"stopped after {started} session(s): {how.detail}. NOT restarted "
            "— if you meant to keep going, run `rite start` again."
        )
    if how.kind == "crashed":
        return (
            f"stopped after {started} session(s): {how.detail}. Not restarted "
            "— a crash that repeats would repeat at your expense. The pane is "
            "still there to read."
        )
    return (
        f"stopped after {started} session(s): {how.detail}, so whether it "
        "finished or was ended cannot be told. Not restarted — refusing when "
        "unsure costs you one command; restarting when unsure spends money."
    )


@dataclass
class Cycle:
    """One session's life, for the report."""

    number: int
    session: str
    resumed_from: str = ""
    prompted: bool = False
    started_at: float = 0.0
    ended_at: float = 0.0
    attended: bool = False
    ending: str = ""


@dataclass
class SuperviseResult:
    ok: bool
    reason: str
    cycles: list[Cycle] = field(default_factory=list)

    @property
    def sessions_started(self) -> int:
        return len(self.cycles)


def supervise(
    root: Path,
    manager: str,
    *,
    engine: str = "",
    max_sessions: int,
    window_seconds: float,
    prompt: str = "",
    verdict: object = None,
    note: object = None,
    starter: object = None,
    resume_id_for: object = None,
    poll: float = POLL_SECONDS,
    now: object = None,
) -> SuperviseResult:
    """Run the Manager until a bound or a stop verdict ends it.

    `verdict`, `starter`, `resume_id_for` and `now` are injectable so this
    can be driven without spending anything. They are NOT an abstraction
    boundary — see the module docstring; they exist so the bounds can be
    verified by running rather than by reading, which is the only way to
    know a ceiling bounds anything.
    """
    clock = now if callable(now) else time.time
    # Injectable so a test can read what a human would have been told,
    # and a no-op by default so nothing prints from a library call.
    say = note if callable(note) else (lambda _m: None)
    hand_over = deliver_prompt
    begin = clock()
    deadline = begin + window_seconds if window_seconds > 0 else None
    launch = starter if callable(starter) else _default_starter
    next_id = resume_id_for if callable(resume_id_for) else _default_resume_id

    cycles: list[Cycle] = []
    resume_from = ""
    live = ""

    while True:
        # BOTH bounds before starting. A ceiling checked afterwards reports
        # rather than bounds, and the window is what limits cost because the
        # count does not (§9.14.5).
        if len(cycles) >= max_sessions:
            return SuperviseResult(
                True,
                f"ceiling reached: {max_sessions} session(s) started. This is "
                f"a COUNT, not a spend limit — a session may run for any "
                f"length of time inside it.",
                cycles,
            )
        if deadline is not None and clock() >= deadline:
            return SuperviseResult(
                True,
                f"window elapsed after {int(clock() - begin)}s "
                f"({len(cycles)} session(s) started)",
                cycles,
            )

        if callable(verdict):
            answer = verdict(root)
            if answer in STOP_VERDICTS:
                return SuperviseResult(
                    True,
                    _why(answer, len(cycles)),
                    cycles,
                )
            if answer not in CONTINUE_VERDICTS:
                # NOT a fallthrough to "carry on". Whatever this is, it is
                # not an answer, and the next step spends money.
                return SuperviseResult(
                    True,
                    f"stopped after {len(cycles)} session(s): the loop "
                    f"returned {answer!r}, which is not one of its verdicts. "
                    f"Refusing to start another session on an answer nobody "
                    f"recognises — an unrecognised verdict that stops costs a "
                    f"restart, one that continues costs quota.",
                    cycles,
                )

        result: StartResult = launch(
            root,
            manager,
            engine=engine,
            resume_id=resume_from,
            max_sessions=max_sessions,
            window_seconds=window_seconds,
        )
        if not result.ok:
            return SuperviseResult(False, result.message, cycles)

        live = result.session
        cycle = Cycle(
            number=len(cycles) + 1,
            session=result.session,
            resumed_from=resume_from,
            started_at=clock(),
        )
        cycles.append(cycle)

        # ⚠ THE FIRST SESSION ONLY (D-90). A resumed session already carries
        # the context the prompt would establish, and re-issuing an
        # instruction into a conversation that is mid-task is the same class
        # of error as restarting a session a human deliberately quit: the
        # tool telling the agent to begin something it is in the middle of.
        # `resume_from` is the observable — empty means this is a fresh
        # context — rather than `len(cycles) == 1`, which would also be true
        # of a first cycle that was itself a resume.
        if prompt and not resume_from:
            handed = hand_over(result.session, prompt)
            cycle.prompted = handed.ok
            if not handed.ok:
                # Reported, NOT fatal. A Manager whose prompt did not arrive
                # is still a running session the human is paying for, and
                # killing it to signal a delivery failure would destroy work
                # to report a problem.
                say(
                    f"warning: the Manager's prompt may not have arrived — "
                    f"{handed.detail}. Attach with `tmux attach -t "
                    f"{result.session}` and check."
                )

        # Wait for it to end. The human can attach throughout — that is
        # §9.14.3, and it is why "nobody is watching" is false here in a way
        # it is not for a cron tick. Whether anybody DID attach is recorded
        # while waiting, because attachment is a moment and the question
        # `ending` asks is whether somebody was ever there.
        attended = False
        try:
            while liveness(result.session).alive:
                if deadline is not None and clock() >= deadline:
                    break
                attended = attended or was_attached(result.session)
                time.sleep(poll)
        except KeyboardInterrupt:
            # ⚠ A HUMAN SAYING STOP, which is a different event from a bound
            # being reached — §9.14.12. A bound leaves the session alive
            # deliberately, because the user may be mid-conversation and a
            # ceiling is an accounting limit. Ctrl-C is not an accounting
            # limit, and leaving a live session spending quota with only the
            # restarts halted is not what was asked for.
            return _torn_down(root, manager, live, cycles, say)
        cycle.ended_at = clock()
        cycle.attended = attended

        how = ending(result.session, human_was_present=attended)
        cycle.ending = how.kind
        if not how.resume:
            return SuperviseResult(
                how.kind != "crashed",
                _stopped_because(how, len(cycles)),
                cycles,
            )

        # Only now, and only for a session that finished cleanly with
        # nobody attached, is a resume the right thing.
        resume_from = next_id(root, manager, cycle.started_at)
        if not resume_from:
            return SuperviseResult(
                True,
                f"stopped after {len(cycles)} session(s): the session "
                "finished but no transcript was found to resume from, so "
                "continuing would start a FRESH context rather than carry "
                "the work on. Refused rather than silently restarting.",
                cycles,
            )


def _torn_down(root, manager: str, session: str, cycles, say) -> SuperviseResult:
    """Ctrl-C: end the session, drop the record, and SAY SO.

    ⚠ **The record must go or the next `rite start` believes this Manager
    is still running** — the stale-lock defect in a new place, and that
    class wedged the loop earlier this week. `forget_instance` existed with
    zero callers until this one; a decision with no mechanism under it is
    indistinguishable from a feature nothing calls.

    ⚠ **It must say what it did.** Silence after Ctrl-C is
    indistinguishable from a signal that did not land, which is how a user
    ends up pressing it three times and killing something mid-write.

    Ordered so a failure to kill does not skip the record: both run, and
    the report names what actually happened rather than what was intended.
    """
    gone = stop_session(session) if session else None
    forget_instance(root, manager)
    if gone is not None and not gone.ok:
        say(
            f"warning: could not stop {session} — {gone.detail}. The record "
            f"is cleared, so `rite start` will not think it is running; "
            f"`tmux kill-session -t {session}` ends it by hand."
        )
        return SuperviseResult(
            False,
            f"stopped supervising Manager {manager!r}, but its session "
            f"{session} may still be running — {gone.detail}",
            cycles,
        )
    if gone is not None and gone.killed:
        return SuperviseResult(
            True, f"stopped Manager {manager!r} and its session", cycles
        )
    return SuperviseResult(
        True,
        f"stopped Manager {manager!r} — its session had already ended",
        cycles,
    )


def _why(answer: str, started: int) -> str:
    """`idle` is a completion; the rest are faults. A lifecycle that exits
    identically for all of them tells a human "finished" when it means
    "jammed" (§9.14.4)."""
    if answer == "idle":
        return f"done: the board has nothing ready ({started} session(s))"
    if answer == "closed":
        return (
            f"stopped: the schedule authorises no Workers in this window "
            f"({started} session(s)). Nothing restarts it when the window "
            f"opens — run `rite start` again."
        )
    return (
        f"stopped on '{answer}' after {started} session(s) — this is a fault, "
        f"not a completion. `rite loop status` says what is stuck."
    )


def _default_starter(root, manager, *, engine, resume_id, max_sessions, window_seconds):
    result = start_session(
        root,
        manager,
        engine=engine,
        command=launch_command(engine, resume_id),
        max_sessions=max_sessions,
        window_seconds=window_seconds,
    )
    if result.ok:
        record_instance(
            root,
            ManagerInstance(
                name=manager,
                session=result.session,
                engine=launch_command(engine, resume_id),
                max_sessions=max_sessions,
                window_seconds=window_seconds,
            ),
        )
    return result
