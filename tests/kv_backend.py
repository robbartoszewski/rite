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
    def __init__(self, port: int, timeout: float = 10.0) -> None:
        self.port = int(port)
        self.timeout = timeout

    def _call(self, request: dict):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(("127.0.0.1", self.port))
                s.sendall((json.dumps(request) + "\n").encode())
                buffer = b""
                while not buffer.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
            reply = json.loads(buffer.decode())
        except (OSError, ValueError) as e:
            # The store could not answer. Whether the write landed is
            # unknown, which is exactly what Unavailable means.
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

    def read_messages(self, since: str | None = None):
        reply = self._call({"op": "range", "since": since})
        if isinstance(reply, Unavailable):
            return reply
        if reply.get("unknown"):
            return Unavailable(f"unknown message cursor: {since!r}")
        return Messages([Message(c, text) for c, text in reply["items"]])
