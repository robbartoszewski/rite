"""What a project commits, asserted against git rather than against text.

SPEC §8.11. The rule is `.rite/*` plus a re-include per shared file, so a
runtime file added later is ignored without anyone remembering to add it.
The enumerated list this replaced had already drifted: it named
`.rite/handover.json` and `.rite/handovers/` but not `.rite/handover/`, the
per-worker directory that replaced the first, so every worker's snapshot
was landing in git.

These assert what `git check-ignore` actually does. Asserting the presence
of a literal line in `.gitignore` asserts the mechanism, and the mechanism
is what changed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from rite_ai.cli.init.scaffold import update_gitignore

# Every runtime path rite writes into a project, including the ones the
# enumerated list had missed.
RUNTIME_PATHS = (
    ".rite/claims.json",
    ".rite/claims.json.lock",
    ".rite/heartbeats/alpha.json",
    ".rite/outbox/1789_0_handover.json",
    ".rite/handover/alpha.json",  # the per-worker dir the old list missed
    ".rite/handover.json",  # and its legacy predecessor
    ".rite/pool.json",
    ".rite/pool-archive.jsonl",
    ".rite/force-releases.jsonl",
    ".rite/schedule-state.json",
    ".rite/scheduler.log",
    ".rite/scheduler.log.1",
    ".rite/scheduler.lock",
    ".rite/scheduler-last-tick",  # added by a later commit; also missed
    ".rite/kb/.cache/example-com.md",
    "workers/alpha/worker.yml",  # this machine's clones of the modules
    # The lock sidecars `state.locked()` puts beside every file it guards.
    # `.rite/claims.json.lock` above sits at the top level, where `.rite/*`
    # already reaches it. These two do not: they land INSIDE the only two
    # directories the block re-includes, and re-including a directory
    # re-includes everything in it.
    ".rite/context/INDEX.md.lock",
    ".rite/kb/INDEX.md.lock",
)

SHARED_PATHS = (
    ".rite/brief.yaml",
    ".rite/modules.yaml",
    ".rite/config.yaml",
    ".rite/review-checklist.md",
    ".rite/.schema_version",
    ".rite/context/INDEX.md",
    ".rite/context/architecture.md",
    ".rite/kb/INDEX.md",
    ".rite/kb/authored-note.md",
    ".rite/gitleaks.toml",
    ".rite/gitleaksignore",
)


def _repo(tmp_path: Path, kb_commit: bool = True) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    update_gitignore(tmp_path, kb_commit=kb_commit)
    return tmp_path


def _ignored(root: Path, relpath: str) -> bool:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text("x")
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", relpath], cwd=root, capture_output=True
        ).returncode
        == 0
    )


@pytest.mark.parametrize("relpath", RUNTIME_PATHS)
def test_runtime_state_is_ignored(tmp_path: Path, relpath: str):
    assert _ignored(_repo(tmp_path), relpath), f"{relpath} would be committed"


@pytest.mark.parametrize("relpath", SHARED_PATHS)
def test_shared_config_is_not_ignored(tmp_path: Path, relpath: str):
    """The other half. Ignoring all of `.rite/` would stop the team sharing
    the config the tool exists to coordinate around."""
    assert not _ignored(_repo(tmp_path), relpath), (
        f"{relpath} is shared config and must stay tracked"
    )


def test_a_runtime_file_nobody_anticipated_is_ignored(tmp_path: Path):
    """The property the enumeration could not have. This is a file no
    commit has invented yet."""
    root = _repo(tmp_path)
    assert _ignored(root, ".rite/some-future-runtime-state.json")
    assert _ignored(root, ".rite/future-dir/state.json")


def test_nothing_runtime_survives_a_full_work_cycle(tmp_path: Path):
    """End to end: scaffold, work, and see what `git add -A` would stage."""
    from rite_ai.claims.ledger import ClaimsLedger
    from rite_ai.handover import write_snapshot
    from rite_ai.reporting.heartbeat import write_heartbeat
    from rite_ai.reporting.outbox import enqueue

    root = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    (root / ".rite").mkdir()
    (root / ".rite" / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
    )
    update_gitignore(root, kb_commit=True)

    ClaimsLedger(root / ".rite" / "claims.json").claim(["src/a.py"], "alpha")
    write_heartbeat(root, "alpha")
    write_snapshot(root, ticket="T-1", worker="alpha")
    enqueue(root, "handover", {"worker": "alpha"})

    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    runtime = [
        f
        for f in staged
        if f.startswith(
            (
                ".rite/claims",
                ".rite/heartbeats",
                ".rite/outbox",
                ".rite/handover",
                "workers/",
            )
        )
    ]
    assert runtime == [], f"runtime state staged for commit: {runtime}"
    assert ".rite/brief.yaml" in staged  # and the shared half still is


class TestAlreadyTrackedRuntimeState:
    """Adding an ignore rule does not untrack a file that is already
    committed, so a project that ran an earlier rite keeps committing its
    ephemera and reads as fixed. `rite doctor` checks what git ACTUALLY
    tracks, which is a different question from what `.gitignore` says.
    """

    def _project_with_tracked_runtime(self, tmp_path: Path) -> Path:
        root = tmp_path
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        (root / ".rite").mkdir()
        (root / ".rite" / "brief.yaml").write_text("project:\n  name: a\n")
        (root / ".rite" / "claims.json").write_text("[]")
        (root / ".rite" / "handover").mkdir()
        (root / ".rite" / "handover" / "alpha.json").write_text("{}")
        # Committed BEFORE the ignore rule existed, as a real upgrade would.
        subprocess.run(
            ["git", "add", "-A", "-f"], cwd=root, check=True, capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "before the rule",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
        update_gitignore(root, kb_commit=True)
        return root

    def test_doctor_reports_runtime_state_that_is_already_tracked(self, tmp_path: Path):
        from rite_ai.cli.main import _tracked_runtime_state

        problems = _tracked_runtime_state(self._project_with_tracked_runtime(tmp_path))

        assert problems, "a half-applied ignore rule reported as clean"
        assert "claims.json" in problems[0]
        assert "git rm --cached" in problems[0], "reported without the remedy"

    def test_it_does_not_report_the_shared_config(self, tmp_path: Path):
        from rite_ai.cli.main import _tracked_runtime_state

        problems = _tracked_runtime_state(self._project_with_tracked_runtime(tmp_path))

        assert "brief.yaml" not in problems[0], (
            "shared config reported as ephemera to untrack"
        )

    def test_it_finds_the_per_worker_handover_directory(self, tmp_path: Path):
        """The path the enumerated list missed — so the check missed it too,
        since the check derived its patterns from that same list."""
        from rite_ai.cli.main import _tracked_runtime_state

        problems = _tracked_runtime_state(self._project_with_tracked_runtime(tmp_path))

        assert "handover/alpha.json" in problems[0], problems

    def test_a_clean_project_reports_nothing(self, tmp_path: Path):
        from rite_ai.cli.main import _tracked_runtime_state

        root = tmp_path
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        (root / ".rite").mkdir()
        (root / ".rite" / "brief.yaml").write_text("project:\n  name: a\n")
        update_gitignore(root, kb_commit=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)

        assert _tracked_runtime_state(root) == []

    def test_doctor_never_untracks_anything_itself(self, tmp_path: Path):
        """Reports, never removes — untracking is a change to someone's
        repo and stays their call."""
        from rite_ai.cli.main import _tracked_runtime_state

        root = self._project_with_tracked_runtime(tmp_path)
        _tracked_runtime_state(root)

        still_tracked = subprocess.run(
            ["git", "ls-files", "--", ".rite/claims.json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert still_tracked == ".rite/claims.json"


class TestLockSidecarsInReIncludedDirectories:
    """The two holes in "ignore by default": `.rite/context/` and `.rite/kb/`.

    `.rite/*` ignores the directory's contents and a `!` re-include brings
    back the two directories whose contents a team really does share. But a
    re-included directory is re-included WHOLE, and each of those two also
    holds an `INDEX.md.lock` — the sidecar `rite_ai.state.locked()` flocks,
    machinery rather than knowledge. `git add -A` after a `rite context add`
    committed it.

    Why this is not cosmetic. `state.locked()`'s own docstring is explicit
    that the sidecar is "created on first use and thereafter only opened —
    never written, replaced, or unlinked", because deleting a lock file
    while it is held gives the next acquirer a fresh inode and lets two
    writers into the critical section at once. Tracking the file hands
    exactly that power to git: a `checkout`, `stash`, `pull` or `clean` that
    removes or replaces it does so with no idea another process is holding
    it. The measured cost of losing that mutual exclusion is in the same
    docstring — twelve concurrent claimants, ten of twenty rounds lost at
    least one claim, every one of them reporting success and exiting 0.
    """

    def test_a_context_lock_sidecar_is_not_committed(self, tmp_path: Path):
        assert _ignored(_repo(tmp_path), ".rite/context/INDEX.md.lock")

    def test_a_kb_lock_sidecar_is_not_committed(self, tmp_path: Path):
        assert _ignored(_repo(tmp_path), ".rite/kb/INDEX.md.lock")

    def test_the_index_beside_it_is_still_shared(self, tmp_path: Path):
        """The re-include has to keep doing its job. An over-broad rule that
        ignored the directory again would silently stop a team sharing the
        context index, which is the whole reason it is re-included."""
        root = _repo(tmp_path)
        assert not _ignored(root, ".rite/context/INDEX.md")
        assert not _ignored(root, ".rite/kb/INDEX.md")
        assert not _ignored(root, ".rite/context/architecture.md")

    def test_a_lock_in_a_directory_nobody_has_re_included_yet(self, tmp_path: Path):
        """Matched by shape, not by the two names known today. The last
        enumeration in this file drifted the moment a new directory
        appeared; a rule naming `context` and `kb` would drift the same way
        the first time a third directory is shared."""
        assert _ignored(_repo(tmp_path), ".rite/future-shared-dir/INDEX.md.lock")

    def test_the_real_command_that_creates_one_leaves_nothing_to_commit(
        self, tmp_path: Path
    ):
        """End to end through the code a user actually runs, not through a
        hand-written path list. `rite context add` takes the lock as a side
        effect of writing the index, which is how the sidecar appears in a
        project at all — no test that never called it could have seen this.
        """
        from rite_ai.context.manage import add_context

        root = tmp_path
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        (root / ".rite" / "context").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text(
            "project:\n  name: acme\n  role: owner\n"
        )
        update_gitignore(root, kb_commit=True)

        add_context(root, "conventions.md", "writing code", "Coding conventions")

        assert (root / ".rite" / "context" / "INDEX.md.lock").exists(), (
            "the command stopped creating a sidecar — this test is stale"
        )

        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()

        assert [f for f in staged if f.endswith(".lock")] == [], (
            f"a lock sidecar was staged for commit: {staged}"
        )
        # The half that must keep working: the entry itself is shared.
        assert ".rite/context/INDEX.md" in staged
        assert ".rite/context/conventions.md" in staged


class TestDoctorSeesAlreadyTrackedLockSidecars:
    """The other half, and a SEPARATE defect from the ignore rule.

    Fixing `.gitignore` does nothing for a project that already committed a
    sidecar — that is the standing lesson of `TestAlreadyTrackedRuntimeState`
    above, and `rite doctor` exists to catch exactly that case. It did not
    catch this one, for the same structural reason the ignore rule missed
    it: `_tracked_runtime_state` calls a path shared when it starts with any
    entry of `AUTHORED_CONFIG`, and two of those entries are DIRECTORIES.
    `.rite/context/INDEX.md.lock` starts with `.rite/context/`, so the check
    classified the lock sidecar as shared config and stayed silent.

    So the two halves fail independently: one lets the file in, the other
    cannot see it once it is in.
    """

    def _project_tracking_a_lock(self, tmp_path: Path) -> Path:
        root = tmp_path
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        (root / ".rite" / "context").mkdir(parents=True)
        (root / ".rite" / "brief.yaml").write_text("project:\n  name: a\n")
        (root / ".rite" / "context" / "INDEX.md").write_text("# Context Index\n")
        (root / ".rite" / "context" / "INDEX.md.lock").write_text("")
        # Committed before the rule existed, as a real upgrade would have.
        subprocess.run(
            ["git", "add", "-A", "-f"], cwd=root, check=True, capture_output=True
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "before the rule",
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
        update_gitignore(root, kb_commit=True)
        return root

    def test_doctor_reports_a_tracked_lock_sidecar(self, tmp_path: Path):
        from rite_ai.cli.main import _tracked_runtime_state

        problems = _tracked_runtime_state(self._project_tracking_a_lock(tmp_path))

        assert problems, "a committed lock sidecar reported as clean"
        assert "INDEX.md.lock" in problems[0], problems
        assert "git rm --cached" in problems[0], "reported without the remedy"

    def test_it_still_does_not_report_the_index_beside_it(self, tmp_path: Path):
        """The shared half of the same directory must stay unreported, or
        the fix would tell people to untrack their context index."""
        from rite_ai.cli.main import _tracked_runtime_state

        problems = _tracked_runtime_state(self._project_tracking_a_lock(tmp_path))

        joined = " ".join(problems)
        assert "context/INDEX.md," not in joined
        assert not re.search(r"context/INDEX\.md(?!\.lock)", joined), (
            f"shared context index reported as ephemera: {problems}"
        )
