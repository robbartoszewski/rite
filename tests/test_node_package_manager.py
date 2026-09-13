"""A Node module's commands use the package manager the project actually uses.

The branch this replaces was "pnpm-lock.yaml, else yarn.lock, else npm". So a
bun project was run with npm, `packageManager` in package.json was never read, a
workspace member — whose lockfile sits at the repository root — was treated as
npm, and a stale second lockfile was settled by precedence. Every one of those
produces commands that fail for a reason that looks like a broken project.
"""

import json
import subprocess
from pathlib import Path

from rite_ai.cli.init.detect import detect_module_commands, module_commands
from rite_ai.config.models import Module, RecordedCommands, SandboxConfig

_SCRIPTS = {
    "build": "tsc",
    "test": "vitest run",
    "lint": "eslint .",
    "format": "prettier -w .",
}


def _node(dir_path: Path, *lockfiles: str, **manifest) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "package.json").write_text(
        json.dumps({"scripts": _SCRIPTS, **manifest})
    )
    for lock in lockfiles:
        (dir_path / lock).write_text("")
    return dir_path


def test_npm_with_a_lockfile_installs_without_rewriting_it(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path, "package-lock.json"))
    assert cmds.install == "npm ci"
    assert cmds.test == "npm run test"
    assert cmds.format == "npm run format"
    assert cmds.source == "package.json, package-lock.json"


def test_pnpm(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path, "pnpm-lock.yaml"))
    assert cmds.install == "pnpm install --frozen-lockfile"
    assert cmds.build == "pnpm run build"


def test_yarn_classic(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path, "yarn.lock"))
    assert cmds.install == "yarn install --frozen-lockfile"
    assert cmds.lint == "yarn run lint"


def test_yarn_berry_is_told_apart_by_its_config_file(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path, "yarn.lock", ".yarnrc.yml"))
    assert cmds.install == "yarn install --immutable"


def test_bun_in_both_lockfile_formats(tmp_path: Path):
    for lock in ("bun.lock", "bun.lockb"):
        cmds = detect_module_commands(_node(tmp_path / lock, lock))
        assert cmds.install == "bun install --frozen-lockfile"
        assert cmds.test == "bun run test"


def test_package_manager_field_is_the_projects_own_statement(tmp_path: Path):
    cmds = detect_module_commands(
        _node(tmp_path, "package-lock.json", packageManager="pnpm@9.12.0+sha512.abc")
    )
    assert cmds.install == "pnpm install --frozen-lockfile"
    assert cmds.source == "package.json, packageManager pnpm@9.12.0+sha512.abc"


def test_package_manager_field_names_yarn_berry_by_major_version(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path, packageManager="yarn@4.5.0"))
    assert cmds.install == "yarn install --immutable"


def test_two_managers_lockfiles_are_not_settled_by_guessing(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path, "yarn.lock", "package-lock.json"))
    assert not cmds.detected
    assert cmds.test is None
    assert "package-lock.json" in cmds.note and "yarn.lock" in cmds.note
    assert "modules.yaml" in cmds.note


def test_no_lockfile_assumes_npm_and_says_so(tmp_path: Path):
    cmds = detect_module_commands(_node(tmp_path))
    assert cmds.install == "npm install"
    assert "assumed npm" in cmds.note


def test_a_workspace_member_uses_the_lockfile_at_its_repository_root(tmp_path: Path):
    repo = tmp_path / "backend"
    _node(repo, "pnpm-lock.yaml")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    member = _node(repo / "packages" / "api")

    cmds = detect_module_commands(member, boundary=tmp_path)

    assert cmds.install == "pnpm install --frozen-lockfile"
    assert cmds.source == "package.json, pnpm-lock.yaml in backend/"


def test_the_search_stops_at_the_repository_root(tmp_path: Path):
    """A lockfile belonging to some other project further up is not this one's."""
    _node(tmp_path, "yarn.lock")
    repo = _node(tmp_path / "service")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    cmds = detect_module_commands(repo, boundary=tmp_path)

    assert cmds.install == "npm install"


def test_without_a_boundary_only_the_module_itself_is_searched(tmp_path: Path):
    _node(tmp_path, "pnpm-lock.yaml")
    cmds = detect_module_commands(_node(tmp_path / "child"))
    assert cmds.install == "npm install"


def test_a_missing_script_is_missing_not_invented(tmp_path: Path):
    module = tmp_path / "m"
    module.mkdir()
    (module / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    cmds = detect_module_commands(module)
    assert cmds.test == "npm run test"
    assert cmds.lint is None and cmds.format is None and cmds.build is None


def test_a_recorded_command_fixes_two_lockfiles_in_one_line(tmp_path: Path):
    """The escape hatch for the case detection refuses to guess."""
    _node(tmp_path / "api", "yarn.lock", "package-lock.json")
    module = Module(
        name="api", path="api/", commands=RecordedCommands(test="npm run test")
    )

    cmds = module_commands(module, tmp_path, SandboxConfig(enabled=False))

    assert cmds.test == "npm run test"
    assert cmds.configured == frozenset({"test"})
    assert "stale" in cmds.note  # still said, for the keys nobody recorded
