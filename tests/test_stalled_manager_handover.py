"""The Owner hands over a stalled Manager's work (P2-3b, D-14's SECOND
trigger).

D-14 says there is one handover function for all three triggers, and that
"do not write a second handover path" applies to the cross-machine wrapper
too — so this shares `hand_over_machines_work` with promotion (P2-3c), and
the tests below check the part that is NOT shared: deciding whether a
Manager is stalled at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rite_ai.claims.ledger import Claim
from rite_ai.coordination.claims_state import publish_claims
from rite_ai.coordination.heartbeat import publish_heartbeat
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.message_log import parse_message
from rite_ai.coordination.takeover import (
    Refused,
    ToldTheBoard,
    Unknown,
    hand_over_stalled_manager,
)

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n  site: test.atlassian.net\n"
        "  projects: {workers: ABC, board: XYZ}\n  credential: ''\n"
        "expertise: {}\npublish_gate:\n  scan_patterns: []\n"
        "  gitleaks_config: .rite/gitleaks.toml\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return root


@pytest.fixture
def layer(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    return LocalStateLayer(state)


@pytest.fixture
def root(tmp_path):
    return _project(tmp_path)


def with_work(layer, machine, ticket="ABC-5"):
    publish_claims(
        layer,
        machine,
        [Claim(paths=["src/a.py"], worker="w1", ticket=ticket, timestamp=1.0)],
        now=NOW,
    )


def hand_over(root, layer, *, stalled="beta", now=NOW):
    return hand_over_stalled_manager(
        root,
        layer,
        owner="alpha",
        stalled=stalled,
        now=now,
        interval_minutes=10,
        stall_threshold=3,
    )


class TestDecidingItIsStalled:
    def test_a_manager_past_the_threshold_has_its_work_handed_over(self, root, layer):
        publish_heartbeat(layer, "beta", workers=["w1"], in_flight=1, now=NOW)
        with_work(layer, "beta")
        got = hand_over(root, layer, now=NOW + timedelta(minutes=31))
        assert isinstance(got, ToldTheBoard)
        assert got.tickets == ["ABC-5"]
        assert got.reason == "manager stalled, handed over by alpha"

    def test_a_manager_still_beating_is_left_alone(self, root, layer):
        """The stall is re-established here rather than trusted: the
        caller's idea of "stalled" may be several ticks old, and taking work
        off a machine that is still doing it is the expensive mistake."""
        publish_heartbeat(layer, "beta", workers=["w1"], in_flight=1, now=NOW)
        with_work(layer, "beta")
        got = hand_over(root, layer, now=NOW + timedelta(minutes=9))
        assert isinstance(got, Refused)
        assert not (root / ".rite" / "outbox").exists()
        assert not layer.read_messages().items, "it recorded a handover it refused"

    def test_a_manager_that_never_published_is_not_declared_stalled(self, root, layer):
        """D-58: "cannot tell" is not "gone". A Manager with no heartbeat at
        all may be a machine that was never set up, and stripping tickets off
        the board on that basis is a guess with consequences."""
        with_work(layer, "beta")
        got = hand_over(root, layer)
        assert isinstance(got, Refused)
        assert "cannot tell" in got.reason

    def test_an_unreadable_heartbeat_is_also_cannot_tell(self, root, layer):
        from rite_ai.coordination.heartbeat import status_key

        key = status_key("beta")
        read = layer.read_state(key)
        layer.write_state(key, b"not json", read.version)
        with_work(layer, "beta")
        assert isinstance(hand_over(root, layer), Refused)


class TestItSharesTheOnePath:
    def test_the_record_is_written_before_the_board_is_touched(self, root, layer):
        publish_heartbeat(layer, "beta", workers=["w1"], in_flight=1, now=NOW)
        with_work(layer, "beta")
        got = hand_over(root, layer, now=NOW + timedelta(minutes=31))
        assert isinstance(got, ToldTheBoard)
        message = parse_message(layer.read_messages().items[0].content)
        assert message.kind == "handover"
        assert message.fields["Stalled"] == "beta"
        assert message.fields["Manager"] == "alpha"
        assert message.fields["Missed"] == "3"

    def test_unreadable_claims_are_unknown_not_nothing_to_do(self, root, layer):
        from rite_ai.coordination.claims_state import CLAIMS_KEY

        publish_heartbeat(layer, "beta", workers=["w1"], in_flight=1, now=NOW)
        read = layer.read_state(CLAIMS_KEY)
        layer.write_state(CLAIMS_KEY, b"not json", read.version)
        got = hand_over(root, layer, now=NOW + timedelta(minutes=31))
        assert isinstance(got, Unknown)

    def test_worker_names_are_qualified_by_machine(self, root, layer):
        """Shared with P2-3c, asserted here too because the consequence is
        silent: `w1` is the default worker name on every machine, and the
        bare name releases OUR w1's claim."""
        from rite_ai.claims.ledger import ClaimsLedger

        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/ours.py"], "w1", "OURS-2")
        publish_heartbeat(layer, "beta", workers=["w1"], in_flight=1, now=NOW)
        with_work(layer, "beta")
        hand_over(root, layer, now=NOW + timedelta(minutes=31))
        assert [c.ticket for c in ledger.list_claims()] == ["OURS-2"]
