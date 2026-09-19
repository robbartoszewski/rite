import json
import os
import subprocess
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
    sandbox_git_environment,
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
        env_values = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
        assert "GITHUB_TOKEN=ghp_supersecrettoken" in env_values
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


class TestStartWorkerBoundary:
    """A sandboxed Worker must reach its project's ledger and its own
    checkout, and nothing belonging to another Worker."""

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_worker_dir_is_yoloais_copy_and_the_ledger_is_writable(
        self, mock_which, tmp_path: Path
    ):
        workdir = tmp_path / "workers" / "alpha"
        workdir.mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()
        ) as mock_run:
            result = start_worker(tmp_path, "alpha", SandboxConfig())
        assert result.ok, result.message
        args = mock_run.call_args[0][0]
        # A full copy: prepare runs outside before start, and work leaves by
        # push. `:copy-all`, because yoloAI's default `:copy` omits gitignored
        # files and nested repos — the whole checkout, in a rite project.
        assert args[-1] == f"{workdir}:copy-all"
        dirs = [args[i + 1] for i, a in enumerate(args) if a == "-d"]
        assert dirs == [f"{tmp_path / '.rite'}:rw"]

    def _start_with_origins(self, tmp_path, monkeypatch, origins):
        """Start a Worker whose clones fetch from `origins` ({name: url}),
        with yoloAI mocked and real git answering `remote get-url`."""
        import rite_ai.sandbox as sb

        monkeypatch.setattr(sb, "_yoloai_binary", lambda: "/usr/local/bin/yoloai")
        workdir = tmp_path / "workers" / "alpha"
        workdir.mkdir(parents=True, exist_ok=True)
        (tmp_path / ".rite").mkdir(exist_ok=True)
        for name, url in origins.items():
            clone = workdir / name
            subprocess.run(["git", "init", "-q", str(clone)], check=True)
            subprocess.run(
                ["git", "-C", str(clone), "remote", "add", "origin", url], check=True
            )
        real_run = subprocess.run

        def run(args, *a, **kw):
            if args[:2] == ["git", "-C"]:
                return real_run(args, *a, **kw)
            return _yoloai_calls()(args, *a, **kw)

        monkeypatch.chdir("/")  # a relative origin must not resolve from here
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run) as mock_run:
            result = start_worker(tmp_path, "alpha", SandboxConfig())
        new = next(c[0][0] for c in mock_run.call_args_list if "new" in c[0][0])
        dirs = [new[i + 1] for i, a in enumerate(new) if a == "-d"]
        return result, dirs

    def test_local_origins_are_mounted_read_only_and_url_origins_are_not(
        self, tmp_path: Path, monkeypatch
    ):
        """Git inside fetches from origin; a local origin the sandbox cannot
        read would leave that module unable to fetch."""
        app = tmp_path / "app"
        lib = tmp_path / "lib"
        for repo in (app, lib):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        result, dirs = self._start_with_origins(
            tmp_path,
            monkeypatch,
            {
                "app": "../../../app",  # relative to the clone, as git reads it
                "lib": f"file://{lib}",
                "web": "https://example.com/w.git",
                "ssh": "git@example.com:w.git",
            },
        )
        assert result.ok, result.message
        assert dirs == [
            f"{tmp_path / '.rite'}:rw",
            str(app.resolve()),
            str(lib.resolve()),
        ]

    def test_an_origin_containing_the_worker_is_not_mounted_and_is_named(
        self, tmp_path: Path, monkeypatch
    ):
        """A module registered at `.` fetches from the project root, which
        contains every Worker's directory. Mounting it would expose them all
        (and yoloAI refuses the overlap), so it is left out and said."""
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        result, dirs = self._start_with_origins(
            tmp_path, monkeypatch, {"root": str(tmp_path)}
        )
        assert result.ok, result.message
        assert dirs == [f"{tmp_path / '.rite'}:rw"]
        assert "cannot be mounted" in result.message
        assert "root/" in result.message

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_project_root_is_never_mounted(self, mock_which, tmp_path: Path):
        """Mounting it would expose every other Worker's checkout."""
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()
        ) as mock_run:
            start_worker(tmp_path, "alpha", SandboxConfig())
        args = mock_run.call_args[0][0]
        root = str(tmp_path)
        assert not any(
            a in (root, f"{root}:rw", f"{root}:copy", f"{root}:copy-all") for a in args
        )

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_project_root_is_named_for_the_agent(self, mock_which, tmp_path: Path):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()
        ) as mock_run:
            start_worker(tmp_path, "alpha", SandboxConfig())
        args = mock_run.call_args[0][0]
        envs = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
        assert f"RITE_PROJECT_ROOT={tmp_path}" in envs


class TestStartWorkerPrompt:
    """A Worker's opening instruction reaches the sandbox, because nothing
    in rite can type into one after it starts."""

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_prompt_goes_through_the_prompt_file_never_argv(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        seen: dict = {}
        default = _yoloai_calls()

        def run(args, *a, **kw):
            if "new" in args:
                path = Path(args[args.index("--prompt-file") + 1])
                seen["path"] = path
                seen["content"] = path.read_text()
            return default(args, *a, **kw)

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run) as mock_run:
            result = start_worker(
                tmp_path, "alpha", SandboxConfig(), prompt="Work ticket ABC-12."
            )
        assert result.ok, result.message
        args = mock_run.call_args[0][0]
        assert seen["content"] == "Work ticket ABC-12.\n"
        assert not any("ABC-12" in a for a in args), args
        assert "--" not in args
        # Read by `yoloai new`, then gone: nothing about the prompt is left
        # behind on the host.
        assert not seen["path"].exists()
        assert not seen["path"].parent.exists()

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_prompt_file_removed_when_yoloai_fails(self, mock_which, tmp_path: Path):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        seen: dict = {}
        failing = _yoloai_calls(new_returncode=1, new_stderr="boom")

        def run(args, *a, **kw):
            if "new" in args:
                seen["path"] = Path(args[args.index("--prompt-file") + 1])
            return failing(args, *a, **kw)

        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = start_worker(tmp_path, "alpha", SandboxConfig(), prompt="x")
        assert not result.ok
        assert not seen["path"].exists()

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_empty_prompt_refused_before_shelling_out(self, mock_which, tmp_path: Path):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run") as mock_run:
            result = start_worker(tmp_path, "alpha", SandboxConfig(), prompt="  \n")
        assert not result.ok
        assert "empty prompt" in result.message
        mock_run.assert_not_called()

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_start_names_the_sandbox_and_how_to_attach(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()):
            result = start_worker(tmp_path, "alpha", SandboxConfig(), prompt="go")
        name = sandbox_name("alpha", tmp_path)
        assert f"sandbox '{name}' started" in result.message
        assert f"yoloai attach {name}" in result.message
        assert "idle" not in result.message

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_start_without_a_prompt_says_the_session_is_idle(
        self, mock_which, tmp_path: Path
    ):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()
        ) as mock_run:
            result = start_worker(tmp_path, "alpha", SandboxConfig())
        assert result.ok
        assert "--prompt-file" not in mock_run.call_args[0][0]
        assert "idle" in result.message
        assert "--ticket" in result.message


@patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
def test_an_existing_sandbox_names_rites_way_forward_not_yoloais(
    mock_which, tmp_path: Path
):
    """yoloAI says "use --replace", a flag `rite sandbox start` does not
    have. Measured against yoloai 0.11.0 by starting the same Worker twice."""
    (tmp_path / "workers" / "alpha").mkdir(parents=True)
    stderr = (
        'yoloai: sandbox "rite-x-alpha" already exists (use --replace to '
        "recreate): sandbox already exists"
    )
    with patch(
        "rite_ai.sandbox.subprocess.run",
        side_effect=_yoloai_calls(new_returncode=1, new_stderr=stderr),
    ):
        result = start_worker(tmp_path, "alpha", SandboxConfig(), prompt="go")
    assert not result.ok
    assert "rite sandbox destroy <worker>" in result.message


class TestSandboxGitEnvironment:
    """Inside a Seatbelt sandbox the keychain credential helper and a signing
    key under `~/.ssh` are both unreadable: measured, an HTTPS push stopped
    at "could not read Username" and every `git commit` failed."""

    @staticmethod
    def _git_env(home: Path, extra: dict | None = None) -> dict:
        # Nothing inherited that could decide the answer: the suite's own
        # fixtures set GIT_CONFIG_* (signing off among them), which would
        # make "signing required" pass without the sandbox settings.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            HOME=str(home),
            GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
            GIT_CONFIG_NOSYSTEM="1",
        )
        env.update(extra or {})
        return env

    def test_only_gh_answers_for_github_and_nothing_answers_elsewhere(self, tmp_path):
        """The property, not the key: with a generic helper and a
        github-scoped one already configured (the osxkeychain shape), the
        helper git actually RUNS for github.com is the sandbox's gh, and
        no helper runs for any other host. Deleting the reset makes this
        fail — the inherited helpers run first."""
        calls = tmp_path / "calls"
        bin_dir = tmp_path / "bin dir"  # a space: git runs `!` helpers via sh
        bin_dir.mkdir()
        for name in ("keychain", "scoped", "gh"):
            helper = bin_dir / name
            helper.write_text(
                f"#!/bin/sh\necho {name} >> '{calls}'\n"
                "echo username=u\necho password=p\n"
            )
            helper.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text(
            f'[credential]\n\thelper = "!\\"{bin_dir}/keychain\\""\n'
            f'[credential "https://github.com"]\n'
            f'\thelper = "!\\"{bin_dir}/scoped\\""\n'
        )
        env = self._git_env(home, sandbox_git_environment(str(bin_dir / "gh")))
        env["GIT_TERMINAL_PROMPT"] = "0"

        def fill(host):
            calls.write_text("")
            subprocess.run(
                ["git", "credential", "fill"],
                input=f"protocol=https\nhost={host}\n\n",
                capture_output=True,
                text=True,
                env=env,
            )
            return calls.read_text().split()

        assert fill("github.com") == ["gh"]
        assert fill("gitlab.com") == []

    def test_the_repositorys_own_hooks_run_and_global_ones_do_not(self, tmp_path):
        """A global hooks directory is unreadable inside the sandbox and failed
        every push; the clone's own hooks are what run instead."""
        ran = tmp_path / "ran"
        global_hooks = tmp_path / "global-hooks"
        global_hooks.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text(f"[core]\n\thooksPath = {global_hooks}\n")
        repo = tmp_path / "repo"
        env = self._git_env(home, sandbox_git_environment("/opt/homebrew/bin/gh"))
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
        for hooks, who in ((global_hooks, "global"), (repo / ".git" / "hooks", "repo")):
            hook = hooks / "pre-commit"
            hook.write_text(f"#!/bin/sh\necho {who} >> '{ran}'\n")
            hook.chmod(0o755)
        subprocess.run(
            ["git", "-C", str(repo), "hook", "run", "pre-commit"],
            check=True,
            capture_output=True,
            env=env,
        )
        assert ran.read_text().split() == ["repo"]

    def test_a_commit_succeeds_when_signing_is_required_by_config(self, tmp_path):
        """Signing on, with a key that does not exist: the commit fails
        without the sandbox settings and succeeds with them."""
        repo = tmp_path / "r"
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text(
            "[user]\n\tname = w\n\temail = w@invalid\n"
            "\tsigningkey = /nonexistent/key.pub\n"
            "[gpg]\n\tformat = ssh\n[commit]\n\tgpgsign = true\n"
        )
        base = self._git_env(home)
        subprocess.run(["git", "init", "-q", str(repo)], env=base, check=True)
        commit = ["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", "x"]
        unsandboxed_rc = subprocess.run(
            commit, env=base, capture_output=True
        ).returncode
        sandboxed_rc = subprocess.run(
            commit,
            env=self._git_env(home, sandbox_git_environment(None)),
            capture_output=True,
        ).returncode
        assert unsandboxed_rc != 0, "signing was not actually required"
        assert sandboxed_rc == 0

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_start_passes_the_git_settings_to_the_sandbox(self, mock_which, tmp_path):
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()
        ) as mock_run:
            start_worker(tmp_path, "alpha", SandboxConfig(), prompt="go")
        args = mock_run.call_args[0][0]
        envs = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
        assert "GIT_CONFIG_KEY_0=credential.helper" in envs
        assert any(
            e.startswith("GIT_CONFIG_VALUE_") and e.endswith("=false") for e in envs
        )


class TestClaudeLoginReachesTheSandbox:
    """A sandboxed session cannot use the keychain Claude login; without a
    token it starts and does nothing. yoloAI reads the token as the claude
    agent's credential from the environment `yoloai new` runs in."""

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_the_token_goes_to_yoloais_environment_not_to_env_flags(
        self, mock_which, tmp_path: Path, monkeypatch
    ):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch(
            "rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()
        ) as mock_run:
            result = start_worker(
                tmp_path,
                "alpha",
                SandboxConfig(),
                prompt="go",
                env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test", "JIRA_EMAIL": "a@b"},
            )
        assert result.ok, result.message
        new = next(c for c in mock_run.call_args_list if "new" in c[0][0])
        args, kwargs = new[0][0], new[1]
        assert not any("sk-ant-oat-test" in a for a in args)
        assert "JIRA_EMAIL=a@b" in args
        assert kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test"
        assert "no Claude login" not in result.message

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_start_says_so_when_there_is_no_login_at_all(
        self, mock_which, tmp_path: Path, monkeypatch
    ):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        (tmp_path / "workers" / "alpha").mkdir(parents=True)
        with patch("rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()):
            result = start_worker(tmp_path, "alpha", SandboxConfig(), prompt="go")
        assert result.ok
        assert "no Claude login" in result.message
        assert "rite credential set claude" in result.message


@patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
def test_host_git_settings_do_not_reach_yoloai(mock_which, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'commit.gpgsign=true'")
    (tmp_path / "workers" / "alpha").mkdir(parents=True)
    with patch("rite_ai.sandbox.subprocess.run", side_effect=_yoloai_calls()) as run:
        start_worker(tmp_path, "alpha", SandboxConfig(), prompt="go")
    new = next(c for c in run.call_args_list if "new" in c[0][0])
    assert not any(k.startswith("GIT_CONFIG") for k in new[1]["env"])


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


class TestWorkOnlyInTheSandboxCopy:
    """The copy is deleted with the sandbox, so commits on no remote there
    are the most expensive silent loss in the model. Real git repositories,
    laid out where yoloAI 0.11.0 was measured to put a copy."""

    NAME = "rite-proj-abc123-alpha"

    def _project(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        root = tmp_path / "proj"
        (root / "workers" / "alpha").mkdir(parents=True)
        monkeypatch.setattr(
            "rite_ai.sandbox.existing_sandbox_name", lambda worker, root=None: self.NAME
        )
        return root

    def _git(self, cwd, *args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=cwd,
            check=True,
            capture_output=True,
        )

    def _copy(self, tmp_path, root, commit=True):
        work = (
            tmp_path
            / "home"
            / ".yoloai"
            / "library"
            / "sandboxes"
            / self.NAME
            / "rw"
            / "work"
        )
        module = work / str(root / "workers" / "alpha").replace("/", "^s") / "app"
        module.mkdir(parents=True)
        self._git(module, "init", "-q", "-b", "ABC-12")
        if commit:
            self._git(module, "commit", "-q", "--allow-empty", "-m", "work")
        return module

    def _run(self):
        real_run = subprocess.run
        calls = []

        def run(args, *a, **kw):
            if args and args[0] == "git":
                return real_run(args, *a, **kw)
            calls.append(list(args))
            return MagicMock(returncode=0, stdout="", stderr="")

        return run, calls

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_destroy_refuses_and_names_branch_and_count(
        self, _which, tmp_path, monkeypatch
    ):
        root = self._project(tmp_path, monkeypatch)
        self._copy(tmp_path, root)
        run, calls = self._run()
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = destroy_worker("alpha", root)
        assert not result.ok
        assert "app @ ABC-12: 1 commit(s) on no remote" in result.message
        assert "--force" in result.message
        assert not any("destroy" in c for c in calls)

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_force_destroys_anyway(self, _which, tmp_path, monkeypatch):
        root = self._project(tmp_path, monkeypatch)
        self._copy(tmp_path, root)
        run, calls = self._run()
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = destroy_worker("alpha", root, force=True)
        assert result.ok
        assert any("destroy" in c for c in calls)

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_pushed_work_does_not_block_destroy(self, _which, tmp_path, monkeypatch):
        root = self._project(tmp_path, monkeypatch)
        module = self._copy(tmp_path, root)
        remote = tmp_path / "remote.git"
        self._git(tmp_path, "init", "-q", "--bare", str(remote))
        self._git(module, "remote", "add", "origin", str(remote))
        self._git(module, "push", "-q", "origin", "ABC-12")
        run, calls = self._run()
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = destroy_worker("alpha", root)
        assert result.ok, result.message
        assert any("destroy" in c for c in calls)

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_a_copy_that_cannot_be_found_is_not_taken_as_safe(
        self, _which, tmp_path, monkeypatch
    ):
        root = self._project(tmp_path, monkeypatch)
        work = (
            tmp_path
            / "home"
            / ".yoloai"
            / "library"
            / "sandboxes"
            / self.NAME
            / "rw"
            / "work"
        )
        (work / "one").mkdir(parents=True)
        (work / "two").mkdir()
        run, calls = self._run()
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = destroy_worker("alpha", root)
        assert not result.ok
        assert "could not find the sandbox's copy" in result.message

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_no_sandbox_on_disk_leaves_the_answer_to_yoloai(
        self, _which, tmp_path, monkeypatch
    ):
        root = self._project(tmp_path, monkeypatch)
        run, calls = self._run()
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = destroy_worker("alpha", root)
        assert result.ok
        assert any("destroy" in c for c in calls)

    @patch("rite_ai.sandbox.shutil.which", return_value="/usr/local/bin/yoloai")
    def test_stop_warns_and_still_stops(self, _which, tmp_path, monkeypatch):
        root = self._project(tmp_path, monkeypatch)
        self._copy(tmp_path, root)
        run, calls = self._run()
        with patch("rite_ai.sandbox.subprocess.run", side_effect=run):
            result = stop_worker("alpha", root)
        assert result.ok
        assert "warning:" in result.message
        assert "app @ ABC-12: 1 commit(s) on no remote" in result.message
        assert any("stop" in c for c in calls)


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

    @staticmethod
    def _shared_token_living_in(monkeypatch, tier: str):
        """A `github_token` that exists, in a named tier.

        Both halves have to be said. `get_scoped` supplies the VALUE and
        walks project-then-machine, so on its own it cannot say which one
        answered — which is exactly the conflation these tests exist for
        now. `resolve` supplies the tier.
        """
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(
            store,
            "get_scoped",
            lambda n, c=None: "TOKEN" if n == "github_token" else None,
        )
        monkeypatch.setattr(
            store,
            "resolve",
            lambda k, c=None: store.Resolved(k, tier, "acct", "proj", k),
        )
        return sb

    def test_this_projects_own_token_is_not_called_machine_global(self, monkeypatch):
        """THE DEFECT. `get_scoped` tries this project's namespaced account
        FIRST, so a token belonging to the project came back labelled
        `global` — the label measured which NAME matched second, not
        whether the token is bounded. The caller warns on `global`, so the
        loud every-start warning fired for the configuration §5.3.4 calls
        correct."""
        from rite_ai.credentials.store import PROJECT

        sb = self._shared_token_living_in(monkeypatch, PROJECT)

        assert sb.resolve_worker_token("w1") == ("TOKEN", "project")

    def test_a_machine_wide_token_is_still_called_global(self, monkeypatch):
        """The half that must survive: a token belonging to the whole
        machine reaches past this project's repos, and saying so is the
        point of the tier."""
        from rite_ai.credentials.store import GLOBAL

        sb = self._shared_token_living_in(monkeypatch, GLOBAL)

        assert sb.resolve_worker_token("w1") == ("TOKEN", "global")

    def test_no_token_reports_none_tier(self, monkeypatch):
        import rite_ai.credentials.store as store
        from rite_ai import sandbox as sb

        monkeypatch.setattr(store, "get_scoped", lambda n, c=None: None)
        assert sb.resolve_worker_token("w1") == (None, "none")

    def test_the_tier_is_returned_not_inferred(self, monkeypatch):
        """The caller must not have to re-derive which token it got — the
        warning text depends on it, and now so does whether there is one."""
        from rite_ai.credentials.store import GLOBAL, PROJECT

        sb = self._shared_token_living_in(monkeypatch, GLOBAL)
        assert sb.resolve_worker_token("anything")[1] == "global"

        sb = self._shared_token_living_in(monkeypatch, PROJECT)
        assert sb.resolve_worker_token("anything")[1] == "project"


def test_gh_is_given_a_config_dir_the_sandbox_may_read():
    """gh's default config under ~/.config is denied inside Seatbelt, and gh
    then exits before git gets a credential."""
    env = sandbox_git_environment("/opt/homebrew/bin/gh")
    assert not env["GH_CONFIG_DIR"].startswith(str(Path.home() / ".config"))
    assert "GH_CONFIG_DIR" not in sandbox_git_environment(None)
