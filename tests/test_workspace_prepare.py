"""Tests for `prepare_workspace` against real temp git repos — including the
required known-bad-input proofs: a dirty tree must block, not be discarded;
a genuine repo error must block; only a real network failure may degrade."""

from __future__ import annotations

from pathlib import Path

from rite_ai.config.models import Module
from rite_ai.workspace import git_ops, prepare
from rite_ai.workspace.git_ops import GitError
from rite_ai.workspace.prepare import (
    STATUS_CLONED,
    STATUS_DIRTY,
    STATUS_READY,
    STATUS_READY_OFFLINE,
    STATUS_REPO_ERROR,
    prepare_module,
    prepare_workspace,
)
from tests.gate_helpers import commit_all, init_repo, write


def _make_upstream(root: Path, name: str = "backend") -> Path:
    upstream = root / f"_upstream_{name}"
    upstream.mkdir()
    init_repo(upstream)
    write(upstream, "app.py", "print('v1')\n")
    commit_all(upstream, "v1")
    return upstream


def test_first_clone_from_url_like_local_path(tmp_path: Path):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)

    worker_dir = tmp_path / "worker"
    result = prepare_module(worker_dir, module, project_root=tmp_path)

    assert result.ok
    assert result.status == STATUS_CLONED
    assert (worker_dir / "backend" / "app.py").exists()


def test_local_only_module_clones_from_project_root(tmp_path: Path):
    """SPEC §8.2's local-only module (no url) — this module's documented
    design choice: clone from the project root's own checkout."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    module_dir_in_root = project_root / "shared"
    module_dir_in_root.mkdir()
    init_repo(module_dir_in_root)
    write(module_dir_in_root, "types.py", "X = 1\n")
    commit_all(module_dir_in_root, "initial")
    branch = git_ops.current_branch(module_dir_in_root)

    module = Module(name="shared", path="shared/", url=None, branch=branch)
    worker_dir = project_root / "workers" / "alpha"
    result = prepare_module(worker_dir, module, project_root=project_root)

    assert result.ok, result.message
    assert result.status == STATUS_CLONED
    assert (worker_dir / "shared" / "types.py").read_text() == "X = 1\n"


def test_idempotent_rerun_is_ready(tmp_path: Path):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"

    first = prepare_module(worker_dir, module, project_root=tmp_path)
    assert first.ok and first.status == STATUS_CLONED

    second = prepare_module(worker_dir, module, project_root=tmp_path)
    assert second.ok
    assert second.status == STATUS_READY

    third = prepare_module(worker_dir, module, project_root=tmp_path)
    assert third.ok
    assert third.status == STATUS_READY


def test_pulls_new_upstream_commits_on_rerun(tmp_path: Path):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"

    prepare_module(worker_dir, module, project_root=tmp_path)
    assert (worker_dir / "backend" / "app.py").read_text() == "print('v1')\n"

    write(upstream, "app.py", "print('v2')\n")
    commit_all(upstream, "v2")

    result = prepare_module(worker_dir, module, project_root=tmp_path)
    assert result.ok and result.status == STATUS_READY
    assert (worker_dir / "backend" / "app.py").read_text() == "print('v2')\n"


def test_dirty_tree_blocks_and_is_never_touched(tmp_path: Path):
    """The load-bearing rule: uncommitted work is a signal, never silently
    discarded. Prep must refuse and leave the tree exactly as it was."""
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"

    prepare_module(worker_dir, module, project_root=tmp_path)
    write(worker_dir, "backend/app.py", "print('uncommitted local edit')\n")
    (worker_dir / "backend" / "residue.txt").write_text("leftover from last task\n")

    result = prepare_module(worker_dir, module, project_root=tmp_path)

    assert not result.ok
    assert result.status == STATUS_DIRTY
    # nothing touched — the uncommitted edit and the residue file both survive
    assert (worker_dir / "backend" / "app.py").read_text() == (
        "print('uncommitted local edit')\n"
    )
    assert (worker_dir / "backend" / "residue.txt").exists()


def test_existing_non_git_directory_is_repo_error_not_overwritten(tmp_path: Path):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"
    (worker_dir / "backend").mkdir(parents=True)
    (worker_dir / "backend" / "not-a-repo.txt").write_text("surprise content\n")

    result = prepare_module(worker_dir, module, project_root=tmp_path)

    assert not result.ok
    assert result.status == STATUS_REPO_ERROR
    # never silently clobbered
    assert (
        worker_dir / "backend" / "not-a-repo.txt"
    ).read_text() == "surprise content\n"


def test_offline_fetch_degrades_without_blocking(tmp_path: Path, monkeypatch):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"
    prepare_module(worker_dir, module, project_root=tmp_path)

    monkeypatch.setattr(
        prepare.git_ops,
        "fetch",
        lambda *a, **kw: GitError("Could not resolve host: github.com", kind="network"),
    )
    result = prepare_module(worker_dir, module, project_root=tmp_path)

    assert result.ok, result.message
    assert result.status == STATUS_READY_OFFLINE
    assert (worker_dir / "backend" / "app.py").exists()  # untouched, not corrupted


def test_non_network_fetch_failure_blocks_not_degrades(tmp_path: Path, monkeypatch):
    """A repo-kind failure must NOT be waved through as "just offline" —
    this is the specific distinction the task asked for."""
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"
    prepare_module(worker_dir, module, project_root=tmp_path)

    monkeypatch.setattr(
        prepare.git_ops,
        "fetch",
        lambda *a, **kw: GitError(
            "fatal: bad object refs/remotes/origin/HEAD", kind="repo"
        ),
    )
    result = prepare_module(worker_dir, module, project_root=tmp_path)

    assert not result.ok
    assert result.status == STATUS_REPO_ERROR


def test_ticket_branch_created_from_default_branch(tmp_path: Path):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"

    result = prepare_module(
        worker_dir, module, project_root=tmp_path, branch="ticket-42"
    )

    assert result.ok, result.message
    assert result.branch == "ticket-42"
    assert git_ops.current_branch(worker_dir / "backend") == "ticket-42"


def test_resuming_a_ticket_branch_is_idempotent(tmp_path: Path):
    upstream = _make_upstream(tmp_path)
    branch = git_ops.current_branch(upstream)
    module = Module(name="backend", path="backend/", url=str(upstream), branch=branch)
    worker_dir = tmp_path / "worker"

    first = prepare_module(
        worker_dir, module, project_root=tmp_path, branch="ticket-42"
    )
    assert first.ok

    second = prepare_module(
        worker_dir, module, project_root=tmp_path, branch="ticket-42"
    )
    assert second.ok
    assert second.status == STATUS_READY
    assert second.branch == "ticket-42"


def test_summary_of_zero_modules_says_so_not_a_blank_line(tmp_path: Path):
    """Previously `summary()` on a worker with no modules assigned
    returned "" — `rite prepare` printed one blank line and nothing
    else, indistinguishable from a crash. Found running `rite prepare`
    by hand on a worker created before its module was registered."""
    result = prepare_workspace(tmp_path, [], tmp_path)
    assert result.summary().strip() != ""
    assert "no modules assigned" in result.summary()


def test_workspace_prep_aggregates_multiple_modules(tmp_path: Path):
    backend_upstream = _make_upstream(tmp_path, "backend")
    frontend_upstream = _make_upstream(tmp_path, "frontend")
    branch = git_ops.current_branch(backend_upstream)
    modules = [
        Module(
            name="backend", path="backend/", url=str(backend_upstream), branch=branch
        ),
        Module(
            name="frontend", path="frontend/", url=str(frontend_upstream), branch=branch
        ),
    ]
    worker_dir = tmp_path / "worker"

    result = prepare_workspace(worker_dir, modules, project_root=tmp_path)

    assert result.ok
    assert len(result.modules) == 2
    assert result.blocking == []


def test_workspace_prep_reports_partial_blocking(tmp_path: Path):
    backend_upstream = _make_upstream(tmp_path, "backend")
    branch = git_ops.current_branch(backend_upstream)
    modules = [
        Module(
            name="backend", path="backend/", url=str(backend_upstream), branch=branch
        ),
        Module(name="frontend", path="frontend/", url=None, branch="main"),
    ]
    worker_dir = tmp_path / "worker"

    # frontend has no url and no project-root copy — its clone must fail cleanly
    result = prepare_workspace(worker_dir, modules, project_root=tmp_path)

    assert not result.ok
    assert len(result.blocking) == 1
    assert result.blocking[0].module == "frontend"
    # backend still succeeded independently
    backend_result = next(m for m in result.modules if m.module == "backend")
    assert backend_result.ok
