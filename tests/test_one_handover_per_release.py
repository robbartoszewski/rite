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


# --- a ticket named explicitly follows the same rule --------------------------------


def _hand_over_naming_the_ticket(args) -> int:
    root, barrier = Path(args[0]), args[1]
    barrier.wait()
    return perform_handover(
        root, worker="alpha", reason="race", ticket="ABC-1"
    ).released_claims


def _notes_and_handovers(root: Path) -> tuple[list, list]:
    queued = _handovers_queued(root)
    notes = [m for m in queued if m.payload.get("released_nothing")]
    return notes, [m for m in queued if not m.payload.get("released_nothing")]


def test_a_named_ticket_with_nothing_released_gets_a_note_not_a_handover(tmp_path):
    """`rite stop --worker alpha --ticket ABC-1` after a window boundary has
    already handed alpha over. Handover is on by default (Robert, 2026-09-28),
    so it posts; what it posts says it released nothing and names both
    causes, because rite cannot tell them apart."""
    from rite_ai.lifecycle import stop

    root = _project(tmp_path)
    ClaimsLedger(root / ".rite" / "claims.json").claim(["src/a.py"], "alpha", "ABC-1")
    perform_handover(root, worker="alpha", reason="scheduled window boundary")

    result = stop(root, worker="alpha", reason="lunch", ticket="ABC-1")

    assert result.ok
    notes, handovers = _notes_and_handovers(root)
    assert len(handovers) == 1 and len(notes) == 1
    assert notes[0].payload["ticket"] == "ABC-1"
    assert "What waits there is a note that this stop released no claims" in (
        result.message
    )
    assert "not a handover" in result.message


def test_skip_handover_releases_the_claims_and_leaves_the_board_alone(tmp_path):
    from rite_ai.lifecycle import stop

    root = _project(tmp_path)
    ClaimsLedger(root / ".rite" / "claims.json").claim(["src/a.py"], "alpha", "ABC-1")

    result = stop(root, worker="alpha", reason="lunch", skip_handover=True)

    assert result.ok and result.released_claims == 1
    assert _handovers_queued(root) == []
    assert "handover skipped (--skip-handover)" in result.message
    assert ClaimsLedger(root / ".rite" / "claims.json").release_claims("alpha") == []


def test_skip_handover_with_a_named_ticket_posts_nothing_either(tmp_path):
    from rite_ai.lifecycle import stop

    root = _project(tmp_path)
    result = stop(root, worker="alpha", ticket="ABC-1", skip_handover=True)
    assert result.ok and _handovers_queued(root) == []


def test_the_cli_takes_the_flag(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    root = _project(tmp_path)
    ClaimsLedger(root / ".rite" / "claims.json").claim(["src/a.py"], "alpha", "ABC-1")
    got = CliRunner().invoke(
        cli, ["stop", str(root), "--worker", "alpha", "--skip-handover"]
    )
    assert got.exit_code == 0, got.output
    assert "handover skipped" in got.output
    assert _handovers_queued(root) == []


class _Board:
    def __init__(self):
        self.comments: list[tuple[str, str]] = []
        self.labels: list[tuple] = []

    def comment(self, ticket, text):
        self.comments.append((ticket, text))

    def label(self, ticket, add, remove=()):
        self.labels.append((ticket, add, remove))


def _deliver(root: Path, payload: dict) -> _Board:
    from rite_ai.lifecycle.commands import _deliver_via_backend
    from rite_ai.reporting.outbox import OutboxMessage

    board = _Board()
    assert _deliver_via_backend(board, root, [])(
        OutboxMessage("handover", payload, 0.0, Path(), project="acme")
    )
    return board


def test_the_note_asserts_no_handover_and_changes_no_label(tmp_path):
    root = _project(tmp_path)
    board = _deliver(
        root,
        {
            "worker": "alpha",
            "reason": "lunch",
            "ticket": "ABC-1",
            "released_claims": 0,
            "released_nothing": True,
        },
    )
    [(ticket, text)] = board.comments
    assert ticket == "ABC-1"
    assert "released no claims, so it handed nothing over" in text
    assert "another handover" in text and "held none" in text
    assert "rite cannot tell which" in text
    assert "rite handover:" not in text
    assert board.labels == []


def test_a_real_handover_still_says_so_and_relabels(tmp_path):
    root = _project(tmp_path)
    board = _deliver(
        root,
        {"worker": "alpha", "reason": "lunch", "ticket": "ABC-1", "released_claims": 1},
    )
    [(_, text)] = board.comments
    assert "rite handover: lunch" in text
    assert board.labels == [("ABC-1", ["scheduled"], ["alpha"])]


def test_racing_handovers_that_name_the_ticket_hand_over_once(tmp_path):
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager, ctx.Pool(RACERS) as pool:
        for n in range(ROUNDS):
            root = _project(tmp_path / f"r{n}")
            ClaimsLedger(root / ".rite" / "claims.json").claim(
                ["src/a.py"], "alpha", "ABC-1"
            )
            barrier = manager.Barrier(RACERS)
            pool.map(_hand_over_naming_the_ticket, [(str(root), barrier)] * RACERS)

            assert len(_handovers_queued(root)) == 1, n


def test_takeover_still_hands_over_though_it_releases_nothing_here(tmp_path):
    """Takeover hands over ANOTHER machine's board state; its
    `<machine>/<worker>` names match no local claim, by design. It opts out
    of the releaser rule by name, or every takeover would be silent."""
    root = _project(tmp_path)

    result = perform_handover(
        root,
        worker="other-machine/w1",
        reason="manager stalled",
        ticket="ABC-9",
        comment_without_release=True,
    )

    assert result.released_claims == 0
    assert result.ticket == "ABC-9"
    assert len(_handovers_queued(root)) == 1


def _stop_naming_the_ticket(args) -> int:
    from rite_ai.lifecycle import stop

    root, barrier = Path(args[0]), args[1]
    barrier.wait()
    return stop(root, worker="alpha", reason="race", ticket="ABC-1").released_claims


def test_racing_stops_that_name_the_ticket_hand_over_once_and_note_the_rest(
    tmp_path,
):
    """With handover on by default, every racing `rite stop --ticket` posts.
    Exactly one of them released the claims and hands over; each of the rest
    posts a note that it released nothing, never a second handover."""
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager, ctx.Pool(RACERS) as pool:
        for n in range(ROUNDS):
            root = _project(tmp_path / f"r{n}")
            ClaimsLedger(root / ".rite" / "claims.json").claim(
                ["src/a.py"], "alpha", "ABC-1"
            )
            barrier = manager.Barrier(RACERS)
            released = pool.map(
                _stop_naming_the_ticket, [(str(root), barrier)] * RACERS
            )

            assert sorted(released) == [0] * (RACERS - 1) + [1], (n, released)
            notes, handovers = _notes_and_handovers(root)
            assert (len(handovers), len(notes)) == (1, RACERS - 1), n
