from pathlib import Path

from rite_ai.reporting.outbox import enqueue, flush_outbox, list_pending


class TestEnqueueAndList:
    def test_enqueue_creates_file(self, tmp_path: Path):
        path = enqueue(tmp_path, "handover", {"worker": "alpha"})
        assert path.exists()
        assert "handover" in path.name

    def test_list_returns_messages(self, tmp_path: Path):
        enqueue(tmp_path, "handover", {"worker": "alpha"})
        enqueue(tmp_path, "blocker", {"reason": "need access"})
        msgs = list_pending(tmp_path)
        assert len(msgs) == 2
        kinds = [m.kind for m in msgs]
        assert "handover" in kinds
        assert "blocker" in kinds

    def test_list_empty_dir(self, tmp_path: Path):
        assert list_pending(tmp_path) == []

    def test_list_no_dir(self, tmp_path: Path):
        assert list_pending(tmp_path / "nonexistent") == []


class TestFlush:
    def test_flush_delivers_and_deletes(self, tmp_path: Path):
        enqueue(tmp_path, "handover", {"worker": "alpha"})
        enqueue(tmp_path, "stall", {"worker": "beta"})
        delivered = flush_outbox(tmp_path, lambda msg: True)
        assert delivered == 2
        assert list_pending(tmp_path) == []

    def test_flush_keeps_failed_deliveries(self, tmp_path: Path):
        enqueue(tmp_path, "handover", {"worker": "alpha"})
        enqueue(tmp_path, "stall", {"worker": "beta"})
        delivered = flush_outbox(tmp_path, lambda msg: False)
        assert delivered == 0
        assert len(list_pending(tmp_path)) == 2

    def test_flush_partial(self, tmp_path: Path):
        enqueue(tmp_path, "handover", {"worker": "alpha"})
        enqueue(tmp_path, "stall", {"worker": "beta"})

        def deliver_only_handovers(msg):
            return msg.kind == "handover"

        delivered = flush_outbox(tmp_path, deliver_only_handovers)
        assert delivered == 1
        remaining = list_pending(tmp_path)
        assert len(remaining) == 1
        assert remaining[0].kind == "stall"
