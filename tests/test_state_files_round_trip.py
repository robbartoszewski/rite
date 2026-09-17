"""P2-1d: an older Manager rewriting a state file erases nothing a newer one wrote.

Exercised the way it happens: bytes a newer rite wrote are read through the
state layer by this version, one field is updated, and the file is written
back. Every key this version does not know, and every known key whose value
has a shape this version does not expect, must come back unchanged."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.promotion import request_from_json, request_to_json
from rite_ai.coordination.schemas import (
    LeaseVerdict,
    lease_from_json,
    lease_to_json,
    status_from_json,
    status_to_json,
)
from rite_ai.coordination.state_layer import ABSENT, Present, Written

NEWER_STATUS = {
    "name": "manager-alpha",
    "last_seen": "2026-09-17T08:00:00Z",
    # A newer rite changed `workers` from names to objects:
    "workers": [{"name": "w1", "capacity": 2}, {"name": "w2", "capacity": 1}],
    "in_flight": 3,
    # ...and added keys this version has never heard of:
    "capabilities": {"gpu": True, "regions": ["eu"]},
    "rite_version": "0.9.0",
}


def _layer(tmp_path: Path) -> LocalStateLayer:
    return LocalStateLayer(tmp_path / "state")


def _write(layer, key, data, expected=ABSENT) -> str:
    result = layer.write_state(key, json.dumps(data).encode(), expected)
    assert isinstance(result, Written), result
    return result.version


def test_an_older_heartbeat_keeps_everything_a_newer_manager_declared(tmp_path):
    layer = _layer(tmp_path)
    key = "managers/manager-alpha.json"
    _write(layer, key, NEWER_STATUS)

    read = layer.read_state(key)
    assert isinstance(read, Present)
    status = status_from_json(read.value.decode())
    assert status.workers == [], "a shape this version cannot use is a placeholder"
    status.last_seen = "2026-09-17T08:10:00Z"  # the only thing a heartbeat changes
    assert isinstance(
        layer.write_state(key, status_to_json(status).encode(), read.version), Written
    )

    after = json.loads(layer.read_state(key).value)
    assert after == {**NEWER_STATUS, "last_seen": "2026-09-17T08:10:00Z"}


def test_a_field_the_writer_sets_takes_the_writers_value(tmp_path):
    status = status_from_json(json.dumps(NEWER_STATUS))
    status.workers = []  # deliberately: this Manager now has none
    assert json.loads(status_to_json(status))["workers"] == []


def test_filling_a_placeholder_in_place_also_wins(tmp_path):
    status = status_from_json(json.dumps(NEWER_STATUS))
    status.workers.append("w3")
    assert json.loads(status_to_json(status))["workers"] == ["w3"]


def test_a_drifted_value_is_never_a_decision_input():
    lease = lease_from_json(
        json.dumps(
            {
                "owner": "a",
                "expires": {"at": "2026-09-17T08:00:00Z"},
                "priority": "high",
            }
        )
    )
    assert lease.expires == "" and lease.priority == 0
    verdict = lease.verdict(
        now=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
        owner_lease_minutes=15,
        skew_tolerance_seconds=60,
    )
    assert verdict == LeaseVerdict.NOT_CREDIBLE
    back = json.loads(lease_to_json(lease))
    assert (
        back["expires"] == {"at": "2026-09-17T08:00:00Z"} and back["priority"] == "high"
    )


def test_the_promotion_request_keeps_drift_too():
    raw = {"requester": "alpha", "incumbent": {"id": "beta"}, "why": "back from leave"}
    assert json.loads(request_to_json(request_from_json(json.dumps(raw)))) == {
        **raw,
        "requested": "",
    }


def _random_value(rng: random.Random, depth: int = 0):
    kind = rng.choice(
        ["str", "int", "bool", "null", "list", "dict"]
        if depth < 2
        else ["str", "int", "bool", "null"]
    )
    return {
        "str": lambda: rng.choice(["", "x", "2026-09-17T08:00:00Z", "ü"]),
        "int": lambda: rng.randint(-5, 5),
        "bool": lambda: rng.choice([True, False]),
        "null": lambda: None,
        "list": lambda: [
            _random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))
        ],
        "dict": lambda: {
            f"k{i}": _random_value(rng, depth + 1) for i in range(rng.randint(0, 3))
        },
    }[kind]()


@pytest.mark.parametrize(
    "parse,dump,known",
    [
        (
            status_from_json,
            status_to_json,
            ["name", "last_seen", "workers", "in_flight"],
        ),
        (lease_from_json, lease_to_json, ["owner", "acquired", "expires", "priority"]),
        (request_from_json, request_to_json, ["requester", "requested", "incumbent"]),
    ],
)
def test_any_object_round_trips_every_value_it_carried(parse, dump, known):
    rng = random.Random(20260917)
    for _ in range(300):
        raw = {
            k: _random_value(rng) for k in rng.sample(known, rng.randint(0, len(known)))
        }
        raw.update(
            {f"future_{i}": _random_value(rng) for i in range(rng.randint(0, 3))}
        )
        back = json.loads(dump(parse(json.dumps(raw))))
        for key, value in raw.items():
            assert back[key] == value, (key, value, back[key])
