from pathlib import Path

from rite_ai.review.checklist import (
    ChecklistItem,
    format_checklist_prompt,
    load_checklist,
)

SAMPLE_CHECKLIST = """\
# Security

- [ ] No SQL injection vectors
- [ ] No XSS vulnerabilities
- [x] Dependencies audited

# Correctness

- [ ] Edge cases tested
- [ ] Error paths covered
"""


class TestLoadChecklist:
    def test_parses_items_with_categories(self, tmp_path: Path):
        path = tmp_path / "checklist.md"
        path.write_text(SAMPLE_CHECKLIST)
        items = load_checklist(path)
        assert len(items) == 5
        security = [i for i in items if i.category == "Security"]
        assert len(security) == 3
        correctness = [i for i in items if i.category == "Correctness"]
        assert len(correctness) == 2

    def test_returns_empty_for_missing_file(self, tmp_path: Path):
        items = load_checklist(tmp_path / "nope.md")
        assert items == []

    def test_items_without_heading_get_general(self, tmp_path: Path):
        path = tmp_path / "cl.md"
        path.write_text("- [ ] Some item\n")
        items = load_checklist(path)
        assert len(items) == 1
        assert items[0].category == "General"

    def test_tracks_line_numbers(self, tmp_path: Path):
        path = tmp_path / "cl.md"
        path.write_text(SAMPLE_CHECKLIST)
        items = load_checklist(path)
        assert items[0].line == 3


class TestFormatChecklistPrompt:
    def test_formats_by_category(self):
        items = [
            ChecklistItem("Security", "No injection", 1),
            ChecklistItem("Security", "No XSS", 2),
            ChecklistItem("Style", "Naming conventions", 3),
        ]
        prompt = format_checklist_prompt(items)
        assert "### Security" in prompt
        assert "### Style" in prompt
        assert "- No injection" in prompt

    def test_empty_items(self):
        assert "No checklist" in format_checklist_prompt([])
