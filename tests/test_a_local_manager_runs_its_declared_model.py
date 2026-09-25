"""A local Manager runs the model and endpoint it declares.

⚠ Measured 2026-09-25: a Manager declared with `model: qwen3:8b` ran
`qwen3-vl:8b-instruct`. Only the Worker's Goose path set GOOSE_MODEL, so a
Manager's Goose silently used the operator's global config. `rite doctor`
probed the declared model, so everything reported healthy while a different
model did the work.
"""

from __future__ import annotations

import rite_ai.managers.supervise as sup
from rite_ai.local.goose_agent import goose_environment
from rite_ai.managers.session import ALLOWED_ON_TMUX_ARGV


def _project(tmp_path, endpoint="http://localhost:11434/v1", model="qwen3:8b"):
    (tmp_path / ".rite").mkdir(exist_ok=True)
    (tmp_path / ".rite" / "config.yaml").write_text(
        "coordination:\n  managers: [small]\n  manager_roles:\n"
        f"  - {{name: small, engine: 'local:small', preset: lead, "
        f"endpoint: '{endpoint}', model: '{model}', agent: goose}}\n"
    )
    return tmp_path


def _launch(tmp_path, monkeypatch, agent="goose", engine="local:small"):
    seen: dict = {}

    def fake_start(root, manager, **kwargs):
        seen.update(kwargs)
        from rite_ai.managers.session import StartResult

        return StartResult(False, "not starting anything in a test")

    monkeypatch.setattr(sup, "start_session", fake_start)
    result = sup._default_starter(
        tmp_path,
        "small",
        engine=engine,
        resume_id="",
        max_sessions=1,
        window_seconds=0,
        permission="",
        prompt="go",
        agent=agent,
    )
    return result, seen


class TestTheDeclaredModelReachesTheEngine:
    def test_the_pane_is_given_the_roles_model_and_endpoint(
        self, tmp_path, monkeypatch
    ):
        _, seen = _launch(_project(tmp_path), monkeypatch)
        env = seen["pane_env"]
        assert env["GOOSE_MODEL"] == "qwen3:8b"
        assert env["GOOSE_PROVIDER"] == "ollama"
        assert env["OLLAMA_HOST"] == "http://localhost:11434"

    def test_the_worker_and_the_manager_use_one_definition(self):
        """So the two Goose paths cannot come to disagree again."""
        got = goose_environment("http://gpu.local:8000/v1", "m")
        assert got == {
            "GOOSE_PROVIDER": "ollama",
            "GOOSE_MODEL": "m",
            "OLLAMA_HOST": "http://gpu.local:8000",
        }

    def test_every_name_it_sets_may_travel_on_tmux_argv(self):
        assert set(goose_environment("http://h/v1", "m")) <= ALLOWED_ON_TMUX_ARGV

    def test_a_claude_manager_is_unchanged(self, tmp_path, monkeypatch):
        _, seen = _launch(tmp_path, monkeypatch, agent="", engine="claude")
        assert "GOOSE_MODEL" not in seen["pane_env"]


class TestItRefusesRatherThanGuess:
    def test_an_endpoint_carrying_credentials_is_refused(self, tmp_path, monkeypatch):
        """These values go on tmux's argv, where `ps` shows them."""
        project = _project(tmp_path, endpoint="http://me:s3cret@gpu.local/v1")
        result, seen = _launch(project, monkeypatch)
        assert not result.ok and "credential" in result.message
        assert not seen, "nothing was launched"

    def test_a_goose_manager_with_no_declared_role_is_refused(
        self, tmp_path, monkeypatch
    ):
        result, seen = _launch(tmp_path, monkeypatch)
        assert not result.ok and "global goose config" in result.message
        assert not seen
