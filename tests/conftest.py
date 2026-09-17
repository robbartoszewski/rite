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
from pathlib import Path

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
