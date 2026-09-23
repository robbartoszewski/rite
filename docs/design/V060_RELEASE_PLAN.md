# v0.6.0 — release plan

**Status: PLAN, for Robert to decide from and a session to execute from.**
v0.5.1 shipped; tag `v0.5.1` at `4df79ed`, verified against the served bytes.

**Robert's ordering, verbatim:** *"Start with Multi-Manager, then local and
other things."*

⚠ **One scope question is open and is NOT assumed either way here.** Two days
ago multi-Manager moved OUT of 0.6.0 into 0.7.0, making 0.6.0 fixes + Slack +
Ollama. Multi-Manager is now first again. **Whether Slack stays in 0.6.0 or
moves is being checked with Robert** — see "Decisions needed" below. Nothing
in this plan depends on the answer; Slack is costed and sequenced separately
so it can be added or dropped without resequencing the rest.

**Decisions already taken are honoured, not reopened.** SPEC §9.14.9 and D-79
carry them: subdirectories inside one root, strictly separated; profiles
shared and per-instance configuration gitignored; `engine` a Manager
attribute; `rite start <name>` names a Manager.
`docs/design/V060_MULTI_MANAGER.md`'s original "separate roots" shape is
**superseded** and must not be re-derived.

---

## A. Multi-Manager — first

**What "multi-Manager" means for this release:** two or more Managers running
at once in one project root, on one machine, without disturbing each other by
accident. The names, the per-Manager directory and the identity already
shipped in 0.5.1. What has not shipped is the *separation*.

| # | Item | What it is | Why this release | Depends on | Size |
|---|---|---|---|---|---|
| A1 | **Enumerate the shared surface, with a test** | §5.4.6 splits `.rite/` into *shared by decision* (claim ledger, `pool.json`, loop and scheduler locks, outbox, coordination cache) and *shared by accident* (everything else). Write the enumeration down and put a test behind it. | Everything else in A depends on knowing which files are which. §5.4.6 says a prose enumeration goes stale — `state.py` already claimed completeness and missed four writers, which is why `test_shared_state_locking.py` exists. | — | 1–2 sittings |
| A2 | **Move shared-by-accident under `<root>/.rite/managers/<name>/`** | The mechanical move §9.14.9 item 3 specifies, so §5.4's containment property becomes statable. | This IS the separation. Without it "strictly separated" is a claim with nothing under it. | A1 | 2–3 sittings |
| A3 | **Make the boundary refuse, not just exist** | A Manager writing outside its own subdirectory should be refused or reported, not merely discouraged by layout. | "One misbehaving manager shouldn't be able to mess with others by accident" is the decision; a directory alone does not deliver it. | A2 | 2 sittings |
| A4 | **Start and supervise N Managers concurrently** | `rite start <name>` runs one in the foreground today. Running several means several foreground processes, or a supervisor that holds more than one. | This is the feature a user sees. Everything above is the safety it needs. | A2, C4 (`_default_starter`) | 3–4 sittings |
| A5 | **`rite status` and `rite doctor` for several Managers** | Report N Managers, their engines, their boundaries, and collisions between them. | A user running two cannot currently tell them apart at a glance; the fleet view is what makes the feature operable. | A4 | 1–2 sittings |

⚠ **A4 is where the drift §9.14.9 warns about will first show.** A shared root
is a second protocol beside the state layer's, and the accepted debt is that
the two can disagree. Whoever builds A4 should be looking for it rather than
discovering it.

---

## B. Carried work — gathered

Recorded across five design notes today; **this table is the index, and the
notes stay the detail.** Nothing here is new analysis.

| # | Item | Where recorded | Why this release | Size |
|---|---|---|---|---|
| B1 | **tmux socket isolation for tests** | `V060_MULTI_MANAGER.md` §3 | The suite and a live Manager share one tmux server, so a test run can kill an operator's Manager. One autouse fixture setting `TMUX_TMPDIR`, no call-site changes. **Blocks A4**: running several Managers multiplies the surface. | 1 sitting |
| B2 | **`_default_starter` defaults `permission=""`** | `V060_MULTI_MANAGER.md` §6, `PERMISSION_MODE…md` | A caller that forgets the flag gets a Manager that cannot act. Same call site that dropped three arguments in two days. **Blocks A4.** | ½ sitting |
| B3 | **The shared test starter records only the resume id** | `V060_MULTI_MANAGER.md` §7 | Tests using it are blind to prompt and permission, which is how those two went unpinned. Fixing it is what makes A4's tests mean anything. | 1 sitting |
| B4 | **The `tmux -e` argv trap** | `CREDENTIAL_HANDLING_FOR_UNATTENDED_RUNS.md` Trap 1 | A credential passed via `-e` lands on the server's argv. Needs a decision before any token is delivered that way. | 1–2 sittings |
| B5 | **Journal redaction** | `CREDENTIAL_HANDLING_FOR_UNATTENDED_RUNS.md` | `redact_secrets` exists with the right shape; the journal path does not use it. | 1 sitting |
| B6 | **Configurable permission allowlist** | `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md` | 0.5.1 ships `--dangerously-skip-permissions` always, and the note already commits 0.6.0 to bringing configurability back as an allowlist of command patterns in `.claude/settings.json`, with the flag still the default. **Promised in a shipped document.** | 2–3 sittings |
| B7 | **The Manager's reply path still hand-writes JSON** | mailbox review, 0.5.1 | The other half of the "two writers of one format" the `send` exemption named. The User side got `rite message`; the Manager side did not. Safer direction — a malformed reply costs a message, not a run. | 1 sitting |
| B8 | **Designation membership check** | `V060_SESSION_CONTINUITY.md` §1 | Nothing verifies a designated session id belongs to this project or Manager. **Multi-Manager raises the stakes**: with several Managers in one root, a wrong id is a Manager resuming another's conversation. The means exist (`project_transcript_dir`, `_stated_session_id`). | 1–2 sittings |
| B9 | **Anchor ASCII limit** | `V060_ANCHOR_LEGIBILITY.md` | Ships correct and too blunt: an anchor written entirely in a non-Latin script is refused though a reader could check it. | 1 sitting |
| B10 | **Journal provenance** | 0.5.1 CHANGELOG, known limitation | An entry has no author field, so a human filing while attached is indistinguishable from the Manager. The entry format is already marked beta. | 1 sitting |
| B11 | **`.rite/user/` file separation is incidental** | `V060_SESSION_CONTINUITY.md` §2 | The designation and instance files are kept apart only because the `*.json` glob's stem validation rejects a dot. A non-`.json` suffix or an explicit skip makes it structural. | ½ sitting |
| B12 | **`session_exists` is not a general tmux predicate** | `V060_MULTI_MANAGER.md` §4 | Named as if general, true only for the shapes it is called with. | ½ sitting |

---

## C. Local models and adapters

**Robert's framing:** rite plans adapters for Cursor and Ollama, and **the
adapters must not require a rework.** The research is in the repo and is
reused here rather than redone.

**What already exists, and it is more than half:** `src/rite_ai/local/` ships
`duty_router.py`, `engine_probe.py`, `harness.py`, `runners.py` and
`decomposition.py`. The duty router is called by `coordination/assignment.py`
and the endpoint probe by `rite doctor`. **The runner and harness have no
caller outside the package** — that is the gap, and it is the half that
executes.

**The endpoint decision is already taken and already measured.** Inference on
the host, tool execution sandboxed, talking over a **local OpenAI-compatible
endpoint** — which Ollama, LM Studio, llama.cpp's server and vLLM all expose,
so rite requires an endpoint rather than a runtime (RL-14). `RL-T1` measured
seatbelt reaching `http://localhost:11434`; **Docker is unmeasured.**

| # | Item | What it is | Why this release | Depends on | Size |
|---|---|---|---|---|---|
| C1 | **RL-T0 spike — can an adopted agent hold a rite task loop on a local model?** | The gating spike the local tickets mark ⛔. | It decides whether C3 adopts an agent or builds a loop. **Doing C3 before this is the rework Robert wants avoided.** | — | 1–2 sittings |
| C2 | **Finish the Docker half of RL-T1** | Whether a Docker-backed sandbox reaches the host endpoint. | Unmeasured today and named as such. Cheap; removes an unknown from the sandbox story. | — | ½ sitting |
| C3 | **Wire the harness and runners to a caller** | Give the executing half a command, so `local:<class>` runs a subtask. | The tier is half-wired: routed and probed but unable to execute. This is the release's local-model deliverable. | C1 | 3–5 sittings |
| C4 | **State the engine contract the adapters implement** | Write down what an engine must provide — launch, resume, prompt delivery, cycle boundary, permission — as the thing Cursor and Ollama both satisfy. | **This is the "no rework" requirement.** `launch_command` today hard-codes Claude Code's spelling (`-p`, `--resume`, `--dangerously-skip-permissions`) and treats the engine string as an executable name, with no registry. A second engine either forks that function or the contract gets written first. | A4 (shares the launch path) | 2–3 sittings |
| C5 | **Ollama as the first non-Claude engine** | `local:<class>` end to end against a real endpoint. | Proves C4 by using it once, which is the only way to know the contract is a contract. | C3, C4 | 2–3 sittings |

⚠ **C4 is the item that most repays being done before, not after.** Every day
`launch_command` grows another Claude-shaped flag it is the day the second
engine gets more expensive. It is sequenced after A4 only because A4 touches
the same function; if A4 slips, C4 should still land.

---

## Sequencing, and where the release can be cut

**Order:** B1, B2, B3 → A1, A2 → A3, A4, A5 → C1, C2 → C4 → C3, C5 → B6, and
the rest of B as it fits.

B1–B3 come first because they are small, they block A4, and two of them are
about tests being blind — which is the condition under which everything after
them would be built unverified.

### The minimum that makes v0.6.0 coherent

**A1, A2, A3, B1, B2, B3.** That is "several Managers, actually separated",
which is the headline and the thing the decisions were taken for. A4 and A5
make it *usable* and should be in the release if at all possible — without
them a user has separation they cannot exercise — but A1–A3 leave nothing
half-built if the release is cut there.

**B6 is promised in a shipped document.** `PERMISSION_MODE_FOR_UNATTENDED_RUNS.md`
tells a reader 0.6.0 brings the allowlist. If it slips, that sentence must
change in the same release, or the doc is describing behaviour that does not
exist — the defect class this project spent 0.5.1 removing.

### What can slip to v0.7.0 without leaving anything half-built

- **C3 and C5** — the local tier is *already* half-wired and has been since
  before this release; slipping leaves it exactly as it is rather than worse.
  **C1, C2 and C4 should still land**: a spike and a written contract are
  cheap, and they are what stop C3 becoming a rework later.
- **B7, B9, B10, B11, B12** — independent, small, no dependants.
- **B8** — unless A4 lands, in which case it stops being optional: several
  Managers in one root is when a wrong designation becomes a Manager resuming
  another's conversation.

### Slack

Not sequenced above, deliberately. `V080_RELAY_CHANNELS.md` holds the design
and the open questions; nothing in A, B or C depends on it, and nothing in it
depends on A, B or C beyond the mailbox that shipped in 0.5.1. It can be
inserted or dropped once Robert answers.

---

## Decisions needed from Robert

**Flagged rather than assumed. Two days of 0.5.1 went on an assumption nobody
checked.**

1. **Does Slack stay in v0.6.0 or move?** Being checked. The plan works either
   way; this is the only genuinely open scope question.
2. **Does "several Managers" mean several foreground processes, or one
   supervisor holding several?** (A4.) The 0.5.1 design leans hard on `rite
   start` being *your* foreground process — that is the whole of §9.12's
   compliance argument. Running three that way means three terminals; one
   supervisor holding three is a different shape and touches that argument.
   **Nothing should be built for A4 until this is answered.**
3. **Does B6's allowlist replace the always-skip default, or sit beside it?**
   The shipped note says "with this flag still the default", which reads as
   beside. Worth confirming, because it is the difference between a feature
   and a reversal.
4. **Is Cursor in scope for 0.6.0, or only the contract that would admit it?**
   C4 writes the contract; C5 proves it with Ollama. Cursor as a *third*
   engine in the same release is not costed here.

5. ⚠ **SPEC already commits 0.6.0 to a QA gate that this plan does not
   include, and it was not in the ordering.** §9.15.4 says the journal
   entries "are the raw material for the QA gate" and that the scenario gate
   (D-81, §7.3) is "what the journal is being refined toward"; §7.3 itself
   says **"Not built."** So a reader of the spec expects a 0.6.0 deliverable
   that appears nowhere in A, B or C.

   Flagged rather than resolved, because both resolutions are real: build it
   this release, or move the commitment. **It is not costed here** — D-81
   describes scenarios committed before the implementation branch and checked
   with `git merge-base`, which is a process change as much as a feature, and
   guessing its size is how a release acquires an item nobody scoped.

   ⚠ It also carries a known gap of its own, already recorded at §9.15.4 and
   §9.15.3a: **the gate cannot reach the entries it is meant to consume**,
   because they are machine-local and uncommitted. Whoever takes this on
   inherits that first.

## What this plan does not do

No SPEC text is proposed for anything unbuilt. The multi-Manager decisions are
already in §9.14.9 and §5.4.6 — including, usefully, the requirement that the
shared-surface enumeration carry a test — so A needs **no new spec before it
starts**; it needs spec *updated as it lands*, which is where the wording
should be written from the code rather than ahead of it.
