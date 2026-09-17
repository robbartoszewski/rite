"""`rite spec verify` — the gate `/spec-digest` finishes on (S-9).

A gate is only worth having if it fails. Every test here is a drift that must
exit non-zero, because the failure mode this guards against is a digest that
reads as current while a Worker loads text the spec no longer says.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.spec.digest_files import render_unit_file, unit_filename, units_dir

SPEC = """# Thing

## 1. Roles
Who does what. See D-1.

### 1.1. Worker
A Worker does the work, unless the Owner has taken the ticket back.

## 2. Handover
A session ending mid-ticket writes a snapshot. See §1.1.

## Decisions
| D | Decision | Choice | Why |
| D-1 | Who writes | The Worker | It holds the context |
"""

FILLER = "\n".join(
    f"## {n}. Filler {n}\nA paragraph about {n}. See §1.1.\n" for n in range(3, 60)
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(SPEC + FILLER)
    return tmp_path


def _run(*args: str):
    return CliRunner().invoke(cli, ["spec", *args], catch_exceptions=False)


def _write_unit(project: Path, unit_id: str, covers, body="derived text") -> Path:
    path = units_dir(project) / unit_filename(unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit_file(unit_id, tuple(covers), "SPEC.md", "", "", body))
    return path


def _digest_everything(project: Path) -> None:
    """What `/spec-digest` does, minus writing anything worth reading."""
    _run("index")
    from rite_ai.spec.graph import INDEX, build_graph, classify
    from rite_ai.spec.units import parse_paths

    parsed = parse_paths(project, ["SPEC.md"])
    kinds = classify(build_graph(parsed))
    for unit in parsed.units:
        if kinds.get(unit.id) != INDEX:
            _write_unit(project, unit.id, [unit.id], f"derived text for {unit.id}")
    assert _run("stamp", "--all").exit_code == 0


def test_a_complete_current_digest_passes(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    result = _run("verify")
    assert result.exit_code == 0, result.output
    assert "✓" in result.output


def test_a_unit_with_no_derived_file_fails(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    (units_dir(project) / unit_filename("1.1")).unlink()
    result = _run("verify")
    assert result.exit_code == 1
    assert "1.1" in result.output
    assert "still to write" in result.output


def test_an_unfinished_digest_is_reported_as_incomplete_not_as_drift(
    project: Path, monkeypatch
):
    """A first digest of a large spec is written over several passes — 189
    units for rite's own — and every pass but the last leaves units unwritten.
    Telling that session "a Worker would be reading something the spec no
    longer says" is false: nothing written has drifted."""
    monkeypatch.chdir(project)
    _run("index")
    _write_unit(project, "1.1", ["1.1"])
    _run("stamp", "1.1")
    result = _run("verify")
    assert result.exit_code == 1
    assert "incomplete:" in result.output and "1 of" in result.output
    assert "nothing that is written has drifted" in result.output
    assert "no longer says" not in result.output


def test_one_real_drift_stops_it_being_reported_as_merely_incomplete(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _run("index")
    path = _write_unit(project, "1.1", ["1.1"])
    _run("stamp", "1.1")
    path.write_text(path.read_text() + "\nby hand\n")
    result = _run("verify")
    assert result.exit_code == 1
    assert "edited by hand" in result.output
    assert "incomplete:" not in result.output


def test_a_stale_file_fails_and_says_to_re_digest(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    (project / "SPEC.md").write_text(
        (SPEC + FILLER).replace("A Worker does the work", "A Worker now does the work")
    )
    result = _run("verify")
    assert result.exit_code == 1
    assert "stale" in result.output and "re-digest" in result.output


def test_a_hand_edited_file_fails(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    path = units_dir(project) / unit_filename("1.1")
    path.write_text(path.read_text() + "\nsomeone typed this\n")
    result = _run("verify")
    assert result.exit_code == 1
    assert "edited by hand" in result.output


def test_a_file_covering_a_deleted_unit_fails(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    _write_unit(project, "9.9", ["9.9"])
    result = _run("verify")
    assert result.exit_code == 1
    assert "no longer has" in result.output


def test_an_unstamped_file_fails(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    path = units_dir(project) / unit_filename("1.1")
    path.write_text(render_unit_file("1.1", ("1.1",), "SPEC.md", "", "", "rewritten"))
    result = _run("verify")
    assert result.exit_code == 1
    assert "never stamped" in result.output


def test_a_file_that_is_not_a_unit_file_fails_rather_than_being_skipped(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _digest_everything(project)
    (units_dir(project) / "notes.md").write_text("someone's scratch notes\n")
    result = _run("verify")
    assert result.exit_code == 1
    assert "notes.md" in result.output


def test_an_index_that_no_longer_matches_the_spec_fails(project: Path, monkeypatch):
    """The index is what a slice is computed from; a stale one hands a Worker
    the wrong line ranges."""
    monkeypatch.chdir(project)
    _digest_everything(project)
    assert _run("verify").exit_code == 0
    (project / "SPEC.md").write_text(SPEC + FILLER + "\n## 99. Late arrival\ntext\n")
    result = _run("verify")
    assert result.exit_code == 1
    assert "rite spec index" in result.output


def test_a_missing_index_fails(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _digest_everything(project)
    from rite_ai.spec.index_file import index_path

    index_path(project).unlink()
    result = _run("verify")
    assert result.exit_code == 1
    assert "index" in result.output


def test_overlap_passes_by_default_and_fails_under_strict(project: Path, monkeypatch):
    """Merging two sections into one derived unit is allowed; being covered
    twice is a question about which one a Worker gets, and --strict is where
    a project answers it."""
    monkeypatch.chdir(project)
    _digest_everything(project)
    second = units_dir(project) / "also-1-1.md"
    second.write_text(render_unit_file("extra", ("1.1",), "SPEC.md", "", "", "again"))
    assert _run("stamp", "--all").exit_code == 0
    assert _run("verify").exit_code == 0
    strict = _run("verify", "--strict")
    assert strict.exit_code == 1
    assert "more than one derived file" in strict.output and "1.1" in strict.output


def test_a_gate_that_could_not_run_does_not_share_an_exit_code_with_a_pass(
    tmp_path: Path, monkeypatch
):
    """Exit 3, not 0 and not 1: `rite spec verify && deploy` must not proceed
    because no spec was registered, and a CI script must be able to tell
    "nothing to check" from "the digest has drifted"."""
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths: []\n")
    monkeypatch.chdir(tmp_path)
    assert _run("verify").exit_code == 3

    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - docs/\n")
    (tmp_path / "docs").mkdir()
    assert _run("verify").exit_code == 3


def test_what_failed_goes_to_stderr_and_a_pass_says_so_on_stdout(
    project: Path, monkeypatch
):
    """A gate wired into a script is read two ways: the exit code by the
    script, the reason by whoever reads the log. Neither should have to be
    separated out of the other."""
    monkeypatch.chdir(project)
    _digest_everything(project)
    runner = CliRunner()
    ok = runner.invoke(cli, ["spec", "verify"], catch_exceptions=False)
    assert "✓" in ok.stdout and ok.stderr == ""
    path = units_dir(project) / unit_filename("1.1")
    path.write_text(path.read_text() + "\nby hand\n")
    failed = runner.invoke(cli, ["spec", "verify"], catch_exceptions=False)
    assert "✗" in failed.stderr and "1.1" in failed.stderr
    assert failed.stdout == ""


def test_nothing_digested_yet_is_not_reported_as_drift(project: Path, monkeypatch):
    """ "Start" and "re-digest a few units" are different instructions, and the
    verdict is the only thing that says which one this is."""
    monkeypatch.chdir(project)
    _run("index")
    result = _run("verify")
    assert result.exit_code == 1
    assert "nothing is digested" in result.output
    assert "no longer says" not in result.output
