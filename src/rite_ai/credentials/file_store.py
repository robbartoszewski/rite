"""rite's credential store: one 0600 file, on every platform.

Decided 2026-09-26 (Robert): **a file store, no passphrase, used on macOS and
Linux alike**, so there is one code path and no keyring branch. Why each part:

- **A file, not the OS keyring.** Ubuntu's keyring works for a logged-in
  desktop user and fails headless and inside a cron tick, where rite used to
  fall back to environment variables SILENTLY. That is the failure this
  release was spent removing: a process that goes without its credential and
  says nothing. On macOS the keychain is unreadable from inside a Manager's
  sandbox (measured), and granting it would mean granting the whole login
  keychain. Granting one file is a far narrower hole. On Linux the
  equivalent grant would be the D-Bus session bus, which reaches every secret
  the user has.
- **Mode 0600, and a wrong mode is REFUSED, not warned about.** File
  permissions are the entire defence, so a file anyone else can read is not
  read at all. The refusal names the file and the command that fixes it.
- **No passphrase, deliberately DEFERRED rather than forgotten.** A passphrase
  asked at Manager start breaks unattended, cron-started Managers, which are
  the case this store exists to serve. And it protects only at rest: a
  compromised Manager gets the decrypted value either way.
- **The declared direction is the credential broker in v0.7.0**, where
  nothing is stored for a Manager at all. This store is the v0.6.0 answer and
  is not meant to be entrenched.

It is installed as `keyring`'s backend when `rite_ai.credentials.store` is
imported, so every existing read and write goes through it unchanged, and the
test suite's in-memory keyring still sits in front of it. The OS keyring is
reached only by `rite credential import-keychain`, which moves an existing
install across.

⚠ **Where it lives, and why there.** `~/.config/rite/credential-store.json`,
overridable by `RITE_CREDENTIALS_FILE`. Not under `~/.rite`, which every
Manager's profile can read. Nothing grants `~/.config/rite`, and the Manager
profile denies it explicitly as well.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from rite_ai.state import write_atomic

REQUIRED_MODE = 0o600


class CredentialStoreError(Exception):
    """The store exists and cannot be trusted or read. Always said, with the
    file and the fix. Never swallowed into "no credential"."""


def store_path() -> Path:
    override = os.environ.get("RITE_CREDENTIALS_FILE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "rite" / "credential-store.json"


def describe() -> str:
    """What `rite doctor` prints: which store, where, and whether it is sound."""
    path = store_path()
    try:
        entries = len(_read(path))
    except CredentialStoreError as e:
        return f"file store at {path}: UNUSABLE — {e}"
    if not path.exists():
        return f"file store at {path} (mode 0600): empty, nothing stored yet"
    return f"file store at {path} (mode 0600): {entries} credential(s)"


def _check_mode(path: Path) -> None:
    st = path.stat()
    mode = stat.S_IMODE(st.st_mode)
    if mode != REQUIRED_MODE:
        raise CredentialStoreError(
            f"{path} has mode {mode:04o}, and rite reads its credential file "
            f"only at 0600, because the file's permissions are its whole "
            f"defence. If you did not change it on purpose, check who could "
            f"have read it, then: chmod 600 {path}"
        )
    if st.st_uid != os.getuid():
        raise CredentialStoreError(
            f"{path} belongs to another user (uid {st.st_uid}), so rite will "
            f"not read credentials from it"
        )


def _read(path: Path) -> dict[str, str]:
    """The stored accounts. A missing file is an empty store, not an error.

    An unreadable file IS an error: "could not look" is not "nothing is
    there", and reporting it as absent is the silent failure this replaces.
    """
    if not path.exists():
        return {}
    try:
        _check_mode(path)
        text = path.read_text()
    except PermissionError as e:
        raise CredentialStoreError(
            f"{path} could not be read ({e.strerror}). Inside a Manager's "
            f"sandbox this is expected: a Manager is not given rite's "
            f"credentials"
        ) from None
    except OSError as e:
        raise CredentialStoreError(f"{path} could not be read: {e}") from None
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as e:
        raise CredentialStoreError(
            f"{path} is not valid JSON ({e}); rite will not guess at its contents"
        ) from None
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise CredentialStoreError(f"{path} is not a map of names to values")
    return data


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write(path: Path, data: dict[str, str]) -> None:
    # `write_atomic` creates the temporary file with mkstemp, which is 0600,
    # and `os.replace` keeps it. The chmod makes that a stated property
    # rather than a side effect of one helper's implementation.
    write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.chmod(path, REQUIRED_MODE)


class FileKeyring(KeyringBackend):
    """`keyring`'s interface over the one file. `service` is always "rite"."""

    priority = 1  # never auto-selected; installed explicitly

    def get_password(self, service: str, username: str) -> str | None:
        return _read(store_path()).get(f"{service}:{username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        path = store_path()
        with _locked(path):
            data = _read(path)
            data[f"{service}:{username}"] = password
            _write(path, data)

    def delete_password(self, service: str, username: str) -> None:
        path = store_path()
        with _locked(path):
            data = _read(path)
            if f"{service}:{username}" not in data:
                raise PasswordDeleteError(f"{username!r} is not stored")
            del data[f"{service}:{username}"]
            _write(path, data)


def install() -> None:
    """Make the file store `keyring`'s backend for this process.

    Called once, at import of `rite_ai.credentials.store`. The suite then puts
    its in-memory keyring in front of it per test, and restores this after.
    """
    import keyring

    if not isinstance(keyring.get_keyring(), FileKeyring):
        keyring.set_keyring(FileKeyring())


def not_yet_imported() -> list[str]:
    """Names rite's registry says were stored, and the file does not hold.

    The registry (`~/.rite/credentials.json`) lists every name `store()` ever
    wrote, and never a value. Before 0.6.0 those values went to the OS
    keychain. Answered WITHOUT touching the keychain, because reading it can
    raise a GUI prompt that blocks with no output (measured in this suite).
    """
    from rite_ai.credentials import store as _store

    try:
        registry = _store._read_registry()
        held = _read(store_path())
    except (CredentialStoreError, _store.RegistryUnreadable, OSError):
        return []
    return [n for n in registry if f"{_store.SERVICE_NAME}:{n}" not in held]


def os_keyring():
    """The OS keyring, for `rite credential import-keychain` ONLY.

    The highest-priority backend keyring would pick if rite had not installed
    its own. Everything else in rite reads the file.
    """
    from keyring.backend import get_all_keyring

    candidates = [
        k
        for k in get_all_keyring()
        if not isinstance(k, FileKeyring)
        and k.priority > 0
        and "chainer" not in type(k).__module__
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda k: k.priority)
