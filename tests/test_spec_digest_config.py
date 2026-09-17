"""Spec digest settings in config.yaml (S-8)."""

from pathlib import Path

import pytest

from rite_ai.cli.init.scaffold import config_to_yaml
from rite_ai.config.models import ProjectConfig, SpecConfig
from rite_ai.config.parse import ParseError, parse_config
from rite_ai.spec.graph import build_graph, classify
from rite_ai.spec.report import decomposition_report
from rite_ai.spec.slice import compute_slice
from rite_ai.spec.units import ITEM, parse_text


def _config(tmp_path: Path, spec_yaml: str):
    path = tmp_path / "config.yaml"
    path.write_text("spec:\n" + spec_yaml)
    return parse_config(path)


def test_defaults_are_the_measured_ones(tmp_path: Path):
    spec = _config(tmp_path, "  paths: [SPEC.md]\n").spec
    assert (spec.extra_units, spec.pin_count, spec.slice_depth, spec.refuse_above) == (
        [],
        8,
        1,
        0.25,
    )


def test_settings_round_trip_through_the_writer(tmp_path: Path):
    config = ProjectConfig(
        spec=SpecConfig(
            paths=["SPEC.md"],
            extra_units=[r"^- (REQ-\d+)\b"],
            pin_count=5,
            slice_depth=2,
            refuse_above=0.4,
        )
    )
    path = tmp_path / "config.yaml"
    path.write_text(config_to_yaml(config))
    assert parse_config(path).spec == config.spec


@pytest.mark.parametrize(
    ("spec_yaml", "fragment"),
    [
        ("  slice_depth: 3\n", "slice_depth must be 1 or 2"),
        ("  slice_depth: 0\n", "slice_depth must be 1 or 2"),
        ("  slice_depth: true\n", "slice_depth must be 1 or 2"),
        ("  pin_count: -1\n", "pin_count"),
        ("  pin_count: '8'\n", "pin_count"),
        ("  refuse_above: 0\n", "refuse_above"),
        ("  refuse_above: 1.5\n", "refuse_above"),
        ("  extra_units: ['(unclosed']\n", "not a regular expression"),
        ("  extra_units: ['REQ-\\d+']\n", "no capture group"),
        ("  extra_units: REQ\n", "list of regular expressions"),
    ],
)
def test_an_unusable_setting_is_refused_not_defaulted(tmp_path, spec_yaml, fragment):
    result = _config(tmp_path, spec_yaml)
    assert isinstance(result, ParseError)
    assert fragment in result.message


def test_a_misspelled_setting_is_refused(tmp_path: Path):
    result = _config(tmp_path, "  slice_dept: 2\n")
    assert isinstance(result, ParseError)
    assert "slice_dept" in result.message


REQUIREMENTS = """\
## 1. Search
- REQ-1 Search by title.
- REQ-2 Results within a second. Depends on REQ-1.
## 2. Offline
- REQ-14 Cached stories open offline.
"""


def test_extra_unit_patterns_make_items_and_their_citations_edges():
    parsed = parse_text(REQUIREMENTS, "SPEC.md", [r"^- (REQ-\d+)\b"])
    items = {u.id: u for u in parsed.units if u.kind == ITEM}
    assert set(items) == {"REQ-1", "REQ-2", "REQ-14"}
    assert items["REQ-14"].parent == "2"
    graph = build_graph(parsed)
    assert "REQ-1" in graph.edges["REQ-2"]
    assert "REQ-1" not in graph.edges["REQ-14"]  # REQ-14 is not REQ-1


def test_without_patterns_there_are_no_items():
    parsed = parse_text(REQUIREMENTS, "SPEC.md")
    assert not [u for u in parsed.units if u.kind == ITEM]


def test_depth_two_follows_one_more_hop_and_three_is_refused():
    text = "## 1. A\n§2\n## 2. B\n§3\n## 3. C\nx\n"
    parsed = parse_text(text, "SPEC.md")
    graph = build_graph(parsed)
    kinds = classify(graph)
    assert "3" not in compute_slice(graph, kinds, "1", parsed.total_lines).units
    assert "3" in compute_slice(graph, kinds, "1", parsed.total_lines, 2).units
    with pytest.raises(ValueError, match="only"):
        compute_slice(graph, kinds, "1", parsed.total_lines, 3)


def test_the_report_uses_the_configured_depth():
    text = "\n".join(
        f"## {n}. S{n}\nsee §{n + 1}\n" + "body\n" * 20 for n in range(1, 12)
    )
    parsed = parse_text(text, "SPEC.md")
    one = decomposition_report(parsed, slice_depth=1)
    two = decomposition_report(parsed, slice_depth=2)
    assert two.p90 > one.p90


# --- found in review ---------------------------------------------------------------


def test_a_float_depth_is_refused_not_stored(tmp_path: Path):
    result = _config(tmp_path, "  slice_depth: 2.0\n")
    assert isinstance(result, ParseError)
    assert "slice_depth must be 1 or 2" in result.message


def test_a_line_that_cites_an_item_does_not_become_another_item():
    parsed = parse_text(REQUIREMENTS, "SPEC.md", [r"(REQ-\d+)"])
    items = [u.id for u in parsed.units if u.kind == ITEM]
    assert items == []  # no line STARTS with an id under an unanchored pattern
    anchored = parse_text(REQUIREMENTS, "SPEC.md", [r"- (REQ-\d+)\b"])
    assert sorted(u.id for u in anchored.units if u.kind == ITEM) == [
        "REQ-1",
        "REQ-14",
        "REQ-2",
    ]


def test_items_are_not_taken_from_fenced_code_or_headings():
    text = "## 1. A\n```\n- REQ-9 example only\n```\n## REQ-7 heading\n- REQ-3 real\n"
    parsed = parse_text(text, "SPEC.md", [r"(?:## |- )(REQ-\d+)\b"])
    assert [u.id for u in parsed.units if u.kind == ITEM] == ["REQ-3"]


def test_a_decision_row_is_not_also_an_item():
    parsed = parse_text(
        "## Decisions\n| D-1 | a | b | c |\n", "SPEC.md", [r"\| (D-\d+) \|"]
    )
    assert [u.kind for u in parsed.units if u.id == "D-1"] == ["decision"]


def test_an_item_colliding_with_another_unit_is_reported_not_renamed():
    parsed = parse_text("## 1. A\n1) first\n", "SPEC.md", [r"(\d+)\)"])
    assert [u.id for u in parsed.units] == ["1"]
    assert any("not addressable" in p for p in parsed.problems)


def test_item_ids_with_regex_characters_are_cited_literally():
    text = "## 1. A\n- R.1 first\n- R.2 needs R.1, not RX1\n"
    parsed = parse_text(text, "SPEC.md", [r"- (R\.\d+)\b"])
    graph = build_graph(parsed)
    assert graph.edges["R.2"] >= {"R.1"}


def test_patterns_reach_every_registered_file(tmp_path: Path):
    from rite_ai.spec.units import parse_paths

    (tmp_path / "A.md").write_text("## 1. A\n- REQ-1 x\n")
    (tmp_path / "B.md").write_text("## 9. B\n- REQ-2 y\n")
    parsed = parse_paths(tmp_path, ["A.md", "B.md"], [r"- (REQ-\d+)\b"])
    assert {u.id for u in parsed.units if u.kind == ITEM} == {"REQ-1", "REQ-2"}
