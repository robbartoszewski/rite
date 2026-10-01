# The decomposer — design for approval

**Status: DESIGN ONLY. Nothing here is built, deliberately.** Robert approved
building the decomposer and set its one hard requirement: it runs on a
**separate, independently-configurable model** from the executor — a strong
model planning, a cheap local one doing the narrow subtasks. This note is the
design he reviews before any of it exists.

**What is already built, verified on `origin/main` (`c4b7f4e`):**
`local/decomposition.py` carries the plan schema, `parse`/`render`, the
APPROVED gate and `problems()`; `local/duty_router.py` routes a `decompose`
stage and enforces RL-6's independence rule for plan review;
`config/managers.py` already defines the `decompose` duty and the `planner`
preset. The execution half is wired and measured — #169 (1a) runs one approved
subtask end to end, with rite running the verify itself.

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

## 0. What needs Robert

**One open input, and it does not block the design: the default decomposer
model.** Everything below is written so this is a *parameter*, not a premise —
no validation rule, no schema field and no test depends on which model it is.

The three candidates, and what each costs:

| Option | Argument for | Argument against |
|---|---|---|
| **Robert's own Manager's model** (whatever the Owner runs) | Zero new configuration; the decomposer is as good as the person's own session | Couples plan quality to an unrelated setting; a cheap Owner silently makes bad plans |
| **`claude` explicitly, with its own `model:`** | Planning is the step where a weak model is most expensive (design §5.2); a Claude decomposer with a local executor is the shape Robert described | Costs API spend on every ticket, and a tier that was meant to be local is not local |
| **A bigger local model** (`local:large`, e.g. a 30B-class model) | Keeps 0.7.0's "Ollama local, both tiers" scope intact; no spend | Unmeasured. We have **no** evidence any local model produces a plan that passes §3's validation, and §5 says that is the single biggest risk |

**My recommendation, and it is a recommendation, not a decision:** ship the
default as **`claude`** and make `local:large` a configured option that works
from day one. Reason: §5.1's failure mode is that an unreliable decomposer
turns the validator into a retry loop that never converges, and we would be
debugging two unproven things at once. Defaulting to the model we know can
produce structured output isolates the variable.

**And for the first proof run, run BOTH** (§4.4) — decomposer=Claude as the
control, decomposer=local as the measurement. The run is the only thing that
can answer whether the local default is viable, and it costs one extra
ticket's worth of time to get both numbers instead of one.

⚠ **Do not read my recommendation as "local cannot do it."** Nobody has tried.
The point of §4.4 is to find out rather than to assume, and if the local
decomposer passes, the default should change.

---

## 1. Config shape — no new keys, and that is the finding

**The configuration for an independently-chosen decomposer model already
exists and is already validated.** This was the part I expected to design and
did not have to.

rite's Manager model has two axes (design §3.1, §3.2): an **engine** that does
the work, and **duties** that say what the Manager is for. `ManagerRole`
carries `engine`, `model`, `endpoint`, `agent`, `context_window` and
`credential` **per Manager**. `PRESETS["planner"]` is already
`(DECOMPOSE, STEP_REVIEW, EXECUTE)`.

So a decomposer is **a Manager holding the `decompose` duty**, and its model is
independently configurable because *every* Manager's model already is:

```yaml
managers:
  - name: lead
    preset: lead            # claude; decide, board, route, spec, plan-review, integrate
  - name: planner
    engine: claude          # ← the decomposer's engine, chosen on its own
    model: claude-opus-4-5  #   and its own model
    duties: [decompose]
  - name: small
    engine: local:small     # ← the executor, a different engine entirely
    endpoint: http://localhost:11434/v1
    model: qwen3:8b
    agent: goose
    context_window: 32768
    duties: [execute]
```

**RL-61: the decomposer is configured as a Manager, never as a decomposer
setting.** No `decomposer.model` key, no `[decompose]` config section. Three
reasons, in order of weight:

1. **A second place to name a model is a second place for it to drift.** S35
   on the local-tier branch is exactly that bug — window enforcement lived in
   two places that did not know about each other, and the one that was wrong
   was the one nobody tested.
2. **The gates are keyed on duties, and a non-Manager decomposer holds no
   duty.** `duty_router.STAGE_DUTY` maps `"decompose"` → `DECOMPOSE` and
   `"plan-review"` → `PLAN_REVIEW`; `_independent()` requires a plan reviewer
   to be a different Manager **and a different engine** from the plan's author
   (RL-6). A decomposer that is a config block rather than a Manager has no
   `engine` for that comparison to read, and RL-6 silently stops holding.
3. **`config/managers.py` already validates it.** `_LOCAL_ONLY` requires
   `endpoint`/`model`/`agent` together for any `local:*` engine; a `claude`
   engine's `model` must be a *Claude* model, so `qwen3:8b` under
   `engine: claude` is refused today rather than reaching a `claude` binary
   that cannot run it. A new config key inherits none of that.

**What this buys for free:** a Claude decomposer with a local executor needs no
code — it is two Manager entries. It also satisfies RL-6 by construction: the
`lead` holds `plan-review` on engine `claude`, so if the decomposer is *also*
`claude`, `_independent()` **refuses the review** and the plan cannot be
approved. That is a real consequence of my recommendation in §0 and §3.5 says
what to do about it.

**What genuinely is missing** (and is small): nothing reads the `decompose`
duty to *start* a decomposer. `local/step.py` (#169) resolves the agent for an
`execute` Manager; the equivalent for `decompose` does not exist. That is
§2.4's work item, not a config change.

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

### 2.4. What has to be added

| Piece | What it is |
|---|---|
| A decomposer caller | The `decompose` analogue of `step.py`: resolve the Manager holding `DECOMPOSE`, build its prompt, run its agent, take the bytes |
| `parse` on the result | Exists. Called by nothing yet |
| Validation (§3) | `problems()` extended, plus a `rejected`/retry path |
| The write | `write(state, plan, expected_version)` with `approval = PENDING` — exists; a decomposer must **never** write `APPROVED`, which §3.5 makes a validation rule, not a convention |

---

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

A real ticket, decomposed and executed end to end, **twice**:

| Run | Decomposer | Executor | Answers |
|---|---|---|---|
| **A (control)** | `claude` | `qwen3:8b` | Does the pipeline work at all when the plan is good? |
| **B (measurement)** | `local:large` | `qwen3:8b` | Is a local decomposer viable? **This is §0's open question.** |

What must be recorded, because without it the run proves nothing:

- **prompt tokens per subtask, against the window** — 1a measured 4,227 and 4,187 against 32,768 (13%, flat, no accumulation). A decomposer's prompt is the ticket plus a spec slice and will be larger; whether it is flat is the question.
- **validation outcome on the FIRST attempt**, per run. This is the number that decides §0.
- **whether rejection reasons changed on retry** (RL-69) — the convergence measurement.
- **the report produced by rite's verify, not by the model.** 1a's report came from rite's own verify and commit; this one must too, or it is the model's self-assessment with extra steps.

⚠ **Run B may simply fail, and that is a result, not a setback.** If a local
decomposer cannot produce a plan that passes §3 in two attempts, the default is
`claude` and we know why — which is worth more than an unmeasured default.

---

## 5. Model-reliability risks, named with what would show each one

Ordered by how much each would cost us, not by likelihood.

### 5.1. ⚠ The decomposer never converges (the one that sinks it)

A model that cannot emit a plan passing §3 in its retry budget makes every
ticket escalate. The tier does nothing and costs a loop.

- **Shows up as:** validation refusing every first attempt, and §3.4's reasons not changing on retry.
- **Measured by:** run B's first-attempt outcome.
- **Fallback if true:** `claude` default, `local:large` stays configurable, documented as unproven. Not a blocker for 0.7.0 on §0's recommendation — which is the point of recommending it.

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
| The config shape needs no new keys (§1) | A real decomposer configuration that cannot be expressed as a Manager entry |
| Validation buys a reviewer's attention (§3.1) | Plan review spending its time on mechanical defects anyway, or §3's rules refusing plans a reviewer would have approved |
| Rejecting whole plans is right (§3.3) | A measured run where the invalid subtask was genuinely separable and refusing all five cost real work |
| Bounded retry helps (§3.4) | RL-69's measurement showing rejection reasons never change between attempts — in which case the retry should be **one** attempt, not two |
| RL-64's coverage proxy is useful | It refuses correctly-sliced plans more often than it catches under-coverage, in which case it stays a warning or goes |
| A local decomposer is viable | Run B (§4.4). This is the open question, not a claim |
| `cannot_fail` is a real gate (§5.3) | **Already false for three commands** — measured, and RL-70 is the fix |

---

## 7. Scope, and what this note does NOT decide

- **Not decided here:** the default decomposer model (§0, Robert's).
- **Not in scope:** 1c (`GOOSE_MODE=auto`), which remains Robert's ruling; 1d (SPEC adoption, `SPEC §9.14.7b`).
- **A follow-up, not folded in:** fixing `_independent()` to fail closed on an unknown author (§3.2, RL-67) — a change to landed behaviour, deserving its own PR and mutation control.
- **A follow-up, not folded in:** the empty-commit-is-not-an-acceptance check (§5.4), which belongs with the harness rather than with plan validation.
- **A follow-up, not folded in, and the one I would do FIRST:** `cannot_fail` accepting bare `true` / `exit 0` / `:` (§5.3, RL-70). A defect in landed code, independent of the decomposer, cheap to fix and cheap to prove.
- **Nothing is built.** Robert reviews this first.
