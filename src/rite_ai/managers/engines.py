"""How each engine spells the things rite needs from it (B3a).

⚠ **WHAT THIS REPLACES.** `launch_command` used the engine string as an
executable name and appended Claude Code's flags to whatever it was: `-p`
always, `--resume <id>` whenever there was an id, and the permission flag as
argv. That is correct for exactly one engine. For any other it produced a
command line built from another tool's vocabulary — and the old test pinned
it, with a docstring saying the interface stays unstable *"until `local`
forces it"*. `local` is forcing it.

**Three axes, and they are where a second implementation pushes back.** Named
in `V060_RELEASE_PLAN.md` before this existed, and each measured since:

1. **Who names the session.** Claude Code *generates* a session id rite must
   discover from a transcript; Goose takes a **name rite chooses**
   (`-n <name>`, measured: it resolves by name rather than recency, fails
   loudly on an unknown name, and works from any directory). Opposite
   directions of control, so the contract admits both rather than modelling
   one.
2. **How permission is expressed.** Claude takes a flag; Goose takes the
   environment variable `GOOSE_MODE`. So "permission" cannot be "a flag
   string" — an engine says where its answer goes.
3. **How the instruction arrives.** Claude `-p` reads stdin, which rite
   redirects from a file so the prompt never becomes an argument visible to
   `ps`. Goose accepts `-i <FILE>` directly.

⚠ **AND ONE THING THE CONTRACT MUST NOT SAY.** For `claude -p` the engine's
own exit IS the cycle boundary. **For Goose it is not** — measured
2026-09-24: `goose run` returns **exit 0** for a missing model *and* for an
unreachable provider. An engine's exit says its process ended, not that the
work happened or that it succeeded. `harness.run_subtask` derives `accepted`
from rite's own verify alone, and that is why.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spelling:
    """One engine's vocabulary for one turn.

    Everything here is how to SAY something rite already needs. Nothing here
    decides whether a turn succeeded — that is rite's verify, deliberately
    (R7).
    """

    binary: str
    """What to run. Empty means "the engine string is the command", which is
    the raw escape hatch below."""

    turn: str = ""
    """Argv fragment that makes the engine do ONE turn and exit. Claude's is
    the flag `-p`; Goose's is the subcommand `run`. A fragment rather than a
    flag because those are not the same kind of thing."""

    resume: str = ""
    """Template for continuing an existing conversation, with `{handle}`.
    Empty means this engine cannot resume, and rite must not invent a
    spelling for it."""

    start: str = ""
    """Template for NAMING a new conversation, with `{handle}`. Only an
    engine whose handle is ours has one.

    ⚠ **Without this, `resume` is unreachable for such an engine.** Goose
    resolves `-n <name> -r` by name and fails loudly on a name it has never
    seen — measured — so a first cycle launched with no `-n` creates a
    conversation under a name Goose chose, and the second cycle asks to
    continue a name that does not exist. The handle has to be declared on
    the way IN, not only on the way back."""

    handle_is_ours: bool = False
    """True when rite CHOOSES the handle (Goose's `-n <name>`), False when the
    engine assigns one rite must discover afterwards (Claude's session id).

    ⚠ This is the axis rite's whole designation machinery sits on —
    `designated`, `_default_resume_id`, the transcript scan all exist
    *because* Claude assigns the id. An engine where rite assigns it bypasses
    all of it, and a contract that modelled only one direction would fight
    the other."""

    per_command_refusals: bool = False
    """True when this engine can refuse an INDIVIDUAL command and leave a
    record of which one.

    ⚠ **A separate axis from `permission_env`, deliberately.** They correlate
    today — Claude has a per-command allowlist and takes a flag, Goose has a
    whole-session mode in the environment — and using one to stand for the
    other would be the conflation this codebase keeps having to undo. What a
    refusal LOOKS like is not the same question as where the mode is kept.

    Measured for Goose 2026-09-24: there is no per-command refusal in a
    headless run at all. Under `GOOSE_MODE=auto` nothing is refused; under
    `approve` the whole session dies on the first tool call with *"Tool
    approval required in non-interactive mode ... Approve/SmartApprove modes
    require an interactive terminal."* So there is nothing per-command to
    collect, and an instrument that reported "no refusals" would be
    reporting the absence of a thing it never looked for."""

    permission_env: str = ""
    """The environment variable this engine keeps its permission mode in, or
    "" when it takes a command-line flag.

    ⚠ **THE DESTINATION, NOT A BOOLEAN.** An earlier version recorded only
    whether permission was argv. That was enough to REFUSE writing a flag
    for Goose and not enough to put the value anywhere — so a Goose Manager
    launched with no permission handling at all, taking whatever
    `GOOSE_MODE` the operator's shell carried or Goose's own default when it
    carried none. Measured 2026-09-24: that default is `auto`, which ran
    `rm` on a file unattended.

    A refusal that leaves a value homeless is worse than no refusal.
    Knowing WHERE it goes is what lets the supervisor put it there."""

    prompt_flag: str = ""
    """How the instruction file is named on argv. Empty means stdin
    redirection, which is what keeps a prompt off `ps` for Claude."""


CLAUDE = Spelling(
    binary="claude",
    turn="-p",
    resume="--resume {handle}",
    per_command_refusals=True,
)
"""Unchanged, byte for byte. Every existing Manager must launch exactly as it
did before this registry existed, and a test asserts the argv."""

GOOSE = Spelling(
    binary="goose",
    turn="run",
    start="-n {handle}",
    resume="-n {handle} -r",
    handle_is_ours=True,
    permission_env="GOOSE_MODE",
    prompt_flag="-i",
)
"""Measured 2026-09-24, not read from a table. `goose run -n <name> -t ...`
then `goose run -n <name> -r` continues the same conversation, and the full
transcript is replayed to the model — verified on the wire with a logging
proxy rather than from goose's own token accounting.

⚠ Registered here so B4a builds the adapter around a spelling that was
measured, rather than inventing one. Nothing launches Goose yet."""

SUBSTITUTED = Spelling(binary="", turn="-p", resume="--resume {handle}")
"""⚠ **A STAND-IN FOR CLAUDE, not an unknown tool — and the distinction is
the whole reason this constant needs a comment.**

An engine string rite does not recognise runs AS GIVEN, with Claude's
spelling around it. That reads like the defect this registry removes, so:

**Production cannot reach it.** `config/managers.py` accepts exactly
`claude`, `human` and `local:<class>` as engines — a closed list — and
`local:*` now resolves through its declared `agent`. So the only callers that
land here are tests substituting a shell script for the `claude` binary
(`engine="sh"`, `engine=".../agent.sh"`), and those scripts are EMULATING
Claude: they want Claude's flags, because Claude's flags are what they are
standing in for.

**Giving them anything else was measured to be wrong.** Refusing a permission
flag here broke two tests whose whole point is that the permission mode
reaches the launch — the check would have removed real coverage to satisfy a
rule about a case production cannot produce.

⚠ **What the registry actually fixed** is narrower than "the engine string is
no longer an executable", and saying so plainly is worth more than the
slogan: a `local:<class>` engine now gets ITS agent's vocabulary instead of
Claude's. That was the live defect, because `local:*` is the engine that
exists and is not Claude."""

_BY_AGENT: dict[str, Spelling] = {"goose": GOOSE}


def permission_placement(engine: str, agent: str, permission: str):
    """Where this engine's permission mode goes.

    `("env", NAME, value)`, `("argv", "", value)`, or None when there is
    nothing to place. One function, so a caller cannot put it in the wrong
    place by forgetting which engine it is holding.
    """
    if not permission:
        return None
    spelling = spelling_for(engine, agent)
    if spelling.permission_env:
        return ("env", spelling.permission_env, permission)
    return ("argv", "", permission)


def spelling_for(engine: str, agent: str = "") -> Spelling:
    """Which vocabulary this Manager's engine speaks.

    `claude` and the empty default are Claude. A `local:<class>` engine names
    a TIER, not a runtime (RL-42), so its vocabulary comes from the `agent`
    its role declares — which is the field `config/managers.py` has required
    of every local engine since before this registry existed. Anything else
    is `RAW`.
    """
    if not engine or engine == "claude":
        return CLAUDE
    if engine.startswith("local:"):
        # ⚠ REFUSED, not defaulted. A local engine with no agent, or one
        # rite has no spelling for, used to fall through to `SUBSTITUTED`,
        # which is Claude's flags on whatever binary. That is the silent
        # shape of a caller that dropped `agent`, and the launch path has
        # dropped an argument three times in two days. Config already
        # requires `agent` on every `local:*` role, so only a caller that
        # lost it reaches this.
        if agent not in _BY_AGENT:
            raise ValueError(
                f"engine {engine!r} needs an agent rite can launch "
                f"({', '.join(sorted(_BY_AGENT))}); got {agent!r} — a caller "
                "that dropped `agent` would otherwise launch it with Claude's "
                "flags"
            )
        return _BY_AGENT[agent]
    return _BY_AGENT.get(engine, SUBSTITUTED)
