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

⚠ **BUT AN UNMAPPED AGENT IS NOT REFUSED, and getting that wrong is the
history of this module.** A first version refused one, on the premise that
without the agent-specific env the window is not enforced. That premise is
false: `context_window.pin_window` gives "a model that is served with exactly
`window` tokens, whatever the server's default" — the pin goes INTO THE MODEL
on the server, so Ollama serves that window to ANY client. The env only tells
the agent the NUMBER, so it can compact before the limit rather than hit it.

So the window is enforced for everyone, and what an entry here buys is the
agent being TOLD. Missing that is a degradation worth saying out loud
(`not_told`), not a refusal — refusing would have turned away a Manager that
works. S33 is the rule that does refuse, and it refuses the thing that
genuinely is unenforceable: a local Manager declaring no window at all.

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


def not_told(agent: str, window: int) -> str:
    """What is lost when `agent` has no entry here — said, never refused.

    The window IS served to it: the pin is in the model, server-side. What it
    does not get is being told the number, so it cannot compact before the
    limit and will meet it instead. A person reading `rite start` should know
    which of those two they have, and the next person extending this should be
    told what to measure rather than only that something is absent.
    """
    known = ", ".join(enforced_agents())
    return (
        f"the {window}-token window is pinned into the model, so the server "
        f"serves it to any client — but rite has no way to TELL agent "
        f"{agent!r} that number, so it cannot compact before the limit and "
        f"will run into it instead. Agents rite can tell: {known}. To add "
        f"one, measure that it reads a window from its environment, then add "
        f"an entry to rite_ai.local.enforcement"
    )
