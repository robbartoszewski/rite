"""The derived cache does not grow for ever (§3.3.1's cost).

Every state write force-pushes a new parentless commit and orphans the last
one, so the cache fills with garbage: measured over 30 ticks of a
two-machine fleet, 332 objects of which 8 were reachable — about 44KB a
cycle with no plateau, which is ~12MB a day at a tick every five minutes, in
a directory nobody looks at.

Git's own `gc --auto` does NOT bound it, and that was measured rather than
assumed: it packs the loose objects, the loose count falls back under the
threshold, and it stops firing while the garbage sits in packfiles. So the
trigger is explicit, and the prune is immediate — safe only because this
cache is derived and anything pruned can be fetched again.
"""

from __future__ import annotations

import subprocess

import pytest

from rite_ai.coordination import git_backend
from rite_ai.coordination.git_backend import GitStateLayer
from rite_ai.coordination.state_layer import ABSENT, Present, Written


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    return str(path)


def loose_objects(layer) -> int:
    out = subprocess.run(
        ["git", "--git-dir", str(layer.cache), "count-objects", "-v"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    counts = [line for line in out.splitlines() if line.startswith("count:")]
    return int(counts[0].split()[1])


def write_many(layer, how_many: int, key: str = "owner-lease.json"):
    version = ABSENT
    for i in range(how_many):
        result = layer.write_state(key, f"state-{i}".encode(), version)
        assert isinstance(result, Written), result
        version = result.version
    return version


def test_a_fresh_cache_expires_unreachable_objects_immediately(tmp_path, remote):
    """Two weeks of retention is right for a repository holding the only
    copy of something. This holds no copy of anything."""
    layer = GitStateLayer(remote, tmp_path / "cache")
    layer.write_state("k.json", b"v", ABSENT)
    setting = subprocess.run(
        ["git", "--git-dir", str(layer.cache), "config", "gc.pruneExpire"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert setting == "now"


def test_the_cache_is_collected_once_it_passes_the_threshold(
    tmp_path, remote, monkeypatch
):
    """The property the measurement is about: growth stops. Thresholds are
    lowered so this runs in a second rather than reproducing the soak."""
    monkeypatch.setattr(git_backend, "_LOOSE_LIMIT", 12)
    layer = GitStateLayer(remote, tmp_path / "cache")

    write_many(layer, 6)
    before = loose_objects(layer)
    write_many(layer, 12, key="managers/a.json")
    after = loose_objects(layer)

    assert before > 0
    assert after < before + 12, (
        f"objects grew unchecked: {before} -> {after}; nothing collected"
    )


def test_the_current_state_survives_collection(tmp_path, remote, monkeypatch):
    """Pruning immediately is only safe if what is IN USE is reachable. If
    this ever fails, the cache is deleting live state, not garbage."""
    monkeypatch.setattr(git_backend, "_LOOSE_LIMIT", 4)
    layer = GitStateLayer(remote, tmp_path / "cache")
    version = write_many(layer, 10, key="owner-lease.json")

    read = layer.read_state("owner-lease.json")
    assert isinstance(read, Present)
    assert read.value == b"state-9"
    assert read.version == version


def test_other_keys_survive_collection_too(tmp_path, remote, monkeypatch):
    """The whole tree is live, not just the key being written."""
    monkeypatch.setattr(git_backend, "_LOOSE_LIMIT", 4)
    layer = GitStateLayer(remote, tmp_path / "cache")
    layer.write_state("claims.json", b"{}", ABSENT)
    write_many(layer, 10, key="owner-lease.json")

    assert layer.read_state("claims.json") == Present(
        b"{}", layer.read_state("claims.json").version
    )


def test_collection_failing_does_not_fail_the_write(tmp_path, remote, monkeypatch):
    """Housekeeping is not the caller's business: a write that LANDED must
    be reported as landed even if the tidying afterwards went wrong.

    The first version of this test asserted the opposite of its own
    docstring — it expected the exception to escape, and the code obliged.
    A successful write surfacing as a crash is the same confusion §2.4.2
    step 5 exists to prevent: the caller cannot tell what happened.
    """
    layer = GitStateLayer(remote, tmp_path / "cache")
    calls = {"n": 0}

    def explode(self):
        calls["n"] += 1
        raise subprocess.SubprocessError("gc exploded")

    monkeypatch.setattr(git_backend.GitStateLayer, "_collect_garbage", explode)
    result = layer.write_state("k.json", b"v", ABSENT)
    assert isinstance(result, Written), result
    assert calls["n"] == 1, "housekeeping did not run at all"
    assert layer.read_state("k.json") == Present(b"v", result.version)
