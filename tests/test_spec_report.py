"""The decomposition verdict (S-4). A gate only ever tested on input it passes
is not tested, so the refusals are tested as carefully as the pass."""

from pathlib import Path

from rite_ai.spec.report import SPARSE_ABOVE, decomposition_report, render
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


# --- a small slice means two opposite things -------------------------------------


def _many_sections(n: int, *, citing: bool) -> str:
    body = "See §1.\n" if citing else "Some prose that references nothing.\n"
    return "# T\n" + "".join(
        f"## {i}. S{i}\n{body}" + "filler line\n" * 8 for i in range(1, n + 1)
    )


def test_a_spec_where_nothing_cites_anything_gets_a_qualified_verdict():
    """Measured: seven real design documents written without a citation gate
    left 73%-100% of their units citing nothing, and their p90 slices came out
    SMALLER than rite's own (0.9%-8.7% against 9.1%). A plain ✓ would sell an
    empty reference graph as a good decomposition."""
    report = decomposition_report(parse_text(_many_sections(40, citing=False), "S.md"))
    assert report.decomposes  # the slices really are small
    assert report.unlinked_share == 1.0
    out = render(report)
    assert "✓ this spec decomposes" in out
    assert "⚠" in out and "cite nothing" in out
    assert "insufficiency rate" in out


def test_a_spec_whose_units_cite_each_other_is_not_qualified():
    report = decomposition_report(parse_text(_many_sections(40, citing=True), "S.md"))
    assert report.decomposes
    assert report.unlinked_share < SPARSE_ABOVE
    assert "⚠" not in render(report)


def test_rites_own_spec_is_on_the_dense_side_of_the_line():
    """The measurement the threshold was drawn from, kept live: if rite's spec
    ever drifts above it, the number is wrong or the spec has decayed."""
    parsed = parse_paths(REPO_ROOT, ["SPEC.md"])
    report = decomposition_report(parsed)
    assert 0 < report.unlinked_share < SPARSE_ABOVE
    assert "⚠" not in render(report)


def test_the_share_counts_citations_not_the_structure_units_get_for_free():
    """Every unit has ancestor edges whether or not it references anything;
    counting those would make an unlinked document look connected."""
    report = decomposition_report(parse_text(_many_sections(40, citing=False), "S.md"))
    # Every section, the title included: ancestor edges connect all of them and
    # excuse none of them.
    assert report.unlinked == report.sections
