"""Regression tests for the defects found by rehearsal round 5.

Round 5 killed a real worker process between claim and commit — the exact
failure the whole handover-and-claims mechanism exists for — and then asked
what the next session can actually recover from `.rite/` alone.

Round 4 tested the reading side of handover. This is the writing side under
the failure it was built for, composed with the claim ledger and with the
one directory in a rite project whose contents can be irreplaceable: the
worker's own checkout.

Each test reproduces the ORIGINAL SYMPTOM, and each was run against the
previous commit to confirm it fails there.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import cli
from rite_ai.workspace import remove_worker

from .gate_helpers import commit_all, init_repo, write


def _project(tmp_path: Path) -> Path:
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir(parents=True)
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text(
        "modules:\n  reviewer:\n    path: reviewer/\n    branch: main\n"
    )
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return tmp_path


def _origin(tmp_path: Path) -> Path:
    """A repo that stands in for the module's remote."""
    origin = tmp_path / "origin"
    origin.mkdir()
    init_repo(origin)
    write(origin, "README.md", "reviewer\n")
    commit_all(origin, "one")
    return origin


def _worker_checkout(root: Path, origin: Path, worker: str = "w1") -> Path:
    """`workers/<worker>/reviewer`, cloned from `origin` — the shape
    `rite add worker` leaves behind."""
    checkout = root / "workers" / worker / "reviewer"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"], cwd=checkout, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=checkout, check=True)
    (root / "workers" / worker / "worker.yml").write_text(
        f'worker:\n  name: "{worker}"\n  modules:\n    - reviewer\n'
    )
    return checkout


# --- the destructive recovery step -----------------------------------------


class TestRemovingAWorkerDoesNotDestroyItsWork:
    """`remove_worker` was an unconditional `shutil.rmtree`.

    Measured on the scenario the mechanism exists for: a worker was
    SIGKILLed between claim and commit, holding `RT-1`, leaving a modified
    `README.md` and a new `src/scoring.ts` on `feature/RT-1`. The Manager
    did the natural thing and retired it:

        $ rite remove worker w1
        worker 'w1' removed          # exit 0

    Both files were gone. Not committed, not pushed, not stashed, not
    anywhere else.

    `prepare_workspace` refuses on exactly this condition, and its module
    docstring describes this function without knowing it existed: the
    dirty-tree rule is how "no residue from the previous task" is enforced
    "rather than being silently swept away by some separate cleanup step
    that could just as easily delete work someone meant to keep."

    Note also what it destroyed versus what it kept: the checkout is
    irreplaceable, and the claim, heartbeat and handover it left behind
    are bookkeeping that can be rebuilt.
    """

    def test_uncommitted_work_is_not_deleted(self, tmp_path: Path):
        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        (checkout / "README.md").write_text("reviewer\n# RT-1: partial\n")
        (checkout / "scoring.ts").write_text("export const scoring = 'wip';\n")

        result = remove_worker(root, "w1")

        assert (checkout / "scoring.ts").is_file(), (
            "deleted a file that existed nowhere else"
        )
        assert "# RT-1: partial" in (checkout / "README.md").read_text()
        assert not result.ok, "and reported success while doing it"
        assert result.unsaved, "refused without saying what was at risk"

    def test_an_untracked_file_alone_is_enough(self, tmp_path: Path):
        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        (checkout / "scoring.ts").write_text("new work\n")

        result = remove_worker(root, "w1")

        assert not result.ok
        assert (checkout / "scoring.ts").is_file()

    def test_a_commit_that_was_never_pushed_is_enough(self, tmp_path: Path):
        """The subtler half, and the one `is_clean` alone would call safe:
        a worker that committed its afternoon and never pushed has a
        spotlessly clean tree."""
        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        write(checkout, "scoring.ts", "done\n")
        commit_all(checkout, "RT-1: migrate scoring call sites")
        assert (
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=checkout,
                capture_output=True,
                text=True,
            ).stdout.strip()
            == ""
        ), "fixture is wrong — the tree should be clean"

        result = remove_worker(root, "w1")

        assert not result.ok, "a clean tree is not the same as nothing to lose"
        assert result.unsaved[0].unpushed == 1
        assert checkout.is_dir()

    def test_the_refusal_names_the_files_and_the_branch(self, tmp_path: Path):
        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        subprocess.run(
            ["git", "checkout", "-q", "-b", "feature/RT-1"], cwd=checkout, check=True
        )
        (checkout / "scoring.ts").write_text("new work\n")

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, ["remove", "worker", "w1"])

        assert result.exit_code == 1
        assert "scoring.ts" in result.output, "does not say what would be lost"
        assert "feature/RT-1" in result.output, "does not say which branch"
        assert "--force" in result.output, "offers no way through"

    def test_force_still_removes(self, tmp_path: Path):
        """Refusing must not remove the capability, only make it deliberate."""
        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        (checkout / "scoring.ts").write_text("new work\n")

        result = remove_worker(root, "w1", force=True)

        assert result.ok, result.message
        assert not (root / "workers" / "w1").exists()

    def test_a_clean_worker_removes_with_no_friction(self, tmp_path: Path):
        root = _project(tmp_path)
        _worker_checkout(root, _origin(tmp_path))

        result = remove_worker(root, "w1")

        assert result.ok, result.message
        assert not (root / "workers" / "w1").exists()

    def test_a_missing_worker_still_reports_that(self, tmp_path: Path):
        root = _project(tmp_path)

        result = remove_worker(root, "nobody")

        assert not result.ok
        assert "does not exist" in result.message

    def test_a_repo_git_cannot_read_counts_as_at_risk(self, tmp_path: Path):
        """ "I could not find out" is not "nothing would be lost", and this
        decision deletes files — the same asymmetry `rite_ai.state` draws
        between an absent file and an unreadable one."""
        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        (checkout / ".git" / "HEAD").write_text("garbage\n")

        from rite_ai.workspace import unsaved_work

        at_risk = unsaved_work(root / "workers" / "w1")

        assert len(at_risk) == 1, "a repository it could not read was waved through"
        assert at_risk[0].unreadable, "and was not said to be unreadable"
        assert at_risk[0].unpushed == 0, (
            "invented a commit count for a repository whose state it could not read"
        )
        assert not remove_worker(root, "w1").ok
        assert checkout.is_dir()


# --- what the next session can see -----------------------------------------


class TestDoctorLooksAtTheCheckoutsWorkHappensIn:
    """Every `module …` line `rite doctor` prints is about
    `<root>/<module>` — the project's own copy, which nothing works in.
    The work happens in `workers/<name>/<module>`, and doctor never looked
    there.

    So on a project whose worker had just died mid-ticket, doctor printed
    `module reviewer: clean` while `workers/w1/reviewer` held a modified
    README and a new file on the ticket branch. Measured across the whole
    orientation set — `start`, `status`, `watchdog`, `doctor`, `handover
    show` — not one of the five mentioned it: the most valuable thing the
    dead session left was the one thing the next session could not find.
    """

    def _dead_worker_project(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        origin = _origin(tmp_path)
        # The project's own copy of the module, which is what doctor read.
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(root / "reviewer")], check=True
        )
        checkout = _worker_checkout(root, origin)
        subprocess.run(
            ["git", "checkout", "-q", "-b", "feature/RT-1"], cwd=checkout, check=True
        )
        (checkout / "README.md").write_text("reviewer\n# RT-1: partial\n")
        (checkout / "scoring.ts").write_text("export const scoring = 'wip';\n")
        return root

    def test_doctor_reports_the_workers_unsaved_work(self, tmp_path: Path):
        root = self._dead_worker_project(tmp_path)

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, ["doctor"])

        assert "module reviewer: clean" in result.output, (
            "fixture is wrong — the project's own copy should be clean, which "
            "is the whole contrast"
        )
        assert "worker w1" in result.output, (
            "doctor still says the project is clean while a dead worker's "
            "checkout holds work that exists nowhere else"
        )
        assert "scoring.ts" in result.output

    def test_it_counts_as_a_problem(self, tmp_path: Path):
        """Asserted as a DIFFERENCE against an otherwise identical project.
        `rite doctor` exits 1 for plenty of unrelated reasons on a real
        machine (`core.hooksPath` alone does it), so "exit code is 1" would
        have passed against the code that could not see this at all."""
        import re

        def problem_count(root: Path) -> int:
            with patch("rite_ai.cli.main._find_project_root", return_value=root):
                out = CliRunner().invoke(cli, ["doctor"]).output
            found = re.search(r"(\d+) problem\(s\) found", out)
            return int(found.group(1)) if found else 0

        with_work = problem_count(self._dead_worker_project(tmp_path / "a"))
        without = problem_count(self._clean_project(tmp_path / "b"))

        assert with_work == without + 1, (
            f"a worker's unrecovered work is not counted as a problem "
            f"({with_work} vs {without})"
        )

    def _clean_project(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        origin = _origin(tmp_path)
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(root / "reviewer")], check=True
        )
        _worker_checkout(root, origin)
        return root

    def test_a_project_with_nothing_unsaved_says_nothing(self, tmp_path: Path):
        """The check must not become noise on a healthy project."""
        root = self._clean_project(tmp_path)

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, ["doctor"])

        assert "unsaved work" not in result.output

    def test_a_project_with_no_workers_directory_is_fine(self, tmp_path: Path):
        root = _project(tmp_path)
        origin = _origin(tmp_path)
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(root / "reviewer")], check=True
        )

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, ["doctor"])

        assert "Traceback" not in result.output
        assert "unsaved work" not in result.output


# --- and the parts that were already right ---------------------------------


class TestTheRecoveryPathThatAlreadyWorked:
    """Kept because round 5 measured them working, and a later change that
    breaks them should say so here rather than in a dogfood run."""

    def test_prepare_refuses_to_touch_a_dead_workers_dirty_tree(self, tmp_path: Path):
        from rite_ai.config.models import Module
        from rite_ai.workspace.prepare import STATUS_DIRTY, prepare_module

        root = _project(tmp_path)
        checkout = _worker_checkout(root, _origin(tmp_path))
        (checkout / "scoring.ts").write_text("new work\n")

        result = prepare_module(
            root / "workers" / "w1",
            Module(name="reviewer", path="reviewer/", branch="main", url=""),
            root,
        )

        assert result.status == STATUS_DIRTY
        assert not result.ok
        assert (checkout / "scoring.ts").is_file()
