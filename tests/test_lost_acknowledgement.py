"""A lost acknowledgement must not cost the role (P2-2c, §2.4.2 step 5).

The hard case in the whole protocol, because the two failures look
IDENTICAL from the writer's side: a push that was rejected, and a push that
succeeded whose answer never came back. Treat the second as the first and an
Owner stands down while it still holds a valid lease — and the role changes
hands for a dropped packet.

The rule, stated once: **stand down only when a re-read genuinely names
someone else.** Never on a failed write, never on a conflict, never on
silence.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.election import Unknown, stand_for_owner
from rite_ai.coordination.git_backend import GitStateLayer
from rite_ai.coordination.lease import (
    LEASE_KEY,
    LeaseLost,
    OwnerLeaseHolder,
    Renewed,
    Uncertain,
)
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.state_layer import ABSENT, Unavailable, Written

START = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)


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
        managers=["alpha", "beta"],
        remote="x",
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )


@pytest.fixture
def layer(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    return LocalStateLayer(root)


class LostAck:
    """A state layer whose writes LAND and then report failure."""

    def __init__(self, inner, swallow_next=True):
        self.inner = inner
        self.swallow_next = swallow_next
        self.swallowed = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def write_state(self, key, value, expected):
        result = self.inner.write_state(key, value, expected)
        if self.swallow_next and isinstance(result, Written):
            self.swallow_next = False
            self.swallowed += 1
            return Unavailable("the connection dropped before the answer arrived")
        return result


class TestTheIncumbent:
    def test_a_renewal_whose_ack_was_lost_does_not_end_the_lease(self, layer, config):
        """The write LANDED. The answer did not come back. An Owner that
        stands down here gives up a lease it demonstrably holds."""
        clock = Clock()
        holder = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        holder.acquire()
        lossy = LostAck(layer)
        holder.layer = lossy

        clock.advance(minutes=5)
        first = holder.renew()
        assert isinstance(first, Uncertain), "silence is not a refusal"
        assert not isinstance(first, LeaseLost)
        assert holder.holds_role(), "the lease is still valid on our own clock"
        assert lossy.swallowed == 1

        # The next renewal re-reads, finds OUR OWN lease there, and carries on.
        got = holder.renew()
        assert isinstance(got, Renewed)
        assert got.lease.owner == "alpha"

    def test_a_failed_write_that_never_landed_is_also_survivable(self, layer, config):
        clock = Clock()
        holder = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        holder.acquire()

        class NeverLands:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def write_state(self, *a, **kw):
                return Unavailable("the remote is unreachable")

        holder.layer = NeverLands(layer)
        clock.advance(minutes=5)
        assert isinstance(holder.renew(), Uncertain)
        assert holder.holds_role()
        holder.layer = layer
        assert isinstance(holder.renew(), Renewed)

    def test_standing_down_happens_only_when_the_lease_names_someone_else(
        self, layer, config
    ):
        """The other half. The rule is not "never stand down" — it is
        "stand down on evidence"."""
        clock = Clock()
        holder = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        holder.acquire()
        clock.advance(minutes=20)
        OwnerLeaseHolder(layer, "beta", config, clock=clock).acquire()
        got = holder.renew()
        assert isinstance(got, LeaseLost)
        assert got.owner == "beta"

    def test_an_election_that_cannot_read_does_not_demote_the_incumbent(
        self, layer, config, tmp_path
    ):
        clock = Clock()
        holder = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        holder.acquire()
        (tmp_path / "state" / "state.json").write_text("{ not a snapshot")
        got = stand_for_owner(holder, heartbeat=HeartbeatConfig(), now=clock())
        assert isinstance(got, Unknown)
        assert holder.holds_role(), "an unreadable state does not end a valid lease"


class TestTheGitBackendItself:
    """§2.4.2 step 5 is owed by the backend: the conformance suite cannot
    see a lost acknowledgement from outside, because from outside it is
    indistinguishable from a write that never happened."""

    @pytest.fixture
    def remote(self, tmp_path):
        r = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(r)], check=True)
        return r

    def test_a_push_that_landed_but_reported_nothing_is_reported_written(
        self, tmp_path, remote
    ):
        class AckLost(GitStateLayer):
            def _push(self, refspec, branch, expected):
                real = super()._push(refspec, branch, expected)
                return ("unknown", "connection reset") if real[0] == "written" else real

        layer = AckLost(str(remote), tmp_path / "cache")
        got = layer.write_state("owner-lease.json", b"held", ABSENT)
        assert isinstance(got, Written), got
        # And it is genuinely on the remote, once, under that version.
        on_remote = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/state"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert on_remote == got.version

    def test_a_push_that_truly_failed_is_still_unavailable(self, tmp_path, remote):
        """The check must not manufacture success: it looks for OUR commit,
        not for any commit."""

        class NeverPushes(GitStateLayer):
            def _push(self, refspec, branch, expected):
                return "unknown", "connection reset"

        layer = NeverPushes(str(remote), tmp_path / "cache")
        assert isinstance(
            layer.write_state("owner-lease.json", b"held", ABSENT), Unavailable
        )

    def test_a_message_whose_ack_was_lost_is_not_appended_twice(
        self, tmp_path, remote
    ):
        """The audit log's version of the same hazard, and worse: a retry
        after a lost ack duplicates the entry, and a duplicated audit record
        is a false one."""

        class AckLost(GitStateLayer):
            swallowed = 0

            def _push(self, refspec, branch, expected):
                real = super()._push(refspec, branch, expected)
                if real[0] == "written" and AckLost.swallowed == 0:
                    AckLost.swallowed = 1
                    return "unknown", "connection reset"
                return real

        layer = AckLost(str(remote), tmp_path / "cache")
        layer.append_message("Rite-Event: promoted\n")
        got = layer.read_messages()
        assert len(got.items) == 1, [m.content for m in got.items]
        assert AckLost.swallowed == 1


class TestTheLeaseFileIsNeverBlindlyRewritten:
    def test_an_uncertain_read_never_writes(self, layer, config, tmp_path):
        """Belt and braces on the rule above: if we could not read the
        lease, we must not write one. Writing after an unreadable read is
        how a lost ack turns into a seizure."""
        clock = Clock()
        holder = OwnerLeaseHolder(layer, "alpha", config, clock=clock)
        holder.acquire()
        before = layer.read_state(LEASE_KEY).version

        class Blind:
            def __init__(self, inner):
                self.inner = inner
                self.writes = 0

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def read_state(self, key):
                return Unavailable("cannot read")

            def write_state(self, *a, **kw):
                self.writes += 1
                return self.inner.write_state(*a, **kw)

        blind = Blind(layer)
        holder.layer = blind
        assert isinstance(holder.renew(), Uncertain)
        assert isinstance(holder.acquire(), Uncertain)
        assert blind.writes == 0
        assert layer.read_state(LEASE_KEY).version == before
