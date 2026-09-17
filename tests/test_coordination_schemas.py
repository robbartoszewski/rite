"""The lease and Manager-status schemas (P2-0b), and D-59's ceiling.

§2.4.1 names the lease's four fields; §3.4 describes the status file only in
prose. These shapes are therefore a proposal — but two behaviours in here are
decisions, not proposals, and they are what this file mostly pins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rite_ai.coordination import (
    LeaseVerdict,
    ManagerStatus,
    OwnerLease,
    lease_from_json,
    lease_to_json,
    status_from_json,
    status_to_json,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
CFG = {"owner_lease_minutes": 15, "skew_tolerance_seconds": 60}


def _lease(minutes_ahead: float) -> OwnerLease:
    return OwnerLease(
        owner="mac-studio",
        expires=(NOW + timedelta(minutes=minutes_ahead)).isoformat(),
    )


class TestTheSkewMarginProtectsTheIncumbent:
    """§2.4.1: expired only once `now > expires + skew_tolerance`, never at
    the bare boundary — otherwise a challenger whose clock runs fast takes a
    lease the incumbent still believes it holds."""

    def test_a_live_lease_is_held(self):
        assert _lease(10).verdict(now=NOW, **CFG) == LeaseVerdict.HELD

    def test_just_past_expiry_is_still_held(self):
        """30 seconds past, inside the 60-second margin."""
        assert _lease(-0.5).verdict(now=NOW, **CFG) == LeaseVerdict.HELD

    def test_past_the_margin_is_expired(self):
        assert _lease(-5).verdict(now=NOW, **CFG) == LeaseVerdict.EXPIRED


class TestTheCredibilityCeiling:
    """D-59. The margin above protects against a fast CHALLENGER; this
    protects against a fast INCUMBENT, which had no rule and read literally
    was a permanent wedge — a Manager whose clock is a day ahead holding the
    role forever, needing no malice, only a wrong clock."""

    def test_a_lease_from_a_wildly_fast_clock_is_challengeable(self):
        assert _lease(60 * 24).verdict(now=NOW, **CFG) == LeaseVerdict.NOT_CREDIBLE

    def test_the_ceiling_is_lease_duration_plus_tolerance(self):
        """Derived from the two values already in `coordination:`, so there
        is no third number to keep in step."""
        assert _lease(16).verdict(now=NOW, **CFG) == LeaseVerdict.HELD  # 15m + 60s
        assert _lease(16.5).verdict(now=NOW, **CFG) == LeaseVerdict.NOT_CREDIBLE

    def test_raising_the_lease_duration_moves_the_ceiling(self):
        """The property that makes it maintainable: change one value and the
        ceiling follows, rather than silently rejecting honest leases."""
        long_cfg = {"owner_lease_minutes": 60, "skew_tolerance_seconds": 60}
        assert _lease(45).verdict(now=NOW, **long_cfg) == LeaseVerdict.HELD
        assert _lease(45).verdict(now=NOW, **CFG) == LeaseVerdict.NOT_CREDIBLE

    def test_not_credible_is_distinct_from_expired(self):
        """Both are challengeable, but only one means somebody's clock is
        wrong — and that is the half worth logging."""
        assert LeaseVerdict.NOT_CREDIBLE != LeaseVerdict.EXPIRED

    def test_an_unusable_expiry_is_not_credible_rather_than_expired(self):
        for bad in ("", "garbage", "2026-13-45T99:99:99Z"):
            assert (
                OwnerLease(expires=bad).verdict(now=NOW, **CFG)
                == LeaseVerdict.NOT_CREDIBLE
            ), bad

    def test_a_corrupt_timestamp_never_reads_as_freshly_renewed(self):
        """The failure that would hide the problem: defaulting an unparseable
        timestamp to `now` makes an invalid lease look healthy."""
        assert OwnerLease(expires="garbage").verdict(now=NOW, **CFG) != (
            LeaseVerdict.HELD
        )


class TestUnknownFieldsSurviveAWriterThatDoesNotKnowThem:
    """Every writer read-merge-writes the WHOLE state (§2.4.2), so an older
    rite routinely rewrites a file a newer one authored. Dropping unknown
    keys would silently delete them — the same shape as the delta-push
    hazard §2.4.2 warns about, and just as invisible."""

    def test_a_lease_round_trips_a_field_this_version_never_heard_of(self):
        out = lease_to_json(lease_from_json('{"owner":"a","term_id":7}'))
        assert '"term_id": 7' in out
        assert '"owner": "a"' in out

    def test_a_status_round_trips_unknown_fields(self):
        out = status_to_json(status_from_json('{"name":"m","in_flight":3}'))
        assert '"in_flight": 3' in out

    def test_known_fields_still_parse_normally(self):
        s = status_from_json('{"name":"m","last_seen":"t","workers":["w1","w2"]}')
        assert (s.name, s.last_seen, s.workers) == ("m", "t", ["w1", "w2"])
        assert s.extra == {}


class TestUnreadableIsNotAbsent:
    """D-58's half that lives here: a caller must be able to tell "could not
    read" from "not there". §2.4.2 step 2 treats a MISSING lease as the
    first-write case, and an unreadable one is emphatically not that."""

    def test_bad_json_is_none_not_an_empty_lease(self):
        assert lease_from_json("{not json") is None
        assert lease_from_json("[]") is None
        assert status_from_json("null") is None

    def test_an_empty_object_is_a_real_lease_not_a_failure(self):
        """Absent fields are a lease with defaults; unparseable bytes are
        not a lease at all. Collapsing the two is the bug."""
        assert lease_from_json("{}") == OwnerLease()

    def test_a_wrong_typed_worker_list_does_not_crash_the_parse(self):
        assert status_from_json('{"name":"m","workers":"w1"}').workers == []


class TestRoundTripStability:
    def test_lease_json_round_trips_unchanged(self):
        original = OwnerLease(
            owner="mac-studio",
            acquired="2026-09-16T11:45:00+00:00",
            expires="2026-09-16T12:00:00+00:00",
            priority=0,
        )
        assert lease_from_json(lease_to_json(original)) == original

    def test_status_json_round_trips_unchanged(self):
        original = ManagerStatus(
            name="laptop", last_seen="2026-09-16T12:00:00+00:00", workers=["w1"]
        )
        assert status_from_json(status_to_json(original)) == original


class TestPriorityIsWrittenForAuditAndIgnoredOnRead:
    """D-60. `coordination.managers` order is priority; the lease's own
    `priority` records what the holder believed at acquisition and never
    decides anything. A stale lease written before a reorder must not be
    able to override it."""

    def test_the_field_is_kept_and_round_trips(self):
        """Not dead weight: it is the audit trail for why a promotion went
        the way it did."""
        out = lease_to_json(OwnerLease(owner="a", expires="x", priority=3))
        assert lease_from_json(out).priority == 3

    def test_priority_cannot_change_a_verdict(self):
        """Two leases identical except for `priority` must be judged
        identically — if this ever fails, something has started reading the
        field for a decision."""
        base = _lease(10)
        for p in (0, 1, 99, -5):
            other = OwnerLease(owner=base.owner, expires=base.expires, priority=p)
            assert other.verdict(now=NOW, **CFG) == base.verdict(now=NOW, **CFG)
