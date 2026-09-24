# The engine contract — what rite needs from a thing that runs a turn

**Status: WRITTEN FROM THE CODE, 2026-09-24 (B3b).** Every capability below
is one rite already implements for Claude Code, and the spellings are ones
that were measured rather than read from a table. It is a description with
one open edge, not a specification of something unbuilt.

**Citation convention:** a bare `§` is a section of `SPEC.md`; this note's own
sections are written out, because the citation gate's regex is context-free
and would match a real SPEC section.

**The implementation is `src/rite_ai/managers/engines.py`.** If this document
and that module disagree, the module is right and this is the bug.

---

## Why this exists

`launch_command` used to take the engine string as an executable name and
append Claude Code's flags to whatever it was — `-p`, `--resume <id>`, the
permission mode on argv. Correct for one engine; another tool's vocabulary
for every other. A second engine would have forked that function or forced
the contract to be written after the fact, around whatever the second engine
happened to need.

**So it is written from the first engine and checked against the second**,
which is the only order that produces a contract rather than a description of
one implementation.

---

## R1–R7: what an engine must provide

| # | Capability | Why rite needs it |
|---|---|---|
| **R1** | Run **one turn** non-interactively from an instruction | The cycle is the unit the supervisor bounds, counts and reports. |
| **R2** | **End observably** | The supervisor has to know a turn is over before it composes the next one. ⚠ See "What R2 is NOT" below — this is the requirement most easily got wrong. |
| **R3** | **Carry continuity** to the next turn | Continuation-by-default is a shipped v0.5.1 decision: a Manager resumes unless told otherwise. |
| **R4** | Be **addressable by a human** for the same conversation | `rite connect`'s argument, and Robert's requirement that local chat keep working alongside any relay. |
| **R5** | Run **without per-action approval** | An unattended Manager that stops to ask is not unattended. Measured in v0.5.1: a working loop around a Manager that could not act, three cycles, no artifact. |
| **R6** | Be **checkable before use** | `rite doctor` answers "is the engine there, is the model there, is the window big enough" before a night is spent finding out. |
| **R7** | ⚠ **Leave verification to rite** | The engine reports what it did; rite decides whether it was done. |

### ⚠ R7 is the one that is not negotiable

`harness.run_subtask` derives `accepted` from **rite's own verify command,
run by rite**, and records the agent's claim of success separately without
ever letting it decide. An adapter that reported success from the agent would
hand verification to the thing being verified, in the tier where the model is
smallest and least able to judge its own work.

**This is not a precaution, it is a measured finding.** On the benchmark,
Open Interpreter returned **exit 0 with no file edited on all five tasks** —
a clean success code over work that did not happen. An engine's own account
of itself is evidence about the engine, not about the work.

### ⚠ What R2 is NOT: "the engine's exit is the boundary"

For `claude -p` the process exit **is** the end of the turn, and that is why
`-p` was chosen — `ending` reads exit statuses for a living rather than
inferring a turn ended from idleness or quiet output.

**It does not generalise, and the contract must not say it does.** Measured
2026-09-24:

- **Goose returns exit 0 for a model that does not exist** and **exit 0 for
  an unreachable provider.** Its exit status cannot be a cycle verdict.
- opencode returns 1 for both, plus a structured `error` event.
- Open Interpreter returns 0 having done nothing.

**No engine in the set distinguishes "did the work" from "talked about it" by
exit code.** So R2 means only: *the supervisor can tell that the turn has
ended.* Process exit satisfies that for all three. **Whether the turn
achieved anything is R7's question, and R7 answers it with rite's verify.**

Writing "the exit is the boundary" into the contract would have been true of
Claude, false of Goose, and would have encoded the first engine's property as
a requirement — the exact failure this document exists to prevent.

---

## The three axes where a second implementation pushes back

Named in the release plan before any of this was built, and each one has
since been measured against a real second engine.

### 1. Who names the conversation — and both directions are legal

| | Claude Code | Goose |
|---|---|---|
| handle | a **session id** the engine generates | a **name rite chooses** (`-n <name>`) |
| rite's job | **discover** it afterwards, from the transcript | **assign** it up front |
| machinery | `designated`, `_default_resume_id`, the transcript scan | none needed |

⚠ **The contract admits both, and this is the single most important thing in
it.** rite's entire designation machinery exists *because* Claude assigns the
id. A Goose-backed Manager bypasses all of it — rite already has a stable
name per Manager, so nothing needs minting, storing or scraping.

**A contract modelling only one direction would fight the other.** Modelling
only "the engine assigns" makes rite invent a discovery step for an engine
that never needed one; modelling only "rite assigns" breaks against Claude
outright.

`Spelling.handle_is_ours` is where this is expressed.

#### ⚠ The handle has to be declared on the way IN, not only on the way back

**Measured 2026-09-24, and it is what break 1 of B4b actually was.** Saying
"rite assigns the name" is not enough: the name has to reach the FIRST
launch. Goose resolves `-r` by name and fails loudly on a name it has never
seen, so a first cycle with no `-n` creates a conversation under a name Goose
chose, and the second cycle asks to continue one that does not exist.

So the contract needs `Spelling.start` (`-n {handle}`) beside
`Spelling.resume` (`-n {handle} -r`). An engine that assigns its own id has
no `start` — there is nothing for rite to say — which is why Claude's command
line is unchanged.

**The observation, three arms, against real Goose and `qwen3:32b`.** Cycle
one is told a token; cycle two is asked for it, in the commands rite itself
composes:

| arm | cycle 2's command | result | exit |
|---|---|---|---|
| **fixed** | `goose run -n <name> -r -i p2` | **replies `PLATYPUS42`** | 0 |
| no `-n` on cycle one | `goose run -n <name> -r -i p2` | `Error: No session found with name ...` | **1** |
| **the break as it was** | `goose run -i p2` | *"I don't have access to any token you've asked me to remember in this conversation."* | **0** |

⚠ **Read the third row's exit code.** Claude's transcript scan returns `""`
for a Goose run, so the resume argument vanishes and the cycle starts fresh
— and says so only to the model, in prose, while reporting success. That is
`_default_resume_id`'s own documented failure arriving by a different road:
*"each cycle began a FRESH context with the ticket half-done and no memory of
it — identical from outside to a resume that worked."*

The second row is the shape to prefer where a choice exists: a handle for a
conversation that was never created fails at the next launch, loudly, rather
than continuing without memory.

⚠ **This also corrects an earlier reading of the B1 spike**, which recorded
that Goose does not replay history on resume. That measurement used
`--resume --session-id` and watched token counts. Under `-n <name> -r` the
model demonstrably has the earlier turn: it answered with a token that
appears nowhere in cycle two's prompt.

Measured for Goose, because "it takes a name" could have meant a label: the
name **resolves by name rather than by recency** (an older named session is
returned in preference to a newer unnamed one), **fails loudly on an unknown
name** rather than silently starting a fresh one, and **works from any
working directory**.

### 2. How permission is expressed

Claude takes a **flag**; Goose takes the **environment variable**
`GOOSE_MODE`. So R5 cannot be "a flag string" — an engine says *where* its
answer goes, and `launch_command` refuses to write a flag for an engine whose
answer belongs in the environment rather than writing one the tool would
reject.

### 3. How the instruction arrives

Claude `-p` reads **stdin**, which rite redirects from a file so the
instruction never becomes an argument (`tmux new-session` puts its command on
tmux's argv, where `ps` shows it to every local account, and a prompt quotes
ticket text and internal names). Goose accepts **`-i <FILE>`** directly.

R1 therefore allows a file path *or* stdin, rather than assuming one.

---

## What a replacement must provide

If the adopted agent disappears, a replacement needs:

1. a command that runs **one turn** from an instruction file or stdin and
   **exits** when the turn ends;
2. a way for the supervisor to **tell the turn ended** — process exit is
   sufficient and is what all three candidates give;
3. a **conversation handle that survives process exit**, either chosen by
   rite or discoverable afterwards;
4. a way to **continue** that conversation on the next turn;
5. a way for a **human to open the same conversation** interactively;
6. a way to run **without per-action approval**;
7. a way to tell it is **installed and reachable**;
8. ⚠ and it must **not** be trusted to report its own success.

**Two tools satisfy all eight today** — Goose and opencode, both measured at
5/5 on rite's benchmark at a correct context window. That is the difference
between a bet and a dependency.

⚠ **Open Interpreter fails (4) and only (4)**: `interpreter exec resume`
accepts none of `--oss`, `--local-provider` or `-m`, and dials
`api.openai.com` regardless of configuration, so a conversation started
against a local model cannot be continued against it. One missing capability
out of eight is enough, which is what makes this a contract rather than a
scorecard.

---

## ⚠ The open edge

**`Spelling` describes how to LAUNCH a turn. It does not yet describe R4, R6
or R7's mechanics**, because those are not expressed in a command line:

- **R4** (a human opens the same conversation) is `rite connect` for Claude
  and would be `goose session --resume -n <name>` for Goose. Not yet
  expressed in the registry.
- **R6** (checkable) lives in `engine_probe.py` and is keyed off the role's
  `agent` and `endpoint`, not off `Spelling`.
- **R7** (verification) is `harness.run_subtask`'s, deliberately — it is a
  property of the orchestration, not of the engine, and putting it in the
  engine's description would be the category error the contract warns about.

**This is stated rather than smoothed** because an abstraction nobody has
implemented twice is a guess, and B4a is the first real second
implementation. Expect the registry to grow when it lands; the axes above are
where to expect it.
