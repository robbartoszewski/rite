"""`rite doctor` must say when a model is served with too little context (B7).

WHY THIS EXISTS. It cost a night. Measured 2026-09-24: Ollama serves every
model at 4096 tokens when `OLLAMA_CONTEXT_LENGTH` is unset, and an agent's
own system prompt is larger than that — opencode sends ~31KB before the task
is added, Goose ~19KB.

⚠ **THE SYMPTOM DOES NOT LOOK LIKE A SETTING.** It looks like a model that
declares tool support, accepts the request and returns empty content with no
error; and like a resumed session answering "that is not in the conversation
history". Four such results reversed at 32768 with nothing else changed. The
spike that found it spent most of a night blaming the models and the tools.

So the check is not "is the endpoint up" — three tests already cover that —
it is "is it up AND able to hold a task".
"""

from __future__ import annotations

from rite_ai.config.managers import ManagerRole
from rite_ai.local.engine_probe import MINIMUM_CONTEXT_WINDOW, probe_engine

ROLE = ManagerRole(
    name="planner",
    engine="local:large",
    preset="planner",
    endpoint="http://localhost:11434/v1",
    model="qwen3:32b",
    agent="goose",
)


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _endpoint(*, running, models=("qwen3:32b",), ps_status=200):
    """An endpoint that serves `models` and reports `running` as loaded."""

    def get(url: str):
        if url.endswith("/api/ps"):
            return FakeResponse(status_code=ps_status, body={"models": running})
        return FakeResponse(body={"data": [{"id": m} for m in models]})

    return get


def _loaded(name="qwen3:32b", window=4096):
    return [{"name": name, "model": name, "context_length": window}]


def test_a_window_below_the_measured_floor_is_a_problem():
    probe = probe_engine(
        ROLE, get=_endpoint(running=_loaded(window=4096)), which=lambda _b: "/bin/goose"
    )
    assert probe.reachable, "the endpoint is up; this is not an endpoint failure"
    assert probe.context_window == 4096
    problems = probe.problems
    assert any("4096" in p for p in problems), (
        f"the served window is not named, so nobody can see it: {problems}"
    )
    assert any("OLLAMA_CONTEXT_LENGTH" in p for p in problems), (
        f"the fix is not named, so the report is a symptom not an action: {problems}"
    )


def test_a_sufficient_window_produces_no_complaint():
    """The other half of the observation. A warning that never clears is a
    warning people learn to scroll past."""
    probe = probe_engine(
        ROLE,
        get=_endpoint(running=_loaded(window=MINIMUM_CONTEXT_WINDOW)),
        which=lambda _b: "/bin/goose",
    )
    assert probe.context_window == MINIMUM_CONTEXT_WINDOW
    assert not any("context window" in p for p in probe.problems), probe.problems


def test_a_model_that_is_not_loaded_is_unknown_rather_than_wrong():
    """⚠ Ollama reports a window only for a RUNNING model. Warning about an
    idle one would be noise, and noise is how a doctor report stops being
    read."""
    probe = probe_engine(ROLE, get=_endpoint(running=[]), which=lambda _b: "/bin/goose")
    assert probe.context_window is None
    assert not any("context window" in p for p in probe.problems), probe.problems
    assert "not loaded" in probe.context_detail, probe.context_detail


def test_an_endpoint_that_is_not_ollama_is_unknown_rather_than_wrong():
    """The served window is not in the OpenAI API, so there is nothing
    provider-neutral to ask. LM Studio, llama.cpp and vLLM answer 404 here."""
    probe = probe_engine(
        ROLE,
        get=_endpoint(running=[], ps_status=404),
        which=lambda _b: "/bin/goose",
    )
    assert probe.context_window is None
    assert not any("context window" in p for p in probe.problems), probe.problems


def test_the_window_check_does_not_mask_a_dead_endpoint():
    """An endpoint that is not answering must still report THAT, first. The
    window question only arises once something is listening."""

    def refuse(_url):
        raise ConnectionError("connection refused")

    probe = probe_engine(ROLE, get=refuse, which=lambda _b: "/bin/goose")
    assert not probe.reachable
    assert probe.context_window is None
    assert any("not answering" in p for p in probe.problems), probe.problems
