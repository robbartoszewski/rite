"""A Worker declares its own engine, on the same terms a Manager does (OL3).

Workers were hardcoded to Claude: `WorkerManifest` had no engine at all and
`sandbox/__init__.py` built `--agent "claude"` as a literal. The v0.7.0 goal is
a Claude Worker and an Ollama Worker under ONE Manager, so the engine moves onto
the Worker — and the rule it obeys is the Manager's rule, enforced by the
Manager's own validator rather than a second copy that would drift.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rite_ai.config.managers import engine_shape_problem, parse_managers
from rite_ai.config.models import WorkerManifest, worker_decomposition_model
from rite_ai.config.parse import ParseError, parse_worker


def _worker(body: str):
    path = Path(tempfile.mkdtemp()) / "worker.yml"
    path.write_text(body)
    return parse_worker(path)


LOCAL = """
worker:
  name: w1
  manager: lead
  engine: local:small
  endpoint: http://localhost:11434
  model: qwen3.8:latest
  agent: goose
  context_window: 32768
"""


def test_a_worker_with_no_engine_is_still_claude():
    # Every manifest written before OL3 keeps working and keeps meaning Claude.
    parsed = _worker("worker:\n  name: w1\n  manager: lead\n")
    assert isinstance(parsed, WorkerManifest)
    assert parsed.engine == "claude"
    assert parsed.is_local is False
    assert (parsed.endpoint, parsed.model, parsed.agent) == ("", "", "")
    assert parsed.context_window == 0


def test_a_local_worker_carries_all_five():
    parsed = _worker(LOCAL)
    assert isinstance(parsed, WorkerManifest), parsed
    assert parsed.engine == "local:small"
    assert parsed.endpoint == "http://localhost:11434"
    assert parsed.model == "qwen3.8:latest"
    assert parsed.agent == "goose"
    assert parsed.context_window == 32768
    assert parsed.is_local is True


def test_a_local_worker_must_say_endpoint_model_and_agent():
    # The class is a label, not a configuration — and the refusal names every
    # key that is missing, not just the first.
    parsed = _worker("worker:\n  name: w1\n  engine: local:small\n")
    assert isinstance(parsed, ParseError)
    for key in ("endpoint", "model", "agent"):
        assert key in parsed.message, parsed.message


def test_the_refusal_calls_it_a_worker():
    # The shared validator must not tell someone editing worker.yml about a
    # "manager" — the subject is the only thing that differs.
    parsed = _worker("worker:\n  name: w1\n  engine: local:small\n")
    assert isinstance(parsed, ParseError)
    assert "worker w1" in parsed.message
    assert "manager" not in parsed.message


def test_an_unknown_engine_is_refused():
    parsed = _worker("worker:\n  name: w1\n  engine: ollama\n")
    assert isinstance(parsed, ParseError)
    assert "ollama" in parsed.message


def test_engine_only_keys_mean_nothing_on_a_claude_worker():
    parsed = _worker("worker:\n  name: w1\n  endpoint: http://localhost:11434\n")
    assert isinstance(parsed, ParseError)
    assert "means nothing" in parsed.message


def test_a_claude_workers_model_must_be_a_claude_name():
    # `qwen3.8:latest` would reach `claude --model`, which cannot run it.
    parsed = _worker("worker:\n  name: w1\n  model: qwen3.8:latest\n")
    assert isinstance(parsed, ParseError)
    assert "Claude model name" in parsed.message
    assert isinstance(_worker("worker:\n  name: w1\n  model: sonnet\n"), WorkerManifest)


def test_a_window_is_refused_on_a_claude_worker():
    parsed = _worker("worker:\n  name: w1\n  context_window: 32768\n")
    assert isinstance(parsed, ParseError)
    assert "context_window means nothing" in parsed.message


def test_a_window_below_the_measured_floor_is_refused():
    # Ollama's 4096 default is the finding RL-T0 section 0 is built on: an
    # agent's own system prompt does not fit below the floor.
    parsed = _worker(LOCAL.replace("context_window: 32768", "context_window: 4096"))
    assert isinstance(parsed, ParseError)
    assert "4096" in parsed.message


def test_a_local_worker_plans_with_its_own_model():
    # Level 2 (RL-61). Before OL3 this returned Opus for every Worker, which an
    # Ollama endpoint has never heard of.
    parsed = _worker(LOCAL)
    assert worker_decomposition_model(parsed) == "qwen3.8:latest"


def test_a_local_workers_decomposer_model_is_not_claude_validated():
    body = LOCAL + "  decomposer:\n    model: qwen3:32b\n"
    parsed = _worker(body)
    assert isinstance(parsed, WorkerManifest), parsed
    assert worker_decomposition_model(parsed) == "qwen3:32b"


def test_the_shared_validator_still_says_manager_for_a_manager():
    # Regression on the extraction: the Manager's own messages are unchanged.
    problem = parse_managers([{"name": "lead", "engine": "local:small"}]).error
    assert "manager lead" in problem
    assert "worker" not in problem
    assert engine_shape_problem({"engine": "local:small"}, "manager lead").startswith(
        "manager lead:"
    )
