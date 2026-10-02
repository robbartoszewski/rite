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

import os
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.managers import (
    checkins,
    claude_login,
    cursor_chat,
    cursor_login,
    delivered,
    designate,
    designated,
    designation_path,
    forget_instance,
    git_settings,
    github_access,
    manager_dir,
    record_chat,
    recorded_chat,
)

# ⚠ THE BOUNDARY IS RESOLVED, NOT IMPORTED. These six used to come straight
# from `enclosure`, which is seatbelt — so every Manager on every platform was
# launched with `sandbox-exec`, and on Linux that is a Manager that starts and
# vanishes. `boundary_for()` picks the mechanism for this machine and raises
# when there is none.
from rite_ai.managers.board_context import board_now
from rite_ai.managers.boundaries import UnsupportedPlatform, boundary_for
from rite_ai.managers.broker import take_requests
from rite_ai.managers.engines import handle_problem, new_handle, spelling_for
from rite_ai.managers.mailbox import INBOX, delivery_note, how_to_reply, put_back, send
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
from rite_ai.managers.progress import Footprint, footprint
from rite_ai.managers.session import (
    PROMPT_FILE,
    StartResult,
    approval_blocked,
    ending,
    liveness,
    session_name,
    was_attached,
)
from rite_ai.managers.session import start as start_session
from rite_ai.managers.session import stop as stop_session
from rite_ai.managers.transcripts import (
    belongs_to_project,
    refused_commands,
    session_id_problem,
)

UNATTENDED_MODE_FOR_ENV_ENGINES = "auto"
"""The permission mode given to an engine that keeps one in its environment.

⚠ **THE MECHANISM IS SETTLED; THIS VALUE IS NOT, AND IT IS ROBERT'S CALL.**
Both available answers are bad and one contradicts a decision just taken:

* `auto` — what Goose defaults to anyway. Measured 2026-09-24: it ran `rm`
  on a file unattended. **Unconstrained**, days after C4 deliberately made
  the secure option the default for a `claude` Manager.
* `approve` / `smart_approve` — measured the same day: in a headless run an
  operation needing approval **fails, exit 1**, rather than prompting. So a
  Manager in that mode stops at its first real operation.

`auto` is used because the alternative does not run at all, and because it
is what happens today anyway — so this changes no behaviour, it only makes
the choice rite's and visible. **It does not close the gap C4 closed for
Claude**, and nothing here can: `GOOSE_MODE` is whole-session with no
per-command concept, so C4's allowlist semantics cannot be expressed for
this engine at all. Closing it properly means the sandbox being the
boundary (B4d measured Goose runs inside one), which means sandboxing
Managers — a much larger change than a permission setting."""

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
STOP_VERDICTS = frozenset(
    {"idle", "deadlocked", "unknown", "closed", "waiting-on-user"}
)
CONTINUE_VERDICTS = frozenset({"ready", "saturated", "blocked", "refining"})
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

Together they are exhaustive over `loop`'s nine verdicts, and a test
asserts it, so a tenth cannot be added without classifying it.

⚠ **`waiting-on-user` stops only the SESSIONS, not the run (TR2).** It
waits in `_wait_for_mail` and spends nothing until the User answers (a
reply is mail) or a refinement round's deadline passes. Ending the run
there would drop an Owner whose only work is waiting on him."""


def launch_command(
    engine: str,
    resume_id: str = "",
    prompt_path: str = "",
    permission: str = "",
    agent: str = "",
    start_handle: str = "",
    model: str = "",
) -> str:
    """What to run in the pane, in the engine's OWN vocabulary (B3a).

    ⚠ **THIS USED TO APPEND CLAUDE CODE'S FLAGS TO WHATEVER IT WAS GIVEN.**
    The engine string was an executable name, `-p` and `--resume <id>` went
    on unconditionally, and the permission mode went on argv — correct for
    exactly one engine and another tool's vocabulary for every other. The
    old test pinned that as behaviour, with a docstring saying the interface
    stays unstable *"until `local` forces it"*. It is forcing it.

    The spelling now comes from `engines.spelling_for`, keyed by the engine
    and — for `local:<class>`, which names a tier and not a runtime — by the
    `agent` its role declares.

    ⚠ **Claude's command line is unchanged, byte for byte.** A registry that
    altered the one engine rite actually launches would be a refactor with a
    behaviour change hidden in it.

    ⚠ **An unrecognised engine keeps CLAUDE's spelling, deliberately — and
    this docstring said the opposite until it was corrected.**

    An earlier draft of this function refused to add anything to an engine it
    did not recognise, and this paragraph described that. The suite showed it
    was wrong: an unrecognised engine string is not an unknown tool, it is a
    script STANDING IN for `claude` (`engine="sh"`, `engine=".../agent.sh"`),
    and those stubs want Claude's flags because Claude is what they emulate.
    Refusing broke two tests whose whole point is that the permission mode
    reaches the launch. **The code changed; this text did not, until now** —
    which is the documentation-describes-absent-behaviour defect this release
    has been clearing, committed by the release that was clearing it.

    **Production cannot reach that path at all.** `config/managers.py` accepts
    exactly `claude`, `human` and `local:<class>`, and `local:*` resolves
    through its declared `agent`. See `engines.SUBSTITUTED`.

    ⚠ **What an engine genuinely cannot express is still refused, not
    dropped** — `GOOSE_MODE` lives in the environment, so a permission FLAG
    for Goose raises rather than being written. Dropping what cannot be said
    is the silent failure this path exists to prevent.
    """
    spelling = spelling_for(engine, agent)
    command = spelling.binary or engine or "claude"
    parts = [command]
    if spelling.turn:
        parts.append(spelling.turn)
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
    if permission and spelling.permission_in_file:
        # ⚠ REFUSED, not written and not dropped. This engine reads its
        # permission from a config file the supervisor writes before every
        # launch (`cursor_login.write_config`). A flag here would be one the
        # engine ignores, which reads as a permission that is in force.
        raise ValueError(
            f"{command!r} reads its permission mode from "
            f"{spelling.permission_in_file}, which the supervisor writes; a "
            "permission flag here would be ignored, so it is refused"
        )
    if permission:
        # ⚠ **EVERY cycle, not just the first.** Resuming with `-p` does not
        # restore the mode a session was in — that restoration explicitly
        # excludes `-p` — so a resumed cycle launched without this is a
        # Manager that can no longer act AND still exits 0, which `ending`
        # reads as a clean finish. D-90's shape exactly, in a second place.
        if spelling.permission_env:
            # ⚠ NOT AN ERROR AND NOT DROPPED. Goose keeps its permission
            # mode in `GOOSE_MODE`, so it does not belong on this command
            # line — `session.start` puts it in the environment instead,
            # from `permission_placement`. Writing a flag here would hand
            # the tool something it rejects; raising here would stop a
            # Manager that is perfectly startable.
            pass
        else:
            parts.append(permission)
    if model:
        # A Claude Manager's declared model (`coordination.manager_roles`).
        # Checked again HERE, at the boundary that reaches the shell, as the
        # resume id is below: the parser's check is one caller's.
        from rite_ai.config.managers import claude_model_problem
        from rite_ai.managers.engines import CLAUDE as CLAUDE_SPELLING
        from rite_ai.managers.engines import SUBSTITUTED

        if spelling not in (CLAUDE_SPELLING, SUBSTITUTED):
            raise ValueError(
                f"{command!r} takes its model from its role's endpoint, not a "
                "--model flag; refused rather than written where it is ignored"
            )
        problem = claude_model_problem(model)
        if problem:
            raise ValueError(f"refusing to launch with model {model!r}: {problem}")
        # QUOTED: `claude-opus-5-5[1m]` is a valid id and `[1m]` is a shell
        # glob, so unquoted, a file in the pane's directory named
        # `claude-opus-5-51` would become the model.
        parts.append(f"--model {shlex.quote(model)}")
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
        problem = handle_problem(spelling, resume_id)
        if problem:
            raise ValueError(f"refusing to resume {resume_id!r}: it {problem}")
        if not spelling.resume:
            raise ValueError(
                f"{command!r} has no resume spelling rite knows, so there is "
                f"no way to continue {resume_id!r} with it. Refused rather "
                "than dropped: dropping it starts a fresh context with the "
                "ticket half-done, which is the failure this path exists to "
                "prevent."
            )
        parts.append(spelling.resume.format(handle=resume_id))
    elif start_handle:
        # ⚠ **NAMING A NEW CONVERSATION, which only an engine whose handle is
        # ours can do.** Claude generates its id and has no `start` spelling,
        # so this branch is unreachable for it and the argv is unchanged.
        #
        # Without it, `resume` is unreachable for Goose: it resolves `-r` by
        # name and fails loudly on a name it has never seen, so a first cycle
        # with no `-n` creates a conversation under a name Goose chose and
        # the second cycle asks to continue one that does not exist. That
        # failure is silent in the only way that matters — the second cycle
        # still starts, just with no memory — which is `_default_resume_id`'s
        # documented defect arriving by a different road.
        problem = session_id_problem(start_handle)
        if problem:
            raise ValueError(
                f"refusing to build a launch command with a session handle "
                f"that {problem}. This string is run by a shell."
            )
        problem = handle_problem(spelling, start_handle)
        if problem:
            raise ValueError(f"refusing to start {start_handle!r}: it {problem}")
        if spelling.start:
            parts.append(spelling.start.format(handle=start_handle))
    base = " ".join(parts)
    if not prompt_path:
        return base
    if spelling.prompt_flag:
        return f"{base} {spelling.prompt_flag} {shlex.quote(str(prompt_path))}"
    # Redirected, not an argument: `tmux new-session` puts its command on
    # tmux's argv where `ps` shows it to every local account.
    return f"{base} < {shlex.quote(str(prompt_path))}"


def _say_if_the_window_was_cut(
    root: Path,
    manager: str,
    agent: str,
    started: float,
    ended: float,
    say,
    told,
    *,
    monotonic_elapsed: float | None = None,
) -> list:
    """Say whether Ollama cut this local Manager's prompt during the cycle.

    Observed (plan, Track MS): a prompt over the window is cut from the front
    with no error, and the cycle then ends normally with work done on a
    fragment. Only Ollama's log records it (`local.truncation`), and it does
    not say whose prompt it was: a cut is the SERVER'S, reported with the
    other Managers on the same endpoint named. Returns the cuts, for the
    check-in record. A "cannot tell" is said once per reason per
    run (`told`), because a line repeated every cycle is one nobody reads.
    """
    if agent != "goose":
        return []
    from rite_ai.config.parse import ParseError, parse_config
    from rite_ai.local import truncation

    parsed = parse_config(root / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return []
    role = next(
        (r for r in parsed.coordination.manager_roles if r.name == manager), None
    )
    if role is None or not role.endpoint:
        return []
    verdict = truncation.check_cycle(
        role.endpoint, started, ended, monotonic_elapsed=monotonic_elapsed
    )

    def base(endpoint: str) -> str:
        return endpoint.rstrip("/").removesuffix("/v1")

    sharing = tuple(
        r.name
        for r in parsed.coordination.manager_roles
        if r.name != manager and r.is_local and base(r.endpoint) == base(role.endpoint)
    )
    line = truncation.describe(manager, role.context_window, verdict, sharing)
    if line and (verdict.cut or line not in told):
        told.add(line)
        say(line)
    return verdict.cuts


def _say_if_the_sandbox_refused(root: Path, manager: str, pane: str, say) -> None:
    """Point at the boundary when something in the pane hit it.

    ⚠ **Because it reads as rite being broken, and it is not.** The
    operator's own Claude Code hooks still load inside the sandbox — `HOME`
    is deliberately not redirected, so their login keeps working — and a
    hook that runs something outside the profile fails inside the boundary
    where it worked outside. Found by hitting it with a real `SessionEnd`
    hook.

    ⚠ **A hint, not a verdict.** `Operation not permitted` in a pane can
    come from something rite never sandboxed, so this says "the sandbox
    refuses things and here is its profile" rather than claiming to know
    which line failed. Saying nothing would leave the user with a denial and
    no idea which of the machine's several boundaries produced it.
    """
    from rite_ai.managers.session import pane_text_for_detection

    if not pane:
        return
    # ⚠ Resolved here rather than held: this runs while explaining a failure,
    # and a machine with no backend must not raise a SECOND error on top of
    # the one being explained.
    try:
        confinement = boundary_for()
    except UnsupportedPlatform:
        return
    if confinement.refusal_looks_like_ours(pane_text_for_detection(pane)):
        say(confinement.why_it_was_refused(root, manager))


def _honour_worker_requests(root: Path, manager: str, broker, say) -> None:
    """Start the Workers this cycle asked for, or say why not.

    ⚠ **A sandboxed Manager cannot start a sandboxed Worker** — the kernel
    refuses a second profile inside the first (B9) — so it writes a request
    and the supervisor, outside the boundary, decides.

    ⚠ **At the cycle boundary rather than in the wait loop, and the cost is
    stated rather than hidden.** `rite sandbox start` prepares a workspace
    and creates a sandbox, which takes tens of seconds; doing that inside
    the two-second poll would stop the loop noticing mail or attachment for
    its duration. So a Worker requested during a cycle starts when that
    cycle ends, and the Manager learns the outcome in its next instruction.
    The prompt says so, because a Manager that expected its Worker to be
    running already would otherwise conclude the request failed.

    ⚠ **Every outcome is SAID.** A refusal nobody sees is the defect C21
    exists for, and here it is worse: a Manager that asked for something it
    may not have is either confused or compromised.

    ⚠ **And every outcome is TOLD to the Manager that asked** (`telling`).
    `broker.instructions` promises it "reports the result in your next
    instruction"; this used to reach only `say`, the operator's terminal, so a
    Manager whose request was refused never heard and waited for a Worker that
    was never coming. Nothing compared the promise, in the Manager's
    instructions, with where the outcome went, a terminal, so nothing would
    have noticed (dogfood DF13). The note is mail: at an idle board it starts a
    session to deliver it, and at any other exit `rite start` says it is
    undelivered.
    """
    from rite_ai.managers.telling import tell_manager

    pending = take_requests(root, manager)
    if not pending:
        return

    def tell(text: str) -> None:
        try:
            tell_manager(root, manager, "a Worker you asked for", text)
        except OSError as e:
            say(f"could not tell {manager!r} what happened to its request: {e}")

    if broker is None:
        said = (
            f"{manager!r} asked to start {len(pending)} Worker(s), and this "
            "run has no broker configured to do it. Nothing was started — "
            "the requests are discarded rather than queued, because nothing "
            "here would run them later."
        )
        say(said)
        tell(
            f"You asked for {len(pending)} Worker(s). None was started: this "
            "run has no way to start Workers, and the requests were discarded, "
            "not queued. Do not wait for them; say so to the User."
        )
        return
    for _, raw in pending:
        ok, message = broker(raw)
        say(("started: " if ok else "") + message)
        tell(
            ("Started: " if ok else "NOT started: ")
            + message
            + (
                ""
                if ok
                else " Nothing is running for this request; do not wait for "
                "it. Fix what it names, or say so to the User."
            )
        )


def _say_refusals(
    root: Path,
    since: float,
    say,
    engine: str = "",
    agent: str = "",
    pane: str = "",
    manager: str = "",
) -> list[str]:
    """Tell the user what the engine refused, and how to permit it.

    Returns what was refused, as the standup records it (plan § K4): each
    refused command, or one whole-session line for an engine that refuses
    whole sessions.

    ⚠ **Break 4 of B4b: this read CLAUDE's transcripts for every engine.**
    `refused_commands` scans Claude Code's transcript directory, which Goose
    does not write — so a Goose Manager's refusals were invisible, and the
    empty list that came back was indistinguishable from "nothing was
    refused". An instrument that reports the absence of a thing it never
    looked for is measuring nothing (defect class 1), and reading that
    absence as zero is class 2. Both, in one call.

    **What a refusal IS differs by engine, and that is the axis**
    (`Spelling.per_command_refusals`). Claude refuses one command and carries
    on, leaving a record naming it. Goose in a headless run has no
    per-command refusal at all: under `GOOSE_MODE=auto` nothing is refused,
    and under `approve` the whole session dies on the first tool call. So for
    such an engine there is nothing per-command to collect — and rite says
    the one thing that IS true rather than silently saying nothing.

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
    spelling = spelling_for(engine, agent)
    if not spelling.per_command_refusals:
        # ⚠ **NOT "no refusals" — a different statement, and the whole point
        # of break 4.** Nothing is scanned here because there is nothing this
        # engine records per command. The one refusal it CAN produce is
        # whole-session, so that is what rite looks for, and it is reported
        # with the remedy for the same reason C21 exists: a refusal a user
        # cannot act on is the same defect as a silent one.
        if pane and approval_blocked(pane):
            say(
                "refused: the engine ended the whole session rather than one "
                "command — it wanted an approval and this run is "
                "non-interactive, so nobody could give it. This engine's "
                "permission mode is whole-session"
                + (f" ({spelling.permission_env})" if spelling.permission_env else "")
                + f", and rite sets it to "
                f"{UNATTENDED_MODE_FOR_ENV_ENGINES!r} — so a mode reached it "
                f"from somewhere rite does not control: a managed engine "
                f"config, or the environment on a path that bypasses the "
                f"launch."
            )
            return ["the whole session (it wanted an approval)"]
        return []
    # A Claude Manager with its own config directory writes its transcripts
    # there (`claude_login`), so that is where its refusals are.
    base = claude_login.projects_dir(root, manager) if manager else None
    refused = list(dict.fromkeys(refused_commands(root, since, base=base)))
    for command in refused:
        if _substitutes(command):
            # ⚠ W9 (v0.6.0 readiness). This used to fall to the branch below
            # and blame the settings file. Claude Code asks approval for a
            # substitution whose inner command is not on the allowlist, and
            # runs it when it is (F14) — either way the text was being read
            # as shell.
            say(
                f"refused: {command.strip()!r} — it runs a command inside "
                "backticks or $( ), which the engine asks approval for. When "
                "that is text for `rite reply`, `rite ask` or `rite route`, "
                "the text goes on stdin through a quoted heredoc, as the "
                "Manager's instructions show, never in double quotes."
            )
        elif allowed(command):
            # ⚠ TWO CAUSES, and the transcript does not say which (SB11,
            # observed on Linux 2026-09-28): `printf … > notes/x.txt` was
            # refused while `echo`, `git status` and `ls` ran under the same
            # allowlist in the same session, so the settings WERE applied.
            # This used to say flatly that they were not. A command that
            # writes a file through a redirection is the observed case, so it
            # is named first when the command has one.
            redirect = _writes_through_a_redirection(command)
            say(
                f"refused: {command.strip()!r} — which rite's own allowlist "
                "appears to cover. rite cannot tell from the transcript why: "
                + (
                    "most likely the engine asks approval for the file this "
                    "command writes through `>`, whatever the rule for its "
                    "program (observed on Linux, with other allowlisted "
                    "commands running in the same session); or "
                    if redirect
                    else "either the engine refuses this form of the command "
                    "despite the rule, or "
                )
                + f"it did not apply {settings_path(root)} (under `-p` a "
                "settings file that fails validation is ignored without a "
                "message). If other allowlisted commands ran in that session, "
                "it is the first. Otherwise check that file parses, and "
                f"`permissions.deny` in {Path('.claude') / 'settings.json'}."
            )
        else:
            say(refusal(command, root))
    if manager:
        for command in refused:
            _tell_the_person_a_relay_was_refused(root, manager, command, since, say)
    return [c.strip() for c in refused]


_RELAY_VERBS = ("reply", "ask", "route")


def _relay_refused(command: str) -> str:
    """What a refused command was carrying to someone, or "" when it was
    not one of rite's relays: `rite reply`, `ask`, `route` or `refine ask`,
    or the stray end line of one (`stdin_text.stray_end`)."""
    from rite_ai.managers.stdin_text import stray_end

    if stray_end(command):
        return "a message in rite's text form"
    words = command.split("\n", 1)[0].split()
    if not words or Path(words[0]).name != "rite":
        return ""
    rest = [w for w in words[1:] if not w.startswith("-")]
    if rest[:1] and rest[0] in _RELAY_VERBS:
        return f"`rite {rest[0]}`"
    if rest[:2] == ["refine", "ask"]:
        return "`rite refine ask`"
    return ""


def _tell_the_person_a_relay_was_refused(root, manager, command, since, say):
    """🔴 SCRUM-23. A relay the engine refused was said only in the
    supervisor's pane, so the person it was for never learned it did not
    come. Told in their DM, through the outbox the message itself would have
    gone through, once per refusal per session.

    ⚠ **The command is not quoted.** Its text is the Manager's, and often
    someone else's (a ticket, a routed reply); the person is told what kind
    of message did not come and why, not handed the text."""
    from rite_ai.managers.asking import raise_to_person
    from rite_ai.managers.stdin_text import stray_end

    what = _relay_refused(command)
    if not what:
        return
    if stray_end(command):
        why = "its end line was written twice, so the engine refused the whole call"
    elif _substitutes(command):
        why = "its text was on the command line with backticks or $( ) in it"
    else:
        why = "the engine refused the command"
    when = time.strftime("%H:%M", time.localtime(since)) if since else "this run"
    try:
        raise_to_person(
            root,
            manager,
            subject="",
            raiser=f"manager:{manager}",
            text=(
                f"{what} from Manager {manager!r} did not reach anyone "
                f"(session from {when}): {why}. Nothing was sent. The Manager "
                "was told and may send it again; until it does, whatever it "
                "was meant to say has not arrived."
            ),
        )
    except Exception as e:  # noqa: BLE001 - said, never raised into the run
        say(f"could not tell the User that {what} was refused: {e}")


def _substitutes(command: str) -> bool:
    """A backtick or `$(` outside single quotes, in the command's own line.
    The body of a quoted heredoc (`<<'X'`) is text, not shell, so only what
    comes before its first newline counts."""
    head = command.split("\n", 1)[0] if "<<'" in command else command
    # An apostrophe inside double quotes ("don't") opens nothing: prose is
    # exactly where F14's backticks were.
    quote, escaped = "", False
    for i, char in enumerate(head):
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote != "'" and (char == "`" or head.startswith("$(", i)):
            return True
        elif char in "'\"" and quote in ("", char):
            quote = "" if quote else char
    return False


def _writes_through_a_redirection(command: str) -> bool:
    """A `>` or `>>` outside quotes. `2>&1` is not one: the lexer reads its
    `>&` as one token, a descriptor duplication that writes no file."""
    import shlex

    try:
        tokens = list(shlex.shlex(command, posix=True, punctuation_chars=True))
    except ValueError:
        return ">" in command
    return any(token in (">", ">>", "&>", ">|") for token in tokens)


def _resume_id_source(engine: str, agent: str = ""):
    """Where this engine's handle COMES FROM — discovered, or chosen by rite.

    ⚠ **Break 1 of B4b: this used to be Claude's answer for every engine.**
    `_default_resume_id` scans Claude Code's transcript directory for
    `*.jsonl`. Goose writes no such transcript, so every resumed cycle got
    `resume_id=""` and started FRESH — and `_default_resume_id`'s own
    docstring already records what that looks like from outside: *"each
    cycle began a FRESH context with the ticket half-done and no memory of
    it — identical from outside to a resume that worked."* The same defect,
    reached by a different road, in the function that documents it.

    **`Spelling.handle_is_ours` is the axis, and it is a source rather than
    a format.** Claude ASSIGNS an id rite must discover afterwards; Goose
    takes a name rite CHOOSES, so there is nothing to discover and the
    answer is known before the cycle runs.

    ⚠ **For a chosen handle this returns the name even when the cycle did no
    work, and that is deliberate.** The alternative is probing Goose for
    whether the session exists, which is a second classifier over a signal
    that means other things. A name for a conversation that was never
    created fails LOUDLY at the next launch — Goose refuses an unknown name,
    measured — where the discovered-id path fails by returning "" and
    letting the supervisor refuse. Both report; neither continues silently,
    which is the property that matters.
    """
    spelling = spelling_for(engine, agent)
    if not spelling.handle_is_ours:
        return _default_resume_id
    if spelling.handle_is_uuid:
        # ⚠ NOT `session_name`. Cursor spells start and continue alike, so a
        # handle derived from the name would make `--fresh` continue the old
        # chat. The handle is a UUID `_open_chat` recorded BEFORE the first
        # launch, so the answer is whatever is recorded.
        def recorded(root: Path, manager: str, since: float = 0.0) -> str:
            chat = recorded_chat(root, manager)
            return chat.handle if chat is not None else ""

        return recorded

    def chosen(root: Path, manager: str, since: float = 0.0) -> str:
        # Deterministic, and the same string the tmux session carries — one
        # name for one Manager's conversation, so a human reading `tmux ls`
        # and a human reading `goose session list` see the same handle.
        return session_name(root, manager)

    return chosen


def _designation_is_ours(
    root: Path, manager: str, designation: str, handle_is_ours: bool
) -> bool:
    """Whether a designation belongs to THIS project — and, where it can be
    told, THIS Manager.

    ⚠ **C29: C8's check only knew one source of handle.** For Claude the
    provider assigns the id, so membership is answered from Claude's
    transcripts (`belongs_to_project`). For an engine whose handle rite
    CHOOSES (Goose, `Spelling.handle_is_ours`), the handle is
    `session_name(root, manager)` and never appears in Claude's transcripts —
    so every Goose Manager's own designation failed the check and every run
    started FRESH, printing that its own session "is not one of this project's
    conversations". The local secondary in the two-Manager shape could never
    continue across runs, which is the property `rite start X` exists for.

    For a rite-chosen handle the check is STRONGER than C8's: the name rite
    would choose here embeds the project's hash AND the Manager's name, so
    equality answers "this project" and "this Manager" at once — the half C8's
    docstring says it could not answer for Claude.
    """
    if handle_is_ours:
        return designation == session_name(root, manager)
    # Where THIS Manager's Claude transcripts are, if it has its own
    # config directory (`claude_login`); otherwise Claude's default.
    return belongs_to_project(
        root, designation, base=claude_login.projects_dir(root, manager)
    )


@dataclass
class _Chat:
    """A Cursor Manager's conversation across one run (CU3)."""

    handle: str
    created_ms: int | None
    config: Path
    expected: int | None = None
    """What `before_turn` found for the cycle now running: None for a first
    turn, else the creation time it must keep."""


def _open_chat(root: Path, manager: str, fresh: bool, say, spelling):
    """The chat this run continues, or a refusal.

    ⚠ **RECORDED BEFORE ANY LAUNCH.** A fresh handle is written to the
    designation first; if that write fails, nothing runs. A launch before the
    record would be a chat rite could not name afterwards.

    ⚠ **`--fresh` rewrites the designation up front here, unlike Claude's
    (C17).** Claude's id exists only once a cycle has run, so an interrupted
    `--fresh` leaves the old designation; Cursor's handle is rite's before
    the first launch, and recording it then is what rules the race out.
    """
    config = cursor_chat.config_dir(root, manager)
    existing = None if fresh else recorded_chat(root, manager)
    if existing is not None and existing.broken:
        return (
            f"refusing to continue Manager {manager!r}: its conversation is "
            f"recorded as broken ({existing.broken}). Start a new one "
            f"deliberately with `rite start {manager} --fresh`."
        )
    if existing is not None and existing.handle:
        say(f"continuing Manager {manager!r}'s Cursor chat {existing.handle}")
        return _Chat(existing.handle, existing.created_ms, config)
    handle = new_handle(spelling)
    try:
        record_chat(root, manager, handle)
    except OSError as err:
        return (
            f"refusing to start Manager {manager!r}: the new chat's handle "
            f"could not be recorded ({err}), and a chat rite cannot name "
            "afterwards could not be continued"
        )
    say(f"Manager {manager!r} starts a new Cursor chat, {handle}")
    return _Chat(handle, None, config)


def _check_chat_after(root: Path, manager: str, chat: _Chat, board, say) -> str:
    """Record what the cycle that just ran did to the chat. Returns the
    reason the conversation is now broken, or ""."""
    after = cursor_chat.after_turn(
        chat.expected, cursor_chat.observe(chat.config, chat.handle)
    )
    if after.outcome == cursor_chat.CONFIRMED:
        record_chat(
            root, manager, chat.handle, created_ms=after.created_at_ms, board=board
        )
        chat.created_ms = after.created_at_ms
    elif after.outcome == cursor_chat.NO_CHAT:
        say(f"{after.reason}; the next cycle is a first turn again")
    elif after.outcome == cursor_chat.REPLACED:
        record_chat(
            root, manager, chat.handle, created_ms=chat.created_ms, broken=after.reason
        )
        say(f"⚠ Manager {manager!r}: {after.reason}")
        return after.reason
    elif chat.created_ms is None and after.created_at_ms is not None:
        # CONTINUED on a chat adopted by `before_turn`: record it now.
        record_chat(root, manager, chat.handle, created_ms=after.created_at_ms)
        chat.created_ms = after.created_at_ms
    return ""


def _default_resume_id(root: Path, manager: str, since: float = 0.0) -> str:
    """The provider session to carry on from — for an engine that ASSIGNS one.

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

    return latest_session_id(
        root, since=since, base=claude_login.projects_dir(root, manager)
    )


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
    idle: bool = False
    """It changed nothing rite can see (`progress.footprint`), so the
    `--sessions` ceiling does not count it (SCRUM-24). False when rite could
    not tell, so an unjudged session is counted."""


def _counted(cycles: list[Cycle]) -> int:
    """The sessions the `--sessions` ceiling counts: those that changed
    something, or that rite could not judge.

    🔴 SCRUM-24. A Manager waiting for an answer woke, found nothing to do,
    and each such session used up the ceiling, so the run stopped on
    "ceiling reached" while it was only waiting."""
    return sum(1 for c in cycles if not c.idle)


def _idle(cycles: list[Cycle]) -> int:
    """Sessions that changed nothing. They still cost tokens, so they have
    their own allowance (`_idle_allowance_spent`)."""
    return sum(1 for c in cycles if c.idle)


PERPETUAL_SESSIONS_PER_CYCLE = 20
"""The per-cycle session ceiling a perpetual run uses (SCRUM-20, perpetual).

⚠ **A GUARDRAIL, NOT A STOP.** `rite start lead` with no bounds runs until the
operator ends it, so this number does not end the run — reaching it ends the
CYCLE, and the next one begins when the schedule and the wait allow. It exists
so a runaway inside one cycle is bounded, not so the run is.
"""

EMPTY_CYCLES_BEFORE_STOPPING = 3
"""How many cycles in a row may start NO session before a perpetual run stops.

🔴 **The relaunch gate, and it is what makes "forever" safe to ship.** A cycle
that begins another cycle without starting anything has made no progress, and a
run that does that without limit is a spin: cheap, silent, and indistinguishable
from working. Found by mutation — breaking the per-cycle counter's reset turned
the loop into exactly that, and the test HUNG instead of failing, which is the
worst way for a defect to present.

So a perpetual run stops rather than spins, and says which it was."""

PERPETUAL_CYCLE_SECONDS = 60.0 * 60.0
"""And the per-cycle window, for the reason D-82 gives: the count and the clock
catch different runaways and neither suffices alone. Reaching it ends the cycle,
not the run."""


def _begin_a_new_cycle(why: str, cycles: list, clock, say) -> tuple[float, int]:
    """End the current cycle and start the next: say why, and return the new
    window's start and the index its counting begins at (SCRUM-20, perpetual).

    ⚠ **The history is NOT discarded.** `cycles` is what the lifecycle record
    and the caller read, so what resets is where the per-cycle ceilings START
    COUNTING (`cycles[cycle_from:]`), never the list. A reset that dropped the
    history would leave a perpetual run unable to say what it had done — which
    is the thing SCRUM-20 just fixed.
    """
    say(
        f"cycle ended: {why}. This run has no bound from you, so it waits for "
        "the next cycle and continues — Ctrl-C ends it."
    )
    return clock(), len(cycles)


def _spinning(empty_cycles: int, cycles: list) -> SuperviseResult | None:
    """A perpetual run that has begun `EMPTY_CYCLES_BEFORE_STOPPING` cycles in a
    row without starting one session is spinning, not waiting. Stop and say so.

    See `EMPTY_CYCLES_BEFORE_STOPPING`: this exists because the alternative
    presents as a hang.
    """
    if empty_cycles < EMPTY_CYCLES_BEFORE_STOPPING:
        return None
    return SuperviseResult(
        True,
        f"stopped: {empty_cycles} cycles in a row began without starting a "
        "session, so this run was spinning rather than waiting. That is a "
        "defect in rite, not a state to wait out — `rite status` says what "
        "the Manager and its Workers were doing.",
        cycles,
    )


def _wait_a_cycle_out(poll: float, say) -> None:
    """Pause between cycles of a perpetual run.

    ⚠ **Through `_sleep`, the module seam a virtual-clock test patches.** A
    direct `time.sleep` here would make every test of a perpetual run wait in
    real time, and the suite would either be slow or stop covering this.
    """
    _sleep(poll)


def _perpetual(max_sessions: int | None, window_seconds: float | None) -> bool:
    """Whether this run has no operator-set bound and so runs until stopped.

    Both absent means perpetual. ONE given means the operator asked for a
    bounded run and gets exactly that: a half-bounded run that silently became
    perpetual would be the surprise this flag exists to avoid.
    """
    return max_sessions is None and window_seconds is None


def _idle_allowance_spent(manager: str, max_sessions: int, cycles):
    """A result that stops the run once `--sessions` idle sessions have run,
    or None.

    ⚠ **Not counting idle sessions must not make them unbounded.** A session
    that changes nothing still spends tokens, and the no-progress guard
    waits between them, but a run with no window and a steady stream of
    wakes would have no limit at all. So idle sessions get their own
    allowance, equal to the ceiling: at most twice what `--sessions`
    allowed before, never more."""
    if _idle(cycles) < max_sessions:
        return None
    return SuperviseResult(
        True,
        f"stopped: {_idle(cycles)} session(s) changed nothing rite can see "
        f"(no commit or edit, claim, reply, route or Worker request). The "
        f"--sessions ceiling does not count those, so they have an allowance "
        f"of their own, the same number ({max_sessions}), and it is spent. "
        f"{_counted(cycles)} session(s) did work.",
        cycles,
    )


@dataclass
class SuperviseResult:
    ok: bool
    reason: str
    cycles: list[Cycle] = field(default_factory=list)

    @property
    def sessions_started(self) -> int:
        return len(self.cycles)


def supervise(
    root: Path, manager: str, *, waiting: object = None, **options
) -> SuperviseResult:
    """Run the Manager until a bound or a stop verdict ends it — see
    `_supervise`, which this wraps.

    `waiting` (`routing.Waiting`, for a Manager in a root shared with others)
    lets mail CAUSE a cycle. With it, this supervisor's own process is
    recorded for the whole run, waits included, and removed however the run
    ends: it is what another Manager's supervisor reads to know this one is
    PROVABLY gone rather than between two cycles.
    """
    from rite_ai.managers.routing import forget_supervisor, record_supervisor

    # 🔴 SCRUM-20. Recorded for EVERY run, not only one that may wait.
    # This used to sit behind `if waiting is None: return _supervise(...)`, so
    # the lifecycle was recorded only in a root several Managers share with an
    # Owner (`_waiting_for` returns None below two Managers). A lone Manager —
    # the commonest project there is, and the one the dogfood runs — recorded
    # no start, no end and no reason, so when it stopped nothing could say
    # whether a bound fired, a verdict ended it, or it crashed. `rite status`
    # then read the INSTANCE record, whose pid is dead between cycles by
    # design, and reported a running Manager as gone.
    #
    # `waiting` keeps its own job: it decides whether this Manager may WAIT
    # for its inbox. It was never the right condition for keeping a record.
    record_supervisor(root, manager, os.getpid())
    how = "stopped by an error"
    counts: dict = {}
    try:
        result = (
            _supervise(root, manager, **options)
            if waiting is None
            else _supervise(root, manager, waiting=waiting, **options)
        )
        # Its own words, so the Owner can say HOW it ended, not only that it
        # did: a Ctrl-C, a bound and a finished queue all end a run cleanly.
        how = result.reason
        # SCRUM-20: and what it did, so a run that ended on a ceiling can be
        # told from one that ended having run nothing.
        counts = {
            "sessions_started": result.sessions_started,
            "sessions_that_did_work": _counted(result.cycles),
            "sessions_that_changed_nothing": _idle(result.cycles),
        }
        if getattr(waiting, "is_owner", False):
            from rite_ai.managers.routing import verification_summary

            counted = verification_summary(root, manager, waiting.began)
            if counted:
                # BESIDE the session count, never inside it.
                result = SuperviseResult(
                    result.ok, f"{result.reason} · {counted}", result.cycles
                )
        return result
    finally:
        # Recorded however the run ends, a Ctrl-C and an error included. Only
        # a KILLED run skips it, and that is exactly what `supervisor_state`
        # reads as DIED.
        # SCRUM-20: the Workers this run leaves behind, BEFORE the record is
        # closed — a reader that sees ENDED must already be able to see what
        # was left mid-flight.
        left = _hand_over_on_stop(root, manager, how, options.get("say"))
        if left:
            counts["left_unattended"] = left
        forget_supervisor(root, manager, os.getpid(), how, counts=counts)


def _workers_left_mid_flight(root: Path) -> list[dict]:
    """Each Worker this project has that is still holding something, as
    `{"worker", "ticket", "paths", "question"}` (SCRUM-20).

    ⚠ **Read, never released.** `perform_handover` releases claims, and that
    is right when a Worker has stopped — but a Manager's Workers OUTLIVE it:
    their sandboxes are their own processes. Releasing a live Worker's claim
    would let a second Worker take paths the first is still editing, which is
    the collision the ledger exists to prevent. So this reports and changes
    nothing.
    """
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.sandbox import worker_sandbox_status
    from rite_ai.sandbox.questions import Unknown, pending_question

    workers_dir = root / "workers"
    if not workers_dir.is_dir():
        return []
    left: list[dict] = []
    ledger = ClaimsLedger(root / ".rite" / "claims.json")
    for entry in sorted(
        p for p in workers_dir.iterdir() if (p / "worker.yml").is_file()
    ):
        worker = entry.name
        try:
            claims = ledger.claims_for(worker)
        except Exception:  # noqa: BLE001
            claims = []
        question = ""
        try:
            status = worker_sandbox_status(worker, root)
            if getattr(status, "known", False) and str(status) != "not found":
                from rite_ai.sandbox import existing_sandbox_name

                name = existing_sandbox_name(worker, root)
                asked = pending_question(name) if name else None
                if asked is not None and not isinstance(asked, Unknown):
                    question = getattr(asked, "question", "") or "a question"
        except Exception:  # noqa: BLE001
            question = ""
        if not claims and not question:
            continue
        left.append(
            {
                "worker": worker,
                "ticket": next((c.ticket for c in claims if c.ticket), ""),
                "paths": sorted({p for c in claims for p in c.paths}),
                "question": question[:200],
            }
        )
    return left


def _hand_over_on_stop(root: Path, manager: str, how: str, say=None) -> list[dict]:
    """🔴 SCRUM-20. When a Manager's run ends, say which Workers it left
    holding something — in the Owner's DM, not only in a pane nobody is
    watching.

    **The gap this closes.** A Manager stopped on a bound while a Worker sat on
    an unanswered question with a claim held and uncommitted work in its
    sandbox. `rite status` said "no handover snapshot recorded yet", nothing
    reached the person, and the Worker waited on an answer whose only carrier
    had gone. The Worker was fine; nobody knew it was alone.

    Returns what it found so the lifecycle record can carry it: a reader that
    sees ENDED must be able to see what was left mid-flight.
    """
    # 🔴 Nothing in a REPORT may end a run. This is called from the run's
    # `finally`, so an exception here would replace whatever actually ended it
    # — and the first draft of this function did exactly that, raising
    # ImportError from a mistyped ledger class and taking the run with it.
    try:
        left = _workers_left_mid_flight(root)
    except Exception as e:  # noqa: BLE001
        if say:
            say(f"could not work out which Workers were left holding work: {e}")
        return []
    if not left:
        return []
    who = ", ".join(
        f"{w['worker']}"
        + (f" on {w['ticket']}" if w["ticket"] else "")
        + (" (waiting on an answer)" if w["question"] else "")
        for w in left
    )
    text = (
        f"Manager {manager!r} has stopped ({how}), and {len(left)} Worker(s) "
        f"are still holding work: {who}. Their sandboxes and claims are "
        f"untouched — nothing was released, because a running Worker's claim "
        f"is what stops a second Worker editing the same files. "
        + (
            "A Worker waiting on an answer will not get one until a Manager "
            "runs again, or you answer it in its own session. "
            if any(w["question"] for w in left)
            else ""
        )
        + f"Start a Manager again to pick them up: `rite start {manager}`."
    )
    try:
        from rite_ai.managers.asking import raise_to_person

        raise_to_person(
            root, manager, subject="", raiser=f"manager:{manager}", text=text
        )
    except Exception as e:  # noqa: BLE001 - said, never raised into the run
        if say:
            say(f"could not tell the User which Workers were left: {e}")
    if say:
        say(text)
    return left


def _supervise(
    root: Path,
    manager: str,
    *,
    engine: str = "",
    agent: str = "",
    max_sessions: int | None = None,
    window_seconds: float | None = None,
    prompt: str = "",
    fresh: bool = False,
    began_under: dict | None = None,
    verdict: object = None,
    note: object = None,
    starter: object = None,
    engine_ready: object = None,
    slack: object = None,
    github: object = None,
    resume_id_for: object = None,
    broker: object = None,
    router: object = None,
    waiting: object = None,
    chores: object = None,
    poll: float = POLL_SECONDS,
    now: object = None,
    watch: object = None,
    refine: object = None,
    refinement_brief: object = None,
) -> SuperviseResult:
    """Run the Manager until a bound or a stop verdict ends it.

    ⚠ **`engine_ready` is checked BEFORE EVERY CYCLE, and its absence is
    how a local Manager would spend a whole window doing nothing.**
    `ending` classifies a cycle by the engine's exit status, which is
    correct for `claude -p` — its exit IS the boundary. **It is not correct
    for every engine.** Measured 2026-09-24: `goose run` returns **exit 0**
    for a model that does not exist and **exit 0** for an unreachable
    provider. So a Goose Manager pointed at a dead endpoint would be read as
    `finished` every cycle, and this loop would keep starting sessions until
    `--sessions` or `--minutes` ran out — the whole window spent, nothing
    done, every cycle reported clean. That is RL-47's night arriving at the
    Manager tier.

    It returns the reasons this engine is not usable, or an empty list. It
    is checked **before the mail is taken**, because `take_mail` deletes what
    it reads and a cycle abandoned after that would lose the messages it was
    carrying.

    `verdict`, `starter`, `engine_ready`, `resume_id_for` and `now` are
    injectable so this
    can be driven without spending anything. They are NOT an abstraction
    boundary — see the module docstring; they exist so the bounds can be
    verified by running rather than by reading, which is the only way to
    know a ceiling bounds anything.
    """
    clock = now if callable(now) else time.time
    # TR2's no-progress guard: what a refinement session was handed to start,
    # and when. Set when one starts, counted when it ends.
    handed: dict = {}
    handed_at = 0.0
    told: set[str] = set()
    """"Cannot tell" lines about cut prompts already said in this run."""
    # Injectable so a test can read what a human would have been told,
    # and a no-op by default so nothing prints from a library call.
    say = note if callable(note) else (lambda _m: None)
    if callable(watch):
        # ⚠ `watch(say)` rides on the router, deliberately (dogfood Q1–Q4,
        # part B): every place this supervisor does its file work with no
        # engine to watch — each poll while a session runs, each cycle
        # boundary, each tick of a wait — already calls `router(say)`. Joined
        # here once, it runs at all of them, and a Worker's question is seen
        # whether or not the Owner has a session. The watcher throttles
        # itself; it is called at the poll rate.
        routing_step = router

        def router(say_):
            if callable(routing_step):
                routing_step(say_)
            watch(say_)

    # 🔴 SCRUM-20 (perpetual). With no bound from the operator, the run does
    # not end: the ceilings below become PER-CYCLE guardrails, and reaching one
    # ends the cycle and waits rather than returning. `perpetual` is resolved
    # once here so every later check reads one answer.
    perpetual = _perpetual(max_sessions, window_seconds)
    if perpetual:
        max_sessions = PERPETUAL_SESSIONS_PER_CYCLE
        window_seconds = PERPETUAL_CYCLE_SECONDS
    elif max_sessions is None or window_seconds is None:
        # One bound given and not the other cannot happen through the CLI,
        # which refuses it; a caller that does it gets the bound it asked for
        # and a default for the other rather than an accidental forever.
        max_sessions = (
            PERPETUAL_SESSIONS_PER_CYCLE if max_sessions is None else max_sessions
        )
        window_seconds = (
            PERPETUAL_CYCLE_SECONDS if window_seconds is None else window_seconds
        )

    begin = clock()
    deadline = begin + window_seconds if window_seconds > 0 else None
    launch = starter if callable(starter) else _default_starter
    next_id = (
        resume_id_for if callable(resume_id_for) else _resume_id_source(engine, agent)
    )

    # ⚠ Said ONCE and passed EVERY cycle. Every cycle because nothing
    # carries a permission mode into `-p`; said because this grant is total
    # and the one line below is all that stands between a user and a
    # surprise.
    spelling = spelling_for(engine, agent)
    # ⚠ **Written before the first launch and passed on EVERY cycle.** The
    # file is rewritten from code each run so the list a Manager gets is the
    # list this release ships; the arguments are re-passed because nothing
    # carries a permission decision into `-p` — the same reason the flag
    # they replace had to be.
    if spelling.permission_env:
        # ⚠ **AN ENGINE THAT KEEPS ITS MODE IN THE ENVIRONMENT GETS ONE, AND
        # UNTIL NOW GOT NOTHING.** `launch_command` correctly refuses to
        # write a flag for such an engine, and nothing put the value
        # anywhere else — so a Goose Manager launched with whatever
        # `GOOSE_MODE` the operator's shell happened to carry, or with
        # Goose's own default when it carried none. Measured 2026-09-24:
        # that default is `auto`, which ran `rm` on a file unattended.
        #
        # Setting it explicitly is what makes the mode rite's decision for
        # the session rather than an ambient one — an operator with
        # `GOOSE_MODE=approve` exported would otherwise get a Manager that
        # fails on its first real operation (measured: exit 1), with
        # nothing saying why.
        permission = UNATTENDED_MODE_FOR_ENV_ENGINES
        say(
            f"permissions: Manager {manager!r} runs its engine with "
            f"{spelling.permission_env}={permission}. ⚠ This engine has no "
            "per-command allowlist — the mode is whole-session, so the "
            "allowlist a `claude` Manager gets does not exist here, and the "
            "sandbox below is the ONLY boundary this Manager has."
        )
    elif spelling.permission_in_file:
        # CU8: the allowlist is written to the engine's own config file before
        # every launch (`cursor_login.write_config`), not passed on argv.
        permission = ""
        say(cursor_login.announcement(manager))
    else:
        permission = launch_arguments(write_settings(root))
        say(announcement(manager))

    # ⚠ **Said every run, and deliberately not folded into the permission
    # line above.** A boundary sold as more than it is would be worse than
    # none, so what it does NOT buy is stated rather than left to be
    # inferred from what it does.
    # ⚠ Named `confinement`, not `boundary`: `checkins.at_boundary` already
    # owns that name later in this function and means a check-in window.
    confinement = boundary_for()
    say(
        f"sandbox: Manager {manager!r} runs inside a {confinement.name} "
        f"boundary ({confinement.mechanism}). What that does NOT do:"
    )
    for limit in confinement.limitations():
        say(f"  - {limit}")

    # ⚠ K6: no daemon, so a window that passed while nothing ran posted
    # nothing. What is waiting, and when it will be asked, is said here.
    say(checkins.start_line(root, manager))

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
    # ⚠ CU3: an engine whose handle is a UUID rite chooses (Cursor) takes its
    # handle from `_open_chat`, recorded BEFORE any launch, and never reaches
    # the discovery, foreign-designation or fresh-fallback paths below. Each
    # of those is correct for an engine that assigns its own id and wrong
    # here: a UUID is never `session_name`, so the foreign check would call
    # every Cursor designation foreign and start fresh.
    chat: _Chat | None = None
    if spelling.handle_is_uuid:
        opened = _open_chat(root, manager, fresh, say, spelling)
        if isinstance(opened, str):
            return SuperviseResult(False, opened, [])
        chat = opened
    resume_from = "" if fresh else designated(root, manager)
    if chat is not None:
        resume_from = chat.handle
    foreign = (
        chat is None
        and bool(resume_from)
        and not _designation_is_ours(
            root, manager, resume_from, spelling_for(engine, agent).handle_is_ours
        )
    )
    if foreign:
        # ⚠ C8. Said in its own words, not `_could_not_continue`'s: "the
        # provider forgot it" and "it is not this project's" are different
        # facts — the timezone precedent — and only the second means the
        # file itself may be wrong. Not deleted: it may be a transcript the
        # user pruned, and the next run's designation replaces it anyway.
        say(
            f"the session designated for {manager!r} ({resume_from}) is not "
            f"one of this project's conversations — its transcript may have "
            f"been pruned, or the designation names another project's "
            f"session — so this run starts FRESH rather than continue it."
        )
        resume_from = ""
    continuing = bool(resume_from)
    # Never for a chat: Cursor does not fail on an unknown chat (it creates
    # one), and falling back to a NEW handle would orphan the recorded one.
    tried_designation = continuing and chat is None
    # The board as it stood when the current cycle LAUNCHED (`board_context`).
    # Read at launch, not when the cycle is designated: a session started with
    # no board may configure one before it ends, and must still be recorded
    # as having begun without one.
    launched_under: dict | None = None
    if chat is None and not fresh and not continuing and not foreign:
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

    # RP1 piece 2: BEFORE the first session, so everything this Manager says
    # from here is tracked until it reaches a person, and only what was
    # already in its outbox is recorded as predating the tracking.
    from rite_ai.managers import pending

    tracking = pending.sync(root, manager)
    if tracking:
        say(tracking)

    # ⚠ THE NO-PROGRESS GUARD (F22). Set when a session the BOARD started
    # ended having changed nothing rite can see; cleared by anything that
    # changes. While set, a board that still reads the same starts no
    # session. See `progress` for what counts and why.
    stalled: _Stalled | None = None
    # ⚠ A PERSON'S MESSAGE IS DELIVERED, OR THE PERSON IS TOLD IT WAS NOT
    # (coordinator, 2026-09-28). The inbox names a session was started to
    # deliver: if the same ones are still there afterwards, that session
    # could not take them, and they are reported rather than retried (F22's
    # lesson: repeating a session with the same inputs is the loop).
    delivering: set[str] = set()
    # The board as the verdict last read it. A cycle mail started is judged
    # against it too: mail is new INPUT, not progress, and a session handed a
    # message that then changes nothing is as idle as one handed none.
    last_basis = None
    # SCRUM-20 (perpetual): where THIS cycle's counting starts. The per-cycle
    # ceilings read `cycles[cycle_from:]`; `cycles` itself keeps the whole run,
    # because that is what the lifecycle record and the caller read.
    cycle_from = 0
    cycle_began = begin
    empty_cycles = 0

    def this_cycle() -> list:
        return cycles[cycle_from:]

    def note_cycle_end() -> int:
        """0 when this cycle started a session, else one more empty cycle.

        ⚠ **Deliberately NOT routed through `this_cycle()`.** It asks the one
        question that cannot be wrong if the per-cycle slice is: did the list
        grow since this cycle began? Mutating `this_cycle()` to return the
        whole run made the slice never-empty, which silenced a counter built
        on it and turned the loop into the hang `_spinning` exists to stop.
        """
        return 0 if len(cycles) > cycle_from else empty_cycles + 1

    while True:
        # ⚠ WHAT CAUSES THIS CYCLE. "" means the ordinary causes: the last
        # session ended cleanly and the board says continue. "mail" means a
        # wait below ended because mail is in the inbox (DF2), and then the
        # ceiling and the verdict are not asked again — the mail IS the cause.
        cause = ""
        # BOTH bounds before starting. A ceiling checked afterwards reports
        # rather than bounds, and the window is what limits cost because the
        # count does not (§9.14.5).
        stopped = _idle_allowance_spent(manager, max_sessions, this_cycle())
        if stopped is not None:
            if not perpetual:
                return stopped
            # Perpetual: the allowance bounds the CYCLE, not the run.
            empty_cycles = note_cycle_end()
            cycle_began, cycle_from = _begin_a_new_cycle(
                "its idle allowance is spent", cycles, clock, say
            )
            deadline = cycle_began + window_seconds
            spun = _spinning(empty_cycles, cycles)
            if spun is not None:
                return spun
            _wait_a_cycle_out(poll, say)
            continue
        if _counted(this_cycle()) >= max_sessions:
            if perpetual:
                # The ceiling bounds the cycle, not the run (SCRUM-20).
                empty_cycles = note_cycle_end()
                cycle_began, cycle_from = _begin_a_new_cycle(
                    f"{max_sessions} session(s) in it did work", cycles, clock, say
                )
                deadline = cycle_began + window_seconds
                spun = _spinning(empty_cycles, cycles)
                if spun is not None:
                    return spun
                _wait_a_cycle_out(poll, say)
                continue
            why = _reason_to_wait(root, manager, waiting, router, slack, say)
            if not why:
                extra = _counted(cycles) - max_sessions
                return SuperviseResult(
                    True,
                    f"ceiling reached: {max_sessions} session(s) that did work"
                    + (
                        f", and {extra} more started by routed mail past it"
                        if extra > 0
                        else ""
                    )
                    + (
                        f" ({len(cycles)} started in all; {_idle(cycles)} "
                        "changed nothing, and the ceiling does not count those)"
                        if _idle(cycles)
                        else ""
                    )
                    + ". This is a COUNT, not a spend limit — a session may "
                    "run for any length of time inside it.",
                    cycles,
                )
            # ⚠ **THE CEILING IS SOFT WHILE ROUTES ARE OUTSTANDING (Robert,
            # 2026-09-27), and that is said every time it bends.** Each cycle
            # past it is started by mail, never by the board, and waiting
            # spends nothing. ⚠ **But not without bound (W15 (a)):** observed,
            # a secondary repeating itself drove five Owner sessions against a
            # ceiling of two. So past the ceiling there is a second limit, the
            # MAIL-STARTED CAP, derived from the work routed this run rather
            # than from a second number, and reaching it is said as itself.
            # ⚠ The cap is checked when a mail-started session would BEGIN,
            # not before waiting: waiting spends nothing, and a note rite
            # writes during the wait (DIED, FINISHED WITHOUT A REPLY) earns
            # its own allowance. Checked before the wait, a death after a
            # reply and a correction was refused before its note existed.
            stopped = _wait_for_mail(
                root,
                manager,
                waiting,
                router,
                slack,
                say,
                clock,
                deadline,
                poll,
                cycles,
                live,
            )
            if stopped is not None:
                return stopped
            stopped = _at_the_cap(manager, waiting, max_sessions, cycles, why)
            if stopped is not None:
                return stopped
            cause = "mail"
            # Recomputed: what was true when the wait began may not be now.
            why = waiting.reason() or why
            say(
                f"the ceiling ({max_sessions} session(s)) is reached, and it is "
                f"SOFT while {why}: mail arrived, so this cycle starts because "
                f"of it (session {_counted(cycles) + 1} of at most "
                f"{waiting.cap(max_sessions)} under the mail-started cap)"
            )
        if deadline is not None and clock() >= deadline:
            if perpetual:
                # The window bounds the cycle, not the run (SCRUM-20).
                empty_cycles = note_cycle_end()
                cycle_began, cycle_from = _begin_a_new_cycle(
                    f"its {int(window_seconds)}s window elapsed", cycles, clock, say
                )
                deadline = cycle_began + window_seconds
                spun = _spinning(empty_cycles, cycles)
                if spun is not None:
                    return spun
                _wait_a_cycle_out(poll, say)
                continue
            return SuperviseResult(
                True,
                f"window elapsed after {int(clock() - begin)}s "
                f"({len(cycles)} session(s) started)",
                cycles,
            )

        if github is not None:
            # Each cycle starts on a token with most of its hour left.
            for line in github.refresh():
                say(line)

        if callable(engine_ready):
            # ⚠ BEFORE `take_mail`. Taking mail deletes it, so a cycle
            # abandoned after that point would lose the message it was
            # about to deliver.
            not_ready = list(engine_ready())
            if not_ready:
                return SuperviseResult(
                    False,
                    f"stopped after {len(cycles)} session(s): the engine is "
                    f"not usable, so no session was started. "
                    + " ".join(not_ready)
                    + " Nothing was spent on this cycle. ⚠ This is checked "
                    "because an engine's exit status does not always say a "
                    "run failed — measured, `goose run` exits 0 for an "
                    "unreachable provider — so without this the run would "
                    "look like a clean cycle and repeat until its bounds "
                    "ran out.",
                    cycles,
                )

        # The verdict THIS cycle read, for its board brief; None on a cycle
        # mail started, which did not read the board (SCRUM-29).
        briefed = None
        if callable(verdict) and not cause:
            answer = verdict(root)
            briefed = answer
            if answer in STOP_VERDICTS:
                why = _reason_to_wait(root, manager, waiting, router, slack, say)
                stopped = None
                if why:
                    # ⚠ NOTHING ON THE BOARD IS NOT NOTHING TO DO when work is
                    # routed (DF2): the Owner waits for what it handed out, a
                    # secondary for what it will be handed (Robert,
                    # 2026-09-27). Waiting spends no session, and the cycle
                    # after it is started by mail.
                    stopped = _wait_for_mail(
                        root,
                        manager,
                        waiting,
                        router,
                        slack,
                        say,
                        clock,
                        deadline,
                        poll,
                        cycles,
                        live,
                    )
                    if stopped is None:
                        cause = "mail"
                if not cause and answer == "waiting-on-user" and stopped is None:
                    # TR2: work is on the board and none of it can start
                    # until the User answers. Wait, spending nothing; a reply
                    # is mail, and a round's deadline passing wakes it too.
                    say(
                        f"{manager!r} waits on the User: "
                        f"{getattr(answer, 'detail', '') or 'refinement'}"
                    )
                    stopped = _wait_for_mail(
                        root,
                        manager,
                        None,
                        router,
                        slack,
                        say,
                        clock,
                        deadline,
                        poll,
                        cycles,
                        live,
                        wake=_refinement_wake(root, manager, clock),
                    )
                    if stopped is not None:
                        return stopped
                    if mail_waiting(root, manager, INBOX):
                        cause = "mail"
                    else:
                        continue
                if not cause and answer == "idle" and stopped is None:
                    # ⚠ AN IDLE BOARD IS NOT NOTHING TO DO WHILE A MESSAGE
                    # WAITS. Found on Linux (SB11, 2026-09-28): `rite message`
                    # said "delivered at the start of its next turn", and
                    # `rite start` stopped on "the board has nothing ready"
                    # with it undelivered and unsaid. Read as a STATE, now,
                    # never as an event: whatever arrived while nothing was
                    # watching is in the inbox, and that is what is asked.
                    # Only `idle`: `closed` is the person's schedule, and the
                    # other stop verdicts are faults; those runs end, and the
                    # undelivered mail is SAID at the end of the run
                    # (`undelivered_line`), as it is for every other exit.
                    names = _inbox_names(root, manager)
                    if names - delivering:
                        delivering |= names
                        cause = "mail"
                        say(
                            f"the board listed nothing ready, and {len(names)} "
                            f"message(s) are waiting for {manager!r}: a session "
                            "starts to deliver them"
                        )
                    elif names:
                        say(
                            f"{len(names)} message(s) are still waiting for "
                            f"{manager!r} after a session was started to "
                            "deliver them, so that session could not take them. "
                            "Not retried: a session started again on the same "
                            "mail would repeat the one that just could not."
                        )
                if not cause:
                    # ⚠ Before stopping: a check-in due in this window goes out
                    # rather than being skipped, and idle with questions queued
                    # means a deferral was wrong, so they are asked now (K2).
                    for said in checkins.before_stopping(root, manager, answer):
                        say(said)
                    return stopped or SuperviseResult(
                        True,
                        _why(answer, len(cycles)),
                        cycles,
                    )
            elif answer not in CONTINUE_VERDICTS:
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
            elif (
                stalled is not None
                and _basis(answer) is not None
                and _basis(answer) == stalled.basis
            ):
                # ⚠ F22: the board reads exactly as it did when a session that
                # changed nothing began. Starting another would repeat it —
                # observed, eight sessions in eighty seconds. Wait instead, in
                # the one wait there is, spending nothing. Routed work keeps
                # its own reasons to end the wait, so `waiting` goes in only
                # when there is one (see `_wait_for_mail`).
                why = _reason_to_wait(root, manager, waiting, router, slack, say)
                stopped = _wait_for_mail(
                    root,
                    manager,
                    waiting if why else None,
                    router,
                    slack,
                    say,
                    clock,
                    deadline,
                    poll,
                    cycles,
                    live,
                    wake=_stalled_wake(root, manager, verdict, stalled, clock),
                    idle_line=_idle_line(manager, stalled, clock),
                )
                if stopped is not None:
                    return stopped
                # Whatever woke it earns ONE session: the guard is set again
                # only by another session that changes nothing.
                stalled = None
                if mail_waiting(root, manager, INBOX):
                    cause = "mail"
                else:
                    # The board or the project changed: decide afresh, from
                    # the top, bounds first.
                    continue
            if not cause:
                last_basis = _basis(answer)
                handed, handed_at = _record_refinement_session(
                    root, manager, answer, clock, say
                )
        cycle_basis = last_basis

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
            if chat is not None:
                # ⚠ BEFORE the mail is taken: a refusal here must not lose it.
                before = cursor_chat.before_turn(
                    chat.created_ms, cursor_chat.observe(chat.config, chat.handle)
                )
                if before.outcome == cursor_chat.REFUSE:
                    return SuperviseResult(
                        False,
                        f"refusing to continue Manager {manager!r}: {before.reason}",
                        cycles,
                    )
                chat.expected = before.created_at_ms
                # CU8: rite's allowlist, written fresh before EVERY launch, from
                # outside the boundary, and checked after the cycle. The
                # Manager can write this file on every platform (Cursor must
                # rewrite it each turn, CU8), so the check is the protection.
                cursor_login.write_config(root, manager)
                cycle_prompt = (
                    CONTINUATION if before.outcome == cursor_chat.CONTINUE else prompt
                )
            # ⚠ **THE ONE HOOK.** Messages are appended to the instruction
            # already composed for this cycle rather than delivered by a
            # second mechanism. The engine runs with `-p` and has read its
            # stdin before anything could type into the pane, so this
            # composition point is where a Manager reliably reads — and a
            # second delivery path would be a second thing to keep correct.
            #
            # Taken, not peeked: a message delivered stays delivered, so a
            # Manager is not told the same thing every cycle until it acts.
            # ⚠ THE QUEUE IS A DRAFT (plan § K3). Inside a check-in window,
            # what was deferred goes into THIS cycle's instruction to be
            # re-read, and what the Manager does not withdraw is asked when
            # the cycle ends. Not asked here: the Manager may have answered
            # it itself since it was deferred.
            boundary = checkins.at_boundary(root, manager)
            if boundary.said:
                say(boundary.said)
            if callable(router):
                # Before the inbox is taken: a secondary's reply written while
                # this Owner was between cycles, or not running at all, belongs
                # in THIS cycle's instruction, not the one after.
                router(say)
            waiting_for_it = take_mail(root, manager, INBOX)
            if waiting_for_it:
                say(f"delivering {len(waiting_for_it)} message(s) to {manager!r}")
                # ⚠ TR9: `take_mail` has just deleted them, and a chore must
                # quote the User's words as delivered, so they are kept here,
                # outside the boundary, before the Manager ever sees an id.
                try:
                    delivered.record(root, manager, waiting_for_it)
                except OSError as e:
                    say(
                        f"could not record the instructions delivered to "
                        f"{manager!r} ({e}); a chore asked for from them will "
                        "be refused"
                    )
            refined_now = _refinement_heard(refine, waiting_for_it, say)
            # Composed once, for both launches below: the fallback needs the
            # same mail and the same reply instructions, differing only in
            # which opening text it starts from.
            extras = (
                delivery_note(waiting_for_it)
                + refined_now
                + _refinement_brief(refinement_brief, say)
                + _board_brief(briefed)
                + how_to_reply(root, manager)
                + checkins.instructions(root, manager)
                + boundary.instruction
            )
            fresh_prompt = prompt + extras
            cycle_prompt = cycle_prompt + extras
            # 🔴 The board `prompt` was COMPOSED from, when the caller says
            # (`_start_a_manager` does): every fresh launch in this run is
            # given that prompt, so that is the board its conversation began
            # under. Read here only for a caller that does not say.
            launched_under = began_under if began_under is not None else board_now(root)
            before = footprint(root, manager) if cycle_basis is not None else None
            # Taken BEFORE the launch: the engine talks to its model the moment
            # tmux starts it, so a window opened after `launch` returns would
            # miss a cut in its first request (`_say_if_the_window_was_cut`).
            launched_wall, launched_mono = clock(), time.monotonic()
            result: StartResult = launch(
                root,
                manager,
                engine=engine,
                agent=agent,
                resume_id=resume_from,
                prompt=cycle_prompt,
                permission=permission,
                max_sessions=max_sessions,
                window_seconds=window_seconds,
            )
            # ⚠ A DEGRADED START IS SAID. `ok` is True and the Manager is
            # running, but something the operator needs is missing — tmux not
            # naming the pane means a clean finish reads as `unclear` and the
            # Manager will not resume. Measured; it used to be swallowed as an
            # empty pane id, and the symptom surfaced three steps later.
            if result.warning:
                say(result.warning)
            if (
                not result.ok
                and tried_designation
                and not cycles
                and getattr(result, "engine_died", False)
            ):
                # ⚠ THE EXISTENCE CHECK IS ON THE SESSION, NOT THE FILE. A
                # designation can be present and perfectly readable while
                # the provider has forgotten that conversation. The property
                # is "the resume did not take" — the start failing — and NOT
                # the wording, because a malformed id and a well-formed
                # unknown one produce DIFFERENT messages and code matching
                # one silently misses the other. The provider validates
                # before doing any work, so trying costs nothing.
                #
                # 🔴 **And only a start whose ENGINE RAN counts.** A start
                # refused before launching — a held session, one already
                # running, no login — never asked the provider, so it is no
                # answer about the conversation. It was read as one: measured,
                # a previous run's held pane refused the resume, this said
                # "could not be continued" and started FRESH, and the
                # conversation was intact on disk. Such a refusal now ends the
                # run below with its own words, and the mail goes back.
                say(_could_not_continue(manager))
                tried_designation = False
                continuing = False
                resume_from = ""
                # ⚠ **CLEAR WHAT THE FAILED RESUME LEFT HELD, or the fresh
                # start below collides with it.** A provider that has
                # forgotten the session exits at once — measured, real
                # `claude -p --resume <gone>` prints "No conversation found"
                # and exits 1 — and `start` has already set
                # `remain-on-exit`, so the dead session stays under the
                # Manager's name. The fallback then met `start`'s own
                # "left over from an earlier run" refusal: observed through
                # `rite start` with a gone designation, the run announced
                # "starts FRESH" and then started nothing. Robert's rule is
                # that a gone designation IS a fresh start, so this is the
                # rule not holding on the one path it names.
                #
                # Only a session tmux confirms is NOT alive is ended: it was
                # created by the attempt above, moments ago, and its exit is
                # the whole reason we are here.
                held = session_name(root, manager)
                here = liveness(held)
                if here.known and not here.alive:
                    stop_session(held)
                result = launch(
                    root,
                    manager,
                    engine=engine,
                    agent=agent,
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
                # ⚠ THE MAIL GOES BACK. It was taken to compose this cycle's
                # instruction, and this cycle did not start — so nothing
                # delivered it. Observed losing a routed instruction when a
                # second `rite start` for a running Manager was refused.
                if waiting_for_it:
                    kept = put_back(waiting_for_it)
                    say(
                        f"{kept} of {len(waiting_for_it)} message(s) taken for "
                        f"this cycle put back in {manager!r}'s inbox — the "
                        f"cycle did not start, so none was delivered"
                    )
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
                if callable(router):
                    # ⚠ ROUTING, in the wait loop rather than at the cycle
                    # boundary: it is file work, not a sandbox launch, and the
                    # Owner's cycles are long — a secondary should not wait for
                    # one to end to receive what it was handed. The secondary
                    # still reads it at ITS next boundary, by the one hook.
                    # `routing.deliver_routes`: the identity is this supervisor's.
                    router(say)
                if slack is not None:
                    # ⚠ **INTO THE INBOX, NOT STRAIGHT INTO THE PROMPT.** A
                    # Slack message becomes an ordinary mailbox file through
                    # the same validated writer `rite message` uses, so it
                    # reaches the Manager by THE ONE HOOK — the instruction
                    # composed at the next cycle boundary. A second delivery
                    # path was refused by name in 0.5.1 and this does not add
                    # one; the mailbox already does not care who wrote a
                    # message, which is the whole of what was done for Slack
                    # in advance.
                    for heard in slack.poll():
                        # Filed by when it was SAID, so two conversations
                        # reach the Manager in send order, not poll order.
                        send(
                            root,
                            manager,
                            INBOX,
                            heard,
                            sent_at=getattr(heard, "sent_at", None),
                        )
                    # The other direction (A4): what the Manager has said goes
                    # out while it is still working, not only at the end.
                    for line in getattr(slack, "post_replies", list)():
                        say(line)
                    for line in getattr(slack, "news", list)():
                        say(line)
                # ⚠ C6/C26: the token is re-minted BEFORE it lapses, from
                # here, so a long cycle does not run out under the Manager.
                # A failure is SAID, and the old file is left: the Manager
                # then gets a loud 401, never a silent fall to anonymous.
                if github is not None:
                    for line in github.refresh():
                        say(line)
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
            # ⚠ BEFORE the Worker requests are honoured below, which removes
            # them: a request is progress, and must still be here to count.
            stalled = None
            if before is not None:
                after = footprint(root, manager)
                if not after.differs_from(before):
                    stalled = _Stalled(cycle.number, cycle_basis, after)
                    # SCRUM-24: and the ceiling does not count it.
                    cycle.idle = True
            refused = _say_refusals(
                root, cycle.started_at, say, engine, agent, live_pane, manager
            )
            # ⚠ Deliveries BEFORE Worker requests: "deliver alpha, then start
            # alpha on its next ticket" in one cycle needs alpha's sandbox gone
            # first, and a delivery removes it (PB1).
            from rite_ai.publishing.requests import honour_deliveries

            honour_deliveries(root, manager, say)
            # And the PRs delivered earlier: merged ones release their
            # claims, and `auto_merge` merges through its gate (PB1 piece 5).
            from rite_ai.publishing.merging import tick as watch_pull_requests

            watch_pull_requests(root, manager, say)
            _honour_worker_requests(root, manager, broker, say)
            if callable(chores):
                # TR9: at the boundary with the Worker requests, and for the
                # same reason: it talks to the board, which the two-second
                # poll must not wait on.
                chores(say)
            if callable(refine):
                # TR2: the rounds the Owner asked for this turn go out, with
                # the board, at the same boundary and for the same reason.
                _refinement_step(refine, say)
            # AFTER the rounds went out: a round sent this turn is progress.
            _count_misses(root, manager, handed, handed_at, say)
            handed, handed_at = {}, 0.0
            if callable(router):
                # And once more at the boundary, for a request written in the
                # cycle's last two seconds.
                router(say)
            _say_if_the_sandbox_refused(root, manager, live_pane, say)
            cuts = _say_if_the_window_was_cut(
                root,
                manager,
                agent,
                launched_wall,
                cycle.ended_at,
                say,
                told,
                # Only a real wall clock can be checked against a monotonic
                # one; an injected clock is a test's.
                monotonic_elapsed=(
                    time.monotonic() - launched_mono if clock is time.time else None
                ),
            )

            how = ending(result.session, human_was_present=attended, pane=live_pane)
            cycle.ending = how.kind
            if waiting is not None and how.kind != "crashed":
                # ⚠ HANDLED = THE CYCLE THAT CARRIED IT ENDED, written only
                # now, after the pane is gone — so anything this cycle said is
                # already in its outbox when the Owner reads this, and a
                # progress reply mid-cycle cannot end the Owner's wait. A
                # crash is not handled: the work may not have happened, and
                # the Owner learns this supervisor is gone instead.
                waiting.handled([m.path.name for m in waiting_for_it])
            # ⚠ DESIGNATED WHATEVER THE ENDING. A cycle that quit or
            # crashed is still the conversation a user comes back to
            # tomorrow — arguably more so. Designating only resumable
            # endings would record nothing for the endings a human most
            # wants to pick up, which is the mechanic the design note left
            # open and warned about.
            observed = next_id(root, manager, cycle.started_at)
            chat_broken = ""
            config_tampered = ""
            if chat is not None:
                chat_broken = _check_chat_after(
                    root, manager, chat, launched_under, say
                )
                # CU8: the allowlist rite wrote must still be the one on disk.
                # A change means something inside the boundary rewrote it, so
                # the run stops rather than continuing under an allowlist rite
                # did not choose.
                config_tampered = cursor_login.config_problem(root, manager)
                if config_tampered:
                    say(f"⚠ Manager {manager!r}: {config_tampered}")
            if observed and chat is None:
                # A fresh cycle — including the fallback after a resume that
                # did not take — records the board it began under; a continued
                # one carries the recorded board forward.
                designate(
                    root,
                    manager,
                    observed,
                    board=None if resume_from else launched_under,
                )
            # ⚠ RECORDED, for the standup (plan § K4): each cycle, how it
            # ended, and what the engine refused in it — with the session id
            # a reader can open. Printed lines are gone by the check-in.
            checkins.record(
                root,
                manager,
                {
                    "event": "cycle",
                    "at": cycle.ended_at,
                    "number": cycle.number,
                    "session": observed or cycle.session,
                    "started_at": cycle.started_at,
                    "ending": how.kind,
                },
            )
            for cut in cuts:
                # For the standup, which renders it (`standup.digest`): printed
                # lines are gone by the check-in. SERVER-scoped by name and by
                # field, because the log does not say whose prompt it was.
                checkins.record(
                    root,
                    manager,
                    {
                        "event": "ollama_cut",
                        "scope": "server",
                        "at": cut.at,
                        "number": cycle.number,
                        "sent": cut.sent,
                        "kept": cut.kept,
                    },
                )
            for command in refused:
                checkins.record(
                    root,
                    manager,
                    {
                        "event": "refusal",
                        "at": cycle.ended_at,
                        "number": cycle.number,
                        "session": observed or cycle.session,
                        "command": command,
                    },
                )
            # A check-in prepared at this cycle's boundary goes out now,
            # whatever the ending, AFTER this cycle is recorded so its
            # standup includes it. A crash is not a reason to leave the User
            # unasked.
            said = checkins.after_cycle(root, manager)
            if said:
                say(said)

            if config_tampered:
                return SuperviseResult(
                    False,
                    f"stopped after {len(cycles)} session(s): {config_tampered}. "
                    "Nothing further runs on an allowlist rite did not write; "
                    "the next `rite start` writes it afresh, and what changed "
                    "it is worth finding first.",
                    cycles,
                )
            if chat_broken:
                return SuperviseResult(
                    False,
                    f"stopped after {len(cycles)} session(s): {chat_broken}. "
                    f"No further cycle runs on it; `rite start {manager} "
                    "--fresh` starts a new conversation deliberately.",
                    cycles,
                )
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
            if cycles and chat is None:
                interrupted_id = next_id(root, manager, cycles[-1].started_at)
                if interrupted_id:
                    designate(
                        root,
                        manager,
                        interrupted_id,
                        board=None if resume_from else launched_under,
                    )
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


def _at_the_cap(manager, waiting, ceiling: int, cycles, why: str):
    """A result that stops the run at the mail-started cap, or None.

    ⚠ **Said as ITSELF, never as the ceiling.** "Reached the session ceiling"
    and "reached the mail-started cap with routed work outstanding" need
    different responses: the first is the number the person chose, the second
    means a Manager kept sending mail past what its routed work explains."""
    cap = waiting.cap(ceiling)
    if _counted(cycles) < cap:
        return None
    routed = waiting.routed_this_run()
    notes = waiting.notes_this_run()
    return SuperviseResult(
        True,
        f"stopped at the MAIL-STARTED CAP, not the ceiling: {_counted(cycles)} "
        f"session(s) did work, and the cap is --sessions {ceiling} plus 2 per "
        f"message routed this run ({routed})"
        + (f" plus 1 per note rite wrote ({notes})" if notes else "")
        + f" = {cap}. It was reached while "
        f"{why}. Anything still arriving waits in the inbox for the next "
        f"`rite start {manager}`.",
        cycles,
    )


_sleep = time.sleep
"""The wait's pause. A module attribute so a virtual-clock test can advance
its clock here instead of sleeping."""


def _relay_tick(root: Path, manager: str, router, slack, say) -> None:
    """One tick of the file work a supervisor does with no engine to watch:
    routing both ways, and the Slack relay in both directions. The same calls
    the in-session wait loop makes, in the same order."""
    if callable(router):
        router(say)
    if slack is not None:
        for heard in slack.poll():
            send(root, manager, INBOX, heard, sent_at=getattr(heard, "sent_at", None))
        for line in getattr(slack, "post_replies", list)():
            say(line)
        for line in getattr(slack, "news", list)():
            say(line)


BOARD_RECHECK_SECONDS = 60.0
"""How often a Manager waiting under the no-progress guard reads the board
again. A read is a request to the backend, so not every poll tick."""
PROJECT_RECHECK_SECONDS = 15.0
"""How often it looks at the project again (two `git` calls)."""


@dataclass(frozen=True)
class _Stalled:
    """A session the board started that changed nothing (F22)."""

    number: int
    basis: object
    footprint: Footprint


def _refinement_step(refine, say) -> None:
    """`refine(say)`: send the rounds asked for, retry unwritten accepts.
    Never ends a run: a refinement that could not run is said."""
    try:
        refine(say)
    except Exception as e:  # noqa: BLE001 - said, and the cycle goes on
        say(f"refinement could not run this cycle: {type(e).__name__}: {e}")


def _refinement_brief(brief, say) -> str:
    """How the Owner refines, and this cycle's refinement work (TR2), or "".
    Never ends a run: a brief that could not be composed is said, and the
    cycle goes on without it, which the Owner is told."""
    if not callable(brief):
        return ""
    try:
        return brief(say) or ""
    except Exception as e:  # noqa: BLE001 - said, and the cycle goes on
        say(f"the refinement brief could not be composed: {e}")
        return (
            "\n\n## Refinement: this cycle (rite)\n\nrite could not compose "
            "this cycle's refinement list. Do not refine from memory.\n"
        )


def _board_brief(answer) -> str:
    """This cycle's board as rite read it outside the boundary, as a section
    of the instruction, or "" (SCRUM-29).

    ⚠ **A Manager was told to read the board with a command that cannot,
    from where it runs.** Its prompt said to work the queue with `rite loop
    run`; inside its sandbox a Jira board's credentials are withheld by
    design (a Manager is not given rite's credentials), so that command can
    only answer `unknown` there — measured, and before 0.7.0a4 it was a
    traceback. The supervisor has just read the same board from outside, so
    the ready and blocked tickets it acted on are handed over here instead.

    Only from a `LoopAnswer` with a basis: a plain verdict, a refinement
    cycle (whose own brief covers it) or a cycle mail started has no board
    reading behind it to report, and nothing is invented for it."""
    basis = getattr(answer, "basis", None)
    if not basis or len(basis) != 3:
        return ""
    from rite_ai.loop import as_of

    verdict, ready, blocked = basis
    lines = [
        "",
        "",
        "## The board this cycle (rite)",
        "",
        f"rite read the board outside your sandbox as of "
        f"{as_of(getattr(answer, 'read_at', None))}: verdict `{verdict}`.",
        "Ready to start: " + (", ".join(ready) if ready else "none") + ".",
    ]
    for ticket, why in blocked:
        lines.append(f"Blocked: {ticket} — {why}")
    lines.append(
        "Work from this list. `rite loop run` inside your sandbox cannot read a "
        "board whose credentials you are not given, and answers `unknown` "
        "there: that is your sandbox, not the board."
    )
    return "\n".join(lines) + "\n"


def _refinement_heard(refine, messages, say) -> str:
    """What the User's replies just delivered did to their rounds, as a
    section of this cycle's instruction, or "" (TR2). rite's lines, in
    rite's words: whether an accept was recorded is rite's to say."""
    if not callable(refine) or not messages:
        return ""
    try:
        lines = refine(say, messages=messages) or []
    except Exception as e:  # noqa: BLE001 - said, and the cycle goes on
        say(f"refinement could not read the delivered replies: {e}")
        return ""
    if not lines:
        return ""
    return (
        "\n\n## Refinement: what the User's replies did (rite)\n\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n"
    )


def _refinement_wake(root: Path, manager: str, clock):
    """`wake` for a wait on the User: a refinement round's deadline passed
    since the wait began. Read from the round ledger alone, never the board,
    so a long wait costs no board reads. A reply needs no wake: it is mail."""
    from rite_ai.refinement import rounds

    began = clock()

    def wake() -> str:
        now = clock()
        events = rounds.events_since(rounds.all_attempts(root, manager), began, now=now)
        if events.deadlines:
            return f"{events.deadlines} refinement round(s) reached their deadline"
        return ""

    return wake


def _record_refinement_session(
    root: Path, manager: str, answer, clock, say
) -> tuple[dict, float]:
    """A session starting for refinement is recorded, so the next one needs
    the User to have done something first (the note's part 3.4 step 0).
    Returns what it was handed to start and when, for `_count_misses`."""
    if str(answer) != "refining":
        return {}, 0.0
    from rite_ai.refinement import rounds

    at = clock()
    handed = dict(getattr(answer, "starting", None) or {})
    try:
        rounds.record_session(root, manager, at)
    except OSError as e:
        # Unrecorded, the next cycle reads a first look again and may start
        # one more refinement session, still bounded by S and K. Said.
        say(f"could not record the refinement session for {manager!r}: {e}")
    return handed, at


def _count_misses(root: Path, manager: str, handed: dict, since: float, say) -> None:
    """At the end of a refinement session: the no-progress guard. Never ends
    a run; a guard that could not run is said."""
    if not handed:
        return
    from rite_ai.refinement import rounds

    try:
        for line in rounds.count_misses(root, manager, handed, since=since):
            say(f"refinement: {line}")
    except OSError as e:
        say(f"could not count refinement misses for {manager!r}: {e}")


def _basis(answer) -> object:
    """What the board looked like behind a verdict, or None when the verdict
    carries none. Then the guard never engages: it cannot tell "unchanged"."""
    return getattr(answer, "basis", None)


def _stalled_wake(root: Path, manager: str, verdict, stalled: _Stalled, clock):
    """`wake` for the guard's wait: why a session should start now, or ""."""
    marks = {"board": clock(), "project": clock()}

    def wake() -> str:
        now = clock()
        if now - marks["project"] >= PROJECT_RECHECK_SECONDS:
            marks["project"] = now
            changed = footprint(root, manager).differs_from(stalled.footprint)
            if changed:
                return "the project changed (" + ", ".join(changed) + ")"
        if now - marks["board"] >= BOARD_RECHECK_SECONDS:
            marks["board"] = now
            answer = verdict(root)
            if answer not in CONTINUE_VERDICTS or _basis(answer) != stalled.basis:
                return "the board changed"
        return ""

    return wake


def _idle_line(manager: str, stalled: _Stalled, clock):
    """`idle_line` for the guard's wait: said when it begins, then every
    `STILL_WAITING_EVERY` with a ⚠, as a routed wait is."""
    from rite_ai.managers.routing import STILL_WAITING_EVERY

    said: dict[str, float | None] = {"at": None}

    def line() -> str:
        now = clock()
        if said["at"] is not None and now - said["at"] < STILL_WAITING_EVERY:
            return ""
        mark = "" if said["at"] is None else "⚠ still: "
        said["at"] = now
        return (
            f"{mark}{manager!r} is waiting, spending nothing: session "
            f"{stalled.number} changed nothing rite can see (no commit or edit "
            "in the project, claim, reply, route or Worker request), so it "
            "does not count toward --sessions, and the "
            "board reads as it did when that session began, "
            "so another would repeat it. Mail wakes it (a Slack DM, a routed "
            f"reply, `rite message {manager} …`), and so does a change on the "
            f"board (read every {int(BOARD_RECHECK_SECONDS)}s) or in the "
            "project. The window still ends the run."
        )

    return line


def _reason_to_wait(root: Path, manager: str, waiting, router, slack, say) -> str:
    """Why to wait rather than stop, read so that a reply is never stranded.
    "" means stop.

    ⚠ **THE STOP DECISION COLLECTS BEFORE IT DECIDES (tag blocker 3).** The
    ceiling check and the idle verdict read `reason()` straight after the
    cycle, and the last collect was at the cycle's boundary. A secondary
    that replied (or rite's silent-finish note, written on collecting) after
    that collect and was marked handled before this read left nothing
    outstanding and nothing in the Owner's inbox, so the Owner stopped with
    the reply in the secondary's outbox until the next `rite start`.
    Reproduced deterministically.

    ⚠ **In the order `_wait_for_mail` documents: read, then collect, then
    read again.** Collecting first is not enough: a reply written just after
    an empty collect and then marked handled is still missed by a read that
    follows. Reading first means "" can only come when every route was
    already handled, so its reply was already in an outbox when the collect
    below ran, and the second read sees it as a reply waiting. A reason
    found the first time is kept, as before, and the wait collects on its
    first tick."""
    if waiting is None:
        return ""
    why = waiting.reason()
    if why:
        return why
    _relay_tick(root, manager, router, slack, say)
    return waiting.reason()


def _wait_for_mail(
    root: Path,
    manager: str,
    waiting,
    router,
    slack,
    say,
    clock,
    deadline,
    poll: float,
    cycles,
    live: str,
    wake=None,
    idle_line=None,
) -> SuperviseResult | None:
    """Wait, with no engine running, until mail is in this Manager's inbox.
    None means start a cycle now, BECAUSE of that mail; a result means stop.

    ⚠ **`waiting` may be None, and `wake` may end the wait too (F22).** The
    no-progress guard waits here as well rather than in a wait of its own:
    one place decides when a Manager with nothing to do spends nothing. With
    no routed work outstanding the guard passes no `waiting`, because
    `Waiting.over()` then answers "every routed message was handled" and
    would end the wait at once as a STOP. `wake()` returns why to start a
    session now (the board or the project changed), or "". `idle_line()` is
    what the wait says, when a line is due, if there is no `waiting` to say
    it. None returned for a wake means the same as for mail: start a cycle.

    ⚠ **WAKE ON STATE, NOT ON AN EVENT.** Anything in the inbox starts the
    cycle — a routed instruction, a collected reply, a Slack DM — so nothing
    has to match a reply to its request, and mail that arrived while the last
    session ran is already there on the first tick.

    ⚠ **The order inside a tick is the point.** `over()` is read BEFORE the
    router runs: a secondary records "handled" only after its cycle ended,
    when its reply is already in its outbox, so reading "handled" first and
    collecting second can never end the wait with the reply one step away.
    Reversed, a tick could collect nothing, then see the route handled, then
    stop — the reply left for the next `rite start`.

    There is no timer (Robert, 2026-09-27). It ends on mail, on `over()`, on
    the window, or on Ctrl-C, and a wait that cannot end by itself is SAID
    every `routing.STILL_WAITING_EVERY`.
    """
    if waiting is not None:
        waiting.begin()
    try:
        while True:
            ended = waiting.over() if waiting is not None else ""
            if ended:
                # Decision 3: a wait ending because a Manager owing work is
                # gone says so to the Owner FIRST. The note is mail, so the
                # check below starts the Owner's session to tell the person.
                waiting.notice_gone(say)
            _relay_tick(root, manager, router, slack, say)
            if mail_waiting(root, manager, INBOX):
                return None
            if ended:
                return SuperviseResult(
                    True,
                    f"stopped after {len(cycles)} session(s): {ended}",
                    cycles,
                )
            if deadline is not None and clock() >= deadline:
                return SuperviseResult(
                    True,
                    f"window elapsed while waiting for mail, after "
                    f"{len(cycles)} session(s)",
                    cycles,
                )
            woke = wake() if callable(wake) else ""
            if woke:
                say(f"{woke}: starting a session for {manager!r}")
                return None
            if waiting is not None:
                line = waiting.still_waiting()
            else:
                line = idle_line() if callable(idle_line) else ""
            if line:
                say(line)
            _sleep(poll)
    except KeyboardInterrupt:
        # Between cycles: the last one is designated already, at its end.
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
            f"stopped by the operator (Ctrl-C) — interrupted during teardown, "
            f"so Manager {manager!r}'s session may still be running",
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
            f"stopped by the operator (Ctrl-C) — Manager {manager!r}'s session "
            f"{session} may still be running: {gone.detail}",
            cycles,
        )
    if gone is not None and gone.killed:
        return SuperviseResult(
            True, f"stopped Manager {manager!r} and its session", cycles
        )
    return SuperviseResult(
        True,
        f"stopped by the operator (Ctrl-C) — Manager {manager!r}'s session "
        f"had already ended",
        cycles,
    )


def _inbox_names(root: Path, manager: str) -> set[str]:
    from rite_ai.managers.mailbox import read

    return {m.path.name for m in read(root, manager, INBOX)}


def undelivered_line(root: Path, manager: str, why: str) -> str:
    """What a run that ends with mail still in the inbox says, or "".

    ⚠ **THE PROPERTY, CHECKED AT THE ONE PLACE EVERY RUN PASSES** (the end of
    `rite start`), rather than at each way a run can stop: a message a person
    sent is delivered, or the person is told it was not. Asked of the inbox as
    it stands, so it covers exits added later too."""
    names = _inbox_names(root, manager)
    if not names:
        return ""
    return (
        f"{len(names)} message(s) sent to {manager!r} were NOT delivered in "
        f"this run ({why}). They stay in its inbox and are delivered at the "
        f"start of its next session: `rite start {manager}`."
    )


def _why(answer: str, started: int) -> str:
    """`idle` is a completion; the rest are faults. A lifecycle that exits
    identically for all of them tells a human "finished" when it means
    "jammed" (§9.14.4)."""
    if answer == "idle":
        # A snapshot with its time (DF4, coordinator 2026-09-29): a person who
        # created a ticket seconds ago can see why it was not picked up, rather
        # than conclude rite is broken. A second read after a delay would only
        # narrow that window, so rite stops and says when it looked.
        read_at = getattr(answer, "read_at", None)
        if read_at is None:
            return (
                f"done: the board listed nothing ready ({started} session(s)); "
                "a ticket created outside rite shortly before may not have been "
                "listed"
            )
        from rite_ai.loop import as_of

        return (
            f"done: the board listed nothing ready as of {as_of(read_at)} "
            f"({started} session(s)); a ticket created outside rite shortly "
            "before then, or since, is not in that read. `rite start` again "
            "reads it afresh"
        )
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


def _declared_claude_model(root: Path, manager: str, engine: str) -> str:
    """The model a Claude Manager's role names, or "" for Claude's default.

    Derived from the config at the launch, as `_engine_model_env` is, so
    there is no argument for a caller to drop."""
    if engine != "claude":
        return ""
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(root / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return ""
    role = next(
        (r for r in parsed.coordination.manager_roles if r.name == manager), None
    )
    return role.model if role else ""


def _undeclared_window(manager: str) -> str:
    """The refusal for a local Manager with no `context_window` (S33): one
    sentence, whatever its agent."""
    return (
        f"{manager!r} declares no context_window, so the window its model "
        "is served with would be whatever its server defaults to, which rite "
        "cannot read before the model loads, and its agent would not be told "
        f"it. Add this line to its entry (`- name: {manager}`) under "
        "coordination.manager_roles in .rite/config.yaml:\n"
        "      context_window: 32768\n"
        "32768 is the least rite accepts; use the model's own window if it is "
        "larger and the machine has the memory"
    )


def _window_refusal(root: Path, manager: str, agent: str) -> str:
    """Why this Manager must not start for its window, or "" (S33).

    ⚠ **Asked of EVERY local agent, and asked FIRST**, before anything that
    depends on which agent it is. The rule used to live inside the Goose
    launch path (`_engine_model_env`), so any other local agent reached a
    launch on the server's default window; and `permission_placement` refuses
    an agent rite has no spelling for, which, asked first, would have told
    that Manager the wrong thing. A Manager with no agent is a Claude one and
    is not read here, so its path is exactly what it was."""
    if not agent:
        return ""
    from rite_ai.config.managers import window_undeclared
    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(root / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return ""  # said by `_engine_model_env`, which reads the same file
    role = next(
        (r for r in parsed.coordination.manager_roles if r.name == manager), None
    )
    if role is not None and window_undeclared(role):
        return _undeclared_window(manager)
    return ""


def _engine_model_env(root: Path, manager: str, agent: str):
    """WHICH model a local Manager's engine runs, from its declared role.

    DERIVED from the config, like everything else at the launch: this call
    site has dropped three passed-in arguments in two days. Returns
    `(environment, refusal)`, and the refusal is "" when there is none.

    ⚠ Before this, a Goose Manager's declared `model` and `endpoint` were
    read by `rite doctor` and by nothing that launched it. Goose then used
    the operator's GLOBAL config instead, silently. Measured 2026-09-25:
    declared `qwen3:8b`, ran `qwen3-vl:8b-instruct`.
    """
    # ⚠ **EVERY LOCAL AGENT, NOT GOOSE (S35).** This read `agent != "goose"`,
    # and it is the FIRST line of the function — so for any other local agent
    # it returned before `pin_window` below ever ran. S33 made the window
    # DECLARATION required of every agent, and that is right, but the
    # declaration was then honoured for Goose alone: a Manager on another
    # agent passed S33's check and launched with nothing pinned, on the
    # server's default. Declaring a window you do not get is worse than being
    # refused for not declaring one.
    #
    # An empty `agent` is a CLAUDE Manager (S33's `_window_refusal` says so
    # too), and its path is exactly what it was.
    if not agent:
        return {}, "", ""
    from urllib.parse import urlsplit

    from rite_ai.config.parse import ParseError, parse_config

    parsed = parse_config(root / ".rite" / "config.yaml")
    if isinstance(parsed, ParseError):
        return {}, f"config.yaml does not parse: {parsed.message}", ""
    role = next(
        (r for r in parsed.coordination.manager_roles if r.name == manager), None
    )
    if role is None or not (role.model and role.endpoint):
        return (
            {},
            (
                "a goose Manager must declare its model and endpoint in "
                "coordination.manager_roles, or Goose silently runs whatever the "
                "operator's global goose config names"
            ),
            "",
        )
    parts = urlsplit(role.endpoint)
    if parts.username or parts.password:
        # These values travel on tmux's argv (ALLOWED_ON_TMUX_ARGV), where
        # `ps` shows them to every local account.
        return (
            {},
            (
                f"the endpoint for {manager!r} carries credentials in its URL, "
                "which would appear on a process list. Put the secret in the "
                "role's `credential` and the bare URL in `endpoint`"
            ),
            "",
        )
    # ⚠ **THE WINDOW IS PINNED INTO THE MODEL, AND AN UNDECLARED ONE REFUSES.**
    # Ollama serves every model at one server-wide default unless the model
    # itself sets `num_ctx`, and that default cannot be read until a model is
    # loaded. So "unknown" was the common case, and it let a Manager start.
    # Goose was not told the window either (`GOOSE_CONTEXT_LIMIT`, which Goose
    # 1.51 reads). Observed 2026-09-28 (Ollama 0.34.2): the pin IS what Ollama
    # serves Goose; a prompt over it is cut to half the window from the front
    # with no error, and only Ollama's log says so (plan, Track MS).
    if not role.context_window:
        return {}, _undeclared_window(manager), ""
    # ⚠ **A DISPATCH, NOT A REFUSAL (S35, corrected against S33).** An
    # earlier version of this refused an agent rite has no env mapping for,
    # even with a window declared. That was wrong, and the reason is
    # `pin_window`'s own contract: it gives "a model that is served with
    # exactly `window` tokens, whatever the server's default" — the pin goes
    # INTO THE MODEL on the server, so Ollama serves that window to ANY
    # client. `GOOSE_CONTEXT_LIMIT` only tells Goose the number so it can
    # compact at 80%; it is not the enforcement. So an unmapped agent is
    # enforced too, and refusing it would have refused a Manager that works.
    #
    # What an unmapped agent does NOT get is being TOLD its window, and that
    # is said rather than hidden: an agent that does not know will run into
    # the limit instead of compacting before it.
    from rite_ai.local.context_window import pin_window
    from rite_ai.local.enforcement import for_agent, not_told

    pinned = pin_window(role.endpoint, role.model, role.context_window)
    if pinned.problem:
        return (
            {},
            (
                f"the {role.context_window}-token window for {manager!r} could not "
                f"be pinned: {pinned.problem}"
            ),
            "",
        )
    # What pinning wrote to the operator's model library, said at the start:
    # `context_window.py`'s rule is that rite names anything it puts there
    # and says how to remove it.
    note = pinned.detail if pinned.created else ""
    enforcement = for_agent(role.agent)
    if enforcement is None:
        # Pinned for everyone; only the agent-specific env is skipped. Handing
        # an agent `GOOSE_CONTEXT_LIMIT` it does not read would be worse than
        # handing it nothing, and pretending it had been told would be worse
        # than both.
        said = not_told(role.agent, role.context_window)
        return {}, "", f"{note} {said}".strip() if note else said
    return (
        enforcement.environment(role.endpoint, pinned.model, role.context_window),
        "",
        note,
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
    agent,
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
    from rite_ai.managers.engines import permission_placement

    window = _window_refusal(root, manager, agent)
    if window:
        return StartResult(False, f"refusing to start Manager {manager!r}: {window}")
    placement = permission_placement(engine, agent, permission)
    model_env, refused, created_note = _engine_model_env(root, manager, agent)
    if refused:
        return StartResult(False, f"refusing to start Manager {manager!r}: {refused}")
    # ⚠ **DERIVED HERE rather than passed in, and that is the point.** This
    # call site has silently dropped `prompt`, then `permission`, then
    # `agent` — three arguments in two days, each producing a Manager that
    # started and could not work. A fourth argument would be a fourth thing
    # to drop, and the fresh fallback below is exactly the caller that keeps
    # dropping them.
    #
    # Nothing has to be threaded: the name is a function of the Manager and
    # its engine, both of which are already here. An engine that assigns its
    # own id has no `start` spelling, so this is "" for Claude and its argv
    # is unchanged.
    # ⚠ **THE BOUNDARY, applied at the one place that builds the launch.**
    # Robert's decision: Managers get a sandbox, because the allowlist is
    # the secure default for Claude and cannot hold for Goose, whose
    # GOOSE_MODE is whole-session. What it does and does not buy is in
    # `enclosure.limitations()` and said out loud every run.
    #
    # ⚠ HOME is NOT redirected and TMPDIR is — see
    # `enclosure.ENGINE_HOME_IS_THE_OPERATORS`. Redirecting HOME costs the
    # Claude login; not redirecting TMPDIR either panics Goose or, if the
    # system temp root is granted instead, leaks every other process's
    # scratch.
    # ⚠ Raises on a platform with no boundary, BEFORE tmux is asked to start
    # anything. A Manager that dies in its pane is the failure this replaces,
    # and it reports the missing binary rather than the missing platform.
    confinement = boundary_for()
    profile = confinement.write_profile(root, manager)
    github_env = github_access.pane_environment(root, manager)
    handle_spelling = spelling_for(engine, agent)
    cursor = engine == "cursor"
    start_handle = (
        session_name(root, manager)
        if handle_spelling.handle_is_ours and not resume_id
        else ""
    )
    result = start_session(
        root,
        manager,
        pane_env={
            **(
                {placement[1]: placement[2]}
                if placement and placement[0] == "env"
                else {}
            ),
            "TMPDIR": str(confinement.engine_tmp(root, manager)),
            **model_env,
            # C6/C26: WHERE the GitHub credential is, never the credential.
            # Derived from what `github_access.open_access` left on disk, so
            # there is no argument to drop.
            **github_env,
            # The operator's git signing, which would fail every commit a
            # Manager made, turned off for this session only and numbered
            # after github's. `rite start` says so. A global hooks path is
            # reported there and deliberately NOT replaced (`git_settings`).
            **git_settings.pane_environment(root, github_env),
            # And WHERE a Claude Manager's own login is (`claude_login`).
            **claude_login.pane_environment(root, manager),
            # And WHERE a Cursor Manager's own state is (`cursor_login`):
            # paths only. Its KEY is never passed with `tmux -e`.
            **(cursor_login.pane_environment(root, manager) if cursor else {}),
        },
        engine=engine,
        # ⚠ CU4: a Cursor Manager's key is read by tmux's shell OUTSIDE the
        # boundary, into the environment of the engine alone
        # (`cursor_login.launch_prefix`). Gated on the ENGINE, not on the
        # file: a copy a killed Cursor run left must never reach a Manager
        # whose engine has since changed.
        command=(cursor_login.launch_prefix(root, manager) if cursor else "")
        + confinement.wrap(
            launch_command(
                engine,
                resume_id,
                str(manager_dir(root, manager) / PROMPT_FILE),
                permission,
                agent,
                start_handle,
                model=_declared_claude_model(root, manager, engine),
            ),
            profile,
        )
        # ⚠ CU7: the pane's shell stays alive as its process group's leader,
        # and after the engine exits it signals that group (Cursor's
        # `worker-server` included) and exits with the engine's status. A
        # live leader's group id cannot be recycled, so the signal cannot
        # reach a stranger, which a lookup-then-kill by pid could.
        + (cursor_login.REAP_SUFFIX if cursor else ""),
        prompt=prompt,
        max_sessions=max_sessions,
        window_seconds=window_seconds,
    )
    if created_note and result.ok:
        result.warning = "; ".join(w for w in (result.warning, created_note) if w)
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
