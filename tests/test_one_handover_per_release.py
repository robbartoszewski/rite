"""A Worker's claims are handed over once, however many callers race for them.

`perform_handover` has three callers: the scheduler tick at a window
boundary, `rite stop`, and a takeover. The tick lock keeps two TICKS apart and
cannot keep a tick apart from a `rite stop`. So the handover itself must be
exclusive: it used to read a Worker's claims under one ledger lock and
release them under another, and two handovers at once both found the ticket
and both posted the handover comment. That was the 0.6.0 known issue's
consequence ("the handover comment posted twice"), reachable through a
second path after the tick lock was fixed.

With no ticket backend configured, every handover that has a ticket queues
exactly one outbox message, so the count of queued handovers is the count of
handover comments that would have been posted.
"""

from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.lifecycle import perform_handover
from rite_ai.reporting.outbox import list_pending

ROUNDS = 20
RACERS = 6


def _project(root: Path) -> Path:
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\nwhat:\n  kind: app\n"
        "technology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
    )
    return root


def _hand_over(args) -> int:
    root, barrier = Path(args[0]), args[1]
    barrier.wait()
    return perform_handover(root, worker="alpha", reason="race").released_claims


def _handovers_queued(root: Path) -> list:
    return [m for m in list_pending(root) if m.kind == "handover"]


def test_released_nothing_posts_nothing(tmp_path):
    """The deterministic half: a handover that finds the claims already gone
    has nothing to hand over, and says nothing to the board."""
    root = _project(tmp_path)
    ClaimsLedger(root / ".rite" / "claims.json").claim(["src/a.py"], "alpha", "ABC-1")

    first = perform_handover(root, worker="alpha", reason="stop")
    second = perform_handover(root, worker="alpha", reason="window boundary")

    assert (first.released_claims, first.ticket) == (1, "ABC-1")
    assert (second.released_claims, second.ticket) == (0, "")
    assert len(_handovers_queued(root)) == 1


def test_racing_handovers_of_one_worker_hand_over_once(tmp_path):
    """The race itself, in real processes released together by a barrier.
    Every round, exactly one caller releases the claims and exactly one
    handover is queued."""
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager, ctx.Pool(RACERS) as pool:
        for n in range(ROUNDS):
            root = _project(tmp_path / f"r{n}")
            ClaimsLedger(root / ".rite" / "claims.json").claim(
                ["src/a.py", "src/b.py"], "alpha", "ABC-1"
            )
            barrier = manager.Barrier(RACERS)
            released = pool.map(_hand_over, [(str(root), barrier)] * RACERS)

            assert sorted(released) == [0] * (RACERS - 1) + [1], (n, released)
            queued = _handovers_queued(root)
            assert len(queued) == 1, (n, [m.payload for m in queued])
            assert queued[0].payload["ticket"] == "ABC-1"
