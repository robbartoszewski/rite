"""Who holds the role, and who has gone quiet (§2.3, §2.4.1, §3.4).

§2.3 makes surfacing stalled Managers to a human the Owner's job. These
tests are about what a human is told — three states per Manager, never two,
and a lease reported with its VERDICT rather than its contents.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rite_ai.config.models import CoordinationConfig, HeartbeatConfig
from rite_ai.coordination.heartbeat import publish_heartbeat, status_key
from rite_ai.coordination.lease import LEASE_KEY, OwnerLeaseHolder
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.overview import (
    ALIVE,
    STALLED,
    UNKNOWN,
    format_overview,
    read_overview,
)
from rite_ai.coordination.schemas import OwnerLease, lease_to_json
from rite_ai.coordination.state_layer import ABSENT, Unavailable

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
BEAT = HeartbeatConfig(interval_minutes=10, stall_threshold=3)


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


def look(layer, config, now=NOW, **kw):
    return read_overview(layer, config, now=now, heartbeat=BEAT, **kw)


class TestTheRole:
    def test_a_held_lease_names_the_owner(self, layer, config):
        OwnerLeaseHolder(layer, "alpha", config, clock=lambda: NOW).acquire()
        overview = look(layer, config)
        assert overview.has_owner and overview.owner == "alpha"

    def test_no_lease_at_all_is_a_note_not_a_problem(self, layer, config):
        """A fleet that has not started yet is not broken."""
        overview = look(layer, config)
        assert not overview.has_owner
        assert overview.notes and not overview.problems

    def test_an_expired_lease_is_reported_as_no_owner(self, layer, config):
        OwnerLeaseHolder(layer, "alpha", config, clock=lambda: NOW).acquire()
        overview = look(layer, config, now=NOW + timedelta(minutes=20))
        assert not overview.has_owner
        assert overview.owner == "alpha", "it still says whose lease it was"

    def test_an_unreadable_lease_is_never_reported_as_nobody(self, layer, config):
        """The distinction the whole design rests on: 'could not read' must
        not render as 'no Owner', or a human reads a network blip as a
        vacancy and starts intervening."""

        class Blind:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def read_state(self, key):
                if key == LEASE_KEY:
                    return Unavailable("the remote is unreachable")
                return self.inner.read_state(key)

        overview = look(Blind(layer), config)
        assert not overview.has_owner
        assert any("could not be read" in n for n in overview.notes)
        assert not overview.problems, "unreachable is not something to act on"

    def test_an_implausible_lease_says_a_clock_is_wrong(self, layer, config):
        """D-59, and §2.4.1 wants it said distinctly — this is the only
        signal anybody gets that a machine's clock is wrong."""
        lease = OwnerLease(
            owner="beta",
            acquired="2026-09-17T12:00:00Z",
            expires="2027-01-01T00:00:00Z",
        )
        layer.write_state(LEASE_KEY, lease_to_json(lease).encode(), ABSENT)
        overview = look(layer, config)
        assert any("clock is wrong" in p for p in overview.problems)


class TestWhoIsAlive:
    def test_a_recent_heartbeat_is_alive(self, layer, config):
        publish_heartbeat(layer, "alpha", workers=["w1"], in_flight=0, now=NOW)
        states = {m.name: m.state for m in look(layer, config).managers}
        assert states["alpha"] == ALIVE

    def test_a_stalled_manager_is_a_problem_a_human_must_see(self, layer, config):
        """§2.3's actual requirement. A stall that is only a field on an
        object has not been surfaced to anybody."""
        publish_heartbeat(layer, "beta", workers=[], in_flight=0, now=NOW)
        overview = look(layer, config, now=NOW + timedelta(minutes=31))
        assert {m.name: m.state for m in overview.managers}["beta"] == STALLED
        assert any("beta" in p and "heartbeat" in p for p in overview.problems)

    def test_a_manager_that_never_published_is_unknown_not_stalled(self, layer, config):
        """D-58's shape again: a machine that was never set up is not a
        machine that died, and handing its work over would be a guess."""
        overview = look(layer, config)
        view = {m.name: m for m in overview.managers}["alpha"]
        assert view.state == UNKNOWN
        assert view.detail, "unknown without a reason is not a report"
        assert not any("alpha" in p for p in overview.problems)
        # And said ONCE: a per-Manager fact repeated as a note is noise.
        assert not any("alpha" in n for n in overview.notes)

    def test_an_unreadable_status_is_unknown_not_stalled(self, layer, config):
        key = status_key("alpha")
        read = layer.read_state(key)
        layer.write_state(key, b"not json", read.version)
        assert {m.name: m.state for m in look(layer, config).managers}[
            "alpha"
        ] == UNKNOWN

    def test_this_machine_is_marked(self, layer, config):
        publish_heartbeat(layer, "alpha", workers=[], in_flight=0, now=NOW)
        overview = look(layer, config, this_machine="alpha")
        assert [m.name for m in overview.managers if m.is_this_machine] == ["alpha"]


class TestAPendingHandover:
    """§2.4's graceful demotion has two halves, and nothing forces the
    second to happen promptly (Q10). A request that sits is invisible
    otherwise: the only symptom is that nothing changes."""

    def request(self, layer, config, requester="alpha", incumbent="beta", now=NOW):
        from rite_ai.coordination.demotion import request_promotion

        return request_promotion(
            layer, requester, incumbent, managers=config.managers, now=now
        )

    def test_a_fresh_request_is_a_note_not_a_problem(self, layer, config):
        """Asking is the protocol working. It becomes news only if the role
        does not move."""
        OwnerLeaseHolder(layer, "beta", config, clock=lambda: NOW).acquire()
        self.request(layer, config)
        overview = look(layer, config)
        assert any("has asked" in n for n in overview.notes)
        assert not overview.problems

    def test_a_request_older_than_a_whole_lease_is_a_problem(self, layer, config):
        """Either the incumbent is never at a boundary, or nothing is
        calling the handover at all — both need a human."""
        OwnerLeaseHolder(layer, "beta", config, clock=lambda: NOW).acquire()
        self.request(layer, config)
        later = NOW + timedelta(minutes=config.owner_lease_minutes + 1)
        overview = look(layer, config, now=later)
        assert any("has not moved" in p for p in overview.problems), overview.problems

    def test_a_request_meant_for_an_earlier_owner_is_not_reported(self, layer, config):
        """Nobody is waiting on it: the role already changed hands some
        other way. Reporting it sends a human looking for a handover that
        is not pending."""
        OwnerLeaseHolder(layer, "alpha", config, clock=lambda: NOW).acquire()
        self.request(layer, config, requester="alpha", incumbent="beta")
        overview = look(layer, config)
        assert not any("has asked" in n for n in overview.notes)
        assert not overview.problems

    def test_no_request_says_nothing_at_all(self, layer, config):
        OwnerLeaseHolder(layer, "beta", config, clock=lambda: NOW).acquire()
        assert not any("has asked" in n for n in look(layer, config).notes)


class TestTheEventLog:
    """Promotions, handovers and claim expiries are recorded so somebody can
    later ask why the role moved. Nothing read the log, so the answer was
    reachable only by running git against the remote by hand."""

    def test_events_come_back_newest_last(self, layer, config):
        from rite_ai.coordination.message_log import format_message, promotion_event
        from rite_ai.coordination.overview import recent_events

        for previous in ("", "alpha", "beta"):
            layer.append_message(
                format_message(promotion_event("gamma", previous, "lease-expired"))
            )
        lines = recent_events(layer)
        assert len(lines) == 3
        assert all(line.startswith("promotion:") for line in lines)
        assert "beta" in lines[-1], "chronological, so the last line is the latest"

    def test_only_the_tail_is_kept(self, layer, config):
        from rite_ai.coordination.message_log import format_message, promotion_event
        from rite_ai.coordination.overview import recent_events

        for i in range(9):
            layer.append_message(
                format_message(promotion_event(f"m{i}", "", "no-owner"))
            )
        assert len(recent_events(layer, limit=4)) == 4

    def test_an_empty_log_says_nothing(self, layer, config):
        from rite_ai.coordination.overview import recent_events

        assert recent_events(layer) == []

    def test_an_unreadable_log_is_reported_not_skipped(self, layer, config):
        """A log with a hole is the one thing an audit trail must not
        present as complete (D-58)."""
        from rite_ai.coordination.overview import recent_events

        class Blind:
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def read_messages(self, since=None):
                return Unavailable("the log is unreachable")

        lines = recent_events(Blind(layer))
        assert lines and "could not be read" in lines[0]

    def test_an_event_this_version_cannot_parse_is_still_counted(self, layer, config):
        """Hiding it would make the log look shorter than it is, which is
        the same lie as truncating it."""
        from rite_ai.coordination.overview import recent_events

        layer.append_message("not a rite event at all")
        assert recent_events(layer) == ["an event this version cannot read"]


class TestWhatAHumanReads:
    def test_the_role_comes_first(self, layer, config):
        """The first question in an incident is who is Owner."""
        OwnerLeaseHolder(layer, "alpha", config, clock=lambda: NOW).acquire()
        assert format_overview(look(layer, config))[0].startswith(
            "coordination: Owner is alpha"
        )

    def test_every_configured_manager_appears_even_when_silent(self, layer, config):
        """A Manager missing from the list reads as "fine"; a Manager listed
        as unknown reads as a question. Only one of those is true."""
        lines = format_overview(look(layer, config))
        assert any("alpha" in line for line in lines)
        assert any("beta" in line for line in lines)
