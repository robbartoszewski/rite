"""A committed file cannot move the publish gate's trusted rules (SCRUM-76).

**The hole.** `PROJECT_MARKERS` are `.rite/brief.yaml` and
`.rite/modules.yaml` — two ordinary files. `_gate_config_root` walked up from
cwd and took the NEAREST directory holding one, before consulting
`RITE_PROJECT_ROOT`. So anything able to commit to the scanned repository
could plant a marker in it, and the gate then read its suppression list
(`.rite/gitleaksignore`) and its ruleset (`.rite/gitleaks.toml`) from inside
that repository — two files on the branch being pushed. **The party the gate
constrains got to choose the file that says what the gate ignores.**

It reaches `rite publish check` and the pre-push hook, which is exactly where
a Worker runs the gate against its own work. Delivery's own gate run is on
the host, and SCRUM-62 already refuses a branch that touches those files at
all — so this is the other door into the same room.

**Why the naive fix is wrong, and is not what this does.** Those marker files
are legitimate work: in a single-repo layout the project root IS the
repository and `.rite/brief.yaml` is committed there by the Owner, and
`rite prepare` lays the markers above the module repositories. Refusing or
ignoring markers inside a repository would break both.

**The rule instead:** when rite has NAMED the project — `RITE_PROJECT_ROOT`,
set by the host when it starts a Manager or a Worker, which no commit can
change — that answer wins over a marker found inside the tree being scanned.
A marker in an ancestor ABOVE the repository still wins, because it is not on
the branch being pushed. When rite has named nothing, the nearest marker still
answers: nobody has been overruled, because nobody said anything.

**What is pinned here:** the planted marker does not move the trusted root,
and — end to end against real gitleaks — does not move which suppression file
the gate obeys. Plus every layout that must keep working, because a fix that
pinned the root by breaking `rite prepare` would be worse than the hole.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import PROJECT_ROOT_ENV, _gate_config_root, _gate_root, cli
from rite_ai.gate import gitleaks_runner
from tests.gate_helpers import commit_all, init_repo, write
from tests.test_the_gate_scans_a_repo_not_a_project_dir import (
    A_PLANTED_TOKEN,
    _prepare_layout,
)

requires_gitleaks = pytest.mark.skipif(
    gitleaks_runner.find_gitleaks_binary() is None, reason="gitleaks not installed"
)

MARKERS = (".rite/brief.yaml", ".rite/modules.yaml")


def _plant(repo: Path, marker: str) -> None:
    """Commit a PROJECT_MARKER into the scanned repository, as a Worker
    could on its own branch."""
    path = repo / marker
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "project:\n  name: mine\n  role: owner\n"
        if marker.endswith("brief.yaml")
        else "modules: {}\n"
    )
    commit_all(repo, f"plant {marker}")


class TestAPlantedMarkerDoesNotMoveTheTrustedRoot:
    @pytest.mark.parametrize("marker", MARKERS)
    def test_the_named_project_still_governs(self, tmp_path, monkeypatch, marker):
        project, module = _prepare_layout(tmp_path)
        _plant(module, marker)

        monkeypatch.chdir(module)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))

        assert _gate_root().resolve() == module.resolve(), "the SCAN is unchanged"
        assert _gate_config_root().resolve() == project.resolve()

    def test_a_marker_in_a_subdirectory_of_the_repo_does_not_either(
        self, tmp_path, monkeypatch
    ):
        """Walking up from a deeper cwd finds it first; it is still inside
        the tree being scanned, so it is still the Worker's to write."""
        project, module = _prepare_layout(tmp_path)
        deep = module / "src" / "pkg"
        deep.mkdir(parents=True)
        write(module, "src/pkg/a.py", "x = 1\n")
        _plant(deep, ".rite/brief.yaml")

        monkeypatch.chdir(deep)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))

        assert _gate_config_root().resolve() == project.resolve()

    def test_nor_one_reached_through_a_symlinked_cwd(self, tmp_path, monkeypatch):
        """⚠ **This passes for a reason that is NOT the resolve() call**, and
        saying so is the point: `Path.cwd()` returns an already-resolved
        path on macOS and Linux, so the link never reaches the comparison.
        Kept as the end-to-end shape, with `TestWhatCountsAsInside` below
        pinning the resolution itself — a mutant that compared unresolved
        paths left this test green."""
        project, module = _prepare_layout(tmp_path)
        _plant(module, ".rite/brief.yaml")
        link = tmp_path / "by-another-name"
        link.symlink_to(module)

        monkeypatch.chdir(link)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))

        assert _gate_config_root().resolve() == project.resolve()


class TestWhatCountsAsInside:
    """`_inside` decides whose rules the gate trusts, so it is asserted
    directly rather than only through a cwd that the OS has already
    resolved for us."""

    def test_a_path_under_the_tree_is_inside(self, tmp_path):
        from rite_ai.cli.main import _inside

        outer = tmp_path / "repo"
        (outer / "a" / "b").mkdir(parents=True)

        assert _inside(outer / "a" / "b", outer)
        assert _inside(outer, outer)

    def test_a_sibling_is_not(self, tmp_path):
        from rite_ai.cli.main import _inside

        (tmp_path / "repo").mkdir()
        (tmp_path / "other").mkdir()

        assert not _inside(tmp_path / "other", tmp_path / "repo")

    def test_an_ancestor_is_not(self, tmp_path):
        """The `rite prepare` shape: the project root is ABOVE the scanned
        repository, so it is not inside it and still governs."""
        from rite_ai.cli.main import _inside

        (tmp_path / "proj" / "mod").mkdir(parents=True)

        assert not _inside(tmp_path / "proj", tmp_path / "proj" / "mod")

    def test_a_symlinked_path_is_still_inside(self, tmp_path):
        """⚠ **The claim the end-to-end test cannot make.** An unresolved
        `is_relative_to` is a string comparison wearing a path's clothes: a
        planted marker reached by a link would read as outside the
        repository it is actually in, and the gate would trust it."""
        from rite_ai.cli.main import _inside

        outer = tmp_path / "repo"
        (outer / ".rite").mkdir(parents=True)
        link = tmp_path / "by-another-name"
        link.symlink_to(outer)

        assert _inside(link / ".rite", outer)

    def test_a_dot_dot_path_is_still_outside(self, tmp_path):
        from rite_ai.cli.main import _inside

        (tmp_path / "repo").mkdir()
        (tmp_path / "other").mkdir()

        assert not _inside(tmp_path / "repo" / ".." / "other", tmp_path / "repo")

    def test_no_outer_tree_is_not_inside_anything(self):
        from rite_ai.cli.main import _inside

        assert not _inside(Path("/anywhere"), None)

    def test_a_path_it_cannot_resolve_counts_as_INSIDE(self, monkeypatch, tmp_path):
        """⚠ Fail-closed, deliberately. "Cannot tell" must not hand the
        choice back to the tree being scanned: treating it as outside would
        let an unresolvable path move the gate's trusted rules, which is the
        hole this ticket closes."""
        from rite_ai.cli import main as main_mod

        def boom(self, *a, **kw):
            raise OSError("too many levels of symbolic links")

        monkeypatch.setattr(Path, "resolve", boom)

        assert main_mod._inside(tmp_path / "x", tmp_path)


class TestTheLayoutsThatMustKeepWorking:
    """⚠ A fix that pinned the root by breaking `rite prepare` would be
    worse than the hole: every suppression the project declared would come
    back as a blocking finding with a reason already written for it, which
    is how a gate gets switched off."""

    def test_the_prepare_layout_still_reads_the_projects_own_rules(
        self, tmp_path, monkeypatch
    ):
        """The marker is ABOVE the repository, so it is not on the branch
        being pushed and is not the Worker's to write."""
        project, module = _prepare_layout(tmp_path)

        monkeypatch.chdir(module)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))

        assert _gate_config_root().resolve() == project.resolve()

    def test_and_without_the_override_too(self, tmp_path, monkeypatch):
        project, module = _prepare_layout(tmp_path)

        monkeypatch.chdir(module)
        monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)

        assert _gate_config_root().resolve() == project.resolve()

    def test_a_single_repo_project_reads_its_own_committed_rules(
        self, tmp_path, monkeypatch
    ):
        """⚠ The case the naive fix breaks. Here the project root IS the
        repository: `.rite/brief.yaml` is committed inside the scanned tree
        by the Owner, and it must still govern — a marker inside the
        repository is only untrustworthy when it DISAGREES with a project
        rite was told."""
        repo = tmp_path / "solo"
        repo.mkdir()
        init_repo(repo)
        write(repo, ".rite/brief.yaml", "project:\n  name: solo\n  role: owner\n")
        write(repo, "a.py", "x = 1\n")
        commit_all(repo, "base")

        monkeypatch.chdir(repo)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(repo))

        assert _gate_config_root().resolve() == repo.resolve()

    def test_a_single_repo_project_with_no_override_too(self, tmp_path, monkeypatch):
        repo = tmp_path / "solo"
        repo.mkdir()
        init_repo(repo)
        write(repo, ".rite/brief.yaml", "project:\n  name: solo\n  role: owner\n")
        write(repo, "a.py", "x = 1\n")
        commit_all(repo, "base")

        monkeypatch.chdir(repo)
        monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)

        assert _gate_config_root().resolve() == repo.resolve()

    def test_the_override_still_answers_with_no_marker_anywhere(
        self, tmp_path, monkeypatch
    ):
        """The sandboxed case the override was added for."""
        repo = tmp_path / "repo"
        repo.mkdir()
        init_repo(repo)
        write(repo, "a.txt", "a\n")
        commit_all(repo, "base")
        named = tmp_path / "named"
        (named / ".rite").mkdir(parents=True)
        (named / ".rite" / "brief.yaml").write_text(
            "project:\n  name: n\n  role: owner\n"
        )

        monkeypatch.chdir(repo)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(named))

        assert _gate_config_root().resolve() == named.resolve()


@requires_gitleaks
class TestTheSuppressionFileItObeysDoesNotMove:
    """⚠ **The property the ticket is actually about**, end to end: not
    which path a function returns, but which file's suppressions the gate
    OBEYS. Real gitleaks, a real planted token, a real suppression."""

    def _fingerprint(self, module: Path, project: Path, monkeypatch) -> str:
        monkeypatch.chdir(module)
        monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))
        first = CliRunner().invoke(cli, ["publish", "check"])
        assert first.exit_code == 2, first.output
        return next(
            line.split("fingerprint:")[1].strip()
            for line in first.output.splitlines()
            if "fingerprint:" in line
        )

    def test_a_worker_authored_suppression_does_not_clear_the_gate(
        self, tmp_path, monkeypatch
    ):
        project, module = _prepare_layout(tmp_path)
        write(module, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
        commit_all(module, "add creds")
        fingerprint = self._fingerprint(module, project, monkeypatch)

        # The Worker plants a marker AND the suppression beside it, inside
        # its own repository — the whole attack in two commits.
        write(
            module,
            ".rite/brief.yaml",
            "project:\n  name: mine\n  role: owner\n",
        )
        write(
            module,
            ".rite/gitleaksignore",
            f"{fingerprint}  # not a real credential, honestly\n",
        )
        commit_all(module, "suppress my own gate")

        again = CliRunner().invoke(cli, ["publish", "check"])

        assert again.exit_code == 2, (
            "the Worker's own suppression file cleared the gate — the "
            "config root was hijacked:\n" + again.output
        )

    def test_the_owners_suppression_at_the_project_root_still_clears_it(
        self, tmp_path, monkeypatch
    ):
        """⚠ The control, and without it the test above would pass on a gate
        that had simply stopped reading suppressions at all."""
        project, module = _prepare_layout(tmp_path)
        write(module, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
        commit_all(module, "add creds")
        fingerprint = self._fingerprint(module, project, monkeypatch)

        (project / ".rite" / "gitleaksignore").write_text(
            f"{fingerprint}  # reviewed by the Owner: a test fixture\n"
        )

        again = CliRunner().invoke(cli, ["publish", "check"])

        assert again.exit_code == 0, again.output

    def test_the_owners_suppression_wins_even_with_a_marker_planted(
        self, tmp_path, monkeypatch
    ):
        """Both files present: the Owner's at the project root, a planted
        marker in the repository. The Owner's is the one obeyed."""
        project, module = _prepare_layout(tmp_path)
        write(module, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
        commit_all(module, "add creds")
        fingerprint = self._fingerprint(module, project, monkeypatch)

        (project / ".rite" / "gitleaksignore").write_text(
            f"{fingerprint}  # reviewed by the Owner\n"
        )
        _plant(module, ".rite/brief.yaml")

        again = CliRunner().invoke(cli, ["publish", "check"])

        assert again.exit_code == 0, again.output
