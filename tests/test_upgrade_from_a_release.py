"""A project built by the PREVIOUS release upgrades cleanly.

Every other refresh test builds the project with today's code and then takes
markers away to imitate an older one. This one runs the last release's own
`rite init` and `rite add worker` from its git tag, so the bytes are what a
real user has — the case that matters, and the one that cannot be imitated
into passing.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

REPO = Path(__file__).resolve().parents[1]
BUILD_WITH_OLD_RITE = """
import sys
from pathlib import Path
import rite_ai.sandbox as sb
sb.platform_can_sandbox = lambda: False
from rite_ai.cli.init import run_init
from rite_ai.workspace.manage import add_worker
root = Path(sys.argv[1])
assert run_init(root, yes=True).status == "created"
assert add_worker(root, "alpha").ok
"""


def _previous_tag() -> str | None:
    try:
        tags = subprocess.run(
            ["git", "tag", "-l", "v*"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(tags)[-1] if tags else None


@pytest.fixture
def project_from_the_last_release(tmp_path: Path) -> Path:
    tag = _previous_tag()
    if tag is None:
        pytest.skip("no release tag to upgrade from")
    src = tmp_path / "old"
    src.mkdir()
    archive = subprocess.run(
        ["git", "archive", tag], cwd=REPO, capture_output=True, check=True
    )
    subprocess.run(["tar", "-x", "-C", str(src)], input=archive.stdout, check=True)

    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    built = subprocess.run(
        [sys.executable, "-c", BUILD_WITH_OLD_RITE, str(root)],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(src / "src"),
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
    )
    if built.returncode != 0:
        pytest.skip(f"{tag}'s init could not run here: {built.stderr.strip()[-200:]}")
    return root


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git/" not in str(p.relative_to(root))
    }


def test_it_upgrades_without_touching_what_the_team_wrote(
    project_from_the_last_release, monkeypatch
):
    root = project_from_the_last_release
    authored = {
        ".rite/architecture.md": "# Architecture\n\nOurs, hand-written.\n",
        ".rite/plan.md": "# Plan\n\nPhase 1.\n",
        ".rite/context/domain.md": "Billing rules.\n",
    }
    for rel, text in authored.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    # An edit inside a generated section, which must survive untouched.
    claude = root / "CLAUDE.md"
    claude.write_text(
        claude.read_text().replace("## Commands", "## Commands\n\nOur own note.", 1)
    )
    before = _hashes(root)
    monkeypatch.chdir(root)

    dry = CliRunner().invoke(cli, ["update", "--files-only", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert _hashes(root) == before, "a dry run wrote something"

    applied = CliRunner().invoke(cli, ["update", "--files-only"])
    assert applied.exit_code == 0, applied.output

    for rel, text in authored.items():
        assert (root / rel).read_text() == text, rel
    assert before[".rite/brief.yaml"] == _hashes(root)[".rite/brief.yaml"]
    assert "Our own note." in claude.read_text(), (
        "an edit inside a generated section was reverted"
    )
    # The file every Worker reads is now this version's, and marked.
    worker = (root / "workers" / "alpha" / "CLAUDE.md").read_text()
    assert "rite:sha256" in worker
    for name in ("review.md",):
        assert (root / "workers" / "alpha" / ".claude" / "commands" / name).is_file()


def test_a_second_run_changes_nothing_further(
    project_from_the_last_release, monkeypatch
):
    """Idempotent: whatever the first run settles stays settled.

    Not "silent". A Worker's module list is written per project, so no
    release's bytes are on record for it and a refresh cannot tell the last
    release's rendering from a list someone edited. That section is reported
    every run until it is taken — which is the mechanism working, not
    something left undone. What must not happen is the file CHANGING again.
    """
    monkeypatch.chdir(project_from_the_last_release)
    assert CliRunner().invoke(cli, ["update", "--files-only"]).exit_code == 0
    settled = _hashes(project_from_the_last_release)

    again = CliRunner().invoke(cli, ["update", "--files-only"])

    assert again.exit_code == 0, again.output
    assert _hashes(project_from_the_last_release) == settled
    assert not [
        ln
        for ln in again.output.splitlines()
        if ("refresh" in ln or "added" in ln or "marked" in ln)
    ], again.output


def test_taking_the_renamed_section_settles_it_for_good(
    project_from_the_last_release, monkeypatch
):
    """The last release calls the Worker's module section `## Your modules`;
    this one calls it `## Modules checked out in your workspace`. Renamed, it
    is still the same section — it must not be added alongside the old one,
    and taking rite's version must leave exactly one."""
    monkeypatch.chdir(project_from_the_last_release)
    worker = project_from_the_last_release / "workers" / "alpha" / "CLAUDE.md"

    both = ("## Your modules", "## Modules checked out in your workspace")

    CliRunner().invoke(cli, ["update", "--files-only"])
    headings = [ln for ln in worker.read_text().splitlines() if ln.startswith("## ")]
    assert len([h for h in headings if h in both]) == 1, f"duplicated: {headings}"

    took = CliRunner().invoke(
        cli,
        [
            "update",
            "--files-only",
            "--take-rite",
            "Modules checked out in your workspace",
        ],
    )
    assert took.exit_code == 0, took.output
    headings = [ln for ln in worker.read_text().splitlines() if ln.startswith("## ")]
    assert headings.count("## Modules checked out in your workspace") == 1, headings
    assert "## Your modules" not in headings

    after = CliRunner().invoke(cli, ["update", "--files-only"])
    assert "generated files already current" in after.output, after.output


def test_doctor_says_the_project_is_behind_before_the_refresh(
    project_from_the_last_release, monkeypatch
):
    monkeypatch.chdir(project_from_the_last_release)
    from unittest.mock import patch

    with patch("keyring.get_password", return_value=None):
        before = CliRunner().invoke(cli, ["doctor"])
    assert "generated files:" in before.output
    assert "current" not in before.output.split("generated files:")[1].splitlines()[0]
