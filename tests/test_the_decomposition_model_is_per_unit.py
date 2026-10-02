"""Level 2 — the decomposition model is an attribute of the executing unit, and
defaults by that unit's type (DD-0/DD-1.5/DD-1.7, RL-61).

The ONE new attribute the per-worker placement forces. A Claude unit plans with
Opus and implements with its Claude model; a GPU unit uses one model for both.
Absent, the common case stays empty and the default is computed, not stored —
so no model name lives in two files (the S35 shape this avoids).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from rite_ai.config.managers import (
    CLAUDE_DECOMPOSER_DEFAULT,
    ManagerRole,
    decomposition_model_for,
    parse_managers,
)
from rite_ai.config.models import WorkerManifest, worker_decomposition_model
from rite_ai.config.parse import ParseError, parse_worker


def _worker(body: str):
    path = Path(tempfile.mkdtemp()) / "worker.yml"
    path.write_text(body)
    return parse_worker(path)


def test_a_claude_unit_defaults_to_opus():
    # Robert's default: plan with Opus, implement with the Claude model.
    assert decomposition_model_for(ManagerRole(name="lead", engine="claude")) == "opus"
    assert CLAUDE_DECOMPOSER_DEFAULT == "opus"


def test_a_local_unit_defaults_to_its_own_model():
    # One GPU, nothing to gain from a second model.
    role = ManagerRole(
        name="small",
        engine="local:small",
        endpoint="http://localhost:11434/v1",
        model="qwen3.8:latest",
        agent="goose",
        context_window=32768,
        duties=("execute",),
    )
    assert decomposition_model_for(role) == "qwen3.8:latest"


def test_an_explicit_decomposer_model_overrides_the_default():
    parsed = parse_managers(
        [
            {
                "name": "lead",
                "engine": "claude",
                "preset": "lead",
                "decomposer": {"model": "claude-opus-4-5"},
            }
        ]
    )
    assert parsed.error == ""
    assert decomposition_model_for(parsed.roles[0]) == "claude-opus-4-5"


def test_a_claude_decomposer_model_must_be_a_claude_name():
    # It reaches `claude --model`, and travels on tmux's argv.
    parsed = parse_managers(
        [
            {
                "name": "lead",
                "engine": "claude",
                "preset": "lead",
                "decomposer": {"model": "qwen3.8:latest"},
            }
        ]
    )
    assert "decomposer.model" in parsed.error
    assert "Claude model name" in parsed.error


def test_a_local_decomposer_model_is_free():
    parsed = parse_managers(
        [
            {
                "name": "small",
                "engine": "local:small",
                "endpoint": "http://x/v1",
                "model": "qwen3.8:latest",
                "agent": "goose",
                "context_window": 32768,
                "duties": ["execute"],
                "decomposer": {"model": "qwen3.8:latest"},
            }
        ]
    )
    assert parsed.error == ""
    assert parsed.roles[0].decomposer.model == "qwen3.8:latest"


def test_an_unknown_key_inside_decomposer_is_refused():
    parsed = parse_managers(
        [
            {
                "name": "small",
                "engine": "local:small",
                "endpoint": "http://x/v1",
                "model": "m",
                "agent": "goose",
                "context_window": 32768,
                "decomposer": {"modle": "x"},
            }
        ]
    )
    assert "decomposer knows only 'model'" in parsed.error


def test_a_worker_defaults_to_opus_and_can_name_its_own():
    nodec = _worker("worker:\n  name: w1\n  manager: lead\n")
    assert isinstance(nodec, WorkerManifest)
    assert worker_decomposition_model(nodec) == "opus"

    named = _worker(
        "worker:\n  name: w2\n  manager: lead\n"
        "  decomposer:\n    model: claude-opus-4-5\n"
    )
    assert isinstance(named, WorkerManifest)
    assert worker_decomposition_model(named) == "claude-opus-4-5"


def test_a_worker_decomposer_model_must_be_a_claude_name():
    # A Worker is a Claude sandbox today, so its decomposition model is Claude's.
    bad = _worker("worker:\n  name: w3\n  decomposer:\n    model: qwen3.8:latest\n")
    assert isinstance(bad, ParseError)
    assert "worker.decomposer.model" in bad.message
