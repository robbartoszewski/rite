"""THE DESIGN TEST: could a Redis backend pass the conformance suite unchanged?

git is the default state backend (D-19/D-20) and the reasons hold — it needs
nothing a team does not already have, it crosses network boundaries they
already trust, and it leaves an audit trail for free. The objection to it is
also real: git-as-CAS costs a round-trip per operation and degrades under
contention in ways a key-value store does not. D-21's answer is substitutable
backends, and that answer is only worth anything if the interface could
actually take one.

So this binds a store with Redis's shape — a separate process, reached over a
socket, per-key compare-and-set, an append-only list, NO trees, NO refs, NO
merges, no "whole state" — to the same `StateLayerConformance` the git and
local backends use, with no change to the suite and no special case in it.

It passing means the suite tests the CONTRACT. It failing would mean the
suite had quietly encoded git's mechanism as a semantic, which is exactly
what happened in P2-1a's first cut: the version was whole-state "because
that is what --force-with-lease compares", and a key-value store could only
have satisfied that by serialising every write through one global version.

What this is NOT: a Redis backend, or a promise of one. Nothing here ships.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from kv_backend import KeyValueStateLayer
from state_layer_conformance import StateLayerConformance


def _start():
    """(process, port). The store prints its port when it is listening."""
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


def _break(port: int, what: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5.0)
        s.connect(("127.0.0.1", port))
        s.sendall((json.dumps({"op": "break", "what": what}) + "\n").encode())
        s.recv(4096)


class TestAKeyValueBackendPassesUnchanged(StateLayerConformance):
    # A socket round-trip is fast, so the bursts land plenty of attempts in
    # the shared window. The floors stay where the local backend's are.
    BURST_SECONDS = 1.0

    @pytest.fixture
    def store(self, tmp_path):
        # Overriding the fixture, not the suite: starting and stopping a
        # server is this backend's business and no test needs to know.
        server, port = _start()
        try:
            yield port
        finally:
            server.kill()
            server.wait(timeout=5)

    def new_store(self, tmp_path):  # pragma: no cover - the fixture supplies it
        raise NotImplementedError

    def open_layer(self, store):
        return KeyValueStateLayer(store)

    def corrupt_state(self, store):
        """A value the store will not produce. There is nothing to corrupt in
        place here — no file, no object — which is the point: "unreadable"
        has to be expressible without naming a mechanism."""
        _break(store, "state")

    def corrupt_messages(self, store):
        _break(store, "messages")

    def actor_layer_spec(self, store, actor):
        return ("kv_backend", "KeyValueStateLayer", (store,))
