# rite — Multi-session Claude coordination for teams

**Version:** 0.18.2 · **Date:** 2026-09-11

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
  timezone: Europe/Warsaw          # required — a schedule with no timezone is a
                                    # trap the first time it's read somewhere else
  windows:
    - hours: "09:00-18:00"
      workers: 3
    - hours: "18:00-09:00"
      workers: 0                   # off overnight — a clean stop, not a kill (§2.7.3)
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
`start_worker` passes `new --backend <b> --agent claude [--env TOKEN=…] <name>
<workdir>` and nothing else: never `--network-isolated`, never `--network-none`.
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
  Note that `destroy` passes `--abandon-unapplied`, so it discards a Worker's
  unapplied changes along with the sandbox — land the work first.
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

Creating or requesting the correctly scoped token per Worker is a **credential-
provisioning responsibility** of `rite init` (§9.3) and `rite add worker` (§9.6) —
the same commands that already generate a Worker's identity and configuration
provision its token as part of the same flow. **The token must never pass through a
chat window** — not typed into a Dispatch prompt, not pasted into a session
transcript. It goes through the same OS-keychain path §10 already specifies for
every other credential, or a GitHub device/App-install flow that hands rite the
token directly without a human ever displaying it in chat.

#### 5.3.5. Practical constraints

- **Optional, and off by default** — `SandboxConfig.enabled` is `False`
  (`config/models.py`), `rite init` does not turn it on, and `rite doctor` treats a
  missing `yoloai` as a note rather than a problem while it stays false. Another
  dependency and a new failure surface shouldn't be forced on rite users who don't
  want it.

  **If that default is ever flipped, it is a product decision with prerequisites,
  not a wording change.** Recorded here because the question has been raised:
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

---

## 8. Configuration

`.rite/` holds configuration files and two knowledge directories:

```
.rite/
├── brief.yaml              # project description (init → AI enriches)
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
| `brief.yaml` | What the project IS — name, role, technology, architecture. | `rite init` (deterministic), then the first Claude session appends follow-ups. |
| `modules.yaml` | What repos/packages the project contains. | `rite init` (detects existing repos), `rite add module`, hand-edited. |
| `config.yaml` | How rite operates — ticket backend, expertise, publish gate, credentials. | `rite init` (basics), hand-edited for advanced config. |
| `context/` | Project-specific knowledge — conventions, architecture decisions, domain rules. | Both user and Claude, as the project progresses. |
| `kb/` | User-provided reference material — coding standards, algorithms, domain knowledge. Authored files committed; fetched caches in `.cache/` gitignored. | User via `rite kb add`, caches refreshed with `rite kb refresh`. |

### 8.1. `brief.yaml`

Generated by `rite init`'s questionnaire (§9.3), then enriched by the first Claude
session which reads it and asks the two or three follow-ups the answers actually
warrant — appending to the same file. Fixed lists cannot follow up; "event sourcing,
CQRS" deserves three more questions, and people skip what they do not understand.
Deterministic capture plus AI enrichment gets both.

```yaml
project:
  name: my-project
  role: owner                    # owner | manager
  root_branch: main              # gitflow setups name develop, next, etc.

what:
  kind: full-stack               # backend | frontend | mobile | full-stack | library | other
  features: "Order tracking with a customer-facing dashboard"
  notes: ""                      # catch-all from init

technology:
  platform: linux
  languages: [python, typescript]
  frameworks: [fastify, react]
  architecture: ""               # free text: "event sourcing, CQRS", etc.

# — AI-enriched section (appended by first Claude session) —
enriched:
  follow_ups:
    - question: "You mentioned event sourcing — is this greenfield or migrating from CRUD?"
      answer: "Migrating incrementally. The read side stays CRUD for now."
  refined_architecture: |
    Event-sourced write side with CQRS. Read models are plain Postgres views
    until traffic justifies projections. No event store — append-only table.
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
```

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
  timezone: Europe/Warsaw          # required (§2.7)
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
the resolved path>`, e.g. `bentora-3f9a2c`. Both halves earn their place. The
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

rite board create/move/list/query/label/link/assign
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
rite schedule set-timezone <tz>    # required before any window is meaningful (D-48);
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

rite sandbox start <worker>        # process-isolate a Worker's session via yoloAI (§5.3).
                                    #   `sandbox.enabled` does NOT gate these commands —
                                    #   it governs SETUP: scoped-token provisioning in
                                    #   `rite add worker`, and whether `rite doctor`
                                    #   treats a missing yoloai as a problem or a note.
                                    #   Refuses when the worker cap cannot be enforced
rite sandbox status <worker>       # is that Worker's sandbox running — exit 1 when the
                                    #   question could not be answered, which is not the
                                    #   same as "no sandbox"
rite sandbox stop <worker>         # stop it, keep it
rite sandbox destroy <worker>      # stop it and discard its state

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
  `pubspec.yaml`, `*.sln`, `go.mod`) to pre-fill technology answers.

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
Root branch defaults to `main`; gitflow setups type `develop` or `next`.

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
rite reads the remote URL and default branch from `.git/config`.

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

The description is free text, skippable. This is the field the first Claude
session will follow up on — "event sourcing, CQRS" triggers architecture
questions; "e-commerce" triggers domain questions.

**Section 5 — Technology** `[5/7]`

Pre-filled from detected markers where possible:

```
─── Technology ─────────────────────────────────────
Platform?       [linux] (detected from environment)
Languages?      [python, typescript] (detected from pyproject.toml, package.json)
Frameworks?     []
Architecture?   [] (e.g. event sourcing, microservices, monolith)
```

All skippable. Auto-detected values shown as defaults. The architecture field
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
  Required (D-48) — a schedule with no timezone is a trap the first time
  it's read from a different machine or a different person's laptop.
```

Defaults to the system timezone, detected. Not skippable — every subsequent
`rite schedule set` (§2.7, §9.1) depends on this being set first.

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

```
Anything else the team should know? []
```

The catch-all is free text, skippable, stored in `brief.yaml` under `what.notes`.

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
✓ Generated .claude/commands/ (3 commands)

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
| `.claude/commands/refine.md` | `/refine` — turn a ticket into a startable spec |

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
| Attempt Owner lease acquisition | Unchanged — Phase 2. | *Not built; §2.4 has no live code at all.* |

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
| `brief.yaml` exists but no project spec | Begin a spec-writing session from the brief |
| `modules.yaml` lists repos with no review checklist | Generate default review checklists |
| `scheduled` tickets in the backlog, at least one claim-safe | Pick the first safe one and start (same logic as §5.2 claims check) |
| `scheduled` tickets in the backlog, but every claim attempt is refused | Report "nothing safe to start" to the human; increment the coordination-cost counter (§2.7.2) — this is a distinct, counted state, not silently the same as either neighbouring row |
| Board is empty | Report "nothing to do" and wait for work |

The table is evaluated top-to-bottom; the first match wins. **The point is that
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
  `rite handover show`, `rite budget`, `rite schedule show`, `rite board list` and
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
(`bentora-7f3a9c21`) is written to `config.yaml` once and committed. Deriving it
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
  `rite sandbox start` injects the token with `--env` (D-31): the Worker cannot
  fetch it. The host process decides which single token to inject, and the
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
so they agree on the *name* — `bentora-7f3a9c21/jira_token` — and each holds its
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
| D-11 | Brief file name | **`brief.yaml`** | Not "seed" — brief is what a person would call it. The questionnaire produces it; the first Claude session enriches it. |
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
| D-48 | Schedule timezone | **Required field, no default** | A schedule with an assumed timezone is correct only where it was written and wrong everywhere else it's read — cheap to require, expensive to debug once omitted. §2.7. |
| D-49 | Ticket-link interface method | **`link(id, target_id, link_type)` added to the abstract `TicketBackend` interface (§6.1), not left as a JIRA-only capability; returns `void \| BackendError`, and a backend with no real link mechanism must return the error rather than substitute a weaker one silently** | §6.2 already specified "blocked by" linking as backend behaviour; found during the Phase-1 implementation-plan rewrite that the interface itself had no method to carry it. A `void`-only return was reviewed and rejected in the same pass — it would let a real link and a silent no-op look identical to a caller that reasons about dependencies. §6.1. |
| D-50 | `rite start`'s setup phase — what it performs vs. reports | **`start` performs anything idempotent, local and free; it REPORTS anything persistent, networked or quota-spending, naming the command that does it.** Three steps the spec had it perform are now reports: registering the scheduler (a standing cron/launchd entry), refreshing the KB cache (network fetches), and topping up the coordinator pool (spawns `claude`, spends quota). `start` gained the health check it genuinely owed — schedule validation, the config it is about to be governed by — without shelling out to `doctor`. | Measured: none of §9.10's four setup steps existed, and the spec had marked them "not built" without deciding whose defect it was. Mostly the spec's. A lifecycle command that installs cron entries and starts paid sessions as a side effect is the wrong default, and two of the three are irreversible in the direction that matters — spent quota is the one damage no cleanup reverses (§5.1.1). §9.10 also contradicted §8.7 outright on the KB cache; §8.7 wins. The outbox flush stays as the one deliberate exception, because §9.10's offline `stop` promises delivery "on next contact" and nothing else would ever deliver it. "Idempotent" is restated precisely: calling `start` twice does not do the work twice, rather than the old and false "is a no-op". §9.10, §2.5.1, §2.7.5, §2.7.4. |
| D-51 | Sandbox default | **Opt-out: `sandbox.enabled: true`, and the §9.3 questionnaire defaults the answer to Yes** | Was opt-in, on the reasoning that "an added dependency should be opted into, not defaulted on" — yoloAI is a separate binary from yoloai.dev, so defaulting on would fail first run for everyone without it. That reasoning stopped holding once two things existed: §9.3 asks at the one moment a human is certainly at a terminal (`install.sh` cannot — piping it to a shell binds stdin to the pipe), and `rite doctor` verifies by starting a real sandbox and tearing it down rather than by finding a file on PATH. A machine without yoloAI now gets a question it can answer and a row naming what is missing, not a first-run failure. On a platform with no backend rite has verified, §9.3 does not ask at all and says why in one line. **Not a precedent for migrating defaults into this register:** numeric defaults keep their rationale in `config/models.py` beside the value, which is where someone changing one will read it. |


---

## 14. Revision history

Kept at the end deliberately. It is a record of what this document got wrong
and when, which is useful for judging how much to trust a section — and useless
as an introduction to the tool.

**Changes in 0.18.2 — §5.3 measured against the installed sandbox.** Three
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
