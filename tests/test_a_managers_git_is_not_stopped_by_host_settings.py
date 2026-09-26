"""🔴 The operator's global git config stopped a Manager's commit and push,
and nothing said why.

`commit.gpgsign` with a key the sandbox cannot read failed every commit; a
global `core.hooksPath` failed every push. Signing is now turned off for a
Manager's git, and said; a global hooks path is REPORTED and deliberately
not bypassed, because a global hook can be a guard. These drive REAL git
against an isolated global config, each with a control.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from rite_ai.managers import git_settings
from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A repository, and a global git config of our own writing."""
    home = tmp_path / "home"
    home.mkdir()
    global_config = tmp_path / "gitconfig"
    global_config.write_text("[user]\n\tname = Op\n\temail = op@example.invalid\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in list(os.environ):
        if name.startswith(
            ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
        ):
            monkeypatch.delenv(name)
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    def set_global(key: str, value: str) -> None:
        subprocess.run(
            ["git", "config", "--global", key, value], check=True, capture_output=True
        )

    return repo, set_global


def _commit(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    (repo / "f.txt").write_text(str(len(list(repo.iterdir()))))
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "m"],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


def test_nothing_is_overridden_for_a_plain_config(host):
    repo, _ = host
    assert git_settings.host_git_findings(repo) == []
    assert git_settings.pane_environment(repo, {}) == {}


def test_ssh_signing_with_an_unreadable_key_no_longer_stops_a_commit(host, tmp_path):
    repo, set_global = host
    set_global("commit.gpgsign", "true")
    set_global("gpg.format", "ssh")
    # The key the sandbox cannot read, as far as git is concerned: absent.
    set_global("user.signingkey", str(tmp_path / "home" / ".ssh" / "id_ed25519"))

    control = _commit(repo, {})
    assert control.returncode != 0, "the control committed: signing did not bite"

    env = git_settings.pane_environment(repo, {})
    assert _commit(repo, env).returncode == 0, env

    (said,) = [o.said for o in git_settings.host_git_findings(repo)]
    assert "commit.gpgsign" in said and "~/.ssh" in said
    assert "unchanged" in said, "the user is not told their own config is untouched"


def test_tag_signing_is_overridden_too(host):
    repo, set_global = host
    set_global("tag.gpgsign", "true")
    keys = [o.key for o in git_settings.host_git_findings(repo)]
    assert keys == ["tag.gpgsign"]


def test_a_global_hooks_path_is_reported_and_NOT_bypassed(host, tmp_path):
    """⚠ A global hook can be a guard — the first machine this ran on has a
    global `pre-push` that stops firm data leaving it. Overriding the path
    would turn a push that fails CLOSED into one that silently skips the
    guard. So the Manager's git still meets the global hook, and the
    operator is told the opt-in and what it costs."""
    repo, set_global = host
    elsewhere = tmp_path / "global-hooks"
    elsewhere.mkdir()
    refuse = elsewhere / "pre-commit"
    refuse.write_text("#!/bin/sh\nexit 1\n")
    refuse.chmod(0o755)
    set_global("core.hooksPath", str(elsewhere))

    (finding,) = git_settings.host_git_findings(repo)
    assert finding.key == "core.hooksPath" and finding.value is None
    assert git_settings.pane_environment(repo, {}) == {}, "the guard was bypassed"
    env = git_settings.pane_environment(repo, {})
    assert _commit(repo, env).returncode != 0, "the global hook did not run"
    assert "does NOT bypass" in finding.said
    assert "also stops your global hooks" in finding.said, "the cost is not said"


def test_the_opt_in_it_names_works_as_printed(host, tmp_path):
    """The remedy is a command; run it exactly as printed. The project's own
    hooks — where the publish gate lives — then run, and the finding goes."""
    repo, set_global = host
    elsewhere = tmp_path / "global-hooks"
    elsewhere.mkdir()
    set_global("core.hooksPath", str(elsewhere))
    marker = tmp_path / "project-hook-ran"
    own = repo / ".git" / "hooks" / "pre-commit"
    own.write_text(f"#!/bin/sh\ntouch {marker}\n")
    own.chmod(0o755)

    (finding,) = git_settings.host_git_findings(repo)
    (command,) = [
        span
        for span in finding.said.split("`")
        if span.startswith("git config --local core.hooksPath ")
    ]
    subprocess.run(shlex.split(command), cwd=repo, check=True)

    assert git_settings.host_git_findings(repo) == []
    assert _commit(repo, {}).returncode == 0
    assert marker.exists(), "the project's own hook did not run after the opt-in"


def test_a_hooks_path_the_project_set_is_left_alone(host, tmp_path):
    """A Manager works in the real checkout: `.husky` and the like are the
    project's choice, unlike a Worker's clone."""
    repo, set_global = host
    set_global("core.hooksPath", str(tmp_path / "global-hooks"))
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "core.hooksPath", ".husky"],
        check=True,
    )
    assert git_settings.host_git_findings(repo) == []


def test_a_relative_global_hooks_path_resolves_inside_the_repo_and_is_left_alone(
    host,
):
    repo, set_global = host
    set_global("core.hooksPath", ".githooks")
    assert git_settings.host_git_findings(repo) == []


def test_the_overrides_are_numbered_after_githubs_and_may_travel_on_tmux(host):
    """`github_access` already sets GIT_CONFIG_COUNT=2; a second count would
    REPLACE the credential helper rather than add to it."""
    repo, set_global = host
    set_global("commit.gpgsign", "true")
    set_global("tag.gpgsign", "true")
    github = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_1": "!gh auth git-credential",
    }
    env = git_settings.pane_environment(repo, github)
    assert env == {
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_2": "commit.gpgsign",
        "GIT_CONFIG_VALUE_2": "false",
        "GIT_CONFIG_KEY_3": "tag.gpgsign",
        "GIT_CONFIG_VALUE_3": "false",
    }
    assert set({**github, **env}) <= ALLOWED_ON_TMUX_ARGV


def test_rite_start_says_what_it_overrode(host, capsys):
    from rite_ai.cli.main import _say_git_findings

    repo, set_global = host
    set_global("commit.gpgsign", "true")
    _say_git_findings(repo, "lead")
    err = capsys.readouterr().err
    assert "git: Manager 'lead'" in err and "signing OFF" in err
