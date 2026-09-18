"""Detection: existing repos, language markers, platform, per-module commands.

Everything here is best-effort and additive-only — it pre-fills questionnaire
defaults and derives generated commands. Nothing here invents an answer; when
detection finds nothing, callers must fall back to an explicit placeholder.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from rite_ai.config.models import Module, RecordedCommands, SandboxConfig

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
    "Package.swift": "swift",
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
    format: str | None = None
    detected: bool = False
    source: str = ""  # which marker triggered detection, for humans
    # Why a command is missing, or was chosen as it was, when `source` does not
    # say: two lockfiles that disagree, no simulator to test on. Shown to people.
    note: str = ""
    # The keys whose command came from `modules.yaml` rather than detection.
    configured: frozenset[str] = frozenset()


# The commands a session needs to verify its own work, in the order a session
# runs them. Taken from `RecordedCommands` so the keys a person can record in
# modules.yaml and the keys detection fills are the same set by construction.
COMMAND_KEYS: tuple[str, ...] = tuple(f.name for f in fields(RecordedCommands))


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
        # `Package.swift` is in `_LANGUAGE_MARKERS`. An app project has no
        # Package.swift at all — only an `.xcodeproj` or `.xcworkspace` bundle
        # and `.swift` sources — and was detected as nothing.
        if "swift" not in seen and _has_swift(dir_path):
            seen.add("swift")
            found.append("swift")

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


def detect_module_commands(
    module_path: Path, nested_sandbox: bool = False, boundary: Path | None = None
) -> ModuleCommands:
    """Best-effort commands for a single module directory.

    `nested_sandbox` is True when the commands will run where starting a
    second sandbox is refused — see `nests_sandboxes`; only Swift reads it.
    `boundary` is the project root: a Node lockfile is looked for in the module
    and then upward, never past the repository root or this directory. With no
    boundary only the module itself is searched.
    """
    if (module_path / "package.json").exists():
        return _detect_node_commands(module_path, boundary)
    if (module_path / "pyproject.toml").exists():
        return _detect_python_commands(module_path)
    if (module_path / "Cargo.toml").exists():
        return ModuleCommands(
            install=None,
            build="cargo build",
            test="cargo test",
            lint="cargo clippy",
            format="cargo fmt",
            detected=True,
            source="Cargo.toml",
        )
    if (module_path / "pubspec.yaml").exists():
        return ModuleCommands(
            install="flutter pub get",
            build="flutter build",
            test="flutter test",
            lint="flutter analyze",
            format="dart format .",
            detected=True,
            source="pubspec.yaml",
        )
    if (module_path / "go.mod").exists():
        return ModuleCommands(
            install=None,
            build="go build ./...",
            test="go test ./...",
            lint="go vet ./...",
            format="gofmt -w .",
            detected=True,
            source="go.mod",
        )
    if _xcode_containers(module_path) or (module_path / "Package.swift").is_file():
        return _swift_commands(module_path, nested_sandbox)
    return ModuleCommands(detected=False)


# --- Node ---------------------------------------------------------------------
#
# The package manager decides whether ANY Node command works. `npm run test` in
# a pnpm workspace fails on missing dependencies, which reads as a broken
# project rather than the wrong tool. This used to be "pnpm-lock.yaml, else
# yarn.lock, else npm": bun projects were run with npm, `packageManager` was
# never read, a workspace member (whose lockfile sits at the repository root)
# was treated as npm, and a stale second lockfile was resolved by precedence.
#
# The signals, strongest first:
#
#   1. `packageManager` in package.json ("pnpm@9.12.0") — the project's own
#      statement, and what Corepack enforces.
#   2. The lockfile, in the module or the nearest directory above it, up to the
#      repository root.
#   3. Nothing at all: npm, which ships with Node — and the note says so.
#
# Lockfiles from two different managers in the same place are NOT resolved.
# One of them is stale, picking either is a guess that fails the same confusing
# way, and the fix is a line in modules.yaml; the note names both files.
_LOCKFILES: tuple[tuple[str, str], ...] = (
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
)
_NODE_MANAGERS = frozenset(manager for _, manager in _LOCKFILES)


@dataclass
class _NodeTool:
    name: str = ""  # "" when it cannot be decided
    evidence: str = ""
    yarn_berry: bool = False
    locked: bool = False  # this manager's own lockfile exists
    note: str = ""


def _lockfile_dir(module_path: Path, boundary: Path | None) -> Path | None:
    """The module, or the nearest directory above it holding a lockfile.

    Stops at the repository root and at `boundary`, so a lockfile belonging to
    an unrelated project further up is never read. Without a boundary only the
    module itself is looked at.
    """
    current = module_path
    while True:
        if any((current / lock).exists() for lock, _ in _LOCKFILES):
            return current
        if boundary is None or (current / ".git").exists():
            return None
        if current == boundary or current.parent == current:
            return None
        current = current.parent


def _node_tool(module_path: Path, manifest: dict, boundary: Path | None) -> _NodeTool:
    where = _lockfile_dir(module_path, boundary)
    present = [
        (lock, pm) for lock, pm in _LOCKFILES if where and (where / lock).exists()
    ]
    shown = (
        (lambda lock: lock)
        if where == module_path
        else (lambda lock: f"{lock} in {where.name}/")
    )

    declared = manifest.get("packageManager")
    if isinstance(declared, str):
        name, _, version = declared.partition("@")
        name = name.strip().lower()
        if name in _NODE_MANAGERS:
            major = re.match(r"\d+", version)
            return _NodeTool(
                name=name,
                evidence=f"packageManager {declared}",
                yarn_berry=name == "yarn" and bool(major) and int(major.group()) >= 2,
                locked=any(pm == name for _, pm in present),
            )

    managers = sorted({pm for _, pm in present})
    if not managers:
        return _NodeTool(
            name="npm",
            evidence="no lockfile",
            note="no lockfile and no `packageManager` in package.json — assumed npm",
        )
    if len(managers) > 1:
        files = ", ".join(shown(lock) for lock, _ in present)
        return _NodeTool(
            note=(
                f"lockfiles from different package managers ({files}): one is "
                "stale, so no package manager was chosen. Delete the stale one, or "
                "record the commands under `commands:` in .rite/modules.yaml"
            )
        )
    lock, name = present[0]
    return _NodeTool(
        name=name,
        evidence=shown(lock),
        yarn_berry=name == "yarn" and (where / ".yarnrc.yml").exists(),
        locked=True,
    )


def _node_install(tool: _NodeTool) -> str:
    """An install that will not rewrite the lockfile.

    A plain `npm install` rewrites package-lock.json. That leaves the Worker's
    checkout dirty, and `rite prepare` blocks on a dirty tree. `npm ci` and
    yarn 1's `--frozen-lockfile` were checked against the tools' own help (npm
    10.8.2, yarn 1.22.22). pnpm's and bun's `--frozen-lockfile` and Yarn 2+'s
    `--immutable` are their documented equivalents, not run where this was
    written.
    """
    if tool.name == "npm":
        return "npm ci" if tool.locked else "npm install"
    if tool.name == "yarn":
        flag = "--immutable" if tool.yarn_berry else "--frozen-lockfile"
        return f"yarn install {flag}"
    return f"{tool.name} install --frozen-lockfile"


def _detect_node_commands(module_path: Path, boundary: Path | None) -> ModuleCommands:
    try:
        raw = json.loads((module_path / "package.json").read_text())
    except (OSError, ValueError):
        return ModuleCommands(detected=False, note="package.json could not be read")
    if not isinstance(raw, dict):
        return ModuleCommands(detected=False, note="package.json is not a JSON object")
    tool = _node_tool(module_path, raw, boundary)
    if not tool.name:
        return ModuleCommands(detected=False, source="package.json", note=tool.note)
    scripts = raw.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}

    def script(*names: str) -> str | None:
        return next((f"{tool.name} run {n}" for n in names if n in scripts), None)

    return ModuleCommands(
        install=_node_install(tool),
        build=script("build"),
        test=script("test"),
        lint=script("lint"),
        format=script("format", "fmt"),
        detected=True,
        source=f"package.json, {tool.evidence}",
        note=tool.note,
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
        format="uv run ruff format ." if has_ruff else None,
        detected=True,
        source="pyproject.toml",
    )


# --- Swift --------------------------------------------------------------------


def _xcode_containers(dir_path: Path) -> list[Path]:
    """`.xcworkspace` bundles, then `.xcodeproj` bundles, directly in `dir_path`.

    A workspace comes first because a project with more than one project in it
    builds from the workspace. The `project.xcworkspace` inside every
    `.xcodeproj` is not matched: it sits a level down.
    """
    return sorted(dir_path.glob("*.xcworkspace")) + sorted(dir_path.glob("*.xcodeproj"))


def _has_swift(dir_path: Path) -> bool:
    return bool(_xcode_containers(dir_path)) or any(dir_path.glob("*.swift"))


def nests_sandboxes(sandbox: SandboxConfig) -> bool:
    """Whether Worker commands run where starting a second sandbox is refused.

    Only the seatbelt backend runs the Worker under macOS's `sandbox-exec`, and
    macOS will not let a sandboxed process apply another sandbox: inside a
    seatbelt Worker, `sandbox-exec` fails with `sandbox_apply: Operation not
    permitted` (measured, exit 71). The other backends isolate with a container
    or a VM instead.
    """
    return sandbox.enabled and sandbox.backend == "seatbelt"


# Why each flag below is there. Every one of them LOOKS removable, and every
# one was measured to be needed (Xcode 26.6, Swift 6.3.3, yoloAI 0.11.0
# seatbelt), with The Composable Architecture as the package under test.
#
# SwiftPM and Xcode run parts of a build under their OWN `sandbox-exec`:
# evaluating `Package.swift`, running package plugins, and starting the plugins
# that implement macros. macOS refuses to start a sandbox from inside one, so
# in a seatbelt Worker each of those steps fails, and the error names the step
# rather than the cause:
#
#   * "Invalid manifest" from `swift build`, and "Could not resolve package
#     dependencies" from xcodebuild, for a manifest that is fine;
#   * "external macro implementation type 'SwiftMacros.TaskLocalMacro' could
#     not be found" — the STANDARD LIBRARY's macro — for every file using any
#     macro, because no macro plugin could start.
#
# Turning those inner sandboxes off DOES NOT UNSANDBOX THE WORKER. It is still
# inside yoloAI's seatbelt profile, which is what D-51's default is about; the
# flags remove a second sandbox that the first one makes impossible, nothing
# more. Outside a sandbox they are left off, so an unsandboxed build keeps
# SwiftPM's and Xcode's own protection.
#
# MEASURED END TO END 2026-09-18, seatbelt backend, Xcode 26.6, iOS 26.4
# simulator, on a SwiftPM package declaring only `.iOS(.v17)`. The friction
# audit carried "no iOS build or test was run inside a sandbox" as its last
# unverified item; this is that run:
#
#   * raw `xcodebuild ... build` inside the sandbox FAILS before compiling
#     anything — "Could not resolve package dependencies: sandbox-exec:
#     sandbox_apply: Operation not permitted" — and the message names
#     sandbox-exec, never the project;
#   * `IDEPackageSupportDisableManifestSandbox=YES` as an ENVIRONMENT
#     variable does not help. It has to be the `-IDEPackage…=YES` argument
#     below, which is how these are passed;
#   * with the flags below, `xcodebuild build` reaches BUILD SUCCEEDED and
#     `xcodebuild test` reaches TEST SUCCEEDED on a booted simulator, from
#     inside the sandbox.
#
# Not covered by that run: the tart backend (not installed here), an
# `.xcodeproj` app with a UI test target, and macro-using packages, whose
# separate failure is recorded above from a host run.
#
# That profile also gives every Worker write access to the SAME host SwiftPM
# and Xcode caches — the DerivedData path in the sandboxed build's own output
# is the host's. Concurrent Workers resolving into them is untested — SPEC
# §5.3.5. Xcode's own cache DB under ~/Library/Caches is denied inside the
# sandbox ("authorization denied"), which is noisy and not fatal.
_SWIFTPM_NESTED = "--disable-sandbox"  # manifests and plugins, for `swift`
_XCODEBUILD_NESTED = (
    # evaluating Package.swift ("Could not resolve package dependencies")
    "-IDEPackageSupportDisableManifestSandbox=YES",
    # package plugins; passed with the other two, not measured on its own
    "-IDEPackageSupportDisablePluginExecutionSandbox=YES",
    # macro plugins, which the COMPILER starts — neither default above reaches
    # them ("external macro implementation type ... could not be found")
    "OTHER_SWIFT_FLAGS=$(inherited) -disable-sandbox",
)
# Passed with or without a sandbox. xcodebuild refuses a package's macros until
# someone trusts them in Xcode's UI, which no Worker can do. Measured on the
# host with no sandbox anywhere: TCA's `xcodebuild test` exits 65, "Macro
# 'ComposableArchitectureMacros' ... must be enabled before it can be used".
_XCODEBUILD_ALWAYS = ("-skipMacroValidation",)

_SWIFT_PACKAGE_NAME = re.compile(r'Package\s*\(\s*name:\s*"([^"]+)"')


def _swift_commands(module_path: Path, nested_sandbox: bool) -> ModuleCommands:
    """SwiftPM for a package that builds on macOS; xcodebuild on an iOS
    simulator for an Xcode project, or a package that only declares iOS."""
    manifest = ""
    try:
        manifest = (module_path / "Package.swift").read_text()
    except (OSError, UnicodeDecodeError):
        pass
    containers = _xcode_containers(module_path)
    ios_only = ".iOS(" in manifest and ".macOS(" not in manifest

    if not containers and not ios_only:
        flag = f" {_SWIFTPM_NESTED}" if nested_sandbox else ""
        return ModuleCommands(
            install=f"swift package{flag} resolve",
            build=f"swift build{flag}",
            test=f"swift test{flag}",
            detected=True,
            source="Package.swift",
        )

    if containers:
        container = containers[0]
        option = "-workspace" if container.suffix == ".xcworkspace" else "-project"
        selector = [option, container.name, "-scheme", _scheme(container)]
        source = container.name
    else:
        name = _SWIFT_PACKAGE_NAME.search(manifest)
        selector = ["-scheme", name.group(1) if name else module_path.name]
        source = "Package.swift"

    flags = [*_XCODEBUILD_ALWAYS, *(_XCODEBUILD_NESTED if nested_sandbox else ())]

    def xcodebuild(action: str, destination: str) -> str:
        words = ["xcodebuild", action, *selector, "-destination", destination, *flags]
        return " ".join(shlex.quote(word) for word in words)

    simulator = _ios_simulator_name()
    return ModuleCommands(
        # Resolution happens inside build and test; a separate resolve command
        # for xcodebuild was not measured, so none is offered.
        install=None,
        # `generic/` needs no particular device to exist. Measured to build.
        build=xcodebuild("build", "generic/platform=iOS Simulator"),
        # A test run needs a concrete device. Measured with a name destination.
        test=(
            xcodebuild("test", f"platform=iOS Simulator,name={simulator}")
            if simulator
            else None
        ),
        detected=True,
        source=source,
        note=(
            ""
            if simulator
            else "no available iPhone simulator on this machine, so no test "
            "command — install an iOS runtime in Xcode, or record `test:`"
        ),
    )


def _scheme(container: Path) -> str:
    """The one shared scheme, else the container's own name.

    Shared schemes are the committed ones (`xcshareddata/xcschemes`); a user's
    own live under `xcuserdata` and are not in a clone. With none shared, Xcode
    makes a scheme per target, and an app's main target conventionally carries
    the project's name. With several shared, picking one would be a guess, so
    the name is used — and a wrong name fails loudly, because xcodebuild lists
    the schemes that do exist.
    """
    schemes = sorted(container.glob("xcshareddata/xcschemes/*.xcscheme"))
    return schemes[0].stem if len(schemes) == 1 else container.stem


def _ios_simulator_name() -> str | None:
    """An available iPhone simulator on the newest installed iOS runtime.

    Looked up rather than written in: simulator names change with Xcode
    releases, and a destination naming a device the machine lacks fails
    before anything builds. None when there is nothing to name.
    """
    try:
        proc = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available", "-j"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        devices = json.loads(proc.stdout).get("devices", {})
    except (ValueError, AttributeError):
        return None
    if not isinstance(devices, dict):
        return None
    best: tuple[tuple[int, ...], str] | None = None
    for runtime, entries in devices.items():
        match = re.search(r"\.iOS-(\d+(?:-\d+)*)$", str(runtime))
        if not match or not isinstance(entries, list):
            continue
        version = tuple(int(part) for part in match.group(1).split("-"))
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("isAvailable", True):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.startswith("iPhone"):
                if best is None or version > best[0]:
                    best = (version, name)
                break
    return best[1] if best else None


# --- what a session runs ------------------------------------------------------


def module_commands(
    module: Module, project_root: Path, sandbox: SandboxConfig
) -> ModuleCommands:
    """What a session should run for `module`.

    A command recorded in `modules.yaml` overrides detection for its own key;
    detection fills only the keys nobody recorded. Configuration is the
    guarantee and detection the convenience, so a wrong detection is always a
    one-line fix and never a wall.
    """
    detected = detect_module_commands(
        project_root / module.path, nests_sandboxes(sandbox), boundary=project_root
    )
    recorded = {key: value for key, value in asdict(module.commands).items() if value}
    if not recorded:
        return detected
    merged = {key: recorded.get(key, getattr(detected, key)) for key in COMMAND_KEYS}
    sources = ["modules.yaml", *([detected.source] if detected.source else [])]
    return ModuleCommands(
        **merged,
        # `detected` means what its docstring says: a known marker file was
        # found. It used to be hardcoded True here, so a module with NO
        # manifest whose commands were recorded by hand came back claiming
        # detection — and the generated `CLAUDE.md` then told Workers the
        # commands had been "read out of each module's own manifest". Seen on
        # a tester's project: two module repos holding only a README, a full
        # command set in modules.yaml, and a root `CLAUDE.md` vouching for a
        # manifest that was never there.
        detected=detected.detected,
        source=" + ".join(sources),
        # A note about detection is noise once every key is recorded.
        note=detected.note if set(COMMAND_KEYS) - set(recorded) else "",
        configured=frozenset(recorded),
    )


def command_rows(cmds: ModuleCommands) -> list[tuple[str, str, str]]:
    """`(key, "configured" | "detected" | "missing", command)` for every key."""
    rows = []
    for key in COMMAND_KEYS:
        value = getattr(cmds, key) or ""
        if key in cmds.configured:
            rows.append((key, "configured", value))
        elif value:
            rows.append((key, "detected", value))
        else:
            rows.append((key, "missing", ""))
    return rows


# What the project's own manifests already say. `kind`, `features` and
# `frameworks` were asked cold — Full-stack, blank and blank — while the answers
# sat in files init already opens: `_detect_python_commands` reads
# pyproject.toml to look for pytest and discards everything else. Like the rest
# of this module these only PRE-FILL; nothing is recorded that was not offered.
#
# Manifests, not a crawl of the tree: the project root and its immediate
# subdirectories, the same reach as `detect_languages`.

# Frameworks worth naming, by the package that signals them. An allowlist rather
# than every dependency: `httpx` or `pyyaml` is a library a project uses, not a
# framework it is built on, and a default padded with libraries is a default
# people clear instead of accepting.
_PYTHON_FRAMEWORKS = {
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "starlette": "starlette",
    "tornado": "tornado",
    "aiohttp": "aiohttp",
    "celery": "celery",
    "click": "click",
    "typer": "typer",
    "pytest": "pytest",
}
_NODE_FRAMEWORKS = {
    "react": "react",
    "next": "next",
    "vue": "vue",
    "nuxt": "nuxt",
    "svelte": "svelte",
    "@sveltejs/kit": "sveltekit",
    "@angular/core": "angular",
    "react-native": "react-native",
    "electron": "electron",
    "express": "express",
    "fastify": "fastify",
    "@nestjs/core": "nestjs",
    "koa": "koa",
    "hono": "hono",
    "jest": "jest",
    "vitest": "vitest",
}
_FRONTEND_FRAMEWORKS = {
    "react",
    "next",
    "vue",
    "nuxt",
    "svelte",
    "sveltekit",
    "angular",
}
_BACKEND_FRAMEWORKS = {
    "django",
    "flask",
    "fastapi",
    "starlette",
    "tornado",
    "aiohttp",
    "express",
    "fastify",
    "nestjs",
    "koa",
    "hono",
    "vapor",
    "hummingbird",
}

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass
class ManifestFacts:
    description: str = ""
    frameworks: list[str] = field(default_factory=list)
    installs_a_command: bool = False
    mobile: bool = False
    # A manifest that says outright it produces a library. Python and Node
    # manifests do not, so only Swift sets it: `.library(` in Package.swift.
    library: bool = False


def _requirement_names(requirements: object) -> list[str]:
    if not isinstance(requirements, list):
        return []
    names = []
    for requirement in requirements:
        if not isinstance(requirement, str):
            continue
        match = _REQUIREMENT_NAME.match(requirement)
        if match:
            names.append(match.group(1).lower().replace("_", "-").replace(".", "-"))
    return names


def _table_keys(table: object) -> list[str]:
    if not isinstance(table, dict):
        return []
    return [key.lower() for key in table if isinstance(key, str)]


def _known(names: list[str], table: dict[str, str]) -> list[str]:
    found: list[str] = []
    for name in names:
        label = table.get(name)
        if label and label not in found:
            found.append(label)
    return found


def _read_pyproject(path: Path) -> ManifestFacts | None:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):  # TOMLDecodeError and UnicodeDecodeError
        return None
    project = data.get("project")
    project = project if isinstance(project, dict) else {}
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    poetry = poetry if isinstance(poetry, dict) else {}

    names = _requirement_names(project.get("dependencies"))
    for groups in (project.get("optional-dependencies"), data.get("dependency-groups")):
        if isinstance(groups, dict):
            for requirements in groups.values():
                names.extend(_requirement_names(requirements))
    names.extend(_table_keys(poetry.get("dependencies")))
    names.extend(_table_keys(poetry.get("dev-dependencies")))
    poetry_groups = poetry.get("group")
    if isinstance(poetry_groups, dict):
        for group in poetry_groups.values():
            if isinstance(group, dict):
                names.extend(_table_keys(group.get("dependencies")))

    description = project.get("description") or poetry.get("description")
    scripts = (
        project.get("scripts") or project.get("gui-scripts") or poetry.get("scripts")
    )
    return ManifestFacts(
        description=description.strip() if isinstance(description, str) else "",
        frameworks=_known(names, _PYTHON_FRAMEWORKS),
        installs_a_command=bool(scripts),
    )


def _read_package_json(path: Path) -> ManifestFacts | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    names = _table_keys(data.get("dependencies")) + _table_keys(
        data.get("devDependencies")
    )
    frameworks = _known(names, _NODE_FRAMEWORKS)
    description = data.get("description")
    return ManifestFacts(
        description=description.strip() if isinstance(description, str) else "",
        frameworks=frameworks,
        installs_a_command=bool(data.get("bin")),
        mobile="react-native" in frameworks,
    )


# Swift manifests are Swift source, not data. They are read with patterns rather
# than `swift package dump-package`, which COMPILES AND RUNS the manifest: slow,
# a side effect detection must not have, and inside a seatbelt Worker a certain
# failure, since SwiftPM sandboxes manifest evaluation (see `_swift_commands`).
# A pattern can miss an unusual manifest; it cannot execute one.
_SWIFT_FRAMEWORKS = {
    "swift-composable-architecture": "composable-architecture",
    "vapor": "vapor",
    "hummingbird": "hummingbird",
    "swift-argument-parser": "swift-argument-parser",
}
_SWIFT_PACKAGE_URL = re.compile(r'\.package\s*\(\s*url:\s*"([^"]+)"')
_PBXPROJ_PACKAGE_URL = re.compile(r'repositoryURL\s*=\s*"([^"]+)"')


def _swift_frameworks(urls: list[str]) -> list[str]:
    names = [
        url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1].lower() for url in urls
    ]
    return _known(names, _SWIFT_FRAMEWORKS)


def _read_package_swift(path: Path) -> ManifestFacts | None:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    return ManifestFacts(
        frameworks=_swift_frameworks(_SWIFT_PACKAGE_URL.findall(text)),
        installs_a_command=".executable(" in text,
        mobile=".iOSApplication(" in text,
        library=".library(" in text,
    )


def _read_xcodeproj(project: Path) -> ManifestFacts | None:
    """What an Xcode project's `project.pbxproj` says: an iOS SDK makes it
    mobile, and its Swift package references name its frameworks. A workspace
    has no pbxproj of its own; the projects inside it do."""
    try:
        text = (project / "project.pbxproj").read_text()
    except (OSError, UnicodeDecodeError):
        return None
    return ManifestFacts(
        frameworks=_swift_frameworks(_PBXPROJ_PACKAGE_URL.findall(text)),
        mobile=re.search(r"\bSDKROOT\s*=\s*iphoneos\b", text) is not None,
    )


def _manifest_facts(dir_path: Path) -> list[ManifestFacts]:
    facts: list[ManifestFacts] = []
    for name, reader in (
        ("pyproject.toml", _read_pyproject),
        ("package.json", _read_package_json),
    ):
        if (dir_path / name).is_file():
            read = reader(dir_path / name)
            if read is not None:
                facts.append(read)
    if (dir_path / "pubspec.yaml").is_file():
        facts.append(ManifestFacts(mobile=True))
    if (dir_path / "Package.swift").is_file():
        read = _read_package_swift(dir_path / "Package.swift")
        if read is not None:
            facts.append(read)
    for project in sorted(dir_path.glob("*.xcodeproj")):
        read = _read_xcodeproj(project)
        if read is not None:
            facts.append(read)
    return facts


def _all_manifest_facts(root: Path) -> list[ManifestFacts]:
    facts = _manifest_facts(root)
    for child in iter_candidate_dirs(root):
        facts.extend(_manifest_facts(child))
    return facts


def detect_frameworks(root: Path) -> list[str]:
    """Recognised frameworks across the project's manifests, in manifest order."""
    found: list[str] = []
    for facts in _all_manifest_facts(root):
        for framework in facts.frameworks:
            if framework not in found:
                found.append(framework)
    return found


def detect_description(root: Path) -> str | None:
    """The root manifest's description; else the one every manifest agrees on.

    Two modules describing themselves differently do not describe the
    project, so that gives no answer rather than picking one.
    """
    for facts in _manifest_facts(root):
        if facts.description:
            return facts.description
    descriptions = {
        facts.description
        for child in iter_candidate_dirs(root)
        for facts in _manifest_facts(child)
        if facts.description
    }
    return next(iter(descriptions)) if len(descriptions) == 1 else None


def detect_kind(root: Path) -> str | None:
    """A `what.kind` the manifests support, or None when they do not say.

    Mobile if a mobile toolkit is declared. Otherwise a frontend framework and
    a web framework together are full-stack, either alone is that alone, and a
    package that installs a command with neither is a library. Anything else is
    no answer — the questionnaire keeps its old default rather than guessing.
    """
    facts = _all_manifest_facts(root)
    if not facts:
        return None
    frameworks = {framework for f in facts for framework in f.frameworks}
    if any(f.mobile for f in facts):
        return "mobile"
    frontend = bool(frameworks & _FRONTEND_FRAMEWORKS)
    backend = bool(frameworks & _BACKEND_FRAMEWORKS)
    if frontend and backend:
        return "full-stack"
    if frontend:
        return "frontend"
    if backend:
        return "backend"
    if any(f.installs_a_command or f.library for f in facts):
        return "library"
    return None


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
    kind: str | None = None
    description: str | None = None
    frameworks: list[str] = field(default_factory=list)


def run_detection(root: Path) -> DetectionSummary:
    repos = detect_repos(root)
    languages = detect_languages(root)
    return DetectionSummary(
        repos=repos,
        languages=languages,
        platform=detect_platform(),
        has_language_markers=bool(languages) or bool(repos),
        root_branch=detect_root_branch(root, repos),
        kind=detect_kind(root),
        description=detect_description(root),
        frameworks=detect_frameworks(root),
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

# The header row of a decision register, `| D | Decision | ...`, which
# `/spec`'s skeleton writes. Recognised alongside the count because a new
# project's register can hold a single row: three references is a bar only a
# mature spec clears, so the register would exist and never be cited.
_REGISTER_HEADER_RE = re.compile(r"(?m)^\|\s*D\s*\|")


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
        hits = len(_DECISION_RE.findall(text))
        if hits >= _DECISION_MINIMUM or (hits and _REGISTER_HEADER_RE.search(text)):
            return f"Decisions are cited as D-<number>; the register is in `{rel}`."
    return ""
