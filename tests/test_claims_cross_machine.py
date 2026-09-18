"""P2-5b: a claim is checked against what other machines have published.

The local ledger alone cannot see a Worker on another machine holding the same
path — which is the whole reason claims are published (§5.2, D-19)."""

from __future__ import annotations

import json
from pathlib import Path

from rite_ai.claims.ledger import ClaimsLedger
from rite_ai.coordination.claims_state import CLAIMS_KEY, publish_claims
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.publish import NotPublished, Published
from rite_ai.coordination.state_layer import ABSENT, Unavailable


def _layer(tmp_path: Path) -> LocalStateLayer:
    return LocalStateLayer(tmp_path / "state")


def _ledger(tmp_path: Path, name: str = "local") -> ClaimsLedger:
    return ClaimsLedger(tmp_path / name / "claims.json")


def _beta_holds(layer, path: str = "src/api") -> None:
    beta = ClaimsLedger(Path(layer.root).parent / "beta" / "claims.json")
    assert beta.claim([path], "w9", ticket="ABC-9").ok
    assert isinstance(
        publish_claims(layer, "manager-beta", beta.list_claims()), Published
    )


def test_a_path_held_on_another_machine_is_refused(tmp_path):
    layer = _layer(tmp_path)
    _beta_holds(layer)
    result = _ledger(tmp_path).claim(
        ["src/api/users.py"], "w1", layer=layer, machine="manager-alpha"
    )
    assert not result.ok
    assert any("held by w9 on manager-beta" in o for o in result.overlaps), result


def test_a_free_path_is_granted_and_published(tmp_path):
    layer = _layer(tmp_path)
    _beta_holds(layer)
    ledger = _ledger(tmp_path)
    result = ledger.claim(
        ["docs"], "w1", ticket="ABC-1", layer=layer, machine="manager-alpha"
    )
    assert result.ok and result.message == ""
    state = json.loads(layer.read_state(CLAIMS_KEY).value)
    assert state["machines"]["manager-alpha"]["claims"][0]["paths"] == ["docs"]
    assert "manager-beta" in state["machines"], "another machine's entry survived"


def test_this_machines_own_published_entry_does_not_block_it(tmp_path):
    """The ledger is the source of truth for its own claims; a published copy
    of them may lag, and must not refuse the machine that wrote it."""
    layer = _layer(tmp_path)
    stale = {
        "machines": {
            "manager-alpha": {"claims": [{"worker": "old", "paths": ["docs"]}]}
        }
    }
    layer.write_state(CLAIMS_KEY, json.dumps(stale).encode(), ABSENT)
    result = _ledger(tmp_path).claim(
        ["docs"], "w1", layer=layer, machine="manager-alpha"
    )
    assert result.ok, result


def test_unreadable_published_claims_refuse_the_claim_and_change_nothing(tmp_path):
    layer = _layer(tmp_path)
    layer.write_state(CLAIMS_KEY, b"{ truncated", ABSENT)
    ledger = _ledger(tmp_path)
    result = ledger.claim(["docs"], "w1", layer=layer, machine="manager-alpha")
    assert not result.ok and "cannot be shown safe" in result.message
    assert ledger.list_claims() == []
    assert layer.read_state(CLAIMS_KEY).value == b"{ truncated"


def test_unreachable_state_refuses_too(tmp_path):
    class Down(LocalStateLayer):
        def read_state(self, key):
            return Unavailable("no network")

    ledger = _ledger(tmp_path)
    result = ledger.claim(["docs"], "w1", layer=Down(tmp_path / "state"), machine="a")
    assert not result.ok and "no network" in result.message
    assert ledger.list_claims() == []


def test_a_claim_that_could_not_be_published_says_so_but_is_held_locally(tmp_path):
    class Flaky(LocalStateLayer):
        def write_state(self, key, value, expected_version):
            return Unavailable("push died")

    ledger = _ledger(tmp_path)
    result = ledger.claim(["docs"], "w1", layer=Flaky(tmp_path / "state"), machine="a")
    assert result.ok and "not published" in result.message
    assert isinstance(ledger.last_publish, NotPublished)
    assert [c.paths for c in ledger.list_claims()] == [["docs"]]


def test_without_a_layer_nothing_changes(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.claim(["docs"], "w1").ok
    assert ledger.last_publish is None
    assert not ledger.claim(["docs"], "w2").ok
