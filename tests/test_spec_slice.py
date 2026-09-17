"""Slices (S-3)."""

import statistics
from pathlib import Path

import pytest

from rite_ai.spec.graph import HUB, INDEX, build_graph, classify
from rite_ai.spec.slice import NotATarget, closure, compute_slice
from rite_ai.spec.units import parse_paths, parse_text
from tests.test_spec_units import SPEC_SESSION_OUTPUT

REPO_ROOT = Path(__file__).resolve().parent.parent


def _setup(text: str, **classify_kwargs):
    parsed = parse_text(text, "SPEC.md")
    graph = build_graph(parsed)
    return parsed, graph, classify(graph, **classify_kwargs)


CHAIN = """\
## 1. A
Needs §2.
## 2. B
Needs §3.
## 3. C
Nothing.
"""


def test_a_slice_stops_at_depth_one():
    parsed, graph, kinds = _setup(CHAIN)
    s = compute_slice(graph, kinds, "1", parsed.total_lines)
    assert s.units == ("1", "2")
    assert "3" not in s.units + s.pinned


def test_depth_one_is_an_under_approximation_on_purpose():
    """Transitive closure on rite's own spec is most of the document.

    This is the test that keeps a slice at depth 1. Following every reference
    rebuilds the monolith: measured at 70.5% of rite's spec at the median when
    the design was written. A depth-1 slice with hubs pinned was 2.3% (9.3% at
    p90), plus the pinned preamble. If this test is failing because someone made
    slices transitive, the fix is to undo that, not to change the thresholds —
    and if depth-1 is not enough for real work, the insufficiency rate
    (`rite_ai.spec.telemetry`) is where that shows, and what should drive depth-2.
    """
    parsed = parse_paths(REPO_ROOT, ["SPEC.md"])
    graph = build_graph(parsed)
    kinds = classify(graph)
    total = parsed.total_lines
    targets = [u for u in graph.units if kinds[u] != INDEX and not u.startswith("D-")]

    def share(ids):
        covered = {
            (graph.units[u].source, n)
            for u in ids
            for n in range(graph.units[u].start, graph.units[u].end + 1)
        }
        return len(covered) / total

    transitive = [
        share(closure(graph, kinds, t, None, respect_kinds=False)) for t in targets
    ]
    sliced = [compute_slice(graph, kinds, t, total) for t in targets]

    assert statistics.median(transitive) > 0.50
    assert statistics.median(s.unpinned_ratio for s in sliced) < 0.05
    assert statistics.median(s.ratio for s in sliced) < 0.25


def test_hubs_are_loaded_but_not_traversed_into():
    text = """\
## 1. Roles
Short. See §5.
## 2. A
Uses §1.
## 3. B
Uses §1.
## 4. C
Uses §1.
## 5. Deep
Only reachable through the hub.
"""
    parsed, graph, kinds = _setup(text)
    assert kinds["1"] == HUB
    s = compute_slice(graph, kinds, "2", parsed.total_lines)
    assert "1" in s.units  # cited directly
    assert "5" not in s.units + s.pinned  # the hub's own reference is not followed


def test_a_hub_is_in_every_slice_even_when_uncited():
    text = "## 1. Hub\nx\n## 2. A\n§1\n## 3. B\n§1\n## 4. C\n§1\n## 5. Unrelated\ny\n"
    parsed, graph, kinds = _setup(text)
    s = compute_slice(graph, kinds, "5", parsed.total_lines)
    assert s.pinned == ("1",)
    assert s.lines > s.unpinned_lines


def test_an_index_is_not_a_retrieval_target():
    rows = "\n".join(f"see §{n}" for n in range(1, 17))
    sections = "\n".join(f"## {n}. S{n}\ntext" for n in range(1, 17))
    parsed, graph, kinds = _setup(f"## 99. Index\n{rows}\n{sections}\n")
    assert kinds["99"] == INDEX
    with pytest.raises(NotATarget, match="index"):
        compute_slice(graph, kinds, "99", parsed.total_lines)


def test_an_unknown_unit_is_not_a_target():
    parsed, graph, kinds = _setup(CHAIN)
    with pytest.raises(NotATarget, match="no unit"):
        compute_slice(graph, kinds, "9", parsed.total_lines)


def test_lines_are_counted_once():
    """A decision row lies inside its register section; slicing both must not
    count the row twice."""
    parsed, graph, kinds = _setup(SPEC_SESSION_OUTPUT)
    s = compute_slice(graph, kinds, "D-2", parsed.total_lines)
    decisions = graph.units["decisions"]
    assert "decisions" in s.units + s.pinned
    assert s.lines <= parsed.total_lines
    assert s.unpinned_lines == decisions.lines  # D-2's row is already inside it


def test_a_spec_session_decision_slices_to_its_register():
    parsed, graph, kinds = _setup(SPEC_SESSION_OUTPUT)
    s = compute_slice(graph, kinds, "architecture", parsed.total_lines)
    assert set(s.units) == {"architecture", "D-2", "D-4"}
    assert s.pinned == ("decisions",)
    assert 0 < s.ratio <= 1
