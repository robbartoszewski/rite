"""Every generated surface is refreshed, not just CLAUDE.md.

A refresh that updates four of six is a trap: the user believes the project is
current. The review checklist is a verbatim copy; the CI workflow is rendered
with an install pin, so an untouched one from an older release keeps installing
that release's rite in the job SPEC §11.5.1 calls the load-bearing layer."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
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


class TestAuthoredContentIsNeverTouched:
    """A real project's `.rite/` holds its brief, modules, config, context and
    knowledge base — and whatever architecture, plan and decisions the team
    keeps beside them. Regenerating instructions must not be able to reach any
    of it. This is the reason `rite init` refuses an initialised directory,
    and refreshing must not become the way around that refusal."""

    AUTHORED = {
        ".rite/architecture.md": "# Architecture\n\nOur own notes.\n",
        ".rite/plan.md": "# Plan\n\nPhase 1: the thing.\n",
        ".rite/decisions.md": "# Decisions\n\nD-1: we chose X.\n",
        ".rite/context/domain.md": "Billing rules.\n",
        "SPEC.md": "# Spec\n\nDesign lives here.\n",
    }

    def _authored(self, root: Path) -> dict[str, str]:
        for rel, text in self.AUTHORED.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and ".git/" not in str(p.relative_to(root))
        }

    def test_a_refresh_that_changes_things_leaves_them_byte_identical(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, monkeypatch)
        before = self._authored(root)
        # Something for the refresh to actually do.
        (root / ".claude" / "commands" / "review.md").unlink()
        claude = root / "CLAUDE.md"
        claude.write_text(
            "".join(
                line
                for line in claude.read_text().splitlines(keepends=True)
                if "rite:sha256" not in line
            )
        )

        result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert result.exit_code == 0, result.output

        after = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and ".git/" not in str(p.relative_to(root))
        }
        changed = {k for k in before if before[k] != after.get(k)}
        assert changed <= {"CLAUDE.md"}, f"refresh touched {changed}"
        for rel in self.AUTHORED:
            assert (root / rel).read_text() == self.AUTHORED[rel]

    def test_the_allowlist_refuses_everything_rite_does_not_generate(self, tmp_path):
        from rite_ai.update.refresh import may_refresh

        root = tmp_path
        for rel in (
            ".rite/brief.yaml",
            ".rite/config.yaml",
            ".rite/modules.yaml",
            ".rite/architecture.md",
            ".rite/context/domain.md",
            ".rite/kb/INDEX.md",
            "SPEC.md",
            "src/app.py",
            "workers/alpha/worker.yml",
            "workers/alpha/module/file.py",
        ):
            assert not may_refresh(root, root / rel), rel
        for rel in (
            "CLAUDE.md",
            ".claude/commands/review.md",
            ".claude/agents/reviewer-round1.md",
            ".rite/review-checklist.md",
            ".github/workflows/publish-gate.yml",
            "workers/alpha/CLAUDE.md",
            "workers/alpha/.claude/commands/review.md",
        ):
            assert may_refresh(root, root / rel), rel

    def test_a_write_outside_the_allowlist_raises_rather_than_happening(self, tmp_path):
        from rite_ai.update.refresh import RefusedWrite, _write

        target = tmp_path / ".rite" / "brief.yaml"
        target.parent.mkdir(parents=True)
        target.write_text("project:\n  name: acme\n")
        with pytest.raises(RefusedWrite):
            _write(tmp_path, target, "clobbered\n")
        assert target.read_text() == "project:\n  name: acme\n"

    def test_a_dry_run_writes_nothing_at_all(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        before = self._authored(root)
        (root / ".claude" / "commands" / "review.md").unlink()
        before.pop(".claude/commands/review.md", None)

        result = CliRunner().invoke(cli, ["update", "--files-only", "--dry-run"])
        assert result.exit_code == 0, result.output
        after = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and ".git/" not in str(p.relative_to(root))
        }
        assert after == before


def test_a_workers_own_added_section_survives(tmp_path, monkeypatch):
    from rite_ai.workspace.manage import add_worker

    root = _project(tmp_path, monkeypatch)
    assert add_worker(root, "alpha").ok
    path = root / "workers" / "alpha" / "CLAUDE.md"
    path.write_text(
        path.read_text() + "\n## Notes from my Manager\n\nUse the staging DB.\n"
    )

    result = CliRunner().invoke(cli, ["update", "--files-only"])
    assert result.exit_code == 0, result.output
    assert "## Notes from my Manager\n\nUse the staging DB.\n" in path.read_text()


def test_init_on_an_existing_project_names_the_refresh(tmp_path, monkeypatch):
    """The dead end this work exists to remove: a project whose `.rite/` holds
    hand-written design cannot wipe, and wiping was the only thing offered."""
    from rite_ai.cli.init import run_init

    root = _project(tmp_path, monkeypatch)
    (root / ".rite" / "architecture.md").write_text("# Architecture\n")
    result = run_init(root, yes=True)
    assert result.status == "already_initialized"
    assert "rite update --files-only" in result.message
    assert (root / ".rite" / "architecture.md").read_text() == "# Architecture\n"


class TestTheGitignoreBlock:
    """rite's ignore block grows between releases, and a project missing a
    line tracks runtime state it should not — which `rite doctor` reports and
    nothing delivered."""

    def test_newer_ignore_lines_are_added_and_the_users_lines_kept(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, monkeypatch)
        gitignore = root / ".gitignore"
        mine = "# my own\nbuild/\n*.log\n"
        gitignore.write_text(mine)

        result = CliRunner().invoke(cli, ["update", "--files-only"])
        assert result.exit_code == 0, result.output
        assert "added rite's newer ignore lines" in result.output
        after = gitignore.read_text()
        assert after.startswith(mine), "the user's lines moved or changed"
        assert ".rite/**/*.lock" in after

    def test_a_project_that_already_has_them_is_left_alone(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        before = (root / ".gitignore").read_text()
        CliRunner().invoke(cli, ["update", "--files-only"])
        assert (root / ".gitignore").read_text() == before

    def test_a_committed_knowledge_base_is_not_flipped_back(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, monkeypatch)
        gitignore = root / ".gitignore"
        gitignore.write_text("!.rite/kb/\n")
        CliRunner().invoke(cli, ["update", "--files-only"])
        after = gitignore.read_text()
        assert after.count("!.rite/kb/") == 1
        assert ".rite/kb/.cache/" in after

    def test_a_dry_run_adds_nothing(self, tmp_path, monkeypatch):
        root = _project(tmp_path, monkeypatch)
        gitignore = root / ".gitignore"
        gitignore.write_text("build/\n")
        result = CliRunner().invoke(cli, ["update", "--files-only", "--dry-run"])
        assert "would add rite's newer ignore lines" in result.output
        assert gitignore.read_text() == "build/\n"
