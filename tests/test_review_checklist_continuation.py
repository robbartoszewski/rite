"""Regression test for a real bug found via `tests/test_review_checklist_template.py`:
`load_checklist` matched only a bullet's own line, silently dropping every
indented continuation line — which is exactly how the project's own default
template writes multi-line items. Fixed 2026-09-09."""

from pathlib import Path

from rite_ai.review.checklist import load_checklist


def test_continuation_line_is_appended_to_item_text(tmp_path: Path):
    path = tmp_path / "checklist.md"
    path.write_text(
        "## Security\n\n"
        "- [ ] No secrets, tokens, or credentials in committed content (source, docs,\n"
        "      comments, test fixtures, commit messages).\n"
    )
    items = load_checklist(path)
    assert len(items) == 1
    assert items[0].text == (
        "No secrets, tokens, or credentials in committed content (source, docs, "
        "comments, test fixtures, commit messages)."
    )


def test_continuation_stops_at_blank_line(tmp_path: Path):
    path = tmp_path / "checklist.md"
    path.write_text(
        "## Security\n\n"
        "- [ ] first item\n"
        "      continues here\n"
        "\n"
        "Some unrelated paragraph that must not be glued onto the item.\n"
    )
    items = load_checklist(path)
    assert len(items) == 1
    assert items[0].text == "first item continues here"


def test_continuation_stops_at_next_bullet(tmp_path: Path):
    path = tmp_path / "checklist.md"
    path.write_text(
        "## Security\n\n- [ ] first item\n      wraps once\n- [ ] second item\n"
    )
    items = load_checklist(path)
    assert len(items) == 2
    assert items[0].text == "first item wraps once"
    assert items[1].text == "second item"


def test_continuation_stops_at_next_heading(tmp_path: Path):
    path = tmp_path / "checklist.md"
    path.write_text(
        "## Security\n\n"
        "- [ ] first item\n"
        "      wraps once\n"
        "## Style\n\n"
        "- [ ] second item\n"
    )
    items = load_checklist(path)
    assert len(items) == 2
    assert items[0].category == "Security"
    assert items[0].text == "first item wraps once"
    assert items[1].category == "Style"


def test_item_wrapping_across_three_lines(tmp_path: Path):
    path = tmp_path / "checklist.md"
    path.write_text(
        "## Verification\n\n"
        "- [ ] line one of the item\n"
        "      line two continues it\n"
        "      line three finishes it\n"
    )
    items = load_checklist(path)
    assert len(items) == 1
    assert (
        items[0].text
        == "line one of the item line two continues it line three finishes it"
    )


def test_single_line_items_unaffected(tmp_path: Path):
    """No regression on the existing, already-committed single-line style."""
    path = tmp_path / "checklist.md"
    path.write_text("## Security\n\n- [ ] No SQL injection vectors\n- [ ] No XSS\n")
    items = load_checklist(path)
    assert [i.text for i in items] == ["No SQL injection vectors", "No XSS"]
