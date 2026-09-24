"""Is this `local:*` Manager's engine actually there? (RL-42)

RL-42 says a local Manager declares `endpoint`, `model` and `agent`, and that
`rite doctor` probes each — because `local:large` names a tier and not a
runtime, and two projects' "large" are different machines. Nothing probed.

**The failure this prevents is the one RL-47 describes from the other end.**
An unreachable endpoint or an unloaded model does not announce itself: the
Manager takes a subtask, the agent cannot reach anything, the subtask fails,
and the harness — correctly — records it as infrastructure rather than as
work. Repeat for every subtask in the decomposition. Everything reports
honestly and nothing progresses, and the first person to look sees a machine
that has been busy all night. One probe at setup answers it in a sentence.

**Three questions, and they fail differently.** They are reported separately
because the fixes are different and a merged "engine not working" sends
somebody to the wrong one:

- is the endpoint answering at all? (nothing listening, wrong port, wrong host)
- does it have the declared model? (a name typo, or a model never pulled)
- is the agent binary on this machine? (`opencode` not installed)
- **is it being served with enough context to work?** (B7, below)

**The fourth question was added because it cost a night.** An endpoint can be
up, serving the right model, with the agent installed, and the tier still
fails — because the model is being served with a context window smaller than
the agent's own system prompt. Measured 2026-09-24: Ollama serves every model
at 4096 tokens when `OLLAMA_CONTEXT_LENGTH` is unset, while opencode sends
~31KB of prompt and tool schemas before the task is added and Goose sends
~19KB.

**It does not present as a configuration fault.** It presents as models that
cannot call tools and agents that lose conversation history — a model
declaring tool support, accepting the request and returning empty content
with no error; a resumed session answering "that is not in the conversation
history". Four such results reversed at 32768 with nothing else changed. The
spike that found it spent most of a night attributing the symptoms to the
models and the tools. One line in `rite doctor` removes the whole class.

**The model list is a courtesy, not a contract.** An endpoint that answers but
does not serve `/v1/models` is not broken — it is simply not one rite can ask,
and that is reported as unknown rather than as absent. Guessing "the model is
missing" from a 404 would send somebody to re-pull a model they already have.

**Nothing here is on any hot path.** It runs from `rite doctor`, with a short
timeout, because a local endpoint that takes ten seconds to answer is already
the answer to the question being asked.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

PROBE_TIMEOUT_SECONDS = 5.0
"""Short on purpose. This is a machine on a LAN or on localhost; a local
engine that cannot answer in five seconds is not one a subtask should wait
behind."""

MINIMUM_CONTEXT_WINDOW = 32_768
"""The lowest window measured to work — NOT a tuned minimum, and the
difference matters.

Measured 2026-09-24 on `tools/rite_local_bench/tasks.py`: at **4096** Goose
scored 4/5 and opencode 0/5 with every task timing out; at **32768** both
scored 5/5. **Nothing in between was measured.** So this is the lowest
known-good value rather than a floor somebody derived, and a window between
the two may well be fine.

It is written this way on purpose: a threshold presented as a tuned minimum
invites somebody to shave it, and there is no evidence here to shave against.
If a smaller window is measured to work, lower this and say what was run."""


@dataclass(frozen=True)
class EngineProbe:
    """What was established about one `local:*` Manager's engine."""

    manager: str
    reachable: bool = False
    detail: str = ""
    models: tuple[str, ...] | None = None
    """What the endpoint says it serves, or None when it could not say —
    which is not the same as serving nothing."""
    model_present: bool | None = None
    """None when the endpoint could not be asked."""
    agent_installed: bool | None = None
    """None when the role declares no agent."""
    context_window: int | None = None
    """Tokens the model is actually being SERVED with, not what it supports.

    `None` when it could not be established, which is the common case and not
    a fault: the endpoint may not be Ollama, or the model may simply not be
    loaded right now. **Reported as unknown rather than guessed** — the same
    rule this module already applies to the model list."""
    context_detail: str = ""
    """Why `context_window` is None, when it is."""

    @property
    def problems(self) -> list[str]:
        """What a person has to act on, each naming its own fix."""
        found: list[str] = []
        if not self.reachable:
            found.append(
                f"manager {self.manager}: its engine endpoint is not answering "
                f"— {self.detail}. Until it does, every subtask routed here "
                "fails as infrastructure and nothing progresses"
            )
            return found
        if self.model_present is False:
            served = ", ".join(self.models or ()) or "nothing"
            found.append(
                f"manager {self.manager}: the endpoint is up but does not "
                f"serve its declared model (it serves: {served})"
            )
        if self.agent_installed is False:
            found.append(
                f"manager {self.manager}: its declared agent is not on this "
                "machine's PATH, so nothing can drive the model"
            )
        if (
            self.context_window is not None
            and self.context_window < MINIMUM_CONTEXT_WINDOW
        ):
            found.append(
                f"manager {self.manager}: its model is being served with a "
                f"{self.context_window}-token context window, below the "
                f"{MINIMUM_CONTEXT_WINDOW} measured to work. An agent's own "
                "system prompt and tool schemas are larger than this, so the "
                "task never fits — it looks like a model that cannot call "
                "tools or an agent that loses history, not like a setting. "
                "Fix: set OLLAMA_CONTEXT_LENGTH (or the equivalent) and "
                "restart the server"
            )
        return found


def _models_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base + "/models" if base.endswith("/v1") else base + "/v1/models"


def _running_url(endpoint: str) -> str:
    """Ollama's native "what is loaded" endpoint.

    Deliberately native rather than OpenAI-compatible: **the served context
    window is not in the OpenAI API at all**, so there is nothing
    provider-neutral to ask. An endpoint that is not Ollama answers 404 and
    the window is reported unknown, which is the honest answer rather than a
    failure."""
    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base + "/api/ps"


def _served_window(endpoint: str, model: str, get) -> tuple[int | None, str]:
    """The window `model` is being served with, or None and why not.

    ⚠ **Only a LOADED model can answer this.** Ollama reports
    `context_length` per running model; a model nothing has called yet is not
    in the list. That is reported as unknown rather than as a problem — a
    warning about a model that is merely idle would be noise, and noise in a
    doctor report is how people learn to scroll past it.
    """
    if not model:
        return None, "the role declares no model"
    try:
        response = get(_running_url(endpoint))
    except Exception as e:  # noqa: BLE001 - any failure is "cannot be asked"
        return None, f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    if getattr(response, "status_code", 0) != 200:
        return None, "the endpoint does not report what it has loaded"
    try:
        running = response.json().get("models", [])
    except Exception:  # noqa: BLE001
        return None, "its loaded-model list could not be read"
    for entry in running:
        names = {str(entry.get("name", "")), str(entry.get("model", ""))}
        if model in names or any(n.startswith(model + "-") for n in names if n):
            window = entry.get("context_length")
            if isinstance(window, int) and window > 0:
                return window, ""
            return None, "it is loaded but does not report a context length"
    return None, "the model is not loaded right now, so its window is not set yet"


def probe_engine(role, *, get=None, which=None) -> EngineProbe:
    """Probe one role's engine. Returns, never raises.

    `get` and `which` are injected so this is testable without a model on the
    machine and without a network — the point of the probe is to report a
    machine that is NOT set up, and a test that needs one set up could not
    cover the case that matters.
    """
    if which is None:
        which = shutil.which
    agent_installed = bool(which(role.agent)) if role.agent else None

    if not role.endpoint:
        return EngineProbe(
            role.name,
            reachable=False,
            detail="no endpoint is declared",
            agent_installed=agent_installed,
        )

    if get is None:
        import httpx

        def get(url: str):
            return httpx.get(url, timeout=PROBE_TIMEOUT_SECONDS)

    try:
        response = get(_models_url(role.endpoint))
    except Exception as e:  # noqa: BLE001 - every client raises its own family
        # The endpoint is the thing being tested, so any failure to reach it
        # is a result rather than an error. Naming the exception TYPE as well
        # as its text because "" is a real httpx message for some refusals.
        return EngineProbe(
            role.name,
            reachable=False,
            detail=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__,
            agent_installed=agent_installed,
        )

    status = getattr(response, "status_code", 0)
    if status >= 500:
        return EngineProbe(
            role.name,
            reachable=False,
            detail=f"it answered {status}",
            agent_installed=agent_installed,
        )
    if status != 200:
        # It ANSWERED, so something is listening — which is the question
        # `reachable` asks. It just cannot be asked what it serves.
        return EngineProbe(
            role.name,
            reachable=True,
            detail=f"answering, but /v1/models returned {status}",
            agent_installed=agent_installed,
        )

    try:
        body = response.json()
        served = tuple(
            str(entry.get("id", ""))
            for entry in body.get("data", [])
            if entry.get("id")
        )
    except Exception:  # noqa: BLE001 - a body that is not the shape we expect
        return EngineProbe(
            role.name,
            reachable=True,
            detail="answering, but its model list could not be read",
            agent_installed=agent_installed,
        )

    window, why = _served_window(role.endpoint, role.model, get)
    return EngineProbe(
        role.name,
        reachable=True,
        detail=f"answering, {len(served)} model(s)",
        models=served,
        context_window=window,
        context_detail=why,
        # Ollama reports `qwen3:8b` and some servers report `qwen3:8b-instruct`
        # for the same pull, so a prefix match rather than equality — a false
        # "missing" sends somebody to re-pull a model they have.
        model_present=any(
            name == role.model or name.startswith(role.model + "-") for name in served
        )
        if role.model
        else None,
        agent_installed=agent_installed,
    )


def probe_local_engines(roles, *, get=None, which=None) -> list[EngineProbe]:
    """Every `local:*` role in the project. `claude` and `human` have nothing
    to probe — one is a session and the other is a person."""
    return [probe_engine(r, get=get, which=which) for r in roles if r.is_local]
