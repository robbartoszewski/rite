"""The Manager's coordination loop (P2-4d, §2.4.1, D-16).

One tick is everything a Manager owes the fleet at one moment. The tests are
written as "two Managers and a clock", because every interesting property of
this loop is a property of what the OTHER Manager sees.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rite_ai.claims.ledger import Claim, ClaimsLedger
from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.claims_state import publish_claims
from rite_ai.coordination.demotion import REQUEST_KEY, request_promotion
from rite_ai.coordination.lease import LEASE_KEY, OwnerLeaseHolder
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.monitor import ManagerMonitor
from rite_ai.coordination.promotion import request_from_json
from rite_ai.coordination.schemas import lease_from_json
from rite_ai.coordination.state_layer import Absent, Unavailable

START = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
BEAT = HeartbeatConfig(interval_minutes=10, stall_threshold=3)


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)
        return self.now


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    rite_dir = root / ".rite"
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
    return root


@pytest.fixture
def config():
    return CoordinationConfig(
        managers=["alpha", "beta", "gamma"],
        remote="x",
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )


@pytest.fixture
def layer(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    return LocalStateLayer(root)


@pytest.fixture
def root(tmp_path):
    return _project(tmp_path)


def monitor(layer, config, name, clock, **kw):
    holder = OwnerLeaseHolder(layer, name, config, clock=clock)
    return ManagerMonitor(holder, heartbeat=BEAT, **kw)


def owner_of(layer):
    read = layer.read_state(LEASE_KEY)
    return None if isinstance(read, Absent) else lease_from_json(
        read.value.decode()
    ).owner


class TestTakingAndKeepingTheRole:
    def test_a_first_tick_with_no_owner_promotes(self, layer, config):
        m = monitor(layer, config, "alpha", Clock())
        tick = m.tick()
        assert tick.owner and tick.action == "promoted"
        assert owner_of(layer) == "alpha"

    def test_later_ticks_renew_rather_than_re_electing(self, layer, config):
        clock = Clock()
        m = monitor(layer, config, "alpha", clock)
        first = m.tick()
        clock.advance(minutes=5)
        second = m.tick()
        assert second.action == "renewed"
        assert second.owner
        # Renewing, not re-acquiring: the acquisition time is untouched and
        # the expiry has moved out.
        stored = lease_from_json(layer.read_state(LEASE_KEY).value.decode())
        assert stored.acquired == "2026-09-17T12:00:00Z"
        assert stored.expires == "2026-09-17T12:20:00Z"
        assert first.action == "promoted"

    def test_an_owner_that_cannot_renew_keeps_the_role_until_its_clock_says_no(
        self, layer, config, tmp_path
    ):
        clock = Clock()
        m = monitor(layer, config, "alpha", clock)
        m.tick()

        class Silent:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def write_state(self, *a, **kw):
                return Unavailable("the remote went away")

        m.holder.layer = Silent(layer)
        clock.advance(minutes=5)
        tick = m.tick()
        assert tick.action == "renewal uncertain"
        assert tick.owner, "silence is not a refusal"
        assert tick.problems
        clock.advance(minutes=11)
        assert not m.tick().owner, "but the clock is"


class TestPromotingProperly:
    def test_promotion_hands_over_the_outgoing_owners_work(
        self, layer, config, root
    ):
        """P2-3c wired to the moment it applies (D-14's third trigger).
        Promoting and leaving the old Owner's tickets assigned to a machine
        that is gone is the state `rite stop` exists to prevent."""
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        publish_claims(
            layer,
            "beta",
            [Claim(paths=["src/a.py"], worker="w1", ticket="ABC-7", timestamp=1.0)],
            now=clock(),
        )
        clock.advance(minutes=20)

        tick = monitor(layer, config, "alpha", clock, root=root).tick()
        assert tick.action == "promoted"
        assert tick.promoted_from == "beta"
        assert tick.handover is not None
        assert tick.handover.tickets == ["ABC-7"]
        assert "board not updated for ABC-7" in tick.problems

    def test_a_promotion_over_an_implausible_lease_says_a_clock_is_wrong(
        self, layer, config
    ):
        """D-55 must reach the caller, not just the state."""
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        beta.acquire()
        read = layer.read_state(LEASE_KEY)
        lease = lease_from_json(read.value.decode())
        lease.expires = "2027-01-01T00:00:00Z"
        from rite_ai.coordination.schemas import lease_to_json

        layer.write_state(LEASE_KEY, lease_to_json(lease).encode(), read.version)

        tick = monitor(layer, config, "alpha", clock).tick()
        assert tick.action == "promoted"
        assert any("clock is wrong" in p for p in tick.problems)

    def test_a_lower_priority_manager_defers_while_a_higher_one_is_alive(
        self, layer, config
    ):
        clock = Clock()
        monitor(layer, config, "alpha", clock, status=lambda: ([], 0)).tick()
        clock.advance(minutes=20)  # alpha's lease lapses, its heartbeat does not
        tick = monitor(layer, config, "gamma", clock).tick()
        assert tick.action == "deferred"
        assert owner_of(layer) == "alpha", "it promoted anyway"


class TestAskingForTheRoleBack:
    def test_a_higher_priority_manager_asks_rather_than_seizing(self, layer, config):
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        tick = monitor(layer, config, "alpha", clock).tick()
        assert tick.action == "not owner"
        assert tick.asked_for_promotion
        assert owner_of(layer) == "beta", "asking changed the lease"
        assert request_from_json(
            layer.read_state(REQUEST_KEY).value.decode()
        ).requester == "alpha"

    def test_it_asks_once_not_every_tick(self, layer, config):
        """A promotion request is a file, not a poll. Rewriting it every
        tick conflicts with every other writer on a whole-ref CAS for no
        gain (§2.4.2)."""
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        m = monitor(layer, config, "alpha", clock)
        assert m.tick().asked_for_promotion
        version = layer.read_state(REQUEST_KEY).version
        clock.advance(minutes=1)
        assert not m.tick().asked_for_promotion
        assert layer.read_state(REQUEST_KEY).version == version

    def test_a_lower_priority_manager_does_not_ask_at_all(self, layer, config):
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        tick = monitor(layer, config, "gamma", clock).tick()
        assert not tick.asked_for_promotion
        assert isinstance(layer.read_state(REQUEST_KEY), Absent)


class TestBeingAsked:
    def test_an_owner_reports_a_request_but_does_not_act_on_it(self, layer, config):
        """D-43: only the caller knows whether it is at an operation
        boundary. A timer that hands over mid-sequence produces exactly the
        inconsistency graceful demotion exists to avoid."""
        clock = Clock()
        m = monitor(layer, config, "beta", clock)
        m.tick()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        clock.advance(minutes=1)
        tick = m.tick()
        assert tick.asked_to_hand_over
        assert tick.owner, "it handed over from a timer"
        assert owner_of(layer) == "beta"

    def test_a_caller_at_a_boundary_hands_over(self, layer, config):
        clock = Clock()
        m = monitor(layer, config, "beta", clock, hand_over_when=lambda: True)
        m.tick()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        clock.advance(minutes=1)
        tick = m.tick()
        assert tick.action == "handed over"
        assert tick.handed_over_to == "alpha"
        assert not tick.owner


class TestWhenNothingCanBeRead:
    def test_an_unreadable_state_changes_nothing_and_says_so(
        self, layer, config, tmp_path
    ):
        (tmp_path / "state" / "state.json").write_text("{ not a snapshot")
        tick = monitor(layer, config, "alpha", Clock()).tick()
        assert tick.action == "unknown"
        assert not tick.owner
        assert tick.problems


class TestLiveness:
    def test_a_tick_publishes_the_heartbeat_when_told_how(self, layer, config):
        clock = Clock()
        monitor(layer, config, "alpha", clock, status=lambda: (["w1"], 2)).tick()
        from rite_ai.coordination.heartbeat import liveness

        live = liveness(layer, "alpha", now=clock(), interval_minutes=10)
        assert live.known and live.missed == 0

    def test_a_manager_that_was_not_told_how_does_not_pretend(self, layer, config):
        clock = Clock()
        monitor(layer, config, "alpha", clock).tick()
        from rite_ai.coordination.heartbeat import liveness

        assert not liveness(layer, "alpha", now=clock(), interval_minutes=10).known


class TestItKeepsOtherManagersClaims:
    def test_promotion_does_not_touch_our_own_workers_claims(
        self, layer, config, root
    ):
        ledger = ClaimsLedger(root / ".rite" / "claims.json")
        ledger.claim(["src/ours.py"], "w1", "OURS-1")
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        publish_claims(
            layer,
            "beta",
            [
                Claim(
                    paths=["src/theirs.py"],
                    worker="w1",
                    ticket="ABC-7",
                    timestamp=1.0,
                )
            ],
            now=clock(),
        )
        clock.advance(minutes=20)
        monitor(layer, config, "alpha", clock, root=root).tick()
        assert [c.ticket for c in ledger.list_claims()] == ["OURS-1"]
