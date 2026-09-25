# Check-ins — 0.6.0

**Status: PLANNED, 2026-09-25.** Scope added by Robert. Sequenced after the
Slack track (A) and before the broker (B9). Tickets K1–K6 in
[`V060_RELEASE_PLAN.md`](V060_RELEASE_PLAN.md) § K. Nothing here is built.

## What Robert asked for

A User wants to do 95% of their interaction with the AI in a few windows a
day — say 9–10, 14–15, 20–21 — planning features, making decisions,
unblocking Managers. The rest of the time is hands-off. So questions that do
not block work are **queued** and asked at the next configured check-in.

Three parts:

1. **The windows** — weekday and hour spans in machine-local time.
2. **The queue is a draft, not a commitment.** A question queued at 10:00 is
   **re-evaluated at 14:00 before delivery**, and dropped if the Manager has
   since answered it itself. Robert's observation: an orchestrator
   repeatedly asks something, retracts it, then discovers it never needed
   deciding. **Deferral doubles as a filter** — by the time the window opens,
   the answer often exists.
3. **A standup at each check-in** — concise bullets covering everything since
   the last one.

## ⚠ The rule the whole feature depends on: ask now unless CLEARLY deferrable

**The failure is asymmetric.** Deferring a blocking question costs a whole
window of idle work — up to five hours between 9–10 and 14–15. Asking a
deferrable question costs the User thirty seconds. So:

- **Asking now stays the default.** `rite reply` is unchanged; deferral is a
  separate, explicit act.
- **Anything uncertain is blocking.** The Manager is told so in those words.
- **A deferral must say what the Manager will do meanwhile.** A question the
  Manager cannot name parallel work for is, by that fact, blocking; rite
  refuses the deferral and tells it to ask now.
- **A safety net for the misjudgement that gets through:** if the loop's
  verdict goes idle while questions are queued, the queue was wrong about at
  least one of them, and rite delivers the queue immediately and says so.

This is in the ticket (K2) as well as here, because it is the part that fails
if the feature fails.

## The windows reuse the schedule, not a second time model

A check-in window is exactly a `schedule.windows[]` span without `workers`:
the same `hours` grammar (`"HH:MM-HH:MM"`, wrapping past midnight), the same
`days` grammar (`"Mon-Fri"`, `"Sat,Sun"`, wrapping ranges), parsed by the same
`parse_days` / `_parse_hours`, in the same zone — `resolve_zone`, which is
machine-local unless `schedule.timezone` says otherwise and says which it
used (`describe()`). A second parser would drift from the first; a second
zone rule would put a 14:00 check-in at 15:00 for somebody whose schedule is
right. Config shape proposed:

```yaml
checkins:
  windows:
    - {days: Mon-Fri, hours: "09:00-10:00"}
    - {days: Mon-Fri, hours: "14:00-15:00"}
    - {days: Mon-Fri, hours: "20:00-21:00"}
```

**No windows configured means no check-ins**, and a deferral then has
nowhere to wait — so it is delivered at once and rite says why (the
asymmetry again: the safe failure is asking).

## The queue is re-evaluated by the Manager, not by rite

rite cannot judge whether a question has been answered; the Manager can. So at
the first cycle boundary inside a window, the supervisor composes that
cycle's instruction with the queued questions and one directive: *withdraw any
you can now answer, citing where the answer came from; the rest will be asked.*
Withdrawal is a command (`rite question withdraw <id> --answered-by <anchor>`)
and needs an anchor, through the journal's anchor floor — "I worked it out" is
not an answer anybody can check. Whatever survives that cycle is delivered.

**The filter's value is recorded, not assumed.** Every digest states how many
questions were queued, how many the Manager withdrew before asking, and how
many were asked. If withdrawals are near zero for weeks, deferral is not
filtering and that is worth knowing.

## ⚠ The standup carries anchors, not prose — non-negotiable

Three things were reported done in the last release that had never run. A
standup is otherwise the most efficient channel there is for unverified
claims. So the digest is **composed by rite from records that are anchored
by construction**, and the Manager's own additions go through a refusal:

- **What rite observed itself** — commits on the project since the last
  check-in (SHA + subject), board transitions (ticket ids), Worker sandbox
  starts and exits, each supervise cycle and how it ended, refusals the
  engine reported (C21). Each is a record rite read, with its identifier.
- **What the Manager states** — `rite checkin note --anchor <x> --observed
  <what was seen>`, through the same anchor floor as `rite journal observe`.
  A note with no anchor is refused. *"Landed `abc1234`; observed a Worker
  authenticate"* passes; *"sorted out the Worker problem"* does not.
- **The two are labelled differently in the digest** — observed by rite vs
  stated by the Manager — for the reason C10 gave journal entries a
  `recorded_from`: a reader must be able to tell evidence from a claim.

## Where the state lives

**Under `manager_dir(root, name)/checkins/`, NOT in `.rite/user/*.json`.**
That directory is keyed by Manager name and has twice held a file that was
not an instance record — a designation (C11) and, since C4, the permission
settings, which collide with the record of a Manager named `permissions`.
A third kind of file there would be the same defect again. The queue, the
last-check-in marker and the withdrawal counts all live under the Manager's
own directory.

## What Slack must provide, and what it did not as written

Checked against the A tickets on 2026-09-25. Three needs:

| need | provided as written? | amendment |
|---|---|---|
| Post a status message **and keep its identity**, so threads can be rooted on it | ❌ A4 posts the outbox and says nothing about keeping `chat.postMessage`'s `ts` | A4 amended: the relay records each posted message's `ts` in its own state, keyed by outbox filename — **not** in the message, which stays identity-free (Decision 1a) |
| **Read replies** — including replies in a thread | ❌ A3 reads `conversations.history` only. Slack documents that thread replies are fetched with `conversations.replies`; `history` returns the parent. **Not yet measured by rite** — A3's amended DoD must observe it | A3 amended: also read the threads of messages rite posted recently, within the Tier 3 budget, and deliver a thread reply labelled with what it replies to |
| A conversation target that is **configuration**, not an assumption | ❌ No config key exists; the proof instrument defaults to `#all-rite` | New **A6**, rewritten the same day for D-95: the **Owner's DM** is the command channel (configured by user ID), and a configurable **broadcast** channel defaults to `#all-rite`; both probed by `rite doctor` |

**The check-in does not need a new mailbox shape.** The digest and the
surviving questions are posted as ONE outbox message, so `rite connect`
(through `rite replies`) and Slack (through A4) both see it, and Slack roots
a thread on it. A reply in that thread comes back through amended A3 into the
inbox with its context as TEXT ("in reply to the 14:00 check-in: …"). Nothing
records a sender and no field is added to a message.

### Who may answer — decided 2026-09-25 (SPEC §9.16, D-94–D-96)

An answer to a queued question is an **instruction**, and an instruction needs
**authority** (the channel) **and addressing** (a mention, or a reply to what
rite asked). So the thread that collects answers is rooted in the **Owner's
DM**, the command channel, which is one-to-one by construction. The digest is
**mirrored** to the broadcast channel (default `#all-rite`). Replies there
reach the Manager as **context**, labelled so, including an `@rite` from
someone who is not the Owner. ⚠ `@rite` answers "is this addressed to me",
never "may this person direct me". Anyone in the workspace can type it.

### What the standup does NOT depend on

**The standup is complete without § N.** If N2 (plan § N, SPEC §6.6, D-97)
ships, its injection-phrase findings are appended to the digest as reported
lines, never blocks. **K does not depend on N**, which is sequenced last so it
can be dropped for free (Robert, 2026-09-25), and nothing in K4 or K5 may
come to rely on it. Whether or not N ships, the digest must not imply that
ticket text is vetted. 8 of 8 agent-directed attacks pass any text filter,
and the control that makes that survivable is egress control
([`V070_EGRESS.md`](V070_EGRESS.md), v0.7.0).

## Open, for Robert

- **A check-in window that passes with no Manager running.** There is no
  daemon (A5, Decision 2), so nothing posts. Proposed (K6): the queue
  persists; `rite start` says how many questions are waiting and when the
  next check-in is; the next digest covers everything since the last one
  that was delivered. Not assumed to be what he wants.
- **Multi-Manager** is v0.7.0. One digest per Manager, per its own channel
  (A6), is the 0.6.0 shape; a combined standup across Managers is not
  planned.
