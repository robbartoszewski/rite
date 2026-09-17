"""Derived unit files and the status that checks them against the spec (S-6).

The point of every test here is that a file which has drifted from the spec is
NAMED. A digest that silently passes is worse than no digest: a Worker reads a
derived unit believing it is the spec.
"""

from pathlib import Path

import pytest

from rite_ai.spec.digest_files import (
    BANNER,
    UnitFile,
    body_hash,
    covers_hash,
    digest_status,
    read_unit_file,
    read_unit_files,
    render_unit_file,
    stamp,
    unit_filename,
    units_dir,
)
from rite_ai.spec.graph import build_graph, classify
from rite_ai.spec.units import parse_text

SPEC = """# Thing

## 1. Roles
The roles.

### 1.1. Worker
A Worker does the work.

## 2. Handover
See §1.1.

## Decisions
| D | Decision | Choice | Why |
| D-1 | a | b | c |
"""


def _spec(text: str = SPEC):
    parsed = parse_text(text, "SPEC.md")
    units = {u.id: u for u in parsed.units}
    return units, classify(build_graph(parsed))


def _write(root: Path, unit_id: str, covers, body: str, *, stamped=True) -> Path:
    units, _ = _spec()
    path = units_dir(root) / unit_filename(unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    covers = tuple(covers)
    path.write_text(
        render_unit_file(
            unit_id,
            covers,
            "SPEC.md",
            covers_hash(units, covers) or "" if stamped else "",
            body_hash(body) if stamped else "",
            body,
        )
    )
    return path


# --- file names and round-tripping -------------------------------------------------


@pytest.mark.parametrize(
    "unit_id",
    ["1.1", "2.4/promotion", "OTHER.md#3", "scope~2", "scope/non-goals", "D-1"],
)
def test_a_unit_file_round_trips_whatever_shape_the_id_has(
    tmp_path: Path, unit_id: str
):
    path = tmp_path / unit_filename(unit_id)
    path.write_text(render_unit_file(unit_id, (unit_id,), "SPEC.md", "a", "b", "body"))
    read = read_unit_file(path)
    assert isinstance(read, UnitFile), read
    assert (read.id, read.covers) == (unit_id, (unit_id,))
    assert read.body.strip() == "body"


def test_ids_that_differ_get_different_file_names():
    """`a/b` and `a__b` are different units; one must not overwrite the other."""
    ids = ["1.1", "2.4/promotion", "OTHER.md#3", "scope~2", "a/b", "a__b", "a#b", "a-b"]
    assert len({unit_filename(i) for i in ids}) == len(ids)
    assert all("/" not in unit_filename(i) for i in ids)


def test_a_unit_file_says_it_is_generated(tmp_path: Path):
    assert render_unit_file("1", ("1",), "SPEC.md", "a", "b", "x").startswith(BANNER)


# --- refusing what is not a unit file ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("just prose, no front matter\n", "no front matter"),
        ("---\nid: [1, 2\n---\nbody\n", "not YAML"),
        ("---\n- a\n- b\n---\nbody\n", "not a mapping"),
        ("---\ncovers: ['1']\n---\nbody\n", "no id"),
        ("---\nid: '1'\n---\nbody\n", "'covers'"),
        ("---\nid: '1'\ncovers: []\n---\nbody\n", "'covers'"),
        ("---\nid: '1'\ncovers: 'not a list'\n---\nbody\n", "'covers'"),
    ],
)
def test_a_file_that_is_not_a_unit_file_is_named_not_skipped(
    tmp_path: Path, text: str, why: str
):
    path = tmp_path / "broken.md"
    path.write_text(text)
    read = read_unit_file(path)
    assert isinstance(read, str)
    assert "broken.md" in read and why in read


def test_unreadable_files_are_reported_alongside_the_readable_ones(tmp_path: Path):
    _write(tmp_path, "1.1", ["1.1"], "body")
    (units_dir(tmp_path) / "junk.md").write_text("no front matter here\n")
    files, problems = read_unit_files(tmp_path)
    assert [f.id for f in files] == ["1.1"]
    assert len(problems) == 1 and "junk.md" in problems[0]


def test_no_units_directory_is_not_an_error(tmp_path: Path):
    assert read_unit_files(tmp_path) == ([], [])


# --- stamping ----------------------------------------------------------------------


def test_stamping_records_hashes_of_the_source_and_of_the_body(tmp_path: Path):
    units, _ = _spec()
    path = _write(tmp_path, "1.1", ["1.1"], "A Worker does the work.", stamped=False)
    unstamped = read_unit_file(path)
    assert isinstance(unstamped, UnitFile) and not unstamped.body_sha
    stamped = stamp(unstamped, units)
    assert isinstance(stamped, UnitFile), stamped
    again = read_unit_file(path)
    assert isinstance(again, UnitFile)
    assert again.body_sha == body_hash(again.body) == stamped.body_sha
    assert again.source_sha == covers_hash(units, ("1.1",))
    assert again.body.strip() == "A Worker does the work."


def test_stamping_is_refused_when_it_covers_something_the_spec_lacks(tmp_path: Path):
    units, _ = _spec()
    path = _write(tmp_path, "9.9", ["9.9"], "invented", stamped=False)
    read = read_unit_file(path)
    assert isinstance(read, UnitFile)
    before = path.read_bytes()
    result = stamp(read, units)
    assert isinstance(result, str)
    assert "9.9" in result and "does not have" in result
    assert path.read_bytes() == before  # refused before writing anything


def test_the_source_hash_changes_when_the_source_unit_changes():
    before, _ = _spec()
    after, _ = _spec(SPEC.replace("A Worker does the work.", "A Worker does more."))
    assert covers_hash(before, ("1.1",)) != covers_hash(after, ("1.1",))
    assert covers_hash(before, ("2",)) == covers_hash(after, ("2",))


def test_the_source_hash_does_not_depend_on_the_order_of_covers():
    units, _ = _spec()
    assert covers_hash(units, ("1", "1.1")) == covers_hash(units, ("1.1", "1"))


# --- status ------------------------------------------------------------------------


def test_a_freshly_stamped_digest_of_everything_is_clean(tmp_path: Path):
    units, kinds = _spec()
    for uid in units:
        _write(tmp_path, uid, [uid], f"derived text for {uid}")
    status = digest_status(tmp_path, units, kinds)
    assert status.clean, vars(status)
    assert status.files == len(units)


def test_an_uncovered_unit_is_new(tmp_path: Path):
    units, kinds = _spec()
    _write(tmp_path, "1.1", ["1.1"], "body")
    status = digest_status(tmp_path, units, kinds)
    assert "1.1" not in status.new
    assert "2" in status.new and "D-1" in status.new


def test_an_index_section_is_not_owed_a_file(tmp_path: Path):
    rows = "\n".join(f"| D-{n} | x | see §{n} | y |" for n in range(1, 21))
    sections = "\n".join(f"## {n}. S{n}\ntext" for n in range(1, 21))
    units, kinds = _spec(f"# T\n## 99. Register\n{rows}\n{sections}\n")
    status = digest_status(tmp_path, units, kinds)
    assert kinds["99"] == "index"
    assert "99" not in status.new


def test_a_changed_source_makes_its_file_stale(tmp_path: Path):
    _write(tmp_path, "1.1", ["1.1"], "A Worker does the work.")
    units, kinds = _spec(SPEC.replace("A Worker does the work.", "A Worker does more."))
    status = digest_status(tmp_path, units, kinds)
    assert status.stale == [unit_filename("1.1")]
    assert not status.tampered  # the body was not touched


def test_a_hand_edited_body_is_tampered_not_stale(tmp_path: Path):
    path = _write(tmp_path, "1.1", ["1.1"], "A Worker does the work.")
    path.write_text(path.read_text() + "\nsomeone added this by hand\n")
    units, kinds = _spec()
    status = digest_status(tmp_path, units, kinds)
    assert status.tampered == [unit_filename("1.1")]
    assert not status.stale
    assert not status.clean


def test_a_file_covering_a_deleted_unit_is_removed(tmp_path: Path):
    _write(tmp_path, "1.1", ["1.1"], "body")
    units, kinds = _spec(SPEC.replace("### 1.1. Worker\nA Worker does the work.\n", ""))
    status = digest_status(tmp_path, units, kinds)
    assert len(status.removed) == 1 and "1.1" in status.removed[0]
    assert not status.stale and not status.tampered  # reported once, by its real cause


def test_a_written_but_unstamped_file_is_not_silently_trusted(tmp_path: Path):
    _write(tmp_path, "1.1", ["1.1"], "body", stamped=False)
    units, kinds = _spec()
    status = digest_status(tmp_path, units, kinds)
    assert status.unstamped == [unit_filename("1.1")]
    assert not status.clean


def test_two_files_claiming_the_same_id_are_reported(tmp_path: Path):
    _write(tmp_path, "1.1", ["1.1"], "body")
    second = units_dir(tmp_path) / "duplicate.md"
    second.write_text(render_unit_file("1.1", ("1.1",), "SPEC.md", "a", "b", "other"))
    units, kinds = _spec()
    status = digest_status(tmp_path, units, kinds)
    assert any("already used by" in u for u in status.unreadable)
    assert not status.clean


def test_one_file_may_cover_several_units(tmp_path: Path):
    units, kinds = _spec()
    _write(tmp_path, "1", ["1", "1.1"], "roles and the worker together")
    status = digest_status(tmp_path, units, kinds)
    assert "1" not in status.new and "1.1" not in status.new
    assert not status.stale and not status.tampered


def test_a_covering_file_goes_stale_when_any_unit_it_covers_changes(tmp_path: Path):
    _write(tmp_path, "1", ["1", "1.1"], "roles and the worker together")
    units, kinds = _spec(SPEC.replace("A Worker does the work.", "A Worker does more."))
    status = digest_status(tmp_path, units, kinds)
    assert status.stale == [unit_filename("1")]


def test_whitespace_at_the_end_of_a_body_is_not_tampering(tmp_path: Path):
    path = _write(tmp_path, "1.1", ["1.1"], "body")
    path.write_text(path.read_text().rstrip("\n") + "\n\n\n")
    units, kinds = _spec()
    assert digest_status(tmp_path, units, kinds).tampered == []


# --- ids YAML would quietly change ------------------------------------------------


@pytest.mark.parametrize(
    "front",
    ["id: 8.10\ncovers: ['8.10']\n", "id: '8.10'\ncovers: [8.10]\n"],
)
def test_an_unquoted_numeric_id_is_refused_not_coerced(tmp_path: Path, front: str):
    """`8.10` unquoted is the number 8.1 — a DIFFERENT section. Coercing it
    would point the derived file at the wrong unit, silently."""
    path = tmp_path / "8.10.md"
    path.write_text(f"---\n{front}---\nbody\n")
    read = read_unit_file(path)
    assert isinstance(read, str)
    assert "must be quoted" in read and "8.1" in read


def test_a_quoted_numeric_id_reads_as_written(tmp_path: Path):
    path = tmp_path / "8.10.md"
    path.write_text(render_unit_file("8.10", ("8.10",), "SPEC.md", "a", "b", "body"))
    read = read_unit_file(path)
    assert isinstance(read, UnitFile) and read.id == "8.10"
