"""`rite add module` leaves the standing instructions wrong about the project.

The module map in every generated `CLAUDE.md` is rendered from
`.rite/modules.yaml`. Registering a module writes that file and nothing
regenerates the map, so the moment the command succeeds the instructions
every session loads are out of date — and on the FIRST module they are
flatly false: "No modules registered yet", in a project with one.

`rite doctor` and `rite start` both notice. Neither is the command the user
just ran, and neither runs on its own.

`pending()` is the shared answer to "what would a refresh do here". It was
open-coded in doctor and in `start` before this needed it a third time, and
the two copies had already drifted: `start` counted CHANGES and called them
FILES, so one CLAUDE.md a release out of date announced itself as three
files behind.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.chdir(root)
    assert CliRunner().invoke(cli, ["init", "--yes", "."]).exit_code == 0
    return root


def _module(root: Path, name: str) -> None:
    path = root / name
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "x"],
        cwd=path,
        check=True,
    )


class TestTheCommandThatCausedItSaysSo:
    def test_add_module_names_the_refresh(self, project):
        """THE DEFECT: it printed "module 'backend' registered" and stopped,
        leaving CLAUDE.md saying no modules are registered."""
        _module(project, "backend")

        out = CliRunner().invoke(cli, ["add", "module", "backend"]).output

        assert "rite update --files-only" in out, out

    def test_the_root_file_really_is_wrong_until_then(self, project):
        """The claim the notice is about, measured rather than assumed."""
        _module(project, "backend")

        CliRunner().invoke(cli, ["add", "module", "backend"])

        assert "No modules registered yet" in (project / "CLAUDE.md").read_text()
        assert CliRunner().invoke(cli, ["update", "--files-only"]).exit_code == 0
        assert "No modules registered yet" not in (project / "CLAUDE.md").read_text()

    def test_remove_module_says_it_too(self, project):
        _module(project, "backend")
        CliRunner().invoke(cli, ["add", "module", "backend"])
        CliRunner().invoke(cli, ["update", "--files-only"])

        out = CliRunner().invoke(cli, ["remove", "module", "backend"]).output

        assert "rite update --files-only" in out, out

    def test_it_is_silent_when_nothing_is_behind(self, project):
        """A line printed every time is a line nobody reads. A freshly
        refreshed project has nothing to say."""
        _module(project, "backend")
        CliRunner().invoke(cli, ["add", "module", "backend"])
        CliRunner().invoke(cli, ["update", "--files-only"])

        out = CliRunner().invoke(cli, ["start"]).output

        assert "behind" not in out, out


class TestPendingCountsFilesAndChangesApart:
    def test_one_file_behind_in_two_sections_is_one_file(self, project):
        """What `start` got wrong. Deleting two generated sections leaves one
        file to fix, and two things to fix in it."""
        claude = project / "CLAUDE.md"
        text = claude.read_text()
        for heading in ("## Claims system", "## Review convention"):
            start = text.index(heading)
            end = text.index("\n## ", start + 1)
            text = text[:start] + text[end + 1 :]
        claude.write_text(text)

        from rite_ai.update.refresh import pending

        plan = pending(project)

        assert len(plan.behind) == 2, [c.target for c in plan.behind]
        assert plan.files == ["CLAUDE.md"]

    def test_start_reports_the_file_count(self, project):
        claude = project / "CLAUDE.md"
        text = claude.read_text()
        for heading in ("## Claims system", "## Review convention"):
            begin = text.index(heading)
            end = text.index("\n## ", begin + 1)
            text = text[:begin] + text[end + 1 :]
        claude.write_text(text)

        out = CliRunner().invoke(cli, ["start"]).output

        assert "1 generated file(s) are behind" in out, out
