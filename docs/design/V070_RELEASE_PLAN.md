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
- **Measured, read or inferred, and it says which.** Four facts in part 0 were
  measured for this plan. The Cursor facts were **read** from Cursor's
  documentation, because the binary is not installed on this machine. The
  yoloAI network facts were **read** from `yoloai help security` (0.11.0).

**Where the specs live.** Anything decided is in `SPEC.md`: §5.5 (egress,
new), §5.4.8 (separation between Managers, new), and corrections to §5.4,
D-62 and D-76. Everything that still needs a decision stays here, because a
spec section for an undecided feature is the defect this project spent two
releases removing.

---

## Part 0 — what changed under the v0.7.0 notes, and four measurements

**Read this before any track.** The design notes this plan works from
(`V070_EGRESS.md`, `V070_MULTI_MANAGER.md`, B9) were written before B9 landed.
B9 changed the ground under three of them.

### 0.1 A Manager now runs inside a boundary

`eb2a88e` and `4ebbbd7` on `main` did two things:

- the Manager's pane runs `sandbox-exec -f <profile> <engine …>`, with the
  profile composed by `managers/enclosure.py`;
- the Manager asks for a Worker, and a host-side broker (`managers/broker.py`)
  validates the request and runs `rite sandbox start`.

The observation in `4ebbbd7` covered both halves: a sandboxed Goose Manager
completed a cycle, and a Worker really started from its request.

**Consequences for what was written earlier:**

- **D-76 ("a Manager is not sandboxed") is reversed in code.** Its cell was
  never marked. Corrected in `SPEC.md` by this plan, under §13's supersession
  convention.
- **§5.4's opening ("Managers do not get one") is stale, and so is §5.4.5.**
  §5.4.5 says there is no per-Manager directory and no `RITE_MANAGER`. Both
  exist (`managers/__init__.py`: `manager_dir`, `MANAGER_ENV`). Corrected.
- **`V070_EGRESS.md` open question 1** says "The Manager does not have one [a
  sandbox]". It has one now, and part 0.3 shows it is the right place to
  enforce egress, in a narrower way than the note hoped.

### 0.2 ⚠ Measured today: the boundary can be walked out of

**Method.** Measured 2026-09-25 against `origin/main` at `b85e52e`, with
`rite_ai` confirmed to resolve to this worktree. The profile came from
`enclosure.compose()` itself, not a hand copy. A private tmux server ran on a
short `TMUX_TMPDIR` so the operator's own server was never touched. Every
probe ran `sandbox-exec -f <that profile> …`.

| probe, from inside the Manager profile | direct | through the tmux server |
|---|---|---|
| write a file in the per-user temp root, which the profile does not grant | **refused**, `Operation not permitted` | **succeeded** |
| `yoloai ls` | **refused**, `open ~/.yoloai: operation not permitted` | **succeeded**: every sandbox on the machine, other projects' included |
| `kill -TERM` a process outside the sandbox that it did not start | **succeeded** | — |
| HTTPS to `example.com`; to `1.1.1.1` | 200; 301 | — |
| list `~/.claude/projects` | **succeeded**: every project's Claude transcripts | — |

**Why the tmux route works.** The profile grants `(allow network*)` and
read+write on `/private/tmp`, which is where the tmux socket lives. The tmux
*server* was started outside the boundary, so a command it is asked to run
(`tmux new-window '<cmd>'`) runs outside it too. `Bash(tmux:*)` is on the
Claude allowlist (`permissions.py`), and Goose has no allowlist, so every
engine can reach this route.

**What that means:**

1. **The broker can be bypassed.** A Manager that can run `yoloai` through
   tmux can create a Worker without passing validation. The broker's own
   commit states the design rule: *"the sandboxed process must not be able to
   obtain, BY ASKING, the capability the sandbox removed"*. Through tmux it
   does not even have to ask.
2. **`enclosure.limitations()` overclaims.** It tells the operator on every
   run that other projects on the machine "are NOT reachable — that is what
   this does buy". Through tmux they are reachable. And `~/.claude` is
   readable, which includes other projects' transcripts.
3. **One Manager can kill another**, or any process the operator owns. The
   `(allow signal)` line has no target filter.

⚠ **This is shipped v0.6.0 code, and this plan changes none of it.** It has
been raised as a separate task, with the probes, so the v0.6.0 session that
owns `enclosure.py` can fix it or change what the announcement says. Tracks SB
and MM below say what remains for v0.7.0 whichever happens.

### 0.3 Measured today: the same line leaks both, and loopback confinement closes both

Same method. Only one line of the composed profile was changed, from
`(allow network*)` to `(allow network-outbound (remote ip "localhost:*"))`:

| probe | shipped profile | loopback-only variant |
|---|---|---|
| HTTPS to `example.com` | 200 | **refused**, curl exit 6 (the name lookup is blocked) |
| HTTPS to `1.1.1.1` | 301 | **refused**, curl exit 7 (the connection is blocked) |
| HTTP to a server on `127.0.0.1` | 200 | **200** |
| `tmux list-sessions` on the private socket | listed | **refused**, `Operation not permitted` |
| `kill -TERM` an outside process | succeeded | **succeeded**: signal is a separate line |

**This is why the broker's unfinished half and egress control are one
problem.** `(allow network*)` covers Unix-domain sockets as well as IP, so the
one grant hands a Manager both the outside network and the tmux server that
runs commands outside the boundary. Take the grant away and both close
together.

⚠ **But the variant cannot ship on its own.** It also removes everything a
Manager legitimately reaches off-machine: the ticket backend, a hosted model
API, `git push`, Slack. That is exactly the gap an egress proxy fills (track
EG). **What is measured is the kernel side only.** No engine, `git` or `gh`
has been run through a proxy under this profile.

### 0.4 Measured today: seatbelt cannot name a destination

A profile containing `(allow network-outbound (remote ip "1.1.1.1:443"))` is
**rejected at load**: `sandbox-exec: host must be * or localhost in network
address`, exit 65.

So **no seatbelt profile can express a destination list.** It has two
positions only: everything, or loopback. D-30 already says seatbelt has no
network *isolation*; this is the narrower and more useful fact that it *does*
have loopback confinement and nothing between. Anything finer for a Manager has
to be enforced by something the Manager's traffic passes through, outside the
boundary.

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
- ⚠ **The sandbox does not separate Managers from each other.** Each Manager's
  profile grants the **whole project tree** (`enclosure.compose`). Two
  Managers in one root can each write the other's `.rite/managers/<other>/`.
  The boundary separates *projects*, not the Managers inside one. That is not
  a defect in B9, whose purpose was the operator's home. It does mean the
  profile is **not** what enforces "strictly separated", and nothing should
  cite it as though it were.

### The requirement, as properties — SPEC §5.4.8

The decided requirement is written into `SPEC.md` §5.4.8 as four properties,
each with the thing that would enforce it and its state today. Summarised:

| # | property | enforced by | state on `main` |
|---|---|---|---|
| P1 | No rite command acting as Manager A writes rite state belonging to Manager B | the name-to-path join (§5.4.2), plus a test over the command surface (§5.4.7) at **Manager** granularity | ❌ most per-project state is still flat in `.rite/` (MM1); the §5.4.7 test does not exist |
| P2 | Manager A's processes cannot signal or drive Manager B's | the profile's `signal` target, and the tmux server being out of reach | ❌ **measured open**, part 0.2. `pkill -f goose` from one Manager's "cleanup" kills its siblings, which is the accident this decision is about |
| P3 | State shared by decision is written only through its locked writer, and the list of it is enumerated by a test | §5.4.6 | ⚠ §5.4.6's list still names the outbox, which has since moved per-Manager. No test enumerates the list |
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
| MM5 | **Process separation (P2).** Signal limited to the Manager's own process tree. The tmux server out of reach. Shares its fix with SB1/EG3 | From inside Manager A's boundary: `kill` of B's engine pid refused; `tmux` against the server refused; A's own engine still completes a cycle, and A's Worker still starts through the broker | SB1 | 1 sitting beyond SB1 |
| MM6 | **The idempotence argument, per name.** §9.14.0 paid for amending D-50 with "one Manager per project". The code refuses per name. Write the per-name argument, including what fails closed (D-74) when two *different* Managers are asked for at once | NOT OBSERVABLE, review gate. §9.14.0 marked in place | — | ½ sitting |
| MM7 | **`session_exists` gets its precondition (C12), before anything enumerates the tmux server.** A multi-Manager view is the first caller that asks about a name rite did not itself validate | Moot if C12 lands in 0.6.0. Otherwise `session_exists("eu:west")` refuses rather than answering False | — | ½ sitting |

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

**MMQ2. Slack's configuration: per project, per Manager, or per instance?**
Checked against `1cee54c` (A3b, landed on `main` while this plan was being
written). **As shipped, `slack:` is ONE project-level section of the
committed `config.yaml`**: `command_channel`, `broadcast_channel` and
`owner_user` (`config/models.py`, `SlackConfig`). That differs from the v0.6.0
plan's A6, which said "per-Manager keys under `coordination.manager_roles[]`".
For one Manager there is no difference. **With two Managers in one root, both
read the same command channel**, so one instruction from the Owner reaches
both, and both act on it. That is a cross-Manager accident of exactly the kind
§5.4.8 exists to prevent, and it arrives with the first multi-Manager project.

| option | what it means | turns on |
|---|---|---|
| (a) per-Manager command channel, in the profile | each Manager reads its own conversation | a Slack DM is one per user and app, so two Managers cannot each have "the Owner's DM" without a second app or channel-based commands |
| (b) one command channel, addressed per Manager (`@rite planner …`) | routing by addressing | D-96 makes `@rite` a filter, not authority. Per-Manager addressing is a new rule on top of it and needs its own decision |
| (c) the Owner's user id per instance (MMQ1), channels per profile | Robert's "per-instance config gitignored", applied to who the Owner is on this machine | `owner_user` is committed today, so this is a config migration |

**Also raised now, for v0.6.0 rather than v0.7.0:** A6's text and the shipped
shape disagree. Whichever is intended, the other should be corrected before
v0.6.0 tags. This plan changes neither, because both belong to the Slack
track.

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
6. **Where it runs.** Minting runs in the **supervisor**, outside the Manager
   boundary, before the pane starts. So the supervisor needs Cursor's
   credential too. Today only the engine inside the pane needs its own.

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
  the `tmux -e` argv trap (C6). The stored route needs the profile to grant a
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
| CU4 | **Profile grants and credentials** | A sandboxed Cursor Manager authenticates with no `HOME` redirection, and its credential is absent from tmux's argv (`ps`) | CU1, C6 | 1 sitting |
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
| **Manager** (seatbelt, on the host, B9) | its profile | **everything, or loopback only.** A named host is rejected at load | **measured**, part 0.3 and 0.4 |

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

Part 0.4 rules out a destination list inside the profile. Part 0.3 shows the
profile *can* confine the Manager to loopback. The shape that follows is a
**forward proxy, run by the supervisor outside the boundary**, listening on
loopback. The Manager's profile allows only loopback, and the proxy allows only
the sanctioned destinations. A client that ignores `HTTPS_PROXY` cannot reach
anything, which **fails closed**.

This shape is **inferred from two measurements, and is not a decision.** It is
option (a) of EGQ1. Its side effect is the reason to prefer it: the same
profile line closes the tmux escape (SB1).

### Tickets

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| EG0 | **Measure first: which destinations do real runs reach?** `V070_EGRESS.md` Q5. The v0.5.1 and v0.6.0 acceptance runs, the benchmark, a Slack-connected run, and CU1 for Cursor. Derive the default list from what was observed, as C4's allowlist was derived from 14,981 recorded invocations | A committed data file of observed destinations per engine and role, and a test that requires every default entry to trace to it | — | 1–2 sittings |
| EG1 | **The list is rite's vocabulary** in `config.yaml`: destinations as hosts (plus ports where needed), grouped by what they are for. No proxy or yoloAI syntax (`V070_EGRESS.md` Q2) | `rite doctor` renders the list and validates it with rite's own error wording. A proxy- or yoloAI-shaped key is refused | EGQ3 | 1 sitting |
| EG2 | **Workers: enforce where the backend can, and say where it cannot.** Docker: pass the list as `--network-allow`. Seatbelt: rite states at start that Worker egress is **not controlled** on this backend, every run. It does not say "restricted" | A docker Worker is refused a destination off the list, and the refusal names it; a seatbelt project's start line says egress is uncontrolled | EG1, EGQ2 | 1–2 sittings |
| EG3 | **Manager enforcement** per EGQ1. If (a): profile to loopback, proxy in the supervisor, `HTTPS_PROXY` in the engine's environment. **Also closes SB1** | A real Claude Manager and a real Goose Manager each complete a cycle, including a ticket read and a `git push` to the sanctioned remote. A request to an unlisted host is refused **and reported** (EG4). `tmux` from inside is refused | EG0, EG1, EGQ1 | 3–4 sittings |
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

**What remains is that the boundary the broker sits behind is not closed.**
Each item cross-references the track that closes it.

| # | what remains | evidence | closed by |
|---|---|---|---|
| SB1 | **The tmux server runs commands outside the boundary**, so the broker can be bypassed (`yoloai` via `tmux new-window`) | measured, part 0.2 | the v0.6.0 task if it fixes it narrowly. Otherwise **EG3**: the loopback profile refuses the socket, measured in part 0.3 |
| SB2 | **`(allow network*)`**: the whole network, and every Unix socket | the profile's own text. Measured, part 0.3 | **EG3**. Same line as SB1, which is why the two tracks are one problem |
| SB3 | **Unfiltered `(allow signal)`**: kill any process the operator owns | measured, part 0.2 and 0.3 (the loopback variant does not change it) | **MM5** |
| SB4 | **`~/.claude` is readable whole**, which includes every project's transcripts | measured, part 0.2 | a narrower grant. ⚠ Not measured: which parts of `~/.claude` a Claude Manager needs. Measure before narrowing, or the login breaks the way `HOME` redirection broke it (B4d correction) |
| SB5 | **`(allow mach-lookup)` with no filter.** Claude Code on macOS keeps its login in the keychain, and `4ebbbd7` measured that the login is found inside the boundary, so at least that item is reachable. That is an inference, not a direct keychain probe. Not measured: whether a Manager can read **other projects'** rite credentials from it. The Worker-profile measurement (keychain content denied) does not carry over, because this profile differs | profile text | a measurement first. If reachable, per-service `mach-lookup` filtering, measured against the login |
| SB6 | **`limitations()` overclaims** (part 0.2, point 2) | measured | the v0.6.0 task. If it ships unchanged, v0.7.0 corrects it before anything else in SB |
| SB7 | **Which Manager may ask for which Worker.** A request names a declared Worker and a ticket. With several Managers in one root, any Manager can ask for any declared Worker. Whether Workers belong to a Manager is not decided (SBQ1) | `broker.py` validates against project-level declarations | MM, after SBQ1 |

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

### Becomes 0.7.0 if it does not land in 0.6.0

The v0.6.0 plan's "can slip to v0.7.0" list. **Check each against the 0.6.0 tag
before planning around it.**

| item | v0.6.0 id |
|---|---|
| Wire `harness.run_subtask` to Goose; prove on the benchmark | B4, B5. B5 has already moved to the Worker tier, which is **scheduled in no release** (below) |
| The Manager's reply path hand-writes JSON | C5 |
| Designation membership check. **Cursor raises the stakes again**: its handle comes from the engine, like Claude's | C8 |
| Journal provenance (author field) | C10 |
| `.rite/user/` separation is incidental | C11. MMQ1(b) makes it worse |
| `session_exists` precondition | C12 = MM7 |
| `journal.instructions()` spells out `--manager` | C13 |
| Outbox retention policy, **undecided** | C23 |
| A check-in window with no Manager running, **Robert to confirm** | K6 |
| Whether §6.6's normalisation extends to Slack text, **Robert to confirm** | the SPEC records it as an inference |
| Where N1/N2 land, **Robert to confirm** | the v0.6.0 plan's part N |

### Deferred without a release, and at risk of evaporating

| item | from | why it is listed |
|---|---|---|
| **The Worker tier**: decompose duty (RL-T7), `harness`/`runners` gaining a caller, B5 | v0.6.0 plan, B4b reshaping | "out of v0.6.0" with no destination. Needs a release named |
| **Re-advertising `--record-issues`** once the journal is redacted | §9.15 (reversed for 0.5.1); C7 | if C7 lands, re-advertising is a decision nobody has been asked for |
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
| S5 | `enclosure.limitations()` | "other projects … NOT reachable" | **Not changed**: v0.6.0 code. Raised as a separate task |
| S6 | `CHANGELOG.md` Unreleased | "A Manager remains **unsandboxed**" in the allowlist entry, now false after `4ebbbd7` | **Not changed**: the release notes belong to the 0.6.0 session. Reported |
| S7 | `V060_RELEASE_PLAN.md` B9 row | "MEASURED NOT POSSIBLE as specified", with no note that the broker shape was built | **Not changed**: the active release's plan. Reported |
| S8 | `spikes/B9-manager-sandboxing.md` | its `HOME` section still says to point `HOME` at the sandbox, the advice `4ebbbd7` corrected in B4d. No banner saying the broker was built | **Corrected**: banner added |
| S9 | `spikes/B4d-…md` section 3 | "Managers do NOT run sandboxed" | **Corrected**: banner line |
| S10 | `spikes/A3a-slack-transport.md`, `A3b-…md` | "the proof is NOT made". The plan records A3a done (0.24 s) | **Corrected**: banner added |
| S11 | `spikes/RL-T0-agent-comparison.md` section 6 | `GOOSE_MODE=auto` "not directly probed"; B4d probed it | **Corrected**: pointer added |
| S12 | `V070_MEMORY.md`, `V080_RELAY_CHANNELS.md` status paragraphs | say the other notes are in gitignored `.docs/` | **Corrected** |
| S13 | `V070_MULTI_MANAGER.md` "Status of the file itself" | says it does not reach a fresh clone | **Corrected** |
| S14 | `carried-limitations-register.md` D1 | "no `RITE_PROJECT_ROOT` env var exists". It does (`cli/main.py`, `PROJECT_ROOT_ENV`) | **Not changed**: the register's rule is that only a named run clears an entry. Reported |
| S15 | `V070_EGRESS.md` | open question 1's premise (no Manager sandbox) | **Corrected**: banner pointing at §5.5 and this plan |
| S16 | `V060_RELEASE_PLAN.md` A6 vs `1cee54c` | A6 says per-Manager keys under `manager_roles[]`. What shipped is one project-level `slack:` section | **Not changed**: the Slack track's. Reported, and MMQ2 |

---

## Decisions needed — consolidated

| id | question | blocks |
|---|---|---|
| MMQ1 | where per-instance configuration lives | MM4, EGQ3 |
| MMQ2 | Slack configuration per project, per Manager, or per instance. As shipped, all Managers in a root read one command channel | any multi-Manager project using Slack |
| MMQ3 | a per-Manager worker cap | — |
| MMQ4 | correlated failure: detect, or document | — |
| MMQ5 | combined check-ins across Managers | — |
| CUQ1 | Cursor for Workers | — |
| CUQ2 | Cursor's credential route | CU4 |
| CUQ3 | if `-p` takes argv only | CU2, after CU1 |
| EGQ1 | how the Manager's egress is enforced | EG3, and SB1/SB2 if the v0.6.0 fix is narrow |
| EGQ2 | seatbelt projects and a configured list | EG2 |
| EGQ3 | the list: committed, or per-instance | EG1 |
| EGQ4 | redirects/DNS under iptables | EG2's documentation |
| EGQ5 | which allowed destinations publish | EG5 |
| SBQ1 | do Workers belong to a Manager | SB7 |
| MEQ1 | where memory sits relative to Robert's test and K3 | memory's ask-time path |
| `V070_MEMORY.md` Q1–Q7 | memory's architecture | any memory spec |
| — | the Worker tier's release | B5, harness |

## Sequencing, and where the release can be cut

**Measurements first, because four of the tracks turn on them:** CU1, EG0,
ME0–ME3, SB4/SB5's two measurements. None needs a decision, and all can run
while the decisions above are asked.

**Then** MM1 → MM2, MM3 (independent of every decision except MMQ1 for MM4);
**then** EG1 → EG3 → EG4, which closes SB1/SB2 and enables MM5; **then** CU2 →
CU6; EG2, EG5, EG6 as their questions land.

**The coherent minimum:** MM1–MM3, MM5, EG0, EG1, EG3, EG4. That is "several
Managers in one root cannot disturb each other by accident, and a fooled
Manager cannot send anything off-list". Cursor and memory can slip whole
without leaving anything half-built. **Egress cannot ship without EG4**: a
silent network refusal is the stall-without-a-message class again.

**Not sized as a total, on purpose.** The v0.6.0 plan's note on units applies:
sittings are ordinal, not a schedule.
