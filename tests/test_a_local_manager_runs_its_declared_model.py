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


def _project(
    tmp_path, endpoint="http://localhost:11434/v1", model="qwen3:8b", window=32768
):
    (tmp_path / ".rite").mkdir(exist_ok=True)
    declared = f", context_window: {window}" if window else ""
    (tmp_path / ".rite" / "config.yaml").write_text(
        "coordination:\n  managers: [small]\n  manager_roles:\n"
        f"  - {{name: small, engine: 'local:small', preset: lead, "
        f"endpoint: '{endpoint}', model: '{model}', agent: goose{declared}}}\n"
    )
    return tmp_path


def _launch(tmp_path, monkeypatch, agent="goose", engine="local:small"):
    seen: dict = {}
    import rite_ai.local.context_window as cw

    # The pin talks to Ollama; answered here, as the model the pin would
    # produce (`derived_name`), so what reaches the pane is checked by name.
    monkeypatch.setattr(
        cw,
        "pin_window",
        lambda endpoint, model, window: cw.Ensured(cw.derived_name(model, window)),
    )

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
        # The declared model, with the declared window pinned into it.
        assert env["GOOSE_MODEL"] == "rite-ctx32768-qwen3-8b"
        assert env["GOOSE_CONTEXT_LIMIT"] == "32768"
        assert env["GOOSE_PROVIDER"] == "ollama"
        assert env["OLLAMA_HOST"] == "http://localhost:11434"

    def test_no_declared_window_refuses_rather_than_guessing(
        self, tmp_path, monkeypatch
    ):
        """Ollama's default window is server-wide and unreadable before the
        model loads, so "not declared" is "not known", and a Manager does not
        start on it."""
        result, seen = _launch(_project(tmp_path, window=0), monkeypatch)
        assert not result.ok
        assert "declares no context_window" in result.message
        assert "- name: " in result.message
        assert "\n      context_window: 32768\n" in result.message
        assert seen == {}, "a session was started without a known window"

    def test_the_worker_and_the_manager_use_one_definition(self):
        """So the two Goose paths cannot come to disagree again."""
        got = goose_environment("http://gpu.local:8000/v1", "m")
        assert got == {
            "GOOSE_PROVIDER": "ollama",
            "GOOSE_MODEL": "m",
            "OLLAMA_HOST": "http://gpu.local:8000",
        }

    def test_every_name_it_sets_may_travel_on_tmux_argv(self):
        names = set(goose_environment("http://h/v1", "m", context_limit=65536))
        assert "GOOSE_CONTEXT_LIMIT" in names
        assert names <= ALLOWED_ON_TMUX_ARGV

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
