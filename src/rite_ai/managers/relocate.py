"""Move every Manager's directory out of the project (MM8), once, safely.

Before 0.7.0 a Manager's own directory was `.rite/managers/<name>/`, in the
tree. It is now `managers.manager_dir`, outside it. The first `rite start`
after an upgrade moves what an older rite left in the tree.

**Every Manager's, not only the one starting.** The Owner's supervisor reads a
secondary's `routes/` from the secondary's directory, so moving one Manager's
state and not another's would leave the Owner looking in the new place while
the secondary still writes the old one. So a start that finds in-tree state
moves all of it.

**UNDER EVERY MANAGER'S RUN LOCK, and that is what makes it safe.** The run
lock is the `flock` each `rite start` holds for its whole run
(`github_access.hold_run`). v0.6.0 takes the same one, on the same file, so a
Manager started by the release being upgraded from holds it too. That was
checked against the v0.6.0 tag, not assumed. The starting Manager already
holds its own. For each other Manager with state to move, this takes its lock
and HOLDS it while moving. If the lock is taken, that Manager is running,
state is not moved out from under it, and the start is refused, naming the
Manager and what to do. This is an exact check, not a guess from a pid.

**Nothing is lost if it is interrupted.** Each entry is copied under a
temporary name beside its destination, renamed into place (atomic on one
filesystem), and only then removed from the tree. An interrupted move leaves
either the source intact, or both copies. On the next start, identical copies
finish the move, and different ones refuse, naming both, rather than choosing.
The legacy `mail/` is `mailbox.adopt_legacy`'s, and is left to it.
"""

from __future__ import annotations

import filecmp
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.managers import MANAGERS_DIRNAME, legacy_manager_dir, manager_dir
from rite_ai.names import name_problem

MOVED_NOTE = "MOVED.txt"
_LEFT_TO_OTHERS = frozenset({"mail", MOVED_NOTE})
"""`mail/` is `mailbox.adopt_legacy`'s; the note is this module's own."""


@dataclass
class Relocation:
    moved: list[str] = field(default_factory=list)
    """Managers whose directory was moved out of the tree by this start."""
    refused: str = ""
    """Why nothing was moved, when something stopped it."""


def _pending(root: Path) -> list[str]:
    """Managers with something of theirs still in the tree, in name order."""
    base = root / ".rite" / MANAGERS_DIRNAME
    if not base.is_dir():
        return []
    found: list[str] = []
    for entry in sorted(base.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if name_problem(entry.name, kind="manager name", must_be_a_tmux_target=True):
            continue
        if any(child.name not in _LEFT_TO_OTHERS for child in entry.iterdir()):
            found.append(entry.name)
    return found


def _same(a: Path, b: Path) -> bool:
    if a.is_dir() and b.is_dir():
        cmp = filecmp.dircmp(a, b)
        if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
            return False
        return all(_same(a / d, b / d) for d in cmp.common_dirs)
    if a.is_file() and b.is_file():
        return filecmp.cmp(a, b, shallow=False)
    return False


def _conflicts(root: Path, name: str) -> list[tuple[Path, Path]]:
    """Entries present in the tree AND outside it with different content."""
    source, target = legacy_manager_dir(root, name), manager_dir(root, name)
    return [
        (child, target / child.name)
        for child in sorted(source.iterdir())
        if child.name not in _LEFT_TO_OTHERS
        and (target / child.name).exists()
        and not _same(child, target / child.name)
    ]


def _move(root: Path, name: str) -> None:
    source, target = legacy_manager_dir(root, name), manager_dir(root, name)
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if child.name in _LEFT_TO_OTHERS:
            continue
        destination = target / child.name
        if not destination.exists():
            staging = target / f".moving-{child.name}"
            if staging.is_dir() and not staging.is_symlink():
                shutil.rmtree(staging)
            elif staging.exists() or staging.is_symlink():
                staging.unlink()
            if child.is_dir():
                shutil.copytree(child, staging, symlinks=True)
            else:
                shutil.copy2(child, staging, follow_symlinks=False)
            os.replace(staging, destination)
        # Here the destination holds the same content (just copied, or found
        # identical by `_conflicts`), so the tree's copy can go.
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    (source / MOVED_NOTE).write_text(
        f"rite 0.7.0 moved this Manager's state out of the project, to\n"
        f"{target}\n(MM8). Nothing here is read any more.\n"
    )


def _refusal(name: str, starting: str) -> str:
    return (
        f"this start moves every Manager's state out of the project, once "
        f"(rite 0.7.0), and Manager {name!r} is running: its `rite start` "
        f"holds its run lock, so its state cannot be moved under it. Nothing "
        f"was moved. Stop it (`rite manager stop {name}`), then run `rite "
        f"start {starting}` again; {name!r}'s state moves too, and {name!r} "
        "can be started again afterwards"
    )


def move_out(root: Path, starting: str, *, hold=None) -> Relocation:
    """Move every Manager's in-tree state out, or refuse and say why.

    The caller must already hold `starting`'s run lock. `hold` takes another
    Manager's run lock, returning a descriptor or None when it is held
    elsewhere; it is `github_access.hold_run` unless a test substitutes it.
    """
    pending = _pending(root)
    if not pending:
        return Relocation()
    if hold is None:
        from rite_ai.managers.github_access import hold_run as hold

    held: list[int] = []
    try:
        for name in pending:
            if name == starting:
                continue
            fd = hold(root, name)
            if fd is None:
                return Relocation(refused=_refusal(name, starting))
            held.append(fd)
        for name in pending:
            clash = _conflicts(root, name)
            if clash:
                inside, outside = clash[0]
                return Relocation(
                    refused=(
                        f"Manager {name!r} has {inside.name!r} both in the "
                        f"project ({inside}) and outside it ({outside}), with "
                        "different content, which happens when an older rite "
                        "wrote into the project after a move. Nothing was "
                        "moved. Keep the copy you want, remove the other, and "
                        "start again"
                    )
                )
        for name in pending:
            _move(root, name)
        return Relocation(moved=pending)
    finally:
        for fd in held:
            os.close(fd)


def notes(relocation: Relocation) -> list[str]:
    """What `rite start` says about it: one line when anything moved."""
    if not relocation.moved:
        return []
    names = ", ".join(repr(n) for n in relocation.moved)
    return [
        f"moved the state of Manager(s) {names} out of the project, beside "
        "their mail in rite's data directory (rite 0.7.0). Nothing else "
        "changes for you; `.rite/managers/` now holds only what older rites "
        "left, and is no longer read"
    ]
