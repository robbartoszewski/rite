"""Regression tests for what the publish gate did on a repository with
real history.

Every earlier test of the gate used a scratch repo with one or two
commits. Driven against git's own repository — 82,180 commits, 392 MB —
two things made the pre-push hook unusable, and both of them block a
legitimate push, which is how a gate gets turned off.

**It scanned all of history on every new branch.** git's own
`pre-push.sample` says "New branch, examine all commits" and rite copied
that reading. Measured: 60,764 commits, 126 MB, 22.9 seconds in gitleaks
alone before the commit-message and pattern passes — and the whole `rite
publish check` did not finish inside two minutes, against a 120-second
subprocess budget it would eventually have blown into `exit 3`, which is
"the gate could not run" and refuses the push. The branch's actual new
work was one commit, which `--not --remotes` scans in 0.06 seconds.

**It blocked on 46 findings the push had nothing to do with.** All of
them hardcoded `/home/user/` paths in git's own test fixtures and
documentation, none in a file the push touched, each needing its own
suppression entry with a reason before any work could go out.

The second one has a decisive argument rather than a preference:
blocking the push does not unpublish anything. A finding in a file the
push does not touch is already on the remote; refusing unrelated work
leaves the exposure exactly where it was and stops the work as well.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.gate.gate import EXIT_FAIL, run_gate
from rite_ai.gate.hook import compute_pre_push_ranges
from rite_ai.gate.pattern_scan import files_touched_by

# Realistic-looking and not a real credential: high entropy, no provider
# prefix that would make it a live-looking key.
PLANTED_SECRET = 'API_KEY = "j8s7Hs9dJ2kLm4Np6Qr8Tv0Wx2Yz4Ab6Cd8Ef0Gh"\n'
# Matches rite's built-in hardcoded-path rule.
PRE_EXISTING_SMELL = 'HOME = "/home/someuser/data"\n'


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _repo_with_a_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A clone with published history, so `--not --remotes` has something
    to exclude — the shape every real project has and no earlier gate
    test did."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    (origin / "legacy.py").write_text(PRE_EXISTING_SMELL)
    _git(origin, "add", "legacy.py")
    _git(origin, "commit", "-qm", "published history")

    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    subprocess.run(
        ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
        cwd=work,
        check=True,
    )
    rite = work / ".rite"
    rite.mkdir()
    (rite / "config.yaml").write_text("ticket_backend:\n  type: none\n")
    return work, origin


class TestANewBranchScansOnlyWhatItWouldPublish:
    def test_the_range_excludes_history_already_on_the_remote(self, tmp_path: Path):
        work, _ = _repo_with_a_remote(tmp_path)
        _git(work, "checkout", "-qb", "feature")
        (work / "new.py").write_text("x = 1\n")
        _git(work, "add", "new.py")
        _git(work, "commit", "-qm", "new work")
        head = _git(work, "rev-parse", "HEAD")

        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/feature {head} refs/heads/feature {'0' * 40}"]
        )

        counted = _git(work, "rev-list", "--count", *rev_range.split())
        assert counted == "1", (
            "the new branch's range covered published history too; on a real "
            "repository that is tens of thousands of commits"
        )

    def test_the_range_is_argv_not_one_argument(self, tmp_path: Path):
        """`"<sha> --not --remotes"` appended whole to `git log` came back
        as `fatal: ambiguous argument ... unknown revision or path`. It
        is one string because gitleaks' `--log-opts` takes one, and argv
        everywhere rite runs git itself. Found by running the hook."""
        work, _ = _repo_with_a_remote(tmp_path)
        _git(work, "checkout", "-qb", "feature")
        (work / "new.py").write_text("x = 1\n")
        _git(work, "add", "new.py")
        _git(work, "commit", "-qm", "new work")
        head = _git(work, "rev-parse", "HEAD")

        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/feature {head} refs/heads/feature {'0' * 40}"]
        )
        touched = files_touched_by(work, rev_range)

        assert touched == {"new.py"}


class TestPreExistingFindingsDoNotBlockAPush:
    def _feature_branch(self, work: Path, content: str) -> str:
        _git(work, "checkout", "-qb", "feature")
        (work / "new.py").write_text(content)
        _git(work, "add", "new.py")
        _git(work, "commit", "-qm", "new work")
        return _git(work, "rev-parse", "HEAD")

    def test_a_clean_push_over_a_dirty_repo_is_not_blocked(self, tmp_path: Path):
        work, _ = _repo_with_a_remote(tmp_path)
        head = self._feature_branch(work, "x = 1\n")
        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/feature {head} refs/heads/feature {'0' * 40}"]
        )

        report = run_gate(work, rev_range=rev_range)

        assert report.findings == [], "blocked on something the push did not add"
        assert [f.file for f in report.pre_existing] == ["legacy.py"]

        # THE OUTCOME, NOT THE ENUM. This asserted `exit_code == EXIT_WARN`,
        # which is 1 — and a pre-push hook exiting non-zero ABORTS the push.
        # So the test certifying "a clean push over a dirty repo is not
        # blocked" passed while the push was blocked, for as long as the
        # feature existed. Asserting the enum is what let that ship.
        assert report.exit_code == 0, "a pre-existing finding must not block"
        assert report.outcome == "warn", "and must still be reported"
        assert report.exit_code_for(strict=True) != 0, "--strict must block"

    def test_a_secret_the_push_adds_still_blocks(self, tmp_path: Path):
        """The half that must not regress."""
        work, _ = _repo_with_a_remote(tmp_path)
        head = self._feature_branch(work, PLANTED_SECRET)
        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/feature {head} refs/heads/feature {'0' * 40}"]
        )

        report = run_gate(work, rev_range=rev_range)

        assert report.exit_code == EXIT_FAIL
        assert [f.file for f in report.findings] == ["new.py"]

    def test_a_finding_in_a_file_the_push_touches_blocks_even_if_it_was_there_before(
        self, tmp_path: Path
    ):
        """Conservative on purpose: touching a file makes its contents
        this push's problem, whoever wrote them."""
        work, _ = _repo_with_a_remote(tmp_path)
        _git(work, "checkout", "-qb", "feature")
        (work / "legacy.py").write_text(PRE_EXISTING_SMELL + "# touched\n")
        _git(work, "add", "legacy.py")
        _git(work, "commit", "-qm", "touch the legacy file")
        head = _git(work, "rev-parse", "HEAD")
        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/feature {head} refs/heads/feature {'0' * 40}"]
        )

        report = run_gate(work, rev_range=rev_range)

        assert [f.file for f in report.findings] == ["legacy.py"]
        assert report.pre_existing == []

    def test_publish_check_still_blocks_on_everything(self, tmp_path: Path):
        """`rite publish check` passes no range. It is the audit — "is
        this repository safe to publish" really does mean all of it — and
        it must not inherit the hook's leniency."""
        work, _ = _repo_with_a_remote(tmp_path)

        report = run_gate(work)

        assert report.exit_code == EXIT_FAIL
        assert "legacy.py" in {f.file for f in report.findings}
        assert report.pre_existing == []

    def test_nothing_is_demoted_when_the_touched_files_cannot_be_listed(
        self, tmp_path: Path, monkeypatch
    ):
        """If rite cannot tell which files the push touched, it does not
        guess in the permissive direction."""
        from rite_ai.gate import pattern_scan

        work, _ = _repo_with_a_remote(tmp_path)
        head = self._feature_branch(work, "x = 1\n")
        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/feature {head} refs/heads/feature {'0' * 40}"]
        )
        monkeypatch.setattr(
            pattern_scan,
            "files_touched_by",
            lambda *a, **k: pattern_scan.ScanError("nope"),
        )

        report = run_gate(work, rev_range=rev_range)

        assert report.exit_code == EXIT_FAIL
        assert report.pre_existing == []


@pytest.mark.parametrize(
    ("rev_range", "expected"),
    [
        ("abc..def", ["abc..def"]),
        ("abcdef --not --remotes", ["abcdef", "--not", "--remotes"]),
        (None, []),
        ("", []),
    ],
)
def test_rev_range_reaches_git_as_separate_arguments(rev_range, expected):
    from rite_ai.gate.findings import rev_range_args

    assert rev_range_args(rev_range) == expected


class TestCommitMessagesAreReadInOneInvocation:
    """Both commit-message scanners — the gitleaks relay and rite's own
    pattern pass — spawned `git show -s --format=%B` once per commit. On
    git's own repository that is 82,180 subprocesses each, and `rite
    publish check` did not finish in the minutes it was given; the dump
    alone now takes 0.8 seconds and the whole audit 38.5.

    They had not diverged, which is the only reason this was one defect
    rather than two different ones. Sharing `iter_commit_messages` is
    what keeps that true."""

    def _repo(self, tmp_path: Path, commits: int) -> Path:
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        (tmp_path / ".rite").mkdir()
        (tmp_path / ".rite" / "config.yaml").write_text(
            "ticket_backend:\n  type: none\n"
        )
        for i in range(commits):
            (tmp_path / f"f{i}.txt").write_text(f"{i}\n")
            _git(tmp_path, "add", f"f{i}.txt")
            _git(tmp_path, "commit", "-qm", f"commit number {i}")
        return tmp_path

    def _count_git_subcommands(self, monkeypatch) -> dict:
        import rite_ai.gate.findings as findings_module

        seen: dict[str, int] = {}
        real = subprocess.run

        def counting(args, *a, **k):
            if isinstance(args, (list, tuple)) and len(args) > 1 and args[0] == "git":
                seen[args[1]] = seen.get(args[1], 0) + 1
            return real(args, *a, **k)

        monkeypatch.setattr(findings_module.subprocess, "run", counting)
        return seen

    def test_the_relay_spawns_one_git_log_not_one_show_per_commit(
        self, tmp_path: Path, monkeypatch
    ):
        from rite_ai.gate.findings import iter_commit_messages

        repo = self._repo(tmp_path, 12)
        seen = self._count_git_subcommands(monkeypatch)

        messages = iter_commit_messages(repo)

        assert not isinstance(messages, str)
        assert len(messages) == 12
        assert seen.get("log") == 1
        assert seen.get("show", 0) == 0

    def test_every_message_and_sha_survives_the_single_invocation(self, tmp_path: Path):
        from rite_ai.gate.findings import iter_commit_messages

        repo = self._repo(tmp_path, 5)
        shas = _git(repo, "log", "--format=%H").splitlines()

        messages = iter_commit_messages(repo)

        assert [sha for sha, _ in messages] == shas
        bodies = [body.strip() for _, body in messages]
        assert bodies == [f"commit number {i}" for i in range(4, -1, -1)]

    def test_a_secret_in_a_commit_message_is_still_found(self, tmp_path: Path):
        """The relay exists because gitleaks does not scan commit
        messages. Making it fast must not make it blind."""
        repo = self._repo(tmp_path, 2)
        (repo / "x.txt").write_text("x\n")
        _git(repo, "add", "x.txt")
        _git(repo, "commit", "-qm", f"oops {PLANTED_SECRET.strip()}")

        report = run_gate(repo)

        assert report.exit_code == EXIT_FAIL
        assert any("commit message" in f.file for f in report.findings)

    def test_the_relay_file_never_contains_a_nul(self, tmp_path: Path):
        """The NUL is a parse delimiter. gitleaks skips any file holding
        one as binary — recorded in `gitleaks_runner`'s docstring as an
        earlier defect, and the reason this fix could have reintroduced
        it."""
        import tempfile

        from rite_ai.gate.gitleaks_runner import _dump_commit_messages

        repo = self._repo(tmp_path, 4)
        with tempfile.TemporaryDirectory() as dest:
            offsets = _dump_commit_messages(repo, Path(dest))
            written = (Path(dest) / "commit-messages.txt").read_bytes()

        assert len(offsets) == 4
        assert b"\x00" not in written
