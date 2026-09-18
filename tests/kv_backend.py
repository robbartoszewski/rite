"""A StateLayer over the key-value server (`kv_server`).

Test-only. It exists to prove the interface is not git with a coat of paint:
this backend has no tree to merge, no ref to force-push, no fetch before a
write, and no notion of a version for "the whole state". One call, one key,
one round-trip.

If it can pass `state_layer_conformance` unchanged, so can Redis — the
operations it needs are GET, a compare-and-set (WATCH/MULTI, or one Lua
script), an append and a range read.
"""

from __future__ import annotations

import base64
import json
import socket
import subprocess
import sys
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


class KeyValueStateLayer(StateLayer):
    """One handle, one connection.

    The connection is held open and reopened when it breaks, which is what
    a client for this kind of store does. It is not a cache — no reply is
    ever reused, and every call is a round-trip. Connecting afresh per call
    was the first version, and under four processes ticking as fast as this
    store answers it exhausted the ephemeral port range, after which every
    operation failed: a property of my test double, not of rite, but it
    looked exactly like the coordination layer giving up.
    """

    def __init__(self, port: int, timeout: float = 10.0) -> None:
        self.port = int(port)
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._reader = None

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.connect(("127.0.0.1", self.port))
        self._socket, self._reader = s, s.makefile("rb")
        return s

    def _drop(self) -> None:
        for handle in (self._reader, self._socket):
            try:
                if handle is not None:
                    handle.close()
            except OSError:
                pass
        self._socket, self._reader = None, None

    def _call(self, request: dict):
        for attempt in (1, 2):  # one reconnect, then give up honestly
            try:
                s = self._socket or self._connect()
                s.sendall((json.dumps(request) + "\n").encode())
                line = self._reader.readline()
                if not line:
                    raise OSError("the store closed the connection")
                reply = json.loads(line.decode())
                break
            except (OSError, ValueError) as e:
                self._drop()
                if attempt == 2:
                    # Whether the write landed is unknown, which is exactly
                    # what Unavailable means.
                    return Unavailable(f"the store did not answer: {e}")
        if "error" in reply:
            return Unavailable(reply["error"])
        return reply

    def read_state(self, key: str):
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        reply = self._call({"op": "get", "key": key})
        if isinstance(reply, Unavailable):
            return reply
        if not reply.get("found"):
            return Absent()
        return Present(base64.b64decode(reply["value"]), reply["version"])

    def write_state(self, key: str, value: bytes, expected_version: str):
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        reply = self._call(
            {
                "op": "cas",
                "key": key,
                "value": base64.b64encode(value).decode(),
                "expected": expected_version,
            }
        )
        if isinstance(reply, Unavailable):
            return reply
        if not reply.get("ok"):
            return Conflict(reply.get("current", ABSENT))
        return Written(reply["version"])

    def append_message(self, content: str):
        reply = self._call({"op": "append", "value": content})
        if isinstance(reply, Unavailable):
            return reply
        return Appended(reply["cursor"])

    def read_messages(self, since: str | None = None, limit: int | None = None):
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        reply = self._call({"op": "range", "since": since, "limit": limit})
        if isinstance(reply, Unavailable):
            return reply
        if reply.get("unknown"):
            return Unavailable(f"unknown message cursor: {since!r}")
        return Messages([Message(c, text) for c, text in reply["items"]])


def start_store():
    """(process, port) for a fresh key-value store. The server prints its
    port once it is listening, so there is nothing to poll and no sleep."""
    server = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "kv_server.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    line = server.stdout.readline().decode().strip()
    if not line.isdigit():
        server.kill()
        raise RuntimeError(f"the store never came up: {server.stderr.read().decode()}")
    return server, int(line)
