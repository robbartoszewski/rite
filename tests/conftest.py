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

import pytest


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
