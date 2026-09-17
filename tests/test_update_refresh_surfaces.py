"""Every generated surface is refreshed, not just CLAUDE.md.

A refresh that updates four of six is a trap: the user believes the project is
current. The review checklist is a verbatim copy; the CI workflow is rendered
with an install pin, so an untouched one from an older release keeps installing
that release's rite in the job SPEC §11.5.1 calls the load-bearing layer."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.init.paths import templates_dir
from rite_ai.cli.init.scaffold import (
    CI_WORKFLOW_REL_PATH,
    render_ci_workflow,
)
from rite_ai.cli.main import cli
from rite_ai.update.refresh import refresh_ci_workflow


def _project(tmp_path: Path, monkeypatch) -> Path:
    import subprocess

    import rite_ai.sandbox as sb
    from rite_ai.cli.init import run_init

    root = tmp_path / "proj"
    root.mkdir()
    # A real repository: `write_ci_workflow` writes nothing outside one, so a
    # test that wants a workflow has to be in one.
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sb, "platform_can_sandbox", lambda: False)
    run_init(root, yes=True)
    return root


class TestTheReviewChecklist:
    def test_a_missing_one_is_installed(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        checklist = root / ".rite" / "review-checklist.md"
        checklist.unlink()
        result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert result.exit_code == 0, result.output
        assert (
            checklist.read_text()
            == (templates_dir() / "review-checklist.md").read_text()
        )

    def test_one_a_release_shipped_is_refreshed(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        checklist = root / ".rite" / "review-checklist.md"
        older = "what an older release shipped\n"
        checklist.write_text(older)
        history = {
            "review-checklist.md": frozenset(
                {hashlib.sha256(older.encode()).hexdigest()}
            )
        }
        with patch("rite_ai.update.template_history.RELEASED", history):
            result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert "refreshed" in result.output
        assert checklist.read_text() != older

    def test_an_edited_one_is_kept_and_reported(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        checklist = root / ".rite" / "review-checklist.md"
        mine = checklist.read_text() + "\n- [ ] My team's extra check\n"
        checklist.write_text(mine)
        result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert "left as it is" in result.output
        assert checklist.read_text() == mine


class TestTheCiWorkflow:
    def _written_by(self, root: Path, version: str) -> Path:
        path = root / CI_WORKFLOW_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_ci_workflow(version))
        return path

    def test_an_untouched_one_is_refreshed_and_its_pin_moves(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, monkeypatch)
        path = self._written_by(root, "0.1.0")
        assert "@v0.1.0" in path.read_text()
        history = {
            "ci/publish-gate.yml": frozenset(
                {hashlib.sha256(render_ci_workflow("0.1.0").encode()).hexdigest()}
            )
        }
        with patch("rite_ai.update.template_history.RELEASED", history):
            change = refresh_ci_workflow(root, frozenset(), apply=True)
        assert change.action == "refreshed"
        assert path.read_text() == render_ci_workflow()
        assert "@v0.1.0" not in path.read_text()

    def test_an_edited_one_is_kept(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        path = self._written_by(root, "0.1.0")
        mine = path.read_text().replace(
            "runs-on: ubuntu-latest", "runs-on: self-hosted"
        )
        path.write_text(mine)
        change = refresh_ci_workflow(root, frozenset(), apply=True)
        assert change.action == "kept-edited"
        assert path.read_text() == mine
        assert "self-hosted" in change.diff

    def test_take_rite_replaces_it_deliberately(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        path = self._written_by(root, "0.1.0")
        path.write_text(path.read_text().replace("ubuntu-latest", "self-hosted"))
        change = refresh_ci_workflow(root, frozenset({"publish-gate.yml"}), apply=True)
        assert change.action == "taken"
        assert path.read_text() == render_ci_workflow()

    def test_an_absent_one_is_reported_never_installed(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        path = root / CI_WORKFLOW_REL_PATH
        if path.exists():
            path.unlink()
        change = refresh_ci_workflow(root, frozenset(), apply=True)
        assert change.action == "absent"
        assert not path.exists()
        result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert "rite publish install-ci" in result.output


def test_a_dry_run_names_every_surface_and_writes_nothing(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch)
    checklist = root / ".rite" / "review-checklist.md"
    checklist.unlink()
    (root / ".claude" / "commands" / "review.md").unlink()
    before_claude = (root / "CLAUDE.md").read_text()

    result = CliRunner().invoke(cli, ["update", "--files-only", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert ".rite/review-checklist.md: would install" in result.output
    assert ".claude/commands/review.md: would install" in result.output
    assert not checklist.exists()
    assert (root / "CLAUDE.md").read_text() == before_claude


class TestDoctorSaysWhenAProjectIsBehind:
    """Nothing else tells anyone the refresh exists, and a project whose
    CLAUDE.md predates a fix cannot report the fix it is missing."""

    def _doctor(self):
        with patch("keyring.get_password", return_value=None):
            return CliRunner().invoke(cli, ["doctor"])

    def test_a_fresh_project_is_current(self, tmp_path, monkeypatch):
        _project(tmp_path, monkeypatch)
        assert "generated files: current" in self._doctor().output

    def test_a_missing_file_is_reported_with_what_to_run(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        (root / ".claude" / "commands" / "review.md").unlink()
        output = self._doctor().output
        assert "generated files: 1 out of date" in output
        assert "rite update --files-only --dry-run" in output

    def test_an_edited_section_is_named_as_yours_not_as_out_of_date(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, monkeypatch)
        claude = root / "CLAUDE.md"
        claude.write_text(
            claude.read_text().replace("Claim → work", "Claim → mine → work", 1)
        )
        assert "changed by you or an older rite" in self._doctor().output

    def test_it_reports_rather_than_failing_the_project(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        (root / ".claude" / "commands" / "review.md").unlink()
        assert self._doctor().exit_code == 0, "being behind is not a problem"


class TestAWorkerFileFromAnOlderRelease:
    """Workers are where the guidance a tester reported missing actually
    lives, and a Worker created before markers existed has no proof of what
    rite wrote. Release history gives it one."""

    def _worker(self, root: Path) -> Path:
        from rite_ai.workspace.manage import add_worker

        result = add_worker(root, "alpha")
        assert result.ok, result.message
        return root / "workers" / "alpha" / "CLAUDE.md"

    def _unmarked(self, text: str) -> str:
        from rite_ai.generated_sections import MARKER_RE

        return "".join(
            line
            for line in text.splitlines(keepends=True)
            if not MARKER_RE.match(line.strip())
        )

    def test_a_section_a_release_wrote_is_refreshed(self, tmp_path, monkeypatch):
        import hashlib

        from rite_ai.generated_sections import parse

        root = _project(tmp_path, monkeypatch)
        path = self._worker(root)
        current = path.read_text()
        older = self._unmarked(current).replace(
            "- Skip the review convention.",
            "- Skip the review convention (v0.2.0 wording).",
            1,
        )
        path.write_text(older)
        section = next(
            b.content
            for b in parse(older)
            if b.kind == "section" and b.heading == "What you must not do"
        )
        history = {
            "What you must not do": frozenset(
                {hashlib.sha256(section.strip().encode()).hexdigest()}
            )
        }
        with patch("rite_ai.update.section_history.SECTIONS", history):
            result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert result.exit_code == 0, result.output
        assert "workers/alpha/CLAUDE.md" in result.output
        assert "v0.2.0 wording" not in path.read_text()

    def test_an_edited_worker_section_is_kept(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        path = self._worker(root)
        mine = path.read_text().replace(
            "- Skip the review convention.",
            "- Skip the review convention.\n- Deploy on Fridays.",
            1,
        )
        path.write_text(mine)
        result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert "left as it is" in result.output
        assert "Deploy on Fridays." in path.read_text()
