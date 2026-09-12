"""Regression tests for what `classify_git_failure` did with the stderr
real git actually produces.

The classification decides whether `rite prepare` BLOCKS on a module or
degrades: a "network" failure reports the module "ready (offline)" and
carries on with whatever is already on disk, while a "repo" failure stops
and surfaces it. `git_ops`'s own docstring says the list is "deliberately
conservative" and that an unrecognised failure is treated as "repo" so a
session stops rather than silently carrying on with stale state.

It was not conservative. Every message below was produced by running git
2.x against a live host, not written from memory.

The signature `"unable to access"` — annotated in the source as the
"curl-style" prefix — is transport-generic in modern git, and it is what
git says in front of every TLS failure:

    fatal: unable to access '<url>': SSL certificate problem: self signed certificate
    fatal: unable to access '<url>': SSL certificate problem: certificate has expired
    fatal: unable to access '<url>': SSL: no alternative certificate subject name
                                     matches target host name

All three classified as "network", so `prepare` reported the module ready
and used the checkout it already had. A wrong-host certificate is what
interception looks like; the response to it was to shrug and continue.

The prefix was not even needed for its stated job. Every genuine
connectivity failure carries "could not resolve host" / "connection
refused" / "operation timed out" in the same message and matches on those
instead — as the first group below shows.
"""

from __future__ import annotations

import pytest

from rite_ai.workspace.git_ops import classify_git_failure

# Captured from real git runs, verbatim.
REACHABILITY_FAILURES = [
    "fatal: unable to access 'https://x/': Could not resolve host: x",
    "ssh: connect to host github.com port 22: Connection refused",
    "fatal: unable to access 'https://x/': Failed to connect to x port 443: "
    "Operation timed out",
    "fatal: unable to access 'https://x/': Could not resolve proxy: proxy.local",
]

TRUST_FAILURES = [
    "fatal: unable to access 'https://self-signed.badssl.com/repo.git/': "
    "SSL certificate problem: self signed certificate",
    "fatal: unable to access 'https://expired.badssl.com/repo.git/': "
    "SSL certificate problem: certificate has expired",
    "fatal: unable to access 'https://wrong.host.badssl.com/repo.git/': "
    "SSL: no alternative certificate subject name matches target host name "
    "'wrong.host.badssl.com'",
    "Host key verification failed.\nfatal: Could not read from remote repository.",
    "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@",
]

REPOSITORY_FAILURES = [
    "fatal: repository 'https://example.com/foo.git/' not found",
    "remote: Invalid username or token. Password authentication is not supported "
    "for Git operations.\nfatal: Authentication failed for "
    "'https://github.com/owner/repo'",
    "fatal: could not read Username for 'https://github.com': terminal prompts "
    "disabled",
    "fatal: Not possible to fast-forward, aborting.",
]


@pytest.mark.parametrize("stderr", REACHABILITY_FAILURES)
def test_a_server_that_cannot_be_reached_is_network(stderr: str):
    """These are the ones prepare may safely degrade on — the module is
    unchanged on disk and no answer was received to act on."""
    assert classify_git_failure(stderr) == "network"


@pytest.mark.parametrize("stderr", TRUST_FAILURES)
def test_a_server_that_cannot_be_believed_blocks(stderr: str):
    """The original defect. "I could not reach the server" and "I reached
    it and could not believe it" have opposite correct responses, and
    only one of them is safe to carry on from. All of these used to
    return "network"."""
    assert classify_git_failure(stderr) == "repo"


@pytest.mark.parametrize("stderr", REPOSITORY_FAILURES)
def test_an_answer_that_says_no_blocks(stderr: str):
    assert classify_git_failure(stderr) == "repo"


def test_a_trust_failure_wins_over_a_reachability_signature():
    """A message can carry both — the transport retried, timed out, and
    also reported a bad certificate. Blocking has to win, or the defect
    comes back through the first message that happens to mention a
    timeout."""
    both = (
        "fatal: unable to access 'https://x/': SSL certificate problem: "
        "self signed certificate\nfatal: Operation timed out"
    )
    assert classify_git_failure(both) == "repo"


def test_an_unrecognised_failure_still_blocks():
    """The stated bias, restated as a test: an unfamiliar error surfaces
    rather than being waved through as "probably offline"."""
    assert classify_git_failure("fatal: something nobody has seen before") == "repo"


class TestStatusLinesStaySingleLines:
    """`prepare` prints one line per module. Embedding raw git stderr
    broke that: a diverged branch put twelve lines of git's `hint:` advice
    block inside one status line, so the sentence that mattered — "Not
    possible to fast-forward" — arrived after a screen of guidance about
    `git merge --no-ff`.

    `summarise_stderr` is imported inside each test rather than at module
    scope so that the classification tests above still COLLECT against a
    build that predates it — a regression test that cannot be run against
    the code it describes proves nothing."""

    def test_the_advice_block_is_dropped(self):
        from rite_ai.workspace.git_ops import summarise_stderr

        stderr = (
            "hint: Diverging branches can't be fast-forwarded, you need to either:\n"
            "hint:\n"
            "hint: \tgit merge --no-ff\n"
            "hint:\n"
            "hint: or:\n"
            "hint:\n"
            "hint: \tgit rebase\n"
            "hint:\n"
            'hint: Disable this message with "git config set advice.diverging false"\n'
            "fatal: Not possible to fast-forward, aborting."
        )
        assert summarise_stderr(stderr) == (
            "fatal: Not possible to fast-forward, aborting."
        )

    def test_everything_that_is_not_advice_is_kept(self):
        from rite_ai.workspace.git_ops import summarise_stderr

        stderr = "remote: Repository not found.\nfatal: repository 'x' not found"
        assert summarise_stderr(stderr) == (
            "remote: Repository not found. / fatal: repository 'x' not found"
        )

    def test_the_result_is_always_one_line(self):
        from rite_ai.workspace.git_ops import summarise_stderr

        for stderr in (*TRUST_FAILURES, *REPOSITORY_FAILURES):
            assert "\n" not in summarise_stderr(stderr)

    def test_empty_stderr_summarises_to_nothing(self):
        from rite_ai.workspace.git_ops import summarise_stderr

        assert summarise_stderr("") == ""
        assert summarise_stderr("\n\n  \n") == ""
