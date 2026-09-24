# v0.6.0 — release plan

**Status: PLAN, for Robert to decide from and a session to execute from.**
v0.5.1 shipped; tag `v0.5.1` at `4df79ed`, verified against the served bytes.

⚠ **THE SPIKE HAS RUN — 2026-09-23/24. B1 and B2 are DONE, and their result
moved this plan rather than confirming it.** Full evidence:
[`spikes/RL-T0-agent-comparison.md`](spikes/RL-T0-agent-comparison.md).
Three things every reader of this plan needs:

1. **`OLLAMA_CONTEXT_LENGTH` is a prerequisite, not a footnote** — see
   "Prerequisite" below. Ollama's 4,096 default is smaller than the agents'
   own system prompts, and it caused almost every failure attributed to
   models or tools.
2. **Goose and opencode both work**, and both score **5/5** on the benchmark
   at a correct window. The choice is no longer a capability question.
3. **Open Interpreter is excluded** — `exec resume` cannot reach a local
   model at all.

**Scope, confirmed 2026-09-23:** *"multi-manager is out, Slack is in."*
v0.6.0 is **fixes + Slack + local models (Ollama)**. Multi-Manager and Cursor
are v0.7.0; memory stays v0.7.0.

⚠ An earlier draft of this plan put multi-Manager first, on a recollection
Robert corrected when asked. `V060_MULTI_MANAGER.md` has been **renamed to
`V070_MULTI_MANAGER.md`** so the filename stops claiming a release it is not
in; its decisions are unaffected and still carried by SPEC §9.14.9 and D-79.
Its "Carried into 0.6.0" section is still 0.6.0 work and appears in the
carried table below.

---

## Prerequisite: `OLLAMA_CONTEXT_LENGTH`

⚠ **The local tier does not work at Ollama's default context window, and the
failure does not look like a configuration failure.** It looks like models
that cannot call tools and agents that lose conversation history.

Measured: Ollama serves every model at **4,096 tokens** when
`OLLAMA_CONTEXT_LENGTH` is unset, including `qwen3:32b`, which declares
`context_length = 40960` and `capabilities: ['completion','tools','thinking']`.
opencode sends ~31 KB of system prompt and tool schemas before the task is
added; Goose sends ~19 KB. **Both overflow the window before the work
begins.**

What that produced, all of it reversed by raising the window alone:

| symptom at 4,096 | at 32,768 |
|---|---|
| `qwen3:32b` writes to `/absolute/path/to/marker.txt` | file created correctly |
| `qwen3.8:latest` returns empty content, no tool call, **no error** | file created correctly |
| opencode benchmark 0/5, every task times out | **5/5** |
| a resumed session answers "not in the conversation history" | answers correctly |

**So this is a release deliverable, not advice.** Three things follow, and
they are items B7, B8 and C18 below: `rite doctor` must report the served
window; the local tier's documentation must state the requirement; and the
adapter should set `num_ctx` per request where the endpoint allows it rather
than trusting the operator's environment.

⚠ **Two different levers, and they are not equivalent.** Machine-wide
`OLLAMA_CONTEXT_LENGTH` changes the operator's Ollama; a derived model
(`ollama create … -f` with `PARAMETER num_ctx`) is scoped and reversible with
`ollama rm`. The spike used the second. **An adapter cannot rely on the
second**, because Open Interpreter — and possibly other tools — refuse a
model name that is not in the registry.

---

## Ordering: Slack or Ollama first

✅ **RESOLVED by the spike. B1 ran and passed: Slack is unblocked from the
local track entirely, and goes next on its own merits.** The coupling argued
below did not materialise — `goose run` exits at the turn boundary, the same
shape as `claude -p`, so the mail delivery point does not move. The reasoning
is kept because it is the argument that would apply again if the engine ever
changed shape.

**Order now: SLACK, then the local track.**

⚠ **The earlier draft of this plan put the whole local track first**, on the
premise that Ollama has no provider-managed session id and would therefore
stress the engine contract hardest. **The survey weakens that premise**: if
the adapter is thin glue over Goose, Goose supplies the session identity and
`goose run` exits at the turn boundary — the same shape as `claude -p` — so
the contract bends much less and the mail delivery point does not move.

**The spike showed Goose does what its docs say on the point that mattered.**
Slack is the release's headline, it is user-facing, and its code is insulated
by the mailbox, so it goes first.

The coupling the spike is testing, stated so the answer is checkable:

**The coupling runs Ollama → Slack, and not back.** Mail is delivered at the
**cycle boundary**, in the instruction composed for each cycle. That boundary
is an *engine* property: `claude -p` was chosen precisely because the
engine's own exit *is* the boundary, so one cycle is one invocation is one
turn. **Ollama has no provider-managed session id** — which is exactly why it
is the adapter worth building — so its continuity comes from somewhere else,
and if a local harness holds a multi-turn loop inside one process then the
inter-cycle boundary either moves or becomes rare. Mail timing moves with it.

Slack cannot move the engine contract in return. It is a second reader and
writer of files the supervisor already polls and composes, and the engine
never learns it exists.

⚠ **But be precise about what is at risk, because it is smaller than it
sounds.** The mailbox insulates Slack's *code*: Slack writes files, the
supervisor delivers them. If Ollama later changes cycle length, **no Slack
code changes** — what changes is the *latency a Slack user experiences*. So
the exposure is a behavioural surprise and a broken promise, not a rework.

That is why the answer is a spike rather than "do all of Ollama first". One
question needs answering before Slack is designed:

> For an engine with no provider-managed session id, what is a turn boundary,
> and does mail still have exactly **one** delivery point?

"One hook, not two" is the mailbox's load-bearing property — a second
delivery path was refused by name in 0.5.1. If a local engine forces a second
one, that is a design change to the mailbox, and it is much cheaper to know
now than after Slack has shipped on the assumption.

**After B1, Slack goes next**, because it is user-facing, its code is
insulated, and it builds on a mailbox that shipped and is tested.

---

## A. Slack

**The shape:** a second reader and writer of the v0.5.1 mailbox. `rite start
X` itself does the listening — **no daemon, no hosting.** The local chat
interface (`rite connect`) keeps working alongside it **permanently**, not
replaced by it.

⚠ **Two blocking design questions, both created by that last requirement.**
Neither is a build task and both need Robert. See "Decisions needed" 1 and 2.

| # | Item | What it is | Why this release | Depends on | Size |
|---|---|---|---|---|---|
| A1 | **Per-reader delivery for the outbox** | Today a reader is told *"delete one once you have relayed it so it is not shown twice."* With two permanent readers, whoever reads first deletes and the other never sees it. Needs per-reader read state, fan-out, or acknowledgement. | **Blocks Slack outright.** Robert's "both keep working permanently" requirement and the current delete-on-relay instruction cannot both hold. | Decision 1 | 1–2 sittings |
| A2 | **Slack credentials through the existing store** | A bot token is a credential; `credentials/` and the gate already have the shapes. | A token is the first thing the adapter needs and the last thing that should be improvised. | — | 1 sitting |
| A3 | **Listening inside `rite start`** | Poll Slack in the supervisor's existing wait loop — where `mail_waiting` already polls — and write inbound messages into the inbox via `send()`. | This is "no daemon" made concrete, and it reuses the validated writer rather than adding a second one. | A2, B1 | 2–3 sittings |
| A4 | **Posting the outbox to Slack** | The other direction, through whatever A1 decides. | Half a channel is not a channel. | A1, A2 | 1–2 sittings |
| A5 | **Say what happens when no Manager is running** | With listening inside `rite start`, a message sent while nothing runs stays in Slack. | ⚠ A user will hit this on day one and read silence as "it is broken". Needs to be either handled or stated. | A3 | ½–1 sitting |

---

## B0. Does an existing tool already do local sessions? — surveyed 2026-09-23, **MEASURED 2026-09-24**

**Robert's instruction: consider existing free-to-use session managers rather
than building session handling ourselves.** Same instinct that produced `rite
connect` — the chat is Claude Code, not something rite reimplements.

**Read from project documentation, not from reputation.** The question that
decides the adapter: does the tool give **both** a headless run for cycles
**and** an interactive session a User can attach to, addressable by something
stable that survives the process exiting — and is the licence compatible with
shipping rite as MIT.

| Tool | Conversation identity | Survives exit | Headless run | Human can attach to the SAME conversation | Needs | Licence |
|---|---|---|---|---|---|---|
| **Goose** (aaif-goose/goose) | `-n/--name` — **MEASURED: a real handle.** Resolves by name not recency, fails loudly on an unknown name, works from any working directory | **MEASURED** — `sessions.db` carries `name` and `user_set_name` beside the generated id | `goose run -i <FILE>` / `-t <TEXT>` / stdin — **MEASURED** | **MEASURED** — `goose run -n <name> -r` replays the full transcript (2 → 4 → 6 messages on the wire) | one binary; Ollama an explicit provider | **Apache-2.0**, Agentic AI Foundation (Linux Foundation) |
| **opencode** (anomalyco/opencode) | `--session <id>` — ⚠ the id is generated, so rite must capture and store it | yes | `opencode run "<text>"`, `--format json` (JSONL) — **MEASURED** | **MEASURED** — `-s <id>` replays the full transcript, identically to Goose | one binary | MIT |
| Aider | chat history files (`.aider.chat.history.md`) | as files, not as an addressable session | `--message` / `--message-file` with `--yes-always` | not natively — session management is recent/third-party | Python | Apache-2.0 |
| OpenHands | conversation id; `--resume <id>` / `--resume --last` | yes | ⚠ **an open PRD, "Support non-interactive CLI mode" (CLI issue #147)** — not settled | yes | Python/binary | MIT |
| Cline | `--id <session_id>`, `<millis>_<5 chars>` | yes | young CLI; ⚠ **open bug: `--json` mode cannot resume an existing session id** (#10856) | yes | Node; primarily an IDE surface | Apache-2.0 |
| Open Interpreter | `exec resume <id>` exists — ⚠ **MEASURED: it cannot reach a local model at all** | n/a | `interpreter exec` — works locally | **no** — `exec resume` accepts none of `--oss` / `--local-provider` / `-m`, and dials `api.openai.com` regardless of config | one binary (an OpenAI Codex fork — see the RL-T0 spike note) | **Apache-2.0** |

⚠ **Two corrections to the first draft of this survey, both found in
review.**

1. **opencode was missed entirely, and it clears the same four bars.** The
   first draft concluded "only Goose satisfies all four". **That conclusion
   was false**, and the mechanism is worth naming: the search stopped when it
   found a winner. opencode is now surveyed beside Goose.
2. **Open Interpreter is Apache-2.0, not AGPL-3.0** — it relicensed, and the
   licence was the first draft's stated reason for excluding it. The reason
   was wrong.
3. ⚠ **And the replacement reason was wrong too.** The second draft excluded
   it as "REPL-first, no turn-per-invocation session contract found". It has
   `interpreter exec` and `exec resume <id>` — exactly that contract. **The
   tool was excluded twice on reasons that were not true**, which deserves
   more attention than the tool does: both times a reason was written without
   running the binary. It is now excluded on a measured one, and that
   measurement should have come first.

### Verdict: **`agent: goose`** — measured, with opencode a viable second

⚠ **The first draft framed this as "should rite adopt a third-party agent
binary". That question was already closed, in code, before this plan was
written.** `config/managers.py:73` has
`_LOCAL_ONLY = ("endpoint", "model", "agent")` with the comment *"Only a
`local:*` engine may carry these, and it must carry all three"*;
`ManagerRole.agent` exists at :90 and is parsed at :228; `opencode` appears
in four test files; and `engine_probe.py` already checks the agent binary is
installed.

**So rite already commits to an adopted agent binary. The open question is
which value `agent:` takes** — `goose` or `opencode` — and that is a
configuration choice a user could in principle make per Manager, not a
dependency approval.

**Both clear every bar, and the benchmark separates them by almost nothing:**

| | Goose | opencode |
|---|---|---|
| benchmark @ 32,768 | **5/5**, 1,958s | **5/5**, 2,363s |
| benchmark @ Ollama default 4,096 | **4/5** | ⚠ **0/5**, every task timed out |
| resume replays context | yes | yes |
| handle | **caller-chosen name** rite already has | generated id rite must capture and store |
| exit code as cycle verdict | ⚠ **0 for a 404 model and an unreachable provider** | **1** for both, plus an `error` event |
| structured event stream | ⚠ none from `run` | `--format json`, ⚠ two open defects (#26855, #49300) |
| licence / governance | Apache-2.0, Linux Foundation AAIF | MIT |

**The deciding fact is the middle row: degradation at default configuration.**
At Ollama's out-of-the-box window Goose still does four-fifths of the work and
opencode does none. rite ships to operators whose Ollama is at its default —
that is exactly the machine this spike ran on, and it is how this plan's own
earlier conclusions went wrong. An agent that degrades is worth more than one
that is 17% faster once everything is correct.

**Handle shape supports the same answer.** `-n <name>` is a measured handle,
not a label: it resolves by name rather than recency, fails loudly on an
unknown name, and works from any directory. rite has a stable Manager name
already, so nothing needs minting or storing — and opencode's generated ids
would need exactly the `designated` machinery that exists for Claude.

⚠ **The cost of choosing Goose, stated rather than buried:** its exit status
is useless as a cycle verdict and it emits no structured event stream from
`run`. **rite must therefore not read Goose's exit code**, and relies on its
own verify — which `harness.run_subtask` already does, deriving `accepted`
from the verify alone (R7, RL-7). So the cost is real but already paid. What
it does mean is that **B3's contract cannot say "the engine's exit is the
boundary"** the way it can for `claude -p`; for Goose the boundary is process
exit, and the verdict is rite's verify.

### ⚠ The biggest risk to the local tier — **measured, and smaller than feared**

**Tool calling was the stated risk. It largely dissolved at a correct context
window.** Of the models tried, only `gemma3:27b` genuinely lacks tool
support, and it fails **loudly at the API** —
`does not support tools` — which is the safe failure. `qwen3:32b`,
`qwen3-vl:8b-instruct` and `qwen3.8:latest` all drove a real tool call and
created the file once the window was raised.

⚠ **`qwen3.8:latest` is the important one.** It had been recorded as the
dangerous case — declares tool support, accepts the request, returns **empty
content with no error**. That is the "says yes and does nothing" behaviour
the local tier must never default to. **At 32,768 it works.** There was no
such model; there was a window too small to answer in.

**What remains true**, from Goose's own providers documentation, verbatim:

> "goose extensively uses tool calling, so models without it can only do chat
> completion. **If using models without tool calling, all goose extensions
> must be disabled.**"

For Ollama it recommends "any model supporting tool-calling" (Qwen2.5 as the
example) and warns that the **default 4096-token context is too low**, so
`OLLAMA_CONTEXT_LENGTH` must be raised.

⚠ **That second sentence was already in this plan before the spike, quoted
from Goose's own documentation, and nobody acted on it.** It was the answer,
sitting in the risk section, while the spike spent most of a night attributing
its symptoms to models and tools. **Reading a warning is not the same as
configuring for it** — which is precisely why it is now items B7 and C18 and
a Prerequisite section, rather than a quotation.

**The residual risk is real but smaller than stated here before:** a model
genuinely without tool calling can only do chat completion, and `gemma3:27b`
is the measured instance — it fails loudly at the API, which is the safe
shape. A reviewer reports an experimental toolshim mitigation requiring a
second interpreter model; **that is not on the providers page and is recorded
here as reported rather than verified.** It was not needed for any model that
worked.

⚠ **It interacts with the harness correction below.** If extensions are
disabled the agent does *less*, which makes `harness.py` running the verify
itself matter **more**, not less.

### What the spike measured — all four, 2026-09-23/24

**Evidence: [`spikes/RL-T0-agent-comparison.md`](spikes/RL-T0-agent-comparison.md).**
The four questions this section previously posed, with their answers:

1. **Headless *and* named-resumable together.** ✅ **Works.**
   `goose run -n <name> -t "…"` then `goose run -n <name> -r -t "…"` — and
   the name is a genuine handle, not a label (it resolves by name rather than
   recency, and fails loudly on an unknown name).
2. **`GOOSE_MODE=auto`.** ⚠ **Not directly probed.** The five-task benchmark
   ran to completion under `auto` with no approval stall, which is evidence
   it does not block in practice. The documented contradiction stands
   unresolved: the permissions page says autonomous mode acts *"without
   requiring approval"*, the headless page says such operations *"will either
   use default permissions or fail"*. **Carried as a risk into B4, not
   closed.**
3. **Exit codes.** ❌ **Measured, and unusable.** Goose returns **exit 0** for
   a 404 model *and* for an unreachable provider. opencode returns 1 for
   both. See the verdict above: rite must not read Goose's exit status.
4. **Whether a local tool-calling model can hold a subtask.** ✅ **Yes, at a
   correct window.** `tools/rite_local_bench/tasks.py`, five tasks, a
   `qwen3:32b`-class model: **Goose 5/5 in 1,958s, opencode 5/5 in 2,363s.**
   Every pass edited a file and passed a real verify; no dishonest report.
   ⚠ The full ten-task set was **not** run.

⚠ **A retraction that belongs here even though it never reached this plan.**
The brief that commissioned this spike asserted that Goose's resume does not
replay conversation context into the model. **That is false.** A logging
proxy between agent and Ollama shows the full transcript sent on every
resumed call, for Goose *and* opencode — 2 → 4 → 6 messages. The original
evidence was flat `inputTokens` plus a wrong answer; both were the 4,096
window. **The method is the lesson: an agent's own token accounting is not
evidence about what the model received.**

### What this does to the engine contract, and to the ordering

⚠ **It weakens the premise that made Ollama urgent, and that is the most
important consequence here.** The argument for Ollama early was that Claude
and Cursor both have provider-managed session ids and **Ollama has none**, so
a contract validated only against the first two would encode that property by
accident.

**Goose supplies the missing identity.** A Goose-backed local Manager has a
stable session name that survives process exit, and `goose run` exits when the
turn ends — the same shape as `claude -p`. So the contract bends much less
than assumed, and **the cycle boundary does not move, which means mail
delivery does not move either.**

**That largely dissolves the Ollama → Slack coupling**, and it changes the
recommendation below.

### Is Goose a safe bet? — project health, measured 2026-09-23

Assessed from the repository and its release history rather than from
reputation. **Every figure below came from the GitHub API on the date above.**

| | |
|---|---|
| **Governance** | `block/goose` **redirects to `aaif-goose/goose`** — the repository was *transferred*, not merely badged. The Linux Foundation / Agentic AI Foundation status is real at the level that matters: the org owns the repo. |
| **Licence** | Apache-2.0 (API `license.spdx_id`). Not archived, not disabled, not a fork. |
| **Scale** | 54,588 stars, 6,299 forks, **452 contributors**. |
| **Alive?** | Last push **the day of this assessment**; `v1.52.0` released the same day. |
| **Cadence** | 10 minor releases in ~10 weeks (`v1.42.0` 2026-07-13 → `v1.52.0` 2026-09-23). Roughly weekly, with one patch release in that window. |
| **Active core** | ~12 humans committing in the last 30 days; top author 49 commits. A small active core under a long tail, which is ordinary for a project this size. |
| **Employer concentration** | Historically Block-originated and still partly so — of six active committers checked, two state Block publicly and four state nothing. **Not conclusively single-employer, and not conclusively not.** |
| **Responsiveness** | Of the last 20 closed PRs, 12 merged: six same-day, two at 2 days, then a tail at 13/32/33 days. Oldest *recently-updated* open issue is ~7 weeks old — the backlog is churned, not rotting. |
| **Breaking changes** | None flagged in the last 10 release notes. ⚠ Absence of the word is not proof of absence of breakage. |

**Read: a healthy, fast-moving project with real foundation governance** —
and the risks are the ordinary ones rather than red flags.

⚠ **The risk that actually applies to rite is the cadence, not the health.**
rite would depend on **CLI flags and behaviour, not an API**, against a
project shipping a minor release most weeks. Nothing in the last ten release
notes flags a break, but a surface moving that fast should be **pinned to a
tested version** rather than tracked.

⚠ **One fact worth noticing rather than filing:** the org move already
invalidated URLs. The install command published in some places points at
`aaif-goose`, and anything that hard-coded `block/goose` is already stale.
That is exactly the dependency-drift the adapter has to absorb.

---

## B0a. The abstraction layer, and what a replacement must implement

**Robert's constraint, verbatim:** *"if Goose disappears overnight, all we
need to do is implement its features but it's not a total local harness
refactor."*

So nothing outside the adapter may know Goose exists — not the supervisor,
not the mailbox, not the CLI, **and not the config keys a user sets.**

### What rite actually needs from an engine

Derived from what the supervisor already does with `claude -p`, not from what
Goose offers:

| # | Capability | Why rite needs it |
|---|---|---|
| R1 | **Run one turn non-interactively from an instruction** | The cycle is the unit the supervisor bounds, counts and reports. |
| R2 | **End observably, with success distinguishable from failure** | `ending` classifies finished / quit / crashed / unclear; the cycle boundary is the whole reason `-p` was chosen. |
| R3 | **Carry continuity to the next turn** | Continuation-by-default is a shipped v0.5.1 decision. |
| R4 | **Be addressable by a human for the same conversation** | `rite connect`'s argument, and Robert's requirement that local chat keep working. |
| R5 | **Run without per-action approval** | An unattended Manager that stops to ask is not unattended — measured, v0.5.1. |
| R6 | **Be checkable before use** | `rite doctor` already probes the endpoint; it must also answer "is the engine there". |
| R7 | ⚠ **Leave verification to rite, not the agent** | Added after review. `harness.py` runs the verify ITSELF and derives `accepted` from that alone, never from the agent's claim. An adapter that let the agent report its own success would hand verification to the thing being verified, in the tier where the model is least trustworthy. |

**That is the interface — seven capabilities, and rite already implements all
seven for Claude**, which is the strongest evidence the list is rite-shaped
rather than agent-shaped.

⚠ **R7 was missing from the first draft, and its absence is what produced
this plan's worst error** (see B4). An interface that omits "who decides the
work was done" will happily admit an adapter that deletes the boundary.

### The replacement checklist — what Robert is buying

If Goose disappears, a replacement must provide:

1. a command that runs **one turn** from an instruction file or stdin and
   **exits** when the turn ends;
2. an **exit status** that distinguishes success from failure;
3. a **session identifier that survives process exit** — either chosen by the
   caller or discoverable afterwards;
4. a way to **continue that session** on the next turn;
5. a way for a **human to open the same session** interactively;
6. a way to run **without per-action approval**;
7. a way to tell it is **installed and reachable**.

**Seven items, and the adapter that wraps them is roughly a day** — provided
a replacement tool has all seven.

8. ⚠ and it must **not** be trusted to report its own success — rite runs
   the verify (R7).

⚠ **The first draft said only Goose satisfied all of these, and used that to
argue the market was the real risk. That was wrong: opencode satisfies them
too**, modulo the two open `--format json` defects noted above. The
substitutability argument therefore softens — there are at least two viable
values for `agent:` today, which is the difference between a bet and a
dependency.

### Which parts of the interface are shaped by Goose rather than by rite

**An abstraction nobody has implemented twice is a guess.** These three axes
are where a second implementation will push back — named now so they are not
discovered later:

1. ⚠ **Who names the session — and this is a genuine conflict, not a
   detail.** Claude Code *generates* a session id that rite must **discover**
   from a transcript; Goose takes a **name rite chooses**. Those are opposite
   directions of control. rite's entire designation machinery — `designated`,
   `_default_resume_id`, the transcript scan — exists *because* Claude
   assigns the id, and a Goose-backed Manager **bypasses all of it**. R3 must
   therefore admit both "the engine assigns; you discover" and "you assign",
   and an interface modelling only one will fight the other.
2. **How permission is expressed.** Claude takes a **flag**
   (`--dangerously-skip-permissions`); Goose takes an **environment
   variable** (`GOOSE_MODE=auto`). R5 cannot be "a flag string".
3. **How the instruction arrives.** Claude `-p` reads **stdin** (rite
   redirects from a file); Goose accepts `-i <FILE>`, `-t <TEXT>` or stdin.
   R1 must allow a file path *or* stdin rather than assuming one.

### ⚠ The configuration surface must stay rite's vocabulary

**An abstraction that leaks through configuration is not an abstraction.** If
a user's config says `goose_mode: auto`, Goose is in rite's public surface
and a replacement has to emulate its vocabulary forever.

**The good news is that the config surface is already rite-shaped**:
`engine: local:<class>` plus an endpoint is existing vocabulary that names a
tier, not a runtime. The rule to hold: **no Goose noun reaches
`config.yaml`.** Anything Goose-specific lives inside the adapter, or at most
in a single opaque passthrough rite does not interpret and does not document
as a supported surface.

### Does the layer cost more than the glue it wraps?

**No — and the reason matters.** The layer *is* the glue. `launch_command`
already hard-codes Claude's spelling with no registry, so **generalising it
was B3's job before Goose entered the picture**. Adding an adapter boundary
is not new cost; it is the cost that was already scheduled, now with a second
implementation to prove it against — which is the only way to know a contract
is a contract.

**B4 does not grow.** What changes is that the release gains a real reason to
do B3 properly, and that `harness.py` is the thing the adapter plugs into
rather than competes with.

## B. Local models — Ollama

**Robert's framing:** rite plans adapters for Cursor and Ollama, and **the
adapters must not require a rework.** Cursor is v0.7.0; the contract that
would admit it is this release's job.

**Ollama is the adapter worth building first** — and this is the whole
argument for doing it now rather than with Cursor: **Claude and Cursor both
have provider-managed session ids, and Ollama has none.** An adapter layer
validated only against two engines that share that property would encode it
by accident. Finding that out during v0.6.0 is much cheaper than after.

**What already exists, and it is more than half.** `src/rite_ai/local/` ships
`duty_router.py`, `engine_probe.py`, `harness.py`, `runners.py` and
`decomposition.py`. The duty router is called by `coordination/assignment.py`
and the endpoint probe by `rite doctor`. 

⚠ **`harness.py` and `runners.py` have no production caller — verified,
zero production imports — but that is a WIRING GAP, not dead code.** An
earlier draft of this plan called them "the uncalled half" and proposed
deleting them. `harness.py` line 3 says **"Orchestration, not an agent
(RL-13)"**: the agent supplies file editing and the model's own loop, and
*nothing else*. `run_subtask` carries the plan-approval gate, claim take and
release on every exit path, the heartbeat, and `accepted` derived from the
verify alone and never from the agent's claim. **They were designed AROUND an
adopted agent** — which is exactly what Goose is — and deleting them would
hand verification to the agent being verified, in the tier where the model is
least trustworthy.

⚠ **How that error was made, because it is the class this project keeps
finding:** purpose inferred from call-graph position. A grep showed no caller
outside the package, "uncalled" became "unnecessary", and the file was never
opened.

⚠ **A note for whoever ever reconsiders their fate:** the dead-wiring guard
does not watch `local/`, and widening it would not help.
`mentioned_outside()` is a word-boundary text search, so two dead files
referencing each other satisfy it, and generic names like `Context` and
`Claims` satisfy it by collision. That needs import analysis; **the guard
will not tell you when it is safe.**

**The endpoint decision is taken and measured.** Inference on the host, tool
execution sandboxed, over a **local OpenAI-compatible endpoint** — Ollama, LM
Studio, llama.cpp's server and vLLM all expose one, so rite requires an
endpoint rather than a runtime (RL-14). `RL-T1` measured seatbelt reaching
`http://localhost:11434`; **Docker is unmeasured.**

| # | Item | What it is | Why this release | Depends on | Size |
|---|---|---|---|---|---|
| ~~B1~~ | ✅ **DONE — spike: does Goose do what its docs say?** | Measured 2026-09-24. Headless + named resume works and the name is a real handle; exit codes are unusable (0 for a 404 model *and* an unreachable provider); `GOOSE_MODE=auto` not directly probed. | — | — | **spent: ~1 sitting** |
| ~~B2~~ | ✅ **DONE — RL-T0: can an adopted agent hold a rite task loop on a local model?** | **Yes, at a correct context window.** Five benchmark tasks, `qwen3:32b`-class: Goose **5/5** in 1,958s, opencode **5/5** in 2,363s, no dishonest reports. ⚠ Five tasks, not the full ten. | — | — | **spent: ~2 sittings** |
| B3 | **State the engine contract** | Write down what an engine must provide — launch, resume-or-equivalent, prompt delivery, cycle boundary, permission — as the thing Claude, Ollama and later Cursor all satisfy. | **This is the "no rework" requirement.** `launch_command` hard-codes Claude Code's spelling (`-p`, `--resume`, `--dangerously-skip-permissions`) and treats the engine string as an executable name, with no registry. A second engine either forks that function or the contract gets written first. | B1, B2 | 2–3 sittings |
| B4 | **Wire `harness.run_subtask` to a Goose adapter** | Give `harness.py` its production caller, with Goose behind the R1–R7 boundary. rite keeps the approval gate, the claims, the heartbeat and the verify; the agent edits files and runs the model's loop. ⚠ **The adapter must not read the exit code as a verdict** (B1). | The tier is routed and probed but nothing invokes the orchestration. **This is the release's local deliverable** — and B5 is its proof, not a second tier. | B2 ✅, B3, Decision 4 | **2–3 sittings** — the spike closed the uncertainty this was withheld for |
| B5 | **Prove B4 on the existing benchmark** | Run `local:<class>` through `harness.run_subtask` on `tools/rite_local_bench/tasks.py`. ⚠ **The bar is now a number, not a vibe:** Goose scored **5/5** driven directly. Through rite's harness it should match; a materially worse score means the harness is the problem, not the model. | Proves the contract by using it, against a measured baseline. | B4 | **2–3 sittings** |
| B6 | **Finish the Docker half of RL-T1** | Whether a Docker-backed sandbox reaches the host endpoint. | Unmeasured today and named as such. Cheap; removes an unknown. | — | ½ sitting |
| B7 | ⚠ **`rite doctor` reports the served context window** | Query the endpoint for the window actually in force and warn when it is below a threshold the tier needs. `engine_probe.py` already probes the endpoint; this is a field on the same probe. | **The single highest-value item in the local track.** An unset `OLLAMA_CONTEXT_LENGTH` presents as models that cannot call tools and agents that lose history — it cost this plan two wrong conclusions. A one-line warning removes the whole class. | — | **1 sitting** |
| B8 | **The adapter sets `num_ctx` per request where it can** | Rather than trusting the operator's environment. ⚠ **Needs checking first**: Ollama's OpenAI-compatible `/v1` path may ignore `options.num_ctx`, in which case this reduces to B7 plus documentation. | Belt and braces on the failure that dominated the spike. | B7 | **½–1 sitting**, or drops out |

---

## C. Carried work — gathered

Recorded across five design notes; **this table is the index, the notes stay
the detail.** No new analysis. Multi-Manager moving to v0.7.0 changes which
of these are urgent, and that is noted per row.

⚠ **Four items were missing from the first draft of this index** (C14–C17),
found in review. **An index that loses items is worse than no index**,
because the notes stop being read once a table claims to cover them — and
Robert's standing rule is postpone, never drop. `V070_MULTI_MANAGER.md` §5 is
correctly absent: it shipped in v0.5.1.

| # | Item | Where recorded | Why this release | Size |
|---|---|---|---|---|
| C1 | **tmux socket isolation for tests** | `V070_MULTI_MANAGER.md` §3 | The suite and a live Manager share one tmux server, so a test run can kill an operator's Manager. One autouse fixture setting `TMUX_TMPDIR`, no call-site changes. | 1 sitting |
| C2 | **`_default_starter` defaults `permission=""`** | `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md` | A caller that forgets the flag gets a Manager that cannot act. Same call site that dropped three arguments in two days. **Touched by B3** — the contract has to say who supplies it. ⚠ *Citation corrected: an earlier draft cited `V070_MULTI_MANAGER.md` §6, which is about `prompt=""` — a different default in the same signature, now C14.* | ½ sitting |
| C3 | **The shared test starter records only the resume id** | `V070_MULTI_MANAGER.md` §7 | Tests using it are blind to prompt and permission, which is how those went unpinned. **Blocks confidence in B4/B5**, whose tests would be equally blind. | 1 sitting |
| C4 | **Configurable permission allowlist — and it REPLACES the default** | `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md`, Decision 3 | 0.5.1 ships `--dangerously-skip-permissions` always. ⚠ **Decision 3(b): the allowlist becomes the default and the flag stops being it** — a behaviour change on upgrade (C22). ⚠ **The shipped default list must be derived from observed invocations**, not imagined; too narrow means a Manager that stalls rather than fails. Proven by C20, explained by C21. ⚠ *The shipped note still says "with this flag still the default" and must be corrected in the same release.* | 2–3 sittings |
| C5 | **The Manager's reply path still hand-writes JSON** | mailbox review, 0.5.1 | The other half of the "two writers of one format" the `send` exemption named; the User side got `rite message`. **Raised by Slack**: A1/A4 add a third participant to that format. | 1 sitting |
| C6 | **The `tmux -e` argv trap** | `CREDENTIAL_HANDLING…md` Trap 1 | A credential passed via `-e` lands on the server's argv. **Raised by A2** — Slack introduces a second token. | 1–2 sittings |
| C7 | **Journal redaction** | `CREDENTIAL_HANDLING…md` | `redact_secrets` exists with the right shape; the journal path does not use it. **Raised by A2** for the same reason. | 1 sitting |
| C8 | **Designation membership check** | `V060_SESSION_CONTINUITY.md` §1 | Nothing verifies a designated id belongs to this project or Manager. **Less urgent now** — multi-Manager was what raised the stakes, and it moved. | 1–2 sittings |
| C9 | **Anchor ASCII limit** | `V060_ANCHOR_LEGIBILITY.md` | Ships correct and too blunt: an anchor entirely in a non-Latin script is refused though a reader could check it. | 1 sitting |
| C10 | **Journal provenance** | 0.5.1 CHANGELOG, known limitation | An entry has no author field, so a human filing while attached is indistinguishable from the Manager. **Raised by Slack**: a third writer makes "who said this" harder, not easier. | 1 sitting |
| C11 | **`.rite/user/` separation is incidental** | `V060_SESSION_CONTINUITY.md` §2 | Two files stay apart only because a glob's stem validation rejects a dot. A non-`.json` suffix or an explicit skip makes it structural. | ½ sitting |
| C12 | **`session_exists` is not a general tmux predicate** | `V070_MULTI_MANAGER.md` §4 | Named as if general, true only for the shapes it is called with. | ½ sitting |
| C13 | **`journal.instructions()` still spells out `--manager`** | `V070_MULTI_MANAGER.md` §2 | Redundant since `RITE_MANAGER`; pinned by tests, so it waits for a deliberate change to that text. | ½ sitting |
| C14 | **An empty prompt file is launchable** | `V070_MULTI_MANAGER.md` §6 — **OPEN** | `_default_starter` defaults `prompt=""` and `start_session` writes `prompt.txt` "even when empty", with no guard; `claude -p` with empty stdin exits 1. The same omission already shipped once, in `supervise`'s fresh fallback. | ½–1 sitting |
| C15 | **The real-tmux tests are load-sensitive and nondeterministic** | `V070_MULTI_MANAGER.md` §1 — **OPEN** | Proven code-independent by a markdown-only control run. Related to C1 but not the same item: C1 isolates the socket, this is the residual nondeterminism. | 1–2 sittings |
| C16 | **Three refusals embed raw tmux stderr** | `CREDENTIAL_HANDLING…md` Trap 2 | A leak only if Trap 1 (C6) happens, and the two are coupled — which is why both belong in one release rather than one being taken alone. | ½–1 sitting |
| C23 | ⚠ **The outbox now grows without bound** | A1, Decision 1 | **A consequence of 1(a), not a defect in it.** Readers no longer delete, so nothing does. A retention rule needs a policy decision — age, count, or "when every registered reader has passed it" — and the last one reintroduces a subscriber list, which is what 1(a) was chosen to avoid. **Not guessed here.** Small today (a reply is a few hundred bytes) and unbounded is still unbounded. | ½–1 sitting once the policy is chosen |
| C20 | ⚠ **Run the benchmark under the DEFAULT allowlist** | this plan, Decision 3 | **The observation C4 does not contain.** Run the five benchmark tasks with the shipped default allowlist in force and confirm they still pass. **Baseline is 5/5** (Goose, 32768 window). A task that stalls on approval means the list is too narrow — and that is the finding, not a test failure to work around. | 1 sitting |
| C21 | **The refusal names the command and how to allow it** | this plan, Decision 3 | A refusal a user cannot act on is the same defect as a silent one. The message must say which command was refused and the line that would permit it. | ½ sitting |
| C22 | ⚠ **Release notes state the upgrade behaviour change** | this plan, Decision 3 | Existing users get **different behaviour on upgrade**: a Manager that ran unattended may now stop for approval. Discovering that mid-run is the worst way to learn it. **Not optional — it is the same doc-describes-reality rule C19 exists for.** | ½ sitting |
| C19 | ⚠ **Amend SPEC §9.15.4 and §7.3 — the QA gate is 0.7.0** | this plan, Decision 5 | Robert moved the scenario gate (D-81) out of this release. Until the spec says so, a spec reader expects a 0.6.0 deliverable that will not arrive. **Required by Decision 5; not optional.** | ½ sitting |
| C18 | ⚠ **The local tier's docs must state the `OLLAMA_CONTEXT_LENGTH` requirement** | this plan, "Prerequisite" | Measured: at Ollama's 4,096 default the tier fails in ways that look like model and tool defects rather than configuration. B7 warns; this tells an operator what to do about it. **Pairs with B7 and should not ship without it.** | ½ sitting |
| C17 | **`if cycles:` — the unstated exception to "designated whatever the ending"** | `V060_SESSION_CONTINUITY.md` item 3 | An interrupt before the first cycle is appended designates nothing. Looks correct; it is the one path where the stated rule does not hold, and an unstated exception is how the next person is surprised. | ½ sitting |

---

## Sizes — B4/B5 now have numbers, and why

⚠ **Both reviews challenged the sizes, and the challenge is right on its own
evidence.** `RITE_LOCAL_ESTIMATE.md:118` says of the unit used throughout
this plan: *"The simulation's per-ticket unit is one sitting and is not
defended anywhere."* Re-asserting it in a new document does not defend it.

**What the numbers in this plan are, stated honestly:** ordinal, not
cardinal. They say which items are small and which are large **relative to
each other**, and they are useful for sequencing and for deciding what to cut.
They are not a schedule, and nothing should be promised on them.

⚠ **B4 and B5 now carry numbers, and they are earned rather than guessed.**
The first draft withheld them because their size was exactly what B1 and B2
were for. **Those spikes ran**, and the branch they were guarding against
did not happen: a locally-runnable tool-calling model holds a subtask
(Goose 5/5, opencode 5/5). So B4 is an adapter behind an existing boundary —
`harness.run_subtask` already exists and already takes an injected `Agent` —
and B5 is a run against a baseline that has a number.

**The one real measurement to plan against is wall clock, not effort.** Five
small tasks took **~33 minutes** of model time. A local Manager is not a fast
Manager, and anything that runs the benchmark in CI should know that before
it sets a timeout. ⚠ The 900s per-task cap was hit repeatedly at the wrong
context window; at the right one the slowest task was 623s, which is
uncomfortably close to it.

⚠ **And one measurement hazard for whoever runs these next:** two agent runs
sharing one GPU invalidate wall-clock comparison completely. A first 32,768
run recorded a task as TIMEOUT at 900s; re-run serialised, the same task took
510s and passed. **Serialise, or the number measures the other process.**

## Sequencing, and where the release can be cut

**Order:** ~~B1, B2~~ ✅ done → **B7 + C18** → C1, C2, C3 → A1 (once
Decision 1 lands), A2 → A3, A4, A5 → B3 → B4, B5 → C4 → B6, B8 and the rest
of C as it fits.

⚠ **B7 and C18 come first among the remaining work**, ahead of even the test
fixes. They are one-and-a-half sittings between them, and they close the
failure that cost this plan two wrong conclusions and cost the spike most of
a night: an operator whose `OLLAMA_CONTEXT_LENGTH` is unset sees models that
appear not to call tools and agents that appear to lose history. Everything
else in the local track is built on top of that being visible.

C1–C3 follow because they are small and two of them are *tests being blind* —
the condition under which everything after gets built unverified.

**A5 depends on Decision 2 and A1 on Decision 1**, so the Slack block cannot
start until those land; if they are slow, B3 can be pulled forward without
disturbing anything.

### The minimum that makes v0.6.0 coherent

**B7, C18, C1, C2, C3, A1–A5.** That is "Slack works, both chat routes keep
working, the test harness can see what it is testing, and an operator is told
when their endpoint is configured in a way that will make everything look
broken".

⚠ **A5 is inside the minimum, not adjacent to it.** An earlier draft named
the set and then argued A5 back in a sentence later, which is not a stated
minimum. Without A5 the first thing a new user meets is unexplained silence.

**B7 and C18 are in the minimum even though the local tier may not ship.**
They cost a sitting and a half, and they are what stops the next person
repeating the spike's night.

**C4 is promised in a shipped document.** If the allowlist slips, that
sentence in `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md` must change in the same
release, or the doc describes behaviour that does not exist — the defect
class 0.5.1 was spent removing.

### What can slip to v0.7.0 without leaving anything half-built

- **B4 and B5** — the local tier is *already* half-wired and has been for
  releases; slipping leaves it as it is rather than worse. ⚠ **B3 should
  still land** — B1 and B2 already have — because a written contract is cheap
  and it is what stops B4 becoming the rework Robert named. Slipping the
  contract to the release that also adds Cursor is how it gets written around
  two engines that share a property Ollama does not have.
- ⚠ **B7 and C18 should NOT slip even if the whole local tier does.**
  `rite doctor` already probes the endpoint; adding the served window costs a
  sitting, and without it the next person to touch this repeats the spike's
  night. A warning about a tier that has not shipped yet is still cheaper than
  the debugging it prevents.
- **C5, C8, C9, C10, C11, C12, C13** — independent, small, no dependants.
- **B6, B8** — measurements, not features.

---

## Decisions — ✅ ALL FIVE DECIDED 2026-09-24

**Robert's answers, recorded as given.** The reasoning under each is kept so
a later reader sees what was weighed, not just what was picked.

| # | Decision | Answer |
|---|---|---|
| 1 | Two permanent readers of one outbox | **(a) per-reader cursor** — readers never delete; each tracks its own position |
| 2 | Slack message with no Manager running | **(a) refuse and say so** |
| 3 | Permission allowlist vs the always-skip default | **(b) replaces** — ✅ confirmed deliberately, with a condition |
| 4 | Which agent `agent:` names | **(a) `goose`** |
| 5 | The 0.6.0 QA gate | **(b) move to 0.7.0 and amend the spec in this release** |

⚠ **Decision 1(a) preserves a test-pinned invariant, which is why it is also
the cheapest.** `tests/test_a_user_can_talk_to_a_running_manager.py::TestTheSupervisorDoesNotCareWhoWrote`
deliberately pins that nothing records a sender. A per-reader cursor keeps
that; fan-out and acknowledgement would have reversed it.

✅ **Decision 3 confirmed 2026-09-24, and the apparent contradiction was
deliberate.** It was queried because it reversed what Robert said on
2026-09-21 (*"`--dangerously-skip-permissions` stays as the default"*).
He changed it on purpose, with a condition that is now the substance of C4:

> *"Let's make the more secure option the default, just make sure the
> allowlist is generous and covers everything a worker needs under normal
> circumstances."*

⚠ **That condition changes what C4 has to prove, and it is the risky half.**
A default allowlist that is too narrow **does not fail loudly**. It produces a
Manager that stalls waiting for an approval nobody is there to give — defect
class 15, *"a prompt is not an exception, it is the absence of an answer"*.
And the agent has form for routing around an obstacle rather than reporting
it: when rite did not tell it how to start Workers, it improvised a bare
`claude`.

**So the default list is DERIVED FROM EVIDENCE, not imagined.** The material
already exists: the benchmark runs, the v0.5.1 acceptance runs and the Manager
transcripts all record what these agents actually invoked. Build from what was
observed — git, the test runner, the build, file operations, `rite` itself,
`gh` — rather than from a plausible-looking set. **A guessed allowlist is the
same instrument as a guessed threshold.**

**Decision 5 adds a task to this release**: amend SPEC §9.15.4 and §7.3 so
they stop promising a 0.6.0 deliverable that is now 0.7.0. See SPEC updates.

**The original framing of each, kept:**

1. ⚠ **Two permanent readers of one outbox — who deletes?** The current
   instruction is *"delete one once you have relayed it so it is not shown
   twice."* With `rite connect` and Slack both reading permanently, whoever
   reads first deletes and the other never sees it. **Robert's requirement
   that both keep working and today's delete-on-relay rule cannot both
   hold.** Per-reader read state, fan-out, or acknowledgement — each is a
   different amount of work. **A1 cannot start until this is answered, and
   Slack cannot start without A1.**

2. **Does a Slack message sent while no Manager is running get delivered
   later, or refused?** Listening lives in `rite start`, so with nothing
   running there is nothing polling. Buffering it means something reads Slack
   outside a run, which reopens "no daemon". Refusing it is coherent but must
   be *said* — see A5.

3. **Does C4's allowlist replace the always-skip default or sit beside it?**
   The shipped note says "with this flag still the default", which reads as
   beside. Worth confirming: it is the difference between a feature and a
   reversal.

4. ✅ **Which agent does `agent:` name? — RECOMMENDED: `goose`. Robert's to confirm.**

   **Not a dependency approval. That decision is already made, in code.**
   `config/managers.py:73` requires a `local:*` engine to carry `endpoint`,
   `model` and `agent`; `ManagerRole.agent` exists and is parsed; `opencode`
   is in four test fixtures; `engine_probe.py` already checks the agent binary
   is installed. **rite is committed to an adopted agent binary** — the open
   question is which value the key takes.

   **Measured, both work.** Benchmark at a correct window: Goose **5/5** in
   1,958s, opencode **5/5** in 2,363s. Both replay conversation context on
   resume. Both drive real tool calls.

   **Recommended `goose`, on one fact: degradation at default configuration.**
   At Ollama's out-of-the-box 4,096-token window Goose scores **4/5** and
   opencode scores **0/5** with every task timing out. rite ships to
   operators whose Ollama is at its default. Supporting but not deciding:
   `-n <name>` is a measured handle rite can derive from the Manager name, so
   nothing needs minting or storing; and Apache-2.0 under the Linux
   Foundation's AAIF is a better governance story than MIT alone.

   ⚠ **The cost, so it is chosen with eyes open:** Goose returns **exit 0**
   for a missing model *and* for an unreachable provider, and emits no
   structured event stream from `run`. **rite cannot use its exit status as a
   cycle verdict** and must rely on its own verify — which `harness.py`
   already does. opencode is better here: exit 1 for both, plus an `error`
   event. If Robert weights infrastructure-failure signalling above
   default-configuration robustness, **opencode is the defensible opposite
   choice** and nothing else in the plan changes.

   ⚠ **Still open on Goose, carried not closed:** `GOOSE_MODE=auto` was not
   directly probed against an operation requiring approval. The benchmark ran
   to completion under `auto`, but the docs contradict themselves — one page
   says autonomous mode acts "without requiring approval", another says such
   operations "will either use default permissions or fail". **B4 should
   settle it before the adapter ships.**

   ⚠ **And pin the version.** Goose ships a minor release most weeks and rite
   would depend on flags rather than an API. Measured against 1.51.0.

5. ⚠ **SPEC commits 0.6.0 to a QA gate that appears nowhere in this plan.**
   §9.15.4 says the journal entries "are the raw material for the QA gate"
   and that the scenario gate (D-81, §7.3) is what the journal is being
   refined toward; §7.3 says **"Not built."** So a spec reader expects a
   0.6.0 deliverable this plan does not contain.

   **Not costed here on purpose** — D-81 describes scenarios committed before
   the implementation branch and checked with `git merge-base`, which is a
   process change as much as a feature, and guessing its size is how a
   release acquires an item nobody scoped. It also carries a recorded gap of
   its own (§9.15.3a): **the gate cannot reach the entries it would consume**,
   because they are machine-local and uncommitted.

6. ⚠ **Open Interpreter — excluded on measurement; the provenance is
   Robert's to judge, not this plan's.**

   **Excluded because `interpreter exec resume` cannot reach a local model at
   all.** It accepts none of `--oss`, `--local-provider` or `-m` — those exist
   only on `exec` — and with every lever it does accept it dials
   `wss://api.openai.com/v1/responses` and fails 401. For a design whose
   mechanism is resuming a session, that is disqualifying on its own. It also
   scored **0/5**, with exit 0 and no files edited on every task — the
   silent-success failure mode.

   **Two earlier exclusions of this tool were written on reasons that were
   not true** (an AGPL-3.0 licence, then "REPL-first with no
   turn-per-invocation contract"). Both were written without running the
   binary. That pattern is worth more attention than the tool.

   **The provenance, stated plainly because rite would ship this as a
   credential** — and recorded for judgement rather than as a verdict, since
   the tool is excluded on other grounds anyway:

   - the binary self-identifies as `interpreter 0.0.45`, "Open Interpreter";
   - it is **byte-identical** to a release asset named
     `codex-aarch64-apple-darwin` (sha256 `56f7c62a…`);
   - its strings carry both `github.com/openinterpreter/openinterpreter` and
     `github.com/openai/codex`, plus `CODEX_INTERNAL_ORIGINATOR_OVERRIDE`;
   - it reads configuration from `CODEX_HOME` / `~/.codex`;
   - **OpenTelemetry and a statsig exporter are compiled in** — default-off,
     but present.

   It is an OpenAI Codex fork under a renamed project. ⚠ **If Robert ever
   wants it reconsidered, that is the thing to weigh**, alongside a further
   hazard: `--oss` treats the model name as a *registry* name and pulls it, so
   a typo matching a real model downloads it unattended with no confirmation.

## SPEC updates

**Two changes, both corrections rather than new spec.**

1. ⚠ **Already landed, recorded so it is not done twice.** SPEC §9.14.9 and
   D-79 cited `docs/design/V060_MULTI_MANAGER.md`, which no longer exists
   under that name; the citations point at `V070_MULTI_MANAGER.md` as of
   commit `3ff6152`. `grep -c V060_MULTI_MANAGER SPEC.md` returns **0**.

2. ⚠ **NEW, required by Decision 5 — this is ticket C19.** §9.15.4 says the
   journal entries "are the raw material for the QA gate" and points at the
   scenario gate (D-81, §7.3) as a 0.6.0 destination; §7.3 says **"Not
   built."** Robert moved the gate to **0.7.0**, so both sections must say so
   in this release. Leaving them is the defect class 0.5.1 was spent
   removing: a shipped document describing behaviour that does not exist.

**Nothing else.** Slack's design is not settled enough to specify — Decision 1
changes its shape — and the engine contract (B3) is the kind of thing that
should be written from the code as it lands rather than ahead of it. No
speculative spec for unbuilt features.
