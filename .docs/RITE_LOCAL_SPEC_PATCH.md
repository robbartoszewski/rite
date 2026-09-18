# rite local — the SPEC edits, pre-written

**This file exists so RL-T2 is a paste, not a redraft.** It holds the exact text
for every SPEC change [`RITE_LOCAL_DESIGN.md`](RITE_LOCAL_DESIGN.md) §13 asks
for, checked against `origin/main`'s current wording. Nothing here is adopted;
adopting it is RL-T2, which waits on Robert's answers to Q1 and Q2.

**Rationale lives in the design note, not here.** Where a register row needs a
"why", copy the *Why* column of the design's §11 verbatim — it was written to be
that row.

---

## 0. Numbering

`origin/main`'s register ends at **D-53** (`/spec` writes `SPEC.md`), so rite
local takes **D-54 onward**, in decision order: **D-(53 + n) for RL-n**, giving
D-54 to D-100 for RL-1 to RL-47. Verified against `origin/main`, not a local
checkout — a forked `main` has produced wrong answers here before.

| Design | Register | Topic |
|---|---|---|
| RL-1 to RL-5 | D-54 to D-58 | Two axes; presets; closed duties; fungibility; routing order |
| RL-6 to RL-10 | D-59 to D-63 | Plan review; mechanical verify; recomposition verify; step review; failure budget |
| RL-11 to RL-14 | D-64 to D-67 | Pushing; harness scope; adopt an agent; where inference runs |
| RL-15 to RL-19 | D-68 to D-72 | The PM; Phase 2 dependency; what an executor reads; planners who work; measurement |
| RL-20 to RL-28 | D-73 to D-81 | Strategies, profiles, duties-not-engines, where they live, resolution, unknown values, termination, refused tasks, discoverability |
| RL-29 to RL-34 | D-82 to D-87 | Decline-with-suggestion; Owner as candidate; clarifications; Owner eligibility; answers; undeclared duties |
| RL-35, RL-36 | D-88, D-89 | Backend neutrality; coordination scopes |
| RL-37 to RL-41 | D-90 to D-94 | Escalation budget; aggregation; pull not queue; `escalation-exhausted`; the stop channel |
| RL-42 | D-95 | A local engine declares endpoint, model and agent |
| RL-43, RL-44 | D-96, D-97 | Instruction channels flow downward only; review rounds are not a success metric |
| RL-45 | D-98 | Every hypothesis ships with a threshold written before its spike |
| RL-46, RL-47 | D-99, D-100 | Composition conflicts return to plan review; infrastructure faults are not attempts |

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

Replace the heading:

> #### 5.3.4. Workers are fungible, so they all get the same credentials

with:

> #### 5.3.4. Workers are fungible **within a Manager**, so they all get the same credentials

and add after its first paragraph:

> **Within a Manager.** Differences between machines, models and authority are
> declared on the Manager (§2.8, and `.docs/PHASE2_ROUTING_DESIGN.md` §a), never
> on a Worker. A Worker inherits its Manager's engine, duties and strategies and
> declares none of its own, so assignment never reasons about *which Worker* —
> only which Manager, over a handful of declared attributes.

### §13 — D-6 replacement row

Replace:

> | D-6 | Expert unavailability | **Timeout and reroute** | Configurable timeout (~2 hours). Fallback to next-best match, then Owner. Block only if Owner explicitly requires a specific person. |

with:

> | D-6 | Expert unavailability | **Timeout and reroute; a timeout is a decline** | Configurable timeout (~2 hours). Fallback to next-best match, then Owner. **A timed-out member joins the question's declined set and is not asked again, which is what bounds rerouting** (§2.9.3); their late answer is still accepted while the question is open. Block only if Owner explicitly requires a specific person. |

### §7.1 Review convention — one addition

Add after "never staffed by whoever proposed them":

> **And never instructed by what they review.** A reviewer's context is built
> from rite's own template, the project spec, and the work under review **as
> evidence** — never from text the reviewed party can place in the reviewer's
> instructions (§2.8.2, D-96). Learned or generated guidance, where it exists,
> reaches the party doing the work and not the party checking it. A change that
> reduces review rounds is judged against defects found later, net of rework
> (D-97).

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
>   separate (D-99). `rite doctor` refuses a configuration with
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

  D-85 is RL-32's register number under the mapping in §0; confirm it when the
  register rows land.
- **§9 (CLI)** gains `rite help strategies` and doctor/status output; those are
  described where they are built (RL-T20, RL-T25), not pre-written here, because
  their wording comes from the registry rather than from prose.
