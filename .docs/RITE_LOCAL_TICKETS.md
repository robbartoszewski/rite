# rite local — implementation plan

Claimable tickets for [`RITE_LOCAL_DESIGN.md`](RITE_LOCAL_DESIGN.md), whose SPEC
edits are pre-written in
[`RITE_LOCAL_SPEC_PATCH.md`](RITE_LOCAL_SPEC_PATCH.md). **Nothing built.** Sized for one worker, one sitting. Tickets cite `RL-n` decisions and
SPEC sections rather than restating them; if a ticket and a decision disagree,
the decision wins and the ticket is the bug.

Prefix `RL-T` is distinct from `P2-*` and `IMP-*`, so this batch can sit beside
them in one import without a local-id collision.

---

## ⚠ Read first — almost nothing here can start today

> ⚠ **CORRECTED 2026-09-19, against the tree rather than against this file.**
> Three of its load-bearing claims are no longer true, and they are the ones
> a reader acts on first:
>
> - **"Phase 2 interface-level core is not built"** — it shipped in v0.4.0.
>   `src/rite_ai/coordination/` carries the state layer, both backends,
>   heartbeats, assignment, refusal and election. The "stuck at round 3"
>   simulation below rests on that premise and does not hold.
> - **"Three tickets are startable now"** — RL-T0, RL-T3 and RL-T6 have
>   LANDED, with the duty router wired into assignment (27ba231), durable
>   decomposition (8d19410) and the harness body (4582bb8). `git log
>   v0.4.0..main` is the status; the per-ticket bodies below have NOT been
>   re-statused one by one, so read each as a description of the work.
> - **"Thirty-two tickets"** — there are thirty-six. RL-T32, RL-T33 and
>   RL-T34 (the missing start path, the router call, the integrate handoff)
>   were added on 2026-09-18 to a COPY of this file at
>   `~/AI/rite/.docs/RITE_LOCAL_TICKETS.md`, which is 904 lines to this
>   one's 765. `.docs/` is gitignored and these five `RITE_LOCAL_*` files
>   were force-added, so the working copy someone edits and the committed
>   copy a new session clones have drifted apart. **This file is the one a
>   stranger gets.** Reconciling them is a decision, not a merge: the other
>   copy has content this one lacks, and nobody has said which is canonical.

**Of thirty-two tickets, three are startable now, one more once Robert answers
Q1 and Q2, two more behind those — and then the plan is completely stuck**
(simulated at the end of this file).
Everything that builds rite local sits behind Phase 2 interface-level core that
is not built (§9 of the design), and several tickets behind questions for
Robert. That is the honest shape of this plan, not a scheduling accident:

| Startable now | Why |
|---|---|
| **RL-T0** spike: can an adopted agent hold a rite task loop on a local model? | No dependencies. **Gates the whole executor tier.** |
| **RL-T1** spike: can each sandbox backend reach a host model endpoint? | No dependencies |
| **RL-T2** SPEC adoption | Needs Robert's answers to Q1 and Q2 first, then no code |
| **RL-T18** strategy registry and project-level parsing | No dependencies — then RL-T20 and RL-T23 open behind it |

**The strategy tickets that can start now are the cheap, inert half.** They fix
the vocabulary, the fail-loud parsing and the discoverability surfaces. Nothing
routes a question until RL-T21, which waits on the same Phase 2 core as the rest.

**External blockers**, from `PHASE2_TICKETS.md` — interface-level core only,
per RL-16, unless tiers span machines:

| Blocker | Unblocks |
|---|---|
| P2-0a `managers:` config — `[NEEDS DECISION]` | RL-T3 |
| P2-0b Manager status schema | RL-T3 |
| P2-1a state-layer interface + local backend | RL-T4, RL-T21's question record |
| P2-1d unknown keys round-trip | RL-T3 |
| P2-4a heartbeat with in-flight count | RL-T5, RL-T6 |
| P2-4c a Manager can refuse | RL-T5 |
| Spec digest slices (`/spec-digest` installed) | RL-T7, RL-T9 |
| **P2-1b/c, P2-2a–P2-2e cross-machine core** | **Only if tiers span machines — Q1** |

### Where claims will collide

The project uses file claims, so two tickets touching one file is a conflict, not
a merge. The hotspots, and who owns what:

| File or surface | Tickets | Rule |
|---|---|---|
| `config/parse.py`, `test_config_roundtrip_is_total.py` | RL-T3, RL-T18, RL-T19, RL-T23 | **RL-T3 first, then RL-T18** — they are ready in the same round and must not be claimed together. RL-T18 keeps its logic in a new `config/strategies.py` and touches `parse.py` only at the call site |
| `rite doctor` / `rite status` output | RL-T3, T16, T19, T20, T21, T22, T25, T31 | **RL-T16 owns the section registry** both commands render through; everyone else adds a section rather than inventing a layout |
| `src/rite_ai/harness/` | RL-T6, T9, T28 | A new package. RL-T6 lands its shape first; T9 and T28 extend it |
| Manager schema rules (RL-32, RL-34) | RL-T3 **only** | RL-T19 cites them and does not re-implement them |

**The spikes must run first, and their answer can change this plan.** If no
adopted agent can hold the contracts in RL-T0, RL-T6 becomes "build an agent
loop" and roughly doubles; if a qwen3-class model cannot complete the loop at
all, the executor tier as designed does not exist and RL-T9 onward needs
rethinking before anyone claims them.

---

## Dependency order

```
RL-T0 spike: agent loop ─┐
RL-T1 spike: endpoint ───┼──────────────────────────────┐
                         │                              ▼
[P2-0a,P2-0b,P2-1d] ─► RL-T3 manager schema ──┬──► RL-T6 harness core ──┬──► RL-T9 execute ──┐
                         │                    │         ▲               │                     │
[P2-1a] ─────────────► RL-T4 decomposition ───┼─────────┼──► RL-T7 decompose ──► RL-T10 step review
                         │   artifact         │         │        ▲                            │
[P2-4a,P2-4c] ───────► RL-T5 duty router ─────┴──► RL-T8 plan-review gate                     │
                                                        │                                     │
                                                        └──► RL-T11 recomposition verify ◄────┘
                                                                  ├──► RL-T12 failure budget ◄── RL-T10
                                                                  │
                                                            RL-T13 integrate handoff
                                                                  │
                                                            RL-T17 measurement

RL-T2 SPEC adoption            — independent, after Q1/Q2
RL-T14 PM manager              — after RL-T3; business questions need SPEC §4 (Q6)
RL-T15 planner idle work       — after RL-T5, RL-T9, RL-T10
RL-T16 doctor/status           — after RL-T6
RL-T24 backend neutrality test — after RL-T6
RL-T25 escalation budget       — after RL-T21
RL-T26 channel segregation     — after RL-T8, RL-T10
RL-T27 scope-aware writes      — after RL-T4; may be withdrawn (design §12)
RL-T28 harness failure paths   — after RL-T6
RL-T29 withdrawing in-flight   — after RL-T8, RL-T9
RL-T30 compose the branch      — after RL-T9; feeds RL-T11
RL-T31 routing part 2          — after RL-T21, RL-T27

Strategies (design §8):
RL-T18 registry + project parsing ──┬──► RL-T19 Manager `questions` ◄── RL-T3
                                     │          │
                                     │          └──► RL-T21 question routing, S1–S4 ◄── RL-T4
                                     ├──► RL-T20 help topic + doctor block
                                     ├──► RL-T22 task-refused ◄── RL-T5
                                     └──► RL-T23 init question + CLAUDE.md
                                                │
                                     RL-T25 escalation budget ◄── RL-T21
```

**Critical path:** `RL-T0 → RL-T6 → RL-T9 → RL-T30 → RL-T11 → RL-T13`, with
RL-T3, RL-T4, RL-T5 and RL-T8 feeding it. RL-T30 (compose the branch) was missing
from the plan entirely until the ticket review: nothing built the branch RL-T11
verifies.

---

## Tickets

### RL-T0 — Spike: an adopted agent holds a rite task loop on a local model  ·  no blocker  ⛔ gates the executor tier

**Write the benchmark first, in this ticket, and commit it.** Ten real, small
tasks in a scratch repo, each with a scope, a verify command and a known-good
solution — pushed somewhere a re-run can fetch, because a spike whose tasks were
invented on the day cannot be compared with its own re-run. Budget the benchmark
as its own sitting; the runs are a second.

Then run **opencode** and **aider** against a host OpenAI-compatible endpoint
serving a qwen3-class model. **Run them on the host, unsandboxed** — this spike
measures the agent, not the sandbox, and RL-T1 answers the sandbox question
separately. Do RL-T1 first anyway: it is an hour, and if no backend can reach a
host endpoint, it changes what this spike should test.

Measure, per agent: **completed** (verify passes); **stayed in scope** (touched
only the named paths); **committed**; **never pushed**; **reported honestly**
(its own claim of success matches the verify result). Also time per task, and
**how many coordination operations a task of this size would generate** — the
cheap early read on §6.5's rate question, taken while local models are already
running.

**The thresholds are set before you run it** (design §12, RL-45), so write the
result against them rather than around them: **fewer than 7 of 10 completing with
a passing verify**, or **any dishonest report at all** (claims success, verify
fails), and the tier does not exist as designed. **More than one clarifying
question per subtask** indicts the spec-digest slice (RL-17), not the model.

**Output is a decision, plus the benchmark:** adopt which agent, or build
(RL-13). The benchmark is the one artifact that lands. If neither agent completes a meaningful fraction,
say so plainly — that changes the plan (see "Read first").

Decisions: RL-13, RL-14, RL-35, RL-45. Throwaway code only; nothing lands.

### RL-T1 — Spike: sandbox backends reach a host model endpoint  ·  no blocker  ·  parallel

From inside a yoloAI sandbox on each backend rite supports (seatbelt, docker),
reach an OpenAI-compatible endpoint on the host (Ollama on `localhost:11434`, or
`host.docker.internal` from Docker) and complete one request.

Record what works per backend, what address it needs, and whether any backend's
network isolation (D-30) blocks it. Decisions: RL-14.

### RL-T2 — Adopt the design into SPEC  ·  blocked by: Robert's answers to Q1 and Q2  ·  parallel

**The text is already written**: paste it from
[`RITE_LOCAL_SPEC_PATCH.md`](RITE_LOCAL_SPEC_PATCH.md), which holds every edit
§13 of the design asks for, quoted against `origin/main`'s current wording — the
SPEC §2.2, SPEC §2.3, SPEC §4, SPEC §5.1.1 and SPEC §5.3.4 replacements, the D-6 row, the D-20/D-21 notes,
and the full text of the new SPEC §2.8 and §2.9. Register rows are D-(53 + n) for
RL-n, D-54 to D-94; their *Why* column is the design §11 row, verbatim.

Check the quoted "replace this" text still matches `origin/main` before pasting —
it was captured 2026-09-17 and SPEC moves. Docs only.

### RL-T3 — Manager role schema: engine, duties, preset  ·  blocked by: P2-0a, P2-0b, P2-1d  ⛔ unblocks the most: five tickets wait on it

Add `engine`, `duties` and `preset` to each Manager entry, plus `endpoint`,
`model` and `agent` on a `local:*` engine (RL-42) — a credential key, never a
secret (SPEC §10). Worked examples: [`RITE_LOCAL_EXAMPLES.md`](RITE_LOCAL_EXAMPLES.md). Presets expand to
their duties (design §3.3); explicit duties override. Parse, serialise,
round-trip (`test_config_roundtrip_is_total.py` covers new fields).

`rite doctor` validation, each a reported problem:
- a `decompose` holder exists with no `plan-review` holder on a **different
  Manager and engine** (RL-6);
- `integrate` held by a `local:*` engine (RL-11);
- an unknown duty (RL-3);
- a second Manager declared with no preset or duties (RL-34);
- no Manager eligible to be Owner — `decide` and `board` on a non-`human`
  engine (RL-32).

Its tests move the one-Manager case forward unchanged: a lone Manager with no
duties declared holds every duty.

Decisions: RL-1, RL-2, RL-3, RL-6, RL-11, RL-32, RL-34, RL-42.

### RL-T4 — Decomposition artifact  ·  blocked by: P2-1a

A durable record, written through the state-layer interface: parent ticket,
and per subtask — scope, paths, verify command, cited decisions (`D-n`), status,
attempt count. Approval state and approver. Never only in a session's memory
(SPEC §2.0).

Decisions: RL-6, RL-7, RL-10.

### RL-T5 — Duty router  ·  blocked by: RL-T3, P2-4a, P2-4c

Route a task: duty → capability and permission (routing design) → free
capacity. **Targets Managers only** once engines or duties differ; a one-Manager
project keeps Worker labels (SPEC §2.3). A mis-routed task is refused back (P2-4c).

Tests: an idle eligible Manager never coexists with a queued task it could run;
no assignment names a Worker when tiers exist.

Decisions: RL-4, RL-5.

### RL-T6 — Harness core  ·  blocked by: RL-T0, RL-T1, RL-T3, P2-4a  ⛔ critical path

Orchestration around the agent RL-T0 chose, for `local:*` engines only (RL-12),
in a new package `src/rite_ai/harness/` — no edits to the pool or sandbox paths,
so this does not collide with CLI work.

**Done when, for one subtask, all of these hold:** an assignment is pulled
through the state-layer interface and nothing else; a workspace exists with the
claim taken; a heartbeat carries the in-flight count (P2-4a); the agent runs with
exactly the subtask and its spec-digest slice as context; **rite runs the verify
itself** and the agent's own claim of success is recorded but never trusted; a
commit exists on a local task branch; the result is reported and the claim
released.

Failure paths are **RL-T28**, deliberately not here — this ticket is the happy
path and stays one sitting.

**Never pushes and never opens a PR** — `test_blast_radius.py` must pass
unchanged. Never invokes `claude`. **All coordination goes through the D-20
state-layer interface** — no git call and no backend name anywhere in harness
code (RL-35); git is used only to commit to the local task branch.

Decisions: RL-11, RL-12, RL-13, RL-14, RL-35.

### RL-T7 — Decompose duty  ·  blocked by: RL-T4, RL-T6, spec digest

Given a ticket and its spec-digest slice, produce a decomposition (RL-T4) with a
verify command per subtask and cited decisions. Released to nobody — it goes to
plan review.

Decisions: RL-6, RL-7, RL-17.

### RL-T8 — Plan-review gate  ·  blocked by: RL-T4, RL-T5  ⛔ the error-propagation gate

A `plan-review` holder on a different Manager and engine approves or rejects a
decomposition, with reasons. **The reviewer's context is built by rite** — its
own template, the project spec, and the decomposition as evidence; no field of
the artifact becomes an instruction to the reviewer (RL-43). **No code path releases a subtask from an
unapproved decomposition** — test it by trying.

Reject criteria include: does not cover the parent; overlapping or missing
scope; a verify that cannot fail (apply `/ticket`'s "delete the fix and re-run").

Scope pending Q3 (every decomposition or a threshold) and Q4 (human approval).
If human approval ships, it draws on the escalation budget like any other request
that reaches a person (Q16, RL-T25).

Decisions: RL-6, RL-7.

### RL-T9 — Execute duty  ·  blocked by: RL-T6, RL-T8, spec digest  ⛔ critical path

One approved subtask: run it with its slice, verify, commit locally. The
executor reads the slice, never the spec or a pointer into it (RL-17).

Decisions: RL-7, RL-11, RL-17.

### RL-T10 — Step review, stateless and advisory  ·  blocked by: RL-T7, RL-T9

Per completed subtask, in a **fresh context** built by rite (RL-43): the plan
item, the diff, the verify output — as evidence, never as instructions. Accept, or send back with reasons. **Cannot gate** — a subtask
whose verify passed is not blocked by step review alone (RL-9).

Decisions: RL-9.

### RL-T11 — Recomposition verify  ·  blocked by: RL-T30  ⛔ critical path

When every subtask of a decomposition is accepted, run the **parent ticket's**
verify on the branch RL-T30 composed. On failure, return the decomposition to **plan
review**, never to the decomposer.

Test: a decomposition whose subtasks all pass and whose parent verify fails must
reach plan review.

Decisions: RL-8.

### RL-T12 — Failure budget and suspect decompositions  ·  blocked by: RL-T10, RL-T11, RL-T25

A per-subtask attempt budget escalates up a tier; a threshold of failed subtasks
in one decomposition marks it suspect and returns it to plan review; returns to
plan review per parent ticket — from recomposition failure, suspicion, or a
clarification that showed the plan wrong — are budgeted and escalate to the
lead's person when spent — **through RL-T25's budget** (RL-37), never as a
second path to a person. Clarification returns only exist once RL-T21 lands;
count them from then, without making RL-T21 a blocker. All thresholds configurable, with defaults recorded
beside the value.

Decisions: RL-10.

### RL-T13 — Integrate handoff  ·  blocked by: RL-T11  ⛔ critical path

A recomposed, verified local branch goes to an `integrate` holder — a Claude
session or a person — for the terminating check (SPEC §7.1), then push and PR.

**What rite builds here is the handoff, not the push**: the branch, its
decomposition, its verify results and its open assumptions are assembled into a
handoff record, and the `integrate` holder is notified. **rite's own code never
pushes and never opens the PR** (SPEC §5.1.1) — the session or person does that,
and `test_blast_radius.py` must still pass.
Claude-authored work gets a **separate** Claude session's terminating check
(RL-18).

Decisions: RL-11, RL-18.

### RL-T14 — Project manager Manager  ·  blocked by: RL-T3; business questions blocked by Q6

Engine `human` (or `claude`, per Q2), duties `decide` and `board`, no `execute`.
Declares expertise `business`. Board and decision duties work without SPEC §4; the
business-question half needs SPEC §4 expertise routing, which is not built.

Decisions: RL-15.

### RL-T15 — Planner idle work  ·  blocked by: RL-T5, RL-T9, RL-T10

A `planner` with no decomposition or step review pending takes `execute` work —
only when executor Managers are saturated, pending Q8, and **never a subtask from
its own decomposition** (RL-18); with one planner, only undecomposed tickets. Its work is step-reviewed
by a different planner or escalates (RL-18).

Decisions: RL-18. Stretch within this plan.

### RL-T16 — `doctor` and `status` for local tiers  ·  blocked by: RL-T3

`doctor`: the model endpoint answers; the configured model is loaded; the chosen
agent runs — all three declared per RL-42, so this needs the schema (RL-T3), not
the harness. Probe, do not locate.

**This ticket owns the shape of `doctor` and `status` output** — the section
registry both commands render through. Seven tickets add blocks to those
surfaces; they add a section, they do not each invent a layout. `status`: per Manager, engine, duties,
in-flight count, decompositions awaiting plan review.

### RL-T24 — Backend neutrality test  ·  blocked by: RL-T6, RL-T18  ·  parallel

A test in the shape of `test_blast_radius.py`: no module under the tier, harness
or strategy paths imports a git library or shells out to `git` for coordination;
the only coordination calls are the D-20 interface (RL-35). The local task-branch
commit is the one allowed `git` use, named explicitly in the test so a reviewer
sees the line rather than inferring it.

Also assert the half of the scope split that needs no routing (RL-36): the Owner
lease and board are never written to the local scope, and per-subtask events are
never published upward. **The question record's scope move is asserted in
RL-T21**, where it is implemented, so this ticket does not wait on it.

Decisions: RL-35, RL-36.

### RL-T17 — Measure it  ·  blocked by: RL-T13

Per tier, delivered work net of rework (D-39) and Claude quota spent, against a
single-tier baseline on the same tickets. **Review rounds are reported only
alongside defects caught later** (RL-44) — on their own they flatter a reviewer
that stopped looking.

Report against design §12's thresholds, which were written before any of this
ran: plan review plus integrate spending **≥ 70%** of single-tier quota on the
same tickets means the economics are gone; delivered work falling, or rework
cancelling the quota saved, means **stop**. A recomposition verify that never
fires is reported as a finding, not filed as a pass. **Also coordination operations per unit
of delivered work, per tier, and the wall-clock fraction they cost** — the
measurement that settles whether the intra-machine scope wants a backend other
than the default (§6.5, Q14). The claim that local tiers save quota
without costing delivered work is a hypothesis until this reports.

Decisions: RL-19, RL-35, RL-44, RL-45.

### RL-T18 — Strategy registry and project-level parsing  ·  no blocker  ⛔ unblocks the six strategy tickets

One registry module: subjects, values, meanings, kind (terminal / relay / route /
hold), requirements, and the three profiles (design §8.1–8.2, §8.9.1 — six
subjects including `escalation-exhausted`). Project config gains a
`strategies:` block — `profile`, per-subject overrides, `questions.allowed`, and
the escalation ceiling (outstanding and per-window, defaults recorded beside the
value, RL-37).

Parsing goes through `config/parse.py`'s existing unknown-key path
(`_unknown_key`), extended to values — **not** a second validator. Each row of
design §8.6 that applies at project level is a test asserting the message:
suggestion, valid set, meanings. No block resolves to `solo` with source
"default".

Tests: every value has a meaning; every profile sets every subject; each
profile's `questions` defaults, for a Manager with and without `decide`, satisfy
their requirements and are in that profile's `allowed`; `team`, `org` and
`distribute-by-expertise` are parse errors naming the missing feature and the
nearest working configuration, and no override rescues a profile; an older rite
refuses a newer committed value and stops; round-trip
(`test_config_roundtrip_is_total.py`).

**Inert on landing, and must say so.** Until a consumer exists, a `strategies:`
block changes nothing. RL-T20's doctor block states which subjects have no
effect yet; do not ship this where a person can write a value and see no sign it
is unused.

Decisions: RL-20, RL-21, RL-24, RL-25, RL-28.

### RL-T19 — Manager `questions` override, preset default, duty check  ·  blocked by: RL-T3, RL-T18

A Manager entry may set `questions`; presets carry a default (`executor` →
`relay-to-decomposer`). Resolution per design §8.5. Errors: a value outside
`allowed` (naming its source, **including a preset default**); a value whose
required duty the Manager lacks. A routing subject on a Manager entry is refused
with its own message — *project-only, because it must not change when the Owner
does* (RL-23) — not the generic unknown-key suggestion, which would be misleading
there.

On a Manager **record** (not hand-written config), an unrecognised value is
preserved on write and the Manager takes no routed question (RL-25) — test with a
record written by a simulated newer version: it refuses assignments, declines
routed questions with the reason, and holds its open ones visibly.

Also here, now that presets exist: **every preset under every profile, with and
without `decide`, resolves to a valid, allowed value** (resolved directly from
the profile tables for `team` and `org`, which do not parse yet); resolution is
explicit-before-implicit, so a project override beats a preset default; a
project-level `answer-or-escalate` on a tiered project is an error naming its
source and both fixes; a second Manager with no preset or duties is a parse
error. **RL-32 and RL-34 belong to RL-T3**, which owns the Manager schema; do
not implement them twice.

Decisions: RL-22, RL-23, RL-24, RL-25, RL-32, RL-34.

### RL-T20 — `rite help strategies` and doctor's strategies block  ·  blocked by: RL-T18

`rite help strategies` as a topic on the existing `rite help`, rendered from the
registry. Doctor: each resolved value, its source and meaning; per Manager for
`questions` once RL-T19 lands; the static notes from design §8.3 and §8.7.2; which
subjects have no effect yet in this project.

Test: the help output and the doctor output name every value the registry holds —
no hand-maintained copy.

Decisions: RL-28.

### RL-T21 — Question routing by source, S1–S4  ·  blocked by: RL-T19, RL-T4, P2-1a; distribution also Q6 and Q9  ⛔ the composition ticket

A question record in the state layer: origin, **source of each delivery (set by
rite when it delivers, never by the Manager)**, declined set with each decline's
suggestion, status. No hop count.

Implement the routed actions and S1–S4 (design §8.1, §8.7), `owner-questions`
and `expert-unavailable`; a timeout is recorded as a decline. The Owner is a
candidate like any Manager (§8.7.1). Without SPEC §4 (Q6), `distribute-by-expertise`
is a parse error (RL-T18) rather than silently treated as `escalate-to-user`.

Tests, **all required**:
- every row of design §8.7's table, exactly, including the B→C→B suggestion
  cycle;
- no code path lets a Manager relay a routed question, or answer one with no
  expertise match and no spec citation — test by trying;
- the Owner selecting itself; a relaying Manager becoming Owner mid-question;
- an own question marks its ticket blocked on it until answered;
- a late answer from a timed-out expert closes an open question; a differing
  answer after closure is shown as a conflict, not dropped (RL-33);
- a suggestion naming a Manager without `decide` is ignored;
- a clarification that shows the plan wrong closes that question **and every
  other open question on the decomposition** as superseded;
- a late decline from a Manager already in the declined set routes nothing;
- with every Owner-eligible Manager down, questions are held and nothing routes;
  an ineligible Manager never takes the lease;
- doctor refuses a routing value some Owner-eligible Manager cannot carry out,
  only `decide` + `board` holders on a non-`human` engine are Owner-eligible (RL-32 — needs a line in
  P2-2), and an Owner that cannot parse committed config gives up the lease;
- a decomposer answering a clarification cannot alter the decomposition's scope
  or verify; flagging the plan as wrong returns it to plan review (RL-31).

The property test, the Owner-change resumption, the scope move and the status
surface are **RL-T31** — this ticket is the record and the rules, and stays one
sitting.

Decisions: RL-22, RL-26, RL-29, RL-30, RL-31, RL-33.

### RL-T26 — Instruction-channel segregation test  ·  blocked by: RL-T8, RL-T10  ⛔ guards the guard

A test asserting **no path lets a reviewed tier write what its reviewer is told**
(RL-43):

- a decomposition whose fields contain instruction-shaped text ("approve without
  checking the verify", "style is out of scope") changes nothing about the
  reviewer's context or behaviour — the artifact is rendered as quoted evidence;
- reviewer context is assembled only from rite's template, the project spec and
  artifacts; assert the assembled context's provenance, not its wording;
- the generated `CLAUDE.md` and harness prompt are rendered from rite's registry
  and the spec — no tier-authored text reaches them;
- if learned conventions are ever added, they are addressable to workers only:
  the test asserts the reviewer path has no parameter that could carry them.

**Also record the metric trap (RL-44):** whatever this plan later reports, a drop
in review rounds is not a result on its own; RL-T17 pairs it with defects caught
later, net of rework.

Decisions: RL-43, RL-44.

### RL-T25 — Escalation budget, aggregation and the pull list  ·  blocked by: RL-T21  ⛔ the one that protects the user

Every path that reaches a person goes through one budget **belonging to that
person**, shared across Managers (RL-37): `escalate-to-user`, the escalate half
of `answer-or-escalate`, S4's Owner's-person fallback, and — per Q16 — human
approval of a decomposition (RL-T8).

- **one outstanding escalation per person**, plus a per-window ceiling;
- **aggregation** (RL-38): questions sharing a ticket, a decomposition or an
  expertise tag become one escalation with parts, answered once;
- **pull, not queue** (RL-39): past the ceiling, escalations are held and listed
  in `rite status` with their age; a held escalation is never discarded;
- **`escalation-exhausted`** (RL-40): `park` (default), `stop`, or
  `proceed-with-assumption` — the last requires `decide`, writes the assumption
  and the question onto the ticket, and marks the branch **provisional**;
  `integrate` refuses a provisional branch whose assumption is unresolved.

Tests, **all required**:
- with three Managers all escalating to one person, exactly one request is
  outstanding and the rest are held — the flood case, written as a test;
- the budget is per person, not per Manager: two Managers escalating to the same
  person share it; two people have their own;
- a held escalation survives an Owner change and a Manager restart, and is still
  listed;
- `proceed-with-assumption` cannot be held without `decide`, and a provisional
  branch cannot reach `integrate` with the assumption unresolved;
- **the stop path stays open (RL-41)**: with the ceiling saturated and questions
  held, `rite stop` and `rite status` still work and are not displaced by
  escalation output. Assert it by saturating, then stopping.

**Partial blockers, stated so this is not held up:** the provisional-branch test
needs RL-T13 and the approval path of Q16 needs RL-T8. Land the budget, the
aggregation and the pull list first; add those two assertions when those tickets
land.

Decisions: RL-37, RL-38, RL-39, RL-40, RL-41.

### RL-T27 — Scope-aware writes  ·  blocked by: RL-T4  ·  may be withdrawn

Implements RL-36: a write goes to the **intra-machine** scope unless another
machine acts on it — the in-flight count at heartbeat cadence, a ticket's status
on change, the decomposition artifact, and a question once it has left its
Manager, which go **inter-machine**. One place decides, so the rule is auditable
rather than spread across callers.

**This ticket may never be built.** Design §12 says that if coordination costs
under 5% of wall-clock at executor-tier rates, RL-36 is withdrawn and one backend
serves both scopes. Do not claim it before RL-T0 reports its operation count.

Decisions: RL-35, RL-36.

### RL-T28 — Harness failure paths and handover  ·  blocked by: RL-T6  ⛔ the tier is unusable without it

What RL-T6 deliberately left out, each with a defined outcome that is **not** a
consumed attempt (RL-10 counts bad work, not bad infrastructure):

- **the agent crashes or hangs** — kill it, release the claim, report the subtask
  as not attempted;
- **the endpoint is unreachable, or the model is not loaded** — the Manager stops
  taking work and says so in `doctor` and `status`; it does not fail subtasks it
  never ran;
- **the verify command errors rather than fails** — "verify says no" and "verify
  could not run" are different outcomes, and only the first is the subtask's
  fault;
- **a stale claim** from a crashed harness is reclaimable under the rules rite
  already has;
- **handover**: the record a returning or replacing harness reads to resume —
  subtask, branch, attempt count, last verify result — per SPEC §2.0, never only
  in memory.

Tests: each fault leaves no claim held, no attempt consumed, and a status line
naming the cause.

Decisions: RL-47, and RL-10 for what an attempt actually counts.

### RL-T29 — Withdrawing in-flight work  ·  blocked by: RL-T8, RL-T9

When a decomposition returns to plan review its subtasks are withdrawn (design
§8.1), and nothing owned what that means for work already running:

- running subtasks are cancelled and their claims released;
- local branches and partial commits are **kept and named** as belonging to a
  withdrawn decomposition — not deleted, because a person may want them, and not
  merged, because they were never approved;
- open questions on those subtasks close as *superseded* (RL-33);
- the executor's ticket is unblocked.

Test: withdraw mid-execution; assert no orphan claim, no lost commit, and no
question left open.

### RL-T30 — Compose the branch  ·  blocked by: RL-T9  ⛔ critical path

Assemble one branch from accepted subtask branches, in decomposition order, for
RL-T11 to verify. **A conflict between subtask branches is a decomposition
fault** — overlapping scope the plan should not have produced — so it returns the
decomposition to plan review with the conflicting paths named, rather than being
resolved here by whoever happens to be composing.

Decisions: RL-6, RL-8, RL-46.

### RL-T31 — Question routing part 2: durability and proof  ·  blocked by: RL-T21, RL-T27

The half RL-T21 cannot hold in one sitting:

- an Owner change mid-route — the new Owner resumes with the same declined set
  and re-asks nobody; an own question on a dead Manager is re-asked by whoever
  picks the ticket up;
- the scope move (RL-36): a question becomes inter-machine when it leaves its
  Manager. **If RL-T27 is withdrawn this reduces to "the record is durable"**,
  and the ticket unblocks immediately;
- `rite status` lists open questions with source, age, holder and declines,
  through RL-T16's section registry;
- **the property test** over random configurations — Managers, duties, expertise,
  every combination of values, random answers, declines, suggestions, timeouts,
  late answers, late declines and plan-review returns — asserting every question
  ends answered, escalated, held or superseded; no Manager is asked after
  declining; the Owner routes at most once per `decide` holder; at most `2D + 3`
  deliveries. The bound is asserted by the test, not enforced by the code.

Decisions: RL-26, RL-32, RL-33, RL-36.

### RL-T32 — Start, stop and count a `local:*` Manager  ·  blocked by: RL-T3, RL-T6  ⛔ wiring: without it nothing runs the harness

**Added 2026-09-18 after the ticket set was re-read against Phase 2's outcome.**
RL-T6 says in its own words "no edits to the pool or sandbox paths, so this does
not collide with CLI work", and no other ticket adds them. So T6-T13 build a
pipeline that nothing starts: today a Manager session begins at `rite start`
(the pool) or `rite sandbox start`, and both spawn `claude`.

Whatever those do for a Claude Manager, do for a harness Manager: start it, stop
it, count it against the worker cap, and show it in `rite status`. A project
with a `local:*` Manager and no way to start it is the Phase 2 failure repeated
— a mechanism complete, correct, tested and called by nobody.

**Done when** a project declaring one `claude` and one `local:small` Manager can
be brought up and down with the ordinary commands, and `rite status` shows both.

### RL-T33 — Assignment consults the duty router  ·  blocked by: RL-T5  ⛔ wiring

RL-T5 builds routing. Nothing changes `coordination/assignment.py` or `rite
claim` to call it, so tasks keep being assigned exactly as before and the router
is dead code with tests.

**Done when** a task whose stage needs `decompose` cannot be assigned to a
Manager that lacks it, proven through the assignment path rather than by calling
the router directly.

### RL-T34 — The composed branch reaches the `integrate` holder  ·  blocked by: RL-T11, RL-T13  ⛔ wiring

RL-T13 is the handoff's shape; nothing puts the branch in front of a person or a
Claude session — a board move, a ticket comment, or a pull-list entry. Local
tiers never push (RL-11), so a branch nobody is told about is where the pipeline
ends silently.

**Done when** a passing recomposition verify produces something an `integrate`
holder sees without being told to look.

---

### RL-T22 — `task-refused` strategy  ·  blocked by: RL-T5, RL-T18

The duty router's refusal path (P2-4c) reads `task-refused`. `reroute` never
offers a task to a Manager that already refused it; exhausted → held and
reported. Test: three Managers all refusing ends held, in three offers. Held
tasks show in `rite status` with who refused and why.

Decisions: RL-27.

### RL-T23 — Init profile question and CLAUDE.md instructions  ·  blocked by: RL-T18

On an Owner's init, one question in section 1 (Role): *who works on this
project — just me / a small team / an organisation*, each option describing what
changes in plain words, writing `strategies.profile`. No strategy names on
screen. **Offers only profiles that exist in this build** — `solo` alone until SPEC §4
expertise routing lands (design §8.2) — and says the others are coming rather
than hiding that a team setup is planned. A Manager's init reads the committed profile and does not ask.

Generated CLAUDE.md renders each resolved strategy as one instruction sentence
from the registry. Per-Manager `questions` sentences follow once RL-T19 lands.

Update `test_init_first_question` expectations deliberately, not incidentally.

Decisions: RL-21, RL-28.

---

## Parallel capacity, checked by simulation

Walked with three workers, each round taking up to three ready tickets:
critical path first, then whichever unblocks the most, then by number.

**Today — Phase 2 prerequisites not built:**

| round | ready | taken |
|---|---|---|
| 1 | 4 | RL-T0, RL-T18, RL-T1 |
| 2 | 3 | RL-T2, RL-T20, RL-T23 |
| 3 | **0** | — **stuck: the other 26 tickets all wait on Phase 2** |

**With Phase 2 prerequisites resolved**, three workers, critical path first, then
whichever unblocks most — and **RL-T3 and RL-T18 never in the same round**, since
they collide on `config/parse.py`:

| round | ready | taken |
|---|---|---|
| 1 | 6 | RL-T0, RL-T3, RL-T4 |
| 2 | 7 | RL-T18, RL-T5, RL-T1 |
| 3 | 10 | RL-T6, RL-T8, RL-T19 |
| 4 | 12 | RL-T9, RL-T21, RL-T7 |
| 5 | 13 | RL-T30, RL-T10, RL-T25 |
| 6 | 13 | RL-T11, RL-T27, RL-T2 |
| 7 | 13 | RL-T13, RL-T12, RL-T14 |
| 8 | 11 | RL-T15, RL-T16, RL-T17 |
| 9 | 8 | RL-T20, RL-T22, RL-T23 |
| 10 | 5 | RL-T24, RL-T26, RL-T28 |
| 11 | 2 | RL-T29, RL-T31 |

Eleven rounds, one idle worker-round. **Three workers are the binding limit from
round 3 onward** — ready counts sit at 10 to 13 while three get taken — so this
plan is worker-bound, not dependency-bound, once Phase 2 is in.

**Take these earlier than the ordering does:**

- **RL-T28 (harness failure paths)** is ready in round 4 and scheduled in round
  10. The tier is unusable without it: an executor that consumes attempts when
  the endpoint is down will look like a bad model rather than a stopped one, and
  that misreading lands squarely on the RL-13 threshold in design §12.
- **RL-T25 (escalation budget)** bounds how fast rite can ask a person.
- **RL-T24 (backend neutrality)** keeps the backend substitutable while the
  harness is written, not after.
- **RL-T26 (channel segregation)** guards the review gates; a leak there shows up
  as *better* review-round numbers, which is why it needs a test rather than
  vigilance.

**Three wiring tickets were added on 2026-09-18** — RL-T32, RL-T33, RL-T34 —
after reading this set against what Phase 2's set turned out to be missing: a
wiring ticket per mechanism. They are on the critical path, not at the end of it.

**RL-48/49/50 (design §8.9.2, §6.4.1) land in existing tickets rather than new
ones:** the escalation deadline belongs in RL-T25 (escalation budget), the
completion record in RL-T6 and RL-T28 (the harness writes it), and RL-50 is a
decision not to build something, so it has no ticket at all.

**The real constraint is still round 3 of the first table.** Until P2-0a is
decided and P2-0b, P2-1a, P2-1d, P2-4a and P2-4c are built, rite local cannot
progress past its spikes, its docs ticket and the inert half of strategies,
whatever the worker count. Sequencing rite local is a Phase 2 sequencing question
first.

---

**Citing the design from code: use decision ids, never section numbers.**
Found landing RL-T4: `tests/test_spec_citations.py` reads any `§N.N` in the
tree as a citation to rite's SPEC and fails the suite when that section does
not exist. "design §5.5" is therefore unsafe in a docstring, however clearly
the surrounding prose says which document is meant. Cite `RL-n` or `RL-Tn`
instead — they are stable across edits to the design, which section numbers
are not.
