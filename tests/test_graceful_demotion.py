"""Handing the role back without interrupting work (P2-2e, §2.4, D-43).

The protocol is five steps and the dangerous ones are the refusals: an
incumbent that hands over to the wrong Manager, or hands over while its own
writes may still be in flight, produces exactly the inconsistency graceful
demotion exists to avoid.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.demotion import (
    REQUEST_KEY,
    Asked,
    HandedOver,
    Refused,
    Unknown,
    hand_over,
    pending_request,
    request_promotion,
)
from rite_ai.coordination.election import Promoted, stand_for_owner
from rite_ai.coordination.lease import LEASE_KEY, OwnerLeaseHolder
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.promotion import request_from_json
from rite_ai.coordination.schemas import lease_from_json
from rite_ai.coordination.state_layer import Absent, Unavailable

START = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
BEAT = HeartbeatConfig()


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)
        return self.now


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


def owner_of(layer):
    read = layer.read_state(LEASE_KEY)
    if isinstance(read, Absent):
        return None
    return lease_from_json(read.value.decode()).owner


class TestAsking:
    def test_a_returning_manager_asks_rather_than_taking(self, layer, config):
        """§2.4: no seizure. The whole point is that the incumbent chooses
        its moment, so the request must not touch the lease at all."""
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        beta.acquire()
        got = request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        assert isinstance(got, Asked)
        assert owner_of(layer) == "beta", "asking changed the lease"
        assert beta.holds_role()

    def test_a_manager_that_does_not_outrank_the_incumbent_is_refused(
        self, layer, config
    ):
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        got = request_promotion(
            layer, "gamma", "beta", managers=config.managers, now=clock()
        )
        assert isinstance(got, Refused)
        assert isinstance(layer.read_state(REQUEST_KEY), Absent), "it asked anyway"

    def test_the_request_names_the_incumbent_it_was_meant_for(self, layer, config):
        clock = Clock()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        stored = request_from_json(layer.read_state(REQUEST_KEY).value.decode())
        assert stored.incumbent == "beta"
        assert stored.requester == "alpha"


class TestSeeingTheRequest:
    def test_an_owner_sees_a_request_addressed_to_it(self, layer, config):
        clock = Clock()
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        assert isinstance(pending_request(layer, "beta"), Asked)

    def test_a_request_meant_for_an_earlier_owner_is_not_acted_on(self, layer, config):
        """The requester asked, and the role changed hands some other way
        before anyone acted. Acting now would be a seizure by accident."""
        clock = Clock()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        assert pending_request(layer, "gamma") is None

    def test_an_unreadable_request_is_unknown_not_absent(self, layer, config):
        """D-58. An Owner that reads garbage as "nobody asked" ignores a
        returning Manager for ever."""
        read = layer.read_state(REQUEST_KEY)
        layer.write_state(REQUEST_KEY, b"not json", read.version)
        assert isinstance(pending_request(layer, "beta"), Unknown)


class TestHandingOver:
    def test_the_full_round_trip_ends_with_the_requester_as_owner(
        self, layer, config
    ):
        """Steps 1-5 end to end, including the skew wait the protocol as
        written still costs (§2.4.1) — asserted, not glossed."""
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        alpha = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        beta.acquire()

        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        # ... beta finishes ONE operation (D-43), then:
        got = hand_over(beta, now=clock())
        assert isinstance(got, HandedOver)
        assert got.to == "alpha"
        assert not beta.holds_role()

        # The request is cleared, so no later Owner acts on it again.
        assert pending_request(layer, "beta") is None

        # A released lease reads exactly like an expired one, so the margin
        # still applies. RAISED as a wart, asserted as the behaviour.
        assert not isinstance(stand_for_owner(alpha, heartbeat=BEAT), Promoted)
        clock.advance(seconds=61)
        assert isinstance(stand_for_owner(alpha, heartbeat=BEAT), Promoted)
        assert owner_of(layer) == "alpha"

    def test_an_owner_with_no_request_stays_owner(self, layer, config):
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        beta.acquire()
        assert isinstance(hand_over(beta, now=clock()), Refused)
        assert beta.holds_role()

    def test_a_request_from_a_lower_priority_manager_is_refused_on_the_config(
        self, layer, config
    ):
        """The authoritative check is the config order (D-60), not the
        request — a file must not be able to promote its own author."""
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        beta.acquire()
        # Written directly, bypassing request_promotion's own refusal.
        from rite_ai.coordination.promotion import PromotionRequest, request_to_json

        read = layer.read_state(REQUEST_KEY)
        layer.write_state(
            REQUEST_KEY,
            request_to_json(
                PromotionRequest(requester="gamma", requested="", incumbent="beta")
            ).encode(),
            read.version,
        )
        got = hand_over(beta, now=clock())
        assert isinstance(got, Refused)
        assert "outrank" in got.reason
        assert beta.holds_role()

    def test_we_do_not_release_when_our_own_state_cannot_be_re_read(
        self, layer, config
    ):
        """§2.4.3's whole purpose: the transfer step exists to make sure
        in-flight writes land before the successor reads. If we cannot even
        read our own state, releasing would hand over mid-write."""
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        beta.acquire()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )

        class ReadsFailAfterTheRequest:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def read_state(self, key):
                if key == LEASE_KEY:
                    return Unavailable("the remote went away")
                return self.inner.read_state(key)

        beta.layer = ReadsFailAfterTheRequest(layer)
        got = hand_over(beta, now=clock())
        assert isinstance(got, Unknown)
        assert beta.holds_role(), "it stood down without confirming its writes"
        assert owner_of(layer) == "beta"
        # And it must not have thrown the request away on its way out: a
        # cleared request with no handover loses the ask entirely, and the
        # returning Manager waits for a reply that will never come.
        beta.layer = layer
        assert isinstance(pending_request(layer, "beta"), Asked)

    def test_the_request_is_cleared_before_the_lease_is_released(
        self, layer, config
    ):
        """Order matters, and only one of the two orders is safe. Releasing
        first and failing to clear leaves a live request addressed to an
        Owner that no longer exists."""
        clock = Clock()
        beta = OwnerLeaseHolder(layer, "beta", config, clock=clock)
        beta.acquire()
        request_promotion(
            layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        order = []
        real_write = layer.write_state

        def watch(key, value, expected):
            order.append(key)
            return real_write(key, value, expected)

        layer.write_state = watch
        beta.layer = layer
        assert isinstance(hand_over(beta, now=clock()), HandedOver)
        layer.write_state = real_write
        assert order == [REQUEST_KEY, LEASE_KEY], order
