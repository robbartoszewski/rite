# The v0.7.0 release-candidate dogfood: setup, pre-registered bar, limits

**Status:** written 2026-09-29, before the run, against `main` `f57f645`. The
bar below is fixed now so the run cannot be graded on whatever went well.
Changing it after the run starts needs Robert's word and a dated note
beside the change.

**What the run is** (Robert, 2026-09-28): he contributes a real fix to yoloAI,
through rite, on a fork he has created, and the diff is read by yoloAI's
maintainer. It is a **tag blocker for v0.7.0**. A second run, on a throwaway
app in the v0.6.0 dogfood's shape, follows it.

**Plan row numbers:** DF9–DF15 are on `main` (DF14 and DF15 by #103).

**Where the findings come from:** the v0.6.0 dogfood (2026-09-27/28, macOS,
`pingr`, Jira `KAN`, a Claude Owner and a Goose secondary, driven in a
deliberately lazy register). Each finding there carried a "fixed when" test,
written so a re-run could not pass by being driven more carefully. Plan rows
DF9–DF15 are the ones filed from it; the rest are listed in part 2 by name.

---

## 1. Setup the run must meet, or it tests the wrong thing

### 1.1 The release candidate is what runs INSIDE every sandbox

⚠ **This is the one that fails silently.** A Worker or Manager runs `rite`
inside its sandbox, and that is not necessarily the `rite` that started it.
rite strips its own virtualenv's `bin` from a sandbox's PATH, because seatbelt
cannot read it (`sandbox.sandbox_environment`); so inside, `rite` is whatever
is next on PATH, normally the machine-wide `~/.local/bin/rite`. The README
says the same: a `rite` installed anywhere else cannot run inside a sandbox.
So a run started from a checkout of the candidate tests the candidate on the
host and **the machine-wide release inside**, and nothing says so. Another
session's live check observed exactly that: a Worker ran released 0.6.0
inside while the host ran a development commit.

Required, in this order, and recorded in the run log:

1. The candidate is installed the way a user installs it: the released
   `install.sh` with `RITE_VERSION=<rc tag>`, into `~/.local`, not a checkout
   and not `uvx --from`. This replaces the machine-wide `rite` for **every
   session on this Mac**; say so to the other sessions first
   (the v0.6.0 dogfood replaced one without warning: finding E4).
2. On the host: `rite --version` prints the candidate's version **and** the
   tag and commit it was installed from.
3. **Inside a sandbox**, before any ticket is worked: `yoloai exec <sandbox>
   rite --version` for the first Worker, and the same from inside the Owner
   Manager's first session (it can run `rite --version`). Both must print the
   candidate's commit. A mismatch voids the run.

### 1.2 A layout that keeps rite's files out of the contribution

- **A separate rite root** (e.g. `~/AI/yoloai-rite/`), with the fork
  registered as a module by its GitHub URL. Workers clone the fork into their
  own workspaces and push branches to it. rite's own files (`.rite/`,
  `CLAUDE.md`, `.claude/`, the gate workflow) never enter the fork.
- **Not** `rite init` inside the fork's own clone: that commits rite's files
  into the repository the upstream PR is made from, and a PR from a fork
  branch carries every commit upstream does not have. (#76 and #96 changed
  what init does inside a repository; the separate root avoids the question.)
- Before the first ticket: `git log upstream/main..origin/main` on the fork is
  empty, so any later difference is the Worker's work.

### 1.3 Credentials and access, each checked before starting

- A **fine-grained GitHub token scoped to the fork only**, with Contents and
  Pull requests read/write, stored for the project with `rite credential set
  github`. **Not a classic token**: it would skip step 10 of part 4 and also
  give every sandbox every repository Robert can write to (#85). `rite doctor`
  shows no GitHub-token problem for any Worker (#77, #86).
- `claude setup-token`, stored with `rite credential set claude_token`, for
  the Owner and for sandboxed Workers.
- **A Slack app of this project's own**, not one shared with another project.
  Since the v0.6.0 dogfood a second project on one app is refused (the
  "one Slack app, one project" fix); the run must not work around that.
- `rite doctor` exits 0, or every problem it lists is written into the run
  log with why it is acceptable.

### 1.4 The board, decided before the run

- **GitHub Issues on the fork, or Jira: Robert's choice, and it decides what
  the run can show.** The v0.6.0 dogfood's headline (F11) was that no Manager
  can read a Jira board, because a Manager is given no Jira credential. #97
  now delivers a Worker's ticket from the host, but the Owner Manager's own
  board reads on Jira are, as of `f57f645`, still what F11 described (read
  from `managers/`, where nothing hands a Manager a Jira credential; not
  re-run). A Jira run exercises that; a GitHub run does not, and says so in
  its results.
- Tickets are filed the way a user files them (`rite board create`, one line),
  and **not labelled by hand**: the dogfood's workaround for F4 is not
  repeated.

### 1.5 The machine

- Record, at the start and at each timing: free memory and **swap** (`sysctl
  vm.swapusage`), the number of other sessions running, and any VM state.
  (On 2026-09-29, swap stood at 34 GB of 34.8 with thirteen sessions.)
- A local model is loaded only with the Parallels VM stopped (Robert's rule).
- A run killed part way is **void, not partial**; it is repeated, not graded.

---

## 2. The pre-registered bar

Each line is what the run must **show**, observed in its log, pane, Slack or
on the board, not inferred from a component. "Exercised" says whether the
yoloAI run can show it at all; a line it cannot exercise is carried to the
throwaway-app run or listed in part 3.

### 2.1 The acceptance criterion: refinement before work (DF8)

Robert, verbatim: "I expect rite to refine the lazy tickets before proceeding
- ask user questions, define a definition of done etc."

- ✅ **Preconditions: met, 2026-09-29.** Corrected against `main` by what
  each item requires (the earlier list, and TR6's old dependency row, named
  TR7 and more than the run needs).
  - **TR2, the round protocol, the gate:** landed in three slices, #117
    (limits, round ledger, round check, the `refining` and
    `waiting-on-user` verdicts), #119 (`rite refine ask`, attribution,
    accept on a word, silence read not assumed, chat instructions filed
    unrefined) and #120 (the Owner's refinement brief, the private channel,
    Robert's correction to TRQ2 and his escalation ladder). Without it the
    only way to an agreed definition of done is Robert typing `rite refine
    accept` at his own terminal, which is not what this criterion tests.
  - **TR5, the route and assignment refusal:** #114, with #121.
  - **TR3, the instructions and the spec:** #73, #108, #115, #122.
  - **Earlier:** the design (#43), the record and predicate (TR1, #83), a
    Worker handed the checked record (TR4, #106), TR9 (#78/#81/#84), and
    TR8's G2 (#110).
  - **Not preconditions:** TR7 (a filter view nothing decides from), TR8
    beyond G2, TR10 (after PB1).
  - **Owed, not a precondition:** SPEC §9.16.5's amendment for the
    refinement channel's authority (TR3's text). It matters only if the run
    sets `refinement.questions_to: channel`.
- **Already seen once with Robert** (`V070_TR2_LIVE_EXCHANGE.md`, one
  sitting, a Claude Owner): rounds shaped as designed, replies matched by
  thread, an inline "Definition of done: ok" correctly not taken as a yes,
  silence read past its deadline and not re-asked. **Not reached there, so
  this run must:** a bare `ok` writing a record; a chat instruction filed as
  an unrefined chore; three unanswered messages parking a ticket; the same
  question coming back when he is next active; an unchanged proposal said,
  and a third one escalated as a blocker; a local-model Owner; the private
  channel, if configured.
- For each ticket Robert files in the dogfood's register (one line, no
  acceptance criteria):
  - Before any routing, Worker request or code, a question about **what the
    ticket means** reaches Robert in Slack. Asking for a ticket id, or for
    scope because a board cannot be read, does not count.
  - A definition of done ends up **on the ticket** (description or comment),
    agreed from Robert's reply, before work starts.
  - Robert answers at least one question partially or tersely. The ticket
    still reaches a definition of done by rite's protocol (a proposal he can
    accept in a word), or is PARKED or BLOCKED and says so; it is never
    worked on a guess. While he is answering there is no limit on rounds
    (his correction to TRQ2); three messages in a row with no reply park it.
- The v0.6.0 failure it must not repeat: KAN-8 "output is ugly, table?" was
  implemented and committed, and only then did the Owner ask whether the JSON
  output mattered for cron.

### 2.2 A Worker does real work, end to end (item 3; F2, #28)

- Exercised: **yes**. This run's whole point.
- A Worker started by rite on a refined ticket has the fork cloned in its
  workspace, commits, pushes a branch to the fork, and a pull request **within
  the fork** is opened. Nothing is set by hand beyond part 1.
- `rite release` runs after the merge, and `rite status` shows no claims.
- The diff contains only the fix: no rite files, no home paths, nothing from
  another ticket.

### 2.3 A Worker's question is heard (DF10, #68/#70)

- Exercised: **likely**, if any ticket is ambiguous enough for a Worker to ask.
- A question a Worker writes (yoloAI's `files/question.json`) reaches
  Robert's Slack DM within one poll, labelled "needs your answer", with a
  first line naming the ticket and question id; `rite status` shows the
  Worker as waiting on a question; `rite sandbox destroy` refuses while it is
  unanswered. The answer goes back by attaching (the agreed manual path).
- Not a pass: a Worker that stops silently, or a status view that says
  `idle` or `not started` for a Worker that is waiting.

### 2.4 Status says what is true (DF14/S1, #101)

- Exercised: **yes**.
- At every point a Worker exists, `rite status`, `rite sandbox status` and
  `rite loop run` agree about it, and none says "not started" for a Worker
  whose sandbox ran. The Owner's reports to Robert about a Worker match those
  views; the dogfood Owner reported "no progress" off a wrong proxy.

### 2.5 The Owner is not started again and again for nothing (DF9/F22, #62)

- Exercised: **if** the Owner has an idle stretch (waiting on Robert, or on
  a Worker).
- After the last session that changed anything, at most one more session
  starts until mail arrives or the board changes. Measured from the run log:
  sessions started per minute after the last useful one. The dogfood's figure
  was 8 in 80 seconds.

### 2.6 A Manager hears the outcome of what it asked for (DF13, #98)

- Exercised: **yes**, at the first Worker request.
- The Owner's next instruction carries "Started: …" or "NOT started: …" for
  every Worker it requested, and its report to Robert matches. The dogfood
  Owner told Robert it had "no further visibility" in the minute rite started
  its Worker.

### 2.7 The verifier does not call a true report a lie (DF15/V1–V2, #102)

- Exercised: **only if** a secondary Manager replies to routed work.
- No CONTRADICTED verdict about a file the verifier could not read.

### 2.8 First five minutes (F1, F2; #72, #76, #96)

- Exercised: **yes**, during setup.
- `rite init` in the separate root, `rite add module` for the fork, and the
  first `git push` of the root succeed with the gate active and no
  suppression added by hand. Anything that needed a workaround is a finding,
  recorded with what was done, as the v0.6.0 dogfood did.

### 2.9 Carried findings, not known to be fixed

These were found in the v0.6.0 dogfood and are **not checked here** against
`main`. The run records whether each still occurs; none is a pass condition
unless Robert makes it one before the run.

- F4: a Manager only sees tickets labelled `scheduled`, and nothing says so.
- F5: a fresh project has no schedule, so a Manager stops at once.
- F3, F6, F7, F9: Jira configuration undocumented; credential key names
  inconsistent; stale keychain wording; the GitHub App requirement for a
  Manager undocumented.
- F8: doctor counts problems for the documented one-machine setup.
- F10/M4: generated files stale after a config edit, without `rite update`.
- F16/F17: a route waits behind the secondary's first session; the executor
  gets Owner instructions.
- F18–F20: Ctrl-C wording contradicts itself; refusals reprinted every cycle
  (DF6); `rite loop --help` stale.
- F14: backticks in a Manager's `rite reply` ran commands.
- #24: a bare start resumes a conversation already at the context limit.
- #25: code done, board unchanged, and nothing says the board is stale.
- #27: a Worker's sandbox loads the user's own Claude hooks.
- #34: sandboxes outlive their purpose with credentials in them.

---

## 3. What this run cannot cover

A pass is a pass for **this** configuration. It says nothing about:

- **Several repositories.** One fork, one module. Module routing, claims
  across modules and cross-repo tickets are untested.
- **Other boards.** Whichever board is chosen, the other is untested. If it is
  GitHub, the Jira Manager path (F11) stays exactly as the v0.6.0 dogfood left
  it.
- **Lazy input from someone who does not know rite.** Robert knows it
  intimately; he can write lazy tickets but cannot forget what rite needs.
  The throwaway-app run, driven in the dogfood's register, is the closer test.
- **A local model doing real work.** In the v0.6.0 dogfood the Goose
  secondary produced no code (it ran out of context). Unless the run routes
  real work to a local model, "several Managers" is exercised only as far as
  the Owner.
- **Linux.** macOS only. Linux has its own recorded Manager cycles (SB11) and
  its own known limits (Workers unsandboxed by default).
- **Several machines, Owner failover.** One machine, one root.
- **Long runs, check-ins and the standup.** One day. Check-in windows,
  deferred questions and a morning standup are not exercised unless
  configured and waited for.
- **The answer path back into a Worker.** Answers go by attaching. A Worker
  reading a host-written `answer.json` has not been measured by anyone.
- **Unattended operation.** Robert drives and watches. Anything that only
  fails with nobody there is not tested.
- **Whether a Worker's pull request says what is true.** Nothing in rite
  checks the claims in a Worker's PR description or commit messages: the
  verifier reads only replies from secondary Managers. A confidently wrong
  "tests pass" or "fixes #N" reaches the maintainer unless Robert checks the
  description against the diff in §4 step 9, and a claim he catches there is
  caught by him, not by rite.
- **Other maintainers.** One external reader of one diff. Their judgement is
  evidence about that diff, not about rite's review process in general.
- **Cost.** Sessions and allowance used are recorded, not judged: there is no
  budget bar.

---

## 4. What Robert does himself, in order

1. **Confirm the preconditions:** TR2 and TR5 are merged (2.1); the release
   candidate tagged; the
   board chosen (1.4).
2. **Tell the other sessions** that `~/.local/bin/rite` is about to become the
   candidate, then install it with the released `install.sh` and
   `RITE_VERSION=<rc tag>` (1.1, steps 1–2).
3. **Create the separate rite root** and run `rite init` there; `rite add
   module <name> <fork URL>`. Push the root once, with the gate active (2.8).
4. **Credentials:** a fine-grained token for the fork only (Contents, Pull
   requests); `claude setup-token`; a Slack app of this project's own; `rite
   doctor` clean or its problems logged (1.3).
5. **Check the version inside a sandbox** (1.1, step 3) before any ticket.
6. **Record the machine:** memory, swap, sessions (1.5).
7. **File the tickets** in the dogfood's register: one line, no acceptance
   criteria, not labelled by hand (1.4).
8. **Start the Owner** (`rite start <owner> --sessions N --minutes M
   --record-issues`) and answer only in Slack, briefly, as in the dogfood.
9. **Review the Worker's diff** on the pull request **within the fork**.
10. **Open the upstream pull request himself.** A fine-grained token cannot
    open a pull request on a repository its owner is not a member of, so this
    step is structurally his (#85): no Worker or Manager can send work to the
    maintainer without his review. A classic token would remove that, and is
    not used (1.3).
11. **Record the maintainer's response** as it comes, and whether the diff
    needed changes a refined definition of done should have caught.
12. **Write the results against part 2**, line by line, with evidence, and
    part 3's limits restated in the summary, before anyone reads the pass as
    broader than it is.
