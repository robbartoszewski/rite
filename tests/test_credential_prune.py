"""`rite credential prune`: leftovers are listed, and removed only by name (SCRUM-18).

🔴 Test and scratch runs left whole namespaces in a real store and registry
(`test-credential-set-does-*`, `rte2e-scratch-*`, `acme-1a2b3c`), and nothing
removed them. On the machine this was written on, the list of "namespaces no
known project uses" ALSO held real projects', because rite had not recorded
those projects there. So the command lists, and removes only what a person
names; it never offers to remove the whole list at once.

The other half is `conftest._suite_leaves_the_real_credentials_alone`, which
fails a run that adds a name to the real files; its comparison is tested here.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.credentials import store
from rite_ai.credentials.file_store import CredentialStoreError
from tests.conftest import appeared_credential_names


def _machine(tmp_path, monkeypatch):
    monkeypatch.setenv("RITE_DISPATCH_DIR", str(tmp_path / "dispatch"))
    real = tmp_path / "real"
    (real / ".rite").mkdir(parents=True)
    (real / ".rite" / "config.yaml").write_text(
        "ticket_backend:\n  type: none\ncredentials:\n  namespace: real-0001\n"
    )
    from rite_ai.machine_projects import note

    note(real)
    store.store("real-0001/jira_token", "kept")
    store.store("test-x-0002/jira_token", "leftover")
    store.store("test-x-0002/jira_email", "leftover@example.com")
    store.store("github_token", "machine-wide")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return real


def _registered():
    return set(store._read_registry())


def test_it_lists_only_unused_namespaces_and_removes_nothing(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)
    before = _registered()
    got = CliRunner().invoke(cli, ["credential", "prune"])
    assert got.exit_code == 0, got.output
    assert "test-x-0002: 2 credential(s)" in got.output
    assert "real-0001" not in got.output
    assert "github_token" not in got.output
    assert "Nothing was removed" in got.output
    # Never one command that removes everything listed.
    assert "--namespace test-x-0002" not in got.output
    assert _registered() == before


def test_a_named_namespace_is_removed_and_nothing_else(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)
    got = CliRunner().invoke(
        cli, ["credential", "prune", "--namespace", "test-x-0002", "--yes"]
    )
    assert got.exit_code == 0, got.output
    assert "removed 2 credential(s) under test-x-0002" in got.output
    assert _registered() == {"real-0001/jira_token", "github_token"}


def test_a_namespace_a_known_project_uses_is_refused(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)
    before = _registered()
    got = CliRunner().invoke(
        cli, ["credential", "prune", "--namespace", "real-0001", "--yes"]
    )
    assert got.exit_code == 1
    assert "refusing real-0001: a project on this machine uses it" in got.output
    assert _registered() == before


def test_a_namespace_with_nothing_stored_is_refused(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)
    got = CliRunner().invoke(
        cli, ["credential", "prune", "--namespace", "never-0003", "--yes"]
    )
    assert got.exit_code == 1 and "nothing is stored under it" in got.output


def test_without_yes_it_asks_and_a_no_leaves_everything(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)
    before = _registered()
    got = CliRunner().invoke(
        cli, ["credential", "prune", "--namespace", "test-x-0002"], input="n\n"
    )
    assert "left unchanged" in got.output
    assert _registered() == before


def test_a_store_that_cannot_be_read_is_not_nothing_to_prune(tmp_path, monkeypatch):
    _machine(tmp_path, monkeypatch)

    def unreadable(used):
        raise CredentialStoreError("the credentials cannot be read here: x")

    monkeypatch.setattr(store, "prune_candidates", unreadable)
    got = CliRunner().invoke(cli, ["credential", "prune"])
    assert got.exit_code == 1
    assert "cannot be read here" in got.output
    assert "nothing to prune" not in got.output


def test_a_name_only_in_the_store_file_is_found(tmp_path, monkeypatch):
    """A leftover the registry lost is still in the file, and still listed."""
    _machine(tmp_path, monkeypatch)
    path = tmp_path / "credential-store.json"
    path.write_text(json.dumps({"rite:orphan-0004/jira_token": "x"}))
    path.chmod(0o600)
    monkeypatch.setenv("RITE_CREDENTIALS_FILE", str(path))
    found = {c.namespace for c in store.prune_candidates({"real-0001"})}
    assert "orphan-0004" in found and "test-x-0002" in found


def test_the_guard_reports_a_name_that_appeared():
    before = {"store": {"a/x"}, "registry": {"a/x"}}
    after = {"store": {"a/x", "leak-1/y"}, "registry": {"a/x"}}
    assert appeared_credential_names(before, after) == ["store: leak-1/y"]


def test_the_guard_does_not_guess_about_a_file_it_could_not_read():
    assert appeared_credential_names({"store": None}, {"store": {"z"}}) == []
    assert appeared_credential_names({"store": {"a"}}, {"store": None}) == []
