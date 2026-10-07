# v0.7.1 — the dogfood fixes: scope, design, order

**Status: PLAN, 2026-10-05, for Robert's review.** No code fix is on this
branch; it carries this plan and the spec amendments that go with it. Base:
`main` at `0118273` (v0.7.0a9 at `29d02d4`).

**Goal.** A dogfood where *every known issue is fixed* and a **real end-to-end
run on a throwaway app** passes: a Claude Manager plus one Claude Worker and one
GPU Worker take real tickets from the board to delivered pull requests, with no
human running host commands.

## 1. Scope (the must-fix set)

| Key | What | Size |
|---|---|---|
| SCRUM-59 | Manager can't run Worker lifecycle or gate diagnosis from its sandbox | M |
| SCRUM-64 | A restarted Manager doesn't reconcile with ground truth | M–L |
| SCRUM-72 | A local/GPU Worker is never driven under a Claude Manager; deterministic staged pipeline (§3.3a) | XL |
| SCRUM-69 (+45, 22) | Relay text depends on a shell heredoc (temp-file EPERM, doubled end line) | S–M |
| SCRUM-21 | `rite reply`/`ask` can't thread under the Owner's message | S |
| SCRUM-39 | CI publish-gate scans every fetched branch, not the PR's range | S |
| SCRUM-62 | A Worker can edit its own gate's suppressions | S–M |
| SCRUM-70 | `release --force` reports success but the slot stays held | S |
| SCRUM-71 | The issues journal misses the failures that matter | M |
| SCRUM-73 | A Done ticket still labelled `ready-to-work` is offered again (caused this run's KAN-28 replay) | S |
| SCRUM-68 (+43) | A release tag at HEAD turns CI red: the tag-relative guards and every-test-passes-somewhere need a declared "nothing to check" state; lands before the re-cut | S |

**Folded in, not separate tickets.** SCRUM-45 and the residual of SCRUM-22 are
the transport SCRUM-69 replaces (§3.4). The `mkdir`/`echo … > $(date)` request
line the Manager was refused on (journal, 2026-10-04 10:48Z) goes with
SCRUM-59's `rite request`. A Worker started with a `CLAUDE.md` older than
`rite done` (journal, 10:56Z) becomes a refusal at start, in SCRUM-64.

**SCRUM-68 is IN (Robert, 2026-10-05):** release tags turn CI red, and the next
RC re-cut would hit it again. It lands before the re-cut (§4.1).

## 2. What the code says (scoping, 2026-10-05)

**SCRUM-59 (M).** There is a working precedent three times over:
- the Manager files a Worker start into `state/requests/`, honoured by
  `supervise._honour_worker_requests` → `broker.honour`;
- deliveries go through `publishing/requests.honour_deliveries`;
- `rite chore` is a CLI the Manager runs that writes its request in Python, so
  nothing reaches the shell.

Every host-side op already exists: `sandbox.stop_worker`, `destroy_worker`,
`restart_worker`, `worker_sandbox_status`, and `gate.run_gate`. Outcomes return
by `telling.tell_manager`. The allowlist already permits the absolute `rite`
path (`permissions._running_rite_rules`), so a CLI needs no allowlist change.
What is missing is the request kind, the CLI, the honour step, and the three
prompt texts that still teach `echo … > $(date).json`.

**SCRUM-64 (M–L).** `rite start` reads none of `claims.json`, heartbeats, publish
records or `watched.json`. Recovery (`recovery.recover_stalled_workers`) acts
only on watchdog stalls, and `left_unattended` is written at exit and released
by nobody. Reconciliation runs host-side, so detection does not need SCRUM-59;
the Manager acting on what it finds does.

**SCRUM-72 (L at first scoping; XL now, §3.3a/§3.3b).** A local Worker's turn runs only through `take_one_step`, which
needs an **approved plan**: there is no single-turn path. The driver is wired
only `if role.is_local` (`cli/main.py:10217–10221`). In a Claude-plus-GPU fleet no
Manager can author the plan:
- `lead` lacks the `decompose` duty;
- the Claude proposer is "not wired yet" (`decompose._proposer_for`);
- RL-67 requires the author to be a Manager;
- RL-6 requires a reviewer running a different model.

Separately, `rite sandbox start` pastes a Claude prompt into a local Worker's
idle sandbox (`main.py:8226`, `sandbox/__init__.py:1678`). Once a Worker is
placed, the executor itself (`worker_step.placement_for`, #202) does not check
the Manager's engine.

## 3. Design decisions

### 3.1 Lifecycle requests (SCRUM-59)
- **`rite request {stop|destroy|restart|status|gate} <worker> [--ticket <id>]`**, run
  by the Manager and modelled on `rite chore`. It writes `{op, worker, ticket?}`
  into `state/lifecycle/` with Python, so no `$()` or `>` ever reaches the
  engine.
- `supervise` honours the requests at the cycle boundary, between deliveries and
  Worker starts, and the result returns as an inbox note. `status` and `gate`
  put their findings in the note.
- **The boundary is not widened.** The Manager gets no credential store, no
  `~/.yoloai` and no git temp-cache.
- **Refused by construction:**
  - `destroy` with `force` (no field exists to carry it);
  - a Worker belonging to another Manager.
- **Shared with recovery:** restarts use recovery's backoff (`recovery.json`).
- **Counts as progress:** the new request kind is added to `progress.footprint`
  and `COORDINATION`, so a session that only files requests is not "idle".
- The three prompt texts and the deliver gate-refusal note switch to the CLI.
  The refusal note carries the gate's findings directly.

### 3.2 Reconciliation (SCRUM-64)
A new `managers/reconcile.py`: a pure planner plus an injected applier, the
shape of `recovery.py`. It runs once at start (after `pending.sync`) and
throttled per cycle.

For each of the Manager's Workers it compares:
- claims
- the live sandbox (`unknown` means do nothing)
- a handback
- the publish record
- the watched PR and the origin branch
- the board
- a recorded-but-absent sandbox

**Rule:** release a claim **only** when the sandbox is known gone **and** the
work is delivered, merged or handed back. Every action goes through SCRUM-59's
executor, and every outcome is told to the Manager so it stops escalating stale
facts.

At start it also:
- refuses a Worker whose `CLAUDE.md` predates the running release's handback
  protocol, or refreshes it;
- runs `reap_dead_selftest_sandboxes`, which its own docstring already claims
  happens.

### 3.3 Local Workers under any Manager (SCRUM-72)
- **The driver keys off Workers, not the Manager.** The supervise loop drives
  the local tier for every ticket held by a **local Worker** of this Manager,
  whatever the Manager's engine. `_local_tier_tickets` filters to those tickets,
  so a Claude Manager's own tickets are never decomposed.
- **Who authors and who approves (DECIDED, Robert, 2026-10-05).**
  - A declared **local Manager with the `planner` preset** (`decompose`, on the
    same Ollama model as the Worker) **writes** the Level 1 plan.
  - **A Manager approves it, whatever its engine** (§3.3b): Claude, local/GPU,
    or any other provider. Approval authority is a Manager responsibility, the
    `plan-review` duty, and is never conditioned on the Manager's engine.

  That keeps Level 1 at Manager tier (DECOMPOSER_DESIGN §1.3). The author is a
  Manager (RL-67), and the reviewer runs a different model (RL-6). No RL rule
  changes. The planner is declared in config. *Inferred, to confirm first while
  building:* the supervisor can drive the planner's decompose step without a
  `rite start` of its own. If it cannot, the planner runs its own loop, and the
  fleet has one more process.
- **No prompt for a local Worker.** `sandbox start`/`restart` pass no prompt
  and no `--resume`, and the output says the local tier drives the Worker.
- **The watchdog must not read an idle sandbox as stalled.** A local Worker's
  sandbox idles between subtasks and never heartbeats. Its liveness becomes
  "has a plan step pending, or is running one".
- **The Manager waits on the plan and its steps, not on `rite done`.** Its
  prompt says so (inferred from the scoping; to confirm while building).

### 3.3b Plan approval is a Manager responsibility, engine-agnostic (Robert, 2026-10-05)
**The principle.** SCRUM-72's root cause was an engine gate: the driver ran only
`if role.is_local`. The approval path must not reintroduce one. Whether a
Manager may approve a plan depends only on its duty and its independence from
the author, never on what kind of engine it runs.

**Checked against the code (2026-10-05).** `approve_plan` (`local/approve.py`)
and `independent_reviewer` (`local/loop.py`) contain no engine-kind check. They
check the `plan-review` duty, DD-3.5, RL-67, and RL-6 through
`engine_identity`: the model for a local engine, and the engine kind otherwise,
so every Claude Manager counts as one identity whatever its `model:`. That is
the only place the approval path reads an engine. It decides independence,
never who may approve, and it stays. Today's engine gate on approval is
**indirect**. Approval runs only inside the local-tier driver, which runs only
for a local Manager, and there it is a **stamp**: `advance_ticket` picks a
holder and writes APPROVED in its name, and no Manager is ever asked to review.
The only door besides the stamp is `rite local approve`, and today any Manager
can run it from inside its boundary in any reviewer's name (next bullet list).

**The design.**
- **Who may approve:** any Manager that holds `plan-review`, is not the author
  (DD-3.5), and runs a different model from the author (RL-6), with the author
  a known Manager (RL-67). Nothing else. `approve_plan` and its new sibling
  `reject_plan` are the only writers of APPROVED and REJECTED.
- **No engine-kind condition** admits or excludes a Manager anywhere on the
  approval path.
- **How it is asked.** When a plan is PENDING, the supervisor picks the reviewer
  (`independent_reviewer`) and sends one plan-review request to that Manager's
  inbox through `telling.tell_manager`, the channel every Manager reads
  whatever its engine. The request carries the plan (subtasks, scope, verify,
  cites) and the refinement record's agreed definition of done. It is sent once
  and tracked, not re-sent every cycle; an unanswered request is re-asked, then
  escalated after the refinement deadline.
- **How it answers.** The Manager runs `rite plan approve <ticket>` or
  `rite plan reject <ticket> --reason-file <path>` inside its own boundary.
  These write a verdict request into the Manager's own directory, SCRUM-59's
  request mechanism. The supervisor honours it at the cycle boundary by calling
  `approve_plan` or `reject_plan`. **The reviewer's identity is the directory
  the request was found in, never a name in the payload.** A local Manager's
  turn answers with the same command. There is one code path for every engine.
- **What makes that identity hold.** That directory is the Manager's
  `manager_dir()`, outside the project since MM8, and only that Manager can
  write it.
  - The supervisor reads a verdict only as a regular file, opened without
    following links, whose resolved path is inside that Manager's resolved
    `manager_dir`. Anything else is refused and reported, so a symlink into a
    peer's directory cannot borrow the peer's name.
  - A verdict carries the plan version it was given, and is refused if the plan
    has changed since: no approving a plan nobody read.
  - Identity holds against other Managers, not against the operator, whose door
    is `rite local approve`.
- **The harness never approves.** At PENDING, `advance_ticket` sends the
  request and waits. The automatic stamp is removed.
- **A Manager cannot write a decomposition, or approve outside the review
  path. This is a NEW property, and it closes a hole that exists today.**
  - `LocalStateLayer` keeps every plan in `.rite/state.json`, inside the
    project tree, which every Manager's profile grants writable: seatbelt in
    `enclosure.compose`, and Landlock's `_fenced_project_paths`, which cannot
    deny. So any Manager can edit a plan's approval.
  - Any Manager can also run `rite local approve <anyone> <ticket>` inside its
    boundary, since `Bash(rite:*)` is allowlisted.
  - **The fix:** the state layer moves outside the project, beside
    `manager_dir` (MM8's pattern, needed because Landlock has no deny). Then
    `rite local approve` works only outside a boundary, fenced by file
    permissions rather than by an environment variable.
  - The same move protects every other state key. **S–M, counted in §6.**
- **`rite local approve <reviewer> <ticket>`** stays as the Owner's host-side
  door, and calls the same `approve_plan`.
- **A rejection** writes REJECTED with the reasons, and the plan goes back to
  its planner with them. **This is a new transition:** today a REJECTED plan
  blocks forever (`advance_ticket` re-authors only when there is no plan). It
  is REJECTED → DECOMPOSED again, with the reasons as decomposer input, bounded
  by the decomposer's retry budget, and then an escalation.
- **`rite doctor` and `rite start`** refuse a fleet with a local Worker but no
  Manager that could independently approve its planner's plans, and say which
  rule fails.

**Out of scope, recorded honestly:** *authoring* still requires a local (goose)
Manager. `decompose._proposer_for` refuses a non-local author because the
Claude proposer adapter is not wired. That is a missing adapter on the
authoring side, not an approval gate, and it gets its own ticket. The approval
path is not allowed to depend on it.

**Acceptance tests for §3.3b:**
1. **Engine-agnostic approval.** Parametrized over every engine kind config
   accepts for the approving Manager: `claude`, `local:<class>` on a different
   model, and `human`, which answers through `rite plan approve` from a
   person's shell. `cursor` joins when CU4 lets config declare it. Each approves
   through the same request → supervisor path, and the plan becomes APPROVED
   with `approved_by` set to that Manager.
2. **Control:** adding an engine-kind condition to the approval path (for
   example `if role.is_local`, or its negation) turns test 1 red. The mutation
   run is recorded.
3. A PENDING plan with no verdict is left byte-unchanged by the harness, and
   exactly one review request is sent.
4. A rejection writes REJECTED with the reasons, and the plan returns to the
   planner, which re-authors with those reasons. When the retry budget runs
   out, the ticket escalates.
5. A verdict request found in Manager X's directory is attributed to X,
   whatever name it carries. A symlinked verdict, and a verdict for a stale
   plan version, are each refused.
6. The refusals for the same model, a self-review and an unknown author hold for
   every engine kind.
7. A Manager's write to the plan state, and its `rite local approve`, are both
   denied by its boundary, on seatbelt and on Landlock.

### 3.3a The staged pipeline is enforced by code (Robert, 2026-10-05)
The definition of done for SCRUM-72: rite's **deterministic harness code**, not
the model or the Manager, drives a local Worker's ticket through every stage, in
order, with the same rigor as the Claude path:
1. a spec/definition session at session start;
2. the refinement prompts;
3. the APPROACH step (Level 2);
4. the review rounds, i.e. the RL gates: RL-6 plan review, RL-7 mechanical
   verify, RL-8 recomposition verify.

The model fills each stage and cannot skip or reorder one. Each stage is a
persisted state with a guard, and none is reachable before its predecessor's
artifact exists and has passed its gate. It is the determinism principle of the
two-level decomposer and RL-6/7/8, applied end to end.

**Tests:** one per guard (a step before approval, an approach that changes
scope/verify/cites, a delivery before recomposition verify, each refused
without advancing the state). The end-to-end run's record must show every stage
in order.

**Mapped against the code (2026-10-05).**

The Claude path enforces less than "the same rigor" assumes. Code enforces only
refinement (`_deliver_ticket` refuses anything not REFINED) and the delivery
gates. The spec session and the review rounds are CLAUDE.md instructions, and
there is no Level 2. The local tier will enforce more than the Claude path does.

| Stage | Local tier today |
|---|---|
| decompose → approve → step ordering | **Enforced.** Persisted plan state; APPROVED is written only by `approve_plan`; a step is refused without it in three places. |
| RL-6 plan review | **Hollow.** It checks the different-model rule and stamps a reviewer, but no reviewer reads the plan and nothing writes REJECTED. v0.7.1 makes it a real review by a Manager of any engine (§3.3b). |
| RL-7 mechanical verify | **Enforced.** rite runs the verify; an empty commit is not acceptance. |
| Spec/definition session | **Missing.** `advance_ticket` calls `author_plan` without `ticket_text`, so the decomposer sees "(no ticket text was supplied)". The refinement record (definition of done, verify, scope) gates the start but is never an input to the work. |
| Level 2 approach | **Optional.** Fail-open by DD-2.4 and never persisted. The §2.4 boundary check compares the plan with itself, so it cannot catch drift. |
| Step review | **Missing.** `STEP_REVIEW` has no executor; a FAILED subtask dead-ends the ticket. |
| RL-8 recomposition verify | **Missing.** Delivery is requested once every subtask is accepted. |
| RL-70 `cannot_fail` | **Open.** A bare `true`, `:` or `exit 0` passes. |

**To build:**
- one persisted `stage` key with a transition table, a single guard and an
  append-only transition log; `advance_ticket` dispatches from it (M);
- DEFINED: a snapshot of the refinement record, fed to the decomposer and the
  slice; halt if it goes STALE (M);
- Level 2 mandatory and persisted, with the boundary check against an
  approval-time hash (M; overrides DD-2.4, §5);
- RL-8: the record's `verify` on the ticket branch before delivery is requested,
  and a failure returns the plan to review (M);
- a delivery guard on honouring the request file as well (S);
- the RL-70 fix (S);
- RL-6 as a real review by a Manager of any engine (§3.3b; M, reusing
  SCRUM-59's request path and the inbox);
- an executor for step review (M–L, only if §5.4 asks for it).

**Guard tests, each asserting the stage did not advance and the persisted state
is byte-unchanged:**
1. a step before approval;
2. a decompose with no DEFINED snapshot, or after the record went STALE;
3. a subtask edited after approval;
4. a step with no persisted approach;
5. a delivery before RECOMPOSED, through both the loop and a hand-written
   request file;
6. a failed RL-8 verify returns the plan to PENDING, with no delivery request;
7. an illegal stage write;
8. end to end with stub agents: the log lists every stage once, in order.

### 3.4 Relay transport (SCRUM-69, absorbing 45 and 22)
The Manager writes its text to a file with its own file-writing tool (not the
shell) and runs
`rite reply|ask|route --manager <m> --from-file <path>`. There is no heredoc,
no temp file the shell must create, and no end line to double. The heredoc
form stays accepted while the prompt teaches only the file form. Paths are
limited to the Manager's own scratch directory.

### 3.5 Light fixes
- **SCRUM-21:** `--message <ts>` on `rite reply`/`ask`, carried to `_post(thread=)`.
- **SCRUM-39:** the CI gate scans the PR's own range.
- **SCRUM-62:** delivery refuses a Worker branch that touches gate suppression
  files, and routes the change to the Owner. The project root's suppression
  file stays Owner-only.
- **SCRUM-70:** `release --force` says the slot is still held while a sandbox
  lives, and offers `rite request stop`. `rite status` stops saying "no active
  claims" for a Worker holding a slot.
- **SCRUM-71:** a defined set of recordable events, written by the supervisor
  itself:
  - relay or delivery refusals
  - recovery and restart
  - reconciliation actions
  - gate refusals
  - a slot held by an idle sandbox
- **SCRUM-73:** readiness excludes Done and Closed statuses whatever the labels
  say.

## 4. How it is built

**One integration branch, `feat/v0.7.1-dogfood-fixes`, one PR at the end.**
1. Each fix lands on the branch as **its own reviewed commit**, with a fast
   targeted test subset run locally. That subset is the touched tests plus
   `test_update_refresh`, the boundary tests and the Manager-prompt tests.
2. When the set is complete: one PR, the **full CI matrix**, plus the **on-Mac
   real-Claude suite**.
3. Fix everything they find in one pass, then merge.
4. **Re-cut the RC tag** and install it. SCRUM-68 has landed by then, so the
   tag does not turn CI red; regenerate the section and template histories
   only if SCRUM-68's fix still asks for it.
5. Run the throwaway-app end-to-end (§4.2).

### 4.1 Order, by dependency
1. **SCRUM-69** (transport). The Manager's own commands must work before
   anything it is asked to do can.
2. **SCRUM-59** (lifecycle requests). The executor everything below relies on.
3. **SCRUM-64** (reconciliation). Builds on 59.
4. **SCRUM-72** (local Workers). §5.1 is decided; needs §5.4 and §5.5. It also uses 59's
   start/stop path, and 64's liveness rule for idle local sandboxes.
5. **SCRUM-70, 71, 73.** These touch the same supervisor seams, so they land
   right after 64.
6. **SCRUM-21, 39, 62.** Independent; can land at any point, in parallel
   sessions.
7. **SCRUM-68** (in, Robert 2026-10-05): before the re-cut.

### 4.2 The acceptance gate: a throwaway app, end to end
A fresh repository with a small app, a fresh rite project and a fresh board
project.

**Fleet:**
- a Claude `lead`;
- a local `planner` (writes the GPU Worker's plans);
- one Claude Worker;
- one GPU Worker (`qwen3.8` at a 32k window, the patched yoloAI first on PATH).

**Tickets:**
- two ordinary tickets for the Claude Worker;
- one ticket for the GPU Worker;
- one ticket whose definition of done needs an answer from the Owner.

**Passes when, with no host command run by a person:**
- every ticket reaches a delivered PR;
- the GPU Worker's plan is written by `planner` and approved by `lead`
  through its inbox (§3.3b), with no host command;
- the Owner's answer reaches its Worker;
- a killed Worker sandbox is recovered by the Manager (through SCRUM-59);
- a Manager restart mid-run reconciles without escalating (SCRUM-64);
- the journal records each induced failure (SCRUM-71);
- no Done ticket is offered again (SCRUM-73).

This gate is the last step before the RC is re-cut. It is not a test in CI.

## 5. Decisions for Robert
1. **SCRUM-72 authorship. DECIDED (Robert, 2026-10-05):** option (a),
   generalized. A local `planner` Manager writes the plan, and a Manager
   approves it **whatever its engine** (§3.3, §3.3b).
2. **SCRUM-68 in v0.7.1. DECIDED (Robert, 2026-10-05): in**, sequenced before
   the RC re-cut so the re-cut does not need the follow-up PR a9 did.
3. **Is SCRUM-22 closed** once SCRUM-69 lands? #179 and #189 fixed its
   reporting, and its transport residual is §3.4.
4. **Must step review be a real reviewer turn** (§3.3a)? RL-6 is now a real
   review by decision 1 (§3.3b). **Recommended: not in v0.7.1.** Step review
   stays RL-7's mechanical verify, RL-8 is built, and a real step reviewer gets
   its own ticket. Saying yes adds about 1–1.5 days to SCRUM-72.
5. **Override DD-2.4**, so Level 2 is a required, persisted stage rather than
   fail-open? Recommended yes. "Cannot skip a stage" requires it.

## 6. Estimate (revised 2026-10-05)
**The compressed 2–3 days (one PR) does not hold.** That figure assumed SCRUM-72
was wiring and SCRUM-59 was small. Scoped against the code, 72 needs an
authorship decision and a plan/approve path for a fleet that has never run one,
and 64 has the highest risk of the set.

| Work | Agent wall-clock |
|---|---|
| SCRUM-69 transport | 0.5 day |
| SCRUM-59 lifecycle requests | 1 day |
| SCRUM-64 reconciliation | 1–1.5 days |
| SCRUM-72 local Workers under any Manager, as a deterministic staged pipeline (§3.3a; after §5) | 5–6.5 days (was 1.5–2.5; 3.5–5 before RL-6 became a real Manager review and plan state moved out of the project, §3.3b); 6–8 if step review is also a real turn (§5.4). Much of it is live Ollama turns measured in minutes |
| SCRUM-70, 71, 73 | 1 day |
| SCRUM-21, 39, 62 (parallelisable) | 1 day |
| SCRUM-68 | 0.5 day |
| Integration PR: full CI (about 35 min a cycle) + on-Mac real-Claude suite + one bulk-fix round | 0.5–1 day |
| Throwaway-app end-to-end gate, plus fixing what it finds | 1–1.5 days |
| Re-cut and install the RC | 0.25 day |

**Range: 11–14 working days with one integrator, or about 9–11 if SCRUM-21,
39 and 62 (and 70, 71, 73 once 64 lands) run in parallel worktrees.** With a
real step reviewer as well (§5.4) it is 12–15.5, or 10–12.5 in parallel.
Against the 1.5–2.5-day SCRUM-72 of the first scoping:
- the deterministic pipeline (§3.3a) added about 2–2.5 days;
- the engine-agnostic Manager review (§3.3b) about 1 more;
- moving plan state out of every Manager's reach (§3.3b) about 0.5.

All of it is on the serial path. The core
chain 69 → 59 → 64 → 72 is serial and sets the floor.

**Drivers, most uncertain first:**
- how well the local planner plans, and how well the reviewer reviews;
- local-model turn time;
- SCRUM-64's "cannot tell" cases;
- CI cycle time;
- how much the end-to-end gate finds.
