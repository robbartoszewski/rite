"""Journal entries can be copied into the project, by a person's choice (MM8
piece 3, approved by Robert 2026-09-28).

Since MM8 a Manager's journal is outside the project (`journal.journal_dir`),
so that on Linux one Manager cannot write another's. Committing entries is
still a thing a person may want; `rite journal export` is how, and it never
overwrites a different file.
"""

from __future__ import annotations

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.managers import journal


def _entries(root, manager="lead"):
    d = journal.journal_dir(root, manager)
    d.mkdir(parents=True)
    (d / "2026-09-28T10-00-00.md").write_text("first\n")
    (d / "2026-09-28T11-00-00.md").write_text("second\n")
    return d


def test_entries_are_copied_and_the_journal_is_left_as_it_is(tmp_path):
    source = _entries(tmp_path)
    got = journal.export(tmp_path, "lead", tmp_path / "docs" / "journal")
    assert got.copied == ["2026-09-28T10-00-00.md", "2026-09-28T11-00-00.md"]
    assert (tmp_path / "docs/journal/2026-09-28T11-00-00.md").read_text() == "second\n"
    assert sorted(p.name for p in source.iterdir()) == got.copied


def test_exporting_again_copies_nothing_twice(tmp_path):
    _entries(tmp_path)
    journal.export(tmp_path, "lead", tmp_path / "out")
    again = journal.export(tmp_path, "lead", tmp_path / "out")
    assert again.copied == [] and len(again.already_there) == 2


def test_a_different_file_of_the_same_name_refuses_and_nothing_is_copied(tmp_path):
    _entries(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "2026-09-28T11-00-00.md").write_text("somebody's edit\n")
    got = journal.export(tmp_path, "lead", out)
    assert "already exists with different content" in got.refused
    assert not (out / "2026-09-28T10-00-00.md").exists(), "half an export"
    assert (out / "2026-09-28T11-00-00.md").read_text() == "somebody's edit\n"


def test_the_command_says_what_it_did(tmp_path, monkeypatch):
    (tmp_path / ".rite").mkdir()
    (tmp_path / ".rite" / "modules.yaml").write_text("modules: {}\n")
    _entries(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["journal", "export", "lead", "--to", "docs/j"])
    assert result.exit_code == 0, result.output
    assert "exported 2 entr(ies) to docs/j" in result.output
    again = CliRunner().invoke(cli, ["journal", "export", "lead", "--to", "docs/j"])
    assert "2 already there, identical" in again.output
