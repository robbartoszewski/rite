"""A local Manager's context window is a fact about its model, not a guess.

Ollama serves every model at one server-wide default unless the model's own
parameters set `num_ctx`, and that default cannot be read until the model is
loaded. So before this, the window a Goose Manager ran with was usually
unknown when it started, and an unknown window let it start. Goose was not
told the window either (`GOOSE_CONTEXT_LIMIT`). In the v0.6.0 dogfood run, a
`qwen3.8` Manager on a 32k window spent 45 minutes on detours, filled the
window, and ended without replying.

Now a local Manager declares `context_window`; rite pins it into the model it
runs (`local.context_window.pin_window`), reads the pin back rather than
trusting `ollama create`, and tells Goose. An undeclared window refuses to
start (`test_a_local_manager_runs_its_declared_model.py`).
"""

from __future__ import annotations

import re

import pytest

from rite_ai.config.managers import parse_managers
from rite_ai.local.context_window import pin_window, pinned_window

ENDPOINT = "http://gpu.local:11434/v1"


class Ollama:
    """Models and their `parameters` text, served as `/api/show` does, and an
    `ollama create` that really reads the Modelfile it is given."""

    def __init__(self, models: dict[str, str] | None = None, *, lie: int = 0):
        self.models = dict(models or {})
        self.created: list[tuple[str, str]] = []
        self.lie = lie
        """When set, `create` exits 0 but pins this instead of what was asked."""

    def post(self, url, body):
        assert url == "http://gpu.local:11434/api/show", url
        name = body["model"]

        class R:
            pass

        r = R()
        if name in self.models:
            r.status_code = 200
            r.json = lambda: {"parameters": self.models[name]}
        else:
            r.status_code = 404
            r.json = lambda: {"error": "not found"}
        return r

    def run(self, argv, **kwargs):
        assert argv[:2] == ["ollama", "create"], argv
        name, path = argv[2], argv[4]
        self.created.append((name, kwargs["env"]["OLLAMA_HOST"]))
        text = open(path).read()
        base = re.search(r"^FROM (\S+)$", text, re.M).group(1)
        window = int(re.search(r"^PARAMETER num_ctx (\d+)$", text, re.M).group(1))

        class Done:
            returncode = 0 if base in self.models else 1
            stdout = ""
            stderr = "" if base in self.models else f"model {base} not found"

        if base in self.models:
            self.models[name] = f"num_ctx {self.lie or window}\nstop <|im_end|>"
        return Done()


def _pin(ollama: Ollama, model: str, window: int):
    return pin_window(ENDPOINT, model, window, post=ollama.post, run=ollama.run)


def test_a_model_that_already_pins_the_window_is_used_as_it_is():
    ollama = Ollama({"qwen3-32b-ctx32k": "num_ctx 32768\nstop <|im_end|>"})

    got = _pin(ollama, "qwen3-32b-ctx32k", 32768)

    assert got.model == "qwen3-32b-ctx32k"
    assert not got.problem and not got.created
    assert ollama.created == []


def test_an_unpinned_model_gets_a_twin_that_pins_it_and_says_so():
    ollama = Ollama({"qwen3.8:latest": "stop <|im_end|>"})

    got = _pin(ollama, "qwen3.8:latest", 65536)

    assert got.model == "rite-ctx65536-qwen3.8-latest"
    assert got.created and not got.problem
    assert pinned_window(ENDPOINT, got.model, post=ollama.post) == (65536, "")
    assert "ollama rm rite-ctx65536-qwen3.8-latest" in got.detail


def test_the_twin_is_made_on_the_endpoints_server_not_localhost():
    ollama = Ollama({"qwen3:8b": ""})
    _pin(ollama, "qwen3:8b", 32768)
    assert ollama.created == [("rite-ctx32768-qwen3-8b", "http://gpu.local:11434")]


def test_an_existing_twin_is_reused_not_recreated():
    ollama = Ollama({"qwen3:8b": "", "rite-ctx32768-qwen3-8b": "num_ctx 32768"})

    got = _pin(ollama, "qwen3:8b", 32768)

    assert got.model == "rite-ctx32768-qwen3-8b"
    assert not got.created and ollama.created == []


def test_a_create_that_exits_0_with_the_wrong_window_is_refused():
    """The pin is READ BACK, not assumed from the exit status."""
    ollama = Ollama({"qwen3:8b": ""}, lie=4096)

    got = _pin(ollama, "qwen3:8b", 32768)

    assert got.problem and "reads back as 4096" in got.problem


def test_a_twin_pinning_another_window_is_refused_not_overwritten():
    ollama = Ollama({"qwen3:8b": "", "rite-ctx32768-qwen3-8b": "num_ctx 8192"})

    got = _pin(ollama, "qwen3:8b", 32768)

    assert got.problem and "ollama rm rite-ctx32768-qwen3-8b" in got.problem
    assert ollama.created == []


def test_a_model_the_server_does_not_have_is_refused():
    got = _pin(Ollama({}), "qwen3.8:latest", 32768)
    assert got.problem and "not found" in got.problem


class TestTheDeclaration:
    def _role(self, **extra):
        entry = {
            "name": "small",
            "engine": "local:small",
            "preset": "lead",
            "endpoint": ENDPOINT,
            "model": "qwen3.8:latest",
            "agent": "goose",
            **extra,
        }
        return parse_managers([entry])

    def test_a_window_is_read_as_tokens(self):
        assert self._role(context_window=65536).roles[0].context_window == 65536

    @pytest.mark.parametrize(
        ("window", "said"),
        [
            (4096, "below 32768"),
            ("64k", "whole number"),
            (True, "whole number"),
        ],
    )
    def test_a_window_rite_cannot_use_is_refused_where_it_is_declared(
        self, window, said
    ):
        assert said in self._role(context_window=window).error

    def test_a_window_on_a_claude_manager_means_nothing_and_says_so(self):
        got = parse_managers([{"name": "c", "context_window": 65536}])
        assert "only 'local:<class>'" in got.error


class TestAClaudeManagersModel:
    def test_it_is_accepted_and_reaches_the_command_line_quoted(self):
        from rite_ai.managers.supervise import launch_command

        role = parse_managers([{"name": "c", "model": "claude-opus-5-5[1m]"}])
        assert role.roles[0].model == "claude-opus-5-5[1m]"
        assert "--model 'claude-opus-5-5[1m]'" in launch_command(
            "claude", "", "/p", "", model=role.roles[0].model
        )

    def test_without_one_the_command_line_is_unchanged(self):
        from rite_ai.managers.supervise import launch_command

        assert launch_command("claude", "", "/p", "") == "claude -p < /p"

    @pytest.mark.parametrize(
        "model", ["a b", "x;rm -rf /", "$(id)", "qwen3:8b", "gpt-5", ""]
    )
    def test_a_name_that_is_not_a_model_is_refused(self, model):
        got = parse_managers([{"name": "c", "model": model}])
        if model:
            assert "is not a Claude model name" in got.error
        else:
            assert got.roles[0].model == ""

    def test_a_goose_manager_never_gets_a_model_flag(self):
        from rite_ai.managers.supervise import launch_command

        with pytest.raises(ValueError, match="not a --model flag"):
            launch_command("local:small", "", "/p", "", "goose", "s1", model="m")


class TestWhatAManagerRunsIsSaid:
    """Track MS: "inspectable, never silent". `rite doctor` and `rite start`
    print this one line, from one function."""

    def _line(self, entry):
        from rite_ai.config.managers import effective_model

        return effective_model(parse_managers([entry]).roles[0])

    def test_a_claude_manager_with_no_model_says_whose_default_it_runs(self):
        line = self._line({"name": "lead"})
        assert "Claude Code's default model for this login" in line
        assert "none declared" in line

    def test_a_declared_claude_model_is_named_with_its_source(self):
        line = self._line({"name": "lead", "model": "opus"})
        assert line == "manager lead: opus (declared in its role)"

    def test_a_local_manager_names_its_model_endpoint_and_window(self):
        line = self._line(
            {
                "name": "helper",
                "engine": "local:small",
                "preset": "executor",
                "endpoint": ENDPOINT,
                "model": "qwen3.8:latest",
                "agent": "goose",
                "context_window": 65536,
            }
        )
        assert "qwen3.8:latest at http://gpu.local:11434/v1" in line
        assert "65536-token window pinned" in line

    def test_a_goose_manager_without_a_window_is_told_it_will_not_start(self):
        line = self._line(
            {
                "name": "helper",
                "engine": "local:small",
                "preset": "executor",
                "endpoint": ENDPOINT,
                "model": "qwen3:8b",
                "agent": "goose",
            }
        )
        assert "NO context_window declared" in line
        assert "refuses" in line
