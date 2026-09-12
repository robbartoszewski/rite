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
