"""P2-4a: a Manager publishes liveness, and the Owner reads it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rite_ai.coordination.heartbeat import (
    NotPublished,
    Published,
    is_stalled,
    liveness,
    publish_heartbeat,
)
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.state_layer import ABSENT, Present, Unavailable

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
KEY = "managers/manager-alpha.json"


def _layer(tmp_path: Path) -> LocalStateLayer:
    return LocalStateLayer(tmp_path / "state")


def _status(layer) -> dict:
    read = layer.read_state(KEY)
    assert isinstance(read, Present), read
    return json.loads(read.value)


def test_a_first_heartbeat_records_who_what_and_when(tmp_path):
    layer = _layer(tmp_path)
    result = publish_heartbeat(
        layer, "manager-alpha", workers=["w1", "w2"], in_flight=2, now=NOW
    )
    assert isinstance(result, Published) and not result.note
    assert _status(layer) == {
        "name": "manager-alpha",
        "last_seen": "2026-09-17T12:00:00Z",
        "workers": ["w1", "w2"],
        "in_flight": 2,
    }


def test_it_keeps_everything_it_does_not_own(tmp_path):
    layer = _layer(tmp_path)
    layer.write_state(
        KEY,
        json.dumps(
            {
                "name": "manager-alpha",
                "last_seen": "old",
                "workers": [{"name": "w1"}],
                "capabilities": {"gpu": True},
            }
        ).encode(),
        ABSENT,
    )
    publish_heartbeat(layer, "manager-alpha", workers=["w1"], in_flight=0, now=NOW)
    after = _status(layer)
    assert after["capabilities"] == {"gpu": True}
    assert after["workers"] == ["w1"], "the writer's own field is the writer's"
    assert after["last_seen"] == "2026-09-17T12:00:00Z"


def test_a_conflict_is_retried_against_the_state_it_lost_to(tmp_path):

    class RacesOnce(LocalStateLayer):
        raced = False

        def write_state(self, key, value, expected_version):
            if not RacesOnce.raced:
                RacesOnce.raced = True
                # Someone else writes a different key first.
                super().write_state("claims.json", b"{}", expected_version)
            return super().write_state(key, value, expected_version)

    racing = RacesOnce(tmp_path / "state")
    result = publish_heartbeat(
        racing, "manager-alpha", workers=[], in_flight=1, now=NOW
    )
    assert isinstance(result, Published) and result.conflicts == 1
    assert json.loads(racing.read_state("claims.json").value) == {}
    assert _status(racing)["in_flight"] == 1


def test_unavailable_is_never_read_as_a_lost_race(tmp_path):

    class Flaky(LocalStateLayer):
        def write_state(self, key, value, expected_version):
            return Unavailable("network went away mid-push")

    result = publish_heartbeat(
        Flaky(tmp_path / "state"), "manager-alpha", workers=[], in_flight=0, now=NOW
    )
    assert isinstance(result, NotPublished) and result.may_have_landed


def test_its_own_unreadable_status_is_replaced_and_said_so(tmp_path):
    layer = _layer(tmp_path)
    layer.write_state(KEY, b"{ truncated", ABSENT)
    result = publish_heartbeat(layer, "manager-alpha", workers=[], in_flight=0, now=NOW)
    assert isinstance(result, Published) and "could not be parsed" in result.note
    assert _status(layer)["name"] == "manager-alpha"


def test_a_name_that_cannot_be_a_key_is_refused(tmp_path):
    result = publish_heartbeat(
        _layer(tmp_path), "../escape", workers=[], in_flight=0, now=NOW
    )
    assert isinstance(result, NotPublished)


class TestReadingSomeoneElsesHeartbeat:
    def _published(self, tmp_path, minutes_ago: int):
        layer = _layer(tmp_path)
        publish_heartbeat(
            layer,
            "manager-alpha",
            workers=[],
            in_flight=0,
            now=NOW - timedelta(minutes=minutes_ago),
        )
        return layer

    def test_missed_intervals_are_counted(self, tmp_path):
        layer = self._published(tmp_path, 25)
        live = liveness(layer, "manager-alpha", now=NOW, interval_minutes=10)
        assert live.missed == 2

    def test_stalled_at_the_threshold_not_before(self, tmp_path):
        layer = self._published(tmp_path, 30)
        live = liveness(layer, "manager-alpha", now=NOW, interval_minutes=10)
        assert is_stalled(live, stall_threshold=3)
        assert not is_stalled(
            liveness(
                self._published(tmp_path, 20),
                "manager-alpha",
                now=NOW,
                interval_minutes=10,
            ),
            stall_threshold=3,
        )

    def test_cannot_tell_is_not_stalled(self, tmp_path):
        layer = _layer(tmp_path)
        never = liveness(layer, "manager-alpha", now=NOW, interval_minutes=10)
        assert never.missed is None and not is_stalled(never, stall_threshold=1)

        layer.write_state(KEY, b"{ truncated", ABSENT)
        unreadable = liveness(layer, "manager-alpha", now=NOW, interval_minutes=10)
        assert unreadable.missed is None and not is_stalled(
            unreadable, stall_threshold=1
        )

        layer.write_state(
            KEY,
            json.dumps({"name": "a", "last_seen": {"at": "x"}}).encode(),
            layer.read_state(KEY).version,
        )
        drifted = liveness(layer, "manager-alpha", now=NOW, interval_minutes=10)
        assert drifted.missed is None
        assert not is_stalled(drifted, stall_threshold=1)

    def test_a_fresh_heartbeat_has_missed_nothing(self, tmp_path):
        live = liveness(
            self._published(tmp_path, 0), "manager-alpha", now=NOW, interval_minutes=10
        )
        assert live.missed == 0 and live.known
