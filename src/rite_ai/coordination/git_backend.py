"""The git compare-and-swap state layer (P2-1b, SPEC §2.4.2, §3.3, D-19).

The default backend. State is one commit on the `state` branch, rewritten with
`--force-with-lease` on every write (§3.3.1); messages are ordinary commits on
`main` (§3.3.2). It passes P2-1a's conformance suite unchanged.

**No working tree anywhere.** Commits are built with `hash-object`, a temporary
index and `commit-tree`, in a bare cache repository. A state write touches no
checkout, so it cannot be disturbed by one.

**The user's git config must not decide an outcome.** Hooks and signing are
disabled per invocation and prompting is off, the same hardening
`remote_probe.py` uses — measured necessary: a global `core.hooksPath` on the
author's machine refuses every push from a bare repo ("must be run in a work
tree"), which would otherwise look like a permanent CAS failure.

**Push results are read from `--porcelain`, measured rather than assumed:**

    exit 0                                  -> Written
    `!` … `[rejected] (stale info)`         -> Conflict   (someone wrote first)
    `!` … anything else                     -> Unavailable (protected branch,
                                               declined hook: misconfiguration,
                                               not contention — §2.4.2's warning)
    no porcelain line (e.g. exit 128)       -> Unavailable (may or may not have
                                               landed: §2.4.2 step 5)

**Every state commit carries a nonce, and that is load-bearing.** A state
commit is parentless with a fixed author, so two writers producing the same
tree in the same second would produce the SAME commit id — and a third writer
holding that id as its expected version would then win a compare-and-swap it
should have lost. That is ABA, and a nonce in the message removes it.

**§2.4.2 step 5 is implemented here rather than left to callers.** When a push
fails ambiguously the backend re-fetches and looks for its own commit: if the
write actually landed, it reports Written rather than a spurious failure. This
is the lost-acknowledgement obligation P2-1a's conformance suite explicitly
could not test from the outside.
"""

from __future__ import annotations

import os
import random
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from rite_ai.coordination.state_layer import (
    ABSENT,
    Absent,
    Appended,
    Conflict,
    Message,
    Messages,
    Present,
    StateLayer,
    Unavailable,
    Written,
    valid_key,
)

_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_STATE_TRACK = "refs/rite/state"
_LOG_TRACK = "refs/rite/log"
_APPEND_ATTEMPTS = 8
# How many times a write re-merges when the REF moved but our key did not.
_MERGE_ATTEMPTS = 8
_LOCK_ATTEMPTS = 8
# When the derived cache gets tidied. Low enough that it never becomes a
# surprise on disk, high enough that a gc is rare next to the round trip
# that triggered it.
_LOOSE_LIMIT = 200
_PACK_LIMIT_KIB = 20 * 1024

# Remote-side ref-lock contention. MEASURED, not anticipated: six writers
# bursting at one bare repository produce this in about 6% of pushes. It is a
# THIRD rejection class the spec does not name (§2.4.2 has only "someone else
# won" and "the remote refused"), and it means NOBODY won — the lease check
# passed and the ref update itself lost a lock. Reporting it as Unavailable
# would make a Manager fail closed under exactly the load coordination exists
# for; reporting it as Conflict would say another Manager is Owner when none
# is. Retrying the identical push is safe because the lease still guards it:
# if someone else did win in between, the retry comes back "stale info".
_CONTENDED = (
    "failed to update ref",
    "cannot lock ref",
    "failed to lock",
    "Unable to create",
)


class GitStateLayer(StateLayer):
    def __init__(
        self,
        remote: str,
        cache_dir: str | Path,
        state_branch: str = "state",
        log_branch: str = "main",
        timeout: int = 60,
    ) -> None:
        self.remote = str(remote)
        self.cache = Path(cache_dir)
        self.state_branch = state_branch
        self.log_branch = log_branch
        self.timeout = timeout
        self._ready = False

    # --- git plumbing ---

    def _git(self, args: list[str], stdin: bytes | None = None):
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "rite",
            "GIT_AUTHOR_EMAIL": "rite@example.invalid",
            "GIT_COMMITTER_NAME": "rite",
            "GIT_COMMITTER_EMAIL": "rite@example.invalid",
        }
        return subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "--git-dir",
                str(self.cache),
                *args,
            ],
            input=stdin,
            capture_output=True,
            timeout=self.timeout,
            env=env,
        )

    def _ensure_cache(self) -> Unavailable | None:
        if self._ready:
            return None
        try:
            if not (self.cache / "HEAD").exists():
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                init = subprocess.run(
                    ["git", "init", "--bare", "-q", str(self.cache)],
                    capture_output=True,
                    timeout=self.timeout,
                )
                if init.returncode != 0:
                    return Unavailable(f"could not create {self.cache}")
            # The empty tree must exist locally to commit an empty state.
            self._git(["hash-object", "-w", "-t", "tree", "--stdin"], stdin=b"")
            # HOUSEKEEPING, set once so git does it for us. Every state write
            # force-pushes a new parentless commit, which orphans the last
            # one: measured over 30 ticks of a two-machine fleet, 332 objects
            # of which 8 were reachable — 98% garbage, growing about 44KB a
            # cycle with no plateau. At a tick every five minutes that is
            # ~12MB a day, for ever, in a directory nobody looks at.
            #
            # This cache is derived: anything pruned can be fetched again. So
            # unreachable objects expire immediately rather than after git's
            # default two weeks, and the pack threshold is low enough that
            # `gc --auto` actually fires on a repo this small.
            self._git(["config", "gc.pruneExpire", "now"])
            self._git(["config", "gc.pruneExpire", "now"])
        except (OSError, subprocess.SubprocessError) as e:
            return Unavailable(f"git could not run: {e}")
        self._ready = True
        return None

    def _fetch(self, branch: str, track: str) -> str | None | Unavailable:
        """The fetched ref's oid, None if the branch does not exist on the
        remote, or Unavailable if the remote could not be read."""
        ready = self._ensure_cache()
        if ready is not None:
            return ready
        try:
            proc = self._git(
                [
                    "fetch",
                    "--no-tags",
                    "-q",
                    self.remote,
                    f"+refs/heads/{branch}:{track}",
                ]
            )
        except (OSError, subprocess.SubprocessError) as e:
            return Unavailable(f"fetch failed: {e}")
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace")
            if "couldn't find remote ref" in stderr or "not our ref" in stderr:
                return None
            return Unavailable(f"could not read {self.remote}: {_last(stderr)}")
        got = self._git(["rev-parse", f"{track}^{{commit}}"])
        if got.returncode != 0:
            # The ref is THERE and we cannot read what it points at — a
            # damaged object, not an empty remote. Reporting "absent" here
            # would let a Manager conclude there is no Owner and elect
            # itself, which is the exact failure this layer exists to
            # prevent. Only the fetch above may say "absent".
            #
            # A BACKSTOP, and no test reaches it: git validates at both ends
            # (a damaged object fails the fetch first — measured; a branch
            # cannot be made to point at a non-commit at all). It stays
            # because the alternative default is "absent", and the cost of
            # being wrong that way is two Owners.
            return Unavailable(
                f"{branch} exists but its commit is unreadable "
                f"(damaged object?): {_last(got.stderr.decode('utf-8', 'replace'))}"
            )
        return got.stdout.decode().strip()

    def _push(self, refspec: str, branch: str, expected: str):
        """(outcome, detail) where outcome is "written" | "conflict" |
        "unknown". "unknown" means the write may or may not have landed."""
        try:
            proc = self._git(
                [
                    "push",
                    "--porcelain",
                    f"--force-with-lease=refs/heads/{branch}:{expected}",
                    self.remote,
                    refspec,
                ]
            )
        except (OSError, subprocess.SubprocessError) as e:
            return "unknown", str(e)
        if proc.returncode == 0:
            return "written", ""
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        rejected = [ln for ln in out.splitlines() if ln.startswith("!")]
        if rejected:
            if any("stale info" in ln or "non-fast-forward" in ln for ln in rejected):
                return "conflict", rejected[0].strip()
            if any(m in ln for ln in rejected for m in _CONTENDED):
                return "contended", rejected[0].strip()
            # A protected branch or a declined hook: permanent, not a race.
            return "refused", rejected[0].strip()
        return "unknown", _last(out)

    def _push_retrying_lock_contention(self, refspec: str, branch: str, expected: str):
        """Absorb ref-lock contention; hand back what is left, as itself.

        Exhausting these retries used to return `refused`, which is the
        branch for a protected branch or a declined hook — permanent, and
        not a race. A burst that lost every lock in a row therefore came
        back as `Unavailable: the remote refused the write ... probes
        force-push permission`: wrong about what happened (a push that never
        took the lock definitely did not land, where `Unavailable` means it
        may have) and wrong about what to do about it (contention is not a
        permissions problem). Measured on Linux CI as ~23 of those in one
        burst run, against a CAS that had not lost a single race.

        **Why the bound is a policy and not a correctness threshold.** Every
        attempt past this one re-reads the ref and rebuilds against it, and
        every push carries `--force-with-lease`, so a writer that really did
        win in between comes back "stale info" and the caller is told
        Conflict. No value of `_LOCK_ATTEMPTS` can produce a double win or a
        silent overwrite; it only decides how long a contended writer tries
        before the caller is told it could not finish. That is why raising
        it would not have been a fix and lowering it is not a risk.
        """
        for attempt in range(_LOCK_ATTEMPTS):
            outcome, detail = self._push(refspec, branch, expected)
            if outcome != "contended":
                return outcome, detail
            # Jittered so a burst of writers does not re-collide in lockstep.
            time.sleep(random.uniform(0.005, 0.02) * (attempt + 1))
        return "contended", f"the remote could not lock {branch}: {detail}"

    # --- StateLayer ---

    def read_state(self, key: str) -> Present | Absent | Unavailable:
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        head = self._fetch(self.state_branch, _STATE_TRACK)
        if isinstance(head, Unavailable):
            return head
        if head is None:
            return Absent()
        oid = self._blob_id(head, key)
        if isinstance(oid, Unavailable):
            return oid
        if oid == ABSENT:
            return Absent()
        blob = self._git(["cat-file", "blob", oid])
        if blob.returncode != 0:
            return Unavailable(
                f"could not read {key!r}: "
                f"{_last(blob.stderr.decode('utf-8', 'replace'))}"
            )
        # The blob id IS the fingerprint of these bytes, which is exactly what
        # a version is (state_layer property 2) — no extra bookkeeping, and
        # identical content deliberately keeps its version.
        return Present(blob.stdout, oid)

    def write_state(
        self, key: str, value: bytes, expected_version: str
    ) -> Written | Conflict | Unavailable:
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")

        contended = ""
        for _ in range(_MERGE_ATTEMPTS):
            head = self._fetch(self.state_branch, _STATE_TRACK)
            if isinstance(head, Unavailable):
                return head

            current = ABSENT if head is None else self._blob_id(head, key)
            if isinstance(current, Unavailable):
                return current
            if current != expected_version:
                # THIS key moved. The caller's decision is stale.
                return Conflict(current)

            built = self._build(head, key, value)
            if isinstance(built, Unavailable):
                return built
            new, blob = built

            lease = "" if head is None else head
            outcome, detail = self._push_retrying_lock_contention(
                f"{new}:refs/heads/{self.state_branch}", self.state_branch, lease
            )
            if outcome == "written":
                try:
                    self._collect_garbage()
                except Exception:  # noqa: BLE001 - see below
                    # The write has ALREADY LANDED. Whatever tidying does
                    # afterwards, the caller must be told what happened to
                    # its write — a success surfacing as an exception is
                    # the same confusion as a lost acknowledgement, and
                    # here it would be self-inflicted. Broad on purpose:
                    # housekeeping has no failure the caller should ever
                    # have to handle.
                    pass
                return Written(blob)
            if outcome == "conflict":
                # The REF moved, which says nothing about our key: somebody
                # wrote a different one. Re-read and merge again. Exporting
                # this as a conflict is what made the old contract git-shaped,
                # and it is also what §2.4.2 warns falsely marks a Manager
                # stalled when its heartbeat keeps losing the ref race.
                continue
            if outcome == "contended":
                # We never took the ref lock, so nothing of ours landed and
                # the ref may have moved while we waited. That is the same
                # situation as a conflict on somebody else's key, and it has
                # the same answer: re-read, rebuild, push again. Returning
                # here instead is what produced a spurious `Unavailable` —
                # and `Unavailable` would tell the caller its write might
                # have landed, which is the one thing that cannot be true of
                # a push that was never allowed to update the ref.
                contended = detail
                continue
            if outcome == "refused":
                return Unavailable(
                    f"the remote refused the write: {detail}. This is not a "
                    "lost race — `rite doctor` probes force-push permission"
                )
            # Ambiguous: §2.4.2 step 5 — re-read before concluding anything.
            landed = self._fetch(self.state_branch, _STATE_TRACK)
            if isinstance(landed, Unavailable) or landed != new:
                return Unavailable(
                    f"the push neither succeeded nor was rejected ({detail}); "
                    "the write may or may not have landed — re-read before "
                    "deciding"
                )
            return Written(blob)

        if contended:
            # Terminal, but say which wall was hit. "Kept moving under us"
            # sends the reader looking for a writer that won; nobody did.
            return Unavailable(
                f"{key}: could not take the ref lock on {self.state_branch} "
                f"after {_MERGE_ATTEMPTS} rounds of contention ({contended}). "
                "Nothing was written — the push never updated the ref"
            )
        return Unavailable(
            f"{key}: the state branch kept moving under us; "
            f"gave up after {_MERGE_ATTEMPTS} merges"
        )

    def _collect_garbage(self) -> None:
        """Keep the cache from growing for ever, on a threshold WE choose.

        Every state write force-pushes a new parentless commit and orphans
        the last, so nearly everything here is garbage: measured over 30
        ticks of a two-machine fleet, 332 objects of which 8 were reachable,
        growing ~44KB a cycle with no plateau — about 12MB a day at a tick
        every five minutes, in a directory nobody looks at.

        Git's own `gc --auto` does not bound it, which was measured rather
        than assumed: it packs the loose objects, the loose count drops back
        under the threshold, and it stops firing while the garbage sits in
        packfiles. The peak still climbed across 60 ticks with `gc.auto` and
        again with a low `gc.autoPackLimit`.

        So the trigger is explicit and the prune is immediate. That is only
        safe because this cache is DERIVED: everything in it can be fetched
        again, so there is nothing to lose by being aggressive. A repository
        holding the only copy of anything must never be treated this way.
        """
        try:
            counted = self._git(["count-objects", "-v"])
        except (OSError, subprocess.SubprocessError):
            # Housekeeping is never the caller's business. The write it
            # follows has already landed, and reporting that as a failure
            # because the tidying afterwards went wrong would make a
            # successful write look like a lost one — the exact confusion
            # §2.4.2 step 5 exists to prevent.
            return
        if counted.returncode != 0:
            return
        stats = {}
        for line in counted.stdout.decode("utf-8", "replace").splitlines():
            name, _, value = line.partition(":")
            stats[name.strip()] = value.strip()
        try:
            loose = int(stats.get("count", "0"))
            packed_kib = int(stats.get("size-pack", "0"))
        except ValueError:
            return
        if loose > _LOOSE_LIMIT or packed_kib > _PACK_LIMIT_KIB:
            try:
                self._git(["gc", "--prune=now", "--quiet"])
            except (OSError, subprocess.SubprocessError):
                return

    def _blob_id(self, head: str, key: str):
        """The id of the blob at `key`, or ABSENT. Unavailable if the tree
        itself cannot be read — which is not the same as the key being
        missing."""
        listed = self._git(["ls-tree", "-z", head, "--", key])
        if listed.returncode != 0:
            return Unavailable(
                f"could not read the state tree: "
                f"{_last(listed.stderr.decode('utf-8', 'replace'))}"
            )
        entry = listed.stdout.decode("utf-8", "replace").strip("\x00").strip()
        if not entry:
            return ABSENT
        # "<mode> <type> <oid>\t<path>"
        try:
            return entry.split("\t", 1)[0].split()[2]
        except IndexError:
            return Unavailable(f"could not parse the tree entry for {key!r}")

    def _build(self, head: str | None, key: str, value: bytes):
        """(commit id, blob id) — a parentless commit holding `head`'s tree
        with `key` replaced, and the id of the value's blob.

        The blob id is the KEY's version: git's own content address, which is
        exactly what a version is (state_layer property 2), so nothing extra
        is stored or kept in step.

        The whole tree is carried over because a force-push REPLACES it — a
        git detail, kept here rather than in the interface, where it would
        force a key-value store to do a pointless merge.
        """
        with tempfile.TemporaryDirectory(prefix="rite-index-") as tmp:
            index = Path(tmp) / "index"
            old = os.environ.get("GIT_INDEX_FILE")
            os.environ["GIT_INDEX_FILE"] = str(index)
            try:
                if head is not None:
                    read = self._git(["read-tree", f"{head}^{{tree}}"])
                    if read.returncode != 0:
                        return Unavailable("could not read the current state tree")
                blob = self._git(["hash-object", "-w", "--stdin"], stdin=value)
                if blob.returncode != 0:
                    return Unavailable("could not write the value")
                oid = blob.stdout.decode().strip()
                add = self._git(
                    ["update-index", "--add", "--cacheinfo", f"100644,{oid},{key}"]
                )
                if add.returncode != 0:
                    return Unavailable(f"could not stage {key!r}")
                tree = self._git(["write-tree"])
                if tree.returncode != 0:
                    return Unavailable("could not build the new state tree")
            finally:
                if old is None:
                    os.environ.pop("GIT_INDEX_FILE", None)
                else:
                    os.environ["GIT_INDEX_FILE"] = old

        # Parentless (§3.3.1: always exactly one commit) with a NONCE, so two
        # writers building the same tree cannot produce the same commit id and
        # let a stale lease succeed.
        nonce = uuid.uuid4().hex
        commit = self._git(
            ["commit-tree", tree.stdout.decode().strip(), "-m", f"rite state {nonce}"]
        )
        if commit.returncode != 0:
            return Unavailable("could not build the state commit")
        return commit.stdout.decode().strip(), oid

    def append_message(self, content: str) -> Appended | Unavailable:
        payload = content.encode("utf-8")
        last_detail = ""
        for _ in range(_APPEND_ATTEMPTS):
            parent = self._fetch(self.log_branch, _LOG_TRACK)
            if isinstance(parent, Unavailable):
                return parent
            tree = _EMPTY_TREE
            if parent is not None:
                got = self._git(["rev-parse", f"{parent}^{{tree}}"])
                if got.returncode == 0:
                    tree = got.stdout.decode().strip()
            args = ["commit-tree", tree] + (["-p", parent] if parent else [])
            commit = self._git(args, stdin=payload)
            if commit.returncode != 0:
                return Unavailable("could not build the message commit")
            new = commit.stdout.decode().strip()
            outcome, detail = self._push_retrying_lock_contention(
                f"{new}:refs/heads/{self.log_branch}", self.log_branch, parent or ""
            )
            last_detail = detail
            if outcome == "written":
                return Appended(new)
            if outcome == "refused":
                return Unavailable(f"the remote refused the message: {detail}")
            if outcome == "unknown":
                # It may have landed. Appending again would DUPLICATE the
                # message, so look for this exact commit before retrying.
                landed = self._fetch(self.log_branch, _LOG_TRACK)
                if isinstance(landed, Unavailable):
                    return Unavailable(
                        f"the message may or may not have landed ({detail})"
                    )
                if (
                    landed
                    and self._git(
                        ["merge-base", "--is-ancestor", new, landed]
                    ).returncode
                    == 0
                ):
                    return Appended(new)
            # conflict: someone appended first — re-parent and retry.
        return Unavailable(
            f"could not append after {_APPEND_ATTEMPTS} attempts: {last_detail}"
        )

    def read_messages(
        self, since: str | None = None, limit: int | None = None
    ) -> Messages | Unavailable:
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        head = self._fetch(self.log_branch, _LOG_TRACK)
        if isinstance(head, Unavailable):
            return head
        if head is None:
            return Messages([])
        if since is not None:
            known = self._git(["cat-file", "-e", f"{since}^{{commit}}"]).returncode == 0
            reachable = (
                known
                and self._git(["merge-base", "--is-ancestor", since, head]).returncode
                == 0
            )
            if not reachable:
                return Unavailable(f"unknown message cursor: {since!r}")
        rng = f"{since}..{head}" if since else head
        # `-n` BEFORE `--reverse`: rev-list chooses the newest n and then
        # reverses what it chose, so this is the last n in append order. The
        # whole point is not reading the rest — each message is its own
        # commit and every one costs a `cat-file`.
        limited = ["-n", str(limit)] if limit is not None else []
        listing = self._git(["rev-list", *limited, "--reverse", rng])
        if listing.returncode != 0:
            return Unavailable("could not list messages")
        items = []
        for oid in listing.stdout.decode().split():
            raw = self._git(["cat-file", "commit", oid])
            if raw.returncode != 0:
                return Unavailable(f"could not read message {oid[:8]}")
            _, _, body = raw.stdout.partition(b"\n\n")
            items.append(Message(oid, body.decode("utf-8", "replace")))
        return Messages(items)


def _last(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else "no output"
