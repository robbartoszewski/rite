"""A Manager's helper sessions are a small on-demand pool, bounded at a moment (D-115).

Robert, 2026-10-02: the Manager is a coordinator, with no heavy
implementation, and it reaches for a HELPER session only on demand, for coordinator
work (a review, a verification, a spec, an answer) that needs fresh eyes or
must not block the interactive thread. Helpers are bounded by concurrency,
never by a rate: at most `MANAGER_HELPER_POOL` at once, and one more WAITS for
a slot. Today the one helper is rite's reply verifier.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from rite_ai.managers import claude_login, helpers, verifier


def _hold_many(n: int, hold: float = 0.2) -> int:
    """`n` helpers at once; the most that ran together."""
    state = {"now": 0, "most": 0}
    lock = threading.Lock()

    def one():
        with helpers.helper_slot():
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            time.sleep(hold)
            with lock:
                state["now"] -= 1

    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a helper never got a slot"
    return state["most"]


def test_no_more_than_the_pool_run_at_once():
    assert _hold_many(helpers.MANAGER_HELPER_POOL * 3) == helpers.MANAGER_HELPER_POOL


def test_a_full_pool_makes_the_next_wait_not_fail():
    """Every one of them ran: none was refused or dropped."""
    done: list[int] = []

    def one(i):
        with helpers.helper_slot():
            time.sleep(0.05)
            done.append(i)

    threads = [
        threading.Thread(target=one, args=(i,))
        for i in range(helpers.MANAGER_HELPER_POOL + 3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(done) == list(range(helpers.MANAGER_HELPER_POOL + 3))


def test_the_pool_is_small():
    assert 1 <= helpers.MANAGER_HELPER_POOL <= 3


def test_the_reply_verifier_holds_a_helper_slot_while_it_runs(tmp_path, monkeypatch):
    """The one helper today goes through the pool, so whatever adds a second
    kind inherits the bound."""
    held = {"during": None}
    real = helpers.helper_slot
    inside = {"n": 0}

    from contextlib import contextmanager

    @contextmanager
    def counting():
        with real():
            inside["n"] += 1
            try:
                yield
            finally:
                inside["n"] -= 1

    monkeypatch.setattr(helpers, "helper_slot", counting)
    config = tmp_path / "owner-login" / "claude"
    monkeypatch.setattr(
        claude_login,
        "pane_environment",
        lambda r, m: {"CLAUDE_CONFIG_DIR": str(config)},
    )

    def runner(argv, **kw):
        held["during"] = inside["n"]
        answer = {"verdict": "confirmed", "evidence": "checked"}
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"structured_output": answer}), ""
        )

    verifier.verify(tmp_path, "lead", "notes/HELLO.txt written", runner=runner)
    assert held["during"] == 1, "the verifier ran outside a helper slot"
    assert inside["n"] == 0, "the slot was not given back"
