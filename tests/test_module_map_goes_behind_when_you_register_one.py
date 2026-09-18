"""`rite add module` leaves the standing instructions wrong about the project.

The module map in every generated `CLAUDE.md` is rendered from
`.rite/modules.yaml`. Registering a module writes that file and nothing
regenerates the map, so the moment the command succeeds the instructions
every session loads are out of date — and on the FIRST module they are
flatly false: "No modules registered yet", in a project with one.

`rite doctor` and `rite start` both notice. Neither is the command the user
just ran, and neither runs on its own.

The notice is not written into `add module`. It lives in the one `invoke`
every command passes through (`cli/staleness.py`), because the same defect
had already turned up three times in three unrelated places in one audit,
and a fourth per-command reminder would only have been waiting for a fifth.
A command that changes an authored input gets it whether or not its author
ever heard of this.

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


class TestAnyCommandGetsIt:
    """The property a per-command reminder cannot have: it holds for commands
    that know nothing about it, including ones not yet written."""

    def test_a_command_that_never_heard_of_this_still_reports(self, project):
        """A throwaway command registered onto the real CLI group. It writes
        an authored input and prints its own single line — exactly what a new
        `rite add <something>` would do — and the notice appears anyway."""
        import click

        from rite_ai.cli.main import add

        @add.command("throwaway")
        def throwaway() -> None:
            path = Path(".rite") / "modules.yaml"
            path.write_text("modules:\n  invented:\n    path: invented/\n")
            click.echo("invented something")

        try:
            out = CliRunner().invoke(cli, ["add", "throwaway"]).output
        finally:
            add.commands.pop("throwaway", None)

        assert "invented something" in out
        assert "rite update --files-only" in out, out

    def test_a_command_that_changes_nothing_stays_silent(self, project):
        """The other half. A notice printed after commands that did not cause
        it is the thing people learn to scroll past."""
        out = CliRunner().invoke(cli, ["doctor"]).output

        assert "now behind what you just changed" not in out

    def test_a_failed_command_is_not_given_a_footnote(self, project):
        """An error is what the reader needs; stapling a notice under it
        buries the line that matters."""
        out = CliRunner().invoke(cli, ["remove", "module", "nope"]).output

        assert "now behind what you just changed" not in out


class TestTheWatchedInputsStayHonest:
    """The gate is a named list, and a named list is the thing that rots.
    Each name has to still matter: changing the file must move `pending()`,
    or it is being watched for nothing and the next reader believes a
    dependency that is gone."""

    def test_every_watched_input_still_changes_what_is_generated(self, project):
        import yaml

        from rite_ai.cli.staleness import GENERATION_INPUTS
        from rite_ai.update.refresh import pending

        edits = {
            ".rite/brief.yaml": lambda d: d.__setitem__(
                "what", {"kind": "library", "features": "invoicing for clinics"}
            ),
            ".rite/modules.yaml": lambda d: d.__setitem__(
                "modules", {"invented": {"path": "invented/"}}
            ),
            ".rite/config.yaml": lambda d: d.__setitem__(
                "ticket_backend",
                {
                    "type": "jira",
                    "site": "https://example.atlassian.net",
                    "repo": "",
                    "projects": {"main": "ZZZ"},
                    "credential": "",
                },
            ),
        }
        assert set(edits) == set(GENERATION_INPUTS), (
            "a watched input has no edit here to prove it matters"
        )

        for rel, edit in edits.items():
            path = project / rel
            original = path.read_text()
            data = yaml.safe_load(original) or {}
            edit(data)
            path.write_text(yaml.safe_dump(data))
            try:
                assert pending(project).behind, (
                    f"{rel} is watched, but changing it leaves nothing behind "
                    "— either the generator stopped reading it, or this is "
                    "the wrong edit to prove it"
                )
            finally:
                path.write_text(original)
