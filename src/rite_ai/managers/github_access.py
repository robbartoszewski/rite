"""GitHub credentials for a sandboxed Manager: a short-lived token, in a file.

C6/C26, decided 2026-09-26 (Robert): **a repository-scoped, short-lived token,
minted outside the sandbox, and nothing else.** An SSH agent grant was
considered and DROPPED. The token already serves `git push` over HTTPS as well
as `gh`, so SSH added no capability, and an agent key reaches every repository
and host its owner can, which is the broader grant. The design and every
measurement behind it are in `docs/design/V060_MANAGER_CREDENTIALS.md`. Read
that before changing anything here.

🔴 **THE PATH THE CREDENTIAL TAKES, and the paths it never takes.** A value
passed as `tmux -e NAME=value` lands on tmux's argv, and `ps` shows it to every
local account. The environment is as bad. So:

- **The GitHub App's private key** is read from rite's credential store (the
  keychain) by THIS process, outside the sandbox. It reaches `openssl` through
  a pipe (`/dev/fd/N`), never a file or argv. The sandbox cannot read the
  keychain (measured), so a Manager cannot mint its own tokens.
- **The installation token** (one hour, only the repository named, only the
  permissions named) is written to `hosts.yml` in a per-Manager `gh` config
  directory, mode 0600, OUTSIDE every path the profile grants. The profile
  then grants that one directory read-only. The pane's environment carries
  `GH_CONFIG_DIR=<that directory>`, which is a path.
- **`git push` over HTTPS** uses the same token through `gh auth
  git-credential`, named by `GIT_CONFIG_*` variables. They carry the helper's
  NAME, not a secret, and they reset the helper list first, so the system's
  `osxkeychain` helper is never asked.

**What an attacker inside a compromised Manager gets**, stated as it is:
read and write on the named repository, for at most an hour after the last
refresh, including force-pushing any unprotected branch. A token copied out
works from anywhere until it expires. Nothing else: not other repositories,
and not the means to mint another.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from rite_ai.state import write_atomic

REFRESH_MARGIN_SECONDS = 10 * 60
"""Mint again when less than this remains. An installation token lives one
hour (GitHub's documentation), so a cycle never runs on a token about to
lapse under it."""

TOKEN_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "metadata": "read",
}
"""Asked for EXPLICITLY at every mint, so the token is no wider than this even
if the App itself was given more. No `workflows` (a Manager must not rewrite CI)
and no `secrets`. Measured need: `rite board` runs `gh issue …`, label changes
via `gh api`, and `gh search issues` (issues); a push needs contents; a Manager
opening a pull request needs pull_requests."""

GITHUB_API = "https://api.github.com"
RESOLVER_SOCKET = "/private/var/run/mDNSResponder"
"""The one Unix socket every Manager keeps. Measured: without it, name
resolution fails (curl exit 6), so no host can be reached by name."""


def _credential_root(home: Path | None = None) -> Path:
    """Where token files live: under no path any Manager profile grants.

    ⚠ NOT under the project and NOT under `~/.rite`. Both are granted to every
    Manager of a project (the project read+write, `~/.rite` read), so a token
    there would be readable by every sibling Manager, which is §5.4.8's P1 by
    another road.
    """
    base = Path(home) if home is not None else Path.home()
    return base / "Library" / "Application Support" / "rite" / "managers"


def _credential_dir(root: Path, manager: str, home: Path | None = None) -> Path:
    """This Manager's own directory for credential files. Resolved."""
    from rite_ai.managers.session import session_name

    return (_credential_root(home) / session_name(root, manager)).resolve()


def _gh_dir(root: Path, manager: str, home: Path | None = None) -> Path:
    return _credential_dir(root, manager, home) / "gh"


# --- what the launch needs, derived from what is on disk -------------------


def pane_environment(root: Path, manager: str, home: Path | None = None) -> dict:
    """The NON-SECRET variables a pane needs, or {} when nothing is set up.

    Derived from what `open_access` left on disk, not passed in: the launch
    site has dropped three passed-in arguments in two days. Every value is a
    path or a helper's name, which is why each is on `ALLOWED_ON_TMUX_ARGV`.
    """
    env: dict[str, str] = {}
    g = _gh_dir(root, manager, home)
    if (g / "hosts.yml").is_file():
        env["GH_CONFIG_DIR"] = str(g)
        # Reset the helper list first (an empty value does that), so the
        # system gitconfig's `osxkeychain` is never asked, then name gh.
        env.update(
            {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
                "GIT_CONFIG_VALUE_0": "",
                "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
                "GIT_CONFIG_VALUE_1": "!gh auth git-credential",
            }
        )
    return env


def _allow_socket(path: str) -> str:
    return f'(allow network-outbound (remote unix-socket (path-literal "{path}")))'


def profile_lines(root: Path, manager: str, home: Path | None = None) -> list[str]:
    """What the Manager's profile adds for credentials. Appended LAST.

    ⚠ **Every Manager gets the Unix-socket denial, credentials or not.**
    Measured 2026-09-26: `(allow network*)` let a sandboxed Manager connect to
    ANY Unix socket, including the operator's own SSH agent, because on macOS
    file rules do not govern `connect(2)`. Only `network-outbound` does. So
    every Unix socket is denied, and the resolver alone is allowed back, by
    exact path. Measured: without it, name resolution fails (curl exit 6).
    """
    lines = [
        "; ⚠ UNIX SOCKETS: none, except the name resolver. `(allow network*)`",
        "; above allowed every socket on the machine, including the operator's",
        "; SSH agent (measured). File rules do not govern connect(2); only",
        "; network-outbound does.",
        "(deny network-outbound (remote unix-socket))",
        _allow_socket(RESOLVER_SOCKET),
    ]
    # ⚠ rite's own credential store (every project's secrets, the App key
    # included) is under no granted path. It is denied here BY NAME as well,
    # so a later, wider grant cannot reach it by accident.
    from rite_ai.credentials.file_store import store_path

    lines.append(
        f'(deny file-read* file-write* (subpath "{store_path().parent.resolve()}"))'
    )
    cdir = _credential_dir(root, manager, home)
    if cdir.is_dir():
        lines += [
            "; This Manager's credential files, READ-ONLY. Written from outside.",
            f'(allow file-read* (subpath "{cdir}"))',
        ]
    return lines


# --- minting -----------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """The JWT GitHub requires to mint an installation token (RS256).

    Signed by the system `openssl`, with the key handed over on a PIPE
    (`/dev/fd/N`). It is never written to a file and never on argv.
    """
    issued = int(now if now is not None else time.time()) - 60
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps({"iat": issued, "exp": issued + 540, "iss": str(app_id)}).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, private_key_pem.encode())
    finally:
        os.close(write_fd)
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{read_fd}"],
            input=signing_input,
            capture_output=True,
            pass_fds=(read_fd,),
            timeout=30,
        )
    finally:
        os.close(read_fd)
    if proc.returncode != 0 or not proc.stdout:
        # openssl's own words, which name no key material.
        raise _MintError(
            "openssl could not sign with the stored GitHub App key: "
            + (proc.stderr.decode(errors="replace").strip() or "no output")
        )
    return f"{header}.{payload}.{_b64url(proc.stdout)}"


class _MintError(Exception):
    """GitHub, or the key, said no. Carries their words for the operator."""


@dataclass
class Minted:
    token: str
    expires_at: float
    repositories: list[str] = field(default_factory=list)


def _mint(
    app_id: str,
    installation_id: str,
    private_key_pem: str,
    repositories: list[str],
    *,
    post=None,
    now: float | None = None,
) -> Minted:
    """Mint a token for `repositories` only, with `TOKEN_PERMISSIONS` only."""
    import datetime

    jwt = _app_jwt(app_id, private_key_pem, now)
    names = [r.split("/", 1)[-1] for r in repositories]
    body = {"repositories": names, "permissions": TOKEN_PERMISSIONS}
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if post is None:
        import httpx

        def post(u, h, b):
            r = httpx.post(u, headers=h, json=b, timeout=30)
            return r.status_code, r.text

    status, text = post(url, headers, body)
    if status != 201:
        try:
            message = json.loads(text).get("message", text)
        except ValueError:
            message = text
        raise _MintError(f"GitHub refused to mint a token (HTTP {status}): {message}")
    data = json.loads(text)
    expires = datetime.datetime.fromisoformat(
        data["expires_at"].replace("Z", "+00:00")
    ).timestamp()
    return Minted(token=data["token"], expires_at=expires, repositories=repositories)


def _write_token(
    root: Path, manager: str, token: str, home: Path | None = None
) -> Path:
    """`hosts.yml` for `gh`, 0600, in a 0700 directory, replaced atomically.

    `gh` reads it on every invocation, so a refreshed token takes effect at
    the Manager's next command with nothing restarted.
    """
    g = _gh_dir(root, manager, home)
    g.mkdir(parents=True, exist_ok=True)
    for d in (g, g.parent):
        os.chmod(d, 0o700)
    path = g / "hosts.yml"
    write_atomic(
        path,
        "github.com:\n"
        f"    oauth_token: {token}\n"
        "    git_protocol: https\n"
        "    user: x-access-token\n",
    )
    os.chmod(path, 0o600)
    return path


def live_secrets(gh_config_dir: str | os.PathLike | None = None) -> list[str]:
    """The token values in force, for EXACT-VALUE redaction.

    The structural rule (`redact_assignments`) does NOT catch
    `oauth_token: <t>`, which is exactly what `cat hosts.yml` prints. rite
    minted the token, so it knows the value and needs no pattern (measured:
    exact matching caught every whole-token shape). Read from
    `$GH_CONFIG_DIR` when not given, which is how a Manager's own
    `rite journal` finds it from inside the sandbox.
    """
    d = gh_config_dir or os.environ.get("GH_CONFIG_DIR", "")
    if not d:
        return []
    try:
        text = (Path(d) / "hosts.yml").read_text()
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "oauth_token" and value.strip():
            found.append(value.strip())
    return found


def manager_secrets(root: Path, manager: str, home: Path | None = None) -> list[str]:
    """The live token values for one Manager, read from OUTSIDE the sandbox.

    What the Slack relay redacts by exact value before posting. The relay
    runs in the supervisor, which has no `GH_CONFIG_DIR` of its own.
    """
    return live_secrets(_gh_dir(root, manager, home))


# --- the run's lifecycle ---------------------------------------------------------


@dataclass
class Access:
    """One run's credentials. Opened at start, refreshed, closed at the end."""

    root: Path
    manager: str
    app: tuple[str, str, list[str]] | None = None  # app_id, installation_id, repos
    app_key: str = ""
    expires_at: float = 0.0
    token: str = ""
    home: Path | None = None
    post: object = None

    def secrets(self) -> list[str]:
        return [self.token] if self.token else []

    def refresh(self, now: float | None = None) -> list[str]:
        """Mint again if the token is near expiry. Lines to SAY, or [].

        ⚠ **On failure the old file is LEFT, never removed.** Removing it would
        make `gh` refuse with exit 4 (measured), and in the operator's own
        `~/.config/gh` shape it went ANONYMOUS, which is the failure this
        exists to remove. The old token runs out, the Manager sees
        `Bad credentials (HTTP 401)`, and this says why, in rite's words.
        """
        if self.app is None:
            return []
        t = time.time() if now is None else now
        if self.token and self.expires_at - t > REFRESH_MARGIN_SECONDS:
            return []
        app_id, installation_id, repos = self.app
        try:
            got = _mint(
                app_id, installation_id, self.app_key, repos, post=self.post, now=t
            )
        except _MintError as e:
            when = time.strftime("%H:%M", time.localtime(self.expires_at))
            return [
                f"github: the token for {', '.join(repos)} could not be refreshed "
                f"and {'expires' if self.expires_at > t else 'expired'} at "
                f"{when}: {e}. "
                "The Manager's gh and git calls will fail with 'Bad credentials' "
                "until it is."
            ]
        self.token, self.expires_at = got.token, got.expires_at
        _write_token(self.root, self.manager, got.token, self.home)
        return []

    def close(self) -> None:
        """End of the run: remove the token file.

        Different from a failed refresh, which leaves the file so the Manager
        gets a loud 401. Here nothing is running to use it, and a token left
        on disk is one the next run's pane would be handed stale.
        """
        _clear(self.root, self.manager, self.home)


def _clear(root: Path, manager: str, home: Path | None = None) -> None:
    """Remove this Manager's token file, whatever state it is in."""
    (_gh_dir(root, manager, home) / "hosts.yml").unlink(missing_ok=True)


def open_access(
    root: Path,
    manager: str,
    config,
    *,
    get_secret=None,
    home: Path | None = None,
    post=None,
) -> tuple[Access | None, str]:
    """Set up this run's credentials. `(access, refusal)`.

    Nothing configured means `(None, "")`. That is not an error, and the
    Manager simply has no GitHub credential. Configured but failing means a
    REFUSAL with GitHub's own words, the rule an unreachable board follows
    (D-74): an unreachable credential is not an absent one.
    """
    from rite_ai.credentials.store import get_scoped

    if get_secret is None:

        def get_secret(key):
            return get_scoped(key, config.credentials)

    app_cfg = getattr(config, "github_app", None)
    wants_app = bool(app_cfg and app_cfg.app_id)
    # A previous run's leftovers are never handed to this one's pane.
    _clear(root, manager, home)
    if not wants_app:
        return None, ""
    access = Access(root=root, manager=manager, home=home, post=post)
    repos = [app_cfg.repository or config.ticket_backend.repo]
    repos = [r for r in repos if r]
    key = get_secret("github_app_key") or ""
    if not repos:
        return None, (
            "github_app is configured but names no repository, and "
            "ticket_backend.repo is empty"
        )
    if not key:
        return None, (
            "github_app is configured but no github_app_key is "
            "stored. `rite credential set github_app_key --stdin "
            "< app.pem`"
        )
    access.app = (app_cfg.app_id, app_cfg.installation_id, repos)
    access.app_key = key
    said = access.refresh()
    if said:
        return None, said[0]
    return access, ""
