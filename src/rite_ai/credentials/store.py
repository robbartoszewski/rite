"""Credential storage — keychain with environment variable fallback."""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import locked, write_atomic

SERVICE_NAME = "rite"


NOT_FOUND = "not_found"


# The credential KEYS rite itself looks up, and what a person would call
# each one. `rite credential set` takes one of these names; the secret is
# prompted for and never appears in argv.
#
# This mapping exists because `set` used to accept any string at all and
# report success. A user setting up JIRA typed their email address as the
# argument — `rite credential set someone@example.com` — and got
# "stored ... in keychain", so they believed JIRA was configured. It was
# not: the address had become a key name, `jira_email` was still unset,
# and nothing surfaced that until a much later failure naming a key they
# had never typed.
KNOWN_CREDENTIALS: dict[str, str] = {
    "jira_email": "JIRA account email — the address you log in with",
    "jira_token": "JIRA API token",
    "github_token": "GitHub personal access token",
    "claude_token": (
        "Claude Code OAuth token — from `claude setup-token`, for sandboxed Workers"
    ),
}

# Per-worker sandbox tokens are a family, not a fixed name — see
# `rite_ai.sandbox.token_credential_name`, which is the one place that
# builds them. Recognised by prefix so `rite add worker`'s provisioning
# step and a human typing the same thing both pass validation.
SANDBOX_TOKEN_PREFIX = "sandbox_token_"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(name: str) -> bool:
    """Whether an argument is an email address rather than a key name.

    Worth a message of its own rather than a generic "unknown key",
    because it is precisely what someone reaching for `jira_email` types,
    and because an email is unambiguously a credential VALUE — no key
    rite uses could ever look like one."""
    return bool(_EMAIL_RE.match(name.strip()))


def is_known_name(name: str, extra: tuple[str, ...] = ()) -> bool:
    """Whether `name` is a credential key rite would ever read.

    `extra` carries names that are only knowable from a project's own
    config — today `ticket_backend.credential`, which renames the JIRA
    token key — so validation does not reject a key the running project
    is configured to use."""
    if name in KNOWN_CREDENTIALS or name in extra:
        return True
    return name.startswith(SANDBOX_TOKEN_PREFIX) and len(name) > len(
        SANDBOX_TOKEN_PREFIX
    )


def suggest_name(name: str, extra: tuple[str, ...] = ()) -> str | None:
    """The closest known key to a typo'd one, or None."""
    candidates = [*KNOWN_CREDENTIALS, *extra]
    matches = difflib.get_close_matches(name.lower(), candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


@dataclass
class CredentialInfo:
    name: str
    source: str  # "keychain" | "env" | "not_found"

    @property
    def found(self) -> bool:
        return self.source != NOT_FOUND

    def describe(self) -> str:
        """How this reads to a person. The stored value stays the machine
        token `not_found` (things compare against it); what gets printed
        is prose, because a terminal line reading `jira_token: not_found`
        is an internal enum leaking into a status report."""
        if self.source == NOT_FOUND:
            return "not found"
        return self.source


def get(name: str) -> str | None:
    """Retrieve a credential by name. Checks keychain first, then env."""
    env_key = f"RITE_{name.upper()}"
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val

    try:
        import keyring

        val = keyring.get_password(SERVICE_NAME, name)
        if val:
            return val
    except Exception:
        pass

    return None


# --- Per-project namespacing (SPEC §10.2) ---
#
# The keychain SERVICE stays `SERVICE_NAME`; what changes is the ACCOUNT.
# A project with namespace `bentora-7f3a9c` keeps its JIRA token under the
# account `bentora-7f3a9c/jira_token`, so a second project on the same
# machine can hold a second JIRA identity — which a flat `jira_token`
# made impossible.
#
# The namespace is RECORDED in `.rite/config.yaml`, never derived. A
# derived name fails two ways that matter: renaming the project orphans
# every secret under the old name, and two checkouts of one project on
# one machine (a worktree, a second clone) collapse into a single
# identity — the opposite of what per-project scoping is for.
#
# READ `SCOPING_IS_NOT_A_SANDBOX` BEFORE TREATING THIS AS A SECURITY
# BOUNDARY. It is a naming convention.

# `/` rather than `_`: a key may itself contain underscores
# (`sandbox_token_alpha`), so `_` leaves `<ns>_sandbox_token_alpha`
# ambiguous about where the namespace ends. `/` cannot occur in a key.
NAMESPACE_SEPARATOR = "/"

# Bounded, and no `/` — a namespace that contained the separator could
# forge a different project's account name.
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")


SCOPING_IS_NOT_A_SANDBOX = """\
Per-project credential names prevent ACCIDENTAL cross-project use and make
the correct credential the easy one to reach. They are NOT a security
boundary. Keychain access is per-user, not per-process, so any unsandboxed
process running as you can read every entry rite has stored, whatever it
is named.

The only enforcement is the sandbox — and a sandboxed Worker cannot read
the keychain AT ALL, not even its own entry, which is why its token is
injected with `--env` rather than fetched.

Measured 2026-09-12 on macOS, yoloAI seatbelt backend, the real installed
CLI, in a freshly created sandbox:

  * `rite credential check jira_token` on the host   -> set (keychain)
  * the same command inside the sandbox              -> not found
  * `keyring.get_password("rite", <any name>)` there -> None, no exception
  * `stat` of login.keychain-db inside the sandbox   -> SUCCEEDS (metadata)
  * `ls` of ~/Library/Keychains, and reading the
    keychain file's CONTENTS, inside the sandbox     -> Operation not permitted

So the mechanism is a filesystem denial of the keychain's CONTENTS (the
profile allows `file-read-metadata` globally, which is why `stat` still
answers and the file looks present). It is not the naming scheme, and it
applies to a project's own credentials exactly as much as to anyone
else's.
"""


# The short form, for the end of every `rite credential list`. The full
# text above is the spec's; a 25-line essay printed on every listing is
# one people learn to scroll past, and a warning nobody reads is not a
# warning. This says the load-bearing half and points at the rest.
SCOPING_IS_NOT_A_SANDBOX_SHORT = """\
Per-project names prevent ACCIDENTAL cross-project use. They are not a
security boundary: any unsandboxed process running as you can read every
entry rite has stored, whatever it is named — and sandbox.enabled is
false by default. The only enforcement is the sandbox, where a Worker
cannot read the keychain at all (its token arrives via --env). SPEC §10.3
has the measurements.
"""


def is_valid_namespace(namespace: str) -> bool:
    return bool(_NAMESPACE_RE.match(namespace))


def make_namespace(project_name: str, entropy: str | None = None) -> str:
    """A project's credential namespace: a readable prefix plus a short
    random suffix.

    Generated ONCE and recorded. The suffix is why: two projects both
    called `backend` must stay distinct, and so must two checkouts of the
    same project. The readable prefix is for the human in Keychain
    Access trying to work out what an entry belongs to.
    """
    import secrets

    slug = re.sub(r"[^a-z0-9]+", "-", project_name.strip().lower()).strip("-")
    slug = slug[:24].strip("-") or "project"
    suffix = entropy if entropy is not None else secrets.token_hex(4)
    candidate = f"{slug}-{suffix}"
    return candidate if is_valid_namespace(candidate) else f"project-{suffix}"


def namespaced(namespace: str, key: str) -> str:
    """The keychain account `key` lives under for a project.

    An empty namespace means the project has none recorded, so the
    account IS the key — exactly the pre-namespacing layout, which is why
    an un-migrated project keeps working untouched."""
    if not namespace:
        return key
    return f"{namespace}{NAMESPACE_SEPARATOR}{key}"


PROJECT = "project"
GLOBAL = "global"
ENV = "env"


def _namespace_of(credentials: object | None) -> str:
    """Duck-typed so this module keeps no import dependency on config."""
    ns = getattr(credentials, "namespace", "") or ""
    return ns if isinstance(ns, str) else ""


@dataclass
class Resolved:
    """Where one credential KEY resolved, and under what account.

    The tier is reported rather than left to the caller to infer, because
    "set" on its own hid the case this redesign is about: a project
    silently using the machine-wide entry while its owner believed it had
    one of its own."""

    key: str
    tier: str  # ENV | PROJECT | GLOBAL | NOT_FOUND
    account: str  # the keychain account, or the env var name for ENV
    project_account: str  # what a project-scoped entry WOULD be called
    global_account: str  # the machine-wide fallback's name

    @property
    def found(self) -> bool:
        return self.tier != NOT_FOUND

    def describe(self) -> str:
        if self.tier == ENV:
            return f"set (environment {self.account})"
        if self.tier == PROJECT:
            return f"set (this project: {self.account})"
        if self.tier == GLOBAL:
            return f"set (machine-global '{self.global_account}')"
        return "not set"


def project_account(key: str, credentials: object | None) -> str:
    """The account THIS project uses for `key`."""
    return namespaced(_namespace_of(credentials), key)


def service_env_name(key: str) -> str:
    """The variable a Worker's sandbox receives `key` as — `JIRA_API_TOKEN`
    for `jira_token` — or "" when it has none.

    `worker_environment` delivers each credential under this name, so the
    reading side has to look for it too. Inside a sandbox the keychain
    answers nothing and `RITE_<KEY>` is never set: a lookup that knew only
    those two made `rite board` fail in every sandbox with the credential
    sitting in its environment."""
    from rite_ai.credentials.services import SERVICES, service_key

    for svc in SERVICES.values():
        for field in svc.secrets:
            if field.env and service_key(svc.name, field.name) == key:
                return field.env
    return ""


def resolve(key: str, credentials: object | None = None) -> Resolved:
    """Where `key` resolves for this project, WITHOUT reading the secret.

    Tiers, highest first:

      1. `RITE_<KEY>` in the environment — unchanged, and deliberately
         still first. A sandboxed Worker cannot read the keychain at all
         (`SCOPING_IS_NOT_A_SANDBOX`), so an env var is the only channel
         that reaches one; demoting it would break the one case that has
         no alternative.
      2. This project's namespaced entry.
      3. The machine-wide entry named by the bare key.
      4. The service's own variable (`JIRA_API_TOKEN`), which is what a
         Worker's sandbox receives (`service_env_name`). Last, so a
         variable exported in a host shell never outranks this project's
         keychain entry.

    Tier 3 is what keeps an existing install working: a `jira_token` set
    before this project had a namespace is still found and still used —
    and reported AS machine-global rather than passed off as this
    project's own, because a silent tier 3 is how a flat namespace
    survives forever while everyone believes it was fixed.
    """
    p_account = project_account(key, credentials)
    g_account = key

    env_key = f"RITE_{key.upper()}"
    if os.environ.get(env_key):
        return Resolved(key, ENV, env_key, p_account, g_account)

    if p_account != g_account and info(p_account).source == "keychain":
        return Resolved(key, PROJECT, p_account, p_account, g_account)

    if info(g_account).source == "keychain":
        return Resolved(key, GLOBAL, g_account, p_account, g_account)

    injected = service_env_name(key)
    if injected and os.environ.get(injected):
        return Resolved(key, ENV, injected, p_account, g_account)

    return Resolved(key, NOT_FOUND, "", p_account, g_account)


def get_scoped(key: str, credentials: object | None = None) -> str | None:
    """The VALUE for `key` under this project's namespace, falling back
    to the machine-wide entry. The read counterpart of `resolve`."""
    env_val = os.environ.get(f"RITE_{key.upper()}")
    if env_val:
        return env_val

    p_account = project_account(key, credentials)
    if p_account != key:
        val = _keychain_get(p_account)
        if val:
            return val
    val = _keychain_get(key)
    if val:
        return val
    # Tier 4 of `resolve`: last, for the same reason.
    injected = service_env_name(key)
    return (os.environ.get(injected) or None) if injected else None


def get_account(account: str) -> str | None:
    """The value stored under one exact keychain ACCOUNT, with no tier
    resolution and no env fallback.

    `rite credential migrate` needs precisely this: it has to read the
    machine-global entry while the project entry is the thing being
    created, so `get_scoped` — which is defined to prefer the project —
    would answer the wrong question the moment it succeeded."""
    return _keychain_get(account)


def _keychain_get(account: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, account)
    except Exception:
        return None


# Per-process, and never reset: one `rite status` builds several backends
# and would otherwise print the same line four times, which is how a
# warning becomes something people filter out. Tests clear it directly.
_WARNED_GLOBAL: set[str] = set()


def warn_if_global(key: str, credentials: object | None = None) -> str | None:
    """Announce a machine-global fallback on stderr, once per process per
    key. Returns the warning text (for tests), or None if no warning was
    due.

    ⚠ **This must not become silent.** A machine-global entry is shared
    with every other project on the machine; a project quietly resolving
    through it while its owner believes it has its own credential is the
    flat namespace surviving under a new name. Announcing names the
    account that WOULD have been used, so the fix is one command away.

    Deduplicated per process because a single `rite status` builds several
    backends and would otherwise print the same line four times, which is
    how a warning becomes something people filter out.
    """
    if key in _WARNED_GLOBAL:
        return None
    r = resolve(key, credentials)
    if r.tier != GLOBAL or r.project_account == r.global_account:
        return None
    _WARNED_GLOBAL.add(key)
    message = (
        f"warning: '{key}' is resolving to the machine-global keychain entry "
        f"'{r.global_account}', which is shared with every other project on "
        f"this machine. This project expects '{r.project_account}'. "
        f"Move it with `rite credential migrate {key}`."
    )
    print(message, file=sys.stderr)
    return message


def worker_environment(
    credentials: object | None = None, worker_token: str | None = None
) -> dict[str, str]:
    """Every credential a Worker receives, as env var -> value (§5.3.4).

    **Every Worker gets every credential the project holds.** Not a
    subset: Workers are fungible, and a Worker that lacked a credential
    another had would differ in capability, which forces whatever assigns
    tickets to reason about which Worker CAN do a job rather than which
    is free.

    Each env var name comes from the service field's own `env`, so rite
    delivers `JIRA_API_TOKEN` rather than a name of its own invention —
    the §10.5 boundary: rite stores and injects, and does not interpret.

    `worker_token` is that Worker's own git token and takes precedence
    for `GITHUB_TOKEN`. It is still one credential per Worker (§5.3.3);
    what the fungibility decision changed is the scope they share, not
    the count.

    ⚠ Every entry here is a secret yoloAI 0.11.0 writes into four files
    inside the sandbox, surviving `stop` and cleared only by `destroy`
    (measured; §5.3.4). This function returning more is a real increase
    in blast radius, which is the cost the decision accepted.
    """
    from rite_ai.credentials.services import SERVICES, service_key

    env: dict[str, str] = {}
    for svc in SERVICES.values():
        for field in svc.secrets:
            if not field.env:
                continue
            value = get_scoped(service_key(svc.name, field.name), credentials)
            if value:
                env[field.env] = value
    if worker_token:
        env["GITHUB_TOKEN"] = worker_token
    return env


def keychain_is_readable() -> bool:
    """Whether this process can read the keychain's backing store at all.

    Distinguishes "the credential is not there" from "this process cannot
    see any credential", which a sandboxed Worker cannot otherwise tell
    apart: `keyring.get_password` returns None for both. Telling someone
    in that position to "run `rite credential set`" is wrong advice —
    setting it changes nothing, because the same process still cannot
    read it back. Same distinction as `SandboxStatus.known` and
    `CountUnavailable`: a failure to CHECK is not a negative result.

    macOS only for now; everywhere else this answers True, which keeps
    the message exactly as it is today rather than inventing a confinement
    story for a platform nobody has measured.
    """
    if sys.platform != "darwin":
        return True
    path = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        return True
    except PermissionError:
        return False
    except OSError:
        # Missing, or something else entirely — not evidence of
        # confinement, so do not claim it.
        return True


def default_rite_home() -> Path:
    """`~/.rite/`, overridable via `RITE_HOME_DIR` — a function, not a
    module-level constant, so tests can redirect it instead of writing
    registry metadata to the real machine's home directory (the same
    reasoning as `rite_ai.dispatch.default_dispatch_dir`)."""
    override = os.environ.get("RITE_HOME_DIR")
    return Path(override) if override else Path.home() / ".rite"


def _registry_path() -> Path:
    return default_rite_home() / "credentials.json"


class RegistryUnreadable(Exception):
    """The registry file exists and will not parse.

    Distinguished from "empty" for the reason `rite_ai.state` exists: a
    corrupt registry read as `{}` and then written back would replace
    every recorded credential with the one being stored. Losing the
    record of a credential is not losing the credential — the keychain
    still holds it — but it is losing the only list `rotate()` has,
    silently, at the moment the user was adding to it.
    """


def _read_registry() -> dict[str, float]:
    path = _registry_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise RegistryUnreadable(f"{path}: {e}") from e
    except OSError as e:
        raise RegistryUnreadable(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise RegistryUnreadable(f"{path}: expected an object")
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def _write_registry(registry: dict[str, float]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(registry, indent=2, sort_keys=True))


def _locked_registry():
    """Exclusion around the registry's read-modify-write.

    `~/.rite/credentials.json` is MACHINE-wide — every project and every
    session on the machine writes it, and `rite add worker` writes it
    while provisioning a Worker's sandbox token, which is exactly the
    moment several of them are being created. It had no lock at all and
    was written with `write_text`. Measured, six concurrent writers over
    five rounds: every round lost entries and the worst kept one of six.
    """
    return locked(_registry_path())


def store(name: str, value: str) -> str:
    """Store a credential in the keychain. Returns the store used.

    Records only `name` and a last-set timestamp in the registry
    (`~/.rite/credentials.json`) — never the value — so `rotate()` can
    later list what exists and how stale it is without keyring's own
    enumeration (most backends have none)."""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, name, value)
    except Exception:
        return "failed"

    # The secret is already safe in the keychain. The registry is
    # bookkeeping, so a problem with it must not be reported as a failure
    # to store — and must not be papered over either, since the registry
    # is the only list `rotate()` has.
    try:
        with _locked_registry():
            registry = _read_registry()
            registry[name] = time.time()
            _write_registry(registry)
    except RegistryUnreadable:
        return "keychain, registry unreadable"
    except OSError:
        return "keychain, registry not updated"
    return "keychain"


def remove(name: str) -> str:
    """Delete a credential from the keychain and drop its registry entry.

    Returns "removed", "not_found", or "failed".

    The counterpart to `store()`, and it did not exist for a long time:
    `set` could create an entry that no rite command could delete, so a
    key typed by mistake stayed in the user's keychain permanently. The
    registry entry is dropped even when the keychain delete reports
    nothing to delete, so a registry that has drifted out of sync with
    the keychain can still be cleaned up rather than listing a phantom
    forever."""
    removed = False
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, name)
        removed = True
    except Exception:
        # Either keyring is unavailable, or there was no such password.
        # Both are survivable: the registry entry still needs clearing.
        pass

    try:
        with _locked_registry():
            registry = _read_registry()
            if name in registry:
                del registry[name]
                _write_registry(registry)
                return "removed"
    except RegistryUnreadable:
        # Leave the file alone for a human to look at rather than
        # rewriting it from a reading we know is wrong.
        return "removed" if removed else "failed"
    except OSError:
        return "failed"

    return "removed" if removed else "not_found"


def info(name: str) -> CredentialInfo:
    """Check where a credential would be found."""
    env_key = f"RITE_{name.upper()}"
    if os.environ.get(env_key):
        return CredentialInfo(name=name, source="env")

    try:
        import keyring

        if keyring.get_password(SERVICE_NAME, name):
            return CredentialInfo(name=name, source="keychain")
    except Exception:
        pass

    return CredentialInfo(name=name, source=NOT_FOUND)


@dataclass
class RotationEntry:
    name: str
    last_set: float | None  # epoch seconds; None if never recorded (pre-registry)
    source: str  # display text: "keychain" | "env" | "not found"


def list_for_rotation() -> list[RotationEntry]:
    """Every credential this rite installation has ever `store()`d,
    with its last-set time and where it resolves *now* (§10: "walks
    through each stored credential, shows when it was last set"). A
    credential satisfied only by an env var that was never `store()`d is
    not in this list — there is no registry entry to find it by, and no
    way to enumerate arbitrary env vars rite might read."""
    try:
        registry = _read_registry()
    except RegistryUnreadable:
        # An unreadable registry is not an empty one, and rotation
        # reporting "nothing to rotate" for a file it could not read is
        # the failure this module's neighbour exists to prevent.
        raise
    entries = [
        RotationEntry(name=name, last_set=ts, source=info(name).describe())
        for name, ts in registry.items()
    ]
    return sorted(entries, key=lambda e: e.name)
