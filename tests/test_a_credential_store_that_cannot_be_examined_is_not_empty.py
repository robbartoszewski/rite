"""A credential store that cannot be examined is not an empty one (SCRUM-30).

🔴 `file_store._read` decided "no store yet" with `Path.exists()`, which
answers False for ANY error from `stat`, not only for a missing file
(Python 3.14, measured: False on EACCES). Inside a sandbox that denies even
the file's metadata, the store therefore read as EMPTY, so every lookup said
"jira_email not found" when the truth was "the credentials cannot be read
here". Only ENOENT and ENOTDIR are absence now; anything else raises
`CredentialStoreError`, which `store` already passes through.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai.credentials import file_store
from rite_ai.credentials.file_store import CredentialStoreError


def _stat_failing_with(path: Path, code: int, monkeypatch):
    real = os.stat

    def fake(p, *a, **k):
        if Path(p) == path:
            raise OSError(code, os.strerror(code), str(p))
        return real(p, *a, **k)

    monkeypatch.setattr(file_store.os, "stat", fake)


@pytest.mark.parametrize("code", [errno.EPERM, errno.EACCES, errno.EIO])
def test_a_store_that_cannot_be_examined_is_an_error_not_empty(
    tmp_path, monkeypatch, code
):
    store = tmp_path / "credential-store.json"
    store.write_text(json.dumps({"rite:p/jira_email": "a@b"}))
    store.chmod(0o600)
    _stat_failing_with(store, code, monkeypatch)
    with pytest.raises(CredentialStoreError) as e:
        file_store._read(store)
    assert "the credentials cannot be read here" in str(e.value)
    monkeypatch.setenv("RITE_CREDENTIALS_FILE", str(store))
    assert "UNUSABLE" in file_store.describe()


@pytest.mark.parametrize("code", [errno.ENOENT, errno.ENOTDIR])
def test_only_a_missing_file_is_an_empty_store(tmp_path, monkeypatch, code):
    store = tmp_path / "credential-store.json"
    _stat_failing_with(store, code, monkeypatch)
    assert file_store._read(store) == {}


def test_a_lookup_says_cannot_be_read_rather_than_not_found(tmp_path, monkeypatch):
    """Through the backend a lookup uses, past `store`'s own exception
    handling, which re-raises `CredentialStoreError` and swallows the rest."""
    store = tmp_path / "credential-store.json"
    store.write_text("{}")
    store.chmod(0o600)
    monkeypatch.setenv("RITE_CREDENTIALS_FILE", str(store))
    _stat_failing_with(store, errno.EPERM, monkeypatch)
    with pytest.raises(CredentialStoreError, match="cannot be read here"):
        file_store.FileKeyring().get_password("rite", "p/jira_email")


@pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS only")
def test_inside_a_sandbox_that_denies_the_file_the_lookup_says_so(tmp_path):
    """Run, not described: the scenario as reported, under `sandbox-exec`,
    with rite's own lookup in a fresh process (no test keyring in front)."""
    hidden = (tmp_path / "hidden").resolve()
    hidden.mkdir()
    store = hidden / "credential-store.json"
    store.write_text(json.dumps({"rite:p/jira_email": "a@b"}))
    store.chmod(0o600)
    profile = tmp_path / "deny.sb"
    profile.write_text(
        f'(version 1)\n(allow default)\n(deny file-read* (subpath "{hidden}"))\n'
    )
    env = {**os.environ, "RITE_CREDENTIALS_FILE": str(store)}
    probe = (
        "from pathlib import Path\n"
        f"print('exists:', Path({str(store)!r}).exists())\n"
        "from rite_ai.credentials.file_store import CredentialStoreError, FileKeyring\n"
        "try:\n"
        "    print('value:', FileKeyring().get_password('rite', 'p/jira_email'))\n"
        "except CredentialStoreError as e:\n"
        "    print('refused:', e)\n"
    )
    done = subprocess.run(
        ["sandbox-exec", "-f", str(profile), sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    # The control: this IS the reported state, where `exists()` says no file.
    assert "exists: False" in done.stdout, done.stdout
    assert "refused: the credentials cannot be read here" in done.stdout, done.stdout
    assert "value:" not in done.stdout
