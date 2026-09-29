# The scenario gate (§7.3, D-81): the design pass, and a size

**Status: DESIGN, 2026-09-29, against `main` `6c97075`. Nothing here is
built.** This note is the pass the v0.7.0 plan requires before the gate can
be sized ("needs its own design pass before a size"). It answers four
questions: §9.15.3a's recorded gap, how the gate composes with refinement,
what the gate is worth, and how it relates to the release-candidate dogfood.
It ends with a size and a recommendation on when to build.

**The short version.**

- **§7.3 as written cannot be built on the project layout rite now
  recommends.** Its evidence lives in `.rite/scenarios/` on the
  implementation branch. The release-candidate layout keeps rite's files out
  of the module's repository altogether (`V070_RC_DOGFOOD.md` 1.2, bar 2.2),
  and the rite root ignores `.rite/*` apart from a fixed list. Its ordering
  check, `git merge-base`, measures the order of commits, not the order of
  writing. That is a proxy, and a rebase moves it.
- **The signed refinement record is the right source,** and it is stronger
  than the ordering check it replaces. It was agreed with the User, it is
  signed with a key no sandbox can read, and it existed before the Worker
  did: TR4 refuses to start a Worker without one. It turns "derived from the
  requirements" from something a reviewer agrees with into something a
  program can compare. **That holds for the record's `verify` commands, not
  for its prose items**, and `verify` may be `"none agreed"` (TRQ9).
- **The gate has to run the commands itself, on the host, against the head
  commit.** A log the Worker writes is a claim about a run, just as a pasted
  transcript is.
- **It would have caught one of the three recent defects** (the 241-test
  disconnection), **not the CI job that ran six files**, and the editor
  corruption only if the agreed definition of done had named the content
  that triggered it.
- **It complements the release-candidate run. It does not duplicate it, and
  it needs the run's findings first.** Build it after the release candidate:
  about 2½ sittings, plus ½ once PB1 exists.

---

## 1. §9.15.3a: the gate cannot reach the journal. What that costs

**Stated plainly: the gate must not read the journal, whether or not it
could reach it.** §9.15.5 says nothing in rite reads the journal, because "a
file that triggers behaviour is a control channel". A merge gate that passed
or refused on journal entries would be exactly that. If the entries were
committed tomorrow, the gate should still ignore them. So the recorded gap
is not a precondition to close before the gate is built. It is a limit on
what the gate can know about itself.

**What the gate can verify without the journal:**

- that a REFINED record exists for the ticket, and that it is the current
  head (not STALE, not CONFLICT). This is one board read through
  `refinement.status.of`, the only path from a ticket id to a state;
- that each agreed `verify` command ran, byte for byte as signed, against
  the branch's head commit, with an exit code rite read itself;
- that the result it recorded is the one being presented at merge time. The
  evidence names the head SHA and the record id, and carries rite's MAC, so
  a result from an older head or an older agreement does not count.

**What it cannot verify, which is the gap's real cost:**

- **Whether the gate is working.** §9.15.4 names the journal entry "a bug was
  not caught during testing" as the evidence that says whether the scenario
  gate works. That judgement stays with a person reading a directory someone
  zipped. The gate reports what it ran. It cannot report what it missed.
- **Whether a Manager's account of the work matches the run.** A journal
  entry saying "Worker verified item 3 manually" is not evidence the gate
  can see, which is correct: it is not evidence.

**The route to efficacy data that does not go through the journal,** and
needs no new mechanism: a defect ticket filed after merge that cites the
merged ticket is on the board and committed history, not machine-local. "How
many merged tickets later had a defect that their agreed commands passed
through" can be answered from the board and `git log`. Proposed as a
retrospective query for a person, not as anything the gate does.

**The same gap applies to the gate's own evidence, and §7.3 does not
notice.** `.rite/scenarios/<phase>/run-<timestamp>.log` sits under `.rite/*`,
which the rite root's `.gitignore` excludes except for `AUTHORED_CONFIG`
(`cli/init/scaffold.py`). In the rite root, that log is as machine-local as
the journal. In the module's repository, where §7.3 puts the branch, it is a
rite file in the contribution, which RC bar 2.2 forbids. Section 3.3 below
puts the evidence where it can be read.

## 2. Composing with refinement: is the agreed record the source?

**Yes. It replaces the ordering condition rather than adding to it, and it
moves the independence condition off the single-operator case.** There are
three limits, and each has to be stated.

### 2.1 What the record already guarantees, against §7.3's three conditions

| §7.3 condition | as §7.3 checks it | what the record already gives | verdict |
|---|---|---|---|
| **1. Independence, by order** | scenario file committed before the branch, checked with `git merge-base` | the record is signed (HMAC, key unreadable from any sandbox, TR0), bound to the ticket's title and description hashes, and dated in its signed `provenance.at`. TR4 refuses to start a Worker without it, so it **predates the Worker by construction**, which is the host's event and not a commit date | **the record is stronger.** A merge-base measures where a branch was forked. A rebase onto a later `main` moves it, and the scenarios could have been written after the code and landed first. The record cannot be written by the implementer at all |
| **1a. Independence, by author** (`independent: false` for one operator) | recorded, not enforced | under TR2 the **Owner** proposes the definition of done and the **User** accepts it. The Worker neither proposes nor accepts it. Under `rite refine accept` it is attested by the person's own session | **the degradation moves.** The one-operator case no longer means author = implementer. What degrades now is `attested` against `accepted`, which the record already carries and names |
| **2. Machine-captured runs** | a log file exists, names every scenario, carries exit codes | nothing yet: this is the gate's own work | **built by the gate** (3.2) |
| **3. Depth declared** (`cli`/`service`/`ui` + a count) | plan declares; gate checks tier against modules | nothing: the record has no tier | **drop it from the gate.** See 2.3 |

### 2.2 The three limits

1. **Only `verify` is executable. The items are prose.** A record's
   `definition_of_done` is a list of sentences, and `verify` is a separate
   flat list of commands, **not linked to the items**. So the gate can
   check "every agreed command passed". It cannot check "every item has a
   passing check". An item with no command behind it is verified by the
   Worker's report, which is an attestation. The gate reports it as such:
   *"3 items, 2 agreed commands, 2 passed. Items are not linked to
   commands; the record does not say which item each command checks."*
   Linking them is a record schema change (v2) inside TR2's proposal format.
   It is filed as a question (SGQ2), not assumed.

2. **`verify` may be `"none agreed"`** (TRQ9, decided: "optional but
   explicit"). Then the gate has nothing to run. It must not pass silently,
   and it must not refuse, because that would reverse TRQ9 by the back door.
   It degrades, visibly and in the merge evidence: *"no commands were agreed
   for this ticket: nothing was run; the evidence is the Worker's report."*
   This is §7.3's own rule ("a skipped condition that announces itself") in
   the place it now applies. Whether a gate-enabled project should require
   commands is **SGQ1**, and it is Robert's call.

3. **An agreed command can be circular.** `verify: pytest tests/test_x.py`,
   agreed before the code existed, passes just as well when the Worker has
   written `test_x.py` to assert nothing. This is the recurring defect class
   (a check that measures a proxy) arriving through the front door, and
   refinement cannot remove it, because the User agreed a command, not its
   contents. **What the gate can do mechanically:** for each command, name
   any path in its argv that the branch added or changed (`git diff
   --name-only <merge-base>..<head>` against the argv tokens). It flags
   *"runs a file this branch wrote: its pass is the implementer's claim"*
   rather than presenting it as independent. The check is cheap and
   one-sided: it misses a command that reaches branch-written code
   indirectly (a bare `pytest`). The flag says what it matched, not
   "independent" when nothing matched.

### 2.3 Why the depth tier comes out

§7.3's tier check refuses a project with a frontend module that declares
`cli`, "so depth cannot be downgraded by classifying it away". That is a
declaration checked against a declaration. Declaring `ui` does not make the
commands exercise a UI, so the check measures the label rather than the
depth. The record has no tier, and adding one gives the Owner a field to
fill in that nothing can check. **The honest replacement is the commands
themselves:** the reviewer reads the agreed commands in the evidence and can
see whether any of them drives a browser. "Scenario count" becomes "the
number of agreed commands", which the record already carries. §7.3's
warning that a UI green is not a CLI green stays as written. It is advice,
and it was always going to be.

### 2.4 Keyed by ticket, not by phase

§7.3 says "per phase rather than per ticket", because it came from rite's own
development, which works in phases. A project rite manages has no phase
object. It has tickets, records, Worker branches and (after PB1) a publish
step per task. The gate is keyed by **ticket**. A person who wants a
phase-level gate runs it over the tickets in the phase, and nothing in the
design prevents that.

## 3. The design

### 3.1 Who runs the scenarios

§7.3 says "a Worker runs test scenarios". **Changed: rite runs them on the
host, in a fresh sandbox, and reads each exit code from its own process.**
A Worker that runs the checks and writes the log is presenting a claim about
a result, which is what §7.3 condition 2 exists to refuse. The Worker's
sandbox also holds the Worker's working tree, not the pushed head.

### 3.2 `rite scenarios run <ticket> --ref <branch>`

1. **One read** through `refinement.status.of`. Anything but REFINED
   refuses, in `refused_for_refinement`'s words: the gate gives no second
   answer to "is this ticket refined". The run is bound to that read's
   record id.
2. Resolve `<branch>` on the module's remote to a **SHA** once, and use only
   the SHA from then on (the standing rule: a green counts only against its
   head SHA).
3. Check that SHA out into a **fresh sandbox with no credentials**. This is
   not the Worker's own sandbox. The commands run code the branch wrote, so
   they never run on the host.
4. Run each agreed command **verbatim from the record rite just read**,
   never from `TICKET.md`, which the Worker can edit. rite captures stdout,
   stderr and the exit code from the sandbox exec on the host side, with a
   per-command timeout. A timeout is recorded as `timeout`, never as a
   failure code or a pass.
5. Write the evidence (3.3).

### 3.3 Where the evidence lives

- **The full log goes in the rite root**, at
  `.rite/scenarios/<ticket>/<sha>-<timestamp>.log`, with `.rite/scenarios/`
  added to `AUTHORED_CONFIG` so the root commits it. The publish gate scans
  it like everything else it commits. Command output can contain secrets,
  so that scan is required, not optional.
- **A signed result goes on the ticket as a board comment**, the same shape
  as the refinement record, under its own `kind` so that neither can be
  passed off as the other. It carries the ticket, the record id, the SHA,
  each command with its exit code or `timeout` and its "runs a
  branch-written file" flag, the log's sha256, and rite's MAC with the
  existing key. A Worker cannot write one, because it cannot read the key.
  **This is what turns condition 2 from presence into provenance.** A
  result rite signed, for this SHA, under this record, cannot have been
  pasted. Fidelity is still open: a correctly captured run of the wrong
  command is still the wrong command, and §7.3's warning stays.
- **Nothing goes in the module's repository.** The contribution stays clean
  (RC bar 2.2).

### 3.4 `rite scenarios check <ticket> --sha <head>`

It exits 0 only when all four of these hold:

- a signed result for exactly this SHA verifies;
- its record id is the ticket's **current** head record. An agreement that
  changed after the run is a new agreement, and the run does not cover it;
- every agreed command in it has exit code 0;
- the degradations are printed: `none agreed`, items not linked to
  commands, `attested` rather than `accepted`, and commands flagged as
  running branch-written files. Each is its own line. None of them turns a
  failure into a pass or a pass into a failure.

**The wiring gets a test at the call site, not only at the function** (the
lesson #116 paid 241 tests for). The test drives whatever calls `check` and
asserts that the check it reaches is this one.

### 3.5 Where it blocks

**Nowhere yet, and this should be said.** Today rite has no merge step. PB1
(the Manager role publishes: `pull_request`, `auto_merge`) is designed and
unbuilt, and in the release-candidate run Robert merges and opens the
upstream PR himself. So the gate has two halves:

- **Before PB1:** `check` is a command a person, or the Owner, runs before
  merging. It is advisory in the same way the publish gate is advisory until
  its CI job is required.
- **After PB1:** `auto_merge` and the Owner's merge decision refuse without
  a passing `check` for the PR's head SHA. This is the same hook TR10 is
  waiting on, and it should be built at the same time.

A GitHub commit status would let a repository make the check required. It
needs the "Commit statuses" permission, which the fork-scoped token in the
RC's 1.3 does not have. That is filed as an option (SGQ3), not as part of
the build.

### 3.6 What this design deliberately does not do

- It does not read the journal (section 1).
- It does not generate scenarios. No model writes a check at gate time. The
  commands are the ones the User agreed.
- It does not judge whether a command was the right one (fidelity). The
  reviewer does that, with the commands printed in front of them.
- It does not replace the terminating review. D-81 still stands: the two
  answer different questions.

## 4. What it is worth

### 4.1 Against D-81's v0.5.0 list

This does not change D-81's own analysis, with one sharpening. D-81's catches
were counterfactual: *"a scenario would have caught"*. Under this design the
question is whether **the User's agreed commands** would have included that
scenario. For "start the loop; confirm it is running", yes: a lazy ticket
reading "loop start does nothing" refines to exactly that command. For the
publish gate blocking every push, only if the definition of done said
"unrelated push with a pre-existing finding succeeds", which is the kind of
hostile-input requirement D-81 already says has to be written down.

### 4.2 Against the three defects since

| defect | caught? | why |
|---|---|---|
| **The Owner's refinement check for assignment, disconnected, with all 241 related tests green** (#116) | **Yes, if the agreed command drives the Owner's tick** and not the function. "File an unrefined ticket, run the Owner's assignment, the ticket stays unassigned" fails when the wire is cut. | This is D-81's written-tested-called-by-nothing class, and the gate's strongest case: a scenario exercises the path, so an unconnected check presents as missing behaviour. **Caveat:** it was in fact caught before merge, by the implementer's mutation test. The gate would have been a second catch, not the first. And it applies to rite's own development only if rite runs this gate on itself, which is a separate choice from shipping it. |
| **The macOS CI job running six files of 323 while reporting green** (tag readiness D12) | **No.** | This is infrastructure, and it is the gate's own defect class. An agreed `pytest` that collects six files exits 0, and the gate records a signed, SHA-matched pass of it. The signature proves rite ran the command. It says nothing about how much the command covered. Nothing in this design detects a command that measures less than its name suggests, and the gate should not be sold as if it did. |
| **An editor that silently corrupted documents** | **Only if the definition of done named the content that triggered it.** | "Silently" means exit code 0, so an exit-code gate sees a pass. A command that edits a document and then diffs it catches it, but only for the content the command uses. That is the path-escape case again: caught where someone thought to require it. ⚠ **I could not find this defect in this repository's history** (issues, PRs or `git log`), so this row is classified from its description alone. Pointing me at it would let me check whether it was behavioural in a way a round-trip command would have seen. |

**So, honestly: one of three, and that one had already been caught another
way.** The pattern is the one D-81 predicted. The gate catches missing
behaviour on a path someone agreed to exercise. It does not catch a check
that measures less than it claims, and that has been this codebase's most
frequent defect since D-81 was written. **The gate adds one more place where
that class can live.** The signed result is designed so that the thing it
measures (this command, on this SHA, under this agreement, exited 0) is
exactly what it says, and nothing more.

## 5. The release-candidate dogfood

**It complements the run, it does not duplicate it, and it needs the run's
findings first.**

- **Where they overlap:** step 9 of the run's part 4, where Robert reviews
  the Worker's diff against what was agreed. The gate automates the part of
  that review that can be written as commands. Nothing else in the run is
  the gate's territory: refinement under lazy input, the Worker's delivery,
  status honesty, the maintainer's response.
- **Where it adds something the run cannot:** the run's own part 3 says
  nothing checks the claims in a Worker's PR description, and "a confidently
  wrong 'tests pass' … reaches the maintainer unless Robert checks". A
  signed, SHA-matched result replaces the Worker's "tests pass" with rite's.
  That is the complementary part, and the one worth building.
- **Why it needs the run first:** the gate's input is TR2's output. What it
  can do depends on what records come out of refinement when Robert answers
  tersely: how often `verify` is `"none agreed"`, whether the commands
  exercise behaviour or just call `pytest`, and whether the items could be
  linked to commands. **None of that has been observed yet**, and TR2 itself
  (#117, #119, #120) is still open. It is the RC's precondition. Building
  the gate first would mean guessing SGQ1 and SGQ2 and then re-cutting it.

**Robert's current view, tested:** "scope it down, or sequence it after the
release candidate". **The sequencing half holds, for three reasons, and the
reasons are not about size:**

1. The gate's input (TR2's records) is unobserved, and the RC is where it is
   first observed.
2. The gate's blocking half has no hook until PB1, which the plan already
   places after the RC (TR10's row).
3. Building it now competes with TR2 for the same machine and the same
   review attention, and TR2 blocks the tag. The gate does not.

**The case for building it before the RC was checked and rejected.** Its
argument was that the RC is the only real observation before the tag, so an
unbuilt gate ships unobserved. That is true, but the answer is a second
observation after the gate is built (the throwaway-app run the RC plan
already schedules), not adding scope ahead of a blocker.

**The "scope it down" half holds too.** §7.3's tier declaration and phase
keying come out (2.3, 2.4), and the Worker-run log is replaced rather than
built (3.1). What remains is two commands and a signed comment.

**One costless thing for the RC, if Robert wants it (his word, because the
bar is pre-registered):** in the results, record for each refined ticket
whether `verify` was agreed, and whether those commands, run by hand at
step 9 against the PR's head, passed. That is the gate run manually once,
and its answers settle SGQ1 and SGQ2. It is a results note, not a pass
condition.

## 6. Size

| id | work | sittings | when |
|---|---|---|---|
| **SG0** | Amend SPEC §7.3 and D-81's row: the record as source, host-run, ticket-keyed, evidence in root + signed comment, tier dropped, and why. Changelog note | ½ | with SG1 |
| **SG1** | `rite scenarios run`: one `of()` read; SHA resolution; a fresh credential-less sandbox at the SHA; verbatim commands from the record; host-side exit codes and timeout; log in the root (`AUTHORED_CONFIG`); signed result comment with its own `kind`; the branch-written-file flag. Observed on one real ticket | 1 | after the RC |
| **SG2** | `rite scenarios check`: SHA match, current head record, degradation lines; the call-site wiring test; a mutation per condition. Observed refusing a result from an older SHA and from a superseded record | 1 | after SG1 |
| **SG3** | PB1's `auto_merge` and the Owner's merge refuse without a passing `check` for the head SHA | ½ | with PB1 / TR10 |

**2½ sittings to a gate a person runs, and ½ more to one that blocks.** ⚠
**The unknown that could move this is SG1's sandbox.** Every sandbox rite
starts today is a Worker with an agent. A credential-less exec-only sandbox
at a given SHA has not been built or measured. If yoloAI's exec path needs
work, SG1 becomes 2 sittings, and that is the first thing SG1 should
measure.

## 7. Decisions for Robert

| id | question | recommendation |
|---|---|---|
| **SGQ0** | Sequence after the RC, as sized above? | **Yes**, for section 5's three reasons |
| **SGQ1** | May a ticket be merged through the gate with `verify: "none agreed"`? | **Yes, with a visible degraded line** (TRQ9 stands). Revisit with the RC's data |
| **SGQ2** | Link each agreed command to the item it checks (a record v2, inside TR2's proposal format)? | **Decide after the RC.** It is the difference between "every command passed" and "every item was checked", and whether it is worth another question to the User per ticket is an RC observation |
| **SGQ3** | Post the result as a GitHub commit status, so a repository can make it required? | **Not in v0.7.0.** It needs a token permission the RC's scoped token does not have. After PB1, the merge is rite's own and the status adds nothing |
| **SGQ4** | Does rite run this gate on its own development? | **Robert's call, separate from shipping it.** The #116 catch only counts if it does |
| **SGQ5** | The RC results note in section 5 | Robert's word, because the bar is pre-registered |
