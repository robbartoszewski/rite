"""Standing for Owner (P2-2b, §2.4 "Promotion", D-42, D-60).

Every case here is a decision about who should hold the role, so every test
asserts the DECISION and, where it matters, that no write happened — a
promotion that quietly rewrote the lease while reporting "not owner" would
pass a looser test and produce two Owners.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.election import (
    Deferred,
    NotEligible,
    NotOwner,
    Promoted,
    StillOwner,
    Unknown,
    stand_for_owner,
)
from rite_ai.coordination.heartbeat import publish_heartbeat
from rite_ai.coordination.lease import LEASE_KEY, OwnerLeaseHolder
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.schemas import OwnerLease, lease_to_json

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


@pytest.fixture
def layer(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    return LocalStateLayer(root)


@pytest.fixture
def config():
    return CoordinationConfig(
        managers=["alpha", "beta", "gamma"],
        remote="git@example.invalid:x.git",
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )


def stand(layer, config, name, clock, **kw):
    holder = OwnerLeaseHolder(layer, name, config, clock=clock)
    return stand_for_owner(holder, heartbeat=BEAT, **kw), holder


def put_lease(layer, owner, expires, acquired=START):
    lease = OwnerLease(
        owner=owner,
        acquired=acquired.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires=expires.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(expires, datetime)
        else expires,
    )
    read = layer.read_state(LEASE_KEY)
    layer.write_state(LEASE_KEY, lease_to_json(lease).encode(), read.version)


def state_version(layer):
    return layer.read_state(LEASE_KEY).version


class TestWhoMayStand:
    def test_a_manager_alone_in_an_empty_list_is_owner_by_default(self, layer):
        """§2.4: "A Manager starting with no other active Manager becomes
        Owner by default" — including the single-machine case, where the
        list has not been filled in at all."""
        config = CoordinationConfig(managers=[], remote="x")
        got, _ = stand(layer, config, "solo", Clock())
        assert isinstance(got, Promoted)

    def test_a_manager_outside_a_non_empty_list_may_not_promote(self, layer, config):
        """The list is the org chart. An unlisted machine holding the role
        would be invisible to everyone else's priority reasoning: they would
        defer to names in the list while an outsider held it."""
        got, _ = stand(layer, config, "stranger", Clock())
        assert isinstance(got, NotEligible)
        assert "coordination.managers" in got.reason


class TestAFreeRole:
    def test_the_highest_priority_manager_takes_a_free_role(self, layer, config):
        got, holder = stand(layer, config, "alpha", Clock())
        assert isinstance(got, Promoted)
        assert holder.holds_role()

    def test_a_lower_priority_manager_defers_to_a_live_higher_one(self, layer, config):
        clock = Clock()
        publish_heartbeat(layer, "alpha", workers=[], in_flight=0, now=clock())
        got, _ = stand(layer, config, "beta", clock)
        assert isinstance(got, Deferred)
        assert got.to == "alpha"

    def test_a_lower_priority_manager_promotes_once_the_higher_one_stalls(
        self, layer, config
    ):
        """§3.4's stall threshold, not a second timer of the election's own."""
        clock = Clock()
        publish_heartbeat(layer, "alpha", workers=[], in_flight=0, now=clock())
        clock.advance(minutes=31)  # > 3 missed 10-minute intervals
        got, _ = stand(layer, config, "beta", clock)
        assert isinstance(got, Promoted)

    def test_a_manager_nobody_can_see_does_not_wedge_the_role(self, layer, config):
        """The inversion of the fail-closed rule, and the reason it is worth
        writing down: a machine listed in config that has never been set up
        is permanently "not known to be inactive". Deferring to it would
        leave the role vacant for ever, and NOTHING corrects a vacancy — an
        early promotion is corrected by graceful demotion."""
        got, _ = stand(layer, config, "gamma", Clock())
        assert isinstance(got, Promoted)
        assert got.unseen == ["alpha", "beta"], "the promotion must say who it outran"

    def test_deferring_beats_promoting_when_even_one_higher_manager_is_live(
        self, layer, config
    ):
        clock = Clock()
        publish_heartbeat(layer, "beta", workers=[], in_flight=0, now=clock())
        got, _ = stand(layer, config, "gamma", clock)
        assert isinstance(got, Deferred)
        assert got.to == "beta"


class TestALiveLease:
    def test_a_live_lease_is_never_seized_even_by_a_higher_priority_manager(
        self, layer, config
    ):
        """§2.4: no seizure. A returning Manager ASKS, through the promotion
        request. Seizing would interrupt work mid-operation, which is the
        inconsistency graceful demotion exists to avoid."""
        clock = Clock()
        put_lease(layer, "beta", START + timedelta(minutes=10))
        before = state_version(layer)
        got, holder = stand(layer, config, "alpha", clock)
        assert isinstance(got, NotOwner)
        assert got.owner == "beta"
        assert not got.higher_priority_than_us
        assert not holder.holds_role()
        assert state_version(layer) == before, "standing for Owner wrote to the state"

    def test_the_incumbent_reports_still_owner_without_extending_its_lease(
        self, layer, config
    ):
        """Renewal is the renewal loop's job. An election that also renewed
        would hide a failing renewal behind a passing election."""
        clock = Clock()
        put_lease(layer, "alpha", START + timedelta(minutes=10))
        before = state_version(layer)
        got, _ = stand(layer, config, "alpha", clock)
        assert isinstance(got, StillOwner)
        assert state_version(layer) == before

    def test_an_incumbent_that_outranks_us_is_reported_as_such(self, layer, config):
        put_lease(layer, "alpha", START + timedelta(minutes=10))
        got, _ = stand(layer, config, "gamma", Clock())
        assert isinstance(got, NotOwner)
        assert got.higher_priority_than_us


class TestTheSkewMargin:
    def test_promotion_does_not_happen_on_the_bare_expiry_boundary(self, layer, config):
        """D-42. The incumbent's own clock may not yet have prompted it to
        renew; promoting at `expires` is a non-crash, non-partition route to
        two Owners."""
        clock = Clock()
        put_lease(layer, "beta", START)
        clock.advance(seconds=59)
        got, _ = stand(layer, config, "alpha", clock)
        assert isinstance(got, NotOwner)

    def test_promotion_happens_once_the_margin_is_past(self, layer, config):
        clock = Clock()
        put_lease(layer, "beta", START)
        clock.advance(seconds=61)
        got, _ = stand(layer, config, "alpha", clock)
        assert isinstance(got, Promoted)

    def test_an_implausible_expiry_is_challenged_and_named(self, layer, config):
        """D-59: a lease a day ahead would otherwise wedge the role for ever,
        and it needs only a wrong clock."""
        put_lease(layer, "beta", START + timedelta(days=1))
        got, _ = stand(layer, config, "alpha", Clock())
        assert isinstance(got, Promoted)
        assert got.displaced_not_credible
        assert "clock is wrong" in got.displaced_not_credible


class TestWhenNothingCanBeRead:
    def test_an_unreadable_state_is_unknown_not_a_free_role(
        self, layer, config, tmp_path
    ):
        (tmp_path / "state" / "state.json").write_text("{ not a snapshot")
        got, holder = stand(layer, config, "alpha", Clock())
        assert isinstance(got, Unknown)
        assert not holder.holds_role()

    def test_losing_the_race_reports_the_winner_not_a_failure(self, layer, config):
        """Two Managers stand at once for a free role. The CAS decides, and
        the loser learns who won by re-reading — not by guessing."""
        clock = Clock()
        alpha = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        real_write = layer.write_state
        raced = {"done": False}

        def race(key, value, expected):
            if not raced["done"]:
                raced["done"] = True
                OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
            return real_write(key, value, expected)

        layer.write_state = race
        got = stand_for_owner(alpha, heartbeat=BEAT)
        layer.write_state = real_write
        assert isinstance(got, NotOwner)
        assert got.owner == "beta"
        assert not alpha.holds_role()
