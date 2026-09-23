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

**Recommendation: a small cycle-boundary spike from the Ollama side first
(B1), then Slack, then the rest of Ollama.** Not a coin toss — the coupling
between them is real, one-directional, and cheap to settle.

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
| B1 | **Spike: what is a cycle boundary without a session id?** | For an engine with no provider-managed session id — what ends a turn, where does continuity live, and does mail keep exactly one delivery point? | **Gates Slack's design** (see Ordering) and decides B3's shape. Cheapest item in the release and the one most other things lean on. | — | 1 sitting |
| B2 | **RL-T0 spike — can an adopted agent hold a rite task loop on a local model?** | The gating spike the local tickets mark ⛔. | Decides whether B3 adopts an agent or builds a loop. **Doing B3 before this is the rework Robert wants avoided.** | — | 1–2 sittings |
| B3 | **State the engine contract** | Write down what an engine must provide — launch, resume-or-equivalent, prompt delivery, cycle boundary, permission — as the thing Claude, Ollama and later Cursor all satisfy. | **This is the "no rework" requirement.** `launch_command` hard-codes Claude Code's spelling (`-p`, `--resume`, `--dangerously-skip-permissions`) and treats the engine string as an executable name, with no registry. A second engine either forks that function or the contract gets written first. | B1, B2 | 2–3 sittings |
| B4 | **Wire the harness and runners to a caller** | Give the executing half a command, so `local:<class>` runs a subtask. | The tier is routed and probed but cannot execute. This is the release's local deliverable. | B2, B3 | 3–5 sittings |
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

B1 is first because it is one sitting and both other tracks lean on it.
C1–C3 follow because they are small and two of them are *tests being blind* —
the condition under which everything after gets built unverified.

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

4. ⚠ **SPEC commits 0.6.0 to a QA gate that appears nowhere in this plan.**
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
