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
    # No `expiresAt`: rite cannot know when the token was minted, and
    # Claude Code reads the file without it (measured, 401 either way).
    assert set(body) == {"accessToken", "scopes"}


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


def test_a_leftover_login_is_REMOVED_and_said(project):
    """A killed run skips its `finally`, and this is a one-year token."""
    path = cl._write_login(project, "lead", "sk-ant-oat01-LEFTOVER")
    (path.parent / "projects").mkdir()
    said = []
    assert cl.prepare(project, "lead", FAKE, say=said.append) == ""
    assert json.loads(path.read_text())["claudeAiOauth"]["accessToken"] == FAKE
    assert (path.parent / "projects").is_dir(), "transcripts are kept"
    assert len(said) == 1 and "did not exit cleanly" in said[0]
    assert "LEFTOVER" not in said[0]
    said.clear()
    cl.remove_login(project, "lead")
    cl.prepare(project, "lead", FAKE, say=said.append)
    assert said == [], "a clean previous exit says nothing"


def test_a_leftover_is_removed_even_when_the_start_is_refused(project):
    path = cl._write_login(project, "lead", FAKE)
    said = []
    assert cl.prepare(project, "lead", None, say=said.append)
    assert not path.exists() and len(said) == 1


def test_the_run_lock_is_held_until_the_process_lets_go(project, home):
    from rite_ai.managers.github_access import hold_run

    first = hold_run(project, "lead", home)
    assert first is not None
    assert hold_run(project, "lead", home) is None, "a second run is refused"
    assert hold_run(project, "other", home) is not None, "another Manager is not"
    os.close(first)
    again = hold_run(project, "lead", home)
    assert again is not None, "a run that ended, however it ended, frees it"
    os.close(again)


def test_the_profile_lets_claude_write_its_directory_but_NOT_its_login(project, home):
    from rite_ai.managers.github_access import profile_lines

    login = cl._write_login(project, "lead", FAKE, home)
    lines = profile_lines(project, "lead", home)
    deny = f'(deny file-write* (literal "{login}"))'
    grant = f'(allow file-read* file-write* (subpath "{login.parent}"))'
    assert lines.index(deny) > lines.index(grant), "seatbelt takes the LAST match"


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")
def test_inside_the_profile_the_login_can_be_read_and_not_replaced(project):
    from rite_ai.managers.enclosure import compose

    login = cl._write_login(project, "lead", FAKE)
    profile = project.parent / "p.sb"
    profile.write_text(compose(project, "lead"))
    d = login.parent

    def inside(script):
        return subprocess.run(
            ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", script],
            capture_output=True,
            text=True,
        )

    assert FAKE in inside(f"cat {login}").stdout
    assert inside(f"mkdir -p {d}/projects && echo t > {d}/projects/t").returncode == 0
    for attempt in (
        f"echo x > {login}",
        f"echo x >> {login}",
        f"echo x > {d}/new && mv {d}/new {login}",
        f"rm {login}",
    ):
        got = inside(attempt)
        assert got.returncode != 0, attempt
    assert FAKE in login.read_text(), "the login is unchanged"


@pytest.mark.claude_login
def test_a_second_start_leaves_a_RUNNING_managers_login_alone(tmp_path, monkeypatch):
    """Before the run lock, a second `rite start` rewrote and then, in its
    `finally`, REMOVED the login of the Manager already running."""
    from click.testing import CliRunner

    from rite_ai.cli.main import cli
    from rite_ai.managers.github_access import hold_run

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    cfg = tmp_path / ".rite" / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "manager_roles: []",
            "manager_roles:\n  - {name: lead, engine: claude, preset: lead}",
        )
    )
    root = tmp_path.resolve()
    running = hold_run(root, "lead")
    login = cl._write_login(root, "lead", FAKE)
    try:
        result = CliRunner().invoke(
            cli, ["start", "lead", "--sessions", "1", "--minutes", "5"]
        )
    finally:
        os.close(running)
    assert result.exit_code == 1, result.output
    assert "still running" in result.output
    assert login.read_text().count(FAKE) == 1, "the running Manager's login stays"


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
def test_inside_the_profile_claude_READS_its_own_file(project):
    """A fake token, rejected by Anthropic, is the success signal here: it
    proves Claude read the file. The failure this fixes is 'Not logged in'.

    The login is under the suite's default credential root, which no profile
    grants. An earlier version put it under /tmp, which the profile grants
    read+write, so it proved nothing about the grant.
    """
    from rite_ai.managers.enclosure import compose, engine_tmp

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


def test_the_journal_redacts_the_login_by_exact_value(project, home, monkeypatch):
    """Measured 2026-09-26: `rite journal observe` quoting `.credentials.json`
    wrote the token verbatim. The structural rule misses the JSON shape."""
    from rite_ai.managers.journal import _redacted

    cl._write_login(project, "lead", FAKE, home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cl._config_dir(project, "lead", home)))
    out = _redacted(f'cat printed {{"claudeAiOauth":{{"accessToken":"{FAKE}"}}}}')
    assert FAKE not in out and "[redacted]" in out


def test_the_relay_reads_the_login_from_outside(project, home):
    assert cl.manager_secrets(project, "lead", home) == []
    cl._write_login(project, "lead", FAKE, home)
    assert cl.manager_secrets(project, "lead", home) == [FAKE]
    cl.remove_login(project, "lead", home)
    assert cl.manager_secrets(project, "lead", home) == []
