"""The harness's two rite-owned roles (RL-T6's other half).

`run_subtask` injected three protocols and `src/` constructed none of them, so
the harness was a shape with no body. These are the two that need no model.

What is worth testing here is not "does it run a command" but the properties a
plausible implementation gets wrong while looking right: that a decomposer's
verify string cannot become two commands, that a tool being absent is not
evidence about the work, and that a commit carries the subtask's scope and not
whatever the agent happened to touch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rite_ai.local.runners import GitCommitter, SubprocessVerifier


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return root


# --- the verify rite runs itself ---------------------------------------------------


def test_a_passing_command_passes(tmp_path):
    result = SubprocessVerifier().run(f"{sys.executable} -c pass", str(tmp_path))
    assert result.passed


def test_a_failing_command_fails_and_keeps_its_output(tmp_path):
    result = SubprocessVerifier().run(
        f"{sys.executable} -c \"import sys; print('boom'); sys.exit(1)\"",
        str(tmp_path),
    )
    assert not result.passed
    assert "boom" in result.output


def test_a_verify_cannot_become_two_commands(tmp_path):
    """rite executes a string a DECOMPOSER wrote, and a decomposer may be a
    small local model. No shell, so `;` and `|` are arguments."""
    marker = tmp_path / "ran"
    result = SubprocessVerifier().run(
        f"{sys.executable} -c pass ; {sys.executable} -c \"open('{marker}','w')\"",
        str(tmp_path),
    )
    assert not marker.exists(), "the second command ran"
    # The `;` and everything after it became ARGUMENTS to the first command,
    # which is the property. Whether that first command then succeeds is its
    # own business — asserting a failure here would be asserting the shape of
    # an accident.
    assert result.output == ""


def test_a_missing_tool_is_not_evidence_about_the_work(tmp_path):
    """RL-47: infrastructure is not failure. A machine without pytest must not
    spend a decomposition's attempts proving it."""
    result = SubprocessVerifier().run(
        "definitely-not-installed --version", str(tmp_path)
    )
    assert not result.passed
    assert "not evidence about the work" in result.output


def test_a_hanging_verify_is_stopped(tmp_path):
    """A verify that never returns holds the subtask's claim, and a claim
    nothing releases blocks the fleet until somebody notices."""
    result = SubprocessVerifier(timeout=1).run(
        f'{sys.executable} -c "import time; time.sleep(30)"', str(tmp_path)
    )
    assert not result.passed
    assert "did not finish within 1s" in result.output


def test_an_unreadable_command_does_not_raise(tmp_path):
    result = SubprocessVerifier().run('pytest "unclosed', str(tmp_path))
    assert not result.passed
    assert "could not be read" in result.output


def test_output_is_capped_from_the_end(tmp_path):
    """A step review reads this, and the failure is at the end."""
    result = SubprocessVerifier().run(
        f"{sys.executable} -c \"print('x' * 100000); print('LAST')\"",
        str(tmp_path),
    )
    assert "LAST" in result.output
    assert "earlier characters dropped" in result.output


# --- the commit, which carries the scope and nothing else --------------------------


def test_the_subtask_s_scope_is_committed_to_its_own_branch(tmp_path):
    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("ok\n")

    commit = GitCommitter(scope=("parser.py",)).commit_to_branch(
        str(repo), "rite-local/ABC-1/s1", "ABC-1 s1: add the parser"
    )

    assert commit.sha and not commit.error
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True
    )
    assert branch.stdout.strip() == "rite-local/ABC-1/s1"


def test_work_outside_the_scope_is_left_behind(tmp_path):
    """An agent that edited outside its subtask's paths did something the plan
    did not sanction. Committing it would launder that into the branch
    composition later applies."""
    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("ok\n")
    (repo / "elsewhere.py").write_text("not asked for\n")

    GitCommitter(scope=("parser.py",)).commit_to_branch(str(repo), "b", "m")

    in_commit = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    assert "parser.py" in in_commit
    assert "elsewhere.py" not in in_commit


def test_edits_only_outside_the_scope_say_exactly_that(tmp_path):
    """The distinction a step review needs: the agent changed things, just not
    the things it was asked to."""
    repo = _repo(tmp_path)
    (repo / "elsewhere.py").write_text("not asked for\n")

    commit = GitCommitter(scope=("parser.py",)).commit_to_branch(str(repo), "b", "m")

    assert not commit.sha
    assert "edited outside it" in commit.error


def test_a_partly_finished_subtask_still_commits_what_exists(tmp_path):
    """Two declared files and one produced is the ordinary partial case.
    `git add -- a b` fails entirely when one pathspec matches nothing, which
    would throw away real work."""
    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("ok\n")

    commit = GitCommitter(scope=("parser.py", "never_created.py")).commit_to_branch(
        str(repo), "b", "m"
    )

    assert commit.sha and not commit.error


def test_an_unchanged_workspace_is_not_a_commit(tmp_path):
    commit = GitCommitter(scope=("parser.py",)).commit_to_branch(
        str(_repo(tmp_path)), "b", "m"
    )
    assert not commit.sha and "unchanged" in commit.error


def test_a_subtask_with_no_scope_commits_nothing(tmp_path):
    """A committer with no scope would commit whatever the agent touched,
    which is the laundering this class exists to prevent."""
    repo = _repo(tmp_path)
    (repo / "anything.py").write_text("x\n")

    commit = GitCommitter().commit_to_branch(str(repo), "b", "m")

    assert not commit.sha and "declares no scope" in commit.error


def test_a_retried_subtask_lands_on_its_own_branch(tmp_path):
    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("one\n")
    GitCommitter(scope=("parser.py",)).commit_to_branch(str(repo), "b", "first")
    (repo / "parser.py").write_text("two\n")

    second = GitCommitter(scope=("parser.py",)).commit_to_branch(
        str(repo), "b", "again"
    )

    assert second.sha and not second.error


def test_a_workspace_that_is_not_a_repository_is_named(tmp_path):
    commit = GitCommitter(scope=("a.py",)).commit_to_branch(str(tmp_path), "b", "m")
    assert "not a git repository" in commit.error


# --- the sha is the whole point of returning a Commit -------------------------------


def test_a_commit_without_a_sha_is_an_error_not_a_success(tmp_path, monkeypatch):
    """`rev-parse` failing must not be reported as a successful commit.

    `commit_to_branch` checked the return code of `git commit` and then ran
    `git rev-parse HEAD` without checking anything, returning
    `Commit(sha=(sha.stdout or "").strip())`. A failed `rev-parse` therefore
    produced `Commit(sha="", error="")` — and `harness.run_subtask` decides
    on `if commit.error:`, so an empty error meant the subtask was marked
    ACCEPTED with `outcome.commit = ""`. The branch is what composition
    later applies, and it would be applying a reference to nothing.

    The trigger is not the interesting part and is hard to stage honestly —
    `rev-parse HEAD` does not fail after a commit that just succeeded except
    under a damaged repository. The gap is unconditional, so it is tested
    where it lives: the function must not treat an unread exit code as
    success.
    """
    from rite_ai.local import runners

    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("ok\n")
    real_git = runners._git

    def fail_only_rev_parse(args: list[str], cwd: str):
        if args[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=128, stdout="", stderr="fatal: bad HEAD"
            )
        return real_git(args, cwd)

    monkeypatch.setattr(runners, "_git", fail_only_rev_parse)
    commit = runners.GitCommitter(scope=("parser.py",)).commit_to_branch(
        str(repo), "rite-local/ABC-1/s1", "ABC-1 s1: add the parser"
    )

    assert commit.error, (
        "a commit whose sha could not be read reported SUCCESS — the harness "
        "decides on `if commit.error:`, so this subtask is ACCEPTED with an "
        "empty commit reference"
    )
    assert not commit.sha, "an errored commit must not also carry a sha"
    assert "bad HEAD" in commit.error or "rev-parse" in commit.error, (
        f"the error must say what failed, not merely that something did: "
        f"{commit.error!r}"
    )


def test_an_empty_sha_is_refused_even_when_rev_parse_succeeds(tmp_path, monkeypatch):
    """Exit code 0 and nothing printed is the same non-answer.

    Guarding only the return code would leave the other half of the shape
    open — a check that reads the exit code for failure and the text for the
    value still has no branch for "succeeded, said nothing".
    """
    from rite_ai.local import runners

    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("ok\n")
    real_git = runners._git

    def empty_rev_parse(args: list[str], cwd: str):
        if args[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(
                args=["git", *args], returncode=0, stdout="  \n", stderr=""
            )
        return real_git(args, cwd)

    monkeypatch.setattr(runners, "_git", empty_rev_parse)
    commit = runners.GitCommitter(scope=("parser.py",)).commit_to_branch(
        str(repo), "rite-local/ABC-1/s2", "ABC-1 s2: add the parser"
    )

    assert commit.error and not commit.sha, (
        "`rev-parse` exited 0 and printed nothing, and that was accepted as "
        "a commit reference"
    )


def test_the_ordinary_commit_still_returns_a_real_sha(tmp_path):
    """So the fix cannot be 'always return an error'."""
    repo = _repo(tmp_path)
    (repo / "parser.py").write_text("ok\n")

    commit = GitCommitter(scope=("parser.py",)).commit_to_branch(
        str(repo), "rite-local/ABC-1/s3", "ABC-1 s3: add the parser"
    )

    assert not commit.error and len(commit.sha) == 40, (
        f"expected a full sha and no error, got {commit!r}"
    )
    shown = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    assert commit.sha == shown.stdout.strip(), "the sha is not the commit that landed"


# --- what this module must not be able to do ---------------------------------------


@pytest.mark.parametrize("forbidden", ["push", "gh pr", "claude"])
def test_the_runners_never_name_a_push_or_the_claude_cli(forbidden):
    """RL-11 and RL-12. `tests/test_blast_radius.py` is the wider gate; this
    is the one that fails in the file where such a line would be added."""
    source = Path(__file__).resolve().parent.parent / "src/rite_ai/local/runners.py"
    in_docstring = False
    for line in source.read_text().splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.count('"""') % 2:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        assert forbidden not in line, f"{forbidden!r} in an executable line: {line}"
