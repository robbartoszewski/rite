import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from rite_ai.config.models import Module, SandboxConfig
from rite_ai.sandbox import (
    CountUnavailable,
    check_token_access,
    count_active_sandboxes,
    destroy_worker,
    is_available,
    owner_repo_from_url,
    sandbox_name,
    start_worker,
    stop_worker,
    token_credential_name,
    worker_sandbox_status,
)


def test_sandbox_name_is_prefixed_per_worker():
    # Without a project this is the legacy, ambiguous form — kept only so
    # a caller with no project in hand still gets a usable name. The
    # project-scoped form is what everything actually uses; see
    # `test_names_carry_the_project.py` (SPEC §8.10).
    assert sandbox_name("alpha") == "rite-alpha"


def test_token_credential_name_matches_naming_convention():
    assert token_credential_name("alpha") == "sandbox_token_alpha"


def _yoloai_calls(
    ls_json: str = '{"sandboxes": []}', new_returncode: int = 0, new_stderr: str = ""
):
    """A `subprocess.run` stand-in that answers `ls` and `new` separately.

    Answering every call with one blank MagicMock used to work only
    because a `yoloai ls` that returned nothing was read as "zero
    sandboxes running" — the fail-open these tests now guard against. A
    start-path test has to supply a real `ls` answer, the way the real
    binary would.
    """

    def run(args, *a, **kw):
        if "ls" in args:
            return MagicMock(returncode=0, stdout=ls_json, stderr="")
        return MagicMock(returncode=new_returncode, stdout="", stderr=new_stderr)

    return run


class TestOwnerRepoFromUrl:
    def test_https_url(self):
        assert owner_repo_from_url("https://github.com/acme/widgets.git") == (
            "acme",
            "widgets",
        )

    def test_https_url_without_dotgit(self):
        assert owner_repo_from_url("https://github.com/acme/widgets") == (
            "acme",
            "widgets",
        )

    def test_ssh_url(self):
        assert owner_repo_from_url("git@github.com:acme/widgets.git") == (
            "acme",
            "widgets",
        )

    def test_non_github_url_returns_none(self):
        assert owner_repo_from_url("https://gitlab.com/acme/widgets.git") is None


class TestIsAvailable:
    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_false_when_binary_missing(self, mock_which):
        assert is_available() is False

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_true_when_binary_present(self, mock_which):
        assert is_available() is True


class TestStartWorker:
    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_missing_binary_refuses_cleanly(self, mock_which, tmp_path: Path):
        result = start_worker(tmp_path, "alpha", SandboxConfig())
        assert not result.ok
        assert "yoloai not found" in result.message

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_missing_workspace_refuses_before_shelling_out(
        self, mock_which, tmp_path: Path
    ):
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            result = start_worker(tmp_path, "alpha", SandboxConfig())
        assert not result.ok
        assert "no such worker workspace" in result.message
        mock_run.assert_not_called()

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_backend_passed_through_verbatim_never_the_wrong_literal(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            mock_run.side_effect = _yoloai_calls()
            result = start_worker(tmp_path, "alpha", SandboxConfig(backend="seatbelt"))
        assert result.ok, result.message
        args = mock_run.call_args[0][0]
        assert "--backend" in args
        assert args[args.index("--backend") + 1] == "seatbelt"
        assert "sandbox-exec" not in args

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_token_delivered_via_env_flag_never_argv_or_file(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            mock_run.side_effect = _yoloai_calls()
            start_worker(
                tmp_path, "alpha", SandboxConfig(), token="ghp_supersecrettoken"
            )
        args = mock_run.call_args[0][0]
        assert "--env" in args
        env_value = args[args.index("--env") + 1]
        assert env_value == "GITHUB_TOKEN=ghp_supersecrettoken"
        # The token must appear only as the --env value, never as a bare
        # standalone argument (which `ps` would show).
        assert args.count("ghp_supersecrettoken") == 0

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_yoloai_failure_surfaces_stderr(self, mock_which, tmp_path: Path):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            mock_run.side_effect = _yoloai_calls(
                new_returncode=1, new_stderr="backend not available"
            )
            result = start_worker(tmp_path, "alpha", SandboxConfig())
        assert not result.ok
        assert "backend not available" in result.message


class TestCountActiveSandboxes:
    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_missing_binary_reports_unavailable_not_zero(self, mock_which):
        """Zero is a number the cap check would act on. "I could not ask"
        is not, and must not be spelled as one."""
        result = count_active_sandboxes()
        assert isinstance(result, CountUnavailable)
        assert "yoloai" in result.reason

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_counts_only_rite_prefixed_sandboxes(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "sandboxes": [
                        {"environment": {"name": "rite-alpha"}},
                        {"environment": {"name": "rite-beta"}},
                        {"environment": {"name": "someone-elses-sandbox"}},
                    ]
                }
            ),
        )
        assert count_active_sandboxes() == 2

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_uses_the_active_flag(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"sandboxes": []}')
        count_active_sandboxes()
        assert "--active" in mock_run.call_args[0][0]


class TestStartWorkerCapEnforcement:
    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=5)
    def test_refuses_when_starting_would_exceed_the_cap(
        self, mock_count, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=5)
            )
        assert not result.ok
        assert "exceeding" in result.message
        assert "refused, not clamped" in result.message
        mock_run.assert_not_called()

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.count_active_sandboxes", return_value=2)
    def test_allows_starting_when_under_the_cap(
        self, mock_count, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(max_concurrent_workers=5)
            )
        assert result.ok
        mock_run.assert_called_once()


class TestStopAndDestroy:
    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_stop_worker_targets_the_right_sandbox_name(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = stop_worker("alpha")
        assert result.ok
        assert mock_run.call_args[0][0][-1] == "rite-alpha"

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_destroy_worker_abandons_unapplied_changes(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        destroy_worker("alpha")
        args = mock_run.call_args[0][0]
        assert "rite-alpha" in args
        assert "--abandon-unapplied" in args


class TestWorkerSandboxStatus:
    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_finds_matching_sandbox_by_name(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"sandboxes": [{"environment": {"name": "rite-alpha"}, '
            '"status": "running"}], "unavailable_backends": []}',
        )
        status = worker_sandbox_status("alpha")
        assert status.value == "running"
        assert status.known

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_no_matching_sandbox_reports_not_found(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"sandboxes": [], "unavailable_backends": []}'
        )
        status = worker_sandbox_status("alpha")
        assert status.value == "not found"
        assert status.known  # a real answer: there is no sandbox

    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_missing_binary_reports_itself_rather_than_not_found(self, mock_which):
        status = worker_sandbox_status("alpha")
        assert status.value == "yoloai not found"
        assert not status.known


class TestCheckTokenAccess:
    @patch("rite_ai.sandbox.shutil.which", return_value=None)
    def test_missing_gh_binary_is_reported_as_a_problem(self, mock_which):
        problems = check_token_access(
            "tok",
            [Module(name="m", path=".", url="https://github.com/acme/widgets.git")],
        )
        assert len(problems) == 1
        assert "gh" in problems[0]

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/gh")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_accessible_repo_is_not_a_problem(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        problems = check_token_access(
            "tok",
            [Module(name="m", path=".", url="https://github.com/acme/widgets.git")],
        )
        assert problems == []

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/gh")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_inaccessible_repo_is_reported(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="404 Not Found"
        )
        problems = check_token_access(
            "tok",
            [Module(name="m", path=".", url="https://github.com/acme/widgets.git")],
        )
        assert len(problems) == 1
        assert "acme/widgets" in problems[0]

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/gh")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_token_passed_via_gh_token_env_not_the_command_line(
        self, mock_run, mock_which
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        check_token_access(
            "ghp_supersecrettoken",
            [Module(name="m", path=".", url="https://github.com/acme/widgets.git")],
        )
        args = mock_run.call_args[0][0]
        assert "ghp_supersecrettoken" not in args
        env = mock_run.call_args.kwargs["env"]
        assert env["GH_TOKEN"] == "ghp_supersecrettoken"

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/gh")
    @patch("rite_ai.sandbox.subprocess.run")
    def test_module_with_no_url_is_skipped_not_a_crash(self, mock_run, mock_which):
        problems = check_token_access("tok", [Module(name="m", path=".", url=None)])
        assert problems == []
        mock_run.assert_not_called()


class TestWorkerPane:
    """A sandboxed Worker is invisible to Claude Code's session tooling
    (verified 2026-09-11), so its screen is the only way to see it. Without
    `rite sandbox pane` a Manager reaches into yoloAI's private library
    directory to look at rite's own worker."""

    def test_returns_the_snapshot_on_success(self, monkeypatch):
        import subprocess

        from rite_ai import sandbox as sb

        monkeypatch.setattr(sb, "_yoloai_binary", lambda: "/usr/bin/yoloai")
        monkeypatch.setattr(
            sb.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "PANE TEXT\n", ""),
        )
        result = sb.worker_pane("w1")
        assert result.ok
        assert result.text == "PANE TEXT"

    def test_asks_yoloai_for_this_workers_sandbox(self, monkeypatch):
        import subprocess

        from rite_ai import sandbox as sb

        seen = {}

        def fake_run(args, **kwargs):
            seen["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(sb, "_yoloai_binary", lambda: "/usr/bin/yoloai")
        monkeypatch.setattr(sb.subprocess, "run", fake_run)
        sb.worker_pane("alpha")
        assert seen["args"][1:] == [
            "sandbox",
            "rite-alpha",
            "terminal-snapshot",
        ]

    def test_ansi_flag_is_passed_through(self, monkeypatch):
        import subprocess

        from rite_ai import sandbox as sb

        seen = {}

        def fake_run(args, **kwargs):
            seen["args"] = args
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(sb, "_yoloai_binary", lambda: "/usr/bin/yoloai")
        monkeypatch.setattr(sb.subprocess, "run", fake_run)
        sb.worker_pane("alpha", ansi=True)
        assert seen["args"][-1] == "--ansi"

    def test_a_failure_is_not_reported_as_an_empty_pane(self, monkeypatch):
        """ "Could not check" must not read the same as "nothing there" —
        a broken yoloAI would otherwise look like a quiet worker."""
        import subprocess

        from rite_ai import sandbox as sb

        monkeypatch.setattr(sb, "_yoloai_binary", lambda: "/usr/bin/yoloai")
        monkeypatch.setattr(
            sb.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], 1, "", "sandbox not found"
            ),
        )
        result = sb.worker_pane("w9")
        assert not result.ok
        assert "sandbox not found" in result.text

    def test_missing_yoloai_is_reported(self, monkeypatch):
        from rite_ai import sandbox as sb

        monkeypatch.setattr(sb, "_yoloai_binary", lambda: None)
        result = sb.worker_pane("w1")
        assert not result.ok
        assert "yoloai not found" in result.text


class TestResolveWorkerToken:
    """`github_token` was advertised by `rite doctor` and `rite credential
    set` and read by nothing. Measured: with only it set, a Worker's
    sandbox received no token while doctor called the credential
    configured."""

    def test_per_worker_token_wins(self, monkeypatch):
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(
            store,
            "get_scoped",
            lambda n, c=None: "SCOPED" if n == "sandbox_token_w1" else "GLOBAL",
        )
        assert sb.resolve_worker_token("w1") == ("SCOPED", "worker")

    def test_falls_back_to_the_global_key(self, monkeypatch):
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(
            store,
            "get_scoped",
            lambda n, c=None: "GLOBAL" if n == "github_token" else None,
        )
        assert sb.resolve_worker_token("w1") == ("GLOBAL", "global")

    def test_no_token_reports_none_tier(self, monkeypatch):
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(store, "get_scoped", lambda n, c=None: None)
        assert sb.resolve_worker_token("w1") == (None, "none")

    def test_the_tier_is_returned_not_inferred(self, monkeypatch):
        """The caller must not have to re-derive which token it got — the
        warning text depends on it."""
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(
            store,
            "get_scoped",
            lambda n, c=None: "GLOBAL" if n == "github_token" else None,
        )
        _, tier = sb.resolve_worker_token("anything")
        assert tier == "global"
