from pathlib import Path

from rite_ai.review.merge import merge_checklists


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_merge_with_no_checklists_returns_empty(tmp_path: Path):
    assert merge_checklists(tmp_path) == []


def test_merge_project_only(tmp_path: Path):
    _write(
        tmp_path / ".rite" / "review-checklist.md",
        "## Security\n\n- [ ] no secrets\n",
    )
    items = merge_checklists(tmp_path)
    assert [i.text for i in items] == ["no secrets"]


def test_merge_appends_repo_checklist_after_project(tmp_path: Path):
    """Project items first, the repo's own after, never the reverse.

    NOT a SPEC requirement, though this docstring used to cite one: §7.2
    gives the two file locations and says nothing about how they combine.
    The clause quoted here was invented, and it outlived the retraction in
    `merge.py`'s own docstring by one file — a fabricated citation is hard
    to kill precisely because it reads like provenance.
    """
    _write(
        tmp_path / ".rite" / "review-checklist.md",
        "## Security\n\n- [ ] project-wide item\n",
    )
    _write(
        tmp_path / "backend" / ".rite" / "review-checklist.md",
        "## Backend-specific\n\n- [ ] repo-only item\n",
    )
    items = merge_checklists(tmp_path, module_path="backend")
    assert [i.text for i in items] == ["project-wide item", "repo-only item"]


def test_merge_without_module_path_skips_repo_checklist(tmp_path: Path):
    _write(
        tmp_path / ".rite" / "review-checklist.md",
        "## Security\n\n- [ ] project-wide item\n",
    )
    _write(
        tmp_path / "backend" / ".rite" / "review-checklist.md",
        "## Backend-specific\n\n- [ ] repo-only item\n",
    )
    items = merge_checklists(tmp_path)
    assert [i.text for i in items] == ["project-wide item"]


def test_merge_missing_repo_checklist_is_not_an_error(tmp_path: Path):
    _write(
        tmp_path / ".rite" / "review-checklist.md",
        "## Security\n\n- [ ] project-wide item\n",
    )
    items = merge_checklists(tmp_path, module_path="frontend")
    assert [i.text for i in items] == ["project-wide item"]
