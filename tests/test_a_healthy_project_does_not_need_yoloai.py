"""`rite doctor` on a healthy project must not depend on what is installed.

Two tests asserted "a project with nothing wrong exits 0" while their
fixtures left `sandbox.enabled` at its default of TRUE. A project
configured to sandbox on a machine with no yoloAI is not healthy, and
doctor is right to say so (SPEC §5.3.5, D-51: such a machine should get
"a row saying what is missing"). So the assertion silently meant "exit 0
IF yoloAI happens to be installed here" — green on a developer laptop,
red on Linux CI, and red there long enough that nobody could tell a real
doctor failure from this one.

The fix is the fixtures saying `enabled: false`, so "healthy" is true on
any machine. This is the guard for it: the same fixture, with yoloAI made
absent, must still pass.

A `skipif` on yoloAI's presence would have been the worse fix, and is the
defect class this repository keeps meeting — a test that does not run
reads exactly like a test that passed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli


@pytest.fixture
def hide_yoloai(monkeypatch):
    """Absent yoloAI, and nothing else changed.

    Not a PATH strip: removing the directory yoloAI lives in also removes
    gitleaks and gh, which doctor reports separately, and the test then
    fails for reasons that have nothing to do with sandboxing."""
    real = shutil.which

    def which(name, *a, **k):
        return None if name == "yoloai" else real(name, *a, **k)

    monkeypatch.setattr(shutil, "which", which)


def _healthy_project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    rite.joinpath("brief.yaml").write_text("project:\n  name: acme\n  role: owner\n")
    rite.joinpath("modules.yaml").write_text("modules: {}\n")
    rite.joinpath("config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: false\n"
    )
    return tmp_path


def test_doctor_passes_a_healthy_project_without_yoloai(
    tmp_path, monkeypatch, hide_yoloai
):
    """THE REGRESSION, in the environment CI actually has."""
    monkeypatch.chdir(_healthy_project(tmp_path))

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 0, result.output


def test_and_still_reports_it_when_the_project_wants_sandboxes(
    tmp_path, monkeypatch, hide_yoloai
):
    """The half that must not be lost. Turning sandboxing ON makes the same
    machine unhealthy, and saying so is doctor's job — asserted as a
    difference from the test above, so neither can pass on a build that
    simply stopped checking."""
    root = _healthy_project(tmp_path)
    (root / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: true\n"
    )
    monkeypatch.chdir(root)

    result = CliRunner().invoke(cli, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "yoloai" in result.output
