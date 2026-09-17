"""A local filesystem state layer (P2-1a).

NOT test scaffolding. D-20 and D-21 require substitutable backends, so this is
the second implementation the abstraction exists for, and the one every
consumer on the path (P2-1d, P2-2a, P2-3a, P2-4a, P2-5a) binds to and is tested
against before the git CAS exists. It passes the same conformance suite the
git backend (P2-1b) will have to.

**The whole state is ONE file.** Git's state branch is one commit on one ref,
and its CAS is whole-ref (§2.4.2). Storing each key as its own file would make
a two-file update non-atomic against a crash, and would tempt a per-key version
that the git backend could never honour. One snapshot, replaced atomically,
behaves the way the git ref does: every write produces a complete new state or
none.

**Locking reuses `rite_ai.state` rather than repeating its mistakes.** Writers
hold `locked()`, which flocks a sidecar — locking the data file itself was
measured losing claims once `os.replace` swapped the inode. And because a lock
that does not exclude is worse than none, the backend measures
`exclusion_holds()` and FAILS CLOSED: on a filesystem where flock is a no-op
(Docker, NFS, SMB) every write returns Unavailable instead of racing silently.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Appended,
    Conflict,
    Message,
    Messages,
    Present,
    StateLayer,
    Unavailable,
    Written,
    valid_key,
)
from rite_ai.state import exclusion_holds, locked, write_atomic

_SNAPSHOT = "state.json"
_MESSAGES = "messages.jsonl"


class LocalStateLayer(StateLayer):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._snapshot = self.root / _SNAPSHOT
        self._messages = self.root / _MESSAGES
        self._exclusion: bool | None = None

    # --- internals ---

    def _excludes(self) -> bool:
        """Measured once per instance: the answer does not change while the
        process runs, and probing on every write would double the I/O."""
        if self._exclusion is None:
            self._exclusion = exclusion_holds(self.root)
        return self._exclusion

    def _no_exclusion(self) -> Unavailable:
        return Unavailable(
            f"file locking does not exclude under {self.root} — two writers "
            "could both be told their write succeeded, so writes are refused "
            "here rather than allowed to race"
        )

    def _load(self) -> tuple[str, dict[str, str]] | Unavailable:
        """(version, {key: base64}) — or Unavailable if the snapshot exists
        and cannot be read. A missing snapshot is the never-written state."""
        try:
            text = self._snapshot.read_text()
        except FileNotFoundError:
            return ABSENT, {}
        except OSError as e:
            return Unavailable(f"{self._snapshot}: could not be read: {e}")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            return Unavailable(f"{self._snapshot}: not valid JSON: {e}")
        if not isinstance(raw, dict):
            return Unavailable(f"{self._snapshot}: not a state snapshot")
        version, keys = raw.get("version"), raw.get("keys")
        if not isinstance(version, str) or not isinstance(keys, dict):
            # The WHOLE state is unreadable, so there is no per-key content to
            # pass through (D-54) and no version to compare against. Refusing
            # is the only honest answer at this level.
            return Unavailable(f"{self._snapshot}: not a state snapshot")
        return version, keys

    # --- StateLayer ---

    def read_state(self, key: str) -> Present | Absent | Unavailable:
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        # No lock: `write_atomic` replaces the file with `os.replace`, so a
        # reader sees the whole old snapshot or the whole new one, never a mix.
        loaded = self._load()
        if isinstance(loaded, Unavailable):
            return loaded
        version, keys = loaded
        if key not in keys:
            return Absent(version)
        try:
            value = base64.b64decode(keys[key], validate=True)
        except (ValueError, TypeError):
            return Unavailable(f"{self._snapshot}: value for {key!r} is corrupt")
        return Present(value, version)

    def write_state(
        self, key: str, value: bytes, expected_version: str
    ) -> Written | Conflict | Unavailable:
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        if not self._excludes():
            return self._no_exclusion()
        try:
            with locked(self._snapshot):
                loaded = self._load()
                if isinstance(loaded, Unavailable):
                    return loaded
                current, keys = loaded
                if current != expected_version:
                    return Conflict(current)
                # Every other key's bytes are carried over untouched — this IS
                # §2.4.2's read-merge-write, and D-54's pass-through.
                keys = dict(keys)
                keys[key] = base64.b64encode(value).decode("ascii")
                new_version = "1" if current == ABSENT else str(int(current) + 1)
                write_atomic(
                    self._snapshot,
                    json.dumps({"version": new_version, "keys": keys}, sort_keys=True),
                )
                return Written(new_version)
        except (OSError, ValueError) as e:
            # The write may or may not have landed; the caller must re-read.
            return Unavailable(f"{self._snapshot}: write failed: {e}")

    def append_message(self, content: str) -> Appended | Unavailable:
        if not self._excludes():
            return self._no_exclusion()
        try:
            with locked(self._messages):
                existing = self._read_log()
                if isinstance(existing, Unavailable):
                    return existing
                seq = (int(existing[-1].cursor) + 1) if existing else 1
                line = json.dumps({"seq": seq, "content": content}) + "\n"
                with open(self._messages, "a") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                return Appended(str(seq))
        except OSError as e:
            return Unavailable(f"{self._messages}: append failed: {e}")

    def _read_log(self) -> list[Message] | Unavailable:
        try:
            text = self._messages.read_text()
        except FileNotFoundError:
            return []
        except OSError as e:
            return Unavailable(f"{self._messages}: could not be read: {e}")
        out: list[Message] = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                out.append(Message(str(int(raw["seq"])), str(raw["content"])))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                # An audit log with a hole in it must not be presented as
                # complete. Refuse, naming the line, rather than skip it.
                return Unavailable(f"{self._messages}: line {n} is corrupt")
        return out

    def read_messages(self, since: str | None = None) -> Messages | Unavailable:
        log = self._read_log()
        if isinstance(log, Unavailable):
            return log
        if since is None:
            return Messages(log)
        if not any(m.cursor == since for m in log):
            # An unknown cursor is not "nothing new": returning an empty list
            # would silently skip every message after a position the caller
            # believes exists.
            return Unavailable(f"unknown message cursor: {since!r}")
        after = int(since)
        return Messages([m for m in log if int(m.cursor) > after])
