import json
import subprocess
from pathlib import Path

from rite_ai.cli.init.detect import (
    detect_languages,
    detect_module_commands,
    detect_platform,
    detect_repos,
    run_detection,
)


def _git_repo(path: Path, origin: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    if origin:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=path, check=True)
    (path / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "x",
        ],
        cwd=path,
        check=True,
    )


def test_detect_repos_finds_git_dirs_with_remote(tmp_path: Path):
    _git_repo(tmp_path / "backend", origin="git@github.com:org/backend.git")
    _git_repo(tmp_path / "shared")  # local only
    (tmp_path / "not-a-repo").mkdir()

    repos = detect_repos(tmp_path)
    names = {r.name for r in repos}
    assert names == {"backend", "shared"}

    backend = next(r for r in repos if r.name == "backend")
    assert backend.url == "git@github.com:org/backend.git"
    assert backend.branch == "main"
    assert backend.local_only is False

    shared = next(r for r in repos if r.name == "shared")
    assert shared.url is None
    assert shared.local_only is True


def test_detect_repos_skips_noise_dirs(tmp_path: Path):
    _git_repo(tmp_path / "node_modules")
    _git_repo(tmp_path / ".venv")
    assert detect_repos(tmp_path) == []


def test_detect_platform_is_stable():
    assert detect_platform() in ("linux", "macos", "windows")


def test_detect_languages_python(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert "python" in detect_languages(tmp_path)


def test_detect_languages_typescript_via_tsconfig(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    (tmp_path / "tsconfig.json").write_text("{}")
    assert detect_languages(tmp_path) == ["typescript"]


def test_detect_languages_typescript_via_devdeps(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"typescript": "^5.0.0"}})
    )
    assert detect_languages(tmp_path) == ["typescript"]


def test_detect_languages_plain_javascript(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}))
    assert detect_languages(tmp_path) == ["javascript"]


def test_detect_languages_aggregates_across_modules(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(json.dumps({}))
    langs = detect_languages(tmp_path)
    assert set(langs) == {"python", "javascript"}


def test_detect_module_commands_python_pytest_ruff(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[dependency-groups]\ndev=['pytest','ruff']\n"
    )
    cmds = detect_module_commands(tmp_path)
    assert cmds.detected is True
    assert cmds.install == "uv sync"
    assert cmds.test == "uv run pytest"
    assert cmds.lint == "uv run ruff check ."


def test_detect_module_commands_python_no_pytest(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cmds = detect_module_commands(tmp_path)
    assert cmds.detected is True
    assert cmds.test is None
    assert cmds.lint is None


def test_detect_module_commands_node_pnpm(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest", "build": "vite build"}})
    )
    (tmp_path / "pnpm-lock.yaml").write_text("")
    cmds = detect_module_commands(tmp_path)
    assert cmds.install == "pnpm install"
    assert cmds.test == "pnpm run test"
    assert cmds.build == "pnpm run build"
    assert cmds.lint is None  # no lint script


def test_detect_module_commands_rust(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    cmds = detect_module_commands(tmp_path)
    assert cmds.test == "cargo test"
    assert cmds.build == "cargo build"


def test_detect_module_commands_unknown(tmp_path: Path):
    cmds = detect_module_commands(tmp_path)
    assert cmds.detected is False
    assert cmds.install is None
    assert cmds.test is None


def test_run_detection_summary(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    summary = run_detection(tmp_path)
    assert summary.has_language_markers is True
    assert "python" in summary.languages
    assert summary.platform in ("linux", "macos", "windows")
