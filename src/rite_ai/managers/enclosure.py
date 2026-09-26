"""The sandbox profile a Manager runs inside (B9, piece 1).

**Robert's decision: Managers get a boundary.** The allowlist is the secure
default for Claude and cannot hold for Goose, whose `GOOSE_MODE` is
whole-session with no per-command concept, so the sandbox is the only
engine-independent boundary rite has.

⚠ **READ `docs/design/spikes/B9-manager-sandboxing.md` BEFORE CHANGING
ANYTHING HERE.** Two measured facts shape this module and neither is
guessable:

1. **A sandboxed Manager cannot start a sandboxed Worker.** Inside a
   seatbelt sandbox only a SEMANTICALLY EQUIVALENT profile may be
   re-applied — a strictly narrower one is refused too — and two yoloAI
   sandbox profiles always differ, because each scopes its paths to its own
   id. So `yoloai` is deliberately NOT reachable from here, and a Manager
   asks the supervisor for a Worker instead.
2. **The shipped Worker profile already contains `(allow network*)`**, and
   so does this one. Seatbelt has no network isolation (D-30). That is a
   line in the file rather than a limitation rite might mitigate.

⚠ **WHAT THIS BOUNDARY IS, STATED HONESTLY.** It keeps the operator's home
outside the named paths unreadable — Documents, Desktop, SSH keys, browser
profiles, **and other rite projects on the same machine**. It does not
confine the network, and it cannot constrain an agent permitted to run
`rite`, because `rite` does what the operator can do.

**A real reduction in blast radius and an unreal reduction in capability.**

A boundary sold as more than it is would be worse than none, so
`limitations()` exists to be printed rather than to be inferred.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from rite_ai.managers import github_access, user_dir
from rite_ai.names import name_problem

PROFILE_SUFFIX = ".sb"
ENGINE_TMP_DIRNAME = "enginetmp"


def profile_path(root: Path, manager: str) -> Path:
    """Where this Manager's profile is written.

    Under `user_dir` with the instance records and the permission list:
    per-user, per-machine, never committed. What a Manager may reach on THIS
    machine is a local fact, and the absolute paths inside the profile make
    it meaningless anywhere else.
    """
    problem = name_problem(manager, kind="manager name", must_be_a_tmux_target=True)
    if problem:
        raise ValueError(f"refusing to build a profile path: {problem}")
    return user_dir(root) / f"{manager}{PROFILE_SUFFIX}"


ENGINE_HOME_IS_THE_OPERATORS = True
"""⚠ **`HOME` is NOT redirected, and this was measured rather than chosen.**

B4d recorded that Goose panics unless `HOME` points somewhere writable, and
the obvious reading is "give the engine its own HOME". Under THIS profile
that is both unnecessary and harmful:

- **Unnecessary.** Goose starts fine with the operator's `HOME`, because the
  profile grants `~/.config/goose` and `~/.local/share/goose` directly — it
  has to anyway, since break 1 made that store hold the conversation handle
  every resumed cycle names. What B4d actually found is "the engine's state
  paths must be writable"; redirection is one way to satisfy that and this
  profile satisfies it the other way. Under yoloAI's Worker profile, which
  grants no part of the operator's home, redirection was the only route and
  the panic was real.
- **Harmful.** Claude Code's login lives under `HOME`. Measured: with `HOME`
  redirected it answers *"Not logged in · Please run /login"*; with the
  operator's `HOME` it finds the stored login and proceeds. Redirecting
  would take the credential away from every existing Claude Manager.

⚠ **`TMPDIR` is still redirected** — see `engine_tmp`. That one is load-
bearing, and granting the system temp root instead was measured to leak
every other process's scratch.
"""


def engine_tmp(root: Path, manager: str) -> Path:
    """The `TMPDIR` the engine gets, and why the system one is not granted.

    ⚠ **Granting `$TMPDIR` was measured to be a real leak, not a
    theoretical one.** Goose writes `.tmpXXXX` directly in the per-user temp
    root while loading extensions, so the naive fix is to allow
    `/var/folders/<..>/T`. The check that was supposed to prove the profile
    denies another project's files then PASSED them: every other process's
    scratch on this machine lives in that same directory, and the Manager
    could read and write all of it.

    So the engine is given its own, inside the boundary, exactly as it is
    given its own `HOME`. The system temp root is not granted at all.
    """
    return user_dir(root) / ENGINE_TMP_DIRNAME / manager


def _quote(path: Path | str) -> str:
    """A path as a seatbelt string literal.

    Seatbelt profiles are S-expressions; a path containing `"` or `\\` would
    end the literal early and the rest would be read as policy. Refused
    rather than escaped, for the reason `session_id_problem` refuses a
    hostile resume id: this file is parsed by something that acts on it.
    """
    text = str(path)
    if '"' in text or "\\" in text:
        raise ValueError(
            f"refusing to put {text!r} in a sandbox profile: a quote or "
            "backslash would end the string literal and the remainder would "
            "be read as policy"
        )
    return f'"{text}"'


def _readable(paths) -> list[str]:
    return [f"(allow file-read* (subpath {_quote(p)}))" for p in paths]


def _writable(paths) -> list[str]:
    return [f"(allow file-read* file-write* (subpath {_quote(p)}))" for p in paths]


def _system_paths() -> tuple[Path, ...]:
    """Read-only system locations any process needs to start at all.

    The same set yoloAI grants a Worker. Not narrowed: a profile that cannot
    load `/usr/lib` does not deny an attacker anything, it just fails to run
    a program.
    """
    return tuple(
        Path(p)
        for p in (
            "/usr/lib",
            "/usr/bin",
            "/usr/sbin",
            "/usr/share",
            "/usr/local",
            "/bin",
            "/sbin",
            "/System",
            "/Library",
            "/private/etc",
            "/opt/homebrew",
            "/Applications",
            "/dev",
            # Resolver and daemon sockets. Measured: without this an HTTPS
            # request fails at the transport (curl exit 56) even though
            # `(allow network*)` is granted — the policy blocks the lookup
            # rather than the connection, which is the confusing shape.
            "/private/var/run",
            "/private/var/db",
        )
    )


def _tool_paths(home: Path) -> tuple[Path, ...]:
    """Where rite, the engines and their configuration live.

    ⚠ **`~/.yoloai` is NOT here, and that is the point of the whole design.**
    A Manager that could run `yoloai` would try to create a Worker sandbox
    from inside a sandbox, which the kernel refuses (B9) — so it would fail
    at the one job the Manager exists for, after appearing to start
    correctly. It asks the supervisor instead.
    """
    return tuple(
        home / p
        for p in (
            ".local/bin",
            # ⚠ ALL of uv's share directory, not just `tools`. rite is a uv
            # tool whose shebang points at an interpreter under
            # `.local/share/uv/python`, and that interpreter dlopens
            # `libpython3.13.dylib` from beside itself. Measured: granting
            # only `tools` gives `Abort trap: 6` and
            # `dyld: Library not loaded ... (file system sandbox blocked
            # open())` — a crash rather than a refusal, which is the worst
            # shape for a missing grant because it reads as a broken rite.
            ".local/share/uv",
            # ⚠ `~/.claude` and `~/.claude.json` are NOT granted any more
            # (SB4). They hold every project's Claude transcripts, and a
            # Claude Manager now signs in from a config directory of its own
            # (`claude_login`), measured to work with no `~/.claude` grant.
            ".config/goose",
            ".rite",
            ".gitconfig",
            ".config/git",
            # ⚠ **`gh` cannot START without this, not merely cannot
            # authenticate.** Measured 2026-09-25: inside the shipped
            # profile `gh api rate_limit` exits 1 with `failed to create
            # root command: failed to read configuration`, and a
            # `GITHUB_TOKEN` does NOT rescue it — gh reads its config
            # directory before it looks at any credential. That took the
            # GitHub board away from every sandboxed Manager, and from
            # `git push` over HTTPS, which uses gh as its credential
            # helper.
            #
            # ⚠ **Necessary, and NOT sufficient.** With this grant gh starts
            # and is ANONYMOUS — `rate_limit` returns 60 rather than the
            # authenticated 5000 — because its stored credential is in the
            # keychain, which it cannot reach from here. A `GITHUB_TOKEN`
            # then works: with a deliberately bogus one the API answered
            # **401 Bad credentials**, which is the could-not-read versus
            # rejected distinction and proves the route is live. How that
            # token reaches a Manager is the open half; see C26.
            ".config/gh",
        )
    )


def _running_rite() -> tuple[Path, ...]:
    """The environment of the rite composing this profile, READ-ONLY.

    ⚠ **The Manager's instructions name this rite by absolute path**
    (`own_command`, `c0e4097`), so the profile must let it run, whichever
    install that is. `_tool_paths` covers a `uv tool install` under
    `~/.local/share/uv` and nothing else. Measured 2026-09-25 at `8d5fc22`,
    with the instructions naming a checkout's venv rite: inside the profile it
    died with `PermissionError: … .venv/pyvenv.cfg`. So a Manager started from
    a dev checkout, or from any rite installed outside uv's tool directory,
    could not run `rite reply` at all. Before `c0e4097` it silently used
    whatever `rite` was first on PATH instead.

    Three roots, each only if it exists:
    * `sys.prefix` — the environment root, as `own_command` uses;
    * `sys.base_prefix` — the interpreter it was built from (a pyenv or
      framework python is not under a granted system path);
    * the directory `rite_ai` is imported FROM — an EDITABLE install puts it
      in the checkout (`src/`), outside the environment. The directory, not
      the package: measured, granting only `src/rite_ai` failed with `No
      module named 'rite_ai.cli'`, because the import system lists `src/`
      to find the package. For a normal install it is `site-packages`,
      already inside `sys.prefix`;
    * and, for that editable install only, the checkout's `VERSION` file —
      the ONE file outside `src/` it reads (`rite_ai.__version__`, SPEC
      §8.5). The file, not the checkout.

    Read-only: nothing here needs writing, and a writable copy of the code a
    Manager runs is one it could change for its next cycle.
    """
    import rite_ai

    package = Path(rite_ai.__file__).resolve().parent
    roots = [
        Path(sys.prefix),
        Path(sys.base_prefix),
        package.parent,
        package.parent.parent / "VERSION",
    ]
    return tuple(dict.fromkeys(r.resolve() for r in roots if r.exists()))


def _engine_state_paths(home: Path) -> tuple[Path, ...]:
    """Engine state the engine must WRITE, not merely read.

    Goose keeps its session store under `~/.local/share/goose`, and since
    break 1 that store holds the conversation handle every resumed cycle
    names. A Manager that cannot write it cannot continue its own work.
    """
    return (home / ".local/share/goose", home / ".local/state/goose")


def _manager_separation(project: Path, manager: str) -> list[str]:
    """Other Managers' state is not writable, and NO Manager's inbox is.

    ⚠ **An inbox write IS an instruction, so who may write one is authority,
    not tidiness.** The cycle's delivery note tells a Manager that a message
    with no bracketed line "was sent from this machine by the Owner, and is an
    instruction". Every Manager's profile granted the whole project tree, so
    measured on `main`: from inside Manager `helper`'s profile, a plain file
    write into `.rite/managers/lead/mail/in/` succeeded, and `lead` would have
    been told at its next cycle that the Owner said it. The same write into a
    Manager's OWN inbox promotes its own text to the Owner's the same way.

    So the writer is fenced, and the content is not trusted to say who wrote
    it: `.rite/managers/` is not writable except this Manager's own directory,
    and its own `mail/in` is refused inside that. Everything that legitimately
    writes an inbox runs OUTSIDE the boundary — the supervisor's Slack relay,
    the start-time drain, a person's `rite message`, and the Owner's routing,
    which the supervisor performs on the Owner's behalf. Measured precedence:
    the more specific rule named later wins, and a rename or a symlink into
    the inbox is refused as a write is.

    It is also §5.4.8's P1 for the one kind of state that carries authority:
    a wrong path join in one Manager cannot write another's files.
    """
    managers = project / ".rite" / "managers"
    own = managers / manager
    return [
        "; ⚠ MANAGERS ARE SEPARATED, and no Manager writes an inbox — see",
        ";   enclosure._manager_separation. Named after the project grant so",
        ";   they win.",
        f"(deny file-write* (subpath {_quote(managers)}))",
        f"(allow file-write* (subpath {_quote(own)}))",
        f"(deny file-write* (subpath {_quote(own / 'mail' / 'in')}))",
    ]


def _socket_denials(socket_dirs) -> list[str]:
    """Close the tmux escape, LAST, because seatbelt takes the last match.

    ⚠ **This is the hole the first profile shipped with.** The tmux server
    is a process outside the sandbox; talking to its socket asks that
    process to run a command, and it runs it unconfined. A Manager lives in
    tmux, so the socket is right there. Measured 2026-09-25 with the profile
    rite itself composes:

        touch ~/other/x                    direct        Operation not permitted
        tmux new-session -d 'touch ...'    through tmux  exit 0, FILE CREATED
        yoloai ls                          direct        refused
        tmux new-session -d 'yoloai ls'    through tmux  listed 9 sandboxes

    ⚠ **Denied at the END and in both spellings.** Seatbelt takes the LAST
    matching rule, and this profile grants `/tmp` and `/private/tmp`
    wholesale further up — a deny placed before those grants is overridden
    and the escape still works. Measured: denying the socket beside the
    network rule changed nothing at all. macOS resolves `/tmp` to
    `/private/tmp`, so both are named.

    ⚠ **Why not narrow the network instead**, which was the first idea:
    `(allow network*)` does grant the socket, and loopback-only blocks it —
    but it also blocks **every outside host**, which takes the ticket
    backend away from every Manager and the API away from a Claude one.
    Seatbelt cannot express a destination allowlist — it rejects any named
    host at profile load, *"host must be * or localhost"* — so there is no
    middle setting. Denying the socket keeps the network and closes the
    hole; the two are separable and it was worth checking rather than
    assuming.

    **What it costs: nothing a Manager needs.** Measured with the socket
    denied — `rite`, `rite status`, `git`, `gh`, `goose`, writes into the
    project and writes into the request directory all still work. The
    Manager's own pane is created by the supervisor OUTSIDE the sandbox, so
    the engine inside never needs to talk to the server that is running it.
    """
    lines = [
        "; ⚠ THE TMUX ESCAPE, closed here and nowhere else — see above.",
        "; Last, because seatbelt takes the last matching rule and the",
        "; temp grants further up would otherwise win.",
    ]
    for directory in dict.fromkeys(socket_dirs):
        lines.append(f"(deny network-outbound (subpath {_quote(directory)}))")
        lines.append(f"(deny file-read* file-write* (subpath {_quote(directory)}))")
    return lines


def compose(
    root: Path,
    manager: str,
    home: Path | None = None,
    tmux_tmpdir: str = "",
) -> str:
    """The seatbelt profile for one Manager.

    ⚠ **`(deny default)` with named allows**, the same shape yoloAI gives a
    Worker — so what is not enumerated is refused, and a path that turns out
    to be needed fails loudly rather than being quietly reachable.
    """
    where = Path(home) if home is not None else Path(os.path.expanduser("~"))
    project = Path(root).resolve()
    sockets = tmux_tmpdir or os.environ.get("TMUX_TMPDIR") or "/private/tmp"
    # ⚠ The engine gets its OWN temp directory, and the system one is not
    # granted. Goose writes `.tmpXXXX` in the per-user temp root while
    # loading extensions — without somewhere to do that it PANICS after
    # printing "goose is ready", so the session appears to start and then
    # dies. Granting `$TMPDIR` fixes that and opens every other process's
    # scratch on this machine; see `engine_tmp`.
    temps = [Path("/tmp"), Path("/private/tmp")]
    # ⚠ **The tmux socket directory is DENIED at the end of this profile,
    # not granted here.** It used to be granted, and that was the hole:
    # the tmux SERVER runs outside the profile, so a command sent through
    # it runs unconfined. Measured 2026-09-25 — a write the profile refused
    # directly succeeded via `tmux new-session`, and `yoloai ls`, refused
    # directly, listed every sandbox on the machine. See `_socket_denials`.
    # ⚠ The socket DIRECTORY, not the temp root. tmux puts its socket in
    # `<TMUX_TMPDIR>/tmux-<uid>/`, and `TMUX_TMPDIR` is unset by default —
    # which makes the temp root `/private/tmp`. Denying that would deny the
    # whole of `/private/tmp`, which this profile grants three lines up and
    # which rite's own worktrees live in.
    socket_dirs = [Path(sockets) / f"tmux-{os.getuid()}"]
    if not str(sockets).startswith("/private"):
        socket_dirs.append(
            Path("/private" + str(sockets).rstrip("/")) / f"tmux-{os.getuid()}"
        )
    lines = [
        "; Composed by rite for a Manager. Do not edit: it is rewritten every",
        "; run, so an edit here vanishes without reporting that it did.",
        "; What this does and does NOT buy:",
        ";   docs/design/spikes/B9-manager-sandboxing.md",
        "(version 1)",
        "(deny default)",
        "",
        "; Starting a process at all",
        "(allow process-exec)",
        "(allow process-fork)",
        "; ⚠ SAME-SANDBOX ONLY. A blanket `(allow signal)` let a Manager",
        "; kill any process this user owns — measured: it killed a process",
        "; outside the sandbox that it had not started, so one Manager could",
        "; end another, or the supervisor watching it. Its own children are",
        "; still signalable, which is what a Manager needs.",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
        '(allow file-read* (literal "/"))',
        "(allow mach-lookup)",
        "(allow ipc-posix-shm-read-data)",
        "(allow ipc-posix-shm-write-data)",
        "(allow ipc-posix-shm-write-create)",
        "(allow ipc-posix-sem)",
        "; ⚠ /dev/null must be WRITABLE, not just readable. Measured: without",
        "; this every `cmd >/dev/null` fails with `Operation not permitted`,",
        "; which is most of what a shell does and none of what a boundary is",
        "; for.",
        '(allow file-write* (subpath "/dev"))',
        "",
        "; ⚠ ALL NETWORK. Seatbelt has no network isolation (D-30), and a",
        "; Manager needs the ticket backend and the engine endpoint anyway.",
        "; The shipped Worker profile carries the same line.",
        "(allow network*)",
        "",
        "; System locations",
        *_readable(_system_paths()),
        "",
        "; The project this Manager manages — the WHOLE tree, because that is",
        "; what an orchestrator works on, unlike a Worker's one workspace.",
        *_writable([project]),
        "",
        "; Shared temporary space and the tmux socket this pane lives on",
        *_writable(dict.fromkeys(temps)),
        "",
        "; The engine's own TMPDIR, inside the boundary. HOME is the",
        "; operator's — see ENGINE_HOME_IS_THE_OPERATORS.",
        *_writable([engine_tmp(root, manager)]),
        "",
        "; rite itself, the engines, and their configuration",
        *_readable(_tool_paths(where)),
        "; ⚠ The rite composing this profile, wherever it is installed: the",
        "; instructions name it by absolute path, so it must be runnable here.",
        *_readable(_running_rite()),
        "",
        "; Engine state that must be written, including Goose's session store",
        *_writable(_engine_state_paths(where)),
        "",
        "; ⚠ Named last so they win: git identity is readable and NOT writable,",
        "; mirroring the Worker profile. An agent that can rewrite git config",
        "; can change what every later commit claims.",
        f"(deny file-write* (subpath {_quote(where / '.gitconfig')}))",
        f"(deny file-write* (subpath {_quote(where / '.config/git')}))",
        "",
        *_manager_separation(project, manager),
        "",
        "; ⚠ yoloAI is unreachable ON PURPOSE. A Manager cannot create a",
        "; sandbox from inside one (B9), so it asks the supervisor instead.",
        f"(deny file-read* file-write* (subpath {_quote(where / '.yoloai')}))",
        "",
        *_socket_denials(socket_dirs),
        "",
        # ⚠ LAST of all: the Unix-socket denial must come after
        # `(allow network*)`, and this Manager's own agent and credential
        # directory are allowed back after it (C6/C26, `github_access`).
        *github_access.profile_lines(root, manager, home),
    ]
    return "\n".join(lines)


def write_profile(root: Path, manager: str, home: Path | None = None) -> Path:
    """Write the profile and the engine's HOME, and return the profile path.

    ⚠ **Rewritten every run**, for the reason `permissions.write_settings`
    is: a write-once file would pin a project to whatever shipped the day it
    was created, so widening or narrowing the surface in a later release
    would reach new projects only.
    """
    path = profile_path(root, manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine_tmp(root, manager).mkdir(parents=True, exist_ok=True)
    path.write_text(compose(root, manager, home) + "\n")
    return path


def limitations() -> tuple[str, ...]:
    """What this boundary does NOT do, to be PRINTED rather than inferred.

    ⚠ **A boundary sold as more than it is would be worse than none**, which
    is why these are a value rite says out loud rather than a paragraph in a
    design note nobody reads at 2am.

    ⚠ **The history here is the reason to distrust any confident version of
    this list.** It first claimed other projects were NOT reachable. That
    shipped, and it was false — the tmux server runs outside the profile, a
    Manager lives in tmux, and a command sent through it ran unconfined. The
    claim was corrected first and the hole closed second, because a false
    claim is what misleads somebody today.

    **The holes are now closed and the wording is still hedged**, on
    purpose: "these routes were tried and refused" is a statement about what
    was measured. "It is contained" would be a statement about every route
    that exists, which nobody here has established.
    """
    return (
        "⚠ THIS BOUNDS FILES, NOT CAPABILITY, and it is not a proof of "
        "containment — it is a set of holes that were looked for and "
        "closed. Two were found and closed on 2026-09-25: reaching the "
        "tmux server, which runs outside the profile and would run anything "
        "you sent it unconfined, and signalling processes outside the "
        "sandbox. Both were measured before and after",
        "/tmp and /private/tmp are readable and writable, so anything kept "
        "there — including other rite worktrees — is reachable",
        "the network is NOT confined — seatbelt has no network isolation, so "
        "a Manager can reach anything this machine can",
        "a Manager can run `rite`, which does whatever you can do to this "
        "project — the sandbox bounds the filesystem, not that",
        "a Claude Manager has a Claude config directory of its own, so your "
        "personal Claude Code settings, hooks and MCP servers do NOT load "
        "into it, and it signs in with its own copy of claude_token rather "
        "than your keychain login",
        "ticket text from your board reaches the engine as instructions; the "
        "sandbox limits what acting on it can touch, it does not vet it",
        "what it DOES buy: your home outside the paths above, your SSH keys, "
        "and other projects outside /tmp are not reachable — direct access "
        "and the tmux route were both tried and both refused",
    )


def wrap(command: str, profile: Path) -> str:
    """Put `command` inside the sandbox.

    ⚠ **Prefixed rather than rebuilt.** The command already carries the
    engine's own vocabulary and, for Claude, a stdin redirection from the
    prompt file. The shell tmux runs this through applies the redirection
    around the whole thing, so prefixing keeps both halves correct without
    this module knowing anything about either.
    """
    return f"sandbox-exec -f {shlex.quote(str(profile))} {command}"


def refusal_looks_like_ours(pane_text: str) -> bool:
    """Did something in the pane fail because THIS boundary refused it?

    ⚠ **Because it will read as rite being broken, and it is not.** The
    operator's own Claude Code hooks still load inside the sandbox — `HOME`
    is deliberately not redirected, so the login keeps working — and a hook
    that runs something outside the profile fails inside the boundary where
    it worked outside. Found by hitting it: a `SessionEnd` hook pointing at
    a path the profile does not grant.

    Matched to DETECT, and the pane is never relayed — the rule
    `_authentication_looks_broken` follows, because a pane can carry
    secrets.
    """
    low = (pane_text or "").lower()
    return "operation not permitted" in low or "sandbox blocked" in low


def why_it_was_refused(root: Path, manager: str) -> str:
    """What to tell a user whose hook or tool just failed inside the box.

    Points at the boundary rather than leaving them to guess, and says the
    two things they can do about it.
    """
    return (
        f"something in Manager {manager!r} was refused by the sandbox rite "
        f"runs it in — that is this boundary, not a broken rite. The profile "
        f"is at {profile_path(root, manager)} and lists every path the "
        f"Manager may reach.\n"
        "  A Claude Manager no longer loads your own Claude Code hooks, so a "
        "hook is not the likely cause for one. A tool or script the Manager "
        "ran that reaches outside the project is.\n"
        "  What this boundary does and does not buy: "
        "docs/design/spikes/B9-manager-sandboxing.md"
    )
