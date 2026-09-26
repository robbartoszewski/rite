"""The Manager boundary on Linux, built with Landlock (C28).

`enclosure.py` is the macOS half of this and is deliberately untouched by it:
seatbelt keeps its own module, its own profile text and its own behaviour.
`boundaries.py` chooses between the two at launch.

⚠ **WHY LANDLOCK AND NOT BUBBLEWRAP.** Measured 2026-09-25 on Ubuntu
24.04.4, kernel 7.0, as an ordinary user: `bwrap` could not start at all —
`bwrap: setting up uid map: Permission denied` — because Ubuntu ships
`apparmor_restrict_unprivileged_userns = 1`. `unshare --user
--map-root-user` failed the same way. Both work as root. So the bubblewrap
route exists only if the operator loosens a kernel security setting, and
rite would be asking for that on the commonest Linux it will meet.

⚠ Note `/proc/sys/kernel/unprivileged_userns_clone` reads **1** on that same
machine. A check that reads only that knob concludes namespaces are
available and is wrong. The AppArmor one decides it.

Landlock needs no privilege, no namespace and no setting changed, and was
measured to hold: project readable and writable, another project denied,
`$HOME` denied — 4 of 4 — on kernel 7.0 (ABI 8) and again inside a container
on kernel 6.12 (ABI 6).

⚠ **NESTING WORKS HERE, AND THAT IS NOT TRUE OF macOS.** On macOS a
sandboxed process can re-enter `sandbox-exec` only with a semantically
equivalent profile; a NARROWER one fails too, which nothing documented.
Landlock rulesets INTERSECT, and it was measured: a narrower ruleset applied
inside an existing one and took effect (subdirectory writable, parent no
longer); a wider one applied but granted nothing back; 16 may be stacked and
a Manager boundary with a Worker boundary inside it needs two. The boundary
also survives `fork` + `exec` with the child narrowing further. So the
constraint that forced the broker's design on macOS is absent here.

🔴 **THE HOLE THIS SHIPS WITH, NAMED HERE RATHER THAN ONLY IN A DESIGN
NOTE.** Landlock's filesystem rules govern OPENING files. They do not govern
`connect(2)`. Measured, in one process with one ruleset as its own control:
`listdir()` of a denied directory was refused, writing into it was refused,
and `connect()` to a unix socket **inside that same directory succeeded** —
with a helper outside the boundary acting on the connection.

So the macOS fix for the tmux escape — denying the socket's path — **has no
Landlock equivalent**. A Manager here can reach a unix socket whose directory
this boundary denies, and rite's own tmux control socket is exactly that
shape. `limitations()` says so out loud every run, because a boundary sold as
more than it is would be worse than none.

A container does not close it either, measured with a real tmux control
socket: with the socket mounted in, a `touch` issued from the confined side
executed in the server's context and landed where the confined side had no
access. Without it mounted there is nothing to reach — so the fix is for the
tmux server to live INSIDE the boundary, which is an architectural change and
not a setting. See `docs/design/spikes/B9b-linux-manager-sandboxing.md`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import shlex
import struct
import sys
from pathlib import Path

from rite_ai.managers import manager_dir, user_dir
from rite_ai.managers.enclosure import (  # noqa: PLC2701
    _engine_state_paths,
    _running_rite,
    _tool_paths,
    engine_tmp,
)

# ⚠ **SHARED WITH SEATBELT ON PURPOSE, NOT DUPLICATED.** These four carry
# measured knowledge about where rite, uv's interpreter, gh's config and
# Goose's session store live, and each was added because a Manager broke
# without it. Copying them here would let the two boundaries drift on exactly
# the paths whose absence is hardest to diagnose — a `dyld`/`ld.so` crash
# rather than a refusal. `_system_paths` is NOT shared: that one is macOS's.
from rite_ai.managers.mailbox import INBOX, OUTBOX, mail_root, mailbox_dir

PROFILE_DIRNAME = "landlock"

# Architecture-neutral syscall numbers: the same on x86_64 and aarch64.
SYS_CREATE_RULESET = 444
SYS_ADD_RULE = 445
SYS_RESTRICT_SELF = 446

CREATE_RULESET_VERSION = 1
PR_SET_NO_NEW_PRIVS = 38

# Filesystem access bits (ABI 1 unless noted).
A_EXECUTE = 1 << 0
A_WRITE_FILE = 1 << 1
A_READ_FILE = 1 << 2
A_READ_DIR = 1 << 3
A_REMOVE_DIR = 1 << 4
A_REMOVE_FILE = 1 << 5
A_MAKE_CHAR = 1 << 6
A_MAKE_DIR = 1 << 7
A_MAKE_REG = 1 << 8
A_MAKE_SOCK = 1 << 9
A_MAKE_FIFO = 1 << 10
A_MAKE_BLOCK = 1 << 11
A_MAKE_SYM = 1 << 12
A_REFER = 1 << 13  # ABI 2
A_TRUNCATE = 1 << 14  # ABI 3

# ⚠ Scoping needs ABI 6. `LANDLOCK_SCOPE_SIGNAL` is the direct equivalent of
# seatbelt's `(allow signal (target same-sandbox))` and was measured to close
# the signal escape, with a control — the same restriction scoping everything
# EXCEPT signals — killing its own target, so the result discriminates rather
# than merely failing.
SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
SCOPE_SIGNAL = 1 << 1
SCOPING_NEEDS_ABI = 6

READ_ONLY = A_EXECUTE | A_READ_FILE | A_READ_DIR
# Everything a Manager does to its own project. TRUNCATE and REFER are added
# only when the kernel handles them; see `_handled`.
READ_WRITE = (
    READ_ONLY
    | A_WRITE_FILE
    | A_REMOVE_DIR
    | A_REMOVE_FILE
    | A_MAKE_DIR
    | A_MAKE_REG
    | A_MAKE_SOCK
    | A_MAKE_FIFO
    | A_MAKE_SYM
    | A_MAKE_CHAR
    | A_MAKE_BLOCK
)


def _libc():
    """libc, loaded by soname first.

    ⚠ `ctypes.util.find_library()` shells out to gcc/ld and needs a writable
    temp directory. Inside a boundary that denies `$TMPDIR` it raises
    `FileNotFoundError` before any measurement happens — found exactly that
    way, when a confined process exec'd a second interpreter.
    """
    last = None
    for candidate in ("libc.so.6", "libc.so"):
        try:
            return ctypes.CDLL(candidate, use_errno=True)
        except OSError as exc:  # pragma: no cover - platform dependent
            last = exc
    found = ctypes.util.find_library("c")
    if found:
        return ctypes.CDLL(found, use_errno=True)
    raise OSError(f"could not load libc: {last}")


def _syscall(number: int, *args):
    libc = _libc()
    libc.syscall.restype = ctypes.c_long
    ctypes.set_errno(0)
    return libc.syscall(ctypes.c_long(number), *args), ctypes.get_errno()


def abi() -> int:
    """The kernel's Landlock ABI level, or 0 when there is no Landlock.

    ⚠ **Read by CALLING the syscall, which is the only way it can be read.**
    Not inferred from the kernel release: measured on kernel 7.0 the release
    proxy would have said 4 and the answer is 8. Not read from
    `/sys/kernel/security/lsm` either — that file is not mounted inside a
    container where Landlock nonetheless works.
    """
    if sys.platform != "linux":
        return 0
    try:
        result, _ = _syscall(
            SYS_CREATE_RULESET,
            None,
            ctypes.c_size_t(0),
            ctypes.c_uint32(CREATE_RULESET_VERSION),
        )
    except OSError:
        return 0
    return result if result > 0 else 0


def _handled(level: int) -> int:
    """Which access rights this kernel will take charge of.

    A ruleset that handles a right the kernel does not know is rejected, so
    the newer bits are added by ABI level rather than hoped for.
    """
    handled = READ_WRITE
    if level >= 2:
        handled |= A_REFER
    if level >= 3:
        handled |= A_TRUNCATE
    return handled


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


# ⚠ **BUILT AS BYTES, NOT AS A ctypes.Structure, AND THAT IS DELIBERATE.**
# `landlock_path_beneath_attr` is `__u64 allowed_access; __s32 parent_fd;` with
# `__attribute__((packed))` — 12 bytes, no trailing padding. Expressing that
# with `ctypes` needs `_pack_ = 1`, and Python 3.14 both warns that `_pack_`
# alone implies the MSVC layout AND refuses `_pack_` together with
# `_layout_ = "gcc-sysv"`, raising `ValueError: _pack_ is not compatible with
# gcc-sysv layout`. Measured on the macOS CI job: naming the layout turned a
# deprecation warning into a hard import error, so `landlock.py` would not
# import at all on 3.14.
#
# `struct.pack("=Qi", ...)` says what the kernel ABI is with no layout dialect
# to choose: `=` is native byte order with standard sizes and NO alignment, so
# it is 8 + 4 = 12 bytes on every platform this runs on. Asserted in
# `PATH_BENEATH_SIZE` rather than trusted.
PATH_BENEATH_FORMAT = "=Qi"
PATH_BENEATH_SIZE = struct.calcsize(PATH_BENEATH_FORMAT)


def _path_beneath(allowed_access: int, parent_fd: int) -> ctypes.Array:
    """The packed `landlock_path_beneath_attr` the kernel expects."""
    return ctypes.create_string_buffer(
        struct.pack(PATH_BENEATH_FORMAT, allowed_access, parent_fd),
        PATH_BENEATH_SIZE,
    )


# ⚠ **SEATBELT ALLOWS EXEC OF ANY PATH; LANDLOCK DOES NOT.** The seatbelt
# profile carries an unfiltered `(allow process-exec)`, so a Manager there can
# execute a binary whose path no `file-read*` rule names. Landlock has no
# separate exec permission: EXECUTE is a filesystem access right, so the binary
# has to sit under a GRANTED path or `execve` fails with EACCES.
#
# ⚠ And it is the REAL path that matters, because a Landlock rule names an
# inode. Measured 2026-09-26 on Ubuntu: the installer makes
# `~/.local/bin/claude` a symlink to
# `~/.local/share/claude/versions/2.1.283`. `.local/bin` was granted and the
# versions directory was not, so a Claude Manager could not start at all —
# `rite: could not start 'claude' inside the boundary: [Errno 13] Permission
# denied`, exit 127, with Robert's token stored and never reached.
#
# Measured on macOS for contrast, so this is understood rather than assumed:
# the Mac has the SAME symlink layout and the same ungranted target, and
# `claude --version` returns 2.1.261 inside the shipped profile. Seatbelt is
# not saving us by luck; it simply does not gate exec on the path.
#
# Resolved per binary rather than by naming the installer's directory, so a
# different layout — npm, Homebrew, a version manager — works without a change
# here. The PARENT is granted, not the file: a binary that loads anything from
# beside itself needs its directory, and `.local/share/uv` is granted for
# exactly that reason already.
ENGINE_BINARIES = ("claude", "goose", "rite", "git", "gh", "tmux")


def _engine_binary_paths() -> list[Path]:
    """Where the binaries a Manager runs REALLY live, for EXECUTE."""
    import shutil

    found: list[Path] = []
    for name in ENGINE_BINARIES:
        where = shutil.which(name)
        if not where:
            continue
        real = Path(where).resolve()
        for candidate in (real if real.is_dir() else real.parent, real):
            if candidate.exists():
                found.append(candidate)
    return list(dict.fromkeys(found))


def policy_path(root: Path, manager: str) -> Path:
    """Where this Manager's path policy is written.

    A JSON document, not a Landlock artefact: Landlock has no profile file:
    the rules are syscalls. The file exists so that the policy a Manager ran
    under is inspectable afterwards, exactly as the `.sb` profile is on
    macOS, and so `why_it_was_refused` can point somebody at it.
    """
    return user_dir(root) / PROFILE_DIRNAME / f"{manager}.json"


def compose_policy(root: Path, manager: str, home: Path | None = None) -> dict:
    """The path policy for one Manager, as data.

    The same intent as the seatbelt profile, expressed in the only vocabulary
    Landlock has: paths and access bits. It is a SEPARATE implementation
    rather than a shared one, because extracting a common policy would mean
    rewriting the seatbelt profile, and that profile's behaviour is pinned.
    """
    where = Path(home) if home is not None else Path(os.path.expanduser("~"))
    project = Path(root).resolve()

    # Read-only system locations any process needs to start at all. The Linux
    # set, not `enclosure._system_paths()`, which names `/System`,
    # `/opt/homebrew` and `/private/var` — macOS's.
    system = [
        Path(p)
        for p in (
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/lib32",
            "/etc",
            "/opt",
            "/proc",
            "/sys",
            "/run",
            "/var/lib",
            "/var/run",
        )
    ]

    readable = [p for p in system if p.exists()]
    readable += [p for p in _tool_paths(where) if p.exists()]
    readable += [p for p in _running_rite() if p.exists()]
    # The engines' real paths — see `_engine_binary_paths`. Without these a
    # symlinked installer layout cannot be exec'd at all.
    readable += _engine_binary_paths()

    # ⚠ `/dev` must be WRITABLE, not merely readable — the same finding as
    # seatbelt's: without it every `cmd >/dev/null` fails, which is most of
    # what a shell does and none of what a boundary is for.
    writable = [Path("/dev")]
    # ⚠ NOT the project as a tree: `_fenced_project_paths` grants it so that
    # no Manager's inbox is writable (MM-2), which Landlock can only express by
    # enumeration. See that function for what it costs.
    writable += _fenced_project_paths(project, manager)
    # ⚠ **THE MAILBOX IS OUTSIDE THE PROJECT** (`mailbox.mail_root`), so no
    # grant above reaches an inbox BY CONSTRUCTION, with nothing to enumerate
    # around it. The Manager gets its own outbox, which `rite reply` and
    # `rite ask` write from in here, and its mail directory read-only, so
    # `prune` can see the readers' cursors. Its inbox is in neither list.
    mail = mail_root(root, manager)
    readable.append(mail)
    writable.append(mail / OUTBOX)
    writable.append(engine_tmp(root, manager))
    # Shared temporary space, mirroring seatbelt. ⚠ This is also where the
    # socket hole lives: see the module docstring and `limitations`.
    writable += [Path("/tmp"), Path("/var/tmp")]
    writable += [p for p in _engine_state_paths(where) if p.exists()]

    # ⚠ **THIS MANAGER'S OWN CREDENTIALS (C6/C26), MATCHED TO SEATBELT FILE
    # FOR FILE.** The seatbelt side narrowed at `3531b51` to two GitHub files
    # by exact path, no grant on the credential directory itself, and a deny on
    # the Claude login so a Manager cannot replace the file that authenticates
    # it. Mirroring the wide version was right while it was wide; leaving it
    # wide now would be the same divergence in reverse, which is how the
    # seatbelt-only grant got past everyone in the first place.
    #
    # ⚠ **THE LOGIN IS READ-ONLY BY ENUMERATION, BECAUSE LANDLOCK CANNOT DENY.**
    # Seatbelt grants `claude/` read+write and then denies write on
    # `.credentials.json` LAST. Landlock unions its grants, so there is nothing
    # to place last: `claude/` is therefore NOT granted as a tree, its existing
    # children are granted individually, and the login is granted READ-ONLY.
    # Same trick as the inbox fence, same cost — Claude Code cannot create a
    # NEW top-level entry in its config directory during a cycle, though
    # anything inside an existing one (`projects/`, where transcripts go) is
    # unaffected because those are granted as trees.
    #
    # ⚠ **SYMLINKS ARE NOT FOLLOWED, AND NARROWING MAKES THAT MORE IMPORTANT.**
    # A Landlock rule names the inode a path resolves to, so a symlink named
    # `hosts.yml` would grant whatever it points at — the exact-path grant is
    # otherwise a widening primitive rather than a narrowing one.
    from rite_ai.managers import github_access

    cdir = github_access._credential_dir(root, manager, where)  # noqa: PLC2701
    gh_dir = cdir / "gh"
    claude_dir = cdir / "claude"

    # The two GitHub files, read-only, by exact path — not the directory.
    for name in ("hosts.yml", "config.yml"):
        one = gh_dir / name
        if one.is_file() and not one.is_symlink():
            readable.append(one)

    if claude_dir.is_dir() and not claude_dir.is_symlink():
        login = claude_dir / ".credentials.json"
        for child in sorted(claude_dir.iterdir()):
            if child.is_symlink() or child == login:
                continue
            writable.append(child)
        if login.is_file() and not login.is_symlink():
            # Readable so Claude can sign in; never writable.
            readable.append(login)

    return {
        "manager": manager,
        "project": str(project),
        # ⚠ `$HOME` is absent from both lists, which is how it is denied.
        # Landlock grants nothing it is not told to grant, so there is no
        # ordering to get wrong — unlike seatbelt, where the deny has to come
        # last to win. `~/.yoloai` is unreachable for the same reason it is
        # explicitly denied on macOS: a Manager cannot build a Worker sandbox
        # from inside one, so it asks the supervisor.
        "readable": [str(p) for p in dict.fromkeys(readable)],
        "writable": [str(p) for p in dict.fromkeys(writable)],
        # Read-only although they sit under a granted tree elsewhere: an agent
        # that can rewrite git config can change what every later commit
        # claims.
        "readonly_overrides": [
            str(where / ".gitconfig"),
            str(where / ".config/git"),
        ],
    }


# ⚠ **SEATBELT CAN GRANT A PATH BEFORE IT EXISTS. LANDLOCK CANNOT.** This is
# the difference that has now caused three separate defects, and the next
# person will assume the macOS semantics, so it is written here rather than
# left to be rediscovered.
#
# A seatbelt rule is TEXT matched against a path when the path is opened, so
# `(allow file-read* (subpath "~/.config/goose"))` works whether or not that
# directory exists yet — it starts working the moment something creates it.
# A Landlock rule names an INODE, resolved by `open(O_PATH)` when the rule is
# ADDED. A path that does not exist cannot be named, so the grant is silently
# absent for the whole life of the boundary, and a directory created later is
# outside it.
#
# Measured on a container with an empty HOME: on a machine where Goose, gh and
# rite have never run, NONE of the five directories below were granted, and a
# Goose Manager panicked on first start. Found by the Linux observation pass.
#
# So rite creates the ones it is responsible for before composing. They are
# all directories the tool itself would create on first run; making them early
# only fixes WHEN.
DIRS_RITE_CREATES = (
    # Goose reads its config here and panics without the directory.
    ".config/goose",
    # Goose's session store, which holds the conversation handle every resumed
    # cycle names — a Manager that cannot write it cannot continue its work.
    ".local/share/goose",
    ".local/state/goose",
    # `~/.config/gh` is no longer here (W8): it holds the OPERATOR's gh login,
    # in plain text on a box with no keyring, and is no longer granted. gh
    # starts from the Manager's own config directory instead
    # (`github_access._own_gh_config`), which rite creates before the ruleset.
    # rite's own state root, granted readable.
    ".rite",
)

# ⚠ **DELIBERATELY NOT CREATED**, so the list above is not mistaken for
# "everything the policy names":
#   * `.local/bin` and `.local/share/uv` exist only if that tool is installed.
#     An empty one would be granted and hold nothing, which fixes no failure.
#   * `.gitconfig` and `.config/git` are read-only grants and their absence is
#     harmless — git simply has no user config. Creating them would be rite
#     inventing state on the operator's behalf.
#   * The per-Manager credential directory IS granted (readable, with
#     `claude/` writable — see the C6/C26 block in `_policy`), and is still
#     not created here: `claude_login.prepare` writes the login and makes the
#     directory before the ruleset is built, so by the time these grants are
#     computed it exists. Creating an empty one here would grant a directory
#     holding nothing, which fixes no failure and hides the ordering that
#     does. ⚠ This bullet used to say the directory was "not granted by this
#     backend at all". That was true before `57469b8` and false after it, and
#     the code twenty lines up had said so the whole time.


def _ensure_grantable(home: Path) -> None:
    """Create the directories the policy will grant, because a Landlock rule
    cannot name a path that does not exist yet. See DIRS_RITE_CREATES."""
    for relative in DIRS_RITE_CREATES:
        (home / relative).mkdir(parents=True, exist_ok=True)


def _fenced_project_paths(project: Path, manager: str) -> list[Path]:
    """The project, granted so that no Manager writes another's directory (P1).

    ⚠ **LANDLOCK HAS NO DENY RULE.** Seatbelt separates Managers with ordered
    rules — deny `.rite/managers`, allow this Manager's own directory — and
    takes the last match. Landlock rules only ever GRANT, and the effective
    access is the UNION, so there is nothing to place last. A carve-out
    therefore has to be an ENUMERATION: grant the siblings of the thing being
    fenced, and never its parent.

    Measured 2026-09-26 in a container, all three modes, as an ordinary user,
    when the inboxes were still in the tree:

        granting the project as a tree   another's inbox WRITABLE, own WRITABLE
        enumerating one level            another's refused, own still WRITABLE
        enumerating two levels           both REFUSED, project source writable

    ⚠ **THE INBOXES HAVE LEFT THE TREE, AND THIS ENUMERATION HAS NOT, ON
    PURPOSE.** Since 0.6.0 the mailbox is under rite's home, fenced by
    construction. But `.rite/managers/<name>/` still holds what a Manager's
    supervisor acts on with that Manager's authority — `routes/`, which the
    Owner's supervisor delivers as "routed by the Owner · INSTRUCTION", and
    `prompt.txt`, which IS the next cycle's instruction. Granting the project
    as a tree would let a secondary write the Owner's route requests. So the
    tree is still enumerated, and only one level of it changed: this
    Manager's own `mail/`, the pre-0.6.0 box rite still reads until it
    drains, is now left out whole.

    ⚠ **WHAT IT COSTS, because it is a real cost and not a theoretical one.**
    The project root is not granted as a tree, so a Manager cannot create a
    NEW TOP-LEVEL entry in its project during a cycle — measured:
    `mkdir /proj/newtopdir` raises PermissionError. Everything inside an
    existing top-level directory is unaffected, including new subdirectories:
    `src/newpkg/` was created and written in the same run. So the limitation is
    the project ROOT, not the project.

    ⚠ **And it is a snapshot.** The enumeration happens when the policy is
    written, which is every launch, so a directory that appears mid-cycle is
    not covered until the next one. Seatbelt's subtree grant is dynamic and
    this is not; that difference is the price of having no deny rule.

    Removing both costs means moving the whole per-Manager directory out of
    the project, as the mailbox was — not a better ruleset.
    """
    managers = project / ".rite" / "managers"
    if not managers.is_dir():
        # No Managers yet: nothing to fence, so the project is granted whole
        # and a first cycle is not crippled before any inbox exists.
        return [project]

    # ⚠ **SYMLINKS ARE SKIPPED, AND THAT IS A FENCE PROPERTY.** Landlock rules
    # name an INODE: a rule added for a symlink grants the inode it resolves
    # to. Measured 2026-09-26 in review — granting ONLY a symlink that pointed
    # at another Manager's `mail/in` made that inbox writable both through the
    # link and directly. So an enumeration that included symlinks could hand
    # back exactly what it is carving out, and one symlink anywhere in the
    # project root would defeat MM-2.
    #
    # Skipped rather than resolved-and-checked: a link whose target moves
    # between composing and applying would pass the check and grant the new
    # target. Not granting a symlinked entry fails closed, and the cost is
    # that a symlink in the project root is not writable — which is the
    # correct trade for a boundary.
    #
    # ⚠ NOT applied to the system paths above: on Linux `/lib` and `/lib64`
    # ARE symlinks into `/usr`, so refusing symlinks there would deny the
    # loader and nothing would start.
    def entries(directory: Path, skip) -> list[Path]:
        return [
            p
            for p in sorted(directory.iterdir())
            if p not in skip and not p.is_symlink()
        ]

    granted: list[Path] = []
    rite_dir = project / ".rite"
    # Every top-level entry except `.rite` — the parent must not be granted.
    granted += entries(project, {rite_dir})
    # Everything in `.rite` except `managers`.
    granted += entries(rite_dir, {managers})
    # This Manager's own directory, by its children, leaving out the
    # pre-0.6.0 `mail/` whole: rite still reads it until it drains.
    own = managers / manager
    if own.is_dir():
        granted += entries(own, {own / "mail"})
    return granted


def write_profile(root: Path, manager: str, home: Path | None = None) -> Path:
    """Write the policy and the engine's TMPDIR, and return the policy path.

    Rewritten every run, for the reason the seatbelt profile is: a write-once
    file pins a project to whatever shipped the day it was created.
    """
    path = policy_path(root, manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine_tmp(root, manager).mkdir(parents=True, exist_ok=True)
    # ⚠ **THE MANAGER'S OWN DIRECTORY AND MAIL BOXES ARE CREATED HERE, and
    # that is load-bearing rather than tidy.** A Landlock rule names an
    # existing inode, so a path absent when the policy is written is a path the
    # Manager cannot reach for the whole cycle. `manager_dir` only computes a
    # path; nothing had created it at this point. Found in review: on a FIRST
    # launch, with `.rite/managers/` present because another Manager exists
    # but this Manager's own directory not yet, the enumeration granted
    # nothing under `managers/` at all and the Manager could not write its own
    # journal, designation or outbox.
    #
    # The outbox matters specifically: `rite reply` writes there from INSIDE
    # the boundary, and it is the one mail box a Manager must be able to write.
    # It is under rite's home now, not the project, and granted by exact path.
    manager_dir(root, manager).mkdir(parents=True, exist_ok=True)
    mailbox_dir(root, manager, OUTBOX).mkdir(parents=True, exist_ok=True)
    mailbox_dir(root, manager, INBOX).mkdir(parents=True, exist_ok=True)
    # ⚠ Before composing, for the same reason and with the same resolution of
    # `home` that `compose_policy` uses — a mismatch here would create one
    # directory and grant another.
    _ensure_grantable(Path(home) if home is not None else Path(os.path.expanduser("~")))
    path.write_text(json.dumps(compose_policy(root, manager, home), indent=2) + "\n")
    return path


def wrap(command: str, profile: Path) -> str:
    """Put `command` inside the boundary.

    ⚠ **Prefixed rather than rebuilt**, exactly as seatbelt's `wrap` is: the
    command already carries the engine's vocabulary and, for Claude, a stdin
    redirection from the prompt file. The shell tmux runs this through applies
    the redirection around the whole thing.

    ⚠ The redirection is applied by that shell BEFORE this launcher execs, so
    the engine inherits an already-open stdin. Landlock governs opening a
    path, not a descriptor already held — measured — so the prompt still
    arrives although the boundary would refuse to open that file.
    """
    return (
        f"{shlex.quote(sys.executable)} -m rite_ai.managers.landlock "
        f"{shlex.quote(str(profile))} -- {command}"
    )


def limitations() -> tuple[str, ...]:
    """What this boundary does NOT do, to be PRINTED rather than inferred.

    ⚠ **Dynamic, unlike seatbelt's.** Signal scoping needs ABI 6 and a stock
    Ubuntu 24.04 kernel is 6.8, which is ABI 4 — so on a common machine the
    signal escape is OPEN. Returning a fixed list would claim it closed
    there. The list says what THIS kernel does.
    """
    level = abi()
    holes = [
        "🔴 A UNIX SOCKET IN A DENIED DIRECTORY IS STILL REACHABLE. Landlock "
        "bounds opening files and does not govern connect(2) — measured, with "
        "the same ruleset refusing to list or write that directory. rite's own "
        "tmux control socket is that shape, so the escape macOS closed by "
        "denying the socket's path is OPEN here. This is a known hole, not an "
        "unexamined one",
        "/tmp and /var/tmp are readable and writable, so anything kept there "
        "— including other rite worktrees — is reachable",
        "the network is NOT confined: a Manager can reach anything this machine can",
        "a Manager can run `rite`, which does whatever you can do to this "
        "project — the boundary bounds the filesystem, not that",
        "your own engine hooks are still loaded, and one that runs something "
        "outside the paths in the policy will FAIL inside the boundary where "
        "it worked outside it",
        "ticket text from your board reaches the engine as instructions; the "
        "boundary limits what acting on it can touch, it does not vet it",
    ]
    if level < SCOPING_NEEDS_ABI:
        holes.insert(
            1,
            "🔴 SIGNALS CROSS THIS BOUNDARY on this kernel. Landlock signal "
            f"scoping needs ABI {SCOPING_NEEDS_ABI} and this kernel reports "
            f"ABI {level or 'none'}, so a Manager can signal processes it did "
            "not start — including another Manager or the supervisor watching "
            "it. A kernel 6.12 or newer closes it",
        )
    else:
        holes.append(
            "what it DOES buy: your home outside the policy's paths, your SSH "
            "keys, and other projects outside /tmp are not reachable, and "
            "signals do not cross the boundary — measured before and after"
        )
    return tuple(holes)


def refusal_looks_like_ours(pane_text: str) -> bool:
    """Did something in the pane fail because THIS boundary refused it?

    Landlock refusals arrive as `EACCES` or `EPERM`, which reach a user as
    "Permission denied" or "Operation not permitted" — the same words an
    ordinary permissions problem uses. So this can only ever be a hint, and
    `why_it_was_refused` is written to say "if this was us" rather than to
    assert it was. Matched to DETECT, and the pane is never relayed.
    """
    low = (pane_text or "").lower()
    return "permission denied" in low or "operation not permitted" in low


def why_it_was_refused(root: Path, manager: str) -> str:
    """What to tell a user whose hook or tool just failed inside the box."""
    return (
        f"something in Manager {manager!r} was refused, and it may have been "
        f"the Landlock boundary rite runs it in rather than a broken rite. "
        f"The policy is at {policy_path(root, manager)} and lists every path "
        f"the Manager may reach.\n"
        "  If it was one of your own engine hooks: hooks still load inside "
        "the boundary, and one that runs something outside the project will "
        "fail here though it works outside.\n"
        "  ⚠ Linux reports a Landlock refusal with the same words as an "
        "ordinary permissions error, so check the path against the policy "
        "before concluding either.\n"
        "  What this boundary does and does not buy: "
        "docs/design/spikes/B9b-linux-manager-sandboxing.md"
    )


# --- applying it ------------------------------------------------------------


# ⚠ **A RULE ON A REGULAR FILE MAY ONLY CARRY FILE RIGHTS.** Granting a
# directory right such as READ_DIR or MAKE_REG on a non-directory is rejected
# with EINVAL, and the whole ruleset fails with it. Measured: the first Linux
# launch died on `landlock_add_rule(/work/repo/VERSION): Invalid argument`,
# because `_running_rite()` names that one FILE and `_tool_paths` names
# `.claude.json` and `.gitconfig`. The spike probe never hit this — it granted
# only directories — which is why it is pinned by a test here.
FILE_ONLY = A_EXECUTE | A_READ_FILE | A_WRITE_FILE | A_TRUNCATE


def _add_rule(ruleset_fd: int, path: str, access: int) -> None:
    if not os.path.isdir(path):
        access &= FILE_ONLY
    if access == 0:
        # Nothing this node can be granted; a zero-right rule is also EINVAL.
        return
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        attr = _path_beneath(access, fd)
        result, err = _syscall(
            SYS_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_uint32(1),  # LANDLOCK_RULE_PATH_BENEATH
            attr,
            ctypes.c_uint32(0),
        )
        if result != 0:
            raise OSError(err, f"landlock_add_rule({path}): {os.strerror(err)}")
    finally:
        os.close(fd)


def apply(policy: dict) -> int:
    """Apply `policy` to THIS process, and return the ABI level used.

    Irreversible for this process and its children, which is the point: the
    engine is exec'd afterwards and inherits it. It cannot reach the caller
    of this function, anything already running, or the machine afterwards.
    """
    level = abi()
    if level == 0:
        raise OSError(
            "this kernel has no Landlock, so rite cannot build a Manager "
            "boundary here. Landlock arrives in Linux 5.13"
        )

    scoped = 0
    if level >= SCOPING_NEEDS_ABI:
        scoped = SCOPE_SIGNAL | SCOPE_ABSTRACT_UNIX_SOCKET

    attr = _RulesetAttr(_handled(level), 0, scoped)
    # ⚠ The struct grew: `handled_access_net` in ABI 4, `scoped` in ABI 6.
    # Sending a longer struct than the kernel knows is rejected, so the size
    # is trimmed to what this one accepts.
    size = 8 if level < 4 else (16 if level < 6 else ctypes.sizeof(attr))
    result, err = _syscall(
        SYS_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(size),
        ctypes.c_uint32(0),
    )
    if result < 0:
        raise OSError(err, f"landlock_create_ruleset: {os.strerror(err)}")
    ruleset_fd = result

    try:
        readonly = {str(p) for p in policy.get("readonly_overrides", ())}
        for path in policy.get("readable", ()):
            if os.path.exists(path):
                _add_rule(ruleset_fd, path, READ_ONLY & _handled(level))
        for path in policy.get("writable", ()):
            if not os.path.exists(path):
                continue
            # ⚠ A read-only override is granted READ_ONLY even though it sits
            # under a writable tree. Landlock takes the UNION of rules for a
            # path, so there is no "deny later" — the narrower grant has to be
            # the only one that names it, and the writable trees above never
            # name these two directly.
            access = READ_ONLY if path in readonly else _handled(level)
            _add_rule(ruleset_fd, path, access)
        for path in readonly:
            if os.path.exists(path):
                _add_rule(ruleset_fd, path, READ_ONLY & _handled(level))

        libc = _libc()
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError("prctl(PR_SET_NO_NEW_PRIVS) failed")
        result, err = _syscall(
            SYS_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0)
        )
        if result != 0:
            raise OSError(err, f"landlock_restrict_self: {os.strerror(err)}")
    finally:
        os.close(ruleset_fd)
    return level


def main(argv: list[str] | None = None) -> int:
    """`python -m rite_ai.managers.landlock <policy.json> -- <command...>`.

    Applies the boundary and then EXECS the command, so there is no wrapper
    process left holding privileges the engine does not have, and the engine
    keeps the pane's pid — which `record_instance` records and `rite status`
    reads.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in args or len(args) < 3:
        sys.stderr.write(
            "usage: python -m rite_ai.managers.landlock <policy.json> -- "
            "<command> [args...]\n"
        )
        return 2
    split = args.index("--")
    # Not `policy_path`: that is the function above, and shadowing it here
    # would silently break any later use in this function.
    policy_args, command = args[:split], args[split + 1 :]
    if len(policy_args) != 1 or not command:
        sys.stderr.write(
            "usage: python -m rite_ai.managers.landlock <policy.json> -- "
            "<command> [args...]\n"
        )
        return 2

    try:
        policy = json.loads(Path(policy_args[0]).read_text())
    except (OSError, ValueError) as exc:
        # ⚠ Loudly, and without running the command. A boundary that could
        # not be read is not a boundary, and launching anyway is how a
        # Manager ends up unconfined while everything reports fine.
        sys.stderr.write(f"rite: could not read the Manager boundary policy: {exc}\n")
        return 1

    try:
        apply(policy)
    except OSError as exc:
        sys.stderr.write(
            f"rite: could not apply the Manager boundary: {exc}\n"
            "rite refuses to start a Manager unconfined, so nothing was run.\n"
        )
        return 1

    try:
        os.execvp(command[0], command)
    except OSError as exc:  # pragma: no cover - exec replaces the process
        sys.stderr.write(
            f"rite: could not start {command[0]!r} inside the boundary: {exc}\n"
        )
        return 127


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
