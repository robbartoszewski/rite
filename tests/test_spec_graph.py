"""The reference graph and hub/index classification (S-2)."""

from pathlib import Path

from rite_ai.spec.graph import HUB, INDEX, ORDINARY, build_graph, classify
from rite_ai.spec.units import parse_paths, parse_text
from tests.test_spec_units import SPEC_SESSION_OUTPUT

REPO_ROOT = Path(__file__).resolve().parent.parent


def _graph(text: str):
    return build_graph(parse_text(text, "SPEC.md"))


def test_section_and_decision_citations_become_edges():
    g = _graph("## 1. A\nSee §2 and D-4.\n## 2. B\n## Decisions\n| D-4 | x | y | z |\n")
    assert g.edges["1"] == {"2", "D-4"}


def test_a_citation_to_something_that_does_not_exist_is_dangling_not_an_edge():
    g = _graph("## 1. A\nSee §9.9 and D-99.\n")
    assert g.edges["1"] == frozenset()
    assert g.dangling["1"] == {"9.9", "D-99"}


def test_every_unit_depends_on_the_sections_it_sits_inside():
    g = _graph("## 2. Roles\n### 2.4. Handover\n#### Promotion\ntext\n")
    assert g.edges["2.4/promotion"] == {"2.4", "2"}


def test_a_register_section_does_not_cite_its_own_rows():
    """Its text names every row; that is containment, not citation."""
    g = _graph(
        "## 13. Decisions\n| D-1 | a | b | see §2 |\n| D-2 | c | d | e |\n## 2. Roles\n"
    )
    assert g.edges["13"] == {"2"}
    assert g.edges["D-1"] == {"13", "2"}


def test_a_decision_citing_the_one_it_replaces_is_an_edge():
    g = _graph("## Decisions\n| D-1 | a | b | c |\n| D-2 | a | replaces D-1 | c |\n")
    assert "D-1" in g.edges["D-2"]


def test_a_hyphenated_word_is_not_a_decision_citation():
    g = _graph(
        "## Decisions\n| D-1 | a | b | c |\n"
        "## Notes\nAN-D-1 and ID-1 are not citations.\n"
    )
    assert "D-1" not in g.edges["notes"]


# --- classification --------------------------------------------------------------


def test_the_spec_session_shape_pins_its_register_not_its_title():
    g = _graph(SPEC_SESSION_OUTPUT)
    kinds = classify(g)
    assert kinds["decisions"] == HUB  # every row sits in it; the register is small
    # The title contains nothing but the document: no edges point at it.
    assert g.in_degree()["news-aggregator"] == 0
    assert kinds["news-aggregator"] == ORDINARY
    assert kinds["D-2"] == ORDINARY
    assert INDEX not in kinds.values()


def test_a_sparse_spec_pins_nothing_it_has_no_evidence_for():
    text = "## A\nx\n## B\ny\n## C\nsee D-1\n## Decisions\n| D-1 | a | b | c |\n"
    kinds = classify(_graph(text), pin_count=8, hub_min_in=3)
    assert HUB not in kinds.values()


def test_an_index_is_never_also_a_hub():
    rows = "\n".join(f"| D-{n} | x | see §{n} | y |" for n in range(1, 21))
    sections = "\n".join(f"## {n}. S{n}\ntext" for n in range(1, 21))
    kinds = classify(_graph(f"## 99. Register\n{rows}\n{sections}\n"))
    assert kinds["99"] == INDEX


def test_rites_own_spec_roles_is_a_hub_and_the_register_an_index():
    """The design's measured examples, still true of the spec as it is now."""
    g = build_graph(parse_paths(REPO_ROOT, ["SPEC.md"]))
    kinds = classify(g)
    assert kinds["2"] == HUB
    assert kinds["13"] == INDEX
    # Sections under the title do not count as citing it, nor §8 as citing
    # the sections inside it.
    title = next(u for u in g.units.values() if u.level == 1)
    assert kinds[title.id] != HUB
    assert kinds["8"] != INDEX
    assert sum(1 for k in kinds.values() if k == HUB) <= 8
    assert all(kinds[d] != INDEX for d in g.units if d.startswith("D-"))


def test_sitting_inside_sections_is_not_citing_them():
    text = "## 1. Top\n### 1.1. A\n#### 1.1.1. B\nno citations here\n"
    g = _graph(text)
    assert g.edges["1.1.1"] == {"1.1", "1"}
    assert g.citations["1.1.1"] == frozenset()
