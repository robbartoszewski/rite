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

from rite_ai.managers import ManagerInstance, record_instance
from rite_ai.managers.session import StartResult, liveness
from rite_ai.managers.session import start as start_session

POLL_SECONDS = 2.0

# Loop verdicts that end the lifecycle (§9.14.4). `closed` is here because a
# window authorising zero Workers is the user saying "not now", and a Manager
# that kept spending through it would be ignoring them.
STOP_VERDICTS = frozenset({"idle", "deadlocked", "unknown", "closed"})


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


@dataclass
class Cycle:
    """One session's life, for the report."""

    number: int
    session: str
    resumed_from: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0


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
    verdict: object = None,
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
    begin = clock()
    deadline = begin + window_seconds if window_seconds > 0 else None
    launch = starter if callable(starter) else _default_starter
    next_id = resume_id_for if callable(resume_id_for) else (lambda _r, _m: "")

    cycles: list[Cycle] = []
    resume_from = ""

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

        cycle = Cycle(
            number=len(cycles) + 1,
            session=result.session,
            resumed_from=resume_from,
            started_at=clock(),
        )
        cycles.append(cycle)

        # Wait for it to end. The human can attach to it throughout — that
        # is §9.14.3, and it is why "nobody is watching" is false here in a
        # way it is not for a cron tick.
        while liveness(result.session).alive:
            if deadline is not None and clock() >= deadline:
                break
            time.sleep(poll)
        cycle.ended_at = clock()

        resume_from = next_id(root, manager)


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
