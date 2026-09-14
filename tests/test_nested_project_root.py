"""A module that is itself a rite project must not capture its own workers.

`rite prepare` clones each module UNDER the project root, so a module that is
a rite repo lands at `workers/<w>/<module>/` — inside the very tree being
coordinated. `_find_project_root` walks `[cwd, *cwd.parents]` with **cwd
first**, so a session started in that clone used to resolve to the CLONE, and
wrote its claims to `workers/<w>/<module>/.rite/claims.json`.

Every worker then holds a private ledger. Nothing is shared, so nothing ever
collides, and the run reports zero collisions, zero refusals and zero
force-releases — the exclusion guarantee absent, presenting as a flawless run.
That is this project's standing defect class: a measurement (no collisions)
standing in for the property (mutual exclusion), when it actually means
nothing was ever shared.

Every rite project ships this hazard, because `scaffold.AUTHORED_CONFIG`
re-includes nine paths under `.rite/` in the `.gitignore` that `rite init`
writes — so a clone of any rite project carries a tracked `.rite/` with it.
Phase 1's own definition of done includes configuring a real, separate
    codebase as a rite project, which would have hit this identically.

Asserted as a property against real directories, in the idiom of
`TestTheSuiteDoesNotWriteIntoThisRepository`: build the shape and ask what
resolves, rather than trusting a fixture to stand in for it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from rite_ai.cli.main import (
    PROJECT_ROOT_ENV,
    _find_project_root,
    _has_project_in_scope,
    _is_project,
    cli,
)


def _project(root: Path) -> Path:
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "brief.yaml").write_text(
        "project:\n  name: outer\n  role: owner\n"
    )
    (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
    return root


def _rite_like_repo(root: Path) -> Path:
    """A clone of rite's OWN repository: a `.rite/` holding the gate's
    suppressions and the review checklist, and no project declaration.
    Verified against the real thing — `git ls-files .rite/` on rite reports
    exactly these two."""
    (root / ".rite").mkdir(parents=True)
    (root / ".rite" / "gitleaksignore").write_text("# suppressions\n")
    (root / ".rite" / "review-checklist.md").write_text("# checklist\n")
    return root


class TestAModuleThatIsARiteRepo:
    def test_a_worker_clone_of_rite_resolves_the_OUTER_project(
        self, tmp_path, monkeypatch
    ):
        """THE REGRESSION. Before this, the clone resolved to itself."""
        outer = _project(tmp_path / "acme")
        clone = _rite_like_repo(outer / "workers" / "w1" / "rite")

        monkeypatch.chdir(clone)
        assert _find_project_root() == outer, (
            "the worker's own clone captured the project root — every worker "
            "would get a private claims ledger and never collide"
        )

    def test_rites_own_repo_is_not_a_project(self, tmp_path):
        """The distinction the marker rests on. A `.rite/` is not a project;
        `.rite/brief.yaml` or `.rite/modules.yaml` is."""
        assert not _is_project(_rite_like_repo(tmp_path / "rite"))
        assert _is_project(_project(tmp_path / "acme"))

    def test_scope_detection_agrees_with_root_resolution(self, tmp_path, monkeypatch):
        """`rite status` picks single-project vs aggregate mode from
        `_has_project_in_scope`. If it disagreed with `_find_project_root`,
        status would aggregate while every other command used the clone."""
        clone = _rite_like_repo(tmp_path / "rite")
        monkeypatch.chdir(clone)
        assert not _has_project_in_scope()

        outer = _project(tmp_path / "acme")
        nested = _rite_like_repo(outer / "workers" / "w1" / "rite")
        monkeypatch.chdir(nested)
        assert _has_project_in_scope()


class TestTheExplicitOverride:
    """The general fix. The marker handles a module that is not a scaffolded
    project; a module that IS one tracks `modules.yaml` and would still
    capture. Nothing but an explicit override answers that case."""

    def test_the_override_wins_over_the_walk(self, tmp_path, monkeypatch):
        outer = _project(tmp_path / "acme")
        inner = _project(outer / "workers" / "w1" / "sub")  # a REAL nested project

        monkeypatch.chdir(inner)
        assert _find_project_root() == inner, (
            "precondition: the walk finds the inner one"
        )

        with patch.dict(os.environ, {PROJECT_ROOT_ENV: str(outer)}):
            assert _find_project_root() == outer
            assert _has_project_in_scope()

    def test_the_override_is_resolved_and_expanded(self, tmp_path, monkeypatch):
        outer = _project(tmp_path / "acme")
        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {PROJECT_ROOT_ENV: str(outer) + "/./"}):
            assert _find_project_root() == outer.resolve()


class TestDoctorSaysSoWhenAModuleIsItselfARiteProject:
    """The half the marker cannot fix, and the reason it needs a voice.

    Hardening `_find_project_root` to a FILE fixed the case where the inner
    repo merely carries a committed `.rite/` — rite's own repository is that
    case. It cannot fix a module that is a fully scaffolded rite project,
    because such a project tracks `brief.yaml` and `modules.yaml` by design:
    `scaffold.AUTHORED_CONFIG` re-includes them in the `.gitignore` that
    `rite init` writes. The walk finds the inner marker first and is right to.

    Only `RITE_PROJECT_ROOT` answers it, and nothing announced that. Phase
    1's definition of done includes configuring a real, separate
    codebase as a rite project, so
    this is the configuration a real run walks into — and its symptom is
    silence: a private ledger, zero collisions, a flawless-looking run.
    """

    def _project_with_module(self, tmp_path, module_is_a_project: bool):
        root = _project(tmp_path / "acme")
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  sub:\n    path: sub\n    description: ''\n"
        )
        sub = root / "sub"
        sub.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
        if module_is_a_project:
            _project(sub)
        return root

    def _doctor(self, root):
        with patch.dict(os.environ, {PROJECT_ROOT_ENV: str(root)}):
            return CliRunner().invoke(cli, ["doctor"])

    def test_doctor_names_the_module_and_what_to_set(self, tmp_path):
        root = self._project_with_module(tmp_path, module_is_a_project=True)

        out = self._doctor(root).output

        assert "is itself a rite project" in out, out
        assert "module sub" in out
        assert PROJECT_ROOT_ENV in out, "reported without the remedy"

    def test_it_counts_as_a_problem_not_a_note(self, tmp_path):
        """`doctor` exits non-zero on problems, and a private claims ledger is
        one — it is the exclusion guarantee silently absent.

        Asserted as a DIFFERENCE against the same project with an ordinary
        module, not as a bare non-zero exit. `doctor` on a bare fixture
        already exits non-zero for unrelated reasons (no credentials, no
        hook), so `!= 0` passes with this check deleted — measured. The
        contribution is what has to be visible."""
        nested = self._doctor(
            self._project_with_module(tmp_path / "a", module_is_a_project=True)
        ).output
        plain = self._doctor(
            self._project_with_module(tmp_path / "b", module_is_a_project=False)
        ).output

        def count(out: str) -> int:
            m = re.search(r"(\d+) problem\(s\) found", out)
            return int(m.group(1)) if m else 0

        assert count(nested) == count(plain) + 1, (
            f"the nested-project module added no problem: "
            f"{count(nested)} vs {count(plain)}"
        )

    def test_an_ordinary_module_is_not_flagged(self, tmp_path):
        """The other half: a module that is a plain git repo says nothing.
        A check that fires on every module would be ignored within a day."""
        root = self._project_with_module(tmp_path, module_is_a_project=False)

        assert "is itself a rite project" not in self._doctor(root).output
