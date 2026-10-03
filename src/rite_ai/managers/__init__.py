"""Running Managers: where their state lives, and who is running one.

**Profiles are committed; instances are not.** A Manager's PROFILE — its
engine, duties, model — is `manager_roles:` in `config.yaml`, because a team
agrees on what a `planner` is. A Manager's INSTANCE — that this machine's
user is running one, its pid, its session — lives under `.rite/user/` and is
never committed, because a shared config claiming a Manager is running on
somebody else's laptop is worse than saying nothing (SPEC §9.14.9 item 4).

**Each Manager also gets its own directory**, which is what makes §5.4's
containment property statable at all: before it there was no per-Manager
path and no per-process identity to key one on, so "nothing outside the
acting Manager's own directory" named a thing that did not exist (D-77).

⚠ **SINCE 0.7.0 (MM8) IT IS OUTSIDE THE PROJECT**, beside the Manager's
mail in rite's data directory: `<data>/rite/mail/<checkout>/<name>/state/`
(`manager_dir`). It was `.rite/managers/<name>/`, in the tree. On Linux,
Landlock has no deny rule, so keeping one Manager out of another's
directory there meant enumerating the project root, and a Manager could not
create a new top-level file in its own project (readiness D17). Out of the
tree, each Manager's profile grants its own directory by path and no other,
with nothing to carve out of the project. An in-tree directory left by an
older rite is moved at the next `rite start` (`relocate`), under every
Manager's run lock.

⚠ **The identity is in the session's ENVIRONMENT, not in the prompt.** It
used to be carried only by the prompt text `rite start` types in — "You are
the Manager 'planner'" — which the supervisor sends on the FIRST session
only, deliberately (D-90). So every resumed session had no readable identity
at all: the name survived in the conversation a model could read back, and
in nothing a process could. `current_manager()` reads `RITE_MANAGER`, which
`session.start` sets on every session it creates — first and resumed alike,
because both go through the same `new-session`.

⚠ **This is the shared-root model, and its cost is known rather than
avoided.** `docs/design/V070_MULTI_MANAGER.md` argued for separate roots on
the grounds that a shared root is "a second protocol that has to be kept in
agreement with the first, and the two would drift". That is true and it is
accepted debt (D-79): what reversed the decision is that separate roots mean
separate claim ledgers, and `claims_channel()` is inert unless both
`coordination.managers` and `coordination.remote` are set — so two Managers
on one machine would silently not see each other's claims.

**Only NEW state goes here.** Existing `.rite/` files stay where they are
until 0.6.0 (no 0.6.0 ticket moved them; it is MM1, v0.7.0). Relocating
live state in a patch release breaks a project that upgrades mid-run, and
the boundary is worth having before the move.
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

MANAGER_ENV = "RITE_MANAGER"
"""The Manager a process is running as, set on the tmux session itself.

Named like `RITE_PROJECT_ROOT`, which `sandbox` already delivers into a
Worker for the same reason: a fact a process cannot derive from its own
surroundings is given to it rather than guessed at.
"""


def current_manager() -> str:
    """Which Manager this process is, or "" if it is not one.

    ⚠ **"" is a real answer, not a failure.** Every `rite` invocation from
    a human's own shell is not a Manager, and that is the common case — so a
    caller uses this to fill a DEFAULT, never to assert one.

    ⚠ **The name is validated before it is returned, by the SAME rule the
    rest of the Manager path uses.** An environment is inherited by anything
    a session starts, and this value reaches `manager_dir` as a path segment
    — which RAISES on a bad name. A default that tracebacks is worse than
    one that is not there, so a value that could not be a Manager's name
    reads as absent.

    ⚠ `must_be_a_tmux_target=True` is not optional here, and leaving it off
    is a live bug rather than a strictness preference: without it `.` is
    admitted (context FILE names come through `name_problem` too), so
    `RITE_MANAGER=v2.0` would be returned here and then raise in
    `manager_dir`. Every other Manager-name caller — `start`, `manager_dir`,
    `instance_path`, the journal's writer — passes it.
    """
    value = os.environ.get(MANAGER_ENV, "").strip()
    if not value or name_problem(
        value, kind="manager name", must_be_a_tmux_target=True
    ):
        return ""
    return value


def user_dir(root: Path) -> Path:
    """Per-user, per-machine runtime state. Never committed."""
    return root / ".rite" / USER_DIRNAME


STATE_DIRNAME = "state"


def _checked(name: str) -> None:
    problem = name_problem(name, kind="manager name", must_be_a_tmux_target=True)
    if problem:
        raise ValueError(problem)


def manager_dir(root: Path, name: str) -> Path:
    """One Manager's own directory — §5.4's boundary, now a real path.

    `<data>/rite/mail/<checkout>/<name>/state/`, beside the Manager's mail
    (`mailbox.mail_root`) and outside the project (MM8, module docstring).
    The ONE place it is spelled: everything a Manager keeps (its prompt,
    routes, Worker requests, check-ins, Slack relay state, journal) is under
    it, so moving it is moving this line.

    Validated rather than joined: a name reaching a path unchecked is the
    defect `rite_ai.names` exists for, and this is a new join.
    """
    _checked(name)
    from rite_ai.managers.mailbox import mail_root

    return mail_root(root, name).parent / STATE_DIRNAME


def legacy_manager_dir(root: Path, name: str) -> Path:
    """`.rite/managers/<name>/`, where a Manager's directory lived before
    MM8. Read only to move what is there (`relocate`), and to find the
    pre-0.6.0 mailbox `mailbox.adopt_legacy` moves."""
    _checked(name)
    return root / ".rite" / MANAGERS_DIRNAME / name


def _instance_filename(name: str) -> str:
    return f"{name}.json"


def instance_path(root: Path, name: str) -> Path:
    problem = manager_name_problem(name)
    if problem:
        raise ValueError(problem)
    return user_dir(root) / _instance_filename(name)


def _rite_owned_user_entries() -> tuple[str, ...]:
    """The entries rite itself keeps in `.rite/user/`, beside the ones keyed
    by a Manager's name.

    ⚠ **Every fixed-name file or directory rite writes into `user_dir` must
    be listed here** — `tests/test_a_manager_name_cannot_be_rites_own_state.py`
    writes all of them for a real Manager and fails on any entry that is
    neither this Manager's nor on this list. An unlisted entry is a name some
    Manager can collide with (C30: `permissions.json`)."""
    from rite_ai.managers.enclosure import ENGINE_TMP_DIRNAME
    from rite_ai.managers.permissions import SETTINGS_FILENAME

    return (SETTINGS_FILENAME, ENGINE_TMP_DIRNAME)


def _per_manager_user_entries(name: str) -> tuple[str, ...]:
    """The entries in `.rite/user/` a Manager called `name` gets, each
    derived from the function that builds its path, so this cannot drift
    from where rite actually writes."""
    here = Path(".")
    # The sandbox profile is not here any more: it lives in the Manager's own
    # credential directory, which no Manager can write (`profile_path`).
    return (
        _instance_filename(name),
        designation_path(here, name).name,
    )


def manager_name_problem(name: str) -> str:
    """Why `name` cannot be a Manager's name, or "".

    ⚠ **C30. `.rite/user/` holds per-Manager files keyed by name AND rite's
    own files, in one directory.** A Manager named `permissions` had its
    instance record at `.rite/user/permissions.json` — the permission
    allowlist rite passes to every Manager — so starting it overwrote the
    list, or the list was read as its record. C11 was the same collision for
    designations. So the CLASS is refused, not the word: any name whose own
    entries would coincide with one of rite's (`_rite_owned_user_entries`).
    Today that is `permissions`, found by computing, not by listing."""
    problem = name_problem(name, kind="manager name", must_be_a_tmux_target=True)
    if problem:
        return problem
    # ⚠ CASE-FOLDED. macOS volumes are case-insensitive by default, so
    # `Permissions.json` IS `permissions.json` there — measured on this
    # machine. An exact comparison refused `permissions` and admitted
    # `Permissions`, which collides just the same.
    owned = {e.casefold() for e in _rite_owned_user_entries()}
    for entry in _per_manager_user_entries(name):
        if entry.casefold() in owned:
            return (
                f"a Manager cannot be called {name!r}: its state would be "
                f"stored as .rite/user/{entry}, which is a file rite keeps "
                "for itself, so each would overwrite the other. Choose "
                "another name."
            )
    return ""


@dataclass
class ManagerInstance:
    """One running Manager, as this machine sees it."""

    name: str
    pid: int
    """⚠ REQUIRED, and the absence of a default is the point.

    It defaulted to 0, and `pid_alive(0)` is False, so a record built
    without one was indistinguishable from a record of a dead process.
    `_default_starter` omitted it and `rite status` reported every running
    Manager as "recorded but not running — the session is gone", on the
    only path the CLI takes. Correcting that one caller would have left the
    next one free to make the same mistake; a value whose default silently
    means "dead" should not have one."""
    session: str = ""
    started_at: float = 0.0
    engine: str = ""
    max_sessions: int | None = 0
    """The session-start ceiling this invocation was given. A COUNT, not
    spend — §2.6.1 says rite cannot read the quota and D-38 forbids the path
    from measurement back to control (D-69). None: a run with no bound
    (D-115)."""
    window_seconds: float | None = 0.0
    """The wall-clock bound. The ceiling above bounds starts; this is what
    bounds duration, and §9.14.5 records that a count alone bounds neither.
    None: a run with no bound (D-115)."""

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
    # A record written before `pid` existed genuinely has no pid, and 0 is
    # the honest answer: we do not know whether that process is alive. This
    # is the ONLY place that may supply one — a constructor call that omits
    # it is a bug, which is why the field has no default.
    known.setdefault("pid", 0)
    try:
        return ManagerInstance(**known)
    except (TypeError, ValueError):
        return None


def designation_path(root: Path, name: str) -> Path:
    """Where this Manager's designated session id is kept.

    ⚠ **A FILE OF ITS OWN, and not `instance_path`'s.** `forget_instance`
    unlinks `<name>.json`, and Ctrl-C calls it — so a designation stored
    there would be erased by the ordinary way a user stops a Manager, which
    is precisely the run they most want to continue tomorrow. The instance
    record describes a session that IS running; the designation describes
    one to come back to. Different lifetimes, different files.

    Under `user_dir()` with the instance record: per-user, per-machine,
    never committed. A provider's session id is not portable between
    machines, and a shared `config.yaml` claiming one on somebody else's
    laptop is worse than saying nothing.
    """
    return user_dir(root) / f"{name}{DESIGNATION_SUFFIX}"


DESIGNATION_SUFFIX = ".designated.json"
"""Named once, because two functions depend on it agreeing: `designation_path`
writes it and `running_instances` must skip it. See the latter for why."""


def designate(
    root: Path, name: str, session_id: str, board: dict | None = None
) -> None:
    """Record the session a bare `rite start <name>` should continue.

    `board` is the board the conversation BEGAN under (`board_context`): given
    when a cycle launched fresh, and None when it continued, in which case
    whatever was recorded is carried forward unchanged.
    """
    if not session_id:
        return
    path = designation_path(root, name)
    record: dict = {"session": session_id}
    if board is not None:
        record["board"] = board
    else:
        try:
            before = json.loads(path.read_text())
        except (OSError, ValueError):
            before = None
        if isinstance(before, dict) and isinstance(before.get("board"), dict):
            record["board"] = before["board"]
    # ⚠ A Cursor chat's record (`record_chat`) belongs to its handle, so it is
    # carried forward while the handle is unchanged, and dropped with it.
    # Rewriting it away would turn a confirmed chat back into "never
    # confirmed", which `cursor_chat.before_turn` reads as a first turn.
    chat = _read_chat(path, session_id)
    if chat is not None:
        record["chat"] = chat
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(record, indent=2) + "\n")


def _read_chat(path: Path, session_id: str) -> dict | None:
    try:
        before = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(before, dict) or before.get("session") != session_id:
        return None
    chat = before.get("chat")
    return chat if isinstance(chat, dict) else None


def record_chat(
    root: Path,
    name: str,
    handle: str,
    *,
    created_ms: int | None = None,
    broken: str = "",
    board: dict | None = None,
) -> None:
    """Record a Cursor chat's handle, and what rite has confirmed about it.

    Written BEFORE the handle's first launch (with no `created_ms`), again
    when the first turn is confirmed, and with `broken` when a turn is found
    to have run in a replaced chat (CU3, `cursor_chat`). Atomic, like
    `designate`; only this Manager's supervisor writes it.
    """
    path = designation_path(root, name)
    record: dict = {"session": handle}
    try:
        before = json.loads(path.read_text())
    except (OSError, ValueError):
        before = None
    if board is not None:
        record["board"] = board
    elif (
        isinstance(before, dict)
        and before.get("session") == handle
        and isinstance(before.get("board"), dict)
    ):
        record["board"] = before["board"]
    chat: dict = {"created_ms": created_ms}
    if broken:
        chat["broken"] = broken
    record["chat"] = chat
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(record, indent=2) + "\n")


@dataclass(frozen=True)
class ChatRecord:
    handle: str
    created_ms: int | None
    broken: str


def recorded_chat(root: Path, name: str) -> ChatRecord | None:
    """The Cursor chat this Manager continues, or None when none is recorded.

    ⚠ **Not "unreadable is absent" here, unlike `designated`.** For Claude
    an unusable designation means start fresh. For Cursor a record that says
    `broken` must stop the next run, and a record whose `chat` part is
    malformed cannot say whether it was confirmed, so it is reported as
    broken rather than read as never-confirmed (which would relaunch as a
    first turn).
    """
    path = designation_path(root, name)
    try:
        raw = json.loads(path.read_text())
    except OSError:
        return None
    except ValueError:
        return ChatRecord("", None, f"{path} is not JSON")
    if not isinstance(raw, dict) or "chat" not in raw:
        return None
    handle = raw.get("session")
    chat = raw.get("chat")
    if not isinstance(handle, str) or not isinstance(chat, dict):
        return ChatRecord("", None, f"{path} does not hold a usable chat record")
    created = chat.get("created_ms")
    if created is not None and (
        not isinstance(created, int) or isinstance(created, bool)
    ):
        return ChatRecord(handle, None, f"{path} holds a malformed created_ms")
    broken = chat.get("broken", "")
    return ChatRecord(handle, created, broken if isinstance(broken, str) else "broken")


def designated(root: Path, name: str) -> str:
    """The designated session id, or "" when there is none to continue.

    ⚠ **Unreadable is the same as absent, deliberately.** There is no error
    state here: nothing to continue is a reason to start fresh, not a
    problem a user must clear before they may work. A corrupt file must not
    stop somebody working.

    ⚠ **And this answers only what was WRITTEN DOWN.** Whether the provider
    still knows that conversation is a different question, and it is not
    answerable from here — the caller finds out by trying it. Treating a
    readable file as proof the session exists is a proxy for the property.
    """
    from rite_ai.managers.transcripts import session_id_problem

    try:
        raw = json.loads(designation_path(root, name).read_text())
    except (OSError, ValueError):
        return ""
    if not isinstance(raw, dict):
        return ""
    got = raw.get("session")
    if not isinstance(got, str) or session_id_problem(got):
        # ⚠ **AN UNUSABLE ID IS AN ABSENT ONE**, by the same rule as the
        # corrupt file above — and this half was missing. `launch_command`
        # REFUSES an id that is not shaped like one, because that string is
        # run by a shell; it raises rather than dropping the flag, so
        # handing one back from here wedged `rite start` with a traceback
        # and kept wedging it, since the file is re-read every run.
        # Measured, with `{"session": "abc\nrm -rf /"}`.
        #
        # Checked HERE rather than only at the write, so a file already
        # poisoned heals on the next run instead of needing a user to find
        # and delete something they were never told about.
        return ""
    return got


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
        if path.name.endswith(DESIGNATION_SUFFIX):
            # ⚠ **SKIPPED BY NAME, ON PURPOSE (C11).** `<name>.designated.json`
            # matches this glob. It used to stay out of the listing only by
            # accident, twice over: `read_instance` validates the stem as a
            # tmux target, which rejects the `.` in `planner.designated`, and
            # it drops any file without a `name` key. Neither rule is about
            # designations. Measured with both relaxed — a naming rule that
            # admits dots, and a designation that records whose it is, which
            # a membership check could reasonably add — `rite status` listed a
            # running Manager and offered `tmux attach` to a provider session
            # id. The two files have different lifetimes on purpose (see
            # `designation_path`); keeping them apart is now this line's job.
            continue
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
