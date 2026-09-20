"""Running Managers: where their state lives, and who is running one.

**Profiles are committed; instances are not.** A Manager's PROFILE — its
engine, duties, model — is `manager_roles:` in `config.yaml`, because a team
agrees on what a `planner` is. A Manager's INSTANCE — that this machine's
user is running one, its pid, its session — lives under `.rite/user/` and is
never committed, because a shared config claiming a Manager is running on
somebody else's laptop is worse than saying nothing (SPEC §9.14.9 item 4).

**Each Manager also gets its own directory**, `.rite/managers/<name>/`, which
is what makes §5.4's containment property statable at all: before this there
was no per-Manager path and no per-process identity to key one on, so
"nothing outside the acting Manager's own directory" named a thing that did
not exist (D-77). The identity arrives as the argument — a Manager started
as `rite start planner` knows it is `planner`.

⚠ **This is the shared-root model, and its cost is known rather than
avoided.** `docs/design/V060_MULTI_MANAGER.md` argued for separate roots on
the grounds that a shared root is "a second protocol that has to be kept in
agreement with the first, and the two would drift". That is true and it is
accepted debt (D-79): what reversed the decision is that separate roots mean
separate claim ledgers, and `claims_channel()` is inert unless both
`coordination.managers` and `coordination.remote` are set — so two Managers
on one machine would silently not see each other's claims.

**Only NEW state goes here.** Existing `.rite/` files stay where they are
until 0.6.0. Relocating live state in a patch release breaks a project that
upgrades mid-run, and the boundary is worth having before the move.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from rite_ai.names import name_problem
from rite_ai.state import write_atomic

USER_DIRNAME = "user"
MANAGERS_DIRNAME = "managers"


def user_dir(root: Path) -> Path:
    """Per-user, per-machine runtime state. Never committed."""
    return root / ".rite" / USER_DIRNAME


def manager_dir(root: Path, name: str) -> Path:
    """One Manager's own directory — §5.4's boundary, now a real path.

    Validated rather than joined: a name reaching a path unchecked is the
    defect `rite_ai.names` exists for, and this is a new join.
    """
    problem = name_problem(name, kind="manager name", must_be_a_tmux_target=True)
    if problem:
        raise ValueError(problem)
    return root / ".rite" / MANAGERS_DIRNAME / name


def instance_path(root: Path, name: str) -> Path:
    problem = name_problem(name, kind="manager name", must_be_a_tmux_target=True)
    if problem:
        raise ValueError(problem)
    return user_dir(root) / f"{name}.json"


@dataclass
class ManagerInstance:
    """One running Manager, as this machine sees it."""

    name: str
    session: str = ""
    pid: int = 0
    started_at: float = 0.0
    engine: str = ""
    max_sessions: int = 0
    """The session-start ceiling this invocation was given. A COUNT, not
    spend — §2.6.1 says rite cannot read the quota and D-38 forbids the path
    from measurement back to control (D-69)."""
    window_seconds: float = 0.0
    """The wall-clock bound. The ceiling above bounds starts; this is what
    bounds duration, and §9.14.5 records that a count alone bounds neither."""

    def __post_init__(self) -> None:
        if self.started_at == 0.0:
            self.started_at = time.time()


def record_instance(root: Path, instance: ManagerInstance) -> None:
    path = instance_path(root, instance.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    manager_dir(root, instance.name).mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(asdict(instance), indent=2) + "\n")


def read_instance(root: Path, name: str) -> ManagerInstance | None:
    """The recorded instance, or None. Returns rather than raises on a
    corrupt file: this is read on the refusal path, and a refusal that
    tracebacks is worse than one that refuses for a stated reason."""
    try:
        raw = json.loads(instance_path(root, name).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or "name" not in raw:
        return None
    known = {f: raw[f] for f in ManagerInstance.__dataclass_fields__ if f in raw}
    try:
        return ManagerInstance(**known)
    except (TypeError, ValueError):
        return None


def forget_instance(root: Path, name: str) -> None:
    try:
        instance_path(root, name).unlink()
    except OSError:
        pass


def running_instances(root: Path) -> list[ManagerInstance]:
    """Every recorded instance. Unreadable entries are skipped, not guessed
    at — a listing that invents a Manager is worse than a short listing."""
    out: list[ManagerInstance] = []
    directory = user_dir(root)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        instance = read_instance(root, path.stem)
        if instance is not None:
            out.append(instance)
    return out


def pid_alive(pid: int) -> bool:
    """Whether a process exists. Not whether it is OURS.

    ⚠ A pid is not an identity: the OS recycles them, so this answers "some
    process has this number". `loop/session.py` carries the same limit and
    the same note. The caller must treat a live pid as necessary and not
    sufficient, which is why the refusal path checks the session too.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError):
        # OverflowError, not OSError, is what an out-of-range pid raises —
        # a corrupt instance file must refuse to answer, never traceback.
        return False
    return True


@dataclass
class Chosen:
    """Which Manager to start, or why not."""

    role: object | None = None
    problem: str = ""

    @property
    def ok(self) -> bool:
        return self.problem == ""


def manager_to_start(roles: list, requested: str = "") -> Chosen:
    """Which Manager `rite start [<name>]` means (D-78).

    ⚠ Named for what it decides, not "choose_manager", because
    `coordination.assignment.choose_manager` already exists and decides
    something else — which Manager a TICKET is routed to. Two public
    functions with one name in one package is a reader's problem before it
    is a tool's, and the dead-wiring guard found it first: the collision
    made another module's exemption look stale.

    Zero configured FAILS rather than starting a default: inventing a
    configuration the user did not write is how a tool spends quota on a
    shape nobody chose.

    One works bare. Requiring a name to state the only possibility is
    ceremony, and the common project has one Manager.

    Two or more REFUSES AND LISTS THEM. Picking one would be a guess about
    which engine spends which quota, and a refusal that says "several are
    configured" without naming them sends the user to `rite doctor` to learn
    what they could have typed — the same rule §9.14.0's duplicate refusal
    follows.
    """
    names = [r.name for r in roles]
    if requested:
        for role in roles:
            if role.name == requested:
                return Chosen(role=role)
        if not names:
            return Chosen(
                problem=(
                    f"no Manager named '{requested}' — this project declares "
                    "none at all. Add one under `coordination.manager_roles` "
                    "in .rite/config.yaml."
                )
            )
        return Chosen(
            problem=(
                f"no Manager named '{requested}'. This project declares: "
                f"{', '.join(names)}."
            )
        )

    if not roles:
        return Chosen(
            problem=(
                "no Managers are configured, so there is nothing to start. "
                "Declare one under `coordination.manager_roles` in "
                ".rite/config.yaml — `rite start` will not invent a default, "
                "because a Manager spends quota and the shape should be one "
                "you wrote."
            )
        )
    if len(roles) == 1:
        return Chosen(role=roles[0])
    listed = ", ".join(names)
    return Chosen(
        problem=(
            f"{len(roles)} Managers are configured and `rite start` will not "
            f"guess which you meant: {listed}. Name one — `rite start "
            f"{names[0]}`."
        )
    )


def name_collisions(
    manager_names: Iterable[str], alias_names: Iterable[str]
) -> list[str]:
    """Names that are BOTH a declared Manager and a registered project alias.

    ⚠ **`rite start <word>` tries Managers FIRST**, so a collision is not a
    tie — the Manager wins and the alias becomes unreachable by name, with
    no message. The user typed a project and started a session.

    **This cannot be a runtime refusal, and that is the whole difficulty.**
    `manager_roles` is committed: everyone on the project has it. The alias
    registry is per machine: nobody else has it. So the collision exists on
    ONE person's laptop, and refusing `rite start` there would reject a
    config that is correct, shared, and unchangeable by them. A check that
    fires where the fault is not is a check people learn to route around.

    It is therefore reported at the two points where it is actionable:

    - `rite projects add`, which is where the machine-local name is being
      chosen right now and another one costs nothing — so it refuses;
    - `rite doctor`, for collisions that appeared afterwards because
      somebody committed a Manager role — so it reports, with both names,
      because the fix is the user's choice of which to rename.

    Sorted, so the same machine reports the same order twice running.
    """
    return sorted(set(manager_names) & set(alias_names))
