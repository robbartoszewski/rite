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

## 7. Carried limitations

- **Level 2 uses the MANAGER's model for a placed turn.** `step._approach_for`
  resolves `decomposition_model_for(<the manager's role>)`, and a Worker has its
  own answer in `config.models.worker_decomposition_model`. Advisory and
  fail-open either way (an approach improves a turn, it is not a gate), so it
  costs correctness nothing today — but a Claude Manager driving a GPU Worker
  plans that Worker's approach with Opus rather than with the Worker's own
  model, which is the split OL3 made for exactly this reason. Its own ticket.
- **One module per local Worker** (section 5).
- **RL-T30 is still unbuilt** for the Manager-tier path (section 2).
