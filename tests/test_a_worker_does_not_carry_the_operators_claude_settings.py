"""A Worker gets rite's Claude settings, not the operator's (dogfood #27).

WHY THIS EXISTS. yoloAI's claude agent copies `~/.claude/settings.json`
from the home of the process that runs it into every sandbox, on every
create, start and restart, and nothing turns that off (yoloAI 0.11.0,
`internal/agent/agent.go` SeedFiles, `envsetup.CopySeedFiles`). In the
v0.6.0 dogfood a Worker producing a diff for someone else's repository ran
its operator's personal hooks and env. A pass on such a run says something
about the operator's machine, not about rite as a stranger receives it.

rite runs `yoloai new` with a home it owns (`worker_home`) and keeps
yoloAI's state where it is (`--data-dir`). That home links back what
yoloAI's seatbelt profile grants from the home (`.local`, the git config),
so rite and git work inside and a Worker commits as the operator, as before.

The live test at the end runs a real sandbox; it is opt-in
(`RITE_LIVE_YOLOAI=1`) because the suite runs concurrently on a shared
machine and on CI without yoloAI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rite_ai import sandbox as sb
from rite_ai.config.models import SandboxConfig
from rite_ai.sandbox import (
    start_worker,
    worker_home,
)


def _new_call(mock_run):
    return next(c for c in mock_run.call_args_list if "new" in c[0][0])


def _start(tmp_path: Path, backend: str = "seatbelt"):
    (tmp_path / "workers" / "alpha").mkdir(parents=True)
    with (
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.count_active_sandboxes", return_value=0),
        patch("rite_ai.sandbox.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = start_worker(tmp_path, "alpha", SandboxConfig(backend=backend))
    assert result.ok, result.message
    return _new_call(mock_run)


def test_yoloai_new_runs_with_rites_home_and_yoloais_own_data_dir(tmp_path):
    call = _start(tmp_path)
    args, env = call[0][0], call[1]["env"]
    assert args[1:4] == ["--data-dir", str(Path.home() / ".yoloai"), "new"]
    home = Path(env["HOME"])
    assert home == worker_home()
    assert home != Path.home()


def test_a_settings_file_left_in_rites_home_is_emptied_again():
    home = worker_home()
    stray = home / ".claude" / "settings.json"
    stray.write_text(json.dumps({"hooks": {"SessionStart": []}}))
    home = worker_home()
    assert sorted(p.name for p in (home / ".claude").iterdir()) == ["settings.json"]
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {}


def test_rites_home_links_what_yoloai_grants(tmp_path):
    """The home decides what the sandbox may read (`writeProfileHomeDir`).
    Measured with an empty home: `rite` inside could not load its Python."""
    real = tmp_path / "real"
    (real / ".local" / "bin").mkdir(parents=True)
    (real / "Library" / "Developer" / "Xcode").mkdir(parents=True)
    (real / ".gitconfig").write_text("[user]\n\tname = x\n")
    (real / ".config" / "git").mkdir(parents=True)
    with patch("rite_ai.sandbox.Path.home", return_value=real):
        home = worker_home()
    assert (home / ".local").resolve() == (real / ".local").resolve()
    xcode = home / "Library" / "Developer" / "Xcode"
    assert xcode.resolve() == (real / "Library" / "Developer" / "Xcode").resolve()
    assert not (home / "Library" / "Caches" / "swift-build").exists()
    assert (home / ".gitconfig").resolve() == (real / ".gitconfig").resolve()
    assert (home / ".config" / "git").resolve() == (real / ".config" / "git").resolve()


def test_other_backends_are_left_as_they_were(tmp_path):
    """Measured on seatbelt only: a container backend's client reads its own
    configuration from the home, so it is not moved until measured."""
    call = _start(tmp_path, backend="docker")
    args, env = call[0][0], call[1]["env"]
    assert "--data-dir" not in args
    assert env.get("HOME") == os.environ.get("HOME")


def _hook_commands(settings: dict) -> set[str]:
    return {
        hook.get("command", "")
        for entries in (settings.get("hooks") or {}).values()
        for entry in entries
        for hook in entry.get("hooks", [])
    }


@pytest.mark.skipif(
    os.environ.get("RITE_LIVE_YOLOAI") != "1" or shutil.which("yoloai") is None,
    reason="live yoloAI run: set RITE_LIVE_YOLOAI=1 on a machine with yoloAI",
)
def test_live_a_real_workers_settings_carry_none_of_the_operators_hooks(
    tmp_path_factory, monkeypatch
):
    """rite's own `start_worker`, a real seatbelt sandbox, a fake key (the
    agent fails to authenticate and does nothing). The control is the
    operator's real file: the test is only meaningful when it has hooks."""
    operator = Path.home() / ".claude" / "settings.json"
    theirs = (
        _hook_commands(json.loads(operator.read_text())) if operator.exists() else set()
    )
    if not theirs:
        pytest.skip("the operator's ~/.claude/settings.json has no hooks to leak")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-rite-live-test")
    # A short directory: the sandbox name is derived from it, and yoloAI's
    # tmux socket path under ~/.yoloai must stay within macOS's 103 bytes.
    tmp_path = tmp_path_factory.mktemp("lw")
    (tmp_path / ".rite").mkdir()
    workdir = tmp_path / "workers" / "alpha"
    workdir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    result = start_worker(tmp_path, "alpha", SandboxConfig(backend="seatbelt"))
    name = sb.sandbox_name("alpha", tmp_path)
    try:
        assert result.ok, result.message
        seeded = (
            Path.home()
            / ".yoloai"
            / "library"
            / "sandboxes"
            / name
            / "rw"
            / "agent-runtime"
            / "settings.json"
        )
        got = _hook_commands(json.loads(seeded.read_text()))
        assert got, "yoloAI's own hooks should be there"
        assert not (got & theirs), got & theirs
    finally:
        subprocess.run(
            ["yoloai", "destroy", "--abandon-unapplied", name],
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )


@pytest.mark.skipif(
    os.environ.get("RITE_LIVE_YOLOAI") != "1" or shutil.which("yoloai") is None,
    reason="live yoloAI run: set RITE_LIVE_YOLOAI=1 on a machine with yoloAI",
)
def test_live_rite_runs_inside_a_sandbox_started_from_rites_home(tmp_path_factory):
    """What the settings test above cannot see: under an empty home rite's
    own Python was unreadable inside, so every rite command a Worker runs
    failed. yoloAI's `test` agent runs the prompt as a shell command; the
    home, PATH and data dir are the ones `start_worker` uses."""
    if shutil.which("rite", path=sb.sandbox_environment()["PATH"]) is None:
        pytest.skip("no machine-wide rite for a sandbox to run")
    repo = tmp_path_factory.mktemp("lr")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f").write_text("x\n")
    subprocess.run([*git, "-C", str(repo), "add", "f"], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "init"], check=True)
    env = {**sb.sandbox_environment(), "HOME": str(worker_home())}
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    name = f"rite-lr-{os.getpid()}"
    probe = (
        "rite --version > out.txt 2>&1; git add out.txt; "
        "git -c user.name=t -c user.email=t@t.invalid commit -qm out"
    )
    try:
        subprocess.run(
            [
                "yoloai",
                "--data-dir",
                str(Path.home() / ".yoloai"),
                "new",
                name,
                str(repo),
                "--backend",
                "seatbelt",
                "--agent",
                "test",
                "-p",
                probe,
            ],
            env=env,
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
        )
        subprocess.run(
            ["yoloai", "wait", name],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        diff = subprocess.run(
            ["yoloai", "diff", name],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        ).stdout
        assert "+rite, version" in diff, diff
        assert "Library not loaded" not in diff, diff
    finally:
        subprocess.run(
            ["yoloai", "destroy", "--abandon-unapplied", name],
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
