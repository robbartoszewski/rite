"""A name a user types is not a path segment until something checks it.

WHY THIS EXISTS. `rite remove worker ..` deleted the entire project. The
worker name went straight into `root / "workers" / name`, `workers/..` is the
project root, `.is_dir()` said yes, and `shutil.rmtree` walked it. Measured on
a scratch project: `.rite/`, `src/`, and a file called `IMPORTANT.md` all
gone, followed by a `FileNotFoundError` traceback — which arrives AFTER the
repository is already empty, so the user's first signal that anything happened
is a stack trace about a directory that no longer exists.

The guard that should have stopped it could not. `unsaved_work` looks for
uncommitted work in the target's DIRECT CHILDREN that are git repositories; a
worker's module clones live there, but a project root's children are `.rite/`,
`docs/`, `src/`. So it found nothing at risk and reported honestly that there
was nothing to lose, about a directory containing everything.

THE FIX IS AT THE BOUNDARY, not in the guard. Making `unsaved_work` smarter
would leave the next caller that joins a user string onto a path exposed —
and there are several: `add_worker`, the heartbeat file, the context index.

⚠ AND A PATH IS NOT THE ONLY THING A NAME BECOMES. This module was written
for the path case and enforced only that, while a Manager name is also a
tmux TARGET and a shell ARGUMENT. `eu:west` is a perfectly good path
segment and a broken tmux target, and it produced a live paid session that
rite reported as failed to start, could not see, and could not stop. The
measurement is in `name_problem`.

So the rule is now an ALLOWLIST — letters, digits, `.`, `-`, `_` — rather
than a longer list of characters to reject. A blacklist is a list of the
failures somebody already thought of, and `:` was on nobody's. The
consumers are not fixed either: the next one added inherits the rule
without anyone remembering to widen it.

Deliberately strict rather than clever. No normalisation, no stripping, no
"did you mean" — those turn a refusal into a guess, and a guess about which
directory to delete is not a guess worth making.
"""

from __future__ import annotations

import os

# `..` and `.` are the traversal cases; a separator means the caller meant a
# path and not a name; NUL and control characters are rejected because a name
# that cannot be printed cannot be reviewed by the person approving it.
_SEPARATORS = {"/", os.sep, os.altsep} - {None, ""}


class UnsafeName(ValueError):
    """A user-supplied name that must not be joined onto a path."""


def name_problem(
    name: str, *, kind: str = "name", must_be_a_tmux_target: bool = False
) -> str:
    """Why `name` must not become a path segment, or "" if it may.

    Returns a sentence rather than a bool: every caller here reports to a
    human, and "invalid name" tells them nothing about which rule they broke
    or what to type instead.
    """
    if not name or not name.strip():
        return f"a {kind} cannot be empty"
    if name in (".", ".."):
        return (
            f"{name!r} is not a {kind}, it is a directory reference — "
            f"`{name}` would resolve outside the intended directory"
        )
    for sep in _SEPARATORS:
        if sep in name:
            return (
                f"a {kind} is one path segment and {name!r} contains {sep!r} — "
                "pass the name alone, not a path"
            )
    if name.startswith("-"):
        # Not traversal: a leading dash becomes an option to any command that
        # later passes this positionally.
        return f"a {kind} cannot start with '-' ({name!r})"
    if any(ch == "\0" or ord(ch) < 32 for ch in name):
        return f"a {kind} cannot contain control characters"
    # ⚠ AN ALLOWLIST, AND EVERYTHING ABOVE IS ONLY FOR BETTER MESSAGES.
    #
    # This function was written to stop a name becoming a path it should not
    # be, and it did that correctly — while a Manager name is ALSO a tmux
    # target, and nothing enforced that. Measured, with `eu:west`:
    #
    #     name_problem('eu:west') -> ''           accepted
    #     start().ok              -> False        "exited immediately"
    #     tmux actually has       -> rite-mgr-...-eu:west   (running)
    #     liveness()              -> alive=False, known=True
    #     stop()                  -> ok=True, "no session named ..."
    #
    # `:` is tmux's window separator, so the session is created under a name
    # no later `-t` lookup resolves. In production that pane runs `claude`:
    # rite reports a failure while a real paid session runs detached with no
    # record, and the recovery command reports success having done nothing.
    #
    # ⚠ It is an allowlist rather than a longer list of bad characters
    # because `:` would not have been on that list either. A blacklist is a
    # list of the failures somebody already thought of; the first fix for a
    # related defect today enumerated invisible Unicode categories and
    # missed U+2800 on its first run. A name has several consumers — a path
    # segment, a tmux target, a shell argument — and the only rule that
    # holds for all of them, including the ones added later, is one that
    # says what a name MAY contain.
    #
    # `.` is permitted because context FILE names come through here too
    # (`database.md`), and `..` is already refused above.
    bad = sorted(
        {ch for ch in name if not (ch.isascii() and (ch.isalnum() or ch in "._-"))}
    )
    if must_be_a_tmux_target and "." in name:
        # ⚠ THE ALLOWLIST ALONE WAS THE WRONG SHAPE, and this is the
        # correction to it. `.` is admitted above because context FILE
        # names come through this function (`database.md`) — and `.` is
        # tmux's PANE separator, so a Manager named `v2.0` creates a
        # session nothing can address. Measured:
        #
        #     tmux new-session -d -s rvw-dot.name  -> created
        #     tmux has-session -t =rvw-dot.name    -> can't find pane: name
        #     tmux kill-session -t =rvw-dot.name   -> can't find pane: name
        #
        # Reachable only by `#{session_id}` — the same unstoppable-orphan
        # shape as the `:` defect this allowlist was written for, through a
        # character that fix deliberately allowed.
        #
        # So the allowed set depends on what the name BECOMES. One rule for
        # every consumer was the mistake: a context file is never a tmux
        # target, a Manager always is.
        return (
            f"a {kind} cannot contain '.' ({name!r}) — it becomes a tmux "
            "session name, and tmux reads '.' as the pane separator, so the "
            "session would be created and then be unreachable by name"
        )
    if bad:
        shown = " ".join(repr(ch) for ch in bad)
        return (
            f"a {kind} may contain letters, digits, '.', '-' and '_' — "
            f"{name!r} also contains {shown}. A name becomes a directory, a "
            "tmux session target and a shell argument, and characters "
            "outside that set break at least one of them silently"
        )
    return ""


def require_safe_name(
    name: str, *, kind: str = "name", must_be_a_tmux_target: bool = False
) -> None:
    """Raise `UnsafeName` unless `name` is safe to join onto a path.

    For call sites with no result type to carry a message. Callers that report
    rather than raise should use `name_problem` and put the sentence in the
    result the user reads.
    """
    problem = name_problem(name, kind=kind, must_be_a_tmux_target=must_be_a_tmux_target)
    if problem:
        raise UnsafeName(problem)
