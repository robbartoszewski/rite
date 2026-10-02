"""The publish gate's rules are rite's, and doctor says which they are (SCRUM-17).

🔴 Reported: the config names `.rite/gitleaks.toml`, which does not exist, and
`scan_patterns` is empty, while doctor calls the gate "active". The missing
file was never the gap: with no `--config`, gitleaks' defaults applied. The
gap was WHERE gitleaks takes its rules from when rite names none: measured
on gitleaks 8.30.1, a `.gitleaks.toml` at the scanned repository's root,
`GITLEAKS_CONFIG` or `GITLEAKS_CONFIG_TOML` pointing at a config that
allowlists every path took a real-shaped token from one finding to ZERO. And
an inline `gitleaks:allow` hid it too, with no reason recorded. So a
repository rite pushes for could switch rite's own gate off.

Now the gate always passes `--config` (the project's own, or gitleaks'
defaults), clears those variables, ignores inline allows, and refuses to
vouch for a scan when a `.gitleaksignore` at the root (which gitleaks
applies on its own, and nothing turns off) could be hiding findings.

Against the real gitleaks binary; skipped, visibly, where it is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rite_ai.config.models import ProjectConfig
from rite_ai.gate import gitleaks_runner
from rite_ai.gate.gate import ruleset, run_gate
from tests.gate_helpers import commit_all, init_repo, write

BINARY = gitleaks_runner.find_gitleaks_binary()
requires_gitleaks = pytest.mark.skipif(BINARY is None, reason="gitleaks not installed")

TOKEN = "ghp_1234567890abcdefghij1234567890ABCDEF"
PERMISSIVE = "[extend]\nuseDefault = true\n[allowlist]\npaths = ['''.*''']\n"


def _repo(tmp_path: Path, line: str = f'token = "{TOKEN}"\n') -> Path:
    init_repo(tmp_path)
    write(tmp_path, "app.py", line)
    commit_all(tmp_path, "add a token")
    return tmp_path


def _found(root: Path) -> int:
    got = gitleaks_runner.scan_history(root, BINARY)
    assert isinstance(got, list), got
    return len(got)


@requires_gitleaks
class TestNothingOutsideRiteChoosesTheRules:
    def test_control_the_token_is_found(self, tmp_path):
        assert _found(_repo(tmp_path)) == 1

    def test_a_repositorys_own_gitleaks_toml_does_not_switch_it_off(self, tmp_path):
        root = _repo(tmp_path)
        write(root, ".gitleaks.toml", PERMISSIVE)
        assert _found(root) == 1

    @pytest.mark.parametrize("variable", ["GITLEAKS_CONFIG", "GITLEAKS_CONFIG_TOML"])
    def test_the_environment_does_not_switch_it_off(
        self, tmp_path, monkeypatch, variable
    ):
        root = _repo(tmp_path)
        permissive = tmp_path / "permissive.toml"
        permissive.write_text(PERMISSIVE)
        monkeypatch.setenv(
            variable,
            str(permissive) if variable == "GITLEAKS_CONFIG" else PERMISSIVE,
        )
        assert _found(root) == 1

    def test_an_inline_allow_does_not_hide_it(self, tmp_path):
        root = _repo(tmp_path, f'token = "{TOKEN}"  # gitleaks:allow\n')
        assert _found(root) == 1

    def test_the_projects_own_config_still_applies(self, tmp_path):
        """Named on purpose, it is the project's choice and rite passes it."""
        root = _repo(tmp_path)
        own = tmp_path / "own.toml"
        own.write_text(PERMISSIVE)
        got = gitleaks_runner.scan_history(root, BINARY, config_path=own)
        assert got == []


@requires_gitleaks
def test_a_gitleaksignore_at_the_root_makes_the_gate_refuse_to_vouch(tmp_path):
    root = _repo(tmp_path)
    (root / ".rite").mkdir()
    (root / ".rite" / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    write(root, ".gitleaksignore", "deadbeef:app.py:github-pat:1\n")
    report = run_gate(root)
    assert any(".gitleaksignore exists" in e for e in report.errors), report.errors


class TestDoctorNamesTheRules:
    def test_with_no_project_config_it_is_gitleaks_defaults(self, tmp_path):
        line, problem = ruleset(tmp_path, ProjectConfig())
        assert "gitleaks' default ruleset, passed by rite explicitly" in line
        assert problem == ""

    def test_a_config_that_extends_the_defaults_says_so(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "gitleaks.toml").write_text(
            "[extend]\nuseDefault = true\n"
        )
        line, problem = ruleset(tmp_path, ProjectConfig())
        assert "extending gitleaks' default ruleset" in line and problem == ""

    def test_a_config_that_replaces_the_defaults_says_so(self, tmp_path):
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "gitleaks.toml").write_text('title = "mine"\n')
        line, _ = ruleset(tmp_path, ProjectConfig())
        assert "REPLACES gitleaks' default ruleset" in line

    def test_a_root_gitleaksignore_is_a_problem(self, tmp_path):
        (tmp_path / ".gitleaksignore").write_text("x\n")
        line, problem = ruleset(tmp_path, ProjectConfig())
        assert "CANNOT VOUCH" in line and problem


def test_gitleaks_is_never_handed_a_config_variable(tmp_path, monkeypatch):
    """The second layer, pinned on its own: `--config` already outranks the
    variables (which is why the behavioural tests above cannot see this),
    and they are cleared as well, so neither layer rests on the other."""
    import subprocess as sp

    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["env"] = args, kwargs.get("env")
        report = args[args.index("-r") + 1]
        Path(report).write_text("[]")
        return sp.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("GITLEAKS_CONFIG", "/somewhere/permissive.toml")
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML", "[allowlist]")
    monkeypatch.setattr(gitleaks_runner.subprocess, "run", fake_run)
    assert gitleaks_runner.scan_history(tmp_path, "gitleaks") == []
    assert seen["env"] is not None
    assert "GITLEAKS_CONFIG" not in seen["env"]
    assert "GITLEAKS_CONFIG_TOML" not in seen["env"]
    assert "--config" in seen["args"] and "--ignore-gitleaks-allow" in seen["args"]
