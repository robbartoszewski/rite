from pathlib import Path

from rite_ai.context import (
    add_context,
    check_integrity,
    list_context,
    remove_context,
)
from rite_ai.context.manage import CONTEXT_INDEX_TEMPLATE


def _setup(tmp_path: Path) -> Path:
    (tmp_path / ".rite" / "context").mkdir(parents=True)
    return tmp_path


class TestAddAndList:
    def test_add_creates_file_and_index_entry(self, tmp_path: Path):
        root = _setup(tmp_path)
        err = add_context(
            root,
            "database.md",
            "Before writing migrations",
            "Postgres conventions",
            content="# Database\n\nUse snake_case.\n",
        )
        assert err is None
        entries = list_context(root)
        assert len(entries) == 1
        assert entries[0].file == "database.md"
        assert entries[0].trigger == "Before writing migrations"
        assert (root / ".rite" / "context" / "database.md").exists()

    def test_add_from_source_path(self, tmp_path: Path):
        root = _setup(tmp_path)
        src = tmp_path / "notes.md"
        src.write_text("# Notes\n")
        err = add_context(
            root, "notes.md", "When reviewing", "Review notes", source_path=src
        )
        assert err is None
        assert (root / ".rite" / "context" / "notes.md").read_text() == "# Notes\n"

    def test_rejects_duplicate(self, tmp_path: Path):
        root = _setup(tmp_path)
        add_context(root, "api.md", "Before routes", "API")
        err = add_context(root, "api.md", "Again", "Dup")
        assert err is not None
        assert "already" in err

    def test_creates_placeholder_file(self, tmp_path: Path):
        root = _setup(tmp_path)
        add_context(root, "testing.md", "Before tests", "Test patterns")
        content = (root / ".rite" / "context" / "testing.md").read_text()
        assert "Testing" in content


class TestRemove:
    def test_removes_entry_and_file(self, tmp_path: Path):
        root = _setup(tmp_path)
        add_context(root, "old.md", "Never", "Old stuff", content="old")
        err = remove_context(root, "old.md")
        assert err is None
        assert not (root / ".rite" / "context" / "old.md").exists()
        assert len(list_context(root)) == 0

    def test_rejects_unknown(self, tmp_path: Path):
        root = _setup(tmp_path)
        err = remove_context(root, "nope.md")
        assert err is not None
        assert "not in index" in err


class TestIntegrity:
    def test_detects_orphan_file(self, tmp_path: Path):
        root = _setup(tmp_path)
        (root / ".rite" / "context" / "INDEX.md").write_text(
            "# Context Index\n\n| File | When | Desc |\n|---|---|---|\n"
        )
        (root / ".rite" / "context" / "stray.md").write_text("x")
        issues = check_integrity(root)
        kinds = [i.kind for i in issues]
        assert "orphan_file" in kinds

    def test_detects_missing_file(self, tmp_path: Path):
        root = _setup(tmp_path)
        add_context(root, "gone.md", "When", "Desc", content="x")
        (root / ".rite" / "context" / "gone.md").unlink()
        issues = check_integrity(root)
        kinds = [i.kind for i in issues]
        assert "missing_file" in kinds

    def test_clean_dir_has_no_issues(self, tmp_path: Path):
        root = _setup(tmp_path)
        add_context(root, "ok.md", "When needed", "Fine", content="ok")
        issues = check_integrity(root)
        assert issues == []


class TestAddContextIntoAnInitCreatedIndex:
    """Regression: `rite init`'s scaffold pre-creates INDEX.md with
    explanatory prose AFTER the (empty) table. Appending a row at end-of-
    file used to land it after that prose — outside the table entirely —
    which `list_context`'s own parser then failed to see, silently."""

    def test_row_lands_inside_the_table_not_after_the_prose(self, tmp_path: Path):
        root = _setup(tmp_path)
        idx = root / ".rite" / "context" / "INDEX.md"
        idx.write_text(CONTEXT_INDEX_TEMPLATE)

        err = add_context(
            root, "database.md", "Before migrations", "Postgres notes", content="x"
        )
        assert err is None

        text = idx.read_text()
        table_end = text.index("This index grows")
        assert "database.md" in text[:table_end], (
            "the new row must appear BEFORE the trailing prose, i.e. inside "
            "the table — not appended after it"
        )

        entries = list_context(root)
        assert len(entries) == 1
        assert entries[0].file == "database.md"

    def test_multiple_rows_added_to_an_init_created_index_stay_in_order(
        self, tmp_path: Path
    ):
        root = _setup(tmp_path)
        idx = root / ".rite" / "context" / "INDEX.md"
        idx.write_text(CONTEXT_INDEX_TEMPLATE)

        add_context(root, "a.md", "When A", "Desc A", content="a")
        add_context(root, "b.md", "When B", "Desc B", content="b")

        entries = list_context(root)
        assert [e.file for e in entries] == ["a.md", "b.md"]
