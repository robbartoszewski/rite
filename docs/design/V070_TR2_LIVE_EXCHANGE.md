# TR2's first exchange with a real person (2026-09-29)

The round protocol (TR2) was exercised live with Robert as the User, in his
own Slack DM with rite, on his own lazy tickets. **Every answer recorded is
his, typed by him in Slack.** No agent wrote a reply, and the coordinator
declined an offer to drive the rest through a browser for that reason: an agent
writing the uncooperative answers for an agent is the flaw that weakened the
v0.6.0 dogfood.

This is evidence for the release-candidate run (TR6), not a substitute for
it: one person, one sitting, a Claude Owner, and several parts not reached.

## Setup

| | |
|---|---|
| Project | `~/AI/dogfood-v060/pingr`, the v0.6.0 dogfood project |
| Board | Jira `ritetest.atlassian.net`, KAN (Workers) |
| Tickets | Robert's own, from the v0.6.0 dogfood, scheduled and unrefined: KAN-6 "pingr retry on fail", KAN-7 "timout is way too long, make it configurable or smth", KAN-8 "output is ugly, table?", KAN-9 "add tests" |
| Owner | `lead`, Claude (the cloud model; not a local model) |
| Where he answered | his Slack DM with the rite app (`questions_to: dm`) |
| Code | TR2 unmerged, run from the `tr2c` worktree: `632cda5` at 10:53, `a64d0e8` from 11:36, `2964e8e` from 11:55, `c814a0b` from 12:01 |
| Shortened for one sitting | `deadline_hours: 0.25` (15 min), `chore_after_minutes: 5`; his defaults are 24 h and 60 min |
| Slack app | lacks `reactions:read`, so only a reply in a thread confirms a question was seen |

## What happened

| time | event |
|---|---|
| 10:53 | Owner started fresh. Loop verdict `refining`: nothing refined, three oldest tickets to start |
| 10:56–10:57 | Round 1 for KAN-6 (questions and a proposal), KAN-7 (three questions), KAN-8 (questions and a proposal), each as a question in his DM and a comment on the ticket |
| 10:56 | Supervisor killed with the operator session's background tasks (operator error, see below) |
| 10:57 | Restarted without `--fresh`: this resumed **last night's** Owner conversation (v0.6.0 instructions) |
| 10:58:25 | **Robert, KAN-7:** "1. CLI flag / 2. Keep 5s. / 3. per attempt" |
| 11:00:04 | **Robert, KAN-8:** "1. Make it look good / 2. a message / Definition of done: ok" |
| 11:00, 11:11 | Two Owner sessions in the old conversation could not run TR2's `rite refine ask`; round 2 was not sent |
| 11:11 | KAN-6's deadline passed unanswered: read past it, WAITING FOR YOU, not asked again |
| 11:11 | KAN-9 PARKED "not started by the Manager" by the no-progress guard (handed over twice to the broken sessions) |
| 11:36 | Restarted `--fresh` on `a64d0e8`; KAN-9 reopened by the operator (`rite refine reopen`, at the host) |
| 11:38 | Round 2 for KAN-7 and KAN-8, and round 1 for KAN-9 |
| 11:53 | All four latest messages past their deadlines unanswered |
| 11:55, 12:01 | Robert's corrections folded in (below) and the Owner restarted on them |
| ~12:02 | Supervisor killed by the operator's own test suite (fixed in #126, below) |
| 12:19 | Restarted; waiting on him, four tickets at 1 of 3 unanswered |

## Measured against the design

| what | designed | observed |
|---|---|---|
| Round shape | ≤ 3 numbered questions; a proposal from round 2; items tagged with an exact quote or `[proposed]` | ✅ Every round sent passed the check. KAN-7 round 2 quoted him exactly ("CLI flag", "Keep 5s.", "per attempt") and labelled its own two items "proposed by lead" |
| Attribution | by the thread he wrote in, never by a model's reading | ✅ Both replies matched to their rounds by thread, recorded with Slack's send time, delivered to the Owner with rite's line saying what was due next |
| Accept is a word | only a bare accept word writes a record | ✅ "Definition of done: ok" was an answer, not a yes: no record was written (see finding 4) |
| Silence | WAITING FOR YOU once the thread is read past the deadline; not asked again while away | ✅ KAN-6 at 11:11: marked after a read past its deadline, and no second message |
| No session while waiting | `waiting-on-user` spends nothing | ✅ Between replies the supervisor waited with no session; his reply started one |
| Pacing | three starts per session, oldest first; a new batch only after a reply or a deadline | ✅ KAN-6, 7, 8 started; KAN-9 queued until a later session |
| No-progress guard | a ticket handed over twice with no round sent parks | ✅ as designed, and it fired on the operator's broken sessions (KAN-9), which is what it is for |

## What did not behave as designed (and what was done)

1. **An answered round whose next round was never sent was not work.** His
   replies (10:58, 11:00) were older than the last session, so the loop
   would have waited on HIM while the Owner owed HIM round 2. Fixed
   (`a64d0e8`): such a round is a reason to start a session whatever the
   clock says, and it is covered by the no-progress guard.
2. **"No answer by its deadline" was said on every read of the thread.**
   Fixed (`a64d0e8`): said once.
3. **A quote too short to mean anything passed the check.** KAN-8 round 2
   tagged an item the Owner invented (tests and a README update) with
   `[answer: "ok"]`. "ok" is in his reply, so the exact-substring check
   passed it, and he was told his words were the source. That is the
   "invented" failure TR2 exists to stop. Fixed (`8559f93`): a quote must be
   whole words and hold a real word, or the item is `[proposed]`.
4. **He tried to accept inline** ("Definition of done: ok" at the end of
   his answers). As designed, not a yes. **Whether it should be is
   Robert's question**, and his later ruling (only a bare accept word
   writes the record; a qualified accept updates the proposal) keeps it as
   built. The Owner is now told explicitly when a reply is an accept with a
   change (`8559f93`).
5. **Quote tags shown raw in questions.** The Owner put `[ticket: "…"]`
   inside two questions; the check reads tags on proposal items only, so he
   saw them. Fixed (`8559f93`): dropped from questions.
6. **A killed session is not recorded as the conversation to continue.** So
   a plain `rite start` after a kill resumed an older conversation, begun
   under different instructions, without a word. **Open**: `rite start`
   should refuse, or say, when the conversation it would continue was begun
   under instructions that differ from today's.
7. **The test suite killed the tmux server it was run from.** A test
   isolated with `TMUX_TMPDIR`, but `$TMUX` inside a tmux session names the
   enclosing server and wins. Run from tmux, the suite froze and killed that
   server, taking a live supervisor with it. Fixed in #126, with a control.

**Operator errors, stated because they cost Robert's time:** the first
supervisor ran as a background task of the operating session and died with
it at 10:56; the restart omitted `--fresh` (finding 6). Round 2 reached him
at 11:38 instead of about 11:03.

## Robert's rulings that came out of it

- **TRQ2, corrected:** "This limit should apply to nudging without a reply,
  not to a discussion. A topic may be complex and need many rounds to
  resolve. As long as the User is responsive, the limit shouldn't apply".
  Built (`2964e8e`): the cap is three consecutive unanswered messages; any
  reply resets it; rounds are uncapped; an unchanged proposal is said in the
  message.
- **An escalation ladder:** "if that happens, the Owner should take over
  that conversation and then relay the outcome to the Manager that asked. If
  the Owner has the same issue, it says so loudly and escalates it as a
  blocker on the board and in the checkpoint status updates". The first rung
  holds by construction (only the Owner refines); the second is built
  (`c814a0b`).

## Not reached in this sitting

A bare `ok` writing a record; a chat instruction filed as an unrefined chore;
parking after three unanswered messages; a nudge when he is back; the
private channel; a local-model Owner (the VMs were running). Each is covered
by tests with the real predicate and real signed records, and none of that is
a substitute for seeing it with him. **These are what the RC run must
exercise.**
