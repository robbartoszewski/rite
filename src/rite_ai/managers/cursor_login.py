"""How a sandboxed Cursor Manager signs in: a per-Manager key file, read by the
pane's shell OUTSIDE the boundary, into the engine's own environment (CU4).

**Why not a file Cursor reads itself.** Cursor's `agent` takes a credential in
exactly two ways: argv (`--api-key`, a hidden `--auth-token`) or the
environment (`CURSOR_API_KEY`, `CURSOR_AUTH_TOKEN`). Read from its bundle,
`2026.09.26-dd393fe`: `--auth-token-file` exists for `agent worker` only, and
the stored-credential route is the macOS keychain, which a sandboxed Manager
cannot read (measured, `spikes/CU1-cursor-cli.md` section 4). argv is
readable by every local account, so the environment of the one process that
needs it is the narrowest place there is.

🔴 **The path the key takes.**
- `cursor_api_key` lives in rite's 0600 file store, which no Manager can read.
- At `rite start` the supervisor, outside the boundary, writes a copy to
  `<this Manager's credential directory>/cursor.key`, mode 0600, in a 0700
  directory. **No profile grants that file**, so a Manager can neither read
  nor replace it.
- Each cycle's pane command is
  `CURSOR_API_KEY="$(cat <that file>)" <boundary> agent -p ...`
  (`launch_prefix`). tmux's shell expands it BEFORE the boundary starts, the
  same way it applies the prompt's `<` redirection. The command text, and so
  tmux's argv, holds the path. The key is in the environment of `agent`
  alone, not the pane's (`tmux -e`) and not tmux's.
- The copy is removed when the run ends (`remove_login`). A copy left by a
  killed run is removed, and said, by the next `prepare`, under the run lock.

**Stated, not reassuring:** the engine's own children (the commands a Manager
runs) inherit its environment, so a compromised Manager can read the key and
use it until it is revoked. That is the same reach a Claude Manager has over
its token file, and the cost of a Manager calling the model at all.

**Cursor's state is per-Manager too.** `CURSOR_CONFIG_DIR` (config and
`chats/`) and `CURSOR_DATA_DIR` (`projects/`: the trust marker and a second
transcript) both point at `<credential directory>/cursor`, so `~/.cursor`,
which holds every project's Cursor transcripts, is granted to nobody.
`CURSOR_DATA_DIR` is read from the bundle; with a credential, where the trust
marker lands under it is NOT yet measured (held with the key; see the plan's
CU4 row).

⚠ **One path this sends to `/tmp`.** Where `CURSOR_DATA_DIR/projects` is
longer than 84 characters, Cursor puts its socket directory under
`/tmp/.cursor/` instead (read from the bundle, and measured: a run created
`/tmp/.cursor/<slug>-<hash>/`). A Manager's credential directory is always
that long, so this depends on the Manager being able to write `/tmp`, which it
can today (SB8). Narrowing `/tmp` must keep `/tmp/.cursor` or break Cursor.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from rite_ai.state import write_atomic

KEY_ENV = "CURSOR_API_KEY"
"""Where Cursor reads its key. Named once; it must never be passed with
`tmux -e`, which would put the value on tmux's argv."""


def _credential_dir(root: Path, manager: str, home: Path | None = None) -> Path:
    from rite_ai.managers.github_access import _credential_dir as cdir

    return cdir(root, manager, home)


def state_dir(root: Path, manager: str, home: Path | None = None) -> Path:
    """This Manager's Cursor directory: `CURSOR_CONFIG_DIR` and
    `CURSOR_DATA_DIR` both. The same path as `cursor_chat.config_dir`, which
    reads the chats in it."""
    return _credential_dir(root, manager, home) / "cursor"


def _key_path(root: Path, manager: str, home: Path | None = None) -> Path:
    """The key's copy: BESIDE the state directory, not in it. The state
    directory is Manager-writable; this file is granted to no profile."""
    return _credential_dir(root, manager, home) / "cursor.key"


def _write_key(root: Path, manager: str, key: str, home: Path | None = None) -> Path:
    d = _credential_dir(root, manager, home)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    state = state_dir(root, manager, home)
    state.mkdir(exist_ok=True)
    os.chmod(state, 0o700)
    path = _key_path(root, manager, home)
    # Stripped: `rite credential set --stdin` stores a trailing newline, and
    # `$(cat ...)` would drop it anyway, but the file should hold the key.
    write_atomic(path, key.strip())
    os.chmod(path, 0o600)
    return path


def prepare(root: Path, manager: str, key: str | None, say=None) -> str:
    """Write this Manager's key copy, or say why a Cursor Manager cannot start.

    Returns a REFUSAL, or "". Without a key Cursor would start and exit 1
    with "Authentication required" in its pane, which reads as a broken
    rite; it is refused here instead.

    ⚠ **A copy already here was left by a run that did not exit cleanly.**
    Removed and SAID, as `claude_login.prepare` does, for the same reason and
    under the same condition: `rite start` holds this Manager's run lock
    (`github_access.hold_run`) before calling this, so no live run owns it.
    """
    stale = _key_path(root, manager)
    if stale.is_file():
        stale.unlink()
        (say or (lambda _: None))(
            f"cursor: removed a key copy left at {stale} by a run of Manager "
            f"{manager!r} that did not exit cleanly (killed, or the machine "
            "stopped), rather than one that ended. Nothing was using it."
        )
    if not key or not key.strip():
        return (
            "a Cursor Manager runs inside a sandbox, which cannot use the "
            "login your own `agent` uses (measured), so it needs an API key. "
            "Store one with `rite credential set cursor_api_key --stdin < "
            "<file>`"
        )
    _write_key(root, manager, key)
    return ""


def remove_login(root: Path, manager: str, home: Path | None = None) -> None:
    """End of the run: the key goes; the chats and config stay."""
    _key_path(root, manager, home).unlink(missing_ok=True)


def launch_prefix(root: Path, manager: str, home: Path | None = None) -> str:
    """What goes IN FRONT of the boundary in the pane's command, or "".

    "" when no copy is written, so a Manager that is not Cursor, or a run
    that never prepared one, gets nothing. A copy removed while a run is
    live makes `cat` fail, the variable empty, and Cursor refuse loudly
    ("Authentication required"), never a silent unauthenticated run.
    """
    path = _key_path(root, manager, home)
    if not path.is_file():
        return ""
    return f'{KEY_ENV}="$(cat {shlex.quote(str(path))})" '


def pane_environment(root: Path, manager: str, home: Path | None = None) -> dict:
    """PATHS for the pane (`tmux -e`), when this Manager has a key copy. The
    key itself never travels this way."""
    if not _key_path(root, manager, home).is_file():
        return {}
    d = str(state_dir(root, manager, home))
    return {"CURSOR_CONFIG_DIR": d, "CURSOR_DATA_DIR": d}


def live_secrets() -> list[str]:
    """The key, for EXACT-VALUE redaction, read from inside the Manager's
    own environment (where `rite journal` runs), or []."""
    key = os.environ.get(KEY_ENV, "").strip()
    return [key] if key else []


def manager_secrets(root: Path, manager: str, home: Path | None = None) -> list[str]:
    """One Manager's key, read from OUTSIDE the sandbox, for the Slack relay,
    which runs in the supervisor."""
    try:
        key = _key_path(root, manager, home).read_text().strip()
    except OSError:
        return []
    return [key] if key else []


# --- CU8: the allowlist, owned by rite -----------------------------------------

CONFIG_NAME = "cli-config.json"
"""Cursor's config file in its `CURSOR_CONFIG_DIR`. It holds the allowlist
(`permissions`), `approvalMode` and `attribution`."""

_OWNED_KEYS = ("permissions", "approvalMode", "attribution")
"""The keys rite decides. Cursor rewrites the file on every run, keeping these
and dropping keys it does not know (measured 2026-09-28, no credential), so
only these are compared."""

REAP_SUFFIX = ' ; s=$? ; trap "" TERM ; kill -TERM 0 2>/dev/null ; exit $s'
"""CU7, appended to a Cursor pane's command. The pane's shell is its process
group's leader (tmux starts each pane in a session of its own), and because
the engine is no longer the last command the shell does not exec into it, so
it stays alive. After the engine exits it ignores TERM itself, signals its
own group, and exits with the ENGINE's status, which is what `ending` reads.

⚠ **Why this and not a lookup and a kill.** A pid that exists is not a pid
that is yours: between finding `worker-server` and signalling it, it can exit
and its number be reused. A live leader's group id cannot be recycled, so
`kill 0` from the leader reaches its own group and nothing else.

⚠ **What this rests on, not yet observed:** that Cursor's `worker-server`
stays in the engine's process group. The bundle spawns it with
`detached: false`, which keeps it there unless it leaves by itself, and no
`worker-server` starts without an authenticated turn, which is held. Tested
against a stand-in that spawns a child exactly that way."""


def _cursor_allowlist(allow: tuple[str, ...] | None = None) -> list[str]:
    """Claude's allowlist in Cursor's vocabulary: `Bash(x:*)` is `Shell(x)`.

    Claude's own tool names (`Read`, `Edit`, ...) have no Cursor entry:
    Cursor's file tools ran under `--trust` with no allowlist entry (CU1
    section 5), so they are skipped by NAME, never silently. ⚠ Anything else is
    REFUSED: an entry this cannot translate would otherwise vanish, and a
    Manager would run with a narrower or wider list than rite's without a
    word. Whether Cursor enforces `Shell(x)` as Claude enforces `Bash(x:*)`
    needs an authenticated turn, and is held.
    """
    from rite_ai.managers.permissions import DEFAULT_ALLOW, TOOL_ALLOW

    out: list[str] = []
    for entry in DEFAULT_ALLOW if allow is None else allow:
        if entry in TOOL_ALLOW:
            continue
        if entry.startswith("Bash(") and entry.endswith(":*)"):
            command = entry[len("Bash(") : -len(":*)")]
            if command and all(c.isalnum() or c in "-_.+" for c in command):
                out.append(f"Shell({command})")
                continue
        raise ValueError(
            f"allowlist entry {entry!r} has no Cursor spelling rite knows; "
            "refused rather than dropped"
        )
    return out


def rite_config() -> dict:
    """What rite writes into a Cursor Manager's config, and later checks."""
    return {
        "version": 1,
        "permissions": {"allow": _cursor_allowlist(), "deny": []},
        "approvalMode": "allowlist",
        # Robert's commits are not attributed to a tool by default. Cursor
        # passes this to the model, which is what adds the attribution; it
        # installs no git hook (read from the bundle).
        "attribution": {"attributeCommitsToAgent": False, "attributePRsToAgent": False},
    }


def write_config(root: Path, manager: str, home: Path | None = None) -> Path:
    """Write rite's config, fresh, before every launch, from OUTSIDE the
    boundary. The Manager's profile denies writing it on macOS."""
    import json

    state = state_dir(root, manager, home)
    state.mkdir(parents=True, exist_ok=True)
    path = state / CONFIG_NAME
    write_atomic(path, json.dumps(rite_config(), indent=2) + "\n")
    return path


def config_problem(root: Path, manager: str, home: Path | None = None) -> str:
    """Why the config on disk is not the allowlist rite wrote, or "".

    Missing or unreadable is a problem, never "fine": a config nobody can
    read is one nobody can vouch for.
    """
    import json

    path = state_dir(root, manager, home) / CONFIG_NAME
    try:
        on_disk = json.loads(path.read_text())
    except (OSError, ValueError) as err:
        return f"Cursor's allowlist at {path} could not be read back ({err})"
    wanted = rite_config()
    changed = [
        k
        for k in _OWNED_KEYS
        if not isinstance(on_disk, dict) or on_disk.get(k) != wanted[k]
    ]
    if changed:
        return (
            f"Cursor's allowlist at {path} is not the one rite wrote "
            f"({', '.join(changed)} changed during the cycle)"
        )
    return ""


def announcement(manager: str) -> str:
    """Said every run, so the operator sees what the Manager's permission is
    and where it does not hold."""
    import sys

    where = (
        "the Manager's sandbox cannot write it"
        if sys.platform == "darwin"
        else "⚠ the Manager CAN write it: Cursor rewrites this file itself on "
        "every turn (a temp file created beside it and renamed over it) and "
        "stops when it cannot, so no sandbox rule can protect it without "
        "stopping Cursor. rite checks it after every cycle and stops on a "
        "change; a change restored before that check is not caught. Accepted "
        "by the operator as a limitation of Cursor"
    )
    return (
        f"permissions: Manager {manager!r} runs Cursor with rite's allowlist, "
        f"written to its {CONFIG_NAME} before every launch; {where}. Commit "
        "attribution to Cursor is off."
    )
