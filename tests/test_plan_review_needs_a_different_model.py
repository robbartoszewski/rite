"""RL-6's independence is about the MODEL, not the class label (Robert, 2026-10-02).

The check was `r.engine != decomposer.engine` — a comparison of class LABELS.
`local:large` and `local:small` are two strings and may be one model, so a
project could satisfy plan review with a reviewer that shares every blind spot
of the plan's author: the one thing plan review exists to prevent.

⚠ Config-breaking, accepted and intended: a project that satisfied the old check
with two labels on one model is now refused.
"""

from __future__ import annotations

from rite_ai.config.managers import (
    ManagerRole,
    engine_identity,
    model_identity,
    parse_managers,
)


def _role(name, engine, model="", **kw):
    return ManagerRole(
        name=name,
        engine=engine,
        model=model,
        endpoint=kw.pop(
            "endpoint", "http://localhost:11434" if "local" in engine else ""
        ),
        agent=kw.pop("agent", "goose" if "local" in engine else ""),
        context_window=kw.pop("context_window", 32768 if "local" in engine else 0),
        **kw,
    )


# ── the pinned copy is the same model ────────────────────────────────────────


def test_rites_own_pin_is_not_a_second_model():
    # context_window.derived_name makes this copy; reading it as a different
    # engine would reopen the loophole under a new name.
    assert model_identity("rite-ctx32768-qwen3-32b") == model_identity("qwen3:32b")


def test_the_tag_separator_does_not_make_two_models():
    assert model_identity("qwen3.8:latest") == model_identity(
        "rite-ctx32768-qwen3.8-latest"
    )


def test_case_does_not_make_two_models():
    assert model_identity("QWEN3:32B") == model_identity("qwen3:32b")


def test_different_models_stay_different():
    assert model_identity("qwen3:32b") != model_identity("qwen3:8b")


# ── identity ─────────────────────────────────────────────────────────────────


def test_two_local_classes_on_one_model_share_an_identity():
    big = _role("large", "local:large", "qwen3.8:latest")
    small = _role("small", "local:small", "qwen3.8:latest")
    assert engine_identity(big) == engine_identity(small)


def test_the_endpoint_is_not_part_of_identity():
    # What shares a blind spot is the weights, not the port.
    a = _role("a", "local:a", "qwen3.8:latest", endpoint="http://localhost:11434")
    b = _role("b", "local:b", "qwen3.8:latest", endpoint="http://127.0.0.1:9999")
    assert engine_identity(a) == engine_identity(b)


def test_local_and_claude_are_independent():
    assert engine_identity(_role("l", "local:s", "qwen3:8b")) != engine_identity(
        _role("c", "claude")
    )


def test_two_claude_managers_are_still_not_independent():
    # Unchanged behaviour: claude identity ignores the model, as before.
    assert engine_identity(_role("a", "claude", "opus")) == engine_identity(
        _role("b", "claude", "sonnet")
    )


# ── the gate ─────────────────────────────────────────────────────────────────

LOCAL = {
    "endpoint": "http://localhost:11434",
    "agent": "goose",
    "context_window": 32768,
}


def _problems(entries):
    from rite_ai.config.managers import configuration_problems

    parsed = parse_managers(entries)
    assert not parsed.error, parsed.error
    return configuration_problems(parsed.roles)


def test_same_model_under_two_labels_is_refused():
    problems = _problems(
        [
            {
                "name": "big",
                "engine": "local:large",
                "model": "qwen3.8:latest",
                "duties": ["decompose"],
                **LOCAL,
            },
            {
                "name": "small",
                "engine": "local:small",
                "model": "qwen3.8:latest",
                "duties": ["plan-review"],
                **LOCAL,
            },
        ]
    )
    assert any("different model" in p for p in problems), problems


def test_different_models_under_two_labels_pass_the_independence_check():
    problems = _problems(
        [
            {
                "name": "big",
                "engine": "local:large",
                "model": "qwen3:32b",
                "duties": ["decompose"],
                **LOCAL,
            },
            {
                "name": "small",
                "engine": "local:small",
                "model": "qwen3:8b",
                "duties": ["plan-review"],
                **LOCAL,
            },
        ]
    )
    assert not any("different model" in p for p in problems), problems


def test_a_claude_reviewer_still_satisfies_a_local_decomposer():
    problems = _problems(
        [
            {
                "name": "gpu",
                "engine": "local:small",
                "model": "qwen3:8b",
                "duties": ["decompose"],
                **LOCAL,
            },
            {"name": "lead", "engine": "claude", "duties": ["plan-review"]},
        ]
    )
    assert not any("different model" in p for p in problems), problems
