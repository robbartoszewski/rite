"""A new Owner hands over the old one's work (P2-3c, D-14's third trigger).

D-14's row records that this caller was undercounted to "both" across the
plan, the shipped docstrings and the register itself. So the tests are about
the trigger existing AT ALL, and about the two ways wiring it naively goes
wrong: releasing our own worker's claim, and touching the board with no
audit record.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.coordination.claims_state import CLAIMS_KEY, publish_claims
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.message_log import parse_message
from rite_ai.coordination.state_layer import Unavailable
from rite_ai.coordination.takeover import (
    ToldTheBoard,
    Unknown,
    hand_over_outgoing_owner,
)

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)


def _setup(tmp_path: Path) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir(parents=True)
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: test.atlassian.net\n"
        "  projects: {workers: ABC, board: XYZ}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return tmp_path


@pytest.fixture
def layer(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    return LocalStateLayer(root)


@pytest.fixture
def root(tmp_path):
    return _setup(tmp_path / "project")


def publish(layer, machine, claims):
    """`claims` as (paths, worker, ticket) triples, published as that
    machine's ledger content."""
    from rite_ai.claims.ledger import Claim

    publish_claims(
        layer,
        machine,
        [
            Claim(paths=paths, worker=worker, ticket=ticket, timestamp=1.0)
            for paths, worker, ticket in claims
        ],
        now=NOW,
    )


class TestTheTriggerExists:
    def test_promotion_hands_over_the_outgoing_owners_tickets(self, root, layer):
        publish(
            layer,
            "beta",
            [(["src/a.py"], "w1", "ABC-1"), (["b.py"], "w2", "ABC-2")],
        )
        got = hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )
        assert isinstance(got, ToldTheBoard)
        assert got.tickets == ["ABC-1", "ABC-2"]

    def test_the_reason_names_the_promoting_manager(self, root, layer):
        """D-14's row and the ticket both specify this string. It is what a
        human reads on the board to understand why their ticket moved."""
        publish(layer, "beta", [(["src/a.py"], "w1", "ABC-1")])
        got = hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )
        assert got.reason == "owner lease expired, promoted by alpha"
        queued = json.loads(
            next((root / ".rite" / "outbox").glob("*.json")).read_text()
        )
        assert queued["payload"]["reason"] == "owner lease expired, promoted by alpha"

    def test_one_handover_per_ticket_not_per_claimed_path(self, root, layer):
        """Two comments on one ticket read as two handovers."""
        publish(
            layer,
            "beta",
            [(["src/a.py"], "w1", "ABC-1"), (["src/b.py"], "w1", "ABC-1")],
        )
        got = hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )
        assert got.tickets == ["ABC-1"]

    def test_a_promotion_with_nothing_in_flight_is_still_recorded(self, root, layer):
        got = hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )
        assert isinstance(got, ToldTheBoard)
        assert got.tickets == []
        assert layer.read_messages().items, "the promotion left no trace"


class TestTheTwoWaysThisGoesWrong:
    def test_our_own_identically_named_worker_keeps_its_claim(self, root, layer):
        """`w1` is the default worker name on EVERY machine. Handing over
        the outgoing Owner's `w1` by bare name releases our own `w1`'s
        claim: silent, local, cross-machine data loss on every promotion."""
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/ours.py"], "w1", "OURS-9")
        publish(layer, "beta", [(["src/theirs.py"], "w1", "ABC-1")])

        hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )

        ours = ClaimsLedger(root / ".rite" / "claims.json").list_claims()
        assert [c.ticket for c in ours] == ["OURS-9"], "it released our own claim"

    def test_the_board_is_not_touched_when_the_record_cannot_be_written(
        self, root, layer
    ):
        """The pattern `claims_state` set for cross-machine action: audit
        first. A promotion that quietly rearranged another machine's work
        with no trace is worse than one that did not happen."""
        publish(layer, "beta", [(["src/a.py"], "w1", "ABC-1")])

        class NoLog:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def append_message(self, content):
                return Unavailable("the log is unreachable")

        got = hand_over_outgoing_owner(
            root, NoLog(layer), new_owner="alpha", previous_owner="beta"
        )
        assert isinstance(got, Unknown)
        assert not (root / ".rite" / "outbox").exists(), "the board was touched anyway"

    def test_unreadable_claims_are_unknown_not_nothing_to_do(self, root, layer):
        """D-54. "It held nothing" and "we could not tell" must not be the
        same answer: promoting without handing over is a decision."""
        read = layer.read_state(CLAIMS_KEY)
        layer.write_state(CLAIMS_KEY, b"not json", read.version)
        got = hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )
        assert isinstance(got, Unknown)
        assert not layer.read_messages().items, "it recorded a promotion it aborted"


    def test_an_unreachable_state_is_unknown_too(self, root, layer):
        """The other half of the same distinction, and a different code
        path: "we could not reach it" is not "it held nothing" either.
        Caught by mutation — the test above only exercises bytes we CAN
        read but cannot parse."""

        class Unreachable:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def read_state(self, key):
                return Unavailable("the remote is unreachable")

        got = hand_over_outgoing_owner(
            root, Unreachable(layer), new_owner="alpha", previous_owner="beta"
        )
        assert isinstance(got, Unknown)
        assert not (root / ".rite" / "outbox").exists()


class TestTheRecord:
    def test_the_promotion_event_names_both_managers_and_the_reason(
        self, root, layer
    ):
        publish(layer, "beta", [(["src/a.py"], "w1", "ABC-1")])
        hand_over_outgoing_owner(
            root,
            layer,
            new_owner="alpha",
            previous_owner="beta",
            promotion_reason="lease-not-credible",
        )
        messages = layer.read_messages().items
        assert len(messages) == 1
        parsed = parse_message(messages[0].content)
        assert parsed.kind == "promotion"
        assert parsed.fields["Manager"] == "alpha"
        assert parsed.fields["Previous-Owner"] == "beta"
        assert parsed.fields["Reason"] == "lease-not-credible"

    def test_a_queued_handover_is_named_not_counted(self, root, layer):
        """With no backend configured the board is NOT updated. Saying which
        tickets are still waiting is the difference between a caller that can
        tell a human and one that reports success."""
        publish(layer, "beta", [(["src/a.py"], "w1", "ABC-1")])
        got = hand_over_outgoing_owner(
            root, layer, new_owner="alpha", previous_owner="beta"
        )
        assert got.queued == ["ABC-1"]
        assert got.board_updated == []
