"""A tiny key-value server, so the state layer's portability can be RUN.

Not a backend rite ships and not a Redis client — a stand-in with Redis's
shape: a separate process holding the data, reached over a socket, serving
one command at a time, with per-key compare-and-set and an append-only list.
No trees, no refs, no merges, no notion of "the whole state".

It exists to answer one question with a test run instead of an opinion:
**could a Redis backend pass the conformance suite unchanged?** Anything in
that suite that quietly assumed git — a whole-tree merge, a ref, a
fetch-then-push — fails here, loudly.

It listens on loopback and prints its port. Protocol: one JSON object per
line, one JSON object back.

    {"op": "get",    "key": k}                        -> found, value, version
    {"op": "cas",    "key": k, "value": v, "expected": ver} -> ok, version
    {"op": "append", "value": s}                      -> cursor
    {"op": "range",  "since": cursor|null}            -> items | unknown
    {"op": "break",  "what": "state"|"messages"}      -> the store starts
                                                         failing that half

`break` is the store's version of damage: a value it will not produce. A
backend cannot tell a caller WHY, only that the answer is unavailable —
which is the point, because "the blob is corrupt" must not be a concept the
interface has.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socketserver
import sys
import threading

LOCK = threading.Lock()
VALUES: dict[str, bytes] = {}
MESSAGES: list[str] = []
BROKEN: set[str] = set()

ABSENT = "absent"


def version_of(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def handle(request: dict) -> dict:
    op = request.get("op")
    with LOCK:  # one command at a time, like the store this imitates
        if op == "break":
            BROKEN.add(request["what"])
            return {"ok": True}
        if op == "get":
            if "state" in BROKEN:
                return {"error": "the value cannot be produced"}
            key = request["key"]
            if key not in VALUES:
                return {"found": False, "version": ABSENT}
            value = VALUES[key]
            return {
                "found": True,
                "value": base64.b64encode(value).decode(),
                "version": version_of(value),
            }
        if op == "cas":
            if "state" in BROKEN:
                return {"error": "the value cannot be produced"}
            key = request["key"]
            current = VALUES.get(key)
            now = ABSENT if current is None else version_of(current)
            if now != request["expected"]:
                return {"ok": False, "current": now}
            value = base64.b64decode(request["value"])
            VALUES[key] = value
            return {"ok": True, "version": version_of(value)}
        if op == "append":
            if "messages" in BROKEN:
                return {"error": "the log cannot be produced"}
            MESSAGES.append(request["value"])
            return {"cursor": str(len(MESSAGES))}
        if op == "range":
            if "messages" in BROKEN:
                return {"error": "the log cannot be produced"}
            since = request.get("since")
            start = 0
            if since is not None:
                if not since.isdigit() or not (0 < int(since) <= len(MESSAGES)):
                    return {"unknown": True}
                start = int(since)
            items = [[str(i + 1), MESSAGES[i]] for i in range(start, len(MESSAGES))]
            return {"items": items}
    return {"error": f"unknown op {op!r}"}


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for line in self.rfile:
            if not line.strip():
                continue
            try:
                reply = handle(json.loads(line))
            except (ValueError, KeyError, TypeError) as e:
                reply = {"error": str(e)}
            self.wfile.write((json.dumps(reply) + "\n").encode())
            self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    # Loopback rather than a unix socket: no path-length limit (macOS caps
    # those around 100 characters, which a pytest tmp path alone exceeds),
    # and a port is what the store being imitated actually listens on.
    with Server(("127.0.0.1", 0), Handler) as server:
        sys.stdout.write(f"{server.server_address[1]}\n")
        sys.stdout.flush()
        server.serve_forever()


if __name__ == "__main__":
    main()
