"""Session-wide test isolation from this machine's real state.

Three independent couplings to the developer's own machine, all of which
made tests pass, fail, or HANG for reasons no test controls.

`rite_ai.budget.read_usage_since` scans `~/.claude/projects/**/*.jsonl` by
default — real, and potentially huge (tens of thousands of lines) on any
machine that has actually used Claude Code. Every test that reaches
`rite status` (directly or via the CLI) would otherwise scan this
machine's real transcript history: slow, and coupled to state no test
controls. This autouse fixture points `RITE_CLAUDE_PROJECTS_DIR` at an
empty directory for every test by default; a test that wants real
transcript data passes `transcripts_dir=` to `read_usage_since` directly
instead, which bypasses the env var entirely.

Git configuration is the second. Tests that build real repos inherit the
developer's global `~/.gitconfig`, and `core.hooksPath` there redirects hooks
wholesale — so `install_pre_push_hook` correctly refuses to install into a
`.git/hooks` that git will never read, and four hook tests fail on that
machine and pass everywhere else. The tests were not wrong and neither was
the code; the environment was leaking in. `GIT_CONFIG_GLOBAL` /
`GIT_CONFIG_SYSTEM` (git >= 2.32) point every git subprocess this suite
spawns — including ones deep inside library code — at a config this file
writes, so repo shape is decided by the suite and only by the suite.

It is a fixed minimal config rather than `/dev/null`, because emptying the
global config exposed a second inherited setting: `init.defaultBranch`. Stock
git still defaults to `master`, and `rite prepare` clones module repos at
`main`, so `test_prepare_worker_workspace` was passing only because this
developer's `~/.gitconfig` happened to set `main`. Pinning it here keeps the
suite honest about which branch name it is actually testing.

Credentials are the third, and the only one that could hang rather than
fail. A module fixture named `https://github.com/acme/widgets.git` and
`rite add worker` cloned it for real; `acme/widgets` does not exist, so git
asked `Username for 'https://github.com':` and the suite stopped dead
waiting for a human. It surfaced mid-release, on the maintainer's own
machine, on the step before an irreversible one — and it is worse on a
stranger's clone, where running the tests is the first thing they do.

The fixture below removes the ability to ask. Every git subprocess this
suite spawns gets `GIT_TERMINAL_PROMPT=0` and askpass helpers that refuse,
so a test that reaches the network FAILS, quickly, naming itself — instead
of blocking on a prompt or, worse, succeeding on whatever credentials the
person running it happens to have. That last case is why this is not just
about hangs: a suite that passes because the maintainer is authenticated is
a suite that fails for everyone else.

This is a backstop, not a licence. A test that needs a remote builds a
local one (see `test_add_worker_with_sandbox_enabled_provisions_a_token`,
which serves a real bare repo out of `tmp_path`).
"""

import os
import tempfile
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

_SOCKET_PATH_LIMIT = 104
"""Where a Unix socket path stops being usable. macOS is 104, Linux 108; the
smaller is used so a path that works here works there."""


@pytest.fixture(autouse=True)
def _isolated_claude_projects_dir(tmp_path_factory, monkeypatch):
    empty_dir = tmp_path_factory.mktemp("empty-claude-projects")
    monkeypatch.setenv("RITE_CLAUDE_PROJECTS_DIR", str(empty_dir))


@pytest.fixture(scope="session")
def _suite_gitconfig(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
    path.write_text(
        "[init]\n"
        "\tdefaultBranch = main\n"
        "[user]\n"
        "\tname = rite test suite\n"
        "\temail = tests@rite.invalid\n"
    )
    return str(path)


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch, _suite_gitconfig):
    """No inherited global or system git config for any test. See the module
    docstring — `core.hooksPath` and `init.defaultBranch` are the two that
    actually bit, but aliases, templates and hooks can all change what a test
    repo looks like."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", _suite_gitconfig)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def _no_network_credentials(monkeypatch):
    """No git subprocess may ask a human for credentials. See the module
    docstring — this converts a hang into a named failure."""
    # `0` makes git fail instead of prompting on a terminal.
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    # And these stop it routing around that via a helper — a configured
    # credential helper or an SSH agent would otherwise let the suite
    # succeed on the credentials of whoever happens to be running it.
    monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/false")
    monkeypatch.setenv("SSH_ASKPASS", "/usr/bin/false")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "never")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "")


class _InMemoryKeyring(KeyringBackend):
    """A keyring backend that is a dict, installed for the whole suite.

    ⚠ THIS SUBSTITUTES THE OS, NOT RITE. It sits BELOW
    `rite_ai.credentials.store` and above nothing — `store.py` still runs
    its own resolution order, its env-var fallback, its exception handling
    and its "which store did we use" reporting, and the tests still
    exercise all of it. What they no longer do is reach the login keychain
    of whoever is running them.

    That distinction is the whole design. Mocking `store.get_credential`
    would remove the code under test; replacing the backend removes only
    the machine.
    """

    priority = 1000  # above every real backend, so `keyring` picks this one

    def __init__(self) -> None:
        super().__init__()
        self._values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._values.pop((service, username), None)


@pytest.fixture(autouse=True)
def _no_os_keychain():
    """The keychain half of `_no_network_credentials`, and it was missing.

    ⚠ MEASURED, TWICE IN ONE SESSION, half an hour each time. A test
    reaching `keyring.get_password` on macOS raises a GUI authorisation
    dialog. The suite then BLOCKS on a window nobody is looking at: no
    output, no timeout, no failure. `SecurityAgent` up at 12:21, the
    pytest log's last write at 12:21, still nothing at 12:53. A verify run
    that takes 440 seconds took 1946.

    `store.py` wraps both its reads in `try/except Exception` — correctly,
    and it does not help: **a prompt is not an exception.** It is the
    absence of an answer, which is the shape this project keeps meeting.

    The fixture above already establishes the principle for git ("no git
    subprocess may ask a human for credentials... this converts a hang
    into a named failure"). The OS keychain is the same hazard by the same
    argument and was simply not covered.

    ⚠ Function-scoped, not session-scoped, so the store starts empty for
    every test. A shared dict would leak a credential written by one test
    into another's resolution order, which is the kind of coupling this
    file exists to remove, reintroduced by the fix for it.
    """
    previous = keyring.get_keyring()
    keyring.set_keyring(_InMemoryKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


@pytest.fixture(scope="session")
def _suite_tmux_socket() -> str:
    """A tmux socket directory belonging to this suite and nothing else.

    ⚠ MEASURED COUPLING, and it is the last shared global on this file's
    list. Every `tmux` invocation in the suite and every one in the product
    calls the bare binary with no `-S`, so `pytest` and a Manager started by
    `rite start` land on the SAME tmux server — as do two suites running at
    once on one machine.

    Evidence it is not hypothetical: a stray `rite-loop-looptest-*` session
    was found on the shared server during a run of a file that creates no
    such session, and a control run elsewhere had a **five-file
    markdown-only diff flip a tmux-detection test**. A documentation change
    cannot affect tmux. A neighbour on the same server can.

    `TMUX_TMPDIR` relocates the socket directory and tmux resolves it
    itself, so setting it once for the pytest process covers the tests AND
    the code under test, which inherit the environment. No call site
    changes.

    ⚠ **THE PATH MUST BE SHORT.** A Unix socket path is capped near 104
    bytes and the socket becomes `$TMUX_TMPDIR/tmux-<uid>/default`.
    pytest's `tmp_path_factory` basetemp is far too long — measured, it
    fails with `error connecting to ... (File name too long)`, which names
    neither tmux nor this fixture. So: a short `mkdtemp`, under `/tmp` when
    it exists, and an explicit check that says what is wrong rather than
    leaving the next person to decode errno 63.
    """
    parent = "/tmp" if os.path.isdir("/tmp") else None
    directory = tempfile.mkdtemp(prefix="rt", dir=parent)
    socket = os.path.join(directory, f"tmux-{os.getuid()}", "default")
    if len(socket) >= _SOCKET_PATH_LIMIT:
        raise RuntimeError(
            f"the suite's tmux socket path is {len(socket)} bytes, at or over "
            f"the {_SOCKET_PATH_LIMIT}-byte limit: {socket}. tmux would fail "
            "with 'File name too long', which names neither tmux nor this "
            "fixture. Use a shorter TMUX_TMPDIR parent."
        )
    return directory


@pytest.fixture(autouse=True)
def _isolated_tmux_server(monkeypatch, _suite_tmux_socket):
    """Point every tmux call at the suite's own server, not the machine's.

    Autouse and function-scoped for the same reason `_no_os_keychain` is:
    a test that unsets or overrides it must not leak that to the next one.

    ⚠ This does NOT clean up sessions the suite creates — tests own their
    own teardown, as they already do. What it guarantees is that a session
    the suite fails to clean up dies with the private server rather than
    accumulating on the developer's, and that a Manager an operator is
    actually running is invisible to the suite and untouched by it.
    """
    monkeypatch.setenv("TMUX_TMPDIR", _suite_tmux_socket)


def _checkout_state() -> str | None:
    """`git status --porcelain` for rite's own checkout, or None if git
    cannot answer (not a repo, no git, a timeout)."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


@pytest.fixture(scope="session", autouse=True)
def _suite_leaves_this_checkout_alone():
    """A test run must leave rite's own working tree exactly as it found it.

    `.rite/config.yaml` was swept into commits FOUR times, each carrying a
    credential namespace derived from whatever directory the tree happened to
    be in. Every diagnosis before the last blamed the `.gitignore` rule or
    `git add -A`. Neither was the cause: the SUITE wrote it, because
    `isolated_home` set `RITE_HOME_DIR` and did nothing about the project
    root, so `credential set` under `CliRunner` resolved the project to this
    repository and wrote there.

    That one leak is fixed at its fixture, with a test asserting the property
    for that one command. This is the general form, and it is the cheap one:
    any test that writes into the checkout — by any route, now or later —
    fails the run instead of leaving a file for `git add -A` to find.

    Compared against a SNAPSHOT rather than asserting a clean tree: a
    maintainer runs the suite with work in progress, and demanding `git
    status` be empty would fail on their own edits rather than on the suite's.

    Skipped rather than failed when git cannot answer — a packaged copy of
    the tests, or a machine without git, is not a leak.
    """
    before = _checkout_state()
    yield
    if before is None:
        return
    after = _checkout_state()
    if after is None or after == before:
        return
    was, now = set(before.splitlines()), set(after.splitlines())
    appeared = sorted(now - was)
    vanished = sorted(was - now)
    detail = "\n".join(
        [f"  appeared: {line}" for line in appeared]
        + [f"  vanished: {line}" for line in vanished]
    )
    raise AssertionError(
        "the test suite changed rite's own working tree:\n"
        f"{detail}\n"
        "A test wrote into this checkout instead of a tmp_path. Find it and "
        "isolate it — a file left here is one `git add -A` away from being "
        "committed, which has happened four times."
    )
