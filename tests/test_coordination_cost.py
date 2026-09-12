from pathlib import Path

from rite_ai.coordination_cost import (
    read_counts,
    record_merge_conflict,
    record_nothing_safe_to_start,
    record_refused_claim,
)


class TestCoordinationCostCounts:
    def test_no_file_reads_as_zero(self, tmp_path: Path):
        counts = read_counts(tmp_path)
        assert counts.refused_claims == 0
        assert counts.nothing_safe_to_start == 0
        assert counts.merge_conflicts == 0

    def test_each_counter_increments_independently(self, tmp_path: Path):
        record_refused_claim(tmp_path)
        record_refused_claim(tmp_path)
        record_nothing_safe_to_start(tmp_path)
        record_merge_conflict(tmp_path)
        record_merge_conflict(tmp_path)
        record_merge_conflict(tmp_path)

        counts = read_counts(tmp_path)
        assert counts.refused_claims == 2
        assert counts.nothing_safe_to_start == 1
        assert counts.merge_conflicts == 3

    def test_counts_persist_across_reads(self, tmp_path: Path):
        record_refused_claim(tmp_path)
        first = read_counts(tmp_path)
        second = read_counts(tmp_path)
        assert first == second

    def test_corrupt_file_reads_as_zero_not_a_crash(self, tmp_path: Path):
        rite_dir = tmp_path / ".rite"
        rite_dir.mkdir()
        (rite_dir / "coordination-cost.json").write_text("not json")
        counts = read_counts(tmp_path)
        assert counts.refused_claims == 0
