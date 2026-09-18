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
