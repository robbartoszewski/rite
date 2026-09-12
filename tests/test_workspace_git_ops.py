from pathlib import Path

from rite_ai.workspace import git_ops
from tests.gate_helpers import commit_all, init_repo, write


def test_classify_known_network_signatures():
    assert (
        git_ops.classify_git_failure(
            "fatal: unable to access 'x': Could not resolve host: x"
        )
        == "network"
    )
    assert (
        git_ops.classify_git_failure(
            "ssh: connect to host example.com port 22: Connection refused"
        )
        == "network"
    )
    assert git_ops.classify_git_failure("Connection timed out") == "network"


def test_classify_unrecognised_failure_defaults_to_repo():
    """The conservative bias, stated in the module docstring: an unfamiliar
    failure must never be silently treated as 'just offline'."""
    assert git_ops.classify_git_failure("fatal: bad object HEAD") == "repo"
    assert git_ops.classify_git_failure("fatal: not a git repository") == "repo"
    assert git_ops.classify_git_failure("") == "repo"


def test_is_clean_true_on_clean_repo(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "initial")
    assert git_ops.is_clean(tmp_path) is True


def test_is_clean_false_on_dirty_repo(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "initial")
    write(tmp_path, "a.txt", "modified, uncommitted\n")
    assert git_ops.is_clean(tmp_path) is False


def test_is_clean_false_on_untracked_file(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "initial")
    write(tmp_path, "residue.txt", "leftover from a previous task\n")
    assert git_ops.is_clean(tmp_path) is False


def test_current_branch(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "initial")
    branch = git_ops.current_branch(tmp_path)
    assert branch in ("main", "master")  # depends on git's init.defaultBranch


def test_local_branch_exists(tmp_path: Path):
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "initial")
    current = git_ops.current_branch(tmp_path)
    assert git_ops.local_branch_exists(tmp_path, current)
    assert not git_ops.local_branch_exists(tmp_path, "does-not-exist")


def test_fetch_local_only_module_is_a_noop(tmp_path: Path):
    """A module with no remote (SPEC §8.2's local-only module) must not be
    treated as an error just because there's nothing to fetch from."""
    init_repo(tmp_path)
    write(tmp_path, "a.txt", "hello\n")
    commit_all(tmp_path, "initial")
    assert git_ops.fetch(tmp_path) is None


def test_clone_from_local_path(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    init_repo(source)
    write(source, "a.txt", "hello\n")
    commit_all(source, "initial")
    branch = git_ops.current_branch(source)

    dest = tmp_path / "dest"
    err = git_ops.clone(str(source), dest, branch)
    assert err is None
    assert (dest / "a.txt").read_text() == "hello\n"


def test_clone_bad_source_is_repo_error(tmp_path: Path):
    dest = tmp_path / "dest"
    err = git_ops.clone(str(tmp_path / "does-not-exist"), dest, "main")
    assert err is not None
    assert err.kind == "repo"


def test_fast_forward_pull_advances_when_possible(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    init_repo(source)
    write(source, "a.txt", "v1\n")
    commit_all(source, "v1")
    branch = git_ops.current_branch(source)

    clone_dir = tmp_path / "clone"
    git_ops.clone(str(source), clone_dir, branch)

    write(source, "a.txt", "v2\n")
    commit_all(source, "v2")

    err = git_ops.fetch(clone_dir)
    assert err is None
    err = git_ops.fast_forward_pull(clone_dir, branch)
    assert err is None
    assert (clone_dir / "a.txt").read_text() == "v2\n"


def test_fast_forward_pull_fails_cleanly_on_divergence(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    init_repo(source)
    write(source, "a.txt", "v1\n")
    commit_all(source, "v1")
    branch = git_ops.current_branch(source)

    clone_dir = tmp_path / "clone"
    git_ops.clone(str(source), clone_dir, branch)

    # diverge both sides
    write(source, "a.txt", "v2 from source\n")
    commit_all(source, "v2 upstream")
    write(clone_dir, "a.txt", "v2 from clone\n")
    commit_all(clone_dir, "v2 local, diverging")

    git_ops.fetch(clone_dir)
    err = git_ops.fast_forward_pull(clone_dir, branch)
    assert err is not None
    assert err.kind == "repo"
    # never silently resolved — local content must be untouched
    assert (clone_dir / "a.txt").read_text() == "v2 from clone\n"
