from unittest.mock import patch

from rite_ai.credentials.store import (
    CredentialInfo,
    get,
    info,
    list_for_rotation,
    store,
)


def test_get_from_env(monkeypatch):
    monkeypatch.setenv("RITE_JIRA_TOKEN", "secret123")
    assert get("jira_token") == "secret123"


def test_get_missing(monkeypatch):
    monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
    result = get("jira_token")
    # Without keyring installed, falls back to None
    assert result is None or isinstance(result, str)


def test_info_from_env(monkeypatch):
    monkeypatch.setenv("RITE_GITHUB_TOKEN", "ghp_xxx")
    result = info("github_token")
    assert isinstance(result, CredentialInfo)
    assert result.source == "env"


def test_info_not_found(monkeypatch):
    monkeypatch.delenv("RITE_MISSING", raising=False)
    result = info("missing")
    assert result.source in ("not_found", "keychain")


def test_store_records_registry_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    with patch("keyring.set_password") as mock_set:
        result = store("jira_token", "secret123")
    assert result == "keychain"
    mock_set.assert_called_once_with("rite", "jira_token", "secret123")
    registry_path = tmp_path / "credentials.json"
    assert registry_path.is_file()
    assert "jira_token" in registry_path.read_text()


def test_store_never_writes_the_value_to_the_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    with patch("keyring.set_password"):
        store("jira_token", "super-secret-value")
    registry_path = tmp_path / "credentials.json"
    assert "super-secret-value" not in registry_path.read_text()


def test_list_for_rotation_reflects_current_source(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
    with patch("keyring.set_password"):
        store("jira_token", "secret123")

    with patch("keyring.get_password", return_value="secret123"):
        entries = list_for_rotation()
    assert len(entries) == 1
    assert entries[0].name == "jira_token"
    assert entries[0].source == "keychain"
    assert entries[0].last_set is not None


def test_list_for_rotation_flags_a_credential_removed_out_of_band(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("RITE_JIRA_TOKEN", raising=False)
    with patch("keyring.set_password"):
        store("jira_token", "secret123")

    with patch("keyring.get_password", return_value=None):
        entries = list_for_rotation()
    # `source` on a rotation entry is display text — this listing is read by
    # a person choosing what to rotate, so it reads as prose rather than as
    # the internal `not_found` token `CredentialInfo.source` still carries.
    assert entries[0].source == "not found"


def test_list_for_rotation_empty_when_nothing_ever_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path))
    assert list_for_rotation() == []
