"""`rite handover write --spec-fallback` (S-7b).

A Worker whose spec slice was not enough, and read the whole spec, says so in
the snapshot it already writes on a schedule. That is what feeds the
insufficiency rate, so it has to count each fallback once however often the
snapshot is rewritten.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.handover import read_snapshots
from rite_ai.spec.telemetry import FALLBACK, read_events


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    monkeypatch.setenv("RITE_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _write(*args: str):
    return CliRunner().invoke(cli, ["handover", "write", *args])


def _fallbacks(root: Path) -> list[tuple[str, str]]:
    return [(e.unit, e.worker) for e in read_events(root).events if e.kind == FALLBACK]


def test_a_fallback_is_kept_in_the_snapshot_and_recorded(project: Path):
    result = _write("--worker", "alpha", "--ticket", "NEWS-7", "--spec-fallback", "D-4")
    assert result.exit_code == 0, result.output
    (snapshot,) = read_snapshots(project)
    assert snapshot.spec_fallback == "D-4"
    assert _fallbacks(project) == [("D-4", "alpha")]


def test_rewriting_the_snapshot_does_not_count_the_same_fallback_again(project: Path):
    for progress in ("reading", "still reading", "done reading"):
        result = _write(
            "--worker", "alpha", "--progress", progress, "--spec-fallback", "D-4"
        )
        assert result.exit_code == 0, result.output
    assert _fallbacks(project) == [("D-4", "alpha")]


def test_a_new_unit_is_a_new_fallback(project: Path):
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "architecture")
    assert _fallbacks(project) == [("D-4", "alpha"), ("architecture", "alpha")]


def test_workers_are_counted_separately(project: Path):
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    _write("--worker", "beta", "--progress", "x", "--spec-fallback", "D-4")
    assert sorted(_fallbacks(project)) == [("D-4", "alpha"), ("D-4", "beta")]


def test_a_fallback_alone_is_something_to_record(project: Path):
    result = _write("--worker", "alpha", "--spec-fallback", "D-4")
    assert result.exit_code == 0, result.output


def test_a_write_without_it_records_no_fallback(project: Path):
    _write("--worker", "alpha", "--progress", "no spec needed")
    (snapshot,) = read_snapshots(project)
    assert snapshot.spec_fallback == ""
    assert _fallbacks(project) == []


def test_a_snapshot_written_before_the_field_existed_still_loads(project: Path):
    path = project / ".rite" / "handover" / "alpha.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"worker": "alpha", "ticket": "NEWS-1", "timestamp": 1.0}\n')
    (snapshot,) = read_snapshots(project)
    assert (snapshot.ticket, snapshot.spec_fallback, snapshot.unreadable) == (
        "NEWS-1",
        "",
        "",
    )


def test_handover_show_says_which_unit_fell_back(project: Path):
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    result = CliRunner().invoke(cli, ["handover", "show"])
    assert result.exit_code == 0, result.output
    assert "spec fallback: D-4" in result.output


# --- found in review: what else rewrites the snapshot ------------------------


def test_a_plain_write_between_does_not_make_the_same_fallback_count_twice(
    project: Path,
):
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    _write("--worker", "alpha", "--progress", "no flag this time")
    _write("--worker", "alpha", "--progress", "y", "--spec-fallback", "D-4")
    assert _fallbacks(project) == [("D-4", "alpha")]


def test_clear_does_not_make_the_same_fallback_count_twice(project: Path):
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    assert _write("--worker", "alpha", "--clear").exit_code == 0
    _write("--worker", "alpha", "--progress", "y", "--spec-fallback", "D-4")
    assert _fallbacks(project) == [("D-4", "alpha")]


def test_a_failed_snapshot_write_records_nothing(project: Path, monkeypatch):
    import rite_ai.handover as handover

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(handover, "write_snapshot", refuse)
    result = _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    assert result.exit_code != 0
    assert _fallbacks(project) == []


def test_an_unreadable_previous_snapshot_does_not_recount(project: Path):
    _write("--worker", "alpha", "--progress", "x", "--spec-fallback", "D-4")
    (project / ".rite" / "handover" / "alpha.json").write_text("{ not json")
    _write("--worker", "alpha", "--progress", "y", "--spec-fallback", "D-4")
    assert _fallbacks(project) == [("D-4", "alpha")]


def test_a_single_coordinator_session_counts_under_no_worker_name(project: Path):
    _write("--progress", "x", "--spec-fallback", "D-4")
    _write("--progress", "y", "--spec-fallback", "D-4")
    assert _fallbacks(project) == [("D-4", "")]


def test_the_refusal_names_the_new_option(project: Path):
    result = _write("--worker", "alpha")
    assert result.exit_code == 2
    assert "--spec-fallback" in result.output
