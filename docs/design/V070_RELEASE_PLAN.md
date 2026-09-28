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

**Robert's first 24 hours of v0.6.0 dogfooding decide this list**, so nothing
is guessed into it. Each fix lands here as a row, with what was observed and
on which platform.

**From the Linux acceptance run, not the dogfood** (2026-09-27, Parallels
Ubuntu 24.04 ARM64 VM, kernel `7.0.0-31-generic`, 4 cores, `main` at
`15312e3`; a Claude Owner `lead` and a Goose secondary `small` on `qwen3:8b`,
one project root, board `robbartoszewski/rite-dogfood-board`). Box times
CEST. Run 1 was `rite start` as authored; run 2 differed only by `--fresh`
on both starts, and is reported as a retry.

| # | observed | done when OBSERVED |
|---|---|---|
| DF1 | 🔴 **Configuring a board does not reset a Manager's context, and nothing warns.** Run 1: a bare `rite start` continued `lead`'s previous conversation, a setup session that had been told "This project has NO ticket backend configured". It never received the board-mode opening, and it declined the routing instruction as a duplicate: "This is the same routing instruction a third time — already completed last turn … I'm not re-routing a duplicate task. Status unchanged: still waiting on the user's choice of ticket backend." Its three sessions ran 00:57:35–00:57:57, and the run ended. `small` likewise resumed Goose session `20260926_2`. With `--fresh` (run 2) `lead` routed at once. A user who runs once, configures a board, and starts again meets exactly this: a Manager confidently declining work. **Status 2026-09-27:** fixed by #12, with #13's follow-ups. It was verified on macOS against real Claude and this board: a bare start after the board is configured, removed or pointed elsewhere, or with an earlier-build designation, is refused, and `--fresh` records the board. #13 also fixed a bound-ended run's held session making the next bare start discard an intact conversation ("could not be continued"). Not re-run on Linux. Left as documented, awaiting a decision: an earlier-build conversation whose board was since REMOVED continues silently. | A Manager whose designated conversation began as a setup session, started again after a board is configured, either starts fresh or is told in its instruction that the board now exists, and `rite start` says which. Observed, not reasoned. |
| DF2 | 🔴 **W13 on Linux: the Owner cannot be running when its secondary replies, as configured.** Run 2: `lead` routed at 01:00:29 and its `rite start` exited at 01:01:48 (`ceiling reached: 3 session(s) started`; sessions of 14s, 54s and 16s, the last two finding nothing). `small` received the route at 01:03:55, because a secondary takes mail only at ITS cycle boundary and its session had started at 01:00:09. Its `rite reply` was queued at 01:04:53, three minutes after the Owner had gone. **The supervisor waits only while a session is alive; between sessions it resumes at once, and it has no "routed, awaiting a reply" state.** A later cycle of the same run WOULD pick the reply up (the router runs in the Owner's wait loop and at each cycle start), so a higher `--sessions` would make it land, but only by spending a Claude session every ~30s on "no reply yet", sized to a latency rite cannot know. That is busy-polling, not waiting. | An Owner that routed work and has a reply outstanding waits between cycles WITHOUT starting a session, until the reply arrives or a stated bound passes, then starts a cycle with the reply delivered. Observed on Linux with a real local secondary: the reply is brought to the Owner and acted on within the same `rite start`. |
| DF3 | ⚠ **Every Manager can read every Manager's mailbox, for every project on the machine.** From inside `small`'s Landlock boundary, listing `lead`'s inbox succeeded (rc 0) while a write was refused (`Permission denied`, rc 1). MM-2 is about writes and held. The read comes from `~/.rite` being granted readable wholesale (`landlock.DIRS_RITE_CREATES`, "rite's own state root"; seatbelt grants `.rite` the same way in `enclosure`), which predates the mailbox moving under `~/.rite/managers/` (`935ceef`). So it is incidental, not designed: the narrow grant for a Manager's own mail directory is also in the policy, and redundant. **Status 2026-09-27:** fixed. Mail lives in rite's data directory (`<data>/rite/mail/`, beside the credential root) as MM-2b recommended. It is moved out of `~/.rite` at each Manager's start, and anything left there is announced at every start. macOS also no longer lets a Manager read another's `.rite/managers/<name>/`. Read tests with a production-shaped HOME: seatbelt (verified to fail on the old code) and Landlock (CI). A follow-up closed the rest of `~/.rite`: it is no longer granted, and on macOS it is denied by name. That covers `dispatch/projects.yaml` (every registered project's path, which is a client list) and the credential NAME registry, since nothing inside a boundary reads either. | From inside a Manager's boundary, listing or reading another Manager's inbox or outbox is refused, on both platforms, while its own mail directory stays readable and its outbox writable. |
| DF4 | 🔴 **A board read right after an issue is created can see zero tickets, and rite reads that as "nothing ready".** Measured 2026-09-27 on `robbartoszewski/rite-dogfood-board` (#8–#14): after `gh issue create` plus labelling `scheduled`, `gh issue list --label scheduled` returned 0 for about 2–3 s. REST `GET /repos/{r}/issues?labels=scheduled&state=open` also returned 0, with the label filter and without it (#9–#13). `GET /repos/{r}/issues/{n}` returned the issue at once, open and labelled. **Every list endpoint is eventually consistent, so changing endpoints cannot fix this.** The consequence: `_ready` (`loop/__init__.py`) returns `[]`, the verdict is `idle`, and a `rite start` right after routing or filing stops with "done: the board has nothing ready". That is a result decided by the timing of two independent things. **Scope (not built):** rite keeps a small per-project ledger of issue ids IT created or labelled `scheduled` (`rite board` create/label, the broker), written outside the boundary. `_ready` takes the list result, unions it with that ledger's recent ids, and confirms each ledger id by single-issue GET (open and still labelled), since that read is consistent. An id is dropped from the ledger once a list has returned it, or once a GET shows it closed or unlabelled. Issues created by a person in the web UI stay subject to the lag, and the idle message should say so rather than claim the board is empty. Jira needs the same measurement before it is assumed either way. | A `rite start` issued immediately after `rite board` creates and schedules an issue sees it on its FIRST read, with no sleep or retry anywhere in the path. Observed against real GitHub. |
| DF5 | ✅ **Cause reproduced: not a flake, and not in `workspace/`.** `tests/test_workspace_prepare.py::test_workspace_prep_reports_partial_blocking` ERRORed in 2 of 4 full local macOS runs on 2026-09-27, across three trees, never in CI. **It is the LAST test collected, and it has no fixture of its own but `tmp_path`,** so an ERROR could only come from an autouse fixture. The session-scoped `_suite_leaves_this_checkout_alone` (`tests/conftest.py`) fails when `git status --porcelain` of the checkout changed during the run, and pytest reports a session-teardown error against the last test. **Reproduced exactly:** `touch` one file in the checkout 1.5 s into `pytest tests/test_workspace_prepare.py` gives `ERROR …::test_workspace_prep_reports_partial_blocking`, "13 passed, 1 error". The control, with nothing touched, gives 13 passed. **The two historical ERRORs match that timeline:** runs 1 and 3 are the runs during which this session edited and committed files in the same checkout; runs 2 and 4 were not. The message from those runs was not kept (the output was piped through `tail`), so that match is INFERRED, not observed. **Fixed in the guard's words, not its behaviour:** it measured "the tree changed during the run" and said "the suite wrote into the checkout", and the error landed on an innocent test. It now names both causes (a test writing into the tree, or a person or another session editing during the run), says the test it is reported against is not the cause, and lists the lines that tell the two apart. The guard itself stays. Where several sessions share one checkout, as rite's does, it will fire whenever anyone edits during a run, and that is correct. **If it recurs with nothing edited:** rerun with `-rfE -o junit_logging=all --junitxml=<file>`, which keeps the full message per test. The guard's message lists what appeared or vanished, and that is the lead. | Met: the cause is named and reproduced on demand, and the message now says what it is. |
| DF6 | ⚠ **The same `refused: …` lines printed on two consecutive runs.** On macOS, the supervisor's refusal report (`supervise._say_refusals`) printed the same two refused compound commands at the end of run 5 (a fresh fallback session) and again at the end of run 6 (a `--resume` of that session), word for word, including `gh auth status …` and `grep -i GH_CONFIG_DIR …`. **The open fork:** either `_say_refusals` re-reports refusals from the RESUMED transcript, which contains the earlier session's entries (a filter on `cycle.started_at` that does not hold for a continued transcript), or the resumed Manager really re-ran the same commands. The session transcripts are under `~/Library/Application Support/rite/managers/rite-mgr-df1probe-5f53ba-lead/claude/projects/`, and their timestamps settle it. The lines themselves also claim the engine ignored `permissions.json` for a command "which rite's own allowlist DOES cover"; check that claim against the compound command separately. | Which branch it is, shown from the transcript's timestamps. If it is the first: a refusal is reported once, in the cycle that made it. |
| DF7 | ⚠ **A test gap: `test_it_cannot_rewrite_the_rite_it_runs` (`tests/test_the_manager_profile_denies_what_it_should.py:406`) measures the temp grant when rite is installed under one.** It skips only when the package is inside the project. When rite's `src/` sits under `/tmp` or `/private/tmp`, which the Manager profile grants writable, the `touch` succeeds and the test fails, reporting a writable rite that no real install has. Seen 2026-09-27 when a scratch worktree under `/private/tmp` ran the suite. It also leaves `rite-probe-should-not-exist` in the package on failure, because the assertion comes before any cleanup, and the checkout guard then fails the run as well (DF5's shape). Real installs are not affected: rite lives under `~/.local/share/uv` or a checkout in `$HOME`. **Fix:** skip, saying why, when the package resolves under a temp root the profile grants, and remove the probe in a `finally`. | The test skips with a stated reason under a granted temp root, still fails when the package is writable anywhere else, and never leaves the probe behind. |

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

## Track MS — Model selection as configuration

**Robert, 2026-09-27:** "I think a set of properties to set in the
configuration would be nice." **Recorded, not built**: he may have opinions
after a day of dogfood, and a schema designed the night before is one we
would be stuck with.

### What exists today (`origin/main` at `c2076d4`, read from the code)

- **Model is a property of a Manager, and only of a LOCAL one.** A
  `manager_roles` entry has `name`, `engine` (`claude`, `human` or
  `local:<class>`), `duties`, `preset`, `endpoint`, `model`, `agent` and
  `credential` (`config/managers.py`). `endpoint`, `model` and `agent` are
  **local-only and must come together** (`_LOCAL_ONLY`). `model:` on a
  `claude` role is REFUSED.
- **Goose** receives it as `GOOSE_MODEL` in the pane's environment
  (`supervise.py`, `goose_agent.py`).
- **Claude: rite passes no model.** A Claude Manager runs whatever Claude
  Code picks for that login. A setup-token Manager was observed on
  `claude-sonnet-5`, and why is unexplained (v0.6.0 K2).
- **Workers: rite passes no model**, though `yoloai new` takes `-m/--model` (yoloAI 0.11.0: "model name or alias"), a ready route for MS1. `yoloai new --agent claude` is fixed
  (`sandbox/__init__.py`). A person can pass `--agent-arg` to
  `rite sandbox start` (inferred, not tested, to carry `--model`), but
  Workers the broker starts for a Manager get no agent arguments. There are
  no local-model Workers in 0.6.0.
- **Duties route work to MANAGERS, not to models.** Presets `lead`, `pm`,
  `planner`, `executor`. The scheduler routes only the ENTRY stage
  (`decompose`) by duty (`scheduler/__init__.py`). The router
  (`local/duty_router.py`) also covers plan-review, execute, step-review and
  integrate, but nothing live routes those (inferred from a search for
  callers). One structural rule exists: a decomposing Manager needs a
  plan-reviewer on a DIFFERENT ENGINE (RL-6, `config/managers.py`), and
  "engine" means `claude` vs `local:x`, not one Claude model vs another.
- **rite's reviewer agents pin no model** (`templates/agents/*.md`).
- **Unknown keys are already refused** in `config.yaml`, `worker.yml`,
  `modules.yaml` and each `manager_roles` entry (`config/parse.py`,
  `_entry_error`): the precedent this design must follow.
- **Documented nowhere a user reads.** README and `docs/guide.md` mention
  neither `manager_roles` nor models. The one example is the Goose snippet
  in the 0.6.0 release notes.

### Measured for this design: a resumed Claude conversation can change model

2026-09-27, Claude Code 2.1.261, the operator's own login: turn 1
`claude -p … --model haiku` ran on `claude-haiku-4-5`. Turn 2
`claude -p … --resume <that session> --model sonnet` ran on
`claude-sonnet-5` in the SAME session, and recalled what turn 1 was told. So
rite can choose a Claude model PER CYCLE, because every cycle is a fresh
`claude -p --resume` process. **Not measured:** whether a `setup-token`
login (`user:inference`) accepts every model, and whether a Goose session
resumed with a different `GOOSE_MODEL` keeps its context.

### The design questions, answered

- **The natural unit is the CYCLE rite starts, not the duty.** A duty is
  something a Manager does WITHIN a cycle. A `lead` cycle may decide, route
  and review in one turn, and the model is fixed for that turn. So "a model
  per duty" is implementable only where RITE knows what a cycle is for
  before starting it: a check-in re-read, a setup session, a stage rite
  routed to that Manager. **Inside a free-form cycle it is not implementable
  as stated, and the honest answer is to declare several Managers**, one per
  model, each holding the duties that model should do. Per Manager stays
  the base, and "per cycle kind, where rite knows the kind" is the
  refinement the measurement allows.
  - **Cost to state in the docs:** switching model forfeits the prompt
    cache, and on a local engine it means a cold instance (the reason
    Track MX pins a Worker to its own machine's Ollama).
- **Workers: yes, and that is where the cost lands.** A Worker is one-shot
  per ticket, so a per-Worker or per-ticket-stage model is natural. rite
  starts every Worker (the broker, or `rite sandbox start`), so it can pass
  the model as an agent argument. A setting that covers only Managers would
  miss most of the spend.
- **Claude: rite CAN choose it,** per cycle (measured above) and per Worker
  (as an agent argument, not yet measured). So the property covers the
  engine most people use. Name it for what it controls, one
  `model` per Manager or Worker plus overrides per cycle kind, not
  "local model". Where an engine cannot honour it, say so at start rather
  than accept it.
- **Defaults: nothing configured means today's behaviour.** Claude Code's
  own choice for Claude, and the role's declared model for a local engine.
  Configuration only overrides.
- 🔴 **Inspectable, never silent.**
  - `rite doctor` and `rite start` print the EFFECTIVE model per Manager,
    per cycle kind and per Worker, with its SOURCE ("Claude Code default",
    "role", "override for review"). This is the same rule as the effective
    publishing strategy (PB1).
  - Unknown keys are REFUSED, as `config/parse.py` already does.
  - A key that names a cycle kind or duty nothing reads is refused, not
    accepted and ignored.
  - An override that loses to something else, such as a model the login
    cannot use, is said at start, and the run does not silently fall back.

### Settled by Robert (2026-09-27): model selection is a WORKER concern

> "I think the Manager itself can run on the same model always. However a
> lot of the work is delegated to Workers and they can switch models for
> different stages of the task."

A Manager keeps one model for its whole life, which sidesteps any change
of model within its conversation. Workers are short-lived and per task, so
they switch between stages. This encodes Robert's own agent setup (a
stronger model for design and review, a cheaper one for implementation)
rather than inventing a pattern. **This supersedes the per-cycle Manager
overrides above.**

### The four questions that decide it, answered from the code and measured

1. **Does a Worker have stages today? Only as prose.** A Worker is launched
   for a ticket and left to it. Its `CLAUDE.md` lists numbered steps
   (`workspace/manage.py`): branch, test and lint, verify its own fix, **7.
   run `/review`**, PR and merge, release. No plan stage exists. rite has
   no stage concept: nothing starts, observes or records a stage. **A model
   per stage therefore needs a stage concept before it needs a config
   key.**
2. **Who decides the stage? The model, so today it can skip review.**
   `/review` is a slash command the Worker chooses to run. `rite review`
   only prints the checklist (`cli/main.py`, "this command does not spawn
   agents itself"), and nothing records that review ran. By the rule
   already applied to publishing (whatever loses work or produces something
   unintended is rite's), **stage transitions must be rite's.** Otherwise
   "review used the stronger model" is a claim about what the model chose
   to do: the proxy problem again.
3. **Does the conversation survive a change of model?**
   - **Within one Claude Worker session, a stage can change model with no
     context boundary:** review is already SUBAGENTS (`/review` spawns
     `reviewer-round1` and others). Measured 2026-09-27: a subagent declared
     with `model: haiku`, invoked from a session on `--model sonnet`, ran on
     `claude-haiku-4-5` (both models in the session's usage record) and
     found the planted bug. rite's reviewer agents pin no model today. A
     subagent starts without the implementer's reasoning, by design ("fresh
     eyes"), and sees the implementation through the diff and files.
   - **Across a resumed Claude session**, the model can change per turn
     with context kept (measured above).
   - **Goose** (resume by name with a different `GOOSE_MODEL`): not
     measured. **Cursor** (resume by UUID): not supported yet.
   - **A fresh Worker launch per stage is a context boundary**: what carries
     across is only the branch and the ticket, so the design must say what a
     review stage is handed.
4. **What does a stage boundary cost?**
   - **As a subagent: no launch.**
   - **As a fresh Worker launch:** a seatbelt sandbox for a small Worker
     directory takes about 1.8 s (measured twice: 1.87 s, 1.79 s; it
     copies the Worker directory, so it grows with checkout size), plus
     `rite prepare`.
   - **A local model's cold load:** 12.7 s for 1.7B on the VM (v0.6.0 D4);
     about 23 s is reported for 8B.
   - **A changed Claude model forfeits the prompt cache.**
   - So three launched stages per ticket on a local tier costs tens of
     seconds of cold load per ticket, and subagents cost none.

**Consequence for MS1:** the cheapest honest shape is **rite-owned stages,
with review as rite-pinned subagents in the same Worker session**. rite
writes the reviewer agents' `model:` from config, and a stage rite starts
and records, not a step the model may skip, is what makes "review ran on
model X" checkable. Separate Worker launches per stage stay an option where
a stage must run on a different engine (a local implementer, a Claude
reviewer), at the costs above.

| # | work | done when | depends | size |
|---|---|---|---|---|
| MS1 | **Model selection for WORKER stages, with rite-owned stage transitions** (Robert: Managers keep one model). First a stage concept rite starts and records. Then a `model` per stage for Workers, default today's behaviour, with review as reviewer subagents whose `model:` rite writes from config. The effective model per stage, and its source, shown in `doctor` and recorded per run | Observed on a real project: a Worker whose implementation runs on model A and whose review runs on model B, with B in the session's model record; a Worker that skips review is DETECTED by rite (the stage never recorded), not reported as reviewed; an unknown key or an unknown stage name refused; a model the login cannot use refused at start | a stage concept (none exists); the setup-token model question measured; Goose resume-with-another-model measured if a local stage is wanted | design first; unsized |

## Track CU — Cursor, the third engine

**Status: MEASURED (CU1), and Robert has ruled.** CU1 was measured
2026-09-27 on macOS, Cursor CLI `2026.09.26-dd393fe`, Pro tier. The spike
note is `docs/design/spikes/CU1-cursor-cli.md`, brought to `main` from
branch `cu1/cursor-measurements` (commits `41a58a1`, `a05349b`) together with
**CU1b** (`spikes/CU1b-cursor-workers-review-and-throughput.md`, measured
2026-09-27 evening): the review convention under Cursor, Cursor inside a
yoloAI Worker sandbox, and whether Claude's allowance is the throughput
limit. What follows is taken from both.

### The design, 2026-09-27

**One engine definition, two roles.** Cursor's spelling lives once, in
`engines.py`, and serves both Manager and Worker, as Claude's does. A Manager
runs it in a tmux pane under rite's boundary. A Worker runs it inside a
yoloAI sandbox. Nothing Cursor-named goes in `config.yaml`: `engine: cursor`
on a Manager, `engine: cursor` in `worker.yml` on a Worker.

**The handle: a UUID4 rite records before the first launch, and a creation
time it checks after every continuation.** Cursor spells "start" and
"continue" identically (`--resume <uuid>`), so the handle cannot be derived
from the session name as Goose's is: `--fresh` would then silently continue
the old chat. So:

1. **Fresh start:** rite generates a UUID4 and records it with `designate`
   BEFORE launching. If the record fails, nothing launches.
2. **After a turn:** rite reads `createdAtMs` from the chat's `meta.json`
   and records it beside the handle.
3. **Before a continuation:** the chat directory must exist and carry the
   recorded `createdAtMs`. Otherwise the cycle is refused, naming the path.
4. **After a continuation:** `createdAtMs` must be unchanged.

**Why step 4, under Robert's rule on races.** Cursor creates a chat for any
UUID it has not seen and reports success (CU1 section 3). So a chat removed
between step 3 and Cursor opening it would be recreated empty, and the turn
would run without its history. rite cannot close that window, because the
create-if-absent is Cursor's. Step 4 makes it fail closed and say so: a
replaced chat has a new `createdAtMs`. The Manager's designation is then
marked broken, and no further cycle runs until the operator passes
`--fresh`.

A handle recorded but whose first turn never produced a chat (no
`createdAtMs` yet) is relaunched as a first turn with the SAME UUID. That is
idempotent: nothing was promised to continue.

**The key: in rite's file store, copied per role as a file, and read into the
one process that needs it.**
- **Store.** It is stored as `cursor_api_key`, in the file store Claude's
  token uses. It is imported from `~/.cursor-api-key` by `rite credential`.
- **Never in `services.py`'s automatic delivery.** A field with `env` goes to
  every Worker by `--env`, onto yoloAI's argv and into the pane, which is
  exactly what Robert ruled out. It is registered as `github_app` is, with
  no `env`, and has its own route.
- **Manager.** A per-Manager copy is written beside `claude/`, at 0600 in a
  0700 directory, as `claude_login` does. The pane's command reads it at
  exec: `CURSOR_API_KEY="$(cat <file>)" exec agent ...`. The command text
  holds a path, never the key. tmux's argv, the pane's environment and every
  other process hold neither.
- **Worker.** A per-Worker directory holds the key, mounted read-only with
  `-d`. CU1b measured it: the key is absent from the pane, from yoloAI's log
  and from the sandbox directory.
- **Exposure, stated.** The agent's own children (the shell commands it
  runs) inherit the variable. That is the same reach a Claude Manager's
  tools have to its token file, so it widens nothing.

**Cursor's state is per role, never `~/.cursor`.**
- **Manager.** `CURSOR_CONFIG_DIR` is per-Manager, as `CLAUDE_CONFIG_DIR`
  is. The three grants are those in "What else the build must handle",
  below.
- **Worker.** `HOME` and `CURSOR_CONFIG_DIR` point into the sandbox's own
  `rw/home`. Otherwise Cursor writes `~/.cursor/projects/`, which yoloAI's
  profile refuses (CU1b: exit 1, loudly). `destroy` then removes the chats
  with the sandbox.

**`worker-server` is reaped by rite, because nothing else does.**
- **Measured:** it survives `yoloai stop` and `yoloai destroy` (CU1b). It
  also outlives a Manager's turn outside tmux (CU1).
- **It ends itself after about 300 s idle** (CU1b, n=1).
- 🔴 **Not by `kill <pid>` after a lookup.** Finding it by working directory
  and then signalling the pid has a pid-reuse race between the two, and
  under Robert's rule that is a defect. Linux has `pidfd_open`, which closes
  it; macOS has no equivalent.
- **What CU7 and CW5 must choose between, then:**
  - rely on the idle exit, and say that the daemon, holding the key in its
    environment, outlives the Manager or the sandbox by up to five minutes;
  - end it through something that names the process rather than a
    reusable number: its process group or session, if the agent's launch
    gives it one rite owns (not measured), or a pidfd on Linux.

  Undecided; this is a design point for CU7, not a default.

### Two decisions, RULED by Robert on 2026-09-28

Both were put to him with the recommendation and reasoning below, and he
accepted both, verbatim:

> "D-CU-1, review gate - I agree with your recommendation"
> "D-CU-2, build in rite vs upstream yoloAI - I agree with your recommendation"

**These are settled.** The reasoning is kept so that a later reader argues
with the evidence rather than reopening them from memory.


**D-CU-1 (RULED: no Cursor-specific closure). Does the review gate hold for
Cursor Workers, and if not, what closes it?**

*Recommendation: it holds as it exists. No Cursor-specific closure; one set
of files.*

- **The gate is instruction, not enforcement.** §7.1 says rite reads none of
  it at runtime. The Worker's `CLAUDE.md` says to run `/review`, and `/review`
  says to spawn the `reviewer-*` agents. rite checks none of it for any
  engine.
- **Measured (CU1b section 1): a headless Cursor turn**
  - applies `CLAUDE.md` and `AGENTS.md`;
  - runs a command from `.claude/commands/`;
  - runs sub-agents from `.claude/agents/`, each through its own `Task` call
    and in a fresh context: neither saw the parent's secret;
  - honoured a reviewer's `tools: Read, Grep, Glob` in behaviour.
- **So the three files rite already generates reach Cursor unchanged.**
  Generating `.cursor/` copies would create a second set to drift.
- **The one difference.** Claude enforces `tools:`. For Cursor, enforcement
  and obedience look the same (n=2).
- **What it adds to CW6's acceptance:** the Cursor Worker's run shows at
  least two reviewer `taskToolCall`s in its `stream-json`.
- **The larger hole, which is not Cursor's.** The gate is unenforced for
  Claude too. If Robert wants it closed, it should be closed for every
  engine: rite checks a review record on the branch before publishing. That
  is not in this track.

**D-CU-2 (RULED: inside rite, on yoloAI's `idle` agent). Build Cursor Workers
inside rite, or through upstream yoloAI?**

*Recommendation: inside rite, on yoloAI's `idle` agent. Do not depend on
upstream.*

- **yoloAI is third-party** (`kstenerud/yoloai`), with a fixed agent list and
  no way to define one locally. Upstream means a contribution and someone
  else's release date on v0.7.0's critical path.
- **Measured (CU1b section 2): rite can already do it.**
  - An `idle` seatbelt sandbox, driven with `yoloai exec`, ran a Cursor turn
    and resumed it on rite's UUID (`LYNX47`).
  - The key arrived only as a file on a read-only mount.
- **Upstream's shape would put the key where Robert ruled it must not go.**
  yoloAI delivers an agent's key through the environment it launches with,
  which reaches its argv and the pane's launch line.
- **The cost of building it ourselves**, all in CW1:
  - rite, not yoloAI, runs the turn, so rite owns its process and its end;
  - `rite sandbox pane` shows the `idle` process, not Cursor, unless rite
    runs the turn in the sandbox's own tmux session;
  - the Docker backend's image has no `agent` binary, so seatbelt comes
    first.
- **Not excluded:** offering yoloAI a Cursor agent later, without waiting on
  it.

**CUQ5. Is Claude's allowance the throughput limit? Answered in part by
measurement (CU1b section 3).**
- **The weekly cap is a real, recurring stop.**
  - Weeks of 9 and 16 September: it ran out after 3.75 and 3.96 days, and
    work resumed at the reset minute, 50 and 64 hours later.
  - Before September: 37 five-hour-window hits.
- **It ran out only when rite shared the account with another project.**
- **Otherwise the person's turnaround dominates:** 71% of rite session gaps
  are an assistant that reported and stopped.
- **So Cursor recovers the capped days and does not touch the larger cost.**
  And whether Pro covers real Worker tickets is still CU6's (CUQ4).

### Robert's rulings, 2026-09-27

- **5a. The API key is the credential, and the reason is measured, not a
  preference.** Neither the stored login nor the desktop app's login works
  inside rite's Manager profile. With Cursor's directories granted, a turn
  fails with *"Authentication required. Please run 'agent login' first, or
  set CURSOR_API_KEY environment variable."* It is Claude's keychain finding
  again.
- 🔴 **5b. Cursor must be feature-equivalent to Claude, INCLUDING
  WORKERS.** Robert overruled a Managers-only recommendation: *"The whole
  point of adding Cursor is to improve throughput."* Workers are where the
  work happens, so a Managers-only Cursor would add an engine that does not
  touch the bottleneck. **This is a scope increase, sized below.**
- **5c. `create-chat` is dropped.** It never contacts Cursor, writes
  nothing, and **can hang** (from instant to about five minutes on a fresh
  config directory), so under Robert's timing rule it was disqualified
  regardless. A rite-generated UUID works identically: two turns recalled a
  token (`OCELOT31`; `HERON52` with the desktop app quit). Dropping it does
  not touch 5b, because it concerns only how a conversation gets its id.
- *(The larger-model experiment belongs to v0.6.0's A6 and the local Goose
  secondary, not to this track. It is not listed here, and it is moot
  anyway: Robert chose the Owner-verifies bar.)*

### ⚠ The Cursor API key was exposed on argv, and is deliberately NOT rotated

Recorded so that anyone investigating odd Cursor account activity finds it
written down rather than having to reconstruct it.

- **What happened.** On **2026-09-27**, during the CU1b measurements on
  Robert's Mac, a check for the key in a sandbox directory ran
  `grep -rlF -- "<the whole key>"`. That put the key's full value on grep's
  argv, which every local account can read with `ps`, for as long as the grep
  ran.
- **The key file.** `~/.cursor-api-key` was also mode 0644. Robert has since
  changed it to 0600.
- **Robert's decision, 2026-09-28, verbatim:** *"I don't want to rotate the
  API key right now."* So the exposure stands rather than being retired.
- **The bar that follows for this work:** no new path to the key, at all, and
  no "briefly". Its value never goes on a command line. Any check of its
  contents reads it inside a program and reports only its length and a hash.
  Authenticated measurements with this key are held.



| R | Cursor, measured (CU1) | still open |
|---|---|---|
| R1 one turn | `agent -p --trust --output-format json < prompt.txt`: **the prompt comes from stdin**, never on argv (`ps` sampled). CUQ3 does not arise | — |
| R2 end observably | exits. JSON output carries `result`, `session_id` and `usage` | exit codes for a missing model and an unreachable service |
| R3 continuity | `--resume <UUID>` on every turn, the first included, with a UUID rite generates. A non-UUID id is refused, exit 1 | ⚠ see "an unknown handle starts fresh" below |
| R4 human attach | — | `agent --resume <id>` interactively: not measured |
| R5 no approval | no `--trust`: exit 1, "Workspace Trust Required", no hang. `--trust`: refusals per command, still exit 0. `--trust --force`: runs commands | where a refusal is recorded (`store.db` not read) |
| R6 checkable | ⚠ **`agent status` with a working key prints "Not logged in" and exits 0**: it answers only for the stored login, so it cannot be the Manager's check | a probe that answers for the key |
| R7 rite verifies | not the engine's to provide | — |

### The handle: Goose's shape, not a third direction

**Corrected from this plan's earlier text, which said Cursor was a third
direction.** Measured: rite chooses the handle (a UUID) and says it on every
launch, exactly as it does for Goose's `-n <name>`. So none of what was costed
for "the engine mints up front" arises: no second engine invocation, no
parsing its stdout, no supervisor holding Cursor's credential to mint, no
mint-then-record partial failure. `ENGINE_CONTRACT.md` is corrected to match.
The one new constraint is that the handle must be a UUID.

🔴 **The sharpest thing in this track: an unknown handle starts fresh, and
says it succeeded.** `-p --resume <a UUID Cursor has never seen>` exits **0**,
reports `"subtype":"success"`, and runs in a **new, empty** chat. That is the
empty-value fault in a new place: a continuation that did not continue, which
looks from outside exactly like one that did. **Before any continuation
cycle, the adapter checks that the chat directory exists**, at
`$CURSOR_CONFIG_DIR/chats/<md5 of the workspace path>/<chat id>/` (holding
`meta.json` and `store.db`; the layout is measured, not documented). If it is
absent, the adapter **refuses loudly**, naming the path it looked for. It
never passes the id through and hopes.

### What else the build must handle, from the measurements

- **Three grants for a Cursor Manager** (measured inside rite's profile):
  1. read the binary at `~/.local/share/cursor-agent`, or it exits 126;
  2. read and write a per-Manager `CURSOR_CONFIG_DIR`, which relocates
     config and chats as `CLAUDE_CONFIG_DIR` does;
  3. ⚠ read and write `~/.cursor/projects/<workspace slug>/`, which
     `CURSOR_CONFIG_DIR` does NOT move. It holds the trust marker, a socket,
     a log and a second transcript, and two Managers in one project SHARE it
     (the slug is the workspace path).
- 🔴 **The key's route into a MANAGER is open.** CU1 handed the key to one
  run as `CURSOR_API_KEY` in its environment. For a Manager that is C6's
  closed hole: the environment, like argv, is visible in `ps`. Claude and
  GitHub reach a Manager as per-Manager files. Whether Cursor reads its key
  from a file (for example its `cli-config.json`) is **not measured**, and
  CU4 cannot be built until it is. **Superseded 2026-09-27 by "The design"
  above:** the key is a per-Manager file, read into the agent's own
  environment at `exec`, and in no other process. Whether Cursor can read it
  from a file itself is still not measured, and no longer blocks CU4. For a
  Worker, rite already delivers every
  credential through yoloAI's `--env` (D-31), so a key there matches today's
  Worker exposure.
- **Never relay Cursor's keychain advice.** Given a key inside the profile,
  the CLI prints *"The keychain item is stuck … `security
  delete-generic-password -s cursor-access-token`"*. In a sandbox that advice
  is wrong, and following it deletes the operator's own CLI login.
- **`worker-server` must be reaped.** Every `agent -p` leaves a detached
  `index.js worker-server` process (parent pid 1), alive for minutes after the
  turn. Killing the tmux session ended it within 5 s. Under
  `remain-on-exit`, as rite runs panes, it is not measured. **`rite stop` must
  reap it**, and inside a Worker sandbox too, where nothing guarantees that
  destroying the sandbox ends a process reparented to pid 1. **Measured
  since (CU1b): it survives both `yoloai stop` and `yoloai destroy`.**
- **Cost is not yet known for real work.** 11 tiny turns used about 1% of the
  Pro allowance (an upper bound). Whether Pro covers Workers doing real
  tickets, which is the throughput case for 5b, is exactly what CU6 must
  measure: requests per cycle, growth of context, allowance per day, and
  behaviour at exhaustion.
- **Egress.** Cursor's model endpoint is Cursor's service (`api2.cursor.sh`
  is the documented default). Track EG's list must name it.
- **Configuration vocabulary.** `engine: cursor`, and nothing Cursor-named
  in `config.yaml` (the v0.6.0 plan's B0a rule).

### 5b sized: Cursor Workers, the second integration point

A Worker runs inside **yoloAI**, not under rite's own boundary, and **yoloAI
0.11.0 ships no Cursor agent** (aider, claude, codex, gemini, opencode, plus
`shell` and `idle`). rite launches every Worker as `yoloai new --agent
claude`. So a Cursor Worker is **rite's first non-Claude Worker**: rite
drives `agent -p` inside a `shell`/`idle` sandbox, as the B4d spike found
possible for Goose (Goose ran in a yoloAI seatbelt sandbox given writable
state paths). That path was never built. The Goose Worker tier is "scheduled
in no release" (Carried forward, below), so **the same path serves both, and
building it revives the Worker tier**.

| # | Worker work | size |
|---|---|---|
| CW1 | **Engine selection in the Worker launch path**: `engine:` in `worker.yml` (a `WorkerManifest` field; unknown keys are refused today); `sandbox.start_worker` launching Claude as now, or Cursor in an `idle` sandbox that rite drives (D-CU-2, recommended). **Measured in CU1b:** a turn and a resume through `yoloai exec`. Still to decide in the build: rite owns the turn's process, and whether it runs in the sandbox's tmux so `rite sandbox pane` shows it | 2–3 sittings |
| CW2 | **The key into the Worker sandbox, as a file**: a per-Worker 0700 directory holding the key at 0600, mounted read-only with `-d`, read into the agent's environment at exec. **Not `--env`**, which puts it on yoloAI's argv and in the pane. Removed after `destroy` succeeds, and kept if it fails. Measured in CU1b: absent from pane, log and sandbox directory | ½ sitting |
| CW3 | **Cursor's state inside yoloAI's sandbox**: `HOME` and `CURSOR_CONFIG_DIR` in the sandbox's `rw/home`, `--trust --force`. **Measured in CU1b:** without the `HOME` move, exit 1 on `~/.cursor/projects`; with it, turn and resume work | ½ sitting (was 1: measured) |
| CW4 | **Chat and transcript handling in a Worker**: the CU3 state machine, per Worker and ticket, so a restarted Worker continues its chat or refuses. `rite sandbox pane` and the Worker's reporting must work for a non-Claude agent | ½ sitting |
| CW5 | **`worker-server` reaped** when the Worker's sandbox is stopped or destroyed. **Required, measured in CU1b:** it survives both `yoloai stop` and `yoloai destroy` | ½ sitting |
| CW6 | **Observed**: a Cursor Worker, started by the broker for a Manager, takes a real ticket to its publishing end state and releases its claim, and its `stream-json` shows `/review`'s reviewers running as `taskToolCall`s (D-CU-1) | 1 sitting |

**Linux:** Workers are unsandboxed there by default (v0.6.0), so a Cursor
Worker on Linux runs unsandboxed, the same as a Claude one. Stated, not
fixed here.

**The number for Robert:**

| | sittings |
|---|---|
| Managers only, after CU1 (CU2–CU6 below) | about **4–5** (creating a chat is gone, which saves 1–1.5 of the earlier estimate) |
| plus Workers (CW1–CW6) | about **5½–6½** more |
| **Cursor feature-equivalent, as ruled** | about **10–11** |

5b roughly **doubles** the track. Two things make it worth stating more than
the arithmetic: CW1 is shared infrastructure (it is also the Goose Worker
tier's missing half), and **the throughput case rests on CU6's cost
measurement**, which is not done. If Pro does not cover Workers on real
tickets, Cursor Workers are an on-demand-billing engine, and that belongs in
front of Robert before CW1 starts.

### Tickets

| # | item | done when OBSERVED | depends on | size |
|---|---|---|---|---|
| ~~CU1~~ | **Done, measured 2026-09-27** (`spikes/CU1-cursor-cli.md`, and `spikes/CU1b-cursor-workers-review-and-throughput.md`) | — | — | spent |
| CU2 | ✅ **BUILT, 2026-09-27** (#31 the pin, #33 the spelling). `engines.CURSOR`; `Spelling.handle_is_uuid` and `permission_unexpressed`; Claude and Goose unchanged across all 96 combinations of `launch_command`, pinned in `test_launch_command_is_pinned.py` before the change. Not reachable in production: config rejects `engine: cursor` until CU4 | Claude's and Goose's command lines unchanged (pinned, 96 of 96), and a Cursor turn and its continuation built by rite (`test_cursor_is_spelled_as_measured.py`) | — | spent |
| CU3 | ✅ **BUILT, 2026-09-27** (#34): `managers/cursor_chat.py` and its wiring in `supervise`. The UUID is recorded before launch, and `createdAtMs` is checked before and after every turn. A replaced chat marks the designation broken, and nothing runs again until `--fresh`. ⚠ **Observed through the real supervisor loop with a starter that does to the chat store what Cursor was measured to do, not yet with Cursor itself**, which needs CU4. Three mutations each turn their test red | A continuation whose chat directory was removed is REFUSED with the path named (observed, stub Cursor); a chat replaced mid-cycle stops the run and every later bare start (observed, stub Cursor); **with real Cursor: CU6** | CU2 | spent |
| CU4 | 🔴 **BUILT, AND ITS KEY ROUTE FAILS THE PROPERTY: WITH ROBERT.** Measured 2026-09-28 with a fake key (CU1b section 4): once in the engine's environment, the key is readable from the same Manager, a sibling Manager and a yoloAI Worker, via `sysctl(KERN_PROCARGS2)`. The route was built on a question that was skipped, not answered. The key is not being rotated (see above), so no authenticated measurement will use it. Built so far (it sat on argv during CU1b, so it is treated as compromised and put nowhere new). `managers/cursor_login.py`, and the following. **The key file:** a 0600 copy at `<credential dir>/cursor.key`, granted to NO profile and denied by name on seatbelt. **The route:** tmux's shell reads it OUTSIDE the boundary, into the engine's own environment (`CURSOR_API_KEY="$(cat <path>)" <boundary> agent ...`). That is the narrowest place there is: read from the CLI bundle, the main `agent` takes a credential only on argv or in the environment, `--auth-token-file` is `agent worker` only, and the stored login is the keychain. **The state:** a per-Manager `cursor/` directory as both `CURSOR_CONFIG_DIR` and `CURSOR_DATA_DIR`. `CURSOR_DATA_DIR` relocates `projects/`, read from the bundle, so `~/.cursor` is granted to nobody. **Grants:** `~/.local/share/cursor-agent` read-only, with its reason in `test_every_manager_grant_has_a_reason`; `agent` added to Landlock's engine binaries. ⚠ **Found:** with a long `CURSOR_DATA_DIR` Cursor puts its socket directory in `/tmp/.cursor/` (bundle, and measured), so Cursor depends on `/tmp` staying writable (SB8, SB11) | A sandboxed Cursor Manager authenticates (**held**). Observed with a fake key inside a real seatbelt profile: the copy can be neither read nor written, the state directory beside it is writable, and the prefix delivers the variable across the boundary, while without it nothing arrives. The key is absent from the command text, the pane's environment and `ALLOWED_ON_TMUX_ARGV`; a copy left by a killed Cursor run never reaches a Manager of another engine. **Held for the rotated key:** where the trust marker lands under `CURSOR_DATA_DIR`, a sandboxed turn and resume, and `ps` during a turn. Not measured on Linux | CU1 | 1 sitting, plus the held observation |
| CU5 | **`rite doctor` for Cursor** (R6). `agent status` cannot be it | With a bad key, the probe says so in Cursor's words and exits non-zero; with a good one, it passes | CU4 | ½–1 sitting |
| CU6 | **The both-halves observation, with cost**: two Manager cycles, the second recalling the first; a Worker through the broker; AND CU1 section 6's four cost measurements (requests per cycle, context growth, allowance per day, exhaustion) | As stated, recorded in the spike note's shape | CU3, CU4 | 1 sitting |
| CU7 | 🟡 **BUILT for Managers, 2026-09-28; one observation HELD.** No pid is looked up or killed. A Cursor pane's command ends with `cursor_login.REAP_SUFFIX`: the pane's shell stays alive as its process group's leader, and after the engine exits it ignores TERM itself, signals its own group and exits with the ENGINE's status (which `ending` reads). A live leader's group id cannot be recycled, so the signal cannot reach a stranger. **Measured with a stand-in** that spawns a child as Cursor's bundle spawns `worker-server` (`detached: false`), in a real tmux pane under `remain-on-exit`: the child is ended and status 3 is kept; without the suffix it survives (control). ⚠ **Found while writing it:** a child that does NOT ignore SIGHUP is already ended by the hangup the kernel sends when a pane's session leader exits, which is why CU1 saw `worker-server` end when the tmux session was killed. **Held (needs an authenticated turn):** that the real `worker-server` stays in the engine's group. **Workers:** a turn run through `yoloai exec` has no terminal and so no hangup; the same suffix applies there when CW1 is built | After a Cursor Manager's cycle, no `worker-server` for that workspace survives (held for the real one; observed for the stand-in) | CU2 | spent, plus the held observation |
| CU8 | 🟡 **BUILT on macOS, 2026-09-28; Linux is WITH ROBERT.** The supervisor writes `cli-config.json` fresh before every launch, from outside the boundary (`cursor_login.write_config`): rite's allowlist translated `Bash(x:*)` → `Shell(x)` (anything untranslatable refused, Claude's own tool names skipped by name), `approvalMode: allowlist`, attribution to Cursor OFF. The spelling passes `--disable-project-configs` (accepted; that it stops a project's `.cursor/cli.json` merging is held). After every cycle the supervisor compares the three keys it owns and STOPS the run on a change. **macOS, measured in the real profile:** with a deny on that one file, overwrite, rename-over, delete and chmod are refused, a write beside it succeeds, and Cursor still reaches authentication. **Linux:** Landlock cannot deny one file inside a granted directory, and the directory must be writable (Cursor keeps its chats there), so the Manager CAN rewrite it. The after-cycle check catches a lasting change but not one restored in time; that is detection, not a boundary, and it is said at every start. **Held (authenticated turn):** that Cursor enforces `Shell(x)` as Claude enforces `Bash(x:*)`, and whether it re-reads the file mid-run. **Found, for Robert:** Cursor's OWN git calls run with `core.hooksPath=/dev/null` (its git runner's hardened default; `repoConfigExecution` is not a config key, so rite cannot turn hooks back on), and its cloud-agent hand-off COMMITS and PUSHES (`push -u origin <branch>`) — probably through that runner, so past Robert's egress guard; whether the hand-off is reachable from a headless `-p` run is not known | On macOS, a Cursor Manager's refused command is refused by the allowlist rite wrote (**held**), and the Manager cannot change it (observed). On Linux: a decision | CU2, CU4 | spent, plus the held observation and Robert's Linux decision |
| CW1–CW6 | **Cursor Workers**, sized above (Robert's 5b) | CW6 | CW1 first; CU2 | 5½–6½ sittings |

### Open questions

- ~~CUQ1. Cursor for Workers?~~ **Answered by Robert: yes (5b).**
- ~~CUQ2. Which credential route?~~ **Answered by measurement: the API key
  (5a).** The key's route into a Manager, as a file, is still open (CU4).
- ~~CUQ3. What if `-p` takes the prompt only as an argument?~~ **Moot:
  measured, it reads stdin.**
- **CUQ4 (new). Does Pro cover Cursor Workers doing real tickets?** CU6
  answers it. It decides whether 5b's throughput case holds on a
  subscription, or means on-demand billing.

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
| SB5 | **`(allow mach-lookup)` with no filter.** Claude Code on macOS keeps its login in the keychain. `4ebbbd7` inferred that the login is found inside the boundary; *that was later measured false: a sandboxed Claude Manager reports `Not logged in` without its own token, which is why v0.6.0 gives it one (`_claude_login` in `cli/main.py`; readiness D3). Corrected at v0.6.0 release prep.* So the keychain item Claude uses is NOT shown reachable, and this row rests on the unfiltered rule alone. Not measured: whether a Manager can read **other projects'** rite credentials from it. The Worker-profile measurement (keychain content denied) does not carry over, because this profile differs | profile text | a measurement first. If reachable, per-service `mach-lookup` filtering, measured against the login |
| SB8 | **`/tmp` and `/private/tmp` are readable and writable**, and other rite worktrees and scratch directories live there | stated by `limitations()` itself, so disclosed rather than hidden | open, and not obviously closable: the engines need a temp space. The same measure-then-narrow method as SB4 |
| SB7 | **Which Manager may ask for which Worker.** A request names a declared Worker and a ticket. With several Managers in one root, any Manager can ask for any declared Worker. Whether Workers belong to a Manager is not decided (SBQ1) | `broker.py` validates against project-level declarations | MM, after SBQ1 |
| C25 | **No opt-out from the Manager's sandbox**, and an operator's own hook already fails inside it. Every narrowing in this track (SB4) raises the stakes | `V060_RELEASE_PLAN.md` C25 | a decision. See the consolidated list |
| C24 | **A requested Worker starts at the cycle boundary**, deliberately | `V060_RELEASE_PLAN.md` C24; `supervise.py::_honour_worker_requests` | a decision, not a fix. See the consolidated list |
| SB9 | **On Linux a Manager can replace the Claude login that authenticates it; on macOS it cannot.** Shipped in v0.6.0 as a documented PLATFORM DIFFERENCE, by Robert's default (2026-09-26: "if it isn't cheap, ship it documented and fix it in v0.7.0"). Why: Claude must write its whole config directory (`248ba5b`: listing its entries one by one broke Claude silently), and Landlock cannot deny one file inside a granted tree | measured 2026-09-26 on macOS in the real profile: **(a)** with `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`, the token read from an open descriptor, out of a 0600 file OUTSIDE `claude/`, signs Claude in and is not copied into the config directory. **(b)** But a local listener showed the order: the descriptor's token is sent FIRST, and on a 401 Claude FALLS BACK to a planted `.credentials.json`. So the move is cheap (about one sitting) and restores only "rite's token wins while it is valid", which is why v0.6.0 ships the wording instead | the login held where the Manager cannot write, AND nothing in a writable directory that Claude will fall back to: either Claude stops falling back (upstream), or the config directory itself is not Manager-writable (which needs SB10's answer), or the credential broker, so that no token is held in the sandbox at all |
| SB10 | **A Manager can plant settings for its own later runs, on BOTH platforms** (v0.6.0 readiness W11). `claude/settings.json` is loaded as USER settings: a planted `env.ANTHROPIC_BASE_URL` redirected the next run's model requests (2 of 2, macOS). It persists across runs. The project's `.claude/settings.json` is Manager-writable too | measured 2026-09-26; `--setting-sources project,local` stopped the user-settings path (0 requests) | exclude user settings, and make the project's `.claude/` read-only to the Manager (a seatbelt deny; on Landlock by listing the project root's entries, as the inbox fence does), or move user widening into rite's own settings file. A decision, then observe both platforms |
| SB11 | **On Linux, the Manager's policy grants `/tmp` and `/var/tmp` read and write** (v0.7.0 by Robert's scope, 2026-09-26: "the `/tmp` grant fix"). Landlock has no deny rule and unions its grants, so for a project living under `/tmp` the wholesale grant overrode the enumeration that fences one Manager from another; the credential narrowing was defeated too (`spikes/MM2b-moving-the-inbox-out-of-the-project.md`). Linux only: seatbelt denies after granting, and the last match wins. The inbox half is gone since the mailbox left the tree (`935ceef`); the MM2b note records the rest as "a separate hole" | `b542c15`'s own message, and the MM2b note, which measured it | **`b542c15`** on `fix/landlock-no-wholesale-temp`, prepared and **not merged**: it removes both grants and keeps the engine's own `TMPDIR`. ⚠ Its message says what is not established: anything that hardcodes `/tmp` breaks, and a grep found nothing on the Manager path, which is not evidence for an engine and its subprocesses. So it needs an observed Manager cycle on Linux before it lands. **Status 2026-09-28: landed WITHOUT that cycle; the cycle is DEFERRED and is now a v0.7.0 tag blocker.** Robert's ruling of 2026-09-28 was "defer": the Ubuntu VM was suspended by the dogfood run to free memory for a 17 GB model and must not be resumed under it, and his Claude allowance is not spent on this now. A macOS cycle cannot stand in, because this change touches only the Landlock policy. Landed anyway because it is unreleased and fails closed: an engine that hardcodes `/tmp` gets a permission error rather than a hole. CI (x86_64, kernel 6.17, Landlock ABI 7) ran all 20 Landlock probes against the real policy, and caught two tests that had passed only through the grant. **What the deferred cycle must still observe:** on Linux, from `main`, one Manager cycle per engine (Claude; Goose; Cursor, whose Linux grants landed in #41 and are also "not measured on Linux") in a project that is NOT under `/tmp`, completing a turn that runs shell commands, with no permission error from `/tmp` or `/var/tmp` in the pane or the journal. Claude is the engine the risk matters most for, and running it spends Robert's allowance, so it needs his go-ahead. Not observed yet: whether Claude Code, Goose or their subprocesses write under `/tmp` rather than `$TMPDIR` ⚠ **A Cursor Manager will hit this (CU4):** with a long `CURSOR_DATA_DIR`, Cursor puts its socket directory in `/tmp/.cursor/`, so on Linux a Cursor Manager's launch will be refused there until CU4 either grants that one path or finds a data path under 84 characters. Fails closed. |
| SB12 | 🔴 **A running process's ENVIRONMENT is readable from every sandbox on the machine, and that includes every credential rite hands a Worker by `--env`, on shipped v0.6.0** (`spikes/SB12-environment-readable-across-sandboxes.md`, measured 2026-09-28 with fake credentials). `sysctl(KERN_PROCARGS2)` from inside another Worker's yoloAI sandbox, and from inside a rite Manager's sandbox, read a Worker's `GITHUB_TOKEN` and `JIRA_API_TOKEN` from the environment of yoloAI's tmux server and session in that Worker. The secret FILES were refused. No rite seatbelt change tried stopped the read (six variants, including an explicit sysctl allowlist without `kern.procargs2`), and a Worker's profile is yoloAI's, not rite's. The same read is what breaks CU4's key route | measured, macOS; Linux and a real `--agent claude` Worker not measured | **With Robert; not a window to narrow.** ✅ **Measured since (SB12 note, section 4): a process owned by a DIFFERENT user is refused by the kernel (errno 22), from outside and from both sandboxes; so separating agents by OS user is the one thing found that closes the read** (targets were root-owned; between two ordinary users it is inferred; a user PER agent is needed for Worker-from-Worker; how rite runs an agent as another user is undesigned). Other directions, not measured: credentials a Worker needs delivered as files the agent reads, not the environment, where the engine supports it; process separation that `KERN_PROCARGS2` respects (a different user); fewer credentials per Worker (§5.3.4's fungibility trade, revisited) |

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
| The scheduler lock admits overlapping ticks (8 processes, 20 s, Linux: 1,352–1,617 overlaps and about 500 false reclaims). The reader crash it caused is fixed (#27); the lock is a 0.6.0 known issue. Done when the handover's own bar is met: 0 overlaps and 0 false reclaims under that stress. **Status 2026-09-27: met.** The lock is now a kernel-held `flock` on a file that is never deleted, with a self-test that refuses to run where `flock` does not exclude. Same harness, three runs each: Ubuntu VM, old 618–665 overlaps and 612–692 false reclaims, new 0 and 0; macOS, old 0–1 and 85–95, new 0 and 0. The Linux figure is from the Parallels VM (aarch64, kernel 7.0, 4 vCPU), not the Docker container that produced the 1,352–1,617, because Docker is stopped to save memory. The guest was idle when the runs started (load 0.00); the Mac host underneath was not quiet. The old code was re-measured in the VM so that pair is comparable; it is not like-for-like with the container's figure, whose load was not recorded. The macOS pair was taken on a loaded Mac (load 3.7 rising to 8.5, three other sessions running), and is recorded as that. Controls on both show the harness catches a broken lock | handover 2026-09-27, item 7 | handover 2026-09-27, item 7 |
| **The loop lock admitted overlapping loops and trusted any pid** (`loop/session.py`, a pid file since L-5). **Status 2026-09-28: fixed.** It is now a kernel `flock` held by the loop process for its life, plus a short gate lock that everyone who takes, releases or asks about it goes through. The gate exists because a reader can only ask "is it held" by trying to take it, and without the gate that try made a starting loop fail. Measured on macOS, this Mac, same harness as the tick lock (8 processes, 20 s, 3 runs), main `4f7e1d2`: **old 51,120–52,154 overlapping holders** of about 58,000–59,600 acquisitions; **new 0** of 3,820–3,971, 0 refusals. Load 2.5→6.6 (old) and 3.7→4.2 (new), 85% memory free, other sessions running. Controls: a no-op `flock` with the self-test removed gave 40,606 overlaps in 5 s; with the self-test kept, every attempt was refused. **Readers vs a lone loop** (1 loop starting and stopping, 6 readers asking): with the gate, 0 false refusals in about 24,000–25,000 starts per run, 0 unknowns, every "running" naming the loop's pid; gate removed, 220,511–222,523 false refusals per run. **Identity:** a zombie (`ps` stat Z) or a live `sleep` recorded as the holder refused every start on main; now both read as not running. ⚠ **No Linux figure**: the VM is suspended for the dogfood run and Docker is stopped, so this is macOS-only, and no macOS number here is comparable to a Linux one | L-5; Robert, 2026-09-28 |
| **A Worker's claims were handed over by every caller that raced for them** (`perform_handover`: the tick at a window boundary, `rite stop`, `rite stop --ticket`). **Status 2026-09-28: fixed for every caller on one machine** (#44, then the named-ticket half). The claims are read and released in one locked step, and only the process that released them posts the handover. Measured on macOS, this Mac under load (5.0–5.9), 50 rounds of 6 processes released together against one Worker: derived ticket, main `a75469d` 6 handovers a round (300), after 1 (50); named ticket, main `4f7e1d2` 6 a round (300), after 1 (50). ⚠ **Behaviour change:** `rite stop --ticket X` for a Worker holding no claims no longer posts a handover. Inside the lock, "never had any" and "another handover took them" are the same observation, so `stop` names both and says to comment by hand. ⚠ **Not covered, filed as v0.8.0 LS2:** takeover releases nothing locally by design, so its exclusivity rests on the Owner lease | 0.6.0 known issue ("post the handover comment on a ticket twice"); Robert, 2026-09-28 |
| **Two projects on one Slack app shared the Owner's DM** (SPEC §9.16.6, D-101 was advice only). Seen in the v0.6.0 dogfood run, when a project reused another's bot token. Each took the other's DMs as INSTRUCTIONS and read the other's conversation, and a stopped project's DMs reached the other at its next start (A5). **Status 2026-09-28: refused on one machine** (`managers/slack_app.py`). An app, by the workspace and bot user `auth.test` reports, is bound to the first project that opens a relay on it, persistently; any other project is refused before its listener exists; an unidentifiable app does not open. Measured on macOS, this Mac, main `f9c98a8`, through `_slack_listener` → `Listener.open` → `poll` against one fake workspace: before, both projects took 10 of 10 DMs as INSTRUCTION, and the later project took all 10 of the stopped one's; after, 0 and 0, with the bound project still taking its 10. Race (6 projects binding one app, 200 rounds, load 1.3–1.6, 86% free): `os.link` 1 winner every round; a check-then-write control bound 5–6 every round. Mutation: the binding removed → 9 tests red; check-then-write → the race test red. ⚠ Not covered: two MACHINES on one app (v0.8.0). This is the confidentiality half of channel separation, not RP1 | the dogfood run; Robert's scope ("channel separation"), 2026-09-28 |
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
- **SB11's observed Manager cycle on Linux is owed before the v0.7.0 tag.** The code landed without it on 2026-09-28 (deferred by Robert: the VM is suspended for the dogfood run, and the Claude cycle spends his allowance). What it must show is in the SB11 row.
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
