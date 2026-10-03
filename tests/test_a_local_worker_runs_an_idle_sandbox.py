"""A local Worker's sandbox runs `--agent idle`, and rite drives the turn (OL5).

`sandbox/__init__.py` built `--agent "claude"` as a literal, which is what made
a Worker Claude-only whatever its manifest said. Generalising it is NOT passing
another yoloAI agent name: `--agent` takes a closed list (aider, claude, codex,
gemini, idle, opencode, shell, test) and **Goose is not on it**. The ruled shape
(D-CU-2) is an `idle` container with rite running each turn through
`yoloai exec`, which also keeps RL-13 — rite still borrows an existing agent's
tool loop rather than writing one.
"""

from __future__ import annotations

from pathlib import Path

from rite_ai.config.models import WorkerManifest
from rite_ai.sandbox import LOCAL_WORKER_AGENT, worker_manifest, yoloai_agent_for
from rite_ai.workspace.manage import _write_worker_manifest

LOCAL = WorkerManifest(
    name="w1",
    manager="lead",
    engine="local:small",
    endpoint="http://localhost:11434",
    model="qwen3.8:latest",
    agent="goose",
    context_window=32768,
)


def _project(tmp_path: Path, manifest: WorkerManifest) -> Path:
    worker_dir = tmp_path / "workers" / manifest.name
    worker_dir.mkdir(parents=True)
    _write_worker_manifest(worker_dir, manifest)
    return tmp_path


def test_a_local_worker_gets_the_idle_agent():
    assert yoloai_agent_for(LOCAL) == LOCAL_WORKER_AGENT == "idle"


def test_a_claude_worker_still_gets_claude():
    assert yoloai_agent_for(WorkerManifest(name="w1")) == "claude"


def test_an_unreadable_manifest_means_claude_not_a_refusal():
    # "Could not tell" keeps the behaviour a Worker had before it had an engine.
    # Failing the start instead would let a typo in one Worker's file stop
    # another's.
    assert yoloai_agent_for(None) == "claude"


def test_the_manifest_is_found_from_the_root_and_the_name(tmp_path):
    root = _project(tmp_path, LOCAL)
    found = worker_manifest(root, "w1")
    assert found is not None
    assert found.engine == "local:small"
    assert yoloai_agent_for(found) == "idle"


def test_a_missing_manifest_is_none_not_an_error(tmp_path):
    assert worker_manifest(tmp_path, "nobody") is None


def test_an_unparseable_manifest_is_none_not_an_error(tmp_path):
    worker_dir = tmp_path / "workers" / "w1"
    worker_dir.mkdir(parents=True)
    # A local engine with no endpoint/model/agent — refused by the parser.
    (worker_dir / "worker.yml").write_text("worker:\n  name: w1\n  engine: local:x\n")
    assert worker_manifest(tmp_path, "w1") is None


# ── The launch itself ─────────────────────────────────────────────────────────
# Same shape as test_a_worker_does_not_carry_the_operators_claude_settings: the
# `new` call's argv and env are inspected, nothing is started.

from unittest.mock import MagicMock, patch  # noqa: E402

from rite_ai.config.models import SandboxConfig  # noqa: E402
from rite_ai.sandbox import CLAUDE_TOKEN_ENV_VAR, start_worker  # noqa: E402


def _new_call(mock_run):
    return next(c for c in mock_run.call_args_list if "new" in c[0][0])


def _launch(root: Path, name: str, env=None):
    with (
        patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai"),
        patch("rite_ai.sandbox.count_active_sandboxes", return_value=0),
        patch("rite_ai.sandbox.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = start_worker(root, name, SandboxConfig(backend="seatbelt"), env=env)
    assert result.ok, result.message
    call = _new_call(mock_run)
    return call[0][0], call[1]["env"]


def test_the_launch_asks_for_idle_when_the_manifest_is_local(tmp_path):
    root = _project(tmp_path, LOCAL)
    argv, _env = _launch(root, "w1")
    assert "--agent" in argv
    assert argv[argv.index("--agent") + 1] == "idle"


def test_the_launch_still_asks_for_claude_otherwise(tmp_path):
    root = _project(tmp_path, WorkerManifest(name="w2", manager="lead"))
    argv, _env = _launch(root, "w2")
    assert argv[argv.index("--agent") + 1] == "claude"


def test_a_local_worker_is_not_handed_the_claude_login(tmp_path):
    # The credential would be unspendable inside and SB12 measured a sandbox's
    # environment readable from other sandboxes on the same machine.
    root = _project(tmp_path, LOCAL)
    argv, env = _launch(root, "w1", env={CLAUDE_TOKEN_ENV_VAR: "secret-token"})
    assert CLAUDE_TOKEN_ENV_VAR not in env
    # Nor by the other road: it must not reach `--env` either.
    assert not any("secret-token" in a for a in argv)


def test_a_claude_worker_still_is(tmp_path):
    root = _project(tmp_path, WorkerManifest(name="w2", manager="lead"))
    _argv, env = _launch(root, "w2", env={CLAUDE_TOKEN_ENV_VAR: "secret-token"})
    assert env[CLAUDE_TOKEN_ENV_VAR] == "secret-token"
