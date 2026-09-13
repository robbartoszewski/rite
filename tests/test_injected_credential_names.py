"""A sandboxed Worker receives each credential under its service's own
variable name (`JIRA_API_TOKEN`). The reading side has to accept that name:
it knew only `RITE_<KEY>` and the keychain, neither of which exists inside a
sandbox, so `rite board` failed there with the credential in its
environment."""

from unittest.mock import patch

import pytest

from rite_ai.credentials.services import SERVICES, service_key
from rite_ai.credentials.store import get_scoped, service_env_name, worker_environment

INJECTED_KEYS = [
    service_key(s.name, f.name) for s in SERVICES.values() for f in s.secrets if f.env
]


@pytest.fixture(autouse=True)
def empty_keychain_and_environment(monkeypatch):
    for key in INJECTED_KEYS:
        monkeypatch.delenv(f"RITE_{key.upper()}", raising=False)
        monkeypatch.delenv(service_env_name(key), raising=False)
    with patch("keyring.get_password", return_value=None):
        yield


def test_every_name_a_sandbox_receives_is_read_back(monkeypatch):
    assert "jira_token" in INJECTED_KEYS and "jira_email" in INJECTED_KEYS
    with patch("keyring.get_password", return_value="from-the-host-keychain"):
        delivered = worker_environment()
    assert set(delivered) == {service_env_name(k) for k in INJECTED_KEYS}
    # Inside the sandbox: no keychain (the fixture), only what was delivered.
    for name, value in delivered.items():
        monkeypatch.setenv(name, value)
    for key in INJECTED_KEYS:
        assert get_scoped(key) == "from-the-host-keychain", key


def test_on_the_host_the_keychain_outranks_an_exported_variable(monkeypatch):
    monkeypatch.setenv("JIRA_API_TOKEN", "exported-for-another-tool")
    with patch("keyring.get_password", return_value="this-projects-entry"):
        assert get_scoped("jira_token") == "this-projects-entry"


def test_the_jira_backend_builds_from_the_injected_names_alone(monkeypatch):
    from rite_ai.tickets import BackendError, create_backend

    monkeypatch.setenv("JIRA_EMAIL", "worker@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "injected")
    backend = create_backend("jira", site="example.atlassian.net", project_key="RT")
    assert not isinstance(backend, BackendError), getattr(backend, "message", "")


def test_the_not_found_message_names_the_injected_variable():
    from rite_ai.tickets import _missing_credential

    assert "$JIRA_API_TOKEN" in _missing_credential("jira_token", None)
