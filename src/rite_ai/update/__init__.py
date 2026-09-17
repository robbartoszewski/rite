"""`rite update` — self-update rite itself, and migrate `.rite/` config files
across schema versions (SPEC §9.9, §8.8's distribution note).

Two independent halves, because they have independent failure modes and
independent "nothing to do" states:

- **Self-update** detects how rite was installed (`uv tool`, `pipx`, plain
  `pip`, or a dev checkout with no package manager to delegate to) and runs
  the right upgrade command. `rite update` is designed so adding a Homebrew
  tap later is one more branch here, not a rework (§8.8).

  The two halves run INDEPENDENTLY, which is the point of listing them
  separately: a self-update that fails still leaves a project whose config
  may need migrating, and skipping the migration because the download
  failed is the one combination that helps nobody. `rite update` reports
  both and exits non-zero if either failed.
- **Config migration** is versioned separately from rite's own release
  version — `.rite/.schema_version` stamps which schema a project's config
  files are on, written by `rite init` and advanced only as far as the
  migrations that actually ran. There is exactly one schema today (nothing
  has shipped a breaking config change yet), so `MIGRATIONS` is empty and
  every real project's config is already current.

  An unstamped project reads as the OLDEST schema, not the current one.
  The reverse was true and made the machinery unable to ever fire: no
  project carried a stamp, every one of them therefore reported as
  current, and the first migration to ship would have applied to nothing
  while stamping every project as though it had run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rite_ai.state import write_atomic

CURRENT_SCHEMA_VERSION = 1
OLDEST_SCHEMA_VERSION = 1
SCHEMA_VERSION_FILE = ".schema_version"

# The name on PyPI, which is deliberately NOT the project name, the command
# name, or the import package name (all three of which are `rite`): the PyPI
# name `rite` is taken by an unrelated, actively-maintained package, so a
# PEP 541 transfer is not available and this ships as `rite-ai`. Anything
# that talks to a package manager — upgrades, metadata lookups — must use
# this constant; anything a user reads or types is still `rite`.
DIST_NAME = "rite-ai"


@dataclass
class Migration:
    from_version: int
    to_version: int
    description: str
    apply: Callable[[Path], None]


# Empty on purpose — see module docstring. Append here, in order, the day a
# config format actually changes; never reorder or remove a landed entry,
# since a project's stamped version records which ones it already applied.
MIGRATIONS: list[Migration] = []


@dataclass
class UpdateResult:
    ok: bool
    message: str


# What each installer leaves in the root of the environment it built, as
# a fact about the install rather than a guess about its path. `uv tool`
# writes `uv-receipt.toml`; pipx writes `pipx_metadata.json`. Both sit
# next to `bin/`, i.e. at `sys.prefix`, for the interpreter running us.
_ENVIRONMENT_MARKERS: tuple[tuple[str, str], ...] = (
    ("uv-receipt.toml", "uv"),
    ("pipx_metadata.json", "pipx"),
)


def detect_install_method() -> str:
    """`"uv"` | `"pipx"` | `"pip"` | `"dev"` (an editable/source checkout —
    nothing to delegate an upgrade to; the user's own `git pull` is the
    upgrade).

    **`uv` was missing entirely, and it is the path `install.sh` PREFERS**
    — that script checks for `uv` first and only falls back to `pipx`.
    Measured on a plain `uv tool install .`: `sys.executable` is
    `~/.local/share/uv/tools/rite-ai/bin/python`, which contains no `pipx`
    component and carries no editable `direct_url.json`, so detection fell
    through to `"pip"`. `rite update` then asked "Update rite via pip?",
    answered yes, and ran that venv's own `python -m pip` — which a uv
    tool environment does not have:

        update command exited 1: .../bin/python3: No module named pip

    So the default install path could not self-update at all, and the one
    line it printed named an interpreter the user never chose and a module
    they cannot install. Had pip been present it would have been worse:
    installing into uv's environment behind uv's back leaves
    `uv-receipt.toml` describing something else than what is on disk.

    Detection is by the MARKER FILE each installer writes into the
    environment root (`_ENVIRONMENT_MARKERS`), not by a substring of the
    interpreter path. `pipx` was matched on `"pipx" in exe.parts`, which
    is only true while `PIPX_HOME` keeps its default: with `PIPX_HOME`
    pointed anywhere else — as it is in this project's own install tests —
    a real pipx install reported as `"pip"` too. The path check stays as a
    fallback for an environment whose marker has been removed.

    Editable-install detection reads `direct_url.json` (PEP 660 / pip's own
    convention: `{"url": "file://...", "dir_info": {"editable": true}}`),
    not a path heuristic on `sys.executable` — an earlier version of this
    function checked for a `src/rite_ai` tree next to the interpreter, which
    broke under `uv`: `sys.executable` resolves THROUGH a symlink to a
    shared toolchain interpreter under `~/.local/share/uv/python/...`, not
    the project's own `.venv/bin/python`, so the sibling-tree check always
    missed and every `uv run` dev invocation was misdetected as `pip`."""
    prefix = Path(sys.prefix)
    for marker, method in _ENVIRONMENT_MARKERS:
        if (prefix / marker).is_file():
            return method
    exe = Path(sys.executable).resolve()
    if "pipx" in exe.parts:
        return "pipx"
    try:
        from importlib.metadata import distribution

        raw = distribution(DIST_NAME).read_text("direct_url.json")
        if raw:
            info = json.loads(raw)
            if info.get("dir_info", {}).get("editable"):
                return "dev"
    except (LookupError, OSError, ValueError):
        pass
    return "pip"


def install_origin(raw: str | None = None) -> str:
    """Where this rite was installed from, for `rite doctor` — "" when the
    install records nothing.

    A version alone does not identify a build. `install.sh` installs from a
    git TAG, and a tag is a movable pointer: two machines reporting
    `rite 0.3.0` can be running different commits, which is exactly what a
    re-pointed tag did once already. pip, uv and pipx all record the source
    of a direct install in `direct_url.json` (PEP 610), including the commit
    a VCS install resolved to, so this is read rather than stamped at build
    time — nothing in the release process has to remember it.

    `raw` is the file's contents, for tests; by default it is read from the
    installed distribution. A wheel from an index records no `vcs_info`, so
    that install says nothing extra."""
    if raw is None:
        try:
            from importlib.metadata import distribution

            raw = distribution(DIST_NAME).read_text("direct_url.json")
        except (LookupError, OSError):
            return ""
    if not raw:
        return ""
    try:
        info = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(info, dict):
        return ""
    vcs = info.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("commit_id"):
        commit = str(vcs["commit_id"])[:12]
        revision = vcs.get("requested_revision")
        asked = f"{revision}, " if revision else ""
        return f"installed from {asked}commit {commit}"
    if info.get("dir_info", {}).get("editable") and info.get("url"):
        return f"dev checkout at {str(info['url']).removeprefix('file://')}"
    return ""


def update_command_for(method: str) -> list[str] | None:
    """Every command here names the DISTRIBUTION, never the command or the
    import package. Getting this wrong is not a cosmetic bug: `pipx upgrade
    rite` / `pip install --upgrade rite` would reach for the unrelated
    project that owns the PyPI name `rite` (see DIST_NAME) and, in the pip
    case, cheerfully install it over the top of this one."""
    if method == "uv":
        return ["uv", "tool", "upgrade", DIST_NAME]
    if method == "pipx":
        return ["pipx", "upgrade", DIST_NAME]
    if method == "pip":
        return [sys.executable, "-m", "pip", "install", "--upgrade", DIST_NAME]
    return None  # "dev" — nothing to run


def run_self_update(method: str | None = None) -> UpdateResult:
    method = method or detect_install_method()
    if method == "dev":
        return UpdateResult(
            True,
            "dev checkout detected (editable install) — `git pull` to update, "
            "not `rite update`",
        )
    command = update_command_for(method)
    if command is None:
        return UpdateResult(
            False, f"no update command known for install method {method!r}"
        )
    binary = command[0]
    if binary != sys.executable and shutil.which(binary) is None:
        return UpdateResult(False, f"`{binary}` not found on PATH — cannot self-update")
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return UpdateResult(False, f"update failed: {e}")
    if proc.returncode != 0:
        return UpdateResult(
            False,
            f"update command exited {proc.returncode}: {proc.stderr.strip()[:300]}",
        )
    return UpdateResult(True, _outcome(command, proc.stdout, proc.stderr))


def _outcome(command: list[str], stdout: str, stderr: str) -> str:
    """What the package manager itself reported, not what rite assumed.

    This used to be `f"updated via {' '.join(command)}"` on any zero exit —
    and a zero exit is what every one of these tools returns when there was
    nothing to do. Measured against the real install: `uv tool upgrade
    rite-ai` on a tool already at the latest prints `Nothing to upgrade`
    and exits 0, and `rite update` answered "updated via uv tool upgrade
    rite-ai". Someone running it weekly is told they upgraded every week,
    and cannot tell a real upgrade from a no-op.

    Rite cannot tell them apart either without parsing three different
    tools' prose, so it stops claiming an outcome and quotes the one
    program that knows. The last non-empty line is the summary in all
    three: uv's `Nothing to upgrade` / `Installed 2 executables: …`,
    pipx's `upgraded package rite-ai from X to Y` / `is already at latest
    version`, pip's `Successfully installed …` / `Requirement already
    satisfied`.
    """
    lines = [
        line.strip()
        for line in ((stdout or "") + "\n" + (stderr or "")).splitlines()
        if line.strip()
    ]
    said = lines[-1] if lines else "no output"
    return f"ran `{' '.join(command)}` — {said[:200]}"


def _schema_version_path(rite_dir: Path) -> Path:
    return rite_dir / SCHEMA_VERSION_FILE


def read_schema_version(rite_dir: Path) -> int:
    """The schema a project's config files are on.

    An unstamped project is the OLDEST schema, not the current one. It
    predates the stamp being written, which places it before every
    migration rather than after them — reading it as current is how the
    first migration to ship would apply to nothing, since no project in
    existence carries a stamp today.
    """
    path = _schema_version_path(rite_dir)
    if not path.is_file():
        return OLDEST_SCHEMA_VERSION
    try:
        return int(path.read_text().strip())
    except ValueError:
        # Unreadable is not "current" either — same reasoning.
        return OLDEST_SCHEMA_VERSION


def migrate_config(rite_dir: Path) -> list[str]:
    """Apply any registered migrations between the project's stamped
    version and `CURRENT_SCHEMA_VERSION`, in order, then re-stamp. Returns
    one message per migration applied — an empty list means already
    current (the common case for every project today, since `MIGRATIONS`
    is empty)."""
    current = read_schema_version(rite_dir)
    applied: list[str] = []
    for migration in MIGRATIONS:
        if migration.from_version < current:
            continue
        if migration.from_version > current:
            break  # migrations are in order; nothing further applies yet
        migration.apply(rite_dir)
        applied.append(migration.description)
        current = migration.to_version
    # Stamp what was actually reached, never `CURRENT_SCHEMA_VERSION`.
    # Stamping the target regardless of what ran would brand a project as
    # migrated that had not been, and the stamp is the only record — the
    # next `rite update` would skip it forever.
    # Atomic: a torn stamp reads as OLDEST_SCHEMA_VERSION, which re-runs
    # every migration a project already applied.
    write_atomic(_schema_version_path(rite_dir), f"{current}\n")
    return applied


def stamp_schema_version(rite_dir: Path, version: int | None = None) -> None:
    """Record which schema a project's config was written against. Called
    by `rite init`, so a project created today is not indistinguishable
    from one created before stamping existed."""
    rite_dir.mkdir(parents=True, exist_ok=True)
    resolved = CURRENT_SCHEMA_VERSION if version is None else version
    write_atomic(_schema_version_path(rite_dir), f"{resolved}\n")
