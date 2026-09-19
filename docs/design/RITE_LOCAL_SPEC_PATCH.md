# rite local — the SPEC edits, pre-written

**This file exists so RL-T2 is a re-base plus a paste, not a redraft.** It holds the exact text
for every SPEC change [`RITE_LOCAL_DESIGN.md`](RITE_LOCAL_DESIGN.md) §13 asks
for, checked against `origin/main`'s current wording. Nothing here is adopted;
adopting it is RL-T2, which waits on Robert's answers to Q1 and Q2.

**Rationale lives in the design note, not here.** Where a register row needs a
"why", copy the *Why* column of the design's §11 verbatim — it was written to be
that row.

---

## 0. Numbering — compute the base, never read it from here

**Do not paste a D-number from this file.** SPEC's register grows while this
patch sits unapplied: it ended at **D-53** when this was written on 2026-09-17
and at **D-61** on 2026-09-19, two days later. Any number written down here is
wrong by the time someone uses it.

The mapping is arithmetic on a base computed at apply time:

```
base = highest existing D-number in SPEC.md
RL-n  ->  D-(base + n)
```

```bash
git show origin/main:SPEC.md | grep -oE '^\| D-[0-9]+ ' | grep -oE '[0-9]+' | sort -n | tail -1
```

As of 2026-09-19 that is 61, so RL-1 to RL-47 would be D-62 to D-108 — **check it
again before you paste**, and take the base from `origin/main`, not from a local
tree, which may be behind or forked.

| Design | Topic |
|---|---|
| RL-1 to RL-5 | Two axes; presets; closed duties; fungibility; routing order |
| RL-6 to RL-10 | Plan review; mechanical verify; recomposition verify; step review; failure budget |
| RL-11 to RL-14 | Pushing; harness scope; adopt an agent; where inference runs |
| RL-15 to RL-19 | The PM; Phase 2 dependency; what an executor reads; planners who work; measurement |
| RL-20 to RL-28 | Strategies, profiles, duties-not-engines, where they live, resolution, unknown values, termination, refused tasks, discoverability |
| RL-29 to RL-34 | Decline-with-suggestion; Owner as candidate; clarifications; Owner eligibility; answers; undeclared duties |
| RL-35, RL-36 | Backend neutrality; coordination scopes |
| RL-37 to RL-41 | Escalation budget; aggregation; pull not queue; `escalation-exhausted`; the stop channel |
| RL-42 | A local engine declares endpoint, model and agent |
| RL-43, RL-44 | Instruction channels flow downward only; review rounds are not a success metric |
| RL-45 | Every hypothesis ships with a threshold written before its spike |
| RL-46, RL-47 | Composition conflicts return to plan review; infrastructure faults are not attempts |
| RL-48 to RL-55 | Escalation deadlines; composing from the log; session lifetime; when duties must be declared; `route` as a duty; the Owner's strategy follows the role; distributing the expertise table; an Owner that always escalates |
| RL-57 | `coordination.assign_unattended` |

**Four rows are not SPEC decisions, and the register should not take them.** They
are notes about how this work was done, and they belong where they are:

| Row | Why it stays out |
|---|---|
| RL-56 | A retrospective on what a claimability-optimised ticket set omits — a lesson about planning, not about rite |
| RL-58 | Answers a `PHASE2-OPEN-QUESTIONS` item; it belongs to that document's register, not to the tier design's block |
| RL-59 | A citation convention between two `.docs/` files; SPEC has no stake in it |
| RL-60 | Names a gap (rite cannot say a machine hosts two Managers) and a ticket, rather than deciding anything |

Adopting them anyway would put four rows in SPEC that no SPEC reader can act
on. Confirm with whoever is adding rows above RL-47 before dropping them.

**The quoted section text below uses `RL-n` deliberately**, so it survives
re-basing. Convert them all in one pass once the base is known, and convert
nothing before.

**Two decisions that landed since this was written already say part of what the
design says** — cite them rather than duplicating:

- **D-58** (unreadable input to a state merge: pass the bytes verbatim, fail
  closed on the decision that needed them) is exactly RL-25's shape for unknown
  strategy values. Write RL-25's row as an application of D-58.
- **D-55** (slice depth 1 plus pinned hubs) already fixes what an executor reads,
  so RL-17 adopts it rather than specifying a second slicing rule.

---

## 1. Edits to existing sections

### §2.2 Manager — first line

Replace:

> One per Claude account, running on a member's machine. A Manager:

with:

> An identity on a member's machine, with a declared **engine** — what does its
> work — and a set of **duties** (§2.8). A Manager running the `claude` engine is
> one per Claude account; a Manager running a local model has no Claude account
> at all. A Manager:

### §2.3 Owner — the assignment bullet, plus one new bullet

Replace:

> - Assigns work by setting a label with a Worker's name (or a Manager's, for the Manager
>   to sub-assign).

with:

> - Assigns work by setting a label with a Manager's name, for the Manager to
>   sub-assign. **A Worker's name is a valid label only in a project whose
>   Managers do not differ in engine or duties** (§2.8) — with tiers, labelling a
>   Worker bypasses routing.

Add, after "Receives expertise-routed decisions from all Managers":

> - **Routes questions relayed to it**, and is the only router (§2.9).

And to the opening sentence of §2.3, after "the result of leader election (§2.4)":

> Only a Manager holding the `decide` and `board` duties, on an engine other than
> `human`, is eligible (§2.8): the Owner decides, owns the board, and renews a
> lease, and a person with no session cannot renew one.

### §2.4 Owner failover — eligibility, not just priority

§2.4 promotes by priority order in `config.yaml`. The new §2.3 rule narrows the
candidate set, and leaving that only in `PHASE2_TICKETS.md` would make SPEC
contradict itself. Add after the priority-order rule:

> **Priority order selects among *eligible* Managers.** A Manager is eligible
> only if it holds the `decide` and `board` duties on an engine other than
> `human` (§2.8): the Owner decides, owns the board, and renews a lease that a
> running process must renew. A project whose Managers include no eligible one is
> refused at parse. Where no eligible Manager is live there is no Owner and
> routing waits — the existing lease-expiry path, not a new one.

### §2.5 Coordinator pool — whose slots

§2.5's pool spawns `claude` sessions. Add one line so a local or human Manager's
absence from it is deliberate rather than undefined:

> The pool serves Managers whose engine is `claude`. A `local:*` Manager's
> concurrency is its own (§2.8's harness), and a `human` Manager has none.

### §3 Communication — a Manager with no session

§3's status reports double as heartbeats, which assumes every Manager runs a
session. A `human` Manager does not, and would otherwise read as permanently
stalled. Add where heartbeats are defined:

> **A Manager whose engine is `human` does not heartbeat and is never reported as
> stalled** (§2.8). What is tracked instead is the age of what is waiting on that
> person — an assigned ticket, or a question — which `rite status` shows.
> Stall detection is about sessions that stopped; a person who has not answered
> yet is a queue, not a fault.

### §4 Expertise routing — item 3 and the timeout paragraph

Replace item 3:

> 3. **Ties broken randomly** among equally-qualified members.

with:

> 3. **Ties broken randomly** among equally-qualified members — except where a
>    strategy distributes a question (§2.9), which breaks ties by a stable order
>    (fewest questions in flight, then Manager name) so a route can be replayed.

Replace the closing paragraph:

> Timeout and reroute to the next-best match when the chosen expert is unavailable
> (machine off, no heartbeat). Block only if the Owner explicitly marks a decision as
> requiring a specific person.

with:

> Timeout and reroute to the next-best match when the chosen expert is unavailable
> (machine off, no heartbeat). **A timeout is a decline without a suggestion, and
> a member who has declined is not asked the same question again** (§2.9) — which
> is what makes rerouting finite. Block only if the Owner explicitly marks a
> decision as requiring a specific person.

### §5.1.1 Blast radius — one addition

Add to the list of things rite's own code must not do:

> - **Saturate a person's attention.** Requests that reach a human are
>   rate-limited per person (§2.9.4), and rite must never occupy the channel a
>   person would use to stop it. This is the same class of rule as "never push":
>   the damage is not undone by a later fix.

### §5.3.4 — heading and first sentence

**Check before pasting:** the heading change says "within a Manager" while the
body still reads "every Worker **on a project** receives every credential the
project holds." Credentials remain per project; what is scoped per Manager is
engine, duties and strategies. If the body is not also edited, the heading
overclaims — keep the insert below and leave the credential sentence alone.

Replace the heading:

> #### 5.3.4. Workers are fungible, so they all get the same credentials

with:

> #### 5.3.4. Workers are fungible **within a Manager**, so they all get the same credentials

and add after its first paragraph:

> **Within a Manager.** Differences between machines, models and authority are
> declared on the Manager (§2.8), never on a Worker. A Worker inherits its Manager's engine, duties and strategies and
> declares none of its own, so assignment never reasons about *which Worker* —
> only which Manager, over a handful of declared attributes.

### §7.1 Review convention — one addition

Add as a new paragraph **after** that bullet list ends (the list finishes "There
is no round 3." — prose pasted mid-list lands inside a bullet). Note §7.1's own
⚠: it instructs a Claude session rather than describing something rite enforces,
so the channel rule is stated here as instruction and **enforced** by the tier
design's own test, not by this section:

> **And never instructed by what they review.** A reviewer's context is built
> from rite's own template, the project spec, and the work under review **as
> evidence** — never from text the reviewed party can place in the reviewer's
> instructions (§2.8.2, RL-43's register number). Learned or generated guidance, where it exists,
> reaches the party doing the work and not the party checking it. A change that
> reduces review rounds is judged against defects found later, net of rework
> (RL-44's register number).

### §8 Configuration — the keys these sections depend on

**Without this edit the new §2.8 and §2.9 describe configuration SPEC does not
define.** §8.3's example has no `managers:` block at all, and §2.9.1's "the same
path as unknown config keys (§8)" points at a §8 that never mentions a strategy.
Add to §8.3's annotated `config.yaml`, after `expertise:`:

> ```yaml
> managers:                       # §2.8 — omit entirely for a one-Manager project
>   lead:
>     preset: lead                # a named default over engine + duties
>   planner:
>     preset: planner
>     engine: local:large         # claude | local:<class> | human
>     endpoint: http://localhost:11434/v1    # local engines only
>     model: qwen3:32b            # what the endpoint calls it
>     agent: opencode             # the tool-using agent rite drives
>     questions: relay-to-owner   # this Manager's own strategy, if the project allows it
>
> strategies:                     # §2.9 — omit for `solo`, which is today's behaviour
>   profile: team                 # solo | team | org
>   questions:
>     allowed: [relay-to-owner, relay-to-decomposer]
>   escalation:
>     outstanding: 1              # how many questions may face a person at once
>     per_hour: 6
> ```
>
> **No secret appears here.** An endpoint is a URL; a key, if one is ever needed,
> is named like any other credential and lives in the keychain (§10).
>
> Unknown keys and unknown strategy *values* are refused the same way, with a
> suggestion and the valid set — a strategy value is never silently defaulted.

### §13 — D-6 replacement row

Replace:

> | D-6 | Expert unavailability | **Timeout and reroute** | Configurable timeout (~2 hours). Fallback to next-best match, then Owner. Block only if Owner explicitly requires a specific person. |

with:

> | D-6 | Expert unavailability | **Timeout and reroute; a timeout is a decline** | Configurable timeout (~2 hours). Fallback to next-best match, then Owner. **A timed-out member joins the question's declined set and is not asked again, which is what bounds rerouting** (§2.9.3); their late answer is still accepted while the question is open. Block only if Owner explicitly requires a specific person. |

### §13 — notes on D-20 and D-21

Append to D-20's rationale:

> Tier and harness code (§2.8) may call **only** this interface: no module there
> names a backend, and a test enforces it (D-88). Coordination has two scopes —
> intra-machine and inter-machine — which may use different backends (D-89).

Append to D-21's rationale:

> Local-model tiers raise the coordination rate per unit of delivered work, which
> is the real form of the "git versus Redis" argument; it is a throughput
> question, not an adoption one, and the interface already answers it.

---

## 2. New §2.8 — Manager tiers

> ### 2.8. Manager tiers: engine and duties
>
> A Manager declares two independent attributes. A single "tier" field would
> conflate them, and a project manager is the proof: not a model size at all.
>
> **Engine — what does the work:** `claude` (the real `claude` CLI, exactly as
> today), `local:<class>` (the rite-local harness driving a local model,
> `<class>` a free label such as `large` or `small`), or `human` (no session; a
> person acting through the board and ticket comments).
>
> **Duties — what the Manager is for.** A closed, rite-defined vocabulary,
> unlike expertise: rite enforces gates keyed on duties, and a gate cannot key on
> a string a project invented.
>
> | Duty | Meaning |
> |---|---|
> | `decide` | Make project decisions; answer escalations |
> | `board` | Own the board: create, move and label tickets |
> | `spec` | Write and amend the project spec |
> | `plan-review` | Approve or reject a decomposition before any of it is released |
> | `integrate` | Run the terminating check on a branch, then push and open the PR |
> | `decompose` | Break a ticket into subtasks, each with a scope and a verify command |
> | `step-review` | Review one completed subtask |
> | `execute` | Do the work of a ticket or subtask |
>
> **Presets** are named defaults, not types: `lead` (`claude`; decide, board,
> spec, plan-review, integrate, execute), `planner` (`local:large`; decompose,
> step-review, execute), `executor` (`local:small`; execute), `pm` (`human` or
> `claude`; decide, board). Any combination is declarable and needs no code.
>
> **Undeclared duties.** A lone Manager with no preset and no duties holds every
> duty — today's single session, unchanged. Once a second Manager is declared,
> every Manager must declare a preset or duties.
>
> #### 2.8.1. What this does not change
>
> **Workers stay fungible** (§5.3.4). A Worker inherits its Manager's engine,
> duties and strategies, declares nothing, and is chosen by "any free one".
> Assignment targets Managers once engines or duties differ.
>
> #### 2.8.2. The pipeline, and what catches a bad decomposition
>
> Claude plans, a large local model decomposes, a small local model executes. The
> danger is structural: a decomposer reviewing the output of subtasks it designed
> shares every blind spot of the plan, so a wrong slicing passes every check it
> wrote. That review is kept, but **demoted to advisory**, and the gates that
> decide sit where the decomposer has no say:
>
> - **Plan review** — a `plan-review` holder on a **different Manager and
>   engine** approves a decomposition before any subtask is released, and rejects
>   a verify command that cannot fail.
> - **Composition** — accepted subtask branches are applied in decomposition
>   order; a conflict between them returns the decomposition to plan review with
>   the paths named, because two subtasks that conflict should not have been
>   separate (RL-46's register number). `rite doctor` refuses a configuration with
>   a `decompose` holder and no such reviewer.
> - **Mechanical verify** — a subtask is done only when its own verify passes.
> - **Recomposition verify** — the parent ticket's verify runs on the combined
>   branch; a failure returns the decomposition to **plan review**, never to the
>   decomposer.
> - **Failure budget** — repeated subtask failure, and repeated returns to plan
>   review from any cause, mark a decomposition suspect rather than retrying it.
> - **Step review** — stateless and advisory; it cannot gate, because it is not
>   independent.
>
> **Residual risk, named:** if the parent's verify and the subtasks' verifies are
> weak in the same direction, every mechanical gate passes, and what remains is
> `integrate`'s terminating check and the human merge — the same exposure as
> today's single-tier flow, not a new one.
>
> #### 2.8.3. The harness
>
> The harness drives **local engines only**. The `claude` engine keeps spawning
> the real `claude` CLI; rite calls no Anthropic API. It orchestrates an adopted
> agent (opencode or aider under yoloAI) rather than implementing a tool loop:
> rite pulls the assignment, prepares the workspace, takes the claim, heartbeats,
> hands over one subtask and its spec-digest slice, **runs the verify itself**
> rather than trusting the agent's report, commits to a local task branch,
> reports and releases. Inference runs on the host behind an OpenAI-compatible
> endpoint; tool execution stays sandboxed.
>
> **Local engines never push** (§5.1.1): they commit to a local branch and stop.
> Pushing and opening the PR is the `integrate` duty, which puts the terminating
> check before anything leaves the machine.

---

## 3. New §2.9 — Strategies

> ### 2.9. Strategies
>
> A **strategy** is a named choice, from a closed set per subject, for how a
> Manager handles a recurring situation. Named values rather than booleans
> because each subject has more than two answers, and a pair of booleans can
> state a contradiction one named value cannot.
>
> **Strategies choose among safe behaviours. None can turn a gate off.** Plan
> review, mechanical verify, recomposition verify, the fail-closed rules of
> §5.1.1 and "local engines never push" are properties, not strategies.
>
> #### 2.9.1. Subjects, profiles and resolution
>
> Six subjects: `questions` (what a Manager does with a question **it
> originates**), `owner-questions`, `expert-unavailable`, `task-refused`,
> `blocked-work`, `escalation-exhausted`. A **profile** — `solo`, `team`, `org` —
> sets them all at once; any one may be overridden. A profile is not a preset: a
> preset shapes a Manager, a profile shapes a project's policy.
>
> **No `strategies:` block means `solo`, which is what rite does today.** A
> profile's `questions` default differs for a Manager that holds `decide` and one
> that does not, so every preset resolves to a valid value. A profile whose
> values need an unbuilt feature — `team` and `org` need §4 matching — is refused
> with the feature named, never quietly weakened.
>
> **Resolution is explicit before implicit:** a Manager's own override, then a
> project override, then the Manager preset's default, then the profile, then
> `solo`. Whatever the source, the resolved value must belong to the subject,
> satisfy the duty it requires, and be in the project's allowed set. **A value
> that fails is an error, never replaced by a different one** — a substituted
> value is a question going somewhere nobody chose.
>
> **A value requires duties, never engines.** `answer-or-escalate` requires
> `decide`; the engine decides only how the answer is produced — a session, the
> harness, or a person on the board. Nothing in the vocabulary names a model.
>
> **Unknown values fail loudly**, through the same path as unknown config keys
> (§8): the suggestion, the valid set, and each value's meaning. A Manager
> *record* carrying a value from a newer rite keeps it on write but refuses to
> act under it.
>
> #### 2.9.2. Where a strategy lives
>
> `owner-questions`, `expert-unavailable`, `task-refused` and `blocked-work` are
> **project-only**: they decide what happens *between* Managers, and the Owner is
> elected, so a routing policy held on the Owner's Manager would silently change
> on failover. `questions` is personal — a project default, a project `allowed`
> set, and a Manager override within it.
>
> #### 2.9.3. Questions terminate by source, not by counting
>
> Every question carries its origin, the Managers it has visited with the value
> each applied, and its source for each delivery — **set by rite when it
> delivers, never declared by the Manager.**
>
> - A Manager applies `questions` only to a question **it originates**; that is
>   the only place a Manager relays.
> - A **routed** question — relayed by the Owner, or passed to a decomposer — gets
>   one of three actions and **never a relay**: answer it (holding `decide`, and
>   within its expertise or settled by the spec, cited); escalate to its own
>   person; or **decline, optionally with a suggestion** ("not mine — try
>   `billing`"). Answering outside its subject is not an action.
> - **Declines go to the Owner, the single router.** A decomposer may clarify its
>   own decomposition without `decide`; a clarification cannot change a subtask's
>   scope or verify, and a question showing the plan is wrong returns the
>   decomposition to plan review and closes as *superseded*.
> - **The Owner never routes a question to a Manager that has declined it**, and
>   ignores a suggestion naming one, or one without `decide`. With nobody left,
>   the question reaches the Owner's person with every decline attached.
> - The Owner is a distribution candidate like any Manager: Owner is an elected
>   role, expertise belongs to a Manager, and an election must not change who
>   answers.
>
> A question closes on its **first** answer, including a late one from a
> timed-out expert; a differing later answer is shown as a conflict, never
> dropped. The Owner routes a question at most once per `decide` holder.
>
> #### 2.9.4. Escalation is rate-limited, and the limit is a person
>
> Every path that reaches a human draws on a budget belonging to **that person**,
> shared by every Manager — a per-Manager budget multiplies by the number of
> Managers, which is the failure mode.
>
> - **at most one escalation outstanding per person**, plus a ceiling per rolling
>   window;
> - **aggregation**: questions sharing a ticket, a decomposition or an expertise
>   tag become one escalation with parts;
> - **pull, not queue**: past the ceiling, escalations are held and listed in
>   `rite status`, never rendered as a backlog of prompts, and never discarded.
>
> **`escalation-exhausted`** says what a Manager does when it wants a person and
> the budget is spent: `park` (hold the question, mark the ticket blocked, take
> other work — the default), `stop` (take no new work), or
> `proceed-with-assumption` (requires `decide`; records the assumption and the
> question on the ticket and marks the branch provisional, which `integrate`
> refuses while the assumption is unresolved).
>
> **rite must never occupy the channel a person would use to stop it.** One
> outstanding request plus a pullable list — never a stream that can bury
> `rite stop`. This one is not configurable (§5.1.1).

---

## 4. What this file does not cover

- **`PHASE2_TICKETS.md`** needs one line against **P2-2b** (promotion with
  clock-skew tolerance). Left for whoever owns that plan rather than edited from
  here, but written out so it is a paste:

  > **Eligibility (from rite local, D-85).** A Manager is a promotion candidate
  > only if it holds the `decide` and `board` duties on an engine other than
  > `human` — the Owner decides, owns the board and renews a lease, and a person
  > with no session cannot renew one. A project whose Managers include no
  > eligible one fails to parse rather than electing a Manager that cannot carry
  > out its routing policy. Where no eligible Manager is **live**, there is no
  > Owner and routing waits visibly; this is the existing lease-expiry path, not
  > a new one.

  Its D-number is RL-32's under §0's arithmetic — `base + 32` — filled in at
  apply time, never copied from here.
- **§9 (CLI)** gains `rite help strategies` and doctor/status output; those are
  described where they are built (RL-T20, RL-T25), not pre-written here, because
  their wording comes from the registry rather than from prose.
