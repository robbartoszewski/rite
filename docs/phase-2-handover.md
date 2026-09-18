# Phase 2 handover

Written 2026-09-18 by the session that built it, for one that has the repo
and none of the context. Shipped in **v0.4.0** (`4e8a6a9`), on `main`.

Three companion files live outside this repo, in the working directory the
phase was run from (`rite-dogfood/phase2/`): `STATUS.md`, generated from git,
says what landed — regenerate it rather than trusting prose, including this
file's; `REMAINING.md` says what is left; `PHASE2-OPEN-QUESTIONS.md` holds
Q8-Q11, which are decisions for Robert. This file is the part that was in
nobody's head but the session that wrote it.

---

## 1. What Phase 2 actually shipped

A fleet of machines runs one project. One is Owner; the role moves on its own.

**The state layer** (`coordination/state_layer.py`) is the contract: four
operations, compare-and-swap per key, versions opaque. Three backends
implement it — `git_backend.py` (the default), `local_backend.py`, and a
Redis-shaped test double (`tests/kv_server.py` + `kv_backend.py`). All three
pass `tests/state_layer_conformance.py` unchanged. That suite is the most
valuable artefact in the phase: it is what makes "substitutable" true rather
than claimed.

**The role** — `lease.py` holds and renews it, `election.py` decides who
should, `demotion.py` hands it back when asked, `takeover.py` hands over the
outgoing or stalled machine's work, `monitor.py` is the per-tick loop that
drives all of it.

**The fleet's view of itself** — `heartbeat.py` (who is alive),
`claims_state.py` (who holds which paths), `message_log.py` (what happened),
`overview.py` (what a human is shown), `last_tick.py` (what this machine
concluded, for `rite status` without a network call).

**What runs it:** `rite_ai/scheduler/__init__.py`'s `_coordination_tick`,
called from `run_tick` — cron, every five minutes by default. Nothing else
runs the Manager loop. If that call is removed, everything above becomes
inert again and no test outside `tests/test_no_dead_wiring.py` will notice.

## 2. Decisions that are NOT in SPEC.md

Four are (D-58 unreadable merge input, D-59 implausible lease expiry, D-60
`priority` written-not-read, D-61 per-key CAS). These are not:

- **`.rite/machine` holds this machine's Manager name.** One line, no `rite
  init` question, absent means not enrolled. It exists because
  `config.yaml` is committed and shared (§9.3 has a Manager fetch the
  Owner's copy), so it cannot name the local machine. Raised as **Q8** —
  a hostname or env var would replace it, and only `identity.this_manager`
  would change.
- **The tick hands over when `in_flight == 0`.** A proxy for D-43's
  operation boundary, not the boundary. See **Q10**.
- **Ref-lock contention is retried, not reported.** git has a third push
  rejection the spec does not name — `[remote rejected] (failed to update
  ref)`, about 6% of pushes under six writers — and it means nobody won. It
  retries under the lease. Spec still describes only two outcomes.
- **An unseen Manager is not deferred to.** §2.4 says the highest-priority
  ACTIVE Manager promotes; "cannot tell" is a third answer, and deferring to
  a machine nobody can see would wedge the role for ever. Fail-closed
  inverts here, deliberately.
- **The tick does not write to the ticket backend.** Distribution and
  assignment are wired but not called from cron. See **Q9**, which is the
  one open question gating real functionality.
- **The git cache prunes itself** past 200 loose objects or 20MiB of packs.
  Safe only because the cache is derived.

## 3. Traps a fresh session will fall into

1. **`PYTHONPATH=src:tests`.** Without it the suite imports `rite_ai` from
   whatever is on PATH — another checkout — and you will debug a fix that
   already works. `pyproject.toml` adds `tests` to the path for pytest, but
   direct python invocations need both.
2. **This checkout is shared.** Other sessions work in it. Use a worktree
   (`git worktree add`), and stage paths explicitly — never `git add -A` in
   the shared tree.
3. **`main` moves under you**, several times an hour. Re-verify against the
   base you will actually push from, then push promptly. Perfect currency is
   not achievable; a green verification at the point of landing is.
4. **Do not trust decision numbers in old commit messages.** Phase 2's
   decisions were renumbered once (D-54/55/56 to D-58/59/60) after `main`
   allocated the same numbers for spec-digest work. Commit messages written
   before the renumber cite the old ones. The register is the truth.
5. **`ruff format` is not run during development** — the convention is that
   it happens at landing. CI runs `ruff format --check` on `main`, so
   formatting must happen before a merge lands or `main` goes red on three
   Python versions.
6. **CI needs gitleaks.** `ci.yml` installs it now (`2e42050`); before that,
   fifteen tests failed on every push to `main` for twelve commits because
   `doctor` reports a missing tool as a problem. If those tests start failing
   again, check the binary before the code.
7. **Concurrency tests must not be time-boxed.** Two of mine were, passed on
   a laptop and failed on a shared runner. They now run until the work is
   done with the clock as a cap. Anything new in this area should too.
8. **The cross-process tests use an accelerated clock** derived from one
   shared `t0` (`tests/election_harness.py`), so processes agree while a
   15-minute lease expires in 1.5 seconds. Do not "fix" it by sleeping.
9. **Read `docs/releasing.md` before cutting a release, not the last
   release's diff.** Inferring the procedure from `git show <last tag>`
   gets the version bumps right and misses step 5 — running
   `tools/template_history.py` and `tools/section_history.py` AFTER the tag
   and committing what they write. Skip it and the release works, ships,
   and quietly cannot deliver file updates to projects it created, because
   every section it wrote looks like a user's edit to the next refresh.
   That is exactly what happened cutting v0.4.0; another session caught it.
10. **A test that calls the thing cannot detect a missing caller.** This is
   the phase's signature defect, nine times. `tests/test_no_dead_wiring.py`
   catches it now; keep its exemption list honest.

## 4. The six things no ticket covered

The ticket set specified mechanisms and nothing owned the integration. These
had to be built anyway, and a Phase 3 ticket set needs a wiring ticket per
mechanism or it will happen again:

1. Something that runs the loop (the scheduler tick).
2. Machine identity (`.rite/machine`).
3. Fleet state for a human (`doctor`'s view, `status`'s local view).
4. Wiring the claim path so `rite claim` actually checks other machines, and
   release actually publishes.
5. The Owner's duties on a stall (handover, then claim expiry).
6. A guard so it cannot recur (`test_no_dead_wiring.py`).

---

# Part 2 — what is in nobody's head but mine

## Why the interface is shaped the way it is

**Compare-and-swap is per key, and that was a correction.** The first cut
made the version whole-state, because that is what `--force-with-lease`
compares, and justified it with "per-key CAS is not implementable on git".
That was wrong. A git backend compares the key's own blob, merges, pushes
with the lease, and when the ref moved because a DIFFERENT key changed it
re-merges rather than reporting a conflict. Whole-state CAS would force a
key-value store to serialise every write through one global version —
slower than git and absurd on its own terms — which is what made the first
contract unportable. It is also better for git: §2.4.2 warns a Manager whose
heartbeat keeps losing the ref race "could be marked falsely stalled", and
under per-key CAS that race never reaches a caller.

**A version is a fingerprint of the value, not a counter.** Equal versions
mean equal bytes. Git uses the blob id it already has; others hash. Two
consequences that are easy to undo by accident: no backend needs durable
version state, and an A→B→A rewrite is harmless because a decision made on
content is still sound when the content is what you read. What it cannot
express is "did anybody write, even the same bytes" — nothing needs that,
and a caller who does needs a sequence number inside the value.

**Three outcomes everywhere, never two.** Present/Absent/Unavailable,
Written/Conflict/Unavailable, alive/stalled/cannot-tell. Every collapse of
the third into one of the others is a bug with a name: "could not read" as
"nothing there" elects a second Owner; "we never heard" as "it failed" makes
an Owner stand down from a lease it holds.

**No caching, and the conformance suite asserts it.** A backend that cached
to hide a slow round trip would pass every single-handle test and hand a
Manager a lease that moved minutes ago.

## The idempotence property, which is the strongest evidence Phase 2 works

A stalled Manager is dealt with once because of ORDER, not a flag: hand the
work back (which needs the claims, to know which tickets), then expire the
claims. The next pass finds nothing and writes nothing. 200 passes against a
permanently stalled machine: one handover, log flat at three entries, pass
time 0.611s then 0.597s. Now in SPEC §3.4. The reason it does not drift is
that there is no "already handled" state to drift — an implementation that
adds one has reintroduced exactly what this avoids.

## Failure modes the spec still describes only at the happy path

- **Ref-lock contention** (above). §2.4.2 has "someone else won" and "the
  remote refused"; git has a third that means nobody won.
- **A released lease is indistinguishable from an expired one.** §2.4's
  graceful demotion says release, then the requester acquires — but the
  successor still waits out `skew_tolerance`, and during that window the
  role is free to ANY Manager, not just the one that asked. Observed in the
  cross-process soak: the outgoing Owner hands over twice and the returning
  one asks twice. Harmless, converges, but not what the protocol looks like.
  One write handing the lease directly to the requester closes both.
- **Assignment has no refusal memory.** If Q9 is answered yes, a Manager
  that refuses a ticket stays the least-loaded candidate, so the Owner
  re-assigns it every five minutes with a board comment each time. The
  refusal is on the ticket as a comment, and nothing reads comments back —
  the message log is where rite can remember it.
- **`read_messages` has no `limit`.** Reading the log is linear at ~9ms per
  message with the git backend. At three events a day a fleet reaches ~1000
  messages within a year and `doctor` spends ~9 seconds to print five. It is
  the only thing in the system that degrades with age — the cache is
  bounded, the state branch is one commit, a stalled fleet is flat over 200
  ticks. That is **Q11**, and it is a defect rather than a preference.
- **A "full" Manager cannot refuse safely.** The heartbeat carries
  `in_flight` but not capacity, so the Owner cannot tell busy from full and
  keeps choosing it. Refusing on "full" would ping-pong; that is why a full
  Manager holds silently instead.

## Things that cost me time and need not cost anyone else

- **Six writers against one bare repo produce ~2 successful state writes a
  second.** That is the scale of git-as-CAS: fine for a fleet, wrong for
  anything chattier. It is why the burst floors differ per backend.
- **`git gc --auto` does not bound a repository of orphaned commits.** It
  packs the loose objects, the loose count drops under the threshold, and it
  stops firing while the garbage sits in packfiles. Measured twice before
  going explicit.
- **Every state write orphans the previous commit.** 30 ticks of a
  two-machine fleet left 332 objects of which 8 were reachable.
- **Mutation testing earned its place repeatedly.** Four times a test passed
  for the wrong reason and only a deliberate break revealed it; twice the
  mutation could not find its target because an edit had silently done
  nothing. Assert that an edit matched before trusting it.
