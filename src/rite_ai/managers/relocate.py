"""Move every Manager's state out of the project (MM8, MM1), once, safely.

Before 0.7.0 a Manager's own directory was `.rite/managers/<name>/`, in the
tree. It is now `managers.manager_dir`, outside it. The first `rite start`
after an upgrade moves what an older rite left in the tree.

⚠ **TWO MOVES, ONE MIGRATION (MM1).** MM8 moved the per-Manager directory;
MM1 moves the per-Manager state that was never in it — the instance record,
the designation and the engine TMPDIR, which sat flat in `.rite/user/` where
every Manager could write them (`managers.user_dir`). They are done together,
under one set of run locks, because two migrations would mean two chances to
catch a Manager mid-run: the second taking a lock the first had just let go.
`flat_entries` derives both sides from the functions that build the paths,
never from a list written here — §5.4.6's warning is that such a list drifts,
and a missed entry here is a lost designation, which reads as a Manager
quietly starting a new conversation rather than as an error.

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

from rite_ai.managers import (
    DESIGNATION_FILENAME,
    INSTANCE_FILENAME,
    MANAGERS_DIRNAME,
    USER_DIRNAME,
    legacy_designation_path,
    legacy_instance_path,
    legacy_manager_dir,
    manager_dir,
)
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


def flat_entries(root: Path, name: str) -> list[tuple[Path, Path]]:
    """`(in the flat `.rite/`, where it goes)` for one Manager (MM1).

    ⚠ **DERIVED FROM THE FUNCTIONS THAT BUILD BOTH PATHS**, never spelled
    here: `managers.legacy_*` for the old side, `manager_dir` for the new.
    §5.4.6's warning is that a hand-written list of state paths drifts —
    `state.py` claimed "every `.rite` state file" and missed four writers —
    and a migration that misses one silently leaves a Manager reading an
    empty designation, which is a lost conversation rather than an error.

    The engine TMPDIR is here for completeness rather than for its contents:
    it is scratch, and moving it matters only so the flat path is GONE. An
    entry whose source does not exist is dropped by `_flat_present`.
    """
    from rite_ai.managers.enclosure import (
        ENGINE_TMP_DIRNAME,
        _legacy_engine_tmp,  # noqa: PLC2701
    )

    target = manager_dir(root, name)
    return [
        (legacy_instance_path(root, name), target / INSTANCE_FILENAME),
        (legacy_designation_path(root, name), target / DESIGNATION_FILENAME),
        (_legacy_engine_tmp(root, name), target / ENGINE_TMP_DIRNAME),
    ]


def _flat_present(root: Path, name: str) -> list[tuple[Path, Path]]:
    return [(a, b) for a, b in flat_entries(root, name) if a.exists()]


def _flat_names(root: Path) -> list[str]:
    """Managers with something of theirs still flat in `.rite/user/`.

    Read from the DIRECTORY rather than from the project's configured
    Managers: a Manager removed from `config.yaml` still has state on disk,
    and leaving it behind is what makes "the flat paths are gone" false.
    """
    directory = root / ".rite" / USER_DIRNAME
    if not directory.is_dir():
        return []
    found: set[str] = set()
    for entry in sorted(directory.iterdir()):
        for candidate in _names_in(entry):
            if name_problem(candidate, kind="manager name", must_be_a_tmux_target=True):
                continue
            if _flat_present(Path(root), candidate):
                found.add(candidate)
    return sorted(found)


def _names_in(entry: Path) -> list[str]:
    """Which Manager names an entry under `.rite/user/` could belong to.

    One entry can suggest one name (`planner.json` → `planner`,
    `planner.designated.json` → `planner`) or, for the engine TMPDIR, one
    per child. Every candidate is checked back against `_flat_present`, so a
    wrong guess here costs nothing — it is a way to find names to test, not
    a parser.
    """
    from rite_ai.managers.enclosure import ENGINE_TMP_DIRNAME

    if entry.name == ENGINE_TMP_DIRNAME and entry.is_dir():
        return [child.name for child in sorted(entry.iterdir())]
    for suffix in (".designated.json", ".json"):
        if entry.name.endswith(suffix):
            return [entry.name[: -len(suffix)]]
    return []


def _dir_names(root: Path) -> list[str]:
    """Managers with a pre-MM8 directory still in the tree, in name order."""
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


def _pending(root: Path) -> list[str]:
    """Managers with anything of theirs still in the tree, in name order.

    ⚠ **Both moves, under ONE refusal.** MM8 moved `.rite/managers/<name>/`
    out; MM1 moves the flat `.rite/user/` state that was never in it. They
    are one migration here rather than two because they take the same run
    lock, and two migrations would mean two chances to catch a Manager
    mid-run — the second one holding a lock the first had just released.
    """
    return sorted(set(_dir_names(root)) | set(_flat_names(root)))


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
    clash: list[tuple[Path, Path]] = []
    if source.is_dir():
        clash += [
            (child, target / child.name)
            for child in sorted(source.iterdir())
            if child.name not in _LEFT_TO_OTHERS
            and (target / child.name).exists()
            and not _same(child, target / child.name)
        ]
    # The flat state MM1 moves, by the same rule: an interrupted move can
    # leave both copies, identical ones finish the move, different ones
    # refuse rather than choosing which is the real designation.
    clash += [
        (a, b) for a, b in _flat_present(root, name) if b.exists() and not _same(a, b)
    ]
    return clash


def _move(root: Path, name: str) -> None:
    source, target = legacy_manager_dir(root, name), manager_dir(root, name)
    target.mkdir(parents=True, exist_ok=True)
    _move_dir(source, target)
    for origin, destination in _flat_present(root, name):
        _move_one(origin, destination)
        _prune_empty_parent(root, origin)


def _prune_empty_parent(root: Path, moved: Path) -> None:
    """Remove the flat container a moved entry leaves behind, if it is empty.

    ⚠ **Found by the test, not by reading the code.** The engine TMPDIR was
    `.rite/user/enginetmp/<name>/`, so moving every Manager's leaves
    `.rite/user/enginetmp/` standing — and "the flat paths are gone" is MM1's
    done-when, not "the flat files are gone". An empty directory left there
    is also a name a Manager can still be refused for, through
    `_rite_owned_user_entries`.

    Only ever the entry's own parent, only when it is empty, and never
    `.rite/user/` itself: that one is rite's own and holds `settings.json`.
    `rmdir` rather than `rmtree`, so a parent that turns out not to be empty
    raises here instead of taking something with it.
    """
    parent = moved.parent
    if parent == root / ".rite" / USER_DIRNAME:
        return
    if not parent.is_dir() or parent.is_symlink():
        return
    if any(parent.iterdir()):
        return
    parent.rmdir()


def _move_dir(source: Path, target: Path) -> None:
    """MM8's half: everything an older rite left in `.rite/managers/<name>/`."""
    if not source.is_dir():
        return
    for child in sorted(source.iterdir()):
        if child.name in _LEFT_TO_OTHERS:
            continue
        _move_one(child, target / child.name)
    (source / MOVED_NOTE).write_text(
        f"rite 0.7.0 moved this Manager's state out of the project, to\n"
        f"{target}\n(MM8). Nothing here is read any more.\n"
    )


def _move_one(child: Path, destination: Path) -> None:
    """One entry, copied to a staging name beside its DESTINATION, renamed
    into place, and only then removed from the tree.

    An interruption leaves either the source intact or both copies, never
    neither. `os.replace` is atomic within one filesystem, which is why the
    staging name is beside the destination rather than in a temp directory.

    ⚠ The staging name is derived from the DESTINATION's name, not the
    source's. MM1 moves `<name>.json` to `instance.json`, so two entries of
    one Manager staged under their source names could collide; under their
    destination names they cannot, because the destinations are distinct.
    """
    if not destination.exists():
        staging = destination.parent / f".moving-{destination.name}"
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)
        elif staging.exists() or staging.is_symlink():
            staging.unlink()
        if child.is_dir() and not child.is_symlink():
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
        "changes for you; `.rite/managers/` and the per-Manager files in "
        "`.rite/user/` now hold only what older rites left, and are no "
        "longer read"
    ]
