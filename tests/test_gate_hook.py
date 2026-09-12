import os
import stat
import subprocess
from pathlib import Path

from rite_ai.gate.hook import (
    HOOK_MARKER,
    PRE_PUSH_HOOK_SCRIPT,
    compute_pre_push_ranges,
    install_pre_push_hook,
    redirected_hooks_dir,
)
from tests.gate_helpers import init_repo


def test_install_refuses_when_not_a_git_repo(tmp_path: Path):
    result = install_pre_push_hook(tmp_path)
    assert not result.ok


def test_install_writes_executable_hook(tmp_path: Path):
    init_repo(tmp_path)
    result = install_pre_push_hook(tmp_path)
    assert result.ok
    hook_path = tmp_path / ".git" / "hooks" / "pre-push"
    assert hook_path.exists()
    assert hook_path.stat().st_mode & 0o111  # executable bit set
    assert HOOK_MARKER in hook_path.read_text()


def test_install_refuses_to_clobber_existing_foreign_hook(tmp_path: Path):
    init_repo(tmp_path)
    hook_path = tmp_path / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\necho someone else's hook\n")

    result = install_pre_push_hook(tmp_path)
    assert not result.ok
    assert "someone else" not in result.message  # doesn't echo content, just refuses
    assert hook_path.read_text() == "#!/bin/sh\necho someone else's hook\n"


def test_install_force_overwrites_foreign_hook(tmp_path: Path):
    init_repo(tmp_path)
    hook_path = tmp_path / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\necho someone else's hook\n")

    result = install_pre_push_hook(tmp_path, force=True)
    assert result.ok
    assert HOOK_MARKER in hook_path.read_text()


def test_install_is_idempotent_over_its_own_hook(tmp_path: Path):
    init_repo(tmp_path)
    first = install_pre_push_hook(tmp_path)
    second = install_pre_push_hook(tmp_path)
    assert first.ok and second.ok


def test_install_upgrades_a_hook_installed_under_the_old_marker(tmp_path: Path):
    """Before the two hook installers were consolidated, `rite init`'s
    scaffold used a different marker ("# rite: publish gate"). A hook
    installed under that marker is still rite's own and must be upgradable
    without --force — refusing it would strand every existing user on the
    unscoped, non-pipx-safe old hook."""
    init_repo(tmp_path)
    hook_path = tmp_path / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(
        "#!/bin/sh\n# rite: publish gate — installed by `rite init`. See SPEC.md §11.\n"
        "exec rite publish check\n"
    )

    result = install_pre_push_hook(tmp_path)
    assert result.ok
    assert HOOK_MARKER in hook_path.read_text()
    assert "rite publish pre-push" in hook_path.read_text()


def test_hook_script_is_pipx_safe():
    """Stage 2 #3: the hook must exec the `rite` console script, never
    `python3 -m rite_ai.gate` — a bare `python3` under a pipx install has no
    access to the isolated venv `rite` lives in."""
    assert "python3" not in PRE_PUSH_HOOK_SCRIPT
    assert "rite publish pre-push" in PRE_PUSH_HOOK_SCRIPT


def test_scaffold_and_module_installer_share_one_script():
    """Stage 2 #3: `rite init`'s scaffold and the standalone
    `python -m rite_ai.gate install-hook` path must write byte-identical hook
    scripts — they used to diverge (different marker, different
    invocation)."""
    from rite_ai.cli.init import scaffold

    assert scaffold.PRE_PUSH_HOOK == PRE_PUSH_HOOK_SCRIPT
    assert scaffold.PRE_PUSH_MARKER == HOOK_MARKER


class TestHookScriptExecution:
    """Both prior versions of this test suite checked the hook script's
    TEXT (the right substrings present/absent) but never actually ran it —
    which is exactly how the `"$@"`-forwarding bug (git invokes pre-push as
    `pre-push <remote-name> <remote-url>`, and `rite publish pre-push` takes
    no arguments) survived two full review rounds. This class executes the
    real script against a stub `rite` on PATH, the way git actually would."""

    def _write_stub_rite(self, bin_dir: Path, argv_log: Path) -> None:
        stub = bin_dir / "rite"
        stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > "{argv_log}"\n'
            "cat > /dev/null\n"  # drain stdin like the real command does
            "exit 0\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    def test_installed_hook_forwards_no_argv_to_rite(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        argv_log = tmp_path / "argv.log"
        self._write_stub_rite(bin_dir, argv_log)

        hook_path = tmp_path / "pre-push"
        hook_path.write_text(PRE_PUSH_HOOK_SCRIPT)
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
        result = subprocess.run(
            ["sh", str(hook_path), "origin", "git@github.com:x/y.git"],
            input="refs/heads/main aaaa refs/heads/main bbbb\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        # The stub is invoked as `rite publish pre-push` — its own argv is
        # legitimately "publish pre-push". What must NOT appear is git's
        # two positional args (the remote name and URL the hook itself was
        # called with) — those belong on stdin's protocol, never forwarded
        # to a command that takes no arguments at all.
        received_argv = argv_log.read_text().splitlines()
        assert received_argv == ["publish", "pre-push"]
        assert "origin" not in received_argv
        assert not any("git@github.com" in a for a in received_argv)


class TestComputePrePushRanges:
    def test_skips_branch_deletion(self):
        remote_sha = "a" * 40
        line = f"refs/heads/gone {'0' * 40} refs/heads/gone {remote_sha}"
        assert compute_pre_push_ranges([line]) == []

    def test_new_ref_scans_what_it_would_publish_not_all_of_history(self):
        """git's own `pre-push.sample` says "New branch, examine all
        commits" and uses the bare local sha. Measured on git's own
        repository (82,180 commits), that scans 60,764 commits and 126 MB
        and takes 22.9s in gitleaks alone — for a branch whose new work
        was one commit, which `--not --remotes` scans in 0.06s. The gate
        is for what this push would publish, not for history somebody
        else already pushed."""
        local_sha = "a" * 40
        line = f"refs/heads/new {local_sha} refs/heads/new {'0' * 40}"
        assert compute_pre_push_ranges([line]) == [f"{local_sha} --not --remotes"]

    def test_a_first_ever_push_to_an_empty_remote_still_scans_everything(
        self, tmp_path
    ):
        """`--not --remotes` excludes nothing when there are no
        remote-tracking refs, which is the conservative answer and the
        correct one: nothing has been published yet."""
        import subprocess

        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
        (tmp_path / "a.txt").write_text("x\n")
        subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@e",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "one",
            ],
            cwd=tmp_path,
            check=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        [rev_range] = compute_pre_push_ranges(
            [f"refs/heads/main {head} refs/heads/main {'0' * 40}"]
        )
        counted = subprocess.run(
            ["git", "rev-list", "--count", *rev_range.split()],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert counted == "1"

    def test_existing_ref_scans_the_pushed_range(self):
        local_sha = "a" * 40
        remote_sha = "b" * 40
        line = f"refs/heads/main {local_sha} refs/heads/main {remote_sha}"
        assert compute_pre_push_ranges([line]) == [f"{remote_sha}..{local_sha}"]

    def test_malformed_line_is_skipped_not_crashed_on(self):
        assert compute_pre_push_ranges(["not enough fields"]) == []

    def test_multiple_refs_each_produce_their_own_range(self):
        local_a, remote_a = "a" * 40, "b" * 40
        local_b, remote_b = "c" * 40, "d" * 40
        lines = [
            f"refs/heads/a {local_a} refs/heads/a {remote_a}",
            f"refs/heads/b {local_b} refs/heads/b {remote_b}",
        ]
        assert compute_pre_push_ranges(lines) == [
            f"{remote_a}..{local_a}",
            f"{remote_b}..{local_b}",
        ]


class TestCoreHooksPathRedirect:
    """`core.hooksPath` makes git ignore `.git/hooks` entirely.

    Found by a cold rehearsal, not by this suite: on a machine with a global
    `core.hooksPath`, `rite init` reported "✓ Created .git/hooks/pre-push"
    and a subsequent push carrying four planted secrets went through with
    exit 0 and no gate output at all. The hook was written, was executable,
    was correct, and was never read.
    """

    def test_install_refuses_when_hooks_are_redirected(self, tmp_path: Path):
        init_repo(tmp_path)
        elsewhere = tmp_path / "shared-hooks"
        elsewhere.mkdir()
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", str(elsewhere)],
            cwd=tmp_path,
            check=True,
        )

        result = install_pre_push_hook(tmp_path)

        assert not result.ok
        assert "core.hooksPath" in result.message
        # Refusing means refusing: no file git would never read, and nothing
        # written into the shared directory, which typically belongs to every
        # other repo the user owns.
        assert not (tmp_path / ".git" / "hooks" / "pre-push").exists()
        assert list(elsewhere.iterdir()) == []

    def test_force_does_not_override_a_redirect(self, tmp_path: Path):
        """`force` exists to overwrite a foreign hook, not to write one into
        a directory git does not read — there is nothing there to force."""
        init_repo(tmp_path)
        elsewhere = tmp_path / "shared-hooks"
        elsewhere.mkdir()
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", str(elsewhere)],
            cwd=tmp_path,
            check=True,
        )

        assert not install_pre_push_hook(tmp_path, force=True).ok

    def test_pointing_hooks_path_at_the_repos_own_dir_is_not_a_redirect(
        self, tmp_path: Path
    ):
        """The documented remedy. Setting it explicitly to `.git/hooks` must
        read as "not redirected", or the fix we tell users to apply would
        leave them just as stuck."""
        init_repo(tmp_path)
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", ".git/hooks"],
            cwd=tmp_path,
            check=True,
        )

        assert redirected_hooks_dir(tmp_path) is None
        assert install_pre_push_hook(tmp_path).ok

    def test_fails_open_when_git_cannot_answer(self, tmp_path: Path):
        """Not a repo at all — the caller installs hooks, it does not police
        git config, so an unanswerable question must not read as a redirect."""
        assert redirected_hooks_dir(tmp_path) is None

    def test_unconfigured_repo_is_not_redirected(self, tmp_path: Path):
        init_repo(tmp_path)
        assert redirected_hooks_dir(tmp_path) is None
