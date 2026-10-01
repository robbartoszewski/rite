"""Board, status and loop commands degrade, not crash, where the credential
store cannot be read (live yoloAI run, finding S25).

Measured in Robert's run on 0.7.0a3: inside the Manager's sandbox `rite
status`, `rite board list` and `rite loop run` each died with an unhandled
`CredentialStoreError` ("credential-store.json could not be read (Operation
not permitted). Inside a Manager's sandbox this is expected: a Manager is not
given rite's credentials") and a Python traceback. It came from
`warn_if_global` in the Jira board's construction, which every one of them
goes through. An expected condition took down the command a Manager uses to
find ready work.

The store is denied here the way the sandbox denies it: its metadata is
readable (so the 0600 check passes) and reading its contents fails with EPERM.
One test runs the real CLI under `sandbox-exec` with exactly that deny.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

EXPECTED = "Inside a Manager's sandbox this is expected"
CANNOT = "the Jira credentials cannot be read here"


def _project(root: Path) -> Path:
    rite = root / ".rite"
    rite.mkdir(parents=True)
    (rite / "brief.yaml").write_text("project:\n  name: p\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: jira\n  site: t.atlassian.net\n"
        "  projects:\n    workers: KAN\n  scope_label: p\n"
        "credentials:\n  namespace: p-1a2b\n"
        "sandbox:\n  enabled: false\n"
        "schedule:\n  timezone: Europe/Warsaw\n  windows:\n"
        "  - hours: 00:00-24:00\n    workers: 1\n"
    )
    return root


@pytest.fixture
def file_keyring():
    """rite's own backend, the file store, as production runs it: the suite
    installs an in-memory keyring for every test, which never reads a file,
    and a test run through it would pass whatever the guard did."""
    import keyring

    from rite_ai.credentials.file_store import FileKeyring

    previous = keyring.get_keyring()
    keyring.set_keyring(FileKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture
def denied_store(monkeypatch, file_keyring) -> Path:
    """The credential file exists at 0600 and its contents cannot be read."""
    store = Path(os.environ["RITE_CREDENTIALS_FILE"])
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{}")
    store.chmod(0o600)
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == store:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return store


def _run(root: Path, monkeypatch, *args: str):
    monkeypatch.chdir(root)
    result = CliRunner().invoke(cli, list(args))
    # Handled means: exited, with or without an error code — never an
    # exception escaping the command.
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"rite {' '.join(args)} raised {result.exception!r}"
    )
    return result


def test_status_reports_the_board_unavailable_and_carries_on(
    tmp_path, monkeypatch, denied_store
):
    result = _run(_project(tmp_path), monkeypatch, "status")
    assert result.exit_code == 0, result.output
    assert f"unavailable — {CANNOT}" in result.output
    assert EXPECTED in result.output


def test_board_list_fails_with_the_reason_not_a_traceback(
    tmp_path, monkeypatch, denied_store
):
    result = _run(_project(tmp_path), monkeypatch, "board", "list")
    assert result.exit_code != 0
    assert CANNOT in result.output and EXPECTED in result.output


def test_loop_run_says_the_board_could_not_be_read(tmp_path, monkeypatch, denied_store):
    result = _run(_project(tmp_path), monkeypatch, "loop", "run")
    out = result.output
    assert "verdict: unknown — " + CANNOT in out
    # Not the other state: this board IS configured.
    assert "no ticket backend is configured" not in out
    # Nothing was read, so nothing is counted, and nothing is claimed.
    assert "waiting on the board: not read this cycle" in out
    assert "waiting on the board: 0" not in out
    assert (
        "a reason to stop: no — whether there is work could not be established" in out
    )
    assert "the work is there" not in out


def test_the_watching_loop_stops_cleanly_and_says_why(
    tmp_path, monkeypatch, denied_store
):
    """`rite loop run --watch` is what `rite loop start` runs (S27): it must
    stop on the unreadable board, saying so, not crash or name the wrong
    cause."""
    result = _run(_project(tmp_path), monkeypatch, "loop", "run", "--watch")
    out = result.output
    assert result.exit_code == 1
    assert "verdict: unknown — " + CANNOT in out
    assert "no ticket backend is configured" not in out
    assert "loop: stopping — something could not be established" in out


def test_the_global_fallback_check_does_not_raise(denied_store):
    from rite_ai.credentials.file_store import CredentialStoreError
    from rite_ai.credentials.store import _WARNED_GLOBAL, resolve, warn_if_global

    # The precondition, so this cannot pass by the store being readable.
    with pytest.raises(CredentialStoreError):
        resolve("jira_token")
    _WARNED_GLOBAL.clear()
    assert warn_if_global("jira_token") is None


def test_control_credentials_in_the_environment_still_build_the_board(
    tmp_path, monkeypatch, denied_store
):
    """The environment is tier 1, ahead of the store, so a Manager given its
    Jira credentials that way never touches the unreadable file."""
    from rite_ai.cli.main import _ticket_backend
    from rite_ai.tickets.jira import JiraBackend
    from rite_ai.tickets.scope import unwrapped

    monkeypatch.setenv("RITE_JIRA_EMAIL", "someone@example.com")
    monkeypatch.setenv("RITE_JIRA_TOKEN", "not-a-real-token")
    root = _project(tmp_path)
    monkeypatch.chdir(root)
    board, problem = _ticket_backend("workers", root=root)
    assert problem in (None, ""), problem
    assert isinstance(unwrapped(board), JiraBackend)


def test_control_a_project_with_no_board_still_says_so(tmp_path, monkeypatch):
    root = _project(tmp_path)
    config = root / ".rite" / "config.yaml"
    config.write_text(config.read_text().replace("type: jira", "type: none"))
    result = _run(root, monkeypatch, "loop", "run")
    assert "verdict: unknown — no ticket backend configured" in result.output
    assert CANNOT not in result.output


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("sandbox-exec"),
    reason="the seatbelt reproduction needs macOS sandbox-exec",
)
@pytest.mark.parametrize("denied", [True, False], ids=["denied", "control"])
def test_under_a_real_seatbelt_deny_the_commands_still_run(tmp_path, denied):
    """The kernel's own EPERM, not a mock: a profile that allows everything
    except reading the store's contents, as a Manager's sandbox does. The
    control runs the same profile without the deny."""
    root = _project(tmp_path / "proj")
    store = tmp_path / "creds" / "credential-store.json"
    store.parent.mkdir()
    store.write_text("{}")
    store.chmod(0o600)
    rules = "(version 1)\n(allow default)\n"
    if denied:
        rules += f'(deny file-read-data (literal "{store.resolve()}"))\n'
    profile = tmp_path / "p.sb"
    profile.write_text(rules)
    src = Path(__file__).resolve().parents[1] / "src"
    env = {
        **os.environ,
        "PYTHONPATH": str(src),
        "RITE_CREDENTIALS_FILE": str(store),
    }
    for args in (["status"], ["board", "list"], ["loop", "run"]):
        proc = subprocess.run(
            [
                "sandbox-exec",
                "-f",
                str(profile),
                sys.executable,
                "-c",
                "from rite_ai.cli.main import cli; cli()",
                *args,
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        both = proc.stdout + proc.stderr
        assert "Traceback" not in both, f"rite {' '.join(args)}:\n{both}"
        if denied:
            assert CANNOT in both, f"rite {' '.join(args)}:\n{both}"
        else:
            # The store reads, is empty, and the board is missing a
            # credential — said as that, not as an unreadable store.
            assert CANNOT not in both, f"rite {' '.join(args)}:\n{both}"
