"""Probing a `local:*` Manager's engine (RL-42).

The failure worth preventing is not "the probe is wrong" but the one RL-47
describes from the other end: an endpoint that is not there fails every
subtask routed to it as INFRASTRUCTURE, which is the honest answer, over and
over, all night, while nothing progresses. A person looking in the morning
sees a machine that has been busy.

So what these test is that the three questions stay separate — endpoint,
model, agent — because each has a different fix, and that "could not ask" is
never reported as "not there".
"""

from __future__ import annotations

import pytest

from rite_ai.config.managers import ManagerRole
from rite_ai.local.engine_probe import EngineProbe, probe_engine, probe_local_engines

ROLE = ManagerRole(
    name="planner",
    engine="local:large",
    preset="planner",
    endpoint="http://localhost:11434/v1",
    model="qwen3:70b",
    agent="opencode",
)


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def _serving(*names):
    return lambda _url: FakeResponse(body={"data": [{"id": n} for n in names]})


def _installed(_name):
    return "/usr/local/bin/opencode"


def _missing(_name):
    return None


# --- the endpoint -----------------------------------------------------------------


def test_a_reachable_engine_serving_the_model_has_no_problems():
    probe = probe_engine(ROLE, get=_serving("qwen3:70b"), which=_installed)
    assert probe.reachable and probe.model_present
    assert probe.problems == []


def test_nothing_listening_names_the_consequence_not_just_the_error():
    def refused(_url):
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    probe = probe_engine(ROLE, get=refused, which=_installed)

    assert not probe.reachable
    assert "Connection refused" in probe.detail
    (problem,) = probe.problems
    assert "fails as infrastructure and nothing progresses" in problem


def test_an_exception_with_no_message_still_says_what_happened():
    """`httpx` raises some refusals with an empty string, and "endpoint is not
    answering — " is a report with the useful half missing."""

    class Silent(Exception):
        pass

    probe = probe_engine(ROLE, get=lambda _u: (_ for _ in ()).throw(Silent()))
    assert "Silent" in probe.detail


def test_a_server_error_is_not_reachable():
    probe = probe_engine(ROLE, get=lambda _u: FakeResponse(500), which=_installed)
    assert not probe.reachable


def test_an_endpoint_that_answers_but_cannot_be_asked_is_still_up():
    """Not every OpenAI-compatible server serves `/v1/models`. Reporting the
    model as missing from a 404 sends somebody to re-pull one they have."""
    probe = probe_engine(ROLE, get=lambda _u: FakeResponse(404), which=_installed)

    assert probe.reachable
    assert probe.model_present is None
    assert probe.problems == []


def test_a_body_that_is_not_the_expected_shape_is_not_a_missing_model():
    probe = probe_engine(ROLE, get=lambda _u: FakeResponse(200), which=_installed)
    assert probe.reachable and probe.model_present is None
    assert "could not be read" in probe.detail


# --- the model --------------------------------------------------------------------


def test_a_model_the_endpoint_does_not_serve_is_a_problem_naming_what_it_does():
    probe = probe_engine(ROLE, get=_serving("qwen3:8b"), which=_installed)

    assert probe.reachable and probe.model_present is False
    (problem,) = probe.problems
    assert "does not serve its declared model" in problem
    assert "qwen3:8b" in problem, "a list of what IS there is the actionable half"


def test_a_suffixed_variant_counts_as_the_declared_model():
    """Servers report the same pull as `qwen3:70b` or `qwen3:70b-instruct`. A
    false 'missing' sends somebody to re-pull a model they have."""
    probe = probe_engine(ROLE, get=_serving("qwen3:70b-instruct"), which=_installed)
    assert probe.model_present


def test_a_different_model_that_merely_starts_the_same_is_not_a_match():
    probe = probe_engine(
        ManagerRole(
            name="p",
            engine="local:small",
            endpoint="http://x/v1",
            model="qwen3:8",
            agent="opencode",
        ),
        get=_serving("qwen3:80b"),
        which=_installed,
    )
    assert probe.model_present is False


# --- the agent --------------------------------------------------------------------


def test_an_agent_that_is_not_installed_is_its_own_problem():
    """Three questions with three fixes. Merged into "the engine is not
    working", a reader goes to the wrong one."""
    probe = probe_engine(ROLE, get=_serving("qwen3:70b"), which=_missing)

    assert probe.agent_installed is False
    (problem,) = probe.problems
    assert "agent is not on this machine's PATH" in problem


def test_an_unreachable_endpoint_reports_only_that():
    """The endpoint is the first thing to fix, and a list of three problems
    where one causes the others is a list nobody reads to the end."""

    def refused(_url):
        raise OSError("no route to host")

    probe = probe_engine(ROLE, get=refused, which=_missing)
    assert len(probe.problems) == 1


def test_a_role_with_no_endpoint_says_so_rather_than_probing_nothing():
    role = ManagerRole(name="p", engine="local:small")
    probe = probe_engine(role, get=_serving(), which=_installed)
    assert not probe.reachable and "no endpoint is declared" in probe.detail


# --- which roles get probed -------------------------------------------------------


@pytest.mark.parametrize("engine", ["claude", "human"])
def test_only_local_engines_are_probed(engine):
    """One is a session and the other is a person; neither has an endpoint to
    ask."""
    roles = [ManagerRole(name="lead", engine=engine), ROLE]
    probes = probe_local_engines(roles, get=_serving("qwen3:70b"), which=_installed)
    assert [p.manager for p in probes] == ["planner"]


def test_the_url_is_built_without_doubling_the_version_segment():
    seen = []

    def record(url):
        seen.append(url)
        return FakeResponse(body={"data": []})

    probe_engine(ROLE, get=record, which=_installed)
    probe_engine(
        ManagerRole(
            name="p", engine="local:small", endpoint="http://h:1234", model="m"
        ),
        get=record,
        which=_installed,
    )

    assert seen == [
        # An endpoint already carrying /v1 must not get a second one...
        "http://localhost:11434/v1/models",
        "http://localhost:11434/api/ps",
        # ...and one without it must get exactly one.
        "http://h:1234/v1/models",
        "http://h:1234/api/ps",
    ], seen

    # ⚠ B7 added the /api/ps call, and it is the SAME doubling hazard from the
    # other direction: the native endpoint must have /v1 STRIPPED, not
    # appended. `http://localhost:11434/v1/api/ps` is a 404 that would report
    # every window as unknown and never say why.
    assert "/v1/api/ps" not in " ".join(seen)


def test_a_probe_object_with_nothing_established_is_a_problem_not_a_pass():
    """The default must not read as healthy: a probe that never ran is a
    machine nobody checked."""
    assert EngineProbe("planner").problems
