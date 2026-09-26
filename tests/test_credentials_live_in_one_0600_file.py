"""rite's credential store is one 0600 file, and it never fails silently.

Decided 2026-09-26 (Robert), C6/C26: a file store on both platforms, no
passphrase (deferred, not forgotten), a wrong mode REFUSED, `doctor` naming
the store, and no silent fall to the environment. See
`src/rite_ai/credentials/file_store.py`.

These run against the real file backend, not the suite's in-memory keyring,
because the file is the thing being tested.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys

import keyring
import pytest
from click.testing import CliRunner

from rite_ai.credentials import file_store, store
from rite_ai.credentials.file_store import CredentialStoreError, FileKeyring


@pytest.fixture
def real_store(monkeypatch, tmp_path):
    path = tmp_path / "cfg" / "rite" / "credential-store.json"
    monkeypatch.setenv("RITE_CREDENTIALS_FILE", str(path))
    previous = keyring.get_keyring()
    keyring.set_keyring(FileKeyring())
    store._ANNOUNCED.clear()
    yield path
    keyring.set_keyring(previous)


def test_a_stored_value_lands_in_a_0600_file_in_a_0700_directory(real_store):
    assert store.store("acme-1a2b3c/jira_token", "s3cret") == store.STORED
    assert stat.S_IMODE(real_store.stat().st_mode) == 0o600
    assert stat.S_IMODE(real_store.parent.stat().st_mode) == 0o700
    assert store.get_account("acme-1a2b3c/jira_token") == "s3cret"


def test_a_missing_file_is_an_empty_store_not_an_error(real_store):
    assert not real_store.exists()
    assert store.get_account("anything") is None


def test_a_wrong_mode_is_REFUSED_not_warned_about(real_store):
    store.store("k", "v")
    os.chmod(real_store, 0o644)
    with pytest.raises(CredentialStoreError, match="0644.*chmod 600"):
        store.get_account("k")
    # And it is not swallowed into "not found" on the scoped path either.
    with pytest.raises(CredentialStoreError):
        store.get_scoped("k")


def test_unreadable_is_said_never_reported_as_absent(real_store):
    real_store.parent.mkdir(parents=True)
    real_store.write_text("{not json")
    os.chmod(real_store, 0o600)
    with pytest.raises(CredentialStoreError, match="not valid JSON"):
        store.get_account("k")


def test_the_environment_is_used_and_SAID_once(real_store, monkeypatch, capsys):
    monkeypatch.setenv("RITE_JIRA_TOKEN", "from-env")
    assert store.get_scoped("jira_token") == "from-env"
    assert store.get_scoped("jira_token") == "from-env"
    err = capsys.readouterr().err
    assert err.count("RITE_JIRA_TOKEN") == 1
    assert "not from the file store" in err


def test_doctor_names_the_store(tmp_path, monkeypatch, real_store):
    from rite_ai.cli.main import cli

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    out = CliRunner().invoke(cli, ["doctor"]).output
    assert f"credential store: file store at {real_store}" in out


def test_doctor_reports_an_unusable_store_as_a_problem(
    tmp_path, monkeypatch, real_store
):
    from rite_ai.cli.main import cli

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    store.store("k", "v")
    os.chmod(real_store, 0o640)
    result = CliRunner().invoke(cli, ["doctor"])
    assert "UNUSABLE" in result.output and "chmod 600" in result.output
    assert result.exit_code != 0


def test_import_keychain_copies_names_and_prints_no_value(
    tmp_path, monkeypatch, real_store
):
    from rite_ai.cli.main import cli

    monkeypatch.setenv("RITE_HOME_DIR", str(tmp_path / "home"))
    # A name the registry holds (stored before 0.6.0), absent from the file.
    with store._locked_registry():
        registry = store._read_registry()
        registry["acme-1a2b3c/jira_token"] = 1.0
        store._write_registry(registry)

    class OldKeychain:
        def get_password(self, service, name):
            return "OLD-SECRET" if name == "acme-1a2b3c/jira_token" else None

    monkeypatch.setattr(file_store, "os_keyring", lambda: OldKeychain())
    result = CliRunner().invoke(cli, ["credential", "import-keychain"])
    assert result.exit_code == 0, result.output
    assert "imported acme-1a2b3c/jira_token" in result.output
    assert "OLD-SECRET" not in result.output
    assert store.get_account("acme-1a2b3c/jira_token") == "OLD-SECRET"
    assert file_store.not_yet_imported() == []


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS")
def test_a_managers_profile_cannot_read_the_store(tmp_path, real_store):
    from rite_ai.managers.enclosure import compose

    store.store("k", "v")
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    profile = tmp_path / "p.sb"
    profile.write_text(compose(project, "lead"))
    outside = subprocess.run(["/bin/cat", str(real_store)], capture_output=True)
    assert outside.returncode == 0, "the control: the file is readable outside"
    inside = subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/cat", str(real_store)],
        capture_output=True,
        text=True,
    )
    assert inside.returncode != 0 and "Operation not permitted" in inside.stderr


def test_set_names_the_project_DIRECTORY_not_only_the_namespace(
    tmp_path, monkeypatch, real_store
):
    """A namespace is not readable as a location, and two checkouts can share
    one. Measured 2026-09-26: an hour lost to "which project did that go to?"."""
    from rite_ai.cli.main import cli

    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init", "--yes"])
    result = CliRunner().invoke(
        cli, ["credential", "set", "jira_token", "--value", "s3cret"]
    )
    out = result.output
    assert f"for this project ({tmp_path.resolve()})" in out or (
        f"for this project ({tmp_path})" in out
    ), out
    assert "s3cret" not in out
