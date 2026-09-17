"""`rite doctor` says when `ANTHROPIC_API_KEY` is exported.

Nothing in rite mentioned it, and every session rite starts inherits it. Claude
Code uses the key ahead of a subscription login, and without asking in
non-interactive mode, so a tester with it exported ran Workers on the key with
no line anywhere saying so. Reported as information, never as a problem, and
never with the value.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rite_ai.cli.init import run_init
from rite_ai.cli.main import api_key_notice, cli

_SECRET = "placeholder-value-for-doctor-test-0123456789"


def _doctor(tmp_path: Path, monkeypatch, key: str | None):
    tmp_path.mkdir(exist_ok=True)
    assert run_init(tmp_path, yes=True).status == "created"
    monkeypatch.chdir(tmp_path)
    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    return CliRunner().invoke(cli, ["doctor"])


def test_an_exported_key_is_reported_with_where_it_goes(tmp_path, monkeypatch):
    result = _doctor(tmp_path, monkeypatch, _SECRET)
    assert "env ANTHROPIC_API_KEY: set" in result.output
    assert "rite pool fill" in result.output
    assert "instead of your Claude subscription login" in result.output
    assert "unset ANTHROPIC_API_KEY" in result.output


def test_the_value_is_never_printed(tmp_path, monkeypatch):
    result = _doctor(tmp_path, monkeypatch, _SECRET)
    assert _SECRET not in result.output
    assert _SECRET[:12] not in result.output


def test_it_is_not_counted_as_a_problem(tmp_path, monkeypatch):
    without = _doctor(tmp_path / "a", monkeypatch, None)
    with_key = _doctor(tmp_path / "b", monkeypatch, _SECRET)
    assert with_key.exit_code == without.exit_code
    assert "ANTHROPIC_API_KEY" not in without.output


def test_an_empty_value_is_not_a_key():
    assert api_key_notice({"ANTHROPIC_API_KEY": ""}) is None
    assert api_key_notice({}) is None
