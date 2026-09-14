"""Three defects routed in from another session's review.

Grouped because they share a shape rather than a subsystem: each one
collapses two different situations into a single wrong answer.

  * a JSON document and a PDF were both "not a text document"
  * a branch that exists on the remote and one that does not were both
    "create it from main"
  * a missing tag and an unreachable network were both "not published yet"

`install.sh` is covered by `test_install_sh_distinguishes_absent_from_unreachable`
below, which runs the real script's own version-check block rather than
asserting on its text.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rite_ai.kb.manage import _unsupported_type

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestJsonIsATextDocument:
    """`rite kb add <json-url>` was refused, and — the half that makes it
    more than an inconvenience — `rite kb refresh` then exited 1 on that
    entry forever, so one JSON link made the whole refresh fail on every
    run.

    The gate exists to stop BINARY containers being written into the KB as
    though they were prose (a PDF's `%PDF-1.4 1 0 obj` once landed in
    INDEX.md looking like a snapshot). JSON is neither binary nor a
    container."""

    @pytest.mark.parametrize(
        "content_type",
        [
            "application/json",
            "application/json; charset=utf-8",
            "APPLICATION/JSON",
            "application/vnd.api+json",
            "application/ld+json",
            "image/svg+xml",
        ],
    )
    def test_textual_types_are_allowed(self, content_type):
        assert _unsupported_type(content_type) is None, content_type

    @pytest.mark.parametrize(
        "content_type",
        ["application/pdf", "image/png", "application/octet-stream", "video/mp4"],
    )
    def test_binary_containers_are_still_refused(self, content_type):
        """The fix must not open the gate it was built to close."""
        message = _unsupported_type(content_type)
        assert message is not None, content_type
        assert "not a text document" in message


class TestAFirstCloneResumesAnExistingRemoteBranch:
    """`_first_clone` only knew how to CREATE a branch, so a fresh clone
    ignored an existing `origin/<target>` and restarted the ticket from
    the module's default branch — reporting "branch created from main" as
    though that were the whole story.

    The resume case is what makes it matter: the same ticket prepared on a
    second machine, or after a workspace is wiped, IS a first clone, and
    is exactly when the branch already exists on the remote."""

    def _module_with_remote_branch(self, tmp_path, branch):
        """A real origin repo carrying `branch`, and a project pointing at
        it. Real git rather than mocks — the defect was a missing call, and
        a mock of the call that was missing proves nothing."""
        origin = tmp_path / "origin"
        origin.mkdir()
        run = lambda *a: subprocess.run(  # noqa: E731
            a, cwd=origin, check=True, capture_output=True
        )
        run("git", "init", "-q", "-b", "main")
        run("git", "config", "user.email", "t@t")
        run("git", "config", "user.name", "t")
        (origin / "base.txt").write_text("base\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "base")
        # Work already pushed on the ticket branch, absent from main.
        run("git", "checkout", "-q", "-b", branch)
        (origin / "ticket-work.txt").write_text("work already done\n")
        run("git", "add", "-A")
        run("git", "commit", "-qm", "ticket work")
        run("git", "checkout", "-q", "main")
        return origin

    def test_it_checks_out_the_remote_branch_instead_of_recreating_it(self, tmp_path):
        from rite_ai.config.models import Module
        from rite_ai.workspace.prepare import _first_clone

        branch = "ticket-DEF-42"
        origin = self._module_with_remote_branch(tmp_path, branch)
        module = Module(name="backend", path="backend", url=str(origin), branch="main")
        module_dir = tmp_path / "workers" / "alpha" / "backend"
        module_dir.parent.mkdir(parents=True)

        result = _first_clone(module_dir, module, branch, tmp_path)

        assert result.ok, result.message
        assert result.branch == branch
        # THE REGRESSION: the file exists only on the pushed branch. If the
        # branch was recreated from main it is absent, and the work that
        # was already pushed has been silently started over.
        assert (module_dir / "ticket-work.txt").exists(), (
            f"branch was recreated from main, not resumed — {result.message}"
        )

    def test_a_genuinely_new_branch_is_still_created(self, tmp_path):
        """The other half: nothing on the remote means create, as before."""
        from rite_ai.config.models import Module
        from rite_ai.workspace.prepare import _first_clone

        origin = self._module_with_remote_branch(tmp_path, "ticket-DEF-42")
        module = Module(name="backend", path="backend", url=str(origin), branch="main")
        module_dir = tmp_path / "workers" / "alpha" / "backend"
        module_dir.parent.mkdir(parents=True)

        result = _first_clone(module_dir, module, "ticket-DEF-99", tmp_path)

        assert result.ok, result.message
        assert result.branch == "ticket-DEF-99"
        assert not (module_dir / "ticket-work.txt").exists()


class TestInstallShDistinguishesAbsentFromUnreachable:
    """A missing tag and an unreachable remote both printed "that release
    is not published yet", with git's own words discarded by `2>/dev/null`.
    Only one of those was ever true, and telling someone their release does
    not exist when their network is down sends them somewhere else
    entirely.

    Runs the real block out of `install.sh` rather than asserting on a
    copy of it, so the test cannot drift from the shipped script."""

    def _run(self, tmp_path, repo, version):
        script = (REPO_ROOT / "install.sh").read_text()
        marker = 'case "$VERSION" in ?'
        start = script.index(marker)
        end = script.index("fi ;; esac", start) + len("fi ;; esac")
        harness = (
            "fail() { printf 'install.sh: %s\\n' \"$*\" >&2; exit 1; }\n"
            'REPO="$1"; VERSION="$2"\n' + script[start:end] + "\n"
        )
        path = tmp_path / "probe.sh"
        path.write_text(harness)
        return subprocess.run(
            ["sh", str(path), repo, version], capture_output=True, text=True
        )

    def test_an_absent_ref_says_not_published(self, tmp_path):
        origin = tmp_path / "origin"
        origin.mkdir()
        subprocess.run(
            ["git", "init", "-q", "-b", "main"],
            cwd=origin,
            check=True,
            capture_output=True,
        )
        proc = self._run(tmp_path, str(origin), "v9.9.9-nope")
        assert proc.returncode == 1
        assert "not published yet" in proc.stderr

    def test_an_unreachable_remote_does_not_claim_it_is_unpublished(self, tmp_path):
        """THE REGRESSION."""
        proc = self._run(tmp_path, str(tmp_path / "no-such-repo-here.git"), "v0.1.0")
        assert proc.returncode == 1
        assert "not published yet" not in proc.stderr, proc.stderr
        # Says the check FAILED, not that the version is absent. Case-folded:
        # the point is the distinction, not the emphasis the message puts on it.
        assert "could not run" in proc.stderr.lower()
        # git's own words are passed on rather than swallowed by `2>/dev/null`.
        assert "git exited" in proc.stderr.lower()
        assert "repository" in proc.stderr.lower() or "not a git" in proc.stderr.lower()
