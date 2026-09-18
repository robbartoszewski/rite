"""P2-5c: claims published by a machine whose heartbeat has lapsed.

Expiring another session's claims is destructive, so Phase 1 requires
attribution, a reason and a durable audit trail. Doing it across machines must
not be the cheap way around that."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.coordination.claims_state import (
    CLAIMS_KEY,
    expire_offline_claims,
    publish_claims,
)
from rite_ai.coordination.heartbeat import publish_heartbeat
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.message_log import parse_message
from rite_ai.coordination.state_layer import ABSENT, Messages, Unavailable

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
EXPIRY = dict(by="manager-alpha", interval_minutes=10, stall_threshold=3)


def _layer(tmp_path: Path) -> LocalStateLayer:
    return LocalStateLayer(tmp_path / "state")


def _machine_with_claims(layer, name: str, minutes_ago: int, path: str = "src/api"):
    ledger = ClaimsLedger(Path(layer.root).parent / name / "claims.json")
    assert ledger.claim([path], "w9", ticket="ABC-9").ok
    publish_claims(
        layer, name, ledger.list_claims(), now=NOW - timedelta(minutes=minutes_ago)
    )
    publish_heartbeat(
        layer,
        name,
        workers=["w9"],
        in_flight=1,
        now=NOW - timedelta(minutes=minutes_ago),
    )


def _state(layer) -> dict:
    return json.loads(layer.read_state(CLAIMS_KEY).value)


def _messages(layer) -> list:
    read = layer.read_messages()
    assert isinstance(read, Messages)
    return [parse_message(m.content) for m in read.items]


def test_a_stalled_machines_claims_expire_with_an_audit_record(tmp_path):
    layer = _layer(tmp_path)
    _machine_with_claims(layer, "manager-beta", minutes_ago=45)
    result = expire_offline_claims(layer, now=NOW, **EXPIRY)
    assert result.expired == {"manager-beta": 1} and not result.refused

    entry = _state(layer)["machines"]["manager-beta"]
    assert entry["claims"] == []
    assert entry["expired_by"] == "manager-alpha"
    assert entry["expired_reason"] == "heartbeat-lapsed"

    (message,) = [m for m in _messages(layer) if m and m.kind == "claim-contention"]
    assert message.fields["Machine"] == "manager-beta"
    assert message.fields["By"] == "manager-alpha"
    assert message.fields["Reason"] == "heartbeat-lapsed"
    assert message.fields["Claims"] == "1"
    assert "Oldest-Claim" in message.fields


def test_a_live_machine_keeps_its_claims(tmp_path):
    layer = _layer(tmp_path)
    _machine_with_claims(layer, "manager-beta", minutes_ago=5)
    result = expire_offline_claims(layer, now=NOW, **EXPIRY)
    assert result.expired == {}
    assert "alive" in result.kept["manager-beta"]
    assert _state(layer)["machines"]["manager-beta"]["claims"]


def test_liveness_that_cannot_be_established_never_expires_anything(tmp_path):
    layer = _layer(tmp_path)
    ledger = ClaimsLedger(tmp_path / "beta" / "claims.json")
    assert ledger.claim(["src/api"], "w9").ok
    publish_claims(layer, "manager-beta", ledger.list_claims(), now=NOW)  # no heartbeat
    result = expire_offline_claims(layer, now=NOW, **EXPIRY)
    assert result.expired == {}
    assert "liveness unknown" in result.kept["manager-beta"]
    assert _state(layer)["machines"]["manager-beta"]["claims"]


def test_other_machines_and_unknown_keys_survive_an_expiry(tmp_path):
    layer = _layer(tmp_path)
    _machine_with_claims(layer, "manager-beta", minutes_ago=45)
    _machine_with_claims(layer, "manager-gamma", minutes_ago=1, path="docs")
    read = layer.read_state(CLAIMS_KEY)
    state = json.loads(read.value)
    state["written_by_a_newer_rite"] = {"x": 1}
    state["machines"]["manager-beta"]["capabilities"] = {"gpu": True}
    layer.write_state(CLAIMS_KEY, json.dumps(state).encode(), read.version)

    expire_offline_claims(layer, now=NOW, **EXPIRY)
    after = _state(layer)
    assert after["written_by_a_newer_rite"] == {"x": 1}
    assert after["machines"]["manager-beta"]["capabilities"] == {"gpu": True}
    assert after["machines"]["manager-gamma"]["claims"], "a live machine untouched"


def test_unreadable_claims_expire_nothing(tmp_path):
    layer = _layer(tmp_path)
    layer.write_state(CLAIMS_KEY, b"{ truncated", ABSENT)
    result = expire_offline_claims(layer, now=NOW, **EXPIRY)
    assert result.expired == {} and "could not be parsed" in result.refused
    assert layer.read_state(CLAIMS_KEY).value == b"{ truncated"


def test_an_expiry_that_cannot_be_recorded_does_not_happen(tmp_path):
    class NoLog(LocalStateLayer):
        def append_message(self, content):
            return Unavailable("message log unreachable")

    layer = NoLog(tmp_path / "state")
    _machine_with_claims(layer, "manager-beta", minutes_ago=45)
    result = expire_offline_claims(layer, now=NOW, **EXPIRY)
    assert result.expired == {}
    assert "attribution" in result.refused
    assert _state(layer)["machines"]["manager-beta"]["claims"], "claims untouched"


def test_a_machine_can_skip_itself(tmp_path):
    layer = _layer(tmp_path)
    _machine_with_claims(layer, "manager-alpha", minutes_ago=45)
    result = expire_offline_claims(
        layer, now=NOW, skip=frozenset({"manager-alpha"}), **EXPIRY
    )
    assert result.expired == {} and result.kept["manager-alpha"] == "skipped"
