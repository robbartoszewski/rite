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
A name is either one ordinary path segment or it is not a name.

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


def name_problem(name: str, *, kind: str = "name") -> str:
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
    return ""


def require_safe_name(name: str, *, kind: str = "name") -> None:
    """Raise `UnsafeName` unless `name` is safe to join onto a path.

    For call sites with no result type to carry a message. Callers that report
    rather than raise should use `name_problem` and put the sentence in the
    result the user reads.
    """
    problem = name_problem(name, kind=kind)
    if problem:
        raise UnsafeName(problem)
