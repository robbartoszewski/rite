"""The unit inventory on disk (S-5)."""

import json
from pathlib import Path

from rite_ai.spec.index_file import (
    ABSENT,
    FORMAT_VERSION,
    PRESENT,
    UNREADABLE,
    from_parsed,
    index_path,
    read_index,
    render,
    write_index,
)
from rite_ai.spec.units import parse_text
from tests.test_spec_units import SPEC_SESSION_OUTPUT


def _index():
    return from_parsed(parse_text(SPEC_SESSION_OUTPUT, "SPEC.md"))


def test_an_index_round_trips(tmp_path: Path):
    index = _index()
    write_index(tmp_path, index)
    read = read_index(tmp_path)
    assert read.status == PRESENT
    assert read.index == index


def test_no_index_is_absent_not_unreadable(tmp_path: Path):
    read = read_index(tmp_path)
    assert (read.status, read.index) == (ABSENT, None)


def test_the_same_units_write_the_same_bytes(tmp_path: Path):
    """A committed index changes in a diff only when the spec did."""
    assert render(_index()) == render(_index())
    write_index(tmp_path, _index())
    first = index_path(tmp_path).read_bytes()
    write_index(tmp_path, _index())
    assert index_path(tmp_path).read_bytes() == first


def test_writing_leaves_nothing_but_the_index(tmp_path: Path):
    write_index(tmp_path, _index())
    assert [p.name for p in index_path(tmp_path).parent.iterdir()] == ["index.json"]


def _write_raw(tmp_path: Path, content: str) -> None:
    path = index_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _corrupted(tmp_path: Path, mutate) -> tuple[str, str]:
    data = json.loads(render(_index()))
    mutate(data)
    _write_raw(tmp_path, json.dumps(data))
    read = read_index(tmp_path)
    return read.status, read.error


def test_a_corrupt_index_is_unreadable_never_empty(tmp_path: Path):
    _write_raw(tmp_path, '{"format": 1, "units": [')
    read = read_index(tmp_path)
    assert read.status == UNREADABLE
    assert read.index is None
    assert "JSON" in read.error


def test_a_unit_with_a_missing_field_is_unreadable(tmp_path: Path):
    status, error = _corrupted(tmp_path, lambda d: d["units"][0].pop("sha"))
    assert status == UNREADABLE and "unit 0" in error


def test_a_unit_with_an_extra_field_is_unreadable(tmp_path: Path):
    status, _ = _corrupted(tmp_path, lambda d: d["units"][0].update(guess=1))
    assert status == UNREADABLE


def test_two_units_with_one_id_is_unreadable(tmp_path: Path):
    status, error = _corrupted(
        tmp_path, lambda d: d["units"].append(dict(d["units"][0]))
    )
    assert status == UNREADABLE and "share the id" in error


def test_an_impossible_line_range_is_unreadable(tmp_path: Path):
    status, _ = _corrupted(tmp_path, lambda d: d["units"][1].update(start=9, end=3))
    assert status == UNREADABLE


def test_an_index_from_a_newer_rite_says_to_upgrade(tmp_path: Path):
    status, error = _corrupted(tmp_path, lambda d: d.update(format=FORMAT_VERSION + 1))
    assert status == UNREADABLE and "newer rite" in error


def test_the_units_list_must_be_a_list(tmp_path: Path):
    status, _ = _corrupted(tmp_path, lambda d: d.update(units={}))
    assert status == UNREADABLE
