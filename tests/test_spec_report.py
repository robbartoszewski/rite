"""The decomposition verdict (S-4). A gate only ever tested on input it passes
is not tested, so the refusals are tested as carefully as the pass."""

from pathlib import Path

from rite_ai.spec.report import decomposition_report, render
from rite_ai.spec.units import parse_paths, parse_text
from tests.test_spec_units import SPEC_SESSION_OUTPUT

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_rites_own_spec_decomposes():
    report = decomposition_report(parse_paths(REPO_ROOT, ["SPEC.md"]))
    assert report.decomposes
    assert report.p90 is not None and report.p90 < 0.15
    assert len(report.hubs) == 8
    assert "13" in report.indexes
    text = render(report)
    assert "✓ this spec decomposes" in text
    assert "projected slice: p50" in text


def _densely_linked(sections: int = 30, cites: int = 12) -> str:
    """Every section cites the next `cites` sections around it: under the index
    threshold, so nothing is excluded, and too many links for any slice to be
    small."""
    parts = []
    for n in range(1, sections + 1):
        refs = ", ".join(f"§{(n + k - 1) % sections + 1}" for k in range(1, cites + 1))
        body = "\n".join(f"Detail line {i} for section {n}." for i in range(8))
        parts.append(f"## {n}. Section {n}\nDepends on {refs}.\n{body}\n")
    return "\n".join(parts)


def test_a_densely_linked_spec_is_refused():
    report = decomposition_report(parse_text(_densely_linked(), "SPEC.md"))
    assert not report.decomposes
    assert report.p90 is not None and report.p90 > report.refuse_above
    assert "✗ this spec does not decompose" in render(report)


def test_the_same_shape_with_few_links_decomposes():
    """Guards the test above against refusing for a reason other than density."""
    report = decomposition_report(parse_text(_densely_linked(cites=1), "SPEC.md"))
    assert report.decomposes


def test_a_small_spec_session_spec_is_refused_as_not_worth_splitting():
    report = decomposition_report(parse_text(SPEC_SESSION_OUTPUT, "SPEC.md"))
    assert not report.decomposes
    text = render(report)
    assert "reading the whole file costs less" in text
    assert "Point Workers at the whole spec" in text


def test_a_spec_with_no_headings_is_refused_without_computing_slices():
    report = decomposition_report(parse_text("Prose.\nMore prose.\n", "notes.md"))
    assert not report.decomposes
    assert report.p90 is None
    assert "no headings" in report.reason
    assert "note: notes.md: no headings" in render(report)


def test_the_limit_is_a_parameter_not_a_constant_in_the_rule():
    parsed = parse_paths(REPO_ROOT, ["SPEC.md"])
    assert not decomposition_report(parsed, refuse_above=0.01).decomposes
