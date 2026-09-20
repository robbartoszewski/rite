"""What a `-p` Manager may DO, composed by rite on every cycle.

⚠ **Nothing carries a permission mode into `-p`.** Resuming from a terminal
restores the mode a session was in, and that restoration explicitly
EXCLUDES `-p` (along with the session picker and `/resume`). So there is no
path where a User grants permission once, interactively, and the unattended
cycles inherit it — which makes the mode a parameter rite owns, exactly as
`--resume <id>` became one: a value composed at the launch site and passed
on every invocation, not a thing that happens to be right.

Measured on a bare `claude -p`, no rite involved, before this existed:

    --permission-mode acceptEdits   exit 0, file created, contents exact
    (no mode)                       exit 0, NO file, "I don't have
                                    permission to write that file"

The second is why a cycle launched without the flag would be a Manager that
silently stops being able to work **and still exits 0** — read by `ending`
as a clean finish, resumed, and reported as a successful run that did
nothing. That is the shape this release spent itself removing.

⚠ **`acceptEdits` permits more than its name says, and that is the role
working rather than a surprise.** Measured under it: a shell command really
executed — `date +%s > stamp.txt` produced a value inside the real time
window, which a model writing the file from memory could not. A Manager
that cannot run `git`, the tests or a build is not a Manager, so shell
execution is a requirement of the role. Written down because the name
invites the opposite conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from rite_ai.managers import user_dir

ACCEPT_EDITS = "acceptEdits"
SKIP_ALL = "dangerously-skip-permissions"
DEFAULT_MODE = ACCEPT_EDITS

MODE_KEY = "permission_mode"

_ARGUMENTS = {
    # A mode VALUE for the one that has one...
    ACCEPT_EDITS: f"--permission-mode {ACCEPT_EDITS}",
    # ...and its own flag for the one that is not a mode value. Passing
    # `--permission-mode dangerously-skip-permissions` would be a different
    # and wrong thing, which is why this mapping exists rather than an
    # f-string at the call site.
    SKIP_ALL: "--dangerously-skip-permissions",
}


@dataclass(frozen=True)
class Permission:
    """The mode this Manager runs under, and where it came from."""

    mode: str
    explicit: bool
    announcement: str
    problem: str = ""


def mode_path(root: Path, manager: str) -> Path:
    """The per-Manager opt-in file.

    ⚠ **Beside `<name>.json`, deliberately NOT inside it.** That file is
    the instance record and `record_instance` rewrites it wholesale on
    every start — a user's authored choice would be destroyed by the next
    `rite start`. This one rite only ever reads.

    Under `.rite/user/`, which is gitignored by default (it is not in
    `AUTHORED_CONFIG`) and per-machine. A committed
    `--dangerously-skip-permissions` would impose one operator's risk
    appetite on every clone, and a Manager is bounded by nothing but the
    user's own filesystem permissions.
    """
    return user_dir(root) / f"{manager}.yaml"


def permission_mode(root: Path, manager: str) -> Permission:
    """What this Manager may do, from its own file or the safe default.

    ⚠ **An unrecognised value REFUSES rather than falling back.** Somebody
    who wrote in this file made a decision about what their Manager may do
    to their machine; running under a different mode because their spelling
    was wrong is the worst of both — they believe one thing is true and
    another is.
    """
    path = mode_path(root, manager)
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError:
        return Permission(DEFAULT_MODE, False, _said(DEFAULT_MODE, None))
    except yaml.YAMLError as e:
        return Permission(
            DEFAULT_MODE, False, "", f"{path}: {MODE_KEY} could not be read: {e}"
        )
    if raw in (None, {}, ""):
        return Permission(DEFAULT_MODE, False, _said(DEFAULT_MODE, None))
    if not isinstance(raw, dict) or MODE_KEY not in raw:
        return Permission(DEFAULT_MODE, False, _said(DEFAULT_MODE, None))

    stated = raw[MODE_KEY]
    if stated in _ARGUMENTS:
        return Permission(stated, True, _said(stated, path))
    return Permission(
        DEFAULT_MODE,
        False,
        "",
        f"{path}: {MODE_KEY} is {stated!r}, which is not one rite knows — "
        f"use {ACCEPT_EDITS!r} (the default, and what it does without this "
        f"file) or {SKIP_ALL!r}. Refusing rather than choosing one for you: "
        f"this setting is what your Manager may do to this machine.",
    )


def permission_argument(mode: str) -> str:
    """The engine flag for a mode. Empty for one rite does not know."""
    return _ARGUMENTS.get(mode, "")


def _said(mode: str, path: Path | None) -> str:
    """What rite prints about the mode, every run.

    ⚠ **Stated rather than assumed**, for the reason the timezone fallback
    is announced: a default nobody is told about is a silent decision. And
    it does work nothing else does — without it, a Manager that CHOSE not
    to act and one that was NOT ALLOWED to act produce the same visible
    result, which is the ambiguity that cost this project a night.
    """
    if mode == SKIP_ALL:
        return (
            f"permissions: {SKIP_ALL} — ALL permission checks bypassed, from "
            f"{path}. A Manager runs unsandboxed in this project's root, so "
            f"this is a choice about this machine."
        )
    if path is None:
        return (
            f"permissions: --permission-mode {mode} (rite's default). It may "
            f"edit files and run commands in this project."
        )
    return f"permissions: --permission-mode {mode}, from {path}."
