"""What a `-p` Manager may DO: everything, and it is told so out loud.

**Robert's decision, 2026-09-21, reversing the one before it:
`--dangerously-skip-permissions` on every cycle, for every Manager.**

⚠ **Nothing carries a permission mode into `-p`.** Resuming from a terminal
restores the mode a session was in, and that restoration explicitly
EXCLUDES `-p`. So the flag is a value rite composes at the launch site and
passes on every invocation, exactly as `--resume <id>` is — not something a
user sets up once.

**Why this replaced `acceptEdits`, which was the previous decision.** That
one was chosen on evidence from a bare `claude -p` probe: under
`acceptEdits` a file was written with the exact contents asked for, and a
shell command really ran. The first real run through `rite start` measured
something the probe had not — a Manager that could reach neither `rite` nor
`gh`:

    Both `rite loop status` and a direct GitHub check (`gh issue list ...`)
    are blocked pending approval — no external/network commands are going
    through in this session.

Three cycles, no ticket ever read, no artifact. **The decision changed
because the measurement did.**

⚠ **The discrepancy between the probe and the real run is UNEXPLAINED, and
was deliberately not chased.** Under `acceptEdits` a bare probe ran `touch`
and `date +%s`; a Manager could not run `rite` or `gh`. Some Bash is
permitted and some is not, and nobody here established the rule. It stopped
mattering for the decision the moment the answer became "skip permissions
entirely", and chasing it would have been work that changed nothing — but
it is written down because somebody will ask, and the honest answer is that
we know the outcome and not the mechanism.

⚠ **There is no per-Manager opt-in any more, and that is deliberate.** An
earlier shape read `.rite/user/<manager>.yaml` so a Manager could be raised
to this level by a knowing act. With this as the only level, that file
would be a key a user could set that changed nothing — worse than no key,
because it reads like a control. It was removed rather than left inert.

**0.6.0 brings configurability back in a different shape** — an allowlist
of command patterns in Claude Code's own `.claude/settings.json`, for the
advanced user, with this flag still the default. The removed code was
shaped around choosing between two mode NAMES, which an allowlist is not,
so none of it would have been reused; see
`docs/design/PERMISSION_MODE_FOR_UNATTENDED_RUNS.md`.

**The grant is real and the announcement is the only thing standing between
a user and a surprise**, which is why it says what it says: a Manager runs
unsandboxed, in the project root, on the user's own machine, and with this
flag it will not stop to ask about anything.
"""

from __future__ import annotations

PERMISSION_FLAG = "--dangerously-skip-permissions"
"""Claude Code's own flag. Not a `--permission-mode` value — passing it as
one would be a different and wrong thing."""


def announcement(manager: str) -> str:
    """What rite prints about permissions, every run.

    ⚠ **Stated rather than assumed**, for the reason the timezone fallback
    is announced: a default nobody is told about is a silent decision. It
    also does work nothing else does — without it, a Manager that CHOSE not
    to act and one that was NOT ALLOWED to act produce the same visible
    result, which is the ambiguity that cost this project a night.

    One line a user can read and know what they have agreed to.
    """
    return (
        f"permissions: {PERMISSION_FLAG} — Manager {manager!r} will NOT ask "
        f"before anything it does. It runs unsandboxed in this project's "
        f"directory, on this machine, with your own file and network access."
    )
