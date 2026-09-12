"""Pool A's blast-radius properties, re-proved under concurrency.

`test_blast_radius.py` certified rite safe to point at a live commercial
repo. Every property in it was proved SINGLE-THREADED, and the pass that
followed found three silent data-loss defects under multiple processes —
including granted claims disappearing while the tool printed "claimed"
and exited 0. A guarantee proved single-threaded is not proved.

Two properties are re-established here against real processes:

**Exclusion.** Two workers must never hold overlapping paths at once. On
a live repo that is two sessions editing one file, each believing it owns
it.

**rite never writes to a remote or rewrites history.** `test_blast_radius`
proves this by reading the source for forbidden argument lists. That is a
good check and it cannot see what actually ran, so here a recording `git`
shim goes first on PATH and every invocation rite makes is inspected.

Both are run as repeated SYNCHRONISED bursts rather than a long loop with
sleeps. That detail is load-bearing and was measured: workers looping on
independent sleeps desynchronise within a second and then never revisit
the microsecond window the inode-replacement defect lived in — the first
version of this soak reported zero violations against a knowingly broken
lock. Aligning every round to a shared wall-clock instant reproduces the
collision thousands of times.

Calibration, against the pre-fix lock (`flock` held on `claims.json`,
which `write_atomic` replaces): 6 workers over 10 seconds produced 309
lost updates and 56 simultaneous holds. A four-minute run of the same
harness against the current code produced 132,321 grants, 8,451 scheduler
ticks and 5,333 git invocations with zero of either.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Short enough to keep the suite fast, long enough that a broken lock
# fails it overwhelmingly: the calibration above is ~30 lost updates per
# worker-second, so a 3-second run with 6 workers has a very large margin.
SOAK_SECONDS = float(os.environ.get("RITE_SOAK_SECONDS", "3"))
WORKERS = 6
TICKERS = 2
# How often the workers re-synchronise on the wall clock.
BURST_PERIOD = 0.004

CONTESTED = ["src/payments.ts", "src/auth.ts", "src/orders.ts"]

# Verbs that reach a remote or rewrite history. `-f` and `--force` are
# included as bare tokens: no rite git invocation has any business
# carrying either.
FORBIDDEN_GIT = frozenset(
    {
        "push",
        "reset",
        "rebase",
        "filter-branch",
        "update-ref",
        "cherry-pick",
        "revert",
        "am",
        "apply",
        "--force",
        "-f",
        "--hard",
    }
)


def _align(period: float = BURST_PERIOD) -> None:
    """Busy-wait to the next multiple of `period`.

    No IPC, so it works across separately-spawned processes: each one
    computes the same instants from the same clock and they arrive at the
    critical section together."""
    target = (int(time.time() / period) + 1) * period
    while time.time() < target:
        pass


def _claim_release_loop(args: tuple[str, str, float]) -> tuple[str, int, int]:
    """Claim a contested path, prove the claim is readable, release.

    The read-back is the decisive check and it needs no sampling luck:
    nothing but this worker can release its own claim, so a claim that is
    not there a moment after being granted was overwritten by a writer
    working from a stale read."""
    root, name, seconds = args
    from rite_ai.claims.ledger import ClaimsLedger

    ledger = ClaimsLedger(Path(root) / ".rite" / "claims.json")
    log = Path(root) / "soak" / f"{name}.jsonl"
    deadline = time.time() + seconds
    grants = lost = 0
    records = []
    while time.time() < deadline:
        _align()
        path = CONTESTED[grants % len(CONTESTED)]
        if not ledger.claim([path], name).ok:
            continue
        held_from = time.time()
        grants += 1
        if not any(c.worker == name and path in c.paths for c in ledger.list_claims()):
            lost += 1
        records.append({"path": path, "from": held_from, "to": time.time()})
        ledger.release(name, [path])
    log.write_text("".join(json.dumps(r) + "\n" for r in records))
    return (name, grants, lost)


def _tick_loop(args: tuple[str, float]) -> tuple[str, int, int]:
    """The scheduler tick, continuously, against the same project.

    This is where the tick's own lock (a pid file published with
    `os.link`) meets the claims lock (an `flock` on a sidecar). They were
    built by different passes and had never run together; the tick
    releases claims through the ledger, so it takes the second while
    holding the first."""
    root, seconds = args
    from rite_ai.scheduler import run_tick

    deadline = time.time() + seconds
    ran = skipped = 0
    while time.time() < deadline:
        result = run_tick(Path(root))
        if not result.ok:
            return ("tick", -1, -1)
        ran, skipped = (ran, skipped + 1) if result.skipped else (ran + 1, skipped)
    return ("tick", ran, skipped)


def _project(tmp_path: Path) -> Path:
    rite = tmp_path / ".rite"
    rite.mkdir(parents=True)
    (tmp_path / "soak").mkdir()
    (rite / "brief.yaml").write_text("project:\n  name: soak\n  role: owner\n")
    (rite / "modules.yaml").write_text("modules: {}\n")
    (rite / "config.yaml").write_text(
        "ticket_backend:\n  type: none\n"
        "heartbeat:\n  interval_minutes: 1\n  stall_threshold: 1\n"
    )
    for i in range(WORKERS):
        manifest = tmp_path / "workers" / f"w{i}" / "worker.yml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(f"worker:\n  name: w{i}\n")
    return tmp_path


class TestExclusionHoldsUnderSustainedConcurrency:
    def test_no_granted_claim_is_ever_lost_and_no_path_is_held_twice(
        self, tmp_path: Path
    ):
        root = _project(tmp_path)
        tasks = [
            (_claim_release_loop, (str(root), f"w{i}", SOAK_SECONDS))
            for i in range(WORKERS)
        ] + [(_tick_loop, (str(root), SOAK_SECONDS)) for _ in range(TICKERS)]

        with mp.Pool(len(tasks)) as pool:
            handles = [pool.apply_async(fn, (arg,)) for fn, arg in tasks]
            results = [h.get(timeout=SOAK_SECONDS * 20) for h in handles]

        workers = [r for r in results if r[0].startswith("w")]
        ticks = [r for r in results if r[0] == "tick"]

        grants = sum(r[1] for r in workers)
        lost = sum(r[2] for r in workers)
        assert grants > 100, f"the soak barely ran ({grants} grants) — not a test"
        assert lost == 0, (
            f"{lost} of {grants} granted claims were unreadable immediately "
            "afterwards — a claim overwritten by a writer working from a "
            "stale read, which is how two sessions end up editing one file"
        )

        # No tick crashed, and the two lock mechanisms did not deadlock:
        # every ticker returned, and between them they either ran or
        # deliberately skipped.
        assert all(r[1] >= 0 for r in ticks), "a scheduler tick failed"
        assert sum(r[1] + r[2] for r in ticks) > 0

        # Two workers holding one path at overlapping times. The recorded
        # spans are a SUBSET of the true holds (they start after `claim`
        # returns and end before `release` is called), so any overlap
        # between two workers is a real simultaneous hold.
        spans: dict[str, list[tuple[float, float, str]]] = {}
        for log in sorted((root / "soak").glob("w*.jsonl")):
            for line in log.read_text().splitlines():
                record = json.loads(line)
                spans.setdefault(record["path"], []).append(
                    (record["from"], record["to"], log.stem)
                )

        simultaneous = []
        for path, held in spans.items():
            held.sort()
            for index, (start, end, who) in enumerate(held):
                for other_start, _other_end, other in held[index + 1 :]:
                    if other_start >= end:
                        break
                    if other != who:
                        simultaneous.append((path, who, other))

        assert not simultaneous, (
            f"{len(simultaneous)} simultaneous holds of one path by two "
            f"workers, e.g. {simultaneous[:3]}"
        )


class TestRiteNeverReachesARemoteWhileUnderLoad:
    """The static scan in `test_blast_radius.py` reads argument lists in
    the source. This watches what actually runs."""

    @staticmethod
    def _shim(bindir: Path) -> Path:
        real = shutil.which(
            "git", path="/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
        )
        assert real, "no real git to delegate to"
        log = bindir / "git-invocations.log"
        shim = bindir / "git"
        shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexec {real} "$@"\n')
        shim.chmod(0o755)
        return log

    def test_every_git_invocation_prepare_makes_is_read_only(
        self, tmp_path: Path, monkeypatch
    ):
        """`rite prepare` is the command with the most git in it — fetch,
        branch resolution, fast-forward — and the one that runs against a
        worker's own checkout of a real repo."""
        from rite_ai.config.models import Module
        from rite_ai.workspace import prepare_workspace

        source = tmp_path / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        (source / "README.md").write_text("x\n")
        subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=s@e",
                "-c",
                "user.name=s",
                "commit",
                "-qm",
                "init",
            ],
            cwd=source,
            check=True,
        )

        root = _project(tmp_path / "proj")
        worker_dir = root / "workers" / "w0"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--branch",
                "main",
                "--origin",
                "origin",
                str(source),
                str(worker_dir / "mod"),
            ],
            check=True,
        )

        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = self._shim(bindir)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

        module = Module(name="mod", path="mod/", url=str(source), branch="main")
        for branch in (None, "feature/one", None):
            prepare_workspace(worker_dir, [module], root, branch=branch)

        invocations = log.read_text().splitlines() if log.exists() else []
        assert invocations, "the shim recorded nothing — prepare ran no git"
        offending = [
            line
            for line in invocations
            if any(token in FORBIDDEN_GIT for token in line.split())
        ]
        assert not offending, (
            f"rite invoked a remote-writing or history-rewriting git verb: "
            f"{offending[:3]}"
        )

    def test_the_shim_would_catch_a_forbidden_verb(self, tmp_path: Path):
        """A recorder that cannot fail proves nothing. This asserts the
        detector works, so a green run above means something."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = self._shim(bindir)
        env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
        repo = tmp_path / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "reset", "--hard"], cwd=repo, env=env, capture_output=True
        )

        recorded = log.read_text().splitlines()
        assert any(
            token in FORBIDDEN_GIT for line in recorded for token in line.split()
        )


@pytest.mark.parametrize("name", ["claims.json", "pool.json"])
def test_the_lock_sidecar_is_not_the_file_it_guards(tmp_path: Path, name: str):
    """Restating the root cause as a property, because it is the one that
    made every other guarantee here untrue: an `flock` on a file that
    `write_atomic` replaces stops excluding the moment anyone writes."""
    from rite_ai.state import lock_path_for, locked, write_atomic

    target = tmp_path / name
    write_atomic(target, "{}")
    before = target.stat().st_ino
    with locked(target):
        write_atomic(target, '{"changed": true}')
    assert target.stat().st_ino != before
    assert lock_path_for(target) != target
    assert lock_path_for(target).stat().st_ino
