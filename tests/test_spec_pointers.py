"""rite points at a project's existing spec (SPEC §9.13, D-52).

Pointers, never copies. `.rite/context/` was the obvious mechanism and is
the wrong one: it copies, and caps an entry at 4,096 bytes — rite's own
spec is ~231,000, so it would be flagged `oversize` on every `rite
doctor`, and a copy goes stale silently the moment the original moves on.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.init.claude_gen import _spec_section as project_spec_section
from rite_ai.cli.init.detect import detect_decision_convention, detect_spec_paths
from rite_ai.cli.main import cli
from rite_ai.config.models import ProjectConfig, SpecConfig
from rite_ai.config.parse import parse_config
from rite_ai.workspace.manage import _spec_section as worker_spec_section


def _project(root: Path, spec_yaml: str = "") -> Path:
    (root / ".rite").mkdir(parents=True, exist_ok=True)
    (root / ".rite" / "brief.yaml").write_text("project:\n  name: p\n  role: manager\n")
    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n" + spec_yaml
    )
    return root


class TestDetection:
    def test_it_finds_the_conventional_names(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# spec\n")
        (tmp_path / "ARCHITECTURE.md").write_text("# arch\n")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("x")
        found = detect_spec_paths(tmp_path)
        assert "SPEC.md" in found
        assert "ARCHITECTURE.md" in found
        assert "docs/" in found

    def test_an_empty_docs_directory_is_not_proposed(self, tmp_path: Path):
        (tmp_path / "docs").mkdir()
        assert detect_spec_paths(tmp_path) == []

    def test_it_looks_at_a_modules_top_level(self, tmp_path: Path):
        """A monorepo genuinely keeps its spec under a module."""
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "DESIGN.md").write_text("# d\n")
        assert detect_spec_paths(tmp_path, ["backend"]) == ["backend/DESIGN.md"]

    def test_it_does_not_recurse_into_a_module(self, tmp_path: Path):
        """Recursing finds the docs of every vendored dependency and
        proposes them with the same confidence as a real spec — and a
        wrong proposal that gets accepted is worse than a missed one."""
        deep = tmp_path / "backend" / "node_modules" / "thing"
        deep.mkdir(parents=True)
        (deep / "SPEC.md").write_text("# someone else's\n")
        assert detect_spec_paths(tmp_path, ["backend"]) == []

    def test_a_module_pointing_at_the_root_is_not_scanned_twice(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text("# spec\n")
        assert detect_spec_paths(tmp_path, ["."]) == ["SPEC.md"]


class TestConventionDetection:
    def test_a_decision_register_is_recognised(self, tmp_path: Path):
        (tmp_path / "SPEC.md").write_text(
            "| D-1 | x | y |\n| D-2 | x | y |\n| D-3 | x | y |\n"
        )
        convention = detect_decision_convention(tmp_path, ["SPEC.md"])
        assert "D-<number>" in convention
        assert "SPEC.md" in convention

    def test_a_stray_match_is_not_a_convention(self, tmp_path: Path):
        """One or two hits are as likely to be a version string."""
        (tmp_path / "SPEC.md").write_text("see D-1 for background\n")
        assert detect_decision_convention(tmp_path, ["SPEC.md"]) == ""

    def test_directories_are_not_walked(self, tmp_path: Path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("D-1 D-2 D-3\n")
        assert detect_decision_convention(tmp_path, ["docs/"]) == ""

    def test_a_missing_file_is_survivable(self, tmp_path: Path):
        assert detect_decision_convention(tmp_path, ["gone.md"]) == ""


class TestTheWorkerIsPointedNotLoaded:
    def test_the_section_carries_paths_not_content(self):
        section = worker_spec_section(SpecConfig(paths=["SPEC.md", "docs/adr/"]))
        assert "`SPEC.md`" in section
        assert "`docs/adr/`" in section
        assert "Read what your ticket needs; do not read it all." in section

    def test_it_states_that_rite_cannot_tell_if_the_spec_is_current(self):
        """The line that turns a stale spec from a silent defect into a
        reported one."""
        section = " ".join(worker_spec_section(SpecConfig(paths=["SPEC.md"])).split())
        assert "rite cannot tell whether this is current" in section
        assert (
            "say so in the ticket rather than silently implementing either" in section
        )

    def test_the_convention_is_included_when_there_is_one(self):
        section = worker_spec_section(
            SpecConfig(paths=["SPEC.md"], convention="Decisions are cited as D-<n>.")
        )
        assert "D-<n>" in section

    def test_no_spec_means_no_section_at_all(self):
        assert worker_spec_section(SpecConfig()) == ""
        assert project_spec_section(ProjectConfig()) == ""

    def test_a_real_worker_claude_md_carries_it(self, tmp_path: Path, monkeypatch):
        from rite_ai.workspace import add_worker

        root = _project(
            tmp_path,
            'spec:\n  paths: [SPEC.md]\n  convention: "Cited as D-<n>."\n',
        )
        (root / "SPEC.md").write_text("# spec\n")
        result = add_worker(root, "alpha")
        assert result.ok, result.message
        md = (root / "workers" / "alpha" / "CLAUDE.md").read_text()
        assert "## Project spec" in md
        assert "`SPEC.md`" in md
        assert "rite cannot tell whether this is current" in md
        # Pointed at, not pasted in.
        assert "# spec" not in md

    def test_a_worker_is_still_created_when_config_is_unreadable(self, tmp_path: Path):
        from rite_ai.workspace import add_worker

        root = _project(tmp_path)
        (root / ".rite" / "config.yaml").write_text("spec: [not, a, mapping\n")
        assert add_worker(root, "alpha").ok


class TestSpecCommands:
    def test_add_records_a_path(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        (root / "SPEC.md").write_text("# s\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        assert result.exit_code == 0, result.output
        assert parse_config(root / ".rite" / "config.yaml").spec.paths == ["SPEC.md"]

    def test_add_refuses_a_path_that_does_not_exist(self, tmp_path, monkeypatch):
        """A pointer to nothing is the one state this cannot survive: the
        Worker is told where the design is and finds nothing there."""
        root = _project(tmp_path)
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "add", "nope.md"])
        assert result.exit_code == 1
        assert "no such path" in result.output

    def test_a_directory_gets_a_trailing_slash(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        (root / "docs").mkdir()
        monkeypatch.chdir(root)
        CliRunner().invoke(cli, ["spec", "add", "docs"])
        assert parse_config(root / ".rite" / "config.yaml").spec.paths == ["docs/"]

    def test_adding_twice_is_not_a_duplicate(self, tmp_path, monkeypatch):
        root = _project(tmp_path)
        (root / "SPEC.md").write_text("# s\n")
        monkeypatch.chdir(root)
        CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        CliRunner().invoke(cli, ["spec", "add", "SPEC.md"])
        assert parse_config(root / ".rite" / "config.yaml").spec.paths == ["SPEC.md"]

    def test_remove_drops_it(self, tmp_path, monkeypatch):
        root = _project(tmp_path, "spec:\n  paths: [SPEC.md]\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "remove", "SPEC.md"])
        assert result.exit_code == 0, result.output
        assert parse_config(root / ".rite" / "config.yaml").spec.paths == []

    def test_remove_tolerates_a_missing_trailing_slash(self, tmp_path, monkeypatch):
        root = _project(tmp_path, "spec:\n  paths: [docs/]\n")
        monkeypatch.chdir(root)
        assert CliRunner().invoke(cli, ["spec", "remove", "docs"]).exit_code == 0

    def test_remove_names_what_is_configured_when_it_misses(
        self, tmp_path, monkeypatch
    ):
        root = _project(tmp_path, "spec:\n  paths: [SPEC.md]\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["spec", "remove", "other.md"])
        assert result.exit_code == 1
        assert "SPEC.md" in result.output

    def test_there_is_no_list_command(self):
        """`rite doctor` already prints every configured path while
        checking it resolves; a second command doing the same read is CLI
        surface that has not earned its place."""
        assert set(cli.commands["spec"].commands) == {"add", "remove"}


class TestDoctorChecksThePointersResolve:
    def _doctor_project(self, tmp_path: Path, spec_yaml: str) -> Path:
        import subprocess

        root = _project(tmp_path, spec_yaml)
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
            cwd=root,
            check=True,
        )
        return root

    def test_a_resolving_path_is_reported(self, tmp_path, monkeypatch):
        root = self._doctor_project(tmp_path, "spec:\n  paths: [SPEC.md]\n")
        (root / "SPEC.md").write_text("# s\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert "spec SPEC.md: found (file)" in result.output

    def test_a_dangling_pointer_is_a_problem(self, tmp_path, monkeypatch):
        """rite cannot know a spec is CURRENT; it can know it is GONE."""
        root = self._doctor_project(tmp_path, "spec:\n  paths: [SPEC.md]\n")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 1
        assert "spec SPEC.md: MISSING" in result.output

    def test_no_spec_configured_says_how_to_add_one(self, tmp_path, monkeypatch):
        root = self._doctor_project(tmp_path, "")
        monkeypatch.chdir(root)
        result = CliRunner().invoke(cli, ["doctor"])
        assert "rite spec add" in result.output
