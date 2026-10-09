# The release gate, end to end: two fleets, watched

Robert gives the release go when he can **see both fleets run** end to end, each
on its own throwaway app and board. This harness builds both, runs each, judges
each against the plan's acceptance checks (`docs/design/V071_DOGFOOD_FIXES.md`
§4.2), and records each run so it can be watched live and replayed afterwards.

It is an instrument. Nothing in `src/` imports it, and the gate itself is not a CI
test. CI holds only the judge and the demonstration's rendering, in
`tests/test_the_v071_acceptance_harness_detects_each_failure.py`. Each check there
passes on a fixture run that met its criterion and fails on one with the defect
it exists to catch.

**Status: scaffolded, not run.** `run` refuses until preflight is clean. That
needs the merged fixes in an installed build, so neither fleet is ever run
against half-built fixes.

## The two scenarios (`scenarios/<name>/`)

| | `mixed` | `all_local` |
|---|---|---|
| Owner and plan reviewer (`lead`) | Claude | local `qwen3-32b-ctx32k`, goose, 32k |
| Plan author (`planner`) | local `qwen3.8`, goose, 32k | local `qwen3.8`, goose, 32k |
| Workers | `alpha` (Claude), `gpu1` (`local:small`, `qwen3.8`, 32k) | `gpu1` (`local:small`, `qwen3.8`, 32k) |
| Pipeline tickets (spec → refine → approach → review → PR) | `gpu-slug` | `gpu-slug`, `gpu-total` |
| Other tickets | `claude-total`, `claude-format`, `owner-currency` (needs the Owner's answer), `done-decoy` | `done-decoy` |
| Killed mid-ticket | `alpha`'s sandbox | `gpu1`'s sandbox |
| Restarted with stale state | `lead` | `lead` |
| Checks | 13 | 13 (adds `no_claude_in_the_fleet`; drops the Owner-answer check) |

**Why `all_local` has two local Managers, not one.** A plan must be approved by a
Manager that did not write it (DD-3.5), running a *different model* from its
author (RL-6). rite's `engine_identity` compares models for a local engine. One
local Manager that both writes and approves can never pass the review stage. So
`planner` (qwen3.8) writes and `lead` (qwen3-32b) approves. A test pins this with
rite's own `model_identity`.

**Memory:** the two models are about 17 GB and 20 GB on a 48 GB machine. Ollama
may swap them, which makes turns slower but breaks nothing. Run the scenarios one
after the other, not at once.

Both scenarios use the same throwaway app, `app/`: `tally`, a tiny library whose
tickets' agreed checks are tests that ship failing. Each run gets:
- a fresh copy of the app;
- a fresh private GitHub repo, which is both the board and the PR target (rite
  has no offline board);
- a fresh rite project with its own mail and home directories, its own
  sandboxes, and its own tmux server.

Nothing is shared with the yoloAI dogfood, Bentora, or the other scenario.

## Watching it: the demonstration

Each run is recorded so it can be shown, not just reported:

- **Live.** `run` opens a `watch` window in the run's tmux, beside the Managers'
  supervisors, and prints the attach command (`tmux -L rite-e2e-<run> attach -t
  e2e:watch`). The window shows:
  - a board of each ticket against the pipeline columns (Spec · Refine · Plan ·
    Review · Approach · Execute · Recompose · Delivered);
  - the latest moments, with the induced failures marked;
  - the verdict once judged.

  The Managers' own windows are in the same tmux session.
- **Replay page.** `<run>/demo.html` is a self-contained page with no network
  use, written when the run is judged. It has:
  - **Play** at ×30, ×120 or ×600, and a scrubber, so the run replays as it
    happened;
  - the pipeline swimlanes filling in as time moves, with each ticket's PR link;
  - the timeline of moments, and the acceptance checks with their evidence.
- **Terminal replay.** `harness replay <run> --speed 120`.
- **The gate page.** `harness gate-index <mixed-run> <all_local-run>` writes one
  page with both scenarios' verdicts and links to each replay.

**What the swimlanes prove.** A cell is ticked only from a record:

| Column | Proof |
|---|---|
| Refine | `rite refine accept` at setup |
| Plan, Review | the plan's `decomposed_by`, `approval` and `approved_by`, snapshotted every poll |
| Execute | every subtask `accepted`, which happens only after rite's own verify (RL-7) |
| Delivered | rite's `delivered` event with its PR URL |
| Spec, Approach, Recompose | **"awaiting SCRUM-72"**, until 72's transition log exists (`hooks.stage_log`). Never ticked before that. |

## Commands (from the rite repo root)

```sh
H="uv run python -m tools.e2e_v071.harness"
$H plan --scenario mixed            # or all_local: fleet, tickets, checks
$H preflight --scenario all_local --probe-df16
$H setup --scenario mixed --create-repo    # creates the run's private repo + board
#   set the credentials setup prints, re-run `rite doctor` in <run>/app until clean
$H run <run-dir> --probe-df16       # the run dir knows its scenario
$H watch <run-dir>                  # live (also opened in the run's tmux)
$H replay <run-dir> --speed 120
$H demo <run-dir>                   # rewrite <run>/demo.html
$H check <run-dir>                  # re-judge offline from the run's records
$H gate-index <mixed-run> <all_local-run>   # the release gate page
$H teardown <run-dir> [--delete-repo]
```

`setup --offline` is a dry setup with no repo, board or network. It checks that
the installed build accepts the scenario's `rite init`, fleet and config. `run`
refuses an offline run directory.

Exit codes: 0 PASS, 1 FAIL, 2 INCOMPLETE (a check is PENDING), 3 refused
(preflight).

## Prerequisites at run time

1. **The fixed build, installed:** `uv tool install` of the merged v0.7.1 fixes.
   Set each scenario's `fixed_build.min_version` to that RC. Preflight probes
   each fix:
   - `rite reply --help` mentions `--from-file` (SCRUM-69);
   - `rite request` exists (SCRUM-59);
   - `rite plan` exists (SCRUM-72);
   - `rite_ai.managers.reconcile` imports (SCRUM-64).

   The harness always tests the **installed** `rite`, with the checkout's
   virtualenv removed from PATH. Today's install is `0.7.0a6`.
2. **The DF16-patched yoloAI:** `~/.local/opt/yoloai-0.11.0-broad/yoloai`
   (reports `0.11.0-broad`). The harness puts it first on PATH itself.
   `--probe-df16` runs the WRITEUP #35 probe with a host-side control. It
   creates and destroys one throwaway sandbox.
3. **Ollama** serving `qwen3.8:latest`, and for `all_local` also
   `qwen3-32b-ctx32k:latest`. Preflight checks both.
4. **`gh` logged in**, able to create a private repo under `robbartoszewski`.
   **The GitHub App** (IDs in each `fleet.yaml`) installed on the new repo, or on
   all repositories.
5. **Credentials** in each new project, set by the operator: `github_token` and
   `github_app_key`, and for `mixed` also `claude` (via `claude setup-token`).
   `setup` prints the commands; the harness never handles a secret.

## The checks

| Check | Criterion | Scenarios | Status |
|---|---|---|---|
| `every_ticket_delivered` | every ticket reaches a delivered PR | both | **wired** |
| `pipeline_tickets_worked_by_gpu_workers` | each pipeline ticket worked and delivered by a GPU Worker only | both | **wired** |
| `plans_written_by_planner_approved_by_owner` | plan written by `planner`, approved by `lead`, every subtask accepted | both | **wired**¹ |
| `plans_approved_through_inbox` | approval asked through `lead`'s inbox (plan 3.3b) | both | PENDING SCRUM-72 |
| `owner_answer_reached_worker` | the Owner's answer is written into the sandbox, and is in the PR | mixed | **wired** |
| `killed_sandbox_recovered` | the killed sandbox is restarted or re-staged on the same ticket, which then delivers | both | **wired** |
| `recovery_went_through_manager` | …through the Manager's `rite request` | both | PENDING SCRUM-59 |
| `restart_releases_stale_claim` | the claim was held while `lead` was down and released after the restart | both | **wired** |
| `restart_did_not_escalate` | …without escalating | both | PENDING SCRUM-64 |
| `induced_failures_journaled` | each induced failure is journaled | both | PENDING SCRUM-71 |
| `done_ticket_never_offered` | the decoy is never claimed, started or requested | both | **wired** |
| `pipeline_stages_in_order` | every stage, in order (plan 3.3a) | both | PENDING SCRUM-72 |
| `no_forbidden_host_commands` | the harness ran no rescue command² | both | **wired** |
| `no_claude_in_the_fleet` | every unit in the project's config is `local:` | all_local | **wired** |

¹ The plan is read through the installed rite. When SCRUM-72 moves plan state
out of the project (plan 3.3b), update `observe.decomposition`.

² It cannot see a shell the harness did not open.

**PENDING is never a pass**: the verdict is INCOMPLETE. Each PENDING check reads a
record its fix will define. The reader is a stub in `hooks.py` that returns
`None`; implement that one function when the fix merges.

## What a run does

1. Preflight. Then start `lead`, and `planner` too while `start_planner_loop` is
   set, each with `--record-issues`, plus the `watch` window, all in the run's
   tmux server.
2. Every `poll_seconds`, it samples claims, Worker start requests, each pipeline
   ticket's plan (whenever it changed), and the Owner's question ledger.
3. **It plays the Owner** with `rite message lead "<qid> <answer>"`. That is the
   User's role, not a rescue. The Owner-answer ticket gets the per-run secret
   code, of which only the hash is in the repo. Any other question gets a
   neutral "use your judgement".
4. **Kill.** After `kill_worker` has worked `kill_after_minutes` on a ticket,
   the harness runs `yoloai stop` on its sandbox.
5. **Restart with stale state.** Once the kill is recovered and a Worker idles on
   a delivered ticket still holding its claim, the harness:
   - stops `lead`;
   - destroys that Worker's sandbox, which makes the claim stale;
   - starts `lead` again.
6. When every ticket is delivered and both inductions are done, it samples for
   four more polls, stops the supervisors, judges, and writes `report.md`,
   `report.json` and `demo.html`.

## Left open by the criteria

- **The all-local fleet's shape:** "a local Manager + GPU Worker(s)" cannot pass
  the review stage with one Manager (DD-3.5, RL-6). The harness uses two local
  Managers on two different models. Is that the intended fleet?
- **The Owner's question in `all_local`:** it has no Owner-answer ticket. A
  local Worker runs goose turns through the local tier, and whether it can ask
  the Owner a question (yoloAI's `question.json`) is unproven. Add one once the
  local path is known to support it?
- **`rite doctor` in `all_local`** still warns that no Claude login is stored
  for sandboxes, although no unit uses Claude. Is that a false positive?
- **Two Managers and no `remote`:** doctor says "no election can ever happen".
  It is not counted as a problem, and plan 3.3's local planner implies this
  shape.
- **"Recovered by the Manager (through SCRUM-59)":** today's recovery is the
  supervisor's own (SCRUM-38). `killed_sandbox_recovered` accepts it, and
  `recovery_went_through_manager` demands the 59 path.
- **"Kill"** is a session stop (`yoloai stop`). Should destroy, which tests
  re-staging, be added?
- **Who released the stale claim:** SCRUM-38's re-stage can also release it, so
  only `restart_did_not_escalate` (SCRUM-64's reports) can attribute it.
- **"Escalating"** has no definition the harness can observe yet (SCRUM-64).
- **The journal** (SCRUM-71): the Manager's journal, or supervisor events? And
  which inductions count?
- **Step review:** if it stays RL-7 in v0.7.1 (plan 5.4), is `step_reviewed` in
  the expected order, or dropped?
- **Who works the pipeline ticket** rests on a hint in the ticket text, and the
  check FAILs if it went elsewhere. Should the harness pre-assign it instead?
- **Whether `planner` needs its own `rite start`** (plan 3.3, "to confirm"). This
  is `start_planner_loop` in each `fleet.yaml`.
