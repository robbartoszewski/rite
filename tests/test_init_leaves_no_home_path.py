"""`rite init` writes no absolute path into the files it asks you to commit.

**Observed** in the v0.6.0 dogfood (F1): `rite init` in `~/AI/dogfood-v060/pingr`,
answering "." for the existing code, wrote the absolute path into
`.rite/brief.yaml`, and rite's own publish gate blocked the project's first push:

    [rite-hardcoded-macos-home-directory-path] .rite/brief.yaml:14 — Hardcoded
    macOS home directory path

The only ways past it were a suppression with a throwaway reason or `git push
--no-verify`, so a new user learns on push one that the gate can be waved
through.

**Pre-registered in the dogfood write-up (#3):** `rite init` → `git add -A;
git commit; git push` succeeds on the first try with the gate active, with no
`.rite/gitleaksignore` entry added by hand. That was run for real before this
landed, in a project under the home directory, with the gate installed as the
pre-push hook: the released 0.6.0 was refused, and this change was not (the
PR records both). The gate's rule matches `/Users/<name>/`, and pytest's
temporary directories are not under a home directory, so here the property
the rule tripped on is checked directly: no generated file holds the
project's absolute path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.init.questionnaire import _as_committed
from rite_ai.cli.main import cli


def _init(tmp_path: Path, source: str = ".") -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("print(1)\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
    preset = tmp_path / "preset.yaml"
    preset.write_text(f"source:\n  path: {source}\n")
    result = CliRunner().invoke(
        cli, ["init", str(project), "--config", str(preset), "--yes"]
    )
    assert result.exit_code == 0, result.output
    return project


def _committed(project: Path) -> list[Path]:
    """What `git add -A` would stage: tracked plus untracked, minus ignored."""
    out = subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "ls-files",
            "--others",
            "--cached",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [project / name for name in out]


def test_no_generated_file_holds_the_projects_absolute_path(tmp_path):
    project = _init(tmp_path)
    here = str(project.resolve())
    offenders = [
        str(p.relative_to(project))
        for p in _committed(project)
        if p.is_file() and here in p.read_text(errors="replace")
    ]
    assert not offenders, f"absolute project path written into {offenders}"
    brief = (project / ".rite" / "brief.yaml").read_text()
    assert "path: ." in brief


def test_the_path_is_relative_to_the_project_root():
    root = Path("/srv/work/proj")
    assert _as_committed(root, root) == "."
    assert _as_committed(root / "docs" / "SPEC.md", root) == "docs/SPEC.md"


def test_a_source_outside_the_project_is_relative_too():
    """True on every clone laid out the same way; an absolute path is true
    on one machine only, and names its user."""
    assert (
        _as_committed(Path("/srv/work/specs/x.md"), Path("/srv/work/proj"))
        == "../specs/x.md"
    )
