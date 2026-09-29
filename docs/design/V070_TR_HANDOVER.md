# Ticket refinement (track TR): handover, 2026-09-29

From the session that ran the audit, the design, TR0, TR1, TR2, TR5's route
and assignment refusal, and the first live exchange with Robert, to the
session that takes the track over. Written as a document in the repo because
messages between sessions went missing thirteen times on the day it was
written.

**Read first:** `V070_TICKET_REFINEMENT.md` (the design, with every ruling in
part 0), the TR rows of `V070_RELEASE_PLAN.md`, `V070_TR2_LIVE_EXCHANGE.md`
(the live run), `V070_RC_DOGFOOD.md` 2.1 (what the release-candidate run must
show). This document does not repeat them; it says where things are, what is
not known, and what will bite.

## 1. Where everything is

`main` at `598a711` when this was written. Every PR below is merged unless it
says otherwise.

| item | state | where |
|---|---|---|
| Design | merged | #43, `docs/design/V070_TICKET_REFINEMENT.md` |
| TR0, measurements | done except one | #109, #110; part 8 of the note. **Owed:** the Linux yoloAI Worker cannot read the key (VM was suspended) |
| TR1, record, predicate, `rite refine status` / `accept` | merged | #83; `src/rite_ai/refinement/{record,key,status,accept}.py` |
| TR2, the round protocol | merged | #117 (limits, ledger, lint, verdicts), #119 (ask, attribution, accept, silence, chat instructions), #120 (Owner's brief, private channel, the TRQ2 correction, the escalation ladder, live findings); `refinement/{rounds,admit,ask,protocol,instructions}.py` |
| TR3, instructions and spec | merged (other sessions) | #73, #108, #115, #122. **Owed:** SPEC §9.16.5's amendment for the refinement channel's authority (only an issue if a project uses `questions_to: channel`) |
| TR4, a Worker starts only on a REFINED ticket | merged (TR4 session) | #106 |
| TR5, route / assignment / handout refusal | merged | #114, plus the separator line #121 (other session) |
| TR6, the acceptance run | **not run**; preconditions met | `V070_RC_DOGFOOD.md` 2.1, #127 |
| TR7, `ready-to-work` label | **not built**; not a precondition | plan row; label ruling in #113 |
| TR8 | G2 pinned (#110); the identity report per TRQ12 is in its plan row | plan row |
| TR9, every Worker job has a ticket | merged | #78, #81, #84 |
| TR10, the Owner working an unrefined ticket itself | **after PB1** (enforced at publish) | plan row |
| Test infra: the suite never touches the enclosing tmux | merged | #126 |

**Open PRs of mine you inherit:** #113 (the label ruling in the plan; being
landed as this was written, check it), #87 and #88 (CI: macOS job file list;
both conflict since 2026-09-28 and may be overtaken by #90; decide or close
them, do not merge them blind).

## 2. Robert's rulings, in his words, and what each changed

Verbatim quotes are in the note's part 0; these are the ones that **changed
the design** rather than picking an option. Keep them word for word when you
cite them.

- **The requirement.** "I expect rite to refine the lazy tickets before
  proceeding - ask user questions, define a definition of done etc." and
  "Yes, I expect the auto-refinement in v0.7.0". Changed: refinement is a
  v0.7.0 feature, not a v0.8.0 idea.
- **TRQ1, enforcement.** "let's just implement it as a standard". No
  configuration key, no opt-out. `refinement.enforce` and similar keys are
  refused as unknown.
- **`scheduled` semantics.** "scheduled + not refined => owner starts
  refinement procedure, scheduled + refined => owner assigns it to a
  Manager". Changed: an unrefined scheduled ticket is refinement WORK, not a
  refusal; this is why the loop has `refining` and `waiting-on-user`, never
  `idle`, for such a board.
- **Labels are a view, never an authority** (TRQ1's second half): the
  `ready-to-work` label is derived from the signed record; no gate reads a
  label. **The label ruling** (relayed by the coordinator, not verbatim; in
  the plan via #113): the capability vocabulary is configurable in project
  config; Managers set labels through a validating interface; **unknown
  labels are ignored, not removed**; the Owner validates and may edit them at
  assignment, with the edit recorded; measurable checks (the context-window
  floor) are independent of any label.
- **TRQ4, "you decide".** "Yes, let's have rite come back with a
  recommendation for a final confirmation". Never an accept.
- **TRQ5, all Worker work ticketed.** "Can we just ticket all work that
  Workers do? (in JIRA that would be chore tickets I guess)". Replaced a
  `--no-ticket` escape.
- **TRQ6, reframed as a guardrail.** "Can we create some guardrails against
  overriding user input without an explicit permission?" → G1/G2; rite never
  edits a ticket's title or description.
- **TRQ7, dissolved.** "Doesn't 'Owner owns the board and assigns tickets.
  Managers manage only the tickets assigned to them' solve this?" Only the
  Owner refines. **This is why rung 1 of the escalation ladder needs no code
  today** (below).
- **TRQ10.** "Allow it." A session running as the person may attest; the
  record says `attested`, findable by `rite-attested`.
- **TRQ11, the chore on silence.** "In most cases, I think the Owner should
  ask follow up questions (if it has any doubts) and refine straight away. If
  the User doesn't reply within some timeout, create a chore with what it's
  got and then refine later". Changed: silence files an unrefined chore
  carrying exactly his words (`chore_after_minutes`); nothing is lost,
  invented, or parked for silence alone.
- **TRQ12.** "Can't the User control it by choosing if they give rite their
  token or create a separate account for them?" rite builds no identity
  management; it reports which identity is in use.
- **Q4.** "Yes, close the bypass. However, instruct that it's meant for
  chores and trivial tickets. Any serious work should be passed to workers."
- **TRQ2, corrected after the live exchange.** "This limit should apply to
  nudging without a reply, not to a discussion. A topic may be complex and
  need many rounds to resolve. As long as the User is responsive, the limit
  shouldn't apply". Changed: `refinement.unanswered` (3) counts consecutive
  unanswered MESSAGES; any reply resets it; rounds are uncapped; the old
  `rounds` key is refused.
- **Only a bare accept word writes the record;** a qualified accept ("ok but
  make it 10s") is an answer that updates the proposal (relayed by the
  coordinator).
- **The escalation ladder.** "if that happens, the Owner should take over
  that conversation and then relay the outcome to the Manager that asked. If
  the Owner has the same issue, it says so loudly and escalates it as a
  blocker on the board and in the checkpoint status updates". Detection is
  the proposal not changing, never a count. **Rung 2 is built** (blocker to
  him, `blocked` label and comment on the ticket, every check-in). **Rung 1
  is not built, deliberately:** only the Owner ever refines (TRQ7), so no
  secondary holds a conversation with him to take over, and code for it
  would be dead (`test_no_dead_wiring`). What it would need if secondaries
  ever refine is written beside the ruling in the plan.

## 3. The live exchange (details: `V070_TR2_LIVE_EXCHANGE.md`)

Robert, his own Slack DM, his own v0.6.0 tickets (KAN-6..9 on `ritetest`),
a Claude Owner, one sitting. **Every answer was his.** Two replies
(10:58, 11:00), then his attention moved on; nothing after.

- **As designed:** round shape, attribution by thread, "Definition of done:
  ok" not taken as a yes, silence read past the deadline and not re-asked, no
  session while waiting, pacing, the no-progress guard.
- **Not as designed, all fixed:** an answered round with no next round sent
  was not work; the silence line repeated; a quote of "ok" vouched for an
  invented item; quote tags shown raw in questions; the suite killed the
  tmux server it ran from.
- **Open:** a plain `rite start` after a killed session resumes an OLDER
  conversation begun under different instructions, silently. That cost
  Robert 35 minutes. `rite start` should refuse or say.
- **The finding in hand: he accepts inline, not with a bare word.** He wrote
  "1. Make it look good / 2. a message / Definition of done: ok". rite
  correctly did not record it. Expect it again in the RC run; the Owner is
  now told explicitly when a reply is an accept with a change.

## 4. What is not measured

Say these plainly when anyone reports TR as done.

- **Jira comment paging at scale.** The endpoint shape is confirmed on
  `ritetest` KAN-11, but at small counts Jira embeds every comment, so the
  real board never took `read_thread`'s paging branch. Only unit tests cover
  it.
- **The Linux yoloAI Worker and the signing key.** Not measured (the VM was
  suspended); on the RC checklist. macOS Manager, macOS Worker and Linux
  Manager (Landlock, in CI) are measured.
- **From the live run, not reached:** a bare `ok` writing a record; a chat
  instruction filed as an unrefined chore; parking after three unanswered
  messages; a nudge when he is back; an unchanged proposal said and a third
  escalated; the private channel (and whether his app holds
  `groups:history`); a **local-model Owner** and its turn times (the
  Parallels VMs were running; a model does not load on the Mac's Ollama while
  they do). All are unit-tested with the real predicate and real signed
  records. None of that is a substitute.
- **Jira comment rendering:** records render with stray asterisks in Jira's
  markup (cosmetic; the JSON block is intact).

## 5. Traps (the part worth reading twice)

- **The one-read property, and why `refinement.status.of()` exists.** A
  refinement decision must come from ONE read of the board: the same read
  gives the ticket text, the record, and the state, and whoever starts work
  hands the Worker `record` and `ticket` from that `Status` object. A second
  read lets an edit land in between, so a Worker would start on text that no
  longer matches what authorised it (race 4). `of()` is the one composition
  of config → board → read → key → predicate; a guard test fails if anything
  outside `refinement/status.py` or `tickets/` calls `read_thread` or
  `evaluate`. Do not add a second path "for convenience".
- **The DF4 wrapper hides board identity.** A board built with a `root` is
  wrapped in `ReadsItsOwnWrites`; `record.board_identity` sees through it now
  (#114). Without that, every record on a wrapped board reads UNREADABLE and
  nothing is ever assigned, for a reason nobody can see.
- **`tell_manager` does NOT truncate.** The coordinator's brief for this
  handover said it does; it was checked in the code: `telling.tell_manager`
  writes the whole note. The 200-character cut is `broker.honour`'s (a failed
  Worker start's reason), which is why `refinement.status.refusal` keeps its
  lines under 200. Check before you design around a limit someone remembers.
- **The darwin gate covers whole files, and only seven of them.** The macOS
  CI job runs a named list of test files, not the suite and not test names.
  A macOS boundary test in any other file runs on no macOS machine in CI, and
  the job's green says nothing about it. Several files are
  `sys.platform != "darwin"`-gated, so the Linux job's green says nothing
  about them either. **Read the log for your file and your test count;
  never trust the check mark alone.**
- **The suite and tmux.** Before #126, a suite run from inside tmux froze and
  killed that tmux server. Branches older than #126 still do: run their
  suites with `env -u TMUX -u TMUX_PANE`.
- **A supervisor for a live run goes in a detached tmux**, not a background
  task of your session (those die at your turn end); restart after a kill
  with `--fresh` (see section 3).
- **One test always fails from `/private/tmp`:**
  `test_the_manager_profile_denies_what_it_should.py::TestTheRiteItsInstructionsNameCanRun::test_it_cannot_rewrite_the_rite_it_runs`.
  Deselect it for worktree runs, and delete the probe file it leaves,
  `src/rite_ai/rite-probe-should-not-exist`.
- **CI runs only on PRs to `main`.** A PR stacked on another branch gets no
  CI until it is retargeted. No force-push: update a branch by merging
  `main` into it.
- **A green that means nothing.** Before trusting a new test, break the code
  it guards and watch it fail (mutation by backup copy). Twice on this
  track a guard existed in two places, so removing one proved nothing;
  mutate both. Once a test board without `label` fell through to a real
  `gh` call: give fakes every method the code under test may call.

## 6. Refusing an instruction is part of the job

The coordinator asked this handover to record, as its most valuable part,
that this session once refused a design the coordinator had approved,
because it let a Manager steer its own verification. **This session's
transcript does not contain that event**; it may belong to another session,
and it should be attributed where it happened rather than here. What is
recorded here is the principle, which the coordinator stated explicitly:
**refusing an instruction is not only allowed but expected when the code says
otherwise.** Cases from this track where the code, not the instruction,
decided:

- **A `/dev/tty` confirmation was dropped from this session's own design.**
  `rite refine accept` was to read the person's "yes" from the terminal as
  proof it was him. Measured: a model can create a terminal (`script`,
  `pty.fork`) and type the answer. A check a model can pass is not a check;
  the signing key, unreadable from every sandbox, is the barrier instead.
- **`rite route` does not check refinement, on purpose.** It was asked for
  at the command. The command runs inside the Manager's sandbox, where the
  signing key is unreadable, so it cannot verify a record; a check there
  would be advice a Manager can bypass by writing the request file. The gate
  is the supervisor, outside the boundary.
- **The loop's `_ready` was not gated alone,** though asked. Gated without
  the `refining` verdict, a board of unrefined tickets reads `idle`, which
  ends the run before anything is refined.
- **Rung 1 of the escalation ladder was not built,** though queued: it would
  be code nothing can call (section 2).
- **`tell_manager`'s 200-character limit** was in the brief and is not in
  the code (section 5).

When an instruction and the code disagree, say so with the file and line,
build what the code supports, and let the coordinator decide with the facts.
