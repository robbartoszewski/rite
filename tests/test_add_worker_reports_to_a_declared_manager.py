"""`rite add worker` creates a Worker that reports to a Manager (SCRUM-26).

Without `--manager` it wrote `manager: ''` while the project declared one, so
the Worker's brief said "No Manager assigned yet." and, further down, "Tell
your Manager you are free" — the contradiction SCRUM-5 fixed for `rite init`
(`test_init_declares_a_whole_worker.py`). The CLI path now makes init's choice:
the first declared Manager. A `--manager` naming nobody declared is refused,
and the one case a Worker reports to nobody, a project with no Manager, is
said. Asserted on what the Worker is told, not on which arguments were passed.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.config.parse import ParseError, parse_worker


def _project(tmp_path: Path, monkeypatch, managers: list[str]) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: t\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    listed = "".join(f"    - {m}\n" for m in managers)
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        + ("coordination:\n  managers:\n" + listed if managers else "")
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _add(*args: str):
    return CliRunner().invoke(cli, ["add", "worker", *args], catch_exceptions=False)


def _reports_to(root: Path, name: str = "alpha") -> str:
    parsed = parse_worker(root / "workers" / name / "worker.yml")
    assert not isinstance(parsed, ParseError), parsed.message
    return parsed.manager


def _brief(root: Path, name: str = "alpha") -> str:
    return (root / "workers" / name / "CLAUDE.md").read_text()


def test_without_manager_it_reports_to_the_first_declared(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch, ["lead", "helper"])

    result = _add("alpha")

    assert result.exit_code == 0, result.output
    assert _reports_to(root) == "lead", (
        "a Worker added to a project with Managers reports to none of them"
    )
    brief = _brief(root)
    assert "Your Manager is **lead**." in brief
    assert "No Manager assigned yet." not in brief
    assert "reports to Manager 'lead' (the first declared" in result.output


def test_a_named_declared_manager_is_the_one_linked(tmp_path, monkeypatch):
    root = _project(tmp_path, monkeypatch, ["lead", "helper"])

    result = _add("alpha", "--manager", "helper")

    assert result.exit_code == 0, result.output
    assert _reports_to(root) == "helper"
    assert "reports to Manager 'helper'" in result.output
    assert "the first declared" not in result.output


def test_a_manager_nobody_declared_is_refused_and_nothing_is_created(
    tmp_path, monkeypatch
):
    root = _project(tmp_path, monkeypatch, ["lead"])

    result = _add("alpha", "--manager", "ghost")

    assert result.exit_code == 1
    assert "no Manager named 'ghost' is declared" in result.output
    assert "lead" in result.output, "the refusal does not say who IS declared"
    assert not (root / "workers" / "alpha").exists()


def test_with_no_manager_declared_it_says_the_worker_reports_to_nobody(
    tmp_path, monkeypatch
):
    """The case init also allows; never silent."""
    root = _project(tmp_path, monkeypatch, [])

    result = _add("alpha")

    assert result.exit_code == 0, result.output
    assert _reports_to(root) == ""
    assert "reports to no Manager: none is declared" in result.output
