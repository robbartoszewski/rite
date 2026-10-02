"""Driving a local engine's turn inside a Worker sandbox (OL5b).

`GooseAgent` already takes an injectable `launch`, so running inside costs a
different LAUNCHER and no change to the tool loop — which is what keeps RL-13
true of the sandboxed path too.

Two things are load-bearing and both were measured
(`docs/design/spikes/OL1-ollama-inside-a-worker-sandbox.md`): the `--`, without
which `yoloai exec` eats the command's own flags, and the fact that only Goose's
own keys may travel, because `yoloai exec` has no `--env` and SB12 measured a
sandbox's environment readable from other sandboxes on this machine.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rite_ai.local.in_sandbox import (
    FORWARDED,
    SecretOnArgv,
    exec_argv,
    exec_launcher,
)
from rite_ai.sandbox import goose_path_root, sandbox_state_dir

GOOSE = ["goose", "run", "-n", "handle", "-i", "/tmp/instr.txt"]
ENV = {
    "GOOSE_PROVIDER": "ollama",
    "GOOSE_MODEL": "qwen3.8:latest",
    "OLLAMA_HOST": "http://localhost:11434",
    "GOOSE_PATH_ROOT": "/sbx/rite/goose",
}


def test_the_double_dash_is_there():
    # Measured: `yoloai exec <box> curl -s ...` fails with "unknown shorthand
    # flag: 's' in -s". The command's flags must not be read as yoloAI's.
    argv = exec_argv("box", GOOSE, ENV)
    assert argv[:4] == ["yoloai", "exec", "box", "--"]
    assert argv.index("--") < argv.index("goose")


def test_goose_keys_travel():
    argv = exec_argv("box", GOOSE, ENV)
    assert "GOOSE_MODEL=qwen3.8:latest" in argv
    assert "OLLAMA_HOST=http://localhost:11434" in argv
    assert "GOOSE_PATH_ROOT=/sbx/rite/goose" in argv


def test_the_host_environment_does_not_travel():
    # GooseAgent.run hands its launcher dict(os.environ) — right on the host,
    # wrong here, because everything forwarded is forwarded ON ARGV.
    argv = exec_argv("box", GOOSE, {**ENV, "PATH": "/usr/bin", "HOME": "/Users/x"})
    joined = " ".join(argv)
    assert "PATH=" not in joined
    assert "HOME=" not in joined


def test_an_empty_value_is_not_placed():
    # `GOOSE_PATH_ROOT=` would point Goose at a relative path.
    argv = exec_argv("box", GOOSE, {**ENV, "GOOSE_PATH_ROOT": ""})
    assert not any(a.startswith("GOOSE_PATH_ROOT") for a in argv)


def test_a_secret_refuses_rather_than_riding_along():
    with pytest.raises(SecretOnArgv) as caught:
        exec_argv("box", GOOSE, {**ENV, "GITHUB_TOKEN": "ghp_secret"})
    assert "GITHUB_TOKEN" in str(caught.value)


def test_a_secret_refuses_rather_than_being_dropped_silently():
    # Dropping it would configure the turn differently from what was asked,
    # and say nothing. Neither outcome is acceptable, so it raises.
    with pytest.raises(SecretOnArgv):
        exec_argv("box", GOOSE, {"ANTHROPIC_API_KEY": "sk-x"})


def test_every_forwarded_key_is_itself_safe():
    # The guard protects the set; this protects the set from growing wrong.
    from rite_ai.local.in_sandbox import _secret_shaped

    assert not [k for k in FORWARDED if _secret_shaped(k)]


def test_the_launcher_ignores_the_host_workspace():
    # `workspace` is a HOST path; the sandbox holds a copy at a path of
    # yoloAI's making, and `yoloai exec` already starts there. Passing a host
    # cwd could run the turn OUTSIDE the sandbox on the operator's real tree.
    with patch("rite_ai.local.in_sandbox.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        exec_launcher("box", binary="/bin/yoloai")(GOOSE, "/host/workspace", ENV)
    assert "cwd" not in run.call_args[1]
    assert "/host/workspace" not in " ".join(run.call_args[0][0])


# ── Where the root comes from ────────────────────────────────────────────────


def _files_path(stdout: str, returncode: int = 0):
    return patch(
        "rite_ai.sandbox.subprocess.run",
        return_value=MagicMock(returncode=returncode, stdout=stdout, stderr=""),
    )


def test_the_layer_is_the_parent_of_the_exchange_directory():
    # `yoloai files <name> path` is the documented accessor; the layer is its
    # parent. Measured live: .../sandboxes/<name>/rw/files -> .../rw
    with patch("rite_ai.sandbox.shutil.which", return_value="/bin/yoloai"):
        with _files_path("/lib/sandboxes/box/rw/files\n"):
            assert sandbox_state_dir("box") == Path("/lib/sandboxes/box/rw")


def test_the_root_is_under_rites_own_namespace():
    with patch("rite_ai.sandbox.shutil.which", return_value="/bin/yoloai"):
        with _files_path("/lib/sandboxes/box/rw/files\n"):
            assert goose_path_root("box") == "/lib/sandboxes/box/rw/rite/goose"


def test_an_unknown_sandbox_gives_no_root_rather_than_a_guess():
    # "" must be read as a refusal to start the turn: Goose with no writable
    # root panics before reaching the model, and a wrong root is worse.
    with patch("rite_ai.sandbox.shutil.which", return_value="/bin/yoloai"):
        with _files_path("", returncode=1):
            assert goose_path_root("box") == ""
            assert sandbox_state_dir("box") is None


def test_no_yoloai_gives_no_root():
    with patch("rite_ai.sandbox.shutil.which", return_value=None):
        assert goose_path_root("box") == ""
