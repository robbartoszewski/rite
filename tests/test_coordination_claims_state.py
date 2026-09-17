"""P2-5a: a machine publishes its claims without touching anyone else's.

`claims.json` holds every machine's claims, which is what makes it different
from a Manager's own status file: unreadable bytes are refused, never
replaced (D-58)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.coordination.claims_state import (
    CLAIMS_KEY,
    publish_claims,
    published_claims,
)
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.publish import NotPublished, Published
from rite_ai.coordination.state_layer import ABSENT, Present, Unavailable

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _layer(tmp_path: Path) -> LocalStateLayer:
    return LocalStateLayer(tmp_path / "state")


def _state(layer) -> dict:
    read = layer.read_state(CLAIMS_KEY)
    assert isinstance(read, Present), read
    return json.loads(read.value)


def _ledger(tmp_path: Path) -> ClaimsLedger:
    return ClaimsLedger(tmp_path / ".rite" / "claims.json")


def test_the_ledgers_claims_become_this_machines_entry(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.claim(["src/api"], "w1", ticket="ABC-1").ok
    layer = _layer(tmp_path)

    result = ledger.publish(layer, "manager-alpha", now=NOW)
    assert isinstance(result, Published), result
    entry = _state(layer)["machines"]["manager-alpha"]
    assert entry["published"] == "2026-09-17T12:00:00Z"
    assert [(c["worker"], c["paths"], c["ticket"]) for c in entry["claims"]] == [
        ("w1", ["src/api"], "ABC-1")
    ]


def test_another_machines_entry_and_unknown_keys_survive(tmp_path):
    layer = _layer(tmp_path)
    existing = {
        "machines": {
            "manager-beta": {"published": "yesterday", "claims": [{"worker": "w9"}]},
            "manager-alpha": {"published": "old", "claims": []},
        },
        "written_by_a_newer_rite": {"shape": 1},
    }
    layer.write_state(CLAIMS_KEY, json.dumps(existing).encode(), ABSENT)

    ledger = _ledger(tmp_path)
    assert ledger.claim(["docs"], "w2").ok
    assert isinstance(ledger.publish(layer, "manager-alpha", now=NOW), Published)

    after = _state(layer)
    assert after["machines"]["manager-beta"] == existing["machines"]["manager-beta"]
    assert after["written_by_a_newer_rite"] == {"shape": 1}
    assert after["machines"]["manager-alpha"]["claims"][0]["worker"] == "w2"


def test_unreadable_claims_are_refused_not_replaced(tmp_path):
    layer = _layer(tmp_path)
    layer.write_state(CLAIMS_KEY, b"{ truncated", ABSENT)
    result = publish_claims(layer, "manager-alpha", [], now=NOW)
    assert isinstance(result, NotPublished)
    assert "destroy other machines' claims" in result.reason
    assert layer.read_state(CLAIMS_KEY).value == b"{ truncated"


def test_a_state_that_is_not_an_object_is_refused_too(tmp_path):
    layer = _layer(tmp_path)
    layer.write_state(CLAIMS_KEY, b"[]", ABSENT)
    assert isinstance(publish_claims(layer, "manager-alpha", [], now=NOW), NotPublished)
    assert layer.read_state(CLAIMS_KEY).value == b"[]"


def test_a_conflict_republishes_against_what_won(tmp_path):
    class RacesOnce(LocalStateLayer):
        raced = False

        def write_state(self, key, value, expected_version):
            if not RacesOnce.raced:
                RacesOnce.raced = True
                # The same key: per-key compare-and-swap means a write to a
                # different one is not a race, which is the whole point of
                # claims and leases not blocking each other.
                super().write_state(key, b"{}", expected_version)
            return super().write_state(key, value, expected_version)

    layer = RacesOnce(tmp_path / "state")
    result = publish_claims(layer, "manager-alpha", [], now=NOW)
    assert isinstance(result, Published) and result.conflicts == 1
    assert isinstance(layer.read_state(CLAIMS_KEY), Present)


def test_unavailable_says_it_may_have_landed(tmp_path):
    class Flaky(LocalStateLayer):
        def write_state(self, key, value, expected_version):
            return Unavailable("push died")

    result = publish_claims(Flaky(tmp_path / "state"), "manager-alpha", [], now=NOW)
    assert isinstance(result, NotPublished) and result.may_have_landed


def test_a_machine_name_that_cannot_be_a_key_is_refused(tmp_path):
    assert isinstance(
        publish_claims(_layer(tmp_path), "../escape", [], now=NOW), NotPublished
    )


def test_reading_skips_an_entry_it_cannot_use_rather_than_reporting_none(tmp_path):
    state = {
        "machines": {
            "good": {"claims": [{"worker": "w1", "paths": ["a"]}]},
            "broken": {"claims": "not a list"},
            "alien": 42,
        }
    }
    assert published_claims(state) == {"good": [{"worker": "w1", "paths": ["a"]}]}
