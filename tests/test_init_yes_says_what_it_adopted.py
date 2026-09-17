"""`rite init --yes` says which spec it pointed Workers at.

Measured: `--yes` in a directory with a `SPEC.md` recorded `spec.paths` and
printed nothing about it — every Worker's CLAUDE.md then cited a file nobody
was shown. The choice matches the interactive default, so the defect is the
silence, not the decision.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli


def _init(tmp_path: Path, *args: str):
    return CliRunner().invoke(cli, ["init", str(tmp_path), "--yes", *args])


def test_it_names_the_spec_and_how_to_undo_it(tmp_path):
    (tmp_path / "SPEC.md").write_text("# spec\n")
    result = _init(tmp_path)
    assert result.exit_code == 0, result.output
    assert "Pointing Workers at the spec found here: `SPEC.md`" in result.output
    assert "rite spec remove" in result.output


def test_with_no_spec_there_is_nothing_to_say(tmp_path):
    result = _init(tmp_path)
    assert result.exit_code == 0, result.output
    assert "Pointing Workers at the spec" not in result.output


def test_a_preset_path_is_not_announced_as_a_default(tmp_path):
    """A spec the preset names was chosen by whoever wrote the preset."""
    (tmp_path / "DESIGN.md").write_text("# design\n")
    preset = tmp_path / "preset.yaml"
    preset.write_text("spec:\n  paths: [DESIGN.md]\n")
    result = _init(tmp_path, "--config", str(preset))
    assert result.exit_code == 0, result.output
    assert "--yes took the default" not in result.output
