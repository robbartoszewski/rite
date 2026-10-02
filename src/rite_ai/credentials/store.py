"""Credential storage: rite's 0600 file store, with a LOUD environment override.

Since 2026-09-26 (C6/C26, Robert) the store is one file on every platform,
`file_store.py`, installed below as `keyring`'s backend. The OS keychain is
read only by `rite credential import-keychain`. An environment variable still
outranks the file where it is set, which is how a sandboxed Worker receives a
credential (§5.3.4). **It is never used silently:** the first use of each one
in a process is said on stderr, naming the variable. A credential that is
absent or unreadable is reported as that, never as a quiet fall to whatever
else happens to be there.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from rite_ai.credentials import file_store
from rite_ai.credentials.file_store import CredentialStoreError
from rite_ai.state import locked, write_atomic

SERVICE_NAME = "rite"

STORED = "file store"
"""What `store()` returns on success, and `info().source` for a stored value."""

# The file store is THE store, for every reader and writer in this process.
file_store.install()

_ANNOUNCED: set[str] = set()


def _from_environment(key: str, variable: str) -> None:
    """Say, once per process, that a credential came from the environment.

    ⚠ The silent version of this is the bug the file store was built to end:
    a keyring that failed headless and a value quietly taken from wherever
    else it could be found. The environment is still a legitimate channel (a
    Worker's sandbox receives its credentials that way), so it is used, and it
    is SAID.
    """
    if variable in _ANNOUNCED:
        return
    _ANNOUNCED.add(variable)
    print(
        f"rite: credential '{key}' is coming from the environment variable "
        f"{variable}, not from the file store",
        file=sys.stderr,
    )


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
    "slack_bot_token": (
        "Slack bot token (xoxb-...) — for the relay that reads and writes a "
        "Manager's mailbox"
    ),
    "claude_token": (
        "Claude Code OAuth token — from `claude setup-token`, for sandboxed Workers"
    ),
    "cursor_api_key": (
        "Cursor API key — for a Cursor Manager or Worker; never put on a "
        "Worker's command line"
    ),
    "github_app_key": (
        "GitHub App private key (PEM) — mints a sandboxed Manager's one-hour, "
        "one-repository token; stays outside the sandbox. Set with --stdin"
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
    source: str  # STORED | "env" | "not_found"

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
        _from_environment(name, env_key)
        return env_val

    try:
        import keyring

        val = keyring.get_password(SERVICE_NAME, name)
        if val:
            return val
    except CredentialStoreError:
        raise
    except Exception:
        pass

    return None


# --- Per-project namespacing (SPEC §10.2) ---
#
# The keychain SERVICE stays `SERVICE_NAME`; what changes is the ACCOUNT.
# A project with namespace `acme-7f3a9c` keeps its JIRA token under the
# account `acme-7f3a9c/jira_token`, so a second project on the same
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

    if p_account != g_account and info(p_account).source == STORED:
        return Resolved(key, PROJECT, p_account, p_account, g_account)

    if info(g_account).source == STORED:
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
        _from_environment(key, f"RITE_{key.upper()}")
        return env_val

    p_account = project_account(key, credentials)
    if p_account != key:
        val = _keychain_get(p_account)
        if val:
            return val
    val = _keychain_get(key)
    if val:
        return val
    # Tier 4 of `resolve`: last, for the same reason, and SAID.
    injected = service_env_name(key)
    if injected and os.environ.get(injected):
        _from_environment(key, injected)
        return os.environ[injected]
    return None


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
    except CredentialStoreError:
        raise
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
    try:
        r = resolve(key, credentials)
    except CredentialStoreError:
        # ⚠ Which tier a key resolves at cannot be told from a store that
        # cannot be read, so there is nothing to warn about HERE; whoever
        # reads the value meets the same store and says why (live run
        # finding S25: inside a Manager's sandbox this raised out of every
        # board, status and loop command as a traceback).
        return None
    if r.tier != GLOBAL or r.project_account == r.global_account:
        return None
    _WARNED_GLOBAL.add(key)
    message = (
        f"warning: '{key}' is resolving to the machine-global credential entry "
        f"'{r.global_account}', which is shared with every other project on "
        f"this machine. This project expects '{r.project_account}'. "
        f"Move it with `rite credential migrate {key}`."
    )
    print(message, file=sys.stderr)
    return message


# The services whose credentials a Worker receives: Claude, the engine every
# Worker runs (`start_worker` passes `--agent claude`). Nothing else.
#
# ⚠ **Not GitHub, since 2026-09-29 (Robert: "push and PR are the
# deterministic code's job").** rite clones and fetches on the host before
# the sandbox starts (`rite prepare`), and `rite deliver` collects the
# Worker's commits and pushes and opens the pull request on the host, with a
# token it resolves there (`publishing/deliver.py`). Nothing a Worker is told
# to do needs GitHub, and in the two Worker transcripts on this machine no
# Worker ran git fetch/pull/push/clone/remote or gh at all. A token in the
# sandbox could only let a Worker push, open a pull request — upstream too —
# or merge, which only instructions forbade.
WORKER_SERVICES: tuple[str, ...] = ("claude",)
"""What a CLAUDE Worker receives. See `worker_services_for` for why the set is
now asked per engine rather than read directly."""


def worker_services_for(engine: str) -> tuple[str, ...]:
    """The services a Worker of this engine receives (OL4).

    ⚠ **A local Worker gets NOTHING, and that is the narrowing — not an
    omission.** `WORKER_SERVICES` is the Claude login, and an Ollama Worker has
    no use for it: its model answers on this machine's own loopback, which needs
    no credential at all (OL1 measured the daemon answering a sandboxed client
    with no token in play). Handing it one anyway would put the operator's
    Claude login inside a sandbox that cannot spend it — and SB12 measured a
    sandbox's environment readable from other sandboxes on the same machine, so
    an unused credential in there is a credential exposed for nothing.

    This is the 2026-09-29 "Narrow it down" rule applied to the axis that did
    not exist when it was made: then the question was WHICH services a Worker
    needs, and every Worker was Claude. Now a Worker has an engine, and the
    answer depends on it.

    ⚠ **An endpoint that does need a credential is not handled here.** A
    Manager names one with `credential:`, a key name and never a secret; a
    Worker declares no such key yet (OL3 added five, not six), because no local
    endpoint on this machine needs one. Add the key when one does, rather than
    widening this to "whatever a Worker might want".
    """
    from rite_ai.config.managers import is_local_engine

    return () if is_local_engine(engine) else WORKER_SERVICES


def worker_environment(
    credentials: object | None = None, *, engine: str = "claude"
) -> dict[str, str]:
    """The credentials a Worker receives, as env var -> value (§5.3.4).

    `engine` defaults to Claude, which is what every Worker was before OL3.

    ⚠ **Only `WORKER_SERVICES`, never "every credential the project holds".**
    That was the rule until 2026-09-29, when Robert reversed it ("Narrow it
    down") on this evidence: the pingr Worker proof's launch line carried
    `SLACK_BOT_TOKEN`, `JIRA_API_TOKEN` and `JIRA_EMAIL`, none of which a
    Worker uses, and SB12 measured that a sandbox's environment is readable
    from other sandboxes on the same machine. Each credential here is also
    written by yoloAI into files inside the sandbox until `destroy`. So a
    service is added to `WORKER_SERVICES` only when a Worker is shown to
    need it — do not restore the old rule as a fix for a Worker that lacks
    something.

    Workers stay fungible: every Worker gets the same set, so assignment
    still never asks which Worker CAN do a job.

    Each env var name comes from the service field's own `env`, so rite
    delivers `GITHUB_TOKEN` rather than a name of its own invention — the
    §10.5 boundary: rite stores and injects, and does not interpret.
    """
    from rite_ai.credentials.services import SERVICES, service_key

    env: dict[str, str] = {}
    for name in worker_services_for(engine):
        svc = SERVICES[name]
        for field in svc.secrets:
            if not field.env:
                continue
            value = get_scoped(service_key(svc.name, field.name), credentials)
            if value:
                env[field.env] = value
    return env


def store_is_readable() -> bool:
    """Whether this process can read rite's credential store at all.

    Distinguishes "the credential is not there" from "this process cannot
    see any credential". Inside a Manager's sandbox the file store is denied
    on purpose, and telling someone there to "run `rite credential set`"
    would be wrong advice: the same process still could not read it back.
    Same distinction as `SandboxStatus.known`: a failure to CHECK is not a
    negative result.
    """
    try:
        file_store._read(file_store.store_path())
        return True
    except CredentialStoreError:
        return False


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
    except CredentialStoreError:
        raise
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
        return f"{STORED}, registry unreadable"
    except OSError:
        return f"{STORED}, registry not updated"
    return STORED


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
    except CredentialStoreError:
        raise
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
            return CredentialInfo(name=name, source=STORED)
    except CredentialStoreError:
        raise
    except Exception:
        pass

    return CredentialInfo(name=name, source=NOT_FOUND)


@dataclass
class RotationEntry:
    name: str
    last_set: float | None  # epoch seconds; None if never recorded (pre-registry)
    source: str  # display text: STORED | "env" | "not found"


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


# --- Which namespace belongs to which project (S18) ---
#
# A re-init (a reset, or `rm -rf` and a fresh clone) used to mint a fresh
# namespace every time, orphaning every credential stored under the old one,
# with nothing said (v0.7.0 dogfood S18). "The same project" is identified by
# its modules' git remote URLs: the one identity that survives the project
# directory being deleted and cloned again. The project's name does not
# (renamed, or `my-project` twice), nor does its path (moved, re-cloned).
#
# `~/.rite/namespaces.json` records, for each namespace, the remotes of the
# project that recorded it. It is written by `rite init` and `rite credential
# set`; it holds no secret and no account name, only which namespace a
# repository's project used. Machine-wide and advisory: init OFFERS a match,
# and never reuses one silently.

NAMESPACES_FILENAME = "namespaces.json"


def _namespaces_path() -> Path:
    return default_rite_home() / NAMESPACES_FILENAME


def normalise_remote(url: str) -> str:
    """A remote URL reduced to `host/owner/repo`, so the forms one repository
    is cloned by compare equal: `https://github.com/O/r.git`,
    `git@github.com:o/r`, `ssh://git@github.com/o/r/`. A local path stays a
    path. "" for nothing."""
    u = (url or "").strip()
    if not u:
        return ""
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", u, re.I)
    if m is None:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+):(?!/)(.+)$", u)
    if m is None:
        return u.rstrip("/").removesuffix(".git")
    host, path = m.group(1), m.group(2)
    return f"{host}/{path}".rstrip("/").removesuffix(".git").lower()


def remember_namespace(namespace: str, remotes: list[str], project: str = "") -> str:
    """Record that `namespace` is the project whose modules are `remotes`.
    Returns "" or why it could not be written (never raises: a record that
    cannot be written must not fail the command writing it)."""
    wanted = sorted({normalise_remote(r) for r in remotes if normalise_remote(r)})
    if not namespace or not wanted:
        return ""
    path = _namespaces_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path):
            try:
                data = json.loads(path.read_text()) if path.is_file() else {}
            except json.JSONDecodeError:
                return f"{path} is not readable JSON; left as it is"
            if not isinstance(data, dict):
                return f"{path} is not a JSON object; left as it is"
            entry = data.get(namespace) if isinstance(data.get(namespace), dict) else {}
            known = set(entry.get("remotes") or [])
            data[namespace] = {
                "remotes": sorted(known | set(wanted)),
                "project": project or entry.get("project", ""),
                "noted_at": time.time(),
            }
            write_atomic(path, json.dumps(data, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        return f"could not record the namespace in {path} ({e})"
    return ""


@dataclass(frozen=True)
class PruneCandidate:
    """A credential namespace no project rite knows on this machine uses."""

    namespace: str
    names: tuple[str, ...]
    """Every stored or registered account under it, in full."""
    last_set: float
    project: str = ""
    remotes: tuple[str, ...] = ()
    """What `namespaces.json` recorded for it: a hint that a real project,
    one rite has not seen on this machine, may still use it."""


def prune_candidates(used: set[str]) -> list[PruneCandidate]:
    """🔴 SCRUM-18. Namespaces holding credentials that no known project uses.

    Test and scratch runs left namespaces in the real store and registry
    (`test-credential-set-does-*`, `rte2e-scratch-*`, `acme-1a2b3c`), and
    nothing removed them. This only FINDS them. `used` is every namespace a
    project rite knows uses; a project rite has never seen on this machine
    is not in it, which is why removal is never automatic (the CLI makes a
    person name each namespace). Unscoped, machine-global entries are never
    candidates. Raises `RegistryUnreadable` or `CredentialStoreError` rather
    than reading an unreadable store as nothing to prune."""
    from rite_ai.credentials import file_store

    registry = _read_registry()
    names: dict[str, float] = dict(registry)
    for key in file_store._read(file_store.store_path()):
        service, _, account = key.partition(":")
        if service == SERVICE_NAME and account:
            names.setdefault(account, 0.0)
    try:
        recorded = json.loads(_namespaces_path().read_text())
    except (OSError, json.JSONDecodeError):
        recorded = {}
    if not isinstance(recorded, dict):
        recorded = {}
    grouped: dict[str, list[str]] = {}
    for name in names:
        ns, sep, _ = name.partition(NAMESPACE_SEPARATOR)
        if sep and is_valid_namespace(ns) and ns not in used:
            grouped.setdefault(ns, []).append(name)
    found = []
    for ns, members in sorted(grouped.items()):
        entry = recorded.get(ns) if isinstance(recorded.get(ns), dict) else {}
        found.append(
            PruneCandidate(
                namespace=ns,
                names=tuple(sorted(members)),
                last_set=max(float(names[m] or 0) for m in members),
                project=str(entry.get("project", "")),
                remotes=tuple(entry.get("remotes") or ()),
            )
        )
    return found


@dataclass(frozen=True)
class NamespaceMatch:
    namespace: str
    project: str
    keys: tuple[str, ...]
    """The credentials stored under it, by key: what reusing it brings back."""


def namespaces_for(remotes: list[str]) -> list[NamespaceMatch]:
    """Namespaces recorded for a project sharing any of `remotes`, that still
    hold at least one stored credential; newest first. An unreadable record
    or registry reads as no match: this only ever feeds an offer."""
    wanted = {normalise_remote(r) for r in remotes if normalise_remote(r)}
    if not wanted:
        return []
    try:
        data = json.loads(_namespaces_path().read_text())
        registry = _read_registry()
    except (OSError, json.JSONDecodeError, RegistryUnreadable):
        return []
    if not isinstance(data, dict):
        return []
    found = []
    for ns, entry in data.items():
        if not isinstance(entry, dict) or not is_valid_namespace(ns):
            continue
        if not wanted & set(entry.get("remotes") or []):
            continue
        prefix = f"{ns}{NAMESPACE_SEPARATOR}"
        keys = tuple(sorted(a[len(prefix) :] for a in registry if a.startswith(prefix)))
        if keys:
            found.append(
                (
                    entry.get("noted_at", 0),
                    NamespaceMatch(ns, str(entry.get("project", "")), keys),
                )
            )
    return [m for _, m in sorted(found, key=lambda x: -float(x[0] or 0))]
