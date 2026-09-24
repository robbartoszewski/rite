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

import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.managers import (
    designate,
    designated,
    designation_path,
    forget_instance,
    manager_dir,
)
from rite_ai.managers.mailbox import INBOX, delivery_note, how_to_reply
from rite_ai.managers.mailbox import take as take_mail
from rite_ai.managers.mailbox import waiting as mail_waiting
from rite_ai.managers.permissions import (
    allowed,
    announcement,
    launch_arguments,
    refusal,
    settings_path,
    write_settings,
)
from rite_ai.managers.session import (
    PROMPT_FILE,
    StartResult,
    ending,
    liveness,
    session_name,
    was_attached,
)
from rite_ai.managers.session import start as start_session
from rite_ai.managers.session import stop as stop_session
from rite_ai.managers.transcripts import refused_commands, session_id_problem

POLL_SECONDS = 2.0

CONTINUATION = (
    "Continue the work you were doing in this session. Re-read your own "
    "last messages for where you left off, do the next step, and stop."
)
"""What a RESUMED cycle is told, and why it is not the opening prompt.

⚠ **D-90 said the prompt goes to the first session only, and that was
right while the engine was a REPL.** Later cycles were the same
conversation continuing, so there was nothing to say. `-p` changed the
premise: each cycle is now a separate invocation that exits, and one
launched with no input does not continue, it EXITS 1 — measured, "Input
must be provided either through stdin or as a prompt argument when using
--print". So a resumed cycle needs an instruction.

⚠ **It must not be the opening prompt again.** Re-issuing "do X" to a
session that already did X is how work happens twice, which is the concern
D-90 recorded in the first place. This says to carry on, and the session's
own history — reached through `--resume` — is where "from what" comes
from.
"""

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


def launch_command(
    engine: str,
    resume_id: str = "",
    prompt_path: str = "",
    permission: str = "",
) -> str:
    """What to run in the pane.

    ⚠ **The engine string is used as an EXECUTABLE NAME.** There is no
    registry, no adapter and no way to express that an engine takes
    different flags — see this module's docstring. `--resume <id>` is
    Claude Code's spelling and is appended unconditionally, which is wrong
    for any engine that spells it differently and meaningless for one with
    no session to resume.
    """
    # ⚠ **`-p` IS THE CYCLE BOUNDARY.** An interactive engine never exits,
    # so a supervisor wanting cycles would have to infer one ended from
    # something else — idleness, quiet output, a timer — and every one of
    # those is a classifier over a signal that means other things too,
    # which is the defect class `ending` spent this release shedding. With
    # `-p` the engine's own exit IS the boundary, and `ending` already
    # reads exit statuses for a living.
    #
    # It is appended unconditionally, for the same reason `--resume` is and
    # with the same cost: this module has no registry and treats the engine
    # string as an executable name (see the module docstring). `-p` is
    # Claude Code's spelling. The config validator accepts only `claude`,
    # `human` and `local:<class>` as engines, so the one engine this
    # actually launches today is the one the flag is for.
    # ⚠ **THE PROMPT ARRIVES ON STDIN, FROM THE ENVIRONMENT.** `claude -p`
    # requires its input AT LAUNCH — measured: with none it exits 1 saying
    # "Input must be provided either through stdin or as a prompt argument
    # when using --print". rite used to type the prompt in after the
    # session started, which works for a REPL and cannot work for a command
    # that has already exited by then.
    #
    # The prompt is REDIRECTED FROM A FILE (`< <path>`), so the instruction
    # never becomes an argument: `tmux new-session <cmd>` puts its command
    # on tmux's argv, where `ps` shows it to every local account, and a
    # prompt quotes ticket text, paths and internal names. Same reason the
    # token is never an argument. (An earlier draft passed it through
    # `$RITE_PROMPT` in the inherited environment; the file is what ships,
    # and `session.PROMPT_FILE` records why.)
    base = f"{engine or 'claude'} -p"
    if permission:
        # ⚠ **EVERY cycle, not just the first.** Resuming with `-p` does not
        # restore the mode a session was in — that restoration explicitly
        # excludes `-p` — so a resumed cycle launched without this is a
        # Manager that can no longer act AND still exits 0, which `ending`
        # reads as a clean finish. D-90's shape exactly, in a second place.
        base = f"{base} {permission}"
    if resume_id:
        # ⚠ **REFUSES rather than escapes, and raises rather than drops the
        # flag.** This string is handed to `tmux new-session`, which runs it
        # through `sh -c` — so an unchecked id is a shell command. Measured
        # before `session_id_problem` existed: a transcript whose
        # `sessionId` was `abc$(touch FILE)` created the file when the
        # session started.
        #
        # Dropping a bad id and returning `base` would start a FRESH context
        # with the ticket half-done — the silent failure this whole path
        # exists to prevent — so a caller that reaches here with one has a
        # defect and is told, loudly, at the boundary that touches the
        # shell. `latest_session_id` already filters, which makes this the
        # second of two checks rather than the only one: the filter keeps
        # the supervisor working, and this keeps a future caller from
        # reintroducing the hole.
        problem = session_id_problem(resume_id)
        if problem:
            raise ValueError(
                f"refusing to build a launch command with a resume id that "
                f"{problem}. This string is run by a shell."
            )
        base = f"{base} --resume {resume_id}"
    return f"{base} < {shlex.quote(str(prompt_path))}" if prompt_path else base


def _say_refusals(root: Path, since: float, say) -> None:
    """Tell the user what the engine refused, and how to permit it.

    ⚠ **C21, and the reason it is here rather than in a report.** A refusal
    the user never sees is the same defect as a silent one: the v0.5.1
    acceptance run was refused `rite loop status` on three consecutive
    cycles, each exited 0, and nothing rite printed said so — the only
    record was a sentence the MODEL chose to write. This reads the denial
    out of the transcript instead of hoping it was mentioned.

    ⚠ **A command rite's own list covers, refused anyway, is a DIFFERENT
    fault and is said differently.** It means the engine never applied
    rite's settings — most likely because a settings file that fails
    validation is silently ignored under `-p` (measured, `claude --help`) —
    and telling that user to add a line they already have would send them
    in the wrong direction.
    """
    for command in dict.fromkeys(refused_commands(root, since)):
        if allowed(command):
            say(
                f"refused: {command.strip()!r} — which rite's own allowlist "
                f"DOES cover. The engine did not apply "
                f"{settings_path(root)}; under `-p` a settings file that "
                f"fails validation is ignored without a message. Check that "
                f"file parses, and check `permissions.deny` in "
                f"{Path('.claude') / 'settings.json'}."
            )
        else:
            say(refusal(command, root))


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


def _could_not_continue(manager: str) -> str:
    """One wording, two routes to it.

    A designation fails either because the provider has forgotten the
    session (the start does not take) or because what was written down
    cannot be used at all (`designated` refuses it). Both are "there was one
    and you are not getting it", which is what the user needs to know, so
    both say this. Two copies of the sentence would drift, which is how
    `rite status` and `rite start` came to describe one session in
    contradictory words.
    """
    return (
        f"the previous session for {manager!r} could not be continued, so "
        f"this run starts FRESH. It will not remember the earlier "
        f"conversation."
    )


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
    mail_waiting: bool = False
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
    fresh: bool = False,
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
    begin = clock()
    deadline = begin + window_seconds if window_seconds > 0 else None
    launch = starter if callable(starter) else _default_starter
    next_id = resume_id_for if callable(resume_id_for) else _default_resume_id

    # ⚠ Said ONCE and passed EVERY cycle. Every cycle because nothing
    # carries a permission mode into `-p`; said because this grant is total
    # and the one line below is all that stands between a user and a
    # surprise.
    say(announcement(manager))
    # ⚠ **Written before the first launch and passed on EVERY cycle.** The
    # file is rewritten from code each run so the list a Manager gets is the
    # list this release ships; the arguments are re-passed because nothing
    # carries a permission decision into `-p` — the same reason the flag
    # they replace had to be.
    permission = launch_arguments(write_settings(root))

    cycles: list[Cycle] = []
    live = ""

    # ⚠ CONTINUATION IS THE DEFAULT. Cycle one used to begin with no memory,
    # so the work a Manager did yesterday was unreachable today. The id is
    # DESIGNATED rather than chosen: there is one candidate and no heuristic
    # to tune, which is the same move as preferring a property the input
    # must satisfy over a list to reject.
    #
    # ⚠ `--fresh` REWRITES the designation rather than skipping it once.
    # Settled, not emergent: skipping would orphan the new session — the
    # user starts over, works all day, and tomorrow's bare `rite start`
    # silently returns to the conversation they deliberately abandoned.
    resume_from = "" if fresh else designated(root, manager)
    continuing = bool(resume_from)
    tried_designation = continuing
    if not fresh and not continuing:
        # ⚠ A DIFFERENT FACT from "the one you had is gone", and it reads
        # differently on purpose — the timezone precedent, where an unset
        # zone and a rejected one do not print the same line.
        #
        # ⚠ **A file that is THERE and gave nothing is the second fact, not
        # the first.** `designated` returns "" both when nothing was ever
        # written and when what was written cannot be used — a corrupt file,
        # or an id `launch_command` would refuse. Reporting the second as
        # "no previous session recorded" tells a user their work was never
        # designated when it was, and is gone.
        if designation_path(root, manager).exists():
            say(_could_not_continue(manager))
        else:
            say(
                f"no previous session recorded for {manager!r}, so this run "
                f"starts fresh."
            )

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

        try:
            # ⚠ **THE PREVIOUS SESSION IS ENDED HERE, and the position is the
            # point.** `start` sets `remain-on-exit on` so the exit status
            # survives for `ending` to read (§9.14.4) — which leaves the
            # finished session ALIVE, holding the name, and `session_name` is
            # deterministic. So `tmux new-session` answered `duplicate session`
            # and every resume died there. The two halves of this release were
            # mutually exclusive:
            #
            #     remain-on-exit on   -> FINISHED reachable, the name is taken
            #     remain-on-exit off  -> the name is free, ending() only ever
            #                            says `unclear`
            #
            # No test saw it because every multi-cycle test injected `starter`,
            # so `new-session` was never called a second time in the suite — and
            # the one test that did drive real tmux twice performed this stop
            # ITSELF, under a comment saying it was doing what the loop does.
            #
            # ⚠ **AFTER the bounds and the verdict, not on the resume path.**
            # Reaching the ceiling, running out of window, or being told to stop
            # must leave the pane where it is: its conversation is what a human
            # attaching afterwards reads, and a bound is an accounting limit
            # rather than an instruction to tidy up. Ending it as soon as the
            # resume was decided took that away from every run that stopped on a
            # bound, and `test_reaching_the_ceiling_does_not_stop_the_session`
            # caught it. Here, the only session ended is one about to be
            # REPLACED by its own continuation.
            #
            # `_torn_down`'s order, for `_torn_down`'s reason: the record goes
            # even if the kill fails, or the next `rite start` believes this
            # Manager is still running.
            if live:
                closed = stop_session(live)
                forget_instance(root, manager)
                if closed is not None and not closed.ok:
                    return SuperviseResult(
                        False,
                        f"stopped after {len(cycles)} session(s): it finished "
                        f"and was ready to resume, but its tmux session could "
                        f"not be ended — {closed.detail}. The next cycle would "
                        f"collide with it under the same name, so nothing was "
                        f"started. End it with `tmux kill-session -t {live}` "
                        f"and run `rite start` again.",
                        cycles,
                    )
            # ⚠ EVERY cycle carries an instruction, and a resumed one
            # carries a DIFFERENT instruction. See `CONTINUATION` for why
            # this amends D-90 rather than working around it.
            cycle_prompt = CONTINUATION if resume_from else prompt
            # ⚠ **THE ONE HOOK.** Messages are appended to the instruction
            # already composed for this cycle rather than delivered by a
            # second mechanism. The engine runs with `-p` and has read its
            # stdin before anything could type into the pane, so this
            # composition point is where a Manager reliably reads — and a
            # second delivery path would be a second thing to keep correct.
            #
            # Taken, not peeked: a message delivered stays delivered, so a
            # Manager is not told the same thing every cycle until it acts.
            waiting_for_it = take_mail(root, manager, INBOX)
            if waiting_for_it:
                say(f"delivering {len(waiting_for_it)} message(s) to {manager!r}")
            # Composed once, for both launches below: the fallback needs the
            # same mail and the same reply instructions, differing only in
            # which opening text it starts from.
            extras = delivery_note(waiting_for_it) + how_to_reply(root, manager)
            fresh_prompt = prompt + extras
            cycle_prompt = cycle_prompt + extras
            result: StartResult = launch(
                root,
                manager,
                engine=engine,
                resume_id=resume_from,
                prompt=cycle_prompt,
                permission=permission,
                max_sessions=max_sessions,
                window_seconds=window_seconds,
            )
            if not result.ok and tried_designation and not cycles:
                # ⚠ THE EXISTENCE CHECK IS ON THE SESSION, NOT THE FILE. A
                # designation can be present and perfectly readable while
                # the provider has forgotten that conversation. The property
                # is "the resume did not take" — the start failing — and NOT
                # the wording, because a malformed id and a well-formed
                # unknown one produce DIFFERENT messages and code matching
                # one silently misses the other. The provider validates
                # before doing any work, so trying costs nothing.
                say(_could_not_continue(manager))
                tried_designation = False
                continuing = False
                resume_from = ""
                result = launch(
                    root,
                    manager,
                    engine=engine,
                    resume_id="",
                    # ⚠ THE PROMPT, which this omitted. Without it the call
                    # took `_default_starter`'s `prompt=""`, `start_session`
                    # wrote an empty `prompt.txt` ("even when empty"), and
                    # the launch became `claude -p < <empty>` — which exits
                    # 1, per the measurement in `launch_command`. So the run
                    # rite had just announced as starting FRESH could not
                    # start, and the operator's instruction was discarded on
                    # the one path that exists to recover.
                    #
                    # ⚠ **`fresh_prompt`, and the composition matters.** This
                    # is a NEW conversation, so it gets the opening
                    # instruction rather than the continuation the resumed
                    # attempt was given — and it must still carry the mail
                    # and the reply instructions, which are appended per
                    # cycle above.
                    #
                    # Passing bare `prompt` here DELETED MESSAGES: `take`
                    # had already emptied the inbox, the supervisor had
                    # already said "delivering N message(s)", and the
                    # session that actually ran never saw them. Third
                    # argument silently dropped at this one call site in two
                    # days, after `prompt` and `permission` — all three are
                    # pinned now.
                    prompt=fresh_prompt,
                    # ⚠ AND THE PERMISSION MODE, lost the same way. It was
                    # added to the launch above when the permission work
                    # landed and not to this one, and `launch_command` only
                    # adds the flag `if permission` — so the fallback ran
                    # `claude -p` with none, which the permission design
                    # records as "a working loop around a Manager that
                    # cannot act". Two arguments, one call site, the same
                    # omission twice: this call is the one that gets
                    # forgotten, so the test now pins both.
                    permission=permission,
                    max_sessions=max_sessions,
                    window_seconds=window_seconds,
                )
            if not result.ok:
                return SuperviseResult(False, result.message, cycles)

            live = result.session
            live_pane = getattr(result, "pane", "")
            cycle = Cycle(
                number=len(cycles) + 1,
                session=result.session,
                resumed_from=resume_from,
                started_at=clock(),
            )
            cycles.append(cycle)

            # ⚠ **THE PROMPT IS NOT TYPED IN ANY MORE.** It went in with
            # the launch, on stdin, from the environment — because
            # `claude -p` needs its input before it runs and has exited by
            # the time anything could type. `deliver_prompt` typing into a
            # live pane was correct for a REPL and is a no-op against a
            # command that reads stdin once.
            cycle.prompted = bool(cycle_prompt)

            # Wait for it to end. The human can attach throughout — that is
            # §9.14.3, and it is why "nobody is watching" is false here in a way
            # it is not for a cron tick. Whether anybody DID attach is recorded
            # while waiting, because attachment is a moment and the question
            # `ending` asks is whether somebody was ever there.
            attended = False
            # ⚠ Mail is NOTICED here and DELIVERED at the next turn
            # boundary, not injected mid-turn. The engine has already read
            # its instruction for this cycle; there is nowhere to put a
            # message that it would read now, and pretending otherwise
            # would mean a message that looks delivered and is not.
            while liveness(result.session).alive:
                if deadline is not None and clock() >= deadline:
                    break
                if not cycle.mail_waiting and mail_waiting(root, manager, INBOX):
                    cycle.mail_waiting = True
                    say(
                        f"a message is waiting for {manager!r}; it is delivered "
                        f"when this session ends and the next one starts"
                    )
                attended = attended or was_attached(result.session)
                time.sleep(poll)
            cycle.ended_at = clock()
            cycle.attended = attended
            _say_refusals(root, cycle.started_at, say)

            how = ending(result.session, human_was_present=attended, pane=live_pane)
            cycle.ending = how.kind
            # ⚠ DESIGNATED WHATEVER THE ENDING. A cycle that quit or
            # crashed is still the conversation a user comes back to
            # tomorrow — arguably more so. Designating only resumable
            # endings would record nothing for the endings a human most
            # wants to pick up, which is the mechanic the design note left
            # open and warned about.
            observed = next_id(root, manager, cycle.started_at)
            if observed:
                designate(root, manager, observed)

            if not how.resume:
                return SuperviseResult(
                    how.kind != "crashed",
                    _stopped_because(how, len(cycles)),
                    cycles,
                )

            # Only now, and only for a session that finished cleanly with
            # nobody attached, is a resume the right thing.
            resume_from = observed
            if not resume_from:
                return SuperviseResult(
                    True,
                    f"stopped after {len(cycles)} session(s): the session "
                    "finished but no transcript was found to resume from, so "
                    "continuing would start a FRESH context rather than carry "
                    "the work on. Refused rather than silently restarting.",
                    cycles,
                )

        except KeyboardInterrupt:
            # ⚠ A HUMAN SAYING STOP — a different event from a bound being
            # reached (§9.14.12). A bound leaves the session alive
            # deliberately, because the user may be mid-conversation and a
            # ceiling is an accounting limit. Ctrl-C is not.
            #
            # ⚠ THE GUARD COVERS THE WHOLE CYCLE, NOT JUST THE WAIT. A draft
            # wrapped only the wait loop, so a Ctrl-C during `start`'s
            # two-second settle window escaped `supervise` entirely: no
            # teardown, a live paid session, and no instance record — after
            # which `rite start` refuses to adopt or kill it and the user
            # cleans up by hand. That window is two seconds of EVERY cycle,
            # and it is exactly when a user who has just realised they
            # started the wrong thing presses Ctrl-C.
            #
            # `live` is empty when the interrupt beat `start`'s return, so
            # the name is derived instead: `start` creates the tmux session
            # before it records anything, so the name is known even when
            # the result is not.
            # ⚠ THE ORDINARY STOP, not an anomaly — so it must leave
            # something to continue. Designated BEFORE the teardown,
            # because `_torn_down` clears the instance record and a user
            # who pressed Ctrl-C still means to come back.
            #
            # ⚠ **THE ONE EXCEPTION TO "DESIGNATED WHATEVER THE ENDING" (C17),
            # stated because an unstated one is how the next person is
            # surprised.** An interrupt before the first cycle is appended —
            # inside the first launch, typically its settle window — designates
            # NOTHING, and whatever was designated before stays. For a bare
            # `rite start` that is right: no conversation began, so the one it
            # set out to continue is still the one to continue.
            #
            # ⚠ For `--fresh` it contradicts a settled rule, which is why it is
            # SAID rather than only written here. `--fresh` REWRITES the
            # designation (see the top of this function), but only once a
            # cycle exists to designate — so a `--fresh` interrupted that early
            # leaves the conversation the user chose to abandon as the one a
            # bare `rite start` continues tomorrow. Measured: designation
            # 'YESTERDAY' before, 'YESTERDAY' after, nothing printed. Not
            # cleared here, because clearing would lose that id for good, and
            # whether `--fresh` should drop it up front is a design decision
            # rather than a fix.
            if cycles:
                interrupted_id = next_id(root, manager, cycles[-1].started_at)
                if interrupted_id:
                    designate(root, manager, interrupted_id)
            elif fresh and designated(root, manager):
                say(
                    f"interrupted before the fresh session's first cycle "
                    f"began, so nothing new was designated: a bare `rite "
                    f"start {manager}` will continue the PREVIOUS "
                    f"conversation, not a fresh one. Pass --fresh again to "
                    f"start over."
                )
            return _torn_down(
                root, manager, live or session_name(root, manager), cycles, say
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
    # ⚠ A SECOND Ctrl-C MUST NOT ORPHAN THE RECORD. A draft called these two
    # in sequence unguarded, so an interrupt landing between them left the
    # session killed and the record behind — the phantom this function
    # exists to remove, produced by the function that removes it. §9.14.12
    # already requires the teardown "survive a partial teardown"; this is
    # that requirement enforced rather than stated.
    gone = None
    interrupted_again = False
    try:
        if session:
            gone = stop_session(session)
    except KeyboardInterrupt:
        interrupted_again = True
    finally:
        # The record goes even if Ctrl-C keeps arriving. `forget_instance`
        # is one unlink and swallows OSError, so this terminates on the
        # first attempt that is not interrupted.
        while True:
            try:
                forget_instance(root, manager)
                break
            except KeyboardInterrupt:
                interrupted_again = True
                continue
    if interrupted_again:
        say(
            f"warning: interrupted again during teardown — the record is "
            f"cleared, so `rite start` will not think {manager!r} is "
            f"running. Check with `tmux has-session -t ={session}` and end "
            f"it with `tmux kill-session -t {session}` if it is still there."
        )
        return SuperviseResult(
            True,
            f"stopped Manager {manager!r} — interrupted during teardown, so "
            f"its session may still be running",
            cycles,
        )
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


def _default_starter(
    root,
    manager,
    *,
    engine,
    resume_id,
    max_sessions,
    window_seconds,
    permission,
    prompt,
):
    """Start one cycle's session. `permission` and `prompt` have NO DEFAULT,
    deliberately.

    ⚠ **THE DEFAULT WAS THE DEFECT, and it is the same shape as
    `ManagerInstance.pid`'s.** `permission=""` means "launch with no
    permission flag", and a Manager launched that way starts cleanly, runs,
    and then cannot act: it stops at the first operation needing approval,
    waiting for a human who is not watching. Nothing raises, nothing is
    logged, and the session spends its window doing nothing — defect class
    15, a prompt is not an exception but the absence of an answer. Measured
    in v0.5.1: three cycles, no artifact.

    The neighbouring argument has already done exactly this. `supervise`'s
    fresh fallback called this function without `prompt`, took the `prompt=""`
    default, and launched `claude -p` against an empty stdin — so the run
    rite had just announced as starting fresh could not start. That was fixed
    at the call site, which leaves the next caller free to repeat it.

    So this is unrepresentable rather than discouraged.

    ⚠ **And a blank prompt is REFUSED here, before a session is spent (C14).**
    Removing the default stops a caller forgetting the argument; it does not
    stop one passing an empty string. The launch built below redirects the
    engine's stdin from `prompt.txt`, and `claude -p` with nothing on stdin
    exits 1 — so without this the session is created, dies at once, is left
    held open under the Manager's name, and the operator is told to run the
    command by hand to see why. Measured before this guard existed.

    The guard lives HERE, not in `start_session`, because this is the one
    place that knows the launch reads the prompt: `start_session` also runs
    a caller's explicit `command`, which may read nothing from stdin at all.
    Whitespace counts as blank — it is what `rite sandbox` refuses too.
    """
    if not prompt.strip():
        return StartResult(
            False,
            f"refusing to start Manager {manager!r}: the cycle's prompt is "
            f"empty, and the engine reads its instruction from it — a "
            f"session launched with nothing to read exits at once. This is "
            f"a defect in whatever called the launch, not something to fix "
            f"in your config.",
        )
    result = start_session(
        root,
        manager,
        engine=engine,
        command=launch_command(
            engine,
            resume_id,
            str(manager_dir(root, manager) / PROMPT_FILE),
            permission,
        ),
        prompt=prompt,
        max_sessions=max_sessions,
        window_seconds=window_seconds,
    )
    # ⚠ NO SECOND `record_instance` HERE. `start_session` has already
    # written the record, with tmux's pane pid and the command that
    # actually ran. This function used to re-record the same instance
    # immediately afterwards, without a pid and with the CONFIGURED engine
    # string — overwriting both fields §9.14.10 exists to keep honest, and
    # making `rite status` report every live Manager as dead.
    #
    # The second write was not merely wrong, it was redundant: every value
    # it set is already set by `start_session`, which receives the same
    # arguments.
    return result
