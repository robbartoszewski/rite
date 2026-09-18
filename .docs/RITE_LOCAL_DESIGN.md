# rite local — Manager tiers backed by local models

**Status: design for approval. Nothing built.** Written 2026-09-17 against
`origin/main` at `68fdc00`. Decisions are numbered `RL-n` here and take the next
free numbers in SPEC's register (currently D-54 onward) on adoption. The
implementation plan is in [`RITE_LOCAL_TICKETS.md`](RITE_LOCAL_TICKETS.md).
Revised the same day to add **strategies** (§8, RL-20 to RL-34), coordination-backend neutrality (§6.5, RL-35 to RL-36) and escalation budgets (§8.9, RL-37 to RL-41), with question
routing by source (Robert's design) replacing a hop count.

**Section references:** `§n` is this note; `SPEC §n` is the project spec.

**Worked configurations, doctor output and an end-to-end trace are in
[`RITE_LOCAL_EXAMPLES.md`](RITE_LOCAL_EXAMPLES.md)** — the shortest way to see
whether this shape is one you want.

**Reading this when you are back:** §0 lists what needs your answer, in the
order it unblocks work. Everything else is decided, with rationale in §11 — and
§12 says what measured result would show each load-bearing claim to be wrong,
including the ones that would mean stopping.

**In one paragraph.** A Manager gains two declared attributes: an **engine** —
what does its work (Claude Code, a local model, or a person) — and a set of
**duties** — what it is for. Robert's three tiers are presets over those two
axes, not a new type, which is what lets the same model express a project
manager who does not code. Engine differences live on the Manager and never on a
Worker, so Workers stay fungible exactly as SPEC §5.3.4 requires, within a Manager.
The pipeline Claude plans → local-large decomposes → local-small executes is
gated so that **no model is the only check on its own plan**: a Claude session
approves every decomposition before any of it runs, every subtask carries a
mechanical verify, and the parent ticket is re-verified on the combined result.
The Python harness exists only for local engines; the Claude engine keeps
spawning the real `claude` CLI, unchanged. How a Manager handles a recurring
situation — a question it cannot settle, an expert who does not answer — is a
**named strategy** from a closed set, chosen once per project by a **profile**
(`solo`, `team`, `org`). Questions are routed by their **source**: only the
Manager a question started with may relay it, the Owner is the single router, and
a Manager holding a question outside its subject declines it back to the Owner
with a suggestion — so no combination of strategies can loop, and expertise
routing survives.

---

## 0. What needs Robert, in unblocking order

**Six questions genuinely need you.** The other eleven are decided, each with a
recommendation the plan proceeds on — §14 lists them with reasons, to overrule
rather than to answer. Terms below are defined in §3 (engines, duties) and §8
(strategies); `RL-Tn` are tickets in
[`RITE_LOCAL_TICKETS.md`](RITE_LOCAL_TICKETS.md).

| # | Needs your answer | Blocks | Proceeding on meanwhile |
|---|---|---|---|
| Q7 | `managers:` config and Manager schema (P2-0a, P2-0b), both `[NEEDS DECISION]` | RL-T3, and through it most of the plan | `engine:`, `duties:`, `preset:`, `questions:` per entry |
| Q1 | One machine or several | Whether Phase 2's cross-machine core is a dependency, and how much §6.5's scope split buys | One machine |
| Q2 | Does the PM run a Claude session | RL-T2, RL-T14 | Both expressible; `human` in the examples |
| Q6 | Build SPEC §4 expertise routing now | `distribute-by-expertise`, the `team` and `org` profiles, the PM's business questions | Not built: only `solo` exists until it is |
| Q3 | Plan review on every decomposition | RL-T8 | Every one |
| Q13 | Only a `decide` + `board` holder, not `human`, may be Owner | P2-2b election; RL-T21 | Yes |

---

## 1. What Robert asked for

Three tiers of Manager:

1. **Claude Manager** — feature-branch termination-check reviews (PRs), writes
   specs, handles the board, makes decisions, does some of the work.
2. **Manager with a local big model** — breaks tasks into smaller steps, reviews
   after each task, does work when idle.
3. **Manager with a small model** (qwen3-class) — executes what Claude planned
   and the big model broke down.

Manager configuration expands to carry a role, and the Claude Code setup becomes
a Python harness for the local-model tiers.

Two further constraints from the brief:

- **One of the new Managers is a project manager, not an engineer** — decides,
  handles the board, routes business questions, does not code.
- **Worker fungibility must survive.** Differences between Workers force an
  assignment engine, which SPEC §5.3.4 exists to avoid.

---

## 2. What this builds on

Nothing here is a parallel model. Each piece extends something already designed.

| Existing | Where | What rite local takes from it |
|---|---|---|
| Capability and permission routing (Phase 2 stretch) | `.docs/PHASE2_ROUTING_DESIGN.md` | **Fungibility is scoped to a Manager** (that note's §a); two-stage assignment — route to a Manager by declared attributes, then any free Worker; capability ≠ permission; the five core seams. |
| Expertise routing | SPEC §4, D-6 | Decisions route to *people* by an open tag set, falling back to the Owner; D-6's timeout. The PM's business questions go through this. Strategies distribute through it, with a stable tiebreak instead of a random one (§8.4, Q9). |
| Workers are fungible | SPEC §5.3.4 | The invariant this design must not break, and the reason engine lives on the Manager. |
| Review convention | SPEC §7.1 | "Never staffed by whoever proposed them." The error-propagation gates are this principle applied across tiers. |
| Blast radius | SPEC §5.1.1, `test_blast_radius.py` | rite's own code never pushes or opens a PR. Decides who integrates. |
| Context concentration | SPEC §2.5.9, D-29 | Local models have small context windows, which makes this worse; step review is stateless because of it. |
| Phase 2 core tickets | `.docs/PHASE2_TICKETS.md` | State-layer interface + local backend (P2-1a), Manager schemas (P2-0b), unknown-key round-trip (P2-1d), heartbeat with in-flight count (P2-4a), refusal (P2-4c). |
| Spec digest | `.docs/SPEC_DIGEST_DESIGN.md` | Per-ticket slices at a median 2.3% of the spec — the only spec form a small model can actually read. |
| yoloAI agents | `yoloai system agents` (0.11.0) | Ships `aider` and `opencode`, both able to drive local models. The sandbox path already exists. |

---

## 3. The model: two axes, not one

A "tier" field would conflate two independent things, and the PM is the proof:
the PM is not a model size at all. So a Manager declares both.

### 3.1. Engine — what does the work

| Engine | Runs as | Cost |
|---|---|---|
| `claude` | The real `claude` CLI, exactly as today (pool, `rite sandbox start`) | Anthropic quota |
| `local:<class>` | The rite-local harness (§6) driving a local model — `<class>` is a free label such as `large` or `small` | Local compute only |

| `human` | No session. A person acting through the board and ticket comments | A person's time |

**A `local:*` Manager declares three more things**, because the class label is a
name, not a configuration: `endpoint` (an OpenAI-compatible URL), `model` (what
that endpoint calls the model), and `agent` (`opencode` or `aider`, whichever
RL-T0 chose). `<class>` stays a free label used by duty routing and by the
project to talk about tiers; the three fields say what actually runs. `rite
doctor` probes all three rather than looking for files: the endpoint answers, the
model is loaded, the agent runs.

**No secret goes in config** (SPEC §10). An endpoint is a URL; if one needs a key, the
config names a credential key and the value lives in the keychain, exactly as
`jira_token` does. A local endpoint on `localhost` normally needs none, which is
one of the quieter arguments for keeping inference on the host.

### 3.2. Duties — what the Manager is for

**A closed vocabulary, deliberately** — unlike expertise and capabilities, which
are open sets. rite enforces gates keyed on duties (§5), and a gate cannot key on
a string a project invented.

| Duty | Meaning |
|---|---|
| `decide` | Make project decisions; answer escalations |
| `board` | Own the board: create, move and label tickets |
| `spec` | Write and amend the project spec (`/spec`) |
| `route` | Decide where a question goes: answer it, distribute it by expertise, or escalate it. Held by the Owner (RL-52) |
| `plan-review` | Approve or reject a decomposition before any of it is released |
| `integrate` | Run the terminating check on a feature branch, then push and open the PR |
| `decompose` | Break a ticket into subtasks, each with a scope and a verify command |
| `step-review` | Review one completed subtask |
| `execute` | Do the work of a ticket or subtask |

### 3.3. Robert's tiers, and the PM, as presets

| Preset | Engine | Duties |
|---|---|---|
| `lead` (tier 1) | `claude` | decide, board, route, spec, plan-review, integrate, execute |
| `planner` (tier 2) | `local:large` | decompose, step-review, execute (when idle) |
| `executor` (tier 3) | `local:small` | execute |
| `pm` | `human` — or `claude` with no `execute`, see Q2 | decide, board, route |

A preset is a named default, not a type. A project may declare any combination,
and new combinations need no code.

**Undeclared duties.** In a one-Manager project, a Manager with no preset and no
`duties:` holds every duty — that is today's single session doing everything,
and it keeps working unchanged.

**Corrected while building RL-T3 (RL-51).** This said "once a project declares a
second Manager, every Manager must declare". Taken literally that breaks every
project already on v0.4.0, which shipped `coordination.managers` as a list of
bare names and may well list three — none of them declaring anything, because
there was nothing to declare. The rule's REASON is that routing by duty cannot
guess, and that only bites once there is something to route between. So:

**Declaration is required from the moment any Manager declares an engine or a
duty, not from the moment a second Manager exists.** A list of bare names keeps
working however long it is; adding the first tier makes every entry declare, and
the error names the ones that have not. That is the same argument §4 already
makes for the one-Manager case, applied to the case it missed.

### 3.4. The attributes of a Manager, and what each one governs

| Attribute | Answers | Set | Routes | Tiebreak | On no match |
|---|---|---|---|---|---|
| **engine** | how does its work get done? | closed | nothing directly — decides which gates apply | — | — |
| **duties** | what is it for? | closed | tasks, by stage | free capacity | queue; never skip a gate |
| **expertise** (SPEC §4) | who knows about X? | open | decisions and questions | random in SPEC §4; stable when a strategy distributes (§8.4) | the Owner |
| **capabilities / permissions** (routing design) | what can its machine do, and what may it do? | open | tasks | scarcity then load / load | queue / never fall back |
| **strategies** (§8) | what does it do when a question or task has nowhere obvious to go? | closed per subject | an own question, a declined question, a refused task | — | a parse error, never a default |

Duties answer "which stage of the pipeline", expertise answers "which person",
capabilities answer "which machine". Merging any two loses something: the PM
needs expertise without `execute`, and a GPU machine needs a capability without
any change to its duties.

---

## 4. Where the fungibility line sits

**Every Worker under a Manager runs that Manager's engine, holds that Manager's
duties' permissions, and has the same credentials. Workers under one Manager are
identical. All differences live on the Manager.**

Stated as the invariants a test must hold:

1. **No per-Worker engine, ever.** A Worker does not declare a model; it inherits
   its Manager's. There is no config key that could make two Workers under one
   Manager differ.
2. **Assignment targets Managers, never Workers, once more than one engine or
   duty set exists in the project.** SPEC §2.3 today lets the Owner label a
   Worker directly; with tiers that bypasses routing, and it is exactly the hole
   the routing design's Seam 3 names. Within a Manager, Worker choice is "any
   free one" — no analysis.
3. **Nothing about a Worker is derived.** Engine and duties are declared on the
   Manager. SPEC §5.3.4's objection was to differences *derived* from how a token was
   provisioned and *invisible* in config; these are declared and correspond to a
   fact — this Manager runs a 70B model or it does not.

**Strategies do not cross the line either.** A Worker has no strategy. Its
question is its Manager's **own** question (§8.1), handled by the Manager's
`questions` value, so two Workers under one Manager can never route the same
question differently.

A project with one Manager keeps today's behaviour: Worker labels still work,
because there is nothing to route between.

**Honest about what this is:** a small assignment engine over a handful of
declared Managers — the one the routing design already accepted — not an engine
over N Workers.

---

## 5. The pipeline, and the error propagation it has to survive

### 5.1. Flow

```
lead (claude)                 planner (local:large)          executor (local:small)
─────────────                 ─────────────────────          ──────────────────────
ticket on the board
   │
   └── assigns ──────────────► decompose
                                  │ writes decomposition
   ◄──── plan-review ─────────────┘  (subtasks + verify each)
   │ approve / reject
   │ (reject → back to decompose, with reasons)
   └── release ──────────────────────────────────────────────► execute subtask
                                                                  │ run verify
                                                                  │ commit to LOCAL task branch
                               step-review ◄──────────────────────┘
                                  │ advisory: accept / send back
                                  ▼
                        all subtasks accepted
                                  │
                        compose the branch  ── conflict? ──► back to plan review
                                  │            (overlapping scope is a plan fault)
   ◄── recomposition verify ──────┘
   │ parent ticket's verify on the composed branch
   │ fail → back to lead's plan-review, NOT to the decomposer
   ▼
integrate: terminating check → push → open PR   (claude session, or a human)
```

### 5.2. The failure mode, precisely

A bad decomposition produces confidently wrong small tasks. If the only gate is
the planner reviewing the output of subtasks it designed, the reviewer shares
every blind spot of the plan: it checks each piece against its own slicing, and a
wrong slicing passes every check it wrote. **That gate is not removed; it is
demoted to advisory**, and the gates that decide are placed where the decomposer
has no say.

### 5.3. What catches a bad decomposition

| Failure | Caught by | Independent of the decomposer because |
|---|---|---|
| Decomposition does not cover the ticket, mis-slices it, or duplicates work | **Plan review** (RL-6), before anything runs | A different engine and context approves it |
| A subtask's verify cannot fail, or does not test what the subtask claims | **Plan review** rejects it (RL-7) | The approver checks the verify, not just the task list |
| A subtask's output does not meet its own check | **Mechanical verify** (RL-7) | Tests and lint, no model opinion |
| Every subtask passes but together they do not satisfy the ticket — the confidently-wrong case | **Recomposition verify** (RL-8), returning to plan review | The parent's own verify, which the decomposer did not write |
| Two subtasks edit the same lines — the decomposition sliced them wrong | **Composition** (RL-46): the conflict returns the decomposition to plan review with the paths named | A merge conflict is evidence about the *plan*, not a chore for whoever composes |
| The decomposition is systematically bad | **Failure clustering** (RL-10) | Counts, not judgement |
| A decomposition that tells its reviewer what not to look at | **Channel segregation** (§5.4, RL-43) | The reviewer's instructions come from rite and the spec; the artifact is evidence |
| Poor quality within a correct slice | Step review — advisory | *Not* independent; that is why it does not gate |
| An executor asks what a subtask means, and the decomposer answers from the same wrong plan | Nothing at answer time — **by design**; recomposition verify afterwards | *Not* independent. Accepted only because a clarification cannot change scope or verify (§8.1); a question that shows the plan is wrong goes to plan review, not to a patched answer |

### 5.4. Instructions flow downward only

**A tier must not be able to edit the instructions of the tier that reviews it.**

This is not hypothetical. The earlier prototype's self-improvement loop injected
learned conventions into worker **and** reviewer spawns through one channel. A
learned rule like *"don't re-litigate style in review"* then measurably cut review
rounds — while cutting review **depth**, because the thing being reviewed had
written part of what the reviewer was told to care about. The fix was channel
segregation: learned content reaches workers, never reviewers.

rite local has three places the same mistake is available, and all three are
closed the same way:

| Channel | Written by | Read by | Rule |
|---|---|---|---|
| The **decomposition artifact** (scope, verify, cited decisions) | `decompose` — a local model | plan review, step review, recomposition | **Evidence, not instruction.** A reviewer reads it as a claim to check, and no field in it can tell the reviewer what to check or what to skip |
| **Generated `CLAUDE.md`** and the harness prompt — the rendered strategies (RL-28), the phase table | rite, from its registry and the project spec | every tier | Rendered by rite. No tier writes it, so no tier can weaken another's instructions through it |
| **Learned conventions**, if rite ever grows them (none today) | whoever holds `spec` | **workers only** | Segregated at the channel, not by wording. A reviewer's context is built from rite's templates, the project spec and the artifacts-as-evidence — nothing a reviewed tier can write |

Stated as one rule: **instructions flow from a reviewer to what it reviews, never
the other way.** A `lead` that holds `spec` may shape what executors are told —
that is the safe direction, and it is how a project improves. Nothing an executor
or a planner emits may shape what `lead` is told when it reviews them.

**It also rules out a metric that looks like progress.** Fewer review rounds is
what a leaky channel produces first, and it reads as efficiency. RL-19 already
insists the claim be measured **net of rework** (D-39); §5.4 adds the specific
trap: a change that reduces review rounds must be checked against defects caught
later, or it is indistinguishable from a reviewer that stopped looking.

### 5.5. Composing the branch is a gate, not a chore

Subtasks are executed on separate local branches, so something has to assemble
them before the parent's verify can run. That step is where a mis-sliced
decomposition shows itself: **two subtasks that conflict were two subtasks that
should not have been separate.**

So composition is ordered, mechanical, and **never resolves a conflict**. It
applies accepted subtask branches in decomposition order, and a conflict returns
the whole decomposition to plan review with the conflicting paths attached —
counting against the same budget as any other return (RL-10). Resolving it in
place would hide the plan's error inside a merge nobody reviews, and hand it to
whichever tier happened to be composing.

### 5.6. Residual risk, named

If the parent ticket's verify and the subtasks' verifies are **both** weak in the
same direction — both test the wrong thing — every mechanical gate passes. What
remains is the `integrate` terminating check (a Claude session reviewing the
branch as a whole) and the human merge. That is the same exposure as today's
single-tier workflow, not a new one, but the pipeline must not be described as
closing it. Plan review's instruction to reject a verify that cannot fail —
"delete the fix and re-run whatever proves it", already in `/ticket` — is the
cheapest mitigation.

---

## 6. The harness

### 6.1. Scope — local engines only

**The harness exists to drive local models. It has no Anthropic dimension at
all.** The `claude` engine keeps spawning the real `claude` CLI — the pool's
`tmux` sessions and `rite sandbox start`'s `yoloai new --agent claude` — which is
the sanctioned mechanism, and nothing here wraps, proxies, replaces or calls it.
rite never calls an Anthropic API itself, before or after this.

### 6.2. Adopt an agent, orchestrate around it

A reliable tool-using agent loop for a small model is the largest and riskiest
thing this design could build. yoloAI 0.11.0 already ships two agents that drive
local models — **opencode** (headless) and **aider** — and rite already runs
yoloAI agents in sandboxes. So the harness is **orchestration, not an agent**:

| rite supplies | the adopted agent supplies |
|---|---|
| pull an assignment for this Manager | reading and editing files |
| prepare the workspace, take the claim | the model's tool-use loop |
| heartbeat and handover | talking to the model endpoint |
| hand the agent one subtask and its spec slice | |
| run the verify command itself, not trust the agent's report | |
| commit to the local task branch | |
| report the result and release | |

Build an agent loop only if the spike (RL-T0) shows neither adopted agent can be
held to the contracts above.

### 6.3. Where inference runs

**On the host. Tool execution runs sandboxed. They talk over a local
OpenAI-compatible endpoint.** Ollama, LM Studio, llama.cpp's server and vLLM all
expose one, so rite requires the endpoint rather than a runtime. This keeps GPU
access out of the sandbox problem entirely — Docker on macOS has no Metal
passthrough — and the sandbox needs only to reach a local port. Whether each
backend can reach the host endpoint is unverified (RL-T1); on seatbelt there is no
network isolation to stop it (D-30), which is convenient here and a known gap
everywhere else.

### 6.4. Local tiers never push

The harness is rite's own code, and SPEC §5.1.1 forbids rite's code a remote-writing
git verb or `gh pr` — `test_blast_radius.py` enforces it. So **local engines
commit to a local task branch and stop.** Pushing and opening the PR is the
`integrate` duty, held by a Claude session or a person, which is Robert's tier 1
exactly. This is not a workaround: it puts the terminating check *before*
anything leaves the machine, which is where it belongs.

### 6.4.1. A Manager publishes what it has completed (RL-49)

**Before a coordinator composes an instruction, what the Manager has already
done must be readable from the message log.** Otherwise the coordinator composes
from memory of what it asked for, and re-states instructions the Manager has
already acted on.

This costs more than it looks. **Measured over 36 hours on this fleet:
re-sent instructions were 10.8% of all context — the single largest avoidable
cost**, ahead of anything the spec digest saves. It is also silent: the Manager
does the work twice, or argues, and neither shows up as an error.

So each duty's completion writes one record — what it finished, against which
ticket or subtask, with the branch or artifact it produced — through the
state-layer interface (D-20), and the harness reads that before it composes the
next hand-off. This is not new machinery: `coordination/message_log.py` and the
heartbeat already exist. What is new is the rule that **an instruction is
composed from the log, never from the composer's recollection**.

### 6.5. When things fail, which is most of the time

A tool loop driven by a small model fails often, and most of those failures are
not the model's fault. **The harness must tell "the work was wrong" apart from
"the work never ran"**, because the attempt budget (RL-10) escalates on the first
and would burn a decomposition's budget on the second.

| Fault | Outcome | Not |
|---|---|---|
| The agent crashes or hangs | Kill it, release the claim, subtask **not attempted** | A failed attempt |
| The endpoint is unreachable, or the model is not loaded | The Manager stops taking work and says why in `doctor` and `status` | Subtasks failing one by one on a machine with nothing running |
| The verify command **errors** rather than fails | Infrastructure: the subtask is not judged | "Verify says no" |
| The verify command fails | The subtask failed — this one is the work | — |
| The harness dies holding a claim | The claim is reclaimable on rite's existing rules | A ticket stuck forever |

**Handover is the same problem one level up.** A replacing harness reads a record
— subtask, branch, attempt count, last verify result — and resumes; per SPEC
§2.0 that record is on disk, never only in a process. Without it, a restarted
Manager either redoes finished work or abandons it.

This is RL-T28, split out of the harness core deliberately: the happy path is one
sitting, and the failure paths are another, but the tier is not usable with only
the first.

### 6.6. What the harness may call

**No rite local code names a coordination backend.** The harness, the duty
router, the decomposition artifact and the question record use the state-layer
interface (D-20, P2-1a) and nothing else; RL-T24 asserts it the way
`test_blast_radius.py` asserts the push rule. Git as *version control* is
untouched — local engines still commit to a local task branch — and keeping those
two senses of "git" apart is most of what this section is for.

**Why it needs saying now.** A claim has been put to Robert that coordinating
through git rather than Redis is what blocks adoption at larger companies. I
cannot test the adoption half: I have no evaluation, won or lost, to point at,
and neither does the claim. What I can do is make it not matter — which is why
this section specifies a constraint rather than arguing a position.

The half that is testable is throughput. A git CAS is a process spawn, an fsync
and possibly a network round trip; D-21 already concedes Redis is the better
technical fit and was dropped on infrastructure cost, not performance. **Tiers
raise the operation rate**: the cheaper a tier's tokens, the more coordination
events it generates per unit of delivered work, and an executor tier is the worst
case. Contention compounds it — every Manager pushing one ref makes CAS retry,
and retries grow with writers.

**Two scopes, not two tiers.** A backend cannot belong to a tier: tiers
coordinate with each other constantly, so they must share a store. What separates
is scope.

| Scope | What lives there | Rate | Natural backend |
|---|---|---|---|
| **Intra-machine** — a Manager and its Workers, subtask statuses, its own open questions | high-frequency, single-writer, worthless to another machine | high, highest under an executor tier | P2-1a's local backend |
| **Inter-machine** — the Owner lease, the board, Manager records, questions that have left their Manager | contended, must survive a machine dying | low | git by default; Redis where a team already runs one |

**What crosses is the rate knob.** Per-subtask events stay local. What crosses is
what another machine acts on: the in-flight count at heartbeat cadence (P2-4a), a
ticket's status when it changes, the decomposition artifact (another Manager
reviews it), and a question once it leaves its Manager. Twenty subtasks do not
mean twenty inter-machine writes.

**When a machine dies, its local scope dies with it** — bounded by that same
rule, since anything another machine needs is already published. The Owner sees
the heartbeat stop and the ticket returns to the queue at its last published
status; an **own** question open on the dead Manager is re-asked by whoever picks
the ticket up, while one that had left it survives, as SPEC §8.7.1 promises.

**The uncomfortable half.** On one machine the inter-machine scope has one
participant, so the rate problem is smallest exactly where this is easiest. If
the tiers span machines (Q1), the executor tier's traffic is inter-machine by
definition, and the answer is D-21's: a lower-latency backend for that scope.

**§12 gives this a falsifier with a consequence**: under 5% of wall-clock, RL-36
is withdrawn and one backend serves both scopes. The split is code, so it has to
earn itself.

---

## 7. The project manager

A first-class Manager with engine `human` and duties `decide` and `board`, no
`execute`. Two things reach the PM, through two existing mechanisms:

- **Business questions**, through SPEC §4 expertise routing, by declaring expertise
  `business`. This is the "questions sent to the person who owns the area" item
  in the README's Planned section — rite local does not re-implement it, and this
  routing is only as built as SPEC §4 is (not built; see Q6). `blocked-work`
  (§8.1) then applies to a Worker waiting on one of those questions.
- **Decisions and board ownership**, through the PM's duties.

Whether the PM works through a Claude session (engine `claude`, duties without
`execute`) or with no session at all (engine `human`) is Q2. The model expresses
both; the difference is how questions reach the PM.

Strategies do not assume the PM is a model (§8.3). `answer-or-escalate` needs the
`decide` duty, which the PM holds; for a `human` engine the question arrives
through the board and is answered by the person, not a session. **The PM is a
person, so questions routed to them are rate-limited like any other** (§8.9) —
the budget is per person, and a Manager with a `human` engine is not a way around
it.

---

## 8. Strategies — named policies, and the profiles that set them

A **strategy** is a named choice, from a closed set per subject, for how a
Manager handles a situation that recurs: a question it cannot settle, an expert
who does not answer, a refused task, a person who cannot be asked yet. Named values rather than booleans because
each subject has more than two answers, and a pair of booleans
(`escalate: true`, `relay: true`) can state a contradiction one named value
cannot.

**Strategies choose among safe behaviours. None can turn a gate off.** Plan
review (RL-6), mechanical verify (RL-7), recomposition verify (RL-8), fail-closed
counters (SPEC §5.1.1) and local engines never pushing (RL-11) are properties, and
there is deliberately no strategy for any of them. "rite fits a solo developer"
must not come to mean "rite is unsafe for a solo developer".

### 8.1. Subjects and values

Six subjects. A subject is added only when a real situation needs a policy, and
each addition takes a decision number. Five are below; the sixth,
`escalation-exhausted`, belongs with the escalation budget and is defined in
§8.9.1.

**"Terminal" means the question stops moving between Managers — not that a person
is asked immediately.** Every terminal that reaches a person draws on that
person's escalation budget (§8.9); past the ceiling the question is held and
`escalation-exhausted` decides what its Manager does meanwhile. Read the `Kind`
column with that in mind.

**A question's source decides which rules apply, not how far it has travelled.**
rite records the source when it delivers a question; a Manager does not declare
it, so a small model cannot mislabel its way into a relay.

| Source | Called | Governed by |
|---|---|---|
| one of the Manager's own Workers, or its own Dispatch session | **own** | `questions` — the Manager's strategy |
| the Owner, distributing — including to its own Manager side (§8.7.1) | **routed** | the routed actions below — **no strategy, and no relay** |
| its executor, via `relay-to-decomposer` | **routed** | the same |

**`questions`** — what a Manager does with an **own** question it cannot settle
alone.

| Value | Kind | Requires | Meaning |
|---|---|---|---|
| `answer-or-escalate` | terminal | `decide` | Answer if the project spec settles it, citing the D-number; otherwise ask this Manager's person |
| `relay-to-owner` | relay | — | Send it to the Owner, the single router |
| `relay-to-decomposer` | relay | — | Send it to the Manager that decomposed the subtask it arose in. Work that was not decomposed has no decomposer, and the question goes to the Owner — the value's defined meaning, stated in `rite help strategies`, not a substitution |
| `escalate-to-user` | terminal | — | Ask this Manager's person |

**Routed actions** — fixed, not a strategy. A Manager that receives a routed
question does exactly one of:

| Action | When | Goes to |
|---|---|---|
| **answer** | it holds `decide`, and the question is within its declared expertise or the spec settles it (cite the D-number) | the question record, where the origin reads it — never only a session, which may have ended (SPEC §2.0) |
| **escalate to its person** | the question is its subject and the spec does not settle it — the expert's person decides, which is the point of SPEC §4 | its person |
| **decline**, optionally **with a suggestion** | not its subject. The suggestion is an expertise tag or a Manager name: *"not mine — try `billing`"* | **always the Owner**, never sideways and never to the sender unless the sender is the Owner |

**Answering outside its subject is not an action.** "Answer it anyway" is the
worst outcome available on a product whose value is not fabricating, so a routed
question a Manager cannot answer from its expertise or the spec has two exits,
neither of them a guess. A decomposer receiving `relay-to-decomposer` answers
questions about **its own decomposition** without `decide` — clarifying what it
wrote is authorship, not a decision — and declines anything else. **A
clarification carries the plan's blind spots**: the author of a wrong slicing
explains it the wrong way. It is allowed only because it cannot change a
subtask's scope or verify; if the question shows the plan itself is wrong, the
decomposer does not answer around it — the decomposition returns to plan review
(RL-6), exactly as a failed recomposition would. **The question closes as
*superseded*, a terminal state**, with the plan-review return linked. **So does
every other open question on that decomposition's subtasks**, wherever it is —
with the decomposer, the Owner or an expert — because the subtasks are withdrawn
with it and nobody is left to use the answers. Every return to plan review, from any cause,
counts against the parent ticket's budget (RL-10), so "approve, ask, flag,
re-approve" cannot repeat without limit.

**While an own question waits, its ticket is blocked on it**, visibly, and
`blocked-work` applies to the Worker. A Worker does not idle holding a question,
and does not guess past it.

**`owner-questions`** — what the Owner does with a question that reaches it
through `relay-to-owner` or a decline.

| Value | Kind | Requires | Meaning |
|---|---|---|---|
| `answer-or-escalate` | terminal | `decide` | As above, asking the Owner's person |
| `distribute-by-expertise` | route | SPEC §4 built (Q6) | Route it to the best SPEC §4 match among `decide` holders **that have not declined it**, preferring a suggestion made by a decline |
| `escalate-to-user` | terminal | — | Ask the Owner's person |

**`expert-unavailable`** — a Manager a question was routed to does not answer
within D-6's timeout. **A timeout is treated as a decline without a suggestion.**

| Value | Kind | Meaning |
|---|---|---|
| `reroute` | route | Back to `owner-questions`, with that Manager added to the declined set |
| `escalate-to-user` | terminal | Ask the Owner's person |
| `wait` | hold | Keep waiting, shown in `rite status` with its age — for a decision that belongs to one specific person (D-6). Applies to timeouts only; a decline is an answer and is never waited on |

**A timeout does not take the question away.** The timed-out Manager joins the
declined set for routing, but its answer is still accepted while the question is
open — a late expert beats an early guess. A late **decline** from a Manager
already in the declined set is recorded and routes nothing: its effect on routing
already happened at the timeout. **The first answer closes the question.** A later answer that differs is attached to the record and shown to
the Owner's person as a conflict; it is never dropped and never silently replaces
the first.

**`task-refused`** — a Manager refuses an assignment (P2-4c).

| Value | Meaning |
|---|---|
| `reroute` | Offer it to the next eligible Manager that has **not** refused it |
| `hold-and-report` | Leave it unassigned; surfaced in `rite status` |

**`blocked-work`** — a Worker whose every ticket is blocked (the routing design's
item 3, which is built outside this plan and reads this subject).

| Value | Meaning |
|---|---|
| `reassign` | The Owner routes an unblocked ticket to the Worker's **Manager**, which gives it to that Worker (RL-4); in a one-Manager project, directly to the Worker as today |
| `hold-and-report` | No reassignment; surfaced in `rite status` |

### 8.2. Profiles

A **profile** sets every subject at once, so a solo developer chooses one word
rather than five policies. Any subject can be overridden on top.

**Deliberately not called a preset.** A Manager preset (§3.3) shapes one Manager
— engine and duties. A profile shapes a project's policy. One word for both would
make "set the preset" ambiguous at the exact moment someone is configuring a team.

| Subject | `solo` | `team` | `org` |
|---|---|---|---|
| `escalation-exhausted` (§8.9.1) | `park` | `park` | `park` |
| escalation ceiling, per person | 1 outstanding, 6/hour | 1 outstanding, 6/hour | 1 outstanding, 6/hour |
| `questions` default, Manager holds `decide` | `answer-or-escalate` | `relay-to-owner` | `relay-to-owner` |
| `questions` default, Manager lacks `decide` | `relay-to-owner` | `relay-to-owner` | `relay-to-owner` |
| `questions` allowed | all four | all four | `relay-to-owner`, `relay-to-decomposer` |
| `owner-questions` | `answer-or-escalate` | `distribute-by-expertise` | `distribute-by-expertise` |
| `expert-unavailable` | `escalate-to-user` | `reroute` | `reroute` |
| `task-refused` | `reroute` | `reroute` | `hold-and-report` |
| `blocked-work` | `reassign` | `reassign` | `hold-and-report` |

**A profile's `questions` default depends on whether the Manager holds
`decide`.** That is part of the profile's definition, not a substitution: one
row for Managers that can answer, one for those that cannot, both fixed and both
shown by `rite help strategies`. Without the split, the default profile would
reject a `planner` — a value requiring `decide` on a Manager without it — and
"adopting strategies changes nothing" would be false the moment a second tier
exists. **Every preset under every profile resolves to a valid value**, and a
test holds that.

**`solo` is what rite does today, and it is the default** when a project has no
`strategies:` block: the session answers what the spec settles and asks the
person otherwise. Adopting strategies changes nothing for an existing project.

**`team` and `org` do not exist until SPEC §4 expertise routing does.** Both route
the Owner's questions by expertise, and rite does not substitute (§8.5), so until
SPEC §4 is built (Q6) choosing either is a **parse error**, and no override rescues
it — a profile that parses must mean its whole table. The error names the missing
feature and the nearest configuration that works today: `profile: solo` with
`questions: relay-to-owner`, which sends questions to the Owner without
distributing them. `distribute-by-expertise` written anywhere is the same parse
error, and `rite init` does not offer `team` or `org`. A profile is a stable
definition; it does not quietly mean something weaker in an older version.

**Absent is not unrecognised.** A missing `strategies:` block is a documented
default, which `rite doctor` shows with its source. A block naming a profile or
value rite does not know is an error (§8.6). The rule is about what was
*written*, not what was left out — exactly as for config keys today.

The escalation ceiling does not vary by profile: the limit is one person's
attention, and a person on a team project is not more interruptible than a solo
developer. Projects override it; profiles do not pretend to know it.

`org` leaves a Manager no personal escalation **of its own questions**: in an
organisation a question goes through the Owner, so there is one place to see what is waiting on whom.
`org` holds refused tasks rather than rerouting them, because in an organisation a
refusal is usually information someone should see.

### 8.3. Values require duties, never engines

"Answer it yourself" means something different for a qwen3-class model, a Claude
session and a project manager. The strategy set does not guess which: **a value
declares the duty it needs, and the Manager's engine decides only how it is
delivered.** Nothing in §8.1 names a model, a prompt or a session.

- `answer-or-escalate` requires `decide`. An `executor` has no `decide`, so it
  cannot hold that value — a parse error naming the Manager and the missing duty,
  **not a silent downgrade to relaying**.
- A `local:*` Manager answers questions only if the project gave it `decide`.
  That is the project's declared choice, visible in config, not rite's assumption
  about what a large model is good for.
- **Delivery follows the engine.** `claude`: the session answers. `local:*`: the
  harness answers. `human`: the question arrives on the board or as a ticket
  comment, and the person answers.
- On a `human` engine, `answer-or-escalate` and `escalate-to-user` reach the same
  person. Accepted, and `rite doctor` notes the equivalence rather than rejecting
  a harmless redundancy. Both draw on that person's budget (§8.9): a `human`
  Manager is not a cheaper way to ask.

### 8.4. Where a strategy lives — decided by what it governs

The lean — project default, Manager override within a project-declared allowed
set (with a preset's default underneath both, §8.5) — is right for one subject
and wrong for the rest. **The line is whether the
subject decides what happens between Managers.**

**Routing subjects are project-only, with no Manager override:**
`owner-questions`, `expert-unavailable`, `task-refused`, `blocked-work`.

The deciding argument is failover. The Owner is elected (SPEC §2.4). If
`owner-questions` lived on the Owner's Manager, **a failover would silently change
the project's routing policy** to whatever the next machine's person chose —
questions start going somewhere else and nobody changed anything. The same holds
for any subject a Manager applies on others' behalf: its outcome must not depend
on which Manager happens to be applying it. Only project config survives a change
of Owner, and only project config is reviewed as a committed file.

**Project-only is necessary but not sufficient**: the policy survives failover
only if every Manager that could become Owner can carry it out. `solo`'s
`owner-questions: answer-or-escalate` requires `decide`, and nothing in Phase 2's
election stops a `local:small` executor winning the lease. So **only a Manager
holding `decide` and `board` is eligible to be Owner**, whenever the project has
one. A project in which **no** Manager holds both is a parse error — SPEC §2.3
requires exactly one Owner, and such a project could never have one. Every
routing value's requirement is `decide` or SPEC §4, so eligibility plus the SPEC §4 check
(§8.2) means any Owner can carry out the project's policy; no separate doctor
check is needed.

**When every eligible Manager is down, there is no Owner** — the same state as
today's Owner machine being off, handled by Phase 2's lease (SPEC §2.4.1), not by a
fallback. Other Managers finish the work they hold; questions and refusals
waiting on the Owner are held and shown in `rite status`; nothing routes until an
eligible Manager takes the lease. Electing an ineligible Manager instead would
trade a visible stall for a routing policy nobody can carry out. SPEC §2.3 already gives the Owner the board and every expertise-routed
decision — this writes down what election has so far assumed, in the duties that
now name those jobs. **And its engine must not be `human`**: the Owner holds a
lease that a running process renews (SPEC §2.4.1), and a person with no session cannot
renew one. That is a property of holding a lease, not of answering questions, so
it does not contradict §8.3 — just as `integrate` is refused to `local:*` engines
(RL-11). The `lead` preset qualifies, and so does `pm` on a `claude` engine;
`pm` on `human`, `planner` and `executor` do not. A `human` PM still receives
every question routed to its expertise — it is a distribution candidate like any
`decide` holder (§8.7.1) — it just cannot be the router. This constrains **P2-2b**
(promotion), where a candidate is chosen; Q13 asks Robert to confirm it.

**`questions` is personal: project default, a project `allowed` set, and a Manager
override within it.** It governs only what a Manager does with questions **it
originates**, before anything leaves it — a legitimate call about one's own work,
and the only subject where a solo developer on a team project and a PM have
genuinely different needs. `allowed` is how a project takes that call away.

**Nondeterminism.** What varies between Managers is only what happens to an
**own** question, and that is fixed by who raised it. For a given origin the route is fully
determined by configuration and board state — there is no random choice anywhere
in §8. (SPEC §4's random tiebreak between equal expertise matches is replaced here by
a stable order — fewest in-flight questions, then Manager name — so a replayed
route is the same route.) `rite doctor` prints every Manager's resolved
`questions` value, so the variation is visible rather than discovered.

### 8.5. Resolution — rite never substitutes

**Explicit before implicit, at every level.** For `questions`, the first value
found wins:

1. an override written on the Manager;
2. an override written in project config;
3. the Manager preset's default (`executor` defaults to `relay-to-decomposer`);
4. the project profile's default for a Manager with or without `decide` (§8.2);
5. the built-in default profile, `solo`, the same way.

Routing subjects use 2, 4 and 5 only.

A project override outranks a preset default because it was written and the
preset default was not: `strategies.questions: relay-to-owner` means every
Manager, including `executor`s, unless a Manager says otherwise itself. The cost
is stated rather than hidden: a project override applies to every Manager, so
one that requires a duty — `answer-or-escalate` — is an error on any tiered
project, and belongs on the Managers that hold `decide` instead (§8.6).

Then, **whatever the source**, the value must belong to the subject, satisfy its
requirement (§8.3), and — for `questions` — be in `allowed`. A value that fails
is an error, **including a preset's default**: an `executor` preset on an `org`
project resolves `relay-to-decomposer`, which `org` allows; on a project whose
`allowed` omits it, the Manager is misconfigured and doctor says so, naming both
fixes (allow the value, or override the Manager). **rite never resolves a
conflict by using a different value** — that is a question quietly going
somewhere nobody chose.

### 8.6. Unknown values fail loudly — one mechanism

`config/parse.py` already refuses unknown keys, with a `difflib` suggestion and
the known set (`_unknown_key`). Strategy values and profile names go through the
same path, not a second one, so keys and values fail the same way.

| Written | The error says |
|---|---|
| a misspelt value (`relay-to-onwer`) | did you mean `relay-to-owner`; the subject's values, each with its meaning |
| a value from another subject (`distribute-by-expertise` under `questions`) | that value belongs to `owner-questions`; the values `questions` accepts |
| an unknown subject | the same unknown-key message as any other key |
| an unknown profile | the profiles, each with one line on who it is for |
| a value needing a duty the Manager lacks | the duty, the Manager, where the value came from, the values it *can* hold, and the fixes — override that Manager, or move the value from project level onto the Managers that hold the duty |
| a value outside `allowed` | the allowed set, and where the value came from (§8.5) |

Parsing stops, as it does today; there is no "warn and use the default".

**Writing preserves; acting refuses.** Manager records must round-trip keys they
do not recognise (P2-1d), which looks like it contradicts failing loudly. It does
not. A Manager record is written by rite, not a person, and an older rite that
meets a newer strategy value on one **keeps it on write and refuses to act under
it** — that Manager says why in doctor and status, refuses new assignments
(P2-4c, so its Workers raise no new questions), **declines** every routed
question with the reason (a decline under S3, so the Owner routes past it), and
holds the questions already open on its own tickets, visibly. Nothing is lost
and nothing is guessed.

**Project config is different, because it is shared.** A newer value committed to
`.rite/config.yaml` is a parse error for an older rite, which therefore **stops**:
it does not start, and a running Manager that re-reads config and fails to parse
it stops taking work and **gives up the lease** if it is Owner, since an Owner
that cannot read the routing policy cannot apply it. Questions routed to it time
out into declines (S3); its own open questions are held until it is upgraded.
`rite doctor` on that machine names the version the config needs.

### 8.7. Composition — closed by source, not by counting

Relaying and distributing compose, and that is where the bugs are: a Manager
relays to the Owner, the Owner distributes by expertise, the best match is the
Manager that relayed it, which relays to the Owner.

**This design uses the question's source (§8.1), not a hop count, and the
difference is not cosmetic.** A hop count counts something in order to
approximate a rule; source *is* the rule. A question the Owner routed has already
been routed by the Owner, so sending it back is not "one hop too many" — it is
wrong on the first attempt. A hop limit also has to be high enough for every
legitimate route, so it permits whatever fits under it — including the pointless
round trip it stands in for. A limit of two still lets a question go out and
straight back before anything stops it. Under source, sending it back is not an
option to be limited; it is not an option.

**The rules:**

- **S1.** A Manager applies `questions` only to an **own** question. That is the
  only place a Manager relays, so any question is relayed by a Manager at most
  once — by its origin.
- **S2.** A **routed** question gets an action (§8.1): answer, escalate to its
  person, or decline to the Owner. No relay, no strategy.
- **S3.** **One router.** Every decline and every timeout returns to the Owner,
  never sideways. The Owner is the only place a question changes Manager after
  its origin's single relay.
- **S4.** **The Owner never routes a question to a Manager that has declined
  it.** A suggestion naming a Manager in the declined set is ignored. With no
  eligible Manager left, the question goes to the Owner's person with every
  decline and suggestion attached.

**S4 is the part that is easy to lose, and it is not a hop count in disguise.**
One router with decline removes every sideways cycle, but not the cycle *through*
the router: the Owner routes to B, B declines suggesting C, the Owner routes to C,
C declines suggesting B, and the Owner routes to B. What stops it is remembering
that *"B said not mine"* — a fact the Manager stated, not a distance travelled.

**Why it closes.** An own question leaves its origin at most once (S1). A routed
question never moves except back to the Owner (S2, S3). Each return to the Owner
adds a Manager to the declined set, and the Owner routes only outside it (S4);
there are finitely many `decide` holders, and only they are eligible — **a
suggestion naming a Manager without `decide` is ignored**, like one naming a
Manager that declined. So the Owner routes a question at most D times, where D is
the number of `decide` holders. Counting deliveries between parties: at most one
relay by the origin, one decline by a decomposer, D routes each followed by at
most one return, and one final escalation — **at most `2D + 3`**, and `2D + 2`
without a decomposer. An Owner change is not a delivery: the record stays where
it is (§8.7.1). The bound is **for tests to assert, not a limit the code
enforces**.

**Checked against the cases:**

| Route | Why it stops |
|---|---|
| Worker → Manager M → Owner → M | M receives it routed and cannot relay. It answers, escalates, or declines to the Owner, which will not route to M again |
| Manager A → Owner → Manager B | B receives it routed and cannot relay — closed |
| The loop from the brief: M relays → Owner distributes → best match is M | Not a loop. M now holds a **routed** question: if it is M's subject, M answers or asks its person (its own relay strategy did not try — `relay-to-owner` sends first and never looks); if not, M declines and the Owner routes past it |
| B declines "try C" → C declines "try B" | S4: B is in the declined set, the suggestion is ignored, the next eligible match or the Owner's person |
| Executor E → decomposer P → P declines → Owner → M times out → no eligible match | `relay-to-decomposer` is E's one relay; P's decline goes to the Owner (S3); the timeout is a decline (`reroute`); S4 leaves nobody; the Owner's person, with P's and M's declines attached |
| Executor E → decomposer P, and the question shows the plan is wrong | Closed as *superseded*; the decomposition returns to plan review; repeat returns are budgeted (RL-10) |

**Distributing back to the origin is allowed, deliberately.** Under `team`, a
Manager relays without trying to answer, so the Owner may find the origin is the
best expert — its own Worker asked about its own area. Excluding the origin would
route the question to someone who knows less.

### 8.7.1. The Owner is a Manager too

**The Owner is a distribution candidate on the same terms as every other
Manager** — its declared expertise, its `decide` duty, its place in the stable
order (§8.4). Selecting itself delivers the question to its own Manager side as a
**routed** question: answer, escalate to its person, or decline — and a decline
adds it to the declined set like anyone else.

The reason is failover. Owner is an elected role, while expertise belongs to a
Manager. If the Owner were excluded from distribution, then electing the billing
expert Owner would remove billing expertise from routing, and billing questions
would go to someone less qualified **because of an election**. Who answers must
not depend on who currently holds the lease.

Three cases that follow, stated rather than left to the implementation:

- **An own question on the Owner's Manager** is governed by its `questions`
  strategy like any Manager's. `relay-to-owner` there delivers directly to
  `owner-questions`, with no message.
- **The Owner changes while a question is in flight.** The question record —
  source, declined set, suggestions — lives in the state layer, not in the old
  Owner's session, so the new Owner resumes routing it under the same project
  `owner-questions` value (§8.4) with the same declined set. Nothing is re-asked.
- **A Manager that relayed a question and then becomes Owner** before it is
  answered routes it under `owner-questions`, and may select itself; the
  question's source for that delivery is routed, and its earlier relay still
  counts as its origin's one relay (S1).

### 8.7.2. Refused tasks, and configurations worth a warning

**Refused tasks use the same shape.** `task-refused: reroute` never offers a task
to a Manager that already refused it; with none left, the task is held and
reported.

**Worth-knowing configurations, caught statically.** Valid, and doctor says so:
`distribute-by-expertise` with no Manager declaring expertise (every question
reaches the Owner's person); `relay-to-decomposer` on a Manager without `execute`
(it never has a decomposer, so every question it relays goes to the Owner);
exactly one `decide` holder (distribution has one candidate, so every decline
ends with the Owner's person); `wait` with no timeout of
its own (a question can wait indefinitely, visibly).

### 8.8. Discoverability — otherwise they are magic strings with extra steps

**One registry in code** holds every subject, value, meaning, kind, requirement
and profile. Every surface below renders from it, so the description of a value
cannot drift from the code that enforces it. A test asserts every value has a
meaning and every profile sets every subject.

| Surface | What it shows |
|---|---|
| **`rite help strategies`** | Every subject, its values and meanings, and which profile chooses each. A topic on the existing `rite help` — **not a new command**; the CLI surface is already wide |
| **`rite doctor`** | Per subject, and per Manager for `questions`: the resolved value, its source, its meaning; the notes from §8.3 and §8.7.2; which subjects have no effect yet in this project and why |
| **`rite status`** | Open questions: source, age, who holds it, declines with suggestions; held tasks and why; `wait` with its age; **the pull list of escalations waiting for this person, with the budget remaining in the window** |
| **`rite init`** | One question, on an Owner's init, in the Role section: *"Who works on this project? — just me / a small team / an organisation"*, each option saying in plain words what changes. Only profiles this build supports are selectable — `solo` alone until SPEC §4 exists — with the others shown as not yet available. **No strategy vocabulary on screen.** A Manager's init takes the project's profile from committed config |
| **Parse errors** | The valid values with their meanings (§8.6) |
| **Generated `CLAUDE.md`** | Each resolved strategy as an instruction — *"A question you cannot settle from the spec: send it to the Owner."* A session cannot follow a policy it cannot see, the same reason the phase table is there. The harness gets the same sentence in its prompt; a `human` Manager gets it in `rite status` |

### 8.9. Escalation is a rate-limited resource, and the limit is a person

Everything above treats "ask the person" as the safe terminal: when in doubt,
escalate. **That is only safe while the person can keep up.** Today a session
generated approval prompts faster than Robert could clear them and locked him out
of his own machine — on mobile the approval UI occupies the same channel he would
have used to stop it, so the flood defended itself. He happened to be near the
computer.

That is not an anecdote about one client; it is a property of this design. Three
Managers escalating freely, or a small-model tier escalating whenever it is
unsure, produces the same failure with the same mechanism: **a person buried in
requests, unable to act on any of them, including the request to stop.** Nothing
in §§8.1–8.8 bounds the rate at which `escalate-to-user` can be consumed, and a
strategy set that does not bound it is specifying a denial-of-service against its
own user.

**The resource belongs to the person, not the Manager.** Budgets per Manager
multiply by the number of Managers, which is the failure. So the budget attaches
to the **recipient**: every escalation aimed at one person draws on one budget,
whichever Manager raised it, and the Owner's-person fallbacks (§8.7's S4, an
exhausted route) draw on it too.

**Three mechanisms, each doing different work:**

| Mechanism | Rule | What it prevents |
|---|---|---|
| **Ceiling** | **At most one escalation outstanding per person at a time**, plus a ceiling per rolling window (default 6/hour, recorded beside the value) | The flood. One thing is asked at a time; nothing can render a second prompt over it |
| **Aggregation** | Questions sharing a ticket, a decomposition or an expertise tag combine into **one** escalation with parts, answered once | Five near-identical prompts from one bad decomposition |
| **Pull, not queue** | Beyond the ceiling, escalations **do not become prompts**. They are held and listed in `rite status`, and the person takes them when ready. A held escalation never expires into nothing — it expires into held | A queue that is itself the flood: a hundred waiting prompts is the same lockout deferred |

**And the constraint the lockout actually teaches, which is broader than
strategies: rite must never occupy the channel a person would use to stop it.**
An escalation is delivered as one outstanding request plus a pullable list; it
must not be able to take over the surface where `rite stop`, `rite pool retire`
or a Ctrl-C lives. Any future delivery mechanism — a mobile client, a chat
integration — inherits this rule, and it is the one property here that is not
configurable.

#### 8.9.0. An Owner that always escalates, and the ceiling (RL-55)

Two of Robert's answers meet here and neither mentions the other: **routing is
an Owner duty**, and **the escalation budget belongs to the person**. An Owner
declaring `owner-questions: escalate-to-user` — "always ask me rather than
route" — consumes that budget for every question in the project, and §8.5 is
explicit that rite never substitutes another value when one cannot be carried
out. So there is no quiet downgrade to `distribute-by-expertise`.

What happens when the ceiling is reached is therefore **`escalation-exhausted`,
applied to the router itself**, and that is a different event from a Manager
hitting it:

| Its value | What it means for one Manager | What it means for the Owner-as-router |
|---|---|---|
| `park` | Hold this question, take other work | **Every question in the project holds.** Nothing else holds `route`, so nothing else can move them |
| `stop` | Take no new work until answered | The project's router stops routing *and* stops working |
| `proceed-with-assumption` | Record the assumption, mark the work provisional | The only value that keeps the question path moving, at the price of provisional work the gates must clear |

**So `escalate-to-user` makes `escalation-exhausted` load-bearing for the whole
project rather than for one Manager**, and RL-48 tightens it further: an
escalation nobody answers resolves the same way, so the ceiling arrives sooner
than a reading of the budget alone suggests — unanswered questions consume it
exactly like answered ones.

`rite doctor` reports the combination. It is not refused: a project that wants
every question in front of a person, and accepts a stall when that person is
away, has said something coherent. It should say it knowingly, which is what a
doctor row is for. The pairing that does not stall is `escalate-to-user` with
`proceed-with-assumption`; the pairing that does not consume the budget at all
is `answer-or-escalate`, which asks the Owner's own judgement first and is the
`solo` default for that reason.

#### 8.9.1. `escalation-exhausted` — what a Manager does when it may not escalate

A Manager that wants a person and cannot have one now needs a defined move, and
"ask anyway" is exactly what the budget exists to refuse. A sixth subject:

| Value | Kind | Requires | Meaning |
|---|---|---|---|
| `park` | hold | — | Hold the question, mark its ticket blocked on it, and take other work. The question joins the pull list |
| `stop` | hold | — | Hold the question and take no new work in this Manager until it is answered — for a project where proceeding around an unanswered question is unacceptable |
| `proceed-with-assumption` | terminal-ish | `decide` | Record an explicit assumption on the ticket with the question attached, mark the work **provisional**, and continue. Plan review and `integrate` see the assumption and the question; a provisional branch cannot be integrated without the assumption being resolved |

Default `park` in every profile. `proceed-with-assumption` is opt-in, needs
`decide`, and **is not a licence to guess**: the assumption is written down where
a reviewer must read it, and the gates (RL-6, RL-7, RL-8) still hold — RL-20's
rule that no strategy disables a gate applies here more than anywhere. An
`executor` cannot hold it, and cannot escalate directly either: its values are
relays (§8.2), so a small model's uncertainty reaches a person only through a
Manager that holds `decide`. That is a property worth keeping when the strategy
set grows.

**Parking is not silence.** A parked question is visible with its age in
`rite status` and counts toward the failure clustering of RL-10: a decomposition
that parks questions repeatedly is a suspect decomposition, not a patient one.

#### 8.9.2. An escalation that is never answered (RL-48)

Everything above answers "the Manager may not escalate". The commoner case is
that it **did** escalate and nobody replied, and this section had no answer for
it. An unanswered question is the normal case, not the exception: people sleep.

**A sent escalation carries a deadline, and on expiry it resolves into an
`escalation-exhausted` value — the same three — rather than continuing to
wait.** Waiting is not a state a Manager may hold indefinitely, because waiting
and working are indistinguishable from outside.

Two mechanism-level rules follow, and neither is negotiable:

- **A Manager never blocks on an interactive prompt.** A prompt that only a
  person can dismiss converts an unanswered question into a frozen session, and
  a frozen session holds its claims, its lease and its in-flight count while
  reporting nothing wrong. Escalation is a message plus a deadline, never a
  blocking call.
- **A waiting escalation is visible with its age**, exactly as a parked question
  is, and ages into the same failure clustering. "Waiting on a person since
  21:22" is a status; silence is not.

**Measured, 2026-09-17/18, on this fleet.** A session escalated through an
interactive prompt at 21:22 and stopped there permanently. Nothing in its
output distinguished it from a session mid-task: no heartbeat gap, no error, no
timeout. It was found by a person noticing it had not spoken, which is the
detection mechanism this design exists to replace.

---

## 9. Dependencies — what has to exist first

**rite local needs Phase 2's interface-level core, not its cross-machine core** —
provided every tier runs on one machine (Q1).

| Needed | Why | Status |
|---|---|---|
| State-layer interface + local backend (P2-1a) | Several Managers coordinate even on one host; **the only thing tier code may call for coordination** (§6.5, D-20) | not built |
| Manager status schema (P2-0b) and unknown-key round-trip (P2-1d) | Engine and duties live on the Manager record; an older writer must not drop them | P2-0b `[NEEDS DECISION]`; P2-1d not built |
| `managers:` in config (P2-0a) | Where a Manager is declared at all | `[NEEDS DECISION]` |
| Heartbeat with in-flight count (P2-4a) | Free-capacity routing | not built |
| Refusal (P2-4c) | A mis-routed subtask needs somewhere to go; `task-refused` | not built |
| SPEC §4 expertise routing | `distribute-by-expertise`, the PM's business questions | designed, not built (Q6) |
| Durable question record | Source and declined set must survive a session ending and an Owner changing; inter-machine scope once relayed (§6.5) | new; built on P2-1a (RL-T21) |
| Spec digest slices | A small model cannot read the spec, or follow a pointer into it | parsing landed; command not installed |
| Git CAS and leader election (P2-1b/c, P2-2) | **Only if tiers span machines** | not built |

---

## 10. Where the existing design conflicts, and which is better

| Conflict | Existing | This design | Better, and why |
|---|---|---|---|
| Manager identity | SPEC §2.2: "one per Claude account" | A Manager is an identity on a machine with a declared engine; a local Manager has no Claude account | **This design.** "Per Claude account" was true when every Manager was a Claude session; it is a description, not a constraint. |
| Assigning to Workers | SPEC §2.3: the Owner may label a Worker directly | Managers only, once engines or duties differ (RL-4) | **This design** when tiers exist; SPEC §2.3 unchanged for a one-Manager project. Direct Worker labels bypass routing — the routing design's Seam 3. |
| Engine as a capability | Routing design: capabilities are open, consumable, per machine | Engine is its own closed attribute | **This design.** A GPU is consumed by a task; an engine decides which *gates* apply. Keying enforcement on an open string would let a typo skip plan review. |
| Where routing runs | P2-6 recommends agent-side routing | Duty routing in code | **Both, split by what is routed.** Decisions are judgement and stay agent-side as P2-6 says; which pipeline stage gets a task is mechanical and gate-bearing, so it must be enforced in code. |
| Who pushes | SPEC §5.1.1: pushing stays a human action; Claude Worker sessions push today | Local engines never push; `integrate` does | **Consistent** — the rule is about rite's code, and it holds. |
| Coordination substrate | D-20: git by default, interface abstracted; D-21: Redis dropped as a default on infrastructure cost | Unchanged, with tier code forbidden from naming a backend and a scope split (§6.5) | **Consistent, and this design tightens it.** D-20 made substitution possible; tiers make it likely enough to need a test that keeps it possible. |
| Context per review | D-29 caps Workers per Manager | Step review is stateless per subtask (RL-9) | **Complementary.** D-29 bounds report count; small context windows also need each review bounded in size. |
| Expertise tiebreak | SPEC §4: random between equal matches | Stable: fewer in-flight questions, then name (§8.4) | **This design, where a strategy distributes** — a replayable route is debuggable, and preferring the less loaded expert keeps the spreading random was for. SPEC §4's own use can stay random. Q9 |
| Unanswered expert | D-6: timeout, then reroute | Timeout is a decline without a suggestion; the declined set forbids re-asking (S4) | **This design** — it is D-6 with the one missing rule that makes rerouting finite. |

---

## 11. Decisions

**This is the register text for adoption, not a summary to read.** Each row
restates in one line something §§3–10 argue in full, because SPEC's register (SPEC §13) needs
exactly that shape (see [`RITE_LOCAL_SPEC_PATCH.md`](RITE_LOCAL_SPEC_PATCH.md)).
On a first read, skip it: §0 says what needs deciding and §12 says what would
show it wrong.

| ID | Decision | Choice | Why |
|---|---|---|---|
| RL-1 | How a Manager's role is modelled | **Two independent axes — engine and duties — not a single tier field** | A tier conflates model size with purpose. The PM has a purpose and no model; a GPU box has a model and ordinary duties. One field cannot express either. |
| RL-2 | Tiers | **Presets over the axes, not a type** | New combinations — a Claude executor, a large model that also integrates under human review — need no code. |
| RL-3 | Duty vocabulary | **Closed and rite-defined**, unlike expertise and capabilities | rite enforces gates keyed on duties. An open set would let a misspelt duty silently skip plan review. |
| RL-4 | Fungibility | **Engine and duties are Manager attributes; every Worker under a Manager is identical; assignment targets Managers once they differ** | Adopts the routing design's "fungible within a Manager". Keeps SPEC §5.3.4's reason intact: no assignment reasoning about *Workers*, and no derived differences. |
| RL-5 | Routing order for a task | **Duty, then capability and permission, then free capacity. Decisions route separately, by expertise (SPEC §4)** | Three routers with three tiebreaks, as the routing design argued; duty is added in front because it decides which stage a task belongs to. |
| RL-6 | Who gates a decomposition | **A `plan-review` holder on a different Manager, and a different engine, from the decomposer — approval before any subtask is released** | The decomposer reviewing its own plan's output shares every blind spot of the plan. Catching a mis-slice before execution is the cheapest place to catch it. `rite doctor` refuses a config where no such holder exists. |
| RL-7 | When a subtask is done | **Only when its mechanical verify passes. Plan review rejects a verify that cannot fail** | A check that is not a model's opinion. Rejecting un-failable verifies closes the easiest way to fake a pass. |
| RL-8 | The whole-versus-parts failure | **Recomposition verify: the parent ticket's verify runs on the combined branch; a failure returns to plan review, not to the decomposer** | Catches the confidently-wrong decomposition, whose parts all pass. Sending it back to the decomposer would ask the author of the mistake to find it. |
| RL-9 | Step review | **Advisory, and stateless: a fresh context with the plan item, the diff and the verify output** | It shares the plan's blind spots, so it cannot gate. Statelessness keeps each review inside a small context window (D-29). |
| RL-10 | Repeated failure | **A per-subtask attempt budget escalates up; a threshold of failed subtasks marks the decomposition suspect and returns it to plan review; returns to plan review per parent ticket, from any cause, are budgeted and escalate to the lead's person when spent. Only work counts — infrastructure faults are not attempts (RL-47)** | Retrying a bad plan harder only spends local compute proving it is bad. |
| RL-11 | Pushing | **Local engines commit to a local task branch and stop. `integrate` — a Claude session or a person — pushes and opens the PR** | SPEC §5.1.1 forbids rite's code a push; the harness is rite's code. It also places the terminating check before anything leaves the machine. |
| RL-12 | What the harness is for | **Local engines only. The `claude` engine keeps spawning the real `claude` CLI; rite never calls an Anthropic API** | The sanctioned path stays exactly as it is. The harness has no Anthropic dimension to get wrong. |
| RL-13 | Build or adopt the agent | **Adopt — opencode or aider under yoloAI, against a local endpoint — and orchestrate around it. Build only if the spike shows neither can be held to the contracts** | A reliable small-model tool loop is the riskiest thing here, and two already exist inside a sandbox rite already drives. |
| RL-14 | Where inference runs | **On the host, behind an OpenAI-compatible endpoint; tool execution sandboxed** | Keeps GPU access out of the sandbox problem, and requires an endpoint rather than a runtime. |
| RL-15 | A non-coding Manager | **First-class: engine `human` (or `claude`), duties without `execute`. Business questions reach the PM through SPEC §4 expertise, not a duty** | "Who owns this area" is expertise; "what is this Manager for" is duties. The PM needs both and does not need a model. |
| RL-16 | Phase 2 dependency | **Interface-level core only, if all tiers share a machine** | Several Managers must coordinate, which needs the state interface — but not git CAS or election unless machines differ. |
| RL-17 | What an executor reads | **The spec-digest slice for its subtask — never the spec, and never a pointer into it** | D-52's pointer assumes a context large enough to read the file. A small model's does not. |
| RL-18 | Planners who also do work | **Their work is reviewed by a different instance: Claude work gets a separate Claude terminating check; planner work gets step review from a different planner, or escalates. A planner never executes a subtask from its own decomposition** | SPEC §7.1's "never staffed by whoever proposed them", applied across tiers. |
| RL-19 | Whether this pays off | **Measured per tier, net of rework (D-39) — not assumed** | "Local tiers save Claude quota without costing delivered work" is the whole claim, and it is a hypothesis. |
| RL-20 | Recurring situations | **Named strategies, a closed set of values per subject. No strategy can disable a gate or a fail-closed property** | More than two answers per subject; booleans can state contradictions. Flexibility is in routing, never in safety. |
| RL-21 | Grouping | **Project profiles `solo`, `team`, `org`, with per-subject override; `questions` defaults split by whether the Manager holds `decide`. Named differently from Manager presets. No block means `solo`, which is today's behaviour. A profile whose values need an unbuilt feature is refused, not weakened** | One choice for most projects; nothing changes for existing ones; a preset and a profile shape different things. |
| RL-22 | Strategies and engines | **A value requires duties, never an engine; the engine decides only delivery** | The PM, a Claude session and a local model all hold `decide` differently. Keying on an engine would build in an assumption about what a model can do. |
| RL-23 | Where a strategy lives | **Routing subjects in project config only. `questions` has a project default, an `allowed` set, and a Manager override within it** | A routing policy on the Owner's Manager would change silently on failover. What a Manager does with its own questions is a legitimate personal call, and `allowed` lets a project remove it. |
| RL-24 | Resolution | **Manager override → project override → preset default → profile → `solo`: explicit before implicit. The result must be valid, satisfy its requirement and be allowed, whatever its source — otherwise an error, never a substitute** | A substituted value is a question going somewhere nobody chose. |
| RL-25 | Unknown values | **Refused at parse through the same mechanism as unknown keys, with a suggestion and the valid set with meanings. On Manager records, preserved on write and refused when acting. A newer value in committed project config stops an older rite, which gives up the Owner lease if it holds it** | One failure shape for keys and values. Round-trip (P2-1d) and fail-loud are compatible once writing and acting are separated. |
| RL-26 | Termination | **By source, not hop count. Only an own question can be relayed, once, by its origin. A routed question is answered, escalated, or declined to the Owner. The Owner never routes to a Manager that declined** | A hop count approximates the rule; source is the rule, and a relayed-back question is wrong on the first attempt. Remembering declines is what makes the single router's re-routing finite. |
| RL-27 | Refused tasks | **The same never-re-offer rule; with no eligible Manager left, hold and report** | One rule for both kinds of routed thing. |
| RL-28 | Discoverability | **One registry renders parse errors, `rite help strategies`, doctor, the init question and CLAUDE.md** | A named value is better than a boolean only if a person can find what it means where they meet it. |
| RL-29 | Recipient that is not the expert | **Decline to the Owner, optionally with a suggestion (expertise tag or Manager name). Never sideways; answering outside its subject is not an action; the suggestion is advisory** | Keeps expertise routing without a second router. "Answer it anyway" is the worst outcome on a product whose value is not fabricating. |
| RL-30 | The Owner as a candidate | **The Owner is distributed to like any Manager and handles its own routed questions under the same actions** | Owner is an elected role; expertise is a Manager's. Excluding the Owner would let an election change who answers. |
| RL-31 | Decomposer clarifications | **A decomposer may clarify its own decomposition without `decide`; a clarification cannot change scope or verify; a question that shows the plan is wrong returns the decomposition to plan review** | Cheap local answers for the commonest executor question, while the author's blind spots stay behind the gates that do not share them. |
| RL-32 | Owner eligibility | **Only a Manager holding `decide` and `board`, on an engine other than `human`, may be Owner. A project where none does is a parse error. With every eligible Manager down there is no Owner, and routing waits visibly** | Project-only policy survives failover only if every possible Owner can apply it. SPEC §2.3 already gives the Owner both jobs. |
| RL-33 | Answers | **A question closes on its first answer, including a late one from a timed-out expert; a differing later answer is a conflict shown to the Owner's person. A question the plan invalidates closes as *superseded*, with every other open question on that decomposition. A late decline from a Manager already declined routes nothing** | Every question needs a defined end, and no answer is dropped or silently replaced. |
| RL-34 | Undeclared duties | **A Manager with no preset or duties holds every duty in a one-Manager project; with a second Manager, every Manager must declare them** | Today's single session keeps working unchanged, and routing by duty never guesses what a Manager is for. |
| RL-35 | Coordination backend | **Tier and harness code use the D-20 state-layer interface only and never name a backend; a test enforces it. Git as version control is unaffected** | D-20/D-21 already make the backend substitutable. Tiers raise the coordination rate, which is the real half of the git-versus-Redis argument; a rate problem must stay a configuration change. |
| RL-36 | Where coordination lives | **Two scopes — intra-machine (high rate, local backend) and inter-machine (contended, durable). A backend belongs to a scope, never to a tier. A question moves to the inter-machine scope when it leaves its Manager** | Tiers coordinate with each other constantly, so they cannot hold different stores; what differs is what must survive a machine dying. |
| RL-37 | Escalation is bounded | **Anything that reaches a person draws on a budget belonging to that **person**, shared by every Manager: one escalation outstanding at a time, plus a ceiling per rolling window** | A per-Manager budget multiplies by the number of Managers, which is the failure. A session once locked a person out of their own machine by asking faster than they could answer — on mobile the prompts occupied the channel they would have used to stop it. Unbounded escalation is a denial-of-service against the user. |
| RL-38 | Related questions | **Questions sharing a ticket, a decomposition or an expertise tag aggregate into one escalation with parts** | One bad decomposition otherwise produces five near-identical prompts, and the person pays for the decomposition's error in attention. |
| RL-39 | Past the ceiling | **Escalations are held and pulled from `rite status`, never queued as prompts; a held escalation expires into held, never into discarded** | A hundred waiting prompts is the same lockout, deferred. Pull keeps the person in control of when they are interrupted; holding rather than discarding keeps the question. |
| RL-40 | Wanting to escalate with no budget | **A sixth subject, `escalation-exhausted`: `park` (default), `stop`, or `proceed-with-assumption` — the last requires `decide`, records the assumption on the ticket, and marks the work provisional so plan review and `integrate` must see it** | "Ask anyway" is what the budget exists to refuse, and silence is worse. Proceeding is legitimate only when what was assumed is written where a reviewer has to read it. |
| RL-41 | The stop channel | **rite never occupies the surface a person would use to stop it: one outstanding request plus a pullable list, never a stream that can bury `rite stop`. Not configurable** | The lockout was not caused by the number of prompts alone but by prompts owning the only channel back. Any future delivery mechanism inherits this. |
| RL-42 | Declaring a local engine | **A `local:*` Manager declares `endpoint`, `model` and `agent`; `<class>` stays a label. `rite doctor` probes each. No secret in config — a key, if needed, is a keychain entry named by config (SPEC §10)** | `local:large` names a tier, not a runtime, and two projects' "large" differ. Probing rather than locating is what already makes `doctor` honest about sandboxes. |
| RL-43 | Instruction channels | **A tier may not write the instructions of a tier that reviews it. Reviewer context is built by rite from its own templates, the project spec and artifacts read as evidence; learned content, if it ever exists, reaches workers only** | The earlier prototype fed learned conventions to workers and reviewers through one channel, and a rule like "don't re-litigate style in review" cut review rounds by cutting review depth. Segregate the channel, not the wording. |
| RL-44 | A metric that flatters | **Fewer review rounds is not a success measure. Any change that reduces them is judged against defects caught later, net of rework (D-39)** | A leaky instruction channel improves round counts first, which is exactly what a reviewer that stopped looking also does. |
| RL-45 | Claims carry falsifiers | **Every hypothesis in this design ships with a threshold written down before the spike that tests it, and the action to take when it is missed (§12 of this note)** | A threshold chosen after the numbers arrive is a rationalisation. This is also the only defence against the sunk-cost version of "keep going because it is built". |
| RL-46 | Composing the branch | **Accepted subtask branches are applied in decomposition order, and a conflict between them returns the decomposition to plan review with the paths named — composition never resolves a conflict** | Two subtasks that conflict were two subtasks that should not have been separate: the conflict is evidence about the plan. Resolving it in place hides a planning error inside a merge nobody reviews. |
| RL-47 | Infrastructure is not failure | **The harness distinguishes work that was wrong from work that never ran. A crashed agent, an unreachable endpoint, an unloaded model or a verify that errors consume no attempt; the Manager stops taking work and says why** | RL-10 escalates on failed attempts, so counting infrastructure faults as attempts would spend a decomposition's budget proving a machine was off — and would read, in RL-13's threshold, as a model that cannot work. |
| RL-48 | An escalation nobody answers | **A sent escalation carries a deadline and resolves into an `escalation-exhausted` value on expiry; a Manager never blocks on an interactive prompt; a waiting escalation is visible with its age** | §8.9.1 answered "may not escalate" and had nothing for "escalated, no reply", which is the common case. Measured 2026-09-17/18: a session escalated through an interactive prompt at 21:22 and froze permanently — holding its claims, lease and in-flight count while indistinguishable from a session mid-task. A person noticing the silence was the only detection. §8.9.2. |
| RL-49 | Composing the next instruction | **A Manager publishes what it has completed to the message log, and the coordinator composes from the log rather than from what it remembers asking for** | Measured over 36 hours on this fleet: re-sent instructions were **10.8% of all context**, the largest avoidable cost — larger than anything the spec digest saves, and silent, because the Manager either repeats the work or argues and neither is an error. The log and heartbeat already exist; the rule is that the instruction is composed from them. §6.4.1. |
| RL-50 | Session lifetime | **No rotation on turn count, and no session-age quota. Deliberate compaction is the lever** | Measured on the same fleet: late turns are CHEAPER than middle ones, because compaction caps context growth; the expensive band is the middle. A recycle-on-N-turns rule would retire sessions at their cheapest and pay the re-warming cost every time. Nothing in this design may assume a long session degrades. |
| RL-51 | When a Manager must declare its duties | **From the moment any Manager declares an engine or a duty — not from the moment a second Manager exists** | RL-34 as written refuses a `managers:` list of two bare names, which is what v0.4.0 shipped and what existing projects have on disk; adopting rite local would have been a parse error on upgrade for every multi-machine project. Routing cannot guess only once there is something to route between, which is §4's own argument for the one-Manager case. Found by building RL-T3 against the real config, not by reading. §3.3. |
| RL-52 | Who decides where a question goes | **`route` is a duty in the closed vocabulary, held by the Owner** — answer, distribute by expertise, or escalate | Robert: the Owner is responsible for routing questions, and may hold different question strategies from the Managers under it. Naming it a duty makes the single-router property something rite enforces rather than something that emerges: a gate can key on it, `rite doctor` can refuse a project whose Owner cannot route, and "a question relayed by the Owner is not relayed onward" has a holder in the literal sense. Owner eligibility becomes `decide` + `board` + `route`. §3.2. |
| RL-53 | Whether the Owner's strategy follows the machine or the role | **The role.** `owner-questions` is project-level and governs whatever Manager holds the lease; a Manager's own `questions` continues to govern its OWN questions while it is Owner | §8.4 already made routing subjects project-only so a failover cannot silently change policy — this states the promotion semantics that were left implicit. A promoted Manager adopts the project's `owner-questions` and keeps its own `questions`: two slots, never one merged set, so nothing about the previous Owner's configuration travels with the role and no behaviour changes silently at promotion. §8.4. |
| RL-54 | How every machine gets the same expertise table | **rite distributes the table; no resolver in code** (Robert's answer to Q6, the agent-side option) | A resolver computing "who knows about X" per machine can disagree with itself across machines, and the disagreement appears as questions going to different people depending on who asked. A published table is one artifact every machine reads, and a difference in it is a diff rather than a behaviour. |
| RL-55 | An Owner that always escalates, once the ceiling is reached | **It falls to `escalation-exhausted`, and because only the Owner holds `route`, that is a project-wide stall rather than a local one. `rite doctor` reports `escalate-to-user` paired with `park` or `stop`; it is not refused** | The intersection of two answers that do not mention each other. rite never substitutes a strategy value (§8.5), so there is no quiet downgrade to routing; and RL-48 means unanswered escalations consume the ceiling exactly like answered ones. A project may knowingly choose to stall rather than proceed on an assumption — it should choose it knowingly. §8.9.0. |
| RL-56 | What a ticket set optimised for claimability leaves out | **The wiring. For every mechanism, name its caller; if the caller lives in a file another ticket owns, that is a ticket.** Checked against the ticket list before anyone starts | Phase 2's set produced nine integration defects this way, and this set had three of the same holes — nothing started the harness, nothing called the router, nothing handed the branch to `integrate`. The cause is structural: integration is where claims collide, so a set written to avoid collisions writes it out. §11.1. |

| RL-57 | Whether an unattended tick may write to the shared board | **One switch, `coordination.assign_unattended` (default false), covering BOTH board arms — the Owner assigning to a Manager and the Manager distributing to its Workers — with Q9's three unambiguity rules on whether or not the switch is** | Robert answered Q9 "yes, with the three rules"; the question's own recommendation is the middle option, and the middle is what a project that has not decided needs, because the off position now SAYS it is off. One key rather than two: they are the same act, and allowing the first while forbidding the second produces tickets that move one step and stop. Built 2026-09-18. |
| RL-58 | Whether rule 3 needs a capacity field in the heartbeat | **No.** `schedule:` is committed config, so the Owner's `workers_at(schedule, minute)` is the same ceiling the receiving machine applies to itself. A published capacity becomes necessary only if machines may carry different schedules | The second time a claim about the heartbeat was made from inference rather than from the tree: first "`in_flight` does not exist" (it does), then "cross-machine still owes a capacity field" (it does not, at this config shape). Both were cheap to check and were not. `refusal.py`'s ⚠ about refusing "full" is still correct — that needs a Manager's OWN ceiling, which is a different number from the schedule's. |
| RL-59 | Two documents, one identifier | **A question id is scoped to its document, and a design that cites another document's question says which.** `Q9` is "may an unattended tick assign work on the board" in `PHASE2-OPEN-QUESTIONS.md` and "the expertise tiebreak" in this one | Found by building: the instruction said "Q9 plus RL-T32/T33" and this document's Q9 is a different question entirely. Both are live, both are cited by bare number, and picking the wrong one is a whole piece of work aimed at the wrong target. Exactly the citation-gate defect (CGT-1) in prose rather than in code — an identifier that resolves by coincidence. |

---

## 11.1. A lesson this plan learned twice (RL-56)

**A plan that optimises for one property leaves the seam where that property
breaks down unrepresented.**

This ticket set optimises for parallel claimability: keep logic in new
packages, touch shared files only at a call site, and never let two ready
tickets collide. Integration is exactly where claims collide — `cli/main.py`,
the pool, `assignment.py` — so the optimisation wrote the integration out.
Three tickets were missing and none of them was an oversight about a
mechanism; all three were the WIRING of a mechanism the set does contain:
nothing started the harness, nothing called the duty router, nothing put the
composed branch in front of the `integrate` holder.

Phase 2's set had the same hole and paid for it: nine integration defects,
every one a mechanism that shipped complete, correct, tested and called by
nobody. This set's own review caught one instance (RL-T30, "nothing built the
branch RL-T11 verifies") without asking the question of the pipeline as a
whole.

**The check that would have caught all four: for every mechanism, name the
caller, and if the caller is in a file another ticket owns, that is a ticket
too.** It is cheap, it is mechanical, and it runs against a ticket list rather
than against code — so it can be done before anyone starts.

The same shape has now appeared three times in rite's own checks, which is
what makes it a lesson rather than an anecdote: a real property guarded by a
cheaper proxy that correlates with it until it does not. The release-checksum
tests pinned a version instead of reading VERSION; the template-history guard
judged the working tree against released hashes; the citation gate reads a
section NUMBER and ignores which document was cited, so it fails on citations
to this design and passes citations that are wrong in the same way when the
number happens to exist in SPEC. Filed as `CGT-1` against the gate rather than
worked around here.

## 12. How we would know this is wrong

Half of this design is **invariants** — fungibility, never pushing, channel
segregation, and *that* escalation is bounded at all. Those are not falsified by
measurement; they are enforced by tests, and a measurement that seemed to argue
against one is an argument about cost, not correctness.

**Their settings are a different matter.** "One outstanding, six an hour" is a
number, not a principle, and the row below can move it. What no measurement may
do is remove the bound — a ceiling that can be measured away protects nobody on
the night it is needed.

The other half is **hypotheses**, and they are the ones that spend Robert's
evenings. Each gets a threshold **written down before the spike runs**, because a
threshold chosen afterwards is a rationalisation of whatever happened (RL-45).

| Claim | Load-bearing because | Measured by | Would be wrong if | Then |
|---|---|---|---|---|
| **RL-13** An adopted agent can hold a rite task loop on a small model | The executor tier is the whole point; building an agent loop is the largest thing here | RL-T0, ten pre-written tasks | **fewer than 7 of 10 complete with a passing verify**, or **any dishonest report** (claims success, verify fails) at all | Dishonest reporting kills the tier as designed — rite would be trusting a claim it cannot check cheaply. Below 7/10 but honest: keep the tier, shrink subtask size, re-run |
| **RL-17** A spec-digest slice is enough context for an executor | A small model cannot read the spec; if the slice is not enough, the tier cannot work from any context rite can give it | RL-T0, then RL-T9 in use | **more than one clarifying question per subtask**, sustained | The slice is the wrong granularity, not the model. Try per-subtask slices assembled by the decomposer before concluding the tier fails |
| **RL-6** Claude plan review is affordable | It is the gate the whole pipeline leans on, and it spends the quota the tiers exist to save | RL-T17, quota per delivered ticket | **plan review + integrate spends ≥ 70% of what single-tier Claude spent on the same tickets** | The economics are gone: the tiers are doing work Claude still has to read. Q3's threshold option (skip review under N subtasks) becomes the fallback, with the failure mode reopened knowingly |
| **RL-19** Local tiers save quota without costing delivered work | The whole claim | RL-T17, net of rework (D-39) | **delivered work per week falls**, or **rework rises enough to cancel the quota saved** | Stop. This is the decision the measurement exists to make, and "keep going because it is built" is the failure this row prevents |
| **RL-8** Recomposition verify catches what subtask verifies miss | With RL-46, the only gate on the confidently-wrong decomposition | RL-T17 | **it never fires** across a meaningful sample | Either decompositions are better than feared — good, and say so — or the parent verifies are too weak to fail, which is the same blind spot one level up (§5.5) |
| **RL-35/RL-36** The coordination rate is worth designing around | Justifies the two-scope split, which is real code | RL-T0's op count, then RL-T17 | **coordination costs < 5% of wall-clock time** at executor-tier rates | **RL-36 is withdrawn**: one backend serves both scopes, RL-T21 stops tracking which scope a question is in, and RL-T24 keeps only the no-backend-named test. The interface stays (RL-35) because it costs nothing; the split does not, so it goes |
| **RL-37** The ceiling's *numbers* let work continue | A ceiling that blocks work will be turned off, and then it protects nobody | RL-T25 in use: parked-question age, ticket-blocked time | **the median parked question waits longer than the person's actual response time**, i.e. rite is holding questions the person would have answered | Raise the ceiling or narrow aggregation — never remove the outstanding-of-one rule, which is what the lockout argues for |

**What none of these permit.** A result that is merely disappointing is not a
reason to widen a threshold after the fact; the row says what happens instead.
And a spike that cannot be run — no local model available, no GPU — reports that,
rather than reporting a guess (RL-T0 already says so).

---

## 13. SPEC edits on adoption

**The text is written out in [`RITE_LOCAL_SPEC_PATCH.md`](RITE_LOCAL_SPEC_PATCH.md)**,
quoted against `origin/main`. This section is the index to it.

- **SPEC §2.2** — Manager identity: an identity on a machine with a declared engine, not "one per Claude account".
- **SPEC §2.3** — assignment targets Managers once engines or duties differ; Worker labels remain for one-Manager projects.
- **SPEC §5.3.4** — "Workers are fungible" gains **"within a Manager"**, pointing here (as the routing design already asks).
- **SPEC §5.1.1** — local engines commit locally; `integrate` pushes.
- **New SPEC §2.8, Manager tiers** — §§3–7 of this note.
- **New SPEC §2.9, Strategies** — §8 of this note, including source rules S1–S4.
- **SPEC §4** — expertise ties broken by a stable order rather than randomly, where a
  strategy distributes (§8.4).
- **D-6** — a timeout is a decline without a suggestion; a Manager that declined is not asked again.
- **SPEC §2.3, SPEC §2.4 / P2-2b** — Owner eligibility requires `decide` and `board` on a non-`human` engine (RL-32); the lease loop itself is P2-2a.
- **D-20, D-21** — note that tier and harness code may call only the interface, and that the two scopes of §6.5 may use different backends.
- **SPEC §7.1** — the review convention gains the channel rule: a reviewer's
  instructions never come from what it reviews (RL-43).
- **SPEC §5.1.1** — add the escalation ceiling as a blast-radius property: rite may not saturate a person's attention, and may never occupy the channel that stops it (RL-41).
- **SPEC §13** — RL-1 to RL-47 as D-54 onward.

---

## 14. Open questions for Robert

Each says what it blocks.

1. **One machine, or separate machines per tier?** If the large model lives on a
   GPU box, rite local needs Phase 2's *cross-machine* core too (git CAS,
   leader election), not only the interface. **Blocks the dependency order.**
2. **Does the PM run a Claude session?** Engine `claude` with no `execute` means
   the PM talks to a session that routes for them; engine `human` means rite
   reaches the PM only through the board and comments. **Blocks RL-T2 and
   RL-T14.**
3. **Plan review on every decomposition, or above a size?** Every one is safest
   and spends Claude quota on the gate this design depends on. A threshold (say,
   fewer than three subtasks skips review) is cheaper and reopens the failure
   mode for small plans. **My recommendation: every one.** Blocks RL-T8.
4. **Should a human be able to approve a decomposition in Claude's place?** A
   fast path for trivial tickets. Does not change the gate's independence.
   **Blocks RL-T8's scope, not its start.**
5. **opencode or aider** — or no preference until the spike reports? **Blocks
   nothing; the spike decides unless you have a reason.**
6. **SPEC §4 expertise routing is not built.** The PM's business questions depend on
   it, and so do the `team` and `org` profiles (§8.2) — without it only `solo`
   exists. Build it as part of this, or ship `solo` and the PM with board and
   decisions only? **Blocks RL-T14's business-question half, RL-T21's
   distribution half, and `team`/`org`.**
7. **`managers:` in config (P2-0a) and the Manager schema (P2-0b) are both
   `[NEEDS DECISION]`.** This design
   needs it answered, and would propose `engine:`, `duties:`, `preset:` and
   `questions:` on each entry. **Blocks RL-T3.**
8. **When the planner is idle, does it take `execute` work before the executor
   tier, or only when the executor tier is saturated?** The second keeps the
   large model free for decomposition, which is its scarce job. **My
   recommendation: only when saturated.** Blocks RL-T15.
9. **Does SPEC §4's random tiebreak give way to a stable order?** §8.4 needs a replayed
   route to be the same route; SPEC §4 chose random to spread load between equal
   experts. A stable order that prefers fewer in-flight questions keeps the
   spreading. **My recommendation: yes.** Blocks RL-T21's distribution half.
10. **Is `org` right to remove personal escalation entirely?** It is the
    strictest line and makes the Owner a bottleneck for every question. The
    alternative allows `escalate-to-user` and relies on the person to route
    upward. **Blocks nothing; it is a table cell.**
11. **Should `wait` exist?** It is the only value that can hold a question with no
    end, and it is there for D-6's "this decision needs this person". Without it,
    such a decision is escalated to the Owner's person, who waits instead.
    **My recommendation: keep it, visible with its age in status.** Blocks nothing.
12. **Should `executor` default to `relay-to-decomposer`?** It answers most
    executor questions with local compute, at the cost that the plan's author
    answers questions about its own plan (§5.3's added row). The alternative,
    `relay-to-owner`, spends Claude quota on every "what did subtask 3 mean".
    **My recommendation: yes — a clarification cannot change scope or verify, and
    recomposition verify still judges the result.** Blocks nothing.
13. **Only a `decide` + `board` holder, not on a `human` engine, may be Owner
    (RL-32).** This constrains Phase 2's election, which does not currently look
    at duties or engines. It also interacts with Q2: a PM on `human` can never be
    Owner, so a project whose only `decide` holder is a `human` PM cannot parse. Without it, a failover can
    elect a Manager that cannot carry out the project's routing policy, and rite
    would have to substitute or stall. **My recommendation: yes.** Blocks RL-T21
    and adds a line to P2-2b.
14. **Which backend for the intra-machine scope (§6.5)?** P2-1a's local backend
    is the obvious default; SQLite buys atomicity and concurrent readers for a
    dependency Python already ships. This only matters once an executor tier's
    measured rate says it does. **My recommendation: P2-1a's local backend now,
    revisit on RL-T17's numbers.** Blocks nothing.
15. **Is one outstanding escalation and six an hour the right ceiling?** The
    shape is decided (RL-37); the numbers are a guess at one person's tolerance
    and are recorded beside the value so they are cheap to change. **My
    recommendation: ship those, and let `rite status` show how often the ceiling
    is hit.** Blocks nothing.
16. **Does human approval of a decomposition (Q4) draw on the same budget?** It
    is a request that reaches a person, so by RL-37 it should. Saying no means a
    project can be flooded with approval requests instead of questions. **My
    recommendation: yes — one budget per person, whatever the request is.**
    Blocks nothing; it is one line in RL-T8.
