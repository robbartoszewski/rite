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
        return found


def _models_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    return base + "/models" if base.endswith("/v1") else base + "/v1/models"


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

    return EngineProbe(
        role.name,
        reachable=True,
        detail=f"answering, {len(served)} model(s)",
        models=served,
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
