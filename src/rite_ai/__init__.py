"""SPEC.md §8.5: `VERSION` at the repo root is the single source of truth.
`pyproject.toml` reads it at build time (`[tool.hatch.version]`); this reads
the copy shipped alongside the package (`force-include`, same file content)
at runtime, so an edited VERSION is reflected immediately in a dev checkout
without a reinstall. Falls back to installed package metadata only if the
file is somehow missing — e.g. a build that didn't carry the force-include
— so `import rite` never hard-fails over this.
"""

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path


def _read_version() -> str:
    try:
        return files("rite_ai").joinpath("VERSION").read_text().strip()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        pass
    # Editable/dev checkout: force-include only lands VERSION next to the
    # package in a BUILT wheel, not in an editable install, which points
    # `rite/` straight at `src/rite_ai/` — the real file is three levels up.
    repo_version = Path(__file__).resolve().parent.parent.parent / "VERSION"
    if repo_version.is_file():
        return repo_version.read_text().strip()
    try:
        # The DISTRIBUTION name, not the import name: this ships to PyPI as
        # `rite-ai` because an unrelated project owns `rite` there. Asking
        # for `version("rite")` in an environment that has both installed
        # would report THAT project's version as this tool's.
        return version("rite-ai")
    except PackageNotFoundError:
        return "0.0.0-unknown"


__version__ = _read_version()


def own_command() -> str:
    """The absolute path of the `rite` that is running, for instructions.

    ⚠ **An instruction that says `rite` names whatever is first on the
    reader's PATH, which need not be the rite that wrote the instruction.**
    Measured 2026-09-25 on this machine: a Manager resolved
    `~/.local/bin/rite` -> **0.4.0**, while the code composing its
    instructions was 0.5.1. It was told to run `rite reply`, which 0.4.0 does
    not have, and got `Usage: rite [OPTIONS] COMMAND [ARGS]...`, exit 2 —
    naming neither the version nor the path, so the Manager reads it as bad
    syntax and retries.

    Naming the binary REMOVES that class rather than detecting it. A
    pre-flight version check was considered and deliberately not built: rite
    has no users today and the dogfood starts on v0.6.0, so prompt text and
    binary come from the same release and the skew cannot arise for the
    person who matters — while a refusal would block a user mid-upgrade
    tomorrow.

    ⚠ **`sys.prefix`, not `sys.executable`.** `update.detect` measured that
    under `uv` the interpreter resolves THROUGH a symlink to a shared
    toolchain python under `~/.local/share/uv/python/...`, so its directory
    is not the tool's `bin`. `sys.prefix` is the environment root in both
    shapes, verified against this project's venv and a real
    `uv tool install`.

    Falls back to the bare name, which is today's behaviour: an instruction
    that says `rite` is worse than one naming the right path and better than
    one naming a path that does not exist.
    """
    import sys
    from pathlib import Path

    for base in (Path(sys.prefix), Path(sys.executable).parent.parent):
        candidate = base / "bin" / "rite"
        if candidate.is_file():
            return str(candidate)
    return "rite"
