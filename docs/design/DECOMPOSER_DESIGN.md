# The decomposer — design, approved and implemented

> ## Decision and implementation status (2026-10-02)
>
> **Robert APPROVED the two-level model (§1.2), 2026-10-02** — Level 1 the
> PLAN at the Manager tier, Level 2 the APPROACH per executing unit, with the
> review gates RL-6/RL-7/RL-8 kept (which is why two-level was chosen over a
> self-decomposing worker: §1.3). Per §1.7 this is **NOT "no new config keys"**
> — Level 2 needs exactly ONE new attribute (`decomposer`, holding a `model`)
> on the executing unit, and that attribute is now built.
>
> **Built in this change:**
> - **Level 1 — the plan pipeline** (`local/decompose.py`): resolve the
>   DECOMPOSE Manager, propose, `parse`, validate, and on failure reject +
>   bounded retry (reasons fed back, RL-69 early-stop on identical reasons) +
>   escalate on exhaustion (never a free-form or degraded fallback). It writes
>   PENDING, never APPROVED.
> - **Plan validation** (`local/plan_validation.py`): RL-63 (cites resolve),
>   RL-65 (scope paths repo-relative, fail closed), RL-66 (2..N subtasks),
>   RL-67 **validation half** (`decomposed_by` names a real DECOMPOSE Manager),
>   a self-approved candidate refused (§3.5), and RL-64 as an advisory WARNING.
> - **Level 2 — the `decomposer` attribute** on `ManagerRole` and
>   `WorkerManifest`, with the computed type default (`decomposition_model_for`,
>   `worker_decomposition_model`): Opus for a Claude unit, the unit's own model
>   for a local one.
>
> **Deliberately deferred (own PRs / dependencies), each flagged below:**
> - Level 2's **front-of-turn execution step** and its scope/verify/cites
>   boundary (§2.4): its Claude side waits on the `--agent claude` literal
>   (§1.7/§7, an open dependency, not a follow-up); the local side lands with
>   the proof runs.
> - RL-70 (`cannot_fail` accepts `true`/`:`/`exit 0`) — a defect in landed code,
>   its own PR (§5.3/§7).
> - The `_independent()` fail-closed behavioural fix (§3.2/§7, RL-67's second
>   half).
> - The empty-commit-is-not-acceptance check (§5.4), and the window-pin gaps
>   (§4.5) — harness follow-ups.
>
> Everything below is the design as reviewed; it is retained unchanged except
> this banner.

**Original status (design-only): nothing here was built, deliberately.** Robert
approved building the decomposer and settled its shape: **decomposition is a
per-worker capacity, with a decomposition model separate from the
implementation model** — Opus for a Claude worker, `qwen3.8:latest` for a GPU
one (§0).

**The question this revision exists to answer is PLACEMENT** — the per-worker
framing against the Manager-tier pipeline 1a actually proved. §1 resolves it:
**there are two decompositions, not one**, and only the second is per-worker.
Everything else follows from that split.

**What is already built, verified on `origin/main` (`c4b7f4e`):**
`local/decomposition.py` carries the plan schema, `parse`/`render`, the
APPROVED gate and `problems()`; `local/duty_router.py` routes a `decompose`
stage and enforces RL-6's independence rule for plan review;
`config/managers.py` already defines the `decompose` duty and the `planner`
preset; `WorkerManifest` is the per-worker config surface. The execution half
is wired and measured — #169 (1a) runs one approved subtask end to end, with
rite running the verify itself.

**What is missing is exactly one thing: nothing ever writes a plan.** There is
no caller of `with_subtask`, and no `Subtask` is constructed outside
`decomposition.py` and its tests. Every gate downstream of the plan is built
and tested against hand-written fixtures. So this design is not "build a
pipeline"; it is **"fill the one hole in a pipeline that otherwise exists"**,
and most of the work is in what rite does when the model fills it badly.

**This note extends [`RITE_LOCAL_DESIGN.md`](RITE_LOCAL_DESIGN.md) §5 rather
than replacing it.** §5.1's flow, §5.3's table of what catches a bad
decomposition and §5.4's channel segregation are approved and assumed. Where
this note adds a decision it numbers it `RL-61` onward — next free above RL-60
**at the time of writing**; recompute before adoption, because the register
moves.

**Section references:** `§n` is this note; `design §n` is
`RITE_LOCAL_DESIGN.md`; `SPEC §n` is the project spec.

---

## 0. What Robert settled, and the one thing it forces

**Settled 2026-10-01.** Decomposition is a **per-worker capacity**, not a
single Manager-level step: a Manager has both GPU (local) and Claude workers,
and each worker can carry a `decomposer` capability with **its own decomposition
model, separate from its implementation model.** Defaults by worker type:

| Worker type | Decomposition model | Implementation model |
|---|---|---|
| **Claude** | **Opus** | its Claude model |
| **GPU (local)** | **`qwen3.8:latest`** | `qwen3.8:latest` |

⚠ **Model-name correction, 2026-10-01:** the local model is **`qwen3.8:latest`**
— "qwen3.8" is the model's NAME, a 17.7 GB / 27.3B Q4_K_M model, **not**
qwen3-at-8-billion. The 1a proof ran `qwen3:8b`, a different and much smaller
model (5.2 GB). Both exist on the machine. §4.4's proof run and §5's risks are
written against `qwen3.8:latest`; where a number came from the 8B run it says
so, because relabelling a measurement would falsify it.

**What this settles:** §0 of the previous draft asked which model the
decomposer uses. Answered, and answered better than the question — the model
is per worker and chosen by worker type, so there is no single global default
to pick.

**What it does NOT settle, and what §1 exists to resolve:** *where the
decomposer runs.* The per-worker framing and the Manager-tier pipeline 1a
proved are two different placements, and Robert's defaults are compatible with
**both**, so the defaults cannot decide it. §1 resolves it.

---

## 1. Placement — the resolved model, and the config that follows

### 1.1. Three measured facts that make the question sharp

**"Worker" means two different mechanisms in rite today, and only one of them
is a Worker.**

| | What it is | Where it runs | Exists? |
|---|---|---|---|
| **Claude worker** | a yoloAI sandbox the Manager asks the broker for | its own sandbox | **Yes.** `sandbox/__init__.py` builds `yoloai new --backend <b> --agent claude` — ⚠ **`--agent claude` is a literal**, so every Worker is Claude |
| **GPU "worker"** | a **Manager role**: `engine: local:small`, `agent: goose`, with the `execute` duty | a tmux pane on the host | **Yes, but not as a Worker.** This is the local tier |
| Per-worker config | `WorkerManifest`, read from `.rite/workers/<name>/worker.yml` (`name`, `manager`, `modules`, `claude_instructions`, `follow_module_docs`) | — | **Yes** |

⚠ **So a "GPU worker" does not exist today in the Worker sense.** The local
tier is Managers, and **that is what 1a ran**: `rite local step small KAN-1`,
where `small` is a `ManagerRole`. Robert's sentence describes the 0.7.0
*target*, where both kinds are workers of a Manager; today the two halves are
different mechanisms, and a design that writes "worker" without saying which
will produce code that fits neither.

**This note therefore says `executing unit`** for "the thing that does one
subtask" — today a Claude Worker (sandbox) or a local Manager role, tomorrow
both as workers. The design below attaches to that role, not to either
mechanism, which is what lets it survive the two converging.

### 1.2. The resolution: there are TWO decompositions, and only one is per-worker

They are not the same operation at different sizes. They answer different
questions, have different readers, and need different gates.

| | **Level 1 — the PLAN** | **Level 2 — the APPROACH** |
|---|---|---|
| Input | a ticket | **one approved subtask** |
| Output | subtasks with `scope`, `verify`, `cites` | the executing unit's own steps |
| It decides | how the ticket splits, and **what would prove each part done** | how this unit will do work already defined |
| Read by | three gates — plan review (RL-6), the executor (RL-7), recomposition (RL-8) | **nobody but the unit itself** |
| Reviewed by | an independent Manager, different engine, before anything runs | nothing, and nothing is needed |
| Persisted as | `decomposition.Decomposition`, APPROVED-gated, CAS-written | a working artifact, not state the gates read |
| **Whose model** | the Manager holding `decompose` | ⭐ **the worker's own decomposition model** |

**Robert's per-worker `decomposer` capability is Level 2, and it is right
there.** Level 1 is the `DECOMPOSE` duty that already exists.

**This squares with 1a exactly.** 1a ran a Manager taking **one approved
subtask at a time** — it was consuming a Level-1 plan (hand-authored, since no
decomposer exists). Level 2 sits *inside* one of those turns, before the unit
starts editing. Nothing about 1a's pipeline changes; it gains a step at the
front of each turn.

### 1.3. ⚠ Why Level 1 must NOT move onto the worker — the one hard constraint

**A unit that decomposes its own assignment and then executes it writes its own
verify.** Every gate rite has for a bad plan then reads an artifact produced by
the thing being gated:

- **RL-6** requires a plan reviewer that is a different Manager *and a
  different engine*, because "a reviewer sharing the decomposer's engine shares
  the blind spots of the plan it is checking". A self-decomposing worker has no
  reviewer at all.
- **RL-7** is "rite runs the verify, never reads the claim". It buys nothing if
  the thing being verified chose the verify.
- **RL-8**'s recomposition verify is explicitly *"the parent's own verify, which
  the decomposer did not write"*. If the worker writes both, there is no parent.

⚠ **That is design §5.2's confidently-wrong case arriving with the gates
removed**, and it is the failure the whole local tier is shaped around. **So
Level 1 stays off the executing unit.** This is the single thing I would not
trade, and if Robert wants it traded, §1.6 says what would have to replace it.

### 1.4. Why Level 2 genuinely must be per-worker — Robert's point, and it is a real one

**One plan cannot be right for two unlike executors at once.** The same
approved subtask handed to Opus-in-a-sandbox and to a 27B local model needs
different approaches: the local model needs smaller, more explicit steps and
more of the spec quoted at it; Opus needs neither and is slowed by both.

A Manager-level decomposition has to pick one sizing and be wrong for the other
executing unit. **Making the approach per-worker is the only way to size the
work to the model doing it** — and because Level 2 changes no scope and no
verify, it can be as model-specific as it likes without weakening a gate. That
is exactly why it is safe to put on the worker and Level 1 is not.

### 1.5. What each level's model is

| | Level 1 (the plan) | Level 2 (the approach) |
|---|---|---|
| Claude worker | the `decompose`-holding Manager's model | **Opus** (Robert's default) |
| GPU worker | the `decompose`-holding Manager's model | **`qwen3.8:latest`** (Robert's default) |
| Independent of | — | ⭐ the implementation model, always |

**Robert's defaults are Level 2 defaults and they read naturally as such:** a
Claude worker *"plans with Opus, implements with its Claude model"* is one unit
using two models for two jobs. A GPU worker using `qwen3.8:latest` for both is
the same shape with one GPU and nothing to gain from a second model.

**Level 1's model stays the open parameter it was** — it is a Manager's model,
chosen as any Manager's is (§2), and nothing in the design depends on it.

### 1.6. ⚠ If Robert wants ONLY per-worker decomposition

Then Level 1 disappears, and these go with it, which I would want said out loud
before it is chosen rather than discovered:

- `decomposition.Decomposition`, the APPROVED gate and `problems()` have nothing to validate — the artifact the three gates read is no longer written by anyone but the executor.
- RL-6, RL-7 and RL-8 become inapplicable rather than satisfied.
- A ticket's split is never reviewed by anything before work starts, and the first signal that it was wrong is the composition conflict or a wrong result.

**That is a coherent design** — it is "trust the worker, catch it at the
branch" — but it is a different product from the one design §5 argues for, and
it should be chosen deliberately. **My recommendation is the two-level model in
§1.2**, which takes Robert's per-worker capability whole and keeps the gates.

### 1.7. Config, now that placement is settled

**Level 1 needs no new keys.** A decomposer is a Manager holding the
`decompose` duty, and every Manager already carries its own `engine`, `model`,
`endpoint`, `agent`, `context_window` and `credential`. `PRESETS["planner"]` is
already `(DECOMPOSE, STEP_REVIEW, EXECUTE)`.

**Level 2 needs exactly one new attribute, on the executing unit.** This is a
correction to the previous draft, which said "no new keys" full stop — true for
the Manager-tier placement, and not true once the capability is per-worker:

```yaml
# .rite/workers/<name>/worker.yml — the Claude worker
name: w-auth
manager: lead
decomposer:
  model: claude-opus-4-5     # ← plans its own approach with Opus
# implementation model stays the Worker's own

# .rite/config.yaml — the GPU executing unit (a Manager role today)
  - name: small
    engine: local:small
    endpoint: http://localhost:11434/v1
    model: qwen3.8:latest    # ← implementation
    agent: goose
    context_window: 32768
    duties: [execute]
    decomposer:
      model: qwen3.8:latest  # ← approach; same model, stated rather than assumed
```

**RL-61 (revised): the decomposition model is an attribute of the unit that
will execute, and it defaults by that unit's type** — Opus for a Claude worker,
the role's own model for a local one. Three reasons it is one attribute in two
files rather than a new config section:

1. **It must travel with the unit, because its right value depends on the unit's model.** A central table of decomposition models would be a second place that has to agree with each unit's engine — the S35 shape, two places that did not know about each other.
2. **`WorkerManifest` is already the per-worker surface** and already carries per-worker instruction settings (`claude_instructions`, `follow_module_docs`). This is the same kind of thing: how this worker is told to work.
3. **A default that is computed, not written.** An absent `decomposer` means "use this unit's type default", so the common case stays empty and nobody maintains a model name in two places.

⚠ **`--agent claude` being a literal is what Level 2 has to wait on for the
Claude side.** A Worker's decomposition model is a model *within* the Claude
agent, which is reachable; but "worker type" as a configurable axis does not
exist while the agent is hardcoded. **Flagging it as the dependency, not
designing around it.**

---

## 2. Plan schema — what exists, and the two fields that are load-bearing

### 2.1. The schema is built and versioned

`Decomposition` is `{ticket, subtasks, decomposed_by, approval, approved_by,
returns, format_version}`; `Subtask` is `{id, intent, scope, verify, cites,
status, attempts, branch, last_failure}`. `render`/`parse` are JSON with
`format_version` refused on mismatch — *"upgrade rite rather than reading it as
this one"*. Writes are CAS against a version (`write(state, plan,
expected_version)`), and `released` requires `approval == APPROVED`.

**RL-62: the decomposer does not get a new schema.** It emits this one. The
schema was designed with three gates reading it (RL-6, RL-7, RL-8) and is the
reason the execution half could be built and measured before a decomposer
existed.

### 2.2. The decomposer's output is NOT this schema

⚠ **This is the distinction the whole design rests on.** A model does not emit
a `Decomposition`; it emits **bytes**, and `parse` either returns a
`Decomposition` or a string saying why those bytes are not one. The decomposer
writes a *candidate*, and `parse` + §3's validation is the boundary between
"what the model said" and "a plan rite will act on".

So the pipeline is **model → bytes → `parse` → `problems()` → approval
gate → execution**, and every arrow is a refusal point. Nothing between the
model and the APPROVED gate trusts the model's self-assessment — which is
RL-7's principle (rite runs the verify, never reads the claim) applied one
level up, to the plan.

### 2.3. Two fields are load-bearing at execution time, and only one is obvious

`scope` and `cites` are not documentation. On the 1a branch:

- **`scope` becomes the commit's path allowlist** — `GitCommitter(scope=subtask.scope)`. A subtask whose scope omits the file it must edit produces a commit that stages nothing while the verify may still pass.
- **`cites` becomes the context the model is given instead of the spec** — `_slice_for(root, subtask.cites)` reads the derived unit text for each cite and concatenates it.

`_slice_for` **already fails closed**, and says why in its own docstring: no
cites at all, or a cite whose derived text is not on disk, stops the step with
a problem rather than sending an empty slice, *"because a subtask run without
its slice is the free-form run this path exists to replace."*

⚠ **So bad `cites` is already caught — but caught LATE.** It is caught per
subtask, at execution, *after* a plan reviewer approved the plan and after the
APPROVED gate released it. A plan citing units that do not exist passes review,
gets approved, and then stalls one subtask at a time. §3 moves that existing
check earlier. **It is not new safety; it is the same refusal, made whole-plan
and pre-approval.** Saying it the other way round would oversell it.

### 2.4. What has to be added, by level

**Level 1 — the plan** (the Manager holding `decompose`):

| Piece | What it is |
|---|---|
| A decomposer caller | The `decompose` analogue of `step.py`: resolve the Manager holding `DECOMPOSE`, build its prompt, run its agent, take the bytes |
| `parse` on the result | Exists. Called by nothing yet |
| Validation (§3) | `problems()` extended, plus a `rejected`/retry path |
| The write | `write(state, plan, expected_version)` with `approval = PENDING` — exists; a decomposer must **never** write `APPROVED`, which §3.5 makes a validation rule, not a convention |

**Level 2 — the approach** (on the executing unit):

| Piece | What it is |
|---|---|
| The `decomposer` attribute | On `WorkerManifest` and on `ManagerRole`, with the computed type default (§1.7) |
| A step at the front of the turn | Before the unit edits: given the approved subtask and its spec slice, produce the unit's own steps with the decomposition model |
| ⚠ **A boundary, enforced** | The approach may not change `scope`, `verify` or `cites`. **This is what keeps Level 2 outside the gates** (§1.3), so it is a check and not a convention — the subtask the unit executes must be byte-identical to the approved one |
| Nothing persisted into the plan | Level 2's output is a working artifact. ⚠ If it is written into `Decomposition`, the gates start reading the executor's own words, which is the thing §1.3 refuses |

## 3. Plan validation — rite validates the SHAPE, a reviewer judges the SLICING

### 3.1. The line, and why it is drawn there

`problems()`'s own docstring already draws it: it is *"what plan review is told
to check mechanically, so it can spend its attention on whether the slicing is
right."* **Mechanical checks exist to buy a reviewer's attention, not to
replace it.** Nothing below tries to decide whether a decomposition is *good*.

**What `problems()` checks today** — verified by reading it:

- the plan has subtasks at all
- every subtask has a verify, and `verdicts.cannot_fail` is asked whether it can fail (*"a verify that always passes makes all three gates decorative while looking green"*) — ⚠ **but see §5.3: it passes bare `true`**
- every subtask has a scope and an intent
- no path is claimed by two subtasks
- no duplicate subtask ids

**What it does not check, in priority order:**

| Gap | Why it matters | Rule |
|---|---|---|
| **`cites` is never validated** | §2.3: it is the model's entire context, and an unresolvable cite stalls the subtask after approval | **RL-63** |
| **The ticket is never covered** | Every subtask can be well-formed while the set omits half the ticket. design §5.3 assigns this to the recomposition verify (RL-8) — correctly, because that is the only check independent of the decomposer — but a plan with *one* subtask for a five-part ticket is visible on its face | **RL-64** |
| **Scope paths are never sanity-checked** | A scope of `/`, `..`, or a path outside the repo reaches `GitCommitter` as an allowlist | **RL-65** |
| **Subtask count is unbounded** | One subtask is not a decomposition; two hundred is a model that has lost the plot. Both are cheap to refuse | **RL-66** |
| **`cannot_fail` passes bare `true` / `exit 0` / `:`** | §5.3: measured. The shortest fake verify there is defeats the check whose whole purpose is fake verifies | **RL-70** |
| **`decomposed_by` is never checked against the config** | A plan claiming to be authored by a Manager that does not exist defeats `_independent()`'s author lookup, which falls through to `return True` when the author is not found — i.e. **RL-6's independence rule fails OPEN on an unknown author** | **RL-67** |

⚠ **RL-67 is the one I would not ship without.** `_independent()` reads
`next((r for r in roles if r.name == task.produced_by), None)` and returns
`True` if that is `None`. A plan whose `decomposed_by` names nothing is
reviewable by *anyone*, including the same engine that wrote it. That is
reachable today with a hand-written plan file; a decomposer that gets its own
name wrong makes it routine.

### 3.2. The four proposed rules, concretely

**RL-63 — every cite must resolve, at plan time.** Validation asks the same
question `_slice_for` asks (is the derived unit text on disk?) and names the
missing units. A subtask with no cites at all is refused for the same reason
`_slice_for` refuses it. ⚠ **This couples plan validation to `rite spec
index` having been run**, which is a real cost: a project with no derived units
can write no valid plan. That is the correct answer — a decomposer that cannot
cite the spec is the free-form run this path replaces — but it must be *said*,
in the refusal, pointing at `rite spec index`.

**RL-64 — the plan must say what it does not cover.** Not "the plan covers the
ticket", which is judgement. The weaker, mechanical form: a plan whose subtask
`intent`s collectively mention none of the ticket's own text is refused, and
the parent's own verify (RL-8) stays the real gate. ⚠ **I am least confident in
this one.** Keyword overlap is a bad proxy for coverage and will produce false
refusals on a correctly-sliced plan that uses different words. **Recommend
shipping RL-64 as a WARNING to plan review, not a refusal**, and promoting it
only if measurement shows it catches something RL-8 does not.

**RL-65 — scope paths are repo-relative, and resolve inside the repo.** No
absolute paths, no `..` escaping the root. Mechanical, no judgement, and the
one gap here that is a safety property rather than a quality one: it fails
closed.

**RL-66 — a plan has between 2 and N subtasks**, N configurable, defaulting
small (8 is my suggestion, chosen to be argued with rather than defended). One
subtask means the ticket did not need decomposing; refuse it and say *that*,
because it is useful information and not an error.

**RL-67 — `decomposed_by` must name a Manager that exists and holds
`DECOMPOSE`.** And separately, `_independent()` should be fixed to fail
**closed** on an unknown author rather than returning `True`. ⚠ That fix is
a change to landed, tested behaviour and should be its own PR with its own
mutation control — not folded into the decomposer.

### 3.3. What rite does with a bad plan: reject, retry bounded, never execute

**RL-68: a plan that fails validation is never written as a plan.** It is
written as a rejected candidate with the reasons attached, and the decomposer
is asked again **with the reasons as input**. The retry is bounded; on
exhaustion the ticket **escalates** (design §8.9's escalation budget, the
existing mechanism) rather than falling back to a free-form run or to a
degraded plan.

Three things this must not do, each because the alternative is a known defect
class:

- ⚠ **Never repair the plan.** `parse`'s docstring already settled this for malformed bytes — *"refused rather than repaired: every gate downstream reads this, and a half-understood plan is a plan whose gates check something else."* The same holds for a plan that parses but fails validation. rite filling in a missing verify is rite writing the plan and then checking its own work.
- ⚠ **Never execute a partially-valid plan.** Not "run the four good subtasks and skip the bad one". A decomposition is a claim about how a ticket splits; dropping a subtask changes the claim, and the remaining four then pass their verifies and compose into something that does not satisfy the ticket. That is design §5.2's confidently-wrong case arriving through the front door.
- ⚠ **Never let the retry count as the attempt budget.** RL-47's rule — settled on the local-tier branch — is that only *work* counts as an attempt. A decomposer producing malformed JSON three times has not consumed a subtask's attempts, because no subtask was tried. The same field (`infrastructure_fault` / `counts_as_attempt`) is the precedent; a rejected plan should reach the same answer.

### 3.4. Retry has a convergence problem, and it is the real risk

⚠ **A bounded retry loop assumes the model's second answer is informed by the
first rejection. If it is not, the retry is three times the cost for the same
plan.** This is §5.1's risk and it is unmeasured. The honest design is:

- the retry bound is **configurable and small** (2 is my suggestion)
- rite **records whether the rejection reasons changed between attempts**, because that is the measurement that tells us whether retrying works at all
- if the reasons are *identical* on a retry, stop early — the loop has converged on failure, and spending the last attempt proves nothing

That last point is a design decision I would want in from the start (**RL-69**),
because it turns a cost into a measurement.

### 3.5. The independence consequence of a Claude decomposer

If the decomposer is `claude` and the `lead` holding `plan-review` is also
`claude`, RL-6's `_independent()` **refuses the review** — the plan cannot be
approved and the ticket stalls. This is not a bug; it is RL-6 working. But it
means **§0's recommended default does not work out of the box in a two-Manager
project**, and that must be said plainly rather than discovered.

The three ways out, and the one I would take:

1. **A local Manager holds `plan-review`** — a different engine, so RL-6 is satisfied. ⚠ But it puts the weakest model on the gate that design §5.2 calls the most important one.
2. **Robert holds `plan-review`** (a `human` engine). Honest, satisfies RL-6, and matches design §5.1's *"the Owner approves every decomposition before any of it runs"*. **This is the one I would take for the first proof run.**
3. **Relax RL-6.** No. It exists because *"a reviewer sharing the decomposer's engine shares the blind spots of the plan it is checking"*, and the first thing an unreliable decomposer needs is a reviewer that is not it.

⚠ **`rite doctor` should refuse this configuration at setup**, not at the
moment a plan needs reviewing. A project whose decomposer and only plan
reviewer share an engine is a project that will decompose a ticket and then
stall — and the stall is five steps away from the cause.

---

## 4. Proof approach

### 4.1. The principle, stated once

**Every assertion is about what rite DOES with a plan, never about what a model
produces.** A test that needs a model to emit good JSON is a test of the model.
The validator's tests are pure functions over hand-built candidates — which is
how `decomposition.py` is already tested, and why it was testable before a
decomposer existed.

### 4.2. The invariant, and where it has to be sampled

The product is **plan defect × validation outcome**: each of the gaps in §3.1,
present and absent, against refused/warned/accepted. The known failure here is
testing the **endpoints** — an empty plan and a perfect plan — and missing the
middles, which is the S11 bug class and is how my own first S35 version got the
behaviour backwards. The cases that matter are the ones with *one* defect in an
otherwise-valid plan, because that is where a validator silently accepts.

Specifically worth their own cases:

- a plan that parses but cites a unit that is not on disk (RL-63) — the case that currently reaches execution
- a plan whose `decomposed_by` names nothing (RL-67) — the independence-fails-open case
- a plan with exactly one subtask (RL-66) — not an error, and the refusal should say so
- a plan with four valid subtasks and one invalid (RL-68) — must refuse **all five**, and the test must assert nothing ran
- a plan already marked `APPROVED` by its author — must be refused; the APPROVED gate is worthless if the thing it gates can set it

### 4.3. Mutation controls — reverted, must go red

Each rule earns one, and the test is written to fail first:

| Mutation | Must go red |
|---|---|
| `cites` validation removed | RL-63's cases |
| `_independent()` returns `True` on unknown author (today's behaviour) | RL-67's case |
| Partial execution allowed — skip the invalid subtask, run the rest | RL-68's all-five case |
| Retry counts as a subtask attempt | RL-47's rule |
| Scope accepts an absolute path | RL-65's case |
| A decomposer-written `APPROVED` honoured | the gate case |
| RL-70's literal list reverted | `true`, `:`, `exit 0` as a subtask's verify |

⚠ **The mutation that matters most is the third**, because partial execution is
the one that produces a green run and a wrong result. It is also the one a test
suite most easily misses, because every individual subtask passes.

⚠ **And a lesson from the local-tier branch that applies directly:** the one
mutation that survived both S33's tests and my own (M1) survived because
**neither set asserted on what the real caller actually did** — both tested the
helpers. So at least one case must drive the **real decomposer caller** and
assert on what reached `write()`, not on `problems()` in isolation.

### 4.4. The run that proves it, and what it must measure

**Robert's proof run: a Claude worker decomposing with Opus, and a GPU worker
decomposing with `qwen3.8:latest`.** Both are Level 2 — the per-worker capacity
is the new thing, so it is the thing the run has to exercise.

| Run | Executing unit | Level-2 model | Implementation model | Answers |
|---|---|---|---|---|
| **A** | Claude worker | **Opus** | its Claude model | Does a strong planner plus a cheaper implementer beat one model doing both? |
| **B** | GPU worker | **`qwen3.8:latest`** | `qwen3.8:latest` | Can a local model usefully plan its own approach, or does it burn window to no effect? |

**And a control each, or neither number means anything:** the same subtask run
with **no Level-2 step at all**. Without it, "the decomposed run worked" cannot
be separated from "the subtask was easy". ⚠ This is the zeros-without-a-control
shape that has produced a false green on this project before.

**Level 1 is exercised separately** and may run with whichever Manager model is
configured, since §0 leaves that open.

What must be recorded:

- **prompt tokens per turn, against the pinned window** — and now with the approach step in front, which is the thing that could make the prompt grow. 1a measured 4,227 and 4,187 tokens flat with no accumulation ⚠ **on `qwen3:8b`, and ⚠ with no window actually pinned** (§4.5). Those numbers are the 8B model's and are not the baseline for `qwen3.8:latest`.
- **whether the approach changed what the unit did** — a Level-2 step that produces text nobody acts on is a cost with no effect, and that is the likeliest way this disappoints.
- **validation outcome on the first attempt** for Level 1, and **whether rejection reasons changed on retry** (RL-69).
- **the report produced by rite's verify, not by the model.** 1a's came from rite's own verify and commit; this one must too.

### 4.5. The 1a re-run on `qwen3.8:latest` — and two corrections it forced

**Run 2026-10-01, the same hand-authored plan, one subtask at a time, pinned to
32,768 via `rite-ctx32768-qwen3.8-latest` (created for this run — the model had
never been pinned).** Both subtasks **accepted**, both commits written by rite's
own committer, acceptance from rite's verify.

| | s1 | s2 |
|---|---|---|
| verdict | accepted (`13f6e5f`) | accepted (`e1273e3`) |
| turns | 8 | 5 |
| prompt tokens | 5,204 → 6,259 | 5,084 → 5,328 |
| **peak vs the 32,768 pin** | **19%** | **17%** |
| `truncated` | **0** | **0** |
| wall clock | 6 m 47 s | 5 m 13 s |
| speed | ~3.6–4.0 t/s | ~3.6–4.0 t/s (vs **~22 t/s** for `qwen3:8b`) |

**What this establishes for the decomposer:** a trivial subtask already costs
**a fifth of the window** on the shipping model, and it **grows within a
subtask** (~+150 tokens/turn). It does **not** carry across subtasks — s2 opened
at 5,084, below s1's peak — which is the property §1.2's Level-2 step must not
break. ⚠ `truncated = 0` across all 13 turns is the first direct evidence the
pin holds end to end.

**Two corrections this forced, both to claims I had made:**

1. ⚠ **The 1a run was NOT window-pinned.** `local/step.py` never calls `pin_window` and never passes `context_limit` to `goose_environment`, so no `GOOSE_CONTEXT_LIMIT` was set and no twin was used — run2's log records the session as plain `ollama qwen3:8b`. The "32,768" the earlier numbers were quoted against is the window the role **declares**, not one that was enforced. The token counts stand; "against a 32,768 window" is withdrawn. My summary of them as "flat, no accumulation" was true across subtasks and **wrong across turns**.
2. ⚠ **A pinned twin fails rite's own probe.** `derived_name` produces an untagged name; Ollama stores it tagged (`…:latest`); `engine_probe` matches `name == role.model or name.startswith(role.model + "-")`, which fits neither. rite refuses the model it just created — measured, and it cost this re-run its first attempt as an infrastructure fault. ⚠ **It reaches the real Manager path**, since `supervise._local_environment` pins and hands `pinned.model` to `GOOSE_MODEL`, so any local Manager needing a pin gets a model `rite doctor` calls missing. **This blocks the enforced path the local tier is built on.**

⚠ **One qualitative finding that bears directly on §1.3.** The model did careful
work — it caught a "no trailing newline" requirement and proved the result with
`od -c` — and in the same run **invented a confident, false explanation** for a
config diff it had not caused (*"a runtime model-name swap goose applies at
startup"*; goose does nothing of the kind), with no hedging. **Right about what
it did, wrong about why the world looked that way.** A model like that must not
be the one writing the verify its own work is judged by, which is §1.3's
argument arriving as evidence rather than as principle.

## 5. Model-reliability risks, named with what would show each one

Ordered by how much each would cost us, not by likelihood.

### 5.1. ⚠ The decomposer never converges (the one that sinks it)

A model that cannot emit a plan passing §3 in its retry budget makes every
ticket escalate. The tier does nothing and costs a loop.

- **Shows up as:** validation refusing every first attempt, and §3.4's reasons not changing on retry.
- **Measured by:** run B's first-attempt outcome.
- **Fallback if true:** `claude` default, `local:large` stays configurable, documented as unproven. Not a blocker for 0.7.0 on §0's recommendation — which is the point of recommending it.

### 5.1a. ⚠ Level 2 spends the window it then has to work in — the per-worker risk

**The new risk the per-worker placement creates, and the one I would measure
first.** A GPU worker planning its approach with `qwen3.8:latest` spends those
tokens inside **the same 32,768-token window** it must then execute in. The
plan is not free context — it is context taken from the work.

- **Shows up as:** prompt tokens per turn rising with the approach step in front, and — the bad case — a unit that compacts mid-subtask because its own plan pushed it over.
- ⚠ **The headroom is now measured, and it is smaller than it looks.** §4.5: a *trivial* one-file subtask already peaks at 19% of the 32,768 window and grows ~150 tokens/turn. A real subtask plus an approach step written by the same model is the case that has to fit, and nothing yet says it does.
- **Why it is sharper for the GPU worker than the Claude one:** a Claude Worker's Opus plan and its implementation model are different context windows, so Level 2 costs it nothing it has to execute in. A local worker using one model for both pays twice out of one window. **The two defaults are not symmetric, and the GPU one is where this bites.**
- **Measured by:** §4.4's run B against its no-Level-2 control.
- **If true:** Level 2 is for the Claude worker and off by default for the GPU one, which is a configuration change and not a design change — the attribute is per-unit precisely so this can differ.

### 5.1b. ⚠ An approach step that changes nothing

The quiet failure. The unit produces a plan, then does what it would have done
anyway, and the only effect is latency and tokens.

- **Shows up as:** §4.4's "did the approach change what the unit did" measurement, which exists for this.
- ⚠ **Not detectable from a green run**, which is why the control matters: both the decomposed and undecomposed runs pass, and only the cost differs.

### 5.2. ⚠ Plausible structure, wrong slicing — the confidently-wrong case

A plan that passes every mechanical check and slices the ticket wrongly. **§3
cannot catch this and must not pretend to.** design §5.2 is explicit that the
mechanical gates are *"demoted to advisory"* for exactly this reason.

- **Caught by:** plan review (RL-6), recomposition verify (RL-8), composition conflicts (RL-46) — all of which exist and none of which depend on the decomposer.
- **The residual risk** is design §5.6's, unchanged by this note. Worth restating only so that nobody reads §3 as having closed it.

### 5.3. ⚠ A verify that passes without doing the work

The single cheapest way for the pipeline to look green and be wrong.

⚠ **And `cannot_fail` has a hole I expected not to find. I checked:
`cannot_fail("true")`, `cannot_fail("exit 0")` and `cannot_fail(":")` all
return `""` — "this command's exit code is its own".** It catches swallows
(`|| true`, `; true`), a trailing `&`, and a pipeline ending in a filter. It
does **not** catch the shortest fake verify there is.

So `problems()`'s promise — *"a verify that cannot fail is not a smaller
version of no verify; it is worse"* — is not kept for the three commands a
model is most likely to emit when it has nothing to verify with. **RL-70: the
always-true commands are refused by name** (`true`, `:`, `exit 0`, and the
`exit 0` variants), as a plain list, because this is a safety property and a
list of literals is the one shape that cannot be argued with.

⚠ **This is a defect in landed code, reachable today with a hand-written
plan — not something the decomposer introduces.** It belongs in its own PR with
its own mutation control (revert the list, the three cases go red), and the
decomposer should not be the reason it gets fixed. **Flagging it rather than
folding it in.**

- **The remaining gap, after RL-70:** a verify that is *runnable* and still cannot fail for a subtler reason — a test file the subtask itself writes, a grep for a string the subtask adds to a comment.
- **Not solvable by inspection**, and I would not claim otherwise. The real defence is RL-8's parent verify, which the decomposer did not write.
- **Worth doing:** record how often `cannot_fail` fires on real decomposer output. If it fires often, the decomposer is reaching for fake verifies and that is a model-quality signal we would otherwise not see.

### 5.4. ⚠ Scope that does not contain the edit

§2.3: `scope` is the commit allowlist. A subtask whose scope omits the file it must edit produces an empty commit — and if its verify was already passing, **an accepted subtask that changed nothing.**

- **Shows up as:** an accepted subtask with an empty diff.
- **Cheap, mechanical check worth having at execution time:** an accepted subtask whose commit is empty is an infrastructure fault, not an acceptance. ⚠ **This is not in §3** — it cannot be known at plan time — and it belongs with the harness. **Flagging it as a follow-up rather than silently folding it in.**

### 5.5. Citations the model invents

A model that cites `§9.14.7b` because it sounds right. RL-63 catches it at plan
time, `_slice_for` catches it at execution. **Covered, listed for completeness.**

### 5.6. Cost and latency of the strong decomposer

A `claude` decomposer costs API spend per ticket, and the ticket waits for it.
Not a correctness risk; a scope one. Worth a number from run A before the
default is settled, which is another reason to run both.

---

## 6. How we would know this design is wrong

| Claim | What would show it false |
|---|---|
| The two-level split is real, not bookkeeping (§1.2) | A Level-2 approach that cannot be written without changing a scope or a verify — which would mean it is Level 1 wearing a disguise |
| Level 1 needs no new keys (§1.7) | A real decomposer configuration that cannot be expressed as a Manager entry |
| Per-worker sizing is worth its complexity (§1.4) | The same plan executing equally well on Opus and on `qwen3.8:latest`, measured |
| Validation buys a reviewer's attention (§3.1) | Plan review spending its time on mechanical defects anyway, or §3's rules refusing plans a reviewer would have approved |
| Rejecting whole plans is right (§3.3) | A measured run where the invalid subtask was genuinely separable and refusing all five cost real work |
| Bounded retry helps (§3.4) | RL-69's measurement showing rejection reasons never change between attempts — in which case the retry should be **one** attempt, not two |
| RL-64's coverage proxy is useful | It refuses correctly-sliced plans more often than it catches under-coverage, in which case it stays a warning or goes |
| A local decomposer is viable | Run B (§4.4). This is the open question, not a claim |
| `cannot_fail` is a real gate (§5.3) | **Already false for three commands** — measured, and RL-70 is the fix |

---

## 7. Scope, and what this note does NOT decide

- **Settled by Robert (§0):** decomposition is per-worker, with Opus for a Claude worker and `qwen3.8:latest` for a GPU one. **Resolved here (§1):** placement — two decompositions, only the approach is per-worker.
- **Not in scope:** 1c (`GOOSE_MODE=auto`) — **Robert ruled `auto`, settled in SPEC §5.4.9**, and it lands with the local-tier group, not here; 1d (SPEC adoption, `SPEC §9.14.7b`).
- **A follow-up, not folded in:** fixing `_independent()` to fail closed on an unknown author (§3.2, RL-67) — a change to landed behaviour, deserving its own PR and mutation control.
- **A follow-up, not folded in:** the empty-commit-is-not-an-acceptance check (§5.4), which belongs with the harness rather than with plan validation.
- **A follow-up, not folded in, and the one I would do FIRST:** `cannot_fail` accepting bare `true` / `exit 0` / `:` (§5.3, RL-70). A defect in landed code, independent of the decomposer, cheap to fix and cheap to prove.
- **A follow-up, and it BLOCKS the local tier's window enforcement:** a pinned twin fails rite's own probe (§4.5) — `derived_name` produces an untagged name, Ollama stores it tagged, and `engine_probe` matches neither. Measured today while re-running 1a.
- **A follow-up:** `local/step.py` never pins the window it reads (§4.5). It probes with the role's `context_window` and then launches with no `GOOSE_CONTEXT_LIMIT` and no twin.
- ⚠ **An open dependency, not a follow-up:** `--agent claude` is a literal in `sandbox/__init__.py`, so "worker type" is not a configurable axis yet (§1.7). Level 2's Claude side waits on it.
- **Built and approved (2026-10-02), per the banner at the top.** Level 1, the
  plan validation, and the Level-2 attribute shipped; the deferred pieces listed
  in that banner remain their own PRs.
