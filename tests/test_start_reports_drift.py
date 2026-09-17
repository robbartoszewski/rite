"""`rite start` says when a session's own instructions are behind.

A session routes on CLAUDE.md and the commands in `.claude/` and cannot tell
that an older rite wrote them. `start` is where a session begins, so it is
where one line about it belongs — and it must never be able to stop `start`
from orienting someone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli


def _project(tmp_path: Path, monkeypatch) -> Path:
    import subprocess

    import rite_ai.sandbox as sb
    from rite_ai.cli.init import run_init

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    run_init(root, yes=True)
    return root


def _start(root: Path):
    return CliRunner().invoke(cli, ["start", str(root)])


def test_a_current_project_says_nothing_about_it(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    assert "behind this rite" not in _start(root).output


def test_a_project_behind_is_told_once_with_what_to_run(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    (root / ".claude" / "commands" / "review.md").unlink()
    output = _start(root).output
    assert "1 generated file(s) are behind this rite" in output
    assert "rite update --files-only --dry-run" in output


def test_an_edited_section_is_not_called_out_of_date(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    claude = root / "CLAUDE.md"
    claude.write_text(
        claude.read_text().replace("Claim → work", "Claim → mine → work", 1)
    )
    assert "behind this rite" not in _start(root).output


def test_it_can_never_stop_start_from_orienting(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    with patch(
        "rite_ai.update.refresh.refresh_project", side_effect=RuntimeError("boom")
    ):
        result = _start(root)
    assert result.exit_code == 0, result.output
    assert "behind this rite" not in result.output
    # Orientation still printed: the project loaded, and the next step named.
    assert "loaded" in result.output and "/spec" in result.output
