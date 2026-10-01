"""Which local agents rite can hold to a context window, and how (S35).

**The gap this closes.** Window enforcement was Goose-shaped in two places
that did not know about each other: `supervise._local_environment` ended in
`goose_environment(...)` unconditionally, and `config.managers.describe`
said a non-Goose local Manager gets "the server's default window". So a
Manager declaring `agent: opencode` started, was served Ollama's server-wide
default, and nothing refused it.

⚠ **That default is 4,096 tokens on an unconfigured Ollama, which is smaller
than the agents' own prompts** — RL-T0 measured opencode sending ~31 KB of
system prompt and tool schemas before the task is added, Goose ~19 KB, and
called that default "the finding that reframes every other number". A local
Manager running there does bad work and reports success, which is W19.

**So an agent rite cannot enforce is REFUSED, not run.** The alternative is a
silent 4,096, and SPEC §5.1.1's rule is that a safety property may fail closed
and never open. Adding an agent is one entry here plus the measurement behind
it, and the refusal names what is missing — so the next person extending this
is told what to go and measure rather than guessing.

⚠ **An entry is a claim that somebody RAN it.** `GOOSE` is here because
2026-09-28 measured the pin being what Ollama serves Goose and
`GOOSE_CONTEXT_LIMIT` being read by Goose 1.51 (per its binary). An entry
added without that is this module lying in a new place.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Enforcement:
    """How one agent is told the window it must stay inside."""

    agent: str
    environment: Callable[..., dict]
    """`(endpoint, model, context_limit) -> env`, the agent's own vocabulary."""
    measured_on: str
    """What was run to earn this entry. Prose, for a person deciding whether
    to trust it — never parsed."""


def _goose_environment(endpoint: str, model: str, context_limit: int) -> dict:
    from rite_ai.local.goose_agent import goose_environment

    return goose_environment(endpoint, model, context_limit=context_limit)


GOOSE = Enforcement(
    agent="goose",
    environment=_goose_environment,
    measured_on=(
        "Goose 1.51.0 against Ollama 0.34.2, 2026-09-28: the pin is what "
        "Ollama serves, and GOOSE_CONTEXT_LIMIT is read by the binary"
    ),
)

_BY_AGENT: dict[str, Enforcement] = {GOOSE.agent: GOOSE}
"""Every agent rite can hold to a window. ⚠ One entry per MEASUREMENT."""


def enforced_agents() -> tuple[str, ...]:
    return tuple(sorted(_BY_AGENT))


def for_agent(agent: str) -> Enforcement | None:
    """The enforcement for `agent`, or None — which callers must refuse on."""
    return _BY_AGENT.get(agent)


def refusal(agent: str) -> str:
    """Why a local Manager on `agent` will not be started.

    Names the measurement that is missing, because the next person here needs
    to know what to run, not only that something is absent.
    """
    known = ", ".join(enforced_agents())
    return (
        f"rite cannot hold agent {agent!r} to a context window, so it will not "
        f"start a local Manager on it: an unenforced window means Ollama's "
        f"server-wide default, which is 4,096 tokens unless the server was "
        f"configured otherwise — smaller than the agents' own prompts, so the "
        f"Manager would do bad work and report success (W19). Enforced today: "
        f"{known}. To add one, measure that the pinned window is what the "
        f"server serves it AND that it reads the window from its environment, "
        f"then add an entry to rite_ai.local.enforcement"
    )
