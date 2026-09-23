# v0.6.0 — release plan

**Status: PLAN, for Robert to decide from and a session to execute from.**
v0.5.1 shipped; tag `v0.5.1` at `4df79ed`, verified against the served bytes.

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

## Ordering: Slack or Ollama first

**Recommendation, revised after the tool survey (B0): run the one-sitting
spike B1 first, then SLACK, then the rest of the local work.**

⚠ **The earlier draft of this plan put the whole local track first**, on the
premise that Ollama has no provider-managed session id and would therefore
stress the engine contract hardest. **The survey weakens that premise**: if
the adapter is thin glue over Goose, Goose supplies the session identity and
`goose run` exits at the turn boundary — the same shape as `claude -p` — so
the contract bends much less and the mail delivery point does not move.

**B1 still goes first, because it is one sitting and it is what tells us
whether that is true.** If the spike shows Goose does what its docs say,
Slack is unblocked from the local track entirely and should go next on its
own merits: it is the release's headline, it is user-facing, and its code is
insulated by the mailbox. If the spike shows otherwise, the coupling below
applies again and the local track moves ahead of Slack.

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

## B0. Does an existing tool already do local sessions? — surveyed 2026-09-23

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
| **Goose** (block/goose) | `--session-id`, `-n/--name`, or `--path` | **Yes** — SQLite `sessions.db` since 1.10.0 | `goose run -i <FILE>` / `-t <TEXT>` / stdin | **Yes** — `goose session --resume -n <name>` continues what a headless run started | one binary; Ollama an explicit provider | **Apache-2.0**, Agentic AI Foundation (Linux Foundation) |
| Aider | chat history files (`.aider.chat.history.md`) | as files, not as an addressable session | `--message` / `--message-file` with `--yes-always` | not natively — session management is recent/third-party | Python | Apache-2.0 |
| OpenHands | conversation id; `--resume <id>` / `--resume --last` | yes | ⚠ **an open PRD, "Support non-interactive CLI mode" (CLI issue #147)** — not settled | yes | Python/binary | MIT |
| Cline | `--id <session_id>`, `<millis>_<5 chars>` | yes | young CLI; ⚠ **open bug: `--json` mode cannot resume an existing session id** (#10856) | yes | Node; primarily an IDE surface | Apache-2.0 |
| Open Interpreter | — | — | — | — | Python | ⚠ **AGPL-3.0** |
| Continue (`cn`) | CLI exists | unconfirmed | unconfirmed | unconfirmed | Node | ⚠ **not confirmed — do not assume** |

### Verdict: **thin glue over Goose**, subject to one spike

Goose is the only candidate that satisfies all four requirements from its own
documentation, and it is the one whose licence and governance suit shipping
as a credential: Apache-2.0, under the Linux Foundation's Agentic AI
Foundation, with Ollama named as a supported provider.

**Thin glue means:** rite composes an instruction, runs `goose run` against a
named session, and reads the exit. Continuity is Goose's `sessions.db`.
Attachment is `goose session --resume -n <name>` — which is `rite connect`'s
argument applied to the local tier, and is the same answer Robert gave for
Claude.

⚠ **It probably makes `harness.py` and `runners.py` unnecessary.** They are
the uncalled half of `src/rite_ai/local/`. If Goose holds the loop, that code
is not wired up — it is **deleted**, and the release gets smaller.

### Three things the spike must MEASURE, not read

⚠ **These are why B1 stays a spike rather than becoming a design.** Each is
an assumption that reading the docs cannot settle.

1. **Headless *and* named-resumable together is documented on two pages and
   demonstrated on neither.** The headless tutorial shows `goose run
   --no-session -t "..."`, i.e. the opposite of resumable; the CLI reference
   lists `-r/--resume`, `--name` and `--session-id` on `goose run`. Nothing
   shows one run starting a named session and a later run continuing it. **Run
   it.**
2. **`GOOSE_MODE=auto` carries rite's own permission failure, verbatim.** The
   docs say operations requiring approval "will either use default
   permissions or **fail**". That is precisely what v0.5.1 measured with
   `claude -p` under `acceptEdits` — a working loop around a Manager that
   could not act, three cycles and no artifact. Whatever Goose's equivalent
   of `--dangerously-skip-permissions` is, it has to be established before
   B4, not after.
3. **Exit codes are not documented.** The docs only imply shell convention
   (`if ! goose run ...`). Cycle-end detection is the engine contract's
   core — `claude -p` was chosen *because* its exit is the boundary — so this
   needs measuring rather than assuming.

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

**That is the interface. Six capabilities, and rite already implements all
six for Claude** — which is the strongest evidence the list is rite-shaped
rather than Goose-shaped.

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

⚠ **And that proviso is the real risk, not the abstraction.** B0 surveyed
six tools against exactly these properties and **only Goose satisfied all of
them.** Aider has no addressable resumable session; OpenHands has not settled
non-interactive mode; Cline cannot resume under `--json`; Open Interpreter is
AGPL-3.0. So "implement its features" is cheap, and "find something else with
these features" is, on today's evidence, **the part that is not guaranteed**.
That is the honest shape of the bet and it should be stated to Robert that
way rather than as a reassurance about layering.

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
do B3 properly, and `harness.py` and `runners.py` become deletions rather
than work.

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
and the endpoint probe by `rite doctor`. **The runner and harness have no
caller outside the package** — that is the gap, and it is the half that
executes.

**The endpoint decision is taken and measured.** Inference on the host, tool
execution sandboxed, over a **local OpenAI-compatible endpoint** — Ollama, LM
Studio, llama.cpp's server and vLLM all expose one, so rite requires an
endpoint rather than a runtime (RL-14). `RL-T1` measured seatbelt reaching
`http://localhost:11434`; **Docker is unmeasured.**

| # | Item | What it is | Why this release | Depends on | Size |
|---|---|---|---|---|---|
| B1 | **Spike: does Goose do what its docs say?** | Run the three measurements in B0: headless *and* named-resumable together; what `GOOSE_MODE=auto` does with an operation needing approval; what the exit code is on success and failure. Against a real Ollama endpoint. | **Decides the whole local track and unblocks Slack's ordering.** Cheapest item in the release and the one most else leans on. | — | 1 sitting |
| B2 | **RL-T0 — can an adopted agent hold a rite task loop on a local model?** | The gating spike the local tickets mark ⛔. **Largely answered if B1 passes**: Goose IS the adopted agent, and RL-T0's question becomes whether it holds rite's contracts rather than whether one exists. | Decides whether B4 is glue or a build. **Doing B4 before this is the rework Robert wants avoided.** | B1 | ½–1 sitting if B1 passes, 1–2 if not |
| B3 | **State the engine contract** | Write down what an engine must provide — launch, resume-or-equivalent, prompt delivery, cycle boundary, permission — as the thing Claude, Ollama and later Cursor all satisfy. | **This is the "no rework" requirement.** `launch_command` hard-codes Claude Code's spelling (`-p`, `--resume`, `--dangerously-skip-permissions`) and treats the engine string as an executable name, with no registry. A second engine either forks that function or the contract gets written first. | B1, B2 | 2–3 sittings |
| B4 | **Glue `local:<class>` to Goose** | Compose the instruction, run `goose run` against a named session, read the exit. ⚠ **And DELETE `harness.py` and `runners.py`** if Goose holds the loop — they are the uncalled half, and leaving them is the built-and-never-wired class this project keeps finding. | The tier is routed and probed but cannot execute. This is the release's local deliverable, and on the thin-glue path it is much smaller than it was. | B2, B3 | 2–3 sittings as glue; 3–5 if B1 fails and rite holds the loop |
| B5 | **Ollama end to end against a real endpoint** | `local:<class>` doing a real subtask. | Proves B3 by using it once, which is the only way to know a contract is a contract. | B4 | 2–3 sittings |
| B6 | **Finish the Docker half of RL-T1** | Whether a Docker-backed sandbox reaches the host endpoint. | Unmeasured today and named as such. Cheap; removes an unknown. | — | ½ sitting |

---

## C. Carried work — gathered

Recorded across five design notes; **this table is the index, the notes stay
the detail.** No new analysis. Multi-Manager moving to v0.7.0 changes which
of these are urgent, and that is noted per row.

| # | Item | Where recorded | Why this release | Size |
|---|---|---|---|---|
| C1 | **tmux socket isolation for tests** | `V070_MULTI_MANAGER.md` §3 | The suite and a live Manager share one tmux server, so a test run can kill an operator's Manager. One autouse fixture setting `TMUX_TMPDIR`, no call-site changes. | 1 sitting |
| C2 | **`_default_starter` defaults `permission=""`** | `V070_MULTI_MANAGER.md` §6, `PERMISSION_MODE…md` | A caller that forgets the flag gets a Manager that cannot act. Same call site that dropped three arguments in two days. **Touched by B3** — the contract has to say who supplies it. | ½ sitting |
| C3 | **The shared test starter records only the resume id** | `V070_MULTI_MANAGER.md` §7 | Tests using it are blind to prompt and permission, which is how those went unpinned. **Blocks confidence in B4/B5**, whose tests would be equally blind. | 1 sitting |
| C4 | **Configurable permission allowlist** | `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md` | 0.5.1 ships `--dangerously-skip-permissions` always, and that note **promises 0.6.0** brings an allowlist of command patterns in `.claude/settings.json`, flag still the default. Promised in a shipped document. | 2–3 sittings |
| C5 | **The Manager's reply path still hand-writes JSON** | mailbox review, 0.5.1 | The other half of the "two writers of one format" the `send` exemption named; the User side got `rite message`. **Raised by Slack**: A1/A4 add a third participant to that format. | 1 sitting |
| C6 | **The `tmux -e` argv trap** | `CREDENTIAL_HANDLING…md` Trap 1 | A credential passed via `-e` lands on the server's argv. **Raised by A2** — Slack introduces a second token. | 1–2 sittings |
| C7 | **Journal redaction** | `CREDENTIAL_HANDLING…md` | `redact_secrets` exists with the right shape; the journal path does not use it. **Raised by A2** for the same reason. | 1 sitting |
| C8 | **Designation membership check** | `V060_SESSION_CONTINUITY.md` §1 | Nothing verifies a designated id belongs to this project or Manager. **Less urgent now** — multi-Manager was what raised the stakes, and it moved. | 1–2 sittings |
| C9 | **Anchor ASCII limit** | `V060_ANCHOR_LEGIBILITY.md` | Ships correct and too blunt: an anchor entirely in a non-Latin script is refused though a reader could check it. | 1 sitting |
| C10 | **Journal provenance** | 0.5.1 CHANGELOG, known limitation | An entry has no author field, so a human filing while attached is indistinguishable from the Manager. **Raised by Slack**: a third writer makes "who said this" harder, not easier. | 1 sitting |
| C11 | **`.rite/user/` separation is incidental** | `V060_SESSION_CONTINUITY.md` §2 | Two files stay apart only because a glob's stem validation rejects a dot. A non-`.json` suffix or an explicit skip makes it structural. | ½ sitting |
| C12 | **`session_exists` is not a general tmux predicate** | `V070_MULTI_MANAGER.md` §4 | Named as if general, true only for the shapes it is called with. | ½ sitting |
| C13 | **`journal.instructions()` still spells out `--manager`** | `V070_MULTI_MANAGER.md` §2 | Redundant since `RITE_MANAGER`; pinned by tests, so it waits for a deliberate change to that text. | ½ sitting |

---

## Sequencing, and where the release can be cut

**Order:** B1 → C1, C2, C3 → A1 (once Decision 1 lands), A2 → A3, A4, A5 →
B2 → B3 → B4, B5 → C4 → B6 and the rest of C as it fits.

B1 is first because it is one sitting and every later choice — Slack's
ordering, B4's size, whether `harness.py` survives — turns on its result.
C1–C3 follow because they are small and two of them are *tests being blind* —
the condition under which everything after gets built unverified.

⚠ **If B1 fails**, Slack and the local track swap: the contract question
becomes live again, B4 grows to 3–5 sittings, and the Ordering section's
coupling argument applies as written.

### The minimum that makes v0.6.0 coherent

**B1, C1, C2, C3, A1–A4.** That is "Slack works, both chat routes keep
working, and the test harness can see what it is testing". Slack is the
release's headline under the corrected scope, and A5 should be in it too —
without A5 the first thing a new user meets is unexplained silence.

**C4 is promised in a shipped document.** If the allowlist slips, that
sentence in `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md` must change in the same
release, or the doc describes behaviour that does not exist — the defect
class 0.5.1 was spent removing.

### What can slip to v0.7.0 without leaving anything half-built

- **B4 and B5** — the local tier is *already* half-wired and has been for
  releases; slipping leaves it as it is rather than worse. ⚠ **B1, B2, B3
  should still land**: two spikes and a written contract are cheap, and they
  are exactly what stops B4 becoming the rework Robert named. Slipping the
  contract to the release that also adds Cursor is how it gets written around
  two engines that share a property Ollama does not have.
- **C5, C8, C9, C10, C11, C12, C13** — independent, small, no dependants.
- **B6** — a measurement, not a feature.

---

## Decisions needed from Robert

**Flagged rather than assumed.**

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

4. ⚠ **Adopt Goose as the local tier's session manager?** Health-assessed in
   B0a and it is a reasonable bet: Apache-2.0, repository actually
   transferred to the Linux Foundation's AAIF org, 452 contributors, released
   and pushed the day it was assessed, PRs mostly merged within two days.

   **The costs, stated plainly so the decision is made on them:**

   - It is a dependency on a **third-party binary a user installs**, not a
     library rite vendors.
   - It ships a **minor release most weeks**, and rite would depend on flags
     rather than an API — so rite should **pin a tested version** rather than
     track latest.
   - ⚠ **The substitutability is the real exposure, not the layering.** The
     replacement checklist is seven items and about a day's work — *if* a
     replacement has all seven. Of six tools surveyed, **only Goose did**. So
     "if Goose disappears we just implement its features" is true of the
     adapter and **not yet true of the market**.

   Against that: rite stops owning an agent loop and a conversation store,
   and `harness.py` and `runners.py` become deletions. **B4 should not start
   until this is answered**, and B1 is what gives the answer its evidence.

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

## SPEC updates

**One change, and it is a correction rather than new spec:** SPEC §9.14.9 and
D-79 cite `docs/design/V060_MULTI_MANAGER.md`, which no longer exists under
that name. Those citations now point at `V070_MULTI_MANAGER.md`. The
decisions they record are unchanged.

**Nothing else.** Slack's design is not settled enough to specify — Decision 1
changes its shape — and the engine contract (B3) is the kind of thing that
should be written from the code as it lands rather than ahead of it. No
speculative spec for unbuilt features.
