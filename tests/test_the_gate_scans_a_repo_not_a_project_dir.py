"""The publish gate's root must be a git REPOSITORY, whatever the layout.

🔴 SCRUM-60. `rite prepare` lays a project out as a plain directory holding
`.rite/` with each module repository as a SUBDIRECTORY — the project root
(`~/projects/yoloAI`) is not a git repo, the repo is `~/projects/yoloAI/yoloai`.
`_gate_root` preferred the project marker unconditionally, so it handed the
gate a directory git knows nothing about and every run died with
`'git ls-files' failed: fatal: not a git repository`.

That is not a cosmetic error. The `pre-push` hook fails CLOSED, so it refused
every push from the layout, and `rite deliver` could therefore never push a
branch or open a pull request for it — the gate was not strict, it was broken,
and the two are indistinguishable from the exit code alone.

Asserted against real directories in the idiom of `test_nested_project_root`:
build the shape on disk and ask what resolves, and then run the two entry
points that actually matter (`publish check` and `publish pre-push`) against a
planted secret, because the defect was that neither could run at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import (
    PROJECT_ROOT_ENV,
    _gate_config_root,
    _gate_root,
    cli,
)
from rite_ai.gate import gitleaks_runner
from tests.gate_helpers import commit_all, init_repo, write

requires_gitleaks = pytest.mark.skipif(
    gitleaks_runner.find_gitleaks_binary() is None, reason="gitleaks not installed"
)

# Split so this file does not trip the very scan it is testing when the gate
# runs over rite's own repository.
A_PLANTED_TOKEN = "ghp_" + "7Qk2mZr9VtXbN4pLs8DwH3JcY6FaE1RuG0Oi"


def _prepare_layout(tmp_path: Path) -> tuple[Path, Path]:
    """`(project_root, module_repo)` in the shape `rite prepare` produces:
    the project root carries the markers and is NOT a repository; the module
    beneath it is."""
    project = tmp_path / "yoloAI"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "brief.yaml").write_text(
        "project:\n  name: yoloAI\n  role: owner\n"
    )
    (project / ".rite" / "modules.yaml").write_text(
        "modules:\n  - name: yoloai\n    path: yoloai\n    branch: main\n"
    )
    module = project / "yoloai"
    module.mkdir()
    init_repo(module)
    write(module, "README.md", "hello\n")
    commit_all(module, "base")
    return project, module


def test_the_project_root_is_not_always_a_repository(tmp_path, monkeypatch):
    """The shape itself, stated once: if this assertion ever fails the rest of
    this file is testing something other than SCRUM-60."""
    project, module = _prepare_layout(tmp_path)
    assert not (project / ".git").exists()
    monkeypatch.chdir(module)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)

    assert _gate_root().resolve() == module.resolve()


def test_the_gate_root_is_the_repo_from_a_subdirectory_too(tmp_path, monkeypatch):
    """A hook or a command run deeper in the module still gets the repo —
    not the subdirectory, which would scan a slice and call it the tree."""
    project, module = _prepare_layout(tmp_path)
    nested = module / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)

    assert _gate_root().resolve() == module.resolve()


def test_rite_project_root_is_honoured_when_cwd_is_inside_it(tmp_path, monkeypatch):
    """`RITE_PROJECT_ROOT` is how a sandboxed Worker is TOLD its root rather
    than finding it, and `_gate_root` consulted it nowhere — so the gate and
    every other command could disagree about which tree they meant."""
    outer = tmp_path / "named"
    outer.mkdir()
    init_repo(outer)
    write(outer, "a.txt", "a\n")
    commit_all(outer, "base")
    inner = outer / "nested"
    inner.mkdir()

    monkeypatch.chdir(inner)
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(outer))
    assert _gate_root().resolve() == outer.resolve()


def test_an_unrelated_rite_project_root_is_not_followed(tmp_path, monkeypatch):
    """The override names a tree, not a licence to scan a different
    repository. The gate is asking WHICH TREE to scan, and the pre-push hook
    has already been handed a revision range belonging to the repository git
    is pushing from: feeding one repository's shas to another made the gate
    exit 3 with `fatal: bad object <sha>` — the SCRUM-60 push refusal again,
    in a new shape. The sandbox sets this variable on every Worker.
    """
    project, module = _prepare_layout(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    init_repo(other)
    write(other, "a.txt", "a\n")
    commit_all(other, "base")

    monkeypatch.chdir(module)
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(other))
    assert _gate_root().resolve() == module.resolve()


def test_a_project_root_that_is_a_repo_still_wins(tmp_path, monkeypatch):
    """The preference that made SCRUM-60 possible is still right when the
    project root qualifies: a scanned project must use its OWN `.rite/`
    suppressions, so the repo root beats the subdirectory cwd sits in."""
    project = tmp_path / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "brief.yaml").write_text(
        "project:\n  name: proj\n  role: owner\n"
    )
    (project / ".rite" / "modules.yaml").write_text("modules: {}\n")
    init_repo(project)
    write(project, "README.md", "hi\n")
    commit_all(project, "base")
    nested = project / "src"
    nested.mkdir()

    monkeypatch.chdir(nested)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    assert _gate_root().resolve() == project.resolve()


@requires_gitleaks
def test_publish_check_scans_the_module_repo_in_the_prepare_layout(
    tmp_path, monkeypatch
):
    """The first of the two broken entry points, end to end: it must reach a
    verdict ON the planted secret rather than EXIT_ERROR before scanning."""
    project, module = _prepare_layout(tmp_path)
    write(module, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
    commit_all(module, "add creds")

    monkeypatch.chdir(module)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    result = CliRunner().invoke(cli, ["publish", "check"])

    assert "not a git repository" not in result.output
    assert "github-pat" in result.output
    assert result.exit_code == 2  # EXIT_FAIL — found it, did not fail to run


@requires_gitleaks
def test_the_pre_push_hook_path_scans_the_module_repo_too(tmp_path, monkeypatch):
    """The second entry point, and the one that blocked every push. Driven
    through git's own pre-push protocol on stdin — the hook is a different
    code path from `check` and the fix has to cover both.

    The secret is in HISTORY and removed from the working tree, so a
    working-tree-only scan would report clean.
    """
    project, module = _prepare_layout(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "work"], cwd=module, check=True)
    write(module, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
    commit_all(module, "add creds")
    subprocess.run(["git", "rm", "-q", "creds.py"], cwd=module, check=True)
    write(module, "feature.txt", "feature\n")
    sha = commit_all(module, "remove creds, add feature")
    assert not (module / "creds.py").exists()

    monkeypatch.chdir(module)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    zeros = "0" * 40
    result = CliRunner().invoke(
        cli,
        ["publish", "pre-push"],
        input=f"refs/heads/work {sha} refs/heads/work {zeros}\n",
    )

    assert "not a git repository" not in result.output
    assert "github-pat" in result.output
    # Non-zero is what aborts the push. Anything else publishes the secret.
    assert result.exit_code == 2


def test_a_project_root_inside_a_larger_repo_keeps_its_own_rules(tmp_path, monkeypatch):
    """Scanned at the repository, ruled by the project — the two questions
    that were one variable.

    Scanning the project SUBDIRECTORY was the obvious way to keep its rules
    and it is quietly wrong: `git ls-files` run there emits subdirectory-
    relative paths while gitleaks and `files_touched_by` emit repository-
    relative ones, so a finding in a file the push ADDS was demoted to
    "already in the repository, NOT from this push — not blocking".

    Scanning the repository and reading rules from the repository is wrong
    the other way: the project's suppressions vanish, and each one comes
    back as a blocking finding that already has a reason written for it.
    """
    outer = tmp_path / "big"
    outer.mkdir()
    init_repo(outer)
    write(outer, "top.txt", "top\n")
    project = outer / "sub" / "proj"
    (project / ".rite").mkdir(parents=True)
    (project / ".rite" / "brief.yaml").write_text(
        "project:\n  name: inner\n  role: owner\n"
    )
    (project / ".rite" / "modules.yaml").write_text("modules: {}\n")
    write(project, "inner.txt", "inner\n")
    commit_all(outer, "base")

    monkeypatch.chdir(project)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    assert _gate_root().resolve() == outer.resolve()
    assert _gate_config_root().resolve() == project.resolve()


@requires_gitleaks
def test_the_prepare_layout_uses_the_projects_own_suppressions(tmp_path, monkeypatch):
    """The half of SCRUM-60 that is not about crashing. The gate scans the
    module repository, and `.rite/gitleaksignore` lives at the PROJECT root
    above it — so resolving both through the scanned tree would lose every
    suppression the project declared and block on findings someone had
    already signed off.
    """
    project, module = _prepare_layout(tmp_path)
    write(module, "creds.py", f'TOKEN = "{A_PLANTED_TOKEN}"\n')
    commit_all(module, "add creds")

    monkeypatch.chdir(module)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    assert _gate_root().resolve() == module.resolve()
    assert _gate_config_root().resolve() == project.resolve()

    # Unsuppressed: it blocks, and prints the fingerprint to suppress with.
    first = CliRunner().invoke(cli, ["publish", "check"])
    assert first.exit_code == 2
    fingerprint = next(
        line.split("fingerprint:")[1].strip()
        for line in first.output.splitlines()
        if "fingerprint:" in line
    )

    # Signed off at the PROJECT root, which is not the tree being scanned.
    (project / ".rite" / "gitleaksignore").write_text(
        f"{fingerprint}  # a planted test token, not a real credential\n"
    )
    second = CliRunner().invoke(cli, ["publish", "check"])

    assert second.exit_code == 0, second.output
    # And it says so: the tree scanned and the tree the rules came from are
    # different directories, which is the fact worth disclosing.
    assert f"rules and suppressions from {project}" in second.output


@requires_gitleaks
def test_publish_check_itself_says_what_it_scanned(tmp_path, monkeypatch):
    """At the CLI, not in a formatter it does not call. `publish check` builds
    its own output, so a coverage line added only to `format_report` reaches
    the hook and `python -m rite_ai.gate` and never the command SCRUM-63 was
    filed against — which printed a bare `gate: clean` over an empty range.
    """
    init_repo(tmp_path)
    write(tmp_path, "README.md", "hello\n")
    commit_all(tmp_path, "base")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    result = CliRunner().invoke(cli, ["publish", "check", "--rev-range", "HEAD..HEAD"])

    assert result.exit_code == 0
    assert "0 commit(s)" in result.output
    assert "NO history was in range" in result.output
    assert "gitleaks ruleset" in result.output


@requires_gitleaks
def test_the_module_entrypoint_makes_the_same_split(tmp_path, monkeypatch):
    """`python -m rite_ai.gate check` is in `GATE_INVOCATIONS` and is what
    rite's own CI workflow runs — the layer local configuration cannot
    switch off. It resolved one root, so in the prepare layout it ran on a
    default `ProjectConfig` (a missing config.yaml returns one, with no
    error): every declared scan pattern dropped and every suppression
    ignored, silently.
    """
    from rite_ai.gate.__main__ import _roots

    project, module = _prepare_layout(tmp_path)
    monkeypatch.chdir(module)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)

    scan_root, config_root = _roots()
    assert scan_root.resolve() == module.resolve()
    assert config_root.resolve() == project.resolve()


def test_a_clone_with_its_own_rite_dir_answers_for_itself_when_nobody_said(
    tmp_path, monkeypatch
):
    """The nearest marker beats a WALK, which is the opposite order from
    `_find_project_root` and deliberately so. That function asks which
    project a session BELONGS to; this asks whose rules govern the tree in
    front of us, and a clone carrying its own `.rite/` answers for itself —
    an outer project's suppressions have fingerprints that cannot match an
    inner repository anyway, so preferring them loses the ones that could.

    ⚠ **Narrowed by SCRUM-76, and this test with it.** It used to set
    `RITE_PROJECT_ROOT` and assert the module won anyway. That is the
    config-root hijack: `PROJECT_MARKERS` are two ordinary files, so
    anything able to commit to the scanned repository could plant one and
    move the gate's trusted suppression list onto its own branch. The
    marker still answers when rite has named NO project, which is a person
    at their own checkout — nobody has been overruled, because nobody said
    anything. `test_the_gate_config_root_cannot_be_hijacked.py` holds the
    other half.
    """
    project, module = _prepare_layout(tmp_path)
    (module / ".rite").mkdir()
    (module / ".rite" / "brief.yaml").write_text(
        "project:\n  name: inner\n  role: owner\n"
    )

    monkeypatch.chdir(module)
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    assert _gate_config_root().resolve() == module.resolve()
    assert project != module, "the layout must actually nest, or this is vacuous"


def test_the_override_still_answers_when_there_is_no_marker(tmp_path, monkeypatch):
    """The sandboxed case it was added for: a Worker told its project, with
    nothing to find by walking."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    write(repo, "a.txt", "a\n")
    commit_all(repo, "base")
    named = tmp_path / "named"
    (named / ".rite").mkdir(parents=True)
    (named / ".rite" / "brief.yaml").write_text("project:\n  name: n\n  role: owner\n")

    monkeypatch.chdir(repo)
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(named))
    assert _gate_config_root().resolve() == named.resolve()
