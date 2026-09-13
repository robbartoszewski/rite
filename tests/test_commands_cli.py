"""Module commands through the CLI: what `doctor` and `prepare` show, and that a
hand-written `commands:` block survives every command that rewrites project
configuration.

The block exists for the moment detection is wrong on someone else's machine.
An unrelated command erasing it would fail exactly the person it is for, so
this is asserted through the real commands rather than reasoned about. The
writers were found by searching for them: `config.yaml` is rewritten by the
schedule and credential commands, `modules.yaml` only by `rite init` and by
`rite add module` / `rite remove module`.
"""

import json
import re
from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.models import RecordedCommands
from rite_ai.config.parse import parse_modules
from tests.gate_helpers import commit_all, init_repo, write

_BLOCK = (
    "modules:\n"
    "  api:\n"
    "    path: api/\n"
    "    branch: main\n"
    "    description: Node backend\n"
    "    commands:\n"
    "      test: npm run test:unit\n"
    "      format: npx prettier -w .\n"
)
_RECORDED = RecordedCommands(test="npm run test:unit", format="npx prettier -w .")


def _project(root: Path, modules_yaml: str = _BLOCK) -> Path:
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite / "modules.yaml").write_text(modules_yaml)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\nsandbox:\n  enabled: false\n"
    )
    api = root / "api"
    api.mkdir()
    init_repo(api)
    write(
        api,
        "package.json",
        json.dumps({"scripts": {"build": "tsc", "lint": "eslint ."}}),
    )
    write(api, "package-lock.json", "{}\n")
    commit_all(api, "init")
    return root


def _problems(output: str) -> int:
    found = re.search(r"(\d+) problem\(s\) found", output)
    return int(found.group(1)) if found else 0


# --- survival -----------------------------------------------------------------


def test_config_writers_leave_modules_yaml_untouched(tmp_path, monkeypatch):
    root = _project(tmp_path)
    path = root / ".rite" / "modules.yaml"
    monkeypatch.chdir(root)
    runner = CliRunner()

    for args in (
        ["schedule", "set-timezone", "Europe/Warsaw"],
        ["schedule", "set", "09:00-18:00", "3"],
    ):
        before = path.read_bytes()
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert path.read_bytes() == before, (
            f"`rite {' '.join(args)}` rewrote modules.yaml"
        )


def test_module_writers_carry_a_hand_written_block_through(tmp_path, monkeypatch):
    root = _project(tmp_path)
    path = root / ".rite" / "modules.yaml"
    monkeypatch.chdir(root)
    runner = CliRunner()

    for args in (["add", "module", "web"], ["remove", "module", "web"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        api = next(m for m in parse_modules(path) if m.name == "api")
        assert api.commands == _RECORDED, f"`rite {' '.join(args)}` lost the block"


# --- doctor -------------------------------------------------------------------


def test_doctor_shows_where_each_command_came_from(tmp_path, monkeypatch):
    monkeypatch.chdir(_project(tmp_path))
    out = CliRunner().invoke(cli, ["doctor"]).output

    assert "module api: commands" in out
    assert "test     configured `npm run test:unit`" in out
    assert "format   configured `npx prettier -w .`" in out
    assert "install  detected   `npm ci`" in out
    assert "lint     detected   `npm run lint`" in out
    assert "no test command" not in out


def test_doctor_counts_a_module_with_no_test_command_as_a_problem(
    tmp_path, monkeypatch
):
    runner = CliRunner()
    monkeypatch.chdir(_project(tmp_path / "with"))
    with_test = runner.invoke(cli, ["doctor"]).output
    no_test = _BLOCK.replace("      test: npm run test:unit\n", "")
    monkeypatch.chdir(_project(tmp_path / "without", no_test))
    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code != 0
    assert "test     missing    —" in result.output
    assert "no test command — add one to .rite/modules.yaml" in result.output
    assert _problems(result.output) == _problems(with_test) + 1


# --- prepare ------------------------------------------------------------------


def test_prepare_prints_the_commands_as_they_are_now(tmp_path, monkeypatch):
    """A Worker's CLAUDE.md lists its commands as of `rite add worker`. A
    correction made afterwards reaches the Worker through `rite prepare`,
    which runs before every task — so that is what is asserted."""
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    runner = CliRunner()
    assert runner.invoke(cli, ["add", "worker", "alpha"]).exit_code == 0

    first = runner.invoke(cli, ["prepare", "--worker", "alpha"]).output
    assert "commands — run inside each module's checkout" in first
    assert "test     configured `npm run test:unit`" in first

    modules = root / ".rite" / "modules.yaml"
    modules.write_text(modules.read_text().replace("test:unit", "test:ci"))
    second = runner.invoke(cli, ["prepare", "--worker", "alpha"]).output
    assert "test     configured `npm run test:ci`" in second
