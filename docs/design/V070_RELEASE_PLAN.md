# v0.7.0 — release plan, and the specs it needs

**Status: PLAN, 2026-09-25; RE-FILED 2026-09-26 to Robert's new scope
(below).** Written while v0.6.0 was still being built, for Robert to decide
from. Two review rounds follow. *"Nothing in it is built" was true when
written. Since then MM5, MM7, SB4 and the MMQ2 decision have landed, and each
row says so.*

**Citation convention.** A bare `§` is a section of `SPEC.md`. This plan's own
parts are called "track MM", "part 0" and so on, never `§`, because the
citation gate's regex is context-free. `DEFECT_CLASSES.md` classes are written
"class N".

⚠ **SUPERSEDED 2026-09-26 by the re-filing below. Kept as the scope this plan
was first written to.** *Scope, as Robert set it.* v0.7.0 carries
**multi-Manager**, **Cursor**, **memory** and **egress control**, plus the
scenario gate that Decision 5 of the v0.6.0 plan moved here (§7.3, D-81). The
relay and self-reflection are v0.8.0 (`V080_RELAY_CHANNELS.md`). This plan
adds nothing to that scope.

## Scope, re-filed 2026-09-26: v0.7.0 is "feature-complete single-machine"

**Robert's shape for the next three releases:**

- **v0.6.0** ships now: Slack as a control channel, check-ins, several
  Managers on one machine in one root, the Manager sandbox on macOS and
  Linux, credentials, text handling (the v0.6.0 plan's part N), a local
  Goose Manager, and Linux support.
- **v0.7.0, "feature-complete single-machine"**, this plan: the **Cursor
  adapter**, plus **fixes from the first 24 hours of Robert's v0.6.0 dogfood
  test**. Proposed to him and taken as accepted: the **publishing
  strategies minus `push_to_shared`** (`commit`, `push`, `pull_request`),
  **channel separation** (RP1), the **per-Manager directory move** that
  removes the Linux top-level-file limitation (readiness D17), and the
  **`/tmp` grant fix** (`b542c15` on `fix/landlock-no-wholesale-temp`).
- **v0.8.0, "feature-complete multi-machine"**: the Remote-Worker,
  multi-machine hardening, the credential broker, egress confinement, and
  `push_to_shared`. Its plan is **`V080_RELEASE_PLAN.md`**, and tracks MX
  and EG moved there verbatim.

**The rule used to file everything else:** an item that one machine needs to
be complete is v0.7.0; an item that only arises across machines is v0.8.0.
Egress is v0.8.0 because Robert named it, not by that rule. Anything the
rule does not place is listed under "Fits neither release", not forced.

### Filed in v0.7.0

| item | where it is | why v0.7.0 |
|---|---|---|
| **CU1–CU6**, CUQ1–CUQ3 | track CU | named by Robert (Cursor) |
| **PB1**: `commit`, `push`, `pull_request`, `squash`, `auto_merge`, the per-module override | track PB | named. `push_to_shared` is split off as PB2 into v0.8.0 |
| **RP1**, and C32's decision, which waits on it | track RP | named ("channel separation") |
| **MM8**, the per-Manager directory out of the project tree | track MM (new row, from readiness D17) | named. It removes D17 on Linux |
| **SB11**, stop granting `/tmp` on Linux | track SB (new row, from `b542c15`) | named |
| **Dogfood fixes** | "Fixes from the v0.6.0 dogfood test", below | named. Not known yet: the test has not run |
| **MM1** (flat state per Manager), **MM2** (the §5.4.7 test), **MM3** (claims carry the Manager), **MM6** (idempotence per name) | track MM | §5.4.8's P1, P3 and P4 between Managers that share one root, which v0.6.0 ships without (SPEC §5.4.8). One machine |
| **MMQ3** (per-Manager worker cap), **MMQ4** (correlated failure), **MMQ5** (two standups) | track MM | several Managers on one machine |
| `V070_MULTI_MANAGER.md` Q3 and Q4 | track MM | Q4 is literally "Managers on one host" |
| Private channels bound to a project | track MM, MMQ2; SPEC §9.16.6 | already "a v0.7.0 convenience", and it is Slack on one project, not multi-machine |
| **SB5** (keychain via `mach-lookup`), **SB7**/SBQ1 (which Manager may ask for which Worker), **SB8** (`/tmp` on macOS), **SB9** (Linux login replaceable), **SB10** (planted settings, readiness W11) | track SB | the Manager's boundary on one machine. SB9 is also promised for 0.7.0 in the CHANGELOG |
| **C24**, **C25** | track SB, decisions | the Manager's boundary on one machine |
| The scenario gate (§7.3, D-81) | carried index | ⚠ **kept here on the strength of the v0.6.0 plan's Decision 5, which nothing has revoked.** Robert's new list does not name it. **Robert to confirm** |
| `--record-issues` re-advertising; K6's confirmation; pinning Goose; a derived Modelfile | carried index, decisions | single-machine, and each is a decision or a sentence, not a feature |
| Jira for a sandboxed Manager (readiness Q3) | decisions | one machine. ⚠ Its third option, "the broker", is now v0.8.0 |
| v0.6.0 remainders that are not on the tag's path: B7's idle half, C1's `$TMUX` half, C3's sibling recorders, C14, C15, C31, C33, C34, readiness W9, W13 and W14 | carried index | ⚠ **v0.7.0 unless they land before the tag.** Each is open in its own row |

### Filed in v0.8.0 (`V080_RELEASE_PLAN.md`)

MX1 and MX2 (MX-P1 to MX-P4), CB1 (the credential broker), EG0–EG6 and
EGQ1–EGQ5, SB2 (closed by EG3; its row stays in track SB), PB2
(`push_to_shared` and the three `shared_repo` rules), MM4 and MMQ1
(per-instance configuration: a judgement, reasons given there), and LS1
(Slack gated on the Owner lease). The v0.8.0 plan gives each one's reason.

### Fits neither release

| item | where it is | why neither |
|---|---|---|
| **Memory**: ME0–ME3, MEQ1, `V070_MEMORY.md` Q1–Q7 | track ME | it was v0.7.0 scope, and Robert's v0.7.0 list does not name it. It is a new capability, not single-machine completeness, and not multi-machine. **Unscheduled; Robert to place** |
| **The relay's escalation chain and self-reflection** | `V080_RELAY_CHANNELS.md` | they were "0.8.0", and v0.8.0 is now multi-machine. The Slack transport itself shipped in v0.6.0. **Unscheduled.** The note's file name still says 0.8.0 |
| **The Worker tier**: the decompose duty (RL-T7), `harness`/`runners` gaining a caller, B4 and B5 | carried index | already "scheduled in no release" |
| **A Linux Worker sandbox** (readiness D18) | readiness D18; track MX open question 3 | single-machine by the rule, but whether a documented limitation is enough is Robert's open call on D18, and it is a design problem (Docker's group is root-equivalent). v0.8.0's MX1 cannot ship without it, so it is due by v0.8.0 at the latest |
| The full ten-task benchmark; opencode's `--format json` defects; Finding B; whether a manifest-created Slack app is "internal customer-built" | carried index | measurements and questions, not release work |

### Fixes from the v0.6.0 dogfood test

**Empty until the test runs.** Robert's first 24 hours of v0.6.0 dogfooding
decide this list, so nothing is guessed into it. Each fix lands here as a
row, with what was observed and on which platform.

## Where the tracks below stand after the re-filing

Tracks MM, RP, PB, CU and SB are v0.7.0, except the rows the tables above
send to v0.8.0. Track ME is unscheduled. **Tracks MX and EG are in
`V080_RELEASE_PLAN.md`.** Part 0 stays here, and the v0.8.0 plan cites it.

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
| list `~/.claude/projects`; read the first bytes of **another** project's transcript | **42 directories listed; the other project's transcript read.** ⚠ Open when measured (SB4). *Closed since: `~/.claude` is not granted any more (`8a61989`)* |

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

**Re-measured at `afa41e9`, 2026-09-26, after `7ae2ecc` changed the profile a
third time** (read-only grants for the running rite's environment, C27). The
same probes, the same method, and the same results: both tmux routes and the
direct ungranted write refused, the outside process and `beta`'s engine
survived, `beta` still killed its own child, HTTPS 200/301, `alpha` wrote
into `beta`'s directory, and **44** projects' transcript directories were
listed with another project's transcript read. SB4 is unchanged.

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

⚠ **SCOPE MOVED 2026-09-26 (Robert): two Managers on one machine are
v0.6.0, delivered Sunday.** The shape is a **Claude Manager as Owner plus a
local secondary**, in one root, and another session is building it. **Several
machines stay out of v0.6.0.** What that does to this track:

- **The separation requirement (SPEC §5.4.8) is now a v0.6.0 requirement.**
  Its state table is what v0.6.0 ships with unless that work changes it: P2
  holds, and P1, P3 and P4 do not. MM1–MM3 and MM5 are the tickets that would
  change it. **Whether they land for Sunday is the building session's call,
  and the release notes must say which properties hold**, or v0.6.0 sells
  separation it lacks. *Where it stands (SPEC §5.4.8, 2026-09-26): MM5
  landed, so P2 is pinned between two Managers, on Linux for the signal half
  only. MM-2 made P1 hold for the per-Manager directories and every inbox,
  not for flat state. MM1–MM3 did not land for v0.6.0 and are v0.7.0.*
- **MMQ2 (Slack: both Managers act on one instruction) and MMQ5 (two
  standups per window) are due in v0.6.0.** Both are behaviour today, not
  designs (SPEC §9.16.7). *MMQ2 is DECIDED (Robert, option (c)) and BUILT:
  only the Owner hears Slack (`e18139d`, SPEC §9.16.7). MMQ5 ships as built
  and its question is v0.7.0's.*
- **Unchanged for v0.7.0:** several machines, MMQ3 (a per-Manager worker
  cap), MMQ4 (correlated failure), and MM4 once MMQ1 is answered. *Re-filed
  2026-09-26: several machines, MM4 and MMQ1 are v0.8.0
  (`V080_RELEASE_PLAN.md`). MMQ3 and MMQ4 stay v0.7.0.*

This plan does not write the v0.6.0 tickets for that shape. They belong to
the session building it, and inventing them here would be the speculative
spec this plan's bar rules out.

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
  check-in queue (K2, built as `3a4fa1f`). The module says existing `.rite/` files "stay where they
  are until 0.6.0". ⚠ **No v0.6.0 ticket moves them**, so that sentence points
  at a release that will not do it. Carried here as MM1. *Since `935ceef`
  and `965b206` the mailbox is no longer here: it lives outside the project
  at `~/.rite/managers/<checkout>/<name>/mail/` (SPEC §5.4.8).*
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
| P1 | No rite command acting as Manager A writes rite state belonging to Manager B | the name-to-path join (§5.4.2), plus a test over the command surface (§5.4.7) at **Manager** granularity | ⚠ **partly, since MM-2**: each profile refuses writes into another Manager's directory and every inbox (SPEC §5.4.8). ❌ most per-project state is still flat in `.rite/` (MM1); the §5.4.7 test does not exist (MM2) |
| P2 | Manager A's processes cannot signal or drive Manager B's | the profile's `signal` target, and the tmux server being out of reach | ✅ **holds on `main`**, measured between two Managers' profiles on `9862b59` (part 0.2). A "cleanup" `pkill -f goose` in one Manager cannot take down its siblings. *Pinned by a two-Manager test since MM5 (`c469a24` macOS; `4bf393f` Linux, the signal half only, because the tmux route is open on Linux: readiness D2)* |
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
| ~~MM4~~ | **Re-filed to v0.8.0** (`V080_RELEASE_PLAN.md`, with MMQ1), 2026-09-26. Kept so the id is not reused | — | — | — |
| ~~MM5~~ | **Done: P2 is pinned between two Managers on both platforms** (`c469a24` macOS, six attacks plus an own-child control; `4bf393f` Linux, the signal half). Kept so the id is not reused | — | — | — |
| MM6 | **The idempotence argument, per name.** §9.14.0 paid for amending D-50 with "one Manager per project". The code refuses per name. Write the per-name argument, including what fails closed (D-74) when two *different* Managers are asked for at once | NOT OBSERVABLE, review gate. §9.14.0 marked in place | — | ½ sitting |
| ~~MM7~~ | **Moot: C12 landed as `0136447`**, "Make session_exists exact for any name, not only rite's". Kept so the id is not reused | — | — | — |
| MM8 | **Move the whole per-Manager directory out of the project tree** (v0.7.0 by Robert's scope, 2026-09-26). Recorded in the v0.6.0 readiness list, D17, which is the source of every clause here: on Linux, Landlock still enumerates the project root to keep one Manager out of another's `.rite/managers/<name>/` (its `routes/` carry the Owner's authority), so a Linux Manager cannot create a new top-level file or directory during a cycle. Moving the directory out lets the project be granted as one tree. The mailbox already moved (`935ceef`) | *Proposed, since D17 records no done-when:* D17's Linux cell stops being a limitation: a Linux Manager creates a new top-level file in its project during a cycle, and one Manager still cannot write another's routes | ⚠ **MM1 moves flat state INTO `.rite/managers/<name>/`, and this moves that directory OUT.** Designed together, or one of them moves the same state twice | about 3.5–4 sittings (D17), with the risk in migrating check-in queues and Slack thread positions, and a decision on whether the journal, meant to be committed, leaves the tree |

### Open questions — Robert's

**MMQ2. Several Managers, one Owner's DM: who acts on an instruction?**
✅ **DECIDED 2026-09-26 (Robert): option (c), and BUILT.** The Owner is the
one Manager holding `route`, and only its `rite start` opens a Slack relay
(`e18139d`, landed as `586a47b`; SPEC §9.16.7, 0.24.12). Observed: a
secondary went from 3 Slack connections at start to 0. The text below is the
question as it was asked. **Still open from it:** the private-channel
convenience (v0.7.0), and Slack gated on the Owner lease when there is a
remote (LS1, v0.8.0).

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
DM is therefore not split between them, and both receive all of it. (Their
posts are told apart by the `*<name>*:` prefix A4 puts on each one.) Both
acting on one instruction is a cross-Manager accident of exactly the kind
§5.4.8 exists to prevent. It also doubles the poll on the project's one app
(§9.16.6's table), and it is now written into SPEC §9.16.7.

⚠ **Due in v0.6.0, not v0.7.0** *(and answered in v0.6.0: see the
decision at the top of this question)*. Robert moved two Managers on one machine
into v0.6.0 on 2026-09-26, so the first project to run a Claude Owner and a
local secondary with Slack enabled meets this. (The first version of this question described a shipped
`command_channel`. That shape has since been replaced, and the question is
rewritten against what is built.)

| option | what it means | turns on |
|---|---|---|
| (a) one app per Manager | each Manager has its own DM with the Owner | every user already creates their own app (A3b). This multiplies it per Manager, and every app is its own setup |
| (b) one DM, addressed per Manager | an instruction names the Manager it is for, and a Manager treats an unaddressed DM message as context | D-96 makes `@rite` a filter, not authority, and per-Manager addressing is a new rule on top of it. It also needs a default: one Manager, or none |
| (c) one Manager per project reads the DM | the others receive nothing from Slack | simplest, and it makes "which Manager is the Slack one" a profile key |

**Across projects, settled 2026-09-25: one Slack app per project (D-101,
SPEC §9.16.6).** Two projects on one app had the same DM accident as two
Managers do here, plus a shared rate bucket, and one app per project removes
both. **That arithmetic bears on this question, because it applies between
Managers too.** Every running relay reads history 30 times a minute on its
project's app: two Managers are 60 against Tier 3's "50+", and three are 90.
So an option that keeps several relays polling one app is bounded by the
multiplication, not only by the authority question.
- **(a)** is D-101's move applied one level down.
- **(c)** keeps the poll at 30 a minute.
- **(b)**, with every Manager still reading, does not.

**Private channels bound to a project, Robert's design: a v0.7.0
CONVENIENCE, no longer the binding.** A user creates a private channel,
invites the app, and names the project it belongs to. There can be one per
person, or a shared team channel.
- **What it buys:** authority stays structural (membership of the channel,
  managed by Slack), and it is auditable (a visible member list, where a DM
  has none).
- ⚠ **What it changes:** the authority rule becomes "members of this bound
  channel", not "the Owner in the Owner's DM". **A person added to the
  channel silently gains command authority**, and nothing in rite records
  that it happened.
- **Open: cap or rotation.** Each bound channel polled every tick is 30 a
  minute on the project's one app, so two are 60 and three are over.
  Rotation keeps it at 30 and makes latency grow with the number of
  channels.
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
per Manager is the 0.6.0 shape, and a combined standup "is not planned". K5
as built (`401e27f`) posts each check-in to the Owner's DM. ⚠ **With two
Managers in v0.6.0 this is due now:** the Owner receives two standups per
window in one DM, each rooting its own answer thread. That works, because a
thread reply reaches only the relay that posted its root (§9.16.7), but
nobody has decided it is what they want. Keep, merge, or make it the
Owner's job. Three windows a day with two Managers is six digests.

**Also still open, from `V070_MULTI_MANAGER.md`:** what `local:<class>`
classes are (Q3: they must come from `engine_probe`, not config), and that
Managers on one host share the keychain, the tmux server, the yoloAI namespace
and `~/.rite` (Q4). Part 0.2 turns the tmux-server half of Q4 from a note into
a measured escape.

---

## Track RP — Reporting: what needs action, apart from what is reading

**Robert, 2026-09-26, from using rite's own reporting all weekend:**

> "In rite we really need to separate the destination for prose like this
> and for check-ins and status updates. Not saying the prose is useless, it
> just makes it more difficult to figure out the actionable steps."

**Recorded, not designed.** Not in v0.6.0. **v0.7.0** (Robert's scope,
2026-09-26: "channel separation").

### What rite produces today, read from `main` at `8897e40`

- **The check-in message** (`checkins.py:634`) is one outbox message, and
  so one Slack post. In order: the header, then the standup
  (`standup.digest`: "Observed by rite", "Stated by the Manager — rite did
  not verify these", reported injection phrases), then the deferred-question
  counts, then withdrawn questions. Only then comes "Questions held for this
  check-in". **The part that needs Robert comes last**, after the narrative.
- **A Manager's `rite reply`** is free text, posted to the Owner's DM as
  written (the Slack relay only redacts it). A blocking question and a
  paragraph explaining how something was measured arrive in the same
  channel, with the same visual weight and no marker.
- **The check-in mirror** copies the whole check-in to the channel, so the
  mix is duplicated, not separated.
- The standup's split is **evidence vs claim** ("observed" vs "stated"), and
  that split is right. It is not the split asked for here, which is
  **needs-action vs reading**. Nothing in rite marks a line as "needs you".

Robert's reporting format (bullets, a marker for what needs him, nothing
between scheduled reports) is not recorded in this repository; it is cited
here as relayed.

### Ticket

| # | work | done when | depends | size |
|---|---|---|---|---|
| RP1 | **Two destinations: what needs action, apart from what is reading.** Decisions needed, blockers and questions awaiting an answer go where Robert can scan them. Narrative, reasoning and measurement detail go somewhere retrievable and out of the way. It covers the check-in message, the standup, `rite reply` and the Slack relay, which are one stream today. Open for design: where each destination is (a DM vs a thread vs a channel vs a file); how a line is classed, since today nothing carries a "needs you" marker and a Manager's free text is not structured; and whether "nothing between scheduled reports" is a rule the relay enforces | Robert reads one check-in and one day of Slack from a real run and can list what needs him from the action destination alone, without opening the other. The narrative is still retrievable. His words are the test, not a format check | — | design first; unsized |

### How it relates to C31 and C32 (the v0.6.0 plan)

- **C32 (a Manager volunteers standup notes): partly subsumed.** C32's cost
  is that "Stated by the Manager" fills by default and crowds the standup.
  With RP1, those notes go to the reading destination, so they stop
  competing with what needs action, and most of that cost goes away. **What
  RP1 does not answer** is C32's own decision: whether the check-in should
  invite notes at all, and why the volunteering follows the model (1 of 6
  inside the sandbox on `claude-sonnet-5`, 3 of 3 outside it on Opus). So
  C32 stays open as a smaller question, and it should be decided after RP1's
  design, not before.
- **C31 (an anchor checked for presence, not support): linked, not
  subsumed.** A note anchored to a line that does not support it is wrong in
  either destination. RP1 makes it matter less for scanning, and C31 is
  still what makes it trustworthy. The common root the three share: **the
  channel does not distinguish what needs action from what needs reading,
  and it does not distinguish evidence from claim at the line level.** RP1 is
  the first half of that; C31 is the second.

## Track PB — Publishing: what happens to a finished task

**Robert's input, 2026-09-26 (relayed; his own words quoted where given):**
- Workers should not push at all. The Manager decides what to do with a
  finished task's result.
- Three strategies: (1) don't push; (2) push and merge to main or a feature
  branch; (3) push and create a PR.
- The Owner needs to know whether to merge PRs once they pass all checks,
  or leave that to the User.
- Some teams want fully hands-off, some want to review every line, and some
  developers need to do every publishing step themselves. On that last
  case, in his words:

  > "In my 'don't advertise rite' case I don't mean that it shouldn't be
  > visible anywhere if someone looks deep. It's just so the developer can
  > squash commits manually, amend the commits etc. before it's ever pushed
  > out. The team that inspects every line of code may be upset if someone
  > pushes AI generated code at them without even looking at it themself."

  *An earlier version of this entry read that case as concealment and
  constrained commit authorship, trailers and generated-by markers. That
  was wrong and is struck. rite's involvement being discoverable is fine.*
- **Load-bearing:** under "don't push" the work must still be COMMITTED to
  the local project repository, or the state is lost once a Worker starts
  a new task. **"Don't push" must never mean "don't commit."**
- > "And by 'the manager' I mean broadly — the LLM part and the automated
  > rite part."
- On squashing:

  > "I think strategy 1 should have auto-squash opt-in. Reasoning — if the
  > code produced is of good quality most of the time and the User wants to
  > review and amend just 1 commit instead of many, then let's make it easy
  > for them."

## The design, settled by Robert on 2026-09-26 (final; it replaces every earlier shape in this track, including a `remote` axis)

```yaml
# .rite/config.yaml (project)
publish:
  strategy: pull_request   # commit | push | pull_request | push_to_shared
  squash: false
  # auto_merge: false      # opt-in, on top of pull_request only

# .rite/modules.yaml (per module; every key optional)
modules:
  <name>:
    publish:
      strategy: push_to_shared     # optional override of the project's strategy
      shared_repo: git@…           # required when the EFFECTIVE strategy is push_to_shared; no default
```

- **`shared_repo` is per MODULE, not per project** (Robert: "because a
  project can have multiple packages", then "rite module"). The term is
  rite's own, **module**, in config and docs. No new concept: a rite module
  already is one git repository with its own `url` (its origin), cloned per
  Worker as `workers/<w>/<module>`. So a shared remote belongs beside that
  `url`, and rule 3 compares a module's `shared_repo` with THAT module's
  origin.

- **`strategy` values are verbs, named rather than numbered.** A config
  saying `strategy: 2` tells a reader nothing, and names let a value be
  added without disturbing an order. This track's history below says
  "strategy 1/2/3": 1 = `commit`, 2 = `push`, 3 = `pull_request`. Robert's
  own quotes keep the numbers he used.
- **`pull_request` is the default** (Robert's decision). With auto-merge
  off it is strictly safer than `push`: the work leaves the machine and is
  visible, but nothing lands on a branch anyone depends on, and the
  reviewer decides. The default matters because a team that inspects every
  line is precisely the team that will not have changed it.
- **`push_to_shared` is a fourth strategy value, not a separate `remote`
  axis.** Robert rejected the axis, rightly: `commit` + `remote: shared` is
  meaningless, so two of six combinations would have been invalid. A fourth
  value makes invalid states unrepresentable instead of needing validation
  to reject nonsense. **Accepted cost:** `pull_request` against a shared
  repository cannot be expressed. That is deliberate: a PR on a repository
  that is not the client's would be reviewed by nobody in particular. If it
  turns out to be needed, it is a fifth value, not a redesign.
- **`squash` is orthogonal**, default off (below).
- **Auto-merge is an opt-in flag on top of `pull_request`**, off by
  default, under the green-matched-to-SHA rule below. Robert checked this
  specifically: opening a PR is safe, merging it is the risk.
- **All values are implemented** (Robert: not much code). ⚠ **Each one must
  be OBSERVED before it is called done**, because every defect that mattered
  this weekend was in a mechanism that existed and had never been run.
  Auto-merge is gated hardest.

### Settled by Robert: `strategy` per project, with an optional per-module override

The project's `strategy` applies to every module unless that module's
`publish.strategy` overrides it, resolved key by key like `commands`. A
task touching several modules is published per module under each module's
EFFECTIVE strategy, and its completion report lists each module's outcome.

- **Resolution is explicit and inspectable.** `rite doctor` (or an
  equivalent) prints the EFFECTIVE strategy per module and where it came
  from ("project default" or "module override"), plus `shared_repo` when it
  applies. An override that silently does not apply is the failure this
  design exists to prevent. PB1 is not done until that line is observed
  changing when an override is added and removed.
- **The Owner resolves per module; it does not "know one strategy".** The
  multi-Manager routing work (Track MM) must read the EFFECTIVE value per
  module, never the project default. A secondary's completion report
  carries the per-module outcome, and the Owner's merge decision (per PR,
  so per module repository) uses that module's effective strategy and
  `auto_merge`.

The code-side view it was decided on:

**Code-side view, from `main`:**
- **Keyed per module today** (`config/models.py` `Module`, `modules.yaml`):
  `path`, `url` (the module's own origin), `branch`, `description`, and
  `commands`. A Worker's manifest picks a subset of modules.
- **An override pattern already exists and fits.** `RecordedCommands`: "a
  recorded command wins over the detected one for its own key only; an
  unrecorded key falls back". A per-module `publish:` block that overrides
  the project's keys one by one is the same shape. It would follow that
  pattern, not fight it.
- **Publishing is per repository anyway.** A push or a PR happens per module
  repository, so rite applies a strategy per module at publish time whether
  or not the config says so.
- **Routing reads no module configuration** (`managers/routing.py` never
  names modules). A ticket names a module only through an OPTIONAL
  `module:<name>` label, at most one, which only the distribution refusal
  reads (`coordination/refusal.py`, marked there as a proposal).

**What a per-module strategy costs.** Resolution itself is cheap. It is
rite's deterministic part, at publish time, per module repository, where
the push or PR already happens. The Owner's merge decision is per PR, so
per module repository, and needs no project-wide answer. **Not cheap is
one task touching two modules with different strategies**, for example one
committed locally and one opened as a PR. Nothing maps a finished task to
its modules today except the diffs themselves: which module repositories
gained commits. A secondary's completion report would then have to carry a
per-module outcome, and the routing design would have to read module
configuration it does not read today.

*(Recommendation made, and adopted by Robert as above.)*

### 🔴 Three rules on `shared_repo`: part of the design, not implementation detail

1. **No default, ever.** Not `origin`, not a derived name. rite's users
   include on-premise clients for whom code leaving their infrastructure is
   the thing they are paying to avoid. A helpful guess harms exactly them.
2. **Refuse at start, with the reason,** when a module's EFFECTIVE
   strategy is `push_to_shared` and its `shared_repo` is not set. The same shape as the missing
   `claude_token` refusal: it names the fix and the module.
3. **Refuse when `shared_repo` resolves to the same remote as `origin`.**
   Someone will configure that by accident eventually, and it would
   silently INVERT the strategy's whole purpose: "never touch the client's
   repository" becomes "always touch it", with nothing saying so.
   **Resolve and compare; do not string-match.** `git@host:x/y.git` and
   `https://host/x/y.git` are the same remote, and so are forms differing
   in `.git`, a trailing slash, letter case or `ssh://`. *This is the rule
   most likely to be dropped as over-engineering by someone who does not
   see what it prevents. What it prevents is the one outcome this strategy
   exists to rule out.*

Not in v0.6.0. **v0.8.0**, with `push_to_shared` (PB2, `V080_RELEASE_PLAN.md`).

### Today, checked for data loss first: NOT a v0.6.0 defect (measured)

**Measured 2026-09-26 on a real seatbelt sandbox, on `main` at `d7e27f8`.**
A scratch project was used, with a module whose origin is a real (local
bare) repository. The Worker was registered with `rite add worker`, and its
sandbox was created as rite creates it (same name, `:copy-all`, seatbelt
backend) with yoloAI's `idle` agent.
1. **The finished task's work, uncommitted, was left in the copy:**
   `M app.txt`, `?? new.txt`.
2. **Next task, by the broker's own command** (`rite sandbox start w
   --ticket T2`): refused, because yoloAI says the sandbox already exists.
   rite's hint was "`rite sandbox destroy <worker>`, then start it again".
   The work was untouched.
3. **`rite sandbox destroy w`: refused.** rite named the files ("svc @ main:
   2 uncommitted (M app.txt, ?? new.txt)") and said where the copy is.
4. **`yoloai destroy` directly, bypassing rite: refused** ("1 sandbox(es)
   have unapplied changes"). The work was still there.
5. **The host checkout `workers/w/svc` holds none of it.** The work exists
   ONLY in the sandbox copy.

So nothing in v0.6.0 discards it. The code agrees: the broker only
starts; nothing automatic destroys, stops or removes; destroy checks
uncommitted AND unpushed (`unsaved_work`); and unsandboxed `rite prepare`
refuses a dirty tree. The guard tests pass (20). *A first attempt hung
because a bare `yoloai new` defaults to Docker and builds an image. rite
always passes the project's backend.*

**Why `strategy: commit` still has no foundation today.** Pushing is the only way
work leaves a sandbox: the Worker instructions (`workspace/manage.py`,
step 4) say to push after every commit, because "a commit that was never
pushed is gone". Under "don't push", step 5 above is the state: the work
is stranded in a copy that blocks the Worker's next task, and one
`--force` by a person deletes it. Strategy 1 first needs rite to bring
the copy's commits into the local project repository before the sandbox
goes. A module whose origin is a local directory, mounted read-only and so
not pushable, is in that state today.

### Ticket

| # | work | done when | depends | size |
|---|---|---|---|---|
| PB1 | **The `publish:` design above, final.** Workers never push; the Manager ROLE publishes, split as drawn below (whatever loses work or publishes something unintended is rite's). `strategy` (`commit`, `push`, `pull_request` default, ~~`push_to_shared`~~) per project with an optional per-module override; ~~`shared_repo` per module under the three rules~~; `squash` opt-in, default off, every strategy; `auto_merge` opt-in on `pull_request`, green matched to the head SHA | **Each value observed, not just built.** One real task per `strategy` goes from a Worker's commit to its end state on a real project. Under `commit`: the work survives the Worker's next task, nothing reached any remote, and a person reworks it with `git rebase -i` without friction, squash off and on. An override observed taking effect: the effective-strategy line changes with it, and a two-module task is published per module and reported per module. ~~`push_to_shared`: observed refusing with no `shared_repo`, and refusing when it is the module's `origin` in another spelling.~~ `auto_merge`: observed REFUSING on each of the four stale-green shapes, and merging on a green whose SHA is the PR's head | a path from the sandbox copy into the local repository; Track MM reading the effective value | design final; unsized |
| ~~PB1's `push_to_shared` half~~ | **Re-filed 2026-09-26 as PB2, v0.8.0** (`V080_RELEASE_PLAN.md`), by Robert's scope: "publishing strategies minus `push_to_shared`". The struck text in PB1 is what moved, word for word. The design above stays whole, because it is one `publish:` configuration | — | — | — |

### Auto-merge: explicit opt-in, and a green matched to the head SHA

All values are implemented. Auto-merge is the one clause gated hardest:

🔴 **Auto-merge after checks requires (a) an explicit opt-in, and (b) a green
matched to the head SHA being merged.** "No failures" is not a green, and a
green on any other commit is not this one's.

**Why, so it is not softened later by someone who has not seen it.** The
trap fired four separate ways on 2026-09-26, and each time "no failures"
looked like success:
1. A merged PR left no open PR, so no checks fired.
2. A `CONFLICTING` PR fired no checks, because GitHub cannot compute a
   merge ref.
3. A poller read an older run's success.
4. A run's SHA matched `HEAD` when read, while `main` had already moved.

An Owner auto-merging on a green that describes a different tree is that
bug with the safety off. (The same rule governs how CI is read in the
v0.6.0 readiness list, D12.)

### Strategy 1: what "a human can comfortably rework it" requires

The requirement, from his quote: **`strategy: commit` leaves the work in a state a
developer can comfortably rework before it goes out.** The reason is social,
not technical: the developer does not push unreviewed AI-generated code at
colleagues who inspect every line.
- **Committed locally, on a branch the developer can rebase.** Not a
  detached HEAD, and nothing done to the commits that makes `git rebase -i`
  unpleasant.
- **Nothing is pushed, including no push to a remote branch "for
  safety".** The developer is the first thing between the work and their
  team. (The shared repository below is a separate, opt-in decision, never
  `origin`.)
- **The commit-message convention is a starting point.** A reasonable
  default message helps; an unamendable one does not.

### Squashing: one opt-in setting, orthogonal to strategy

- **Default off.** A person who has not thought about it gets the full
  history, not a decision made for them. Opting in states that the output
  is usually good enough to review as one change.
- **Amendable either way.** Squashed or not, the branch must rebase cleanly
  and be comfortable in `git rebase -i`. A squash that leaves the branch
  awkward trades a convenience for the thing `strategy: commit` exists to protect.
- **A squashed commit needs a message for the whole change,** not the last
  commit's, and it is still a default to amend.
- **Modelled once, not per strategy.** All three strategies plausibly want
  the same setting. Nothing found so far says it differs by strategy; revisit
  if the design finds a reason.

### Who does what: rite's part and the model's part

**The line: anything whose failure loses work or publishes something
unintended is rite's, not the model's.** The local commit under `strategy: commit`
is the clearest case. A model that forgets to commit loses the task, which
is exactly the failure Robert called out. The engine contract already draws
this line once. R7 ("Leave verification to rite": `harness.run_subtask`
decides `accepted` from rite's own verify command, never from the engine's
claim) exists because an engine reported exit 0 over work that did not
happen on all five benchmark tasks. The same reasoning puts the
load-bearing half of publishing in rite rather than in a prompt.

| rite, deterministically | the model, by judgement |
|---|---|
| Which strategy is in force: per-project config, never a per-task choice | The commit message's content, within the convention rite applies |
| Committing a finished task's work locally, so nothing is lost, whatever the model does | Whether the result is fit to publish, above rite's floors |
| Squashing, when opted in; applying the commit-message convention as an amendable default | What the PR says |
| Opening the PR, pushing, and merging, as the strategy allows | Whether to ask the User before an action the strategy permits |
| Whether merging after checks is permitted at all | |
| **Floors the model cannot talk past:** "finished" means rite's verify command passed (R7), and nothing is published unless rite's publish gate passed. A check counts only when matched to the SHA it ran on (V060 readiness D12) | |

**Refined against the code:** "whether the work is actually finished" is
not the model's alone. R7 already makes rite's verify the floor, so the
model judges only what verification cannot see.

**Where it meets Multi-Manager routing (Track MM):** the strategy is
config, so a secondary's completion report and the Owner's merge decision
are bounded by the same setting. The Owner reads the strategy to know
whether it may merge, and that is deterministic too. A secondary does not
need to be trusted to have chosen a strategy, because it does not choose
one.

### What the design must answer (captured, not designed)

- **The Owner must know the setting.** `integrate` assumes pushing and
  opening a PR today (`config/managers.py`: "pushing and opening the PR
  needs a claude engine or a person"). How a secondary reports a finished
  task upward then has to carry what was done with it under the strategy.
- **Committing gets MORE central under `strategy: commit`, and it is broken on
  Robert's Mac today.** Measured 2026-09-26 (D10): his global SSH commit
  signing (`~/.ssh` unreadable) and his global Node pre-push hook both fail
  inside the Manager's sandbox, so no Manager can commit there. Robert's
  preferred design makes that blocker worse, not better. The Worker side
  (yoloAI sandboxes, a different boundary) has not been measured.

### `push_to_shared`: a shared repository for multi-Manager work (was: a future improvement)

Robert, verbatim, in the order given:

> "I think that we may want to log a future improvement for strategy 1
> that it publishes to a secondary shared repository. So managers can
> still work uninterrupted and nothing gets pushed to origin (client's
> repo)."

> "I mean in multi manager scenario"

> "Or we can make it a separate strategy"

**What it is: a collaboration mechanism for several Managers.** With two
or more Managers, "commit locally and stop" leaves each Manager's work only
where it ran. Managers cannot build on each other's output, and the Owner
cannot route work that depends on a secondary's finished changes. A shared
remote that is not the client's `origin` lets them keep working. It also
gives durability, which is secondary here.

🔴 **The shared remote must be operator-chosen, with no hosted default,
and "off" is the safest default.** For some clients, their code leaving
their own infrastructure is unacceptable, and that is the on-premise
constraint rite's own target users live under. A default that pushed a law
firm's code to a convenient hosted remote would be the worst thing in this
design. This is a setting with a compliance dimension, not a convenience
toggle.

**Resolved by Robert: a fourth strategy value, `push_to_shared`,** after a
`remote` axis was tried and rejected (it made meaningless combinations
expressible). The Owner's merge answer depends on one setting, `strategy`
(plus the `auto_merge` flag).

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
  C6's closed trap (`c58e4e5`): only named variables are allowed onto
  tmux's argv, so a key sent that way must be admitted by name, with the
  exposure that brings. C6's open half, how a credential reaches a pane at
  all, applies to Cursor's key as it does to Claude's and GitHub's (C26). *(Since then C6's delivery half is built: Claude's login and the GitHub token reach the pane as per-Manager files, `8a61989` and `f3926a1`.)* The stored route needs the profile to grant a
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

### ✅ CLOSED by `8a61989`, kept as the record: SB4, a live cross-project read

*This was the item to raise first while it was open. The row in the table
below records the closure; the text here is as it was written.*

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
| ~~SB4~~ | **Closed: `~/.claude` is no longer granted** (`8a61989`): a Claude Manager signs in from its own `CLAUDE_CONFIG_DIR`, observed on macOS. Kept so the id is not reused | — | — |
| SB2 | **`(allow network*)`**: the whole network | the profile's text; probe 4 in part 0.2 | **EG3**, as egress work in its own right. No longer the fix for any escape (part 0.3). **So v0.8.0**, with EG3 (`V080_RELEASE_PLAN.md`) |
| SB5 | **`(allow mach-lookup)` with no filter.** Claude Code on macOS keeps its login in the keychain, and `4ebbbd7` measured that the login is found inside the boundary, so at least that item is reachable. That is an inference, not a direct keychain probe. Not measured: whether a Manager can read **other projects'** rite credentials from it. The Worker-profile measurement (keychain content denied) does not carry over, because this profile differs | profile text | a measurement first. If reachable, per-service `mach-lookup` filtering, measured against the login |
| SB8 | **`/tmp` and `/private/tmp` are readable and writable**, and other rite worktrees and scratch directories live there | stated by `limitations()` itself, so disclosed rather than hidden | open, and not obviously closable: the engines need a temp space. The same measure-then-narrow method as SB4 |
| SB7 | **Which Manager may ask for which Worker.** A request names a declared Worker and a ticket. With several Managers in one root, any Manager can ask for any declared Worker. Whether Workers belong to a Manager is not decided (SBQ1) | `broker.py` validates against project-level declarations | MM, after SBQ1 |
| C25 | **No opt-out from the Manager's sandbox**, and an operator's own hook already fails inside it. Every narrowing in this track (SB4) raises the stakes | `V060_RELEASE_PLAN.md` C25 | a decision. See the consolidated list |
| C24 | **A requested Worker starts at the cycle boundary**, deliberately | `V060_RELEASE_PLAN.md` C24; `supervise.py::_honour_worker_requests` | a decision, not a fix. See the consolidated list |
| SB9 | **On Linux a Manager can replace the Claude login that authenticates it; on macOS it cannot.** Shipped in v0.6.0 as a documented PLATFORM DIFFERENCE, by Robert's default (2026-09-26: "if it isn't cheap, ship it documented and fix it in v0.7.0"). Why: Claude must write its whole config directory (`248ba5b`: listing its entries one by one broke Claude silently), and Landlock cannot deny one file inside a granted tree | measured 2026-09-26 on macOS in the real profile: **(a)** with `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`, the token read from an open descriptor, out of a 0600 file OUTSIDE `claude/`, signs Claude in and is not copied into the config directory. **(b)** But a local listener showed the order: the descriptor's token is sent FIRST, and on a 401 Claude FALLS BACK to a planted `.credentials.json`. So the move is cheap (about one sitting) and restores only "rite's token wins while it is valid", which is why v0.6.0 ships the wording instead | the login held where the Manager cannot write, AND nothing in a writable directory that Claude will fall back to: either Claude stops falling back (upstream), or the config directory itself is not Manager-writable (which needs SB10's answer), or the credential broker, so that no token is held in the sandbox at all |
| SB10 | **A Manager can plant settings for its own later runs, on BOTH platforms** (v0.6.0 readiness W11). `claude/settings.json` is loaded as USER settings: a planted `env.ANTHROPIC_BASE_URL` redirected the next run's model requests (2 of 2, macOS). It persists across runs. The project's `.claude/settings.json` is Manager-writable too | measured 2026-09-26; `--setting-sources project,local` stopped the user-settings path (0 requests) | exclude user settings, and make the project's `.claude/` read-only to the Manager (a seatbelt deny; on Landlock by listing the project root's entries, as the inbox fence does), or move user widening into rite's own settings file. A decision, then observe both platforms |
| SB11 | **On Linux, the Manager's policy grants `/tmp` and `/var/tmp` read and write** (v0.7.0 by Robert's scope, 2026-09-26: "the `/tmp` grant fix"). Landlock has no deny rule and unions its grants, so for a project living under `/tmp` the wholesale grant overrode the enumeration that fences one Manager from another; the credential narrowing was defeated too (`spikes/MM2b-moving-the-inbox-out-of-the-project.md`). Linux only: seatbelt denies after granting, and the last match wins. The inbox half is gone since the mailbox left the tree (`935ceef`); the MM2b note records the rest as "a separate hole" | `b542c15`'s own message, and the MM2b note, which measured it | **`b542c15`** on `fix/landlock-no-wholesale-temp`, prepared and **not merged**: it removes both grants and keeps the engine's own `TMPDIR`. ⚠ Its message says what is not established: anything that hardcodes `/tmp` breaks, and a grep found nothing on the Manager path, which is not evidence for an engine and its subprocesses. So it needs an observed Manager cycle on Linux before it lands |

**SBQ1. Do Workers belong to a Manager?** (a) No: Workers are project-level and
fungible (§5.3.4), and any Manager may request any of them within the caps; (b)
yes, declared per Manager profile. (a) is today's model and needs nothing. (b)
makes P1 cover Workers and changes `rite add worker`.

---

## Track ME — Memory

⚠ **UNSCHEDULED since 2026-09-26.** Memory was v0.7.0 scope. Robert's
v0.7.0 ("feature-complete single-machine") does not name it, and it is not
multi-machine work, so it is filed in neither release ("Fits neither
release", top of this plan). Kept here whole until Robert places it.

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
| K3, the queue as a draft | questions that **resolve themselves with time**: the answer arrives through later work | at window open, for **deferred** questions only | v0.6.0: built, stub half observed (`c0429dd`); the real-model half open |
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
it was recorded. *Re-checked against `main` at `7718465` on 2026-09-26, for
the re-filing. Where a row's release changed, the row says so.*

### Scheduled for 0.7.0 by an earlier decision

| item | from | state |
|---|---|---|
| **The scenario gate** (§7.3, D-81) | v0.6.0 plan, Decision 5; SPEC 0.22.1 | Not costed, deliberately: it is a process change as much as a feature. **Needs its own design pass before a size**, and that pass must answer §9.15.3a's recorded gap first: the gate cannot reach journal entries, because they are machine-local and uncommitted |
| **Relocate flat `.rite/` state per Manager** | §5.4.5 step 2; §9.14.9 item 3; `managers/__init__.py` ("until 0.6.0") | MM1. No v0.6.0 ticket carries it. Designed with MM8, which moves the directory it moves into |
| `V070_MULTI_MANAGER.md` Q1–Q4 | that note | MMQ3, MMQ4, MM, SB. *Q1 (correlated failure) and Q2 (worker cap) are v0.7.0 as MMQ4 and MMQ3; Q3 and Q4 stay v0.7.0* |
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
| A check-in window with no Manager running: **built as proposed and observed** (`d8b235c`), and the proposal is still **Robert's to confirm** | K6. *It ships in v0.6.0 as built; the confirmation is a v0.7.0 decision* |
| ~~N1/N2, ticket-text cleanup and phrase reporting. **Placed last in v0.6.0 and droppable** (Robert's ruling, `beac07f`). If they are dropped for quota, they arrive here. Applying §6.6 to Slack text is accepted~~ **Not carried: both landed in v0.6.0** and are observed (the v0.6.0 plan's N1 and N2 rows; readiness D9) | the v0.6.0 plan's part N |

**Added 2026-09-26: v0.6.0 rows that are open and not on the tag's path.**
Each is v0.7.0 unless it lands before the tag. The detail is in its row.

| item | v0.6.0 id |
|---|---|
| `rite doctor` is silent about the served window when the model is idle: `context_detail` is recorded and never printed | B7 (and C18, whose guide text already says so) |
| The test tmux fixture never unsets `$TMUX`, so a run from inside tmux lands on the enclosing server | C1 |
| Three test starters still swallow `**kw` | C3 |
| The empty-prompt guard cannot fire from `supervise` | C14 |
| C15's remainder: settle checks read "could not ask tmux" as "died"; two refusals relay unredacted tmux stderr | C15 |
| An anchor is checked for presence, not support | C31 |
| A Manager expects `rite question list` | C33 |
| `sandbox.enabled` does not decide whether Workers are sandboxed ("after the tag") | C34 |
| A `rite reply` with backticks is refused and misdiagnosed | readiness W9 |
| A Claude Owner cannot wait for its secondary (a named rough edge of v0.6.0) | readiness W13 |
| The Slack relay ignores `Retry-After` | readiness W14 |
| K5's reply halves, if Robert has not typed them before the tag | K5 |

### Deferred without a release, and at risk of evaporating

| item | from | why it is listed |
|---|---|---|
| **The Worker tier**: decompose duty (RL-T7), `harness`/`runners` gaining a caller, B5 | v0.6.0 plan, B4b reshaping | "out of v0.6.0" with no destination. Needs a release named. *Still none after the re-filing: neither v0.7.0 nor v0.8.0 names it* |
| **Re-advertising `--record-issues`**, now that the journal is redacted | §9.15 (reversed for 0.5.1 "until the leak path is closed"); C7, **landed** as `5ec5173` | ⚠ **The precondition is met and nobody has been asked.** §9.15 kept the feature unadvertised only because the journal applied no redaction. It does now, and the flag is still absent from the README, the guide and the CHANGELOG. Whether to advertise it in 0.6.0 or 0.7.0 is Robert's call. It is in the consolidated list |
| **Pin the Goose version** | v0.6.0 plan, Decision 4 ("And pin the version") | a sentence with no ticket. No pin was found in `src/` (searched for the measured version string and for "pin"). *Filed v0.7.0 by the re-filing: a local Manager on one machine* |
| **A derived Modelfile instead of warning about the 4,096 window** | v0.6.0 plan, B8's closing section | an option recorded for a decision: it writes into the operator's Ollama library. *Filed v0.7.0 as a decision* |
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
| S5 | `enclosure.limitations()` | "other projects … NOT reachable" | **Closed on `main`** by `7b5462d`/`9862b59`, re-measured (part 0.2). One gap remains: it does not name other projects' **transcripts** under `~/.claude` (SB4). *That gap closed with SB4 (`8a61989`): `~/.claude` is not granted any more* |
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

**Re-stated 2026-09-26, round two, against `main` at `112939c`.** Split by
when the answer is needed, because two Managers on one machine moved into
v0.6.0 and several questions moved with them. Each row says what it blocks.
"Blocks nothing" means the behaviour exists today and the question is
whether to change it.

### Due before v0.6.0 tags (Sunday)

**Where each stands, re-checked 2026-09-26 at `7718465`:** MMQ2, C28 and
C6/C26 are answered and built (each row's first cell says how). C25 is still
open. MMQ5, K6, C7 and C24 ship as built, so they block nothing in v0.6.0,
and all five are **v0.7.0 decisions** now (next table).

| id | question | options | blocks |
|---|---|---|---|
| ✅ **MMQ2**: decided (c), built (`e18139d`, SPEC §9.16.7) | Two Managers in one root both read the Owner's DM: **who acts on an instruction?** (SPEC §9.16.7: today, both act, and two relays are 60 calls a minute against Tier 3's "50+") | one app per Manager; per-Manager addressing in the one DM; one Slack-reading Manager per project; the Owner's id per instance | **any two-Manager project with Slack enabled**, which is the Sunday shape if Slack is on |
| ✅ **C28**: answered by (c), a Linux boundary: Landlock, chosen by platform (`4b2c3db`); CI green on `main` at `691a248` (readiness D12) | **What does a Manager do on Linux?** It cannot start there today, and CI has been red since `cecbbdd` | run unsandboxed and say so every run; refuse to start and say why; a Linux boundary once the spike reports | **Linux as a platform** (the README claims it), and a green CI before the tag |
| **C25** | **Can a Manager start outside its sandbox, and how?** An operator's own hook already fails inside it | a config key; a `--no-sandbox` flag; widening the profile per project. Each needs the announcement to say the boundary is off | any setup whose hooks or tools reach outside the profile. It gets sharper with C28(a), SB4 and EG3 |
| ✅ **C6 / C26**: decided (token only, from files) and built (`f3926a1`, `8a61989`); observed against GitHub on macOS (`93d63f5`, readiness D10) | **How does a credential reach a Manager's pane?** The argv trap is closed, and delivery is not. Today only tmux-server inheritance, which works only when `rite start` starts the server. The GitHub board is reached anonymously from inside the sandbox (60 requests an hour, not 5000, and no private repositories) | named in `CREDENTIAL_HANDLING…md` and C26. Not yet laid out as options | **every sandboxed Manager on a private GitHub board**, and `git push` over HTTPS |
| **MMQ5** | Two Managers mean **two standups per window** in the Owner's DM, each with its own answer thread. Keep, merge, or make it the Owner's job? | keep (works today); merge into one; the Owner composes | nothing. It is what ships unless changed |
| **K6** | **A window with no Manager running**: built as proposed and observed (`d8b235c`). Confirm the proposal? | confirm; change | nothing. It is built |
| **C7 → `--record-issues`** | **Re-advertise issue recording now that the journal is redacted?** §9.15 held it back only "until the leak path is closed". C7 (`5ec5173`) closed it, and the flag is still in no README, guide or CHANGELOG | advertise in 0.6.0; in 0.7.0; not yet | nothing. The feature works and is unadvertised |
| **C24** | **Should a Worker requested mid-cycle start at once**, or at the cycle boundary as now? | boundary (latency, which the Manager is told about); at once (a non-blocking launch in the supervisor's loop) | nothing. It is behaviour. It interacts with CU3, which would add Cursor's mint to the same loop |

**Also blocking Sunday, but not decisions:** C29 (every Goose Manager starts
fresh on each run, because C8's check refuses its own designation), C30 (a
Manager named `permissions` overwrites the allowlist) and the rest of C28's
nine red tests. Each has a fix shape in its row. They need work, not an
answer. *All three are done: C29 landed as `9a952be`, C30 is fixed (its
v0.6.0 row), and CI is green at `691a248`.* Two Managers' state separation (SPEC §5.4.8 P1, P3, P4) is the
building session's call, and the release notes must say which properties
hold.

### Due for v0.7.0

*Re-filed 2026-09-26: MMQ1 and EGQ1–EGQ5 moved to `V080_RELEASE_PLAN.md`,
and memory's questions to "Without a release". The five decisions that ship
as built in v0.6.0 (MMQ5, K6, C7, C24, and C25, which is still open) join
this table, with the rest the re-filing places here.*

| id | question | blocks |
|---|---|---|
| MMQ3 | a per-Manager worker cap | — |
| MMQ4 | correlated failure across Managers on one machine: detect, or document | — |
| MMQ5 | two standups per window in the Owner's DM: keep, merge, or the Owner composes | nothing; it ships as built |
| SBQ1 | do Workers belong to a Manager | SB7 |
| SB10 | exclude user settings and make the project's `.claude/` read-only to the Manager, or move user widening into rite's own settings file | SB10, and SB9's second route |
| C25 | can a Manager start outside its sandbox, and how | setups whose hooks or tools reach outside the profile |
| C24 | should a Worker requested mid-cycle start at once | nothing; it is behaviour. Interacts with CU3 |
| K6 | confirm the no-Manager-window proposal | nothing; it is built |
| C7 → `--record-issues` | re-advertise issue recording | nothing; it works and is unadvertised |
| C32 | should the check-in invite Manager notes at all | decided after RP1's design |
| C34 | make `sandbox.enabled` load-bearing, or rename it | C34 |
| readiness Q3 | Jira for a sandboxed Manager: refuse, or a service account by the file route (the broker, the third option, is now v0.8.0) | Jira projects |
| B8's option | a derived Modelfile instead of the warning | nothing |
| the scenario gate | ⚠ **confirm it stays v0.7.0** (Decision 5 put it here; the new scope does not name it) | the gate's design pass |
| CUQ1 | Cursor for Workers | — |
| CUQ2 | Cursor's credential route (API key or stored login) | CU4 |
| CUQ3 | what to do if CU1 finds `-p` takes the prompt only as an argument | CU2, after CU1 |
| PB | what a v0.7.0 build does with `strategy: push_to_shared` before PB2 lands | PB1's config parser |

### Without a release

| question | blocks |
|---|---|
| the Worker tier's release (the decompose duty, `harness`/`runners` gaining a caller, B5) | B5, the harness |
| will rite ever be commercial, and does Slack agree a manifest-created app is "internal customer-built" (A3b) | the polling transport, if the answer is no |
| where memory goes, now that neither v0.7.0 nor v0.8.0 names it; then MEQ1 and `V070_MEMORY.md` Q1–Q7 | any memory spec |
| where the relay's escalation chain and self-reflection go (`V080_RELAY_CHANNELS.md`) | that note's open questions |
| a Linux Worker sandbox, or accept readiness D18's documented limitation | D18; v0.8.0's MX1 |

## Sequencing, and where the release can be cut

### After the re-filing, 2026-09-26

**What Robert named is the release:** Cursor (CU), the dogfood fixes, PB1,
RP1, MM8 and SB11. The other rows filed in v0.7.0 are single-machine
completeness under the same heading, and none of them is named as a
condition of tagging. Which of them must land is Robert's to say.

**Measurements first:** CU1, and SB5's keychain measurement. Neither needs a
decision.

**Edges the rows already state:**
- **MM1 and MM8 are designed together.** One moves flat state into
  `.rite/managers/<name>/`, the other moves that directory out of the tree.
- **PB1 needs** a path from the sandbox copy into the local repository, and
  a Manager that can commit inside its sandbox. D10 measured that the
  operator's commit signing and pre-push hook both fail there.
- **SB11 needs** an observed Manager cycle on Linux before `b542c15` lands.
- **C32 is decided after RP1's design**, not before.
- **CU2 → CU6** after CU1, as track CU orders them.

### The sequencing as written on 2026-09-25, before the re-filing

⚠ **SUPERSEDED, kept as the record.** SB4 and MM5 have since landed, and
every EG item named below is now v0.8.0.

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
