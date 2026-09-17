"""`brief.yaml`, `config.yaml` and `worker.yml` refuse keys they do not know.

Only `modules.yaml` did. In the other three a typo'd key was read as nothing:
`root_brach: develop` left the project on `main`, `interval: 2` left the
heartbeat at ten minutes, and the file looked configured. The mechanism is
modules.yaml's — known keys from the dataclasses, a "did you mean" — with the
two keys earlier releases left behind accepted, and every writer stopping on
the refusal so the file its author typed is never overwritten.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.init import run_init
from rite_ai.cli.main import cli
from rite_ai.config import parse
from rite_ai.config.models import ProjectBrief, ProjectConfig
from rite_ai.config.parse import ParseError, parse_brief, parse_config, parse_worker
from rite_ai.workspace.manage import add_worker


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


_BRIEF = "project:\n  name: news\n  role: owner\n  root_branch: develop\n"


# --- refused and named ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "key", "suggestion"),
    [
        ("project:\n  name: news\n  root_brach: dev\n", "root_brach", "root_branch"),
        (_BRIEF + "what:\n  kinds: backend\n", "kinds", "kind"),
        (_BRIEF + "technology:\n  language: [python]\n", "language", "languages"),
        (_BRIEF + "sources:\n  path: .\n", "sources", "source"),
    ],
)
def test_brief_refuses_an_unknown_key(tmp_path, text, key, suggestion):
    result = parse_brief(_write(tmp_path / "brief.yaml", text))
    assert isinstance(result, ParseError)
    assert f"'{key}'" in result.message
    assert f"did you mean '{suggestion}'" in result.message


@pytest.mark.parametrize(
    ("text", "key", "suggestion"),
    [
        ("heartbeat:\n  interval: 2\n", "interval", "interval_minutes"),
        (
            "schedule:\n  timezone: UTC\n  window:\n    - {hours: 09:00-17:00}\n",
            "window",
            "windows",
        ),
        (
            "schedule:\n  timezone: UTC\n  windows:\n"
            "    - {hours: '09:00-17:00', worker: 2}\n",
            "worker",
            "workers",
        ),
        ("sandbox:\n  enable: false\n", "enable", "enabled"),
        ("ticket_backnd:\n  type: jira\n", "ticket_backnd", "ticket_backend"),
        ("expertise:\n  ios:\n    tag: [swift]\n", "tag", "tags"),
        (
            "publish_gate:\n  scan_patterns:\n    - {type: regex, patern: x}\n",
            "patern",
            "pattern",
        ),
    ],
)
def test_config_refuses_an_unknown_key(tmp_path, text, key, suggestion):
    result = parse_config(_write(tmp_path / "config.yaml", text))
    assert isinstance(result, ParseError)
    assert f"'{key}'" in result.message
    assert f"did you mean '{suggestion}'" in result.message


def test_a_key_with_no_near_match_lists_what_is_known(tmp_path):
    result = parse_config(_write(tmp_path / "config.yaml", "project:\n  name: x\n"))
    assert isinstance(result, ParseError)
    assert "unknown key 'project'" in result.message
    assert "known: " in result.message and "ticket_backend" in result.message


def test_worker_refuses_an_unknown_key(tmp_path):
    result = parse_worker(
        _write(
            tmp_path / "worker.yml",
            "worker:\n  name: alpha\n  instructions: Stay in api\n",
        )
    )
    assert isinstance(result, ParseError)
    assert "did you mean 'claude_instructions'" in result.message


def test_free_form_mappings_are_not_checked(tmp_path):
    """Role names under `projects` and expertise names are the user's own."""
    config = parse_config(
        _write(
            tmp_path / "config.yaml",
            "ticket_backend:\n  type: jira\n  projects: {board: RT, anything: RT}\n"
            "expertise:\n  whatever-name:\n    tags: [swift]\n",
        )
    )
    assert isinstance(config, ProjectConfig)


# --- what earlier releases left behind ------------------------------------------


def test_a_v0_1_0_brief_with_what_notes_still_parses(tmp_path):
    brief = parse_brief(
        _write(
            tmp_path / "brief.yaml",
            _BRIEF + "what:\n  kind: backend\n  features: ''\n  notes: ''\n",
        )
    )
    assert isinstance(brief, ProjectBrief)
    assert brief.root_branch == "develop"


def test_an_enriched_section_still_parses_and_doctor_still_reports_it(
    tmp_path, monkeypatch
):
    assert run_init(tmp_path, yes=True).status == "created"
    brief_path = tmp_path / ".rite" / "brief.yaml"
    brief_path.write_text(brief_path.read_text() + "enriched:\n  follow_ups: [x]\n")
    assert isinstance(parse_brief(brief_path), ProjectBrief)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["doctor"])
    assert "`enriched:` section nothing reads" in result.output


# --- every writer stops before writing ----------------------------------------


def _project_with(tmp_path: Path, rel: str, typo: str) -> tuple[Path, str]:
    assert run_init(tmp_path, yes=True).status == "created"
    path = tmp_path / rel
    text = path.read_text() + typo
    path.write_text(text)
    return path, text


@pytest.mark.parametrize(
    "args",
    [
        ["schedule", "set", "09:00-17:00", "2"],
        ["schedule", "set-timezone", "Europe/Warsaw"],
        ["spec", "add", "SPEC.md"],
        ["spec", "remove", "SPEC.md"],
    ],
)
def test_a_refused_config_is_left_exactly_as_it_was(tmp_path, monkeypatch, args):
    path, text = _project_with(tmp_path, ".rite/config.yaml", "heartbeat_:\n  x: 1\n")
    (tmp_path / "SPEC.md").write_text("# spec\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, args)

    assert result.exit_code != 0, result.output
    assert "heartbeat_" in result.output
    assert path.read_text() == text


def test_credential_set_does_not_rewrite_a_refused_config(tmp_path, monkeypatch):
    path, text = _project_with(tmp_path, ".rite/config.yaml", "sandbox:\n  enable: 0\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("keyring.set_password", lambda *a: None)

    result = CliRunner().invoke(
        cli,
        ["credential", "set", "jira"],
        input="acme.atlassian.net\nRT\nme@acme.com\nTOK\nTOK\n",
    )

    assert result.exit_code != 0, result.output
    assert "'enable'" in result.output
    assert path.read_text() == text


def test_init_on_an_existing_project_with_a_refused_brief_changes_nothing(tmp_path):
    path, text = _project_with(tmp_path, ".rite/brief.yaml", "wat:\n  kind: x\n")

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path)], input="y\n\nA change\ny\n"
    )

    assert result.exit_code == 1, result.output
    assert "'wat'" in result.output
    assert "Wipe and start over" not in result.output
    assert path.read_text() == text


def test_adding_a_worker_again_does_not_rewrite_its_refused_manifest(tmp_path):
    run_init(tmp_path, yes=True)
    path = _write(
        tmp_path / "workers" / "alpha" / "worker.yml",
        "worker:\n  name: alpha\n  instructions: Stay in api\n",
    )
    text = path.read_text()

    add_worker(tmp_path, "alpha")

    assert path.read_text() == text


# --- the key sets follow the dataclasses --------------------------------------


def test_brief_sections_cover_every_field_and_nothing_else():
    names = {f.name for f in dataclasses.fields(ProjectBrief)}
    from_sections = {
        f"source_{key}" if section == "source" else key
        for section, keys in parse._BRIEF_SECTIONS.items()
        for key in keys
    }
    assert from_sections == names


def test_config_sections_cover_every_field():
    names = {f.name for f in dataclasses.fields(ProjectConfig)}
    assert set(parse._CONFIG_SECTIONS) | {"expertise"} == names
