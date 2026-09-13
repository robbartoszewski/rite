"""Detection: existing repos, language markers, platform, per-module commands.

Everything here is best-effort and additive-only — it pre-fills questionnaire
defaults and derives generated commands. Nothing here invents an answer; when
detection finds nothing, callers must fall back to an explicit placeholder.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {
    ".git",
    ".rite",
    ".claude",
    "workers",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".mypy_cache",
    ".tox",
}

_LANGUAGE_MARKERS: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Cargo.toml": "rust",
    "pubspec.yaml": "dart",
    "go.mod": "go",
}


@dataclass
class DetectedRepo:
    name: str
    path: str  # relative, trailing slash, e.g. "backend/"
    url: str | None
    branch: str
    local_only: bool


@dataclass
class ModuleCommands:
    """Build/test/lint commands derived from a module's own manifest files.

    `detected` is False when no known marker file was found — callers must
    render an explicit placeholder rather than guessing.
    """

    install: str | None = None
    build: str | None = None
    test: str | None = None
    lint: str | None = None
    detected: bool = False
    source: str = ""  # which marker triggered detection, for humans


def iter_candidate_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in _SKIP_DIRS or child.name.startswith("."):
            continue
        out.append(child)
    return out


def detect_repos(root: Path) -> list[DetectedRepo]:
    """Scan immediate subdirectories of root for git repositories."""
    repos: list[DetectedRepo] = []
    for child in iter_candidate_dirs(root):
        if (child / ".git").exists():
            repos.append(_describe_repo(child))
    return repos


def _describe_repo(path: Path) -> DetectedRepo:
    url = _git(path, ["remote", "get-url", "origin"])
    branch = _git(path, ["branch", "--show-current"]) or "main"
    return DetectedRepo(
        name=path.name,
        path=f"{path.name}/",
        url=url or None,
        branch=branch,
        local_only=url is None,
    )


def _git(path: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def detect_platform() -> str:
    mapping = {"darwin": "macos", "win32": "windows", "cygwin": "windows"}
    return mapping.get(sys.platform, "linux")


def detect_languages(root: Path) -> list[str]:
    """Aggregate language markers across root and its immediate subdirectories."""
    found: list[str] = []
    seen: set[str] = set()

    def scan(dir_path: Path) -> None:
        for marker, lang in _LANGUAGE_MARKERS.items():
            if (dir_path / marker).exists() and lang not in seen:
                seen.add(lang)
                found.append(lang)
        pkg = dir_path / "package.json"
        if pkg.exists():
            lang = _js_or_ts(dir_path)
            if lang not in seen:
                seen.add(lang)
                found.append(lang)
        for sln in dir_path.glob("*.sln"):
            if "csharp" not in seen:
                seen.add("csharp")
                found.append("csharp")
            break

    scan(root)
    for child in iter_candidate_dirs(root):
        scan(child)

    return found


def _js_or_ts(dir_path: Path) -> str:
    if (dir_path / "tsconfig.json").exists():
        return "typescript"
    try:
        raw = json.loads((dir_path / "package.json").read_text())
    except (OSError, json.JSONDecodeError):
        return "javascript"
    deps = {**raw.get("dependencies", {}), **raw.get("devDependencies", {})}
    if isinstance(deps, dict) and "typescript" in deps:
        return "typescript"
    return "javascript"


def _package_manager(dir_path: Path) -> str:
    if (dir_path / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (dir_path / "yarn.lock").exists():
        return "yarn"
    return "npm"


def detect_module_commands(module_path: Path) -> ModuleCommands:
    """Best-effort build/test/lint commands for a single module directory."""
    if (module_path / "package.json").exists():
        return _detect_node_commands(module_path)
    if (module_path / "pyproject.toml").exists():
        return _detect_python_commands(module_path)
    if (module_path / "Cargo.toml").exists():
        return ModuleCommands(
            install=None,
            build="cargo build",
            test="cargo test",
            lint="cargo clippy",
            detected=True,
            source="Cargo.toml",
        )
    if (module_path / "pubspec.yaml").exists():
        return ModuleCommands(
            install="flutter pub get",
            build="flutter build",
            test="flutter test",
            lint="flutter analyze",
            detected=True,
            source="pubspec.yaml",
        )
    if (module_path / "go.mod").exists():
        return ModuleCommands(
            install=None,
            build="go build ./...",
            test="go test ./...",
            lint="go vet ./...",
            detected=True,
            source="go.mod",
        )
    return ModuleCommands(detected=False)


def _detect_node_commands(module_path: Path) -> ModuleCommands:
    pm = _package_manager(module_path)
    try:
        raw = json.loads((module_path / "package.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ModuleCommands(detected=False)
    scripts = raw.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}

    def cmd(key: str) -> str | None:
        return f"{pm} run {key}" if key in scripts else None

    return ModuleCommands(
        install=f"{pm} install",
        build=cmd("build"),
        test=cmd("test"),
        lint=cmd("lint"),
        detected=True,
        source="package.json",
    )


def _detect_python_commands(module_path: Path) -> ModuleCommands:
    text = ""
    try:
        text = (module_path / "pyproject.toml").read_text()
    except OSError:
        pass

    has_pytest = "pytest" in text
    has_ruff = "ruff" in text

    return ModuleCommands(
        install="uv sync",
        build=None,
        test="uv run pytest" if has_pytest else None,
        lint="uv run ruff check ." if has_ruff else None,
        detected=True,
        source="pyproject.toml",
    )


def detect_root_branch(root: Path, repos: list[DetectedRepo]) -> str | None:
    """The branch this project's line is on, or None when detection cannot say.

    A project root that is itself a repository answers for itself. Otherwise
    the answer is the one branch every detected repository shares — the same
    values `modules.yaml` records, so the root branch `init` offers cannot
    disagree with the modules it registers in the same run. Repositories on
    different branches give no answer rather than a guess.

    `.git` is checked directly rather than asking git about `root`: `git -C`
    walks UP to the nearest repository, so a project directory that merely
    sits inside some other checkout would be handed that checkout's branch.
    """
    if (root / ".git").exists():
        return _git(root, ["branch", "--show-current"])
    branches = {r.branch for r in repos}
    if len(branches) == 1:
        return next(iter(branches))
    return None


@dataclass
class DetectionSummary:
    repos: list[DetectedRepo] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    platform: str = "linux"
    has_language_markers: bool = False
    root_branch: str | None = None


def run_detection(root: Path) -> DetectionSummary:
    repos = detect_repos(root)
    languages = detect_languages(root)
    return DetectionSummary(
        repos=repos,
        languages=languages,
        platform=detect_platform(),
        has_language_markers=bool(languages) or bool(repos),
        root_branch=detect_root_branch(root, repos),
    )


# The shapes a spec already comes in. Files first, then directories, so a
# proposal reads in the order someone would meet them. Deliberately a fixed
# list of conventional names rather than a content sniff: proposing the
# wrong file confidently is worse than missing one, because a wrong
# proposal gets accepted.
SPEC_FILES = ("SPEC.md", "DESIGN.md", "ARCHITECTURE.md")
SPEC_DIRS = ("docs", "adr", "rfcs", "design")


def detect_spec_paths(root: Path, module_paths: list[str] | None = None) -> list[str]:
    """Spec files and directories already in this project, repo-relative.

    Looks in the project root and at the TOP LEVEL of each registered
    module — not recursively. A monorepo genuinely keeps its spec in
    `backend/docs/`, so root-only misses a real case; recursing finds the
    docs directory of every vendored dependency and proposes it with the
    same confidence as a real one, and a wrong proposal that gets accepted
    is worse than a missed one.
    """
    found: list[str] = []

    def _scan(base: Path, prefix: str) -> None:
        for name in SPEC_FILES:
            if (base / name).is_file():
                found.append(f"{prefix}{name}")
        for name in SPEC_DIRS:
            candidate = base / name
            if candidate.is_dir() and any(candidate.iterdir()):
                found.append(f"{prefix}{name}/")

    _scan(root, "")
    for module_path in module_paths or []:
        base = (root / module_path).resolve()
        if base == root.resolve() or not base.is_dir():
            continue
        _scan(base, f"{module_path.rstrip('/')}/")
    return found


# A decision register worth citing — `| D-12 |` in a table, `## D-12`, or
# `D-12:` in prose. Three or more is the bar: one or two stray matches are
# as likely to be a version string as a convention.
_DECISION_RE = re.compile(r"(?:^|[|\s#])D-\d+\b")
_DECISION_MINIMUM = 3


def detect_decision_convention(root: Path, paths: list[str]) -> str:
    """A citation convention proposal, or "" if the project has none.

    Read once, at init, purely to decide whether to PROPOSE a line — the
    spec itself is never read again by rite. Only files are scanned; a
    directory of specs is not walked, because the point is to notice an
    existing convention cheaply, not to index anybody's documentation.
    """
    for rel in paths:
        if rel.endswith("/"):
            continue
        candidate = root / rel
        try:
            text = candidate.read_text(errors="replace")
        except OSError:
            continue
        if len(_DECISION_RE.findall(text)) >= _DECISION_MINIMUM:
            return (
                f"Decisions are cited as D-<number>; the register is in `{rel}`."
            )
    return ""
