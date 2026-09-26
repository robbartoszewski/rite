"""A sandboxed Claude Manager signs in from a file of its own (C6/C26).

Measured 2026-09-26: inside the Manager profile `claude -p` said `Not logged
in`, because the profile cannot read the keychain. `claude_login` gives each
Claude Manager a `CLAUDE_CONFIG_DIR` with its own `.credentials.json`, so no
keychain grant and no `~/.claude` grant is needed.

No real token anywhere: the one test that runs `claude` uses a fake, and passes
when Anthropic REJECTS it (401), because that proves the file was read, where
"Not logged in" would prove it was not.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from rite_ai.managers import claude_login as cl

FAKE = "sk-ant-oat01-THISisAfakeTOKENforTESTS"


@pytest.fixture
def home():
    d = Path(tempfile.mkdtemp(prefix="rcl", dir="/tmp")).resolve()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / ".rite").mkdir(parents=True)
    return root


def test_the_login_is_a_0600_file_in_a_0700_directory(project, home):
    path = cl._write_login(project, "lead", FAKE, home)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    body = json.loads(path.read_text())["claudeAiOauth"]
    assert body["accessToken"] == FAKE
    assert body["scopes"] == ["user:inference"]
    # No plan and no refresh token: rite knows neither.
    assert set(body) == {"accessToken", "expiresAt", "scopes"}


def test_the_pane_is_told_where_the_login_is_never_the_token(project, home):
    from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV

    assert cl.pane_environment(project, "lead", home) == {}
    cl._write_login(project, "lead", FAKE, home)
    env = cl.pane_environment(project, "lead", home)
    assert set(env) == {"CLAUDE_CONFIG_DIR"} <= ALLOWED_ON_TMUX_ARGV
    assert FAKE not in env["CLAUDE_CONFIG_DIR"]


def test_transcripts_are_read_from_the_managers_own_directory(project, home):
    assert cl.projects_dir(project, "lead", home) is None
    cl._write_login(project, "lead", FAKE, home)
    assert cl.projects_dir(project, "lead", home) == (
        cl._config_dir(project, "lead", home) / "projects"
    )


def test_the_run_ends_with_the_login_removed_and_transcripts_kept(project, home):
    path = cl._write_login(project, "lead", FAKE, home)
    (path.parent / "projects").mkdir()
    cl.remove_login(project, "lead", home)
    assert not path.exists()
    assert (path.parent / "projects").is_dir()


def test_no_claude_token_is_a_refusal_with_the_fix(project):
    refusal = cl.prepare(project, "lead", None)
    assert "claude setup-token" in refusal
    assert "rite credential set claude_token" in refusal


def test_the_profile_no_longer_grants_the_users_claude_directory(project):
    from rite_ai.managers.enclosure import compose

    text = compose(project, "lead")
    home = os.path.expanduser("~")
    assert f'"{home}/.claude"' not in text
    assert f'"{home}/.claude.json"' not in text


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("claude") is None,
    reason="needs seatbelt and a real claude binary",
)
def test_inside_the_profile_claude_READS_its_own_file(project, home, monkeypatch):
    """A fake token, rejected by Anthropic, is the success signal here: it
    proves Claude read the file. The failure this fixes is 'Not logged in'."""
    import rite_ai.managers.github_access as ga
    from rite_ai.managers.enclosure import compose, engine_tmp

    monkeypatch.setattr(ga, "_credential_root", lambda home_=None: home / "creds")
    cl._write_login(project, "lead", FAKE)
    profile = project / "p.sb"
    profile.write_text(compose(project, "lead"))
    tmp = engine_tmp(project, "lead")
    tmp.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
    }
    env.update(cl.pane_environment(project, "lead"), TMPDIR=str(tmp))
    out = subprocess.run(
        ["sandbox-exec", "-f", str(profile), "claude", "-p", "say hi"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        cwd=project,
        timeout=120,
    )
    said = out.stdout + out.stderr
    assert "Not logged in" not in said, said
    assert "401" in said or "invalid" in said.lower(), said


@pytest.mark.claude_login
def test_rite_start_REFUSES_a_claude_manager_with_no_token(tmp_path, monkeypatch):
    """Through the real CLI: no `claude_token`, no launch, and the fix named."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    cfg = tmp_path / ".rite" / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "manager_roles: []",
            "manager_roles:\n  - {name: lead, engine: claude, preset: lead}",
        )
    )
    starts = []
    import rite_ai.managers.supervise as sup

    monkeypatch.setattr(sup, "supervise", lambda *a, **k: starts.append(1))
    result = CliRunner().invoke(
        cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
    )
    assert result.exit_code == 1, result.output
    assert "rite credential set claude_token" in result.output
    assert starts == [], "nothing may be launched without the login"
