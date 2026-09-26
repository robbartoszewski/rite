"""How a sandboxed Claude Manager signs in: a per-Manager file, never the keychain.

**The blocker this fixes, measured 2026-09-26.** Inside the Manager profile,
`claude -p` answered `Not logged in · Please run /login`: Claude Code keeps its
login in the macOS keychain, and the profile cannot read
`~/Library/Keychains`. Granting that would grant the keychain FILE, every item
in it, rite's own GitHub and Slack tokens included, because seatbelt cannot
scope to one item.

**The route, measured the same day with a fake token (no secret involved):**
`CLAUDE_CONFIG_DIR` points Claude Code at a directory of its own, and it
reads `.credentials.json` there. Inside the real profile, with that one
directory granted and NO `~/.claude` grant at all, the fake token reached
Anthropic (`401 OAuth access token is invalid`, where the same profile
without it says `Not logged in`). So a real token signs in, no keychain grant
is needed, and `~/.claude`, which holds every project's transcripts (SB4),
stops being granted.

🔴 **The path the credential takes.** `claude_token` (from `claude
setup-token`: `user:inference` scope, one year) lives in rite's 0600 file
store, which no Manager can read. At `rite start` the supervisor, outside the
boundary, writes a copy into THIS Manager's directory, mode 0600, which only
this Manager's profile grants. The pane's environment carries
`CLAUDE_CONFIG_DIR=<that directory>`, which is a path. The copy is removed
when the run ends.

⚠ **A run that is killed does not remove it**, and this is a one-year token,
so "it expires soon anyway" (true of the one-hour GitHub token) does not
apply. The next `rite start` for this Manager, holding the run lock
(`github_access.hold_run`), finds any copy still there, knows no live run
owns it, removes it and SAYS so (`prepare`).

**Stated, not reassuring:** a compromised Manager can read that file, copy the
token out, and make model requests on the subscription until the token
expires or is revoked. That is the cost of a Manager being able to call the
model at all. The credential broker (v0.7.0) is the declared way past it.

**What moves with it:** Claude writes its transcripts to
`<this directory>/projects/`, so rite's readers (resume, the C8 check,
refused commands) look there for this Manager (`projects_dir`). A session
designated before this lived under `~/.claude` and cannot be resumed from
here, so the first run after upgrading starts a fresh conversation, which is
said.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from rite_ai.state import write_atomic


def _config_dir(root: Path, manager: str, home: Path | None = None) -> Path:
    """This Manager's `CLAUDE_CONFIG_DIR`: beside its GitHub token, under no
    path any other profile grants."""
    from rite_ai.managers.github_access import _credential_dir

    return _credential_dir(root, manager, home) / "claude"


def projects_dir(root: Path, manager: str, home: Path | None = None) -> Path | None:
    """Where THIS Manager's Claude transcripts are, or None when it has no
    config directory of its own (a local engine, or before this ran)."""
    d = _config_dir(root, manager, home)
    return d / "projects" if (d / ".credentials.json").is_file() else None


def pane_environment(root: Path, manager: str, home: Path | None = None) -> dict:
    """`CLAUDE_CONFIG_DIR`, a path, when this Manager has a login written."""
    d = _config_dir(root, manager, home)
    return {"CLAUDE_CONFIG_DIR": str(d)} if (d / ".credentials.json").is_file() else {}


def _write_login(
    root: Path, manager: str, token: str, home: Path | None = None
) -> Path:
    """This Manager's `.credentials.json`, 0600, in a 0700 directory.

    Only the token and the scope. No plan name, no refresh token and ⚠ **no
    expiry**: rite knows none of them, and each would be a claim written into
    the file that rite cannot check. An expiry used to be written as "a year
    from now", which overstates a token minted months ago. Measured
    2026-09-26: with a fake token, Claude Code answered `401 OAuth access
    token is invalid` with and without `expiresAt`, so it reads the file
    either way and the field bought nothing. A real expired token is
    reported by Claude itself, loudly (401).
    """
    d = _config_dir(root, manager, home)
    d.mkdir(parents=True, exist_ok=True)
    for p in (d, d.parent):
        os.chmod(p, 0o700)
    path = d / ".credentials.json"
    write_atomic(
        path,
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": token.strip(),
                    "scopes": ["user:inference"],
                }
            }
        ),
    )
    os.chmod(path, 0o600)
    return path


def remove_login(root: Path, manager: str, home: Path | None = None) -> None:
    """End of the run: the credential goes, the transcripts stay."""
    (_config_dir(root, manager, home) / ".credentials.json").unlink(missing_ok=True)


def live_secrets(config_dir: str | os.PathLike | None = None) -> list[str]:
    """The login's token, for EXACT-VALUE redaction, or [].

    Measured 2026-09-26: an observation quoting `.credentials.json` reached
    the journal verbatim, because the structural rule does not recognise
    `"accessToken":"<t>"`. The Manager can read its own login, so it can
    print it. Read from `$CLAUDE_CONFIG_DIR` when not given, which is how a
    Manager's own `rite journal` finds it from inside the sandbox.
    """
    d = config_dir or os.environ.get("CLAUDE_CONFIG_DIR", "")
    if not d:
        return []
    try:
        body = json.loads((Path(d) / ".credentials.json").read_text())
        token = body["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [token] if isinstance(token, str) and token else []


def manager_secrets(root: Path, manager: str, home: Path | None = None) -> list[str]:
    """One Manager's login token, read from OUTSIDE the sandbox, for the
    Slack relay, which runs in the supervisor."""
    return live_secrets(_config_dir(root, manager, home))


def prepare(root: Path, manager: str, token: str | None, say=None) -> str:
    """Write this Manager's login, or say why a Claude Manager cannot start.

    Returns a REFUSAL, or "". A Claude Manager with no `claude_token` would
    start and then print `Not logged in` inside its pane, which reads as a
    broken rite; it is refused here instead, with the two commands that fix it.

    ⚠ **A login already here was left by a run that did not exit cleanly.**
    The copy is removed in a `finally`, which a killed process skips, and the
    token lives a year. The next start is the only moment that reliably
    happens, so it is removed here, and SAID: the line matters as much as the
    unlink. A leftover is a small problem; not noticing it is the real one,
    and the line tells the operator their Manager was killed rather than
    exited. Safe only because `rite start` holds this Manager's run lock
    (`github_access.hold_run`) before calling this, so no live run owns it.
    **Not covered:** a Manager killed and never started again leaves the file
    indefinitely. A `doctor` row listing copies with no live run would close
    that.
    """
    stale = _config_dir(root, manager) / ".credentials.json"
    if stale.is_file():
        stale.unlink()
        (say or (lambda _: None))(
            f"claude: removed a login copy left at {stale} by a run of Manager "
            f"{manager!r} that did not exit cleanly (killed, or the machine "
            "stopped), rather than one that ended. Nothing was using it."
        )
    if not token:
        return (
            "a Claude Manager runs inside a sandbox, which cannot use the "
            "login your own `claude` uses, so it needs a token of its own. Run "
            "`claude setup-token`, then `rite credential set claude_token` "
            "and paste what it printed"
        )
    _write_login(root, manager, token)
    return ""
