"""The coordination layer, over every backend (D-20, D-21, D-61).

`state_layer_conformance` proves the STATE LAYER is substitutable. That is
half the claim. The other half is whether anything built on it smuggled an
assumption about git back in — an election that expects a write to conflict
when another Manager's heartbeat lands, a demotion that expects versions to
be ordered, a consumer that reads one key and writes another with the same
version. None of that would fail on git, where it happens to be true.

So the whole stack runs here against three stores with nothing in common
but the interface:

    local   a file on disk, one snapshot, a lock
    git     a bare remote, force-push with a lease, whole-tree merges
    kv      a separate process over a socket, per-key compare-and-set,
            no trees, no refs, no merges (Redis's shape)

Every test is written against behaviour the SPEC states, never a store's
mechanism. If one of them needs a backend named, it does not belong here.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from kv_backend import KeyValueStateLayer, start_store
from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.demotion import (
    HandedOver,
    hand_over,
    pending_request,
    request_promotion,
)
from rite_ai.coordination.election import (
    Deferred,
    NotOwner,
    Promoted,
    StillOwner,
    stand_for_owner,
)
from rite_ai.coordination.git_backend import GitStateLayer
from rite_ai.coordination.heartbeat import is_stalled, liveness, publish_heartbeat
from rite_ai.coordination.lease import (
    LEASE_KEY,
    LeaseLost,
    OwnerLeaseHolder,
    Renewed,
)
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.message_log import (
    format_message,
    parse_message,
    promotion_event,
)
from rite_ai.coordination.publish import Published
from rite_ai.coordination.schemas import lease_from_json
from rite_ai.coordination.state_layer import Present

START = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
BEAT = HeartbeatConfig(interval_minutes=10, stall_threshold=3)


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)
        return self.now


@pytest.fixture(params=["local", "git", "kv"])
def open_layer(request, tmp_path):
    """A factory for FRESH handles on one shared store — every Manager in
    these tests opens its own, as separate machines would."""
    kind = request.param
    if kind == "local":
        root = tmp_path / "state"
        root.mkdir()
        yield lambda: LocalStateLayer(root)
        return
    if kind == "git":
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        counter = {"n": 0}

        def open_git():
            counter["n"] += 1
            return GitStateLayer(str(remote), tmp_path / f"cache-{counter['n']}")

        yield open_git
        return
    server, port = start_store()
    try:
        yield lambda: KeyValueStateLayer(port)
    finally:
        server.kill()
        server.wait(timeout=5)


@pytest.fixture
def config():
    return CoordinationConfig(
        managers=["alpha", "beta"],
        remote="x",
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )


def owner_of(layer):
    read = layer.read_state(LEASE_KEY)
    if not isinstance(read, Present):
        return None
    return lease_from_json(read.value.decode()).owner


class TestTheElection:
    def test_a_full_turnover_works_on_any_store(self, open_layer, config):
        """Promote, renew, lapse, promote again — §2.4's whole cycle."""
        clock = Clock()
        alpha = OwnerLeaseHolder(open_layer(), "alpha", config, clock=clock)
        beta = OwnerLeaseHolder(open_layer(), "beta", config, clock=clock)

        assert isinstance(stand_for_owner(alpha, heartbeat=BEAT), Promoted)
        assert isinstance(stand_for_owner(beta, heartbeat=BEAT), NotOwner)

        clock.advance(minutes=5)
        assert isinstance(alpha.renew(), Renewed)
        assert isinstance(stand_for_owner(alpha, heartbeat=BEAT), StillOwner)

        clock.advance(minutes=30)  # alpha stops renewing
        assert isinstance(stand_for_owner(beta, heartbeat=BEAT), Promoted)
        assert owner_of(beta.layer) == "beta"
        assert isinstance(alpha.renew(), LeaseLost)

    def test_the_skew_margin_holds_on_any_store(self, open_layer, config):
        """D-42 is arithmetic on timestamps, and must stay that way: a store
        that made expiry depend on its own clock or write order would pass
        on one backend and fail on another."""
        clock = Clock()
        alpha = OwnerLeaseHolder(open_layer(), "alpha", config, clock=clock)
        beta = OwnerLeaseHolder(open_layer(), "beta", config, clock=clock)
        stand_for_owner(alpha, heartbeat=BEAT)

        clock.advance(minutes=15, seconds=59)
        assert isinstance(stand_for_owner(beta, heartbeat=BEAT), NotOwner)
        clock.advance(seconds=2)
        assert isinstance(stand_for_owner(beta, heartbeat=BEAT), Promoted)

    def test_deferring_to_a_live_higher_priority_manager(self, open_layer, config):
        clock = Clock()
        alpha_layer = open_layer()
        publish_heartbeat(alpha_layer, "alpha", workers=[], in_flight=0, now=clock())
        beta = OwnerLeaseHolder(open_layer(), "beta", config, clock=clock)
        assert isinstance(stand_for_owner(beta, heartbeat=BEAT), Deferred)


class TestTheHazardPerKeyCASRemoves:
    def test_a_heartbeat_costs_the_owner_nothing_not_even_a_retry(
        self, open_layer, config
    ):
        """§2.4.2(b), the falsely-stalled hazard, at the level it bites.

        The heartbeat lands BETWEEN the Owner's read and its write — the
        interleaving that matters, and the one a sequential test never
        reaches. Under whole-state CAS the renewal conflicted and had to be
        retried; on a busy fleet that is constant, and §2.4.2 says it can end
        with a live Manager marked stalled.

        The assertion is the count, not the outcome: a retry still ends in
        Renewed, so only counting writes distinguishes a contract where
        unrelated traffic is free from one where it is merely survivable."""
        clock = Clock()
        owner_layer = open_layer()
        beta_layer = open_layer()
        alpha = OwnerLeaseHolder(owner_layer, "alpha", config, clock=clock)
        assert isinstance(stand_for_owner(alpha, heartbeat=BEAT), Promoted)

        writes = {"n": 0}
        real_write = owner_layer.write_state

        def beat_in_between(key, value, expected):
            writes["n"] += 1
            if writes["n"] == 1:
                published = publish_heartbeat(
                    beta_layer, "beta", workers=["w1"], in_flight=2, now=clock()
                )
                assert isinstance(published, Published)
            return real_write(key, value, expected)

        owner_layer.write_state = beat_in_between
        clock.advance(minutes=1)
        assert isinstance(alpha.renew(), Renewed), "a heartbeat broke the renewal"
        assert writes["n"] == 1, (
            f"the renewal took {writes['n']} writes — another Manager's "
            "heartbeat cost the Owner a retry"
        )

    def test_two_managers_publishing_at_once_do_not_conflict(self, open_layer, config):
        """Each Manager owns its own status file, so two of them writing at
        the same moment are not racing — on any store where that is true of
        the contract rather than of the file layout."""
        clock = Clock()
        alpha_layer = open_layer()
        beta_layer = open_layer()
        real_write = alpha_layer.write_state
        first = {"done": False}

        def beta_writes_in_between(key, value, expected):
            if not first["done"]:
                first["done"] = True
                publish_heartbeat(
                    beta_layer, "beta", workers=["w2"], in_flight=5, now=clock()
                )
            return real_write(key, value, expected)

        alpha_layer.write_state = beta_writes_in_between
        result = publish_heartbeat(
            alpha_layer, "alpha", workers=["w1"], in_flight=1, now=clock()
        )
        assert isinstance(result, Published)
        assert result.conflicts == 0, "another Manager's heartbeat was a conflict"
        alpha_layer.write_state = real_write
        reader = open_layer()
        for name in ("alpha", "beta"):
            live = liveness(reader, name, now=clock(), interval_minutes=10)
            assert live.known and not is_stalled(live, stall_threshold=3)


class TestGracefulDemotion:
    def test_the_round_trip_works_on_any_store(self, open_layer, config):
        clock = Clock()
        beta = OwnerLeaseHolder(open_layer(), "beta", config, clock=clock)
        alpha = OwnerLeaseHolder(open_layer(), "alpha", config, clock=clock)
        assert isinstance(stand_for_owner(beta, heartbeat=BEAT), Promoted)

        asked = request_promotion(
            alpha.layer, "alpha", "beta", managers=config.managers, now=clock()
        )
        assert not isinstance(asked, str)
        assert pending_request(beta.layer, "beta") is not None

        handed = hand_over(beta, now=clock())
        assert isinstance(handed, HandedOver) and handed.to == "alpha"

        clock.advance(seconds=61)  # the margin a released lease still costs
        assert isinstance(stand_for_owner(alpha, heartbeat=BEAT), Promoted)
        assert owner_of(alpha.layer) == "alpha"


class TestTheMessageLog:
    def test_a_promotion_event_round_trips_with_its_trailers(self, open_layer):
        """P2-0d's trailers are text, and must survive whatever the store
        does with a message — a commit body, a JSON string, a list entry."""
        layer = open_layer()
        layer.append_message(
            format_message(promotion_event("alpha", "beta", "lease-expired"))
        )
        got = layer.read_messages()
        parsed = parse_message(got.items[0].content)
        assert parsed.kind == "promotion"
        assert parsed.fields["Manager"] == "alpha"
        assert parsed.fields["Previous-Owner"] == "beta"
