from pathlib import Path

from rite_ai.handover import read_snapshot, write_snapshot


class TestWriteAndReadSnapshot:
    def test_no_snapshot_reads_as_none(self, tmp_path: Path):
        assert read_snapshot(tmp_path) is None

    def test_round_trips_all_fields(self, tmp_path: Path):
        write_snapshot(
            tmp_path,
            ticket="RW-9",
            progress="wiring the CLI",
            next_step="add tests",
            blockers=["waiting on credentials"],
        )
        snapshot = read_snapshot(tmp_path)
        assert snapshot is not None
        assert snapshot.ticket == "RW-9"
        assert snapshot.progress == "wiring the CLI"
        assert snapshot.next_step == "add tests"
        assert snapshot.blockers == ["waiting on credentials"]
        assert snapshot.timestamp > 0

    def test_each_write_replaces_the_previous_snapshot_entirely(self, tmp_path: Path):
        write_snapshot(tmp_path, ticket="RW-1", progress="first")
        write_snapshot(tmp_path, ticket="RW-2", progress="second")
        snapshot = read_snapshot(tmp_path)
        assert snapshot.ticket == "RW-2"
        assert snapshot.progress == "second"

    def test_omitted_fields_default_empty(self, tmp_path: Path):
        write_snapshot(tmp_path, ticket="RW-1")
        snapshot = read_snapshot(tmp_path)
        assert snapshot.progress == ""
        assert snapshot.next_step == ""
        assert snapshot.blockers == []

    def test_corrupt_snapshot_is_reported_not_a_crash_and_not_a_none(
        self, tmp_path: Path
    ):
        """This asserted `is None`, which is what every reader then
        rendered as "no handover snapshot recorded yet" — see
        `tests/test_rehearsal_round4.py`. Not crashing is still the
        requirement; disappearing is not."""
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        (rite_dir / "handover.json").write_text("not json")

        snapshot = read_snapshot(tmp_path)

        assert snapshot is not None
        assert snapshot.unreadable
        assert "handover.json" in snapshot.unreadable
