"""Git that rite runs on the host, in a repository a Manager can write, does
not execute a program that repository's own config names (SCRUM-75 follow-up;
the SCRUM-59 final review measured a repo-set `core.fsmonitor` running on
`git status`, which the progress footprint runs every cycle).

`githost.hardened_git_env` forces `core.fsmonitor` off and `core.hooksPath`
to nowhere, overriding the repo's `.git/config`. These probes set a
fsmonitor and a pre-commit hook that would drop a marker file when they run,
then run the host-side git the way rite does, and check the marker never
appears — while the control (a plain `git status` with the operator's own
environment) shows the fsmonitor WOULD have run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rite_ai.githost import hardened_git_env


def _repo(tmp_path: Path, marker: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t.invalid")
    (repo / "a.txt").write_text("x\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "one")
    # A repository-defined fsmonitor: git runs it on `git status`. A real
    # attack would read a secret or write an operator file; this only drops a
    # marker so the test can see whether it ran.
    git("config", "core.fsmonitor", f"sh -c 'touch {marker}'; echo")
    return repo


def test_hardened_status_does_not_run_the_repos_fsmonitor(tmp_path):
    marker = tmp_path / "RAN"
    repo = _repo(tmp_path, marker)
    subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        env=hardened_git_env(),
        capture_output=True,
        timeout=10,
    )
    assert not marker.exists(), "the repo's fsmonitor ran under the hardened env"


def test_control_the_fsmonitor_would_run_without_hardening(tmp_path):
    marker = tmp_path / "RAN"
    repo = _repo(tmp_path, marker)
    subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        capture_output=True,
        timeout=10,
    )
    # If this control ever stops creating the marker, the probe above proves
    # nothing — git changed how fsmonitor fires, and the test must be redone.
    assert marker.exists(), "control: the fsmonitor did not run even unhardened"


def test_the_progress_footprint_uses_the_hardened_env(tmp_path, monkeypatch):
    """The per-cycle `git status` specifically: `_git_state` must run it
    hardened, since it touches a Manager-writable tree every cycle."""
    from rite_ai.managers import progress

    marker = tmp_path / "RAN"
    repo = _repo(tmp_path, marker)
    progress._git_state(repo)
    assert not marker.exists()


def test_hardened_env_appends_after_existing_git_config_entries(tmp_path):
    base = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "store",
    }
    env = hardened_git_env(base)
    # The existing entry is untouched...
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    # ...and the hardening is appended, not overwritten.
    count = int(env["GIT_CONFIG_COUNT"])
    keys = {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)
    }
    assert keys["core.fsmonitor"] == "false"
    assert keys["core.hooksPath"] != ""
    assert count == 4


def test_hardening_does_not_null_the_operators_identity(tmp_path):
    """It must stay additive: a host-side commit still finds user.name from
    the operator's global config (the 'git without config uses the operator's
    name' contract)."""
    env = hardened_git_env({"PATH": "/usr/bin"})
    assert "GIT_CONFIG_GLOBAL" not in env
    assert "GIT_CONFIG_SYSTEM" not in env


def _gitleaks():
    from rite_ai.gate.gitleaks_runner import find_gitleaks_binary

    return find_gitleaks_binary()


def test_the_history_scan_does_not_run_a_repo_textconv(tmp_path):
    """🔴 The measured path: gitleaks' `git log -p` runs a repository-defined
    diff `textconv` program. The scan must disable it (`--no-textconv`)."""
    import subprocess

    binary = _gitleaks()
    if binary is None:
        import pytest

        pytest.skip("gitleaks not installed")
    marker = tmp_path / "RAN"
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t.invalid")
    (repo / "f.bin").write_text("some content\n")
    (repo / ".gitattributes").write_text("f.bin diff=pwn\n")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    # A diff driver whose textconv is a program: git runs it on `log -p`.
    git("config", "diff.pwn.textconv", f"sh -c 'touch {marker}'; cat")
    (repo / "f.bin").write_text("changed\n")
    git("commit", "-aqm", "two")

    from rite_ai.gate.gitleaks_runner import scan_history

    result = scan_history(repo, binary)
    assert not isinstance(result, type(None))
    assert not marker.exists(), "a repo-defined textconv ran during the scan"


def test_control_the_textconv_would_run_without_the_guard(tmp_path):
    """Proves the probe above is real: the same textconv DOES run under a
    plain `git log -p`."""
    import subprocess

    marker = tmp_path / "RAN"
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t.invalid")
    (repo / "f.bin").write_text("x\n")
    (repo / ".gitattributes").write_text("f.bin diff=pwn\n")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    git("config", "diff.pwn.textconv", f"sh -c 'touch {marker}'; cat")
    (repo / "f.bin").write_text("y\n")
    git("commit", "-aqm", "two")
    subprocess.run(["git", "log", "-p"], cwd=repo, capture_output=True)
    assert marker.exists(), "control: textconv did not run even unguarded"


def _repo_with_textconv(tmp_path: Path, marker: Path) -> Path:
    repo = tmp_path / "m"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t.invalid")
    (repo / "f.bin").write_text("one\n")
    (repo / ".gitattributes").write_text("f.bin diff=pwn\n")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    git("config", "diff.pwn.textconv", f"sh -c 'touch {marker}'; cat")
    (repo / "f.bin").write_text("two\n")
    git("commit", "-aqm", "two")
    return repo


def test_scope_budget_diff_runs_no_repo_textconv(tmp_path):
    """Option A review, measured: scope_budget's `git diff` ran a repo
    textconv on the host every delivery."""
    from rite_ai.publishing.scope_budget import measure

    marker = tmp_path / "RAN"
    repo = _repo_with_textconv(tmp_path, marker)
    measure(
        repo,
        "main~1..main",
        dod_paths=set(),
        exclude=[],
        items=1,
        lines_per_item=10,
        factor=3.0,
    )
    assert not marker.exists(), "scope_budget ran the repo's textconv"


def test_git_ops_status_runs_no_repo_fsmonitor(tmp_path):
    """Option A review, measured: `prepare` runs git_ops on the reused module
    checkout every cycle; a repo-set fsmonitor ran there."""
    from rite_ai.workspace import git_ops

    marker = tmp_path / "RAN"
    repo = tmp_path / "m"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t.invalid")
    (repo / "a").write_text("x\n")
    git("add", "a")
    git("commit", "-q", "-m", "one")
    git("config", "core.fsmonitor", f"sh -c 'touch {marker}'; echo")
    git_ops.is_clean(repo)
    git_ops.uncommitted_paths(repo)
    assert not marker.exists(), "git_ops ran the repo's fsmonitor"
