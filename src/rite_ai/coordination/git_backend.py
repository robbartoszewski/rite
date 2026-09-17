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
_LOCK_ATTEMPTS = 8

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
        for attempt in range(_LOCK_ATTEMPTS):
            outcome, detail = self._push(refspec, branch, expected)
            if outcome != "contended":
                return outcome, detail
            # Jittered so a burst of writers does not re-collide in lockstep.
            time.sleep(random.uniform(0.005, 0.02) * (attempt + 1))
        return "refused", f"the remote could not lock {branch}: {detail}"

    # --- StateLayer ---

    def read_state(self, key: str) -> Present | Absent | Unavailable:
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        head = self._fetch(self.state_branch, _STATE_TRACK)
        if isinstance(head, Unavailable):
            return head
        if head is None:
            return Absent(ABSENT)
        blob = self._git(["cat-file", "blob", f"{head}:{key}"])
        if blob.returncode != 0:
            stderr = blob.stderr.decode("utf-8", "replace")
            if "does not exist" in stderr or "Not a valid object name" in stderr:
                return Absent(head)
            return Unavailable(f"could not read {key!r}: {_last(stderr)}")
        return Present(blob.stdout, head)

    def write_state(
        self, key: str, value: bytes, expected_version: str
    ) -> Written | Conflict | Unavailable:
        if not valid_key(key):
            return Unavailable(f"invalid state key: {key!r}")
        head = self._fetch(self.state_branch, _STATE_TRACK)
        if isinstance(head, Unavailable):
            return head
        if expected_version == ABSENT:
            if head is not None:
                return Conflict(head)
        elif head is None or self._git(
            ["cat-file", "-e", f"{expected_version}^{{commit}}"]
        ).returncode != 0:
            # The version expected is not one this remote can be holding.
            return Conflict(head)

        with tempfile.TemporaryDirectory(prefix="rite-index-") as tmp:
            index = Path(tmp) / "index"
            env_index = {"GIT_INDEX_FILE": str(index)}
            old = os.environ.get("GIT_INDEX_FILE")
            os.environ.update(env_index)
            try:
                if expected_version != ABSENT:
                    read = self._git(["read-tree", f"{expected_version}^{{tree}}"])
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
        # writers building the same tree cannot produce the same commit id.
        nonce = uuid.uuid4().hex
        commit = self._git(
            ["commit-tree", tree.stdout.decode().strip(), "-m", f"rite state {nonce}"]
        )
        if commit.returncode != 0:
            return Unavailable("could not build the state commit")
        new = commit.stdout.decode().strip()

        lease = "" if expected_version == ABSENT else expected_version
        outcome, detail = self._push_retrying_lock_contention(
            f"{new}:refs/heads/{self.state_branch}", self.state_branch, lease
        )
        if outcome == "written":
            return Written(new)
        if outcome == "conflict":
            return Conflict(head)
        if outcome == "refused":
            return Unavailable(
                f"the remote refused the write: {detail}. This is not a lost "
                "race — `rite doctor` probes force-push permission (P2-1e)"
            )
        # Ambiguous: §2.4.2 step 5 — re-read before concluding anything.
        landed = self._fetch(self.state_branch, _STATE_TRACK)
        if isinstance(landed, Unavailable) or landed != new:
            return Unavailable(
                f"the push neither succeeded nor was rejected ({detail}); the "
                "write may or may not have landed — re-read before deciding"
            )
        return Written(new)

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
                if landed and self._git(
                    ["merge-base", "--is-ancestor", new, landed]
                ).returncode == 0:
                    return Appended(new)
            # conflict: someone appended first — re-parent and retry.
        return Unavailable(
            f"could not append after {_APPEND_ATTEMPTS} attempts: {last_detail}"
        )

    def read_messages(self, since: str | None = None) -> Messages | Unavailable:
        head = self._fetch(self.log_branch, _LOG_TRACK)
        if isinstance(head, Unavailable):
            return head
        if head is None:
            return Messages([])
        if since is not None:
            known = self._git(["cat-file", "-e", f"{since}^{{commit}}"]).returncode == 0
            reachable = known and self._git(
                ["merge-base", "--is-ancestor", since, head]
            ).returncode == 0
            if not reachable:
                return Unavailable(f"unknown message cursor: {since!r}")
        rng = f"{since}..{head}" if since else head
        listing = self._git(["rev-list", "--reverse", rng])
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
