"""Running git on the HOST, in a repository a Manager can write, without
executing anything that repository's own configuration names (SCRUM-75
follow-up; the SCRUM-59 final review measured the hole).

rite runs git on the host in the project and in each module's checkout — a
`git status` every cycle (`progress`), the publish gate, a delivery. Those
trees are writable by a Manager's sandbox, and git executes programs named
in a repository's own `.git/config`: a `core.fsmonitor` command runs on
`git status`, a hook on many commands. So every host-side git call rite makes
is given an environment that forces those two off, OVERRIDING whatever the
repository's config says.

**Through the environment, not a `-c` flag.** `tests/test_blast_radius.py`
enumerates every literal `["git", ...]` list in the package to prove no verb
writes history or a remote; a wrapper that prepended `git -c …` would hide
those verbs. `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` inject
config at the same precedence as `-c` without touching the argument lists, so
the audit still sees plain `["git", ...]`.

**Additive.** It appends to any `GIT_CONFIG_*` already in the base
environment (the GitHub credential helper, a signing setting), and does NOT
null the operator's global config — a host-side delivery still commits under
the operator's name (`git_settings`, and the "git without config still uses
the operator's name" lesson).

⚠ **What this does NOT close: a per-file diff `textconv` program.** `git log
-p` runs a repository-defined `diff.<driver>.textconv`, and a driver cannot
be disabled wholesale through injected config (only `--no-textconv` on the
command does it, which gitleaks' internal `git log` does not take). The gate's
history scan therefore runs against a copy with no config (`gate`, the
`cp-of-a-worktree` rule), not through this.
"""

from __future__ import annotations

import os

# core.fsmonitor: a repo-set value runs a program on `git status` and other
# commands. false disables it. core.hooksPath at /dev/null means git finds no
# hook to run (the form `remote_probe` and `git_backend` already use).
_HARDENING = (
    ("core.fsmonitor", "false"),
    ("core.hooksPath", os.devnull),
    # A fsmonitor set as a hook rather than a command is gated by this too.
    ("core.useBuiltinFSMonitor", "false"),
)


def hardened_git_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """`base` (or the current environment) with fsmonitor and hooks forced
    off for any git child, appended after any `GIT_CONFIG_*` already there."""
    env = dict(os.environ if base is None else base)
    try:
        start = int(env.get("GIT_CONFIG_COUNT", "") or 0)
    except ValueError:
        start = 0
    for i, (key, value) in enumerate(_HARDENING):
        env[f"GIT_CONFIG_KEY_{start + i}"] = key
        env[f"GIT_CONFIG_VALUE_{start + i}"] = value
    env["GIT_CONFIG_COUNT"] = str(start + len(_HARDENING))
    return env
