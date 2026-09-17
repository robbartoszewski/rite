"""Regression tests for the defects found by rehearsal round 3.

Round 3 installed the real CLI the way `install.sh` installs it — `uv tool
install`, from a clone, outside the repo — and drove it from scratch
directories that were not rite projects. That is the surface rounds 1 and 2
never stood on: every earlier round ran either the test suite or a CLI built
from the source tree, and both of those are always inside a project.

Each test reproduces the ORIGINAL SYMPTOM rather than asserting the fixed
behaviour, and each was run against the previous commit to confirm it fails
there.
"""

from __future__ import annotations

import http.server
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from rite_ai.cli.main import cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path) -> Path:
    """A minimal but real project — enough `.rite/` for the guard to pass."""
    rite_dir = tmp_path / ".rite"
    rite_dir.mkdir()
    (rite_dir / "brief.yaml").write_text(
        "project:\n  name: acme\n  role: owner\n"
        "what:\n  kind: app\ntechnology:\n  languages:\n    - python\n"
    )
    (rite_dir / "modules.yaml").write_text("modules: {}\n")
    (rite_dir / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 10\n  stall_threshold: 3\n"
    )
    return tmp_path


# --- writing commands outside a project ------------------------------------


class TestWritersRefuseOutsideAProject:
    """Every command that WRITES `.rite/` state created that directory
    wherever it was run, and reported success.

    `rite claim src/ --worker alpha` typed one directory too high printed
    "claimed 1 path(s) for alpha" and exited 0, against a ledger that no
    other session reads — the exclusion guarantee gone, with exactly the
    output of a real claim. The phantom `.rite/` then captured every
    directory beneath it through `_find_project_root`'s walk-up. That is
    not hypothetical: this machine had a stray `/private/tmp/.rite` left
    by an earlier session, and `rite doctor` run from a scratch directory
    six levels below it reported `project: /private/tmp`.

    Round 2 found the same shape in `rite pool status` and fixed it in
    that one command.
    """

    # (argv, the phrase the command used to print on success)
    WRITERS = [
        (["claim", "src", "--worker", "alpha"], "claimed 1 path(s)"),
        (["release", "--worker", "alpha"], "released 0 claim(s)"),
        (["heartbeat", "--worker", "alpha"], "heartbeat recorded"),
        (["kb", "add", "https://example.com"], "added:"),
        (["kb", "refresh"], "nothing to refresh"),
        (["context", "add", "f.md", "when", "what"], "added f.md"),
        (["handover", "write", "--progress", "x"], ""),
        (["schedule", "set-timezone", "UTC"], "timezone set to"),
        (["schedule", "set", "09:00-18:00", "2"], "schedule updated"),
        (["scheduler-tick"], ""),
    ]

    @pytest.mark.parametrize("argv,old_success", WRITERS)
    def test_it_creates_no_rite_directory(
        self, tmp_path, monkeypatch, argv, old_success
    ):
        here = tmp_path / "somewhere-else"
        here.mkdir()
        monkeypatch.chdir(here)

        result = CliRunner().invoke(cli, argv)

        assert not (here / ".rite").exists(), (
            f"`rite {' '.join(argv)}` manufactured a .rite/ directory in a "
            "directory that is not a rite project: "
            f"{sorted(p.name for p in (here / '.rite').iterdir())}"
        )
        assert result.exit_code != 0, (
            f"`rite {' '.join(argv)}` reported success outside a project:\n"
            f"{result.output}"
        )
        if old_success:
            assert old_success not in result.output, (
                "still prints the message it printed when it silently "
                "created a phantom project"
            )
        assert "not a rite project" in result.output

    def test_schedule_set_timezone_does_not_traceback(self, tmp_path, monkeypatch):
        """This one did not merely mislead — it died on an unhandled
        `FileNotFoundError` out of `pathlib`, because `_load_config_for_write`
        tolerates a missing config.yaml and `write_config` then wrote into a
        `.rite/` that was never there. A stranger's first wrong turn got a
        stack trace naming site-packages."""
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli, ["schedule", "set-timezone", "UTC"])

        assert "Traceback" not in result.output
        assert not isinstance(result.exception, FileNotFoundError), (
            "still crashes instead of refusing"
        )
        assert "not a rite project" in result.output

    @pytest.mark.parametrize(
        "argv",
        [["claim", "src", "--worker", "alpha"], ["heartbeat", "--worker", "alpha"]],
    )
    def test_inside_a_project_they_still_work(self, tmp_path, argv):
        """The refusal must key on "is there a project", not on "is this
        the directory I was started in" — a claim from a subdirectory of a
        real project is the ordinary case."""
        root = _project(tmp_path)
        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, argv)

        assert result.exit_code == 0, result.output

    def test_machine_wide_readers_are_untouched(self, tmp_path, monkeypatch):
        """`rite budget` reports the whole machine's usage and `rite pool
        status` is a documented zero-token probe; neither is project-scoped
        and neither may be caught by this. An over-broad guard put on the
        shared config loader refused both."""
        monkeypatch.chdir(tmp_path)
        for argv in (["budget"], ["pool", "status"], ["status"]):
            result = CliRunner().invoke(cli, argv)
            assert result.exit_code == 0, (
                f"`rite {' '.join(argv)}` is machine-wide and must keep "
                f"working outside a project:\n{result.output}"
            )


# --- `rite update` on the install path install.sh prefers -------------------


class TestUpdateKnowsAboutUvToolInstalls:
    """`install.sh` checks for `uv` FIRST and only falls back to `pipx`, so
    `uv tool install` is the default way rite is installed — and
    `detect_install_method` had no branch for it. `sys.executable` under a
    uv tool venv carries no `pipx` component and no editable
    `direct_url.json`, so detection fell through to "pip", `rite update`
    asked "Update rite via pip?", and then ran a pip that a uv tool
    environment does not contain:

        update command exited 1: .../bin/python3: No module named pip
    """

    def _env(self, tmp_path: Path, marker: str) -> Path:
        prefix = tmp_path / "env"
        (prefix / "bin").mkdir(parents=True)
        (prefix / marker).write_text("")
        return prefix

    def test_a_uv_tool_environment_is_detected_as_uv(self, tmp_path):
        from rite_ai.update import detect_install_method, update_command_for

        prefix = self._env(tmp_path, "uv-receipt.toml")
        with patch("rite_ai.update.sys.prefix", str(prefix)):
            method = detect_install_method()
            command = update_command_for(method)

        assert method == "uv", f"a uv tool install still reports as {method!r}"
        assert command is not None
        assert command[:3] == ["uv", "tool", "upgrade"], command
        assert "pip" not in command, (
            "still reaches for pip inside an environment uv built"
        )

    def test_a_pipx_environment_is_detected_wherever_pipx_home_points(self, tmp_path):
        """The pipx branch matched `"pipx" in sys.executable.parts`, which
        is only true while `PIPX_HOME` keeps its default. Pointed anywhere
        else — as it is whenever pipx is exercised without clobbering the
        real install — a genuine pipx install reported as "pip" too."""
        from rite_ai.update import detect_install_method

        prefix = self._env(tmp_path, "pipx_metadata.json")
        with patch("rite_ai.update.sys.prefix", str(prefix)):
            assert detect_install_method() == "pipx"

    def test_the_pip_branch_still_exists_for_a_plain_pip_install(self, tmp_path):
        from rite_ai.update import detect_install_method

        prefix = self._env(tmp_path, "unrelated.txt")
        # No marker, no `pipx` in the path, and no editable `direct_url.json`
        # — the last is patched out because the interpreter running this
        # suite IS an editable checkout, which is the `dev` branch.
        with (
            patch("rite_ai.update.sys.prefix", str(prefix)),
            patch("rite_ai.update.sys.executable", str(prefix / "bin" / "python")),
            patch("importlib.metadata.distribution", side_effect=LookupError),
        ):
            assert detect_install_method() == "pip"


class TestConfigMigrationDoesNotDependOnTheDownload:
    """`rite_ai.update`'s own docstring opens "Two independent halves,
    because they have independent failure modes" — and the command made
    the second depend on the first, exiting on a failed self-update before
    the config was even looked at. Every user on a uv install was in
    exactly that state.
    """

    def test_a_failed_self_update_still_migrates_and_still_fails(self, tmp_path):
        from rite_ai.update import UpdateResult

        root = _project(tmp_path)
        with (
            patch("rite_ai.cli.main._find_project_root", return_value=root),
            patch("rite_ai.update.detect_install_method", return_value="pip"),
            patch(
                "rite_ai.update.run_self_update",
                return_value=UpdateResult(False, "update command exited 1: boom"),
            ),
        ):
            result = CliRunner().invoke(cli, ["update", "--yes"])

        assert "boom" in result.output
        assert "config already current" in result.output, (
            "the config half was skipped because the download half failed"
        )
        assert result.exit_code == 1, "a failed update must still fail"


# --- `rite add worker` -------------------------------------------------------


class TestAddWorkerDoesNotClaimClonesItDidNotMake:
    """`_clone_local` ran `git clone` with `capture_output=True` and no
    `check=True`, so a non-zero exit was indistinguishable from success.
    `add_worker` appended the module to `cloned` regardless, and

        rite add worker alpha
        worker 'alpha' created with 1 module(s)
          cloned: backend

    exited 0 with no `workers/alpha/backend` on disk at all. `rite prepare`
    then reported the same module as `repo_error` — the failure was
    discoverable, one command too late.
    """

    def _project_with_unclonable_module(self, tmp_path: Path) -> Path:
        root = _project(tmp_path)
        (root / ".rite" / "modules.yaml").write_text(
            "modules:\n  backend:\n    path: backend/\n    branch: main\n"
        )
        # A real git repository with no commits: `git clone --branch main`
        # against it fails with "Remote branch main not found in upstream
        # origin". This is exactly what `rite add module <name>` with no
        # url leaves behind before anything has been committed to it.
        source = root / "backend"
        source.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        return root

    def test_a_failed_clone_is_not_reported_as_cloned(self, tmp_path):
        from rite_ai.workspace.manage import add_worker

        root = self._project_with_unclonable_module(tmp_path)

        result = add_worker(root, "alpha")

        assert not (root / "workers" / "alpha" / "backend").exists(), (
            "fixture is wrong — the clone was supposed to fail"
        )
        assert "backend" not in result.cloned_modules, (
            "reports a module as cloned that has no checkout on disk"
        )
        assert [name for name, _ in result.failed_modules] == ["backend"]
        assert "main" in result.failed_modules[0][1], (
            "git's own reason was captured and then thrown away"
        )

    def test_the_command_says_so_and_exits_non_zero(self, tmp_path):
        root = self._project_with_unclonable_module(tmp_path)

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            result = CliRunner().invoke(cli, ["add", "worker", "alpha"])

        reported_cloned = [
            line
            for line in result.output.splitlines()
            if line.strip().startswith("cloned:")
        ]
        assert reported_cloned == [], (
            "still prints the line it printed for a clone that never "
            f"happened: {reported_cloned}"
        )
        assert "NOT cloned: backend" in result.output
        assert result.exit_code == 1, (
            "exited 0 after failing to give the worker a module it was assigned"
        )

    def test_the_worker_is_still_registered(self, tmp_path):
        """Refusing is not the same as rolling back: the worker exists, and
        telling someone to re-run `rite add worker` against a directory that
        now exists would be a worse error than the one being fixed."""
        root = self._project_with_unclonable_module(tmp_path)

        with patch("rite_ai.cli.main._find_project_root", return_value=root):
            CliRunner().invoke(cli, ["add", "worker", "alpha"])

        assert (root / "workers" / "alpha" / "worker.yml").is_file()


# --- `rite prepare`: which branch, and how you got onto it ------------------


class TestPrepareSaysHowItGotOntoTheBranch:
    """`_ensure_branch` distinguishes four cases — already there, an
    existing local branch, one that exists on origin, and a brand-new one —
    computed all four and returned `None` for every one of them. So

        rite prepare --worker alpha --branch feature/ABC-12

    printed `✓ backend @ feature/ABC-12 — ready: up to date` whether it had
    resumed the branch carrying your commits or created an empty one off
    `main` because you mistyped the ticket id. "up to date" is true of both
    and distinguishes neither, in the command whose whole job is SPEC
    §2.1's "right repos, right branches".
    """

    def _module_repo(self, tmp_path: Path) -> tuple[Path, Path]:
        """A real source repo with a commit on `main`, and a worker
        directory holding a clone of it."""
        source = tmp_path / "source"
        source.mkdir()
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        (source / "README.md").write_text("x\n")
        subprocess.run([*git, "add", "-A"], cwd=source, check=True)
        subprocess.run([*git, "commit", "-qm", "one"], cwd=source, check=True)
        worker_dir = tmp_path / "workers" / "alpha"
        worker_dir.mkdir(parents=True)
        return source, worker_dir

    def _module(self, tmp_path: Path):
        from rite_ai.config.models import Module

        return Module(name="source", path="source/", branch="main", url="")

    def _prepare(self, worker_dir, module, tmp_path, branch=None):
        from rite_ai.workspace.prepare import prepare_module

        return prepare_module(worker_dir, module, tmp_path, branch)

    def test_a_created_branch_does_not_read_as_up_to_date(self, tmp_path):
        source, worker_dir = self._module_repo(tmp_path)
        module = self._module(tmp_path)
        self._prepare(worker_dir, module, tmp_path)  # first clone

        created = self._prepare(worker_dir, module, tmp_path, branch="feature/ABC-12")

        assert created.ok, created.message
        assert created.message != "up to date", (
            "a branch created from scratch reports the same words as one "
            "that was already there"
        )
        assert "NEW branch" in created.message
        assert "main" in created.message, "does not say what it was based on"

    def test_a_resumed_branch_reads_differently_from_a_created_one(self, tmp_path):
        source, worker_dir = self._module_repo(tmp_path)
        module = self._module(tmp_path)
        self._prepare(worker_dir, module, tmp_path)
        created = self._prepare(worker_dir, module, tmp_path, branch="feature/ABC-12")
        self._prepare(worker_dir, module, tmp_path, branch="main")

        resumed = self._prepare(worker_dir, module, tmp_path, branch="feature/ABC-12")

        assert resumed.ok, resumed.message
        assert resumed.message != created.message, (
            "resuming an existing branch and creating a new one are "
            "indistinguishable — the whole defect"
        )
        assert "resumed" in resumed.message
        assert "NEW branch" not in resumed.message

    def test_a_branch_that_exists_on_origin_says_so(self, tmp_path):
        source, worker_dir = self._module_repo(tmp_path)
        module = self._module(tmp_path)
        self._prepare(worker_dir, module, tmp_path)
        subprocess.run(["git", "branch", "team/ABC-77", "main"], cwd=source, check=True)

        tracked = self._prepare(worker_dir, module, tmp_path, branch="team/ABC-77")

        assert tracked.ok, tracked.message
        assert "origin" in tracked.message, (
            "checking out the team's branch reads as creating a new one"
        )
        assert "NEW branch" not in tracked.message

    def test_the_default_branch_still_reads_plainly(self, tmp_path):
        """Nothing happened to the branch, so nothing should be said about
        it — the common case must not gain noise."""
        source, worker_dir = self._module_repo(tmp_path)
        module = self._module(tmp_path)
        self._prepare(worker_dir, module, tmp_path)

        again = self._prepare(worker_dir, module, tmp_path)

        assert again.message == "up to date", again.message


# --- the knowledge base ------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever the test class put in `ROUTES`: (content-type, body)."""

    ROUTES: dict[str, tuple[str, bytes]] = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own spelling
        route = self.ROUTES.get(self.path)
        if route is None:
            self.send_error(404)
            return
        content_type, body = route
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def kb_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _kb_project(tmp_path: Path) -> Path:
    (tmp_path / ".rite" / "kb" / ".cache").mkdir(parents=True)
    # A `.rite/` alone is not a project — `_find_project_root` keys on
    # brief.yaml/modules.yaml so a module that is itself a rite repo cannot
    # capture its own workers. These tests chdir here and expect rite to
    # resolve THIS directory.
    (tmp_path / ".rite" / "brief.yaml").write_text(
        "project:\n  name: t\n  role: owner\n"
    )
    return tmp_path


class TestKbRefusesWhatItCannotRead:
    """Nothing looked at `Content-Type`. `rite kb add <pdf-url>` — the most
    ordinary thing anyone wants in a knowledge base — reported `cached:`,
    exited 0, and wrote the PDF's container syntax into the KB as though it
    were the document:

        # http://.../doc.pdf
        %PDF-1.4 1 0 obj >endobj 2 0 obj >endobj trailer > %%EOF

    A PNG produced 633 bytes of U+FFFD. Both sat in `INDEX.md` looking
    exactly like the good snapshot beside them, for a session to read as
    reference material.
    """

    def test_a_pdf_is_refused_rather_than_tag_stripped(self, tmp_path, kb_server):
        from rite_ai.kb import add_link, refresh

        _Handler.ROUTES = {
            "/doc.pdf": (
                "application/pdf",
                b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF\n",
            )
        }
        root = _kb_project(tmp_path)
        add_link(root, f"{kb_server}/doc.pdf")

        report = refresh(root)

        assert len(report.failures) == 1, report.messages
        assert "Content-Type: application/pdf" in report.failures[0]
        cached = (root / ".rite" / "kb" / ".cache").glob("*.md")
        body = "\n".join(p.read_text() for p in cached)
        assert "%PDF" not in body, (
            "still writes the document's container bytes into the KB as "
            "though they were its text"
        )
        assert "fetch failed" in body

    def test_an_image_is_refused_rather_than_stored_as_replacement_chars(
        self, tmp_path, kb_server
    ):
        from rite_ai.kb import add_link, refresh

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 512
        _Handler.ROUTES = {"/pic.png": ("image/png", png)}
        root = _kb_project(tmp_path)
        add_link(root, f"{kb_server}/pic.png")

        report = refresh(root)

        assert len(report.failures) == 1, report.messages
        body = "\n".join(
            p.read_text() for p in (root / ".rite" / "kb" / ".cache").glob("*.md")
        )
        assert "�" not in body, "still stores undecodable bytes as a snapshot"

    def test_html_and_plain_text_still_go_through(self, tmp_path, kb_server):
        """The refusal must not cost the ordinary cases. A server that
        sends no Content-Type at all is also allowed — plenty do, and
        refusing them would lose real pages to fix a problem they do not
        have."""
        from rite_ai.kb import add_link, refresh
        from rite_ai.kb.manage import _unsupported_type

        _Handler.ROUTES = {
            "/a.html": ("text/html; charset=utf-8", b"<p>Readable</p>"),
            "/b.txt": ("text/plain", b"Also readable"),
        }
        root = _kb_project(tmp_path)
        add_link(root, f"{kb_server}/a.html")
        add_link(root, f"{kb_server}/b.txt")

        report = refresh(root)

        assert report.failures == [], report.failures
        assert _unsupported_type("") is None, "a missing Content-Type must pass"


class TestKbRefreshFailureIsVisibleToAScript:
    """Both the `cached:` lines and the `error:` lines went to stdout and
    the command exited 0, so a refresh that failed on half the KB was — to
    a cron entry, a script, or a session routine — a clean run."""

    def test_a_failed_fetch_exits_non_zero_and_writes_to_stderr(
        self, tmp_path, monkeypatch, kb_server
    ):
        from rite_ai.kb import add_link

        _Handler.ROUTES = {"/ok.html": ("text/html", b"<p>fine</p>")}
        root = _kb_project(tmp_path)
        add_link(root, f"{kb_server}/ok.html")
        add_link(root, f"{kb_server}/gone.html")  # 404s
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["kb", "refresh"])

        assert result.exit_code == 1, (
            f"a refresh that failed on an entry still reports success:\n{result.stdout}"
        )
        assert "HTTP 404" in result.stderr, (
            "the failure is still on stdout, where nothing distinguishes it "
            "from the successes"
        )
        assert "cached:" in result.stdout

    def test_a_clean_refresh_still_exits_zero(self, tmp_path, monkeypatch, kb_server):
        from rite_ai.kb import add_link

        _Handler.ROUTES = {"/ok.html": ("text/html", b"<p>fine</p>")}
        root = _kb_project(tmp_path)
        add_link(root, f"{kb_server}/ok.html")
        monkeypatch.chdir(root)

        result = CliRunner().invoke(cli, ["kb", "refresh"])

        assert result.exit_code == 0, result.output


class TestKbBoundsWhatItDownloads:
    """`httpx.get` pulled the whole response into memory before anything
    looked at it, and a `link` entry keeps only the first 8000 characters
    of extracted text. Measured against a 120 MB page: peak RSS 1.93 GB,
    9.5 seconds, for an 8 KB snapshot. A KB entry is whatever URL someone
    pasted; unbounded is not a size."""

    def test_a_page_larger_than_the_cap_is_not_read_whole(self, tmp_path, kb_server):
        from rite_ai.kb.manage import MAX_FETCH_BYTES, _fetch_url

        oversize = b"<p>" + b"x" * (MAX_FETCH_BYTES * 2) + b"</p>"
        _Handler.ROUTES = {"/huge.html": ("text/html", oversize)}

        fetched = _fetch_url(f"{kb_server}/huge.html")

        assert fetched.error is None
        assert len(fetched.text) <= MAX_FETCH_BYTES, (
            f"read {len(fetched.text)} characters of a "
            f"{len(oversize)}-byte page with a {MAX_FETCH_BYTES}-byte cap"
        )
        assert fetched.capped is True

    def test_the_snapshot_says_it_is_only_the_start(self, tmp_path, kb_server):
        from rite_ai.kb import add_link, refresh
        from rite_ai.kb.manage import MAX_FETCH_BYTES

        _Handler.ROUTES = {
            "/huge.html": ("text/html", b"<p>" + b"word " * MAX_FETCH_BYTES + b"</p>")
        }
        root = _kb_project(tmp_path)
        add_link(root, f"{kb_server}/huge.html")

        refresh(root)

        body = "\n".join(
            p.read_text() for p in (root / ".rite" / "kb" / ".cache").glob("*.md")
        )
        assert "stopped at" in body, (
            "a partial download is presented as the whole document"
        )

    def test_a_page_under_the_cap_is_not_marked_capped(self, tmp_path, kb_server):
        from rite_ai.kb.manage import _fetch_url

        _Handler.ROUTES = {"/small.html": ("text/html", b"<p>little</p>")}

        fetched = _fetch_url(f"{kb_server}/small.html")

        assert fetched.capped is False
        assert "little" in fetched.text


class TestKbIndexDropsItsEmptyPlaceholder:
    """`rite init` seeds `kb/INDEX.md` with a `_(none yet)_` row and nothing
    ever removed it, so the table read

        | _(none yet)_ |  |  |  |
        | https://example.com | link | ... |

    — a file people and sessions READ, saying it is empty directly above
    its contents."""

    def test_the_placeholder_goes_when_the_first_entry_arrives(self, tmp_path):
        from rite_ai.kb import add_link, list_entries
        from rite_ai.kb.manage import _PLACEHOLDER_ROW

        root = _kb_project(tmp_path)
        index = root / ".rite" / "kb" / "INDEX.md"
        index.write_text(
            "# Knowledge Base Index\n\n"
            "| Entry | Type | Source | Notes |\n"
            "|-------|------|--------|-------|\n"
            f"| {_PLACEHOLDER_ROW} | | | |\n"
        )

        add_link(root, "https://example.com")

        assert _PLACEHOLDER_ROW not in index.read_text(), (
            "index still claims to be empty while listing an entry"
        )
        assert len(list_entries(root)) == 1
        assert "| Entry | Type | Source | Notes |" in index.read_text()


# --- install.sh --------------------------------------------------------------


class TestInstallerChecksTheVersionExists:
    """Every documented way of installing rite pins `v0.1.0`, and that tag
    does not exist in the repository. Measured against the real remote, the
    uv branch handed the spec straight to uv and a new user's first contact
    with this project was

        error: Git operation failed
          Caused by: failed to fetch branch or tag `v0.1.0`
          ...
        fatal: couldn't find remote ref refs/tags/v0.1.0

    — accurate, and silent on what to do about it. Worse, the installer was
    reached at all: it is the package manager that fails, several steps in,
    rather than the script saying the version is not there.

    The repository's own tag is a release action and not something a test
    can fix. What it CAN hold is that the script finds out first and says
    so, and that it does not invoke a package manager it already knows will
    fail.
    """

    def _fixture(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """(installer, a PATH dir whose `uv` records being called, marker)."""
        origin = tmp_path / "origin"
        origin.mkdir()
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
        (origin / "f.txt").write_text("x\n")
        subprocess.run([*git, "add", "-A"], cwd=origin, check=True)
        subprocess.run([*git, "commit", "-qm", "one"], cwd=origin, check=True)

        installer = tmp_path / "install.sh"
        script = (REPO_ROOT / "install.sh").read_text()
        assert 'REPO="https://github.com/robbartoszewski/rite.git"' in script
        installer.write_text(
            script.replace(
                'REPO="https://github.com/robbartoszewski/rite.git"',
                f'REPO="{origin}"',
            )
        )

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        marker = tmp_path / "uv-was-run"
        stub = bin_dir / "uv"
        stub.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\n')
        stub.chmod(0o755)
        return installer, bin_dir, marker

    def _run(self, installer: Path, bin_dir: Path, version: str | None = None):
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(installer.parent),
        }
        if version is not None:
            env["RITE_VERSION"] = version
        return subprocess.run(
            ["sh", str(installer)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def test_a_version_that_is_not_there_fails_before_the_installer_runs(
        self, tmp_path
    ):
        installer, bin_dir, marker = self._fixture(tmp_path)

        proc = self._run(installer, bin_dir, "v0.1.0")

        assert proc.returncode != 0, proc.stdout
        assert not marker.exists(), (
            "still hands a version it could have checked to the package "
            f"manager: uv was called with {marker.read_text().strip()!r}"
        )
        assert "v0.1.0" in proc.stderr
        assert "not a tag or a branch" in proc.stderr
        assert "RITE_VERSION" in proc.stderr, "does not say how to choose another"
        assert "couldn't find remote ref" not in proc.stderr, (
            "still surfaces git's transport error instead of a sentence"
        )

    def test_a_tag_that_is_there_installs(self, tmp_path):
        installer, bin_dir, marker = self._fixture(tmp_path)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "tag",
                "-a",
                "v9.9.9",
                "-m",
                "r",
            ],
            cwd=tmp_path / "origin",
            check=True,
        )

        proc = self._run(installer, bin_dir, "v9.9.9")

        assert proc.returncode == 0, proc.stderr
        assert marker.exists(), "the check now blocks an install that should work"
        assert "v9.9.9" in marker.read_text()

    def test_a_branch_is_accepted_too(self, tmp_path):
        """`RITE_VERSION` is documented as a version, but `main` is a
        perfectly ordinary thing to point it at while testing, and a check
        that only knew about tags would refuse it."""
        installer, bin_dir, marker = self._fixture(tmp_path)

        proc = self._run(installer, bin_dir, "main")

        assert proc.returncode == 0, proc.stderr
        assert marker.exists()

    def test_a_commit_sha_is_let_through_unchecked(self, tmp_path):
        """`ls-remote` resolves refs and not object ids, so checking a SHA
        the way a tag is checked would refuse the one reference the README
        tells a careful reader to prefer."""
        installer, bin_dir, marker = self._fixture(tmp_path)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path / "origin",
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert len(sha) == 40

        proc = self._run(installer, bin_dir, sha)

        assert proc.returncode == 0, proc.stderr
        assert marker.exists(), "refused a commit SHA it cannot resolve either way"
        assert sha in marker.read_text()


# --- `rite doctor`'s tool-version line ---------------------------------------


class TestToolVersionElisionStaysReadable:
    """`rite doctor` cuts a long version line to 48 characters and marks the
    cut, so a truncated version cannot read as part of the sentence after
    it. That is not enough when the cut lands inside a bracket, because the
    caller appends its own parenthetical. Against the installed yoloAI,
    whose version line is `yoloai version 0.11.0 (commit: 95a6b8ee…)`:

        tool yoloai: yoloai version 0.11.0 (commit: 95a6b8ee…
            (sandbox.enabled is false — not required)      <- one line

    Two opening brackets and one closing one, so the line reads as saying
    the commit is "sandbox.enabled is false" — the exact misreading the
    ellipsis was added to prevent.
    """

    def test_the_symptom_through_the_function_that_produced_it(self, tmp_path):
        """Driven through `_tool_runs` and a stub binary rather than the
        helper, so this fails on an assertion against the code as it was
        rather than on an import of something that did not exist yet."""
        from rite_ai.cli.main import _tool_runs

        version = (
            "yoloai version 0.11.0 "
            "(commit: 95a6b8eeea135ad1b2c3d4e5f6, built 2026-08-30)"
        )
        stub = tmp_path / "yoloai"
        stub.write_text(f"#!/bin/sh\necho '{version}'\n")
        stub.chmod(0o755)

        runs, detail = _tool_runs(str(stub), ["version"])
        line = f"tool yoloai: {detail} (sandbox.enabled is false — not required)"

        assert runs
        assert detail != version, (
            "fixture is wrong — the version line is short enough not to be cut"
        )
        assert line.count("(") == line.count(")"), (
            f"doctor prints an unbalanced line a reader cannot parse: {line}"
        )

    def test_a_cut_inside_a_bracket_closes_it(self):
        from rite_ai.cli.main import _elide

        out = _elide("yoloai version 0.11.0 (commit: 95a6b8eeea135ad1b2c3d4", 47)

        assert out.count("(") == out.count(")"), (
            f"leaves a bracket open for the caller's own parenthetical: {out!r}"
        )
        assert "…" in out, "no longer says it was truncated"

    def test_the_whole_doctor_line_is_balanced(self):
        """The defect is only visible where the two halves meet."""
        from rite_ai.cli.main import _elide

        detail = _elide("yoloai version 0.11.0 (commit: 95a6b8eeea135ad1b2c3", 47)
        line = f"tool yoloai: {detail} (sandbox.enabled is false — not required)"

        assert line.count("(") == line.count(")"), line

    def test_a_cut_outside_any_bracket_gains_nothing(self):
        from rite_ai.cli.main import _elide

        assert _elide("a" * 60, 47) == "a" * 47 + "…"

    def test_nesting_is_closed_innermost_first(self):
        from rite_ai.cli.main import _elide

        assert _elide("x ([y", 5) == "x ([y…])"

    def test_a_bracket_already_closed_is_not_closed_twice(self):
        from rite_ai.cli.main import _elide

        assert _elide("v1 (abc) more", 9) == "v1 (abc) …"


# --- what `rite update` claims happened --------------------------------------


class TestUpdateReportsWhatThePackageManagerSaid:
    """`run_self_update` returned "updated via <command>" on any zero exit,
    and a zero exit is what all three package managers return when there
    was nothing to do. Measured against the real install: `uv tool upgrade
    rite-ai` on a tool already at the latest prints `Nothing to upgrade`
    and exits 0, and `rite update` answered

        updated via uv tool upgrade rite-ai

    Run weekly, that says you upgraded every week.
    """

    def _run(self, stdout: str, stderr: str = "", code: int = 0):
        from rite_ai.update import run_self_update

        completed = subprocess.CompletedProcess(
            args=["uv", "tool", "upgrade", "rite-ai"],
            returncode=code,
            stdout=stdout,
            stderr=stderr,
        )
        with (
            patch("rite_ai.update.shutil.which", return_value="/usr/bin/uv"),
            patch("rite_ai.update.subprocess.run", return_value=completed),
        ):
            return run_self_update("uv")

    def test_nothing_to_upgrade_is_not_reported_as_an_upgrade(self):
        result = self._run("Nothing to upgrade\n")

        assert result.ok
        assert "updated" not in result.message.lower(), (
            f"claims an upgrade the package manager said it did not do: "
            f"{result.message!r}"
        )
        assert "Nothing to upgrade" in result.message

    def test_a_real_upgrade_says_so(self):
        result = self._run(
            "Resolved 1 package\n"
            "Updated rite-ai v0.1.0 -> v0.2.0\n"
            "Installed 2 executables: rite, rite-ai\n"
        )

        assert result.ok
        assert "Installed 2 executables" in result.message
        assert result.message != self._run("Nothing to upgrade\n").message, (
            "an upgrade and a no-op still read the same"
        )

    def test_a_silent_command_does_not_invent_an_outcome(self):
        result = self._run("")

        assert result.ok
        assert "no output" in result.message

    def test_a_failure_is_still_a_failure(self):
        result = self._run("", "error: could not reach the index\n", 1)

        assert not result.ok
        assert "could not reach the index" in result.message
