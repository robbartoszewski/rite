# Phase 2 — capability and permission routing

> ## 🟡 PHASE 2 **STRETCH GOAL** — not core scope
>
> Robert's decision, 2026-09-12. **Phase 2's core ships first:** the state layer
> and CAS primitive (P2.1), Owner lease and failover (P2.2), lease-versus-
> heartbeat. Routing follows **if there is room**, and does not gate the phase.
>
> The design below is kept complete anyway. That is deliberate and has a
> precedent that worked: **yoloAI sandboxing was a Phase 1 stretch goal, written
> up properly while it was fresh, and it landed.** A stretch goal someone can
> pick up is worth the hour; a note someone has to re-derive is not.
>
> **The one part that needs attention BEFORE the core is built is
> [§Seams](#seams-what-the-core-must-not-foreclose)** — the short list of places
> where a core decision would turn this from an addition into a rewrite.
> Everything else here can wait until someone picks it up.

**Status: design note, nothing built. Captured 2026-09-12 during the
soft-publish freeze.** Robert's input, worked through rather than transcribed.

## What he asked for

1. **Capability routing.** Four machines, one with a fast GPU. Anything needing
   an LLM job during execution or verification routes to workers under that
   machine's Manager. **No predefined capability set** — open and flexible.
   Machines carry a **capacity**: how many parallel tasks, **unlimited by
   default**.

   Two failure modes he named, which pull opposite ways:
   - the GPU machine given four heavy jobs when it can run one;
   - the GPU machine given four jobs that did not need it, so the queue fills
     with LLM work nobody else can take.

2. **Permission routing.** Push to production, merge to main, sensitive
   configuration. Not everyone in an organisation should do these — *"that
   would be a very broken security model."* Tasks are tagged; tagged tasks go to
   workers under a Manager holding that permission.

   Mid-task denial: **write a handover, file a ticket for the blocking step,
   move the original to Blocked.**

3. **Blocked-worker reassignment.** The Owner assigns tickets to workers blocked
   on all of theirs, so work does not halt when enough tasks are blocked and
   nobody is free to unblock them, and so workers do not idle holding a blocked
   task.

---

## a. Does this contradict worker fungibility? No — but the spec must say why

§5.3.4 says workers are fungible, on the grounds that differing workers force
assignment to reason about which worker *can* do a job. Capability routing is
precisely that reasoning, so the tension is real and needs resolving in the
text rather than left to a reader.

**The resolution: fungibility is scoped to a Manager.** Workers under one
Manager are identical — same machine, same capabilities, same credentials
(§5.3.4 already gives them the same credential set). Differences live at the
**Manager/machine** level. Assignment becomes two stages:

1. **Route to a Manager** — by capability and permission. The candidate set is
   the number of machines, which is four, and their attributes are *declared*.
2. **Pick a worker under it** — any free one. No analysis.

That preserves what the invariant was protecting. Robert's objection was to
analysing *workers*, whose differences would have been **derived** (from a
module subset) and **invisible** (emerging from how a token happened to be
provisioned). A Manager's capabilities are **declared** and correspond to
physical fact: a GPU either exists in that machine or does not. **Modelling a
difference that exists is not overengineering; inventing one is.**

Be honest about what this still is: an assignment engine. A small one, over a
handful of declared managers rather than over N workers with inferred
capability — but the phrase "we must not need to analyse which worker can do
the job" becomes "we must not need to analyse which *worker*", and §5.3.4
should be amended to say exactly that rather than left to imply more.

**Spec edit required:** §5.3.4's "workers are fungible" gains *"within a
Manager"*, with a pointer here.

---

## b. Least-capable-eligible — right as a preference, wrong as a rule

The proposal: assign to the least-capable eligible worker, not any eligible
worker. Tested against both failure modes:

- four heavy jobs → only the GPU Manager is eligible, three queue behind
  capacity 1 ✓ (this is **capacity** doing the work, not the preference)
- four ordinary jobs → all eligible, preference sends them to weak machines,
  GPU stays free ✓ (this is the **preference** doing the work)

So the two mechanisms are not redundant; each covers one failure mode. But the
rule breaks three ways as stated.

**Break 1 — "least capable" is not a total order.** Capabilities are an open
set, so `{gpu}` and `{docker}` are incomparable and cardinality treats a rare
GPU as equal to a common Docker. Replace it with **scarcity-weighted waste**:
for each eligible worker, sum over the capabilities it holds that the task does
not need, weighting each by rarity across the fleet (a capability on 1 of 4
machines weighs more than one on 4 of 4). Choose the minimum. Well-defined
without a total order, and it says the thing actually meant — *do not consume
the scarce thing*.

**Break 2 — saturation-induced idling. This is the real one.** Strict
least-capable-first means twenty ordinary tasks pile onto the weak machines
while the GPU sits idle. The preference becomes a throughput loss, and can be
worse than random assignment.

**The fix, and it must be in the rule as written:** the preference ranks only
among workers that are *eligible and have free capacity*. It is never a reason
to queue. Filter by eligibility → filter by free capacity → among those,
minimise scarcity-weighted waste → queue only when every eligible worker is
full. An idle machine must never coexist with a queued task it could run.

**Break 3 — it is greedy, and should stay that way.** Even corrected: if the
GPU is the only free machine and an ordinary task arrives, it takes the GPU, and
a heavy task arriving a second later queues. The optimal play holds the GPU
idle, which is speculative scheduling against a queue model rite does not have
and should not acquire. **Accept it and name it here** so nobody later "fixes"
it with reservations: the cost is a delay, not a deadlock, and capacity is the
mitigation.

---

## c. Capabilities and permissions are different kinds of thing

They both narrow the eligible set, and that similarity is a trap. One mechanism
should not model both.

| | capability | permission |
|---|---|---|
| what it is | a **consumable resource** | a **filter** on authority |
| consumed by use | yes — hence capacity | no |
| capacity | meaningful, unlimited by default | not meaningful |
| least-X preference | **yes** — preserve scarcity | **no** — nothing is preserved by it |
| may degrade | sometimes (run slower without the GPU) | **never** |

Two consequences worth writing down:

- **There is no least-privileged preference to have.** A permission is not
  depleted by being exercised. What *is* consumed is that person's worker
  capacity, and capacity already handles it. Preferring a less-privileged
  holder would be modelling a scarcity that does not exist.
- **Permissions must fail closed; capabilities may fall back.** A task needing a
  GPU can legitimately run slower elsewhere if the project says so. A task
  needing production-push authority must never run without it. So a capability
  may carry an optional fallback and a permission may not — and the mid-task
  denial flow exists for permissions precisely because there is no degraded
  path.

**Where they converge:** the mid-task flow. A worker discovering mid-task that
it lacks a permission does handover → blocking ticket → Blocked. A worker
discovering it needs a capability its machine lacks should do the *same thing*.
That is Robert's out-of-scope rule (`6788184`) applying a third time, which is
good evidence the rule generalises — and it means the flow is built once and
used by both.

**Relation to §4 Expertise routing**, which already exists and is the nearest
precedent: expertise routes *decisions* to *people* from an open tag set, ties
broken **randomly**, falling back to the Owner. Capability routes *tasks* to
*machines*, ties broken by **scarcity then load**. Permission routes *tasks* to
*authority*, ties broken by **load**, with **no fallback at all**. Same shape,
three different tiebreaks and three different failure behaviours — reuse the
declaration and matching machinery, do not reuse the resolution policy.

---

## Cost, and what can be deferred

Robert is at 86% quota with a Wednesday reset, and this grows Phase 2. Deferral
boundaries, cheapest first:

**1. Blocked-worker reassignment (his #3) — ship first, independently.** It
needs no capability model, no permission model and no routing layer: it is a
scheduling rule over ticket state that rite already has. It is also the piece
with the most immediate value, because it addresses work halting *today* rather
than a four-machine topology that does not exist yet. **Not blocked by anything
in this note.**

**2. The routing layer, capabilities only.** Declaration, task tagging,
eligibility filter, capacity, and the corrected preference from §b. This is the
shared machinery; permissions reuse all of it.

**3. Permissions.** Adds only the tag semantics plus the mid-task denial flow —
and that flow reuses `6788184`'s out-of-scope-becomes-a-ticket mechanism.

**A capabilities-first cut is legitimate and cheaper**, with one caveat that
must not be lost: **do not ship permission *tags* before the denial flow.** A
tag that narrows assignment but has no mid-task enforcement reads like a
security control and is not one — a worker that reaches a production push it
cannot make would simply fail, and §5.3.4's lesson this week was precisely about
not shipping a security property the design does not have.

---

## Seams — what the core must not foreclose

**This is the only part of this note that needs attention before the core is
built.** Five places where a Phase 2 core decision would turn this stretch goal
from an addition into a rewrite. Each names the core sub-phase it applies to and
what it costs now.

Three cost nothing (shape, not features). Two add one field each. **None of them
is routing** — do not let this expand core scope beyond the list.

### 1. `managers/<name>.json` must preserve fields it does not understand — P2.1

**Cost now: nothing. It is a read-modify-write discipline, not a feature.**

The sharpest one, and it is a data-loss seam rather than an inconvenience. P2.1
already requires *"read-merge-write the FULL state before every push"*, because
a force-push replaces the ref's tree. If a Manager deserialises
`managers/<name>.json` into a closed struct and writes back only the fields it
knows, then **an older Manager silently deletes a newer one's capability
declaration** on its next heartbeat — and does so without error, in the same
class as the delta-push failure §2.4.2 already warns about.

So: unknown keys round-trip. Whatever the struct, the writer must merge into the
parsed document rather than replace it. Worth doing for the core's own sake;
required if capabilities are ever to live there.

### 2. The Manager heartbeat must carry an in-flight count — P2.4

**Cost now: one integer in a payload that is already being pushed.**

Capacity is unenforceable without it: the Owner cannot respect *"this machine
runs one heavy job at a time"* if it cannot see how many are running. Adding it
later is not a code change so much as a **fleet-wide upgrade problem** — the
Owner cannot trust the field until every machine reports it, so capacity routing
would be unusable for however long the slowest machine takes to upgrade.

### 3. Worker→Manager ownership must be explicit in coordination state — P2.3/P2.5

**Cost now: publish workers under their Manager's record, which P2.5 is already
doing for claims.**

P2.3 says the Owner *"assigns tickets by labelling with a Manager **or Worker**
name."* If a Worker can be a routing target while the coordination state does
not say which Manager owns it, then routing-by-Manager-attribute has a hole that
is invisible: the Owner labels a Worker, and nothing checks whether that
Worker's machine has the GPU. Today ownership is implicit — workers live in a
directory on one machine — and implicit is exactly what does not survive going
multi-machine.

### 4. A Manager must be able to REFUSE an assignment — P2.4

**Cost now: a return path in the Manager↔Owner protocol. Small, and the core
needs it anyway.**

P2.4 is written as distribute-what-you-are-given. With no refusal, a mis-routed
ticket has nowhere to go and the stretch goal's mid-task denial flow has no
home. The core needs this independently — a Manager that is full, shutting
down, or missing a module has the same problem — so this is less a seam than a
gap the routing work happens to expose early.

### 5. `src/rite/routing/` should treat expertise as one router, not the router — P2.6

**Cost now: nothing. Package shape.**

P2.6 builds expertise routing into `src/rite/routing/`. Expertise, capability
and permission share declaration and matching but differ in tiebreak (random /
scarcity-then-load / load) and in failure behaviour (fall back to Owner / queue
/ never fall back). If P2.6 hardcodes expertise's policy into the package's
entry point, adding two more routers means restructuring it. Leaving a resolver
seam costs one indirection.

---

## Open questions for Robert

1. **Where is capability declared?** Per machine, so it belongs to the Manager,
   not the project — but a project may need to say *"this task needs a GPU"*,
   which is a project-level tag. Two files, and the Phase 2 coordination repo is
   probably where the machine half lives.
2. **Unlimited capacity by default** means an unconfigured machine is
   preferred by no rule and limited by nothing. Sensible default; worth
   confirming it should not warn once a machine is visibly overloaded.
3. **Who tags tasks?** Board labels are rite's existing assignment mechanism
   (`rite board label`). Reusing them means capability and permission tags live
   on the ticket, visible to everyone, which seems right — but it puts routing
   metadata in a system outside rite's control.
