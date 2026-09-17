"""`rite spec index | status | slice | stamp` (S-6).

The public surface of the digest. What these pin down is the refusals: a spec
that should not be digested, a target that cannot be sliced, a file that was
never stamped. A command that quietly does nothing here leaves a Worker reading
a derived unit that no longer matches the spec.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.spec.digest_files import render_unit_file, unit_filename, units_dir
from rite_ai.spec.index_file import index_path
from rite_ai.spec.telemetry import read_events

SPEC = """# Thing

## 1. Roles
Who does what. See D-1.

### 1.1. Worker
A Worker does the work, unless the Owner has taken the ticket back.

## 2. Handover
A session ending mid-ticket writes a snapshot. See §1.1.

## 3. Sandboxing
Workers run sandboxed. See §1.1 and D-1.

## Decisions
| D | Decision | Choice | Why |
| D-1 | Who writes | The Worker | It holds the context |
"""

# Long enough that one unit is a small share of it: a spec this size is the
# case the digest exists for, and a 20-line one correctly refuses.
FILLER = "\n".join(
    f"## {n}. Filler {n}\nA paragraph about {n}. See §1.1.\n" for n in range(4, 60)
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(SPEC + FILLER)
    return tmp_path


def _run(project: Path, *args: str):
    return CliRunner().invoke(cli, ["spec", *args], catch_exceptions=False)


def _write_unit(project: Path, unit_id: str, covers, body="derived text") -> Path:
    path = units_dir(project) / unit_filename(unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit_file(unit_id, tuple(covers), "SPEC.md", "", "", body))
    return path


# --- index -------------------------------------------------------------------------


def test_index_writes_the_inventory_and_reports_the_verdict(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    result = _run(project, "index")
    assert result.exit_code == 0, result.output
    assert index_path(project).exists()
    assert "decomposes" in result.output
    assert "unit(s)" in result.output


def test_index_refuses_a_spec_a_slice_cannot_help(tmp_path: Path, monkeypatch):
    """A short spec is loaded whole. Refusing is the supported outcome, and it
    has to cost nothing — this runs before any unit is written."""
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(SPEC)
    monkeypatch.chdir(tmp_path)
    result = _run(tmp_path, "index")
    assert result.exit_code == 1
    assert "✗" in result.output and "whole spec" in result.output


def test_every_command_says_what_to_do_when_no_spec_is_registered(
    tmp_path: Path, monkeypatch
):
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths: []\n")
    monkeypatch.chdir(tmp_path)
    for args in (["index"], ["status"], ["slice", "1"], ["stamp", "1"]):
        result = _run(tmp_path, *args)
        assert result.exit_code == 1, args
        assert "rite spec add" in result.output, args


# --- status ------------------------------------------------------------------------


def test_status_names_uncovered_units_with_the_path_to_write(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    result = _run(project, "status")
    assert result.exit_code == 0, result.output
    assert "no derived file yet" in result.output
    assert (
        f"1.1 → {units_dir(Path('.')) / unit_filename('1.1')}"
        in result.output.replace("./", "")
    )


def test_status_says_the_index_is_missing_before_it_is_written(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    assert "not written yet" in _run(project, "status").output
    _run(project, "index")
    assert "index: current" in _run(project, "status").output


def test_status_notices_the_index_no_longer_matches_the_spec(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _run(project, "index")
    (project / "SPEC.md").write_text(
        SPEC.replace("Who does what.", "Rewritten.") + FILLER
    )
    assert "out of date" in _run(project, "status").output


def test_status_reports_a_hand_edited_unit(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    path = _write_unit(project, "1.1", ["1.1"])
    _run(project, "stamp", "1.1")
    path.write_text(path.read_text() + "\nsomeone typed this\n")
    result = _run(project, "status")
    assert "edited by hand since stamping" in result.output


def test_status_reports_an_unstamped_unit(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1"])
    assert "never stamped" in _run(project, "status").output


def test_status_carries_the_insufficiency_rate_even_with_no_data(
    project: Path, monkeypatch
):
    """`NO_DATA` is reported, never printed as 0% — nobody using the slices and
    the slices always being enough are opposite readings."""
    monkeypatch.chdir(project)
    out = _run(project, "status").output
    assert "no spec slices retrieved" in out
    _run(project, "slice", "1.1")
    assert "1 slice retrievals" in _run(project, "status").output


# --- slice -------------------------------------------------------------------------


def test_slice_prints_the_unit_and_what_it_references(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    result = _run(project, "slice", "2")
    assert result.exit_code == 0, result.output
    assert "A session ending mid-ticket" in result.output  # the unit
    assert "A Worker does the work" in result.output  # §1.1, which it cites


def test_slice_does_not_print_the_whole_spec(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    out = _run(project, "slice", "2").output
    assert "Filler 40" not in out
    assert (
        len(out.splitlines()) < len((project / "SPEC.md").read_text().splitlines()) / 2
    )


def test_slice_prints_each_line_once_even_when_a_parent_and_child_are_both_in(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    out = _run(project, "slice", "1.1").output
    assert out.count("A Worker does the work") == 1


def test_slice_refuses_an_index_and_says_what_to_slice_instead(
    tmp_path: Path, monkeypatch
):
    rows = "\n".join(f"| D-{n} | x | see §{n} | y |" for n in range(1, 21))
    sections = "\n".join(f"## {n}. S{n}\ntext about {n}\n" for n in range(1, 21))
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(f"# T\n## 99. Register\n{rows}\n{sections}\n")
    monkeypatch.chdir(tmp_path)
    result = _run(tmp_path, "slice", "99")
    assert result.exit_code == 1
    assert "index" in result.output


def test_slice_refuses_a_unit_the_spec_does_not_have(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    result = _run(project, "slice", "44.7")
    assert result.exit_code == 1
    assert "44.7" in result.output


def test_slice_records_the_retrieval_so_the_rate_has_a_denominator(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _run(project, "slice", "2", "--worker", "alpha")
    events = read_events(project).events
    assert [(e.kind, e.unit, e.worker) for e in events] == [("retrieval", "2", "alpha")]
    assert 0 < events[0].slice_ratio < 1


def test_a_refused_slice_is_not_counted_as_a_retrieval(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _run(project, "slice", "44.7")
    assert read_events(project).events == []


def test_slice_depth_can_be_overridden_and_only_1_or_2_are_allowed(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    shallow = _run(project, "slice", "2", "--depth", "1")
    deep = _run(project, "slice", "2", "--depth", "2")
    assert shallow.exit_code == 0 and deep.exit_code == 0
    assert len(deep.output) >= len(shallow.output)
    refused = _run(project, "slice", "2", "--depth", "3")
    assert refused.exit_code == 2
    assert "only (1, 2)" in refused.output  # a sentence, not a traceback


# --- stamp -------------------------------------------------------------------------


def test_stamp_records_the_hashes_for_the_units_it_is_given(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    path = _write_unit(project, "1.1", ["1.1"])
    result = _run(project, "stamp", "1.1")
    assert result.exit_code == 0, result.output
    assert "stamped" in result.output
    assert "body_sha: ''" not in path.read_text()
    assert "never stamped" not in _run(project, "status").output


def test_stamp_refuses_a_unit_with_no_file_written_yet(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    result = _run(project, "stamp", "1.1")
    assert result.exit_code == 1
    assert "write it first" in result.output


def test_stamp_refuses_a_file_covering_something_the_spec_lacks(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1", "9.9"])
    result = _run(project, "stamp", "1.1")
    assert result.exit_code == 1
    assert "9.9" in result.output


def test_stamp_all_stamps_every_file_and_still_fails_on_a_bad_one(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1"])
    _write_unit(project, "2", ["2"])
    assert _run(project, "stamp", "--all").exit_code == 0
    assert "never stamped" not in _run(project, "status").output
    (units_dir(project) / "junk.md").write_text("not a unit file\n")
    result = _run(project, "stamp", "--all")
    assert result.exit_code == 1 and "junk.md" in result.output


def test_stamp_needs_either_units_or_all(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    assert _run(project, "stamp").exit_code == 2
    _write_unit(project, "1.1", ["1.1"])
    assert _run(project, "stamp", "1.1", "--all").exit_code == 2


# --- found in review ---------------------------------------------------------------


def test_stamp_all_refuses_to_bless_a_hand_edited_file(project: Path, monkeypatch):
    """`--all` stamping everything erased every drift signal the feature
    exists to produce, in one command, and exited 0."""
    monkeypatch.chdir(project)
    path = _write_unit(project, "1.1", ["1.1"])
    _run(project, "stamp", "1.1")
    path.write_text(path.read_text() + "\nsomeone typed this\n")
    result = _run(project, "stamp", "--all")
    assert result.exit_code == 1
    assert "not stamped" in result.output and "by name" in result.output
    assert "edited by hand since stamping" in _run(project, "status").output


def test_stamp_all_refuses_to_bless_a_stale_file(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1"])
    _run(project, "stamp", "--all")
    (project / "SPEC.md").write_text(
        (SPEC + FILLER).replace("A Worker does the work", "A Worker now does it")
    )
    result = _run(project, "stamp", "--all")
    assert result.exit_code == 1
    assert "stale" in _run(project, "status").output


def test_stamping_a_unit_by_name_is_still_how_a_rewrite_is_recorded(
    project: Path, monkeypatch
):
    """The deliberate act stays available: `--all` is the bulk convenience,
    naming a unit is the statement that its text was just rewritten."""
    monkeypatch.chdir(project)
    path = _write_unit(project, "1.1", ["1.1"])
    _run(project, "stamp", "1.1")
    path.write_text(
        render_unit_file("1.1", ("1.1",), "SPEC.md", "", "", "rewritten by hand")
    )
    assert _run(project, "stamp", "1.1").exit_code == 0
    assert _run(project, "status").output.count("edited by hand") == 0


def test_index_writes_nothing_for_a_spec_it_refuses(tmp_path: Path, monkeypatch):
    """A refusal that leaves an index behind invites the digest it refused."""
    from rite_ai.spec.index_file import index_path

    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(SPEC)
    monkeypatch.chdir(tmp_path)
    assert _run(tmp_path, "index").exit_code == 1
    assert not index_path(tmp_path).exists()


def test_status_repeats_the_refusal_rather_than_listing_work_to_do(
    tmp_path: Path, monkeypatch
):
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(SPEC)
    monkeypatch.chdir(tmp_path)
    assert "should not be digested" in _run(tmp_path, "status").output


def test_status_reports_two_files_covering_one_unit(project: Path, monkeypatch):
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1"])
    (units_dir(project) / "other.md").write_text(
        render_unit_file("other", ("1.1",), "SPEC.md", "", "", "a second account")
    )
    assert "more than one derived file" in _run(project, "status").output


def test_a_spliced_section_is_never_presented_as_a_whole_one(
    tmp_path: Path, monkeypatch
):
    """A register printed without one of its rows, under the register's own
    line range, reads as a complete table — and a Worker concludes that
    decision does not exist. The rows are separate units, so this happens
    whenever a row is in the slice and its register is pinned."""
    rows = "\n".join(f"| D-{n} | Choice {n} | Because {n}. |" for n in range(1, 6))
    sections = "\n".join(
        f"## {n}. S{n}\nSection {n}, which implements D-3 and D-1.\n"
        for n in range(1, 30)
    )
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "config.yaml").write_text("spec:\n  paths:\n    - SPEC.md\n")
    (tmp_path / "SPEC.md").write_text(
        f"# T\n{sections}\n## 99. Decisions\n| D | Choice | Why |\n{rows}\n"
    )
    monkeypatch.chdir(tmp_path)
    out = _run(tmp_path, "slice", "7").output
    register = out[out.index("# 99 (") :]
    assert "| D-1 | Choice 1" not in register  # already printed as its own unit
    # …so the register must not claim to be the whole table.
    assert "printed elsewhere" in register
    assert "# 99 (SPEC.md:" in register and "-" in register.splitlines()[0]


# --- show: the derived text, which is what the review rounds produced -------------


def test_show_prints_the_derived_text_and_where_it_came_from(
    project: Path, monkeypatch
):
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1"], "A Worker does the work. See §2.")
    _run(project, "stamp", "1.1")
    result = _run(project, "show", "1.1")
    assert result.exit_code == 0, result.output
    assert "A Worker does the work. See §2." in result.output
    assert "derived from SPEC.md:" in result.output  # the source pointer
    assert "covers 1.1" in result.output


def test_show_says_nothing_is_derived_yet_rather_than_printing_the_source(
    project: Path, monkeypatch
):
    """Silently falling back to source text would make an undigested spec look
    digested, and the two-round review is the whole difference."""
    monkeypatch.chdir(project)
    result = _run(project, "show", "1.1")
    assert result.exit_code == 1
    assert "nothing derived for it yet" in result.output
    assert "rite spec slice 1.1" in result.output


@pytest.mark.parametrize("how", ["stale", "tampered", "unstamped"])
def test_show_hands_over_the_text_and_says_it_cannot_be_trusted(
    project: Path, monkeypatch, how: str
):
    """Both halves matter: a Worker handed nothing cannot judge anything, and
    a Worker handed drifted text with no warning cannot either."""
    monkeypatch.chdir(project)
    path = _write_unit(project, "1.1", ["1.1"], "derived text")
    if how != "unstamped":
        _run(project, "stamp", "1.1")
    if how == "tampered":
        path.write_text(path.read_text() + "\nadded by hand\n")
    elif how == "stale":
        (project / "SPEC.md").write_text(
            (SPEC + FILLER).replace("A Worker does the work", "A Worker now does it")
        )
    result = _run(project, "show", "1.1")
    assert result.exit_code == 1
    assert "derived text" in result.output  # handed over anyway
    assert "⚠" in result.output and "Re-digest" in result.output


def test_show_counts_as_a_retrieval(project: Path, monkeypatch):
    """Reading a derived unit is a retrieval like a slice is: a fallback after
    either one means the same thing, and the rate needs both in its
    denominator."""
    monkeypatch.chdir(project)
    _write_unit(project, "1.1", ["1.1"])
    _run(project, "stamp", "1.1")
    _run(project, "show", "1.1", "--worker", "alpha")
    events = read_events(project).events
    assert [(e.kind, e.unit, e.worker) for e in events] == [
        ("retrieval", "1.1", "alpha")
    ]


def test_index_says_a_parse_problem_once(project: Path, monkeypatch):
    """It was said twice — once by the shared parse step and again as a note
    under the verdict — which reads as two different problems."""
    monkeypatch.chdir(project)
    (project / "SPEC.md").write_text(
        SPEC + FILLER + "```bash\necho hi\n## 90. Lost to the fence\ntext\n"
    )
    result = _run(project, "index")
    assert result.output.count("never closed") == 1
