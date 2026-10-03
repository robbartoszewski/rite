# A GPU Worker runs a subtask inside its sandbox

**Status: measured 2026-10-03 on this machine (macOS 26.2, yoloAI 0.11.0
`95a6b8e`, seatbelt, Ollama on loopback). The wiring is SCRUM-54; the CLI half
is SCRUM-55.**

**Citation convention:** a bare `§` means a section of `SPEC.md`; this note's
own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

## 0. What was wrong, in one line

`in_sandbox.exec_launcher`, `in_sandbox.instruction_dir_for` and
`sandbox.goose_path_root` were written, measured against a real sandbox (OL1)
and unit-tested — and had **no caller in `src/`**. So the local tier ran at
MANAGER tier (`local/step.py`: `worker=manager`, `workspace=<project root>`,
the operator's own tree), and a mixed Claude+GPU fleet was *configurable* since
OL3 gave `WorkerManifest` its five engine keys and **not runnable**.

## 1. The split: agent inside, verify and commit outside

The agent runs INSIDE the sandbox. The verify and the commit run on the HOST,
against the sandbox's own copy. Three measurements, each with its control.

### 1.1 The work is in the copy, not the host tree

Fixture under an **ungranted** root (`~/rite-probe-scrum54/wd`, deliberately not
under `/tmp`, which is granted to every sandbox here — a fixture there would
have been written THROUGH and the measurement would have meant nothing).

```
$ yoloai exec s54probe -- sh -c 'echo written-inside > from_inside.txt;
                                 echo mutated-inside > marker.txt; pwd; ls'
/Users/.../sandboxes/s54probe/rw/work/^sUsers^s...^swd
from_inside.txt
marker.txt

CONTROL — the ORIGINAL host workdir:
$ cat ~/rite-probe-scrum54/wd/marker.txt
original                       # untouched: the sandbox worked in its copy
```

⚠ So a verify run in the host's project root would test a tree the model never
touched. It would pass or fail on something else entirely, and **both answers
are lies about the work.**

Note the `pwd`: the copy's path is the **same string** inside and on the host,
which is why nothing has to be rewritten on argv.

### 1.2 The host can reach the copy, with the sandbox still RUNNING

```
$ cd "<copy>"
$ git status --porcelain
 M marker.txt
?? from_inside.txt                     # the host sees the turn's edits
$ git checkout -b T-1-s1 && git add -- … && git commit -m …
COMMIT OK: a5adad5 on T-1-s1           # sandbox was active throughout
```

So `SubprocessVerifier` and `GitCommitter` are **reused unchanged**, pointed at
the copy. No second verify mechanism, no second committer.

### 1.3 It is how a Claude Worker already hands back

`publishing/deliver.py` stops the sandbox, finds that same copy
(`_sandbox_copy`), and collects `refs/heads/<ticket>` from each module clone in
it. A GPU Worker that commits there hands back through **the same path** — which
is what "the same way a Claude Worker does" has to mean to mean anything.

⚠ **And it keeps RL-7 stronger than the alternative.** "rite runs the verify" is
the one thing this tier does not delegate. Run through `yoloai exec`, the verify
would execute inside the box the agent has had unconstrained `auto`-mode run of
— its PATH, its interpreters, its installed tools. Run on the host against the
copy, the agent can still edit the test FILES (they are its work) but cannot
reach the toolchain that judges them.

## 2. The branch is the TICKET

`deliver` collects `refs/heads/<ticket>` and **nothing else**:

> `tip = _sha(clone, f"refs/heads/{ticket}")` … "the Worker made no branch
> `<ticket>`"

The harness's own `branch_for` is `rite-local/<ticket>/<subtask>`, one branch per
subtask, which something later composes (RL-T30 — **unbuilt**, no `compose` for
it exists). A placed turn therefore passes `branch=<ticket>` to `run_subtask`,
and the subtasks of one ticket — run one at a time, in plan order, in one copy —
compose by being committed in sequence onto it.

⚠ That is **not** a shortcut around RL-T30. It is why the Worker path does not
need it; the Manager path still does.

## 3. Two defects the wiring surfaced

### 3.1 A secret in the operator's shell killed every sandboxed turn

`GooseAgent.run` builds `dict(os.environ)` — correct on the host, where goose
needs the operator's `PATH` and `HOME`. `exec_argv` refuses to put a
secret-shaped name on argv — correct in a sandbox, since SB12 measured a
sandbox's environment readable from other sandboxes on this machine.

Together, with `GITHUB_TOKEN` merely PRESENT:

```
AgentReport.claimed_success : False
AgentReport.summary         : could not start goose: SecretOnArgv: GITHUB_TOKEN
                              was passed to a sandboxed turn …
AgentReport.infrastructure_fault : False     # ← and so it counted as an ATTEMPT
```

⚠ Two things make this worse than a bug. It would have fired on **every
yoloAI-launched session**, whose launch line exports every token the operator
holds — so the path would have been dead on the machine it was built for. And
`infrastructure_fault` is False, so RL-47 ("an endpoint that was down is not an
attempt") broke too: a turn refused by rite's own leak guard spent the subtask.

`GooseAgent.inherit_environment` closes it. **Nothing is lost by not
inheriting**, and that is measured, not assumed: `env KEY=VAL cmd` ADDS to the
environment it was handed (`env -i` would replace it, and is not used), so the
turn still gets the sandbox's own `PATH` —

```
$ yoloai exec <box> -- env -C app GOOSE_…=… sh -c 'echo $PATH'
/…/.venv/bin:/opt/homebrew/opt/node@20/bin:/Users/robba…
```

### 3.2 The turn ran one directory above the repository

`yoloai exec` has **no `--cwd`** (checked on 0.11.0: its flags are the global
ones and nothing else) and starts in the copy's ROOT. A Worker's workdir is
`workers/<name>/` while its modules are clones one level down, so the turn would
have run the model against the directory *above* the repository it was meant to
edit.

`exec_argv` gained `subdir`, placed with **`env -C`** and not `sh -c 'cd … && …'`:

```
$ yoloai exec s54probe -- env -C sub/module FOO=bar pwd
/…/rw/work/^s…^swd/sub/module          # macOS's own env honours -C
```

⚠ `local/runners.py` makes "no shell" a property of this tier — a verify is
`shlex.split` so a decomposer cannot smuggle a second command — and a launcher
reaching for a shell would take that property away from the half that runs a
MODEL's turn. `env` was already being run to place the environment, so this adds
a flag and not a process. An absolute `subdir` is **refused**: it would be a
host path, which is the mistake `exec_launcher`'s docstring measured.

## 3.3 The sandbox vanishing mid-turn, and RL-47 not being in force

**The race, and rite is what causes it.** `placement_for` checks the sandbox is
active and the turn starts afterwards — and the thing that stops a Worker's
sandbox in between is `publishing/deliver.py`, which stops it FIRST and by
design ("so the Worker cannot commit between the collect and anything that
later trusts it"). A supervisor cycle stepping a subtask while a delivery is
honoured is the ordinary case.

Left alone it was silent and wrong in the worst direction. `GooseAgent` never
reads an exit code — because `goose run` returns 0 for an unreachable provider
— so `yoloai exec`'s own refusal arrived as a turn that RAN, with yoloAI's
error text as the model's output; the verify then failed on an unchanged tree
and the subtask recorded a failed attempt.

⚠ **yoloAI's exit code is trustworthy where goose's is not, and that is
measured, not assumed:**

```
stopped sandbox   yoloai exec s54stopped -- echo hi    → exit 1, nothing ran
missing sandbox   yoloai exec no-such -- echo hi       → exit 1, "sandbox not found"
inner command     yoloai exec <live> -- sh -c 'exit 3' → exit 3   (passed through)
                  yoloai exec <live> -- sh -c 'exit 7' → exit 7
```

So the code alone cannot answer the question — `1` is equally "goose exited 1"
and "there is no sandbox". It is only a reason to ask yoloAI a second, definite
one. `yoloai exec --json` is not available (`"--json is not supported for
interactive command \"exec\""`), so the accessor is the existing
`yoloai ls --json` reader, extracted to `sandbox.sandbox_status_named` rather
than parsed a second time.

Measured against the real binary, with both controls:

```
sandbox stopped between check and turn → SandboxGone raised; the agent reports
  infrastructure_fault=True, so the subtask spends NO attempt
CONTROL live sandbox, inner command exits 3 → returncode 3, stdout "ran-inside",
  no raise      # a failing turn is a result, not a race
CONTROL yoloAI unaskable (known=False) → no raise
  # "I could not check" is not "the sandbox is gone"
```

**And the rule it depends on was not in force anywhere.**
`Outcome.counts_as_attempt` states RL-47 — "work counts, and a turn that never
happened does not" — and had **no reader in `src/`**: `step._record` wrote
`attempts=subtask.attempts + 1` unconditionally. So an endpoint that was down,
a sandbox a delivery had just stopped, and a launch rite's own leak guard
refused each spent one of the subtask's attempts, which its own docstring calls
out as the thing not to do: "it is a machine that was not ready, and counting it
would retire a subtask nobody tried". `_record` now asks the property.
`GooseAgent`'s launch-failure branch was also missing `infrastructure_fault`,
so even a raised launch counted — both halves are closed.

## 4. The engine environment, verified from INSIDE a real Worker

Against the real placement for a real `rite sandbox start`ed Worker:

```
the turn's working directory IS the module clone   /…/^sworkers^sgpu1/app
rite's env arrived    qwen3.8:latest / 32768 / /…/rw/rite/goose
goose is on PATH inside                            /opt/homebrew/bin/goose
Ollama answers on loopback from inside             HTTP 200
PATH is the SANDBOX's, though rite forwarded none  /…/.venv/bin:/opt/homebrew/…
the clone is a real repo inside                    main
```

And the window rite declares is the window the model is served with:

```
$ ollama ps
NAME            SIZE   PROCESSOR  CONTEXT
qwen3.8:latest  17 GB  100% GPU   32768
```

## 5. What a multi-module local Worker does: REFUSE

A `Subtask` carries `scope` — paths — and **no module**. With two clones in the
copy, `src/x.py` names a file in both, and rite would have to guess which
repository to run the verify in and which to commit to. A wrong guess commits
the work to the wrong module's branch, where `deliver` collects it into the
wrong project checkout — **silently, since both halves succeed.**

So one module runs, and more than one is a named refusal. Giving a subtask a
module is a decomposition-format change and belongs to its own ticket.

## 6. ⚠ What is NOT measured: a live agentic GPU turn

**DF16 (the Worker sandbox escape) is open on this machine**, re-measured here
with its controls against yoloAI 0.11.0 `95a6b8e` — the exact commit DF16 names:

```
CONTROL  direct write to an ungranted host path, from inside:
  touch ~/rite-df16-marker.txt → "Operation not permitted", exit 1
  marker exists? no                       # the sandbox IS enforcing

ESCAPE   through a tmux server started OUTSIDE the sandbox:
  tmux -S /private/tmp/df16.sock ls          → connects, exit 0
  tmux -S … new-window "touch <same path>"   → exit 0
  marker exists? YES                      # the outside server ran it as the operator
```

(The socket path must be ≤103 bytes or tmux reports "File name too long" and the
probe reads as contained when it is not.)

Robert's gate (2026-09-29) holds the yoloAI Worker run while DF16 is open, so no
agentic model was run inside a sandbox for this work. What IS measured is the
whole path with the model replaced by a **scripted command of the same argv
shape** through rite's own `exec_launcher`, plus everything in section 4. The
remaining step — a real `goose` turn against `qwen3.8` inside a Worker — is
gated on a yoloAI binary carrying the path-scoped network deny, which cannot be
built on this machine (no Go toolchain).

## 6.1 What a review caught that the tests did not

A `/code-review` pass over the branch found five things worth recording,
because four of them are the same shape: a rule that was *stated* somewhere and
not *in force*.

- **`== "active"` was a positive test on a mostly-AGENT field.** `yoloai ls
  --json`'s `status` is "active=working, idle=waiting at prompt, done=finished,
  failed=error" plus `stopped` — three of five describing a container that is
  UP. Measured: a local Worker's sandbox runs the `idle` NO-OP agent and reports
  `active`, so nothing was refused in the field; the real run above went through
  that gate and executed. But it was working by luck, every other reader in
  `src/` tests the negative, and `activity.py` says a word yoloAI adds later is
  printed as itself rather than mapped — so the next word was the one it broke
  on. Worse, the same literal in `exec_launcher` would have read every genuine
  `goose` failure on an `idle` sandbox as "the sandbox is gone", throwing the
  model's output away as a turn that never happened — inverting the RL-47 fix it
  was added to serve. Now `SandboxStatus.container_is_down`.
- **A timeout was recorded as a turn that never happened.** The new
  `infrastructure_fault=True` on the launch-failure branch also caught
  `TimeoutExpired`, so a turn that ran the full twenty minutes and may well have
  edited files cost no attempt — and a subtask that hangs every time would be
  retried for ever.
- **`run_subtask`'s own "nothing ran" returns still spent an attempt.** The
  claim-conflict path says so in its own note — "not started, so nothing here
  counts as an attempt" — and left `infrastructure_fault` False, so a subtask
  blocked by a neighbour's claim could be retired without ever being tried. Same
  for the unapproved-plan return.
- **Level 2 was not placed at all** (see below).
- Two smaller ones: the placed-turn line said "committed to `<branch>`" even
  when the verify failed and no branch existed, and the sandbox NAME and its
  STATUS were asked independently — `existing_sandbox_name` prefers the scoped
  name while `worker_sandbox_status` answers for either, so on a machine holding
  both the gate could pass on one sandbox and the turn run in another.

## 6.2 Level 2 is placed too

`step._approach_for` resolved the MANAGER's role and ran its Goose on the HOST
with `workspace=<project root>`. For a placed subtask that is wrong three ways,
and the first is the serious one:

1. **A write-capable auto-mode model turn in the operator's real project
   tree** — the containment this whole module exists to provide, skipped by the
   planning half of the same subtask.
2. **It planned against the wrong tree.** The subtask's scope paths are relative
   to the module clone and do not exist at the project root, and the earlier
   subtasks' commits are not there either. Observed in the real run: the model
   reasoned about `app/greet.py` and `workers/gpu1/app/greet.py` and had to guess
   which it was being asked about.
3. **It ignored OL3's model split.** `worker_decomposition_model` says a local
   Worker plans with its OWN model ("one GPU, nothing to gain from loading a
   second") and a Claude one with Opus — and read from the Manager, a CLAUDE
   Manager driving a GPU Worker returned no approach at all, which is the
   headline mixed-fleet configuration. The function had no caller in `src/`.

`Placement.approach` now carries a ready-made Level 2 that runs inside the
sandbox, with the Worker's own model, against the module clone. ⚠ It stays
advisory and fail-open: a Level 2 that could not run is reported and the subtask
executes exactly as it would have without one.

`GooseProposer` needed the same `inherit_environment` fix as `GooseAgent`, for
the same measured reason — it also built `dict(os.environ)`, so the planning
half would have been dead on every machine whose shell holds a token.

## 7. Carried limitations

- **One module per local Worker** (section 5).
- **RL-T30 is still unbuilt** for the Manager-tier path (section 2).
- **A live agentic GPU turn inside a sandbox** (section 6), gated on DF16.
