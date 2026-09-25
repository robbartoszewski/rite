# v0.7.0 — release plan, and the specs it needs

**Status: PLAN, 2026-09-25. Written while v0.6.0 is still being built, for
Robert to decide from. Nothing in it is built.** Two review rounds follow.

**Citation convention.** A bare `§` is a section of `SPEC.md`. This plan's own
parts are called "track MM", "part 0" and so on, never `§`, because the
citation gate's regex is context-free. `DEFECT_CLASSES.md` classes are written
"class N".

**Scope, as Robert set it.** v0.7.0 carries **multi-Manager**, **Cursor**,
**memory** and **egress control**, plus the scenario gate that Decision 5 of
the v0.6.0 plan moved here (§7.3, D-81). The relay and self-reflection are
v0.8.0 (`V080_RELAY_CHANNELS.md`). This plan adds nothing to that scope.

**The bar for every line below.**

- **Decided** means Robert decided it, and the line names where that is
  recorded. Anything else is written as an **open question**, with the options
  and what turns on each. No option is presented as settled.
- **Nothing here describes behaviour rite does not have.** Where a sentence
  says what rite does, it names the commit or the file. Where it says what
  rite *will* do, it sits under a ticket and says "not built".
- **Measured, read or inferred, and it says which.** Everything in part 0 was
  measured for this plan, and re-measured against `9862b59` after review. The Cursor facts were **read** from Cursor's
  documentation, because the binary is not installed on this machine. The
  yoloAI network facts were **read** from `yoloai help security` (0.11.0).

**Where the specs live.** Anything decided is in `SPEC.md`: §5.5 (egress,
new), §5.4.8 (separation between Managers, new), and corrections to §5.4,
D-62 and D-76. Everything that still needs a decision stays here, because a
spec section for an undecided feature is the defect this project spent two
releases removing.

---

## Part 0 — what changed under the v0.7.0 notes, and what was measured

**Read this before any track.** The design notes this plan works from
(`V070_EGRESS.md`, `V070_MULTI_MANAGER.md`, B9) were written before B9 landed.
B9 changed the ground under three of them.

⚠ **REVISED after review round one.** The first version of this part was
measured against `b85e52e`. `main` then moved twice in a few hours, and
`9862b59` closed two of the three holes it reported. **Every probe below was
re-run against `9862b59`**, and the prose follows the new results. The first
version's figures are kept in 0.5, because a hole that existed and was closed
is worth a record. Its central conclusion, that the broker's unfinished half
and egress are one problem, **was disproven** by the fix, and 0.3 says how.

### 0.1 A Manager runs inside a boundary

On `main`:

- the Manager's pane runs `sandbox-exec -f <profile> <engine …>`, with the
  profile composed by `managers/enclosure.py` (`4ebbbd7`);
- the Manager asks for a Worker, and a host-side broker (`managers/broker.py`,
  `eb2a88e`) validates the request and runs `rite sandbox start`, at the cycle
  boundary (C24, deliberately; see the decisions list);
- since `9862b59`, the profile denies the tmux socket directory and limits
  signals to `(target same-sandbox)`.

The observation in `4ebbbd7` covered both halves: a sandboxed Goose Manager
completed a cycle, and a Worker really started from its request. `9862b59`
re-ran a real engine cycle under the fixed profile.

**Consequences for what was written earlier:**

- **D-76 ("a Manager is not sandboxed") is reversed in code.** Its cell was
  never marked. Corrected in `SPEC.md`, under §13's supersession convention.
- **§5.4's opening ("Managers do not get one") was stale, and so was
  §5.4.5.** §5.4.5 said there was no per-Manager directory and no
  `RITE_MANAGER`. Both exist (`managers/__init__.py`: `manager_dir`,
  `MANAGER_ENV`). Corrected.
- **`V070_EGRESS.md` open question 1** says "The Manager does not have one [a
  sandbox]". It has one now. 0.3 says what that profile can and cannot do
  about egress.

### 0.2 Measured on `9862b59`: what the boundary holds, and what it does not

**Method.** 2026-09-25, `rite_ai` confirmed to resolve to this worktree, at
`9862b59`. The profiles came from `enclosure.compose()` itself, not hand
copies. A private tmux server ran on a short `TMUX_TMPDIR`, so the operator's
own server was never touched. Every probe ran `sandbox-exec -f <that
profile> …`.

**The five probes the first version reported:**

| probe, from inside a Manager's profile | on `9862b59` |
|---|---|
| write a file in the per-user temp root, which the profile does not grant: directly / through the tmux server | **refused** / **refused**: `error connecting to …/tmux-<uid>/default (Operation not permitted)`. No file appeared |
| `yoloai ls`: directly / through the tmux server | **refused** (`open ~/.yoloai: operation not permitted`) / **refused**, the same socket error |
| `kill -TERM` a process outside the sandbox that it did not start | **refused**, `Operation not permitted`. The process survived |
| HTTPS to `example.com`; to `1.1.1.1` | **200; 301**. The network is open, as `limitations()` says |
| list `~/.claude/projects`; read the first bytes of **another** project's transcript | **42 directories listed; the other project's transcript read.** ⚠ Still open (SB4) |

**Two Managers in one root** (profiles for `alpha` and `beta`, same project).
`beta`'s engine stands in as a process running inside `beta`'s profile, in
its own tmux session:

| probe, from inside `alpha`'s profile | result |
|---|---|
| `kill -TERM` `beta`'s engine (the pane's pid) | **refused**, `Operation not permitted`. `beta`'s engine survived |
| `tmux send-keys -t beta …`; `tmux kill-session -t beta` | **refused**, the socket error. `beta`'s session survived both |
| control: `beta` kills its own child | **succeeded**. Same-sandbox signalling still works, which a Manager needs |
| write a file into `.rite/managers/beta/` | ⚠ **succeeded**. Each profile grants the whole project tree |

**Re-measured at `8d5fc22`, 2026-09-25 evening, because the profile moved
again.** `b9a5f76` added a read-only grant for `~/.config/gh` after the table
above was measured. This is the same kind of change that made the first
version stale, so all nine probes were re-run. The method was the same:
profiles from `compose()`, a private tmux server, and `rite_ai` confirmed to
resolve to the tree measured. **Every result reproduced.**
- P1: temp-root write refused, directly and through tmux.
- P2: `yoloai ls` refused (`open ~/.yoloai: operation not permitted`, and the
  socket error through tmux).
- P3: the outside process survived.
- P4: HTTPS 200/301.
- P5: 44 directories listed and another project's transcript read. The count
  grows as sessions are created. SB4 is unchanged.
- Two Managers: kill refused, beta survived; `send-keys` and `kill-session`
  refused, beta's session survived; beta killed its own child; alpha wrote
  into `.rite/managers/beta/`.
- Also measured, for the new grant: `~/.config/gh/config.yml` is readable
  and a write there is refused. It is read-only, as `b9a5f76` says.

So **§5.4.8's P2 (process separation) holds on `main`**, by exactly the two
mechanisms the property names. **P1 (state separation) does not**, and the
profile is not what could make it hold. See track MM.

### 0.3 The network and the tmux route are separable, and were separated

The first version of this plan measured that `(allow network*)` grants the
tmux socket (a Unix-domain socket counts as network in seatbelt), and that
loopback-only confinement closed both. It concluded that the broker's
unfinished half and egress were **one problem**, which would close together.

⚠ **That conclusion was wrong, and `9862b59` is the disproof.** The socket was
closed **without touching `(allow network*)`**, by denying its path:

    (deny network-outbound (subpath "<TMUX_TMPDIR>/tmux-<uid>"))
    (deny file-read* file-write* (subpath "<TMUX_TMPDIR>/tmux-<uid>"))

placed **last**, because seatbelt takes the last matching rule and the
profile grants `/tmp` wholesale further up. The outside network and loopback
both still answer (0.2, probe 4). **So track SB and track EG sequence
independently.** EG is no longer the thing that closes a known escape, and SB
does not wait for it.

The first measurement still holds as a fact about the kernel. The loopback
variant refused outside hosts (curl exit 6 by name, exit 7 by address), kept
loopback (200), and refused the tmux socket. It is still the most likely
shape for a Manager's egress (EG3). It is no longer the only way to close
the tmux route.

### 0.4 What a seatbelt profile can say about the network

Two things were measured (the second on `9862b59`):

- **An IP destination can only be `*` or `localhost`.** A profile containing
  `(remote ip "1.1.1.1:443")` is rejected at load: `sandbox-exec: host must be
  * or localhost in network address`.
- **A Unix-socket destination can be named by path.**
  `(deny network-outbound (subpath …))` loads and is enforced. It is how the
  tmux socket is denied on `main` today.

⚠ **So the earlier sentence "no seatbelt profile can express a destination
list" was too broad.** For **IP** destinations it is true: everything, or
loopback. For **local sockets** the profile can allow or refuse by path. The
conclusion for egress survives: a list of *hosts* cannot live in a Manager's
profile, and has to be enforced by something outside it that the Manager's
traffic passes through. But it changes what that something must carry (track
EG): **local-socket destinations can be refused in the profile itself**, and
only IP traffic needs the proxy.

### 0.5 The record: what the first version measured on `b85e52e`

Kept because it shipped. On `b85e52e`, a write the profile refused directly
succeeded through the tmux server. `yoloai ls`, refused directly, listed every
sandbox on the machine through it. And a blanket `(allow signal)` let the
sandbox kill a process outside it. `limitations()` told the operator other
projects were unreachable. `7b5462d` corrected the claim, and `9862b59` closed
both holes with before-and-after measurements.

---

## Track MM — Multi-Manager

### Decided — honoured here, not reopened

| decision | where recorded |
|---|---|
| **Subdirectories inside one root**: `<root>/.rite/managers/<name>/`, not separate roots | D-79, §9.14.9 item 3. Robert: *"subdirectories inside one root. Strictly separated — one misbehaving manager shouldn't be able to mess with others by accident."* Taken with the counter-argument in front of him (`V070_MULTI_MANAGER.md`, superseded banner) |
| **Strictly separated, against accident** | same quote. §5.4.1 already carries it: containment, not fairness, and "by accident" |
| **Profiles shared and committed; per-instance configuration gitignored** | §9.14.9 item 4, and Robert's brief for this plan |
| Identity is the name the Manager was started with | D-77 |
| Engine is a Manager attribute, several engines at once | D-72 |
| `rite start` bare: 0 fails, 1 works, 2+ refuses and lists | D-78 |

The cost D-79 accepted is carried forward too. A shared root is a second
protocol that can drift from the state layer's, and whoever builds this should
be **looking for** that drift.

### What exists on `main` today

This is what the specs below build on. Each line is checked against the code.

- **Per-process identity.** `RITE_MANAGER` is set on every session
  `session.start` creates (`managers/__init__.py`, `MANAGER_ENV`).
- **A per-Manager directory, for new state only.** `manager_dir()` →
  `.rite/managers/<name>/`. The mailbox lives there
  (`mailbox.py`: `.rite/managers/<manager>/mail/<box>/`), and so will the
  check-in queue (K2). The module says existing `.rite/` files "stay where they
  are until 0.6.0". ⚠ **No v0.6.0 ticket moves them**, so that sentence points
  at a release that will not do it. Carried here as MM1.
- **Per-instance records** live under `.rite/user/`: instance JSON, the
  permission settings and the seatbelt profile. `.rite/*` is gitignored by
  `rite init`'s block, so both `managers/` and `user/` are uncommitted by
  construction.
- **The duplicate-start refusal is per Manager name**, not per project
  (`session.py`: *"Manager '<name>' is already running as …"*). So several
  Managers per project can already run. §9.14.0 and D-62 still say "at most
  one Manager session per project". §9.14.7b recorded that the tier model
  voids that sentence, and neither was ever marked. The D-62 cell is narrowed
  in `SPEC.md` by this plan, and the idempotence argument per name is owed
  (MM6).
- **The sandbox separates Managers' processes, and not their files.**
  Measured on `9862b59` (part 0.2): from inside Manager `alpha`'s profile,
  killing `beta`'s engine and driving or killing `beta`'s tmux session were
  all refused, and `beta` survived. That is `(target same-sandbox)` and the
  socket denial. But each profile grants the **whole project tree**
  (`enclosure.compose`), and `alpha` wrote into `.rite/managers/beta/`. That
  is not a defect in B9, whose purpose was the operator's home. It does mean
  the profile enforces the *process* half of "strictly separated" and not
  the *state* half, and nothing should cite it for the second.

### The requirement, as properties — SPEC §5.4.8

The decided requirement is written into `SPEC.md` §5.4.8 as four properties,
each with the thing that would enforce it and its state today. Summarised:

| # | property | enforced by | state on `main` |
|---|---|---|---|
| P1 | No rite command acting as Manager A writes rite state belonging to Manager B | the name-to-path join (§5.4.2), plus a test over the command surface (§5.4.7) at **Manager** granularity | ❌ most per-project state is still flat in `.rite/` (MM1); the §5.4.7 test does not exist |
| P2 | Manager A's processes cannot signal or drive Manager B's | the profile's `signal` target, and the tmux server being out of reach | ✅ **holds on `main`**, measured between two Managers' profiles on `9862b59` (part 0.2). A "cleanup" `pkill -f goose` in one Manager cannot take down its siblings. Not yet pinned by a two-Manager test (MM5) |
| P3 | State shared by decision is written only through its locked writer, and the list of it is enumerated by a test | §5.4.6 | ⚠ §5.4.6's list named the outbox, which has moved per-Manager. Struck from the list in SPEC 0.24.0. No test enumerates the list |
| P4 | Releasing or destroying names the Manager whose thing it is | §5.4.3 | ❌ `Claim` has no manager field, so "release only my Manager's claims" cannot be expressed (MM3) |

⚠ **"By accident" sets the bar, and the bar is not "against a hostile
Manager".** Robert's words and §5.4.1 agree. A Manager may run `rite`, and
`rite` does what the operator can (`limitations()` says so). Nothing in this
track claims to contain a Manager that *tries*. What it claims is that the
ordinary mistakes cannot cross: a wrong path join, a broad `pkill`, a
`tmux kill-server`, a force-release matched by path.

### Tickets

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| MM1 | **Move shared-by-accident state under `.rite/managers/<name>/`.** §5.4.5 step 2 and §9.14.9 item 3. Enumerate first, with a test that fails when a new flat path appears (§5.4.6's warning: `state.py` once claimed "every state file" and missed four writers). ⚠ **An upgrade must not happen mid-run.** Relocating live state breaks a project that upgrades while a Manager runs, which is why `managers/__init__.py` deferred it. The upgrade path needs its own refusal: detect a running Manager and refuse to migrate. | Two Managers started in one real project: each one's runtime state appears under its own directory, the flat paths are gone, and an upgrade attempted while a Manager runs is refused, naming it | — | 2–3 sittings |
| MM2 | **The §5.4.7 test, at Manager granularity.** Every leaf command, run as Manager A (with `RITE_MANAGER=A`) with hostile arguments, writes nothing under B's directory or B's instance files | The test exists, and a deliberately planted join that writes into B's directory fails it (mutation by backup copy, never a git restore, per the v0.6.0 plan's practice note) | MM1 | 1–2 sittings |
| MM3 | **Claims carry the Manager.** A `manager` field on `Claim`. `force_release` scoped to it, with no default that matches across Managers | Two Managers hold claims on different paths; A's force-release by path refuses to touch B's and says whose it is | — | 1–2 sittings |
| MM4 | **A home for per-instance configuration.** Decided: gitignored. Location: see MMQ1 | Per MMQ1's answer: a key set in the instance file changes one machine's Manager and appears in no `git status` | MMQ1 | 1 sitting |
| MM5 | **Pin P2 between two Managers.** The mechanism landed in `9862b59`. Its tests pin "a process outside the sandbox", not a sibling Manager's sandboxed process, and a later profile change (say, a shared grant for check-ins) could reopen the second without failing the first | A test composes two Managers' profiles in one root, runs B's stand-in engine inside B's profile, and asserts A's `kill` and A's `tmux` against B's session are refused and B survives. This plan's part 0.2 did the same by hand | — | ½ sitting |
| MM6 | **The idempotence argument, per name.** §9.14.0 paid for amending D-50 with "one Manager per project". The code refuses per name. Write the per-name argument, including what fails closed (D-74) when two *different* Managers are asked for at once | NOT OBSERVABLE, review gate. §9.14.0 marked in place | — | ½ sitting |
| ~~MM7~~ | **Moot: C12 landed as `0136447`**, "Make session_exists exact for any name, not only rite's". Kept so the id is not reused | — | — | — |

### Open questions — Robert's

**MMQ1. Where does per-instance configuration live?** Decided that it is
gitignored; not decided where. Nothing like it exists today: `.rite/user/`
holds runtime records, not configuration a person writes.

| option | for | against |
|---|---|---|
| (a) `.rite/managers/<name>/config.yaml` | beside the Manager's own state, so the §5.4.8 boundary covers it | a person edits a file under a runtime directory |
| (b) `.rite/user/<name>.yaml` | beside the instance record it configures | `.rite/user/` has twice held a file that was not an instance record (C11, and the permission settings). A third kind of file there is the collision class again |
| (c) a `.rite/config.local.yaml` overlay, keyed by Manager | one file to edit, the familiar `*.local` pattern | an overlay invites any committed key to be overridden per machine, which reverses "profiles are shared" by the back door unless the overlay's keys are enumerated |

What turns on it: whether the per-instance keys are **enumerated** (profile
keys refused in the instance file) or merely layered. Layering is simpler.
Enumeration is what keeps "a team agrees what a `planner` is" true.

**MMQ2. Several Managers, one Owner's DM: who acts on an instruction?**
As built on `main` since A6 (`b555b20`), Slack configuration is **per
project**: `slack.owner_user` names the Owner, whose DM with the app is the
command channel, and `slack.broadcast_channel` is where status goes. A
`command_channel` key is **refused by the parser**, because it let authority
be pointed at a shared channel (D-95, SPEC 0.24.3). Per project is settled
for v0.6.0, for a reason that holds in 0.7.0 as well: a Slack DM is one per
user and app, so two Managers cannot each have "the Owner's DM" without a
second app.

**What stays open is the accident.** With two Managers in one root, **both
read the one DM**, so one instruction from the Owner reaches both, and both
act on it. That comes from the code, not from an observation. Each Manager's
relay keeps its own cursor, in `.rite/managers/<name>/slack.json`
(`slack.py`, `_state_path`), so each one delivers every message. The shared
DM is therefore not split between them, and both receive all of it. Their
posts are told apart by the `*<name>*:` prefix A4 puts on each one. That is a cross-Manager accident of exactly the kind §5.4.8 exists
to prevent, and it arrives with the first multi-Manager project that uses
Slack. (The first version of this question described a shipped
`command_channel`. That shape has since been replaced, and the question is
rewritten against what is built.)

| option | what it means | turns on |
|---|---|---|
| (a) one app per Manager | each Manager has its own DM with the Owner | every user already creates their own app (A3b). This multiplies it per Manager, and every app is its own setup |
| (b) one DM, addressed per Manager | an instruction names the Manager it is for, and a Manager treats an unaddressed DM message as context | D-96 makes `@rite` a filter, not authority, and per-Manager addressing is a new rule on top of it. It also needs a default: one Manager, or none |
| (c) one Manager per project reads the DM | the others receive nothing from Slack | simplest, and it makes "which Manager is the Slack one" a profile key |
| (d) the Owner's id per instance (MMQ1) | each person's machine names its own Owner | answers a different question, who the Owner is on this machine, and not which Manager acts. Combinable with (b) or (c) |

**MMQ3. The worker cap's third denominator** (`V070_MULTI_MANAGER.md` Q2).
Per-project and machine-wide exist. Per-Manager does not. Options: (a) no
per-Manager cap, so Managers compete first-come within the project cap; (b) a
per-Manager cap in the profile; (c) in the instance configuration. A Manager
that takes every slot is a P-style accident ("misbehaving by accident"), which
argues for (b) or (c). `SANDBOX_CAPACITY.md` records that the machine-wide
count already includes other projects' sandboxes, and that interacts.

**MMQ4. Correlated failure** (`V070_MULTI_MANAGER.md` Q1). Several Managers on
one machine going quiet together reads as several independent deaths to a
per-holder timeout. Not decided whether v0.7.0 detects it, or only documents
it.

**MMQ5. A combined check-in across Managers.** `V060_CHECKINS.md`: one digest
per Manager is the 0.6.0 shape, and a combined standup "is not planned". With
three Managers and three windows a day, that is nine digests. Keep, merge, or
make it the Owner's job. Not scheduled until answered.

**Also still open, from `V070_MULTI_MANAGER.md`:** what `local:<class>`
classes are (Q3: they must come from `engine_probe`, not config), and that
Managers on one host share the keychain, the tmux server, the yoloAI namespace
and `~/.rite` (Q4). Part 0.2 turns the tmux-server half of Q4 from a note into
a measured escape.

---

## Track CU — Cursor, the third engine

**Status: READ, NOT MEASURED.** From Cursor's CLI reference
(`cursor.com/docs/cli/reference/parameters`, read 2026-09-25). The binary is
not installed here. This project has excluded one tool twice on reasons nobody
ran (the v0.6.0 plan, B0), so **CU1 is a measurement and nothing after it
starts until it reports.**

### The documented surface, against R1–R7

| R | Cursor, as documented | open until measured |
|---|---|---|
| R1 one turn | `agent -p` / `--print`: "Has access to all tools, including write and shell" | ⚠ whether the instruction can come from **stdin or a file**. The docs show it as an argument. rite refuses to put a prompt on argv, because `tmux new-session` puts argv where `ps` shows every account (ENGINE_CONTRACT axis 3). If only argv works, that is a blocker, not a detail |
| R2 end observably | the process exits. `--output-format json\|stream-json` | exit codes are **not documented** (the docs page says so). Measure a missing model and an unreachable service, as B1 did for Goose |
| R3 continuity | `--resume [chatId]`; `create-chat`: "Create a new empty chat and return its ID" | whether `create-chat` needs the network or an account, and what it prints |
| R4 human attach | `agent --resume <id>` interactively | whether it is the same conversation. B1's method: a token told in turn 1 and asked for in turn 2 |
| R5 no approval | `-f`/`--force`: "Force allow commands unless explicitly denied". `--trust`: "Trust the workspace without prompting (headless mode only)". `--approve-mcps` | whether `--force` without `--trust` **hangs** headless (class 15). "Unless explicitly denied" suggests a deny list, so `per_command_refusals` may be True. Unknown |
| R6 checkable | `agent status`/`whoami`, `agent models`, `--version` | whether `status` exits non-zero when logged out |
| R7 rite verifies | not the engine's to provide | — |

### The handle, and what it costs the contract

The engine contract admits two directions today (`ENGINE_CONTRACT.md` axis 1,
`Spelling.handle_is_ours`):

| | who mints the handle | when | rite's job |
|---|---|---|---|
| Claude | the engine | **during** the first turn | discover it afterwards, from the transcript |
| Goose | **rite** (`-n <name>`) | before, by declaring it | say it on the first launch (`Spelling.start`) |
| **Cursor** | the engine | **before** the first turn, **on request** | ask for one (`agent create-chat`), record it, pass `--resume <id>` on every turn, the first included |

**Cursor is a third direction, not a variant of either.** The costs, in order
of how much they change:

1. **A boolean can no longer carry the axis.** `handle_is_ours` answers
   "rite chooses, or rite discovers". Cursor needs neither: rite does not
   choose the id and has nothing to discover. The axis becomes "who mints, and
   when". Three values, and the supervisor branches on all three.
2. **The contract gains an engine invocation that is not a turn.** Every field
   of `Spelling` describes the argv of one turn. `create-chat` is a separate
   process with its own exit status and its own output to parse. It is the
   first place rite reads an engine's **stdout as data**, and so the first
   place a changed output format breaks rite. It needs its own R2 (did it
   succeed?) and R6 (is it reachable?).
3. **A new partial-failure state.** Mint, then fail to record, and an orphan
   chat exists that rite cannot name. Record, then the first turn fails, and a
   designated handle points at an empty chat. The second is benign. The first
   must not leave rite believing it has a conversation. The order is: mint →
   record atomically → launch.
4. **The id is engine-supplied text on its way into argv and a tmux command.**
   It gets `session_id_problem`'s hostile-id refusal, as Claude's does. Goose
   never needed that, because its handle is a Manager name rite validated.
5. **What it buys, which is worth saying.** The two measured resume failures
   were Claude's (the transcript scan returns `""`, the cycle starts fresh and
   reports success) and Goose's break 1 (no `-n` on the first cycle). **Both
   are structurally impossible** when the handle exists and is recorded before
   the first turn. The only question is whether `create-chat` can fail
   silently. CU1 measures that.
6. **Where it runs: proposed, not decided.** The natural place is the
   **supervisor**, outside the Manager boundary, before the pane starts,
   because that is where the launch is built and where a mint that fails can
   be refused before anything starts. The cost is that the supervisor would
   then need Cursor's credential too, where today only the engine inside the
   pane needs its own. The alternative, minting inside the pane as the
   engine's first act, keeps the credential in one place and brings back the
   partial-failure states in item 3 inside the boundary, where the supervisor
   cannot see them. CU3 is written for the supervisor. If CU1 shows the mint
   needs something only the pane has, revisit it.

**D-63's freeze condition is untouched by this.** It says the adapter
interface freezes when `local` binds unchanged to a conformance suite. Cursor
is a reason the interface is **not** frozen yet, which is what D-63 expected.

### Other axes Cursor pushes on

- **Cursor has its own sandbox**, `--sandbox enabled|disabled`. ⚠ **Inference,
  not measurement:** if it applies a seatbelt profile on macOS, B9's rule
  (only a semantically equivalent profile may be re-applied inside a sandbox)
  predicts it is **refused** inside rite's Manager profile. So a Cursor Manager
  would run `--sandbox disabled` inside rite's boundary, or fail at launch.
  CU1 measures it.
- **Credentials.** `CURSOR_API_KEY` in the environment, or `agent login`'s
  stored login, whose location is not documented. The environment route meets
  C6's rule (landed, `c58e4e5`): only named variables are allowed onto tmux's
argv, so a key sent that way must be admitted by name, with the exposure
that brings, or go another route. The stored route needs the profile to grant a
  path nobody has named yet, the way it grants `~/.claude`. Either goes
  through the per-project credential store (§10.2) under rite's vocabulary.
- **Egress.** A Cursor Manager's model endpoint is Cursor's service. The local
  tier's "nothing leaves this machine" never applies to it, and track EG's
  list must name the endpoint. Which hosts they are is not documented. EG0
  measures them.
- **Workers.** yoloAI's agents are aider, claude, codex, gemini, idle,
  opencode, shell and test (B4d). **There is no Cursor Worker through yoloAI.**
  Every mention of `cursor` in the design is a Manager engine
  (`V070_MULTI_MANAGER.md`, D-64), so this plan scopes Cursor to Managers.
  See CUQ1.
- **Configuration vocabulary.** `engine: cursor` and nothing Cursor-named in
  `config.yaml` (the v0.6.0 plan's B0a rule: no engine's nouns reach config).

### Tickets

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| CU1 | **Spike: does Cursor do what its docs say?** Every "open until measured" cell above, plus: `create-chat` output and failure modes; nested `--sandbox enabled` inside rite's Manager profile; which hosts it contacts (feeds EG0). Pin the version measured | A spike note under `spikes/`, in the shape of B1/B4d: every row measured or marked not measured. A two-turn token test against a real account passes or fails on the wire | — | 1–2 sittings |
| CU2 | **Generalise the handle axis in `engines.py`**, then update `ENGINE_CONTRACT.md` from the code as it lands (B3b's rule: the module is right, and the note is the bug) | Claude's and Goose's launch commands byte-identical across B3a's 54 argv combinations, and a Cursor spelling that mints, records and resumes | CU1 | 1–2 sittings |
| CU3 | **Mint in the supervisor, record atomically, refuse on failure** | Through `rite start` with a stub `agent`: a mint that fails produces a refusal and no pane; a mint whose record fails leaves no designation; a hostile id is refused | CU2 | 1 sitting |
| CU4 | **Profile grants and credentials** | A sandboxed Cursor Manager authenticates with no `HOME` redirection, and its credential is absent from tmux's argv (`ps`) | CU1 | 1 sitting |
| CU5 | **`rite doctor` for Cursor** (R6) | Logged out, the probe says so in Cursor's own words and exits non-zero | CU1 | ½ sitting |
| CU6 | **The both-halves observation, for Cursor** | A sandboxed Cursor Manager completes two cycles, the second recalls a token from the first, and it gets a Worker started through the broker, as `4ebbbd7` did for Goose | CU3, CU4 | 1 sitting |

### Open questions — Robert's

**CUQ1. Cursor for Workers?** Not through yoloAI as it stands. Options: (a)
Managers only, stated; (b) a Worker through yoloAI's `shell`/`idle` agent with
rite driving Cursor inside it, as B4d found for Goose; (c) wait for yoloAI.
Recommend (a) for 0.7.0. Nothing in the recorded design asks for more.

**CUQ2. Which credential route is supported?** The API key (scriptable, meets
C6) or the stored login (interactive setup, meets the profile). Which one the
docs tell a user to set up is a product choice.

**CUQ3. What if CU1 finds `-p` takes the prompt only as an argument?** Options:
(a) do not ship Cursor; (b) accept the prompt on argv for this engine, with
the exposure stated at start; (c) a wrapper that reads a file. (c) is the kind
of glue the contract exists to keep inside the adapter. Decide after CU1, not
before.

---

## Track EG — Egress control

**The spec is `SPEC.md` §5.5**, decided by Robert on 2026-09-25 in the brief
for this plan (D-99, D-100). It states the property positively, puts
enforcement at the network layer, and confines content scanning to allowed
destinations that publish. This track is how to get there, and what is still
open.

### What the enforcement points can and cannot do

| where | mechanism | what it can express | source |
|---|---|---|---|
| **Worker, docker backend** | yoloAI `--network-isolated` plus `--network-allow <domain>` | a domain allowlist, **tamper-resistant**: the rules are installed from a helper container and the sandbox has no `NET_ADMIN` | read, `yoloai help security` |
| Worker, apple/podman/containerd | same flags | a guardrail only: the sandbox holds `NET_ADMIN` and can flush the rules | read, same |
| **Worker, seatbelt (rite's default `sandbox.backend`)** | none | `--network-isolated` is **refused** | read, same. D-30 |
| every backend | `--network-none` | nothing leaves | read, same |
| **Manager** (seatbelt, on the host, B9) | its profile | **IP: everything, or loopback only.** A named host is rejected at load. **Local sockets: allowed or refused by path**, as the tmux socket is refused on `main` | **measured**, part 0.3 and 0.4 |

⚠ **Two consequences a reader must not miss:**

1. **A Worker on the default backend cannot be egress-controlled at all.**
   yoloAI refuses the flag rather than pretending, which is the right shape.
   So the spec's property holds for Workers only where the backend can enforce
   it, and rite must say which case a project is in (EG2).
2. **yoloAI's list is IPv4 only** ("IPv6 IS NOT FILTERED"), and a domain list
   enforced by iptables is enforced on the **addresses the domain resolved
   to**. A CDN address shared with other tenants admits them too. Both belong
   in what rite prints, not in a footnote.

### The Manager: loopback confinement plus a proxy outside the boundary

Part 0.4 rules out a list of **hosts** inside the profile. Part 0.3 shows the
profile *can* confine the Manager's IP traffic to loopback. The shape that
follows is a **forward proxy, run by the supervisor outside the boundary**,
listening on loopback. The Manager's profile allows only loopback for IP, and
the proxy allows only the sanctioned hosts. A client that ignores
`HTTPS_PROXY` cannot reach anything, which **fails closed**.

**The work divides by transport, and part 0.4 is why.** The profile can
already name local sockets by path, so **local-socket destinations are
decided in the profile** and never reach the proxy: the tmux socket is denied
there today. Others (the name resolver's socket, anything under
`/private/var/run`) can be allowed or refused the same way. The keychain is
not one of them: it is reached through `mach-lookup`, a different rule (SB5).
**Only IP traffic needs the proxy.** ⚠ Not measured: which local sockets a
Manager's engines and tools need once IP is loopback-only. Name resolution
is the obvious one. A client that goes through a CONNECT proxy should not
need to resolve names itself, but that is unverified for every client in
question.

⚠ **Loopback is not one destination.** `localhost:*` admits every listener
on the machine: the local model endpoint (which the local tier needs), but
also any database, dev server or admin port the operator runs. **And, with
several Managers, each other's proxies.** If each Manager's proxy enforces its
own list, Manager A could send through B's proxy and get B's list.
**Measured 2026-09-25: a profile can allow one loopback port and refuse the
rest.** With `(deny network-outbound)` and
`(allow network-outbound (remote ip "localhost:18765"))`, port 18765 answered
200 and port 18766 was refused (curl exit 7). So each Manager's profile can
admit only its own proxy's port, which closes the cross-proxy route and also
shuts out the operator's other listeners. The local model endpoint then has
to be admitted by its port, or go through the proxy.

This shape is **inferred from measurements, and is not a decision.** It is
option (a) of EGQ1. ⚠ The first version of this plan preferred it partly
because the loopback line would also close the tmux escape. That reason is
gone: `9862b59` closed the escape by denying the socket's path and left
`(allow network*)` alone (part 0.3). EG3 now stands on egress alone.

### Tickets

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| EG0 | **Measure first: which destinations do real runs reach?** `V070_EGRESS.md` Q5. The v0.5.1 and v0.6.0 acceptance runs, the benchmark, a Slack-connected run, and CU1 for Cursor. Derive the default list from what was observed, as C4's allowlist was derived from 14,981 recorded invocations | A committed data file of observed destinations per engine and role, and a test that requires every default entry to trace to it | — | 1–2 sittings |
| EG1 | **The list is rite's vocabulary** in `config.yaml`: destinations as hosts (plus ports where needed), grouped by what they are for. No proxy or yoloAI syntax (`V070_EGRESS.md` Q2) | `rite doctor` renders the list and validates it with rite's own error wording. A proxy- or yoloAI-shaped key is refused | EGQ3 | 1 sitting |
| EG2 | **Workers: enforce where the backend can, and say where it cannot.** Docker: pass the list as `--network-allow`. Seatbelt: rite states at start that Worker egress is **not controlled** on this backend, every run. It does not say "restricted" | A docker Worker is refused a destination off the list, and the refusal names it; a seatbelt project's start line says egress is uncontrolled | EG1, EGQ2 | 1–2 sittings |
| EG3 | **Manager enforcement** per EGQ1. If (a): profile to loopback, proxy in the supervisor, `HTTPS_PROXY` in the engine's environment. Local sockets decided in the profile by path (part 0.4) | A real Claude Manager and a real Goose Manager each complete a cycle, including a ticket read and a `git push` to the sanctioned remote. A request to an unlisted host is refused **and reported** (EG4). With two Managers running, each one's profile admits only its own proxy's loopback port: A's request to B's proxy port is refused. `tmux` from inside is refused | EG0, EG1, EGQ1 | 3–4 sittings |
| EG4 | **A refusal names the destination and the line that permits it**, the C21 shape (`V070_EGRESS.md` Q3). A client that fails on a refused CONNECT reports a network error that looks like an outage, so the report must come from rite's side, read from the proxy's log, not from the client's message | The refused host appears in the pane-side refusal line **and** in the next check-in digest, with the config line that would allow it | EG3 | 1 sitting |
| EG5 | **Content scanning, on allowed destinations that publish, only** (D-100). The structural credential rule of `redact_secrets`/C7, not a list of token formats. **Model calls never scanned** | A token pasted into a `gh issue create` body on the sanctioned repo is caught and reported. An ordinary model request is not scanned, which the proxy's own counters show | EG3, EGQ5 | 2 sittings |
| EG6 | **Demonstrable for the local tier.** With a local engine and an internal-only list, `rite doctor` shows the list and a live refusal of an outside host | On a local-tier project, doctor's output contains the policy and a refused probe to a public host | EG3 | ½ sitting |

### Open questions — Robert's

**EGQ1. How is the Manager's egress enforced?**

| option | what it is | turns on |
|---|---|---|
| (a) loopback profile + rite's proxy in the supervisor | inferred shape above | rite owns a security-relevant proxy: CONNECT only, no TLS interception, host allowlist. Needs every client used by a Manager to honour `HTTPS_PROXY`, which is **not measured** for `claude`, `goose`, `git`, `gh` or `uv` |
| (b) loopback profile + a third-party proxy rite configures | as (a), a dependency instead of code | an external binary's vocabulary must stay out of `config.yaml` (B0a's rule), and it becomes a pinned dependency |
| (c) a host firewall (`pf`) | system-level rules | ⚠ changes a system security setting and needs admin rights. It is also machine-wide, not per-Manager |
| (d) Manager unconfined; Workers only | ship EG2 alone | the injection case in §6.6.3 lands on the Manager, which reads every ticket, so the property would not hold where it matters most |

**EGQ2. What does a seatbelt-backed project get?** (a) Egress is Worker-
uncontrolled and said so on every start (EG2 as written); (b) make docker the
default backend when an egress list is configured; (c) refuse to start Workers
when a list is configured and the backend cannot enforce it. (c) follows
§5.1.1's fail-closed rule most closely, and it breaks every existing seatbelt
project that adds a list.

**EGQ3. Who edits the list, and is it profile or instance?** Committed
(a team agrees where code may go) or per-instance (MMQ1). The destinations are
a team policy, which argues for committed. A single developer's local endpoint
is per machine.

**EGQ4. Redirects and DNS** (`V070_EGRESS.md` Q4). Under a CONNECT proxy, a
redirect to another host is a new CONNECT and is judged on its own, so it falls
out. Under yoloAI's iptables, the rule is on resolved addresses. Decide whether
rite documents that difference or narrows Workers to docker-with-proxy too.

**EGQ5. Which allowed destinations "publish"?** The line between "allowed" and
"allowed but public" is where scanning applies. `gh issue create` on a public
repository publishes; on a private one it arguably does not. Options: (a) mark
destinations `public: true` by hand; (b) treat every non-model destination as
publishing; (c) ask the host (the repo's visibility) at scan time. (b) is
simplest, and the false positives land on legitimate pushes.

---

## Track SB — the broker's unfinished half

**B9 answered one question for v0.6.0:** a sandboxed Manager cannot start a
sandboxed Worker (the kernel refuses a non-equivalent nested profile), so it
asks, and the broker decides. That half is built and observed (`eb2a88e`,
`4ebbbd7`).

⚠ **REVISED after review round one.** The first version listed seven items,
three of them measured holes. `7b5462d` and `9862b59` closed those three
(part 0.2 re-measured them). They are recorded as closed rather than deleted.
And the first version said this track and egress were one problem. They are
not (part 0.3), so **SB sequences independently of EG**.

### Closed on `main`

| # | what it was | closed by | re-measured on `9862b59` |
|---|---|---|---|
| ~~SB1~~ | the tmux server ran commands outside the boundary, so the broker could be bypassed | `9862b59`: the socket directory denied by path, placed last | ✅ a write and `yoloai ls` through tmux both refused with `Operation not permitted` |
| ~~SB3~~ | a blanket `(allow signal)` could kill any process the operator owned | `9862b59`: `(target same-sandbox)` | ✅ an outside process and a sibling Manager's engine both survived, and a Manager's own child can still be signalled |
| ~~SB6~~ | `limitations()` claimed other projects were unreachable | `7b5462d`, then `9862b59` | ✅ it now says the profile "bounds FILES, not capability", names `/tmp` as reachable, and describes the closed routes as "tried and refused" rather than as containment |

### ⚠ Open, and the one to raise first: SB4, a live cross-project read

**`~/.claude` is granted readable, whole.** Measured on `9862b59` from inside
a Manager's profile: 42 project directories under `~/.claude/projects` were
listed, and **another project's transcript was read.** A Claude transcript
holds that project's prompts, tool output and file contents. So any Manager
can read every other Claude Code project's history on the machine. That
includes projects that are not rite projects, and ones the operator would
never think of as "in reach".

⚠ **`limitations()` does not say this in these words.** It says "your home
outside the paths above … not reachable", and `~/.claude` is one of the paths
above. That is literally true, and nobody reading it would conclude that
every other project's transcripts are readable. The same class as the claim
`7b5462d` corrected.

**Why it was granted, and why narrowing is not a one-line fix:**
`~/.claude` holds the engine's settings, its hooks and its session store, and
the grant was needed for a Claude Manager to run at all. What is not measured
is which parts it needs. **Narrow it by measurement, not by guess.** The
`HOME` episode (B4d's correction) is the precedent: the plausible change there
broke the Claude login.

### Open

| # | what remains | evidence | closed by |
|---|---|---|---|
| **SB4** | **`~/.claude` readable whole**: every project's transcripts. See above | measured, part 0.2 | a grant narrowed to what a Claude Manager needs: its settings, its login-related files, and **this project's** transcript directory. ⚠ Measure the set first. Then observe a real Claude Manager completing a resumed cycle under the narrowed profile, and fail to read another project's transcript from inside it |
| SB2 | **`(allow network*)`**: the whole network | the profile's text; probe 4 in part 0.2 | **EG3**, as egress work in its own right. No longer the fix for any escape (part 0.3) |
| SB5 | **`(allow mach-lookup)` with no filter.** Claude Code on macOS keeps its login in the keychain, and `4ebbbd7` measured that the login is found inside the boundary, so at least that item is reachable. That is an inference, not a direct keychain probe. Not measured: whether a Manager can read **other projects'** rite credentials from it. The Worker-profile measurement (keychain content denied) does not carry over, because this profile differs | profile text | a measurement first. If reachable, per-service `mach-lookup` filtering, measured against the login |
| SB8 | **`/tmp` and `/private/tmp` are readable and writable**, and other rite worktrees and scratch directories live there | stated by `limitations()` itself, so disclosed rather than hidden | open, and not obviously closable: the engines need a temp space. The same measure-then-narrow method as SB4 |
| SB7 | **Which Manager may ask for which Worker.** A request names a declared Worker and a ticket. With several Managers in one root, any Manager can ask for any declared Worker. Whether Workers belong to a Manager is not decided (SBQ1) | `broker.py` validates against project-level declarations | MM, after SBQ1 |
| C25 | **No opt-out from the Manager's sandbox**, and an operator's own hook already fails inside it. Every narrowing in this track (SB4) raises the stakes | `V060_RELEASE_PLAN.md` C25 | a decision. See the consolidated list |
| C24 | **A requested Worker starts at the cycle boundary**, deliberately | `V060_RELEASE_PLAN.md` C24; `supervise.py::_honour_worker_requests` | a decision, not a fix. See the consolidated list |

**SBQ1. Do Workers belong to a Manager?** (a) No: Workers are project-level and
fungible (§5.3.4), and any Manager may request any of them within the caps; (b)
yes, declared per Manager profile. (a) is today's model and needs nothing. (b)
makes P1 cover Workers and changes `rite add worker`.

---

## Track ME — Memory

**Status: Robert's shape is recorded (`V070_MEMORY.md`). Its mechanics are not
decided, and this plan writes no spec for them.** Four of the note's seven open
questions decide the architecture: who writes the memory repository (Q2),
whether it passes the publish gate (Q3), whether it extends or replaces D-54
(Q4), and the retrieval unit for source (Q5). A spec written before those are
answered would be the speculative spec the bar rules out.

**What can proceed without a decision is measurement**, and each one changes
what the decisions should be:

| # | measurement | why first | source |
|---|---|---|---|
| ME0 | **The 50k-line claim.** Bucket closed tickets by total lines in the files touched, and compare delivery net of rework (§2.6.3, D-39) | the feature's motivation is an uninstrumented anecdote with no stated measure | `V070_MEMORY.md` Analysis 9 |
| ME1 | **The churn curve.** Replay this repository's last month against a built index. Where does blob-SHA churn bend? | the regeneration trigger's number must not be guessed | Analysis 5 |
| ME2 | **Local regeneration throughput** over a corpus this size | "the local tier absorbs it" is unmeasured | Analysis 7 |
| ME3 | **Write up the three blocked-session incidents** in `REVIEW_COST.md`'s table shape, or record that they cannot be reconstructed | the case for escalation machinery rests on them, and they are recollection | `V080_RELAY_CHANNELS.md` Analysis 1 |

### ⚠ Memory and check-ins filter different questions — MEQ1

Robert's escalation test, recorded in `V080_RELAY_CHANNELS.md`: *"do you have
all the information necessary to answer it straight away?"*

**Check-ins already filter one class of question, and memory would filter a
different one.** The two must not be built as one filter, or counted as one.

| filter | what it removes | when | built? |
|---|---|---|---|
| K3, the queue as a draft | questions that **resolve themselves with time**: the answer arrives through later work | at window open, for **deferred** questions only | v0.6.0, planned |
| memory, applying Robert's test | questions the Manager **could answer now from what the fleet already knows** | at ask time, immediate or deferred | not designed |
| `V080` Analysis 2's category rule | nothing. It **forces** escalation of decisions and irreversible acts whatever any model thinks | before either | not built (0.8.0 note) |

**The ordering matters, and it is where this goes wrong if it goes wrong.** A
decision is not a fact, and a model answering it from memory is not retrieving
it but *making* it (Analysis 2). So the category rule has to run **before**
the memory filter, never after.

**Robert's test is a self-report**, and `V080_RELAY_CHANNELS.md` Q11 records
that nothing measures whether the Manager looked. Memory is the first thing
that could make "did it look" checkable, because rite can run the retrieval
itself. The options:

| option | what happens | turns on |
|---|---|---|
| (a) prompt only | the Manager is told the test; nothing checks it | the instrument `V080` says already failed once |
| (b) **rite retrieves, and attaches the hits** to the question the User sees | the User sees what memory said beside the question; nothing is suppressed | costs a retrieval per question and reads as noise when memory is stale. Suppresses nothing, so its failure is visible |
| (c) rite retrieves, and **bounces** the question to the Manager when hits exist | the question is withheld until the Manager rules the hits out | stale memory suppresses a real question, which is `V070_MEMORY.md` Analysis 3's invisible wrong-exclude failure. It also contradicts check-ins' "anything uncertain is blocking, ask now" |
| (d) as (b), but only at K3's re-evaluation, for deferred questions | memory joins the draft filter, not the immediate path | smallest, and leaves immediate questions to the self-report |

**Counted separately whatever is chosen.** K3's digest already reports queued
/ withdrawn / asked. A memory filter adds its own count ("answered from memory
before asking"). Otherwise a rise in withdrawals cannot be attributed, and
"deferral filters" stops being measured.

Recommendation, not decision: (b) or (d). Both leave a stale memory visible
rather than silently deciding what the User is asked.

### Carried from `V070_MEMORY.md`, unchanged

Open questions 1–7 stand as written there and are not repeated here. Two
status paragraphs in that note (and in `V080_RELAY_CHANNELS.md`) are stale:
they say the other design notes live in gitignored `.docs/`. They are all
under `docs/design/` now (see `README.md` here). Corrected in both notes by
this plan.

---

## Carried forward — the index

**Robert's rule is postpone, never drop.** Every item below was deferred
somewhere and is still open. This table is an index. The detail stays where
it was recorded.

### Scheduled for 0.7.0 by an earlier decision

| item | from | state |
|---|---|---|
| **The scenario gate** (§7.3, D-81) | v0.6.0 plan, Decision 5; SPEC 0.22.1 | Not costed, deliberately: it is a process change as much as a feature. **Needs its own design pass before a size**, and that pass must answer §9.15.3a's recorded gap first: the gate cannot reach journal entries, because they are machine-local and uncommitted |
| **Relocate flat `.rite/` state per Manager** | §5.4.5 step 2; §9.14.9 item 3; `managers/__init__.py` ("until 0.6.0") | MM1. No v0.6.0 ticket carries it |
| `V070_MULTI_MANAGER.md` Q1–Q4 | that note | MMQ3, MMQ4, MM, SB |
| **C25: no opt-out from the Manager's sandbox**, Robert's | `V060_RELEASE_PLAN.md` C25, landed after this plan's second version | **A decision.** In the consolidated list as C25, because SB4 and EG3 sharpen it |
| **C24: a requested Worker starts at the cycle boundary**, deliberately. Robert's to change | `V060_RELEASE_PLAN.md` C24, landed `5bda48a` after this plan's first version | **A decision, not a carried fix.** In the consolidated list as C24 |

### Becomes 0.7.0 if it does not land in 0.6.0

The v0.6.0 plan's "can slip to v0.7.0" list, **checked against `main` on
2026-09-25.** C5, C8, C10, C11, C12, C13 and C23 have all landed (each row in
`V060_RELEASE_PLAN.md` now names its commit), so they are not carried. What
is left:

| item | v0.6.0 id |
|---|---|
| Wire `harness.run_subtask` to Goose; prove on the benchmark | B4, B5. B5 has already moved to the Worker tier, which is **scheduled in no release** (below) |
| A check-in window with no Manager running, **Robert to confirm** | K6 |
| N1/N2, ticket-text cleanup and phrase reporting. **Placed last in v0.6.0 and droppable** (Robert's ruling, `beac07f`). If they are dropped for quota, they arrive here. Applying §6.6 to Slack text is accepted | the v0.6.0 plan's part N |

### Deferred without a release, and at risk of evaporating

| item | from | why it is listed |
|---|---|---|
| **The Worker tier**: decompose duty (RL-T7), `harness`/`runners` gaining a caller, B5 | v0.6.0 plan, B4b reshaping | "out of v0.6.0" with no destination. Needs a release named |
| **Re-advertising `--record-issues`**, now that the journal is redacted | §9.15 (reversed for 0.5.1 "until the leak path is closed"); C7, **landed** as `5ec5173` | ⚠ **The precondition is met and nobody has been asked.** §9.15 kept the feature unadvertised only because the journal applied no redaction. It does now, and the flag is still absent from the README, the guide and the CHANGELOG. Whether to advertise it in 0.6.0 or 0.7.0 is Robert's call. It is in the consolidated list |
| **Pin the Goose version** | v0.6.0 plan, Decision 4 ("And pin the version") | a sentence with no ticket. No pin was found in `src/` (searched for the measured version string and for "pin") |
| **A derived Modelfile instead of warning about the 4,096 window** | v0.6.0 plan, B8's closing section | an option recorded for a decision: it writes into the operator's Ollama library |
| **The full ten-task benchmark**; opencode's two `--format json` defects | RL-T0 section 6 | measured on five tasks only |
| **Finding B is written down nowhere** | `V080_RELAY_CHANNELS.md` | the note restates it but cannot cite it |
| **Slack: is a manifest-created app "internal customer-built"?** Will rite ever be commercial? | A3b | both Robert's. The transport depends on the first |

---

## Stale findings — what this plan found, and what it did about each

| # | where | what is stale | action |
|---|---|---|---|
| S1 | `SPEC.md` D-76 and §5.4 opening | "a Manager is not sandboxed" / "Managers do not get one" | **Corrected** by this plan: D-76 marked superseded, §5.4 opened with the correction |
| S2 | `SPEC.md` §5.4.5 | "there is no per-Manager directory … no `RITE_MANAGER`" | **Corrected**: banner saying both exist and step 2 does not |
| S3 | `SPEC.md` §5.4.6 | lists the outbox as shared, and it is now per-Manager (`mailbox.py`) | **Corrected** in place |
| S4 | `SPEC.md` §9.14.0 / D-62 | "at most one Manager session per project"; code refuses per name | **D-62 narrowed**. §9.14.0's argument owed as MM6 |
| S5 | `enclosure.limitations()` | "other projects … NOT reachable" | **Closed on `main`** by `7b5462d`/`9862b59`, re-measured (part 0.2). One gap remains: it does not name other projects' **transcripts** under `~/.claude` (SB4) |
| S6 | `CHANGELOG.md` Unreleased, and `README.md`'s "Why you might not want it" | "A Manager remains **unsandboxed**" (CHANGELOG), and "no permission gate, unsandboxed" (README heading), both false since the allowlist and `4ebbbd7` | **Corrected** after review round one. Unreleased also gains an entry for the Manager boundary itself, which the 0.6.0 release notes did not mention at all |
| S7 | `V060_RELEASE_PLAN.md` B9 row | "MEASURED NOT POSSIBLE as specified", with no note that the broker shape was built | **Corrected** after review round one: a DONE note naming `eb2a88e`, `4ebbbd7`, `7b5462d` and `9862b59`, with the original text kept |
| S8 | `spikes/B9-manager-sandboxing.md` | its `HOME` section still says to point `HOME` at the sandbox, the advice `4ebbbd7` corrected in B4d. No banner saying the broker was built | **Corrected**: banner added |
| S9 | `spikes/B4d-…md` section 3 | "Managers do NOT run sandboxed" | **Corrected**: banner line |
| S10 | `spikes/A3a-slack-transport.md`, `A3b-…md` | "the proof is NOT made". The plan records A3a done (0.24 s) | **Corrected**: banner added |
| S11 | `spikes/RL-T0-agent-comparison.md` section 6 | `GOOSE_MODE=auto` "not directly probed"; B4d probed it | **Corrected**: pointer added |
| S12 | `V070_MEMORY.md`, `V080_RELAY_CHANNELS.md` status paragraphs | say the other notes are in gitignored `.docs/` | **Corrected** |
| S13 | `V070_MULTI_MANAGER.md` "Status of the file itself" | says it does not reach a fresh clone | **Corrected** |
| S14 | `carried-limitations-register.md` D1 | "no `RITE_PROJECT_ROOT` env var exists". It does, and the marker is now a file rather than the bare directory (`cli/main.py`, `_find_project_root`) | **Annotated** after review round one: both suggested fixes are in the code. **The entry stays OPEN**, because the register's rule is that only a named run clears it |
| S15 | `V070_EGRESS.md` | open question 1's premise (no Manager sandbox) | **Corrected**: banner pointing at §5.5 and this plan |
| S16 | `V060_RELEASE_PLAN.md` A6 vs `1cee54c` | A6 said per-Manager keys under `manager_roles[]`. What shipped was one project-level `slack:` section with a `command_channel` | **Resolved by A6 itself** (`b555b20`): per project, with `command_channel` refused. MMQ2 is rewritten against that shape |
| S18 | `V060_RELEASE_PLAN.md` C5 and C7 rows | both read as open. C5 landed as `4c67b7e` (`rite reply`) and C7 as `5ec5173` (journal redaction) | **Annotated**: each row now names its commit |
| S17 | this plan's own first version | part 0.2's three holes, part 0.3's "one problem", "no seatbelt profile can express a destination list", SB1/SB3/SB6 open, and SPEC §5.4.8 saying none of the four properties hold | **Corrected** after review round one, by re-measuring against `9862b59` (part 0) |

---

## Decisions needed — consolidated

| id | question | blocks |
|---|---|---|
| MMQ1 | where per-instance configuration lives | MM4, EGQ3 |
| MMQ2 | several Managers read the one Owner's DM: who acts on an instruction | any multi-Manager project using Slack |
| MMQ3 | a per-Manager worker cap | — |
| MMQ4 | correlated failure: detect, or document | — |
| MMQ5 | combined check-ins across Managers | — |
| CUQ1 | Cursor for Workers | — |
| CUQ2 | Cursor's credential route | CU4 |
| CUQ3 | if `-p` takes argv only | CU2, after CU1 |
| EGQ1 | how the Manager's egress is enforced | EG3 (and SB2) |
| EGQ2 | seatbelt projects and a configured list | EG2 |
| EGQ3 | the list: committed, or per-instance | EG1 |
| EGQ4 | redirects/DNS under iptables | EG2's documentation |
| EGQ5 | which allowed destinations publish | EG5 |
| SBQ1 | do Workers belong to a Manager | SB7 |
| **C25** | **Should a Manager be startable outside its sandbox, and how?** The profile is unconditional, and a real operator's own `SessionEnd` hook already fails inside it (`V060_RELEASE_PLAN.md` C25, with options: a config key, a `--no-sandbox` flag, or widening the profile per project). **It becomes a 0.7.0 question if 0.6.0 does not answer it, and 0.7.0 makes it sharper.** SB4 narrows `~/.claude`, and EG3's loopback-only profile would cut a hook's network. Each narrowing turns more working setups into failures inside the boundary, and today there is no way out. Whatever is chosen must make the announcement say the boundary is off (the false-claim class) | SB4 and EG3 should not ship before it is answered, or they should ship with it |
| **C24** | **Should a Worker requested mid-cycle start at once, or at the cycle boundary as it does now?** (a) At the boundary, as shipped: the cost is latency, which the Manager's prompt tells it about. (b) At once: `rite sandbox start` takes tens of seconds, so it has to run without blocking the supervisor's two-second poll (a thread or a watched subprocess), and a cycle that ends mid-launch needs a defined meaning. What turns on it: how much machinery goes into the one loop 0.6.0 spent its time simplifying. It also interacts with CU3, which adds a second piece of pre-launch work (Cursor's mint) to the same supervisor | nothing blocks on it. It is already behaviour, and Robert's to change (`V060_RELEASE_PLAN.md` C24) |
| MEQ1 | where memory sits relative to Robert's test and K3 | memory's ask-time path |
| `V070_MEMORY.md` Q1–Q7 | memory's architecture | any memory spec |
| — | the Worker tier's release | B5, harness |
| — | re-advertise `--record-issues`, now that C7 has redacted the journal: in 0.6.0, 0.7.0, or not yet | nothing. The feature works and is unadvertised (§9.15) |

## Sequencing, and where the release can be cut

**Measurements first, because four of the tracks turn on them:** CU1, EG0,
ME0–ME3, SB4/SB5's two measurements. None needs a decision, and all can run
while the decisions above are asked.

**Then** MM1 → MM2, MM3 (independent of every decision except MMQ1 for MM4);
**then** EG1 → EG3 → EG4; **then** CU2 → CU6; EG2, EG5, EG6 as their
questions land. **SB4 and MM5 do not wait for egress.** The first version
sequenced them behind EG3 on the "one problem" reading, which part 0.3
withdraws. SB4 comes first in SB, because it is a live cross-project read.

**The coherent minimum:** MM1–MM3, MM5, SB4, EG0, EG1, EG3, EG4. That is "several
Managers in one root cannot disturb each other by accident, and a fooled
Manager cannot send anything off-list". Cursor and memory can slip whole
without leaving anything half-built. **Egress cannot ship without EG4**: a
silent network refusal is the stall-without-a-message class again.

**Not sized as a total, on purpose.** The v0.6.0 plan's note on units applies:
sittings are ordinal, not a schedule.
