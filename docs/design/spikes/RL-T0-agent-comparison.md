# RL-T0 — which agent binary holds a rite task loop on a local model

**Citation convention:** a bare `§` means a section of `SPEC.md`; this
note's own sections are written out, because the citation gate's regex is
context-free and would match a real SPEC section.

**Measured 2026-09-23/24. Goose 1.51.0, opencode 1.18.32, Open Interpreter
0.0.45, against Ollama 0.34.2 on an M-series Mac (14 cores, 48 GB).**

This is the spike the release plan called B1 and B2. It ran, and it moved the
plan's conclusions rather than confirming them. **Everything below is a
measurement; where a thing could not be measured it says so.**

---

## 0. The finding that reframes every other number

⚠ **Ollama serves every model at a 4,096-token context window by default,
and that — not agent behaviour — produced nearly every failure in this
exercise, including the ones in the brief this spike started from.**

`OLLAMA_CONTEXT_LENGTH` was unset, so `ollama ps` reported `CONTEXT 4096`
even for `qwen3:32b`, which itself declares `qwen3.context_length = 40960`
and `capabilities: ['completion','tools','thinking']`.

**That window is smaller than the agents' own prompts.** Measured on the
wire: opencode sends ~31 KB of system prompt plus tool schemas before the
task is added; Goose sends ~19 KB. Both overflow 4,096 tokens before the
work begins.

**Four separate "the model is broken" results dissolved when the window was
raised, with nothing else changed:**

| model | agent | 4,096 | 32,768 |
|---|---|---|---|
| `qwen3:32b` | opencode | no file, `FileSystem.makeDirectory /absolute/path/to` | **file created**, exit 0, 264s |
| `qwen3-vl:8b-instruct` | opencode | no file, `Write /marker.txt failed` | **file created**, exit 0, 32s |
| `qwen3.8:latest` | opencode | ⚠ *empty content, no tool call, no error* | **file created**, exit 0, 141s |
| `qwen3:32b` | opencode (benchmark) | 0/5, all five TIMEOUT at 900s | **5/5**, 2,363s |

⚠ **`qwen3.8:latest` is the one that matters most.** It was recorded as the
dangerous case — declares tool support, accepts the request, returns empty
content with **no error at all**. At a correct window it works. **There is no
"says yes and does nothing" model in this set**; there was a "says yes and is
given no room to answer" configuration.

**Only `gemma3:27b` fails for a real reason**, and it fails loudly at the
API: `registry.ollama.ai/library/gemma3:27b does not support tools`.

### How the window was raised, and how to undo it

Scoped to the measurement rather than changing the machine:

```
printf 'FROM qwen3:32b\nPARAMETER num_ctx 32768\n' > Modelfile
ollama create qwen3-32b-ctx32k -f Modelfile     # undo: ollama rm qwen3-32b-ctx32k
```

⚠ **This route does not work for Open Interpreter** — see section 3 of this note.

---

## 1. Tool calling · M1

Method: one prompt — *"Create a file named marker.txt in the current
directory containing exactly the text PLATYPUS42 and nothing else"* — scored
on **whether the file exists afterwards**, not on whether a tool call was
attempted.

| agent | model | window | file created |
|---|---|---|---|
| opencode | `qwen3:32b` | 32,768 | **yes** (264s) |
| opencode | `qwen3-vl:8b-instruct` | 32,768 | **yes** (32s) |
| opencode | `qwen3.8:latest` | 32,768 | **yes** (141s) |
| opencode | `gemma3:27b` | 4,096 | no — loud API error, model lacks tools |
| Open Interpreter | `qwen3:32b`, `qwen3:8b` | 4,096 | **no**, and **exit 0** |

⚠ Open Interpreter was given a corrected model catalog declaring
`supported_parameters: ["tools","tool_choice"]` and `shell_type:
unified_exec`; without one it tells the model its tools are *"for agent
management, web searches, and goal tracking"*. Even with it, no file.

---

## 2. Headless plus resume, with context reaching the model · M2

⚠ **Method matters here, because the obvious method gives the wrong answer.**
Neither the agents' own token counts nor a "resuming" banner were trusted. A
logging HTTP proxy was placed between agent and Ollama, recording **the
messages in the actual request body**.

| agent | messages sent T1 → T2 → T3 | marker token in payload | model's answer on resume |
|---|---|---|---|
| **Goose** `run -n <name> -r` | 2 → **4** → **6** | every call | **correct**, even at 4,096 |
| **opencode** `run -s <id>` | 2 → **4** → **6** | every call | wrong at 4,096; **correct at 32,768** |
| **Open Interpreter** `exec resume` | — | — | **cannot reach a local model at all** |

### ⚠ RETRACTION, for the record

**The claim that Goose's resume does not replay conversation context into the
model is withdrawn. It is false.** It never reached this plan, but it was
load-bearing in the brief this spike was commissioned from, and it would have
excluded the recommended tool.

What produced it: flat `inputTokens` across resumes (2050 / 4060 / 2050 /
2050) plus a wrong answer, read as "the transcript is on disk but not in
context". **The proxy shows the transcript is sent every time.** The flat
counts are an accounting artefact and the wrong answer was the 4,096 window
truncating the history away — note that the reported 4060 is the 4,096
ceiling.

**The general lesson is the method, not the number: an agent's self-reported
token accounting is not evidence about what the model received.** Capture the
request.

### Is Goose's `-n <name>` a real handle or only a label?

**A real handle. Measured**, because half the argument for Goose is that rite
already has a stable name per Manager:

- session `alpha` learned `PLATYPUS42`; a *later* session `beta` learned
  `WOMBAT99`;
- `goose run -n alpha -r` returned **PLATYPUS42** — it resolved the name, not
  "the most recent session";
- `goose run -n beta -r` returned **WOMBAT99**;
- `goose run -n <never-used> -r` **exits 1** rather than silently starting a
  new session;
- resuming `alpha` **from a different working directory** still returned
  `PLATYPUS42`;
- `sessions.db` carries `sessions.name` and `sessions.user_set_name` as
  columns beside the generated `id` (`20260924_1`).

**So rite can derive the handle from the Manager name and never store one.**

---

## 3. Cycle end · M3

Five cases. opencode and Goose at 32,768; Open Interpreter at 4,096 (section 0 of this note).

| case | opencode | Goose | Open Interpreter |
|---|---|---|---|
| real work | **0** — file created | **0** — file created | **0** — ⚠ *no file created* |
| no-op | 0 | 0 | 0 |
| model not found (404) | **1** + `{"type":"error"}` event | ⚠ **0** | **1** |
| provider unreachable | **1** | ⚠ **0** | untestable — see below |
| model silently declines | 0 | 0 | 0 |

**Goose returns exit 0 for a missing model and for an unreachable provider.**
Its exit status cannot be a cycle verdict.

**opencode returns 1 for both**, and emits a distinct `error` event. Its
`--format json` stream carries `step_start` / `tool_use` / `step_finish`
(with `tokens`) / `error` — `tool_use` distinguishes *acted* from *answered*.

**Open Interpreter's `--json` is the cleanest stream of the three**:
`thread.started` (with `thread_id`), `turn.started`, `item.completed`
(`reasoning` | `agent_message` | `error`), and **`turn.completed` carrying
`usage`**. ⚠ But `turn.completed` fired on a turn that created nothing —
**it marks that the turn ended, not that the work happened**, which is
exactly why RL-7 and R7 have rite run the verify itself.

⚠ **No agent distinguishes "did the work" from "talked about it" by exit
code.** That is not a defect to fix in the adapter; it is the reason
`harness.run_subtask` derives `accepted` from the verify alone.

---

## 4. Benchmark · M4

`tools/rite_local_bench/tasks.py`, the five tasks excluding `ALREADY_PASSING`,
on a `qwen3:32b`-class model, machine otherwise idle, runs serialised.

| agent | window | passed | wall | per task |
|---|---|---|---|---|
| Goose | 4,096 | 4/5 | 1,860s | — |
| **Goose** | **32,768** | **5/5** | **1,958s** | 613 / 451 / 330 / 317 / 246 |
| opencode | 4,096 | **0/5** — all TIMEOUT at 900s | 4,501s | — |
| **opencode** | **32,768** | **5/5** | **2,363s** | 510 / 403 / 441 / 623 / 385 |
| Open Interpreter | 4,096 | **0/5** | 460s | exit 0 and `edited=False` on every task |

⚠ **One number was measured and discarded**, disclosed because discarding it
quietly is the failure this spike exists to avoid: a first 32,768 run
recorded `add-function` as TIMEOUT at 900s. A second session was running its
own benchmark on the same GPU concurrently. The run was killed, the GPU left
to clear, and the task re-run serialised — **510s and a pass**. Concurrent
inference invalidates wall-clock comparison as thoroughly as the context
window invalidates correctness.

**RL-T0's thresholds** (set before the runs, per RL-45): fewer than 7 of 10
completing, or any dishonest report, and the tier does not exist as designed.
On the 5-task subset at a correct window **both Goose and opencode passed
everything they attempted, with no dishonest report** — every pass had
`edited=True` and a passing verify. ⚠ **The full ten-task set was not run**;
these five are the comparable subset.

---

## 5. Open Interpreter — excluded, and why

**Excluded on the resume finding, which is structural.**
`interpreter exec resume` accepts **none** of `--oss`, `--local-provider`,
`-m` or `-C` — those exist only on `exec`. With every lever `resume` does
accept (`-c model_provider`, `-c oss_provider`, `-c model`, a `config.toml`,
`CODEX_HOME`), it dials `wss://api.openai.com/v1/responses` and fails 401.
**A thread created against Ollama cannot be continued against Ollama.** For a
design whose mechanism is resuming a session, that is disqualifying by
itself.

⚠ **An earlier exclusion of this tool, on an AGPL-3.0 licence claim, was
false** — and so was the replacement reason, "REPL-first, no
turn-per-invocation contract". It has `interpreter exec` and
`exec resume <id>`, which is exactly that contract. The tool was excluded
twice for reasons that were not true before being excluded for one that is.

### Provenance — for Robert's judgement, not as a verdict

rite would ship this as a credential, so it is stated plainly:

- the binary self-identifies as `interpreter 0.0.45`, "Open Interpreter";
- it is **byte-identical** (sha256 `56f7c62aa26e48c9a36e5ce8ee98188cf628cc6da66f8edc23fe037c80e0ac82`) to a
  release asset named `codex-aarch64-apple-darwin`;
- its strings carry both `github.com/openinterpreter/openinterpreter` and
  `github.com/openai/codex`, plus `CODEX_INTERNAL_ORIGINATOR_OVERRIDE`,
  `codex.process.start`, `codex-otel-shutdown`;
- it uses `CODEX_HOME` and `~/.codex` for configuration;
- **OpenTelemetry and a statsig exporter are compiled in.** Default-off per
  the string `"No OTEL exporter enabled in settings."` — present, not active.

It is an OpenAI Codex fork under a renamed project. That is a fact about what
would be installed, not an accusation.

### The `--oss` auto-pull hazard

`interpreter exec --oss -m <name>` treats the model name as a **registry**
name and pulls it. Measured precisely:

- a name matching nothing fails at manifest lookup in ~1s —
  `Pull failed: pull model manifest: file does not exist`;
- ⚠ **a typo that matches a different real registry model would download it**,
  unattended, with no confirmation;
- and a **locally-created model cannot be used at all** — which is why Open
  Interpreter could not be measured at a raised context window, and why it
  also ignores `OLLAMA_HOST` (the proxy saw zero calls from it).

---

## 6. What this spike did not measure

- The full ten-task benchmark — five tasks only.
- Open Interpreter at any window above 4,096 — no scoped route exists.
- Open Interpreter against an unreachable provider — `--oss` hardwires
  `localhost:11434` with no base-url lever.
- `GOOSE_MODE=auto` against an operation that requires approval. The
  benchmark ran to completion under `auto`, which is evidence it does not
  block in practice, but the documented contradiction — the permissions page
  says autonomous mode acts *"without requiring approval"*, the headless page
  says such operations *"will either use default permissions or fail"* — was
  not directly probed.
- Whether opencode's two open `--format json` defects (#26855, #49300)
  reproduce. rite would depend on that stream if opencode were chosen.
