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


def without_wholesale_temp_grants(policy):
    """A policy with `/tmp` and `/var/tmp` removed from the writable set.

    ⚠ **THIS IS A REAL HOLE, NOT A TEST CONVENIENCE, and it is wider than the
    inbox.** `compose_policy` grants those two read+write to mirror seatbelt.
    Seatbelt can place a deny AFTER a grant and win; Landlock takes the UNION
    of its grants and has no deny, so a wholesale temp grant cannot be carved
    and it overrides everything narrower.

    Measured 2026-09-26: under a pytest `tmp_path`, which lives in `/tmp`, it
    defeated the MM-2 inbox fence (no longer: the inbox left the tree, see
    `test_a_project_under_tmp_no_longer_exposes_an_inbox`) AND the credential
    narrowing — a third file in
    the credential directory became readable, the Claude login became
    overwritable, and `run.lock` became holdable. A real install is unaffected
    because both the project and `_credential_root` sit under `$HOME`; what is
    exposed is scratch projects.

    So every test of a NARROW property has to drop these grants, or it measures
    the hole instead of the property. `fix/landlock-no-wholesale-temp` removes
    them; this helper goes when that lands.
    """
    temps = {"/tmp", "/var/tmp"}
    return {**policy, "writable": [w for w in policy["writable"] if w not in temps]}


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
    assert landlock.PATH_BENEATH_SIZE == 12, (
        "landlock_path_beneath_attr must be 12 packed bytes; a padded 16 makes "
        "the kernel read the fd out of the wrong bytes"
    )
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


@NO_LANDLOCK
class TestTheInboxFenceOnLinux:
    """MM-2 on Linux, against the kernel rather than against the policy text.

    ⚠ **`test_no_manager_writes_an_inbox.py` is wholly `darwin`-gated**, so
    until this existed the inbox fence had no Linux test at all — on the
    platform where a rule names an INODE and granting a symlink grants its
    target. Both defects below were real and were found by review, not by a
    test; nothing would have caught their return.

    ⚠ Landlock has no deny rule: rules only GRANT and the effective access is
    the UNION, so the fence is an ENUMERATION of siblings rather than a deny
    placed last. That is why a symlink matters here and does not on macOS,
    where seatbelt checks the resolved path when the file is opened.
    """

    def _project(self, tmp_path, *, symlink_in_root=False):
        from rite_ai.managers.mailbox import (
            INBOX,
            OUTBOX,
            mailbox_dir,
        )
        from rite_ai.managers.mailbox import (
            _legacy_mail_root as legacy_mail_root,
        )

        project = tmp_path / "proj"
        (project / "src").mkdir(parents=True)
        for name in ("lead", "helper"):
            mailbox_dir(project, name, INBOX).mkdir(parents=True)
            mailbox_dir(project, name, OUTBOX).mkdir(parents=True)
            # A pre-0.6.0 project: the old in-tree boxes, still read.
            (legacy_mail_root(project, name) / INBOX).mkdir(parents=True)
            (legacy_mail_root(project, name) / OUTBOX).mkdir(parents=True)
        (project / ".rite" / "user").mkdir(exist_ok=True)
        if symlink_in_root:
            # The bypass: a symlink in the project root aimed at a fenced inbox.
            (project / "shortcut").symlink_to(mailbox_dir(project, "helper", INBOX))
        return project

    @staticmethod
    def _without_the_wholesale_temp_grants(policy):
        # ⚠ Also what makes the NEW inbox a real measurement: the suite's rite
        # home is under `/tmp`, which the policy grants wholesale. Without
        # this the inbox would be writable through that grant and the test
        # would prove nothing about the fence.
        return without_wholesale_temp_grants(policy)

    def _refused(self, policy, target) -> bool:
        def child():
            landlock.apply(policy)
            try:
                with open(os.path.join(str(target), ".probe"), "w") as handle:
                    handle.write("x")
                os.unlink(os.path.join(str(target), ".probe"))
                return 1  # allowed
            except OSError:
                return 0  # refused
            except Exception:  # noqa: BLE001
                return 2

        return _in_child(child) == 0

    def test_no_manager_writes_an_inbox_its_own_included(self, tmp_path):
        from rite_ai.managers.mailbox import INBOX, mailbox_dir

        project = self._project(tmp_path)
        policy = self._without_the_wholesale_temp_grants(
            landlock.compose_policy(project, "lead", tmp_path / "home")
        )

        assert self._refused(policy, mailbox_dir(project, "helper", INBOX)), (
            "another Manager's inbox is writable — an inbox write IS an "
            "instruction, so this is authority, not tidiness"
        )
        assert self._refused(policy, mailbox_dir(project, "lead", INBOX)), (
            "a Manager can write its OWN inbox, which promotes its own text to "
            "the Owner's"
        )

    def test_the_fence_does_not_break_the_manager(self, tmp_path):
        """The other half: a fence that stopped a Manager working would be
        traded for the wrong thing. `rite reply` writes the outbox from INSIDE
        the boundary."""
        from rite_ai.managers.mailbox import OUTBOX, mailbox_dir

        project = self._project(tmp_path)
        policy = self._without_the_wholesale_temp_grants(
            landlock.compose_policy(project, "lead", tmp_path / "home")
        )

        assert not self._refused(policy, mailbox_dir(project, "lead", OUTBOX)), (
            "the Manager cannot write its own outbox, so it cannot reply"
        )
        assert not self._refused(policy, project / "src"), (
            "the Manager cannot write its own project source"
        )

    def test_a_symlink_in_the_project_root_does_not_hand_the_fence_back(self, tmp_path):
        """🔴 **THE DEFECT THIS PINS.** A Landlock rule names the inode a path
        resolves to. Measured in review: granting ONLY a symlink that pointed
        at another Manager's `mail/in` made that inbox writable both through
        the link and directly — so one symlink anywhere in the project root
        would have defeated MM-2 entirely. Symlinked entries are skipped, which
        fails closed."""
        from rite_ai.managers.mailbox import INBOX, mailbox_dir

        project = self._project(tmp_path, symlink_in_root=True)
        raw = landlock.compose_policy(project, "lead", tmp_path / "home")
        policy = self._without_the_wholesale_temp_grants(raw)

        assert str(project / "shortcut") not in raw["writable"], (
            "a symlink in the project root was granted; it names the inode of "
            "the inbox it points at"
        )
        # And the fence still holds through both routes.
        assert self._refused(policy, project / "shortcut")
        assert self._refused(policy, mailbox_dir(project, "helper", INBOX))


@NO_LANDLOCK
def test_a_project_under_tmp_no_longer_exposes_an_inbox(tmp_path, monkeypatch):
    """The wholesale `/tmp` grant stays, and MM-2 no longer depends on it.

    ⚠ **THIS USED TO ASSERT THE OPPOSITE**, as a known hole: `compose_policy`
    grants `/tmp` and `/var/tmp` read+write, Landlock has no deny, so for a
    project under them — rite's own pool worktrees, every test project — the
    in-tree inbox was writable through that grant. The inbox has left the
    tree, and the old in-tree box is moved once and never read again, so the
    grant now reaches nothing that is delivered.

    Measured with the REAL policy, temp grants included, and rite's home
    somewhere they do not cover — as it is in a real install. The control
    shows the grant is still live: the project, under `/tmp`, is writable.
    """
    import shutil
    import uuid

    from rite_ai.managers.mailbox import INBOX, OUTBOX, mailbox_dir

    home = Path.home() / f".rite-landlock-test-{uuid.uuid4().hex[:8]}"
    if str(home.resolve()).startswith(("/tmp", "/var/tmp")):
        pytest.skip(f"HOME is under a temp grant ({home}), so nothing is measured")
    monkeypatch.setenv("RITE_HOME_DIR", str(home))
    try:
        project = tmp_path / "proj"
        (project / "src").mkdir(parents=True)
        for name in ("lead", "helper"):
            mailbox_dir(project, name, INBOX).mkdir(parents=True)
            mailbox_dir(project, name, OUTBOX).mkdir(parents=True)
        policy = landlock.compose_policy(project, "lead", tmp_path / "home")
        assert any(w in ("/tmp", "/var/tmp") for w in policy["writable"]), (
            "the temp grants are gone — this test's control no longer holds"
        )
        assert str(tmp_path).startswith(("/tmp", "/var/tmp")), (
            f"tmp_path is {tmp_path}, not under a granted tree: nothing measured"
        )

        def writes(target):
            def child():
                landlock.apply(policy)
                try:
                    (Path(target) / ".probe").write_text("x")
                    return 0
                except OSError:
                    return 1

            return _in_child(child) == 0

        assert writes(project / "src"), "control: the /tmp grant is not live"
        assert not writes(mailbox_dir(project, "helper", INBOX))
        assert not writes(mailbox_dir(project, "lead", INBOX))
        assert writes(mailbox_dir(project, "lead", OUTBOX)), "cannot reply"
    finally:
        shutil.rmtree(home, ignore_errors=True)


class TestP2BetweenTwoManagersSharingARoot:
    """§5.4.8's P2 between TWO MANAGERS, the Linux half.

    ⚠ **`TestTheTwoEscapes` measures the boundary against a BYSTANDER.** Its
    victim is an unconfined process the test forked, so it answers "can a
    Manager signal something outside its boundary". That is not the property
    §5.4.8 states, which is about Manager A and Manager B — and a change that
    separated a Manager from the operator while letting two Managers reach
    each other would pass it unchanged. Here the victim is inside Manager B's
    OWN ruleset, which is a second Landlock domain.

    The macOS half of this property is
    `test_the_manager_profile_denies_what_it_should.py`'s class of the same
    name. It covers signals AND tmux; this one covers signals only, because
    the tmux escape is OPEN on Linux — Landlock bounds opening files and does
    not govern `connect(2)`, which is what a control socket is. That hole is
    asserted open in `TestTheTwoEscapes`, so it is stated once rather than
    twice.
    """

    @staticmethod
    def _running(pid: int) -> bool:
        """⚠ **`kill(pid, 0)` is not liveness — it succeeds for a ZOMBIE.**
        Found while mutation-testing the macOS half: with the signal grant
        widened, the victim was killed and every "it survived" assertion
        still passed, because the dead process answered until it was reaped.
        `/proc` reports the state instead.
        """
        try:
            with open(f"/proc/{pid}/stat") as handle:
                return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False

    @NEEDS_SCOPING
    def test_manager_a_cannot_signal_manager_bs_process(self, tmp_path):
        project = tmp_path / "project"
        alpha, beta = project / "alpha", project / "beta"
        for directory in (project, alpha, beta):
            directory.mkdir()
        ready_read, ready_write = os.pipe()
        victim = os.fork()
        if victim == 0:
            os.close(ready_read)
            landlock.apply(_policy(writable=[beta]))
            os.write(ready_write, b"1")
            os.close(ready_write)
            signal.pause()
            os._exit(0)
        os.close(ready_write)
        try:
            # ⚠ Wait until B is INSIDE its own ruleset. Without the handshake
            # this could pass while B was still unconfined — which is the
            # weaker claim `TestTheTwoEscapes` already makes, dressed up as
            # this one.
            assert os.read(ready_read, 1) == b"1", "Manager B never confined itself"
            assert self._running(victim), "Manager B died before it was signalled"

            def manager_a(beta_pid=victim):
                landlock.apply(_policy(writable=[alpha]))
                try:
                    os.kill(beta_pid, signal.SIGTERM)
                except PermissionError:
                    return 0
                return 1

            assert _in_child(manager_a) == 0, (
                "Manager A signalled Manager B's process across two Landlock "
                "domains — P2 does not hold on this kernel"
            )
            assert self._running(victim), "Manager B's process was killed by A"
        finally:
            os.close(ready_read)
            os.kill(victim, signal.SIGKILL)
            os.waitpid(victim, 0)

    @NEEDS_SCOPING
    def test_and_manager_a_can_still_signal_its_OWN_child(self, tmp_path):
        """Without this the test above would pass on a boundary that forbade
        signalling altogether, which would break every Manager: one that
        cannot stop a build it started is a Manager with a new problem."""

        def manager_a():
            landlock.apply(_policy(writable=[tmp_path]))
            mine = os.fork()
            if mine == 0:
                signal.pause()
                os._exit(0)
            try:
                os.kill(mine, signal.SIGTERM)
            except PermissionError:
                return 1
            os.waitpid(mine, 0)
            return 0

        assert _in_child(manager_a) == 0


@NO_LANDLOCK
class TestTheManagersCredentialsInsideTheBoundary:
    """The Linux mirror of two macOS tests, against the KERNEL rather than the
    policy: `test_inside_the_profile_ONLY_the_two_gh_files_are_readable` and
    `test_inside_the_profile_the_login_can_be_read_and_not_replaced`.

    ⚠ **The policy-level versions in
    `test_the_sandbox_backend_is_chosen_by_platform.py` assert what
    `compose_policy` RETURNS.** That is not the same claim: a correct list and
    a ruleset that does not enforce it look identical from there. These apply
    the ruleset and try the operations, which is what the macOS pair do inside
    a real profile.

    ⚠ Landlock has no deny rule, so where seatbelt denies the login write LAST,
    here `claude/` is not granted as a tree at all and its children are granted
    individually. The properties are the same; the mechanism is not.
    """

    def _laid_out(self, tmp_path):
        from rite_ai.managers import github_access

        root = tmp_path / "proj"
        (root / "src").mkdir(parents=True)
        (root / ".rite" / "user").mkdir(parents=True)
        (root / "decoy").write_text("d")
        home = tmp_path / "home"
        home.mkdir()
        cdir = github_access._credential_dir(root, "lead", home)
        (cdir / "gh").mkdir(parents=True)
        (cdir / "claude" / "projects").mkdir(parents=True)
        (cdir / "gh" / "hosts.yml").write_text("h")
        (cdir / "gh" / "config.yml").write_text("c")
        (cdir / "gh" / "other.yml").write_text("x")
        (cdir / "claude" / ".credentials.json").write_text('{"t": "SENTINEL"}')
        # The run lock `rite start` takes before touching any credential.
        (cdir / "run.lock").write_text("")
        return root, home, cdir

    @staticmethod
    def _inside(policy, fn) -> int:
        # ⚠ The credential directory lands under `/tmp` in a test, and the
        # wholesale temp grant would override every narrow grant here — see
        # `without_wholesale_temp_grants`. Measured: without this, a third file
        # is readable and the login is overwritable.
        def child():
            landlock.apply(without_wholesale_temp_grants(policy))
            return fn()

        return _in_child(child)

    def test_only_the_two_gh_files_are_readable(self, tmp_path):
        root, home, cdir = self._laid_out(tmp_path)
        policy = landlock.compose_policy(root, "lead", home)

        def attempt():
            def readable(path):
                try:
                    open(path).read()
                    return True
                except OSError:
                    return False

            wanted = readable(cdir / "gh" / "hosts.yml") and readable(
                cdir / "gh" / "config.yml"
            )
            third = readable(cdir / "gh" / "other.yml")
            return 0 if (wanted and not third) else (1 if not wanted else 2)

        outcome = self._inside(policy, attempt)
        assert outcome != 1, (
            "gh cannot read its own token, so it cannot reach the board"
        )
        assert outcome == 0, (
            "a third file in the credential directory is readable — the "
            "narrowing to two exact paths is not being enforced"
        )

    def test_the_login_is_readable_and_claude_can_write_its_state(self, tmp_path):
        """⚠ **DIVERGES FROM macOS DELIBERATELY** — see
        `test_claude_gets_its_config_directory_as_a_tree` for the measurement
        that forced it. Seatbelt denies write on the login last; Landlock has
        no deny, and granting only the existing children made Claude Code write
        nothing at all and the Manager unable to resume.

        What must still hold: the login is READABLE, so Claude can sign in, and
        the rest of the credential directory stays unwritable so a Manager
        cannot replace the gh token written for it from outside.
        """
        root, home, cdir = self._laid_out(tmp_path)
        policy = landlock.compose_policy(root, "lead", home)
        login = cdir / "claude" / ".credentials.json"

        def attempt():
            try:
                open(login).read()
            except OSError:
                return 1  # Claude cannot sign in
            try:
                # The session state Claude writes on every run.
                (cdir / "claude" / "sessions").mkdir(exist_ok=True)
                (cdir / "claude" / ".claude.json").write_text("{}")
            except OSError:
                return 2  # it cannot write its state, so it cannot resume
            try:
                (cdir / "planted").write_text("x")
                return 3  # the credential directory is writable
            except OSError:
                return 0

        outcome = self._inside(policy, attempt)
        assert outcome != 1, "the login is unreadable, so Claude cannot sign in"
        assert outcome != 2, (
            "Claude cannot create its session state, which is what made a "
            "Linux Manager silently unable to resume"
        )
        assert outcome != 3, "the credential directory itself is writable"
        assert outcome == 0

    def test_the_run_lock_cannot_be_held_by_the_manager(self, tmp_path):
        """🔴 **A SELF-INFLICTED DENIAL OF SERVICE IF IT COULD.** `rite start`
        takes `run.lock` in the credential directory before touching any
        credential, so a Manager able to open and hold it would make its own
        next start refuse — presenting as "rite randomly will not start my
        Manager". Raised against the pre-narrowing state, where the whole
        credential directory was readable. Measured after narrowing: the file
        cannot be opened at all, because the directory is not granted."""
        root, home, cdir = self._laid_out(tmp_path)
        policy = landlock.compose_policy(root, "lead", home)

        def attempt():
            import fcntl

            try:
                handle = os.open(str(cdir / "run.lock"), os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                return 0  # cannot even open it
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return 2  # HELD — the next start would refuse
            except OSError:
                return 1  # opened but not lockable
            finally:
                os.close(handle)

        outcome = self._inside(policy, attempt)
        assert outcome == 0, (
            "a confined Manager can open its own run lock; if it holds it, its "
            "next start refuses and reads as rite being broken"
        )
