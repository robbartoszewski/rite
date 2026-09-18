"""The Owner lease and its renewal loop (P2-2a, §2.4.1, D-17, D-59, D-60).

The clock is injected, so every boundary is asserted at the exact second
rather than by sleeping — a lease test that sleeps is a lease test somebody
marks flaky and skips six months from now.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rite_ai.config.models import CoordinationConfig
from rite_ai.coordination.lease import (
    LEASE_KEY,
    Acquired,
    HeldByOther,
    LeaseLost,
    OwnerLeaseHolder,
    Released,
    RenewalLoop,
    Renewed,
    Uncertain,
    renewal_interval,
)
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.schemas import OwnerLease, lease_from_json, lease_to_json

START = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)
        return self.now


@pytest.fixture
def config():
    return CoordinationConfig(
        managers=["alpha", "beta"],
        remote="git@example.invalid:x.git",
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )


@pytest.fixture
def layer(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    return LocalStateLayer(root)


def holder(layer, config, name="alpha", clock=None):
    return OwnerLeaseHolder(layer, name, config, clock=clock or Clock())


def put_lease(layer, lease):
    read = layer.read_state(LEASE_KEY)
    layer.write_state(LEASE_KEY, lease_to_json(lease).encode(), read.version)


def a_lease(owner="beta", expires=START + timedelta(minutes=10), **kw):
    return OwnerLease(
        owner=owner,
        acquired=kw.get("acquired", START.strftime("%Y-%m-%dT%H:%M:%SZ")),
        expires=expires.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(expires, datetime)
        else expires,
        priority=kw.get("priority", 1),
    )


class TestTakingIt:
    def test_an_absent_lease_is_taken(self, layer, config):
        clock = Clock()
        got = holder(layer, config, clock=clock).acquire()
        assert isinstance(got, Acquired)
        assert got.lease.owner == "alpha"
        assert got.lease.expires == "2026-09-17T12:15:00Z"

    def test_a_live_lease_belonging_to_someone_else_is_not_taken(self, layer, config):
        put_lease(layer, a_lease(owner="beta"))
        got = holder(layer, config).acquire()
        assert isinstance(got, HeldByOther)
        assert got.owner == "beta"

    def test_a_lease_is_not_taken_at_the_bare_expiry_boundary(self, layer, config):
        """§2.4.1's skew margin. Taking it the instant `expires` passes is
        the split brain the margin exists to prevent: the incumbent's own
        clock may not yet have prompted it to renew."""
        clock = Clock()
        put_lease(layer, a_lease(owner="beta", expires=START))
        clock.advance(seconds=30)  # past expiry, inside the 60s tolerance
        assert isinstance(holder(layer, config, clock=clock).acquire(), HeldByOther)
        clock.advance(seconds=31)  # now past expiry + tolerance
        assert isinstance(holder(layer, config, clock=clock).acquire(), Acquired)

    def test_an_implausible_expiry_is_challenged_and_reported(self, layer, config):
        """D-59. A lease a day ahead would otherwise wedge the role for ever,
        and it needs no malice — only a wrong clock. Taking it is half the
        requirement; §2.4.1 also requires saying so distinctly, because this
        is the only signal that somebody's clock is wrong."""
        put_lease(layer, a_lease(owner="beta", expires=START + timedelta(days=1)))
        got = holder(layer, config).acquire()
        assert isinstance(got, Acquired)
        assert got.displaced_not_credible
        assert "clock is wrong" in got.displaced_not_credible

    def test_our_own_live_lease_is_not_reacquired_but_reported_as_held(
        self, layer, config
    ):
        """A Manager that restarts inside its own lease window still holds
        the role; it must not reset `acquired` and lose when it took it."""
        clock = Clock()
        first = holder(layer, config, clock=clock).acquire()
        clock.advance(minutes=5)
        again = holder(layer, config, clock=clock).acquire()
        assert isinstance(again, Acquired)
        assert again.lease.acquired == first.lease.acquired

    def test_the_recorded_priority_is_the_config_order(self, layer, config):
        """D-60: written for audit, never read for a decision."""
        assert holder(layer, config, "beta").acquire().lease.priority == 1
        stored = lease_from_json(layer.read_state(LEASE_KEY).value.decode())
        assert stored.priority == 1

    def test_a_manager_absent_from_the_list_records_minus_one(self, layer, config):
        """Not 0 — 0 reads as "highest priority" in the audit trail."""
        assert holder(layer, config, "gamma").acquire().lease.priority == -1


class TestHoldingIt:
    def test_renewal_extends_the_expiry_and_keeps_the_acquisition_time(
        self, layer, config
    ):
        clock = Clock()
        h = holder(layer, config, clock=clock)
        first = h.acquire()
        clock.advance(minutes=5)
        got = h.renew()
        assert isinstance(got, Renewed)
        assert got.lease.expires == "2026-09-17T12:20:00Z"
        assert got.lease.acquired == first.lease.acquired

    def test_holds_role_lapses_on_the_clock_even_with_no_network_at_all(
        self, layer, config
    ):
        """THE point of a lease over a heartbeat (D-17). A hung Owner that
        cannot renew must stop being Owner on its own, with nobody telling
        it and nothing to ask."""
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        assert h.holds_role()
        clock.advance(minutes=14, seconds=59)
        assert h.holds_role()
        clock.advance(seconds=2)
        assert not h.holds_role()

    def test_a_lapsed_owner_is_told_to_re_elect_rather_than_renewing(
        self, layer, config
    ):
        """Silently extending a lapsed lease is exactly the hung-Owner-holds-
        for-ever failure the lease exists to remove."""
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        clock.advance(minutes=20)
        got = h.renew()
        assert isinstance(got, LeaseLost)
        assert "re-elect" in got.reason
        assert not h.holds_role()

    def test_losing_the_lease_to_another_manager_is_reported_with_the_owner(
        self, layer, config
    ):
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        clock.advance(minutes=20)
        holder(layer, config, "beta", clock=clock).acquire()
        got = h.renew()
        assert isinstance(got, LeaseLost)
        assert got.owner == "beta"

    def test_unknown_fields_survive_a_renewal(self, layer, config):
        """A newer rite's fields must not be deleted by an older one's
        renewal — the same silent-deletion shape as a delta push."""
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        stored = lease_from_json(layer.read_state(LEASE_KEY).value.decode())
        stored.extra["generation"] = 7
        put_lease(layer, stored)
        clock.advance(minutes=1)
        assert isinstance(h.renew(), Renewed)
        after = lease_from_json(layer.read_state(LEASE_KEY).value.decode())
        assert after.extra["generation"] == 7


class TestGivingItUp:
    def test_release_expires_the_lease_in_place_and_frees_it_immediately(
        self, layer, config
    ):
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        assert isinstance(h.release(), Released)
        assert not h.holds_role()
        # A DELIBERATE stand-down still costs the skew margin, because the
        # file cannot say why it expired: a successor reading `expires` in
        # the past cannot tell "I finished" from "my clock is ahead", and
        # §2.4.1's margin exists for the second case. So the role is free a
        # minute later, not instantly. RAISED, not decided: marking a
        # release explicitly would remove the wait, and would need a field
        # §2.4.1 does not have.
        assert isinstance(
            holder(layer, config, "beta", clock=clock).acquire(), HeldByOther
        )
        clock.advance(seconds=61)
        assert isinstance(
            holder(layer, config, "beta", clock=clock).acquire(), Acquired
        )

    def test_release_keeps_who_held_it_rather_than_deleting_the_file(
        self, layer, config
    ):
        h = holder(layer, config)
        h.acquire()
        h.release()
        stored = lease_from_json(layer.read_state(LEASE_KEY).value.decode())
        assert stored.owner == "alpha"
        assert stored.acquired


class TestWhenTheTruthIsUnavailable:
    """Uncertain is not "no". Both wrong readings of it produce a split
    brain or an abandoned role."""

    def test_an_unreadable_state_is_uncertain_not_free(self, layer, config, tmp_path):
        (tmp_path / "state" / "state.json").write_text("{ not a snapshot")
        got = holder(layer, config).acquire()
        assert isinstance(got, Uncertain)

    def test_an_unreadable_lease_file_is_uncertain_not_absent(self, layer, config):
        read = layer.read_state(LEASE_KEY)
        layer.write_state(LEASE_KEY, b"not json at all", read.version)
        assert isinstance(holder(layer, config).acquire(), Uncertain)

    def test_a_failed_renewal_is_uncertain_and_the_role_still_lapses(
        self, layer, config, tmp_path
    ):
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        (tmp_path / "state" / "state.json").write_text("{ not a snapshot")
        clock.advance(minutes=5)
        assert isinstance(h.renew(), Uncertain)
        assert h.holds_role(), "an unreadable remote does not end a valid lease"
        clock.advance(minutes=11)
        assert not h.holds_role(), "but the clock does"

    def test_two_managers_racing_for_a_free_lease_produce_one_owner(
        self, layer, config
    ):
        """The reason any of this exists. Both read the same absent lease;
        the compare-and-swap decides, and the loser is TOLD it lost rather
        than being left believing it won."""
        clock = Clock()
        alpha = holder(layer, config, "alpha", clock=clock)
        beta = holder(layer, config, "beta", clock=clock)
        real_write = layer.write_state
        raced = {"done": False}

        def race(key, value, expected):
            if not raced["done"]:
                raced["done"] = True  # beta gets in between alpha's read and write
                real_write(key, lease_to_json(a_lease(owner="beta")).encode(), expected)
            return real_write(key, value, expected)

        layer.write_state = race
        got = alpha.acquire()
        layer.write_state = real_write
        assert isinstance(got, HeldByOther), got
        assert got.owner == "beta"
        assert not alpha.holds_role()
        assert isinstance(beta.acquire(), Acquired)

    def test_a_conflicting_write_is_retried_not_reported_as_lost(self, layer, config):
        """A lost race on the lease itself is retried from a fresh read, not
        reported as losing the role: the re-read may still name us, and
        standing down on a retryable conflict hands the role away on
        ordinary load."""
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        real_write = layer.write_state
        calls = {"n": 0}

        def conflict_once(key, value, expected):
            calls["n"] += 1
            if calls["n"] == 1:
                # Somebody writes THIS key between our read and our write —
                # re-writing our own lease unchanged, so the re-read still
                # names us and the renewal can legitimately continue.
                current = layer.read_state(LEASE_KEY)
                real_write(LEASE_KEY, current.value + b" ", current.version)
            return real_write(key, value, expected)

        layer.write_state = conflict_once
        assert isinstance(h.renew(), Renewed)
        assert calls["n"] == 2


class TestTheRenewalLoop:
    def test_the_interval_leaves_room_for_two_failures(self, config):
        assert renewal_interval(config) == pytest.approx(300.0)
        assert renewal_interval(config) * 3 == config.owner_lease_minutes * 60

    def test_the_loop_reports_a_lost_lease_once_and_stops(self, layer, config):
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        lost = []
        loop = RenewalLoop(h, on_lost=lost.append)
        clock.advance(minutes=5)
        assert isinstance(loop.tick(), Renewed)
        clock.advance(minutes=30)
        assert isinstance(loop.tick(), LeaseLost)
        assert len(lost) == 1
        assert loop._stop.is_set()

    def test_the_loop_never_re_acquires_on_its_own(self, layer, config):
        """Standing for election again is a decision about priority, not
        about timing — the timer must not make it."""
        clock = Clock()
        h = holder(layer, config, clock=clock)
        h.acquire()
        loop = RenewalLoop(h)
        clock.advance(minutes=30)
        loop.tick()
        loop.tick()
        stored = lease_from_json(layer.read_state(LEASE_KEY).value.decode())
        assert stored.expires == "2026-09-17T12:15:00Z", "the loop re-took the role"


class TestItIsBackendAgnostic:
    def test_the_whole_lease_works_over_git(self, tmp_path, config):
        """The lease speaks only the state-layer interface, so the default
        backend must need no special case anywhere in it."""
        import subprocess

        from rite_ai.coordination.git_backend import GitStateLayer

        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        clock = Clock()
        alpha = OwnerLeaseHolder(
            GitStateLayer(str(remote), tmp_path / "a"), "alpha", config, clock=clock
        )
        beta = OwnerLeaseHolder(
            GitStateLayer(str(remote), tmp_path / "b"), "beta", config, clock=clock
        )
        assert isinstance(alpha.acquire(), Acquired)
        assert isinstance(beta.acquire(), HeldByOther)
        clock.advance(minutes=5)
        assert isinstance(alpha.renew(), Renewed)
        clock.advance(minutes=30)
        assert isinstance(beta.acquire(), Acquired)
        assert isinstance(alpha.renew(), LeaseLost)
