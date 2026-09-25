"""What a `-p` Manager may DO: a generous allowlist, and it is told so out loud.

**Robert's decision, 2026-09-24, reversing the one before it: the allowlist
REPLACES `--dangerously-skip-permissions` as the default.** The flag is not
passed any more. It remains spelled here because the release notes name it
and because a user may still set it themselves — but nothing in rite does.

His condition is the whole of this module's risk:

> *"Let's make the more secure option the default, just make sure the
> allowlist is generous and covers everything a worker needs under normal
> circumstances."*

⚠ **A too-narrow list does not fail loudly.** It produces a Manager stalled
on an approval nobody is there to give — defect class 15, *a prompt is not
an exception, it is the absence of an answer*. That is why `DEFAULT_ALLOW`
below is DERIVED FROM OBSERVED INVOCATIONS rather than imagined, and why
`--permission-prompts none` is passed alongside it: see `launch_arguments`.

## Where the default list came from

Every count below is the number of times that executable led a Bash
invocation in the recorded transcripts of rite-driven agent runs — the
v0.5.1 acceptance runs (`rtart`, `rtaccproj`, `ritectx`, the `w1`/`w2`
Workers), the permission probes, and the development sessions in this
project. 14,964 Bash invocations were parsed; heredoc bodies were stripped
first so that text inside a `cat <<EOF` did not count as a command.

**A guessed allowlist is the same instrument as a guessed threshold**, so
the two halves of this list are kept apart and labelled: what was measured,
and what was added on purpose without evidence.

## What is deliberately NOT allowed, and why

Each of these WAS observed, and each is excluded anyway:

**`bash`, `sh`** — seen 119, 54. A shell wrapper defeats the list entirely:
`bash -c "curl …"` is one hop around every other row. Allowing these would
make the rest decorative.

**`security`, `launchctl`** — seen 79, 34. Keychain and system-daemon access.
Observed only in developer sessions, never in a Manager run.
`security find-generic-password` is how a credential leaves this machine.

**`kill`, `pkill`** — seen 53, 52. A Manager that may kill processes may kill
its own supervisor.

**`curl`, `wget`** — seen 79, 0. Arbitrary network egress. The observed
GitHub need is served by `gh`.

**`claude`** — seen 48. ⚠ **This one is the documented failure.** Told
nothing about how to start Workers, a Manager improvised a bare `claude` and
the Workers died on launch. `rite sandbox start` is the supported route and
the prompt now says so; granting `claude` re-opens the road around it.

**`sudo`, `ssh`, `docker`** — seen 1, 2, 10. Barely observed, and each widens
the blast radius past this project.

⚠ **The list is a speed bump, not a sandbox, and the announcement must keep
saying so.** `git` runs hooks; `python -c` runs anything. A Manager is still
unsandboxed with the user's own file and network access — §5.1.1's asymmetry
is unchanged. What the list buys is that the *casual* route to the rest of
the machine is closed and a refusal is visible, not that a determined agent
is contained.

## How a user changes it

rite ALWAYS rewrites its own file from the constant below, so the shipped
list and the running list can never drift — the failure that a
write-once-if-absent file would have introduced on every upgrade. A user
does not edit rite's file; they use Claude Code's own
`.claude/settings.json`, which `--settings` merges with:

- to **widen**, add to `permissions.allow` there;
- to **narrow**, add to `permissions.deny` there — deny beats allow, which
  is the only direction a merge cannot express by adding.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from rite_ai.managers import user_dir

BYPASS_FLAG = "--dangerously-skip-permissions"
"""⚠ **No longer passed by anything.** Kept because C22's release note names
it and a reader of the 0.5.1 behaviour needs the string to find it."""

SETTINGS_FILENAME = "permissions.json"
"""Under `user_dir` with the instance records: per-user, per-machine, never
committed. What a Manager on THIS machine may run is a local trust decision,
not a property of the repository."""

TOOL_ALLOW: tuple[str, ...] = (
    "Read",
    "Edit",
    "Write",
    "NotebookEdit",
    "Glob",
    "Grep",
    "TodoWrite",
    "Task",
)
"""⚠ **Not Bash at all, and leaving these out scored 0/5 on the first real
C20 run.**

Claude Code's permission rules cover its OWN tools as well as shell
commands, and `Bash(...)` entries say nothing about them. A list of only
shell patterns produces an agent that can RUN anything on the list and
CHANGE nothing — measured: all five benchmark tasks worked out the correct
fix, were denied `Edit`, and printed the patch for a human to apply.
Nothing stalled and nothing crashed. The work simply did not happen, which
is this module's failure mode wearing a different hat.

The denial, verbatim from that run's transcript:

    tool=Edit
    Permission for this tool use was denied. It requires approval, and
    this session has no approval surface — nobody can answer a permission
    prompt here

⚠ **`WebFetch` and `WebSearch` are deliberately absent**, for the same
reason `curl` is: arbitrary network egress. That is a judgement rather than
a measurement, and it is the one here most likely to need revisiting.
"""


OBSERVED_ALLOW: tuple[str, ...] = (
    # Reading and navigating the project. The long tail of every run.
    "Bash(echo:*)",
    "Bash(cd:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(grep:*)",
    "Bash(sed:*)",
    "Bash(awk:*)",
    "Bash(cut:*)",
    "Bash(tr:*)",
    "Bash(sort:*)",
    "Bash(uniq:*)",
    "Bash(wc:*)",
    "Bash(find:*)",
    "Bash(diff:*)",
    "Bash(file:*)",
    "Bash(realpath:*)",
    "Bash(pwd:*)",
    "Bash(which:*)",
    "Bash(date:*)",
    "Bash(printf:*)",
    "Bash(true:*)",
    "Bash(test:*)",
    "Bash(env:*)",
    "Bash(xargs:*)",
    "Bash(stat:*)",
    # Source control. 9,971 invocations — the single largest real tool.
    "Bash(git:*)",
    # rite itself. 1,398 — and `rite --version` being refused is the exact
    # denial the v0.5.1 acceptance run recorded.
    "Bash(rite:*)",
    # The Worker sandbox a Manager is told to start Workers with.
    "Bash(yoloai:*)",
    # Running and testing code.
    "Bash(python:*)",
    "Bash(python3:*)",
    "Bash(pytest:*)",
    "Bash(uv:*)",
    "Bash(pip:*)",
    "Bash(node:*)",
    "Bash(npm:*)",
    "Bash(npx:*)",
    "Bash(go:*)",
    "Bash(ruff:*)",
    # Changing files, which is the job.
    "Bash(mkdir:*)",
    "Bash(cp:*)",
    "Bash(mv:*)",
    "Bash(rm:*)",
    "Bash(touch:*)",
    "Bash(chmod:*)",
    "Bash(ln:*)",
    "Bash(tee:*)",
    # The board.
    "Bash(gh:*)",
    # Watching its own work.
    "Bash(ps:*)",
    "Bash(pgrep:*)",
    "Bash(sleep:*)",
    "Bash(tmux:*)",
    # The local tier. `agent: goose` is 0.6.0's decided default, so a
    # Manager on that tier reaches for these the way it reaches for `git`.
    "Bash(goose:*)",
    "Bash(ollama:*)",
    "Bash(opencode:*)",
    # Verification a Worker was observed running.
    "Bash(gitleaks:*)",
    "Bash(sqlite3:*)",
    "Bash(df:*)",
)
"""Every entry here led at least one recorded Bash invocation."""

GENEROUS_ALLOW: tuple[str, ...] = (
    "Bash(basename:*)",
    "Bash(dirname:*)",
    "Bash(uvx:*)",
    "Bash(pip3:*)",
    "Bash(make:*)",
    "Bash(cargo:*)",
    "Bash(mypy:*)",
    "Bash(black:*)",
    "Bash(xcodebuild:*)",
    "Bash(xcrun:*)",
)
"""⚠ **NOT observed — added deliberately, and separated so nobody later
mistakes them for evidence.**

Robert's condition is "everything a worker needs under normal
circumstances", and these are the ordinary build and test entry points of
projects rite has simply not been pointed at yet. A Worker in a Rust or
Make-driven repository that cannot run its own build stalls, and a stall is
the failure mode this whole module exists to avoid. The cost of each wrong
guess here is one more allowed command; the cost of omission is a hung run.
"""

DEFAULT_ALLOW: tuple[str, ...] = TOOL_ALLOW + OBSERVED_ALLOW + GENEROUS_ALLOW

NOT_ALLOWED: dict[str, str] = {
    "bash": "a shell wrapper defeats the list — `bash -c` is one hop around every row",
    "sh": "as `bash`",
    "zsh": "as `bash`",
    "security": "keychain access; this is how a credential leaves the machine",
    "launchctl": "system daemons, and past this project's blast radius",
    "kill": "a Manager that may kill processes may kill its own supervisor",
    "pkill": "as `kill`, and by pattern rather than by pid",
    "nohup": "detaches work from the cycle the supervisor is bounding",
    "curl": "arbitrary network egress; the observed GitHub need is served by `gh`",
    "wget": "as `curl`",
    "claude": "⚠ the documented route-around: told nothing about starting "
    "Workers, a Manager improvised a bare `claude` and they died on "
    "launch. `rite sandbox start` is the supported route",
    "sudo": "privilege escalation",
    "ssh": "reaches another machine",
    "docker": "reaches outside the project and can mount the rest of the disk",
    "brew": "installs software system-wide; unattended package installation "
    "is a surprise rather than normal Worker work",
    "log": "macOS unified logging — a developer diagnostic, never agent work",
    "sample": "as `log`",
    "uptime": "as `log`",
    "mount": "as `log`, and changes the filesystem the project sits on",
    "md5": "as `log`; `shasum` covers the same need and was not needed either",
    "install": "a coreutils/BSD file installer that was noise in the corpus, "
    "not a thing any run needed",
}
"""⚠ **Observed and refused anyway, each with its reason.**

This exists so the refusal is a DECISION rather than an omission.
`tests/test_what_a_manager_may_do.py` requires every command in the recorded
corpus to be in `DEFAULT_ALLOW` or in here — so a future edit that narrows
the list has to say why, in this dict, rather than silently producing the
stall this module is built to avoid.
"""


def settings_path(root: Path) -> Path:
    """Where rite writes the settings document it passes to the engine."""
    return user_dir(root) / SETTINGS_FILENAME


def settings_document(allow: tuple[str, ...] = DEFAULT_ALLOW) -> dict:
    """Claude Code's own settings shape, holding only permissions.

    Nothing else goes in here. `--settings` loads ADDITIONAL settings on top
    of the user's own, so every key rite writes is a key it takes away from
    them.
    """
    return {"permissions": {"allow": list(allow)}}


def write_settings(root: Path, allow: tuple[str, ...] = DEFAULT_ALLOW) -> Path:
    """Write the settings file, overwriting whatever was there.

    ⚠ **Overwriting is the point.** A write-once-if-absent file would leave
    every existing project pinned to the list that shipped the day it was
    created, so widening the default in a later release would reach new
    users only — code and behaviour drifting apart silently, which is the
    defect `rite doctor` spends its life on. A user's own changes belong in
    `.claude/settings.json`, which this never touches.
    """
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written whole rather than merged: a partial write that still parses is
    # a settings file the engine will SILENTLY IGNORE under `-p` (measured:
    # `claude --help` says files that fail validation are ignored with no
    # error in print mode), and an ignored allowlist denies everything.
    path.write_text(json.dumps(settings_document(allow), indent=2) + "\n")
    return path


def launch_arguments(path: Path) -> str:
    """What `launch_command` appends for permissions.

    ⚠ **`--permission-prompts none` is not decoration — it is the whole
    reason this is safe to default.** Under `-p` the default target is
    `host`, and with no host attached a prompt has nowhere to go. `none`
    makes anything that would prompt DENY immediately. A denial is an
    answer; a prompt with no answerer is the stall that defect class 15 is
    about, and it is what the v0.5.1 acceptance run actually hit.
    """
    return f"--settings {shlex.quote(str(path))} --permission-prompts none"


def allowed(command: str, allow: tuple[str, ...] = DEFAULT_ALLOW) -> bool:
    """Whether rite's own list would permit `command`.

    ⚠ **This is rite's check for rite's message, NOT a reimplementation of
    the engine's matcher.** The engine decides what actually runs. This
    exists so that the coverage of `DEFAULT_ALLOW` can be tested against the
    recorded corpus, and so `refusal` can name the line to add. Where the
    two disagree the engine wins and this is wrong — which is why nothing
    downstream of a real launch consults it.
    """
    if command in _TOOL_NAMES:
        return command in allow
    head = _leading_executable(command)
    return bool(head) and f"Bash({head}:*)" in allow


_TOOL_NAMES = frozenset(
    {
        "Read",
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
        "Glob",
        "Grep",
        "TodoWrite",
        "Task",
        "WebFetch",
        "WebSearch",
        "Bash",
    }
)
"""The engine's own tool names, so a denial of one is not mistaken for a
shell command that happens to be called `Edit`."""


def _leading_executable(command: str) -> str:
    """The executable a shell would run first, or "" if there isn't one."""
    try:
        words = shlex.split(command)
    except ValueError:
        return ""
    for word in words:
        if "=" in word and not word.startswith(("/", ".", "-")):
            continue  # VAR=value prefixes
        return Path(word).name if word.startswith(("/", "./", "../")) else word
    return ""


def refusal(command: str, root: Path) -> str:
    """C21: what to tell a user when a command was not permitted.

    ⚠ **A refusal a user cannot act on is the same defect as a silent one.**
    So this says which command, and the exact line that would permit it —
    not "adjust your permissions".
    """
    # ⚠ A denial of one of the engine's OWN tools is not a shell command,
    # and telling a user to add `Bash(Edit:*)` would be advice that does
    # nothing. The first C20 run failed on exactly that tool.
    if command in _TOOL_NAMES:
        head = command
        line = f'"{command}"'
    else:
        head = _leading_executable(command) or command.strip()
        line = f'"Bash({head}:*)"'
    return (
        f"refused: {command.strip()!r} — {head!r} is not in the permission "
        f"allowlist rite passes to the engine.\n"
        f'To permit it, add this line to the "allow" list in '
        f"{Path('.claude') / 'settings.json'} in this project:\n"
        f"    {line}\n"
        f"rite's own list is at {settings_path(root)} and is rewritten every "
        f"run, so edit the project file rather than that one."
    )


def announcement(manager: str, allow: tuple[str, ...] = DEFAULT_ALLOW) -> str:
    """What rite prints about permissions, every run.

    ⚠ **Stated rather than assumed**, for the reason the timezone fallback
    is announced: a default nobody is told about is a silent decision. It
    also does work nothing else does — without it, a Manager that CHOSE not
    to act and one that was NOT ALLOWED to act produce the same visible
    result, which is the ambiguity that cost this project a night.

    ⚠ **It no longer says "will not ask before anything".** That sentence
    was true of `--dangerously-skip-permissions` and is false now; leaving
    it would be the doc-describes-reality defect C19 exists for.

    ⚠ **And it no longer says "unsandboxed", for the same reason.** Managers
    run inside a profile now (B9). What that boundary does NOT buy is said
    separately by `enclosure.limitations()` rather than crammed in here: a
    boundary sold as more than it is would be worse than none, and the
    honest version is too long for one line.
    """
    return (
        f"permissions: Manager {manager!r} may run {len(allow)} allowlisted "
        f"command families ({SETTINGS_FILENAME}); anything else is REFUSED "
        f"rather than queued for approval. It runs inside a sandbox — a "
        f"GUARD RAIL against mistakes, not containment. See the limitations "
        f"printed below."
    )
