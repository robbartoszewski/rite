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


class TestAMessageConsumedWhileListingIsNotAnError:
    """The CI failure in `test_blast_radius_concurrent`, made deterministic:
    `list_pending` globbed a message, another tick consumed it, and the read
    raised FileNotFoundError, crashing the listing tick and hiding every
    message after it. Only two things remove a message, `flush_outbox` after
    delivery and the scheduler's blocker reconciliation, so a vanished one
    was taken, and it is simply no longer pending."""

    def test_the_rest_are_still_listed(self, tmp_path: Path, monkeypatch):
        taken = enqueue(tmp_path, "blocker", {"detail": "consumed meanwhile"})
        kept = enqueue(tmp_path, "handover", {"worker": "alpha"})
        real_read_text = Path.read_text

        def another_tick_takes_it_first(self, *args, **kwargs):
            if self == taken:
                self.unlink()
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", another_tick_takes_it_first)
        msgs = list_pending(tmp_path)
        assert [m.path for m in msgs] == [kept]

    def test_a_flush_racing_it_delivers_what_remains(self, tmp_path: Path, monkeypatch):
        taken = enqueue(tmp_path, "handover", {"worker": "alpha"})
        enqueue(tmp_path, "handover", {"worker": "beta"})
        real_read_text = Path.read_text

        def another_tick_takes_it_first(self, *args, **kwargs):
            if self == taken:
                self.unlink(missing_ok=True)
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", another_tick_takes_it_first)
        seen = []
        assert flush_outbox(tmp_path, lambda m: seen.append(m.payload) or True) == 1
        assert seen == [{"worker": "beta"}]
