"""S33: a local Manager with no declared context window is refused, whatever
its agent.

`f340103` made `rite start` refuse, and `rite doctor` warn about, a local
Manager that declares no `context_window`, but only when `agent == "goose"`:
Goose was the only local agent then. Nothing about the rule is Goose's. A
model served by an endpoint takes that server's default window unless rite
sets one; the default cannot be read before the model loads; and a prompt over
it is cut from the front with no error (S34: 4096 on Robert's Mac, `truncating
input prompt limit=2050 prompt=5583`). Gated on the agent, the next local agent
would have started on exactly that default, silently, and `rite doctor` said
of it only "the server's default window".

The invariant runs every agent name a role may carry (the parser takes any)
against a declared window and an undeclared one, through all three places that
decide: `rite doctor`'s probe, `effective_model` (what doctor and start print),
and the start path's refusal. A Claude and a human Manager are the controls.
"""

from __future__ import annotations

import itertools

import pytest
import yaml

from rite_ai.config.managers import (
    ManagerRole,
    effective_model,
    parse_managers,
    window_undeclared,
)
from rite_ai.local.engine_probe import MINIMUM_CONTEXT_WINDOW, probe_engine
from rite_ai.managers import supervise

AGENTS = ("goose", "opencode", "aider", "cursor", "some-future-agent")
WINDOWS = {"declared": MINIMUM_CONTEXT_WINDOW, "larger": 131072, "undeclared": 0}
UNDECLARED = "declares no context_window"


def _role(agent: str, window: int, name: str = "helper") -> ManagerRole:
    return ManagerRole(
        name=name,
        engine="local:small",
        preset="executor",
        endpoint="http://localhost:11434/v1",
        model="qwen3:8b",
        agent=agent,
        context_window=window,
    )


def _serving(url: str):
    class R:
        status_code = 200

        def json(self):
            if url.endswith("/v1/models"):
                return {"data": [{"id": "qwen3:8b"}]}
            return {"models": []}

    return R()


def _installed(binary: str) -> str:
    return f"/usr/local/bin/{binary}"


def _project(tmp_path, role: ManagerRole):
    root = tmp_path / "p"
    (root / ".rite").mkdir(parents=True)
    entry = {
        "name": role.name,
        "engine": role.engine,
        "preset": role.preset,
        "endpoint": role.endpoint,
        "model": role.model,
        "agent": role.agent,
    }
    if role.context_window:
        entry["context_window"] = role.context_window
    (root / ".rite" / "config.yaml").write_text(
        yaml.safe_dump(
            {"coordination": {"managers": [role.name], "manager_roles": [entry]}}
        )
    )
    return root


@pytest.mark.parametrize(
    "agent,window", list(itertools.product(AGENTS, WINDOWS)), ids="-".join
)
def test_undeclared_is_refused_and_said_exactly_when_undeclared_whatever_the_agent(
    tmp_path, agent, window
):
    role = _role(agent, WINDOWS[window])
    undeclared = window == "undeclared"

    assert window_undeclared(role) == undeclared

    # `rite doctor`'s probe.
    problems = probe_engine(role, get=_serving, which=_installed).problems
    assert any(UNDECLARED in p for p in problems) == undeclared, problems

    # What doctor and start print about the Manager.
    said = effective_model(role)
    assert ("refuses it" in said) == undeclared, said
    assert "the server's default window" not in said, said

    # The start path's own refusal.
    refusal = supervise._window_refusal(_project(tmp_path, role), role.name, agent)
    assert bool(refusal) == undeclared, refusal
    if undeclared:
        assert "context_window: 32768" in refusal, "it says the line to add"
        assert "Goose" not in refusal, "one sentence, whatever the agent"


@pytest.mark.parametrize("agent", [a for a in AGENTS if a != "goose"])
def test_the_window_is_refused_before_anything_asks_which_agent_it_is(tmp_path, agent):
    """`permission_placement` refuses an agent rite has no spelling for. Asked
    first, a Manager with no window would be told the wrong thing, so the
    window is asked first, on the real start path."""
    role = _role(agent, 0)
    result = supervise._default_starter(
        _project(tmp_path, role),
        role.name,
        engine=role.engine,
        resume_id="",
        max_sessions=1,
        window_seconds=60,
        permission="bypassPermissions",
        prompt="do the work",
        agent=agent,
    )
    assert not result.ok
    assert UNDECLARED in result.message, result.message


@pytest.mark.parametrize(
    "role",
    [
        ManagerRole(name="lead", preset="lead"),
        ManagerRole(name="person", engine="human", preset="pm"),
    ],
    ids=["claude", "human"],
)
def test_a_manager_that_is_not_local_is_untouched(tmp_path, role):
    assert not window_undeclared(role)
    assert "context_window" not in effective_model(role)
    assert supervise._window_refusal(tmp_path, role.name, "") == ""


@pytest.mark.parametrize("agent", AGENTS)
def test_too_small_is_refused_at_parse_whatever_the_agent(agent):
    """The other half, unchanged and already agent-independent: a declared
    window below the minimum never parses."""
    entry = {
        "name": "helper",
        "engine": "local:small",
        "preset": "executor",
        "endpoint": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "agent": agent,
        "context_window": MINIMUM_CONTEXT_WINDOW - 1,
    }
    assert "below" in parse_managers([entry]).error
