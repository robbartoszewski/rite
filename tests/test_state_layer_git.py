"""The git backend against P2-1a's conformance suite, unchanged (P2-1b).

The suite is the point: the git backend and the local backend are held to the
same properties by the same code, so "the default backend is correct" means
the same thing as "the local backend is correct".

The remote here is a bare repository on disk. That is the same transport git
uses for a real remote minus the network — every object, ref update and lease
check is the real implementation, and it is what an offline `rite doctor`
probe can exercise too.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from rite_ai.coordination.git_backend import GitStateLayer
from rite_ai.coordination.state_layer import ABSENT, Unavailable, Written
from state_layer_conformance import StateLayerConformance


def _tip(remote) -> str:
    """The state branch's commit, named explicitly. Tests that need a git
    thing ask git for it — the layer's `version` is a value fingerprint and
    says nothing about commits."""
    return subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/state"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _git(*args, cwd=None):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
    )


class TestGitStateLayer(StateLayerConformance):
    # git's transport forks several processes per operation, so the same
    # wall-clock burst lands far fewer attempts than the local backend's.
    # The floors drop; the PROPERTIES do not.
    # Measured, not guessed: six contending writers sustain roughly two
    # successful state writes a second through git, because every push is a
    # serialised round-trip. The local backend does thousands. So the bursts
    # run longer here and the floors sit well under what was observed.
    BURST_SECONDS = 12.0
    MIN_CAS_ATTEMPTS = 60
    MIN_WRITES = 15
    MIN_MESSAGES = 20

    def new_store(self, tmp_path):
        remote = tmp_path / "remote.git"
        _git("init", "--bare", "-q", str(remote))
        (tmp_path / "caches").mkdir()
        return tmp_path

    def open_layer(self, store):
        return GitStateLayer(
            remote=str(store / "remote.git"),
            cache_dir=store / "caches" / uuid.uuid4().hex,
        )

    def actor_layer_spec(self, store, actor):
        return (
            "rite_ai.coordination.git_backend",
            "GitStateLayer",
            (str(store / "remote.git"), str(store / "caches" / f"actor-{actor}")),
        )

    def corrupt_state(self, store):
        self._corrupt_branch(store, "state")

    def corrupt_messages(self, store):
        self._corrupt_branch(store, "main")

    def _corrupt_branch(self, store, branch):
        """Damage the commit object the branch points at — the real fault
        (a half-written object, a bad disk), not a fabricated one: git itself
        refuses to point a ref at an object that is missing. A reader then
        cannot know what the state is, which is exactly `Unavailable`."""
        remote = store / "remote.git"
        oid = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        damaged = 0
        for repo in [remote, *(store / "caches").iterdir()]:
            loose = repo / "objects" / oid[:2] / oid[2:]
            if loose.exists():
                loose.chmod(0o644)  # git writes objects read-only
                loose.write_bytes(b"not a git object")
                damaged += 1
        assert damaged, f"{branch} tip is not a loose object anywhere"


class TestGitSpecifics:
    """Behaviours the shared suite cannot see from the outside."""

    @pytest.fixture
    def remote(self, tmp_path):
        r = tmp_path / "remote.git"
        _git("init", "--bare", "-q", str(r))
        return r

    @pytest.fixture
    def layer(self, tmp_path, remote):
        return GitStateLayer(str(remote), tmp_path / "cache")

    def test_identical_writes_still_produce_distinct_commits(self, tmp_path, remote):
        """The nonce, at the level it actually guards.

        A version is a fingerprint of the VALUE now, so two writes of the
        same bytes SHARE a version by design — that is what makes A -> B -> A
        harmless. The commit is different: it is what `--force-with-lease`
        compares, and a parentless commit with a fixed author and the same
        tree would otherwise repeat, letting a stale lease succeed. So the
        commits must differ even when the values do not."""
        layer = GitStateLayer(str(remote), tmp_path / "c1")
        first = layer.write_state("owner-lease.json", b"same", ABSENT)
        assert isinstance(first, Written)
        before = _tip(remote)
        second = layer.write_state("owner-lease.json", b"same", first.version)
        assert isinstance(second, Written)
        assert second.version == first.version, "same bytes, so same version"
        assert _tip(remote) != before, "identical writes produced one commit (ABA)"

    def test_state_is_always_exactly_one_commit(self, layer, remote):
        """§3.3.1. The state branch must not accumulate history — it is a
        snapshot, and a repository that grows without bound is an operational
        bug that only shows up months in."""
        version = ABSENT
        for i in range(5):
            result = layer.write_state("k.json", str(i).encode(), version)
            assert isinstance(result, Written)
            version = result.version
        log = subprocess.run(
            ["git", "--git-dir", str(layer.cache), "rev-list", "--count", _tip(remote)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert log.stdout.strip() == "1"

    def test_a_push_preserves_every_other_managers_file(self, tmp_path, remote):
        """§2.4.2's own warning, stated at the level it happens (P2-1c).

        A force-push REPLACES the tree. A Manager that pushes a tree built
        from only its own file does not fail — it silently deletes every
        other Manager's state, and the next read looks like a clean start.
        So the assertion is on the commit's tree, not on what one reader
        happens to see: everything that was there must still be there."""
        managers = [GitStateLayer(str(remote), tmp_path / f"c{i}") for i in range(4)]
        for i, manager in enumerate(managers):
            # Each Manager writes only its OWN key, and expects only its own
            # key's prior state — none of them has read the others'.
            result = manager.write_state(
                f"managers/m{i}.json", f"m{i}".encode(), ABSENT
            )
            assert isinstance(result, Written), result

        listed = subprocess.run(
            [
                "git",
                "--git-dir",
                str(remote),
                "ls-tree",
                "-r",
                "--name-only",
                _tip(remote),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert sorted(listed) == [f"managers/m{i}.json" for i in range(4)], listed

    def test_an_unreachable_remote_is_unavailable_not_absent(self, tmp_path):
        """The distinction the whole design rests on: a remote we cannot read
        says NOTHING about whether an Owner exists. Reporting Absent here
        would elect a second Owner every time the network blips."""
        layer = GitStateLayer(str(tmp_path / "nope.git"), tmp_path / "cache")
        assert isinstance(layer.read_state("owner-lease.json"), Unavailable)
        assert isinstance(layer.read_messages(), Unavailable)
        assert isinstance(
            layer.write_state("owner-lease.json", b"x", ABSENT), Unavailable
        )

    def test_a_damaged_CACHE_is_unavailable_not_absent(self, tmp_path, remote):
        """The cache is a copy, and a copy can rot on its own. When the
        remote is healthy but OUR object is damaged, answering "absent"
        would tell a Manager there is no Owner because of a bad byte on its
        own disk — and it would elect itself.

        Which layer catches it is git's business (in practice the fetch
        does, before the ref is ever resolved); what matters is that no
        layer answers "absent"."""
        layer = GitStateLayer(str(remote), tmp_path / "cache")
        written = layer.write_state("owner-lease.json", b"held", ABSENT)
        assert isinstance(written, Written)
        loose = layer.cache / "objects" / written.version[:2] / written.version[2:]
        loose.chmod(0o644)
        loose.write_bytes(b"not a git object")
        assert isinstance(layer.read_state("owner-lease.json"), Unavailable)

    def test_a_write_never_touches_a_working_tree(self, tmp_path, remote):
        """No checkout is involved, so a write cannot disturb one or be
        disturbed by one. The cache is bare and stays bare."""
        layer = GitStateLayer(str(remote), tmp_path / "cache")
        assert isinstance(layer.write_state("k.json", b"v", ABSENT), Written)
        assert not (layer.cache / "index").exists()
        bare = subprocess.run(
            ["git", "--git-dir", str(layer.cache), "config", "core.bare"],
            capture_output=True,
            text=True,
        )
        assert bare.stdout.strip() == "true"


class TestLockContentionThatOutlastsTheInnerRetry:
    """Contention exhausting `_LOCK_ATTEMPTS` is not a refusal, and not a
    write that may have landed.

    git updates a ref by CREATING `<ref>.lock` and failing if it exists — a
    non-blocking lock, unlike the local backend's `flock` and the key-value
    store's mutex, which WAIT. So "somebody else held the ref for a moment"
    is an outcome only this backend has, and the one every writer against a
    shared remote meets under load. `_push_retrying_lock_contention` already
    retries it; the defect is where it goes when those retries run out.

    It returned `("refused", ...)`, and `refused` is the branch for a
    protected branch or a declined hook — permanent, not a race. So a burst
    that lost eight lock races in a row surfaced as

        Unavailable: the remote refused the write ... `rite doctor` probes
        force-push permission

    which is wrong twice over. `Unavailable` means the write MAY have landed;
    a push that never took the lock definitely did not. And it sends the
    reader to check permissions for a condition that is transient contention.

    Under the Owner lease that is the expensive shape: a renewal reported as
    maybe-landed cannot be safely retried, so an Owner that is merely
    contended fails closed and the fleet churns — under exactly the load
    coordination exists for.
    """

    def _layer_that_loses_locks(self, tmp_path, losses: int):
        from rite_ai.coordination import git_backend

        remote = tmp_path / "remote.git"
        _git("init", "--bare", "-q", str(remote))
        layer = GitStateLayer(remote=str(remote), cache_dir=tmp_path / "cache")
        real = layer._push
        state = {"left": losses}

        def flaky(refspec, branch, expected):
            if state["left"] > 0:
                state["left"] -= 1
                return "contended", "cannot lock ref 'refs/heads/state'"
            return real(refspec, branch, expected)

        layer._push = flaky
        return layer, git_backend

    def test_contention_past_the_bound_is_not_reported_as_a_refusal(self, tmp_path):
        """THE DEFECT. One more loss than the inner retry absorbs."""
        from rite_ai.coordination.git_backend import _LOCK_ATTEMPTS

        layer, _ = self._layer_that_loses_locks(tmp_path, _LOCK_ATTEMPTS + 1)

        result = layer.write_state("owner-lease.json", b"mine", ABSENT)

        assert isinstance(result, Written), (
            f"lock contention that outlasts the inner retry came back as "
            f"{type(result).__name__}: {getattr(result, 'reason', '')}"
        )

    def test_it_does_not_send_the_reader_to_check_permissions(self, tmp_path):
        """The diagnosis matters as much as the outcome: someone reading
        this at 3am is told to go and look at force-push rights."""
        from rite_ai.coordination.git_backend import _LOCK_ATTEMPTS

        layer, _ = self._layer_that_loses_locks(tmp_path, _LOCK_ATTEMPTS + 1)

        result = layer.write_state("owner-lease.json", b"mine", ABSENT)

        assert "force-push permission" not in getattr(result, "reason", "")

    def test_relentless_contention_still_terminates_and_says_so(self, tmp_path):
        """The bound must still exist. A writer that can never take the lock
        is told so — and told it is contention, not permissions, and not a
        write that might have landed."""
        layer, _ = self._layer_that_loses_locks(tmp_path, 10_000)

        result = layer.write_state("owner-lease.json", b"mine", ABSENT)

        assert isinstance(result, Unavailable), result
        assert "lock" in result.reason or "contention" in result.reason
        assert "may or may not have landed" not in result.reason

    def test_a_genuine_race_is_still_a_conflict(self, tmp_path):
        """The safety property this must not trade away. `--force-with-lease`
        guards every retry, so if somebody really did win in between, the
        retry comes back stale and the caller is told Conflict — never
        Written."""
        layer, _ = self._layer_that_loses_locks(tmp_path, 0)
        first = layer.write_state("owner-lease.json", b"first", ABSENT)
        assert isinstance(first, Written)

        stale = layer.write_state("owner-lease.json", b"second", ABSENT)

        assert not isinstance(stale, Written), stale


class TestTheRemotesOwnRejectionWording:
    """A lost race the REMOTE rejected, in the words newer git uses.

    This is what the Linux CI failure actually was, and it took adding the
    reason to the conformance assertion to see it: 11 `Unavailable`, every
    one reading

        the remote refused the write: ! <sha>:refs/heads/state
        [remote rejected] (incorrect old value)

    or `(reference already exists)`. Both are the receiving end's ref
    transaction saying the ref was not what the update expected — somebody
    else won — and neither matched "stale info" or "non-fast-forward", so
    both fell through to `refused`: "a protected branch or a declined hook:
    permanent, not a race."

    macOS git 2.50.1 reports the same two conditions as `[rejected] (stale
    info)`, verified by provoking both against a bare repo on disk, which
    is why this never appeared on a developer's machine and why the suite
    was green here and red there.
    """

    @pytest.mark.parametrize(
        "wording",
        ["incorrect old value", "reference already exists"],
        ids=["incorrect-old-value", "reference-already-exists"],
    )
    def test_it_is_a_conflict_not_a_refusal(self, tmp_path, wording):
        """THE DEFECT, per wording. Classified through `_push`'s own
        parser, from the porcelain line git really prints."""
        from rite_ai.coordination.git_backend import GitStateLayer as G

        remote = tmp_path / "remote.git"
        _git("init", "--bare", "-q", str(remote))
        layer = G(remote=str(remote), cache_dir=tmp_path / "cache")

        class Proc:
            returncode = 1
            stdout = (
                f"!\t0123456789abcdef:refs/heads/state\t[remote rejected] ({wording})\n"
            ).encode()
            stderr = b""

        layer._git = lambda *a, **k: Proc()

        outcome, _ = layer._push("x:refs/heads/state", "state", "")

        assert outcome == "conflict", (
            f"a lost race the remote reported as {wording!r} came back as "
            f"{outcome!r} — `refused` means permanent, and this is a race"
        )

    def test_a_real_refusal_is_still_a_refusal(self, tmp_path):
        """The half that must survive: a declined hook or a protected
        branch is permanent, and telling the caller to retry for ever would
        be worse than the bug this fixes."""
        from rite_ai.coordination.git_backend import GitStateLayer as G

        remote = tmp_path / "remote.git"
        _git("init", "--bare", "-q", str(remote))
        layer = G(remote=str(remote), cache_dir=tmp_path / "cache")

        class Proc:
            returncode = 1
            stdout = (
                b"!\t0123456789abcdef:refs/heads/state\t"
                b"[remote rejected] (pre-receive hook declined)\n"
            )
            stderr = b""

        layer._git = lambda *a, **k: Proc()

        outcome, _ = layer._push("x:refs/heads/state", "state", "")

        assert outcome == "refused", outcome
