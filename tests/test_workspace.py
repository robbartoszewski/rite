import subprocess
from pathlib import Path

from rite_ai.config.parse import parse_modules, parse_worker
from rite_ai.workspace import add_module, add_worker, remove_module, remove_worker


def _init_project(tmp_path: Path) -> Path:
    """Set up a minimal .rite/ directory so workspace operations can run."""
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "brief.yaml").write_text("project:\n  name: test\n  role: owner\n")
    return tmp_path


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        capture_output=True,
    )


class TestAddModule:
    def test_adds_local_module(self, tmp_path: Path):
        root = _init_project(tmp_path)
        result = add_module(root, "backend", description="The API")
        assert result.ok
        assert result.module is not None
        assert result.module.name == "backend"
        assert (root / "backend" / ".git").exists()

        modules = parse_modules(root / ".rite" / "modules.yaml")
        assert isinstance(modules, list)
        assert len(modules) == 1
        assert modules[0].name == "backend"
        assert modules[0].description == "The API"

    def test_rejects_duplicate_name(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_module(root, "backend")
        result = add_module(root, "backend")
        assert not result.ok
        assert "already registered" in result.message

    def test_requires_rite_dir(self, tmp_path: Path):
        result = add_module(tmp_path, "backend")
        assert not result.ok
        assert "rite init" in result.message

    def test_preserves_existing_modules(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_module(root, "backend")
        add_module(root, "frontend", description="UI")
        modules = parse_modules(root / ".rite" / "modules.yaml")
        assert isinstance(modules, list)
        assert {m.name for m in modules} == {"backend", "frontend"}


class TestRemoveModule:
    def test_removes_module(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_module(root, "backend")
        result = remove_module(root, "backend")
        assert result.ok
        assert "deregistered" in result.message
        assert (root / "backend").is_dir()

        modules = parse_modules(root / ".rite" / "modules.yaml")
        assert isinstance(modules, list)
        assert len(modules) == 0

    def test_rejects_unknown_module(self, tmp_path: Path):
        root = _init_project(tmp_path)
        result = remove_module(root, "nope")
        assert not result.ok
        assert "not registered" in result.message


class TestAddWorker:
    def test_creates_worker_directory(self, tmp_path: Path):
        root = _init_project(tmp_path)
        _init_git(root)
        add_module(root, "backend")

        result = add_worker(root, "alpha", manager="alice")
        assert result.ok
        assert result.worker is not None
        assert result.worker.name == "alpha"
        assert (root / "workers" / "alpha").is_dir()
        assert (root / "workers" / "alpha" / "worker.yml").exists()
        assert (root / "workers" / "alpha" / "CLAUDE.md").exists()

    def test_worker_manifest_roundtrips(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_module(root, "backend")
        add_worker(root, "alpha", manager="alice")

        manifest = parse_worker(root / "workers" / "alpha" / "worker.yml")
        assert not hasattr(manifest, "message")
        assert manifest.name == "alpha"
        assert manifest.manager == "alice"
        assert "backend" in manifest.modules

    def test_rejects_duplicate_worker(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_worker(root, "alpha")
        result = add_worker(root, "alpha")
        assert not result.ok
        assert "already exists" in result.message

    def test_module_subset(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_module(root, "backend")
        add_module(root, "frontend")

        result = add_worker(root, "alpha", module_subset=["frontend"])
        assert result.ok
        manifest = parse_worker(root / "workers" / "alpha" / "worker.yml")
        assert manifest.modules == ["frontend"]

    def test_rejects_unknown_module_subset(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_module(root, "backend")
        result = add_worker(root, "alpha", module_subset=["nope"])
        assert not result.ok
        assert "unknown module" in result.message

    def test_worker_claude_md_has_worker_name(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_worker(root, "beta", manager="bob")
        md = (root / "workers" / "beta" / "CLAUDE.md").read_text()
        assert "Worker beta" in md
        assert "bob" in md
        assert "rite claim" in md

    def test_worker_claude_md_tells_it_to_run_the_detected_commands(
        self, tmp_path: Path
    ):
        """`rite init` detects each module's `Test:` and `Lint:` command and
        writes it into the module map in the project root's CLAUDE.md. Until
        this line existed, no instruction anywhere told a Worker to run
        either — the detection ran, the map was correct, and nothing
        consumed it.

        Detection with no consumer is the same defect shape as a module with
        no callers, one step further out: the artifact is right and inert.
        """
        root = _init_project(tmp_path)
        add_worker(root, "beta", manager="bob")
        md = (root / "workers" / "beta" / "CLAUDE.md").read_text()

        assert "test and lint" in md
        assert "`Test:` and `Lint:`" in md
        # It has to say WHERE, because a Worker's own CLAUDE.md is not the
        # file the module map is written into.
        assert "project root's `CLAUDE.md`" in md
        # And what to do when a command was not detected, rather than
        # leaving the Worker to invent one that exits 0.
        assert "not detected" in md

        workflow = md[md.index("## Workflow") : md.index("## What you must not do")]
        assert workflow.index("test and lint") < workflow.index("`/review`")


class TestRemoveWorker:
    def test_removes_worker_directory(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_worker(root, "alpha")
        assert (root / "workers" / "alpha").is_dir()

        result = remove_worker(root, "alpha")
        assert result.ok
        assert not (root / "workers" / "alpha").exists()

    def test_rejects_missing_worker(self, tmp_path: Path):
        root = _init_project(tmp_path)
        result = remove_worker(root, "nope")
        assert not result.ok
        assert "does not exist" in result.message

    def test_cleans_up_empty_workers_dir(self, tmp_path: Path):
        root = _init_project(tmp_path)
        add_worker(root, "alpha")
        remove_worker(root, "alpha")
        assert not (root / "workers").exists()


class TestAddModuleDefaultBranch:
    """`add_module` cloned with a hardcoded `--branch main`, so registering
    any repo whose default is `master`/`develop`/`trunk` failed outright:

        $ rite add module legacy https://github.com/acme/legacy.git
        git clone failed: fatal: Remote branch main not found in upstream origin

    Registering existing repos is the first thing a new project does, so
    this sat directly in the single-developer acceptance path.
    """

    def _remote_on(self, tmp_path: Path, branch: str) -> str:
        remote = tmp_path / "remote"
        remote.mkdir()
        subprocess.run(
            ["git", "init", "-b", branch], cwd=remote, capture_output=True, check=True
        )
        (remote / "f.txt").write_text("hello\n")
        subprocess.run(
            ["git", "add", "-A"], cwd=remote, capture_output=True, check=True
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-m",
                "init",
            ],
            cwd=remote,
            capture_output=True,
            check=True,
        )
        return str(remote)

    def _project(self, tmp_path: Path) -> Path:
        root = tmp_path / "proj"
        (root / ".rite").mkdir(parents=True)
        (root / ".rite" / "modules.yaml").write_text("modules: {}\n")
        return root

    def test_clones_a_master_branch_repo(self, tmp_path: Path):
        url = self._remote_on(tmp_path, "master")
        root = self._project(tmp_path)

        result = add_module(root, "legacy", url=url)

        assert result.ok, result.message
        assert (root / "legacy" / "f.txt").is_file()

    def test_records_the_branch_actually_checked_out(self, tmp_path: Path):
        url = self._remote_on(tmp_path, "trunk")
        root = self._project(tmp_path)

        result = add_module(root, "legacy", url=url)

        assert result.ok, result.message
        assert result.module.branch == "trunk", (
            f"recorded {result.module.branch!r} — a later `prepare` reads this "
            "back and would clone a branch that does not exist"
        )

    def test_explicit_branch_is_still_honoured(self, tmp_path: Path):
        url = self._remote_on(tmp_path, "master")
        root = self._project(tmp_path)
        subprocess.run(
            ["git", "branch", "feature"], cwd=url, capture_output=True, check=True
        )

        result = add_module(root, "legacy", url=url, branch="feature")

        assert result.ok, result.message
        assert result.module.branch == "feature"

    def test_a_main_branch_repo_still_works(self, tmp_path: Path):
        url = self._remote_on(tmp_path, "main")
        root = self._project(tmp_path)

        result = add_module(root, "legacy", url=url)

        assert result.ok, result.message
        assert result.module.branch == "main"
