# The v0.7.1 acceptance gate, end to end

The harness for `docs/design/V071_DOGFOOD_FIXES.md` §4.2: a throwaway app, a fresh
rite project and board, a mixed fleet, and the gate's pass/fail criteria as
automated checks. It is an instrument. Nothing in `src/` imports it, and the gate
is not a CI test. CI holds only the judge, in
`tests/test_the_v071_acceptance_harness_detects_each_failure.py`: each check
passes on a fixture run that met its criterion, and fails on one with the defect
it exists to catch.

**Status: scaffolded, not run.** `run` refuses until preflight is clean. That
needs the merged fixes in an installed build, so the gate is never run as a
partial fleet against half-built fixes.

## What it builds

- **The throwaway app**, `app/`: `tally`, a tiny Python library. Each ticket's
  agreed verify is a test that ships failing and passes once the ticket is
  delivered.
- **A fresh project per run**, in `~/AI/rite-e2e-runs/<run-id>/`:
  - its own mail, publish and refinement-key directories (`RITE_MAIL_DIR`), and
    its own `RITE_HOME_DIR`;
  - its own sandboxes, named from the project;
  - its own private tmux server for the supervisors.
  
  Nothing it creates is shared with the yoloAI dogfood, Bentora or any other
  project. Credentials stay the operator's, from the normal credential store.
- **A fresh board and PR target:** one private GitHub repo per run,
  `robbartoszewski/rite-e2e-v071-<run-id>`. rite has no offline board (only Jira
  and GitHub), and `pull_request` delivery needs a GitHub origin.
- **The fleet** (`fleet.yaml`):

  | Name | Role | Engine |
  |---|---|---|
  | `lead` | Owner, plan reviewer (`lead` preset) | Claude |
  | `planner` | writes the GPU Worker's plans (`planner` preset) | `local:small`, goose, `qwen3.8:latest`, 32k |
  | `alpha` | Claude Worker | Claude |
  | `gpu1` | GPU Worker | `local:small`, goose, `qwen3.8:latest`, 32k |

- **The tickets** (`tickets.yaml`), each REFINED at setup with
  `rite refine accept`:
  - `claude-total` and `claude-format` (Claude Worker);
  - `gpu-slug` (GPU Worker);
  - `owner-currency`: its value is a per-run random code only the Owner knows.
    The repo holds only its sha256, so a passing verify proves the answer
    reached the Worker;
  - `done-decoy`: closed on the board but still labelled `scheduled` and
    `ready-to-work`, with a valid refinement record. That is the SCRUM-73 shape.

## Commands (from the rite repo root)

```sh
uv run python -m tools.e2e_v071.harness plan                  # fleet, tickets, checks
uv run python -m tools.e2e_v071.harness preflight --probe-df16
uv run python -m tools.e2e_v071.harness setup --create-repo   # creates the private repo + board
#   then set the credentials setup prints, and re-run `rite doctor` in <run>/app until clean
uv run python -m tools.e2e_v071.harness run <run-dir> --probe-df16
uv run python -m tools.e2e_v071.harness check <run-dir>       # re-judge offline from the logs
uv run python -m tools.e2e_v071.harness teardown <run-dir> [--delete-repo]
```

`setup --offline` is a dry setup with no repo, board or network. It checks that
`rite init`, the fleet commands and the config edits are accepted by the
installed build. `run` refuses an offline run directory.

Exit codes: 0 PASS, 1 FAIL, 2 INCOMPLETE (some check PENDING), 3 refused
(preflight).

## Prerequisites at run time

1. **The fixed build, installed:** `uv tool install` of the merged v0.7.1 fixes.
   Set `fleet.yaml` `fixed_build.min_version` to that RC. Preflight probes each
   fix:
   - `rite reply --help` mentions `--from-file` (SCRUM-69);
   - `rite request` exists (SCRUM-59);
   - `rite plan` exists (SCRUM-72);
   - `rite_ai.managers.reconcile` imports (SCRUM-64).

   The harness always tests the **installed** `rite`. It removes the active
   virtualenv from PATH, so the checkout's dev `rite` cannot shadow it.
2. **The DF16-patched yoloAI first on PATH:**
   `~/.local/opt/yoloai-0.11.0-broad/yoloai`, which reports `0.11.0-broad`. The
   harness puts it first itself. `--probe-df16` runs the WRITEUP #35 probe:
   - a host tmux socket must be refused from inside a throwaway sandbox;
   - the same socket must work on the host (the control);
   - it creates and destroys one sandbox named `e2e-df16-probe-<ts>`.
3. **Ollama** serving `qwen3.8:latest`. rite makes the pinned-window twin
   `rite-ctx32768-qwen3.8-latest` itself.
4. **`gh` logged in**, with permission to create a private repo under
   `robbartoszewski`.
5. **The GitHub App** (IDs in `fleet.yaml`) installed on the new repo, or on all
   repositories.
6. **Credentials in the new project's namespace**, set by the operator.
   `setup` prints the commands; the harness never handles a secret:
   - `github_token`
   - `github_app_key`
   - `claude`, via `claude setup-token`

## The checks

| # | Criterion (§4.2) | Check | Status |
|---|---|---|---|
| 1 | every ticket reaches a delivered PR | `every_ticket_delivered`: a `delivered` event with "PR <url>" for each work ticket | **wired** |
| 2 | (the GPU ticket really ran on the GPU Worker) | `gpu_ticket_worked_by_gpu_worker`: started and delivered only by `gpu1` | **wired** |
| 3 | GPU plan written by `planner`, approved by `lead` | `gpu_plan_by_planner_approved_by_owner`: plan `decomposed_by`, `approved_by`, every subtask accepted | **wired**¹ |
| 4 | …through `lead`'s inbox, no host command (§3.3b) | `plan_approved_through_inbox` | PENDING SCRUM-72 |
| 5 | the Owner's answer reaches its Worker | `owner_answer_reached_worker`: answered → written into the sandbox (`_delivered`) → in the delivered PR | **wired** |
| 6 | a killed Worker sandbox is recovered | `killed_sandbox_recovered`: a restart or re-stage on the killed ticket, then that ticket delivered | **wired** |
| 7 | …by the Manager, through SCRUM-59 | `recovery_went_through_manager` | PENDING SCRUM-59 |
| 8 | a Manager restart reconciles stale state | `restart_releases_stale_claim`: the claim was held while `lead` was down, and released after the restart | **wired** |
| 9 | …without escalating | `restart_did_not_escalate` | PENDING SCRUM-64 |
| 10 | the journal records each induced failure | `induced_failures_journaled`: collects generic matches now | PENDING SCRUM-71 |
| 11 | no Done ticket is offered again | `done_ticket_never_offered`: the decoy is never claimed, started or requested, across every sample | **wired** |
| 12 | the GPU ticket's record shows every stage in order | `gpu_pipeline_stages_in_order` | PENDING SCRUM-72 |
| 13 | no host command run by a person | `no_forbidden_host_commands`: an audit of the harness's own command log | **wired**² |

¹ The plan is read through the installed rite (`decomposition.read`). §3.3b moves
plan state out of the project, so update `observe.decomposition` when SCRUM-72
lands. Until then it reads the old place, and the check FAILs with that reason.

² It proves the harness ran no rescue command (`rite sandbox …`, `deliver`,
`local`, `plan`, `request`, `release`, `done`, `publish`, board moves). It
cannot see a shell the harness did not open.

**PENDING is never a pass.** Each PENDING check reads a record its fix defines.
The reader is a stub in `hooks.py` that returns `None`. When the fix merges,
implement that one function and the check starts judging.

## What the run does

1. Preflight, then start `lead`, and `planner` too while `start_planner_loop` is
   set, each with `--record-issues`, in the run's private tmux server.
2. Every `poll_seconds`, until `deadline_minutes`, it samples three things:
   - claims;
   - every Worker start request (they are consumed when honoured, so only a
     sampler sees them all);
   - the Owner's question ledger.
3. **It plays the Owner** with `rite message lead "<qid> <answer>"`. That is the
   User's role, not a rescue. The `owner-currency` question gets the secret
   code; any other question gets a neutral "use your judgement" reply.
4. **Kill.** After the Claude Worker has worked `kill_after_minutes` on an
   ordinary ticket, the harness runs `yoloai stop` on its sandbox.
5. **Restart with stale state.** Once the kill is recovered, and a Worker idles
   on a delivered ticket while still holding its claim, the harness:
   - stops `lead`'s supervisor (Ctrl-C);
   - destroys that Worker's sandbox (sandbox gone + work delivered = §3.2's
     stale claim);
   - starts `lead` again.
6. When every work ticket is delivered and both inductions are done, it samples
   for four more polls, stops the supervisors and judges. The verdict goes to
   `<run>/report.md` and `report.json`.

## Left open by the plan's criteria

- **"Recovered by the Manager (through SCRUM-59)".** Today's recovery is the
  supervisor's own (SCRUM-38: restart in place or re-stage), with no Manager
  request. Check 6 accepts either; check 7 demands the 59 path. Does the gate
  require the Manager's request, or is the supervisor's recovery enough?
- **"Kill".** It isn't defined. The harness stops the session (`yoloai stop`;
  the sandbox survives). Destroying the sandbox (`yoloai destroy`) would test
  re-staging instead. Which one, or both?
- **Who released the stale claim.** Check 8 sees only that the claim was
  released. Recovery's own re-stage (SCRUM-38) can also release a claim when the
  sandbox is gone, so check 8 alone does not prove SCRUM-64 did it. Check 9,
  which reads 64's reports, is what attributes it.
- **"Reconciles without escalating".** It needs a definition of "escalating"
  that the harness can observe: a message to the Owner, a Slack post, or a
  journal entry. That depends on SCRUM-64.
- **"The journal records each induced failure".** Is it the Manager's journal
  under `--record-issues`, or the supervisor-written events of SCRUM-71? And
  which inductions count?
- **"Every stage".** §3.3a's list includes step review. If step review stays
  RL-7 in v0.7.1 (§5.4), does `step_reviewed` appear in the log, or is it
  dropped from the expected order?
- **"The Owner's answer"** could be a refinement-time answer or a Worker
  question. The harness tests the Worker-question path, since only that one
  reaches a Worker. The tickets are refined at setup with `rite refine accept`.
- **Who works the GPU ticket.** The Manager distributes, so `gpu-slug` going to
  `gpu1` rests on a hint in the ticket text, and check 2 FAILs if it went
  elsewhere. Should the harness pre-assign it instead, so the gate does not
  depend on the Manager reading the hint?
- **Whether `planner` needs its own `rite start`** (plan §3.3, "inferred, to
  confirm"). This is `start_planner_loop` in `fleet.yaml`.
- **"No host command run by a person"** can only be audited for the harness's own
  commands.
