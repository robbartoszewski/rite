"""The Landlock backend, exercised against the kernel rather than described.

⚠ **THIS IS THE FILE THAT MAKES LINUX VERIFIABLE WITHOUT THE VM.** Every
Linux claim rite would make in a release note was measured on one Ubuntu
24.04 aarch64 virtual machine. A machine that is about to be deleted is not
evidence, so the same probes run here, in CI, on `ubuntu-latest` — which is a
real **x86_64** kernel.

⚠ **Nothing here is architecture-specific by design, and that is the claim
being tested.** The three Landlock syscalls sit in the architecture-neutral
range and the rules are generic LSM logic, so aarch64 and x86_64 should agree.
`test_the_kernel_struct_sizes_are_what_the_kernel_expects` is the one that
would catch it if they did not.

Where a probe cannot run — no Landlock, or an ABI too old for the feature —
it **skips with the reason stated**. A silent skip is how a green CI lies.
"""

from __future__ import annotations

import ctypes
import os
import signal
import socket
import sys
from pathlib import Path

import pytest

import rite_ai
from rite_ai.managers import enclosure, landlock

ABI = landlock.abi()
NO_LANDLOCK = pytest.mark.skipif(
    ABI == 0,
    reason=(
        f"no Landlock on this kernel (platform={sys.platform}, "
        f"ABI={ABI}) — Landlock arrives in Linux 5.13"
    ),
)
NEEDS_SCOPING = pytest.mark.skipif(
    ABI < landlock.SCOPING_NEEDS_ABI,
    reason=(
        f"Landlock ABI {ABI} has no scoping; signal isolation needs "
        f"ABI {landlock.SCOPING_NEEDS_ABI} (Linux 6.12)"
    ),
)


def _policy(readable=(), writable=()):
    """A policy dict of the shape `landlock.apply` consumes."""
    system = [
        p
        for p in ("/usr", "/bin", "/lib", "/lib64", "/etc", "/proc")
        if os.path.exists(p)
    ]
    return {
        "readable": [*system, *[str(p) for p in readable]],
        "writable": [str(p) for p in writable],
        "readonly_overrides": [],
    }


def _in_child(fn) -> int:
    """Run `fn` behind a fork, because applying a ruleset is irreversible.

    ⚠ `os._exit` skips stdio flushing, so anything the child printed would be
    lost. Found that way: a probe that reported nothing looked like a probe
    that measured nothing.
    """
    pid = os.fork()
    if pid == 0:
        try:
            code = fn()
        except Exception:  # noqa: BLE001 - the exit code IS the result here
            code = 99
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001, S110
            pass
        os._exit(code)
    return os.WEXITSTATUS(os.waitpid(pid, 0)[1])


def _can_write(path) -> bool:
    probe = os.path.join(str(path), ".landlock-probe")
    try:
        with open(probe, "w") as handle:
            handle.write("x")
        os.unlink(probe)
        return True
    except OSError:
        return False


def test_the_kernel_struct_sizes_are_what_the_kernel_expects():
    """⚠ **The one thing that could plausibly differ by architecture**, and
    the reason it is asserted rather than assumed: `landlock_path_beneath_attr`
    is a PACKED u64 + s32. Unpacked it is 16 bytes on every LP64 platform and
    the kernel reads the fd out of the wrong bytes. This runs everywhere,
    including on x86_64 CI, and needs no Landlock at all."""
    assert ctypes.sizeof(landlock._PathBeneathAttr) == 12
    assert ctypes.sizeof(landlock._RulesetAttr) == 24
    assert (
        landlock.SYS_CREATE_RULESET,
        landlock.SYS_ADD_RULE,
        landlock.SYS_RESTRICT_SELF,
    ) == (
        444,
        445,
        446,
    )


@NO_LANDLOCK
class TestTheBoundaryHolds:
    """Project readable and writable, another project and `$HOME` denied —
    the same four checks the macOS profile is held to."""

    def test_four_of_four(self, tmp_path):
        project = tmp_path / "proj"
        other = tmp_path / "other"
        project.mkdir()
        other.mkdir()
        (project / "in.txt").write_text("inside")
        (other / "secret.txt").write_text("secret")

        def child():
            landlock.apply(_policy(writable=[project]))
            score = 0
            try:
                (project / "in.txt").read_text()
                score += 1
            except OSError:
                pass
            if _can_write(project):
                score += 1
            try:
                (other / "secret.txt").read_text()
            except OSError:
                score += 1
            try:
                os.listdir(os.path.expanduser("~"))
            except OSError:
                score += 1
            return score

        assert _in_child(child) == 4


@NO_LANDLOCK
class TestNesting:
    """⚠ **The question that bit this project on macOS.** There a sandboxed
    process could re-enter `sandbox-exec` only with a semantically equivalent
    profile; a NARROWER one failed too, and nothing documented it."""

    def test_an_identical_ruleset_can_be_reapplied(self, tmp_path):
        def child():
            landlock.apply(_policy(writable=[tmp_path]))
            landlock.apply(_policy(writable=[tmp_path]))
            return 0 if _can_write(tmp_path) else 1

        assert _in_child(child) == 0

    def test_a_narrower_ruleset_applies_and_takes_effect(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()

        def child():
            landlock.apply(_policy(writable=[tmp_path]))
            landlock.apply(_policy(writable=[sub]))
            # The subdirectory stays writable; its parent stops being.
            return 0 if (_can_write(sub) and not _can_write(tmp_path)) else 1

        assert _in_child(child) == 0

    def test_a_wider_ruleset_cannot_grant_back_a_denied_path(self, tmp_path):
        """The property confinement rests on: rulesets INTERSECT."""
        project = tmp_path / "proj"
        other = tmp_path / "other"
        project.mkdir()
        other.mkdir()

        def child():
            landlock.apply(_policy(writable=[project]))
            if _can_write(other):
                return 2  # the first boundary never held; nothing measured
            landlock.apply(_policy(writable=[project, other]))
            return 1 if _can_write(other) else 0

        assert _in_child(child) == 0

    def test_at_least_two_can_be_stacked(self, tmp_path):
        """A Manager boundary with a Worker boundary inside it needs two.
        The kernel's limit is 16; this asserts the floor rite depends on."""

        def child():
            applied = 0
            for _ in range(4):
                try:
                    landlock.apply(_policy(writable=[tmp_path]))
                    applied += 1
                except OSError:
                    break
            return applied

        assert _in_child(child) >= 2


# The child half of the exec test, kept as a readable script rather than a
# string built with % substitution: it runs INSIDE the parent's boundary, as a
# separately exec'd interpreter, and reports whether it inherited it.
_INNER = """
import os, sys
from rite_ai.managers import landlock

outside, sub = sys.argv[1], sys.argv[2]
try:
    open(os.path.join(outside, "x"), "w")
    inherited = False
except OSError:
    inherited = True

landlock.apply(
    {
        "readable": [p for p in ("/usr", "/lib", "/lib64", "/etc", "/proc", "/bin")
                     if os.path.exists(p)],
        "writable": [sub],
        "readonly_overrides": [],
    }
)
narrowed = os.access(sub, os.W_OK)
print("inherited=%s narrowed=%s" % (inherited, narrowed))
sys.exit(0 if inherited else 3)
"""


@NO_LANDLOCK
def test_the_boundary_survives_exec_and_the_child_can_narrow_it(tmp_path):
    """The shape rite actually uses: a confined Manager execs a Worker, and
    the Worker applies its own, narrower boundary."""
    import subprocess

    project = tmp_path / "proj"
    sub = project / "sub"
    sub.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    # ⚠ Inside the project, because the boundary denies reading anything else —
    # a script the confined process cannot open is not a measurement.
    inner = project / "inner.py"
    inner.write_text(_INNER)

    package_root = str(Path(rite_ai.__file__).resolve().parent.parent)

    def child():
        # ⚠ `_running_rite()` rather than a hand-written list. It names the
        # interpreter roots AND the checkout's `VERSION` file, which
        # `rite_ai.__init__` reads at import — an editable install cannot even
        # be imported without it. Measured here: granting only `src/` gave
        # `PermissionError: /work/repo/VERSION` inside the boundary, which is
        # the finding enclosure.py already records for the seatbelt profile,
        # reproduced on Linux.
        landlock.apply(
            _policy(
                readable=[
                    sys.prefix,
                    sys.base_prefix,
                    package_root,
                    *enclosure._running_rite(),
                ],
                writable=[project],
            )
        )
        done = subprocess.run(
            [sys.executable, str(inner), str(outside), str(sub)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "PYTHONPATH": package_root},
        )
        sys.stderr.write(done.stdout + done.stderr)
        return 0 if done.returncode == 0 else 4

    assert _in_child(child) == 0, (
        "the boundary did not survive exec, or the child could not narrow it"
    )


@NO_LANDLOCK
class TestTheTwoEscapes:
    """The two macOS shipped with. One is closed here and one is not, and the
    open one is asserted OPEN on purpose — if a later kernel closes it, this
    test says so instead of the hole quietly disappearing from the docs."""

    @NEEDS_SCOPING
    def test_signals_do_not_cross_the_boundary(self, tmp_path):
        victim = os.fork()
        if victim == 0:
            signal.pause()
            os._exit(0)
        control_target = os.fork()
        if control_target == 0:
            signal.pause()
            os._exit(0)
        try:

            def scoped_child():
                landlock.apply(_policy(writable=[tmp_path]))
                try:
                    os.kill(victim, signal.SIGTERM)
                except PermissionError:
                    return 0
                return 1

            # ⚠ THE CONTROL. Without it, "the target survived" could mean the
            # boundary worked or that nothing was ever signalled. It scopes
            # something OTHER than signals — an empty ruleset is rejected by
            # the kernel, and a control that fails to build looks like a
            # boundary that held.
            def control_child():
                attr = landlock._RulesetAttr(
                    landlock._handled(ABI), 0, landlock.SCOPE_ABSTRACT_UNIX_SOCKET
                )
                result, _ = landlock._syscall(
                    landlock.SYS_CREATE_RULESET,
                    ctypes.byref(attr),
                    ctypes.c_size_t(ctypes.sizeof(attr)),
                    ctypes.c_uint32(0),
                )
                if result < 0:
                    return 2
                libc = landlock._libc()
                libc.prctl(landlock.PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
                landlock._syscall(
                    landlock.SYS_RESTRICT_SELF, ctypes.c_int(result), ctypes.c_uint32(0)
                )
                os.close(result)
                try:
                    os.kill(control_target, signal.SIGTERM)
                except PermissionError:
                    return 1
                return 0

            assert _in_child(control_child) == 0, (
                "the control could not signal its own target, so this test "
                "cannot discriminate and the scoped result means nothing"
            )
            assert _in_child(scoped_child) == 0, (
                "LANDLOCK_SCOPE_SIGNAL did not refuse the signal"
            )
        finally:
            for pid in (victim, control_target):
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except OSError:
                    pass

    def test_a_unix_socket_in_a_denied_directory_is_still_reachable(self, tmp_path):
        """🔴 **THE HOLE THIS SHIPS WITH, asserted rather than described.**
        Landlock bounds opening files and does not govern `connect(2)`, so the
        macOS fix for the tmux escape — denying the socket's path — has no
        equivalent here. The same ruleset refuses to list or write that
        directory, which is what makes this a hole and not a missing grant."""
        project = tmp_path / "proj"
        project.mkdir()
        sockdir = tmp_path / "sockets"
        sockdir.mkdir()
        sockpath = sockdir / "helper.sock"

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sockpath))
        server.listen(1)
        try:

            def child():
                landlock.apply(_policy(writable=[project]))
                try:
                    os.listdir(str(sockdir))
                    return 5  # the directory was not denied; nothing measured
                except OSError:
                    pass
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.connect(str(sockpath))
                except OSError:
                    return 1  # refused — the hole would be CLOSED
                finally:
                    client.close()
                return 0  # connected THROUGH the deny

            outcome = _in_child(child)
            assert outcome != 5, "the directory was not denied, so nothing was measured"
            assert outcome == 0, (
                "connect() into a denied directory was refused — the socket "
                "escape appears CLOSED on this kernel. That is good news and "
                "it contradicts what rite documents in landlock.limitations(); "
                "update that before relaxing anything."
            )
        finally:
            server.close()
