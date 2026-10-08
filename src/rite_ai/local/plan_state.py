"""Where a plan lives, and why it is not in the project (SCRUM-72 §3.3b).

🔴 **The hole this closes, and it exists today.** `LocalStateLayer` kept every
decomposition in `.rite/state.json` — inside the project tree, which **every
Manager's profile grants writable**: seatbelt in `enclosure.compose`, and
Landlock through `_fenced_project_paths`, which cannot deny a path inside a
granted one. So any Manager could open that file and set a plan's `approval`
to APPROVED, without holding `plan-review`, without being independent of the
author, and without `approve_plan` ever running. Every rule RL-6, DD-3.5 and
RL-67 are made of was enforced on one door while a second stood open.

Any Manager could also run `rite local approve <anyone> <ticket>` from inside
its boundary, because `Bash(rite:*)` is allowlisted — approving in a peer's
name.

**The fix is a move, not a check.** The state goes beside the mailboxes and
the Worker-owner records (`mailbox._mail_home`'s parent), the MM8 pattern: a
Manager is granted its OWN directory there by exact path and reaches no other,
so the plan state is denied by the kernel. ⚠ **That matters because Landlock
has no deny.** A check inside rite would be a check inside a process a Manager
can reach; a path outside every grant is enforced by the kernel whether rite
asks it to be or not. The same move protects every other key the layer holds.

⚠ **And it fences `rite local approve` with no environment variable.** That
command run inside a boundary now fails because it cannot open the state at
all — not because rite looked at `RITE_MANAGER` and decided. An env var is
inherited by anything a session starts and is exactly what a compromised one
would set; a path the kernel denies is not negotiable.

**Keyed by checkout path, not by the committed namespace**
(`mailbox._checkout_key`'s reason): rite itself has ~19 worktrees sharing one
committed namespace, and two checkouts' `T-1` sharing one plan would have each
taking the other's.

**Nothing is migrated, deliberately.** A `.rite/state.json` from before this
is content a Manager could have written, so adopting it would import on day
one exactly the forgery the move exists to prevent. It is left where it is,
untouched, and `stranded` says so once — a plan silently lost is worse than a
plan reported stranded.
"""

from __future__ import annotations

from pathlib import Path

DIRNAME = "plan-state"


def home(root: Path) -> Path:
    """Where this checkout's plan state lives: outside every Manager's grant."""
    from rite_ai.managers.mailbox import _checkout_key, _mail_home  # noqa: PLC2701

    return _mail_home().parent / DIRNAME / _checkout_key(Path(root))


def layer(root: Path):
    """The state layer every local-tier consumer binds to.

    One spelling, because seven call sites each saying
    `LocalStateLayer(root / ".rite")` is how six of them would come to be
    moved and one would not.
    """
    from rite_ai.coordination.local_backend import LocalStateLayer

    where = home(Path(root))
    where.mkdir(parents=True, exist_ok=True)
    return LocalStateLayer(where)


def legacy_path(root: Path) -> Path:
    """Where the plan state used to live, inside the project tree."""
    return Path(root) / ".rite" / "state.json"


def stranded(root: Path) -> str:
    """A notice about a pre-SCRUM-72 plan state left in the project, or "".

    Said rather than migrated: see the module docstring. Only while the new
    home holds nothing — once this checkout has written a plan outside the
    project, the old file is simply history.
    """
    old = legacy_path(root)
    if not old.is_file():
        return ""
    new = home(Path(root)) / "state.json"
    if new.is_file():
        return ""
    return (
        f"{old} holds plan state written before SCRUM-72, and rite does not "
        "read it any more: the plan state moved outside the project tree, "
        "because every Manager's profile grants the project tree writable and "
        "a Manager could set a plan's own approval there. It is NOT migrated — "
        "that would import the forgery the move prevents. Any decomposition "
        "in flight has to be authored again; the file is left for you to read"
    )
