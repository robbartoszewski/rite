"""Credentials are scoped by the Worker's engine (OL4).

Every Worker used to receive `WORKER_SERVICES` — the Claude login — because
every Worker was Claude. An Ollama Worker has no use for it: its model answers
on this machine's loopback with no credential in play (OL1). SB12 measured a
sandbox's environment readable from other sandboxes here, so an unused
credential inside one is a credential exposed for nothing.

This is the 2026-09-29 "Narrow it down" rule applied to an axis that did not
exist when it was made.
"""

from __future__ import annotations

from unittest.mock import patch

from rite_ai.config.models import CredentialsConfig
from rite_ai.credentials.store import (
    WORKER_SERVICES,
    worker_environment,
    worker_services_for,
)

NS = "acme-1a2b3c"


def _resolves(mapping):
    return patch(
        "rite_ai.credentials.store.get_scoped",
        side_effect=lambda key, _c=None: mapping.get(key.split(":")[-1], ""),
    )


def test_a_claude_worker_still_gets_the_claude_login():
    assert worker_services_for("claude") == WORKER_SERVICES
    assert "claude" in worker_services_for("claude")


def test_a_local_worker_gets_nothing():
    assert worker_services_for("local:small") == ()
    assert worker_services_for("local:large") == ()


def test_the_default_is_claude_so_nothing_before_ol3_changes():
    # Every call site that has not been taught about engines keeps its old
    # behaviour rather than silently losing a credential.
    assert worker_services_for("claude") == WORKER_SERVICES


def test_a_local_worker_is_handed_no_environment_at_all():
    with _resolves({"claude_token": "c", "jira_token": "t"}):
        creds = CredentialsConfig(namespace=NS)
        claude = worker_environment(creds, engine="claude")
        local = worker_environment(creds, engine="local:small")
    assert claude, "a Claude Worker must still receive its login"
    assert local == {}, f"a local Worker received {sorted(local)}"


def test_two_workers_of_one_engine_are_still_alike():
    # §5.3.4's fungibility, within an engine: narrowing by engine must not
    # become narrowing by Worker, which is the per-Worker scoping that was
    # dropped on purpose.
    with _resolves({"claude_token": "c"}):
        creds = CredentialsConfig(namespace=NS)
        assert worker_environment(creds, engine="claude") == worker_environment(
            creds, engine="claude"
        )
