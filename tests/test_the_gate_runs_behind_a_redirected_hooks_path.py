"""SCRUM-9 / C6: the publish gate runs even when `core.hooksPath` is redirected.

🔴 **SECURITY-SENSITIVE, in two directions at once, so say both plainly.**

Measured on a real machine: `git config --global core.hooksPath` pointed at
another project's hooks directory, so `install_pre_push_hook` refused — and
rite's publish gate ran NOWHERE automatically, in any rite project, for the
life of that redirect. No `pre-push` in the project root, none in any module.

The refusal was right twice over. A hook written where git is not reading is a
gate that reports installed and never runs — a cold rehearsal pushed four
planted secrets with exit 0 under exactly that. And the redirected directory is
typically shared across every repository the user owns: the one measured here
held a firm data-leak gate belonging to another project, whose own header says
a hook inside a worktree is *"silently disarmed by checking out any commit that
predates it"*. Writing into it, or pointing git away from it, would trade a
confidentiality control for a secrets one.

So rite CHAINS: this repository's own `pre-push` runs the redirected hook
first and the gate second, and only this repository's `core.hooksPath` is
changed. **Whether the upstream hook still runs, in order, with the ref list,
and whether its refusal is final, is the property this file exists to pin** —
getting it wrong silently disables somebody else's gate, which is strictly
worse than the loud refusal it replaces.

Also here: `install_pre_push_hook` began `if not (repo_root / ".git").is_dir()`,
and in a worktree `.git` is a FILE. No worktree has ever had the gate
installed, for a reason that had nothing to do with the redirect it blamed.

Real git, real hooks, real pushes to a real (local) remote. `HOME` and the
system scope are pinned per test so the machine's own `core.hooksPath` cannot
make these pass or fail.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from rite_ai.gate.ci import script_invokes
from rite_ai.gate.hook import (
    HOOK_MARKER,
    PRE_PUSH_INVOCATIONS,
    gate_hook_status,
    install_pre_push_hook,
    local_hooks_path,
    own_hooks_dir,
    redirected_hooks_dir,
    upstream_hooks_dir,
)

UPSTREAM_RAN = "UPSTREAM-HOOK-RAN"
GATE_RAN = "RITE-GATE-RAN"


def _global_config(tmp_path: Path) -> Path:
    return tmp_path / "gitconfig"


@pytest.fixture(autouse=True)
def _own_git_world(tmp_path, monkeypatch):
    """A global git config of this TEST's own, and no system config.

    ⚠ Two reasons, both measured. The machine's real global `core.hooksPath` is
    exactly the condition under test, so a test that inherited it would pass
    here and nowhere else — `conftest.py`'s `_isolated_git_config` already
    pins that away. But its file is SESSION-scoped and shared, and these tests
    WRITE a global redirect: without a per-test file, the first test that set
    one left it set for every later test in the session, which showed up as
    "nothing changes with no redirect" failing while the redirect tests
    passed.
    """
    path = _global_config(tmp_path)
    path.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=a@b", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "one")
    return path


def _remote(tmp_path: Path, repo: Path) -> Path:
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    return bare


def _an_upstream_hook(tmp_path: Path, exit_code: int = 0) -> Path:
    """Another project's hooks directory, with a `pre-push` that reports what it
    saw — argv and the ref list — and exits `exit_code`."""
    hooks = tmp_path / "other-project-hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        f'echo "{UPSTREAM_RAN} argv=[$*]" >&2\n'
        'while read -r line; do echo "' + UPSTREAM_RAN + ' ref=$line" >&2; done\n'
        f"exit {exit_code}\n"
    )
    hook.chmod(0o755)
    return hooks


def _a_fake_rite(tmp_path: Path, exit_code: int = 0) -> Path:
    """A `rite` on PATH that reports the refs the gate was given. The gate
    itself is tested elsewhere; what is under test here is whether it is
    reached, and with what."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "rite"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "{GATE_RAN} argv=[$*]" >&2\n'
        'while read -r line; do echo "' + GATE_RAN + ' ref=$line" >&2; done\n'
        f"exit {exit_code}\n"
    )
    fake.chmod(0o755)
    return bin_dir


def _push(repo: Path, bin_dir: Path, branch: str = "probe"):
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=a@b",
            "-c",
            "user.name=t",
            "push",
            "origin",
            f"HEAD:refs/heads/{branch}",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def _redirect_globally(repo: Path, hooks: Path) -> None:
    _git(repo, "config", "--global", "core.hooksPath", str(hooks))


class TestBothGatesRun:
    """The property. Nothing else in this file matters if this is wrong."""

    def _chained(self, tmp_path, *, upstream_exit=0, gate_exit=0):
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        hooks = _an_upstream_hook(tmp_path, upstream_exit)
        _redirect_globally(repo, hooks)
        result = install_pre_push_hook(repo)
        assert result.ok, result.message
        return repo, _a_fake_rite(tmp_path, gate_exit), hooks

    def test_the_other_projects_hook_still_runs(self, tmp_path):
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        assert UPSTREAM_RAN in out

    def test_and_the_gate_runs_too(self, tmp_path):
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        assert GATE_RAN in out

    def test_the_other_projects_hook_runs_FIRST(self, tmp_path):
        """🔴 A confidentiality gate consulted after the push has been decided
        is not a gate."""
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        assert out.index(UPSTREAM_RAN) < out.index(GATE_RAN)

    def test_the_ref_list_reaches_both(self, tmp_path):
        """⚠ git feeds pre-push its refs on STDIN, which one reader consumes.
        A naive chain leaves the second hook with nothing to scan — and a gate
        handed no refs reports "nothing to scan" and exits 0."""
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        upstream_refs = [ln for ln in out.splitlines() if f"{UPSTREAM_RAN} ref=" in ln]
        gate_refs = [ln for ln in out.splitlines() if f"{GATE_RAN} ref=" in ln]
        assert upstream_refs and gate_refs
        assert [ln.split("ref=", 1)[1] for ln in upstream_refs] == [
            ln.split("ref=", 1)[1] for ln in gate_refs
        ]

    def test_and_the_refs_are_the_ones_git_sent(self, tmp_path):
        """Not an empty line, not a re-echo: the four fields of git's own
        pre-push protocol."""
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        refs = [
            ln.split("ref=", 1)[1]
            for ln in out.splitlines()
            if f"{GATE_RAN} ref=" in ln
        ]
        assert refs
        for ref in refs:
            assert len(ref.split()) == 4, ref

    def test_the_upstream_hook_gets_gits_argv(self, tmp_path):
        """It is a real pre-push hook and may use `<remote-name> <remote-url>`."""
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        line = next(ln for ln in out.splitlines() if f"{UPSTREAM_RAN} argv=" in ln)
        assert "origin" in line

    def test_the_gate_gets_none(self, tmp_path):
        """⚠ `rite publish pre-push` takes no arguments: forwarding argv made
        every push fail with a Click usage error (exit 2, indistinguishable
        from a real gate failure) before ever reading stdin."""
        repo, bin_dir, _ = self._chained(tmp_path)

        out = _push(repo, bin_dir).stderr

        line = next(ln for ln in out.splitlines() if f"{GATE_RAN} argv=" in ln)
        assert line.endswith("argv=[publish pre-push]")

    def test_a_clean_push_goes_through(self, tmp_path):
        """⚠ The control for the whole class. A chain that blocked everything
        would pass every assertion above."""
        repo, bin_dir, _ = self._chained(tmp_path)

        assert _push(repo, bin_dir).returncode == 0


class TestEitherRefusalIsFinal:
    def _chained(self, tmp_path, *, upstream_exit=0, gate_exit=0):
        return TestBothGatesRun()._chained(
            tmp_path, upstream_exit=upstream_exit, gate_exit=gate_exit
        )

    def test_the_other_projects_hook_can_still_block_a_push(self, tmp_path):
        """🔴 The one that matters most: rite must not become a way to get
        past somebody else's gate."""
        repo, bin_dir, _ = self._chained(tmp_path, upstream_exit=7)

        assert _push(repo, bin_dir).returncode != 0

    def test_and_the_gate_is_not_even_reached(self, tmp_path):
        repo, bin_dir, _ = self._chained(tmp_path, upstream_exit=7)

        out = _push(repo, bin_dir).stderr

        assert GATE_RAN not in out

    def test_which_is_said_rather_than_left_to_be_inferred(self, tmp_path):
        repo, bin_dir, hooks = self._chained(tmp_path, upstream_exit=7)

        out = _push(repo, bin_dir).stderr

        assert f"{hooks}/pre-push refused this push" in out
        assert "exit 7" in out

    def test_the_gate_can_block_a_push_too(self, tmp_path):
        repo, bin_dir, _ = self._chained(tmp_path, gate_exit=3)

        assert _push(repo, bin_dir).returncode != 0

    def test_and_the_other_hook_ran_before_it_did(self, tmp_path):
        """Order again, on the path where rite is the one refusing."""
        repo, bin_dir, _ = self._chained(tmp_path, gate_exit=3)

        assert UPSTREAM_RAN in _push(repo, bin_dir).stderr


class TestNothingOutsideTheRepositoryIsTouched:
    """The reason the old refusal existed. A fix that reached into the shared
    directory, or switched the global redirect off, would be the "surprise in
    someone else's repo" the installer refuses to cause."""

    def _installed(self, tmp_path):
        repo = _repo(tmp_path / "app")
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        before = _global_config(tmp_path).read_text()
        assert install_pre_push_hook(repo).ok
        return repo, hooks, before

    def test_the_global_config_is_unchanged(self, tmp_path):
        _repo_, _hooks, before = self._installed(tmp_path)

        assert _global_config(tmp_path).read_text() == before

    def test_the_global_redirect_still_says_what_it_said(self, tmp_path):
        repo, hooks, _ = self._installed(tmp_path)

        assert _git(repo, "config", "--global", "--get", "core.hooksPath") == str(hooks)

    def test_the_other_projects_hook_file_is_untouched(self, tmp_path):
        _repo_, hooks, _ = self._installed(tmp_path)

        text = (hooks / "pre-push").read_text()
        assert HOOK_MARKER not in text
        assert "rite" not in text

    def test_only_this_repositorys_hooks_path_is_set(self, tmp_path):
        repo, _hooks, _ = self._installed(tmp_path)

        assert local_hooks_path(repo) == own_hooks_dir(repo)

    def test_and_it_is_absolute(self, tmp_path):
        """⚠ **Measured, and the reason this is not spelled `.git/hooks`.** A
        relative `core.hooksPath` resolves against the working tree a hook runs
        in. With `.git/hooks` set, a push from the main checkout ran the hook
        and a push from a WORKTREE ran nothing at all and succeeded — both
        gates gone, silently."""
        repo, _hooks, _ = self._installed(tmp_path)

        raw = _git(repo, "config", "--local", "--get", "core.hooksPath")
        assert Path(raw).is_absolute()


class TestTheChainIsResolvedWhenItRuns:
    def test_the_hook_does_not_bake_in_the_other_projects_path(self, tmp_path):
        """If that project moves or renames its hooks directory, the chain
        follows whatever git says today rather than carrying a stale path."""
        repo = _repo(tmp_path / "app")
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        install_pre_push_hook(repo)

        script = (own_hooks_dir(repo) / "pre-push").read_text()

        assert str(hooks) not in script
        assert "core.hooksPath" in script

    def test_moving_the_other_projects_hooks_keeps_the_chain_working(self, tmp_path):
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        install_pre_push_hook(repo)
        moved = tmp_path / "moved-hooks"
        hooks.rename(moved)
        _redirect_globally(repo, moved)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        assert UPSTREAM_RAN in out
        assert GATE_RAN in out

    def test_an_upstream_that_has_no_pre_push_yet_is_simply_skipped(self, tmp_path):
        """And picked up the moment it appears — which is why resolving at run
        time matters rather than refusing at install time: the other project's
        installer may write its hook after rite ran."""
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        empty = tmp_path / "empty-hooks"
        empty.mkdir()
        _redirect_globally(repo, empty)
        assert install_pre_push_hook(repo).ok
        bin_dir = _a_fake_rite(tmp_path)

        first = _push(repo, bin_dir, "p1").stderr
        assert GATE_RAN in first and UPSTREAM_RAN not in first

        later = empty / "pre-push"
        later.write_text(f'#!/bin/sh\necho "{UPSTREAM_RAN}" >&2\nexit 0\n')
        later.chmod(0o755)
        (repo / "a.txt").write_text("two\n")
        _git(repo, "commit", "-aqm", "two")

        second = _push(repo, bin_dir, "p2").stderr
        assert UPSTREAM_RAN in second and GATE_RAN in second


class TestReInstallingDoesNotQuietlyDropTheChain:
    """🔴 The trap this almost fell into. Once `core.hooksPath` points back at
    the repo, "is this repo redirected?" answers NO — so an installer that
    asked that question would write the PLAIN script on the second run and
    disarm the other project's gate, with every signal still saying active."""

    def test_running_it_twice_keeps_the_chain(self, tmp_path):
        repo = _repo(tmp_path / "app")
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        install_pre_push_hook(repo)

        again = install_pre_push_hook(repo, force=True)

        assert again.ok, again.message
        assert "core.hooksPath" in (own_hooks_dir(repo) / "pre-push").read_text()

    def test_and_both_still_run(self, tmp_path):
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        install_pre_push_hook(repo)
        install_pre_push_hook(repo, force=True)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        assert UPSTREAM_RAN in out and GATE_RAN in out

    def test_upstream_is_asked_of_the_global_scope_not_of_git_s_answer(self, tmp_path):
        repo = _repo(tmp_path / "app")
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        install_pre_push_hook(repo)

        # git now reads hooks from this repo, so there is no redirect…
        assert redirected_hooks_dir(repo) is None
        # …and the thing that has to keep running is still there.
        assert upstream_hooks_dir(repo) == hooks


class TestItRefusesLoudlyRatherThanHalfChaining:
    def test_a_hooks_path_this_repository_set_itself_is_not_overwritten(self, tmp_path):
        """Somebody chose that, here, for this repository. rite will not
        silently repoint it — and cannot chain behind it, because the chain
        resolves the global and system scopes at run time, which is exactly
        what a local value overrides."""
        repo = _repo(tmp_path / "app")
        mine = tmp_path / "my-own-hooks"
        mine.mkdir()
        _git(repo, "config", "--local", "core.hooksPath", str(mine))

        result = install_pre_push_hook(repo)

        assert not result.ok
        assert (
            "your own git config" in result.message
            or "own git config" in result.message
        )
        assert str(mine) in result.message

    def test_and_leaves_that_config_alone(self, tmp_path):
        repo = _repo(tmp_path / "app")
        mine = tmp_path / "my-own-hooks"
        mine.mkdir()
        _git(repo, "config", "--local", "core.hooksPath", str(mine))

        install_pre_push_hook(repo)

        assert local_hooks_path(repo) == mine.resolve()

    def test_the_hook_exists_before_git_is_pointed_at_it(self, tmp_path, monkeypatch):
        """🔴 **The order IS the safety property, so it is asserted directly
        rather than inferred.** Setting `core.hooksPath` first opens a window in
        which git reads a directory with no `pre-push` in it — and anything
        that fails in that window silently drops the other project's gate,
        which is strictly worse than the loud refusal this replaced. Checked by
        looking at the filesystem at the moment the config is written.
        """
        from rite_ai.gate import hook as hook_mod

        repo = _repo(tmp_path / "app")
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        own = own_hooks_dir(repo)
        seen = {}
        real = hook_mod._git

        def watch(root, *args):
            if args[:2] == ("config", "--local") and len(args) > 3:
                seen["hook_was_there"] = (own / "pre-push").is_file()
            return real(root, *args)

        monkeypatch.setattr(hook_mod, "_git", watch)

        assert install_pre_push_hook(repo).ok
        assert seen.get("hook_was_there") is True

    def test_a_config_that_cannot_be_written_refuses_and_says_what_still_runs(
        self, tmp_path, monkeypatch
    ):
        """🔴 The order is the safety property: the hook is written BEFORE
        `core.hooksPath` is pointed at it, so a failure leaves an inert file in
        a directory git is not reading — the status quo — rather than a window
        in which git reads a directory with no pre-push in it."""
        from rite_ai.gate import hook as hook_mod

        repo = _repo(tmp_path / "app")
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        real = hook_mod._git

        def no_config(root, *args):
            if args[:2] == ("config", "--local") and len(args) > 3:
                return None  # the write fails; reads still work
            return real(root, *args)

        monkeypatch.setattr(hook_mod, "_git", no_config)

        result = install_pre_push_hook(repo)

        assert not result.ok
        assert "does NOT run on push" in result.message
        assert "Nothing else was changed" in result.message

    def test_and_the_other_projects_gate_is_still_the_one_git_runs(
        self, tmp_path, monkeypatch
    ):
        from rite_ai.gate import hook as hook_mod

        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(repo, hooks)
        real = hook_mod._git
        monkeypatch.setattr(
            hook_mod,
            "_git",
            lambda root, *a: (
                None
                if (a[:2] == ("config", "--local") and len(a) > 3)
                else real(root, *a)
            ),
        )
        install_pre_push_hook(repo)
        # Put only `_git` back. `monkeypatch.undo()` would also revert the
        # autouse fixture's GIT_CONFIG_GLOBAL, taking the redirect under test
        # with it — and the push would then find no upstream hook for a reason
        # that has nothing to do with the failure being rehearsed.
        monkeypatch.setattr(hook_mod, "_git", real)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        # Unchanged from before rite ran: the other project's hook, and only it.
        assert UPSTREAM_RAN in out
        assert GATE_RAN not in out


class TestAWorktreeGetsOneAtAll:
    """⚠ `install_pre_push_hook` began `if not (repo_root / ".git").is_dir()`,
    and in a worktree `.git` is a FILE — 56 bytes of text pointing at the
    common directory. So no worktree has ever had the gate installed, for a
    reason that had nothing to do with `core.hooksPath`; and
    `redirected_hooks_dir` reported a redirect in every worktree, with none
    set, because it compared git's answer against a path that does not exist
    there."""

    def _with_a_worktree(self, tmp_path) -> tuple[Path, Path]:
        main = _repo(tmp_path / "main")
        wt = tmp_path / "wt"
        _git(main, "worktree", "add", "-q", str(wt))
        assert (wt / ".git").is_file(), "the premise"
        return main, wt

    def test_a_worktree_is_recognised_as_a_repository(self, tmp_path):
        _main, wt = self._with_a_worktree(tmp_path)

        result = install_pre_push_hook(wt)

        assert result.ok, result.message

    def test_it_is_not_reported_as_redirected_when_nothing_redirects_it(self, tmp_path):
        _main, wt = self._with_a_worktree(tmp_path)

        assert redirected_hooks_dir(wt) is None

    def test_the_hook_lands_where_git_actually_reads_it(self, tmp_path):
        """Hooks are shared between a repository's worktrees — measured with
        `git rev-parse --git-path hooks`, which reports the common directory
        from inside a worktree."""
        main, wt = self._with_a_worktree(tmp_path)

        install_pre_push_hook(wt)

        assert own_hooks_dir(wt) == own_hooks_dir(main)
        assert (own_hooks_dir(wt) / "pre-push").is_file()

    def test_and_the_gate_reports_itself_active_there(self, tmp_path):
        _main, wt = self._with_a_worktree(tmp_path)
        install_pre_push_hook(wt)

        status = gate_hook_status(wt)

        assert status.active, status.detail

    def test_a_push_from_a_worktree_runs_the_chain(self, tmp_path):
        """The two defects crossed: a worktree AND a global redirect, which is
        the machine this was found on."""
        main, wt = self._with_a_worktree(tmp_path)
        _remote(tmp_path, main)
        _redirect_globally(main, _an_upstream_hook(tmp_path))
        assert install_pre_push_hook(wt).ok
        (wt / "b.txt").write_text("x\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "b")

        out = _push(wt, _a_fake_rite(tmp_path)).stderr

        assert UPSTREAM_RAN in out and GATE_RAN in out

    def test_a_plain_subdirectory_is_still_not_a_repository(self, tmp_path):
        """⚠ The control. git answers for any directory INSIDE a repository, so
        asking it alone would make a module registered as `backend/` report the
        project's hooks as its own and `rite doctor` call its gate active."""
        main = _repo(tmp_path / "main")
        (main / "backend").mkdir()

        assert own_hooks_dir(main / "backend") is None
        assert gate_hook_status(main / "backend").state == "not_a_repo"


class TestTheChainedScriptIsReadAsRunningTheGate:
    """`gate/ci.py::script_invokes` is what `rite doctor` and
    `gate_hook_status` use to decide whether a hook really runs the gate, and
    it answers NO where it cannot tell. A chained hook it cannot read is a
    chained hook every instrument calls inactive."""

    def test_it_is_recognised(self, tmp_path):
        repo = _repo(tmp_path / "app")
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        install_pre_push_hook(repo)

        script = (own_hooks_dir(repo) / "pre-push").read_text()

        assert script_invokes(script, PRE_PUSH_INVOCATIONS)

    def test_and_rite_doctor_calls_it_active(self, tmp_path):
        repo = _repo(tmp_path / "app")
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        install_pre_push_hook(repo)

        status = gate_hook_status(repo)

        assert status.active, status.detail

    def test_it_carries_rites_marker_so_an_upgrade_may_replace_it(self, tmp_path):
        repo = _repo(tmp_path / "app")
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        install_pre_push_hook(repo)

        assert HOOK_MARKER in (own_hooks_dir(repo) / "pre-push").read_text()

    def test_a_hand_written_hook_is_still_not_clobbered(self, tmp_path):
        """⚠ Unchanged by any of this, and asserted because the chained path
        writes in a case the old code refused outright."""
        repo = _repo(tmp_path / "app")
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        own = own_hooks_dir(repo)
        own.mkdir(parents=True, exist_ok=True)
        (own / "pre-push").write_text("#!/bin/sh\necho mine\n")

        result = install_pre_push_hook(repo)

        assert not result.ok
        assert "not installed by rite" in result.message
        assert (own / "pre-push").read_text() == "#!/bin/sh\necho mine\n"


class TestWithNoRedirectNothingChanges:
    """⚠ The control for the whole file. The ordinary case must keep behaving
    exactly as it did, including not touching git config at all."""

    def test_the_plain_hook_is_written(self, tmp_path):
        repo = _repo(tmp_path / "app")

        assert install_pre_push_hook(repo).ok
        script = (own_hooks_dir(repo) / "pre-push").read_text()
        assert script == "#!/bin/sh\n" + HOOK_MARKER + "\nexec rite publish pre-push\n"

    def test_and_core_hookspath_is_not_set(self, tmp_path):
        repo = _repo(tmp_path / "app")

        install_pre_push_hook(repo)

        assert local_hooks_path(repo) is None

    def test_the_gate_still_runs_on_a_push(self, tmp_path):
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        install_pre_push_hook(repo)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        assert GATE_RAN in out


class TestInitSaysWhatItInstalled:
    """`rite init` is where most people meet this, and "no pre-push hook
    installed" is the loudest line it prints. It had one `.git/hooks` path, one
    redirect refusal and one write of its own — a SECOND installer, kept in
    step with `gate/hook.py` by hand. The chaining went into that module, so
    until init delegated, `rite init` would have gone on refusing while `rite
    publish install-hook` chained."""

    def _project(self, tmp_path: Path) -> Path:
        root = _repo(tmp_path / "proj")
        return root

    def _init(self, root: Path):
        from click.testing import CliRunner

        from rite_ai.cli.main import cli

        result = CliRunner().invoke(cli, ["init", "--yes", str(root)])
        assert result.exit_code == 0, result.output
        return result.output

    def test_it_says_the_hook_was_installed_and_what_it_runs_behind(self, tmp_path):
        root = self._project(tmp_path)
        hooks = _an_upstream_hook(tmp_path)
        _redirect_globally(root, hooks)

        out = self._init(root)

        assert "pre-push hook" in out
        assert f"chained behind {hooks}/pre-push" in out

    def test_and_the_hook_it_installed_really_chains(self, tmp_path):
        """Not only the sentence. ⚠ init used to print `✓ Created
        .git/hooks/pre-push` for a hook git would never read — the claim and
        the file are checked separately for that reason."""
        root = self._project(tmp_path)
        _redirect_globally(root, _an_upstream_hook(tmp_path))
        _remote(tmp_path, root)

        self._init(root)
        out = _push(root, _a_fake_rite(tmp_path)).stderr

        assert UPSTREAM_RAN in out and GATE_RAN in out

    def test_with_no_redirect_it_says_nothing_about_chaining(self, tmp_path):
        """⚠ The control."""
        root = self._project(tmp_path)

        out = self._init(root)

        assert "pre-push hook" in out
        assert "chained behind" not in out

    def test_a_refusal_is_reported_in_the_installers_own_words(self, tmp_path):
        """One installer means one set of words for each cause. init could only
        ever describe the one cause it knew about."""
        root = self._project(tmp_path)
        mine = tmp_path / "my-own-hooks"
        mine.mkdir()
        _git(root, "config", "--local", "core.hooksPath", str(mine))

        out = self._init(root)

        assert "no pre-push hook installed" in out
        assert str(mine) in out

    def test_init_goes_through_the_one_installer(self, tmp_path, monkeypatch):
        from rite_ai.cli.init import scaffold

        calls = []
        real = install_pre_push_hook
        monkeypatch.setattr(
            "rite_ai.gate.hook.install_pre_push_hook",
            lambda root, force=False: calls.append(root) or real(root, force=force),
        )
        root = self._project(tmp_path)
        _redirect_globally(root, _an_upstream_hook(tmp_path))

        scaffold.install_pre_push_hooks(root, [])

        assert calls == [root]


class TestATildeInTheRedirectIsStillTheSameDirectory:
    """🔴 **The whole chain hangs on both halves reading one value the same
    way, and they did not.**

    git stores `core.hooksPath` verbatim. A value written `~/…` — which is how
    a person or an installer naturally writes a path under `$HOME` — comes back
    from a bare `git config --get` with a literal tilde, and no shell expands a
    tilde inside quotes. So `[ -x "~/…/pre-push" ]` is FALSE for a hook that is
    there, the script falls through to `rite publish pre-push` alone, and the
    upstream hook is **silently disarmed while the installer reports a chain**
    — the exact failure the loud refusal was replaced to avoid, reintroduced
    one layer down.

    The Python half always expanduser()d, so it saw the hook, decided to chain,
    and said so. The disagreement between the two halves is the defect; the
    tilde is only what exposes it. `git config --type=path` is git's own
    expansion, measured to expand `~` and to leave absolute and relative values
    untouched, so both halves now read the value through the same rule.

    ⚠ Latent rather than live on the machine this was found on, whose value is
    absolute — and latent is the point: anything that rewrites that line in `~`
    form turns the gate off with no output anywhere.
    """

    def _with_a_tilde_redirect(self, tmp_path, monkeypatch):
        """A redirect written `~/…`, with `$HOME` this test's own so the tilde
        resolves somewhere it can be checked."""
        home = tmp_path / "home"
        (home / "other-project-hooks").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        hook = home / "other-project-hooks" / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            f'echo "{UPSTREAM_RAN} argv=[$*]" >&2\n'
            'while read -r line; do echo "' + UPSTREAM_RAN + ' ref=$line" >&2; done\n'
            "exit 0\n"
        )
        hook.chmod(0o755)
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        # The tilde FORM, not the expanded path: that is the condition.
        _git(repo, "config", "--global", "core.hooksPath", "~/other-project-hooks")
        assert (
            _git(repo, "config", "--global", "--get", "core.hooksPath")
            == "~/other-project-hooks"
        ), "the premise: git stores it verbatim"
        return repo, home

    def test_the_installer_still_sees_it_and_chains(self, tmp_path, monkeypatch):
        repo, home = self._with_a_tilde_redirect(tmp_path, monkeypatch)

        result = install_pre_push_hook(repo)

        assert result.ok, result.message
        assert "chained behind" in result.message

    def test_and_the_upstream_hook_RUNS(self, tmp_path, monkeypatch):
        """🔴 The assertion the defect would fail. Everything else about the
        install reported success while this was false."""
        repo, _home = self._with_a_tilde_redirect(tmp_path, monkeypatch)
        assert install_pre_push_hook(repo).ok

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        assert out.count(UPSTREAM_RAN + " argv=") == 1, out

    def test_and_it_saw_the_refs(self, tmp_path, monkeypatch):
        repo, _home = self._with_a_tilde_redirect(tmp_path, monkeypatch)
        install_pre_push_hook(repo)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        refs = [ln for ln in out.splitlines() if f"{UPSTREAM_RAN} ref=" in ln]
        assert refs, out
        for ref in refs:
            assert len(ref.split("ref=", 1)[1].split()) == 4, ref

    def test_the_gate_runs_after_it_as_usual(self, tmp_path, monkeypatch):
        repo, _home = self._with_a_tilde_redirect(tmp_path, monkeypatch)
        install_pre_push_hook(repo)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        assert UPSTREAM_RAN in out and GATE_RAN in out
        assert out.index(UPSTREAM_RAN) < out.index(GATE_RAN)

    def test_a_tilde_upstream_refusal_is_still_final(self, tmp_path, monkeypatch):
        """The consequence that matters: not just that it ran, but that it can
        still stop a push."""
        repo, home = self._with_a_tilde_redirect(tmp_path, monkeypatch)
        hook = home / "other-project-hooks" / "pre-push"
        hook.write_text(hook.read_text().replace("exit 0", "exit 9"))
        hook.chmod(0o755)
        install_pre_push_hook(repo)

        pushed = _push(repo, _a_fake_rite(tmp_path))

        assert pushed.returncode != 0
        assert GATE_RAN not in pushed.stderr

    def test_the_hook_reads_the_value_through_gits_own_expansion(self, tmp_path):
        """⚠ Pinned as TEXT as well as behaviour, because the behavioural test
        above passes for an absolute value too: the script must not go back to a
        bare `--get`, which is what made the two halves disagree."""
        repo = _repo(tmp_path / "app")
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        install_pre_push_hook(repo)

        script = (own_hooks_dir(repo) / "pre-push").read_text()

        assert "--type=path --get core.hooksPath" in script
        assert "--global --get core.hooksPath" not in script
        assert "--system --get core.hooksPath" not in script

    def test_an_absolute_redirect_is_unaffected_by_the_expansion(
        self, tmp_path, monkeypatch
    ):
        """⚠ The control. `--type=path` leaves an absolute value alone —
        measured — so the machine this was found on keeps working exactly as
        it did."""
        repo = _repo(tmp_path / "app")
        _remote(tmp_path, repo)
        _redirect_globally(repo, _an_upstream_hook(tmp_path))
        install_pre_push_hook(repo)

        out = _push(repo, _a_fake_rite(tmp_path)).stderr

        assert UPSTREAM_RAN in out and GATE_RAN in out
