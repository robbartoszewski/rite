# rite — Multi-session Claude coordination for teams

**Version:** 0.24.2 · **Date:** 2026-09-25

**Revision history** is at the end of this document (§14) — it records what
each version corrected and why, including the claims that did not survive
checking. Read it if you want to know how far to trust a given section; skip
it if you are here to find out what rite is.

## 1. What rite is

rite is a project-agnostic tool for coordinating parallel Claude Code sessions across
repos and machines. Its primary use case is a **single developer** running multiple
Claude sessions on one machine — preventing collisions, managing claims, generating
Claude config, and keeping a ticket board in sync. Most users will never need more
than this.

For teams, rite extends to multiple machines and people — routing decisions, distributing
tickets, surfacing stalls across Dispatches that have no native awareness of each other.
That coordination layer is a strict superset of the single-developer case and ships
separately.

### 1.1. Origin

rite generalises a multi-session Claude setup built for one commercial project,
referred to throughout as **the upstream**. That project will consume the published
version, so development doubles as dogfooding. It is deliberately not named here:
rite is project-agnostic, and a spec that names one customer invites the reader to
calibrate the design against that customer rather than their own.

The upstream is not the target audience — it is the complexity benchmark the design
had to handle: strict confidentiality requirements, heavy LLM use, several
technologies, multiple repos, and a regulated domain. Where a section below leans on
that shape rather than on something general, it says so.

### 1.2. Claude-native, explicitly

**rite is a Claude Code tool.** `CLAUDE.md`, `.claude/agents/`, Dispatch, and Claude
Code sessions are first-class concepts, not an implementation detail behind an
interface. There is no AI-provider abstraction layer and none is planned.

This is a feature, not a limitation. rite's design fits how Claude Code actually
behaves — its session model, its agent spawning, its file-based configuration, its
slash commands. An abstraction layer that pretends these are interchangeable with
other providers would weaken every integration point to the lowest common denominator.
Anyone wanting a customised setup for a different provider can contract for it.

### 1.3. Non-goals

- rite is not an AI agent framework. It coordinates Claude Code sessions; it does not
  replace or wrap the Claude API.
- rite is not a project management tool. It integrates with existing ticket backends
  (JIRA, GitHub Issues, etc.) rather than replacing them.
- rite does not manage Claude API keys, billing, or model selection.
- rite does not abstract over AI providers. See §1.2.

---

**A note on `P1.x` / `P2.x` labels.** These index work items in an internal
implementation plan that is not published with this document. They appear here only
as shorthand for "Phase 1, already built" or "Phase 2, not yet" — nothing in this
spec depends on being able to look one up.

## 2. Roles

Three roles. The hierarchy is flat — one Owner, one or more Managers, each Manager
with its own Workers.

### 2.0. Durable state — the general rule, not just Owner's

**Dispatch — whichever role, Owner, Manager, or the hub session in a multi-project
setup (§8.9) — is the reliable layer. Claude Code sessions themselves, at every
level, are disposable.** §2.4.3 states this for Owner state specifically ("Owner
state lives in the coordination repo, not in memory") because Owner failover is the
most catastrophic case if state is lost — but the rule generalises: **durable state
lives in the Manager, the coordination repo, or the ticket backend — never only in
a session's memory or an uncommitted working tree, at any role.** Each role's
section below states its own instance of this rule.

### 2.1. Worker

One session, one task at a time. Each Worker has:

- **A physical workspace** at `<project-root>/workers/<worker-name>/`, containing its
  own checked-out repos and its own Claude instructions. Workers never share directories.
- **Minimal instructions** — enough to do the job and communicate with its Manager.
  A Worker does not see the org chart or other Workers' state.

A Worker behaves like a real developer: pulls before committing, opens PRs, merges
reviewed work. A **workspace preparation script** runs before each task to ensure the
workspace is clean and current — right repos, right branches, no residue from the
previous task.

**Workers commit early and often, because their death is expected — not a rare
crash but the normal failure mode** (§12.3 already treats Worker session death as
something the Manager routinely reclaims from, not an exceptional event). Commit at
each meaningful checkpoint — a passing test, a completed sub-step — not only when
the whole task is done. Work that exists only in an uncommitted working tree is
work that will be lost with real, non-negligible probability; per §2.0, a Worker
that batches everything into one end-of-task commit has made **no** recoverable
progress until that commit lands, regardless of how much work actually happened.

### 2.2. Manager

One per Claude account, running on a member's machine. A Manager:

- Dispatches work to its own Workers.
- Creates tickets and moves its own Workers' tickets on the board.
- Defers to the Owner on project-wide matters.
- Reports status and blockers upward; receives assignments and decisions downward.

A Manager is scoped to its own Workers. It cannot see or control another Manager's
Workers directly.

**Per §2.0, a Manager's own state — its Worker roster, its claims, its in-flight
assignments — must also be reconstructable from files, not held only in the
Manager's session.** This is what makes §9.10's `start` orientation table work for
a *returning* Manager, not only for the Owner-failover case §2.4.3 covers: a
Manager whose session dies and restarts reads the same durable sources (ticket
backend, claims ledger, outbox/heartbeat log, the handover snapshot at §9.10.1) a
fresh Manager would, and reaches the same conclusion about what to resume.

### 2.3. Owner

A Manager plus hub. **Exactly one active Owner at any time** — not a static
designation but the result of leader election (§2.4). The Owner:

- Owns the board and the project-wide ticket queue.
- Assigns work by setting a label with a Worker's name (or a Manager's, for the Manager
  to sub-assign).
- Monitors blocked and stalled tickets.
- Receives expertise-routed decisions from all Managers.
- Detects stalled Managers (no heartbeat) and surfaces them to the human.
- Holds and renews its **Owner lease** — loses the role if the lease expires.

### 2.4. Owner failover

⚠ **Phase 2.** Everything in §2.4 is cross-machine leader election — the coordination
repo, the lease, the promotion protocol. A single-developer Phase 1 setup has exactly
one Manager, which is trivially Owner by default (§2.4 "Promotion", bullet 1) and never
executes any of the mechanics below: no lease file, no renewal loop, no promotion race.
`rite init`'s "Owner or Manager machine?" question (§9.3) still applies in Phase 1 —
answering "Manager" there only fetches the Owner's `config.yaml` for alignment
(§9.3 §1); it does not enrol in election, because there is nothing to enrol in until
a second machine exists. This section is written as unconditional behavior because
that is what it becomes once Phase 2 ships; nothing below is live code today.

Managers are listed in **priority order** in `config.yaml`. The first active Manager
is Owner. The ordering is an org chart: if the CTO's machine is unavailable the team
lead answers; if not the team lead, the most senior developer — that is one team's
ordering as an illustration, not a set of roles rite assumes (§4). The
expertise-routing fallback to "the Owner" inherits whatever chain the team wrote,
automatically — no separate fallback list needed.

#### Promotion

- A Manager starting with no other active Manager becomes Owner by default.
- When the current Owner's lease expires (§2.4.1), the highest-priority active Manager
  promotes itself.
- Promotion is **atomic** — exactly one winner when two race (§2.4.2).

#### Graceful demotion

When a higher-priority Manager returns, the role hands back **without interrupting
current work:**

1. The returning Manager publishes a **promotion request** to the coordination repo.
2. The incumbent Owner sees the request, **finishes its current operation** — defined
   precisely as **one ticket-backend write or one board-state transition**: a single
   `create`/`update`/`move`/`assign`/`label`/`comment` call (§6.1) that has already
   started, or a single state-branch push already in flight. Not the current ticket
   end-to-end, and not a multi-call sequence (unassign → assign → relabel → comment is
   FOUR operations under this definition, and the incumbent may hand over between any
   two of them) — a smaller unit than "ticket" was found to be genuinely ambiguous
   without a stated boundary; this is that boundary.
3. The incumbent **transfers state** (§2.4.3) by pushing it to the coordination repo.
4. The incumbent **releases the lease** and demotes to a regular Manager.
5. The returning Manager acquires the lease and becomes Owner.

No seizure. An interrupted assignment produces the same inconsistency a crash does,
and the point of graceful demotion is to avoid that when we can.

#### 2.4.1. Lease, not heartbeat

The Owner holds a lease that **expires unless renewed.** Under heartbeats, a hung
Owner that still looks alive holds the role indefinitely; under leases it loses it
automatically.

**Measured, from the upstream's coordination ledger.** That ledger is an append-only
log of claim, release and force-release events. A force-release exists for exactly one
situation: a claim has to be released by someone other than the session holding it.
Across its recorded history it contains **46 force-releases, and 44 of them record the
holder as gone** — a shutdown hook that never fired, a machine restart that killed the
session without releasing, a session idle for hours with no outbox entry, a claim on a
ticket that had already merged. **45 of the 46 fall on two consecutive days.**

That is the failure mode this lease exists for, counted rather than remembered: the
session was gone and its claim record still read as live, so a human had to go in and
say so. A heartbeat model cannot distinguish those 44 from healthy work; a lease
expires on its own.

⚠ Earlier versions cited "three sessions hung while reporting as running in one day"
here. That figure could not be traced to anything outside this document, and the
numbers first offered as a replacement did not survive checking either — they mixed
whole-ledger totals with a single day's. The figures above were recomputed from the
ledger directly. In a document whose value is that its claims are checkable, an
unsourceable measurement is worse than none.

The lease is a file in the coordination repo (`owner-lease.json`):

```json
{
  "owner": "manager-alpha",
  "acquired": "2026-09-09T14:30:00Z",
  "expires": "2026-09-09T14:45:00Z",
  "priority": 0
}
```

**`priority` is written for audit and ignored on read** (D-60). It records what the
holder believed its priority was when it acquired the lease, which is genuinely useful
when reconstructing why a promotion went the way it did. It never participates in a
decision: **priority is the order of `coordination.managers`**, and only that list is
consulted. The list is declared intent under version control; a lease is ephemeral
runtime state. If a lease could override the list, a deliberate reorder would silently
fail to take effect until some lease happened to expire — the worst kind of "it'll fix
itself eventually". Do not delete the field as dead weight, and do not start reading
it.

Renewal: the Owner pushes an updated `expires` timestamp before the current one
lapses. Default lease duration: **15 minutes** (configurable). If the Owner crashes
or hangs, the lease expires on its own and the next Manager in priority order
promotes.

⚠ **Clock skew — a real gap, not a hypothetical.** `expires` is written using the
incumbent's clock and read against each challenger's own. With no tolerance stated, a
challenger whose clock runs fast can read the lease as expired minutes before the
incumbent's own clock would prompt it to renew — producing the exact temporary
split-brain §2.4.2 exists to prevent, with no crash and no network partition
involved. **Requires NTP-synced clocks as a documented precondition**, and a
**skew-tolerance margin**: a challenger treats the lease as expired only once
`now > expires + skew_tolerance` (default **60 seconds**, configurable), never at
the bare `expires` boundary. This narrows the window; it does not close it to zero —
state it as a residual risk, not a solved one.

⚠ **The tolerance cuts both ways, and the far side is worse.** The rule above only
protects an incumbent against a challenger whose clock runs FAST. A lease written by
a machine whose clock runs fast is the opposite case, and read literally the rule
above never expires it: a Manager whose clock is a day ahead writes `expires`
a day ahead, every challenger reads it as live, and **no challenger can ever
legitimately take the role back**. That is not a race — it is a permanent wedge, and
it needs no malice, only a wrong clock or a corrupted timestamp.

So a lease is **not credible if it expires further ahead than an honest writer could
have set it.** Nothing honest can write an `expires` more than
`owner_lease_minutes + skew_tolerance` from now, because that is the longest lease
the configuration permits plus the most drift it tolerates. A lease beyond that
ceiling is treated as **invalid, and therefore challengeable** (D-59).

The ceiling is derived, not chosen: it falls out of the two values already in
`coordination:`, so raising the lease duration moves it automatically and there is no
third number to keep in step. **Log a lease rejected as not-credible distinctly** —
it means somebody's clock is wrong, which is worth knowing rather than silently
recovering from, and it is the only signal that will say so.

#### 2.4.1a. ⚠ OBSERVED ONCE: two simultaneous Owners under extreme load

**The property this section exists to provide failed once, and the
mechanism is not established.** Recorded here rather than closed, because a
safety property that has been seen to break belongs in the spec even when
it cannot be reproduced.

`tests/test_graceful_handover_across_processes.py` asserts
`not overlapping_owners(runs)`. On 2026-09-20 it failed inside a full-suite
run:

    beta's lease   ...0594.8 -> ...3644.8
    alpha acquired ...3540.9
    OVERLAP          103.9s

⚠ **Note the direction: alpha acquired 103.9s BEFORE beta's lease
expired.** `stand_for_owner` cannot promote against a lease its holder
reads as `HELD` — it returns `StillOwner`/`NotOwner` and stops. So alpha
did not take the role because beta was slow to yield. Either alpha read a
lease that beta had already renewed past, or the two processes' clocks
disagreed by more than `skew_tolerance_seconds`. **Which of those it was is
unknown**, and the difference matters: one is a defect in the state layer's
read, one is a defect in `verdict`, and one is an environment fact.

**What is established, and what is not:**

| | |
|---|---|
| The failure is the PROPERTY, not a timeout | established — the assertion is `overlapping_owners` |
| It occurred at load average ~140, 1023 processes | established |
| Reachable at ordinary CPU contention | **NO** — 10 consecutive passes at 14 hogs on 14 cores |
| The mechanism | **NOT established** |

⚠ **"It needs load 140" is not "it is not real".** A dogfood run on
somebody else's machine is not a controlled environment, and this is the
guarantee the whole coordination layer exists to provide. It is recorded as
a **stated bound** — the Owner-lease guarantee has been observed to fail
under load roughly ten times core saturation — rather than as a closed
ticket.

**The next occurrence is self-diagnosing.** The harness now records, every
tick, the lease each process actually READ beside that process's own clock,
and the failure message splits the three causes: a promotion where
`read_was_expired=False` means the challenger promoted against a lease it
read as VALID (`verdict`); `True` means it acted correctly on a stale read
(the state layer, or clock skew). Neither could be told from the timestamps
alone, which is why this took a conversation rather than a log line.

#### 2.4.2. Atomic promotion via git push

**Split brain — two Managers both believing they are Owner — is the critical failure
mode.** Two Owners produce duplicate assignments and contradictory labels: worse than
no Owner.

**`git push --force-with-lease` on the state branch is the compare-and-swap
primitive** (see §3.3.1). The push fails if the remote's ref has moved since the last
fetch, so two simultaneous claimants produce one success and one explicit failure.
JIRA fields offer no equivalent guarantee — a REST write succeeds silently when two
hit the same field.

The promotion protocol:

1. Fetch the state branch from the coordination repo. **First-ever write to a
   brand-new `state` branch is a ref-creation, not a ref-update** —
   `--force-with-lease` alone does not express "must not already exist"; use the
   explicit-refspec form (`--force-with-lease=state:<expected-oid-or-absent>`) so a
   genuinely first write and a race against a since-created branch are both handled,
   not just the common update case.
2. Read `owner-lease.json`. If the lease is not expired (§2.4.1's skew tolerance
   applies here), stand down. If no lease file has ever existed, treat this as the
   first-write case in step 1 — there is nothing to compare against.
3. Write a new lease file with this Manager's name, commit on the state branch, and
   push with `--force-with-lease`.
4. If the push succeeds: this Manager is Owner. Start the lease renewal loop.
   Append a promotion event to the message log on `main` (§3.3.2).
5. If the push fails (ref moved): **re-fetch and re-read before concluding you
   lost.** `--force-with-lease` guarantees no silent clobbering of a write — it does
   not by itself guarantee you can distinguish "another Manager won" from "my own
   push landed but the local ack was lost to a network drop, and this retry's
   expected-old-value is now stale against my own successful write." If the re-read
   lease names *this* Manager, treat it as already-Owner and start the renewal loop
   — do not stand down from a lease you actually hold. Only stand down if the re-read
   lease names someone else.

⚠ **The CAS guarantee is whole-ref, not whole-intent — it protects the write, not
only the election.** §3.3.1 bundles the lease file, every Manager's heartbeat, and
the claims snapshot onto **one** ref. `--force-with-lease` has no notion that a
heartbeat write and a lease write touch unrelated files: any two writers racing on
that ref get the identical one-success-one-rejection outcome as a genuine election
collision, even when their changes don't actually conflict. Two consequences, both
real: (a) because a force-push **replaces** the ref's tree rather than merging into
it, every writer must read-merge-write the *entire* current state before pushing —
a writer that naively pushes just its own delta silently destroys concurrent
Managers' files; (b) a Manager whose heartbeat repeatedly loses this ref-level race
against other Managers' renewals could be marked falsely stalled (§3.4) despite
being alive. Every state-branch writer — not only the Owner-election path — follows
the same fetch → **read-merge-write the full state** → push-with-lease →
on-rejection-refetch-and-retry loop; §2.4.2's steps above are the specific instance
of that loop for the lease file.

**That whole-ref behaviour is the git BACKEND's, and it stops at the state-layer
interface (D-61).** Consequence (a) is how the git backend implements a write;
consequence (b) does not reach a caller at all, because the backend answers a
rejection caused by somebody else's key by re-merging and retrying rather than by
reporting a conflict. What the interface offers is compare-and-swap on **one key**:
a write conflicts only if that key changed. A store with no shared structure
between keys could satisfy the whole-ref rule only by serialising every write
through a single global version — slower than git, and absurd on its own terms —
which would make D-21's substitutable backends decoration.

⚠ **What a writer does when the state it must merge cannot be parsed.** Read-merge-
write assumes every file in the tree can be read. Another Manager's `claims.json` may
be truncated by a killed push or corrupt on arrival, and the writer still has to
produce a whole tree. Two obvious answers are both wrong: dropping the file destroys
another machine's data to satisfy a merge, and refusing to write at all turns one bad
file into a fleet-wide outage.

The rule is **pass the bytes through verbatim, and fail closed on the decision that
needed them** (D-58). The unreadable file is copied into the new tree unchanged — no
loss, no silent repair, and whoever wrote it can still recover it — while any
operation whose correctness depends on reading it is refused. Granting a claim is the
obvious case: a claim that might overlap an unreadable `claims.json` cannot be shown
safe, so it is declined. Everything not dependent on that file proceeds normally.

This is the same shape as the worker cap failing closed when the sandbox count cannot
be taken (D-29's `CountUnavailable`): **unknown must not be treated as nothing**, and
the response to unknown is to decline the unsafe act — not to halt, and not to guess.
Report it loudly, naming the file and the Manager whose file it is; a writer that
merges an unreadable file silently has hidden the one fact someone needs.

⚠ **This mechanism requires the coordination repo's host to permit force-pushes on
the `state` branch, unconditionally.** Many hosted git providers (GitHub, GitLab)
offer org-wide "no force pushes" protection rulesets, sometimes applied to all
branches by default. If enabled on the coordination repo, every promotion and every
heartbeat push fails **permanently**, not as a race-loser — the entire mechanism is
dead on arrival, loudly rather than as silent split-brain, but dead all the same.
`rite doctor` (§9.8) must probe this (a real trial force-push against the state
branch) as part of Phase-2 setup, and the `state` branch should be excluded from any
CI/webhook trigger the coordination repo's host might apply by default — a lease
renewal every 15 minutes and a heartbeat every 10 minutes × N Managers firing a CI
run against a branch carrying no code is pure noise.

The coordination repo serves two purposes that JIRA cannot — this is why it exists
alongside the ticket backend:

| Concern | Coordination repo (git) | JIRA |
|---------|------------------------|------|
| Rate limit | Unlimited local, push on change only | 500/hr free tier |
| Atomic compare-and-swap | `--force-with-lease` rejection | No equivalent — last-write-wins |
| Offline resilience | Full local copy, sync on recovery | Unavailable during outage |
| Cost | Free (any git remote) | Paid plan for automation features |
| Repo growth | State branch: always 1 commit (§3.3.1) | N/A |

JIRA remains the **ticket backend** — it is where humans read the board. The
coordination repo is the **machine-to-machine channel** for leader election, claims
publication, and heartbeat/lease state. See §3.3 for the full transport design.

#### 2.4.3. Owner state — what must transfer

Anything held only in the outgoing Owner's session is lost on a crash. So Owner state
lives in the coordination repo, not in memory.

| State | Location | Why |
|-------|----------|-----|
| Current lease | `owner-lease.json` | Must be readable by all Managers to detect expiry |
| Manager roster and priority | `config.yaml` (per-project) | Static config, not runtime state |
| Active assignments | Ticket labels in the ticket backend | Already external — labels are the assignment system |
| Claims ledger | Each Manager pushes its claims to the coordination repo | Already specified in §5.2 |
| Pending decisions | Coordination repo or ticket comments | Queued decisions must survive a crash |
| Heartbeat/liveness | Each Manager's last push timestamp | Derivable from git log |

**A new Owner taking over reads the coordination repo and the ticket backend.** It
does not need a hand-off message from the predecessor — it reconstructs the full
picture from durable state. The graceful demotion protocol (step 3 above) exists to
ensure the predecessor's in-flight writes land before the successor reads, not to
transfer state that would otherwise be lost.

### 2.5. Coordinator redundancy pool

⚠ **Superseded framing, 2026-09-10 (D-35).** An earlier draft of this section sized
a pool of idle **Worker** sessions at worker-count × 4, justified by "session
creation needs human approval." That justification no longer holds: **a Manager
session, once a human has started it, creates its Workers' sandboxes from within
itself as ordinary tool calls (§5.3)** — so it spawns a sandboxed Worker on demand
instead of drawing from a pre-warmed pool. Nothing here bypasses a confirmation
step; what changed is that the human approval happens once, at the Manager, rather
than once per Worker. **Worker pooling as originally scoped is cut.**

What survives is narrower: **a small pool of standby Manager/Owner sessions**,
because Managers and the Owner are still ordinary Claude Code sessions that
require a human to start (§2.2, §2.3) — nothing about on-demand Worker spawning
changes that. The pool exists so that if the active Owner or a Manager dies, a
warm coordinator session can take over immediately rather than waiting for a
human to notice and start a new one.

#### 2.5.1. Pool lifecycle

A Manager keeps a small pool of idle **coordinator** sessions, sized per §2.5.4.
Each pooled session is a real, already-approved Claude Code session sitting idle
with no role assigned — not a queued request, an actual session.

⚠ **Filling the pool is an explicit command and nothing else — decided
2026-09-11, D-50.** An earlier version of this section had `rite init` warm the
pool and `rite start` top it up "on every bring-up while the user is present."
Neither was ever built; `fill()` has exactly one caller, `rite pool fill`. **The
spec is what changed, not the code.** Three reasons, in order of weight:

- **Every pooled slot runs `claude`.** A bring-up that spawns sessions as a side
  effect spends quota the user did not ask to spend, and spent quota is the one
  kind of damage in this design that no cleanup reverses (§5.1.1).
- **It contradicts §9.10's idempotence.** `start` is defined as "calling it twice
  does not do the work twice"; a top-up on every bring-up does exactly that.
- **The failure mode is already demonstrated.** Before the ceiling existed,
  `rite pool fill --count 500` started as many sessions as it was asked for, with
  no prompt and no pause (§5.1.1). The remedy was to make the count explicit and
  refuse it above `sandbox.max_concurrent_workers` — which is undone if an
  unrelated command fills the pool on its own.

On promotion or Manager restart, the pool supplies a warm session rather than
waiting for a human to start one. Pool depth decrements, and stays decremented
until someone runs `rite pool fill` (§2.5.7). `rite status` and `rite pool
status` **report** the shortfall and name that command (§2.5.6); neither acts
on it.

#### 2.5.2. Readiness — verify, don't assume

**A pool believed warm but silently dead is worse than no pool** — it gets planned
around as capacity that doesn't exist. **The Manager checks, the pooled session
does not self-report.** An idle session is blocked on input and cannot run a turn
to renew anything itself — so liveness has to be verified from the outside, not
asserted from the inside. Each scheduled cycle (the same one the watchdog, §3.5,
and the heartbeat/lease-renewal loop already run on), the Manager mechanically
probes each pooled session — an OS-level liveness check on the session process,
not a prompt — and stamps a **readiness lease** with the result. A slot whose
lease has not been refreshed within the expiry window drops out of the live count
automatically — the window is `pool.lease_expiry_minutes` (§8.3, default 15).
Dropping out of the live count is a **reporting** change and nothing more: no slot
is retired and no claim is released on this threshold. Retirement is §2.5.10, on a
separate and deliberately more conservative timer. This is deliberately **not** the
Owner's distributed lease
(§2.4.1) — that is a cross-machine compare-and-swap over the coordination repo,
Phase 2; the pool's readiness lease is a local, single-Manager health check that
borrows the same *shape* (expires unless refreshed) without needing any of the
machinery behind it. `rite status` reports live and stale slots separately,
never one number that conflates the two.

#### 2.5.3. Idle cost

**A pooled session costs nothing while idle** — no tokens spent, no turn running.
It is a created session blocked on input, not a loop that polls or "thinks." The
Manager's liveness probe (§2.5.2) must not cost an LLM turn either — it is a
mechanical, external check, not a prompt sent into the idle session — so a pool
of N idle sessions costs the same as a pool of zero.

#### 2.5.4. Sizing — fixed and small, not worker-proportional

**Default: 2–3 standby coordinator sessions**, configurable
(`pool.coordinator_standby`, default `2`). There is no worker-count-scaled
quantity to size any more — drop the `pool.ratio` multiplier and the `pool.max`
ceiling entirely (they existed to bound worker-proportional pools; at 2–3 fixed
sessions, session-list usability is never at risk the way forty idle Worker
sessions once were).

#### 2.5.5. Pooling does not survive correlated failure

Pooling protects against **independent** churn — one coordinator session hanging
while others keep going. It does **not** protect against **correlated**
failure — a quota reset or machine restart that takes every session at once. When
the cause is shared, the pool dies alongside the sessions it exists to replace,
and no multiplier fixes that. `rite pool fill` (§2.5.7) is the real answer:
closing the gap in seconds the moment a human is present, not a deeper pool.

#### 2.5.6. Active management — warn before exhaustion

Pool depth is tracked continuously, on the same scheduled cycle as the watchdog
(§3.5). The Manager warns at a configurable threshold (`pool.warn_threshold`,
default 50% of target depth), stating how many sessions are needed —
*"coordinator pool at 1/2 — 1 more needed, run `rite pool fill`"* — not merely
that the pool is low.

#### 2.5.7. Replenishment

`rite pool fill [--count N]` — tops the pool back up to its configured depth in
one command. `rite status` renders the live/stale split.

#### 2.5.8. Human-gated resources — the general class

Pool depth is not the only thing rite can consume but only a human can
replenish — token quota is the other (§2.6). For anything in this class, the
Manager's job is to forecast, warn early, and make the human's action trivial when
they arrive — not to consume silently and report the outage after the fact.

#### 2.5.9. Context concentration — the constraint on-demand spawning introduces

On-demand Worker spawning removes the pooling problem but creates a different
one: **N sandboxed Workers reporting into one outer Manager session accumulates
in that Manager's context on every turn.** Two mitigations, both load-bearing:

- **Cap concurrent Workers per Manager** to a configurable number
  (`sandbox.max_concurrent_workers` — the same field §2.7.5 and §8.3's config
  example use; named under `sandbox:` because the cap only applies to the
  on-demand-spawned Workers §5.3 introduces); beyond it, queue or spin up a second
  Manager rather than one Manager absorbing unbounded worker traffic.
- **Workers report conclusions, not transcripts** (§3.1.1) — the primary lever,
  since it reduces the *size* of each report rather than just the count of
  Workers reporting.

#### 2.5.10. Retiring a slot — the other half of `fill`

A pooled session that dies *without running its shutdown hook* — killed, crashed,
machine restarted — leaves two things behind: a `pool.json` record that still reads
as capacity, and, if that session had been promoted onto real work, a claims-ledger
entry under its name that reads as live work being done by nobody. The second is the
expensive one: §5.2 refuses overlapping claims, so a dead session's claims keep
refusing live sessions indefinitely, and nothing in §5.2's force-release path tells
the human *which* session to attribute the release to.

**`rite pool archive` retires the record and releases those claims together**,
recording both in `.rite/pool-archive.jsonl`. Doing either alone is what produces a
false alarm: release without retiring and the next `fill` recycles the slot name;
retire without releasing and the claim outlives the only record that could explain
it. `rite pool history` reads that log back — it is the answer to "my claim was here
yesterday and now it's gone".

**Two guards, because this force-releases another session's claims (§5.2):**

- A slot is only touched after the same OS-level probe as §2.5.2 fails
  **continuously** for `pool.archive_after_minutes` (§8.3, default 30) — not the
  readiness lease, which is a reporting threshold and much shorter. A session that
  answers the probe is never archived.
- If the probe **cannot run at all** (no tmux), `archive` refuses rather than
  proceeding. "I could not check" must never read the same as "it is dead" for a
  destructive operation. This is §11's exit-3 principle applied outside the gate:
  a check that could not run is never reported as a check that passed.

**Promotion stays out of scope, deliberately.** §2.5.1's "the pool supplies a warm
session" describes availability, not automation — *who* takes over and what they do
first is a judgement call a human makes by attaching to the pooled session
(`tmux attach -t <name>`). The pool's job ends at keeping a real, named, live
session ready to attach to.

### 2.6. Burn-rate measurement (reporting only — no adaptive control)

**Goal: give the user the data to run continuously across the week and land at
~95% of quota themselves — measurement, not automation.** Throughput is rate ×
uptime; a burst that exhausts quota converts a high rate into zero uptime for
the rest of the week. Observed pattern this project has already hit: two days
hot, then four days stopped. Arithmetic: 95% over 7 days ≈ **13.6%/day**;
measured at ~2.15%/hour running hot, that is roughly **6 hours/day hot**, or
continuous running at about a quarter of that intensity.

⚠ **This arithmetic is illustrative, not something rite computes.** §2.6.2
establishes that nothing local exposes the size of an Anthropic weekly quota, so
rite reports raw token counts and cannot turn them into a percentage of quota.
The percentages above are a worked example of how to reason about the trade, done
by hand against numbers the user supplies. Do not read them as an output.

⚠ **An earlier draft of this section specified an adaptive controller that
automatically throttled Worker concurrency toward the target trajectory. Rejected
— the schedule (§2.7) is authoritative instead, and this section is measurement
only.** Two reasons, recorded because the principle behind them recurs:

1. **Measuring is nearly free; acting is not.** Parsing usage from transcripts is
   a script — the read path below costs nothing. But *deciding* to change
   concurrency requires a Manager turn to reason about the decision, and a
   Manager turn is measured at ~484K tokens (the same cost §3.5's watchdog
   exists to avoid for stall detection). An adaptive controller would spend real
   money making small concurrency adjustments — the mechanism meant to protect
   the budget would itself consume it.
2. **Predictability outranks efficiency here.** A schedule is something a user
   can look at and know what their machine should be doing. An adaptive
   controller underneath produces "why are four Workers running when I said
   three?" — and even with a correct answer, the user has lost the certainty a
   published, stranger-facing tool needs to earn trust. That is a poor trade for
   the efficiency gained.

**The general principle, stated because it will recur elsewhere in rite:**
⚠ **automation earns its place when a decision is frequent or fast-moving.
Schedule tuning is neither** — a user would revisit it weekly at most, with
full information, in seconds. So give the user the data (this section, plus
§2.7.2's coordination-cost instrumentation) and let them set the number (§2.7's
schedule). Instrumentation plus manual control, not a control loop.

#### 2.6.1. Burn rate is measurable locally

Claude Code transcripts on disk (`~/.claude/projects/**/*.jsonl`) carry, per
message: a `timestamp` and a `usage` block with `input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, and `cache_read_input_tokens`. **Verified against a
real transcript** — every usage-bearing line carries all of these plus
`sessionId`, so consumption rate is computable across every concurrent session on
a machine with no external API call, by a plain script — no LLM turn anywhere in
the read path.

**One API response is many transcript lines. Count responses, not lines.** Claude
Code writes one JSONL record per content block: a single assistant response with
fourteen `tool_use` blocks becomes fourteen records, each with its own `uuid` and
`timestamp`, each repeating the same `message.id` and the same whole-response
`usage`. Summing lines therefore bills that response fourteen times. Measured on
a real corpus (one week, 3,416 transcript files): 4,212 of 5,924 responses were
multi-record, and the line sum came to 3.32 billion tokens against a true 1.84
billion — an **80% overcount**, scaling with how tool-heavy the week was, which
is the direction that makes the reading untrustworthy precisely when the machine
is busiest. Deduplicate on `message.id`, keeping the largest `usage` seen (a
record captured mid-stream carries a partial `output_tokens`).

**Cache reads are ~98% of the total, so the total must never be shown
undivided.** On the same corpus, `cache_read_input_tokens` was 98.7% of the
week's tokens; new input, output and cache writes together were 1.3%. Re-reading
cached context is a different economic event from generating new output, and a
bare "1.8 billion tokens" invites reading a cache-read figure as consumption.
Every surface splits the total into cache reads and new tokens.

**Nothing local can turn either figure into a percentage of an account's weekly
quota, and rite must not imply otherwise.** No file under `~/.claude/` exposes
the quota or the weighting it applies to cached reads (`stats-cache.json` is a
stale single-day cache and keeps `cacheReadInputTokens` as its own field), and
`/usage` is reachable only inside an interactive session. `budget.weekly_token_budget`
compares against a figure **the user supplies** — the display says exactly that
and never says "quota".

#### 2.6.2. What it reports

A scheduled process (the same cadence as §3.5's watchdog) that:

- **Reads usage from transcripts** and computes a token rate. This reading is
  **machine-wide** (§2.6.1): every Claude Code session on the machine, from any
  directory, because that is the scope Anthropic's weekly quota is charged at.
- **Surfaces current rate and projected week-end total** in `rite status` —
  the same "forecast, warn early" pattern §2.5.8 already names for pool depth.
- **Reports no percentage, and no early-exhaustion warning.** ⚠ **Amended
  2026-09-10.** Earlier text here required "%/hour against a configured weekly
  budget" and a warning derived from it. Both were implemented by dividing the
  machine-wide total by `budget.weekly_token_budget`, which is one project's
  `.rite/config.yaml` key — different scopes, so the ratio was never a
  quantity. It read `used: 215126.9% of weekly_token_budget` and warned
  "projected to exhaust after 0.0 day(s)", which would fire for every project
  on a working machine regardless of its target.

  **The percentage is not recoverable by measuring harder.** Per-project
  attribution does not exist: the quota is account-wide, a session can be run
  against a project from any working directory, and per-worker sessions have
  their own transcript directories. Do not reintroduce a ratio between these
  two numbers.

  `budget.weekly_token_budget` remains valid configuration — it is the user's
  own per-project allocation target, and the user manages quota through the
  schedule (§2.7), per this section's rejected-controller note. rite records
  it and names it; it does not measure against it.

**It takes no action of its own.** The user reads the rate and the projection
and adjusts their own schedule (§2.7) — there is no path from this section back
into Worker concurrency.

#### 2.6.3. Success metric — measure, don't assume

**Steady beats bursty is a hypothesis, not a conclusion.** The metric that
governs it: **weekly work delivered, net of rework** — merged tickets minus
tickets reopened or redone. A raw ticket-merged count rewards bursting even when
the work is wrong; rework net of that count does not. (Concrete case this project
already produced: one component shipped three separate times in states that
passed typecheck, the full test suite, and two review stages while being entirely
non-functional — a raw count would have shown three "done" tickets for zero real
delivery.) **Build the measurement first.** If a full week of data says bursting
wins, that is an argument for the user to change their own schedule (§2.7) — not
for reintroducing automatic control.

### 2.7. Worker scheduling

**A user-defined schedule per project: which hours, how many active Workers, with
0 meaning rite is off for that project during those hours.** The control surface
is uptime (`rite start`/`rite stop`) plus Worker count — set by the user, from
data (§2.6, §2.7.2), not computed automatically (§2.6's rejected-controller note
is the reasoning for why this is manual).

```yaml
schedule:
  timezone: Europe/Warsaw          # OPTIONAL since 0.5.0 — defaults to the
                                   # machine's own clock, because "nine to
                                   # five" means the operator's day. `rite
                                   # start` prints which clock it resolved,
                                   # since a container with no zone set
                                   # silently becomes UTC and shifts the
                                   # whole day with every number still
                                   # looking right.
  windows:
    - days: "Mon-Fri"              # OPTIONAL — omitted means EVERY day,
                                   # which is what every window meant before
                                   # 0.5.0. Ranges wrap: "Fri-Mon" is four
                                   # days, as "18:00-09:00" wraps midnight.
      hours: "09:00-17:00"
      workers: 3
    - days: "Mon-Fri"
      hours: "17:00-09:00"
      workers: 1
    - days: "Sat-Sun"
      hours: "00:00-23:59"
      workers: 0                   # off at weekends — a clean stop, not a
                                   # kill (§2.7.3)

# Time covered by NO window is 0 Workers — not the flat cap, not unbounded.
# Documented here because it is the value most projects meet first and never
# configure. Since 0.5.0 `rite sandbox start` REFUSES outside the window and
# names when it next opens; before then the schedule informed the loop's
# verdict and stopped nothing.
                                    # spans midnight: end < start means "through
                                    # midnight into the next day" (see parsing
                                    # rules below)
```

**Parsing rules, stated explicitly rather than left inferable from the example:**

- **Midnight wraparound:** an `hours` range where the end time is earlier than the
  start (`"18:00-09:00"`) spans midnight into the next day. `"00:00-24:00"` (or
  simply omitting `windows` and giving a single top-level `workers: N` for the
  whole day) is the syntax for "all day, one constant count" — the "three for
  twenty-four" comparison case in §2.7.1.
- **Hours not covered by any window default to `workers: 0`** — the same
  fail-safe default §2.7.3/D-46 uses for an explicit zero, so a gap in the
  schedule behaves like an intentional off-window rather than an undefined or
  inherited count. `rite doctor` (§9.8) validates full 24-hour coverage and warns
  on gaps, so a silent gap is visible even though it's handled safely.
- **`rite schedule set <hours> <n>` (§9.1) is an upsert, not a whole-list
  replace.** It sets the given range to `n`, splitting or trimming any existing
  window it overlaps, and leaves the rest of the `windows` list untouched —
  building the two-window example above is exactly two calls
  (`rite schedule set 09:00-18:00 3`, `rite schedule set 18:00-09:00 0`), each
  independent of the other.

#### 2.7.1. Why coordination cost isn't linear, and why rite doesn't pick the number

⚠ **Phase 1.**

**The mechanism is combinatorial, not the measured curve — be precise about
which claim this is.** More Workers means more *pairs* that can collide (claims
overlap, merge conflicts, review bandwidth) — the number of pairs among N
Workers grows as N², which is why contention should be expected to grow faster
than Worker count itself. Nine parallel Workers for eight hours a day could
plausibly beat three for twenty-four, or the reverse could be true; it depends
on how separable the codebase is, and **that varies by project**, which is why
this is a user decision rather than something rite computes.

**Only two points have actually been observed, not a curve — say so plainly.**
At 25 concurrent sessions, the board jammed completely — four sessions in a row
reported nothing safe to start, because every ticket was blocked by another
session's claims. At 3, it ran fine. Two points establish that contention is
non-trivial somewhere between 3 and 25; they do not establish the shape of the
curve between them. It could be a gradual knee (§2.7.2's instrumentation would
show it as a rising rate a user can watch and stop before), or it could be a
harder cliff from claims-serialization — one hot, contended path stalling many
sessions at once, discontinuously, rather than smoothly. **Users should not
assume a smooth, extrapolatable curve from §2.7.2's numbers alone** — a good
rate right up to a step-change is a real possibility this section does not rule
out, and the instrumentation is there to catch the trend early, not to promise
the trend is linear-enough-to-predict.

#### 2.7.2. Instrument coordination cost so users find their own knee

⚠ **This is arguably more valuable than the schedule itself — the schedule is
the control, this is what makes it usable with data instead of a guess.**
Three sub-metrics, each with its own collection point — they are events, not a
poll, unlike §2.6.2's burn-rate reporting (which genuinely needs periodic
re-computation of a rate from an ever-growing transcript log):

- **"Nothing safe to start" frequency** — counted at orientation time (§9.10):
  when the orientation table reaches the "`scheduled` tickets in the backlog"
  row and finds tickets present but **none claim-safe** (a state that row must
  distinguish from both "picked one and started" and "board is empty" — see the
  fix to §9.10's table below), the Manager increments a counter, written to the
  same local `.rite/` state the heartbeat/pool mechanisms already use. This is
  the metric with the most direct evidentiary basis (§2.7.1's "four sessions in
  a row" observation) and the cheapest to collect, since orientation already
  runs on every `start` and every scheduled re-check.
- **Merge conflicts per merge** — counted at merge time, incremented whenever a
  Worker's PR merge (§9.4.3's ticket workflow) required manual conflict
  resolution rather than merging clean.
- **Claim contention** — §5.2 does not currently give a Worker anything to wait
  on: a claim attempt is refused outright, not queued (§5.2's own wording). This
  metric is therefore **not** "time blocked" (there is no specified entity that
  waits) but **count of refused claim attempts per scheduled cycle** — a Worker
  or the Manager on its behalf records a refusal when it happens; nothing here
  requires inventing a retry-and-wait policy that doesn't otherwise exist in the
  spec. See §5.2's own addition below for the corresponding refusal-count
  bookkeeping.

Surface all three in `rite status` alongside the burn-rate numbers (§2.6.2). A
user watching their own project's "nothing safe to start" rate climb as they
raise the Worker count has found their knee empirically, without guessing.

⚠ **Shipped status: one of the three has a collection point.** Claim contention
(`refused_claims`) is instrumented and counts real events. The other two are
specified above but **not yet wired**, and each waits on a mechanism that does not
exist yet: "nothing safe to start" needs an orientation step that queries the ticket
backend, which `rite start` does not do; merge conflicts need a merge step rite owns,
and rite owns none. `rite status` therefore labels those two `not instrumented yet`
rather than rendering a bare `0` — an unqualified zero claims a measurement that was
never taken, and a user cannot tell "no merge conflicts occurred" from "this isn't
counted". Drop the label when a collection point lands; do not wire a counter to a
number it cannot actually observe.

#### 2.7.3. Zero Workers is a clean stop, not a kill

A scheduled window with `workers: 0` runs the **same handover `rite stop`
already specifies** (§9.10) — write the handover, return tickets to the pool,
release claims — not a process kill. The alternative strands work every evening
exactly the way an uncommitted working tree does (§2.1, D-33): the schedule
existing at all must not reintroduce the failure mode §2.0's durable-state rule
exists to close.

**This is a FOURTH handover trigger, distinct from §9.10's three (clean `stop`,
heartbeat timeout, lease expiry) — named as a fourth here rather than silently
folded into "clean stop," because it has a property the other three don't: it
fires unattended, on a timer, with no human watching at the moment it happens.**
A manual `rite stop` mid-task is a judgement call the human made; a schedule
boundary at `18:00:00` is not. §2.1's "commit early and often" (D-33) is what
bounds the damage — a Worker mid-ticket at the boundary loses at most the work
since its last checkpoint commit, never an uncommitted whole task — but the
window boundary does **not** wait for an in-flight Worker to reach a checkpoint
the way §2.4's graceful demotion waits for the Owner to finish "one operation"
(§2.4, §2.4's precise definition) before handing over. The asymmetry is
deliberate: Owner handover protects against split-brain (two Owners acting at
once), which justifies waiting; a Worker stopping slightly early costs at most
one checkpoint's worth of re-work next time it's picked up, which does not.

#### 2.7.4. The hub checks aggregate load across projects

⚠ **Phase 1 for a single machine coordinating multiple projects (§8.9); the
"one Manager, one Claude account" framing (§2.2) is what makes this a shared
resource in the first place — no cross-machine coordination involved.**

Three projects each scheduling five Workers is fifteen concurrent — enough to
blow the weekly budget even though each project's own schedule looks sane in
isolation. Only the multi-project hub (§8.9) sees the total across all
registered projects, and it is the clearest concrete justification for that hub
existing at all, beyond thin routing. **It warns at configuration time** — when
`rite projects add` or a schedule edit would push the aggregate over budget —
not after the fact when quota is already gone.

**The ceiling is expressed in quota terms, not a bare Worker-count sum, because
Worker count alone doesn't predict quota risk** (Manager-turn cost is roughly
per-decision, not per-Worker-hour — §2.6's own reasoning for why the burn-rate
controller was rejected applies here too: more Workers doesn't linearly mean
more spend).

⚠ **This warning is a sum of configured intentions, not a projection of measured
use — and it cannot become one. Amended 2026-09-11, D-50.** An earlier version of
this paragraph had the hub read "each registered project's own
`budget.weekly_quota_pct` *and its recent burn-rate history* (§2.6.1)" to project
what all projects' schedules would together consume. The second half of that is
not implementable, and the reason is already recorded one section up:
**per-project attribution does not exist** (§2.6.2). The quota is account-wide, a
session can be run against a project from any working directory, and per-worker
sessions write their own transcript directories, so `budget` can report one
machine-wide rate and no per-project rate at all. There is nothing to sum.

**What the hub actually does, and what it is good for:** it sums the
`budget.weekly_quota_pct` each registered project *explicitly sets* — projects
that never set the key are named and excluded rather than counted at their
dataclass default — and warns at `projects add` time when the total crosses the
threshold. Three projects each configured for 50% of the week sum to 150%, and
that is a real misconfiguration, visible before any quota is spent. The warning
says in its own text that it is a sum of configuration rather than a measurement,
and points at `rite budget` for the real machine-wide rate.

This is a heuristic described as a heuristic. It is **not** a placeholder waiting
for better data, and no future measurement will upgrade it — do not re-specify the
burn-rate projection here.

`~/.rite/dispatch/config.yaml` (§8.9) carries the hub-level warning threshold
alongside the "default alias, notification prefs" settings already named there.

#### 2.7.5. Non-zero transitions, and the schedule's relationship to the sandbox cap

⚠ **Phase 1.** §2.7.3 specifies the zero case precisely; the general case —
one window's Worker count differing from the next, neither of them zero — needs
its own two rules, stated explicitly rather than left to guesswork:

- **A rise (e.g. 2→5) raises a ceiling; it does not spawn anything.** ⚠ **Not
  built, and deliberately not on the roadmap — decided 2026-09-11, D-50.** The
  scheduler tick acts only on a genuine transition INTO zero (`last_count != 0
  and current_count == 0`); a rise triggers nothing. Read as an unmarked
  intention, the older wording said rite starts Claude sessions on a timer —
  which is precisely the behaviour this design does not want to have. The
  schedule is a **cap the user sets**, and Workers start when a Manager has safe
  work to give one, never because a clock crossed a boundary with nobody
  watching. §9.12 states the general rule: nothing rite runs unattended starts a
  Claude session. Treat this bullet as describing the ceiling's new value, not a
  ramp-up mechanism to be built later.
- **A drop (e.g. 5→2) does NOT force-stop the excess immediately.** In-flight
  Workers are allowed to reach their next checkpoint commit (§2.1, D-33) and
  then are not replaced when they finish — the same "let one finish without
  replacing it" throttling language the rejected burn-rate controller used
  (§2.6) turns out to be exactly the right shape for a schedule-driven decrease,
  just triggered by the schedule instead of a burn-rate target. No Worker is
  selected as a "victim" and interrupted mid-task; the count converges downward
  as tasks complete.

**`schedule.windows[].workers` is a target the schedule authorises, capped by
`sandbox.max_concurrent_workers` (§2.5.9) — the schedule can never authorise
more concurrent Workers than the context-concentration cap allows.** A schedule
window requesting more than the sandbox cap is a configuration error: `rite
schedule set`/`rite doctor` refuse it rather than silently clamping, so the
mismatch is visible at configuration time instead of producing a schedule that
quietly never reaches its stated count.

---

## 3. Communication

### 3.1. Manager ↔ Owner channel

Carries:

- **Existence and roster:** a Manager announces itself and its Workers on startup;
  the Owner knows the full topology.
- **Blockers, both ways:** a Manager surfaces blockers it cannot resolve; the Owner
  pushes decisions or re-assignments down.
- **Project-wide and feature-wide decisions:** so Managers working the same feature
  do not diverge.
- **Status reports doubling as heartbeats.** A Manager that stops reporting gets no
  new work assigned. The Owner's Dispatch asks the human what to do with
  already-assigned tickets.

⚠ **Phase 2.** This channel is Manager↔Owner, cross-machine — it does not exist
until a second Manager does. **Do not confuse it with Manager↔Worker heartbeats**
(§9.10, P1.11, already built): a single Phase-1 Manager still tracks its own
Workers' liveness and surfaces Worker stalls to the human — that relationship is
real today and unrelated to this section.

#### 3.1.1. Report shape: conclusions, not transcripts

**A status report is a conclusion, not a transcript.** What was done, what's
blocked, what's next — never the reasoning or the tool-call log that produced it.
This is a hard requirement, not a style preference, for a reason specific to how
LLM context works: **cost is turns × context size**, and a coordinator session
re-reads its *entire* accumulated context on every subsequent turn. Measured on
this project's own Dispatch session: **~500K tokens re-read every turn, ~98% of
total spend** — and that accumulation comes overwhelmingly from what gets reported
*up* into it, not from any static instruction file (contrast §8.6's 74 KB
`CLAUDE.md` measurement — a different, much smaller problem; see §8.6.1). A Worker
or sub-Manager that forwards its full transcript into its Manager's context
multiplies that cost by every reporter doing the same thing into the same session
— this is the mechanism behind §2.5.9's "context concentration" and the reason
§8.9's multi-project hub must stay thin.

Concretely: a report is a few sentences — outcome, blocker if any, next step —
never a pasted tool output, never "here's what I did" followed by the work
itself. The generated Worker/Manager `CLAUDE.md` (§9.4) and
`.claude/commands/ticket.md` state this explicitly as a reporting rule, not
something left to be inferred from the channel's existence. **This is the same
principle behind the watchdog (§3.5): keep the expensive component — an LLM
reading full context — out of the loop unless judgement is genuinely required.**
A report a human or an Owner must read with judgement is not a candidate for
compression; a heartbeat that only needs "still alive, still working" is not a
candidate for an LLM-authored transcript in the first place.

### 3.2. Transport

**Python over REST.** No MCP dependency in the product.

Every MCP call costs an LLM turn, and heartbeats/polling/labels are nearly all the
traffic. MCP is also rate-limited at 500 requests/hour on the free tier and may
re-prompt for OAuth. REST calls from Python are zero-LLM-cost, unlimited, and
predictable.

The transport implementation talks directly to the ticket backend's REST API (JIRA,
GitHub, etc.) using standard HTTP libraries. A generic `query()` function (§6.1) recovers
ad-hoc flexibility for JIRA's JQL and GitHub's search qualifiers without building a full
query DSL.

### 3.3. Coordination transport

⚠ **Phase 2.** The coordination repo does not exist in a single-Manager Phase 1
setup — there is no cross-machine state to coordinate. Everything below describes
Phase 2 machinery.

The coordination repo (§2.4.2) carries two fundamentally different kinds of data:

- **Ephemeral state** — who is online, who holds the Owner lease, last-seen times,
  current claims. High frequency, **no historical value**, needs atomicity and expiry.
- **Durable messages** — decisions, blockers, handovers. Low frequency, **history
  matters** — a human might later ask "why did that happen?"

Naively committing everything produces ~864 commits/day for three Managers with
10-minute heartbeats (~315,000/year). The git log becomes unreadable and fresh clones
slow to a crawl. The growth comes from conflating the two kinds.

#### 3.3.1. State branch — always exactly one commit

A dedicated branch (e.g. `state`) that is **force-pushed with `--force-with-lease`**
on every update. Each push rewrites rather than appends, so the branch never grows
past one commit.

Critically, `--force-with-lease` **preserves compare-and-swap**: it fails if the
remote's ref has moved since the last fetch. This is precisely the atomicity Owner
election needs — two simultaneous claimants produce one success and one explicit
failure, same guarantee as non-fast-forward rejection but without accumulating
history.

The state branch contains:

| File | Content | Update frequency |
|------|---------|-----------------|
| `owner-lease.json` | Current Owner, acquired/expires timestamps, priority | Every lease renewal (~15 min) |
| `managers/<name>.json` | Manager status, last-seen, worker list | Every heartbeat (~10 min) |
| `claims.json` | All published claims across all Managers | On claim/release |
| `promotion-request.json` | Graceful demotion request from returning Manager | Rare — only on failback |

**`promotion-request.json` — proposed shape (P2-0d, not yet a decision):**
`requester` (the returning Manager), `requested` (ISO-8601, audit only) and
`incumbent` (the lease holder the request was addressed to). The incumbent acts on
a request only when `incumbent` names the current lease holder; a request addressed
to an earlier Owner is left over and ignored, which needs no clock. No priority is
carried: who outranks whom is the order of `coordination.managers` (D-60). Unknown
fields round-trip and unreadable bytes are "could not read", as for every state file
(D-58).

**Nothing on this branch has historical value BY DESIGN — but the old commits are
not actually erased, and the claim should not be read as a confidentiality
guarantee.** A force-push replaces the branch tip; it does not scrub the overwritten
commit. Locally, every Manager's own clone retains a reflog entry on its remote-tracking
ref each time it fetches (default retention ~90 days via `gc.reflogExpire`), so
historical lease/claims snapshots are recoverable from any machine that ever fetched
them, for months. On the remote, the orphaned object persists until garbage
collection (`gc.pruneExpire`, commonly ~2 weeks locally; many hosted providers retain
dangling objects considerably longer). "Nothing has historical value" means "not
*intentionally* retained, and nothing a reader should rely on being there" — it does
not mean "no trace exists anywhere." If lease or claims files ever carry anything
sensitive, this retention footprint is real and unaddressed; today they carry only
Manager names, timestamps, and paths, which is why this has not mattered yet.

#### 3.3.2. Message log on `main` — ordinary commits

Decisions, blockers, handovers and other audit-worthy events are ordinary commits on
`main`. Low volume — a few per day at most — and the history is what you would
actually want to read later.

| Content | Example |
|---------|---------|
| Decisions | "OD-3 answered: use polling, not webhooks. Reason: ..." |
| Blockers surfaced | "Worker alpha blocked on missing credentials for staging" |
| Handovers | "Manager-beta stopped: 2 tickets returned to pool, claims released" |
| Promotion events | "Manager-alpha promoted to Owner (manager-beta lease expired)" |

**Commit convention — proposed (P2-0d, not yet a decision).** The subject stays
human, as in the examples above; what a machine needs is carried as git trailers in
the final paragraph — `Rite-Event: <kind>` plus `Rite-<Field>: <value>` lines (for a
promotion: `Rite-Manager`, `Rite-Previous-Owner`, `Rite-Reason`). A commit without
`Rite-Event` is an ordinary commit, not a message. Trailers survive a reworded
subject, are read by `git interpret-trailers` without rite, and — because values are
written on one line and only the final paragraph is read — cannot be forged from a
subject, body or value. An unknown kind from a newer rite is preserved, not refused.

#### 3.3.3. The boundary — what lives where

Get this wrong and either the repo grows again, or something worth auditing is lost to
a force-push. The rule: **anything derivable or momentary is state; anything a human
might later ask "why did that happen?" about is a message.**

| Data | State or message? | Reason |
|------|-------------------|--------|
| Owner lease (who, until when) | **State** | Momentary — only the current lease matters |
| Manager heartbeat / last-seen | **State** | Derivable from the most recent push |
| Published claims | **State** | Momentary — a released claim has no value |
| Promotion request | **State** | Momentary — only the pending request matters |
| Promotion event (who replaced whom) | **Message** | Audit trail — "why did the Owner change?" |
| Decision and its rationale | **Message** | Audit trail — "what was decided and why?" |
| Handover comment | **Message** | Audit trail — "what was the state when this Manager stopped?" |
| Blocker raised | **Message** | Audit trail — "what blocked progress on this ticket?" |
| Claim contention (attempted and failed) | **Message** | Audit trail — "why didn't this worker start?" |

#### 3.3.4. Alternatives considered

| Alternative | Strength | Why it lost |
|-------------|----------|-------------|
| **JIRA coordination board** | Already exists if you use JIRA | No atomic primitive; consumes the 500/hr rate limit; heartbeats-as-tickets is grotesque. Dropped — JIRA remains the *ticket* backend. |
| **Redis with TTL keys** | The best technical fit — leases with expiry are natively what Redis does, sub-second latency | Paid infrastructure, another credential, and a hard dependency for every rite user. Wrong default for an open-source tool. |
| **S3/GCS conditional writes** | Provides CAS, cheap, durable | Requires a cloud account. Same objection as Redis for a zero-infrastructure default. |
| **Dedicated REST service** | Full control | A service to run and keep running. rite's value is coordination without infrastructure. |

#### 3.3.5. State-layer interface

**The state layer is behind an interface**, exactly as the ticket backend (§6.1).
The default implementation is git (state branch + message log). The interface
exposes:

- `read_state(key) → value` — read a state file.
- `write_state(key, value, expected_version) → success|conflict` — CAS write.
- `append_message(content)` — durable, ordered, auditable.
- `read_messages(since) → list` — read the log.

A team that outgrows git's latency can substitute Redis (for state) or a database
(for messages) by implementing this interface. rite works with **zero infrastructure
by default** — any git remote — and scales if the team needs it.

### 3.4. Heartbeat, leases, and stall detection

**Managers use heartbeats; the Owner uses a lease.** The distinction matters.

**A stalled Manager is dealt with ONCE, and that is a property rather than a
check.** The Owner hands the stalled machine's work back and then expires the
claims it was holding, in that order — the handover needs those claims to know
which tickets to comment on. Because the expiry removes them, the next pass
finds nothing to hand over and writes nothing at all: no second handover
comment, no second audit record, no guard flag to get out of step with
reality. Measured rather than reasoned: 200 consecutive Owner passes against a
permanently stalled Manager produced one handover, a message log flat at three
entries, and no drift in how long a pass took (0.611s over the first twenty,
0.597s over the last twenty).

The order is therefore load-bearing. Expiring first would silently reduce the
handover to nothing; handing over without expiring would repeat it for as long
as the machine stayed down. An implementation that adds a "already handled"
flag instead has reintroduced state that can disagree with the claims it
describes.

⚠ **This section, as written, is Manager↔Owner (Phase 2) — but the SAME shape
already applies one level down, in Phase 1, and is built (P1.11).** A single
Manager tracks its own Workers' heartbeats exactly as the Owner tracks Managers'
here: a Worker that misses N consecutive heartbeats is marked stalled and surfaced
to the human. Read every "Manager"/"Owner" pair below as also meaning "Worker"/
"Manager" one level down — the mechanism is identical, only the roles shift.

A **Manager's heartbeat** is its status report pushed to the coordination repo. The
Owner monitors these; a Manager that misses N consecutive heartbeats is marked stalled.
The Owner's Dispatch surfaces the stall to the human with the Manager's last known
state and its assigned tickets.

The **Owner's lease** is a stronger guarantee — it expires unless actively renewed
(§2.4.1). Under a heartbeat-only model, a hung Owner that still looks alive holds the
role indefinitely; under a lease it loses the role automatically and the next Manager
in priority order promotes. The dirty path is the common one — a session gone while
its claim record still reads as live, rather than one that exits cleanly. §2.4.1
quantifies it: 44 of 46 force-releases in the upstream's ledger record the holder as
gone.

A heartbeat timeout, a lease expiry, and a clean `rite stop` produce **identical board
state** — see §9.10. The difference is only who writes the handover: `stop` writes its
own; the Owner writes it on the Manager's behalf on heartbeat lapse; the next-in-line
Manager writes it on the Owner's behalf on lease expiry.

### 3.5. The watchdog — cheap liveness checks, no LLM in the loop

**Phase 1, and urgent — this replaces an expensive mechanism already in use.**
Today, checking "is anything stalled" wakes a full Dispatch session, which re-reads
its entire accumulated context to answer a question that needs no judgement.
Measured: **~484K tokens per wake-up**, and most wake-ups conclude nothing has
changed. This is the same waste §3.1.1 names from the other direction — an LLM
turn spent on a question a deterministic check could answer.

**Specification: a plain script, not a Claude session, runnable every
`watchdog.interval_minutes` (§8.3, default 5), that checks session liveness and
wakes the Manager only when something actually needs judgement** — a stalled session (missed heartbeats past `heartbeat.
stall_threshold`, §8.3), a blocker recorded in the outbox, or a decision queued
with no response. No LLM call anywhere in the check itself; it is a cron-style
process inspection plus a read of the same heartbeat/outbox files P1.11 already
writes. On the common case (nothing wrong), it exits having spent zero tokens. On
the rare case (something needs a human or a Manager's judgement), it starts or
signals the Manager session — the expensive component is invoked only when its
judgement is genuinely required, never to confirm the absence of a problem.

**The general principle, stated once because it recurs:** ⚠ **keep the expensive
component out of the loop unless judgement is genuinely required.** This watchdog
is one instance; §3.1.1's "conclusions, not transcripts" is another — both trade an
LLM's judgement for a cheaper mechanism everywhere the judgement isn't actually
needed, and reserve the LLM for the cases where it is.

---

## 4. Expertise routing

Members declare expertise from an open set — engineering, business, UI/UX, plus
whatever tags the project needs (`infrastructure`, `frontend`, `data`, or a domain
tag like `compliance` for a project that has a compliance question to route).

When a decision needs routing:

1. The requesting session describes the decision domain.
2. rite matches the domain against declared expertise.
3. **Ties broken randomly** among equally-qualified members.
4. **No match falls back to the Owner** — and because the Owner is the
   highest-priority active Manager (§2.4), this inherits a sensible chain
   automatically from whatever order the team put its Managers in. No separate
   fallback list needed. The priority list is an org chart of the team's own
   choosing; rite does not assume a particular set of roles.

Timeout and reroute to the next-best match when the chosen expert is unavailable
(machine off, no heartbeat). Block only if the Owner explicitly marks a decision as
requiring a specific person.

---

## 5. Workspace isolation

### 5.0. What rite may prevent, and what it may not

The principle governing this section.

**rite cannot prevent a user from making a bad choice, and should not try.**
Someone may run five Claude Managers and exhaust a weekly quota in a day.
Someone may point a Manager at a small local model and get results that do
not clear the project's bar. Both are choices, and refusing them would be
wrong for the person whose case is legitimate — deliberately spending a
week's quota in a day because that is what the week needs, or deliberately
running a small model because the task is small.

**What rite owes is that nothing happens by accident.** That is narrower than
safety and more achievable.

#### Three questions, not one

An earlier draft asked a single question — accident, or choice? — and claimed
it settled the class. Review showed it does not: applied to `rite pool fill
--count 500`, which the user typed explicitly, it says "choice, therefore
never refuse", and rite refuses. The dichotomy was hiding a third case.

**1. Is it an accident?** Then it is rite's, and rite's job is to make it
impossible — not to warn about it. A Manager writing into another Manager's
files is the clear case. The properties in §5.1.1 are mostly of this kind.

**2. Does it contradict a choice the user already made?** Then rite honours
the earlier one and refuses, and this is not paternalism: rite is not
choosing for the user, it is declining to let an unstated intent silently
override a stated one. `--count 500` is refused because it exceeds
`sandbox.max_concurrent_workers`, a number the user wrote down. The remedy is
always to change the earlier choice, and the refusal says so. Clamping would
be the paternalistic option — it substitutes rite's number for the user's
while appearing to comply.

**3. Otherwise it is a free choice, and rite's job is to make its cost
visible** — not to refuse it. Five Managers is this case. So is a weak
engine.

The containment requirement falls out of the first and third. **A Manager
that fails to deliver its work** is the third case, and what follows is a
coordination matter between that Manager and the Owner — rite reports it and
does not arbitrate. **A Manager that writes into another Manager's files** is
the first, and rite's bug. The one is fairness and belongs to the people
involved; the other is containment and belongs to the tool.

#### Costs that arrive late

The third case needs care wherever the consequence is separated from the
choice in time. Five Claude Managers do not produce a bill; they produce a
result a week later, attributed to nothing. By then "it was your choice" is
true and useless.

**Where the cost of a choice arrives long after the choice, the equivalent of
refusing is stating it at the moment of the decision.** A line at start time
saying what this configuration is likely to consume stops nobody, and
stopping people is not the goal. It stops them being surprised.

This is why §9.14.5 refuses to default its ceiling. ⚠ Note what that refusal
is and is not: it is the second case, not the third — rite declines to pick a
number the user did not pick, and the number then bounds them. §9.14.5 also
records that a count ceiling does not bound cost, so it is cited here as the
right shape rather than a solved problem.

#### When a choice stops being only the chooser's

"Try a small local model and see" is a reasonable experiment. It stops being
only the user's at the moment its output enters the codebase, because the
cost then falls on later readers.

**What keeps it an experiment rather than a quiet degradation is a
terminating check that refuses work which is not good enough, whatever
produced it.** A check that asks only whether the work meets the standard,
and never which engine wrote it, lets anyone try a weak model freely: the
worst outcome is wasted effort rather than a worse repository.

**This is the argument for that check being structural rather than a
setting.** ⚠ **Today it is not.** §7.1's review templates are plain Markdown
that a project may rewrite, and rite reads none of it at runtime — it is
instruction to a session, not behaviour rite enforces. A check that can be
edited away will be edited away by exactly the configuration most likely to
need it. Engine quality is the user's to choose; merged quality is not solely
theirs, and that gap is unclosed.

⚠ **This principle does not permit refusing something because the user might
regret it.** If an argument for a refusal cannot name either the accident it
prevents or the earlier choice it honours, it is an argument for a default, a
warning, or a line of output — most often for stating a cost the user cannot
otherwise see.

### 5.1. Physical isolation

Each Worker has its own directory with its own repo checkouts. No two Workers share a
working directory. This eliminates the class of collision where two sessions edit the
same file in the same checkout.

### 5.1.1. Blast radius — what rite may not do

The bounding rule for pointing rite at a live commercial repo. These are
properties, not features, and `tests/test_blast_radius.py` asserts them.

**rite never writes to a remote and never rewrites history.** No `git push`,
`--force`, `reset --hard`, `rebase`, `filter-branch`, `update-ref` or
`cherry-pick` appears in any git invocation in the package; the only
repository-mutating verbs are `clone`, `fetch --prune`, `checkout`,
`checkout -b` and `merge --ff-only`, and every one of them runs against a
worker's own clone under `workers/<name>/`, never against the project's
checkout. `gh` is confined to the issue board — never `pr`, never `repo`.
Merging and pushing stay a human action, so the blast area is new changes
rather than project history. A test enumerates every `["git", ...]` argument
list rather than grepping file text, because docstrings legitimately discuss
pushing.

**A safety property may fail closed, never open.** Two counters govern
whether more work may start, and neither may answer "no problem" when it
cannot answer at all. `count_active_sandboxes` returns `CountUnavailable`
rather than a plausible `0`, and its caller refuses to start. `pool fill`
refuses a target above `sandbox.max_concurrent_workers` rather than clamping
to it — every pooled slot runs `claude`, and `rite pool fill --count 500`
once issued 500 `tmux new-session` calls without a prompt. Spent quota is the
one kind of damage here that no cleanup reverses.

**Corrupt state is not empty state.** `.rite/` files are written through
`write_atomic` (temp sibling, `fsync`, `os.replace`) so an interrupted write
cannot leave a prefix on disk, and read through `read_json_state`, which
raises rather than returning a default. The two were one bug: a half-written
`claims.json` parsed as zero claims, `rite status` reported "no active
claims", and the next `rite claim` handed a second worker a file the first
still held — exit 0, no message. An empty ledger means "nothing is claimed";
an unreadable one means "I do not know what is claimed", and rendering them
alike destroys the exclusion guarantee this section exists to provide.
Recovery is deleting the named file, never hand-editing it, and the error
says so.

**Claims do not expire, and their age is always shown.** rite cannot
distinguish a crashed session from a session thinking hard, so an automatic
release would sooner or later steal a path from a live worker — worse than
leaving a stale one. `rite release --force` stays a human action requiring
`--by` and `--reason`, written to an append-only audit trail; nothing
automated calls it. What `rite status` must do is render age and flag
anything past twelve hours, so a days-old claim cannot read as live.

**No unattended call waits forever.** Every `subprocess.run` in the package
carries a timeout, and each one degrades to that caller's existing failure
value rather than an exception — a wedged `yoloai` or `tmux` under a
`scheduler-tick` running from cron must not become an accumulating pile of
stalled processes.

### 5.1.2. The scheduler tick — one at a time, and a log that ends

`scheduler-tick` runs unattended on a fixed interval. Two properties keep an
unattended run from degrading over time; both are asserted in
`tests/test_scheduler_lock.py`.

**One tick at a time, guarded by a pid rather than a timeout.** A lockfile in
`.rite/scheduler.lock` records the owning process. "Is the previous tick still
running" is a question the OS answers exactly on the single machine Phase 1
targets (`os.kill(pid, 0)`), whereas a duration has to be simultaneously short
enough not to wedge the scheduler after a kill and long enough not to steal a
slow tick's lock. A tick that finds the lock genuinely held reports the holder
and exits 0 — contention is normal, but a silent skip would be the same defect
as the empty scheduler log, indistinguishable from a scheduler that never
fired. A lock whose owner is gone is reclaimed and the reclaim is reported.
The lockfile is published with `os.link` from an already-written temp file,
not `O_CREAT | O_EXCL`: the latter makes the path exist before its contents
do, and twenty concurrent real ticks reliably produced a reader that saw the
empty window and took a held lock.

This is the stale-claim rule from §5.2 and it gets the opposite answer on
purpose. A claim guards a human's in-flight edits and rite cannot tell a
crashed session from a thinking one, so claims never auto-expire. A tick lock
guards a five-minute mechanical job owned by an observable local process, so
reclaiming it is safe where releasing a claim is not.

`_PID_REUSE_BACKSTOP_SECONDS` is the only duration involved, and only because
a recycled pid landing on an unrelated long-lived process would otherwise
wedge the scheduler permanently. It is derived, not chosen: a test asserts it
exceeds the longest bounded wait anywhere in the package, so it can never fire
on a tick that is merely slow.

**The log is bounded.** Every tick writes at least one line by design, so
`.rite/scheduler.log` grew without limit — ~105,000 lines a year at the default
cadence. It rotates at 1 MiB keeping three archives, by copy-and-truncate
rather than rename: rite does not own the file handle, since cron appends
through a shell redirect and launchd holds the path open as `StandardOutPath`,
and renaming out from under either leaves the writer filling the rotated inode
— rotation that appears to work while the log grows forever somewhere else.
Both backends are asserted to target the same file the rotator maintains,
because a one-sided fix is invisible from whichever platform did it.

### 5.2. Claims system

Before starting work, a Worker claims the paths it will touch. Claims are checked at
file or directory granularity — never whole-repo, which serialises unrelated work.

The claims ledger is the source of truth for "who is working on what." It supports:

- `claim` — acquire paths, refused on overlap with another session. **A refusal
  is a single point-in-time response, not a queue entry** — rite does not
  specify a retry-and-wait policy for a refused claim (a Worker or the Manager
  assigning it decides what to do next: try a different ticket, wait for a
  human decision, or return it to the pool). Each refusal is counted — this is
  §2.7.2's coordination-cost instrumentation, and it is a count of discrete
  events, not a measurement of time spent waiting, precisely because nothing
  here waits.
- `release` — free your own paths.
- `force-release` — free another session's paths, with attribution and a reason.

File-level claims cover the majority case for v1. Semantic conflicts (two Workers
changing the same function on different branches) are caught at PR review time, as
they are in any multi-developer project. The cost of a richer system is not justified
until measurement says otherwise.

**Claims are held until merge, not until the PR opens** — the workflow is
claim → work → PR → review → merge → release (§9.4.3), and release is the last
step. A claim released at "PR open" leaves the paths free for the entire review
window; a second Worker can claim and start conflicting work while the first PR
is still open, and if review comes back with change requests, the original Worker
may find its own paths already taken by someone else.

### 5.3. Process isolation — worker sandboxing

**Optional.** §5.1 isolates Workers by *directory*; this extends isolation to the
*process* — filesystem, network, and system commands — using
[`kstenerud/yoloai`](https://yoloai.dev), an open-source CLI that runs AI coding
agents (Claude Code included) inside disposable sandboxes (Docker, Podman, Tart, or
macOS's `sandbox-exec` mechanism — yoloAI's `--backend seatbelt`).

**Why this matters for rite specifically:** rite passes no permission flag to Claude
Code — nothing in the package sets one, and a pooled session is spawned as a bare
`claude`. How permissive an *unsandboxed* Worker's session is remains the operator's
choice, made outside rite.

⚠ **For a sandboxed Worker that choice has already been made, and it is the
permissive one.** yoloAI launches the agent as `claude
--dangerously-skip-permissions`. Grounded in the installed 0.11.0 rather than
relayed: the string is in the binary, and `~/.yoloai/library/defaults/
base-image.Dockerfile` builds `/usr/local/bin/yolo-claude` as `exec claude
--dangerously-skip-permissions "$@"`. A dogfood session separately reports seeing it
in a live sandbox's launch line via `rite sandbox pane`. So the
accurate sentence about a sandboxed Worker is **"it runs in bypass-permissions mode
inside a sandbox"**: the bypass is *contained*, not removed. "Workers no longer run
in bypass-permissions mode" would be false, and a false claim is worse than the
weaker one it replaces — this is the second time §5.3's wording has had to be pulled
back from a property the product does not have (see the changelog below), and the
correction runs in the opposite direction to the first.

That is a better posture than an unsandboxed permissive session, and it is the whole
point of this section: the sandbox exists precisely to make the permissive case safe.
A team running Workers under normal confirmation prompts needs less of what follows,
not none of it. It contains a hostile
or malformed ticket **by construction rather than by policy** — a production
`tf-apply` run from an unverified ticket, or a stray `git checkout origin/main -- .`
in a tree the session doesn't own, is bounded by the sandbox regardless of whether a
human happens to notice in time. Vigilance is not a guarantee; a process boundary is.

#### 5.3.1. The tension, and how it resolves

yoloAI's default model is *agent works isolated, human reviews and applies the
diff*. rite's model (§2.1) is *Workers behave like real developers — pull, branch,
PR, merge, autonomously*. Full sandboxing with no credentials inside it means a
Worker can't push, and human-applies-diffs reintroduces exactly the bottleneck rite
exists to remove.

**Resolved and verified working, 2026-09-10: pass a scoped git credential into the
sandbox.** A Worker with a token available inside its sandbox uses git normally —
pull, branch, push, open a PR — from inside the isolated process. rite gets yoloAI's
filesystem and process isolation *without* giving up the autonomous model.
`yoloai mcp serve` maps onto Manager → Worker for this — **adopted and tested
directly: 14 sandbox tools exposed, and the process/filesystem boundary confirmed
empirically**, not merely designed against yoloAI's own documentation. What this buys is
mundane and worth stating plainly rather than leaving to inference: **a human starts
the Manager session, and that session creates its Workers' sandboxes from inside
itself.** Sandbox creation is an ordinary tool call within an already-running,
already-authorised session — it is not a way around a confirmation step, and rite
neither has nor wants one. §2.5's revised framing rests on that: the human approval happens once, when the
Manager session is started, rather than once per Worker.

#### 5.3.2. What the sandbox protects, and what it doesn't

**State this plainly, because it changes what "sandboxed" means here:** a token
inside the sandbox bounds damage to the **machine** — filesystem, other repositories
on disk, system commands, the `rm -rf` and stray-`git checkout` class. It does
**not** bound damage to the **GitHub account** — whatever the token can reach stays
reachable from inside the sandbox exactly as it would from an unsandboxed session.
The sandbox contributes nothing to that half.

**So token scope is the security boundary for everything the sandbox can't contain.**
The model is a layered pair, not a single mechanism: **the sandbox bounds local
damage, the token scope bounds remote damage. Neither alone is sufficient; together
they're a real boundary.** A wide-scoped token inside a sandbox is not a sandboxed
Worker in any sense that matters — it has simply moved where the unbounded risk
lives.

**This boundary is backend-dependent, and the difference is not cosmetic.** Under
Docker/Podman/Tart, "bounds damage to the machine" can include network — a
container's network namespace can be isolated by policy. Under macOS `sandbox-exec`
(Seatbelt), it cannot: **Seatbelt enforces filesystem and process isolation but
provides no network isolation at all, verified empirically.** A Worker sandboxed
with `sandbox-exec` can make arbitrary outbound connections exactly as an
unsandboxed process can. Do not read "sandboxed" as a single guarantee — ask which
backend, and what that backend actually restricts.

⚠ **And rite exposes no way to ask for network isolation on any backend.**
`SandboxConfig` (`config/models.py`) has fields for `enabled`, `backend`,
`token_permissions` and `max_concurrent_workers` — and nothing for the network.
`start_worker` passes `new --backend <b> --agent claude`, an `--env` per credential
plus `RITE_PROJECT_ROOT` and git's `GIT_CONFIG_*` settings, its `-d` mounts, an
optional `--prompt-file`, `<name>` and `<workdir>` — never `--network-isolated`,
never `--network-none`.
**Until such a field exists, every rite-managed sandbox has unrestricted outbound
network, on every backend.** That is a statement about rite, and it is the one that
matters here — the backend differences below decide only what rite *could* ask for.

For completeness, from yoloAI 0.11.0's own `help security` (read from its
documentation, not measured): `--network-isolated` installs an IPv4 allowlist, and
what that allowlist is worth differs by backend. On **docker** the rules are
installed from a helper container and the sandbox is denied `NET_ADMIN`, so an agent
cannot remove them — the only backend where the allowlist contains a hostile agent.
On **apple, podman and containerd** the sandbox installs the rules itself and holds
`NET_ADMIN`, so an agent that tries can flush them: a guardrail against careless
egress, not containment. On **tart and seatbelt** there is no allowlist at all, and
`--network-isolated` is *refused* rather than silently unenforced — the refusal is
the right behaviour and worth crediting. Separately, `--network-none` removes the
network rather than filtering it and holds on every backend, seatbelt included; and
the allowlist is IPv4-only, with no `ip6tables` rules on any backend — which
restricts nothing today only because the networks yoloAI creates give the guest no
globally-routable IPv6 address. That is a property of those networks, not a
guarantee the flag makes; a guest that does have routable IPv6 is not covered by the
allowlist at all.

#### 5.3.3. Token scoping requirements

- **Fine-grained PAT scoped to the PROJECT's repos** — every module in
  `modules.yaml`, and nothing else on the account. That bound is real and worth
  keeping: it is narrower than "everything this person can reach", which is what
  a personal token would give a sandboxed agent.

  ⚠ **It is NOT scoped to the individual Worker's module subset, and this
  section used to say it was.** See §5.3.4 — workers are fungible by design, and
  a token narrower than the project would make them differ in capability.

- **Minimum permissions** — contents and pull requests only. Not admin, not
  workflow, not settings. This is the half of least privilege that survives the
  fungibility decision, and it is the more valuable half: it bounds what a token
  can DO, where repo scoping only bounds what it can reach.

- **One token per Worker, same scope.** Still one credential each rather than a
  shared one, so a compromise is attributable to one Worker and revocable
  without disrupting any other. What changed is the SCOPE they share, not the
  count.
- **Delivery: `--env` into the shell that execs the agent.** The token is passed
  as an environment variable via `--env` when the sandbox invokes the shell that
  runs the Worker's Claude Code process — never written to a file inside the
  sandbox and never passed as a CLI argument (which would appear in `ps`/process
  listings visible to anything else running in the same sandbox namespace).

  ⚠ **rite honours this at its own boundary; what happens inside the sandbox is
  not rite's to guarantee.** Verified here: `start_worker` passes `--env` and
  nothing else — no file, no argv. What yoloAI then does with the value is the
  half D-31 cannot speak for, and a dogfood session on this machine reported on
  2026-09-11 that 0.11.0 materialises it as a secrets file inside the sandbox and
  also leaves it in the sandbox's shell history and agent log, with `yoloai
  destroy` clearing them and `stop` not.

  **That report is recorded here unverified, and deliberately not restated as
  fact.** No attempt was made to reproduce it here — not a failed attempt, none —
  so second-hand is the only honest status. Note what would NOT settle it either
  way: the exact path strings are absent from the installed binary, but yoloAI
  composes them at runtime (`os.path.join(logs_dir, "agent.log")`), so a composite
  literal could never appear there and its absence is evidence of nothing. And
  `help security` scopes its documented `/run/secrets/` to "inside the container",
  while the report is from a seatbelt run, which is not a container — so differing
  spellings are expected rather than suspicious. **Anyone relying on the specific
  locations must measure them on the backend they use.**

  What survives regardless, and is the operationally useful part: **a stopped
  sandbox is preserved state, and preserved state can include the Worker's token.
  Prefer `rite sandbox destroy` over `rite sandbox stop` once a token is no longer
  wanted on a machine, and rotate it if a stopped sandbox has been sitting
  around** (§10 already requires rotation to be cheap for exactly this reason).
  Note that `destroy` passes `--abandon-unapplied`, so it discards whatever is
  in the sandbox's copy of the Worker's workspace. A Worker's work leaves by
  pushing its branch; anything not pushed is gone. So `destroy` first reads
  the copy with local git and refuses, without `--force`, while it holds
  uncommitted changes or commits on no remote, naming module, branch and
  count; `stop` reports the same and stops anyway. The copy's location is
  yoloAI's layout as measured on 0.11.0, not an interface: a copy that
  cannot be found is reported, not taken as safe.
- **Short expiry, easy rotation** — the same principle §10 already states for every
  credential rite manages: a credential that's painful to rotate never gets rotated.
- **GitHub App installation tokens** (short-lived, scoped to the app's installation)
  are worth considering as the team-scale alternative to fine-grained PATs — more
  setup per project, but better properties (no per-user PAT to leak, centrally
  revocable, naturally short-lived). The trade is recorded here rather than decided.
  Fine-grained PATs are the shipped baseline; evaluate App tokens once that
  baseline is in real use, and record the outcome as a decision row below.

#### 5.3.4. Workers are fungible, so they all get the same credentials

**Every Worker on a project receives every credential the project holds.** Not
a per-Worker subset. This section previously specified narrow per-Worker
scoping and presented it as least privilege; that is no longer what the design
does, and leaving the claim in place would be overclaiming a security property
the tool does not have.

**Why, and it is an engineering trade rather than a security argument.** A
Worker is an abstract entity that maps to a workspace and, at any one moment,
one session. Anything that makes Workers differ pushes a matching problem into
assignment: give `w1` a token covering `polly,reviewer` and it cannot push to
`writer`, so whatever assigns tickets must first reason about which Worker
*can* do a job. That is an assignment engine, and it is a large thing to build
in order to hold a bound that — see below — buys less than it appears to.

Fungibility is what makes "any Worker takes any ticket" true, and it is what
keeps assignment a matter of who is free rather than who is capable.

**What the bound actually is.** The project's repos, not everything the person
can reach. A sandboxed agent with the project's token cannot touch that
person's other repositories, their employer's org, or anything else on the
account. That is a real limit and §5.3.3 keeps it. What is gone is the
narrower limit *between* Workers of the same project.

**What that costs, stated rather than implied:**

- A compromised Worker reaches every repo in the project, not the subset it was
  working on. Attributability survives (one token each); containment between
  Workers does not.
- Every credential injected is written by yoloAI 0.11.0 into **four** files
  inside the sandbox — `ro/secrets/<NAME>`, the shell history, the agent log
  and `sandbox.jsonl` — all of which survive `yoloai stop` and are removed only
  by `destroy` (measured 2026-09-12; this settles the report §5.3.3 previously
  carried as unverified). Injecting a project's whole credential set rather than
  one token multiplies that surface by the number of credentials. **`destroy`,
  not `stop`, is therefore load-bearing** and is what the daily loop should use.
- Least privilege now rests entirely on the permission bound (contents and pull
  requests) rather than on the repo bound.

**Future improvement, if anyone ever asks for it.** A per-task scoping pass —
analyse what a Worker needs to finish the ticket it is about to take, and hand
it only that — would restore the narrow bound without a static assignment
problem, because the scope would be derived per task rather than per Worker.
Recorded here with its condition attached: **it is a nice-to-have, and it
should be built when someone asks, not before.** Nothing in this tool needs it
today, and this project has enough machinery that was built before anyone
needed it.

#### 5.3.4.1. Provisioning is `rite init` and `rite add worker`'s job, not chat's

**Corrected.** This subsection described "the correctly scoped token per
Worker" — the per-Worker scoping its own parent section retired, and which
§5.3.4 says in as many words would be "overclaiming a security property the
tool does not have". A subsection contradicting its parent is worse than
either version alone: a reader who stops at the more specific one comes away
believing in a bound that is not there.

What survives the correction is everything that was never about scoping.
Provisioning the project's token is a **credential-provisioning
responsibility** of `rite init` (§9.3) and `rite add worker` (§9.6) — the same
commands that already generate a Worker's identity and configuration provision
its credentials as part of the same flow. **A token must never pass through a
chat window** — not typed into a Dispatch prompt, not pasted into a session
transcript. It goes through the same OS-keychain path §10 already specifies
for every other credential, or a GitHub device/App-install flow that hands
rite the token directly without a human ever displaying it in chat.

`rite add worker --scoped-token` still provisions a token for one Worker
alone, and it stays available; what changed is that it is an option a person
chooses, not the model, and not something any other part of the design assumes
is in force.

#### 5.3.5. Practical constraints

- **Optional, and ON by default since D-51** — `SandboxConfig.enabled` is
  `True` (`config/models.py`). `rite init` asks, and `rite doctor` verifies by
  starting a real sandbox, so a machine without `yoloai` gets a question it can
  answer and a row saying what is missing rather than a failed first run. That
  is the reasoning D-51 gives, and it is a good one.

  ⚠ **It is not the reasoning this section asked for, and the prerequisites
  below are still open.** The paragraph that follows was written as a gate —
  "fix those first, then flip it, then reword. Do not reword first" — and the
  flip happened against a different argument, about first-run friction, while
  every item on the list stayed unfixed. Checked at `67b2631`: there is still
  no network field in `SandboxConfig`, so no backend rite can request isolates
  the network; and `docker` remains in the measured-not-to-work list because
  `flock` is a no-op inside it, which is the property every claim rests on.

  This is recorded rather than reworded away, and it is a **release question,
  not a documentation one**: either the gate was wrong and should be retired
  explicitly, or the default is ahead of it. Both are decisions; neither is a
  wording change, which is precisely what this section already said.

  The original list, unchanged, because it is what the gate was about:
  sandboxed-by-default would make `yoloai` a hard dependency, would make the §5.3.3
  token a hard requirement of `rite add worker`, and would ship every open problem
  in this section to everyone by default rather than to the people who opted in —
  no network isolation on any backend rite can request; an unresolved report that
  a Worker's token persists inside a stopped sandbox; and, on the one backend that
  can contain network egress, a `flock` that does not exclude, which is the
  property every claim in this tool rests on. Fix those first, then flip it, then
  reword. Do not reword first.
- **Backend choice is a security decision, not just a resource one.**
  `sandbox-exec` (yoloAI's `seatbelt`) is cheaper — no container per worker — and
  is the right default when the goal is filesystem/process containment for routine
  work. It enforces **no network isolation**, so a ticket of unknown provenance can
  still make outbound calls: exfiltrate the §5.3.3 token, reach an untrusted
  endpoint, phone home.

  **Corrected 0.18.2, against `yoloai help security` rather than against this
  document's own earlier guess.** This bullet used to say "use Docker (or
  Podman/Tart) whenever network isolation is the actual goal", and two thirds of
  that is wrong. **Tart has no network isolation at all** — the same category as
  seatbelt, so recommending it for this was the opposite of the truth. On **Podman**
  the sandbox installs the allowlist itself and keeps `NET_ADMIN`, so an agent that
  tries can flush it: a guardrail, not containment. **Docker is the only backend
  where the allowlist actually contains a hostile agent**, because the rules come
  from a helper container and the sandbox is denied `NET_ADMIN`. So: **Docker, and
  only Docker**, when network isolation is the goal — or `--network-none`, which
  removes the network instead of filtering it and holds everywhere.

  ⚠ **None of which rite can currently ask for** (§5.3.2): there is no network
  field in `SandboxConfig` and neither flag is ever passed. Choosing Docker today
  buys the *possibility* of isolation and not the thing itself. Until that field
  exists, this bullet describes a decision that has to be made outside rite.
- **Swift toolchains sandbox their own sub-steps, and seatbelt forbids nesting.**
  SwiftPM and Xcode evaluate `Package.swift`, run package plugins, and start
  macro plugins under their own `sandbox-exec`; macOS refuses to apply a sandbox
  from inside one (`sandbox_apply: Operation not permitted`, measured). Inside a
  seatbelt Worker every Swift build therefore fails, with errors that name the
  step rather than the cause — "Invalid manifest", or a missing
  `SwiftMacros.TaskLocalMacro`. Detected Swift commands carry the flags that turn
  those inner sandboxes off whenever `sandbox.enabled` is set and the backend is
  `seatbelt`; `cli/init/detect.py` says why each one is there. **That does not
  unsandbox the Worker**: it stays inside yoloAI's profile, and D-51's default
  holds.

  ⚠ **That profile gives every Worker write access to the same host caches** —
  `~/Library/Caches/org.swift.swiftpm`, `~/Library/org.swift.swiftpm`,
  `~/Library/Caches/swift-build` and `~/Library/Developer/Xcode`. Several Workers
  resolving and building the same packages into one cache at once is **untested**,
  and it is also a shared-write surface: one Worker can change a cached checkout
  another builds from. Tracked as dogfood ticket SBX-1.
- ⚠ **`flock` is a no-op inside a Docker sandbox, and every claim rests on
  `flock`.** Reported by a dogfood session on this machine, 2026-09-11, testing
  `fcntl.flock` on one file three ways: on the **host** a second lock is refused,
  inside a **seatbelt** sandbox it is refused, and inside a **docker** sandbox it
  is *also acquired*. With several sandboxed Workers on docker, two can hold the
  same path and both be told "claimed" — the exact failure the claims system
  exists to prevent, arriving silently.

  **This collides head-on with the backend advice in the bullet above.** Docker is
  the only backend where the network allowlist actually contains a hostile agent,
  and it is the backend where rite's central safety property stops holding. No
  configuration gives both today. Choosing docker for network containment means
  running **one Worker**, or accepting that claims do not exclude — a trade
  currently stated nowhere a user would meet it, which is a defect of its own and
  not merely an unfortunate fact.

  rite does detect it: `state.exclusion_holds` probes the real filesystem and
  `rite doctor` reports `file locking: DOES NOT WORK` and exits non-zero. It
  **warns rather than refusing**, and its message names "a network mount or a VM
  shared folder" — the cases it was written for — so a docker user gets the right
  verdict with the wrong explanation.

- **Claims do still exclude on the seatbelt backend**, from the same session: 88
  concurrent claim attempts from four sandboxed Workers over 22 rounds, including
  nested and decorated paths, gave exactly one grant per round and no anomalies.
  Recorded with its provenance because this document did not run it, and scoped
  narrowly — one machine, one backend, a shared host filesystem. It is a reason
  seatbelt stays the default, and it does not transfer to docker.
- **The sandbox boundary may change what the workspace-prep script (§2.1, P1.3)
  sees** — a sandboxed Worker's view of the filesystem differs from an
  unsandboxed one's, and the prep script's assumptions need to be checked against
  that, not assumed to transfer unchanged.

---

### 5.4. Manager guardrails — containment without a sandbox

⚠ **REVERSED IN 0.6.0, and the text below is kept as the argument that was
reversed (D-76, superseded).** Robert put Manager sandboxing into 0.6.0
because the permission allowlist cannot hold for Goose, whose `GOOSE_MODE`
is whole-session. So the sandbox is the only boundary that works for every
engine. Since `4ebbbd7` a Manager's pane runs inside a seatbelt profile that
`managers/enclosure.py` composes. The three reasons below fared differently:

- **Broad project access** did not stop it. The profile grants the whole
  project tree, and that is also why the profile does **not** separate two
  Managers in one root from each other (§5.4.8).
- **Attachability** did not stop it either. The tmux pane is created outside
  the boundary, and a human still attaches to it.
- **Nesting was measured true** (`docs/design/spikes/B9-manager-sandboxing.md`):
  only a semantically equivalent profile may be re-applied inside a sandbox,
  so a sandboxed Manager cannot start a sandboxed Worker. It is answered by a
  **broker**. The Manager requests a Worker, and the supervisor, outside the
  boundary, validates the request and runs `rite sandbox start`
  (`managers/broker.py`).

⚠ **What the boundary does not do** is printed on every run
(`enclosure.limitations()`). It bounds files, not capability, and it does not
confine the network. Two holes in the first version were found and closed on
2026-09-25 (`9862b59`), and each was measured succeeding and then failing:
the tmux server, which runs outside the profile and ran commands sent to it
unconfined, is now unreachable (its socket directory is denied by path); and
signals are limited to the Manager's own sandbox. **One cross-project read is
still open.** `~/.claude` is readable whole, so a Manager can read other
projects' Claude transcripts. See `docs/design/V070_RELEASE_PLAN.md`, part 0
and SB4. The remaining containment below, **what a Manager is permitted to
do**, still applies in full. The sandbox adds to it and replaces none of it.

§5.3 gives Workers a sandbox. Managers do not get one, and this section is
the other half of the design rather than an exception to it.

**yoloAI is the WORKER RUNTIME, not a provider.** It belongs on a different
axis from `claude` / `cursor` / `local` (§9.14), which are the engines a
*Manager* session runs on. Conflating them produces the reasonable-sounding
and wrong conclusion that a Manager should be sandboxed the way a Worker is.

**A Manager cannot be sandboxed, for three independent reasons**, any one of
which would be sufficient:

- it needs broad access to the project by its nature — that is what
  distinguishes it from a Worker, which is handed one subtask and one
  workspace;
- it must be attachable by a human (§9.14.3), and a sandbox is the wrong
  side of that boundary;
- macOS may refuse a sandbox inside a sandbox, which would make a sandboxed
  Manager **structurally unable to start sandboxed Workers** — the one thing
  a Manager exists to do.

So a Manager's containment comes from **what it is permitted to do**, not
from where it runs.

#### 5.4.1. The requirement

**A misbehaving Manager must not be able to interfere with another Manager's
internal working by accident.**

This is §5.0's first case, and the wording is exact. **A Manager that fails
to deliver its work** is §5.0's third case — a coordination matter between
that Manager and the Owner, which rite reports and does not arbitrate.
**A Manager that writes into another Manager's files** is an accident, and
rite's bug to prevent. The requirement is containment, not fairness, and it
does not extend to making Managers behave well.

#### 5.4.2. Path boundaries are enforced at every name-to-path join

**Every place a caller-supplied name becomes a path must validate that the
result is inside the boundary it belongs to, at the join.** Not at the entry
point, not by convention: at the join, because that is the only place a
second caller cannot bypass.

**All four refuse as of `6a8a5b2`, at the library AND at the CLI.** Measured,
with the invocations written out because a claim of this kind is not
falsifiable without them:

    $ rite context add ../../IMPORTANT.md trig desc
      exit 1: a context file name is one path segment and '../../IMPORTANT.md' is not
    $ rite context remove ../../IMPORTANT.md          exit 1, same refusal
    $ rite heartbeat --worker ../../pwned             exit 1, nothing written
    $ rite add module ../../escaped-mod               exit 1, nothing created
      add_module(project, "../../escaped-lib")        ok=False, nothing created

The fix (`rite_ai.names.require_safe_name`) is called **inside** the library
functions rather than at the command, which is what this subsection requires
and what makes the last line above hold. Its own docstring states the rule:
"the fix is at the boundary, not in the guard."

⚠ **Establishing that took three attempts across two sessions and the errors
are worth more than the result.** One report said four boundaries escaped;
a run said two; the run was wrong because `rite context add` takes POSITIONAL
arguments and had been given flags, so the add failed on usage, nothing
entered the index, and the subsequent remove reported `'../../IMPORTANT.md'
not in index` and unlinked nothing. **The output of the test not running was
indistinguishable from the output of it passing** — inside the verification of
a path-traversal defect, which is the same shape as the defect.

So: paste the invocation. "I ran it and it did not escape" is not a
measurement, and one report was right for the wrong reason while one run was
wrong by method, which is the more uncomfortable combination of the two.

⚠ **The CLI and the library are separate surfaces, and a fix to one is not a
fix to the other.** Before `6a8a5b2`, `add_module` was exactly that state —
refused at the command, permitted in the library, returning `ok=True` and
creating a directory outside the tree. **It looked safe from the command
line.** That is the same shape as a check fixed at one entry point while the
entry point people actually use goes on calling the unguarded one, and it is
why this subsection requires validation at the join rather than at the
command.

A boundary by convention holds until the first unvalidated join.

#### 5.4.3. Destructive operations must name what they are scoped to

**Anything that removes or releases must name whose thing it is removing and
refuse to act outside that scope.** Two current shapes fail it, both by
omission rather than by hostility:

- `destroy_worker(worker, root=None)` — `root` defaults to `None`, making it
  a library-callable destroy with no project boundary. The module's own
  docstring already says omitting `root` "should be treated as a bug in the
  caller, not a supported mode"; the default stayed anyway.
- `force_release(paths, by, reason, *, worker=None)` — `worker` is an
  optional keyword, so the default match is by path across every holder.

⚠ **Neither of those is a MANAGER boundary, and the heading of this
subsection used to claim they were.** `destroy_worker`'s missing scope is the
PROJECT, and two Managers on one project share a root, so fixing it buys
nothing against Manager-on-Manager interference. `force_release`'s `worker=`
is a WORKER scope. **`Claim` carries `paths`, `worker`, `ticket` and
`timestamp` and has no manager field at all, so the ledger structurally
cannot express "release only my own Manager's claims."** Both fixes are worth
making and neither delivers §5.4.1 on its own.

#### 5.4.4. Credentials are scoped to what the Manager needs

A Manager running a local engine holding the Claude token is an exposure with
no purpose. §5.3.4 argues that *Workers* are fungible and so all get every
credential; **Managers are not fungible** — they differ by engine, by duty and
by which services their work touches — so the argument does not carry across
and should not be assumed to.

⚠ In tension with §10's per-project credential model, which delivers a
project's credentials as a set. The same open question §9.14.7 records, named
here so the two are recognised as one problem rather than two.

#### 5.4.5. ⚠ "The acting Manager's own directory" does not exist yet

⚠ **PARTLY STALE: step 1 has landed, and so has the directory. Step 2 has
not.** A Manager session carries `RITE_MANAGER` (`managers/__init__.py`,
`MANAGER_ENV`), set on every session `rite start` creates. So a process can
answer "which Manager am I". And `manager_dir()` gives each Manager
`.rite/managers/<name>/`, where NEW state lives (the mailbox, the journal).
**Existing per-project state has not moved**, and no 0.6.0 ticket moves it.
Step 2 is carried to 0.7.0 as MM1 in `docs/design/V070_RELEASE_PLAN.md`. The
text below is the analysis as written before either landed.

**An earlier draft of this section proposed the property "nothing is written
outside the acting Manager's own directory except the enumerated shared
files", and called it checkable. It is not, and the reason is structural
rather than a matter of implementation effort.**

**There is no per-Manager directory.** `.rite/` is flat. Of roughly twenty
per-project state paths — claims, heartbeats, outbox, pool state and archive,
the loop lock and log, dispatch intents, the scheduler lock, log and tick
state, coordination cache and last-tick, `machine`, the three config files,
context and kb indexes, spec index and units, telemetry — **exactly one is
keyed by identity**, `handover/<name>.json`, and it defaults to the reserved
name `_owner`, so two Managers calling it without a name collide on one file.
Four more paths live in `~/.rite/`, outside the project root entirely, where
the framing does not reach at all.

**And there is no per-Manager identity to key a directory on.** `this_manager`
returns the FIRST LINE of `.rite/machine`. The rite-local case this section
exists to serve puts three Managers in one checkout with one such file, and
there is no `RITE_MANAGER` in the environment. **A running process cannot
answer "which Manager am I", so it could not compute its own directory even
if one existed.**

**And the property would be false anyway for the thing a Manager mainly
does.** §5.4 opens by saying a Manager needs broad access by its nature; the
`lead` preset holds EXECUTE and INTEGRATE. A Manager writes source files into
the shared checkout and commits them, by design. **Any usable form of this
property is scoped to rite's own state, not to project files.**

So what §5.4.1 requires, in order:

1. **a per-process Manager identity** that `.rite/machine` cannot currently
   supply;
2. **relocating per-project runtime state** from a flat `.rite/` into a
   per-Manager subtree;
3. **and only then** the boundary property, scoped to rite state.

#### 5.4.6. The shared surface is enumerated — and the ledger is not the only one

⚠ **An earlier draft said "the claim ledger is the one thing Managers must
agree on" and enumerated exactly that. It is empirically wrong**: `pool.json`
is a sandbox register two Managers must agree on exactly as they must agree
on the ledger, and the loop and scheduler locks are mutual-exclusion
primitives whose entire purpose is being shared.

The enumeration must therefore split two kinds:

- **Shared by decision** — the claim ledger, `pool.json`, the loop and
  scheduler locks, the coordination cache. Each stays shared and each needs
  its reason written next to it. ⚠ *The outbox was on this list. It is no
  longer shared: the mailbox lives under `.rite/managers/<name>/mail/`
  (`managers/mailbox.py`), one per Manager.*
- **Shared by accident** — everything else in the flat `.rite/`, which is
  shared because nothing gave it an owner, and which §5.4.5's step 2 moves.

⚠ **A prose enumeration is not sufficient on its own, and this codebase has
already proved it.** `state.py` records a claim of completeness — "every
`.rite` state file" — that was not every state file; four writers were
missed, and `tests/test_shared_state_locking.py` exists because of it. The
enumeration needs a test behind it or it goes stale the same way.

#### 5.4.7. The test, narrowed to one that can actually be written

**Every rite CLI command, invoked with hostile arguments in a scratch
project, must write nothing outside an explicit allowlist.**

⚠ **An earlier draft said "one Manager attempts every write path available to
it", which is not writable.** A Manager is a Claude session with a shell —
§9.14.3 requires it be attachable and live — so its write paths are every
command, plus arbitrary file writes by the agent, plus git. There is no
interface to enumerate.

What is enumerable is rite's own command surface, and the repository already
has both halves: `tests/test_cli_help_text.py` walks the click command tree
recursively, and `tests/test_blast_radius.py` establishes asserting a
property over an enumerated surface rather than grepping text. The test
builds a scratch project, snapshots the project root, its parent and `$HOME`,
invokes every leaf command with `../`-bearing and absolute-path arguments,
and asserts every path created, modified or deleted is in the allowlist.

**That test would catch all four of §5.4.2's escapes today.** Until §5.4.5's
first two steps land, **its allowlist is per-PROJECT, not per-Manager** —
which is a weaker property than §5.4.1 asks for, and worth having now rather
than waiting for the stronger one.

A rule in this document is read once by whoever implements the thing it
governs. The four escapes were all added by authors who would have endorsed
the rule had they been asked, which is what makes a rule the wrong instrument
here.

#### 5.4.8. Separation between Managers that share a root — the requirement (0.7.0)

**Status: DECIDED (Robert, 2026-09-20: D-79, §9.14.9). Not enforced.**
Several Managers run in **one** project root, each with its own
`.rite/managers/<name>/`, and they are **strictly separated**, in Robert's
words: *"one misbehaving manager shouldn't be able to mess with others by
accident."* Profiles are committed and shared. Per-instance configuration is
gitignored.

The requirement is §5.4.1's, applied between Managers rather than between a
Manager and the operator's machine. Written as four properties so each can
be tested:

| | property | what must enforce it |
|---|---|---|
| **P1** | No rite command acting as Manager A writes rite state belonging to Manager B | the name-to-path join (§5.4.2), and §5.4.7's test at **Manager** granularity |
| **P2** | Manager A's processes cannot signal Manager B's, or drive B's session | the Manager's sandbox profile: signals limited to its own processes, and the tmux server out of reach |
| **P3** | State shared by decision (§5.4.6) is written only through its locked writer, and the list is enumerated by a test | §5.4.6 |
| **P4** | A release or destroy names the Manager whose thing it is | §5.4.3, with a Manager field on the claim |

**State on `main`, measured 2026-09-25 at `9862b59`:**

- **P2 holds.** With two Managers' profiles in one root, killing the other
  Manager's engine and driving or killing its tmux session were all
  refused, and the other Manager survived. It holds by exactly the two
  mechanisms named above: `(target same-sandbox)` signals and the denied tmux
  socket. It is not yet pinned by a test between two Managers.
- **P1, P3 and P4 do not.** Most per-project state is still flat (§5.4.5).
  The shared-by-decision list has no test behind it (§5.4.6). And `Claim` has
  no Manager field (§5.4.3).

⚠ **The Manager's sandbox is not what enforces P1, and was measured not to.**
Each Manager's profile grants the whole project tree, and one Manager wrote
into the other's `.rite/managers/<name>/`. The sandbox separates a Manager's
**processes** from its siblings'. It does not separate their **files**. P1 is
enforced by rite's own writers, or it is not enforced.

**"By accident" is the bar, and it is not "against a hostile Manager."** A
Manager may run `rite`, and `rite` does what the operator can. What this
section requires is that ordinary mistakes cannot cross between Managers: a
wrong path join, a broad `pkill`, a `tmux kill-server`, a force-release
matched by path. The tickets and the open questions (where per-instance
configuration lives, whether Workers belong to a Manager, a per-Manager
worker cap) are in `docs/design/V070_RELEASE_PLAN.md`, track MM.

### 5.5. Egress — where an agent may talk (0.7.0)

**Status: DECIDED (Robert, 2026-09-25: D-99, D-100). Not built.** Until it
ships, §6.6.3's warning stands unqualified. Design, measurements and open
questions: `docs/design/V070_EGRESS.md` and
`docs/design/V070_RELEASE_PLAN.md`, tracks EG and SB.

#### 5.5.1. The property, stated positively

**An agent may talk only to destinations the operator sanctioned, outbound
and inbound.**

It is a list of what is permitted, not of what is forbidden. A list of
threats loses to the one nobody listed. A list of destinations is closed by
construction, and whatever is not on it is refused, including the
destination nobody thought of. One rule covers a push to a fork, a registry
upload, a webhook, a paste site, a comment on a third party's tracker, and
in the other direction a `git clone`, a package install or a `curl | sh`
from anywhere unsanctioned.

**This is the control that makes §6.6.3 survivable.** Text filters try to
stop an agent from being fooled, and 8 of 8 agent-directed attacks passed
one. This control does not care what the agent believes. An instruction to
post `.env` to a collector goes nowhere, because the collector is not on the
list.

#### 5.5.2. Enforced at the network layer, never the tool layer

**The list is enforced where connections are made**, not by deciding which
programs may run. The command allowlist (C4) refuses `curl` and permits
`git` and `gh`. Both of those reach the network and either can reach an
unsanctioned destination. And `python -c`, a build script, a git hook or a
package's post-install step can all make requests. **The command allowlist
is not an egress control and must never be described as one.**

**What the enforcement points can express, and this constrains every design
of it:**

- **A Worker on a docker backend.** yoloAI's `--network-isolated` with
  `--network-allow` enforces a domain allowlist that the sandbox cannot
  remove. On apple, podman and containerd the same flags are a guardrail the
  sandbox can flush. On **seatbelt, rite's default backend,** yoloAI
  **refuses** the flag. The allowlist is IPv4 only. (Read from `yoloai help
  security`, 0.11.0; not measured by rite.)
- **A Manager** runs under seatbelt on the host (§5.4). Measured 2026-09-25:
  for **IP** destinations a seatbelt profile has two positions, everything or
  **loopback**. A named host is rejected when the profile loads (*"host must
  be * or localhost in network address"*). For **local sockets** it can allow
  or refuse by **path**, and that is how the tmux socket is denied today. So
  a Manager's list of *hosts* cannot live in its profile. IP traffic has to
  pass through something outside the boundary that enforces it, while
  local-socket destinations can be decided in the profile itself. How is
  open.

⚠ **So a project whose Workers run on seatbelt gets no Worker egress control,
and rite must say so rather than imply otherwise.** The words "restricted",
"isolated" or "controlled" may be used only for an enforcement point that
actually enforces.

#### 5.5.3. A refusal is reported, not silent

A refused connection is reported the way a refused command is (C21): which
destination was refused, and the configuration line that would permit it. A
client that meets a refused connection usually reports something that looks
like an outage. So the report comes from rite's side of the enforcement
point, not from the client's error. A silent network failure would be the
stall-without-a-message class again.

#### 5.5.4. Content scanning — only on allowed destinations that publish (D-100)

**The destination is the primary control. Content scanning is secondary, and
it applies to one case: a destination that is allowed but publishes.** A
`gh issue create` on the operator's own repository passes the destination
check, and a token pasted into the body would go with it. For that case the
outbound payload is scanned for credential-shaped content, using the
structural rule `redact_secrets` already has, not a list of token formats.

⚠ **Model calls are never scanned.** Code goes to the model provider by
design, and that is the product working. A scanner on that path fires on
every request, and one tuned to ignore it leaves the largest channel
unwatched while appearing to watch it. The provider endpoint is a sanctioned
destination, and rite says so rather than pretending to inspect it.

**Which allowed destinations count as publishing is not decided**
(`docs/design/V070_RELEASE_PLAN.md`, EGQ5).

#### 5.5.5. The vocabulary is rite's

The list lives in rite's configuration, in rite's words: hosts, grouped by
what they are for. No proxy's syntax and no yoloAI flag reaches
`config.yaml`, by the same rule that keeps engine nouns out of it. Whether
the list is committed or per-instance is open (EGQ3).

#### 5.5.6. What this makes demonstrable

With a local engine (the local tier), no legitimate full-content egress
exists. The list can then be entirely internal (the model endpoint, the
internal git host, the package mirror), and *"nothing leaves this machine"*
becomes something rite can **show**: the policy, and a refusal of an outside
host. With a hosted engine the honest claim is narrower: nothing leaves
except to the provider the operator chose.

## 6. Ticket backend

### 6.1. Abstract interface

The ticket backend is behind an interface so that JIRA, GitHub Issues, Trello, Linear,
and others can be swapped without touching rite's core logic. The interface exposes:

```
create(ticket)          → id
read(id)                → ticket
update(id, fields)      → void
move(id, status)        → void
assign(id, worker)      → void
label(id, labels)       → void
list_tickets(filters)   → ticket[]
comment(id, text)       → void
query(raw_query)        → ticket[]      # pass-through for backend-native queries (JQL
                                          #   for JIRA, search qualifiers for GitHub)
link(id, target_id, link_type) → void | BackendError
                                          # e.g. "blocked by" (§6.2) — cross-ticket
                                          #   relationships, backend-native semantics
```

**Method names here are the literal names an implementation should use** — this
interface used to write `list(filters)` and `jql(query)` as shorthand while the actual
code correctly implemented `list_tickets`/`query` (Python's `list` is a builtin, and
`jql` is JIRA-specific vocabulary for a backend-agnostic method); this section is
corrected to match rather than leaving a name that only ever existed in prose.

Fields are a superset normalised to rite's model; backend-specific fields pass through
as opaque metadata. `query()` accepts a raw query string in the backend's native
language and returns normalised results — recovering ad-hoc flexibility without
building a universal query DSL.

**`link()` is part of this interface, not a JIRA-only extension (D-49)** — §6.2 already
specifies "Worker tickets link to project tickets as 'blocked by'" as backend behaviour;
an earlier version of this section left that unimplementable, because the interface
itself had no method to carry it. Each backend maps `link_type` to its own native
relationship (JIRA issue links). **A backend with no real link mechanism must return
`BackendError` naming the gap, never silently substitute a comment or any other
weaker mechanism** — §6.2 makes "blocked by" load-bearing for dependency logic ("Ready
for testing" lets dependents proceed), so a caller needs to be able to tell a real link
from a no-op; a `void` return on both makes that impossible. As of `gh` CLI 2.98.0 there
is no first-class issue-dependency subcommand, so the GitHub backend is expected to
return `BackendError` here rather than fabricate a substitute — `gh api` against
GitHub's REST issue-dependency endpoints is the escape hatch to evaluate if and when
this becomes worth building, not assumed available now.

### 6.2. JIRA (default backend)

The default implementation uses JIRA Cloud REST API.

⚠ **This is the upstream project's board layout, recorded because it is the one
this design was validated against — not a structure rite requires or creates.**
rite reads and writes whatever board the project already has; `ticket_backend.
projects` maps rite's three ROLES (board / workers / testing) onto whatever JIRA
projects exist. A team that runs everything on one board sets all three keys to that
one project — verified working, but note it must be all three: setting only `board`
fails with `no project configured for role 'workers'`, naming a role the user never
chose.
The column names below are likewise the upstream's, not a vocabulary rite enforces —
see the status-vocabulary gap in §6.5.

**Reference board structure** (the layout this was validated against):

| Project | Key | Type | Purpose |
|---------|-----|------|---------|
| Project board | configurable | Scrum (team-managed) | Business-level tickets. Epics for complex features. |
| Workers board | configurable | Company-managed | Worker tickets. Company-managed because it has the right shape for automation: shared workflows, better automation rules. |
| Testing board | configurable | any | Scratch / QA. |

- Worker tickets link to project tickets as **"blocked by"** — the one structural
  assumption rite does depend on, because §6.1's `link()` and the dependency logic
  that reads it need *some* backend-native "this blocks that" relationship. A backend
  without one must return `BackendError` (D-49) rather than fake it.
- **"Ready for testing"** is the upstream's name for a column that is functionally
  Done for dependency purposes — dependents proceed while a human decides whether it
  is truly finished. The *concept* is what rite needs; the *name* is the upstream's.
  A project whose equivalent column is called "In review", "Staged", or nothing at
  all is not misconfigured — see §6.5.

Why company-managed for workers: the automation that assigns, moves, and labels
worker tickets needs shared workflows and JQL-queryable fields. Team-managed boards
lack these. Record this reasoning so nobody "simplifies" the setup later.

### 6.3. GitHub Issues (alternate backend)

Uses `gh` CLI for all operations. This avoids token refresh issues that plague
MCP-based GitHub integrations.

### 6.4. UI design gate

**Applies only to projects that do UI design at all** — a library, a CLI, or a
backend service has nothing to attach, and the gate is simply inert there rather
than something to be switched off.

A feature needing UI design must have the design attached to the business ticket
before implementation begins — a mockup, a Figma link, or a Claude Design handoff
bundle.

- No design attached → the Owner escalates to the human, who may explicitly defer
  design to Claude Code.
- Claude Design ships a real bidirectional handoff (`/design`, `/design-sync`) —
  treat the handoff bundle as a first-class input to implementation.
- The gate is advisory, not blocking: a ticket without design is flagged, not refused.
  Some tickets genuinely don't need it, and the human's override is the escape.

### 6.5. Status vocabulary — a known gap, not a decision

⚠ **rite has no configurable status vocabulary, and the one it has is inherited
from the upstream project's board.** Recorded here as a gap because it is the
place a new user with a different workflow will meet rite as a bug rather than as
a preference.

Concretely: `GitHubBackend` classifies statuses against two hardcoded sets —
`{open, reopen, reopened, to do, todo, in progress}` and
`{closed, close, done, cancelled, canceled}`. A team whose columns are "Triage",
"In review", "Staged", "Shipped" gets a `BackendError` naming a vocabulary it
never chose. The error is at least honest — it refuses rather than silently
collapsing an unrecognised status to "close", which would be data loss (§6.3) —
but refusing correctly is not the same as working.

What a fix looks like, so it is not rediscovered from scratch:
`ticket_backend.statuses` mapping the project's own column names onto the two
things rite actually needs to reason about — *is this ticket startable* and *may
its dependents proceed*. That is a small config addition and a change to one
method per backend. It is not done, and until it is, §6.2's column names are load-
bearing in a way a project-agnostic tool's should not be.

The **"blocked by" link is different and is a genuine requirement** — §6.1's
`link()` and the dependency logic that reads it need a real backend-native
relationship, and D-49 already says a backend without one must return
`BackendError` rather than substitute something weaker. That one is a stated
dependency, not an inherited habit.

---

### 6.6. Ticket text reaching an agent — what is built, what is planned, and what is NOT vetted

**Status: DECIDED 2026-09-25 (Robert, D-97, D-98). Planned for 0.6.0,
sequenced LAST and droppable** (plan § N).

**What rite does TODAY, whether or not § N ships: nothing to ticket text.**
A ticket's title, body and comments reach a Manager or Worker's context
**verbatim**: no characters removed, no tag characters decoded, no HTML
comments stripped, no phrases scanned. Whoever can write a ticket can write to
that context. **This paragraph stays true until § N is built, and must be
edited in the same change that builds it.**

**What § N adds, IF it lands** (§6.6.1 and §6.6.2 below describe that design,
not current behaviour). There are two separate things. **Neither makes ticket
text safe, and this section exists as much to say that as to specify them.**
§6.6.3 holds whether or not they land.

#### 6.6.1. Normalisation — so the agent sees what a human reviewer sees (D-98) — § N1, not built

Not a security control. A correctness one: text a reviewer cannot see must
not be text an agent acts on. As designed, once built:

- **Invisible characters** are removed. These are the characters §9.15.3's
  sweep already derives, by name and by unrenderable category.
- **Hidden tag characters** (the Unicode tag block, which can spell ASCII
  that renders as nothing) are **decoded and shown**, not silently dropped.
  Their presence is itself worth reporting.
- **HTML comments** are stripped or surfaced. A comment renders as nothing
  in a tracker's UI and arrives whole in the API body.

#### 6.6.2. Injection phrases are REPORTED, never blocked (D-97) — § N2, not built

Once built, rite scans ticket text for phrases commonly used in prompt
injection and **surfaces a match to the User at the next check-in** (§9.16,
plan § K). It will never quarantine, filter, rewrite or withhold the ticket.

**Why reporting, when blocking was rejected.** An evaluation of a phrase
sanitizer (on the Bentora project) quarantined **9 of 18 ordinary tickets** in
blocking mode. At that false-positive rate, blocking stops real work. Reporting
changes what a false positive costs: a line in a standup instead of a blocked
ticket. The same evaluation caught **6 of 9 model-directed attacks**, and
under reporting that detection is free signal.

It is named for what it does. A report says *"this ticket contains a phrase
commonly used in prompt injection"*, which is true. rite never says a ticket
has been *"sanitized"*, *"cleaned"* or *"checked"*, because those claim
something the scan does not do.

#### 6.6.3. ⚠ What this does NOT do — ticket text is not vetted

The same evaluation measured **8 of 8 agent-directed attacks passing**:
`curl … | bash` in a setup step, "paste `.env` into a comment", "add this SSH
key". They read as ordinary ticket requirements, and **no text filter catches
them**, because nothing in the words distinguishes them from legitimate work.

**So a reader of this document must not conclude that ticket text is
vetted.** Today it is neither normalised nor scanned. Even once § N is built,
it is only normalised and phrase-scanned, and an instruction to exfiltrate,
worded as a task, still reaches the agent unflagged. What limits the damage is not
here. It is the command allowlist (C4) today, and destination control
(§5.5, v0.7.0, decided and not built), which makes a fooled agent harmless
rather than trying to stop it being fooled.

## 7. Review convention and checklists

### 7.1. The convention `rite init` ships

`rite init` generates a `/review` command and four reviewer agents implementing a
**two-stage** convention. Recorded here because the generated `CLAUDE.md` cites this
section for it, and because "two stages" is a choice, not a law:

- **Round 1** — independent fresh-eyes reviewers (`reviewer-round1`) against the
  merged checklists; two for ordinary work, at least three for a gate, a shared
  contract, a migration, customer data, or user-facing copy. Plus `reviewer-decisions`
  once (does the build match what was already decided?), and `reviewer-seam` for
  anything crossing a module boundary. Findings go in a delta register, and the
  **class** of each defect is named before the fix is written — a fix spelled against
  the exact case shown closes that case and nothing else.
- **The terminating check** — two fresh `reviewer-terminating` agents scoped to
  round 1's *fixes*, not its findings, and never staffed by whoever proposed them.
  There is no round 3.

⚠ **The stage count and agent counts are not configurable, and nothing justifies two
over one or three.** They are inherited from the setup rite was generalised out of.
The templates are plain Markdown and a project is free to rewrite them; rite reads
none of this at runtime — it is instruction to a Claude session, not behaviour rite
enforces. Treat the numbers as a starting point, not a finding.

### 7.2. Checklists

Review checklists are `.md` files committed per repo:

- **One project-wide checklist** at `<project-root>/.rite/review-checklist.md`.
- **One per package/repo** at `<repo>/.rite/review-checklist.md`.

Checklists are editable by Managers and by hand. They ship with sensible defaults
covering:

- Security (OWASP top 10 basics).
- Correctness (test coverage, edge cases).
- Style (project conventions).
- Dependencies (license, security advisories).

The point is incremental quality: known issues get a checklist item and are eliminated
permanently. A checklist item that keeps firing becomes a lint rule or a gate; one that
never fires gets removed.

### 7.3. Requirements-derived scenarios, before a branch merges

**Not built, and not in 0.6.0 — targeted at 0.7.0.** Specified here so the
procedure exists before the code does, which is most of the point. Robert
moved the gate out of 0.6.0 on 2026-09-24 (release plan, Decision 5); see
§9.15.4, which used to point at it as a 0.6.0 destination.

After a feature branch has been through its terminating check and the
resulting fixes, a Worker runs **test scenarios derived from the
requirements** against the branch. The branch does not merge until they pass
and the run is evidenced. The scenarios are written as part of the
implementation plan, not after the code — a plan that cannot say what success
looks like has not finished specifying the work.

**The second benefit is the one that is easy to lose, so it is stated rather
than implied: writing the scenarios surfaces questions nobody answered.** The
scenario author has to say what should happen when the user does X, and a
plan can leave that implicit where a scenario cannot. Those answers are
cheaper before implementation than during it — a decision taken mid-flight is
a decision taken by whoever happened to hit it, under time pressure, without
the context that would have settled it.

#### Why this, and not more review

The evidence is this project's own v0.5.0, whose defects were found by a
four-reviewer terminating check. Going through them one at a time, a
requirements-derived scenario would have caught:

- `rite loop start` reported success and started nothing — *"start the loop;
  confirm it is running"* fails on the session table.
- `rite pool fill` reported two sessions started with none running — same
  shape, same scenario.
- The publish gate **blocked every push it documented as non-blocking** —
  *"with a pre-existing finding in the repository, push unrelated work"*
  fails immediately. This is the clearest case: the requirement was written
  down, the behaviour was its opposite, and two unit tests asserted the enum
  rather than the outcome and stayed green for the life of the bug.
- The schedule that looked enforced and was not — *"configure 0 Workers at
  the weekend; start a Worker on a Saturday"*.
- `rite update` destroying prose under a promise never to — *"edit a
  generated section; run the update; confirm the edit survives"*.
- `rite sandbox destroy` discarding unapplied work — *"create work, do not
  push it, destroy"*.
- The resume that returned a fresh session with no context, and the
  supervisor that restarted a session a human had deliberately quit.
- **Every instance of the written-tested-called-by-nothing class**, because a
  scenario exercises a path and an uncalled function presents as missing
  behaviour rather than as missing coverage. `manager_to_start` implemented
  the 0/1/2+ rule, had passing unit tests, and was called by nothing; a
  scenario running `rite start` with two Managers configured fails at once.

**And what it would NOT have caught**, which matters as much, because a gate
sold as catching everything gets trusted where it should not be:

- Credentials readable on the process table. Invisible from outside the
  behaviour — the command works exactly as specified while doing it.
- The suite hanging on a real keychain prompt; 19 tests never running in CI.
  Infrastructure, not behaviour.
- Documentation claims that were false — 61 decisions described as 52,
  multi-machine described as single-machine. A scenario tests the code.
- Path escapes such as `rite remove worker ..`, **unless** the requirements
  say what an invalid name does. They are caught only where someone thought
  to require it, which is an argument for requirements that name hostile
  input rather than for scenarios alone.

So: most of the behavioural defects, none of the security-invisible,
documentation or infrastructure ones. That is a strong result and a bounded
one, and it is the honest basis for adopting this.

#### Three conditions, each with an artifact a gate can check

A condition a reviewer can only agree with is not a condition. Each of these
names something that exists in the tree, so "did this happen" is answerable
without forming a judgement.

**1. Independence, enforced by ORDER rather than by attestation.**
The scenarios for a phase are committed **before** the implementation branch
is created, and that is the check: `git merge-base` on the scenario file and
the branch. It is not fakeable by a reviewer who reads the diff first,
because when the scenarios were written there was no diff.

Ordering is used instead of authorship because authorship is unverifiable
after the fact — nothing distinguishes a scenario derived from the
requirements from one reverse-engineered out of the code and signed by
somebody else. The scenario file also records `derived_from:`, naming the
requirement or ticket each scenario comes from; a scenario that cannot name
one is testing the implementation.

**THE SINGLE-OPERATOR CASE IS NOT AN EXCEPTION, IT IS THE COMMON CASE.**
rite is routinely run by one person, and a condition with no defined
behaviour there is a condition that gets skipped silently. So: with no second
author available, the ordering requirement stands alone and independence is
recorded as **not satisfied** — `independent: false` in the scenario file.
The gate does not block. It degrades, visibly, and the merge record says
which check was weaker. A skipped condition that announces itself is worth
more than one that quietly does not apply.

**2. Self-evidencing runs — machine-captured, not pasted.**
Results go in `.rite/scenarios/<phase>/run-<timestamp>.log`, written by
redirecting the command's own output, and each entry carries the command,
its output, and **its exit code read directly**. A hand-pasted transcript is
not a result: it is a claim about a result, and it is editable.

The gate checks the file exists, names every scenario in the plan, and
carries an exit code for each.

⚠ **This closes presence, not fidelity, and the difference is the whole
lesson of the release that motivated this.** Four times in v0.5.0 a check
produced clean-looking output while exercising nothing — a wrong CLI
invocation read as "does not escape", a test file nothing collected, a suite
that skipped 19 tests for a missing binary, a harness reported working that
had never attached. A transcript proves a command ran. It does not prove the
command was the right one. **The residual risk is stated rather than
engineered away, because nothing in this section removes it** — the only
known defence is the independence of whoever wrote the invocation, which is
the condition above and the one that degrades first.

**3. Depth declared, not assumed.**
The implementation plan states a tier — `cli`, `service`, or `ui` — and a
scenario count for it. The gate checks the declared tier against the project's
own modules: a project with a frontend module declaring `cli` is refused, so
depth cannot be downgraded by classifying it away.

**UI scenarios are slow, flaky, and lie in both directions.** Measured on a
sibling project this week: a QA run reported "does not reproduce" for a real
defect because keyboard focus did not survive a batched interaction — the
scenario ran, its assertions passed, and the bug was present throughout. They
carry their own budget line, and their green is **not** equivalent to a CLI
scenario's green; a plan that treats it as equivalent is drawing a conclusion
the evidence does not support.

#### Who decides, and what happens when they disagree

**The phase's reviewer decides sufficiency, not the implementer**, and the
decision is recorded with the merge. A gate with no named decider defaults to
whoever holds merge rights, which is usually the person whose work is being
gated.

Where the scenario author and the implementer disagree about whether
behaviour matches the requirement, **the requirement is the authority, and if
it is ambiguous the disagreement is the finding** — it is escalated as an
unanswered question rather than settled by whoever is more insistent. That
ambiguity surfacing here, before merge, is the second benefit of this gate
arriving one stage earlier than usual.

#### Where the gate sits

It composes with batching the terminating review **per phase rather than per
ticket**: one gate at the point where a phase branch reaches trunk, carrying
two checks — the terminating review of the code, and the requirements-derived
scenarios of the behaviour. They answer different questions. The review asks
"is this code correct"; the scenarios ask "is this what was asked for", and
v0.5.0 shipped a release where the second question had no answer because
nobody had been assigned to ask it.

---

## 8. Configuration

`.rite/` holds configuration files and two knowledge directories:

```
.rite/
├── brief.yaml              # project description, from `rite init`
├── modules.yaml            # module registry
├── config.yaml             # operational settings
├── review-checklist.md     # project-wide review checklist
├── context/                # project-specific knowledge (indexed)
│   ├── INDEX.md            # when-to-consult triggers
│   └── *.md                # individual context files
├── kb/                     # user-provided reference material (committed)
│   ├── INDEX.md            # source tracking + fetch dates
│   ├── *.md                # authored files, summaries, references
│   └── .cache/             # fetched link caches (gitignored)
├── gitleaks.toml           # publish gate rules (optional)
├── gitleaksignore          # suppressed findings
├── .schema_version         # config schema this project is on (§9.9)
│
│   # Runtime state below — written by rite as it runs, not by `rite init`.
│   # Listed because "what is this file?" is otherwise unanswerable, and
│   # because §2.0's durable-state rule is about exactly these.
├── claims.json             # the claims ledger (§5.2)
├── force-releases.jsonl    # who force-released what, and why (§5.2)
├── handover/<session>.json # continuous handover snapshot, one per session
│                           #   (§9.10.1). A bare `handover.json` is the
│                           #   legacy unkeyed path: still READ so a project
│                           #   written by an older rite keeps working, never
│                           #   written any more.
├── heartbeats/<worker>.json  # last beat per worker (§3.5)
├── outbox/                 # queued messages awaiting backend contact (§9.10)
├── pool.json               # coordinator pool slots (§2.5)
├── pool-archive.jsonl      # retired slots + released claims (§2.5.10)
├── coordination-cost.json  # refusal/contention counters (§2.7.2, D-45)
├── schedule-state.json     # last schedule window acted on (§2.7.3)
├── scheduler-last-tick     # when the scheduler last ran (§9.12)
├── scheduler.lock          # one tick at a time (§9.12)
├── scheduler.log           # what cron/launchd's ticks wrote (§9.12)
└── *.lock                  # flock sidecars, one beside each durable file
                            #   above — machinery, not state. Held while a
                            #   writer is in its critical section; see
                            #   `rite_ai.state.locked` for why the lock is
                            #   NOT taken on the data file itself.
```

| Path | What it holds | Who writes it |
|------|--------------|---------------|
| `brief.yaml` | What the project IS — name, role, technology, architecture. | `rite init`. Not appended to afterwards: follow-ups go into the project spec (§9.13.1, D-53). |
| `modules.yaml` | What repos/packages the project contains. | `rite init` (detects existing repos), `rite add module`, hand-edited. |
| `config.yaml` | How rite operates — ticket backend, expertise, publish gate, credentials. | `rite init` (basics), hand-edited for advanced config. |
| `context/` | Project-specific knowledge — conventions, architecture decisions, domain rules. | Both user and Claude, as the project progresses. |
| `kb/` | User-provided reference material — coding standards, algorithms, domain knowledge. Authored files committed; fetched caches in `.cache/` gitignored. | User via `rite kb add`, caches refreshed with `rite kb refresh`. |

### 8.1. `brief.yaml`

Generated by `rite init`'s questionnaire (§9.3). Fixed lists cannot follow up —
"event sourcing, CQRS" deserves three more questions, and people skip what they do
not understand — so the follow-ups happen in `/spec` (§9.13.1), and the answers are
written into the project spec: a settled answer becomes a decision row, an
unanswered one an open question.

**`brief.yaml` is not enriched in place (D-53).** Earlier versions told the first
Claude session to append follow-ups here under an `enriched:` section. Nothing parsed
it — `ProjectBrief` never had the field — and its only reader was the
`reviewer-decisions` agent, so answers recorded there reached no Worker and no
command. `rite doctor` reports an `enriched:` section it finds, so a project that
followed the old instruction can move those answers into its spec.

```yaml
project:
  name: my-project
  role: owner                    # owner | manager
  root_branch: main              # gitflow setups name develop, next, etc.

what:
  kind: full-stack               # backend | frontend | mobile | full-stack | library | other
  features: "Order tracking with a customer-facing dashboard"

technology:
  platform: linux
  languages: [python, typescript]
  frameworks: [fastify, react]
  architecture: ""               # free text: "event sourcing, CQRS", etc.
```

### 8.2. `modules.yaml`

```yaml
modules:
  backend:
    path: backend/
    url: git@github.com:org/backend.git
    branch: main
    description: "Fastify API server + Postgres"

  frontend:
    path: frontend/
    url: git@github.com:org/frontend.git
    branch: main
    description: "React SPA"

  shared:
    path: shared/
    # no url — local-only module, no remote
    description: "Shared types and constants"

  app:
    path: app/
    branch: main
    description: "iOS client"
    commands:                    # optional — only what detection gets wrong
      test: xcodebuild test -project NewsApp.xcodeproj -scheme NewsApp -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -skipMacroValidation
```

**`commands`** is the guarantee; detection (§9.3) is the convenience. It records
a module's `install`, `build`, `test`, `lint` and `format` — the commands a
session needs to verify its own work — and each recorded command **overrides**
detection for its own key, while unrecorded keys fall back to detection. Any
ecosystem detection does not cover is one block here, not a wall. The block is
written only when something is recorded, so existing files are not rewritten,
and `rite add module` / `rite remove module` carry every module's block through
their rewrites. Comments inside `modules.yaml` are not preserved by those
rewrites; the values are.

Where it is read, and when:

- **`rite prepare`** prints every one of the Worker's modules' commands, resolved
  at that moment, before each task. This is the reader a correction reaches
  immediately.
- **`rite doctor`** shows, per module, each key as `configured`, `detected` or
  `missing`, with the command it resolved to. A module with no `test` command is
  a problem (non-zero exit): a Worker that cannot run tests cannot check its own
  work, and that should be visible before a run.
- A Worker's `CLAUDE.md` lists them as of `rite add worker` (§9.6), and the project
  root's `CLAUDE.md` as of `rite init`. Neither is regenerated.

**Unknown keys are an error, not ignored.** `parse_modules` refuses any key
outside the ones shown — including a command written flat (`test:` instead of
`commands: {test: …}`), which it names — and every writer stops on that error
before writing. An ignored key is a setting its author believes they made, and
the next `rite add module` would have dropped it from the file. The known keys
come from the dataclasses, so a new field needs no second edit. Nothing is
retired: a v0.1.0 `modules.yaml` carries no other key.

### 8.3. `config.yaml`

```yaml
ticket_backend:
  type: jira                     # jira | github | none
  site: myteam.atlassian.net     # JIRA only — a pasted browser URL
                                 # ("https://myteam.atlassian.net/...") is
                                 # accepted and normalised to the host
  repo: myorg/myrepo             # GitHub only — "owner/name"; unused for jira
  projects:
    board: PROJ
    workers: WORK
    testing: TEST
  credential: jira_token         # key name in keychain / env var

expertise:
  alice:
    tags: [engineering, infrastructure]
  bob:
    tags: [engineering, frontend, ui-ux]

publish_gate:
  scan_patterns:
    # Whatever identifies your org, its customers, or the work — rite ships
    # none of these and cannot guess them (§11.3). Two shapes, as examples:
    - type: regex
      pattern: "ACME-[0-9]{4,}"
      description: "Internal account number"
    - type: regex
      pattern: "\\b(Northwind|Contoso)\\b"
      description: "Customer names under NDA"
    - type: path
      pattern: "/Users/*/"
      description: "Hardcoded home directory path"
  gitleaks_config: .rite/gitleaks.toml

spec:                              # where this project's design already lives (§9.13)
  paths: [SPEC.md, docs/adr/]      # POINTERS, never copies — D-52
  convention: "Decisions are cited as D-<number>; the register is in `SPEC.md`."

heartbeat:
  interval_minutes: 10
  stall_threshold: 3             # missed heartbeats before marking stalled

watchdog:
  interval_minutes: 5             # cheap, non-LLM liveness check cadence (§3.5)

pool:
  coordinator_standby: 2          # standby Manager/Owner sessions, fixed not worker-proportional (§2.5.4)
  warn_threshold: 0.5             # Manager warns when live pool drops below this fraction (§2.5.6)
  lease_expiry_minutes: 15        # readiness lease (§2.5.2) — a slot unverified for this
                                   # long drops out of the LIVE COUNT. Reporting only:
                                   # nothing is retired or released on this threshold.
  archive_after_minutes: 30       # how long a slot must be CONTINUOUSLY, PROVABLY
                                   # unreachable before `rite pool archive` retires it and
                                   # force-releases its claims (§2.5.10). Deliberately
                                   # separate from, and more conservative than, the lease
                                   # above — dropping out of a count is reporting, archiving
                                   # is destructive.

sandbox:
  enabled: false                  # opt-in (§5.3). Governs SETUP, not command
                                   # availability: scoped-token provisioning in `rite add
                                   # worker`, and whether `rite doctor` treats a missing
                                   # yoloai as a problem or a note. `rite sandbox start`
                                   # works either way.
  backend: seatbelt                # seatbelt | docker | podman | tart — yoloAI's literal
                                   # `--backend` values, verified via `yoloai system backends`.
                                   # "seatbelt" is yoloAI's name for macOS's sandbox-exec
                                   # mechanism (D-30); "sandbox-exec" itself is NOT an accepted
                                   # value.
                                   # NOTE: seatbelt has NO network isolation (D-30) — use
                                   # docker/podman/tart when containing untrusted work that
                                   # must not reach the network.
  token_permissions: [contents, pull_requests]   # minimum scope (§5.3.3)
  max_concurrent_workers: 5       # per Manager — bounds context concentration (§2.5.9)

budget:
  weekly_quota_pct: 95            # burn-rate MEASUREMENT target — reporting only, no
                                   # adaptive control (§2.6); the schedule below is
                                   # what actually governs concurrency
  weekly_token_budget: null       # absolute token figure for "100% of quota". rite has no
                                   # API to read a plan's quota size, so this is the only
                                   # side of §2.6.2's choice a local script can compute a
                                   # percentage from. null → report the raw rate only: no
                                   # percentage, no early-exhaustion warning.
  week_start_day: monday          # boundary for "week-end projection". A fixed documented
                                   # convention (ISO week), NOT a discovered fact about any
                                   # Anthropic billing cycle — override if yours differs.

schedule:
  timezone: Europe/Warsaw          # OPTIONAL (D-48) — unset means this
                                   # machine's own clock; `rite start`
                                   # prints which zone it resolved
  windows:
    - hours: "09:00-18:00"
      workers: 3
    - hours: "18:00-09:00"
      workers: 0                   # clean stop, not a kill (§2.7.3)
```

#### 8.3.1. Which defaults above mean something

Every value in `config.yaml` is configurable, so the risk is not that a default is
wrong — it is that a default carried over from one project reads as a
recommendation. Stated plainly, once:

**Defaults with a basis.** `sandbox.backend: seatbelt` (verified against `yoloai
system backends`, D-30). `budget.weekly_quota_pct: 95` (§2.6's stated goal — land
near quota rather than under it). `pool.coordinator_standby: 2` (§2.5.4 reasons
about it: fixed and small, because session-list usability is what a large pool
costs). `pool.archive_after_minutes: 30` against `pool.lease_expiry_minutes: 15`
(§2.5.10's deliberate asymmetry — reporting is cheap to get wrong, force-releasing
another session's claims is not). `budget.week_start_day: monday` (a documented
convention, explicitly not a discovered fact about anyone's billing cycle).

**Defaults that are placeholders.** `heartbeat.interval_minutes: 10` with
`stall_threshold: 3` — nothing anywhere justifies these, and together they mean a
dead Worker goes unnoticed for half an hour, which is a workflow judgement nobody
made. `watchdog.interval_minutes: 5` — §3.5 says "~5 minutes" without saying why.
`pool.warn_threshold: 0.5`. `sandbox.max_concurrent_workers: 5` — illustrative, and
meant to be picked from real context-size usability rather than guessed.

(D-6's ~2-hour expert timeout is deliberately NOT in that list: it is neither a
`config.yaml` key nor implemented — a design intent with no code and no default.
Naming it among shipped defaults would be the exact error this subsection exists
to stop.)

None of these is wrong. They are unmeasured, and a user tuning them is not
departing from a recommendation, because there isn't one. Replace each with a
measured value or a stated rationale as the evidence arrives; do not leave this
list to rot once it exists.

### 8.4. Worker manifest

`<project-root>/workers/<name>/worker.yml`:

```yaml
worker:
  name: alpha
  manager: alice
  modules:
    - backend
    - frontend
  claude_instructions: |
    You are Worker alpha. Your Manager is alice.
    ...
```

### 8.5. `VERSION`

A plain file at the repo root containing the semver string (e.g. `0.1.0`). This is
the single source of truth — `pyproject.toml` reads it at build time rather than
duplicating it. `rite --version` reads it at runtime.

### 8.6. Project context directory (`context/`)

**Why this exists, measured.** The upstream project's `CLAUDE.md` reached **74 KB** and
is loaded on every turn of every session — every session pays for all of it regardless
of relevance. That is plausibly a larger token cost than any model-routing change. An
indexed directory means a session reads only what it needs.

`CLAUDE.md` carries only the basic rite material: role, module list, the review
convention, the claims system, and pointers to `context/` and `kb/`. Project-specific
knowledge — database conventions, API patterns, architecture decisions, domain rules —
lives in separate `.md` files under `.rite/context/`, navigated by an index.

#### The index (`context/INDEX.md`)

The index determines whether the directory works at all. Each entry needs three
things: a filename, a one-line description, and **when to consult it** — triggers,
not topics.

```markdown
# Context Index

| File | When to consult | Description |
|------|-----------------|-------------|
| `database.md` | Before writing migrations or changing schema | Postgres naming, index strategy, migration conventions |
| `api-conventions.md` | Before adding or modifying API routes | REST patterns, error shapes, auth headers |
| `deployment.md` | Before changing CI, Docker, or infra config | Pipeline stages, env vars, secrets handling |
| `testing.md` | Before writing or modifying tests | Test naming, fixture patterns, what to mock |
```

`database.md — read before writing migrations` is actionable.
`database.md — about the database` is not. The trigger is the difference between
a session that reads the right file and one that reads everything or nothing.

**Maintenance:** both the user and Claude update context files as the project
progresses. **Adding a file without an index entry is a defect** — `rite doctor`
checks for orphaned files and missing entries. Removing an index entry without
removing the file is also a defect.

**Size guideline:** aim for **20–30 files**, each under **4 KB**. Knowledge grows
without bound, and a 200-file directory with a vague index is worse than none — the
index itself stops being readable. When a file grows past 4 KB, split it. When the
directory grows past 30 files, consolidate related files. `rite doctor` warns at
these thresholds.

#### 8.6.1. Coordinator context growth is a different problem from `CLAUDE.md` size

The measurement above (74 KB `CLAUDE.md`, loaded every turn) justifies the
`context/`+`kb/` index split, and that split is still correct — but it addresses
**static instruction-file size**, which is not a long-running coordinator's real
cost. An Owner/Manager Dispatch session's context grows from **accumulated
conversation history** — tool outputs, worker reports, ticket detail — not from
`CLAUDE.md`, and that growth is measured at **~500K tokens per turn, ~98% of
spend** on this project's own coordinator session: two orders of magnitude past
the instruction-file problem this section solves.

Three things follow, and they are one theme — **keep the coordinator thin** — not
three separate fixes:

- **Workers report conclusions, not transcripts** (§3.1.1) — the report entering
  the coordinator's context should be small regardless of how much work produced
  it.
- **The multi-project hub stays thin** (§8.9) — it delegates to per-project
  Managers rather than holding every project's detail itself, for the same reason
  a Manager shouldn't hold every Worker's full transcript.
- **The handover snapshot (§9.10.1) is also a context-growth mitigation, not only
  a session-loss one.** Because a fresh session can cheaply reconstruct full state
  from the snapshot plus durable files, **periodically restarting a long-running
  coordinator session is a legitimate, low-cost way to reset its accumulated
  context back near zero** — an intended use of the handover mechanism, not
  merely a crash-recovery side effect. A coordinator that has been running long
  enough to accumulate hundreds of thousands of tokens of context is a candidate
  for a deliberate restart, not just an unplanned one.

### 8.7. User knowledge base (`kb/`)

User-provided reference material: principles and coding patterns the user wants
applied, and specialised knowledge the work requires — algorithms, dos and don'ts,
proprietary material, niche domain knowledge. This is how a user teaches rite their
standards rather than accepting defaults.

#### The organising principle

**Authored content is committed. Fetched content is cached and gitignored.**

This is the split that makes the directory structure principled rather than arbitrary:

- **Authored knowledge** — principles, patterns, domain notes, internal algorithms,
  coding standards the team wrote — is a team asset. It is committed, versioned,
  reviewable, and new members inherit it automatically.
- **Fetched link caches** — snapshots of external pages — are reproducible from source,
  potentially large, and possibly licence-encumbered. They are cached locally and
  gitignored by default.

`rite init` asks whether to gitignore the knowledge base directory itself. The default
is **committed** (with the fetched cache subdirectory gitignored), and the reasoning is
stated at the prompt: gitignoring means everyone keeps a private copy, which defeats the
purpose of shared knowledge. The user can override this — some teams may want KB local
only — but it is an explicit choice, not a silent default.

#### Links: fetch with caching

`rite kb add <url>` fetches the page, extracts the content, and stores a local
Markdown summary. **Cached so repeated reads are free and offline works; refreshable
on demand with `rite kb refresh`.**

The index entry in `kb/INDEX.md` records the source URL and is committed. The fetched
cache file lives in `kb/.cache/` (gitignored) and records the fetch date in its
frontmatter:

```markdown
---
source: https://example.com/coding-standards
fetched: 2026-09-09
type: link-cache
---

# Example Corp Coding Standards

(summary of the page content)
```

A session reading a cache file six months old can tell it is stale. `rite kb refresh`
re-fetches all link-type entries and updates the cache. It shows a diff of what
changed so the user can review before accepting. A fresh clone gets no cache files —
run `rite kb refresh` after cloning to populate them.

#### Files

`rite kb add <file>` copies the file into `kb/` and registers it in the index. For
Markdown and plain text, the content is used directly. For PDFs and other formats,
rite extracts text and stores a Markdown summary alongside the original. Authored
files are committed (they are the team's knowledge, not a reproducible cache).

#### Proprietary content and the publish gate

The knowledge base is exactly where proprietary and niche material will live —
internal algorithms, competitive analysis, domain expertise that is the user's edge.
And rite publishes things.

##### The gate scans COMMIT MESSAGES, not only the diff

⚠ **This is the more important half of what the gate does, not a curiosity
— and gitleaks does not do it on its own**, which is why rite relays
messages through it deliberately (`scan_commit_messages`).

The asymmetry is what makes it matter: **a secret in a FILE is fixed by a
commit; a secret in a commit MESSAGE is fixed only by rewriting history.**
rite is forbidden from rewriting history — `tests/test_blast_radius.py`
asserts rite's own source contains no `filter-branch`, no `reset --hard`,
no force push — so the remedy is one the human has to perform by hand, on a
published repo, after the fact.

Observed while writing §9.15.3a: the gate refused a path in a file, the
path was corrected, and the gate then refused **the same string quoted in
the commit message explaining the correction.** Both refusals were right.

**Explicit rule: shared with the team, never published.** The `kb/` directory is
committed to the project repo (so all team members have it) but the publish gate
(§11) scans it **hardest**. "Our internal algorithm for X" is more damaging in a
public repo than an API key, because you can rotate a key. The gate's default rules
flag any content in `kb/` that also appears in files outside `kb/` — the usual leak
path is copying a snippet from a reference into source code.

#### Third-party material and licensing

Copying documentation or book content into a repo may breach the original licence.
**Prefer reference-by-link with a local summary over wholesale copying.** The
snapshot mechanism (link → fetch → summarise) produces a summary, not a copy. The
original URL is recorded so a reader can consult the source.

`rite kb add --full <url>` stores the full content for cases where a summary loses
essential detail (an algorithm, a specification). The user is warned that full-copy
may have licensing implications, and the file is flagged in the index as `full-copy`
so it can be reviewed before publishing.

### 8.8. Technology choices

rite is project- and technology-agnostic — it coordinates any codebase. rite itself
is built with:

- **Python 3.11+** (D-8). Modern type hints (`str | None`), `tomllib` in stdlib,
  `ExceptionGroup`, `TaskGroup`.
- **CLI: `click`** (D-10).
- **Licence: MIT** (D-22).

#### Distribution

Two paths from the start:

- **`pipx install rite-ai`** — the primary route. Isolated environment, no conflicts
  with system Python. `pipx` is the recommended way to install Python CLI tools.
  The distribution is `rite-ai`, not `rite`: the PyPI name `rite` belongs to an
  unrelated, actively-maintained package (so PEP 541 offers no route to it) whose
  wheel also ships a `rite` console script AND occupies the top-level import name
  `rite`. Hence three distinct names — distribution `rite-ai`, import package
  `rite_ai`, command `rite` (with `rite-ai` installed alongside as a collision-free
  alias). Isolation is what makes the shared command name safe; see D-24.
- **`curl … | sh` install script** — for someone who wants to try the latest version
  quickly. Downloads the latest release, installs into an isolated venv, and symlinks
  the binary.

Homebrew is deferred until traction warrants it. `rite update` is designed so adding
a Homebrew tap later does not require rework — it detects the installation method and
delegates to the appropriate updater (`pipx upgrade`, `brew upgrade`, or
re-downloading the script).

### 8.9. Multi-project registry (the Dispatch directory)

**Additive only — a single-project setup (§8's existing structure) needs none of
this and is unaffected.** For a human coordinating several unrelated projects,
rite adds one more directory, **owned by the human's Dispatch hub, not by any
project**:

```
~/.rite/dispatch/                # the Dispatch directory — NOT inside any project's .rite/
├── CLAUDE.md                    # hub instructions — thin: "here is the registry, spawn/attach
│                                #   to a per-project Manager rather than holding project
│                                #   context yourself" (§8.6.1)
├── config.yaml                  # hub-level settings (default alias, notification prefs)
├── projects.yaml                # the registry — see below
└── handovers/                   # per-project handover snapshots the hub last saw (§9.10.1)
    └── <alias>.json
```

`projects.yaml` — **addressing only, never live state** (per §2.0: state lives in
each project's own `.rite/` and coordination repo, never in the registry):

```yaml
projects:
  backend-platform:
    path: ~/work/backend-platform
    role: manager            # this machine's role for this project
  acme-app:
    path: ~/work/acme
    role: owner
```

An alias resolves to a `path` the same way a bare `rite start .` resolves to the
current directory — the registry is a lookup table over directories, not a
hierarchy replacing them, and a project's own `.rite/` is completely unaware the
registry exists.

**CLI additions** (§9.1), purely additive:

```
rite projects list                     # show registered aliases, paths, roles, last status
rite projects add <alias> <path>       # register a project under an alias — checks
                                        #   aggregate scheduled load across all registered
                                        #   projects and warns before saving if the
                                        #   projected quota sum would exceed the hub's
                                        #   warning threshold (§2.7.4)
rite projects remove <alias>           # deregister (does not touch the project's .rite/)

rite start <alias>                     # resolves alias via the registry, then behaves exactly
rite stop <alias>                      #   as `rite start/stop <path>` does today (§9.10) — no
                                        #   new lifecycle semantics, just a second way to name
                                        #   the target directory
```

`rite start [<dir>]` / `rite stop [<dir>]` (§9.10) are **unchanged** for anyone not
using the registry — a bare directory argument (or none, defaulting to `.`) still
works exactly as specified, with no dependency on `~/.rite/dispatch/` existing at
all. `rite start <alias>` is sugar over the same function once the alias resolves
to a path.

**`rite status` becomes context-sensitive:** run inside a project root (as today,
§9.8), it reports that project's detail unchanged. Run from anywhere with no
project in scope but a Dispatch directory present, it aggregates: one row per
registered project — alias, role, health (via that project's own heartbeat/lease
files), coordinator-pool depth (§2.5), blocked-ticket count. This is a new
rendering path in the same command, not a second command, and it degrades to "no
Dispatch directory found — run `rite status` inside a project, or `rite projects
add` to register one" when there's nothing to aggregate.

**The constraint that makes this safe:** per §2.0, a per-project Manager must be
reconstructable from that project's own files without the hub session's memory.
The registry existing or not existing changes nothing about any individual
project's durability — losing `~/.rite/dispatch/projects.yaml` costs only the
alias list (itself a small checked-in YAML file, trivially regenerated by `rite
projects add`), never a project's actual state.

---

### 8.10. Names that reach a human say which project they belong to

**Convention, stated once so new surfaces inherit it rather than being fixed
one at a time.** Any identifier a person reads in a listing — and especially
any identifier a person might act on — carries the project.

This is the same principle as §8.9's `● <project>` message labelling, applied to
names instead of messages. A message arriving alone needs to say what it is
about; a name in `tmux ls` or `yoloai ls` needs to say what it belongs to, for
the same reason and on the same machine.

**The failure it prevents is not confusion, it is acting on the wrong thing.**
rite is built for a machine running several projects at once, and every project
names its workers `w1`, `w2`, `w3`. A sandbox called `rite-w1` is ambiguous
across two projects, so `rite sandbox destroy w1` typed in the wrong directory
destroys another project's worker — a destructive mistake reachable by typing
the correct command in the wrong place.

**The form** is `rite_ai.label.project_slug(root)`: `<readable-name>-<6 hex of
the resolved path>`, e.g. `acme-3f9a2c`. Both halves earn their place. The
name is what the human reads. The hash keeps it unique, because two projects
legitimately share a name — two checkouts, two clients' `backend` — and a
listing showing the same name twice is worse than one showing a path, since the
reader believes it is unambiguous.

| surface | name |
|---|---|
| yoloAI sandbox | `rite-<slug>-<worker>` |
| pool tmux session | `rite-pool-<slug>-<index>` |

The leading `rite-` stays: it is what separates rite-managed sandboxes from a
human's own, and `sandbox.count_active_sandboxes` filters the worker cap on it.

**Two consequences, recorded rather than discovered later:**

- **Renaming a project changes its slug**, orphaning sessions and sandboxes
  created under the old one. They remain visible, under a name that still says
  which project they came from. That is the accepted cost of putting a
  human-meaningful name first; the alternative is a listing nobody can read.
- **Names created before this convention still resolve.** `stop`, `destroy`,
  `pane` and `status` fall back to the pre-§8.10 `rite-<worker>` when a sandbox
  is genuinely running under it, so an existing sandbox is not stranded by an
  upgrade. Nothing creates that form any more.

---

### 8.11. What a project commits — Phase 1

**Everything under `.rite/` is gitignored except the files the team shares.**
Shared: `brief.yaml`, `modules.yaml`, `config.yaml`, `review-checklist.md`,
`.schema_version`, `context/`, and `kb/` (§8's layout already marks that one
"committed"; §7 already requires checklists committed per repo). Ignored:
everything else under `.rite/`, plus `workers/`, which holds this machine's
clones of the module repos (§2.1).

**Ignored by default, not by enumeration.** The rule is `.rite/*` plus a
re-include per shared file, so a runtime file added later is ignored because
nobody did anything. An enumerated list was tried first and drifted within
weeks: it named `.rite/handover.json` and `.rite/handovers/` but not
`.rite/handover/`, the per-worker directory that replaced the first, so every
worker's snapshot was landing in git. The commits that introduced the gap had
no reason to be thinking about gitignore, which is the argument against a
list of things to remember.

**Why none of it can be committed in Phase 1.** Runtime state is meaningful
only on the machine that wrote it — a claims ledger names this machine's
workers, a heartbeat is a local clock reading, the outbox is an undelivered
queue. Committing it puts one machine's ephemera into shared history and
conflicts on every pull. Nothing reads any of it out of version control: the
cross-machine transport for handovers is the Dispatch hub (§8.9) at
`~/.rite/dispatch/`, a local directory.

**⚠ This is a Phase-1 answer, not a permanent one.** Phase 2's state branch
(§3.3) makes claims and messages *shared coordination state* — the point of
the design is that other machines read them. When that lands, the question
"what does a project commit?" is reopened for exactly the files this section
ignores, and the answer for some of them becomes the opposite. Revisit this
section with §3.3, not independently.

**Adopting the rule does not untrack what is already tracked.** A project
that ran an earlier rite has these files committed, and a `.gitignore` entry
does not change that. `rite doctor` compares against what git *actually*
tracks rather than against `.gitignore`, reports what it finds, and names
`git rm --cached` as the remedy. It never runs it: untracking destroys no
content but is still a change to someone's repo, and the same reasoning that
stops `rite init` writing into a shared hooks directory applies.

---

## 9. CLI

The CLI covers the **non-AI surface** — setup, workspace management, claims, status,
publishing. Manager mode, refinement, and similar stay in Dispatch chat. Implementation
is `click` — better than `argparse` for nested commands, and it gives `--help` on every
subcommand, exit 0 for help, and exit 2 for misuse for free.

Actual logic lives in scripts under `src/cli/`, with one file (`src/cli/main.py`)
exposing the CLI interface that calls them.

### 9.1. Command reference

```
rite init                          # interactive project setup (§9.3)
rite init --config <file> [--yes]  # non-interactive setup from a file

rite add worker <name>             # create a worker workspace
rite add module <name> [git-url]   # register (and optionally clone) a module

rite remove worker <name>          # remove a worker workspace and deregister
rite remove module <name>          # deregister a module (does not delete files)

rite prepare --worker <name>       # sync a worker's workspace before a task (§2.1):
                                    #   right repos, right branches, no residue.
                                    #   Idempotent; a dirty tree fails loudly rather
                                    #   than being discarded
rite prepare --worker <n> --branch <b>
                                    # prepare onto a specific ticket branch

rite status                        # what's happening: workers, tasks, board state
rite doctor                        # is this healthy: tokens, deps, init state, versions
rite heartbeat --worker <name>     # record a worker's "still alive" beat (§3.5); call
                                    #   every `heartbeat.interval_minutes`
rite budget                        # burn rate + week-end projection (§2.6.2). WHOLE
                                    #   MACHINE, not this project — the quota is
                                    #   account-wide (§2.6.1)
rite handover show                 # read the continuous handover snapshot (§9.10.1)
rite handover write                # write it now, rather than at the next trigger

rite claim <paths...> --worker <n> # claim paths for a worker (--worker is
                                    #   required; there is no ambient "current
                                    #   worker" anywhere in rite)
rite release [paths...]            # release claimed paths
rite release --history             # the force-release audit trail — "why did my
                                    #   claim disappear?" (§5.2)
rite release --force <paths...> --by <name> --reason <text>
                                    # force-release another session's paths, with
                                    # attribution and a recorded reason (§5.2)

rite board create/move/list/query/show/label/link/assign
                                    # mechanical ticket-backend CRUD (§6.1) — NOT
                                    #   `rite ticket`, which names the Dispatch
                                    #   end-to-end workflow instead (D-40).
                                    #   `list`/`query` say when more rows exist
                                    #   rather than silently truncating a page

rite pool fill [--count N]         # top up the coordinator-redundancy pool (§2.5.7)
rite pool status                   # live/stale split (§2.5.2). Read-only: spawns no
                                    #   session, writes no state, costs no tokens
rite pool archive [--dry-run]      # retire provably-dead slots and release the claims
                                    #   they died holding (§2.5.10). Refuses when the
                                    #   liveness probe cannot run
rite pool history [-n N]           # what `pool archive` retired, and which claims went
                                    #   with it — reads `.rite/pool-archive.jsonl`

rite projects list                 # show registered projects: alias, path, role, status (§8.9)
rite projects add <alias> <path>   # register a project under an alias
rite projects remove <alias>       # deregister (does not touch the project's .rite/)

rite schedule show                 # this project's worker schedule + timezone (§2.7)
rite schedule set <hours> <n>      # set worker count for an hour range, e.g.
                                    #   'rite schedule set 09:00-18:00 3'
rite schedule set <hours> 0        # off for that window — a clean stop (§2.7.3),
                                    #   not a kill
rite schedule set-timezone <tz>    # OPTIONAL (D-48) — unset runs on this machine's
                                    #   clock; set it when the project spans machines;
                                    #   'rite init' also asks for this in Section 6

rite watchdog                      # cheap, non-LLM liveness check (§3.5). Exit 0 when
                                    #   nothing needs judgement, 1 when something does
rite scheduler-tick                # one scheduler cycle: the watchdog check plus the
                                    #   schedule window-boundary handover (§2.7.3, D-46).
                                    #   This is what `scheduler install` wires up
rite scheduler install             # register the tick with cron/launchd, at
                                    #   `watchdog.interval_minutes` (§9.12)
rite scheduler status              # is it registered (cadence readback is not
                                    #   implemented — see §9.12)
rite scheduler uninstall           # deregister it

rite sandbox start <worker> [--ticket ID | --prompt TEXT]
                                    # process-isolate a Worker's session via yoloAI (§5.3).
                                    #   Prepares the workspace first (as `rite prepare`)
                                    #   and refuses when it cannot. The opening prompt goes
                                    #   in as a prompt file. The Worker works on yoloAI's
                                    #   full copy (`:copy-all`, gitignored files included:
                                    #   the default `:copy` omits them and nested repos
                                    #   inside a git repository) of workers/<worker>/,
                                    #   so its work leaves only
                                    #   by being pushed; .rite/ is mounted writable and each
                                    #   local repository its clones fetch from read-only,
                                    #   which a module with such an origin cannot push to.
                                    #   Git inside is set to authenticate github.com through
                                    #   `gh`, not to sign, and to run only each repository's
                                    #   own hooks (`core.hooksPath=.git/hooks`), never global ones. The Claude login stored by
                                    #   `rite credential set claude` goes to yoloAI as
                                    #   CLAUDE_CODE_OAUTH_TOKEN; without one start says the
                                    #   session will do nothing, and `rite doctor` counts it
                                    #   a problem. Start prints the name for `yoloai attach`.
                                    #   `sandbox.enabled` does NOT gate these commands —
                                    #   it governs SETUP: scoped-token provisioning in
                                    #   `rite add worker`, and whether `rite doctor`
                                    #   treats a missing yoloai as a problem or a note.
                                    #   Refuses when the worker cap cannot be enforced
rite sandbox status <worker>       # is that Worker's sandbox running — exit 1 when the
                                    #   question could not be answered, which is not the
                                    #   same as "no sandbox"
rite sandbox stop <worker>         # stop it, keep it
rite sandbox destroy <worker> [--force]
                                    # stop it and discard its state; refuses while
                                    #   its copy holds work on no remote (§5.3.3)

rite review                        # run the review convention with checklists
rite publish check                 # dry-run the publish gate (§11) — exit 0 clean,
                                    #   1 stale suppressions, 2 findings, 3 could not run
rite publish install-hook          # install the pre-push hook into an already-
                                    #   initialised project. Exit 1 when git would
                                    #   never read it (§11.5.1) — `rite init` is not
                                    #   the remedy; it offers to wipe the config
rite publish pre-push              # range-scoped scan for the installed pre-push
                                    #   hook (§11.5) — reads git's pre-push protocol
                                    #   from stdin; not meant to be typed by a human

rite credential set <name>         # store a credential in the OS keychain
rite credential check <name>       # is it available — exit 0 yes, 1 no, so
                                    #   `rite credential check X && ...` guards correctly
rite credential rotate             # guided rotation of all stored credentials
rite credential list               # what is stored on this machine — read-only,
                                    #   prompts for nothing
rite credential remove <name>      # delete one from the keychain and the registry

rite context add <file> <trigger> <desc>
                                    # add a file to the project context index (§8.6)
rite context list                  # what's in the context directory, with triggers
rite context remove <file>         # drop one, and its index entry

rite kb add <url|file>             # add a link (snapshot) or file to the knowledge base
rite kb add --full <url>           # snapshot full content (licensing warning)
rite kb refresh                    # re-fetch all link snapshots, show diff
rite kb list                       # list KB entries with source and fetch date

rite spec add <path>               # point Workers at a design document (§9.13)
rite spec remove <path>            # stop pointing at one

rite spec index                    # inventory the spec's units and write
                                    #   .rite/spec/index.json; refuses a spec a slice
                                    #   cannot help (§9.13.2)
rite spec status                   # which units have no derived file, which are stale,
                                    #   hand-edited or never stamped, and how often a
                                    #   slice was not enough
rite spec slice <unit>             # print one unit, what it cites and the pinned hubs.
                                    #   Slice on stdout, measurement on stderr, so a
                                    #   Worker cannot read the measurement as spec text
rite spec show <unit>              # the DERIVED text for one unit, with the source
                                    #   range it came from. Exit 1 when it is stale,
                                    #   hand-edited or unstamped — and it prints it
                                    #   anyway, saying what is wrong
rite spec stamp <unit>... | --all  # record on each derived file the spec it was
                                    #   written from
rite spec verify [--strict]        # the gate: every unit covered, nothing stale,
                                    #   hand-edited or unstamped. 0 current · 1 drifted
                                    #   · 3 could not run. --strict also refuses two
                                    #   files covering one unit

rite start [<dir>]                 # bring rite up — Claude app setup, scheduled tasks
rite stop [<dir>]                  # shut down with handover — ticket comment, board update

rite update                        # update rite itself + migrate .rite/ config
rite help                          # friendly command list with examples
rite -v / --version                # version from VERSION file
```

### 9.2. Conventions

Borrowed from tools people already know — npm init, create-next-app, gh repo create,
sst init, Cookiecutter — rather than invented.

**What was borrowed and why:**

| Pattern | Source | Why |
|---------|--------|-----|
| Defaults in brackets, Enter to skip | npm init | Lowest cognitive load; universal convention |
| Auto-detect existing state before prompting | sst init, flutter create | Skip questions when answers are obvious |
| Group related questions by section | create-next-app | Long flows (10+ prompts) need structure |
| `--yes` for all defaults | npm init, Cookiecutter (`--no-input`) | Single flag for headless/CI mode |
| `--config <file>` for file-based non-interactive | Cookiecutter, Copier | Needed for testing rite itself |
| Dependent defaults (computed from prior answers) | Cookiecutter | Project name → derived values |
| Separate prompting from file writing | Yeoman | Collect all answers, then scaffold |
| Works in current directory | cargo init, git init | Not `rite new` — init is in-place |
| Examples in `--help` text | gh CLI | People copy rather than read |

**Terminal UX:**

- Skippable questions show `[default]` and accept Enter.
- Sections are visually separated with a header line.
- Progress: `[3/6]` section counter, not a progress bar.
- No wall of prompts — one question visible at a time, with the section header.
- Multi-choice uses arrow keys (click's `choice` type), not "type a number".
- Free-text fields accept empty (skip) unless marked required.

**Non-interactive mode:**

`rite init --config <file>` reads a YAML file with the same structure as `brief.yaml`
and produces `.rite/` without prompting. `--yes` accepts all defaults for fields not
in the file. Together: `rite init --config project.yaml --yes` is fully automated.

Needed for CI, scripted setup, and testing rite itself. A questionnaire that cannot
be automated cannot be tested.

### 9.3. `rite init` — the questionnaire

Run in the current directory, like `git init`. A longer interactive process that
produces `.rite/brief.yaml`, `.rite/modules.yaml`, and `.rite/config.yaml`.

**Pre-flight:**

- If `.rite/` exists: *"This directory is already initialised. Wipe and start
  over?"* [y/N]. Default No — accidental re-init should not destroy config.
- Scan for `.git` directories to detect existing repos.
- Scan for language markers (`package.json`, `pyproject.toml`, `Cargo.toml`,
  `pubspec.yaml`, `*.sln`, `go.mod`, `Package.swift`, `*.xcodeproj`,
  `*.xcworkspace`, `*.swift`) to pre-fill technology answers. Swift manifests are
  read with patterns, never by running `swift package dump-package`, which
  executes the manifest.
- Derive each module's commands (`install`, `build`, `test`, `lint`, `format`).
  For Node the **package manager decides whether any command works**: it is
  taken from `packageManager` in `package.json`, then from the lockfile in the
  module or the nearest directory above it up to the repository root, and only
  then defaults to npm. Lockfiles from two different managers leave the commands
  undetected with a note naming both — one is stale, and guessing fails in a way
  that looks like a broken project. Installs never rewrite the lockfile (`npm ci`,
  `--frozen-lockfile`, `--immutable`), because a rewritten lockfile is a dirty
  tree and `rite prepare` blocks on one. Anything detected wrongly is corrected
  in `modules.yaml` (§8.2), which overrides detection.

**The first question** is *"Do you have a spec or existing code for this
project? [y/N]"*, asked before anything else.

- **Yes** — *"Path: [.]"*, defaulting to the current directory, checked, and
  asked again until it exists: a mistyped path never falls through to the
  sections below. Then *"Reading <path> — languages, structure and conventions
  will be taken from what's there."* and one open question: *"Anything stale, or
  that you'd like changed? Free text, or Enter to skip."* The brief records the
  path and that answer as `source.path` and `source.changes`, and none of the
  sections below is asked. When the path is already a rite project, `init` says
  *"This is already a rite project — I'll apply your changes rather than starting
  over."* and records the answer in that project's brief; nothing else there is
  touched, an earlier answer is kept beside the new one, and Enter changes
  nothing. A preset answers it with `source.path` and `source.changes`; a
  `source.path` that does not exist is an error. `--yes` without one answers no.
- **No** — the sections below, unchanged.

**Section 1 — Role** `[1/7]`

```
─── Role ───────────────────────────────────────────
Is this the Owner machine or a Manager machine?

  ▸ Owner    — owns the board, assigns work, one per project
    Manager  — receives work from an Owner, runs its own workers
```

Required. Not skippable — nothing downstream works without this. **In Phase 1
(single machine), this only determines whether `rite init` fetches an existing
Owner's `config.yaml` for alignment (§2.4) — it has no other effect until a
second machine exists.** Answering "Manager" does not enrol in leader election;
there is nothing to enrol in yet.

If Manager: *"Owner's project URL or config path?"* — to pull the project's
`config.yaml` and align on ticket backend, expertise tags, etc.

**Section 2 — Project** `[2/7]`

```
─── Project ────────────────────────────────────────
Project name? [my-project]
Root branch?  [main]
```

Project name defaults to the current directory name (npm init pattern).
Root branch defaults to the branch detection found: the project root's own if
the root is itself a repository, otherwise the one branch every detected
repository shares. That is the value `modules.yaml` records for each module, so
pressing Enter cannot produce a root branch that disagrees with the modules
registered beside it — which a fixed `main` did, silently, for any project not
on `main`. Repositories on different branches get no guess: init names them and
offers `main`, which is also the default when nothing is detected.

**Section 3 — Modules** `[3/7]`

If existing repos were detected:

```
─── Modules ────────────────────────────────────────
Found 3 repositories:

  ✓ backend/     (git@github.com:org/backend.git)
  ✓ frontend/    (git@github.com:org/frontend.git)
  ✓ shared/      (local only)

Add all as modules? [Y/n]
```

Default Yes. Individual repos can be deselected. For each added module,
rite records the remote URL and the branch currently checked out.

If no repos found:

```
─── Modules ────────────────────────────────────────
No repositories found. Add a module?
Module name?  []
Git URL?      [] (leave empty for local-only)
```

Repeats until the user gives an empty name (Enter to finish).

**Section 4 — What's being built** `[4/7]`

For existing codebases (detected by language markers or non-empty repos):

```
─── What is this? ──────────────────────────────────
What kind of project is this?

  ▸ Full-stack
    Backend
    Frontend
    Mobile
    Library
    Other

Describe what this project does: []
```

For empty/new projects, the header reads **"What will this be?"** instead.

Both are pre-filled from the project's own manifests (`pyproject.toml`,
`package.json`, `pubspec.yaml`) in the root and its immediate subdirectories.
Kind: a mobile toolkit is mobile; a frontend framework with a web framework is
full-stack, and either alone is that alone; a package that installs a command
with neither is a library. The description is the root manifest's
`description`, or the one every module's manifest agrees on. When the
manifests do not say, kind stays Full-stack and the description stays blank.

The description is free text, skippable. This is the field the first Claude
session will follow up on — "event sourcing, CQRS" triggers architecture
questions; "e-commerce" triggers domain questions.

**Section 5 — Technology** `[5/7]`

Pre-filled from detected markers where possible:

```
─── Technology ─────────────────────────────────────
Platform?       [linux] (detected from environment)
Languages?      [python, typescript] (detected from pyproject.toml, package.json)
Frameworks?     [click, pytest] (recognised packages in the dependencies)
Architecture?   [] (e.g. event sourcing, microservices, monolith)
```

All skippable. Auto-detected values shown as defaults. Frameworks are named
from a fixed list of recognised packages found in the manifests' dependencies
— not every dependency, since a library a project uses is not a framework it
is built on. The architecture field
is where domain-specific patterns go — the AI enrichment step (§8.1) will ask
targeted follow-ups based on what the user types here.

**Section 6 — Operations** `[6/7]`

```
─── Operations ─────────────────────────────────────
Ticket backend?

  ▸ JIRA
    GitHub Issues
    None for now
```

If JIRA: *"JIRA site? (e.g. myteam.atlassian.net)"* — stored in `config.yaml`.

```
Sandbox Workers with yoloAI? [y/N]
  Runs each Worker in an isolated process (filesystem, network) — optional,
  needs yoloAI installed separately. See §5.3.
```

Default Yes (D-51). If Yes, `sandbox.enabled: true` is written to
`config.yaml` along with the backend that was verified — the questionnaire
itself does not collect or generate any token.

The question has three shapes, because there are three situations:

- **A backend rite has verified is available** — ask, default Yes.
- **yoloAI is not installed** — offer to install it (`brew install --cask
  yoloai`), then ask. The installer's exit code is not taken as proof: rite
  re-resolves the binary afterwards, so an install that reports success and
  leaves nothing on PATH is reported as what it is. A decline, a failure and
  a half-success all continue to the sandbox question — answering Yes records
  the setting, and `rite doctor` then reports the sandbox as not working
  until yoloAI is there, which is the honest state.

  **Declining the install is remembered for the machine, not the project**
  (`~/.rite/init-prefs.json`). The two questions are about different things:
  whether to install a binary is a fact about this machine and the same
  answer serves every project on it, while whether Workers run sandboxed is
  a per-project choice. So the offer is made once and the setting is asked
  every time. Declining suppresses the offer only — never the question, and
  never the feature.
- **No backend rite has verified works here** — do not ask. seatbelt is
  macOS-only and `flock` is a no-op inside a docker sandbox (§5.3), so on
  Linux there is no backend that keeps claims excluding. A question whose
  Yes cannot be honoured is worse than one line saying why, so it states it
  and continues with sandboxing off. Decided from the platform alone and
  BEFORE the install offer — with yoloAI absent there is nothing to ask
  about backends, and offering to install it where no verified backend can
  exist helps nobody.

**`sandbox.enabled` does not govern credentials.** Per-Worker token scoping
is asked for with `rite add worker --scoped-token` and is off by default;
otherwise Workers share the credentials the project already holds. The two
were wired together, which meant turning sandboxing on silently narrowed
every Worker to its own token.

```
Worker schedule timezone? [detected: Europe/Warsaw]
  Optional (D-48) — leave it blank and the schedule runs on whichever
  machine reads it. Set it when the project is read from more than one:
  the same window then means the same hours everywhere.
```

Defaults to the system timezone, detected, and may be left blank. `rite
start` prints which zone it resolved, so a machine running on its own clock
says so rather than looking configured. A zone that IS set and cannot be
resolved is a different state entirely — an error, named by `rite doctor`,
quoting the string it could not use (D-48).

**Section 7 — Knowledge** `[7/7]`

```
─── Knowledge ──────────────────────────────────────
Reference material to include? Links, documents, coding standards,
or domain knowledge. You can add more later with 'rite kb add'.

  Add a link?  [] (URL — will be fetched and cached)
  Add a file?  [] (path — will be copied into .rite/kb/)
```

Repeats until the user gives an empty input. Skippable entirely with Enter.

```
Commit the knowledge base to git? [Y/n]
  Authored knowledge (principles, patterns, notes) is a team asset —
  versioned, reviewable, new members inherit it. Fetched link caches
  are always gitignored regardless.
```

Default Yes. See §8.7 for the reasoning.

There is no closing catch-all. `init` used to end on *"Anything else the team
should know?"*, stored as `what.notes` and read by nothing — never rendered into
CLAUDE.md, never shown to a session. What the team should know belongs in
`.rite/context/`, which sessions are pointed at.

**Output:**

```
✓ Created .rite/brief.yaml
✓ Created .rite/modules.yaml
✓ Created .rite/config.yaml
✓ Created .rite/context/INDEX.md (empty — grows as the project does)
✓ Created .rite/kb/INDEX.md (2 entries)
✓ Created .rite/review-checklist.md (default)
✓ Generated CLAUDE.md (owner)
✓ Generated .claude/agents/ (4 agents)
✓ Generated .claude/commands/ (4 commands)

Ready. Start a Dispatch session — it knows what to do from here.
```

### 9.4. Generated Claude configuration

`rite init` generates the full Claude Code configuration so that **when the command
returns, a Dispatch session knows what to do from the user's first prompt.** No manual
step between setup and working.

This is the core of rite's value: the CLI bootstraps both the human-facing structure
(repos, tickets, claims) and the AI-facing instructions (CLAUDE.md, agents, commands)
from the same answers.

#### 9.4.1. What is generated

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Project instructions — role, modules, conventions, review rules. Points at `.rite/context/` and `.rite/kb/` rather than embedding their content. |
| `.claude/agents/reviewer-round1.md` | Round 1 review agent |
| `.claude/agents/reviewer-terminating.md` | Terminating check agent |
| `.claude/agents/reviewer-seam.md` | Cross-module seam reviewer |
| `.claude/agents/reviewer-decisions.md` | Answered-decisions checker |
| `.claude/commands/ticket.md` | `/ticket` — work a ticket end-to-end |
| `.claude/commands/review.md` | `/review` — run the review convention |
| `.claude/commands/refine.md` | `/refine` — turn a ticket or a vague phrase into a ticket someone could start cold. Writes no project spec; that is `/spec` |
| `.claude/commands/spec.md` | `/spec` — write the project spec from the brief, with a numbered decision register, and register it (§9.13.1) |

#### 9.4.2. Role-appropriate content

Each role gets different files, because workers don't need the org chart and
managers don't need worker-level instructions:

**Owner** — full CLAUDE.md: module map, board management, assignment logic,
expertise routing, stall detection, all review agents and commands. The Owner's
Dispatch reads this and can assign work, create tickets, monitor managers.

**Manager** — scoped CLAUDE.md: its own workers, board interaction for its
workers' tickets, reporting to Owner, the review convention. No assignment logic,
no other Managers' state.

**Worker** — minimal CLAUDE.md: the modules it works on, its Manager's name,
the claims system, the review convention, the ticket workflow. No org chart, no
board management, no other workers' state. A worker should be able to start cold
on a ticket with only its own CLAUDE.md.

#### 9.4.3. Templated versus derived

Two layers. The **templated** layer is the same regardless of project technology —
it is the coordination machinery. The **derived** layer uses `brief.yaml` to
produce technology-specific instructions.

**Templated** (identical across projects):

- Role structure (Owner/Manager/Worker responsibilities and boundaries)
- The review convention (stages, agent counts, register format)
- The claims system (how to claim, release, check for overlaps)
- The ticket workflow (claim → work → PR → review → merge → release)
- The publish gate (when and how to run it)
- Communication rules (what to report, when to escalate)

**Derived from `brief.yaml`**:

- Module map with descriptions (from `modules.yaml`)
- Build, test, and lint commands (inferred from detected technology —
  `pnpm test` for a Node project, `uv run pytest` for Python, `flutter test`
  for Dart)
- Framework conventions (component patterns for React, widget patterns for
  Flutter, handler patterns for Fastify)
- Architecture guidance (event sourcing patterns, microservice boundaries,
  monolith conventions — only if the user named them in `brief.yaml`)
- Ticket backend specifics (JIRA project keys, board column names)

The derived layer is **best-effort from detection, not invention.** If rite
detects `pyproject.toml` with a `[tool.pytest.ini_options]` section, it writes
`uv run pytest` as the test command. If it detects nothing, it writes a
placeholder that says "configure your test command here." It does not guess.

The first Claude session then reads `brief.yaml` and the generated CLAUDE.md
together, and may refine the derived sections — asking the user targeted
follow-ups that the fixed questionnaire could not anticipate.

### 9.5. `rite add module`

`rite add module <name>` without a URL creates a local-only git repo at `<name>/`
and registers it in `modules.yaml`. With a URL, it clones from the URL.

**rite does not create remote repositories.** The user creates repos where they want
them (GitHub, GitLab, Bitbucket) using the tool they already have (`gh repo create`,
`glab`, etc.), then registers them with `rite add module <name> <url>`. This keeps
rite's auth surface to what it needs for its own operations — reading/writing tickets,
pushing code — not org admin for repo creation.

### 9.6. `rite add worker`

Creates `workers/<name>/` with cloned copies of all modules registered in
`modules.yaml`, generates `worker.yml`, and generates the **worker-scoped**
Claude configuration — a minimal `CLAUDE.md` and `.claude/` directory scoped
to this worker's modules and its Manager (§9.4.2). Only valid from a directory
where `.rite/` exists and the role is Owner or Manager.

**If `sandbox.enabled: true` (§5.3, §9.3 Section 6), this is also where the
Worker's scoped git credential is provisioned** — a fine-grained PAT covering
exactly this Worker's `modules.yaml` repos, contents+PR permissions only
(§5.3.3), stored via the same keychain path §10 uses for every other
credential. The token is created or requested here, as part of the same flow
that creates the Worker; it never passes through chat (§5.3.4).

### 9.7. `rite remove`

`rite remove module <name>` deregisters from `modules.yaml` but does **not** delete
the directory. Deleting code is the user's decision, not rite's. Without this command,
people delete directories and leave `modules.yaml` stale.

`rite remove worker <name>` removes the worker directory and deregisters. Workers
are rite's own artifact, so removing them is safe — unlike module directories which
may contain uncommitted work.

### 9.8. `rite status` vs `rite doctor`

Two distinct questions:

- **`rite status`** — *"What's happening?"* Workers and their current tasks, claims,
  board state, blockers, ticket counts by column. The view a Manager or Owner reads
  to decide what to do next.
- **`rite doctor`** — *"Is this healthy?"* Token validity, dependency versions,
  `.rite/` integrity, module sync state, git remote reachability, **and schedule
  validation** (§2.7): full 24-hour coverage with no unintentional gaps, and
  every window's `workers` count within `sandbox.max_concurrent_workers` — the
  same check `rite schedule set` runs at write time, re-run here for a schedule
  that was hand-edited into `config.yaml` directly. The view someone reads when
  something is broken.

### 9.9. `rite update`

Updates rite itself (via pip/uv) **and migrates `.rite/` config files across
versions**. Config migration is inevitable once published — file formats will evolve,
and a tool that cannot upgrade its own config forces manual migration on every user.

`rite update` reads the current config version, applies any necessary transforms,
and writes the new version. It shows what changed and asks for confirmation before
writing. `--yes` skips the confirmation.

### 9.10. `rite start` and `rite stop`

Lifecycle commands. `start` brings rite up for a directory; `stop` shuts it down
with a full handover.

#### `rite start [<dir>]`

Brings rite up for the project rooted at `<dir>` (default: `.`). **`start` is not
just "turn on" — it is "assess state and act."**

#### Setup phase — the acting/reporting line (resolved 2026-09-11, D-50)

An earlier version of this section listed four setup steps, none of which was built,
and a later revision marked them all *not built* without deciding whether that was a
code gap or a spec gap. **It was mostly a spec gap, and this is the decision.**

The table below carries a fifth row the older list did not have. The pool top-up was
specified one section away, in §2.5.1, rather than here — which is part of why it went
unexamined for so long: the step with the largest irreversible cost was the one §9.10's
own setup list never mentioned. It is stated here with the others now.

**`start` performs anything idempotent, local and free. It reports anything
persistent, networked, or quota-spending, naming the command that does it.** That line
is the whole design, and it is the same principle §5.1.1 states for blast radius:
a command's surprising effects should be things the user asked for by name.

| Step | Resolution | Behaviour today |
|---|---|---|
| Verify `.rite/` is healthy | **Spec was over-specific.** "(runs `rite doctor` internally)" is struck — `doctor` is a presentation command printing forty-odd rows, and reprinting them buries `start`'s own orientation output. What `start` owes the caller is the subset it is about to be *governed by*. | **Built.** Config must parse (hard failure), context integrity is checked, and **the schedule is validated** (§2.7: 24-hour coverage, per-window `sandbox.max_concurrent_workers`) — because `start` brings the project up under that schedule. Problems are reported and `rite doctor` is named for the rest. |
| Start scheduled tasks | **Spec defect.** Registering the tick writes a cron entry or a launchd plist: a standing change to the machine that outlives the process and survives a reboot. §9.12's contract is that a human runs `rite scheduler install` the way they would run `crontab -e`. Doing it as a side effect of `start` also breaks idempotence in the direction that matters — a second `start` silently re-registers. | **Built, as a report.** `start` states whether the tick is installed, and whether it is overdue. This matters more than it sounds: the watchdog, window boundaries and snapshot freshness all depend on the tick, and **none of them says anything when it is not registered**. |
| Populate `kb/.cache/` | **Spec contradicted itself.** §8.7 already said "a fresh clone gets no cache files — run `rite kb refresh` after cloning to populate them", putting the refresh in the user's hands; §9.10 had `start` do it silently. `kb refresh` fetches every registered URL over the network, so a `start` run offline would hang or fail on it. **§9.10 defers to §8.7.** | **Built, as a report.** `start` says the cache is empty and names `rite kb refresh`. |
| Top up the coordinator pool | **Spec defect, the same one and the sharpest case of it.** Every pooled slot runs `claude`, so a bring-up that tops up spends quota nobody asked to spend — and unlike a cron entry, spent quota is the one damage no cleanup reverses (§5.1.1). `fill()` has exactly one caller and keeps it: `rite pool fill`. **§2.5.1 carries the full argument.** | **Built, as a report.** `start` probes pool liveness — mechanical, zero-token, local (§2.5.2) — and reports depth in `rite pool status`'s own words, naming `rite pool fill`. The probe writes no pool state where none existed, so reporting cannot become filling by accident. |
| Attempt Owner lease acquisition | **Resolved by the same rule, 2026-09-19.** A lease is a write to shared state that outlives the process — persistent and networked, the exact category this row's four neighbours are reports for. So `start` should not acquire one, and the periodic tick is the right owner: a lease has to be renewed, and a one-shot bring-up command cannot renew anything. | **Correctly not performed, and not reported either — which is the gap.** The earlier note here (*"§2.4 has no live code at all"*) is stale twice over: §2.4 shipped in v0.4.0, and it has a production caller — the scheduler tick builds `OwnerLeaseHolder` and hands over when this machine has nothing in flight (`scheduler/__init__.py:454-487`). What unblocked it was machine identity (`coordination/identity.py`), without which no caller could supply a Manager name. `start` still says NOTHING about any of it, while reporting the scheduler, the cache and the pool — so a Manager machine is brought up with no word on whether it holds the Owner lease, is enrolled at all, or is about to compete for it. The remaining work is a report, not a mechanism. |

**The outbox flush is the one deliberate exception, and it is documented rather than
removed.** `start` delivers any queued handover messages through the live ticket
backend — a network write to shared state, which the rule above would otherwise
forbid. It stays because §9.10 below already promises that a `stop` performed offline
queues its handover and flushes it *on next contact*, and `start` is the next contact.
Nothing else in the package would ever deliver it. Without this, §9.10's offline
`stop` is a comment written to a file nobody reads.

**Idempotent, stated precisely.** The older wording — "calling `start` on an
already-running project is a no-op" — was wrong about the first call and is corrected:
**calling `start` twice does not do the work twice.** The second call finds the outbox
empty, the scheduler already registered, the same claims. The first call after an
offline `stop` is not side-effect-free, and should not claim to be.

**Orientation phase — the decision table:**

After setup, Dispatch reads the project state and acts on the first matching condition:

| Condition | Action |
|-----------|--------|
| In-progress tickets assigned to this Manager's workers | Resume: reconnect to sessions, check for stalls, continue work |
| Blocked tickets with unresolved blockers | Surface blockers to the human; work on unblocked items |
| No project spec registered (`spec.paths` empty) | Run `/spec` before planning or ticketing (§9.13.1). `rite start` and `rite doctor` report this row, and report a spec file that exists but is not registered as that instead |
| `modules.yaml` lists repos with no review checklist | Generate default review checklists |
| `scheduled` tickets in the backlog, at least one claim-safe | Pick the first safe one and start (same logic as §5.2 claims check) |
| `scheduled` tickets in the backlog, but every claim attempt is refused | Report "nothing safe to start" to the human; increment the coordination-cost counter (§2.7.2) — this is a distinct, counted state, not silently the same as either neighbouring row |
| Board is empty | Report "nothing to do" and wait for work |

The table is evaluated top-to-bottom; the first match wins. **It is evaluated by the
session, not by `rite start`**, which reports rather than acts: of these rows it
reports only the project-spec one, and the generated `CLAUDE.md` carries the table so
a session can route on it. **The point is that
Dispatch orients itself** — a Manager that was offline for two hours, or one starting
for the first time, reads the same state and reaches the same conclusion about what
to do next.

Idempotent in the sense defined above: calling `start` twice does not do the work
twice. Not side-effect-free on a first call that has a queued handover to deliver.

#### `rite stop [<dir>]`

Shuts down the project rooted at `<dir>` (default: `.`) **and performs a handover:**

1. **Writes a handover comment on the active JIRA ticket** — what was done, what
   remains, current state.
2. **Updates the coordination board** so the Owner knows the work returns to the
   TODO pool.
3. **Reassigns or unassigns the ticket through the rite label mechanism** — not
   through the JIRA assignee field. The label (`scheduled`, worker name, etc.) is
   rite's assignment system; JIRA's assignee field is informational only.
4. **Releases all claims** held by this machine's workers.
5. **Stops scheduled tasks** and the heartbeat.

#### `stop`, heartbeat timeout, and lease expiry are the same event

Three triggers that converge on the same function, plus a fourth (§2.7.3) that
uses the same function but is not held to the identical-timing guarantee below:

| Trigger | Who writes the handover | Who detects |
|---------|------------------------|-------------|
| `rite stop` (clean) | The Manager itself | Self |
| Heartbeat timeout (Manager stall) | The Owner, on the Manager's behalf | Owner |
| Lease expiry (Owner stall) | The next-in-line Manager, on the Owner's behalf | Next Manager |
| Scheduled window boundary → 0 Workers (§2.7.3) | The Manager itself, on the same ~5-minute cadence as §3.5's watchdog | The schedule check that watchdog cycle already runs |

**The first three MUST converge on identical board state** (§9.10's shared
`perform_handover` guarantee, below) **— the fourth calls the same function for
the same reason (no stranded work, §2.0) but is deliberately not required to
wait for in-flight work the way graceful Owner demotion does (§2.4, §2.7.3
explains why the asymmetry is safe).** If the first three are implemented as
separate paths they will diverge, and you get one behaviour for a clean exit and
another for a crash. Given that sessions hang while appearing to run, the dirty path
is not hypothetical.

The shared logic is a single function (e.g. `perform_handover(reason, ticket, claims)`)
called by `stop`, the Owner's stall-detection handler, and the failover promotion
path. The function writes the ticket comment, updates the board labels, and releases
claims. The only input that differs is the reason string — `"clean shutdown"` vs
`"heartbeat timeout after N missed beats"` vs `"owner lease expired, promoted by
manager-beta"`.

**Reassignment is implemented once and reused** by all three paths. This is not a
design preference — it is a correctness requirement. A `stop` that updates labels
differently from a timeout creates two kinds of "returned to pool", and the Owner's
pickup logic must handle both or it drops tickets.

#### `stop` must succeed offline

`stop` must succeed when JIRA is unreachable — otherwise you cannot shut down during an
Atlassian outage or without network. The handover is queued locally (in the outbox)
and flushed on next contact. Local shutdown — releasing claims, stopping tasks,
closing sessions — does not depend on the network.

#### 9.10.1. Continuous handover — a second, cheaper mechanism

**`perform_handover` marks a transition; it does not, by itself, make session
loss free.** A session that dies between scheduled cycles, before any of the
three triggers above fires, leaves whatever handover last existed — stale by up
to one detection window. To close that gap, a **handover snapshot** is written on
the same scheduled cycle that already renews leases and checks pool/coordinator
health (the watchdog, §3.5) — **every cycle, unconditionally, regardless of
whether a transition is happening.**

This is **ephemeral state**, not a message (§3.3.3's boundary applies directly,
Phase 2 for the cross-machine case; the same shape applies locally in Phase 1):
it lives as `managers/<name>-handover.json` on the state branch (Phase 2) or
locally under `.rite/` (Phase 1's single-machine case), overwritten every cycle,
carrying no historical value in isolation — only the latest snapshot matters.
Fields: current ticket, a short progress summary, next planned step, open
blockers, timestamp. Writing it costs a file write (and, in Phase 2, a push), not
an LLM turn — the same "mechanical, zero-token" property §2.5.3 already requires
of the pool's readiness-lease renewal.

**The two mechanisms compose, they don't compete:**

| Mechanism | Trigger | Where it lives | What it answers |
|---|---|---|---|
| Handover snapshot (continuous) | Every scheduled cycle | Ephemeral, overwritten | "What is the state *right now*?" |
| `perform_handover` (transition) | `stop` / heartbeat timeout / lease expiry | Durable — message log (Phase 2) or ticket comment (Phase 1) | "Why did this transition happen?" |

`perform_handover`, when it fires, writes a **final** handover snapshot (so the
snapshot and the transition record never disagree at the moment of transition)
and then promotes a copy — with the reason string — into the durable record. The
snapshot answers "what's the state," cheaply, always; `perform_handover` answers
"why did it change," rarely, durably.

**Generated `CLAUDE.md` instructs the new session to read this at startup.** Add
to §9.4.1's generated content and to this section's **setup phase** (before the
orientation decision table runs): *"Read the latest handover snapshot for this
project before evaluating orientation — a fresh session reconstructs where the
previous one left off from this file, not from memory it doesn't have."* This is
§2.0/§2.2's "reconstructable from files" requirement, applied at session-startup.

**Offline behaviour matches this section's existing rule:** the snapshot write
queues in the local outbox and flushes on next contact, exactly as `stop`'s
handover does — a network outage delays the push, never blocks the local write.

### 9.11. `--help` and exit-code conventions

- `--help` on every subcommand, not just root.
- Exit 0 for help, exit 2 for usage errors.
- Examples in every help text — people copy rather than read:

```
$ rite add worker --help
Usage: rite add worker [OPTIONS] NAME

  Create a new worker workspace. If `sandbox.enabled` is set, also walks
  through provisioning that Worker's scoped sandbox token (§5.3.3, §5.3.4) —
  never displayed in this project's chat, only typed directly into this
  terminal prompt.

  Examples:
    rite add worker alpha
    rite add worker beta --modules backend,shared

Options:
  -m, --manager TEXT  Manager name
  --modules TEXT      Comma-separated module subset (default: all)
  --help              Show this message and exit.
```

**A command that answers a question answers it in the exit code, not only in
its output.** Stated because its absence has already produced defects in both
directions: `rite credential check X` printed `X: not_found` and exited 0, so
`rite credential check X && ...` walked straight into the failure it was written
to guard; and `rite pool status`, described as a read-only probe, wrote state
unconditionally, creating a `.rite/` holding only `pool.json` in whatever
directory it ran in — which `rite init` then refused as already initialised.

| Command | 0 | non-zero |
|---------|---|----------|
| `rite doctor` | no problems found | 1 — problems found, listed above the count |
| `rite watchdog` | nothing needs judgement, zero tokens spent | 1 — something does (§3.5) |
| `rite credential check <name>` | available | 1 — not found |
| `rite publish check` | clean | 1 stale suppressions · 2 findings · 3 **could not run** (§11) |
| `rite pool archive` | archived, or nothing to archive | 1 — the liveness probe could not run (§2.5.10) |
| `rite spec index` | the spec decomposes; the index is written | 1 — it does not, and a Worker should read it whole (§9.13.2) |
| `rite spec verify` | the digest matches the spec | 1 drift · 3 **could not run** — no spec registered, or the paths hold nothing |
| `rite sandbox start <w>` | started | 1 — yoloai missing, **or the worker cap could not be counted** (§5.3) |
| `rite sandbox status <w>` | answered: running, or genuinely no sandbox | 1 — the question could not be answered |
| `rite publish install-hook` | installed | 1 — git would never read it (§11.5.1) |

Two rules behind the table:

- **A check that could not run is never reported as a check that passed.** The
  gate's exit 3 is the fully worked example (§11); §2.5.10's refusal-without-tmux
  is the same rule outside the gate. So is `sandbox.max_concurrent_workers`: when
  the running-sandbox count cannot be read, `rite sandbox start` refuses rather
  than comparing against a plausible-looking zero, because a cap that silently
  stops capping leaves the operator believing they still have one. **A failure
  must never be returned disguised as a valid answer** — the caller cannot decide
  what it means if it cannot tell the two apart. And a third variant, one step
  earlier than the other two: **a check that was never installed must not report
  itself installed** (§11.5.1) — `rite init` printing `✓ Created
  .git/hooks/pre-push` for a hook `core.hooksPath` guarantees git will never read
  is the same lie told at setup time rather than at run time.
- **Read-only means read-only.** `rite status`, `rite doctor`, `rite pool status`,
  `rite handover show`, `rite budget`, `rite schedule show`, `rite board list`,
  `rite board show` and
  `rite publish check` create no directory, write no state, and spawn no session.
  A probe with a side effect is not a probe.

**Settled, 2026-09-11: `rite doctor` in a directory with no `.rite/` exits 1.**
It used to exit 0 ("no .rite/ directory found"), so `rite doctor && ...`
succeeded where rite was never set up. The case for 0 was that nothing was
checked and nothing is therefore unhealthy; the case for non-zero is that "not
set up" is exactly what a guard wants to catch — and this section's own rule
decides it, since it is the rule that came from `rite credential check` printing
`not_found` and exiting 0. Exit 1 rather than 2: this is doctor's own "not
healthy enough to proceed", not a usage error about the arguments given.

### 9.12. The scheduler

`rite scheduler-tick` is one cycle: the §3.5 watchdog check, plus the schedule
window-boundary check that hands over any active Worker when the schedule drops
to zero (§2.7.3, D-46). No LLM call anywhere in it.

**Nothing rite runs unattended starts a Claude session.** The tick checks liveness
and, at a window boundary into zero, performs a handover. Neither spawns, resumes, or
prompts a session, and no other scheduled path does either. This is deliberate, not an
accident of what has been built: anything scheduled that could start a session would
be spending tokens while nobody is watching, which is §3.5's whole argument.

Sessions start only from a command a human types — today `rite pool fill` (refused
above `sandbox.max_concurrent_workers`) and `rite sandbox start <worker>`, which has
yoloAI launch `claude` inside the new sandbox. Both are explicit and in the
foreground.

⚠ **The tick is not purely local, and anyone deciding whether cron is safe to install
needs this.** The window-boundary handover calls `perform_handover`, which builds the
configured ticket backend and posts a comment and a label to the board — a network
write to shared state, made unattended. It is the same handover `rite stop` performs
and it fails safe (an unreachable backend queues the message to the outbox rather than
losing it), but "no LLM call" is not the same as "touches nothing outside this
machine". Only liveness checking is purely local.

`rite scheduler install` registers that tick with the OS — cron on Linux, launchd
on macOS — **at `watchdog.interval_minutes` (§8.3), not a hardcoded cadence**, and
states the cadence it registered. `scheduler status` reports whether it is
registered — ⚠ **not at what interval, despite what this section said through
v0.16.0: `is_installed()` returns a bare bool and the cadence is never read back
out of the crontab line or the plist.** Flagged rather than dropped, because the
missing readback is arguably the defect: the installer's own comment warns that
"an installer that hardcoded its own 5 registered an agent that disagreed with the
config the user had just edited, silently", and reading the registered cadence back
is the only way to catch that afterwards. Edit `watchdog.interval_minutes` today
and last week's registered agent keeps its old cadence with nothing to reveal it.
Either implement the readback or narrow this line to "registered / not registered"
— do not leave it claiming both; `scheduler uninstall` deregisters it.

Its output goes to `.rite/scheduler.log`, which makes two things load-bearing that
would otherwise be cosmetic:

- **A clean cycle must still write a line.** An empty log is indistinguishable
  from a scheduler that never fired.
- **A cycle that found something must write what it found, not how many.**
  "needs attention — 1 reason(s)" is unactionable in a log nobody is watching
  live.

⚠ **The tick must not read its own output back as input.** A tick that recorded a
blocker built from the watchdog's reasons, where the watchdog's reasons include
the blockers it reads out of that same outbox, re-reports the previous tick's
report every cycle — the recorded text doubles each time, and at a 5-minute
cadence one unattended stalled worker fills the disk within hours. Record only
what the outbox does not already hold, and only when an identical entry is not
already pending. This is a standing constraint on anything the scheduler writes,
not a one-off fix.

---

### 9.13. Project spec

Most projects that reach for rite already have a spec. A tool that ignores it
makes the developer re-explain their own design to every Worker, so `rite
init` looks for one and points Workers at what it finds.

**Pointers, never copies.** `spec.paths` in `config.yaml` holds repo-relative
files and directories. rite does not read them, index them, or copy them
anywhere; the Worker's generated `CLAUDE.md` carries the paths and the
citation convention, and the Worker reads what its ticket needs. See D-52.

```yaml
spec:
  paths: [SPEC.md, docs/adr/]
  convention: "Decisions are cited as D-<number>; the register is in `SPEC.md`."
```

**Detected and proposed, never asked blank.** §9.3 looks for `SPEC.md`,
`DESIGN.md`, `ARCHITECTURE.md`, and non-empty `docs/`, `adr/`, `rfcs/`,
`design/` — in the project root and at the **top level of each registered
module**, not recursively. A monorepo genuinely keeps its spec under a
module; recursing would find the docs directory of every vendored dependency
and propose it with equal confidence, and a wrong proposal that gets accepted
is worse than a missed one. Nothing found means one line naming what was
looked for, not a prompt asking the user to type a path rite could not find.

**No rite-specific format.** A file, several files, or a directory all work
as they are. Requiring conversion would defeat the point, since the shapes
people keep specs in already exist and are not rite's to define.

**The citation convention is detected too.** A spec whose text carries three
or more `D-<number>` references gets a proposed convention line, confirmed
rather than assumed. This is what makes a ticket saying "implement per D-16"
a live reference: the Worker has the path, knows the convention, and knows
where the register is. Phase 2 tickets cite decisions instead of restating
them, which is only sound if that chain holds.

**rite cannot tell whether a spec is current**, and says so in the Worker's
own `CLAUDE.md`: *"rite cannot tell whether this is current. If it
contradicts the code, say so in the ticket rather than silently implementing
either."* A stale spec handed over confidently is worse than none — the
Worker implements it and nobody finds out until review. What rite **can**
check is that the paths still resolve, which `rite doctor` does on every run,
reporting a renamed or deleted spec as a problem.

`rite spec add <path>` / `rite spec remove <path>` change it afterwards.
There is deliberately no `rite spec list`: `rite doctor` already prints every
configured path while checking it, and a second command doing the same read
is CLI surface that has not earned its place.

#### 9.13.1. Writing a spec: `/spec`

A greenfield project has no spec to point at, and until D-53 nothing produced one.
The orientation table's "no project spec" row had no code behind it; the generated
`CLAUDE.md` told a session to consult a table it did not contain; `rite start` printed
`ready`; and a `SPEC.md` a session did write reached no Worker until someone ran
`rite spec add`, and never reached the Owner, whose `CLAUDE.md` only `rite init`
wrote. rite orchestrated work someone else had planned.

`/spec` is the first session's command, and the Owner's `CLAUDE.md` routes to it while
`spec.paths` is empty. It reads `brief.yaml`, asks the two or three follow-ups the
brief warrants — about the project, never about rite, since the person may have
installed it an hour ago — and drafts a spec from a fixed skeleton: Problem, Scope and
Non-goals, Architecture, **Decisions**, Open questions. It implements nothing and
creates no tickets.

**Nothing is written until the user approves it.** The draft is shown in the session,
and `SPEC.md` is written and registered only on their say-so: a spec nobody agreed to
should not be left sitting in their project, and someone who walks away mid-command
would neither have approved that file nor been told it exists.

**It ends by telling them to commit** `SPEC.md`, `.rite/config.yaml` and `CLAUDE.md`,
and says why: a Worker gets its files by cloning (§2.1), so an uncommitted spec exists
in no Worker's checkout and the pointer `rite spec add` just recorded resolves to a
path that is not in their tree. It says it rather than running it — what else is
uncommitted in the user's tree is theirs to judge.

**The decision register is mandatory, even with one row.** A spec without it is prose;
with it, a ticket saying "implement per D-3" resolves, and D-52's citation convention
has something to cite. Every project decides at least what its first version includes,
so D-1 is always that. Convention detection recognises the register's header row as
well as three or more `D-<number>` references, because a new project's register can
hold a single row and the count alone would never propose the convention for it.

**Registering rewrites every generated `CLAUDE.md`'s spec section.** `rite spec add`
and `rite spec remove` replace the section between its markers in the Owner's file and
each Worker's, insert one into a file generated before the markers existed, and leave
a `CLAUDE.md` rite did not generate untouched, saying so.

**Reported, not acted on.** `rite start` and `rite doctor` print `spec: no project
spec — run /spec` when none is registered, and name a spec file that exists but is not
registered. Neither writes one: that is a session's work and the user's decision.

**The phase, from disk.** `rite start` ends with where the project is and the next
step, worked out from what is on disk rather than what `config.yaml` claims — a spec
path pointing at nothing is no spec. The same table is written into the generated
`CLAUDE.md` (§9.4.1) so a session can route on it, with the instruction to tell the
user the phase before doing anything else. rite has no planning step, so the phase
after a spec says so and names `/refine` rather than promising a command that does
not exist.

**Not verified end to end.** No live Claude session has been run through `/spec`, so
whether one writes a usable spec from it is untested — that costs quota and is not
deterministic. What is tested is everything around it: the command is installed and
routed to, an unregistered file it leaves is reported, and registering it reaches
every generated `CLAUDE.md`. Before this section existed, nothing at the code level
told a session to write a spec, and nothing would have registered one it wrote.

Planning work from a spec, and turning a plan into tickets, are not built. `/spec`
stops at the spec.

⚠ **Still true as of 2026-09-19, and it now reads as though it were not.**
`src/rite_ai/local/decomposition.py` exists and is easy to mistake for the
second half of that sentence. It is a different thing at both ends: it starts
from a TICKET, not a spec, and it produces SUBTASKS in the state layer, not
tickets on a board. Nothing reads a spec and emits a plan, and nothing turns a
plan into board tickets — `rite board create` is the only thing that makes a
ticket and a human types it.

Recorded because the failure mode here is the opposite of a stale claim and
costs the same: a reader who finds `decomposition.py` concludes this sentence
is out of date, "corrects" it, and puts a false claim into the spec while
believing they are removing one. A claim that has become ambiguous because
something adjacent shipped needs the distinction written down, not the claim
withdrawn.

#### 9.13.2. The spec digest: loading part of a spec

Pointing a Worker at a 4000-line spec and telling it to read what it needs is a
budget, not a mechanism. The digest turns the spec into addressable units and
gives a Worker the one its ticket names, plus what that unit depends on.

**Units.** A numbered heading is its number (`5.3.3`); an unnumbered one is a
slug path under its parent (`2.4/promotion`); each row of the decision register
is its own unit (`D-31`), because a register that is one unit is the whole
register on every retrieval. Headings inside fenced code and front matter are
not units. `rite spec index` writes the inventory to `.rite/spec/index.json`,
which is committed.

**A slice is an under-approximation, on purpose.** Following every reference
transitively loads 71.5% of rite's own spec at the median — the monolith the
digest exists to avoid. A slice is the unit, what it cites at depth 1, and the
pinned hubs, which is 9.3% at p90. **Hub pinning is what makes depth 1 viable:**
the top 8 sections by in-degree are loaded whatever the target, and without them
a Worker following references by hand rebuilds most of the document anyway.
Index sections — the ones that cite 15 or more units and say little themselves —
are neither traversed into nor sliceable: nobody's ticket is a table of contents.

**A spec that should not be digested is refused, before anything is written.**
When the projected p90 slice is above `spec.refuse_above` (0.25), or the
document has no headings, `rite spec index` says so and exits non-zero. Reading
a small or densely interlinked spec whole is cheaper than maintaining a digest
of it, and that is a supported outcome rather than a failure to work around.

**Derived units are stamped, never self-certifying.** `/spec-digest` writes one
file per unit under `.rite/spec/units/`, then `rite spec stamp` records a hash
of the source it covers and of its own body. Stamping is a separate command
rather than something `rite spec index` does, because an automatic restamp would
bless both a source change nobody had read and a hand edit nobody had made —
after which nothing would ever read as stale or tampered again. `rite spec
status` reports both, by file name.

**Two sides, and the commands do not cross.** `rite spec slice` and `rite spec
show` are what a Worker runs, and both work from `.rite/` plus whatever of the
spec it can reach. `rite spec index`, `status`, `verify` and `stamp` read the
whole spec by nature, so they run where the spec lives, which is the project
itself; `/spec-digest` says so in its opening. It matters because a sandboxed
Worker mounts `.rite/` and often not the project root (§5.3), so a host-side
command run from inside cannot work, and telling someone to run it again from
there is advice that never succeeds.

**Two retrieval paths, and they are not the same.** `rite spec slice <unit>`
prints SOURCE text — the unit, what it cites, the pinned hubs — and works on any
registered spec, digested or not. `rite spec show <unit>` prints the DERIVED
text `/spec-digest` wrote and reviewed for that unit, with the `source_lines` it
came from. Both record a retrieval, because a fallback after either means the
same thing. `show` prints a stale, hand-edited or unstamped unit rather than
withholding it, and exits non-zero saying which: a Worker handed nothing cannot
judge anything, and one handed drifted text with no warning cannot either.

**What counts as an index is read from the spec, not remembered.** A section is
an index because of how many units it cites, so an edit elsewhere can turn one
into an ordinary unit — and that unit is then owed a derived file it never
needed before. Measured on a full digest of this spec: deleting a section made
§8.3 stop classifying as an index, and `rite spec verify` began reporting it as
uncovered. That is the gate doing its job rather than a defect, but it means a
verify failure can name a unit nobody touched.

**The gate is a command, not a convention.** `rite spec verify` exits 0 only
when every non-index unit is covered, nothing covers a unit the spec no longer
has, nothing is stale, hand-edited or unstamped, and the index still matches the
spec. It reads nothing for meaning — the two review rounds in `/spec-digest` do
that — and it exits **3** when it could not run at all, so a script cannot read
"no spec registered" as "nothing has drifted". `--strict` additionally refuses
one unit covered by two derived files; by default that is allowed, because
merging and splitting units is how a digest is written.

**A small slice means two opposite things, and the verdict says which.**
rite's own spec has had a gate requiring `§N` and `D-n` cross-references for
months, so its graph is dense: 39% of its units cite nothing. Seven real design
documents from other projects, written with no such gate, measured 73%, 77%,
86%, 100%, 100% — and their projected slices came out SMALLER than rite's
(0.9%-8.7% at p90 against 9.1%). That is not a better decomposition, it is an
emptier graph: a unit that cites nothing gets itself and the pinned hubs
whatever it actually depends on. So above 60% unlinked — between the two
measured populations — the ✓ is printed qualified, naming the share and
pointing at the insufficiency rate, which is the only thing that can tell a
small-and-sufficient slice from a small-and-empty one. It is deliberately not
a config key: a project that could tune it would be tuning away the warning.

**The two levers are measured, and only one of them works on a sparse spec.**
Raising `spec.slice_depth` to 2 follows references one step further; on rite's
own spec that took the p90 slice from 8.9% to 14.9% (median 5.4% to 6.7%), still
under the refusal threshold. On three specs whose units mostly cite nothing it
changed the median by **nothing at all** — 8.8% to 8.8%, 10.1% to 10.1%, 0.9% to
0.9% — because there is no second reference to follow. That is the shape most
likely to be falling back, so `rite spec status` names the other levers there: a
larger `spec.pin_count`, which loads more shared context into every slice
(measured: pin 16 costs more at the median than depth 2 does, 9.5% against
6.7%), and writing the missing references into the spec. Pinning nothing looks
cheapest of all — p50 1.4% on this spec — and is the trap the whole design turns
on: those slices are small because they are missing the context every unit
depends on.

**The insufficiency rate is not optional.** A slice that was not enough is
invisible: the Worker reads the whole spec and the digest looks like it worked.
So `rite spec slice` records every retrieval, `rite handover write
--spec-fallback <unit>` records every fallback, and `rite spec status` reports
fallbacks over retrievals for the last 7 days. **No data is reported as no data,
never as 0%** — a feature nobody used and a feature that always worked are
opposite readings, and only one of them justifies leaving the depth at 1. The
rate is the evidence for `spec.slice_depth: 2` or for pinning more hubs; without
it the depth is a guess defended by argument.

**What it cannot do.** Classification is structural, so a section that reads
like an index without citing like one is not detected (in rite's own spec, §8.3
is classified as an index and is not one). A spec with few explicit
cross-references produces small slices whose dependencies are real and simply
unwritten — the rate is what surfaces that, not the slice.

---

### 9.14. `rite start <provider>` — the session lifecycle, and its adapters

**Status: the mechanism is specified; the adapter interface is NOT frozen.**
§9.14.2 says why, and that instability is load-bearing rather than a
disclaimer.

`rite start <provider>` brings a project up and keeps it up: it starts the
loop **and** a Manager session under the named provider, as one command. A
human types it once; it runs until a stop condition or a budget ceiling ends
it, and says which.

#### 9.14.0. This AMENDS D-50. It does not inherit it

⚠ **An earlier draft of this section said `rite start <provider>` starts the
loop "(§9.10's `start` behaviour, unchanged)" and that it "subsumes" `rite
start` with "none of that" changing. Both halves were false, and the second
one hid a real conflict.**

**§9.10 does not start the loop — it deliberately refuses to**, and the
refusal is reasoned in `lifecycle/commands.py` in terms this section has to
answer rather than ignore: the loop "is free — it spends no quota, because it
starts no sessions (§9.12) — but it is a background tmux session that
outlives the command, which fails the first test, not the third."

D-50 says `start` performs what is **idempotent, local and free**, and
reports the rest. A Manager session fails all three, where the loop failed
only one. So this section does not extend D-50; **for `rite start
<provider>` it amends it**, and the amendment is recorded rather than
implied:

**What buys the amendment** is not that a Manager session is cheap — it is
the most expensive thing rite can start. It is that D-50's rule exists to
stop `start` having surprising effects, and §5.1.1 states the actual
principle: a command's surprising effects should be things the user asked
for *by name*. `rite start claude` names the provider. The pool top-up was
struck from `start` because `rite start` spent quota nobody mentioned; here
the quota spend **is** what was typed.

**What the amendment costs, and must therefore be paid for here:**

- **Idempotence must be restored explicitly, because D-50's first test still
  applies and this section is not exempting itself from it.** A project has
  **at most one Manager session**, and a second `rite start <provider>`
  against a live one is **refused, naming the running session and how to
  reach it** — not silently joined, not started alongside.

  ⚠ **The liveness check behind that refusal MUST FAIL CLOSED, and this
  paragraph says so because a draft of it did not.** An earlier version said
  a Manager session "needs at least one" of the loop's two mechanisms.
  Review pointed out where that lands: the mechanism nearest to hand is tmux
  `has-session`, which answers "does a session exist", not "is the command
  inside it running" — the exact facade this project fixed twice in one night
  in `loop.start` and in `pool.fill`. Worse, it returns false when tmux is
  missing or the call times out, so under a refusal rule an unanswerable
  check becomes **two paid Manager sessions**. §5.1.1's rule is that a safety
  property may fail closed and never open.

  So: the check must establish that the **inner process** is running, not
  that a session exists; a check that cannot be run refuses the start rather
  than permitting it; and the refusal must print the remedy, because this
  design's ordinary exit is an ungraceful terminal close and a marker left by
  a process killed without cleanup will be stale most mornings. A rule that
  wedges on its own intended exit path, with no printed way out, is the
  failure `loop/session.py` records: "a user who rebooted with a loop running
  could not start another one again, ever, and nothing printed the one
  command that would fix it."

  ⚠ **"Per project" is undefined here and a tracked `.rite/` makes every git
  worktree its own project root.** Two worktrees of one repository would get
  two Managers against one board with neither refusing, unless the rule
  inherits the loop's outright refusal to run in a worktree — which would be
  a significant usability fact nobody has stated. The plan must settle it.

  Refusing is the behaviour §2.5.1 and `check_worker_cap` establish — though
  note they refuse against a **configured cap**, which is always readable,
  where this refuses against **observed runtime state**, which is the part
  that fails. The precedent supplies the verdict, not the mechanism.
- **`rite start` with NO provider keeps its current behaviour exactly, and
  that is load-bearing rather than a convenience.** `start` is the command a
  session runs to orient itself — the generated `/rite-start` instructions
  say so — so a bare `start` that began a session would spawn one every time
  any session came up. `lifecycle/commands.py` names that recursion as its
  reason for not starting even the free loop. **The provider argument is
  what separates "orient me" from "start work", and nothing may collapse
  them.**

  ⚠ **D-78 contradicts the sentence above, and the contradiction is resolved
  in D-78's favour on a better distinction.** D-78 says bare `rite start`
  with exactly one Manager configured starts it; this bullet says a bare
  `start` must never begin a session. Both cannot hold.

  **What separates orientation from starting work is not the ARGUMENT, it is
  whether a Manager is already running** — which is observable, where the
  argument relies on the caller remembering which form to type. So:

  - bare `rite start`, no Manager running → D-78 applies (0 fails, 1 starts,
    2+ refuses and lists);
  - bare `rite start`, a Manager already running → reports it and exits 0.
    **Orientation, not a refusal**, because the session doing the orienting
    is usually the Manager itself and an orientation command that exits 1 on
    success is useless.

  This answers the recursion this bullet was written to prevent — a session
  that runs `start` to orient finds the Manager it is running inside and does
  not spawn a second — and it answers it with a fact rather than a
  convention. The §9.14.0 idempotence rule is what makes it safe, so the two
  are one mechanism rather than two.
- **Starting the loop is itself a change to §9.10's behaviour** and is listed
  here as one, not smuggled in as "unchanged".

#### 9.14.1. What a provider adapter is for

A provider is a thing that can hold a working session: today Claude Code,
next a purely local engine, later Cursor. The adapter is the narrow piece
that knows how to start one, notice it has ended, bring it back, and let a
human talk to it.

⚠ **An earlier draft claimed everything above the adapter — the loop, the
verdicts, the budget, the stop conditions, the claims — was
"provider-independent by construction, because it already exists". That is a
non-sequitur and it is false in three places, measured:**

- **The budget is a Claude transcript reader.** `rite_ai.budget` parses
  `~/.claude/projects/**/*.jsonl` and has no other input. For a local
  provider it does not return zero — it returns the machine's Claude spend
  regardless of what the local session did.
- **Worker freeness is "is a Claude sandbox running".** The loop asks
  `worker_sandbox_status`, and the sandbox is launched `--agent claude`. A
  provider with no sandbox reads every Worker free for ever, and the loop
  dispatches against capacity that does not exist.
- **The capacity model is one provider's billing.** `cycle.capacity` comes
  from `sandbox.max_concurrent_workers` under §2.7's schedule, and §2.7
  exists to ration a weekly quota. §9.14.8 says a local engine has neither.

So cost accounting and capacity are **provider-specific and currently sit
above the adapter, on the wrong side of the line.** Moving them is part of
the work the `local` adapter forces, and it is the concrete reason §9.14.2
refuses to freeze the interface. What IS genuinely provider-independent is
narrower: the claims ledger, the verdict *vocabulary*, and the stop rules in
§9.14.4.
That is the test of whether a thing belongs in the adapter: **if two providers
would need the IDENTICAL code, it is above the adapter; if it differs between
them, it is in it.** (An earlier wording said "would have to be written
twice" for the first half — the same condition as "differs", so it matched
every input and classified nothing.)

#### 9.14.2. The adapter interface is explicitly unstable until `local` exists

⚠ **Do not treat the interface shipped with the Claude adapter as a contract.
It will change when the second adapter is written, and that is the plan
rather than a risk.**

One implementation produces an interface shaped like that implementation.
The names, the arguments and the lifecycle hooks will all be reasonable, and
they will encode assumptions nobody noticed making — a session id exists, a
session can be resumed, ending is distinguishable from crashing, tokens are
the unit of cost — because for Claude Code every one of those is true.

**rite already got this right once, and the precedent is worth citing
precisely.** The state layer (§3.3) is genuinely backend-agnostic because git,
a local filesystem and a key-value store all existed *before* the interface
froze. Its compare-and-swap is per key with an opaque version token (D-61)
rather than a whole-state swap, and it is that way because a git backend and a
Redis backend disagreed about what a version is while the interface was still
being written. An interface that had been designed against git alone would
have had a commit sha in it.

So: the adapter interface is **unstable until the `local` adapter exists and
passes the same conformance suite.** Until then, changing it is a normal
change rather than a breaking one, and nothing outside rite should depend on
it.

#### 9.14.3. The session must be attachable by a human

**A Manager session a human cannot talk to is not a Manager session.** The
human must be able to reach the running session, read what it is doing, and
say something to it — the same relationship they have with a session they
started by hand.

This is stated here, at the interface, and not in the Claude adapter,
**because it is the requirement most likely to be lost by an abstraction
designed without it.** An adapter interface written from the mechanics alone
arrives naturally at "start a process, capture its output, log it" — which
satisfies every other requirement in this section and fails this one
completely. A detached process whose output is merely logged is a report
about a session, not a session.

What it demands of an adapter:

- a session must have a **name or handle a human can use** to reach it, and
  the command that reaches it must be printed when the session starts —
  §9.10's rule that a command's surprising effects should be things the user
  asked for by name applies to the useful ones too;
- the session's input must remain **live**, not replayed — the human types
  into the running session, they do not queue a message for it;
- attaching must be **read-write and non-destructive**: attaching must not
  restart, interrupt or reconfigure the session, and detaching must leave it
  running.

For the Claude adapter tmux supplies all three nearly free, which is why the
requirement is written at the interface rather than in the adapter.

⚠ **An earlier draft added "it costs the first adapter nothing; it constrains
the second and third, which is the point." That sentence is false and review
caught it.** The escape hatch below exempts exactly the provider that would
have been constrained: `local` has no input channel anywhere, so it takes the
hatch, and a requirement waived by its own text in the only case where it
bites constrains nothing. It is kept as a requirement because it is the right
default and because declaring non-attachability should be a visible act — not
because it currently costs anybody anything.

⚠ **The local adapter is where this gets hard, and that is a reason to
write the requirement now rather than discover it later.** A local engine may
have no interactive surface at all, in which case the adapter either provides
one or the provider is honestly declared non-attachable and `rite start
local` says so at startup. **It must not silently degrade to a log file.**

#### 9.14.4. Stop conditions come from the loop's verdicts

The loop already decides what a project's state is, and its seven verdicts are a
complete answer to "should this continue". The
session lifecycle does not invent a second opinion:

| verdict | lifecycle | why |
|---|---|---|
| `closed` | **stop** | the schedule authorises zero Workers in this window — the user has said "not now" |
| `ready` | continue | work is ready and a Worker is free |
| `saturated` | continue | a queue, not a fault |
| `blocked` | continue | the work is real and the holder will let go — the loop's own text says stopping here is wrong |
| `idle` | **stop** | the board has nothing ready; the work is done |
| `deadlocked` | **stop** | nobody is coming back, so waiting is indefinite |
| `unknown` | **stop** | something could not be established, and a loop's default on the unknown is to stop and say so |

⚠ **`closed` was missing from the first draft of this table, which said
"six verdicts" and listed six.** It is the row with the sharpest consequence
and both review rounds found it independently: under a window the user
configured to authorise zero Workers — typically overnight — the loop's own
answer is "sleep and go round", so a lifecycle that simply followed the loop
would keep a Manager session running, and spending, through the hours the
user explicitly told rite to be idle. §2.7.3 hands the *Workers* back at that
boundary and nothing was handing the Manager back.

It stops. That also removes the case §9.14.6's failed argument conceded as
its weakest, which is a good sign about the narrower design rather than a
coincidence: the hour hardest to defend is the hour the user already said no
to.

⚠ **The loop itself is not specified in this document.** It is built
(`rite loop run/start/status/stop`, shipped in 0.5.0) and its verdicts are
load-bearing for the table above, but §9 has no section describing it. This
section therefore depends on behaviour whose only specification is its
source. That is a gap in this document, recorded here rather than papered
over with a citation to a section that does not exist — and it should be
closed before the adapter interface freezes, because a stop condition
derived from an unspecified verdict set is a contract with no text.

**`idle` is a different kind of stop from the other two and the exit must say
so.** `idle` is completion: the session did what it was started for and there
is nothing left. `deadlocked` and `unknown` are faults: the session stopped
because the project is stuck or unreadable, and somebody has to look. A
lifecycle that exits identically for all three tells a human "finished" when
it means "jammed", which is the reporting defect this document keeps finding
in other forms. Distinct exit codes and distinct final lines, per §9.11.

⚠ **`closed` is an OVERRIDE, not a derivation, and saying otherwise would
break D-66 in the same table that cites it.** The loop's own answer on
`closed` is "sleep to the boundary, do not exit" (§2.7.3). This section
diverges on exactly that row. The divergence is right — a window authorising
zero Workers is the user saying "not now", and a Manager that kept spending
through it would be ignoring them — but it is a second opinion, and D-66 says
a second opinion needs a rule for when it may be held. **The rule: the
lifecycle may override the loop only where the loop's answer is "keep
waiting" and the user has already said not to.** That is one case and this is
it.

⚠ **`closed` needs its own exit class, and the two this section offers do not
fit.** It is neither completion nor fault: it is "come back when the window
opens". Reported as `idle` it reads as finished; as a fault it reads as
jammed. A third class is required, its final line must say when the window
opens and **that nothing will restart the session then**, and §9.11 has no
exit-code row for this command at all — which the plan must resolve rather
than inherit.

⚠ **Stopping leaves the loop running.** On `idle`, `deadlocked` and
`unknown` the loop's own watch returns, so both halves stop together. On
`closed` the loop sleeps and continues while the lifecycle stops, leaving a
detached loop alive with no Manager. Today that is harmless because the loop
dispatches nothing. **It stops being harmless the moment the dispatching
layer lands** — a loop started by `rite start <provider>` and orphaned by a
terminal close would then be a thing rite runs unattended that starts
sessions, which is the §9.12 breach §9.14.6 spent its length refusing. The
lifecycle must state the loop's fate on every stop. It does not yet, and that
is a required input to the plan.

⚠ **The stop verdict must be evaluated BEFORE the first session starts.**
§9.14.5 requires the ceiling checked before each start; the verdict needs the
same treatment and for a sharper reason. Every cause of `unknown` is free to
establish — config that did not load, a git worktree, an unresolvable
timezone, no ticket backend, an unreadable claims ledger. As written, "it
runs until a stop condition ends it" starts the most expensive thing rite can
start and then fault-exits on something knowable for nothing.

⚠ **No stop path is wired to a handover, and claims do not expire.** A
Manager session holds claims; a session ended without a handover leaves a
claim held by a process that no longer exists. Four stop conditions here,
none of them connected to the handover path §2.7.3 requires at exactly this
boundary. The plan must wire them or state why not.

**Deriving the stop condition rather than configuring it is deliberate.** A
separately configured stop condition is a second definition of "done" that
can disagree with the loop's, and the first time they disagree there is no
way to say which is right.

#### 9.14.5. The budget ceiling is mandatory

A `rite start <provider>` invocation **must** carry a ceiling on what it may
spend, and the command refuses to start without one — no default that means
"unlimited", and no default at all where the provider has a metered cost.

Refused rather than defaulted, matching §2.5.1 and `check_worker_cap`:
silently choosing a number the user did not choose is how `rite pool fill
--count 500` became possible, and spent quota is the one kind of damage no
cleanup reverses (§5.1.1).

The ceiling is **checked before each session start, not only at the end**. A
ceiling enforced after the fact is a report, not a ceiling.

⚠ **The unit is a session COUNT, not spend, and the spec says so because
rite cannot measure spend.** §2.6.1 establishes that no file under
`~/.claude/` exposes the quota and `/usage` is reachable only inside an
interactive session; `rite_ai.budget` is machine-wide by construction and
states that per-project attribution is unavailable. D-38 goes further and
forbids the path from burn-rate measurement back into concurrency control.

A ceiling "checked before each session start" against spend would therefore
be a check that cannot run — and §9.11's rule is that a check which could not
run is never reported as one that passed. So until per-session accounting
exists, **the mandatory ceiling is a maximum number of session starts, plus a
wall-clock window**, both of which rite can enforce exactly. It is named as a
count in the interface and in the command's output, never as a spend figure
it cannot substantiate.

⚠ **A count ceiling does not bound cost, and this section will not pretend
otherwise.** Three sessions may run arbitrarily long and burn arbitrarily
much. §9.14.5's own line — "a ceiling enforced after the fact is a report,
not a ceiling" — applies to itself: a count checked at start time is a report
about starts. **The wall-clock window is the bound that actually limits
spend, and it is currently the least specified object in this section** — no
unit, no statement of whether it is equally mandatory, no behaviour at
expiry, no exit class, no interaction with `closed`. Specify it to the same
depth as the count before implementing either.

⚠ **Neither bound survives re-invocation unless it is persisted, and nothing
here says it is.** Both live in a process designed to die with the terminal.
What actually bounds a day's spend is §9.14.0's one-Manager refusal — a rule
resting on a liveness check, which is the mechanism this project has just
twice found to be a facade.

⚠ **The unit is still provider-specific and the interface must not assume
tokens.**
For Claude Code the natural unit is the provider's own accounting; for a
local engine there may be no metered cost at all, in which case the adapter
declares the ceiling not applicable rather than pretending to a number.
**"Not applicable" must be a distinct answer from "unlimited"** — the first
is a property of the provider and the second is a decision nobody made.


##### Two bounds, because neither one suffices (D-82)

`--sessions` caps how many provider sessions a run may START. `--minutes`
caps how long it may go on starting them. **Both are mandatory and neither
has a default.**

They are not two spellings of one bound, and the supervisor was measured to
establish that rather than argued about:

| the run | `--sessions` | `--minutes` | what actually stopped it |
|---|---|---|---|
| sessions that end instantly | 1000 | 1 second | **the count**, at 1000 — the clock was never reached |
| sessions of realistic length | 1000 | short | **the clock**, after 3 — the count was never approached |

So a run bounded only by a count is unbounded in time, and a run bounded
only by time is unbounded in spend. Which one binds depends on how the
provider behaves in that particular run, which is not knowable in advance —
a crash loop and a working Manager are the two rows of that table.

⚠ **`--minutes` existed before this and did nothing.** The value was
recorded on the instance, threaded through three modules and passed to
`supervise` — by a call site that always passed zero, because no flag set
it. Every piece reviewed clean, and the feature was reported as done. The
dead-wiring guard cannot see this: it asks whether a FUNCTION is called,
never whether a PARAMETER is ever supplied.

A duration that defaults to forever is a bound in name only, which is why
this one is mandatory rather than defaulted. That is D-69's reasoning about
`--sessions` — silently choosing a number the user did not choose is how
`rite pool fill --count 500` became possible — applied to the other axis.

#### 9.14.6. §9.12, and the argument that FAILED

§9.12 says: **"Nothing rite runs unattended starts a Claude session."**
Sessions start only from a command a human types, in the foreground.

**Two review rounds killed the first version of this subsection, and the
rejected argument is kept because the rejection is the useful part.**

**What was argued, and why it fails.** The first draft kept §9.12 unamended
and defended unattended resumption on four legs. It had already discarded the
weakest one — "a resume is a continuation, not a start" — on the ground that
§9.12's reason is unattended token spend, which a resume incurs. Review
removed the other three:

1. **The `rite pool fill` precedent was quote-mined.** §9.12's sentence ends
   "**Both are explicit and in the foreground.**" The draft cited the first
   half — one command, N sessions — and dropped the clause that decides the
   case. `fill`'s sessions all start *synchronously, inside the process the
   human is watching*; the command does not return until they have. The
   precedent establishes that one command may start many sessions **while it
   runs**. It says nothing about starting sessions **after it returns**,
   which is the only thing the draft needed from it.
2. **"Bounded authorisation" does not distinguish anything.** A cron tick can
   be given a ceiling and a terminating condition too — rite owns every piece
   needed (`watch(limit=…)`, per-window caps, `max_concurrent_workers`). And
   a cron tick also "traces to exactly one command a human typed": `rite
   scheduler install`. §9.12 forbids it anyway. An argument which, if
   accepted, would permit a bounded cron dispatcher dissolves the rule it
   claims to satisfy.
3. **"Attachable" was already rejected by §9.12 itself.** §9.12 makes the
   scheduler log load-bearing *and calls it "a log nobody is watching live"*
   while still classifying the tick as unattended. A log a human can read
   afterwards is a strictly stronger artefact than a session they could have
   attached to had they been awake. §9.12 has ruled on this class.

**So the honest verdict is that the argument fails, and §9.12 wins.** Writing
it up any other way would have produced a spec that papers over its own
central question.

**What survives, and it authorises materially less.** Resumption is permitted
**only while the human's own invocation is still the live foreground process
they started.** `rite start <provider>` does not return and then resume from
somewhere else; it *is* the process, and every session it starts — first or
resumed — begins inside a command the human launched and can see. That is the
`pool fill` precedent applied honestly rather than stretched, and §9.12 needs
no amendment for it.

**The distinction that makes this coherent is between the SESSION and the
RESUMER, and the first draft of this narrowing collapsed them.** Round 2
caught it: §9.14.3 requires a session a second terminal can attach to and
which survives detaching, and an earlier version of this subsection said
"nothing that outlives the terminal". Those are mutually exclusive, and rite
has exactly one persistence mechanism — tmux — which is detached by
construction. As written, the two requirements denied each other.

They separate cleanly:

- **The session may outlive the terminal, and should.** A tmux session the
  human started is precisely what §9.12 already permits: `rite sandbox start
  <worker>` leaves one running and is named in §9.12 as compliant. Surviving
  detach is what makes §9.14.3's attachability real rather than nominal.
- **The RESUMER may not.** The thing that notices a session has ended and
  starts another one is the human's own foreground invocation, and it dies
  with their terminal. **No daemon, no scheduled re-entry, nothing that
  brings a session back once the human's process is gone.**

So after a terminal closes: a Manager session that is still running keeps
running and stays attachable, and when it ends, nothing restarts it. That is
the whole of the narrowing, and it is the half §9.12 actually speaks to —
§9.12 forbids unattended *starts*, not sessions that continue.

Consequently:

- **When the human's process ends, the LIFECYCLE ends** — no further session
  is started. The session already running is not killed, because rite has no
  kill path and deliberately so: `loop/session.py` records that ending a
  session without a handover "leaves a claim held by a process that no longer
  exists".
- **Resumption is bounded in COUNT and TIME, not in nominal spend** — see
  §9.14.5, which now says why count is the only bound rite can currently
  enforce.
- **`closed` stops the lifecycle** (§9.14.4). A schedule window authorising
  zero Workers is the user saying "not now", typically overnight — which was
  exactly the case the failed argument conceded as its weakest. Stopping
  there removes it rather than defending it.

⚠ **What this gives up, stated so nobody re-derives it as a missing feature.**
An unattended overnight Manager session is **out of scope**, not deferred.
Anyone wanting one is asking for §9.12 to be amended, and that is a decision
for the project owner, in public, with this subsection's failed argument in
front of them — not something to be reached by a spec that defines its way
around the rule.

#### 9.14.7. Compliance constraints, as design requirements

These come from Anthropic's terms and they bind the implementation. They are
recorded here rather than in the adapter because an adapter is free to be
replaced and these are not.

1. **Never modify, patch, wrap or vendor the Claude Code binary.** Invoke the
   installed one, found on `PATH`, as a user would.
2. **Never alter how it identifies itself** to Anthropic's servers — no
   spoofed client identifiers, no altered user agent, no interception of its
   traffic.
3. **Never read, persist, log or transmit the token.** It is consumed from
   the environment and nowhere else.
4. **No login flow.** The user mints their own token and puts it in their
   environment. rite does not offer to obtain one.
5. **"Claude" stays out of product and command names** beyond factual prose.
   The provider argument names the provider — `rite start claude` — which is
   a factual statement about what is being started, and the adapter is
   described as "the Claude Code adapter". Nothing rite ships is *called*
   Claude.
6. **The budget ceiling and the stop condition are not optional.** They are
   why §9.14.5 refuses to start without one.

   ⚠ **An earlier draft called them "the evidence that this is ordinary
   individual usage rather than automated resale of capacity". After D-69
   narrowed the unit to a session COUNT, that claim rests on its weakest
   support and is withdrawn**: a count of session starts is not evidence
   about capacity consumed. What the constraints actually evidence is that
   the run is bounded and terminating, which is a weaker and true statement.
   The stronger one becomes available only if per-session accounting ever
   exists.

⚠ **Requirement 3 contradicts how rite delivers credentials today, in two
places, and the spec records it rather than assuming the implementation will
notice.** Measured 2026-09-19:

- **rite reads the token.** `resolve_worker_token` takes it out of the macOS
  keychain and hands the plaintext to `worker_environment`, so rite holds the
  value in process memory. The whole per-project credential scoping (§10.2) is
  built on rite reading and re-delivering secrets.
- **rite then puts it on argv**, as `--env KEY=VAL` to `yoloai new`. **argv is
  not uid-restricted on macOS**: measured, an unprivileged account read the
  full argument lists of processes owned by root, `_usbmuxd`, `_distnote` and
  `_windowserver`. So a credential passed that way is readable by *any local
  account* for as long as the process lives.

So the Claude adapter **cannot inherit the existing delivery mechanism**. The
token must reach the provider process by being inherited from an environment
rite never materialises into its own memory, or fetched by the adapter's
child itself. That is a constraint on the adapter interface, not an
implementation detail, and it is cheaper to state now than to retrofit.

⚠ **A draft of this section held `CLAUDE_CODE_OAUTH_TOKEN` up as the
compliant shape and said "it works". Review found that it is the clearest
violation of requirement 3, not the exemplar of it.** The lift into the
child's environment fixes **argv exposure only**. The rest of the chain is
untouched and is exactly what requirement 3 forbids: `rite credential set
claude` **prompts** for a token the user pastes from `claude setup-token`,
`claude_token` is **persisted** to the OS keychain, and `worker_environment`
**reads** it back into rite's memory on every start.

That is **two** of requirement 3's four verbs — read and persist — plus a
separate breach of requirement 4, whose territory "prompts for" is. An
earlier draft said "three of the four verbs" by counting prompting and
reading back as different ones. The argument survives the correction; the
count did not, and a section that corrected "six verdicts" cannot keep it.

⚠ **A draft of this paragraph called the contradiction "structural" on the
ground that "injection is the only channel that reaches a sandboxed Worker".
Review refuted it from the code, and the refutation matters because it turns
an impossibility into a choice.**

There is a second channel and rite already uses it: the Claude token is
POPPED out of the `--env` set and placed in the environment of the `yoloai
new` child process, which the sandbox tool reads from there. So a design
satisfying requirement 3 exists today — the user exports the token, rite
passes its environment through and never calls the keychain for it.

**What that costs is one sentence of §10, not the section**: per-project
scoping for this one credential, and the convenience of a sandbox started
from any terminal rather than only from one where the token is exported.

**And the irreconcilable pair is not 3 and 4.** Those two agree —
requirement 4 *is* the design that satisfies requirement 3. The conflict is
between **requirement 3 and §10's "a credential belongs to one project, held
in rite's keychain"**: you cannot hold a per-project token and never read it,
because reading it back is the whole point of holding it. An implementer told
to "rewrite 3 and 4 together" would patch the two clauses that are consistent
and leave the contradiction in §10 untouched.

**The measurement that decides it** is one sandbox away and has not been
taken: whether a token delivered by inheritance is persisted the way `--env`
values are (§5.3.4 records that those are written to four files surviving
`stop`). If inheritance persists it too, the design above buys nothing and
requirement 3 must be narrowed. **Take that measurement before the plan, not
after.**

One measurement is missing and is the one that matters: §5.3.4 records that
every credential injected by the sandbox tool is written to four files that
survive `stop`, but the Claude token travels a different channel and **nobody
has measured whether that channel persists it.** Under a requirement that
says "never persist", that is the fact to establish before implementation,
not after.

#### 9.14.7a. The command name is not settled — `rite start <x>` is already taken twice

⚠ **`rite start <provider>` collides with two existing meanings of the same
positional argument and this section does not get to ignore it.**

§8.9 defines `rite start <alias>`, resolved against the Dispatch registry,
and promises that `rite start <dir>` is "unchanged for anyone not using the
registry". §9.10 and §9.1 define `rite start [<dir>]`. A third meaning makes
`rite start local` genuinely ambiguous — alias, directory, or provider — and
nothing reserves provider names against the registry, so an alias called
`claude` would silently win or silently lose depending on resolution order.

**Resolve it before implementation, not after.** The options are a flag
(`rite start --provider claude`), a subcommand (`rite session start
claude`), or reserved provider names enforced at registration. This document
does not pick one, because the choice is the project owner's and it is
cheaper to make deliberately than to discover in a bug report. **What it
does say is that `rite start <provider>` as a bare positional is not
available**, and any implementation plan that assumes it has not read §8.9.

##### ⚠ RESOLVED (2026-09-20), and this subsection was stale against the shipped code

The paragraph above says a bare positional "is not available". **What
shipped uses one**, and the contradiction stood in this document while the
code disagreed with it — which is the failure this section was written to
prevent, committed by the section itself.

What settled it is that the positional is no longer a PROVIDER. D-78 and
D-79 made it a **Manager name** — a project-declared identity, not a
vocabulary rite owns — and §9.14.9 fixed the resolution order: Manager names
are matched BEFORE the Dispatch registry, so the ambiguity is decided rather
than left to chance.

The third option in the list above is what actually carries it, in the only
form that works across machines. **Provider names cannot be "reserved" at
registration, because `manager_roles` is committed and the registry is per
machine** — the collision exists on one laptop, over a config that is
correct and not that user's to change. So the check lives where somebody can
act on it: `rite projects add` refuses a colliding alias, and `rite doctor`
reports a collision that appeared later because a Manager role was committed
(`managers.name_collisions`).

`rite stop <manager>` inherits the same problem and is **NOT** resolved by
this: see §9.14.13.

#### 9.14.7b. ⚠ `local` is already built, in a shape this section does not fit

**This is the largest unresolved finding of the two review rounds and it is
recorded rather than papered over, because resolving it is a design decision
and not a drafting one.**

`src/rite_ai/local/` exists. It is not a stub. And it does not match the
model this section assumes, in three ways that compound:

1. **A provider is not a command argument in the built design; it is a
   Manager attribute.** `engine` is a field on `ManagerRole`, beside
   `endpoint`, `model`, `agent` and `credential`, matching `local:<class>`.
2. **Providers are therefore not mutually exclusive.** The local design
   presets an ordinary project as **three Managers running concurrently** —
   `lead` on `claude`, `planner` on `local:large`, `executor` on
   `local:small` — and `duty_router` exists to route between them.
   `rite start <provider>` cannot express the configuration the project
   already ships config for.
3. **`local` is a family, not an adapter.** A `local:*` role is meaningful
   only with its endpoint, model and agent; `probe_local_engines` iterates
   several. An adapter keyed on the string `local` has nowhere to put them —
   a fourth ambiguity for `rite start local` that none of §9.14.7a's three
   proposed fixes resolves.

**What this invalidates here.** §9.14.0's "at most one Manager session per
project" — the concession that pays for the D-50 amendment — contradicts the
tier model directly. Either it means one per Manager ROLE, in which case the
idempotence argument needs redoing, or mixed-engine projects are out of scope
and D-64's ordering is ordering something other than what is built.

**And the built unit is not a session.** `run_subtask` is a synchronous call
in rite's own process whose unit is one subtask, terminating on completion by
design. Against §9.14.1's four duties: there is no process to start, ending
is a `return` rather than an event, and "bring it back" is actively wrong —
re-running an accepted subtask re-enters the commit path. The local design's
own resumption story is at a different granularity: a durable record of
subtask, branch, attempt count and last verify result. **§9.14.1 merges two
different duties — resume a live session, and resume a plan from a record —
and only the first is Claude's.**

⚠ **`run_subtask` has no production caller.** The honest status is that
`local` is a library with no driver, in a shape orthogonal to this section's,
while §9.14.8 proposes writing "the local adapter" as though from zero.

**Required before any implementation plan:** a decision on whether a provider
is a command argument or a Manager attribute. That is a register row, and it
is not mine to make.



**`local` is second on purpose, and the order is not a priority ranking.**

A local engine is the most *different* provider: no session concept, no
session id to resume, no quota, no rate limits, possibly no interactive
surface. Every assumption the Claude adapter will quietly bake in is one a
local engine violates. Writing it second is what turns the interface from a
description of Claude Code into an interface — **an interface that survives
`local` survives anything, and one that has only ever met two hosted
assistants has not been tested at all.**

Cursor third is the easy case, and it is third because easy cases do not
discover interface defects. Taking `cursor` second would produce two adapters
that agree with each other and an interface that fails on the third, which is
the same mistake as freezing the state layer against git alone.

§9.14.2's instability ends when `local` lands, not when the second adapter
lands.

#### 9.14.11. ⚠ There is no adapter yet, and the engine string is an executable name

**Stated plainly so nobody mistakes a fallback for a contract.** What ships
in 0.5.1 is not the adapter interface §9.14.1 describes. There is no
protocol, nothing for a second engine to implement, and no way to express
that an engine takes different flags. `launch_command(engine, resume_id)`
runs **the engine string as a command** and appends `--resume <id>`, which
is Claude Code's spelling, unconditionally.

**What that assumes, structurally:** that an engine IS a long-running
interactive process in a pane. A local model is request/response — no pane,
no session to resume, and `settled_alive` would reject it for exiting
immediately, which is the correct behaviour for a shape this code cannot
express.

⚠ **The interface is unstable and is NOT being generalised from one
implementation.** §9.14.2 and D-63 give the reason, and it is the state
layer's: git, a filesystem and a key-value store all existed before that
interface froze, which is the only reason it is genuinely
backend-agnostic. Guessing the boundary from Claude alone would produce an
interface shaped like Claude, and the second adapter would discover it.

**So the boundary is drawn when `local` forces it, and until then this
section is the honest description of what exists.**

#### 9.14.10. A recorded value names the thing it claims to name

**A value written into state must describe what is actually running, not
what the recording process happened to know.** Stated as a rule because the
first Manager implementation broke it four times in one function.

`pid` recorded `os.getpid()` — the rite CLI's own process, which exits
seconds later and whose number the OS then recycles. Measured: recorded
41437 where the pane was 41470. `engine` recorded the configured engine
while the thing launched was `command or engine or "claude"`, so an
explicit command meant the field named something that was not running.

⚠ **A recorded value that names a different thing than it claims is worse
than no value, because it reads as evidence.** An absent pid makes a reader
go and look; a wrong one makes them act. This is the same argument §3.5
makes for heartbeats — a stale beat is worse than a missing one — applied
to identity rather than to time.

**So: ask the thing itself.** tmux knows its pane's pid (`#{pane_pid}`) and
when the session was created (`#{session_created}`); the process that spawns
it knows neither, and knowing a value at recording time is not the same as
the value being true.

#### 9.14.9. The shape settled (2026-09-20), and what it overturns

**Three questions this section left open have been answered, and the answers
change the section rather than completing it.** Recorded together because
they interlock: each one is what makes the next affordable.

**1. A provider is a Manager ATTRIBUTE, not a command argument.** D-72 is
resolved in favour of what `src/rite_ai/local/` already built: `engine` is a
field on a Manager, and a project may run several Managers on different
engines at once. **§9.14's original model — one command argument selecting
the project's single Manager session — is withdrawn.** It was written
without knowledge of the built design, and §9.14.7b recorded that as the
largest unresolved finding; this is its resolution.

**2. `rite start <name>` names a MANAGER.** Not a provider, not a directory,
not a registry alias. D-71's ambiguity narrows but does not vanish: §8.9's
`rite start <alias>` and §9.10's `rite start <dir>` still exist, so the
resolution order must be stated and a Manager name that collides with a
registered alias must be refused at registration rather than resolved by
precedence.

**3. Identity comes from the name, and the per-Manager directory follows.**
§9.14.5 and D-77 recorded that the boundary property was blocked on a
per-process Manager identity that `.rite/machine` could not supply. **A
Manager started as `rite start planner` knows it is `planner`** — the
identity is the argument, which is why questions 2 and 3 are one decision.
Each Manager gets its own subdirectory inside the single project root, so
§5.4's containment property becomes statable: the boundary is
`<root>/.rite/managers/<name>/`, and everything in §5.4.6's "shared by
accident" list moves under it.

⚠ **`docs/design/V070_MULTI_MANAGER.md` recorded the OPPOSITE shape and is
now marked superseded (D-79).** That document says
"separate roots per Manager, coordinating through the shared state layer —
each Manager owns its own project root and its own `.rite/`", and rejects a
shared `.rite/` by name: "a second protocol that has to be kept in agreement
with the first, and the two would drift." It calls that **the load-bearing
decision**.

The two were written a day apart, neither cites the other, and this
paragraph was written without knowledge of that one — which is the defect
this document keeps recording, now committed inside its own resolution of it.

**Resolved in favour of this section, on 2026-09-20, by an explicit answer
given with the counter-argument in hand.** What reversed it is a fact about
the code rather than a preference: separate roots mean separate claim
ledgers, and `claims_channel()` returns nothing unless BOTH
`coordination.managers` and `coordination.remote` are set — so two Managers
on one machine would silently not see each other'''s claims, which is the
failure this project hit twice in one day, made the default.

V060'''s cost is accepted rather than refuted: a shared root IS a second
protocol that can drift from the state layer'''s, and whoever builds
`<root>/.rite/managers/<name>/` should expect that drift and be looking for
it. D-79.

**4. Profiles are shared; instances are per-user.** A Manager's *profile* —
its engine, duties, model — is committed config, because a team agrees on
what a `planner` is. A Manager's *instance* — that this machine's user is
running one, and its runtime state — lives in `.rite/user/` and is not
committed, because it is meaningful only on the machine that wrote it. This
is the same line `.rite/` already draws (§8.x) applied one level down, and it
is what keeps a shared `config.yaml` from claiming a Manager is running on
somebody else's laptop.

**5. Dispatch is 0/1/2+, and each case behaves differently on purpose.**

| Managers configured | `rite start` with no name | Why |
|---|---|---|
| 0 | **fails** | There is nothing to start, and starting a default would invent a configuration the user did not write |
| 1 | **works bare** | The common case, and requiring a name to state the obvious is ceremony |
| 2+ | **refuses, and LISTS them** | Picking one would be a guess, and a guess about which engine spends which quota is not a guess worth making |

⚠ **The 2+ case must list the names, not merely refuse.** A refusal that says
"several Managers are configured" and stops leaves the user running `rite
doctor` to find out what they could have typed. This is the same rule
§9.14.0's duplicate refusal follows: a refusal that names the remedy is a
different thing from one that only says no.

⚠ **What this does NOT resolve.** §9.14.6's narrowing stands — the lifecycle
is the human's foreground process and an unattended overnight Manager is out
of scope. The compliance contradiction in §9.14.7 stands. And §9.14.5's
ceiling is still a count, because §2.6.1 has not changed.


#### 9.14.11a. `rite start <manager>` prompts the session

**Decided; it simply never reached this document, which is why an
implementation plan read it as unspecified.** A Manager session that starts
with an empty prompt waits for a human to type something — which is the
behaviour `rite start` exists to remove, and it is the same gap
`/rite-start` was added to paper over for the Claude app.

The prompt is sent after `settled_alive` confirms the command survived, by
the same `send-keys` path the capability probe uses, with the Manager's own
name available to it (§9.14.9) so the session knows which Manager it is.

⚠ **A keystroke can be swallowed by a shell that is not yet reading.** That
is measured, not hypothetical: it is why the capability probe treats a lost
`exit 3` as "no answer" rather than as evidence about the machine
(§9.14.10). A prompt that vanishes leaves a Manager sitting idle and
spending nothing while the supervisor waits for it to finish, so delivery
is confirmed rather than assumed.

##### One sub-question the decision did not reach, answered here as a stated default

**Is the prompt re-sent to a RESUMED session?** The decision covers
starting; §9.14.9's resume path is a second start of the same work.

**Default taken: NO — the prompt is sent to the first session only.** A
resumed session already carries the context the prompt would establish, and
re-issuing an instruction into a conversation that is mid-task is the same
class of error as restarting a session a human deliberately quit: the tool
telling the agent to begin something it is in the middle of. The asymmetry
decides it — a missing prompt on a resume costs a session that continues
what it was doing, and a spurious one costs a session that starts over.

**Marked as a default rather than as a decision**, because it is inferred
from the resume design rather than stated by the project owner, and it is
cheap to reverse if the dogfood shows a resumed Manager drifting.

#### 9.14.12. Stopping: three outcomes, three behaviours

⚠ **A bound being reached and a human pressing Ctrl+C are not the same
event, and a tool that treats them alike fights its user.** This is the same
shape as finished-versus-quit-versus-crashed in §9.14.10: three outcomes,
and conflating any two produces the wrong default for one of them.

| what happened | supervision | the Manager's session | why |
|---|---|---|---|
| **ceiling or window reached** | stops | **survives** | The user is mid-conversation and a ceiling is an accounting limit, not an instruction to stop talking. Verified: the pane outlives the supervisor. |
| **Ctrl+C** | stops | **stops** | A human saying stop. Leaving a live session spending quota with only the restarts halted is not what they asked for, and it should not take two commands. |
| **supervisor died unexpectedly** | already gone | still running | The orphan case. Nothing was there to stop it. §9.14.13. |

**The default path is one action that stops both.** Ctrl+C on `rite start
<manager>` ends the supervisor AND the Manager session.

⚠ **These two paths must not share their teardown.** They currently reach
the same place — the supervisor returns and the pane is left alone — and
separating them is the work. The bound case keeps today's behaviour, which
was a deliberate decision and is still right.

##### Ctrl+C must be clean, not merely abrupt

- **It releases the instance record.** `forget_instance` exists with **zero
  callers**; this is its caller. A stop that leaves a record behind makes
  the next `rite start` believe a Manager is still running — the stale-lock
  defect in a new place, and that class wedged the loop earlier this week.
- **It says what it did:** `stopped Manager 'planner' and its session`.
  Silence after Ctrl+C is indistinguishable from a signal that did not land,
  which is how a user ends up pressing it three times and killing something
  mid-write.
- **It is idempotent and survives a partial teardown.** A Ctrl+C arriving
  while the session is already gone still clears the record and still
  reports, because the failure being prevented is a phantom record, and a
  teardown that only works on the happy path leaves exactly that.

#### 9.14.13. `rite stop <manager>` is a recovery action, not the default

For the orphan case only: the supervising process ended unexpectedly — a
crash, a closed laptop, a killed terminal — and the Manager session is still
running with nothing watching it.

⚠ **It is not how a user ordinarily stops a Manager.** Ctrl+C is
(§9.14.12). Documenting `rite stop <manager>` as the normal route would
teach two commands where one is correct and would leave users who only press
Ctrl+C with orphaned sessions, which is the state this command exists to
clean up.

⚠ **THE NAME IS NOT AVAILABLE AS A BARE POSITIONAL, for the same reason
§9.14.7a gives about `start`.** `rite stop [DIRECTORY]` already exists — it
means *shut down with handover: release claims, update the board* — and it
already resolves a registered alias. So `rite stop planner` is ambiguous in
exactly the way `rite start planner` was, and it is worse here because the
existing command has side effects on the board.

**This must be resolved before implementation, not discovered in a bug
report.** §9.14.9 settled `start` by intercepting Manager names ahead of
alias resolution and refusing a colliding alias at `rite projects add`; the
same mechanism is available here and the collision check already exists.
Whether it is the right answer for a command that also releases claims is an
**open question for the project owner** — recorded rather than decided,
because guessing at it is how the `start` collision became three meanings
for one positional.

### 9.15. The Manager's process journal — a diagnostic mode, off by default

**Feedback about how rite is WORKING currently only exists where a human is
watching.** This week's most valuable findings came from a session noticing
something odd and saying so out loud — *"the mutant survived and the mutant
never ran produce an identical report"*, *"a wrong CLI invocation produces
output indistinguishable from the feature working"*, *"my forty minutes was
a hang, not a slow machine"*. None of that survives an unattended run. It
goes into a pane's scrollback and dies with the session.

The journal gives the unsupervised part a voice: a Manager writes what it
noticed to files, so that something going wrong at 3am with nobody attached
leaves evidence instead of silence.

⚠ **It is OFF by default and it is a BETA feature.** §9.15.1 says why, what
it costs, and what would make the default flip.

⚠ **It is v0.5.1 scope, settled, and the REASON changes how it is built.**
It was raised as the release's scope lever — the one item not needed for
"full-featured single Manager" to be true — and kept, because the next
large unattended run is a dogfood on Bentora **run by somebody who is not
the project owner, with the owner not watching.** This diagnostic is how
anything comes back from that run. It is not a nice-to-have in this
release; it is the instrument for the only big unattended run currently
planned, which is precisely the case §9.15 was written for.

⚠ **REVERSED FOR v0.5.1 (owner's decision). The requirement below is NOT
in force in that release.** Issue recording ships **undocumented**: it is
absent from the README, the CHANGELOG's 0.5.1 section and the guide, and
nothing invites a user to enable it. `--record-issues` keeps working
exactly as specified — the machinery is untouched — but it is not
advertised, and no operator is encouraged to send its output anywhere.

The reason is scope, not doubt about the design: the journal applies **no
redaction**, while §9.15.6 asks a Manager to record "a command with its
output" and §9.15.3a tells an operator to zip the directory and send it.
Redaction was assessed and deliberately deferred past 0.5.1
(`docs/design/CREDENTIAL_HANDLING_FOR_UNATTENDED_RUNS.md`), so the
feature is not promoted until the leak path is closed.

**The discoverability argument below stands on its merits and returns with
the feature.** It is recorded rather than deleted because it is the reason
the requirement existed, and whoever re-advertises this needs it.

##### Two consequences that follow from WHO runs it

**1. It must be discoverable by somebody who has not read this spec.** An
opt-in diagnostic nobody enables produces nothing, and the person running
the dogfood has no reason to know the flag exists. So `--record-issues`
appears in `rite start --help`, where somebody starting a Manager will see
it, and the docs say plainly that **an unattended or experimental run is
exactly when to turn it on.** *A capability nobody is told about is a
capability nobody uses* — the fourth instance of that class this week, and
the one where the cost is the whole point of the feature.

**2. Somebody has to know WHERE the entries are**, because the operator is
the one who copies them off. That is one printed line and nothing more —
rite builds no retrieval. §9.15.3a, including why an earlier revision
escalated this and was wrong to.

#### 9.15.0. A process issue, not a work issue — and this is the load-bearing line

A ticket that fails is **work**. It goes to the board, which is what the
board is for.

A Manager unable to tell whether a Worker is alive, a command that reports
success while doing nothing, a gate that passed on a file it could not open,
forty minutes lost to a hang that looked like a slow machine — that is
**process**. It is about the tool and the method, not about the product,
and today it has nowhere to go.

**This distinction is the whole defence against the journal becoming a
dumping ground**, which is the ordinary fate of a write-only log. A Manager
that records every failing test has produced noise; a Manager that records
*"the test command exits 0 when the file is unreadable"* has produced a
finding. Where an entry could plausibly be either, it is a work issue and
belongs on the board.

#### 9.15.1. Opt-in, beta, and the flip that is conditional on quality

Enabled by `--record-issues` on `rite start <manager>`, absent by default.

**Named for what it DOES, not for what it is.** `--diagnostics` describes
the category; `--record-issues` tells a user reading `--help` what will
appear on disk, which is the thing they are deciding about.

**Why off:** writing observations and retrospectives means a Manager
spending tokens on reflection rather than on work. Most users will not want
that burn, and the case for paying it is weak today because **nothing
consumes the output**. Self-reflection — the thing that would make it pay
for itself — is expected around v0.8.0.

**What the user is told when they turn it on.** A statement, not a warning,
printed at start alongside the resolved timezone and the engine line, for
the same reason: a cost is cheapest to understand at the moment of the
decision, not in a log afterwards.

**Genuinely off when off.** Not "writes fewer files" — a Manager running
without the flag is **not prompted to reflect at all**, and §9.15.6 makes
the `CLAUDE.md` instructions conditional to guarantee it. A half-disabled
diagnostic that still costs something is the worst of both, and it is the
shape that turns up two releases later as *"why is this slower than it
should be"*.

**The stated trajectory, recorded so it is a plan rather than an accident of
sequencing.** It ships opt-in and stays opt-in through 0.6.0 and 0.7.0,
refined as it goes, so that by the time self-reflection lands it produces
output worth reading.

This is written down for two reasons:

1. **It tells whoever maintains it what "refined" means.** For two releases
   this will look exactly like dead weight, because nothing reads what it
   writes. A stated destination — *"this becomes default-on when its output
   is good enough, and until then we are improving what it writes"* — is
   the difference between continued investment and deletion as cruft.
2. **It sets the bar for the flip.** Default-on commits every Manager to
   spending tokens reflecting, so the flip needs evidence the output earns
   it.

⚠ **The flip is conditional on QUALITY, not on self-reflection's arrival
date.** The test: *are the entries good enough that a human reading them
learns something they did not already know?* The findings quoted at the top
of §9.15 are the benchmark. If a Manager's unattended entries reach that
standard, the flip is earned. If they read as noise, it stays opt-in however
long self-reflection has existed. Without this sentence *"self-reflection
shipped, so turn it on"* becomes a calendar decision, which is how a beta
becomes a default nobody validated.

**Marked beta in the flag's help text and in the docs**, so a user enabling
it knows the entry format may change. That is what beta buys, and it is the
licence to keep refining without a compatibility argument.

⚠ **"Beta" and "turn this on for an unattended run" are not in tension, and
the help text must not read as though they were.** Raised in review as a
mixed message, and it would be one if beta meant *not ready to use*. It
does not: it is a statement about the stability of the ENTRY FORMAT, not
about whether the feature works. The two say different things to the same
reader — *use this when nobody is watching* and *do not build a parser
against what it writes yet* — and both are true. So the help text carries
both, and **neither hedges the other** — which is the property; the order
is the implementer's. A draft of this sentence mandated an order, and the
implementation reads better with `BETA.` as a leading tag than as a
trailing qualifier. A spec that fixes the order of two clauses is
legislating prose style, and review caught it doing so.

#### 9.15.2. Two kinds of entry, because the important judgements are not available in the moment

**Observations** are written when **something behaves differently from
what the docs, or the tool's own output, claimed** — at the moment it does.

⚠ That trigger is narrow ON PURPOSE, and an earlier draft of this section
had it as *"when something looks wrong"*, which is not the same thing and
is materially worse. "Looks wrong" admits every failing test and produces
the dumping ground §9.15.0 exists to prevent. **The claim-versus-behaviour
gap is the specific judgement that produced this release's findings** — a
gate reporting success on a file it could not open, a wrong invocation
producing output indistinguishable from the feature working, a probe
reporting a capability the same call then denied. Each is a document or an
output saying one thing while the system did another, and none of them is a
test going red.

**Retrospectives** are written at a boundary — a ticket closing, a review
round finishing, a merge landing.

The second kind exists because the most valuable process questions are not
knowable when the work happens. *"A bug escaped testing"* is only visible
when the bug turns up later. *"That review round produced nothing"* is only
visible after seeing what it produced. An observation-only journal
systematically misses exactly the class of finding that motivated the
feature.

#### 9.15.3. Entry format, and the anchor requirement that makes an entry checkable

One file per entry, timestamped, under the Manager's own directory
(§9.14.9): `.rite/managers/<name>/journal/<timestamp>-<kind>.md`.

**One file per observation, not a running file per session.** A single
appended file invites a stream, and separate files make promoting one to a
ticket a copy rather than an extraction.

**Gitignored by default,** which needs no new machinery: `.gitignore`
already excludes `.rite/*`, so a journal under `.rite/managers/` is ignored.
It is a Manager's own observation, not a shared artefact, until a human
promotes it — and **promotion is a COPY into a ticket**, which is the same
reason §9.15.3 keeps one file per entry.

⚠ **A draft added "unless somebody deliberately re-includes it", and that is
FALSE for this path.** `.rite/*` excludes `.rite/managers` as a DIRECTORY,
and git does not descend into an excluded directory, so a `!` negation three
levels down has no effect. Measured:

    .gitignore:  .rite/*
                 !.rite/config.yaml
                 !.rite/managers/lead/journal/e.md

    check-ignore .rite/config.yaml                  -> NOT ignored (negation works)
    check-ignore .rite/managers/lead/journal/e.md   -> ignored by `.rite/*`
    git add      .rite/managers/lead/journal/e.md   -> refused: ".rite/managers"

The negation works for `config.yaml` because it is a DIRECT child — which is
why the existing `.gitignore` comment explains `.rite/*` rather than
`.rite/`. Committing a journal entry in place needs `git add -f`. Found in
review by rite-dd, who measured it rather than reading the rule.

##### The required fields

| field | rule |
|---|---|
| `anchor` | **Required. An entry without one is not written — refused on the WRITING PATH, before any file is created.** A commit SHA, a file path with a line, a command with its output, a ticket id, or a named log file with a timestamp in it. |
| `observed` | What was seen. Factual, and tied to the anchor. |
| `expected` | What the Manager expected instead. |
| `inferred` | What the Manager concludes. **Separate from `observed`, syntactically.** |

`expected` is required because *"X failed"* without it is unactionable the
next morning. Every good finding this week had that shape: a claim about
what happened and a claim about what should have happened, which is what
lets a reader disagree with either half.

##### Measures against invented events

⚠ **An issue log containing events that did not happen is worse than no log
at all.** It is the confidently-wrong document again, and it would poison
self-reflection later — the one thing eventually meant to read it. These are
requirements on the format, not guidance to the Manager.

1. **Every entry carries a verifiable anchor** (above). This is the same
   principle as *paste the invocation and its output* — the rule this
   project adopted after a wrong CLI invocation produced output
   indistinguishable from the feature working — and the same principle a
   mandatory citation on a scenario would carry. **One principle, several
   surfaces.**

   ⚠ *Two of those surfaces were cited here by ticket id and section number
   in a draft, and NEITHER reference existed in this repository.* They were
   taken from a conversation rather than checked against the tree — which is
   this very rule being broken inside the section that states it, and it is
   left recorded rather than quietly deleted. If those rules live in an
   external tracker, the ids belong here; until somebody confirms them,
   this cites the principle and not a number.
2. **Written at the moment, never reconstructed.** Observations when
   observed; retrospectives at the boundary, while the evidence is still in
   context. **Compaction is the specific enemy.** This week produced a
   session quoting SHAs that were stale after a history rewrite, and another
   reporting reviewers as running that had never been launched. Both were
   memory, not malice.
3. **Observed and inferred are separate fields.** This week's best findings
   had exactly that shape, and its worst errors were conclusions presented
   as observations — *"the mutant survived"*, when it had never run.
4. **No claims about another agent's internal state — or about rite's
   own components' reasoning.** A Manager may record that a review produced
   no commits; it may **not** record that a reviewer *"did not try"* or
   *"was not thorough"*. Unobservable, and it is the form a hallucination
   naturally takes. The same applies inward: *"the loop did not check the
   schedule"* is recordable and checkable, *"the loop assumed the schedule
   was advisory"* is a claim about code's intent that no anchor can carry.
5. **Anchors are checked where checking is cheap.** If an entry cites a
   commit, verify it exists. Mechanical, no judgement, and it catches the
   worst class.

   ⚠ **NOT IMPLEMENTED as of v0.5.1, and named rather than left to be
   assumed.** An entry must HAVE an anchor — that is enforced (D-87). An
   anchor that is present is not yet verified to resolve, so a plausible
   but invented SHA is accepted today. Reported by rite-dd rather than
   quietly shipped as done.

   ⚠ **Every permitted anchor must be checkable, or the requirement leaks.**
   A draft allowed "a log timestamp", and a bare timestamp has nothing to
   check against — it is indistinguishable from an invented one, which is
   the exact failure this subsection exists to prevent. It now has to name
   the log FILE as well, so there is something to open. The other four were
   already verifiable. Found in review by rite-dd.

##### The refusal is a branch in the writer, and RITE OWNS THE WRITER

⚠ **D-87 was unenforceable as first written, and rite-dd found it while
implementing.** This section required a refusal on the writing path and
never said who writes. If a Manager composes markdown with its own file
tools, rite is nowhere near that path, and "refused before any file is
created" degrades into asking the agent nicely — which is what the closing
line of this subsection says beats nothing. **A refusal requirement implies
a refuser**, and the refuser has to be rite.

So entries are written through rite — `rite journal observe` and `rite
journal retrospective` — and that is what turns the anchor rule from a
convention into a rule. Measured, not asserted:

    rite journal observe --manager lead --anchor "" ...
      -> refusing to write a journal entry with no anchor
      -> files written: 0

    rite journal observe --manager lead --anchor <sha> ...
      -> recorded: .../lead/journal/<timestamp>-observation.md

##### The refusal is a branch in the writer, not a rule the writer is asked to follow

⚠ **Settled in review (rite-dd), and this section's own closing argument is
what settles it.** A requirement on the format is honoured by the Manager
choosing to honour it, which is exhortation wearing a table — and the
paragraph below names two instances this week where an explicit instruction
to check carefully immediately preceded the error it warned against.

So: **the function that writes an entry refuses an unanchored one and
creates no file.** Both halves are required and they fail for different
reasons — a refusal that still writes means the check runs after the write,
and a silent no-write means the caller cannot tell a refusal from a success.

It costs one branch, not a subsystem: the writer already has to open and
name a file, so refusing before that is cheaper than validating afterwards.

⚠ **What does NOT work, named because it is the first thing anyone
reaches for: instructing the Manager to be careful.** This week has two
instances where an explicit instruction to check carefully immediately
preceded the error it warned against. **A format that makes an unanchored
entry impossible to write beats any amount of exhortation.**

#### 9.15.3a. Getting entries off the machine is the operator's business, not rite's

**Decided: rite builds nothing for retrieval.** The entries are files on
disk in a known directory. Whoever runs an unattended session copies that
directory and sends it on.

⚠ **An earlier revision of this section called that a DEFECT and escalated
it**, on the grounds that a diagnostic whose output never leaves the host
returns nothing to the reader it exists for. The observation is true and
the escalation was wrong, and the correction is worth more than the
conclusion: **the journal's reader, in the run this was written for, is a
person the operator will speak to directly.** They will zip the directory
and send it. A mechanism was being designed for a problem that two people
who talk to each other do not have.

**What is kept, and why the PATH is now the load-bearing part:**

1. **The start line prints the journal's absolute path.** Somebody has to
   know where to copy from, and that line is the only thing that tells
   them. This is the whole of the mechanism.
2. **The `git add -f` note stays, demoted to ONE WAY of doing it rather
   than THE route.** It costs a line, it is true, and it is measured
   against this repository (below) — but committing entries is now an
   option a user might take, not the path rite recommends.

**What is explicitly NOT built:** no `rite journal export`, no archive
step, no sync, no upload. Not deferred pending a better design — not
wanted. §9.15.5's inertness already forbade transmitting them; this says
rite does not package them either.

⚠ **This also deflates the alarm the earlier revision raised about the
journal being gitignored and inside a directory a torn-down sandbox takes
with it.** The entries survive exactly as long as the directory does, and
the operator copies them before tearing anything down. That is an ordinary
sequence, not a race.

**What would make this wrong, since it is a decision and not a law:** a
user whose journal's reader is NOT somebody they can hand a zip file to —
a team, a CI pipeline, a future gate (§9.15.4). **The honest statement is
"the path is printed; how it reaches its reader is the operator's
business", and that stops being adequate the moment the reader is not a
person in the same conversation.** A later release may want a real route.
Building one now would be solving a problem this run does not have.

The measurement behind the `git add -f` line, kept because the line is
printed for somebody to paste — and measured against THIS repository
rather than against a reproduction of its rule, which an earlier draft did:

    check-ignore  .rite/managers/lead/journal/<entry>.md
      -> ignored by .gitignore:55  `.rite/*`
    git add       .rite/managers/lead/journal   -> REFUSED (.rite/managers)
    git add -f    .rite/managers/lead/journal   -> adds the entry

#### 9.15.4. Judging process efficacy, where the obvious metric is inverted

Retrospectives cover whether the gates did anything: *did the bug get caught
in testing, did that review round produce an improvement.*

⚠ **The obvious measure is backwards, and this project has already measured
it.** A round ending *"fix these three things"* produces a commit. A round
ending *"this design would force-release live Workers, start again"*
produces nothing. **Commit-based review value is biased toward cheap
reviews by construction**, so a Manager judging by output would
systematically rank bad reviewing above good. D-39 records the same shape
for delivery — a raw merged-ticket count rewards bursting even when the work
is wrong — and D-67 and D-70 are the instances: two review rounds that
produced no commits, killed three legs of an argument and caught a stop
condition whose absence would have spent money all night.

**So an entry records three facts and does not draw a verdict:**

- **what it cost** (tokens, wall-clock, rounds),
- **what changed** as a result,
- **whether the change would have been caught elsewhere.**

*"Round 2 cost 150k and changed nothing"* is useful and checkable. *"Round 2
was a waste"* is a conclusion the Manager is not positioned to draw — the
round that changed nothing may be the round that killed a design which
looked fine.

⚠ **This was a 0.6.0 question until it became a 0.5.1 one. See §9.15.3a.**
Raised in review by rite-dd as "the gate cannot reach these entries"; the
answer to *who runs the dogfood* turned the same gap into a defect in this
release, because the reader who cannot reach them is now a person rather
than a future gate.

**Connection to 0.7.0 — moved from 0.6.0.** These entries are the raw
material for the QA gate: *"a bug was not caught during testing"* is
precisely the evidence that says whether the scenario gate (D-81, §7.3) is
working. That is what the journal is being refined toward, and it is why the
entries have to be checkable rather than merely present.

⚠ **The gate is NOT a 0.6.0 deliverable.** This paragraph named 0.6.0 as its
destination until Robert moved the scenario gate to 0.7.0 on 2026-09-24
(release plan, Decision 5). Nothing in 0.6.0 consumes these entries; they
are still written, still inert (§9.15.5), and the gap recorded in the 0.20.1
revision note — the gate cannot reach machine-local, uncommitted entries —
travels with the gate to 0.7.0 unchanged.

#### 9.15.5. Nothing reads it — and the anchors are what make that safe

**A file that triggers behaviour is a control channel. This is a notebook.**
Nothing in rite reads the journal, parses it, or changes what it does
because of it.

⚠ **A Manager must not vary its own process on the strength of its own
retrospectives.** A Manager recording that reviews seem unproductive is
data. A Manager *skipping reviews* because it concluded they are
unproductive is a catastrophe — and it is the natural next step the moment
anything reads these files.

**Inertness alone is not the whole protection.** A bad entry still misleads
the human who reads it, and inertness only guarantees it misleads a person
rather than steering the system. **The anchors are what make that person's
check possible**, which is why §9.15.3 requires them rather than
recommending them.

#### 9.15.6. Two instructions, delivered in the START PROMPT — not in `CLAUDE.md`

**A capability nobody is told about is a capability nobody uses** — the
third instance of that class this week. So a Manager started with
`--record-issues` is told three things:

1. **That the directory exists**, and where.
2. **When to write:** *when something behaves differently from what the
   docs, or the tool's own output, claimed.* That specific judgement is what
   produced this week's findings, and it is far more useful than "record
   problems", which produces a log of failing tests.
3. **HOW to write — the `rite journal observe` command.**

⚠ **The third item exists because D-92 created the need for it.** While the
Manager was assumed to compose markdown itself, telling it *where* and
*when* was enough. Now that the anchor refusal lives behind `rite journal
observe` (D-92), **a Manager told where and when but not how will write a
markdown file by hand into that directory** — bypassing the refusal
entirely and producing exactly the unanchored entries D-87 exists to
prevent. The command is part of the instruction, or the mechanism is
optional.

That is a defect introduced by a fix, one step removed from the fix — the
shape this project files as class 12. Raised by rite-dd while implementing
against the two-item version.

⚠ **A draft put both in the generated `CLAUDE.md` and that CANNOT BE
IMPLEMENTED.** `CLAUDE.md` is project-level: written by `rite init`,
refreshed by `rite update`, and never touched by `rite start`.
`--record-issues` is per-START. Two Managers in one project started
differently would need two versions of one shared file, and neither start
writes it. Found by rite-dd while implementing, reported rather than worked
around.

⚠ **The consequence while the gap was open is the exact class this
subsection exists to prevent**: a Manager started with `--record-issues` was
told where the journal is and was never told to write anything to it. *A
capability nobody is told about*, reinstated by the section written to
prevent it.

**They go in the prompt sent at start (§9.14.11a),** which is per-session by
construction — so "genuinely off when off" (§9.15.1) becomes exact rather
than aspirational. A Manager started without the flag does not receive the
instructions because they were never composed, not because a shared file
was filtered. **This is a stronger guarantee than the `CLAUDE.md` route
could have given**, and it is the one case this week where a gap improved
the design rather than costing something.

It also makes §9.14.11a's prompt load-bearing rather than a convenience:
the prompt is the only per-session channel to the Manager that rite owns.

### 9.16. Talking to a Manager — channels, authority, and what counts as an instruction

**Status: DECIDED 2026-09-25 (Robert, D-94–D-96). Not built.** The mailbox
(`rite message`, `rite reply`, `rite replies`, `rite connect`) shipped in 0.5.1
and 0.6.0. The Slack relay and the check-ins that use it are planned in
`docs/design/V060_RELEASE_PLAN.md` § A and § K, with the design in
`docs/design/V060_CHECKINS.md`.

#### 9.16.1. Two separate questions, and neither answers the other

Every message that reaches a Manager is asked two things, and they are
**independent**:

| | asks | decided by | answers |
|---|---|---|---|
| **Authority** | *may this person direct the Manager?* | **which channel it arrived on** (D-95) | the Owner's DM, or the local machine: yes. Anywhere else: no |
| **Addressing** | *is this meant for the Manager at all?* | an `@rite` mention, or a reply to something rite asked (D-94, D-96) | addressed, or unaddressed |

**A message is an INSTRUCTION only when it is both authorised AND
addressed.** Everything else still reaches the Manager — as **context**. That
rule is the composition of D-94, D-95 and D-96. Each decision is recorded on
its own below, so that none of them is later read as implying another.

#### 9.16.2. Authority comes from the channel (D-95)

**The Owner's direct message with the rite app is the command channel.** It
is one-to-one by construction: only the Owner and the app are in it. So
"only the Owner can instruct" is a property of where a message was posted,
not a check rite has to get right on every message. The local routes,
`rite message` and `rite connect` run by the machine's user, carry the same
authority. They are the Owner at the terminal.

**A configurable channel is BROADCAST, defaulting to `#all-rite`.** Status
updates and check-in digests are posted there for anyone to read. Messages
typed in it reach the Manager as context. **They never carry authority,
whoever types them and whatever they say.**

⚠ **This adds Slack scopes.** A3a established that `channels:history` and
`chat:write` suffice for a public channel. Reading the Owner's DM needs the
IM equivalents. The exact set is to be measured when the relay is built
(plan § A6), not assumed here.

#### 9.16.3. Unaddressed thread comments are context, never instruction (D-94)

A reply in a thread under a status update, with no `@rite` and not answering
a question rite asked, **still reaches the Manager**. People discussing a
standup are telling the Manager something worth knowing. But it arrives as
context, and **the prompt says so explicitly**: every relayed message
carries a line naming its channel, whether it was addressed, and therefore
what it counts as. The distinction must not rest on the model noticing that
a mention was absent. An absent word is the weakest signal there is.

#### 9.16.4. `@rite` is a filter for "is this addressed to me", not a "do this" (D-96)

A mention means *this is meant for you*. It does not mean *do it*: an
addressed instruction is still **judged** like any other request from the
Owner, and it can be declined or questioned.

⚠ **`@rite` is NOT an access control, and nothing in rite's documentation
may describe it as one.** Anyone in the workspace can type it. A mention in
the broadcast channel is **addressed and unauthorised**: it reaches the
Manager labelled as a request from someone who is not the Owner, and is
context. Authority is §9.16.2's question, answered by the channel. A
document that says "mention @rite to give it an instruction" without naming
the channel has blurred the two, and is wrong.

#### 9.16.5. What a relayed message looks like to the Manager

Composed as TEXT, so the mailbox keeps its invariant that nothing records a
sender (the 0.5.1 mailbox, kept by the per-reader cursor of Decision 1a and pinned by `TestTheSupervisorDoesNotCareWhoWrote`). The Slack relay
states what it observed; the mailbox does not grow a field.

    [Owner's DM · addressed · INSTRUCTION] <text>
    [#all-rite · thread under the 14:00 check-in · unaddressed · context] <author>: <text>
    [#all-rite · @rite from <author>, not the Owner · context — not an instruction] <text>

**If § N lands**, inbound Slack text passes through §6.6's normalisation and
phrase reporting before it is relayed, because a Slack message is untrusted
text from outside, like a ticket. **Until then, it is relayed as typed.**
⚠ **Extending §6.6 to Slack is this section's inference, not part of the
decisions above.** Robert accepted it on 2026-09-25, and the label stays
because it records where the rule came from.

## 10. Credentials

**OS keychain via Python `keyring`** (macOS Keychain, Linux Secret Service, Windows
Credential Manager), with an explicit **environment-variable fallback** for headless
contexts (CI, containers). On startup, rite announces which store it used — keychain
or env var — so the user knows where their credentials live.

- **Scoped tokens, least privilege — by PERMISSION, and by project.** A JIRA
  token gets board read/write, not admin. A GitHub token is scoped to the
  project's repos with contents and pull-requests only, not org admin and not
  the rest of the account. It is **not** scoped per Worker: §5.3.4 explains why
  workers are fungible and what that costs.
- **Easy rotation.** `rite credential rotate` walks through each stored credential,
  shows when it was last set, and prompts for a replacement. A credential that is
  painful to rotate never gets rotated.
- **No credentials in config files.** `config.yaml` names the credential key
  (e.g. `jira_token`), never the value. The value lives in the keychain or an env var.

### 10.1. What is stored where

| | |
|---|---|
| the secret | OS keychain, service `rite`, account `<namespace>/<key>` |
| the namespace | `.rite/config.yaml`, key `credentials.namespace` — **committed** |
| the composed account names | nowhere; `rite credential list` composes and prints them |

### 10.2. Per-project credentials

A credential belongs to **one project**. The secret lives in the OS keychain;
the project's **namespace** lives in its committed `config.yaml`, and the
keychain account is composed as `<namespace>/<key>`.

**The defect this removes.** The keychain service was `rite` keyed by the bare
credential name — a **flat namespace**. Every project on a machine addressed the
same `jira_token`, so two projects could not hold two JIRA identities, and on a
machine running a client-privileged project beside anything else, the client's
token was the entry every other project read.

**The namespace is recorded, never derived.** A generated id
(`acme-7f3a9c21`) is written to `config.yaml` once and committed. Deriving it
from `project.name` would also survive a clone, but fails two ways that matter:
renaming the project orphans every secret under the old name, and two checkouts
of one project on one machine — a second clone, a worktree — collapse into a
single identity, which is the opposite of the point. **Names travel; secrets
never do.** A teammate cloning the project learns exactly what to set, by name,
without being given anything — the `.env.example` pattern.

**Resolution order**, for every credential:

1. `RITE_<KEY>` in the environment.
2. This project's namespaced entry, `<namespace>/<key>`.
3. The machine-wide entry named by the bare `<key>`.

Tier 3 is what keeps an existing install working, and **it must never be
silent**. A project resolving through a shared entry while its owner believes it
has its own is the flat namespace surviving under a new name, so every use
announces itself and names the account that would have been used.
`rite credential migrate <key>` copies global → project-scoped, leaving the
global entry in place for the other projects still resolving through it.

Tier 1 stays on top because a sandboxed Worker **cannot read the keychain at
all** — see below — so an env var is the only channel that reaches one.

### 10.3. What namespacing enforces — and what it does not

> Per-project credential namespacing prevents accidental cross-project use and
> makes the correct credential the easy one to reach. **It is not a security
> boundary.** Keychain access is per-user, not per-process, so any unsandboxed
> process running as the user can read every entry rite has stored. The only
> enforcement is the sandbox — and a sandboxed Worker cannot read the keychain
> at all, which is why its token is injected rather than fetched.

Measured 2026-09-12, macOS, yoloAI seatbelt backend, the real installed CLI, in
a freshly created sandbox:

| | result |
|---|---|
| `rite credential check jira_token` on the host | `set` |
| the same command **inside the sandbox** | `not found` |
| `keyring.get_password("rite", <any name>)` inside | `None` — no exception, no prompt |
| `stat` of `login.keychain-db` inside | **succeeds** (metadata) |
| `ls ~/Library/Keychains`, and reading the file's **contents**, inside | `Operation not permitted` |
| unsandboxed, same interpreter, every account under service `rite` | **all readable** |

The mechanism is a **filesystem denial of the keychain's contents**. The profile
allows `file-read-metadata` globally, which is why `stat` still answers and the
file looks present — so "the file is visible" is true of its metadata only, and
is not the reason the lookup fails. The denial applies to a project's **own**
credentials exactly as much as to any other project's.

Two consequences follow, and both are design, not accident:

- **For a sandboxed Worker the boundary is real and total**, and it is *why*
  `rite sandbox start` injects the token with `--env` (D-31): the Worker cannot fetch it. Measured
  on yoloAI 0.11.0 with Seatbelt, `--env` values do not stay out of sight
  inside: yoloAI types them into the session's shell as `export NAME='value'`
  in its launch command, so they are on the session's screen, which
  `yoloai attach` shows. `rite sandbox pane`, which Claude sessions read,
  redacts them. The host process decides which single token to inject, and the
  sandbox stops the Worker going around that decision. Namespacing determines
  *which* token the host picks — it is not what protects it.
- **For an unsandboxed Worker the namespace is convention only.** `sandbox.enabled`
  defaults to `false`, so this is the default path. A process running as the user
  reads every entry under service `rite`, exactly as any user process can.

Because the sandboxed lookup returns `None` rather than raising, "not found"
inside a sandbox means **"cannot check"**, not "missing" — the same distinction
`SandboxStatus.known` and `CountUnavailable` exist to preserve. `rite credential
check` and `rite credential list` detect the unreadable keychain and say so,
rather than advising `rite credential set`, which would change nothing.

### 10.4. Phase 2 — multi-machine credential identity

**Module lists are per-project, shared between Managers.** Two Managers on two
machines work from the same `modules.yaml`; a Manager does not carry a module
set of its own, and neither does a Worker beyond the subset it was created
with. Recorded here rather than only in §5.3.4 because it is a Phase 2
statement: when several Managers coordinate on one project, the set of repos
the project consists of is a property of the project, not of whichever machine
happens to be running.

Combined with §5.3.4's fungibility decision, that means the credential set is
the same everywhere: per project, per person's own keychain, identical in
name across machines, and identical in contents across the Workers any one
Manager starts.

Multi-machine credential identity was flagged as a Phase 2 blocker that was on no
list: with a flat namespace, nothing said how two machines hold distinct
identities for the same project.

**A recorded namespace answers it.** Both machines clone the same `config.yaml`,
so they agree on the *name* — `acme-7f3a9c21/jira_token` — and each holds its
**own secret** in its **own** keychain under that name. Same logical key, a
different value per machine, no coordination and nothing shared. That is exactly
what per-machine identity requires, and it falls out of §10.2 rather than needing
machinery of its own.

### 10.5. Credentials belong to a service, not to a key

`rite credential set` took a KEY — `jira_email`, `jira_token`. That is rite's
internal vocabulary, and it required the user to know both that JIRA needs two
of them and what each is called before they could set anything.

**The reported failure is the direct consequence.** Someone reaching for their
JIRA login typed `rite credential set <their-email-address>`; rite stored the
ADDRESS as a key name, printed "stored ... in keychain", and JIRA was still
unconfigured — surfacing much later as a failure naming a key they had never
typed. Validation can only ever *detect* that after the fact. Taking a
**service** makes it unavailable: `jira` is validated against the registry,
and rite then asks for the fields JIRA has, in order, in the user's words.

```
rite credential set jira        # asks: account email, then API token
rite credential set github      # asks: token. No username — see below
rite credential set claude      # asks: the token `claude setup-token` prints, which
                                #   `rite sandbox start` passes in (a sandbox cannot
                                #   read the keychain's Claude login)
```

**Each service carries its own field list** (`credentials/services.py`), and
that is load-bearing rather than an implementation detail. A GitHub
fine-grained PAT has no username; a key-plus-secret service has two secrets
and no address. One hardcoded username-then-token pair would make every
service that is not JIRA-shaped either wrong or a `custom`, and `custom` would
become the dumping ground for everything real.

A field's stored key is `<service>_<field>`, which yields exactly the
`jira_email` / `jira_token` / `github_token` that already existed — so nothing
stored before this needs moving or re-typing, and the single-key form
(`rite credential set jira_token`) still works for replacing one field.

**A field is either a secret or configuration, and they go to different
places.** JIRA needs a site and a board key alongside the token, and those are
not credentials — they are the same for everyone on the team. Secrets go to the
keychain; config fields carry a `config_path` and are written to the committed
`config.yaml`. That completes the `.env.example` pattern §10.2 started: a
teammate clones the project, already has the site and the board key, and needs
only their own token. One command sets up a working integration instead of
setting a credential and then separately discovering the config it also needed.

**rite stores and injects; it does not interpret.** A credential is a name, a
set of fields, and a destination environment variable. rite does not know what
the API does and must not grow per-service logic beyond the field list and its
validation — that boundary is what keeps this from becoming a secrets manager.

---

## 11. Publish gate

Deterministic scanning built on **gitleaks**, extended with project-specific rules.
Zero LLM tokens. Seconds to run.

### 11.1. Why this exists

**An early build of this repository is the working example** — before its history
was rebuilt, so the commit described no longer exists to inspect.

A session building this tool copied the upstream coordination repo wholesale into the
repo intended for publication. It carried across, in ~40 places over 12 files, a
directory name identifying a real legal matter — **a personal one, not a client's and
not any project's data** — that the upstream's own deny-list existed to protect.

**Nothing there was wrong where it was written.** A deny-list has to contain what it
denies; leak-probe fixtures have to contain the thing they probe for. In a repository
that was never going to be published, every one of those occurrences was correct, and
every human who read them was right not to flag them. **The failure was the boundary
crossing, and a boundary has no reviewer** — the copy was one action, and correctness
does not travel with content into a repo whose whole purpose is publication.

That is what makes this the normal failure mode rather than carelessness. Nobody has
to be inattentive for it to happen; the content has to move, and content moves
constantly. A human check asks "is this wrong?" and gets the right answer. Only a
check that runs *at* the boundary asks the question that matters, which is "is this
wrong **here**?"

**It never reached a remote.** The repository had none; the contamination was caught
and the repo rebuilt clean before one existed.

**Most of those occurrences were not in code** — they were in review registers and
source comments *documenting* the protection. That detail is load-bearing for §11.2:
a gate that scanned only source would have passed the tree.

It is the case for a deterministic check that does not rely on anyone remembering.

### 11.2. What it scans

The gate scans **all committed content** — source code, documentation, comments,
test fixtures, commit messages, and file names. Not just code.

In §11.1's incident, most occurrences were not in code at all: they sat in review
registers and source comments *describing* the protection. A gate that examined only
source would have passed that tree — the strings that mattered were in the prose.

The `kb/` directory is scanned **hardest** — it is where proprietary and niche material
lives, the most likely vector for content that should not leave the team (see §8.7).

### 11.3. Default rules

Two classes of finding are built in:

1. **Project-specific markers.** Configurable patterns in `config.yaml` — whatever
   identifies your organisation, its customers, or the work itself: company and
   customer names, internal project codenames, account or contract numbers, record
   identifiers in a regulated domain. rite ships none of these by default and cannot
   guess them; the shape of "what must not leave" is the one thing only the project
   knows. Extended via a `.rite/gitleaks.toml` for full gitleaks rule syntax.

2. **Hardcoded local paths.** `/Users/<username>/...`, `/home/<username>/...`, and
   `C:\Users\<username>\...` in committed code. These are not client data, but they
   expose who wrote the code and how their machine is arranged. For a tool published
   to strangers, that is a leak. Paths in committed code should be configuration or
   environment variables, never literals.

### 11.4. Suppression

Opt-out is **per-finding suppression** with a recorded reason, `.gitleaksignore`-style.
Never a global off switch.

A finding that is genuinely safe (a test fixture using an invented name that happens to
match a pattern, a documentation example) gets a one-line suppression with the reason.
The suppression file is committed and reviewed like any other change. The gate checks
that every suppression still matches a live finding — stale suppressions are noise and
a sign that someone is accumulating rather than deciding.

**A suppression names what was found, not where it was found.** gitleaks identifies a
finding as `commit:file:rule:line`, and a line number is a proxy: inserting a line
above a suppressed one moves it, so the entry matches nothing, the finding it covered
starts blocking, and the entry is reported stale — while the code someone decided
about has not changed. Measured in rite's own repository, mid-push: three added
comment lines in `gate/gate.py` moved a suppressed docstring by three lines and failed
the gate on a decision already made and written down. rite therefore also accepts
`commit:file:rule:sha256-<digest>`, the matched text's digest in place of the line,
and every report prints that form for any finding that has one. The line form stays
valid — it is what gitleaks prints, and existing files are full of it.

The content form is narrower in the way that matters: change the matched text and the
suppression stops applying, which is exactly when the decision deserves making again.
For the same reason a stale content entry is **never** offered a "re-point" — it did
not move, its text is gone, and one paste onto whatever replaced it would carry an
accepted reason to a string nobody has read.

It is wider in one way, and that width must be reported rather than assumed: dropping
the line means two identical matches of one rule in one file share an entry, so the
gate says how many findings an entry is covering. And because a digest commits to its
text — for a home path or a username, a confirmable guess rather than a one-way
function — an entry must be deleted in the same change that scrubs the string it
names, not left behind. Both are properties of the file, which is committed and
published with the project.

### 11.5. Integration

- `rite publish check` runs the gate locally. It is a dry run — it refuses or passes
  but changes nothing.
- The gate runs before any `git push` from a rite-managed workspace, via a
  `pre-push` hook installed by `rite init` — **when git will actually read it.**
  See below; this is not a given.
- `rite publish install-hook` installs that hook into a project that is already
  initialised and has none. Re-running `rite init` is not the remedy: it offers to
  wipe the project's config rather than touch the hook.
- CI integration: a GitHub Action / JIRA automation that runs the same gate. Belt and
  braces — the hook catches it at the developer's machine, CI catches it if the hook
  is bypassed.

#### 11.5.1. `core.hooksPath` — when the hook silently does not exist

⚠ **`core.hooksPath` redirects hooks wholesale, and git then ignores `.git/hooks`
completely.** A gate written there is executable, correct, and never read. Found by a
cold rehearsal on a machine with a global `core.hooksPath`: `rite init` printed
`✓ Created .git/hooks/pre-push`, and a subsequent push carrying four planted secrets
went through with exit 0 and no gate output at all.

**This is the worst shape a defect in this tool can take** — the publish gate is the
thing standing between a secret and a remote, and it was reporting itself installed
while being entirely inactive. It is the same family as §2.5.10's refusal-without-tmux
and §5.3's uncountable worker cap, one step earlier: not a check that could not run,
but a check that was never installed, reporting itself installed.

**Both installers ask git where it will actually look** (`git rev-parse --git-path
hooks`) and compare that against the repo's own hooks directory. When they differ,
`rite publish install-hook` refuses with the reason and the remedy, and `rite init`
declines to count the repo and prints a warning rather than silently omitting the
line. The remedy is the user's choice of `git config --local core.hooksPath
.git/hooks` followed by `rite publish install-hook`, or adding `exec rite publish
pre-push` to the pre-push hook in the redirected directory by hand.

**Deliberately NOT handled by writing into the redirected directory.** That path is
typically shared across every repo the user owns, so installing there would reach far
outside the project `rite init` was pointed at — the same "surprise in someone else's
repo" the installer already refuses to cause by clobbering a hand-written hook.

**Fails OPEN when git cannot answer** (not a repo, git missing, a timeout). The
installer's job is to install a hook, not to police git's configuration, and a false
"redirected" would block a legitimate install for no reason. Note the asymmetry
against §9.11's rule: refusing on *uncertainty* is right when the operation is
destructive (§2.5.10) or when a silent pass defeats a cap (§5.3); here the uncertain
answer blocks nothing dangerous, and the cost of being wrong runs the other way.

⚠ **Consequence for §11.5's "belt and braces": the two layers are not independent.**
The hook lives on the developer's machine and is disarmable by that machine's own git
configuration, without any action by the developer and with no signal that it
happened. CI is the only layer that local configuration cannot switch off — which
makes it the load-bearing one, not the backup, and makes a red or absent CI gate a
publish-blocking condition rather than an untidiness.

---

## 12. Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Owner machine                     │
│  ┌──────────┐   ┌──────────┐   ┌────────────────┐  │
│  │  Owner    │──▶│ Manager  │──▶│  Worker alpha   │  │
│  │ Dispatch  │   │ Dispatch │   │  Worker beta    │  │
│  └────┬─────┘   └──────────┘   └────────────────┘  │
│       │                                              │
│       │  (REST: JIRA / GitHub API)                    │
│       │                                              │
├───────┼──────────────────────────────────────────────┤
│       ▼          Member machine                      │
│  ┌──────────┐   ┌────────────────┐                   │
│  │ Manager  │──▶│  Worker gamma   │                   │
│  │ Dispatch │   │  Worker delta   │                   │
│  └──────────┘   └────────────────┘                   │
└─────────────────────────────────────────────────────┘

Shared state:
  ┌─────────────┐   ┌──────────────────────────────┐
  │ Ticket       │   │ Coordination repo (git)       │
  │ backend      │   │                               │
  │ (JIRA/GH)   │   │  state branch (1 commit)      │
  │              │   │   owner-lease, claims,         │
  │              │   │   heartbeats, promotions       │
  │              │   │                               │
  │              │   │  main (message log)            │
  │              │   │   decisions, handovers,         │
  │              │   │   blockers, audit trail         │
  └─────────────┘   └──────────────────────────────┘
```

### 12.1. Per-machine state

Each machine runs:

- One Manager Dispatch (a long-running Claude session or a cron).
- N Worker sessions, each in its own directory.
- A local claims ledger for its own Workers.

### 12.2. Cross-machine state

Two shared stores, each for a different purpose:

**Coordination repo (git)** — the machine-to-machine channel, split into two
mechanisms (§3.3):

- **State branch** (force-pushed, always 1 commit) — Owner lease, Manager heartbeats,
  published claims, promotion requests. Ephemeral, no historical value, atomic via
  `--force-with-lease`.
- **Message log on `main`** (ordinary commits) — decisions, blockers, handovers,
  promotion events. Durable, auditable, low volume.

The state/message boundary is specified in §3.3.3. The state layer is behind an
interface (§3.3.5) so git can be replaced by Redis or a database for teams that
outgrow its latency.

**Ticket backend (JIRA / GitHub)** — the human-readable surface:

- Ticket assignments and status (via labels, not assignee fields).
- Board columns and workflow state.
- Handover comments (written by `perform_handover`, readable by humans).
- Manager ↔ Owner messages for decisions that benefit from ticket-thread context.

### 12.3. Failure modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Manager machine offline | Owner sees no heartbeat | `perform_handover` on its behalf; return tickets to pool |
| Worker session dies | Manager sees no progress on claimed paths | Reclaim and reassign or surface |
| Owner machine offline | Owner lease expires (§2.4.1) | Next-in-line Manager promotes via atomic push (§2.4.2) |
| Split brain (two Owners) | Non-fast-forward push rejection (§2.4.2) | Loser pulls, re-reads, stands down. Exactly one winner. |
| Owner hung but alive | Lease expires despite apparent liveness | Same as offline — lease is the authority, not the process |
| Graceful demotion race | Higher-priority Manager returns mid-operation | Protocol: request → finish → transfer → release (§2.4) |
| Stale claim | Transcript mtime > threshold | Auto-expire; `force-release` for manual override |
| Ticket backend down | API errors | Retry with backoff; continue work locally; sync on recovery |
| Coordination repo unreachable | Push fails | Lease renewal fails → eventual expiry; local work continues |
| Clock skew between Managers | A challenger's clock disagrees with the incumbent's renewal timing | 60s skew-tolerance margin on lease expiry (§2.4.1, D-42) — narrows the window, does not close it |
| Non-election writers race on the state branch | Heartbeat/claims push rejected despite no real conflict | Fetch → read-merge-write the FULL state → push-with-lease → retry-on-rejection, the same loop as promotion (§2.4.2) |
| Coordination repo host blocks force-push | Every promotion/heartbeat push fails, permanently not as a race-loser | `rite doctor` probes this at setup (a real trial force-push); state branch must be excluded from branch-protection rules and CI/webhook triggers |
| Coordinator pool exhausted by correlated failure | `rite status` shows live pool near zero, stale count high | `rite pool fill` — closing the gap in seconds the moment a human is present (§2.5.5) |
| Quota projected to exhaust before week-end | Burn-rate measurement's projection crosses the budget line early | Warn with the projected shortfall (§2.6.2) — the user adjusts their own schedule (§2.7), rite does not act automatically |
| Aggregate scheduled load across projects exceeds weekly budget | Hub sums registered projects' schedules at configuration time (§2.7.4) | Warn before the schedule/registry change is saved, not after quota is exhausted |

---

## 13. Decisions register

**Supersession convention:** a decision that is later reversed or narrowed says so
in its own Decision cell (`⚠ Superseded — see D-NN`), rather than being silently
edited in place. §13's history had exactly one un-marked reversal before this
convention existed (the old D-3, below) — the convention exists because that
happened once already and left no trace until this review found it.

| ID | Question | Decision | Rationale |
|----|----------|----------|-----------|
| D-1 | Manager ↔ Owner transport | **Python over REST** | No MCP: each MCP call costs an LLM turn; heartbeats/polling/labels are nearly all the traffic; rate-limited at 500/hr free tier. REST is zero-LLM-cost and unlimited. |
| D-2 | Ticket backend | **JIRA (default), abstract interface for others** | Validated against a real JIRA Cloud instance. Company-managed board for workers (shared workflows, better automation). GitHub Issues as alternate backend. |
| D-3 | Owner availability | **Lease-based failover.** ⚠ The "+ degraded mode" half of this decision's original headline was never given a body mechanism and was silently dropped from §12.3's failure table at v0.6 — striking it here rather than letting a reversed decision pose as active. With a priority list, "no Manager can promote" only occurs when zero Managers are active, in which case there is no session left to self-assign to; the case the old clause described does not actually arise under the lease model. | Priority-ordered Manager list; first active is Owner. Lease expires unless renewed → next-in-line promotes via atomic git push (§2.4.2, with the clock-skew tolerance and self-check-before-standdown fixes added 2026-09-10). |
| D-4 | Publish gate | **gitleaks + project rules** | Deterministic, zero tokens, seconds to run. Scans all content including docs and comments. Per-finding suppression, never global off. See §11.1 for the incident this exists for. |
| D-5 | Semantic conflicts | **File-level claims for v1** | Semantic conflicts are rare enough to handle at review time. Revisit on measurement. |
| D-6 | Expert unavailability | **Timeout and reroute** | Configurable timeout (~2 hours). Fallback to next-best match, then Owner. Block only if Owner explicitly requires a specific person. |
| D-7 | Credentials | **OS keychain via `keyring`** | macOS Keychain / Linux Secret Service / Windows Credential Manager. Env-var fallback for headless. Easy rotation command. No credentials in config files. |
| D-8 | Implementation language | **Python** | The upstream is Python + Bash. The transport is REST from Python. No reason to introduce Node.js/TypeScript for a coordination tool whose users are Claude Code sessions. |
| D-9 | `rite add module` scope | **Local only; no remote repo creation** | Keeps auth surface to ticket read/write and code push. Users create remotes with their existing tool (`gh`, `glab`, etc.) and register the URL with rite. |
| D-10 | CLI framework | **`click`** | Better than `argparse` for nested commands. Gives `--help` on every subcommand, exit codes, and type validation for free. |
| D-11 | Brief file name | **`brief.yaml`** | Not "seed" — brief is what a person would call it. The questionnaire produces it; follow-ups go into the project spec rather than back into it (D-53). |
| D-12 | KB link handling | **Fetch with caching + manual refresh** | Cached so repeated reads are free and offline works. `rite kb refresh` re-fetches on demand. Fetch dates recorded for staleness detection. |
| D-13 | KB git tracking | **Authored committed, fetched cached/gitignored** | Authored knowledge is a team asset (versioned, reviewable, new members inherit). Fetched caches are reproducible from source, large, and possibly licence-encumbered. User chooses at init; default is committed. |
| D-14 | Stop/timeout convergence | **Single handover function for all THREE triggers** — `rite stop`, heartbeat timeout, AND Owner lease expiry (Phase 2) | `perform_handover` is called by `stop`, by the Owner on a stalled Manager's behalf, and by the next-in-line Manager on a stalled Owner's behalf. ⚠ The third caller was consistently undercounted to "both" across the plan, the shipped code's own docstrings, and this row — corrected here; Phase-2 work implementing P2.2/P2.3 must call `perform_handover` for the outgoing Owner's own in-flight tickets/claims on promotion, not only for a stalled Manager's. |
| D-15 | Stop offline resilience | **Queue locally, flush on contact** | `stop` must succeed when JIRA is unreachable. Handover queued in the outbox and flushed on next contact. Local shutdown never depends on the network. |
| D-16 | Owner election primitive | **Git push as compare-and-swap** | Non-fast-forward push rejected → exactly one winner. JIRA has no equivalent (last-write-wins). Second argument (after rate limit) for a coordination repo alongside the ticket backend. |
| D-17 | Owner liveness | **Lease, not heartbeat** | A heartbeat says "I was alive"; a lease says "I am alive until T". Hung-but-alive Owner loses the role automatically on expiry. Measured (§2.4.1): the upstream's coordination ledger records 46 force-releases, 44 of them because the holder was gone — dead, killed without its release hook firing, or idle with no outbox entry — while its claim record still read as live. A heartbeat cannot tell those from healthy work. |
| D-18 | `start` orientation | **Decision table, not just setup** | `start` assesses project state and acts: resume in-progress, surface blockers, begin spec from brief, pick up scheduled tickets. A returning Manager reaches the same conclusion as a fresh one. |
| D-19 | Coordination transport | **One git repo, two mechanisms** | State branch (force-pushed, 1 commit) for ephemeral data; message log on `main` for durable audit trail. Solves repo-growth objection (~315k commits/yr) without losing CAS atomicity (`--force-with-lease`). |
| D-20 | State-layer interface | **Abstracted, git is default** | State layer behind an interface (read/write CAS + append message). Default is git (zero infrastructure). Redis or S3 can substitute for teams needing lower latency. |
| D-21 | Alternatives to git coordination | **Redis/S3/JIRA all dropped as defaults** | Redis: best fit but paid infrastructure + credential. S3/GCS: CAS but requires cloud account. JIRA: no atomic primitive, rate-limited, heartbeats-as-tickets grotesque. All available via the interface. |
| D-22 | Licence | **MIT** | — |
| D-23 | AI provider abstraction | **None — Claude-native** | No provider abstraction layer. `CLAUDE.md`, `.claude/agents/`, Dispatch, Claude Code sessions are first-class concepts. Abstraction weakens every integration point to the lowest common denominator. Customisation for other providers available via consulting. |
| D-24 | Distribution | **`pipx install rite-ai` + curl script** | `pipx` primary (isolated env, no conflicts). `curl \| sh` for quick try. Homebrew deferred until traction. `rite update` detects installation method. Distribution name is `rite-ai` and the import package is `rite_ai` because PyPI `rite` is taken by an active unrelated project that also ships a `rite` command and owns the `rite` import name; measured, a non-isolated install of both silently merges the two `rite/` trees and breaks both, and an alias command alone does NOT avoid it. Commands shipped: `rite` (primary) and `rite-ai` (alias). |
| D-25 | Python version | **3.11+** | `tomllib` in stdlib, `ExceptionGroup`, `TaskGroup`, modern type hints. No reason to support older versions for a new tool. |
| D-26 | Worker capacity model | **On-demand sandbox spawn via yoloAI `mcp serve`, not a pre-warmed Worker pool** | A human starts the Manager session; that session creates its Workers' sandboxes from within itself, as an ordinary tool call. The pre-warmed Worker pool existed because session creation was believed to need per-session human approval (§2.5's superseded framing); once a human-started Manager creates its Workers' sandboxes itself, that approval is paid once rather than per Worker, and the pool's reason for existing goes with it. Nothing here routes around a confirmation step. Verified: 14 sandbox tools, boundary confirmed empirically (§5.3.1). |
| D-27 | Coordinator redundancy | **Small fixed standby pool (2–3), not worker-count-proportional** | Managers/Owner are still human-started sessions (D-26 doesn't apply to them) — standby coordinators remain useful for fast takeover on death. §2.5. |
| D-28 | Pool readiness | **Manager-probed readiness lease, borrows the Owner lease's shape not its cross-machine machinery** | A pool believed warm but silently dead is worse than no pool. Verified from the outside (mechanical, zero-token), not self-reported by the idle session, which cannot run a turn to renew anything. §2.5.2. |
| D-29 | Context concentration | **Cap concurrent Workers per Manager; Workers report conclusions not transcripts** | On-demand spawning (D-26) removes pool sizing as a problem but creates a new one — N Workers reporting into one Manager session accumulates in that session's context every turn. §2.5.9, §3.1.1. |
| D-30 | Sandbox network isolation | **Seatbelt (macOS's `sandbox-exec` mechanism; yoloAI's `--backend seatbelt`) provides none; Docker/Podman/Tart required whenever network containment is the goal** | Verified empirically, both the isolation gap and the flag value itself — an earlier version of `sandbox.backend`'s default and `rite init`'s config example both wrote the literal string `sandbox-exec`, which `yoloai new --backend` rejects outright (`yoloai system backends` lists `seatbelt`, not `sandbox-exec`). The "layered pair" security model (§5.3.2) only holds for local damage under Seatbelt — network is an uncontained third channel unless the backend is switched. |
| D-31 | Sandbox token delivery | **`--env` into the shell that execs the agent** | Rules out file-based or argv-based delivery, both of which leak beyond the intended process (file persists in sandbox FS; argv is visible via `ps`). §5.3.3. |
| D-32 | Durable-state rule scope | **Generalised from Owner-only (§2.4.3) to all roles (§2.0)** | Was already implicit for Manager (via `start`'s orientation table) and unstated for Worker; stating it once, generally, prevents each role's persistence requirement from being independently rediscovered. |
| D-33 | Worker commit cadence | **Commit at each meaningful checkpoint, never batched to task-end** | Worker death is the expected failure mode (§12.3), not exceptional. An uncommitted working tree at time of death is unrecoverable work, regardless of effort spent. §2.1. |
| D-34 | Multi-project structure | **Registry with aliases (`~/.rite/dispatch/projects.yaml`), not a parent-directory hierarchy; additive to the existing single-`.rite/`-per-project model** | Keeps the primary single-developer, single-project product (§1) untouched — no registry required unless multiple projects are actually in play. Aliases avoid forcing all projects under one parent path. §8.9. |
| D-35 | Handover cadence | **Two mechanisms: a continuously-written ephemeral snapshot (every scheduled cycle) plus the existing event-triggered `perform_handover`** | Closes the stale-handover window between detection cycles without replacing the transition-marking function that releases claims and writes the audit trail. §9.10.1. |
| D-36 | Coordinator context cost | **Reports are conclusions, not transcripts; hub sessions stay thin; handover snapshots enable deliberate coordinator restarts as a cost control, not only crash recovery** | Measured: coordinator session ~500K tokens/turn, ~98% of spend — driven by accumulated history, not `CLAUDE.md` size. §8.6.1, §3.1.1. |
| D-37 | Stall detection mechanism | **A plain, non-LLM watchdog script (~5 min cadence) replaces the LLM-costed sweep** | Measured: the sweep it replaces cost ~484K tokens per wake-up, most of which concluded nothing had changed. The watchdog wakes the Manager only when judgement is actually required. §3.5. |
| D-38 | Quota utilisation model | **Burn-rate MEASUREMENT only — reporting current rate and week-end projection; NO adaptive concurrency control.** An earlier draft specified an adaptive controller; rejected. | Measuring is nearly free (a script parsing transcripts); acting is not (a Manager turn to decide, ~484K tokens). Predictability outranks efficiency for a published tool — an adaptive controller underneath a schedule the user set produces "why are N Workers running when I said M?" General principle: automation earns its place when a decision is frequent or fast-moving; schedule tuning is neither. §2.6. |
| D-39 | Steady vs. bursty utilisation | **Hypothesis, measured by net-of-rework delivered work — not assumed** | A raw merged-ticket count rewards bursting even when the work is wrong (one component shipped three times in states that passed typecheck, full tests, and two review stages while non-functional). Whether the user's schedule (D-44) should favor steady or bursty running changes if a full week of real data says bursting wins — this decides nothing on its own, it makes the comparison possible. §2.6.3. |
| D-40 | `rite ticket` | **Not a CLI command — the Dispatch slash command `.claude/commands/ticket.md`** | §9's own charter scopes the CLI to the non-AI surface; working a ticket end-to-end needs judgement. This row was ambiguous across every SPEC version through v0.9 despite §9.1 never listing it as a CLI subcommand. The CLI-level pieces it composes — `rite claim`, `rite release`, `rite board`'s mechanical ticket CRUD, and workspace prep — are real subcommands; only the orchestration over them is the slash command. |
| D-41 | Claim release timing | **Held until merge, not until the PR opens** | §9.4.3 already specified this order; the shipped `/ticket` template released at PR-open instead, leaving claimed paths free for the entire review window. Corrected to match the specified order. §5.2. |
| D-42 | Leader-election clock assumption | **NTP-synced clocks required; 60s skew-tolerance margin on lease expiry** | Wall-clock lease comparison across machines with no tolerance lets a fast-clocked challenger promote against an incumbent whose own clock disagrees — a real, non-crash, non-partition route to temporary split-brain. Narrows the window; does not close it to zero. §2.4.1. |
| D-43 | Graceful-demotion unit of work | **"Operation" = one ticket-backend write or one board-state transition, not the whole ticket** | The term was used twice in the spec and defined nowhere; an implementer had no way to know the handover boundary. §2.4, "Graceful demotion" step 2. |
| D-44 | Worker concurrency control | **User-defined schedule (hours × Worker count per project), not automatic** | Coordination cost is real and may dominate throughput, and how much depends on the project — a decision that should be the user's, informed by data (D-45), not rite's to compute. §2.7. |
| D-45 | Coordination-cost visibility | **Instrument refused-claim-attempt COUNT (not wait time — claims are refused outright, never queued), merge conflicts per merge, and "nothing safe to start" frequency; surface in `rite status`** | Contention grows roughly with N² (pairs of Workers collide), and the knee varies by project — observed directly: 25 concurrent sessions jammed the board (four in a row found nothing safe to start), 3 ran fine. Gives the user their own project's knee from data instead of a guess. §2.7.2. |
| D-46 | Zero-Worker schedule window | **Runs the same `rite stop` handover, never a kill** | A schedule window strands work every evening exactly like an uncommitted working tree does (D-33) unless it goes through the existing clean-stop path. §2.7.3. |
| D-47 | Cross-project load | **The multi-project hub (§8.9) warns on aggregate concurrency across all registered projects, at configuration time** | Per-project schedules can each look sane and still sum to more than the weekly budget supports — only the hub sees the total. Warning at configuration time (not exhaustion) is the same forecast-early pattern as D-32/pool warnings. §2.7.4. |
| D-48 | Schedule timezone | **Optional (relaxed 0.5.1). Unset means this machine's clock; a zone that was SET and cannot be resolved is an error** | ⚠ RELAXED, not drifted. It required the field with no default, because a schedule with an assumed timezone is correct only where it was written and wrong everywhere else it is read. **That reasoning still holds and was measured**: one committed `09:00-17:00 Mon-Fri` at Friday 23:00 UTC gives 3 Workers in America/Los_Angeles (Fri 16:00) and 0 in Asia/Tokyo (Sat 08:00) — the day itself differs. Relaxed anyway because a schedule expresses the operator's working day, and a one-machine project should not have to name its own zone; the loudness the requirement bought is bought back by REPORTING which clock was resolved instead of refusing to run. **TWO STATES, DELIBERATELY DISTINCT:** *no zone given* is a supported configuration and reports `machine local`; *a zone given and not resolvable* is an ERROR — it names the rejected string and `rite doctor` reports it. Until 0.5.1 these were byte-identical, so a typo read exactly like an unconfigured project. ⚠ OPEN: `validate_schedule` still reports an unset timezone as "required (D-48)", which this decision makes false. What replaces it is undecided — the hazard is real only where a schedule is read by more than one machine, and neither tracked-ness (a solo developer commits everything; rite's own `.rite/config.yaml` is tracked) nor anything `validate_schedule` can see distinguishes that. §2.7. |
| D-49 | Ticket-link interface method | **`link(id, target_id, link_type)` added to the abstract `TicketBackend` interface (§6.1), not left as a JIRA-only capability; returns `void \| BackendError`, and a backend with no real link mechanism must return the error rather than substitute a weaker one silently** | §6.2 already specified "blocked by" linking as backend behaviour; found during the Phase-1 implementation-plan rewrite that the interface itself had no method to carry it. A `void`-only return was reviewed and rejected in the same pass — it would let a real link and a silent no-op look identical to a caller that reasons about dependencies. §6.1. |
| D-50 | `rite start`'s setup phase — what it performs vs. reports | **`start` performs anything idempotent, local and free; it REPORTS anything persistent, networked or quota-spending, naming the command that does it.** Three steps the spec had it perform are now reports: registering the scheduler (a standing cron/launchd entry), refreshing the KB cache (network fetches), and topping up the coordinator pool (spawns `claude`, spends quota). `start` gained the health check it genuinely owed — schedule validation, the config it is about to be governed by — without shelling out to `doctor`. | Measured: none of §9.10's four setup steps existed, and the spec had marked them "not built" without deciding whose defect it was. Mostly the spec's. A lifecycle command that installs cron entries and starts paid sessions as a side effect is the wrong default, and two of the three are irreversible in the direction that matters — spent quota is the one damage no cleanup reverses (§5.1.1). §9.10 also contradicted §8.7 outright on the KB cache; §8.7 wins. The outbox flush stays as the one deliberate exception, because §9.10's offline `stop` promises delivery "on next contact" and nothing else would ever deliver it. "Idempotent" is restated precisely: calling `start` twice does not do the work twice, rather than the old and false "is a no-op". §9.10, §2.5.1, §2.7.5, §2.7.4. |
| D-51 | Sandbox default | **Opt-out: `sandbox.enabled: true`, and the §9.3 questionnaire defaults the answer to Yes** | Was opt-in, on the reasoning that "an added dependency should be opted into, not defaulted on" — yoloAI is a separate binary from yoloai.dev, so defaulting on would fail first run for everyone without it. That reasoning stopped holding once two things existed: §9.3 asks at the one moment a human is certainly at a terminal (`install.sh` cannot — piping it to a shell binds stdin to the pipe), and `rite doctor` verifies by starting a real sandbox and tearing it down rather than by finding a file on PATH. A machine without yoloAI now gets a question it can answer and a row naming what is missing, not a first-run failure. On a platform with no backend rite has verified, §9.3 does not ask at all and says why in one line. **Not a precedent for migrating defaults into this register:** numeric defaults keep their rationale in `config/models.py` beside the value, which is where someone changing one will read it. |
| D-52 | Existing project specs | **Point at them, never inline them** | A spec is routinely thousands of lines — rite's own is ~3,800. Inlining one into every Worker's context on every job spends, on repetition, exactly the quota this tool exists to make last overnight. It is the same arithmetic that produced D-1: an MCP call costs an LLM turn, so the cheap-per-call-but-constant thing loses to the one-off. Workers get the path and the citation convention and read what the ticket needs. `.rite/context/` was considered and rejected as the mechanism: it copies (`shutil.copyfile`) and caps an entry at 4,096 bytes, so rite's own spec would be flagged `oversize` by `rite doctor` on every run, and a copy goes stale silently the moment the original is edited. §9.13. |
| D-53 | Greenfield spec | **`/spec` writes `SPEC.md` with a mandatory decision register; follow-ups go into the spec, not `brief.yaml`** | rite pointed at specs (D-52) and produced none, so a greenfield project had nothing for tickets to cite, and the orientation table's "no project spec" row had no code behind it. The register is required even for a one-row spec because it is what makes a spec addressable rather than prose. `brief.yaml`'s `enriched:` section is dropped: `ProjectBrief` never modelled it and only an agent read it, so it was a second, half-read home for answers the spec now holds. `rite start` reports the phase from disk, and the generated `CLAUDE.md` carries the table rather than pointing at one it does not contain. Not verified with a live session. §9.13.1. |
| D-54 | Large specs: point, or digest | **Both. `rite spec add` still only points (D-52); a spec too large to hold is additionally digested into addressable units, and a spec a slice cannot help is REFUSED before anything is written.** `rite spec index` prints the verdict and writes `.rite/spec/index.json` only when it passes. | D-52 says Workers read what the ticket needs, which is a budget, not a mechanism: on rite's own 4,000-line spec a Worker either reads the whole thing or guesses which sections matter. A slice is the unit, what it cites at depth 1, and the pinned hubs — measured 9.1% at p90 here, against 71.5% for following every reference transitively. The refusal is the other half and is not a failure mode: below the threshold the document is cheaper read whole, and a fresh `/spec` spec (71 lines, measured) is correctly refused. Measured caution, also in the verdict: rite's spec has had a citation gate for months and carries 2.30 citations per unit with 39% of its units citing nothing, while nine design documents from other projects measured 0.00-0.86 and 73-100% — and produced SMALLER slices (0.9-8.7% at p90) purely because their graphs are empty. Above 60% unlinked the ✓ says so. §9.13.2. |
| D-55 | How far a slice follows references | **Depth 1, plus the top 8 sections by in-degree pinned into every slice; 2 is the maximum the config accepts** | Transitive closure loads 71.5% of rite's spec at the median — the monolith the digest exists to avoid — so depth is an under-approximation on purpose and a test pins it against transitive, because "follow them all" is the obvious later improvement. Pinning is what makes depth 1 usable rather than merely small: without the top 8 hubs the median closure drops from 70.5% to 10.3%, which is the same Worker rebuilding the missing context by hand. A slice therefore misses real dependencies by design, and D-57 is how that becomes visible instead of invisible. §9.13.2. |
| D-56 | When a derived unit's hashes are recorded | **Only by an explicit `rite spec stamp`, never as a side effect of `rite spec index` or any other command** | The stamp is the whole basis for telling a current derived unit from a stale or hand-edited one. A stamp applied automatically would record the spec as it is now against text written from an older spec — blessing a source change nobody read and a hand edit nobody made — after which nothing could ever read as stale or tampered again. Found in review that `stamp --all` did exactly this; it now stamps only never-stamped files and refuses drifted ones by name. `rite spec verify` is the gate: 0 current, 1 drifted, 3 could not run, so a script cannot read "no spec registered" as "nothing has drifted". §9.13.2. |
| D-57 | Measuring whether slices are enough | **Mandatory instrumentation: every retrieval and every fallback is recorded, and no data is reported as no data, never as 0%** | A slice that was not enough is invisible — the Worker reads the whole spec and the digest looks like it worked — so without a count the depth in D-55 is a guess defended by argument. `rite spec slice` records the retrieval; `rite handover write --spec-fallback <unit>` records the fallback in the snapshot a session already writes, counted once however often that snapshot is rewritten. A feature nobody used and a feature that always worked are opposite readings, and only one of them justifies leaving the depth alone, so `NO_DATA` and `FALLBACKS_ONLY` are their own statuses and an unreadable log line is counted and reported rather than skipped. This rate is the evidence that would justify depth 2 or another pinned hub. §9.13.2. |

| D-58 | Unreadable input to a state merge | **Pass the bytes through verbatim; fail closed on the decision that needed them** | Read-merge-write (§2.4.2) must produce a whole tree, but another Manager's file may be unparseable. Dropping it destroys data to satisfy a merge; refusing to write makes one bad file a fleet-wide outage. Copying the bytes unchanged loses nothing and repairs nothing, while declining only the operations that depend on reading it — granting a possibly-overlapping claim, most obviously — keeps everything else available. Same shape as D-29's fail-closed cap: unknown is not nothing, and the answer to unknown is to decline the unsafe act rather than halt or guess. Reported loudly, naming the file and its Manager. §2.4.2. |
| D-59 | A lease that expires implausibly far ahead | **Not credible beyond `owner_lease_minutes + skew_tolerance`, and therefore challengeable** | §2.4.1's tolerance protects an incumbent from a fast challenger; the reverse case had no rule, and read literally a Manager whose clock is a day ahead holds the role permanently — a wedge needing no malice, only a wrong clock. Nothing honest can write an expiry beyond the longest permitted lease plus the most drift tolerated, so anything past that ceiling is invalid. Derived from two values already in `coordination:` rather than a third number to keep in step: raising the lease duration moves the ceiling with it. Logged distinctly, because it means somebody's clock is wrong. §2.4.1. |
| D-60 | The lease's `priority` field | **Written for audit, ignored on read; config order always wins** | `coordination.managers` is declared intent under version control; a lease is ephemeral runtime state. A stale lease written before someone reordered the list must not override that reorder, or a deliberate config change silently fails to take effect until a lease happens to expire. The field is kept because what the holder believed its priority was at acquisition is useful when reconstructing why a promotion went the way it did — but it never participates in the comparison. Both halves stated so the field is neither deleted as dead weight nor, worse, started being read. §2.4.1. |
| D-61 | Granularity of the state layer's compare-and-swap | **Per key, not whole-state; the version is an opaque fingerprint of the value** | The interface must be substitutable (D-20, D-21) or the git-versus-Redis question has no answer but "rewrite it". The first cut made the version whole-state because that is what `--force-with-lease` compares, on the stated ground that per-key CAS was not implementable on git — which was wrong: a git backend compares the key's own value, merges, pushes with the lease, and re-merges when the ref moved for an unrelated key, absorbing §2.4.2(b)'s ref-level race instead of exporting it. Better for git (that race can no longer mark a live Manager falsely stalled) and necessary for anything else (a key-value store would otherwise funnel every write through one global version). A version fingerprints the VALUE, so no backend needs a durable counter and an A→B→A rewrite is harmless: a decision made on content stays sound when the content is what was read. Proven rather than argued — `tests/test_state_layer_kv.py` binds a socket-served key-value store with no trees, refs or merges to the conformance suite unchanged, and it passes, including the process-burst concurrency tests. |
| D-62 | Whether `rite start <provider>` inherits D-50 | **No — it AMENDS D-50, and the amendment is recorded rather than implied** | A Manager session is not idempotent, local or free; it fails all three of D-50's tests where the loop failed only one, and §9.10 refuses to start even the free loop. What buys the amendment is §5.1.1 — a command's surprising effects should be things the user asked for BY NAME, and `rite start claude` names it. What it costs is paid here: at most one Manager session per project, a second invocation refused, and bare `rite start` unchanged because it is what a session runs to orient itself. §9.14.0. ⚠ **Narrowed, 2026-09-25 (recorded, not newly decided): the shipped refusal is per Manager NAME, not per project** (`managers/session.py`). D-72 and D-79 made several Managers per project legal, and §9.14.7b recorded that they void "one per project". The idempotence argument per name is owed (`docs/design/V070_RELEASE_PLAN.md`, MM6). |
| D-63 | When the provider adapter interface freezes | **When `local` binds UNCHANGED to an adapter conformance suite — and that suite is written WITH the `claude` adapter, against the contract, not deferred to the freeze** | §3.3's precedent is sharper than a draft of §9.14.2 read it: `tests/state_layer_conformance.py` was P2-1a, written with the FIRST backend against the contract, and the git backend had to bind to it unchanged. Deferring the suite to the freeze moment inverts the thing that made it work. No adapter suite, interface or code exists today, so as drafted this froze on an unwritten artifact. Writing it now is also where the §9.14.7b mismatch with the built `local` package would surface automatically. §9.14.2. |
| D-64 | Provider order after `claude` | **`local` second, `cursor` third — the most DIFFERENT provider second, not the easiest** | A local engine has no session concept, no session id to resume, no quota and possibly no interactive surface, so it violates every assumption the first adapter will bake in. Two hosted assistants would agree with each other and fail on the third. §9.14.8. |
| D-65 | Whether a Manager session may be detached with logged output | **No — attachability is an interface requirement, not a Claude implementation detail** | A session a human cannot talk to is a report about a session. Stated at the interface because an abstraction designed from the mechanics alone arrives at "start, capture, log", which satisfies everything else and fails this completely. tmux gives the first adapter all of it free, which is exactly why it constrains the second. §9.14.3. |
| D-66 | Where the session's stop condition comes from | **Derived from the loop's verdicts, never separately configured** | A configured stop condition is a second definition of "done" that can disagree with the loop's, with no way to say which is right. `idle` is a completion stop; `deadlocked` and `unknown` are fault stops, and the exits must differ or a jam reports as a finish. §9.14.5. |
| D-67 | Whether `claude --resume` breaches §9.12 | **The permissive argument FAILED review; §9.12 wins. Resumption only while the human's own foreground invocation is still live** | Two rounds killed three legs: the `rite pool fill` precedent was quote-mined (§9.12 ends "Both are explicit and **in the foreground**", and fill's sessions all start inside the process the human is watching); "bounded authorisation" does not distinguish, because a cron tick can be given a ceiling and also traces to one typed command (`rite scheduler install`); and "attachable" was already rejected by §9.12, which calls the scheduler log "a log nobody is watching live" and classifies the tick as unattended anyway. What survives authorises less: the lifecycle IS the human's process and dies with it. An unattended overnight Manager session is out of scope, not deferred. §9.14.6. |
| D-68 | Whether the budget ceiling may have a default | **No — refused rather than defaulted, and "not applicable" is a distinct answer from "unlimited"** | Silently choosing a number the user did not choose is how `rite pool fill --count 500` became possible, and spent quota is the one damage no cleanup reverses (§5.1.1). A provider with no metered cost declares the ceiling inapplicable; that is a property of the provider, where "unlimited" is a decision nobody made. §9.14.5. |
| D-69 | The unit of the mandatory ceiling | **A session-start COUNT plus a wall-clock window — never a spend figure** | §2.6.1: no file under `~/.claude/` exposes the quota and `/usage` is reachable only inside an interactive session; `budget` is machine-wide and states per-project attribution is unavailable; D-38 forbids the path from measurement back to control. A ceiling checked against spend is a check that cannot run, and §9.11 forbids reporting that as a pass. §9.14.5. |
| D-70 | What the lifecycle does on the `closed` verdict | **Stops** | The schedule authorising zero Workers is the user saying "not now". The loop sleeps through it by design (§2.7.3), so a lifecycle that merely followed the loop would keep a Manager session spending through the hours the user told rite to be idle. Missing from the first draft, found independently by both review rounds. §9.14.5. |
| D-71 | What `rite start <name>` names | **A MANAGER — settled 2026-09-20** | Not a provider, not a directory, not a registry alias. §8.9's `rite start <alias>` and §9.10's `rite start <dir>` still exist, so the resolution order must be stated and a Manager name colliding with a registered alias is refused AT REGISTRATION rather than resolved by precedence — a precedence rule is a silent winner. §9.14.9. |
| D-72 | Whether a provider is a command argument or a Manager attribute | **A Manager ATTRIBUTE — settled 2026-09-20, in favour of what `local/` already built** | `engine` is a field on a Manager and a project may run several on different engines at once, so §9.14's original model — one argument selecting the project's single Manager — is withdrawn. It was written without knowledge of the built design; §9.14.7b recorded that as the largest unresolved finding and this resolves it. §9.14.9. |
| D-73 | Whether the session or the resumer dies with the terminal | **The SESSION may outlive it; the RESUMER may not** | Review found the two requirements denying each other: §9.14.3 needs a session that survives detaching, §9.14.6 said nothing outlives the terminal, and tmux — rite's only persistence — is detached by construction. They separate: a session the human started continuing is what §9.12 already permits (`rite sandbox start` leaves one running); what §9.12 forbids is an unattended START, so it is the resumer that must die. §9.14.6. |
| D-74 | How the one-Manager refusal establishes liveness | **Fail CLOSED, against the INNER PROCESS, with the remedy printed** | The mechanism nearest to hand is tmux `has-session`, which answers "does a session exist" rather than "is the command running" — the facade fixed twice in one night in `loop.start` and `pool.fill` — and it returns false when tmux is missing or times out, so an unanswerable check would permit two PAID sessions. §5.1.1: a safety property may fail closed, never open. The remedy must be printed because this design's ordinary exit is an ungraceful terminal close, so a stale marker is the common morning state. Raised by a peer session at the stage where it is still free to fix. §9.14.0. |
| D-75 | What rite may refuse a user | **Three cases, not two: an ACCIDENT rite makes impossible; a choice CONTRADICTING an earlier choice of the user's own, where rite honours the earlier one and refuses; and a free choice, whose cost rite makes visible and never refuses** | The two-case form was drafted first and review falsified it on rite's own behaviour: `rite pool fill --count 500` is typed explicitly, so the dichotomy calls it a choice and says never refuse — and rite refuses. The third case is what it was hiding, and it is not paternalism: refusing `--count 500` honours `sandbox.max_concurrent_workers`, a number the user wrote down, and clamping would be the paternalistic option because it substitutes rite's number while appearing to comply. §5.0. |
| D-76 | Whether a Manager is sandboxed | ⚠ **Superseded in 0.6.0: YES, inside a seatbelt profile, with Workers requested through a broker (B9, `4ebbbd7`).** Robert reversed it because the allowlist cannot hold for Goose. The nesting reason below was measured true and answered by the broker, not by dropping the sandbox. Containment from what a Manager is PERMITTED to do still applies in full. §5.4. Original decision: **No — containment comes from what it is PERMITTED to do, not from where it runs** | Three independent reasons: it needs broad project access by its nature, it must be attachable by a human (§9.14.3), and macOS may refuse a sandbox inside a sandbox — which would leave a sandboxed Manager structurally unable to start sandboxed Workers, the one thing it exists to do. yoloAI is the WORKER RUNTIME and sits on a different axis from `claude`/`cursor`/`local`, which are Manager engines; conflating them produces the reasonable-sounding and wrong conclusion that a Manager should be sandboxed like a Worker. §5.4. |
| D-77 | What blocks the Manager boundary property | **UNBLOCKED 2026-09-20: identity is the name you started with** | A Manager started as `rite start planner` knows it is `planner`, so the identity `.rite/machine` could not supply arrives as the argument — which is why D-71 and D-77 are one decision. Each Manager gets its own subdirectory in the single project root, so §5.4's boundary becomes statable as `<root>/.rite/managers/<name>/`, and §5.4.6's shared-by-accident list moves under it. Profiles stay committed; instances live in `.rite/user/`, uncommitted, because a shared config must not claim a Manager is running on somebody else's laptop. §9.14.9. |
| D-78 | `rite start` with no name, by Manager count | **0 fails, 1 works bare, 2+ refuses AND LISTS them** | Starting a default where none is configured invents a configuration the user did not write; requiring a name where there is one Manager is ceremony; picking among several is a guess about which engine spends which quota. The 2+ case must print the names — a refusal that says "several are configured" and stops sends the user to `rite doctor` to learn what they could have typed. §9.14.9. |
| D-79 | One root with per-Manager subdirectories, or separate roots per Manager | **SUBDIRECTORIES UNDER ONE ROOT — settled 2026-09-20, with the counter-argument in front of the decider** | `docs/design/V070_MULTI_MANAGER.md` recorded separate roots and is now marked superseded rather than rewritten, because its cost — "a second protocol... the two would drift" — is real and is now accepted debt rather than an avoided one. What reversed it is a fact about the code: separate roots mean separate claim ledgers, and `claims_channel()` returns nothing unless BOTH `coordination.managers` and `coordination.remote` are set, so two Managers on one machine would silently not see each other's claims — the failure this project hit twice in one day, made the default. §9.14.9. |
| D-80 | Bare `rite start`: orient, or start the one Manager? | **BOTH, distinguished by whether one is already RUNNING rather than by the argument typed** | §9.14.0 said a bare start must never begin a session, because start is what a session runs to orient itself; D-78 said one Manager works bare. Direct contradiction. Resolved on the observable fact: no Manager running means start it, a Manager running means report it and exit 0. That answers §9.14.0's recursion fear with a mechanism rather than a convention — the orienting session finds the Manager it is running inside — and makes §9.14.0's idempotence rule load-bearing rather than incidental. §9.14.0. |
| D-81 | Does a terminating review make a pre-merge behaviour gate redundant? | **NO — they answer different questions, and v0.5.0 is the evidence** | A terminating check on v0.5.0's source found its defects and the release still shipped without the features it was scoped for, because reviewing code asks "is this correct" and nobody was assigned to ask "is this what was asked for". Going through that release's findings one at a time, a requirements-derived scenario would have caught most of the behavioural defects — including every instance of the implemented-tested-called-by-nothing class, which a scenario surfaces as missing behaviour rather than as missing coverage — and none of the security-invisible, documentation or infrastructure ones. Adopted with the bound stated, because a gate sold as catching everything gets trusted where it should not be. Each condition carries a checkable artifact rather than an attestation — independence is enforced by ORDER (scenarios committed before the implementation branch, checkable with `git merge-base`) because authorship is unverifiable after the fact; and the single-operator case degrades visibly with `independent: false` rather than being silently skipped, since one operator is this tool's common case and not an exception. §7.3. |
| D-82 | Should `--minutes` have a default, as `--sessions` has none? | **NO DEFAULT, for the same reason and with a measurement behind it** | The two bounds catch different runaways and neither suffices alone. Measured on the real supervisor: sessions that end instantly reached the COUNT (1000) with a one-second clock untouched, and sessions of realistic length reached the CLOCK after three with a ceiling of 1000 untouched. So a run bounded only by a count is unbounded in time, and one bounded only by time is unbounded in spend. A duration defaulting to forever is a bound in name only — and this is the command that spends quota unattended, which is the argument that made `--sessions` mandatory (D-69) applied unchanged. §9.14.5. |
| D-83 | Does the Manager's process journal ship on by default? | **NO — opt-in, beta, and the flip is conditional on QUALITY rather than on a release date** | Writing observations and retrospectives means a Manager spending tokens reflecting instead of working, and nothing consumes the output yet; self-reflection, which would make it pay for itself, is expected around 0.8.0. It stays opt-in through 0.6.0 and 0.7.0 while what it writes is refined. The flip is earned when the entries are good enough that a human reading them learns something they did not know — not when self-reflection arrives, because "self-reflection shipped, so turn it on" is a calendar decision and that is how a beta becomes a default nobody validated. Recorded as a stated plan rather than left implicit, because for two releases this will look exactly like dead weight to anyone deciding whether to keep investing in it. §9.15.1. |
| D-84 | How a Manager judges whether a review or a test round was worth it | **It records cost, what changed, and whether the change would have been caught elsewhere — and draws NO verdict** | The obvious metric is inverted and this project has already measured the shape: a round ending "fix these three things" produces a commit, a round ending "this design would force-release live Workers, start again" produces nothing, so commit-based review value is biased toward cheap reviews BY CONSTRUCTION and a Manager judging by output would rank bad reviewing above good. D-39 records the same inversion for delivery (a raw merged-ticket count rewards bursting even when the work is wrong); D-67 and D-70 are the instances — review rounds that produced no commits, killed three legs of an argument and caught a stop condition whose absence would have spent money all night. "Round 2 cost 150k and changed nothing" is checkable; "round 2 was a waste" is a conclusion the Manager is not positioned to draw. §9.15.4. |
| D-85 | What stops a Manager: Ctrl+C, a bound, or `rite stop` | **Ctrl+C stops BOTH supervisor and session; a bound stops supervision and leaves the session alive; `rite stop <manager>` is the orphan-recovery path only** | A bound is an accounting limit and the user may be mid-conversation, so the pane surviving is right and was verified deliberately. Ctrl+C is a human saying stop, and leaving a live session spending quota with only the restarts halted is not what they asked for — nor should it take two commands. The two paths currently share their teardown, so separating them is the work. Ctrl+C must also call `forget_instance` (zero callers today) or a stopped Manager leaves a record that makes the next `rite start` believe it is running, which is the stale-lock defect in a new place. §9.14.12. |
| D-86 | How the journal is protected against invented events | **By the FORMAT — a required verifiable anchor and a syntactic observed/inferred split — never by instructing the Manager to be careful** | An issue log containing events that did not happen is worse than no log, and it would poison self-reflection later, which is the one thing eventually meant to read it. An entry with no anchor (commit SHA, file and line, command with output, ticket id, log timestamp) is not written; anchors are checked where checking is cheap; entries are written at the moment rather than reconstructed, because compaction is the specific enemy (this week: a session quoting SHAs stale after a history rewrite, and another reporting reviewers as running that were never launched — memory, not malice); and no entry may claim another agent's internal state, which is the form a hallucination naturally takes. Exhortation is explicitly rejected: this week has two instances where an instruction to check carefully immediately preceded the error it warned against. The same principle underlies the project's paste-the-invocation rule. (A draft cited that rule and a scenario-citation rule by id; neither reference existed in this repository, so the ids are an open question rather than a citation — the rule broken inside the section that states it.) §9.15.3. |
| D-87 | Is the journal's anchor requirement enforced, or asked for? | **ENFORCED on the writing path — the writer refuses an unanchored entry and creates no file** | A requirement on the format is honoured by the Manager choosing to honour it, which is exhortation wearing a table — and §9.15.3 names two instances this week where an explicit instruction to check carefully immediately preceded the error it warned against. Both halves are required and fail for different reasons: a refusal that still writes means the check runs after the write, and a silent no-write means the caller cannot tell refusal from success. It costs one branch, not a subsystem, because the writer already has to open and name a file. Settled in review (rite-dd) against the section's own closing argument. §9.15.3. |
| D-88 | Does the process journal stay in v0.5.1? | **YES — it is the instrument for the next unattended run, not a nice-to-have** | It was the release's scope lever, being the one item not required for "full-featured single Manager". Kept because the next large unattended run is a Bentora dogfood run by somebody who is not the project owner, with the owner not watching — so this diagnostic is the only channel by which anything comes back from it. That reason changes the build: the flag must be DISCOVERABLE by a person who has not read this spec (named in `rite start --help`, with the docs saying an unattended run is when to enable it), and the entries must be able to leave the machine that wrote them (§9.15.3a) — a gap that was a 0.6.0 design question until the answer to "who runs it" made it a 0.5.1 defect. §9.15.1. |
| D-89 | The diagnostic flag's name | **`--record-issues`** | Named for what it DOES rather than what it IS. `--diagnostics` describes the category; `--record-issues` tells a user reading `--help` what will appear on disk, which is what they are actually deciding about. §9.15.1. |
| D-90 | Does `rite start <manager>` prompt the session? | **YES, on EVERY session — the opening prompt on the first, a CONTINUATION instruction on a resume.** ⚠ Amended at 0.5.1: the original decision said "not on a resume", and **the premise changed rather than the reasoning being wrong.** While the engine was an interactive REPL a resumed cycle was the same conversation continuing, so there was nothing to say. Since the engine is launched with `-p` (Finding B, option 1) each cycle is a separate invocation that EXITS, and one launched with no input does not continue — it exits 1: "Input must be provided either through stdin or as a prompt argument when using --print", measured against real `claude`. A resumed cycle with no prompt is therefore a crash, not a continuation. What the original decision was protecting still holds and is still enforced: the OPENING prompt is never re-issued, because telling a session to do work it has already done is how it gets done twice. | A Manager that starts with an empty prompt waits for a human to type, which is the behaviour the command exists to remove. The resume half was not covered by the decision and is inferred from the resume design: a resumed session already carries the context the prompt would establish, and re-issuing an instruction mid-task is the same class of error as restarting a session a human deliberately quit. The asymmetry that decided the original still decides the amendment, in the same direction: a missing prompt on resume now costs a cycle that cannot start at all, and a spurious OPENING prompt costs a session that starts over — so every cycle gets input, and a resumed one gets input that says carry on. The prompt reaches the engine on stdin from the environment, never as an argument (§9.14.7's rule for the token, same reason: `tmux new-session <cmd>` puts its command on tmux's argv). §9.14.11a. |
| D-91 | Does rite provide a way to get journal entries off the machine? | **NO — the path is printed and retrieval is the operator's business** | An earlier revision called this a defect and escalated it: a diagnostic whose output never leaves the host returns nothing to the reader it exists for. True, and the escalation was still wrong — the journal's reader in the run this was written for is a person the operator will speak to directly, who will be sent a zip. A mechanism was being designed for a problem that two people who talk to each other do not have. What is kept is the start line printing the absolute path, because somebody has to know where to copy from; the `git add -f` note is demoted from THE route to one way of doing it. No export command, no archive step, no sync — not deferred, not wanted. This also deflates the same revision's alarm about a torn-down sandbox: the entries survive as long as the directory does and are copied before anything is torn down. **It stops being adequate the moment the reader is not a person in the same conversation** — a team, a CI pipeline, or §9.15.4's future gate — which is the condition to watch rather than a reason to build now. §9.15.3a. |
| D-92 | Who writes a journal entry — the Manager, or rite? | **RITE, through `rite journal observe` / `rite journal retrospective`** | D-87 required the anchor refusal to happen on the writing path and never said who writes. If a Manager composes markdown with its own file tools, rite is nowhere near that path and the requirement degrades into asking the agent nicely — which §9.15.3's own closing line says beats nothing. A refusal requirement implies a refuser. Found by rite-dd while implementing D-87, which is a decision I argued for and adopted while it had no mechanism under it. The dead-wiring guard caught the same thing from the other side: `write_observation` and `write_retrospective` read as uncalled until the commands existed. §9.15.3. |
| D-93 | Where the journal instructions reach the Manager | **The START PROMPT (§9.14.11a), not the generated `CLAUDE.md`** | `CLAUDE.md` is project-level — written by `rite init`, refreshed by `rite update`, never touched by `rite start` — while `--record-issues` is per-START, so two Managers in one project started differently would need two versions of one shared file that neither start writes. Unimplementable as specified; found by rite-dd while implementing. The prompt is per-session by construction, so "genuinely off when off" becomes exact rather than aspirational: a Manager without the flag does not receive the instructions because they were never composed, not because a shared file was filtered. A stronger guarantee than the `CLAUDE.md` route could have given, and it makes §9.14.11a's prompt load-bearing rather than a convenience — it is the only per-session channel to the Manager that rite owns. §9.15.6. |
| D-94 | Is an unaddressed thread comment an instruction? | **NO — it reaches the Manager as CONTEXT, never as instruction** | A reply under a status update without `@rite` is people discussing the standup, which is worth knowing and is not a request. Only a mention or a reply to something rite asked is addressed. The distinction is written into every relayed message's header, not left to the model noticing that a mention was absent. §9.16.3. |
| D-95 | Where may the Manager be instructed from? | **The Owner's DM (and the local machine). A configurable channel is BROADCAST, default `#all-rite`** | Authority is a property of the channel. The DM is one-to-one by construction, so "only the Owner can instruct" is not a check rite has to get right per message. Messages in the broadcast channel never carry authority, whoever types them. It adds IM scopes to what A3a measured. §9.16.2. |
| D-96 | What does `@rite` mean? | **"This is addressed to me" — a filter, not "do this", and NOT an access control** | A mention is still judged. Anyone in a workspace can type it, so it can never authorise. Authority (D-95) and addressing (D-96) are separate questions, and the docs must not blur them. §9.16.4. |
| D-97 | Should rite scan ticket text for injection phrases? | **YES — report at the next check-in, NEVER block. Reverses the earlier advice against scanning** | The earlier advice rested on 9 of 18 ordinary tickets quarantined — in BLOCKING mode. Reporting makes a false positive cost a standup line, and the measured 6-of-9 catch rate on model-directed attacks becomes free signal. Named for what it does ("a phrase commonly used in prompt injection"), never "sanitized". ⚠ It catches none of 8 agent-directed attacks, and ticket text is not vetted. §6.6. **Planned 0.6.0, sequenced last and droppable (plan § N); not built.** |
| D-98 | What input normalisation does ticket text get? | **Invisible characters removed, hidden tag characters decoded and shown, HTML comments stripped or surfaced** | Correctness, not security: the agent must see what a human reviewer sees. §6.6.1. **Planned 0.6.0, sequenced last and droppable (plan § N); not built.** |
| D-99 | Where may an agent talk, and where is that enforced? | **Only to destinations the operator sanctioned, inbound and outbound, enforced at the NETWORK layer — never by which program runs** | A permitted list is closed by construction, and a list of threats loses to the one nobody listed. It is the control that makes §6.6.3's 8-of-8 survivable: it does not care what the agent believes. The tool layer cannot carry it, because `git` and `gh` are permitted and reach the network, and `python -c` or a hook can make any request. Measured constraint: for IP, a seatbelt profile can confine to loopback and name no host, so a Manager's host list is enforced outside its boundary. Local sockets it can refuse by path. §5.5. |
| D-100 | What content is scanned on the way out? | **Only payloads to ALLOWED destinations that PUBLISH, with the structural credential rule. Model calls are NEVER scanned** | The destination is the primary control, and scanning covers the one case it passes: a token in an issue body on the operator's own repository. A scanner on the model path alarms on every request, or is tuned to ignore it and watches nothing while appearing to watch. Which destinations count as publishing is open. §5.5.4. |

---

## 14. Revision history

Kept at the end deliberately. It is a record of what this document got wrong
and when, which is useful for judging how much to trust a section — and useless
as an introduction to the tool.

**Changes in 0.24.2 — 0.24.0 was measured against a tree that had already moved, and three of its claims were wrong by the time it landed.** Re-measured at `9862b59`, which closed the tmux and signal escapes: §5.4.8 now says **P2 (process separation between Managers) holds**, measured between two Managers' profiles, and that the sandbox separates processes but not files (one Manager wrote into the other's directory). §5.4's banner no longer reports the two closed holes as open. It names the one cross-project read that is still open: `~/.claude` is readable whole, other projects' transcripts included. §5.5.2 and D-99 no longer say a seatbelt profile can express "nothing finer" than loopback. That is true for IP destinations, and false for local sockets, which can be refused by path, and that is how the tmux socket is closed. **0.24.0's history entry below is left as written**, including its "none of which holds today", because it was the claim made at the time.

**Changes in 0.24.1 — §6.6 reads true whether or not its tickets ship.** Robert placed § N (text cleanup and phrase reporting) in 0.6.0, last, so it can be dropped if quota runs out. §6.6 now opens with what rite does today, which is nothing to ticket text, stated separately from what § N would add, and marks §6.6.1 and §6.6.2 as not built. A release without N therefore does not describe behaviour rite lacks. §9.16.5's line on inbound Slack text is made conditional the same way, and the inference label on it stays, now marked accepted. D-97 and D-98 carry the placement.

**Changes in 0.24.0 — v0.7.0 specs, and a section the code had already overtaken.** §5.5 is new: egress control, decided and not built. The property is stated positively (only sanctioned destinations, both directions), enforced at the network layer (D-99), with content scanning confined to allowed destinations that publish and model calls never scanned (D-100). It carries a measured constraint the design note did not have: a seatbelt profile can confine a process to loopback and nothing finer, so a Manager's list must be enforced outside its boundary. §5.4.8 is new: the decided separation between Managers that share a root, written as four properties, **none of which holds today**. It says so, and says that the Manager's sandbox is not what enforces the first.

⚠ **§5.4 was stale against `main`, and so were D-76 and §5.4.5.** B9 (`4ebbbd7`) put the Manager inside a seatbelt profile with a broker for Workers. D-76 ("a Manager is not sandboxed") was never marked, and §5.4.5 still said there was no per-Manager directory and no `RITE_MANAGER`. Both now say what the code does. The outbox is struck from §5.4.6's shared list, because it moved per-Manager, and D-62's "one Manager session per project" is narrowed to the per-name refusal that shipped. The measurements behind these corrections, including two ways out of the Manager's boundary found on 2026-09-25, are in `docs/design/V070_RELEASE_PLAN.md`, part 0.

**Changes in 0.23.0 — who may instruct a Manager, and what ticket text is (and is not).** §9.16 is new: authority comes from the channel (the Owner's DM, or the local machine; a configurable broadcast channel, default `#all-rite`, never carries authority), addressing comes from `@rite` or a reply, and only a message that is both counts as an instruction (D-94–D-96). `@rite` is recorded as a filter and explicitly NOT an access control. §6.6 is new: ticket text is normalised (D-98), and injection phrases are reported at the check-in and never blocked (D-97). That reverses earlier advice, which rested on a 9-of-18 false-positive rate measured in blocking mode. §6.6.3 states plainly that 8 of 8 agent-directed attacks pass any text filter and that ticket text is not vetted. Destination control is a separate v0.7.0 design note, `docs/design/V070_EGRESS.md`.

**Changes in 0.22.1 — the QA gate is 0.7.0, not 0.6.0.** §9.15.4 pointed at the scenario gate (D-81, §7.3) as the 0.6.0 destination the journal was being refined toward, and §7.3 said only "Not built". Robert moved the gate to 0.7.0 (v0.6.0 release plan, Decision 5), so a reader of this document was being promised a 0.6.0 deliverable that will not arrive — the class of stale claim 0.5.1 was spent removing. Both sections now say 0.7.0, and the 0.20.1 note below that named "the 0.6.0 gate" says where it went rather than being rewritten. Nothing about the gate's design changed.

**Changes in 0.22.0 — two things §9.15 required that could not be built.** Both found by rite-dd while implementing, and both reported rather than worked around. D-87's anchor refusal named no refuser, so it was a rule the Manager was asked to follow rather than one the tool enforced; rite now owns the writing path (D-92). And §9.15.6 put the journal instructions in the generated `CLAUDE.md`, which is project-level while the flag is per-start — unimplementable, and its consequence while open was a Manager told where the journal is and never told to write to it, which is the class the subsection exists to prevent. They move to the start prompt (D-93), which is per-session by construction and a stronger guarantee than the file could have given. §9.15.3's fifth measure — verifying that a present anchor RESOLVES — is marked unimplemented rather than left to read as done.

**Changes in 0.21.1 — an escalation withdrawn.** §9.15.3a said the journal's entries "must be able to leave the machine that wrote them" and treated their being gitignored as a defect in this release. Withdrawn (D-91): rite builds no retrieval, the start line prints the path, and how the entries reach their reader is the operator's business — in the run this was written for, a zip file between two people who talk to each other. The observation was sound and the escalation was not, and the section keeps both rather than reading as though it had always said this. The condition that would make it wrong is stated instead: a reader who is not a person in the same conversation.

**Changes in 0.21.0 — the open questions answered, and one answer that created a defect.** The process journal stays in v0.5.1 (D-88), its flag is `--record-issues` (D-89), and `rite start <manager>` prompts the session (D-90, with the resume half recorded as a stated default rather than as a decision). §9.14.11a is new for the prompting, which had been decided and had never reached this document.

⚠ **The reason the journal was kept turns one of its properties into a defect.** The next unattended run is on somebody else's machine with the owner not watching, so the journal being gitignored and machine-local means its only reader cannot reach it — §9.15.3a, which was a 0.6.0 design question until the answer to "who runs it" arrived. Closed with documentation rather than machinery, and an export command is recorded as the first thing to build if the dogfood shows the documented route is not taken.

**Changes in 0.20.1 — §9.15 after review.** The anchor requirement is enforced on the writing path rather than asked for (D-87); "a log timestamp" is no longer a permitted anchor on its own, because a bare timestamp is indistinguishable from an invented one and every permitted anchor must be checkable or the requirement leaks; and a claim that a journal entry could be re-included into git was FALSE and is corrected with the measurement — `.rite/*` excludes `.rite/managers` as a directory and git does not descend into an excluded directory, so a `!` negation three levels down has no effect. One gap is recorded rather than closed: the 0.6.0 gate (moved to 0.7.0 in 0.22.1) that is meant to consume these entries cannot reach them, because they are machine-local and uncommitted. All four found by rite-dd in review.

**Changes in 0.20.0 — the Manager's process journal (§9.15), stop semantics (§9.14.12–13), and a subsection that contradicted the code.**

§9.15 specifies a diagnostic mode in which a Manager records process issues as files: observations when something looks wrong, retrospectives at a boundary, because *"a bug escaped testing"* is only visible later. Opt-in and beta (D-83), inert by design, and protected against invented events by the entry FORMAT rather than by instructing the Manager to be careful (D-86) — this week has two instances where an instruction to check carefully immediately preceded the error it warned against.

§9.14.12 separates three stop outcomes that currently share one path: a bound reached leaves the session alive, Ctrl+C stops both, and the orphan case is what `rite stop <manager>` is for (D-85).

⚠ **§9.14.7a was STALE AGAINST THE SHIPPED CODE and is corrected.** It said a bare positional "is not available" for `rite start`; what shipped uses one. The contradiction stood in this document while the code disagreed with it — the exact failure that subsection exists to prevent. What resolved it is that the positional became a Manager name rather than a provider, with the resolution order fixed and the cross-machine collision handled where somebody can act on it. `rite stop <manager>` inherits the same collision and is recorded as UNRESOLVED rather than quietly assumed (§9.14.13).

**Changes in 0.19.1 — the second bound on `rite start`, and a race the exit status lost.** `--minutes` joins `--sessions` as a mandatory bound (D-82): the duration was already threaded through three modules and passed to the supervisor, by a call site that always passed zero because no flag set it. Written, tested, reached by nothing — the class the dead-wiring guard cannot see, because it asks whether a FUNCTION is called and never whether a PARAMETER is ever supplied.

Separately, §9.14.10's exit-status reading was wrong about tmux and had been wrong in four different ways. `#{pane_dead}` and `#{pane_dead_status}` do not arrive together — a pane is marked dead when its descriptor closes and the status is filled in when the child is reaped, and nothing orders those two. Reading once turned that window into a permanent `unclear`, so CLEAN exits failed to resume on Linux while crashes passed, and the capability probe reported the feature present because it polled where production did not. The probe now answers through the production function itself, so there is nothing left for it to disagree with.

**Changes in 0.19.0 — §9.14, `rite start <provider>` and its adapters.** A
new section specifying the session lifecycle: one command that starts the
loop and a Manager session, a mandatory ceiling, and stop conditions derived
from the loop's verdicts rather than configured separately.

**Two review rounds changed what this section says, not merely how it says
it, and the changes are the reason to trust it.** The first draft argued that
`claude --resume` does not breach §9.12. **That argument failed and is
recorded as failed** (§9.14.6): it quote-mined §9.12, whose sentence ends
"Both are explicit and **in the foreground**"; its "bounded authorisation"
test would equally permit a bounded cron dispatcher, which §9.12 forbids; and
its appeal to attachability was already rejected by §9.12, which calls the
scheduler log "a log nobody is watching live" and classifies the tick as
unattended regardless. What replaced it authorises materially less — the
lifecycle IS the human's foreground process and dies with it, and an
unattended overnight Manager session is **out of scope rather than
deferred**.

The draft also claimed to leave §9.10 "unchanged" while doing two things
§9.10 refuses. **It amends D-50 and now says so** (§9.14.0), paying for the
amendment with a one-Manager-per-project rule and a refusal on the second
invocation, because D-50's idempotence test still applies.

Three further corrections, each from a measurement rather than an opinion:
the loop has **seven** verdicts and the draft listed six — the missing
`closed` is the one that would have let a Manager session spend through a
window the user configured to authorise zero Workers; the ceiling is a
session COUNT, not spend, because §2.6.1 says rite cannot read the quota and
D-38 forbids the path from measurement to control; and the claim that
everything above the adapter is "provider-independent by construction" is
false in three places, the budget being a reader of Claude's own transcripts.

Two things are recorded as unsettled rather than decided: **`rite start
<provider>` as a bare positional is not available**, because §8.9 and §9.10
already define two meanings for it (§9.14.7a); and **the compliance
requirement that rite never reads or persists the provider token is
contradicted by rite's own credential design**, which prompts for the Claude
token, keychains it, and reads it back — a contradiction §10.2 makes
structural rather than incidental.

Round 2 changed it again, and the largest finding is unresolved by design
rather than by neglect. **`src/rite_ai/local/` is already built and does not
fit this section's model**: `engine` is a field on `ManagerRole`, an ordinary
project runs three Managers concurrently on different engines, and the built
unit is a synchronous subtask call rather than a session. That voids
§9.14.0's one-Manager-per-project rule — the concession paying for the D-50
amendment — and it is recorded as **D-72, blocking the implementation plan**,
because whether a provider is a command argument or a Manager attribute is
the owner's decision.

Round 2 also caught a contradiction round 1's fix introduced: §9.14.3 needs a
session surviving detachment and the narrowed §9.14.6 said nothing outlives
the terminal. **The session may; the resumer may not** (D-73). And it refuted
this section's own claim that per-project keychain delivery is the only
channel to a sandboxed Worker — rite already uses inheritance for exactly one
token — which turns "structurally impossible" into a trade worth one sentence
of §10, with the deciding measurement still untaken.

One gap is recorded rather than closed: **the loop is not specified in this**Changes in 0.18.2 — §5.3 measured against the installed sandbox.** Three
statements about worker sandboxing were checked against the installed yoloAI 0.11.0
— its binary, its base image, and its own `help security`, which is documentation
and is labelled as such where it is used — and all three needed correcting in the
same direction: the spec described a containment weaker than it read. A fourth
problem, `flock` not excluding inside a docker sandbox, came from a dogfood session
and is recorded in §5.3.5 with its provenance.

§5.3 said permissiveness is "the operator's choice, made outside rite" — true of an
unsandboxed Worker, and not of a sandboxed one, because yoloAI launches the agent as
`claude --dangerously-skip-permissions`. **This is the second correction to §5.3's
wording and it runs opposite to the first** — 0.16.0 pulled back a claim that bypass
mode was a property of the product; this one restores a weaker version of it for the
sandboxed case. The honest line is that the bypass is *contained*, not absent. §5.3.5 recommended "Docker (or Podman/Tart)" for network
isolation — Tart has none at all and Podman's allowlist can be flushed by the agent
it is meant to contain, so only Docker was ever right — and `SandboxConfig` has no
network field, so rite cannot ask for it on any backend anyway. §5.3.5 also now carries the docker/`flock` collision: the one backend whose
network allowlist can contain a hostile agent is the one where rite's claims stop
excluding, and nothing told a user that. And D-31's "never written to a file" is
guaranteed only at rite's boundary: what the sandbox does with the value afterwards is outside it, and
a report that 0.11.0 persists it is recorded there unverified rather than restated
as measured, because this document could not reproduce it.

The pattern is worth naming because it is the same one §11.5.1 records: each was a
property the spec asserted and nobody had run. None of the three is a change of
intent — the sandbox is still the right mechanism and still worth having — but a
security section that overstates its boundary is worse than one that admits a gap,
because the gap is what a reader plans around. §5.3.5 now also records what flipping
`sandbox.enabled` to true by default would require, since the question was raised:
fix those two defects first, flip second, reword third.

**Changes in 0.18.1 — editorial.** The revision history moved from between the
title and §1 to the end of the document. It had grown to 107 lines, which meant
a stranger opening this file read four versions of internal correction notes
before reaching a sentence saying what rite is. No content changed.

**Changes in 0.18.0 — the evidence base audited.** Every measured claim in this
document was checked against what could actually be traced. Most reproduced
independently — the transcript corpus, the ~98% cache-read share, the 500-session
pool fill, the 25-session jam, the `core.hooksPath` bypass. Three needed correcting,
and the corrections make the arguments stronger, not weaker:

§11.1 named the wrong repository. The near-miss was an early build of **this** repo,
not the upstream, and the section is rebuilt around what actually went wrong. It had
claimed three review stages missed the string; they did not miss anything. A
deny-list has to contain what it denies, so in a repo that was never going to be
published every occurrence was correct and every reader was right not to flag it. The
failure was the boundary crossing — one copy action, and a boundary has no reviewer —
which is a better argument for a gate than a review failure would have been, because
it does not require anyone to have been inattentive. §11.2 carried the same
pre-correction story and is corrected with it. The section states the blast radius
(local only, no remote existed, caught and rebuilt before one did) and says plainly
that the protected string was a personal legal matter, not client or project data.

§8.6's `CLAUDE.md` measurement was stale: 42 KB was the figure at the upstream's first
commit, and it had been 74 KB for four days before this spec cited it. The real file
was 76% larger than the figure quoted — the old number understated the argument it was
making by 43%.

§2.4.1's "three sessions hung in one day" could not be substantiated — every trace of
it leads back to this document and no further. It is now replaced by a figure that can
be checked: the upstream's coordination ledger records 46 force-releases, 44 of them
because the holder was gone while its claim record still read as live. That is the
same phenomenon, counted from a file rather than recalled.

Worth recording how close that went wrong a second time. The replacement figures first
offered — a peak of 29 concurrent sessions, 66 distinct sessions, 307 claims — did not
survive recomputation: they mixed whole-ledger totals with a single day's, and the
concurrency peak was 14, not 29. Had they been taken on trust, this version would have
put five unverifiable numbers into the section that argues for verifiable claims. The
figures published above were computed from the ledger directly, and the per-event
reasons were read to confirm they attest to what they are cited for.

**Changes in 0.17.0 — the slow half of round 1.** The spec-versus-code reviewer ran
long and reported after 0.16.0 shipped; its findings land here. `rite claim`'s
synopsis said "for the current worker" — `--worker` is required and no ambient
current worker exists anywhere, so the line as printed did not run. §9.11's
`rite add worker --help` transcript was written as captured output but had drifted
from the real thing; it is now literally captured. §8.3.1 listed D-6's ~2-hour
expert timeout among `config.yaml`'s defaults, and it is neither a key nor
implemented — the exact error that subsection exists to prevent, committed inside
it. `rite release --history` existed and was documented nowhere. And §9.12 claimed
`scheduler status` reports the registered interval, which it does not; that one is
flagged rather than deleted, because the missing readback is arguably the defect
rather than the claim — see the section.

Everything else that reviewer checked came back exact, including all eight rows of
§9.11's exit-code table verified in both directions, the read-only contract tested
in an empty directory and inside a project, §11.5.1 reproduced live on a machine
with a global `core.hooksPath`, and every value in §8.3 and §8.3.1 matched against
`config/models.py`.

**Changes in 0.16.0 — two review rounds over the de-upstreaming pass.** Reviewers
were told only "this is a project-agnostic tool" and asked what reads as written for
one project, plus an independent check of every claim against the code.

*Overclaims removed — the spec described a system that was intended, not the one that
exists.* §9.10's four setup-phase behaviours: `start` does none of them. §2.5's claim
that `rite init` and `rite start` fill the coordinator pool: `fill()` has one caller,
`rite pool fill`. §2.7.5's schedule rise: the tick acts only on the transition into
zero, so unmarked it read as "rite starts sessions on a timer". §2.6's worked example,
denominated in a percentage §2.6.2 proves rite cannot compute. Each is now marked at
the claim rather than quietly rewritten.

*Wording that gave away more than it meant.* §5.3 stated bypass-permissions mode as a
property of the product; rite passes no permission flag at all and the mode is the
operator's choice. §5.3.1 and D-26 described sandbox creation as "needing no human
approval", which reads as routing around a confirmation step; what is meant is that a
human starts the session and it creates its own sandboxes.

*Fingerprints the first pass missed.* One industry's word for its own customers,
shipping in every generated project's `/review` rule; a product description in the canonical `brief.yaml`;
two upstream repo names in §5.3.3; an org chart in §2.4 that §4 had already
disclaimed; citations to an implementation plan that is gitignored and does not ship.

*Added.* §7 now describes the two-stage review convention the generated `CLAUDE.md`
cites it for — it previously described no stages at all — and says plainly that the
stage and agent counts are inherited and unjustified. §9.12 and the README now state
that nothing rite runs unattended starts a Claude session.

*Removed.* §13, the porting worksheet; it named private-repo paths and its own note
said it should not ship. D-40's reasoning was rehomed into D-40 first. The decisions
register renumbers to §13.

**Earlier versions, condensed.** ⚠ **Versions 0.10.0–0.12.0 never existed as
reviewed, merged commits** — do not cite them; they name nothing. 0.13.0 was the
first point since 0.9.0 where §2.5 and §5.3 had reviewed content on `main`, and
carried the coordinator-redundancy pool, yoloAI sandboxing with Seatbelt's lack of
network isolation made explicit, the durable-state rule (§2.0), the multi-project
registry (§8.9), continuous handover snapshots (§9.10.1), the non-LLM watchdog
(§3.5), burn-rate measurement as reporting only (§2.6), and the user-set schedule
(§2.7). 0.14.0 reconciled the document against the shipped code after it had
drifted a full implementation day behind — 22 of 56 commands appeared nowhere in
it, four config keys were undocumented, and §8.1 listed none of the runtime state
files; it also added the exit-code contract (§9.11), the scheduler (§9.12), and
§11.5.1's `core.hooksPath` defect, where a pre-push gate reported itself installed
while git never read it. 0.15.0 was the first de-upstreaming pass, which 0.16.0's
two review rounds then corrected.
