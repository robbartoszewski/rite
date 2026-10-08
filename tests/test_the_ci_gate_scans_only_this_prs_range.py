"""The CI publish gate judges what the pull request introduces (SCRUM-39).

**Measured live, 2026-10-02.** A fake `ghp_…` fixture in commit `9aa4336` on
`fix/credentials-doctor-setup` (#183) failed the publish gate on #184 and
effectively every pull request to main. The suppression had to be landed in
main (#186) before anything could merge, and quoting the same string in a
commit message caused the #185→#186 redo.

**The cause, measured again here.** `gitleaks detect` with no `--log-opts`
scans every commit the repository can reach, and `actions/checkout` with
`fetch-depth: 0` has fetched every ref. So the gate asked "does any fetched
ref anywhere contain a leak main has not suppressed?" rather than "does THIS
change introduce one", and coupled every pull request's verdict to every
other open branch.

**What is pinned here:**

* the range decision itself, as a pure function over the CI environment —
  these run everywhere, because this is where a wrong answer would live;
* that it NEVER narrows on a guess. Every case it cannot establish falls
  back to the full scan and says so. Too strict is recoverable; a gate that
  silently checked nothing is not, and would pass while reporting green;
* the end-to-end behaviour against real git and real gitleaks: a leak on
  another fetched branch does not fail a clean branch, and a leak the branch
  itself introduces still does. That is the ticket's own acceptance test;
* that both workflows and the generated template actually pass the flag. The
  decision is worthless if nothing asks for it, and a YAML file is the one
  layer with no other test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from rite_ai.gate.ci_range import DEFAULT_BRANCH_ENV, range_for_ci

REPO = Path(__file__).resolve().parent.parent
GATE_WORKFLOW = REPO / ".github/workflows/publish-gate.yml"
HISTORY_WORKFLOW = REPO / ".github/workflows/publish-gate-history.yml"
TEMPLATE = REPO / "templates/ci/publish-gate.yml"

needs_gitleaks = pytest.mark.skipif(
    shutil.which("gitleaks") is None,
    reason="the gate is built on gitleaks and does not reimplement detection",
)


def _resolver(*known: str, commits: str = "3"):
    """A stand-in for git that resolves only the refs named.

    `commits` is what `rev-list --count` answers: non-zero by default, since
    a range with nothing in it is refused and every test here that expects a
    narrowed range needs one that is not empty.
    """

    def run_git(_root, *args):
        if args[0] == "rev-list":
            return commits
        wanted = args[-1].removesuffix("^{commit}")
        return "c0ffee" if wanted in known else None

    return run_git


class TestWhatItScopesTo:
    def test_a_pull_request_scopes_to_its_own_base(self):
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_BASE_REF": "main",
            },
            REPO,
            _resolver("refs/remotes/origin/main"),
        )

        assert got.rev_range == "refs/remotes/origin/main..HEAD"
        assert got.narrowed

    def test_pull_request_target_too(self):
        """It runs against the base repository and still carries a base
        ref, so it is the same question."""
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request_target",
                "GITHUB_BASE_REF": "main",
            },
            REPO,
            _resolver("refs/remotes/origin/main"),
        )

        assert got.narrowed

    def test_a_pushed_branch_scopes_to_the_trunk(self):
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_NAME": "feat/thing",
                DEFAULT_BRANCH_ENV: "main",
            },
            REPO,
            _resolver("refs/remotes/origin/main"),
        )

        assert got.rev_range == "refs/remotes/origin/main..HEAD"

    def test_a_local_base_branch_is_accepted_when_there_is_no_remote_one(self):
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_BASE_REF": "main",
            },
            REPO,
            _resolver("main"),
        )

        assert got.rev_range == "main..HEAD"


class TestItNeverNarrowsOnAGuess:
    """⚠ The property that matters more than the narrowing. A range computed
    wrong is a gate that passes without having looked, and reports green
    doing it — strictly worse than the over-strict behaviour being replaced.
    Each of these must be the FULL scan, and must say why."""

    @pytest.mark.parametrize(
        "env,run_git",
        [
            # Not in CI at all: `rite publish check --ci-range` typed by hand.
            ({}, _resolver("refs/remotes/origin/main")),
            # In CI, but an event that carries no base.
            (
                {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "schedule"},
                _resolver("refs/remotes/origin/main"),
            ),
            (
                {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch"},
                _resolver("refs/remotes/origin/main"),
            ),
            ({"GITHUB_ACTIONS": "true"}, _resolver("refs/remotes/origin/main")),
            # A pull request whose base was not fetched (a shallow clone).
            (
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_BASE_REF": "main",
                },
                _resolver(),
            ),
            # A push with no trunk name to compare against.
            (
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF_NAME": "feat/x",
                },
                _resolver("refs/remotes/origin/main"),
            ),
        ],
    )
    def test_it_falls_back_to_the_whole_history(self, env, run_git):
        got = range_for_ci(env, REPO, run_git)

        assert got.rev_range is None
        assert not got.narrowed
        assert "whole history is scanned" in got.why

    def test_an_empty_base_ref_does_not_become_the_range_dot_dot(self):
        """⚠ The specific bug an earlier shape of this had.
        `GITHUB_BASE_REF` is set to the EMPTY STRING on a push, which
        stripped to nothing and built the range `"..HEAD"` — which git reads
        as every commit reachable from HEAD, i.e. not a scoping at all, while
        the output claimed a narrowed scan."""
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_BASE_REF": "",
            },
            REPO,
            _resolver("refs/remotes/origin/main"),
        )

        assert got.rev_range is None

    def test_the_trunk_itself_gets_the_full_scan(self):
        """A push to the default branch has no "own range" — it is the
        history everybody else forks from, and "safe to publish" there means
        the whole of it."""
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_NAME": "main",
                DEFAULT_BRANCH_ENV: "main",
            },
            REPO,
            _resolver("refs/remotes/origin/main"),
        )

        assert got.rev_range is None
        assert "default branch" in got.why

    def test_a_tag_push_does_not_narrow(self):
        """⚠ **The vacuous pass this module exists to prevent, and it got
        in anyway.** `on: [push]` is unfiltered and fires on tags;
        `GITHUB_REF_NAME` is then the TAG's name, so the "is this the trunk?"
        test never matched and a release tag cut on the trunk resolved to
        `origin/main..HEAD` — zero commits. The gate would have scanned
        nothing and reported green. This repository cuts release tags, so it
        would have happened."""
        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REF_NAME": "v0.7.0",
                "GITHUB_REF_TYPE": "tag",
                DEFAULT_BRANCH_ENV: "main",
            },
            REPO,
            _resolver("refs/remotes/origin/main"),
        )

        assert got.rev_range is None
        assert "tag push" in got.why

    @pytest.mark.parametrize("count", ["0", "", "not a number", None])
    def test_a_range_with_no_commits_in_it_is_refused(self, count):
        """⚠ **The guard that does not depend on enumerating cases.** The
        tag push above is a named case; this catches the next one nobody
        thought of. A range holding nothing makes the gate scan nothing and
        report green, which is invisible in a way the over-strict behaviour
        never was."""

        def run_git(_root, *args):
            if args[0] == "rev-list":
                return count
            return "c0ffee"

        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_BASE_REF": "main",
            },
            REPO,
            run_git,
        )

        assert got.rev_range is None
        assert "whole history is scanned" in got.why

    def test_a_range_with_commits_in_it_is_used(self):
        """The control: the guard must not refuse every range."""

        def run_git(_root, *args):
            return "3" if args[0] == "rev-list" else "c0ffee"

        got = range_for_ci(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_BASE_REF": "main",
            },
            REPO,
            run_git,
        )

        assert got.rev_range == "refs/remotes/origin/main..HEAD"

    def test_every_answer_says_why(self):
        """The chosen range is printed by both callers, so a reader of a
        green gate can see which commits earned it."""
        for env in ({}, {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push"}):
            assert range_for_ci(env, REPO, _resolver()).why.strip()


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", *args],
        cwd=where,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _a_leak() -> str:
    """A secret-shaped string gitleaks' own ruleset flags.

    Built rather than written out, so this file does not itself carry a
    `ghp_`-shaped literal — that is precisely what #183 did to #184.
    """
    return 'token = "ghp_' + "0123456789abcdefghij0123456789abcdef" + '"\n'


def _repo_with_a_leak_on_another_branch(tmp_path: Path) -> Path:
    """The 2026-10-02 shape: a clean branch, and a leak that exists only on
    a different branch the checkout has fetched."""
    where = tmp_path / "repo"
    where.mkdir()
    _git(where, "init", "-q", "-b", "main", ".")
    (where / "a.txt").write_text("hello\n")
    _git(where, "add", "a.txt")
    _git(where, "commit", "-qm", "base")

    _git(where, "checkout", "-q", "-b", "somebody-elses-branch")
    (where / "fixture.txt").write_text(_a_leak())
    _git(where, "add", "fixture.txt")
    _git(where, "commit", "-qm", "a test fixture, on a branch of its own")

    _git(where, "checkout", "-q", "main")
    _git(where, "checkout", "-q", "-b", "my-clean-branch")
    (where / "b.txt").write_text("unrelated work\n")
    _git(where, "add", "b.txt")
    _git(where, "commit", "-qm", "my own clean change")
    return where


@needs_gitleaks
class TestAgainstRealGitAndRealGitleaks:
    """The ticket's own acceptance test. Nothing here is mocked but the CI
    environment: real commits, the real gitleaks binary, the real gate."""

    def _run(self, where: Path, rev_range: str | None):
        from rite_ai.gate.gate import run_gate

        return run_gate(where, rev_range=rev_range)

    def test_the_full_scan_fails_on_somebody_elses_branch(self, tmp_path):
        """⚠ The control, and the whole reason the fix is needed. Without it
        the test below would pass on a gate that had simply stopped
        detecting anything."""
        where = _repo_with_a_leak_on_another_branch(tmp_path)

        report = self._run(where, None)

        assert report.findings, "the leak on the other branch was not detected at all"

    def test_the_prs_own_range_does_not(self, tmp_path):
        where = _repo_with_a_leak_on_another_branch(tmp_path)

        report = self._run(where, "main..HEAD")

        assert not report.findings, (
            "a leak that exists only on another fetched branch failed a clean "
            "branch's gate — the 2026-10-02 coupling"
        )
        assert not report.errors, report.errors

    def test_a_leak_the_branch_itself_introduces_still_fails(self, tmp_path):
        """⚠ The other half of the acceptance criterion. Narrowing must not
        become not-checking."""
        where = _repo_with_a_leak_on_another_branch(tmp_path)
        (where / "mine.txt").write_text(_a_leak())
        _git(where, "add", "mine.txt")
        _git(where, "commit", "-qm", "my own change, with a secret in it")

        report = self._run(where, "main..HEAD")

        assert report.findings, (
            "a secret introduced by this branch passed the scoped gate"
        )

    def test_a_leak_in_this_branchs_commit_MESSAGE_still_fails(self, tmp_path):
        """The ticket's second symptom. The message scan is scoped by the
        same range, so a quoted secret in the PR's own message is still its
        own problem — and one in another branch's message is not."""
        where = _repo_with_a_leak_on_another_branch(tmp_path)
        (where / "c.txt").write_text("more work\n")
        _git(where, "add", "c.txt")
        _git(where, "commit", "-qm", "quoting " + _a_leak().strip())

        report = self._run(where, "main..HEAD")

        assert report.findings


class TestARangeDoesNotManufactureWarnings:
    """⚠ **The fix for SCRUM-39 nearly shipped a worse regression than the
    bug.** Suppression entries are pinned to commits. Under a range the scan
    deliberately does not look at most of history, so every entry pinned
    outside it matches nothing and was reported STALE — and `--strict`, which
    the generated template passes, makes stale blocking.

    Measured on rite's own repository, 2026-10-07: `rite publish check
    --ci-range --strict` reported 10 stale suppressions and exited 1, where
    the same command without a range exits 0. Shipped in the template, that
    is a red required check on every pull request of every project rite
    creates."""

    def _report(self, where: Path, rev_range: str | None):
        from rite_ai.gate.gate import run_gate

        return run_gate(where, rev_range=rev_range)

    def _repo_with_a_suppression_for_old_history(self, tmp_path: Path) -> Path:
        where = _repo_with_a_leak_on_another_branch(tmp_path)
        rite = where / ".rite"
        rite.mkdir(exist_ok=True)
        # An entry pinned to a commit this branch's range will never scan.
        (rite / "gitleaksignore").write_text(
            "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef:old.txt:github-pat:"
            "sha256-0000  # a decision somebody already took\n"
        )
        _git(where, "add", ".rite/gitleaksignore")
        _git(where, "commit", "-qm", "the Owner's suppression")
        return where

    @needs_gitleaks
    def test_a_scoped_run_does_not_call_them_stale(self, tmp_path):
        where = self._repo_with_a_suppression_for_old_history(tmp_path)

        report = self._report(where, "main..HEAD")

        assert report.stale_suppressions == []
        assert report.exit_code_for(strict=True) == 0, (
            "a range-scoped run blocked on suppressions it never had the scope to judge"
        )

    @needs_gitleaks
    def test_it_says_it_did_not_check_them(self, tmp_path):
        """⚠ Not silence. Printing nothing would read as "every entry still
        earns its place", which is the one thing a range cannot know."""
        from rite_ai.gate.gate import format_report

        where = self._repo_with_a_suppression_for_old_history(tmp_path)

        report = self._report(where, "main..HEAD")

        assert report.stale_unknown
        assert "stale suppressions: not checked" in format_report(report)

    @needs_gitleaks
    def test_the_full_scan_still_judges_them(self, tmp_path):
        """⚠ The control. Without it the fix could be "never report a stale
        suppression again", which would lose a real warning."""
        where = self._repo_with_a_suppression_for_old_history(tmp_path)

        report = self._report(where, None)

        assert report.stale_suppressions, (
            "the full-history run must still say an entry matches nothing"
        )
        assert not report.stale_unknown


class TestTheRangeOptionIsReadBothWays:
    def test_the_joined_form_is_not_silently_dropped(self):
        """⚠ `--range=A..B` matched nothing and fell through to a FULL scan
        printing nothing, which is what `_range_option`'s own docstring
        promises not to do. Measured: 1555 commits scanned under a flag
        asking for 11."""
        from rite_ai.gate.__main__ import _range_option

        assert _range_option(["check", "--range=origin/main..HEAD"]) == (
            "origin/main..HEAD",
            None,
        )
        assert _range_option(["check", "--range", "origin/main..HEAD"]) == (
            "origin/main..HEAD",
            None,
        )

    def test_a_missing_or_malformed_value_is_refused_not_ignored(self):
        from rite_ai.gate.__main__ import _range_option

        for argv in (["check", "--range"], ["check", "--range", "HEAD"]):
            value, problem = _range_option(argv)
            assert value is None and problem, argv

    def test_no_range_option_is_no_range(self):
        from rite_ai.gate.__main__ import _range_option

        assert _range_option(["check"]) == (None, None)


class TestTheWorkflowsAskForIt:
    """⚠ A tested decision nothing calls is not a fix. The workflow files
    are the one layer with no other test, and a bare `check` in them would
    restore the defect silently."""

    def _gate_step(self, path: Path, job: str) -> dict:
        """The step that RUNS the gate.

        Selected by the gate's own invocation spellings, not by the word
        "check" — the gitleaks install step contains `sha256sum --check`,
        and matching that made two of these tests assert against the wrong
        step entirely.
        """
        from rite_ai.gate.ci import GATE_INVOCATIONS

        doc = yaml.safe_load(path.read_text())
        steps = doc["jobs"][job]["steps"]
        found = [
            s
            for s in steps
            if any(i in str(s.get("run", "")) for i in GATE_INVOCATIONS)
        ]
        assert len(found) == 1, f"{path}: expected one gate step, got {len(found)}"
        return found[0]

    def test_the_required_per_pr_gate_is_scoped(self):
        step = self._gate_step(GATE_WORKFLOW, "publish-gate")

        assert "--ci-range" in step["run"]
        assert DEFAULT_BRANCH_ENV in step.get("env", {}), (
            "a push event carries no base ref, so without this the trunk's "
            "name is unknown and every push falls back to the full scan"
        )

    def test_the_required_gate_is_still_named_the_same(self):
        """⚠ main's ruleset names required checks by job name. A rename here
        breaks every open pull request until somebody edits the ruleset, so
        this fix deliberately did not touch it."""
        doc = yaml.safe_load(GATE_WORKFLOW.read_text())

        assert list(doc["jobs"]) == ["publish-gate"]

    def test_it_still_fetches_everything(self):
        """The full fetch is what makes the base ref resolvable. Dropping it
        would make the scoping silently fall back to the full scan — green,
        and for the wrong reason."""
        doc = yaml.safe_load(GATE_WORKFLOW.read_text())
        checkout = next(
            s
            for s in doc["jobs"]["publish-gate"]["steps"]
            if str(s.get("uses", "")).startswith("actions/checkout")
        )

        assert checkout["with"]["fetch-depth"] == 0

    def test_the_generated_template_is_scoped_too(self):
        """rite ships this workflow to every project it creates. A fix only
        rite's own repository carried would leave the defect in the product."""
        step = self._gate_step(TEMPLATE, "publish-gate")

        assert "--ci-range" in step["run"]
        assert "--strict" in step["run"], "unchanged: --strict is a separate axis"
        assert DEFAULT_BRANCH_ENV in step.get("env", {})

    def test_the_full_history_scan_exists_and_is_not_a_merge_blocker(self):
        """The ticket's third criterion: keep a deliberate full-history
        scan, as a separate scheduled job."""
        doc = yaml.safe_load(HISTORY_WORKFLOW.read_text())
        events = doc.get("on", doc.get(True))

        assert set(events) == {"schedule", "workflow_dispatch"}, (
            "it must not trigger on push or pull_request: a required check "
            "that can go red for somebody else's branch is what was removed"
        )
        step = self._gate_step(HISTORY_WORKFLOW, "publish-gate-history")
        assert "--ci-range" not in step["run"], "this job is the full scan"

    def test_the_full_history_job_cannot_be_mistaken_for_the_gate(self):
        """`ci_workflow_status` reads one path, and `inspect_workflow` must
        read this file as not guarding the way to the remote — which it is
        not, and which is why it is a separate file."""
        from rite_ai.gate.ci import inspect_workflow

        armed, why = inspect_workflow(HISTORY_WORKFLOW.read_text())

        assert not armed
        assert "on the way to the remote" in why

    def test_both_workflows_still_verify_the_gitleaks_download(self):
        """The new file reuses the per-PR workflow's install steps; a copy
        that dropped the checksum would be a security control downgraded by
        a refactor."""
        for path, job in (
            (GATE_WORKFLOW, "publish-gate"),
            (HISTORY_WORKFLOW, "publish-gate-history"),
        ):
            doc = yaml.safe_load(path.read_text())
            install = next(
                s
                for s in doc["jobs"][job]["steps"]
                if s.get("name") == "Install gitleaks"
            )
            assert "sha256sum --check" in install["run"], path
