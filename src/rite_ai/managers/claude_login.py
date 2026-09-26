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
import time
from pathlib import Path

from rite_ai.state import write_atomic

LIFETIME_SECONDS = 365 * 24 * 3600
"""`claude setup-token` mints a one-year token (Claude Code's documentation).
rite does not know when it was minted, so the expiry written is a year from
now. Claude reports an expired token itself, loudly (401)."""


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

    Only the fields Claude Code was measured to need: the token, an expiry
    and the scope. No plan name and no refresh token: rite knows neither, and
    a file claiming a plan would be a claim rite cannot check.
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
                    "expiresAt": int((time.time() + LIFETIME_SECONDS) * 1000),
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


def prepare(root: Path, manager: str, token: str | None) -> str:
    """Write this Manager's login, or say why a Claude Manager cannot start.

    Returns a REFUSAL, or "". A Claude Manager with no `claude_token` would
    start and then print `Not logged in` inside its pane, which reads as a
    broken rite; it is refused here instead, with the two commands that fix it.
    """
    if not token:
        return (
            "a Claude Manager runs inside a sandbox, which cannot read your "
            "keychain login, so it needs a token of its own. Run "
            "`claude setup-token`, then `rite credential set claude_token` "
            "and paste what it printed"
        )
    _write_login(root, manager, token)
    return ""
