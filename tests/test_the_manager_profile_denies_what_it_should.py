"""The sandbox profile a Manager runs inside (B9, piece 1).

⚠ **The tests that matter here RUN `sandbox-exec`**, because a profile that
parses is not a profile that denies anything. Every grant in `enclosure.py`
was added in response to a measured failure, and three of them were added
after a check that was supposed to fail passed instead.

⚠ **Read `docs/design/spikes/B9-manager-sandboxing.md` first.** A sandboxed
Manager cannot start a sandboxed Worker — the kernel refuses — which is why
`~/.yoloai` is denied here on purpose rather than by omission.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from rite_ai.managers.enclosure import (
    compose,
    engine_tmp,
    limitations,
    profile_path,
    write_profile,
)

on_macos = pytest.mark.skipif(sys.platform != "darwin", reason="seatbelt is macOS only")


def _under(profile: Path, command: str) -> int:
    done = subprocess.run(
        ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return done.returncode


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".rite").mkdir()
    (tmp_path / "file.txt").write_text("project content\n")
    return tmp_path


class TestTheProfileIsDenyDefault:
    def test_nothing_is_permitted_unless_named(self, project):
        assert "(deny default)" in compose(project, "lead")

    def test_it_points_at_what_it_does_and_does_not_buy(self, project):
        """A boundary sold as more than it is would be worse than none."""
        assert "B9-manager-sandboxing" in compose(project, "lead")

    def test_it_says_the_network_is_not_confined(self, project):
        """⚠ `(allow network*)` is in the shipped WORKER profile too.
        Seatbelt has no network isolation (D-30) — a line in the file rather
        than a limitation rite might mitigate."""
        text = compose(project, "lead")
        assert "(allow network*)" in text
        assert any("network is NOT confined" in line for line in limitations())


class TestAHostilePathCannotBecomePolicy:
    """This file is parsed by something that acts on it, so the rule is the
    one `session_id_problem` uses: refuse, do not escape."""

    @pytest.mark.parametrize("bad", ['pro"ject', "pro\\ject"])
    def test_a_quote_or_backslash_in_the_root_is_refused(self, tmp_path, bad):
        root = tmp_path / bad
        root.mkdir()
        with pytest.raises(ValueError) as raised:
            compose(root, "lead")
        assert "read as policy" in str(raised.value)

    def test_a_manager_name_that_is_not_a_name_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            profile_path(tmp_path, "../escape")


class TestTheProfileIsRewrittenEveryRun:
    def test_an_edit_does_not_survive(self, project):
        """As `permissions.write_settings`: a write-once file pins a project
        to whatever shipped the day it was created."""
        path = write_profile(project, "lead")
        path.write_text("(version 1)\n(allow default)\n")
        assert "(deny default)" in write_profile(project, "lead").read_text()

    def test_the_engine_gets_its_own_tmp(self, project):
        """⚠ But NOT its own HOME: Claude's login lives there, and
        redirecting it answers "Not logged in". See
        `ENGINE_HOME_IS_THE_OPERATORS`."""
        write_profile(project, "lead")
        assert engine_tmp(project, "lead").is_dir()


@on_macos
class TestItActuallyPermitsWhatAManagerNeeds:
    """Each of these was a real failure before the grant that fixes it."""

    def test_the_project_tree_is_readable_and_writable(self, project):
        profile = write_profile(project, "lead")
        assert _under(profile, f"cat {project}/file.txt") == 0
        assert _under(profile, f"touch {project}/new.txt") == 0

    def test_rite_itself_runs(self, project):
        """⚠ Granting only `uv/tools` gave `Abort trap: 6` and
        `dyld: Library not loaded ... (file system sandbox blocked open())` —
        a crash rather than a refusal, which reads as a broken rite."""
        if not _which("rite"):
            pytest.skip("rite is not on PATH in this environment")
        profile = write_profile(project, "lead")
        assert _under(profile, "rite --version >/dev/null 2>&1") == 0

    def test_a_shell_can_redirect_to_dev_null(self, project):
        """Without a writable `/dev`, every `cmd >/dev/null` fails — most of
        what a shell does, and none of what a boundary is for."""
        profile = write_profile(project, "lead")
        assert _under(profile, "echo hello >/dev/null") == 0


@on_macos
class TestItActuallyDeniesWhatItShould:
    def test_another_project_on_this_machine_is_unreachable(self, project, tmp_path):
        """⚠ **This check passed when it should have failed**, because an
        earlier draft granted the whole per-user `$TMPDIR` to give the engine
        somewhere to write — and every other process's scratch lives there.
        The engine got its own instead."""
        other = tmp_path.parent / (tmp_path.name + "-other")
        other.mkdir()
        (other / "secret.txt").write_text("another project's file\n")
        profile = write_profile(project, "lead")
        assert _under(profile, f"cat {other}/secret.txt") != 0

    @pytest.mark.parametrize("private", ["Documents", ".ssh"])
    def test_the_operators_home_outside_the_named_paths_is_unreachable(
        self, project, private
    ):
        profile = write_profile(project, "lead")
        assert _under(profile, f"ls {Path.home() / private}") != 0

    def test_git_identity_is_readable_and_NOT_writable(self, project):
        """An agent that can rewrite git config can change what every later
        commit claims."""
        profile = write_profile(project, "lead")
        assert _under(profile, f"touch {Path.home() / '.gitconfig'}") != 0

    def test_the_yoloAI_library_is_unreachable_ON_PURPOSE(self, project):
        """⚠ Not an omission. A Manager cannot create a sandbox from inside
        one (B9), so reaching `yoloai` would fail at the one job a Manager
        exists for, after appearing to start correctly."""
        profile = write_profile(project, "lead")
        assert _under(profile, f"ls {Path.home() / '.yoloai' / 'library'}") != 0


class TestTheLimitationsAreSaidOutLoud:
    def test_there_are_some(self):
        assert limitations()

    def test_they_name_the_three_things_this_does_not_do(self):
        said = " ".join(limitations()).lower()
        assert "network" in said
        assert "rite" in said
        assert "ticket text" in said


def _which(binary: str) -> str | None:
    import shutil

    return shutil.which(binary, path=os.environ.get("PATH", ""))


@on_macos
class TestTheEscapesThatWereFoundAfterItShipped:
    """⚠ **The first profile shipped with two holes, and both were found by
    running rather than by reading it.** These tests exist so they cannot
    come back quietly.

    A profile refuses what a process does DIRECTLY. It does not refuse what
    a process asks something else to do — and a Manager lives in tmux, whose
    server runs outside the sandbox.
    """

    def _server(self, tmp_path):
        """A tmux server of our own, so nothing here touches the
        operator's."""
        sockets = tmp_path / "sock"
        sockets.mkdir()
        env = dict(os.environ, TMUX_TMPDIR=str(sockets))
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", "victim", "sleep 300"],
            env=env,
            timeout=60,
            capture_output=True,
        )
        return env

    def _under(self, profile, command, env):
        return subprocess.run(
            ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        ).returncode

    def test_a_write_cannot_be_smuggled_through_the_tmux_server(
        self, project, tmp_path, monkeypatch
    ):
        """⚠ **Measured before the fix: this SUCCEEDED.** The file appeared
        outside the boundary, written by the tmux server on the Manager's
        behalf."""
        env = self._server(tmp_path)
        monkeypatch.setenv("TMUX_TMPDIR", env["TMUX_TMPDIR"])
        profile = write_profile(project, "lead")
        target = tmp_path / "escaped.txt"
        try:
            self._under(profile, f"tmux new-session -d 'touch {target}'", env)
            time.sleep(2)
            assert not target.exists(), (
                "the tmux server wrote a file on the sandbox's behalf — the "
                "escape is open again"
            )
        finally:
            subprocess.run(
                ["tmux", "kill-server"], env=env, timeout=30, capture_output=True
            )

    def test_a_process_outside_the_sandbox_cannot_be_signalled(self, project):
        """⚠ **Measured before the fix: the sandbox killed a process it had
        not started.** One Manager could end another, or the supervisor
        watching it."""
        profile = write_profile(project, "lead")
        victim = subprocess.Popen(["sleep", "300"])
        try:
            time.sleep(0.4)
            self._under(profile, f"kill {victim.pid}", dict(os.environ))
            time.sleep(0.4)
            assert victim.poll() is None, (
                "a process outside the sandbox was killed from inside it"
            )
        finally:
            victim.kill()

    def test_a_manager_can_still_signal_its_OWN_children(self, project):
        """`same-sandbox`, not `self`: a Manager that cannot stop a build it
        started is a Manager with a new problem."""
        profile = write_profile(project, "lead")
        assert (
            self._under(
                profile, "sleep 30 & p=$!; sleep 0.3; kill $p", dict(os.environ)
            )
            == 0
        )

    def test_the_denials_are_LAST_because_seatbelt_takes_the_last_match(self, project):
        """⚠ Measured: the same denial placed beside the network rule
        changed nothing, because `/private/tmp` is granted further down and
        won."""
        text = compose(project, "lead")
        last_deny = text.rindex("(deny ")
        last_temp_grant = text.rindex(
            '(allow file-read* file-write* (subpath "/private/tmp")'
        )
        assert last_deny > last_temp_grant

    def test_the_socket_DIRECTORY_is_denied_and_not_the_whole_temp_root(self, project):
        """⚠ `TMUX_TMPDIR` is unset in production, which makes the temp root
        `/private/tmp` — denying THAT would deny what this profile grants
        three lines up, and what rite's own worktrees live in. The suite
        sets `TMUX_TMPDIR` for socket isolation (C1), so the directory is
        derived rather than written out."""
        text = compose(project, "lead")
        root = os.environ.get("TMUX_TMPDIR") or "/private/tmp"
        assert f'(subpath "{root}/tmux-{os.getuid()}"))' in text
        assert f'(deny network-outbound (subpath "{root}"))' not in text
        assert '(deny file-read* file-write* (subpath "/private/tmp"))' not in text


@on_macos
class TestTheNetworkIsNotNarrowedInstead:
    """⚠ The first idea was to narrow `(allow network*)` to loopback, which
    does block the tmux socket. Measured: it also blocks every outside host,
    which takes the ticket backend from every Manager and the API from a
    Claude one — and seatbelt cannot express a destination allowlist, it
    rejects a named host at load with *"host must be * or localhost"*. So
    the socket is denied and the network is left alone."""

    def test_the_network_is_still_granted(self, project):
        assert "(allow network*)" in compose(project, "lead")

    def test_and_the_limitations_still_say_it_is_not_confined(self):
        assert any("network is NOT confined" in line for line in limitations())


@on_macos
class TestTheBoardIsStillReachable:
    """⚠ **A regression the profile introduced, found after it shipped.**

    `gh` cannot START without its config directory — not merely cannot
    authenticate. Measured 2026-09-25 inside the shipped profile:

        gh api rate_limit  ->  exit 1
        failed to create root command: failed to read configuration:
        open ~/.config/gh/config.yml: operation not permitted

    and a `GITHUB_TOKEN` did not rescue it, because gh reads its config
    before it looks at any credential. That took the GitHub board away from
    every sandboxed Manager, and from `git push` over HTTPS, which uses gh
    as its credential helper.
    """

    def test_gh_can_start(self, project):
        if not _which("gh"):
            pytest.skip("gh is not installed in this environment")
        profile = write_profile(project, "lead")
        assert _under(profile, "gh api rate_limit --jq .rate.limit >/dev/null") == 0

    def test_the_grant_is_read_only(self, project):
        """It needs to READ its configuration. Nothing needs to write it,
        and a writable credential store is a credential an agent can
        rewrite."""
        text = compose(project, "lead")
        home = Path.home()
        assert f'(allow file-read* (subpath "{home}/.config/gh"))' in text
        assert f'file-write* (subpath "{home}/.config/gh")' not in text


class TestTheRiteItsInstructionsNameCanRun:
    """The Manager's instructions name rite by ABSOLUTE PATH (`own_command`,
    `c0e4097`). Measured 2026-09-25 at `8d5fc22`: with that path in a
    checkout's venv, the profile refused it (`PermissionError: …
    .venv/pyvenv.cfg`), so a Manager started from anywhere but a uv tool
    install could not run `rite reply` at all."""

    @on_macos
    def test_the_named_rite_runs_inside_the_profile(self, project):
        from rite_ai import own_command

        profile = write_profile(project, "lead")
        assert _under(profile, f"{own_command()} reply --help >/dev/null 2>&1") == 0

    @on_macos
    def test_it_cannot_rewrite_the_rite_it_runs(self, project):
        """Read-only: a writable copy of its own code is one a Manager could
        change for its next cycle."""
        import rite_ai

        package = Path(rite_ai.__file__).resolve().parent
        if package.is_relative_to(project):
            pytest.skip("the package lives inside the project under test")
        target = package / "rite-probe-should-not-exist"
        profile = write_profile(project, "lead")
        assert _under(profile, f"touch {target}") != 0
        assert not target.exists()

    def test_a_checkout_is_granted_its_src_and_version_not_the_whole_tree(
        self, project
    ):
        import rite_ai

        package = Path(rite_ai.__file__).resolve().parent
        checkout = package.parent.parent
        text = compose(project, "lead")
        assert f'(subpath "{checkout}")' not in text
        assert f'(allow file-read* (subpath "{package.parent}"))' in text


@on_macos
class TestP2BetweenTwoManagersSharingARoot:
    """§5.4.8's P2, pinned between **two Managers** rather than between one
    Manager and a bystander.

    ⚠ **This is the case §5.4.8 recorded as measured but NOT pinned.** The
    tests above establish the two mechanisms — `(target same-sandbox)` and
    the denied tmux socket — against a process outside the sandbox and
    against the Manager's own children. Neither is the property the section
    states, which is about A and B: an outside process is not a sibling
    Manager, and a Manager's own child is the case that must keep working.
    So a change that separated a Manager from the operator while letting two
    Managers reach each other would have passed everything above.

    **The bar is §5.4.8's "by accident", so the accident is what is run**:
    a `pkill` broad enough to match a sibling's engine, and the tmux routes
    a Manager would actually reach for.
    """

    def _profiles(self, project):
        return write_profile(project, "alpha"), write_profile(project, "beta")

    def _run(self, profile, command, env=None):
        return subprocess.run(
            ["sandbox-exec", "-f", str(profile), "/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=90,
            env=env or dict(os.environ),
        )

    def _betas_process(self, project, beta):
        """A long-lived process inside BETA's sandbox, and its pid.

        `exec` so the pid in the file is the sleep's own and not a shell
        that has already gone.
        """
        pidfile = project / "beta.pid"
        started = subprocess.Popen(
            [
                "sandbox-exec",
                "-f",
                str(beta),
                "/bin/sh",
                "-c",
                f"echo $$ > {pidfile}; exec sleep 300",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.05)
        return started, int(pidfile.read_text().strip())

    @staticmethod
    def _running(pid: int) -> bool:
        """⚠ **`kill -0` is not liveness — it succeeds for a ZOMBIE.**
        Measured while mutation-testing this class: with the signal grant
        widened to a blanket `(allow signal)`, alpha killed beta's process
        and every "beta survived" assertion still passed, because the victim
        was `Z <defunct>` and answering `kill -0` yes until its parent reaped
        it. `ps` reports the state, so a reaped-or-gone process and a zombie
        both read as not running.
        """
        done = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        )
        state = done.stdout.strip()
        return bool(state) and not state.startswith("Z")

    @pytest.mark.parametrize("signal_name", ["TERM", "KILL"])
    def test_alpha_cannot_signal_betas_engine(self, project, signal_name):
        alpha, beta = self._profiles(project)
        started, pid = self._betas_process(project, beta)
        try:
            done = self._run(alpha, f"kill -{signal_name} {pid}")
            time.sleep(0.4)
            assert done.returncode != 0, (
                f"alpha's kill -{signal_name} of beta's process was permitted"
            )
            assert self._running(pid), "beta's process was killed by alpha"
        finally:
            started.kill()

    def test_a_broad_pkill_does_not_reach_a_sibling(self, project):
        """The accident §5.4.8 names: a pattern wide enough to match a
        sibling's engine, not a pid deliberately chosen."""
        alpha, beta = self._profiles(project)
        started, pid = self._betas_process(project, beta)
        try:
            self._run(alpha, "pkill -9 -f 'sleep 300'")
            time.sleep(0.4)
            assert self._running(pid), "a broad pkill in alpha reached beta's process"
        finally:
            started.kill()

    @pytest.mark.parametrize(
        "route",
        [
            "send-keys -t victim 'touch {target}' Enter",
            "kill-session -t victim",
            "kill-server",
        ],
    )
    def test_alpha_cannot_drive_or_end_betas_session(
        self, project, tmp_path, monkeypatch, route
    ):
        """Beta's session stands for beta's Manager pane. The tmux server
        runs OUTSIDE both profiles, so anything it accepts runs unconfined —
        which is why the socket rather than the command is what is denied.

        ⚠ **The socket directory is a SHORT temp path, not `tmp_path`.**
        Measured: under `tmp_path` the socket exceeded the AF_UNIX path
        limit, tmux failed with "File name too long", and beta's session
        never started — on which every assertion below is either vacuous or
        an accusation about a session that was never there. Hence the
        precondition.
        """
        sockets = Path(tempfile.mkdtemp(prefix="p2-", dir="/private/tmp"))
        env = dict(os.environ, TMUX_TMPDIR=str(sockets))
        monkeypatch.setenv("TMUX_TMPDIR", str(sockets))
        alpha, _ = self._profiles(project)
        target = tmp_path / "escaped.txt"

        def betas_session_is_up() -> bool:
            return (
                subprocess.run(
                    ["tmux", "has-session", "-t", "victim"],
                    env=env,
                    capture_output=True,
                    timeout=30,
                ).returncode
                == 0
            )

        subprocess.run(
            ["tmux", "new-session", "-d", "-s", "victim", "sleep 300"],
            env=env,
            timeout=60,
            capture_output=True,
        )
        try:
            assert betas_session_is_up(), (
                "beta's session did not start, so this test proves nothing"
            )
            done = self._run(alpha, f"tmux {route.format(target=target)}", env)
            time.sleep(1)
            assert done.returncode != 0, f"alpha's `tmux {route}` was permitted"
            assert not target.exists(), "alpha smuggled a write through beta's server"
            assert betas_session_is_up(), "alpha ended beta's session"
        finally:
            subprocess.run(
                ["tmux", "kill-server"], env=env, timeout=30, capture_output=True
            )
            shutil.rmtree(sockets, ignore_errors=True)

    def test_and_alpha_can_still_signal_its_own_child(self, project):
        """Without this the four above would pass on a profile that
        forbade signalling altogether, which would break every Manager."""
        alpha, _ = self._profiles(project)
        assert self._run(alpha, "sleep 30 & p=$!; sleep 0.3; kill $p").returncode == 0
