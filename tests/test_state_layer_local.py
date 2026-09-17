"""The local filesystem backend, bound to the shared conformance suite.

Everything in `StateLayerConformance` runs here. Tests below the class are
local-only behaviour that has no git equivalent.
"""

from __future__ import annotations

from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.coordination.state_layer import ABSENT, Unavailable
from state_layer_conformance import StateLayerConformance


class TestLocalStateLayerConformance(StateLayerConformance):
    def new_store(self, tmp_path):
        root = tmp_path / "state"
        root.mkdir()
        return root

    def open_layer(self, store):
        return LocalStateLayer(store)

    def corrupt_state(self, store):
        (store / "state.json").write_text("{ this is not a snapshot")

    def corrupt_messages(self, store):
        with open(store / "messages.jsonl", "a") as f:
            f.write("not json at all\n")


class TestLocalBackendFailsClosedWithoutExclusion:
    """Where flock is a no-op (Docker, NFS, SMB) two writers could both be
    told their write succeeded. The backend refuses rather than races."""

    def test_writes_are_unavailable_when_flock_does_not_exclude(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "rite_ai.coordination.local_backend.exclusion_holds", lambda d: False
        )
        layer = LocalStateLayer(tmp_path)
        result = layer.write_state("owner-lease.json", b"{}", ABSENT)
        assert isinstance(result, Unavailable)
        assert "does not exclude" in result.reason
        assert isinstance(layer.append_message("x"), Unavailable)

    def test_reads_still_work_without_exclusion(self, tmp_path, monkeypatch):
        """Reading needs no lock — only writes race."""
        LocalStateLayer(tmp_path).write_state("a.json", b"x", ABSENT)
        monkeypatch.setattr(
            "rite_ai.coordination.local_backend.exclusion_holds", lambda d: False
        )
        assert LocalStateLayer(tmp_path).read_state("a.json").value == b"x"
