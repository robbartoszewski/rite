"""The key that signs refinement records, and where it lives.

**What stops a Manager or a Worker writing its own record is this key, not a
prompt and not a terminal** (the note's part 3.6: a `/dev/tty` confirmation was
measured NOT to be a barrier). So the key sits beside the credential root,
under no path any Manager profile grants, as `cursor.key` does (CU4). Whether
each boundary really denies it is a measurement (TR0), not an assumption here.

⚠ **Never in an argv or an environment**, not even this process's own. CU1b
section 4 read an engine's environment through `sysctl(KERN_PROCARGS2)` from
inside the same Manager, a sibling and a yoloAI Worker. The key is read from
the file into memory by the process that uses it, and passed as bytes.

**Reading never creates it.** `rite refine status` on a machine that has signed
nothing answers from what is on the board; only a writer (`ensure`) makes a
key, so a status check can never mint the secret it then trusts.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

KEY_DIR_ENV = "RITE_REFINEMENT_KEY_DIR"
"""Overrides `key_dir`, as `RITE_MAIL_DIR` overrides the mail root: tests."""

KEY_BYTES = 32


def key_dir() -> Path:
    override = os.environ.get(KEY_DIR_ENV)
    if override:
        return Path(override)
    from rite_ai.managers import github_access

    return github_access._credential_root().parent / "refinement"  # noqa: SLF001


def key_path() -> Path:
    return key_dir() / "key"


@dataclass(frozen=True)
class Loaded:
    key: bytes | None
    problem: str = ""
    """Why there is no key: "" when there simply is none yet."""


def load() -> Loaded:
    path = key_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Loaded(None)
    except OSError as e:
        return Loaded(None, f"the refinement key at {path} cannot be read: {e}")
    if len(data) != KEY_BYTES:
        return Loaded(
            None,
            f"the refinement key at {path} is {len(data)} bytes, not {KEY_BYTES}: "
            "it was damaged or replaced, so no record can be checked",
        )
    return Loaded(data)


def ensure() -> bytes:
    """The key, created on first use.

    ⚠ **Published whole, never visible half-written.** The key is written to
    a private temporary file first and then `link`ed into place. `link` is
    atomic and refuses when the name exists. So two writers starting at once
    cannot each sign with a key of their own (the loser reads the winner's),
    and a reader never sees a short key and calls it damaged. Creating the
    real name with `O_EXCL` and then writing it would leave exactly that
    window.
    """
    loaded = load()
    if loaded.key is not None:
        return loaded.key
    if loaded.problem:
        raise OSError(loaded.problem)
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.parent / f".key.{os.getpid()}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, secrets.token_bytes(KEY_BYTES))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    final = load()
    if final.key is None:
        raise OSError(final.problem or f"{path} could not be created")
    return final.key
