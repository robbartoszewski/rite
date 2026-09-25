# Slack/Discord as the relay layer, 0.8.0 — Robert's design, recorded

**Recorded, not derived.** Robert's shape for the 0.8.0 relay, written down
before it gets lost in a transcript. Nothing here is built. 0.8.0 also carries
self-reflection (see `V070_MEMORY.md`, Analysis 8) and the v0.7.0 fixes; this
note covers the relay only. The analysis is mine and is aimed at what building
will have to decide.

⚠ **UPDATED 2026-09-25: the status paragraph below is stale.** `.docs/` was
replaced by this directory, and every design note, `V070_MULTI_MANAGER.md`
included, is tracked here (see `README.md`). v0.7.0 planning, including the
open questions this note raises, is in `V070_RELEASE_PLAN.md`.

**Status of the file itself.** Committed under `docs/design/`, for the reasons
given in `V070_MEMORY.md`'s corresponding note — the other design material
lives in gitignored `.docs/`, where nothing is tracked, and does not reach a
fresh clone.

**Citation convention.** A bare `§` is a section of `SPEC.md`; a section of
any other document is written out as "<file> section N", never with a `§`: the
gate's regex is context-free, so `§6.4` in another document's numbering would
silently match a real SPEC section. And `DEFECT_CLASSES.md`'s numbered classes
are written "class N" rather than `§N`. This note's own sections are "Analysis
N", never `§N`, because `tests/test_spec_citations.py` names exactly that
collision as the reason it does not scan documents that number their own
sections.

---

## What the relay is for: a Manager has no channel

**Recorded because it reframes the role, not just the transport.** The rest
of this note described a relay for questions. This is what the relay is
FOR, and the document was written without it.

### The observation

This project's own Dispatch thread has been run all evening by something
acting as a Manager: taking scope from Robert in chat, spawning and
directing Workers, judging their reports, escalating what it could not
decide, answering what it could, and reporting unprompted when something
mattered.

**A rite Manager can do none of that, and the difference is one thing.**
rite gives the User a tmux pane to attach to — a viewing window, not a
conversation. Attaching shows you what a Manager did; it does not let the
Manager ask you anything and wait. **A Manager that can only be watched
cannot escalate**, so every mechanism in this note downstream of
escalation has had nothing upstream of it.

### The requirement, in Robert's words

The User needs a continuous chat that reaches the Manager, with control
over it, able to discuss scope and new specs with it. And explicitly:
*"we augment the standard chat flow rather than replacing it."*

### Four properties, written so something could be tested against them

**1. Continuous — the conversation outlives any single Manager session.**
A Manager ends and resumes; the chat does not restart. Testable: send a
message, let the Manager's session end and a new one resume, and the
Manager can still refer to what was said before the resume.

⚠ **This is the one a naive implementation breaks first, and it is coupled
to the resume path.** A resumed cycle is a *new tmux session* and a new
provider session continued by `--resume` — so a design that keys the
conversation on either of those identifiers gets a fresh conversation
every cycle, and looks identical from outside to one that worked. That is
this project's recurring shape, and it is the same failure
`_default_resume_id` already had: a resume that resumes nothing. **The
conversation's identity must be the MANAGER**, which is the durable name,
not the session, which is deliberately not.

**2. Reaches the Manager — a message arrives while it is working, without
stopping it.** Testable: with a Manager mid-task, a message posted to the
channel becomes visible to it within a bounded interval, and the task it
was already doing still completes. This rules out any design where
delivery requires the Manager to be idle, or restarts it to deliver.

The cost is named rather than discovered: §3.1.1 measures ~500K tokens
re-read per turn in this project's coordinator session, and every message
delivered into a Manager's context is re-read on every subsequent turn.

**3. Control — the Manager can be redirected mid-flight, not merely
observed.** Testable: a message that changes scope changes what the
Manager does next, and the change shows up in its next report. This is
the property the pane does not have — a human can type into a pane, but
nothing downstream treats what they typed as an instruction with standing.

**4. Augments rather than replaces — rite does not build a chat client.**
Testable negatively: no rite command opens a chat UI, and the surface the
User talks in is one they already had. This is the strongest argument for
the Slack/Discord direction the rest of this note assumes, and it bounds
the feature: **adapters, not a product.**

### The channel carries authority, not status

**If the User agrees new scope with a Manager in chat, that agreement must
become durable or the next session loses it.** Robert has already
specified that the spec is derived, with User amendments that survive
regeneration. **Chat-agreed scope is one of those amendments.**

Two things follow, and they connect this note to `V070_MEMORY.md` from
opposite ends:

- Analysis 3 below argues an *answer* must produce a recorded decision
  rather than a reply in a thread. This is the same requirement arriving
  from the other direction: it is not only answers to escalations that
  must be written down, it is **anything the channel settles.**
- `V070_MEMORY.md` asks what a Manager may answer directly and what it
  must escalate. **That is the same question as what the channel is
  allowed to settle**, and it was being treated as two.

Robert's own refinement of the test: *"do you have all the information
necessary to answer it straight away?"* Record it beside Analysis 2's
decision-versus-fact rule rather than instead of it, because they fail
differently. His test asks about the Manager's own state, which is the
thing actually able to answer; Analysis 2's asks about the question's
category, which is inspectable. His is a **self-report** — the instrument
Analysis 2 exists because it already failed once today.

### What this does to Finding B

Finding B held that the pane must stay attachable because it is the
User's only way in. **If control arrives through a channel, the pane
needs only to be READABLE**, which is a strictly weaker constraint and one
that is already measured: after the engine exits, `remain-on-exit` holds
the pane and its conversation stays readable through `tmux attach`, which
the left-over refusal now tells the operator in as many words.

**Not a decision — Robert has not made one.** What is established is that
the constraint is weaker than it was taken to be, and that "there is no
other way in" has stopped being an argument for keeping the pane
attachable. Whether it should stay attachable for other reasons is open.

⚠ **Finding B is not written down anywhere in this repository.** Searched
every tracked file, every branch, and gitignored `.docs/`: no hits. It
lives in the Dispatch thread. That is Analysis 3's own subject happening
one level up — this note can restate the finding but cannot cite it, and
a reader six weeks from now has only this paragraph.

---

## The shape

**Dispatch is no longer the relay, so questions need a transport, and the
transport is a chat channel.** Slack or Discord, extendable by adapters —
neither is privileged in the design.

**All questions route to the Owner. There are no human roles at the Manager.**
The Owner is the single point that faces humans, and it posts each question to
the appropriate channel — through an MCP where one exists.

**A human answers by replying to the message.** The Owner scans the configured
channels and picks the reply up from the thread.

**Channels are configuration, not code.** The worked example is `#law`,
`#architecture`, `#ios`, `#backend`, `#frontend`, with a **default channel**
for anything general — but the set is fully configurable and nothing in that
list is hardcoded.

### The escalation chain

```
Worker has a question
  → escalated to Manager
      → answered by the LLM, or escalated to Owner
          → answered by the LLM, or escalated to the appropriate channel
```

Each hop tries to answer before it escalates. A human sees a question only
when two LLM layers have declined it.

### What this retires, which the shape does not say

Both headline sentences remove something that exists and is specified today,
and neither is currently recorded as a change:

- **"Dispatch is no longer the relay."** §3.1 assigns Dispatch that role ("The
  Owner's Dispatch asks the human what to do with already-assigned tickets"),
  §9.10's orientation decision table is Dispatch-driven throughout, and D-40
  makes `rite ticket` a Dispatch slash command rather than a CLI command
  precisely because working a ticket end-to-end needs judgement.
- **"No human roles at the Manager."** §3.1's ⚠ Phase-2 note carves out the
  opposite for today: "a single Phase-1 Manager still tracks its own Workers'
  liveness and **surfaces Worker stalls to the human** — that relationship is
  real today and unrelated to this section." §9.10's orientation table has the
  Manager's Dispatch surfacing blockers to the human as well.

Under §13's supersession convention a reversed or narrowed decision says so in
its own cell rather than being silently edited. **Whoever builds this owes
§3.1's carve-out, §9.10's blocked row and D-40 an explicit answer** — kept,
narrowed, or retired — rather than letting them stop being true by attrition.

---

## Analysis

### 1. It solves a problem this project hit repeatedly today

**Three separate sessions blocked on questions today, and each one reached a
human only because an orchestrator happened to be watching.** That is the case
for this feature, stated plainly: the current relay is *attention*, and
attention is not a mechanism. When nobody was looking, a session either waited
or — worse, and this also happened — answered its own question and carried on.

**The evidence is reported, not logged, and that is a gap in the argument
rather than a detail.** This note carries no session ids, tickets or
timestamps for those three, because none were recorded at the time; the claim
reaches this file the way the observation reached the orchestrator, as
recollection. `REVIEW_COST.md` is the house template for presenting exactly
this class of same-day observational evidence — a brief/model/outcome table,
one row per instance — and it exists *because* an unattributed same-day claim
nearly became a default. **Before this feature is scoped, the three should be
written up in that shape**: session or ticket, the question asked, what
actually happened. If they cannot be reconstructed, that is itself the first
finding — it means the fleet has no record of its own blocking events, which
is a thing the relay would have to create anyway.

What the observation establishes, and which is not in doubt: **there is no
path from a Worker's question to a human that does not depend on a person
being in front of a terminal at that moment.** §3.1 already specifies that the
Manager↔Owner channel carries "blockers, both ways". The missing piece is the
last hop, from Owner to a human who is not currently watching.

### 2. The escalation chain's risk is that it is too good at not escalating

Every hop is a filter with the test "can the LLM answer this?" — and that test
**selects against exactly the questions that most need a human.** A question
the model cannot answer looks like a gap and escalates. A question the model
is *confidently wrong* about looks answered and stops.

This is not hypothetical here either. Today produced several such answers,
each fluent, each plausible in shape, each wrong — and none presented as
uncertain. The same caveat as Analysis 1 applies: those are reported, not
logged, and belong in the same table. Two layers of this filter compose: a
question has to survive being confidently mis-answered twice, and the second
layer sees the first layer's confidence rather than the original question.

**The mitigation is a category rule, not a confidence threshold.** A threshold
is calibrated on the model's own self-report, which is the instrument that
just failed; `DEFECT_CLASSES.md` class 1 is the class where the measuring
instrument reports success while measuring nothing, and a confidence gate over
a confidently-wrong answer is a clean instance of it.

The rule instead:

| Question is… | Behaviour |
|---|---|
| **Irreversible** in effect | **Escalates**, regardless of whether a model could answer it |
| A **decision** rather than a fact | **Escalates**, regardless of whether a model could answer it |
| A fact, reversible, checkable | May be answered by the LLM layer |

The distinction that does the work is **decision versus fact**. "What is the
retry limit in `config.yaml`?" is a fact; it has an answer in the repository
and a wrong answer is caught by looking. "Should this retry?" is a decision;
it has no answer in the repository, and an LLM producing one is not retrieving
it but *making* it — which is the thing a human is in the loop for.

Both rows are stated in terms of the question's category, which is
inspectable, rather than the answerer's certainty, which is not.

### 3. A decision recorded in Slack is a decision lost

Slack is **searchable and not durable**, and those are different properties.
The reasoning behind an answer scatters across a thread, the thread ages out
of attention, and six weeks later the artifact of the decision is a message
someone has to know exists in order to find. Nothing reads it at the moment it
matters, which is when the next Worker meets the same question.

This release's entire lesson is the other half of that sentence: **decisions
must be written where they are read.** rite already has the places — §13's
decisions register, with a supersession convention requiring a reversed or
narrowed decision to say so in its own cell; `kb/` (§8.7) for authored
standards, committed because they are a team asset; and
`coordination/message_log.py`, which `RITE_LOCAL_DESIGN.md` section 6.4.1
already uses to carry what a duty has completed through the state layer
(D-20).

So the design's output must be **a recorded decision in the repository, not
merely a reply in a thread.** The chat message is the transport; the artifact
is a commit. Concretely, an answered escalation should produce:

- the **question** as asked, with the ticket and Worker that raised it;
- the **answer**, and enough of the thread's reasoning to survive the thread;
- **who** answered, and when;
- a **link back** to the thread, for the parts that did not survive.

**This is the real prize in Robert's note about improving how decisions are
propagated and recorded**, and it connects directly to 0.7.0: §9.13.2 already
makes **each row of the decision register its own retrieval unit** ("a
register that is one unit is the whole register on every retrieval"), so an
answer committed as a register row is retrievable by the existing digest with
nothing new built. That is a stronger argument for the register than for
`kb/`, and it is the one to test first.

### 4. Who may answer is an authentication question

**A reply in a channel steers autonomous agents.** That makes the channel a
control surface, and chat platforms are not built to be one: membership is
usually wider than the engineering team, guests and integrations can post, and
in most workspaces there is no notion of *authority to decide* attached to the
ability to type in a room.

The concrete exposure, in ascending order of how ordinary it is:

- a **guest** in `#architecture` answers a routed question and a Worker acts
  on it;
- a **teammate without context** answers helpfully and wrongly, in good faith;
- **another bot** replies in-thread and the Owner's scanner treats it as a
  human answer.

The third is worth flagging now, because it costs nothing to prevent at design
time and is awkward afterwards: a reply-scanning Owner needs to know which
replies are from humans at all.

**Recorded as an open question rather than solved here** (open question 3).
How much authentication is proportionate depends on the workspace, and that is
Robert's to judge.

### 5. Blocking and latency — rite already has the answer

A Worker that has asked a human a question may wait hours. Overnight, most of
a working day. A design that leaves the Worker holding its session open for
that is spending a session slot, a sandbox, and context on waiting.

rite's existing answer is the right one: **move the ticket to blocked, ticket
the unblocker, take other work.** §9.10's orientation decision table already
carries the row — *blocked tickets with unresolved blockers* → *surface
blockers to the human; work on unblocked items* — and §6.2 makes "blocked by"
a real structural link rather than a note, with D-49 putting `link(id,
target_id, link_type)` on the abstract `TicketBackend` interface specifically
so that a backend with no link mechanism must **return an error rather than
silently substitute a weaker one**.

**But note where that row lives: it is orientation, run at `rite start`.** It
describes what a Manager does when it *finds* blocked tickets, not what
happens at the moment a question is asked. The gap between those two is the
work this feature has to do.

Stating the rule here rather than leaving it to be rediscovered, because the
alternative is attractive in the small: a Worker holding its context while it
waits does not have to re-establish anything when the answer arrives, and for
a question answered in ninety seconds that is genuinely cheaper. It stops
being cheaper the first time a question is asked at 6pm. Assume the 6pm case.

**One wrinkle that is not free, and §6.5 already says what the fix looks
like.** §6.5 records that rite has no configurable status vocabulary and that
the one it has is inherited from the upstream project's board — a known gap
rather than a decision. "Move it to blocked" assumes a status that exists on
every backend. §6.5 also names the shape of the remedy so it is not
rediscovered from scratch: a `ticket_backend.statuses` mapping from the
project's own column names onto the sets that matter, rather than
`GitHubBackend`'s two hardcoded sets. Whoever builds this should start there.

### 6. The polling half already has a home, and it is not an LLM turn

Robert's shape says the Owner posts "through an MCP if one exists". **D-1 says
Python over REST, no MCP dependency in the product** — because every MCP call
costs an LLM turn, heartbeats and polling and labels are nearly all the
traffic, and MCP is rate-limited at 500 requests/hour on the free tier.

D-1's argument is **about traffic volume**, and the two halves of this design
sit differently against it:

| | D-1's subject (heartbeats, labels, polling) | Posting a question | Scanning for replies |
|---|---|---|---|
| Volume | Constant, per-interval | Rare — Analysis 2's filter means only hard questions arrive | **Constant, per-interval** |
| Per-call LLM turn | Ruinous | Roughly free at this rate | **Ruinous** |

So posting through an MCP may be fine on D-1's own reasoning. Scanning is
exactly the traffic shape D-1 exists to keep off an LLM turn.

**And rite already has the component for it.** §3.5 specifies the watchdog: "a
plain script, not a Claude session, runnable every `watchdog.interval_minutes`
(§8.3, default 5)" that wakes the Manager only on a stalled session, a blocker
in the outbox, **or a decision queued with no response** — "no LLM call
anywhere in the check itself". It replaced a sweep measured at **~484K tokens
per wake-up** (D-37).

Two consequences:

- **The scan belongs in the watchdog, and that satisfies D-1 without narrowing
  it at all.** A zero-token poller is not the thing D-1 forbids.
- **§3.5's "a decision queued with no response" is already the trigger** open
  question 2 asks about. The SLA question is therefore not "does anything
  notice" — it is "how many intervals, and which action fires".

Whether D-1 governs the Owner↔human channel at all is open (open question 5):
its stated subject is "Manager ↔ Owner transport", which is a different
channel. Note also that the OAuth re-prompt frequently quoted alongside D-1 is
in §3.2's prose, not in D-1's own cell.

---

## Open questions, for whoever builds it

Not objections. Several of these are Robert's.

1. **Does the category rule (Analysis 2) need a model to apply it?**
   "Irreversible, or a decision rather than a fact" is a judgement about the
   question, and if an LLM makes that judgement then the filter meant to be
   confidence-independent has a model in it again. **Half of it is already
   enumerated:** §5.1.1 is an inspectable static list of irreversible
   operations, enforced by `tests/test_blast_radius.py`. That covers
   "irreversible"; it does not cover "a decision rather than a fact", and
   whether that half can be enumerated at all is the open part.

2. **How many `watchdog.interval_minutes` before a queued decision counts as
   expired, and which action fires?** Per Analysis 6, §3.5 already supplies
   the trigger, so what is missing is a number and a branch: re-post, escalate
   to the default channel, escalate to a named person, or leave the ticket
   blocked indefinitely. They behave very differently at 3am, and the design
   chooses none. An owner for the choice is missing too.

3. **Who may answer, and how does the Owner know?** From Analysis 4.
   Sub-questions a builder meets immediately: is a reply from any channel
   member authoritative; is there a per-channel allowlist; how are bot replies
   distinguished from human ones; and does an answer that steers an
   irreversible action (per open question 1, §5.1.1's list) need a second
   confirmation. Deliberately unresolved — proportionality depends on the
   workspace.

4. **Where does the recorded decision live?** Analysis 3 argues the artifact
   must be a commit, not a thread, and that §9.13.2's per-row retrieval unit
   favours §13's register. It does not choose between §13 (heavyweight,
   project-wide, versioned, with a supersession convention), `kb/` (§8.7,
   authored team knowledge), and `coordination/message_log.py`
   (`RITE_LOCAL_DESIGN.md` section 6.4.1, already carrying completion records
   through the state layer under D-20). Routine answers probably do not belong
   in §13 and architectural ones probably do, which suggests the routing input
   is the *channel* the question went to — a guess, not a decision.

5. **Does D-1 govern this channel at all?** Its subject is "Manager ↔ Owner
   transport"; the Owner↔human relay is a different channel, so the outcome
   may be a **new** register entry rather than a narrowing of D-1. If it is a
   narrowing, §13's convention says D-1's own cell records it. Either way the
   entry should say why scanning runs in §3.5's watchdog rather than on an LLM
   turn.

6. **What does the Owner do while a channel is unreachable?** Slack down,
   token expired, adapter misconfigured, channel renamed. The failure that
   matters is the silent one: an Owner that cannot post but believes it did,
   leaving a Worker blocked on an unblocker nobody will ever see. That is
   `DEFECT_CLASSES.md` class 1 — the instrument reporting success while
   measuring nothing — and a post-then-verify read is the obvious shape, at
   the cost of a second call.

7. **Does the relay payload pass `rite publish check` (§11), and does the gate
   even watch this path?** A question carries the context that makes it
   answerable, which may be source, customer data, or credentials-adjacent
   detail. §11.2's gate "scans **all committed content**" — committed being
   the operative word. **A relay posting context into Slack is content leaving
   the repository by a route §11 does not watch at all**, which is a gap in
   the gate rather than a finding against it, and chat channels are frequently
   wider than the repository's read access. Same class of question as
   `V070_MEMORY.md` open question 3, and the harder instance of it.

8. **What happens to a message that arrives while no Manager is running?**
   ⚠ *Answered for Slack in v0.6.0 by design (A5, 2026-09-25; built, and the
   delivery half not yet observed): the relay keeps each
   conversation's cursor across runs, so the message is delivered at the next
   start's first turn, and each run posts a stop line when it ends. Queued,
   not refused, and said. The general question below stands for other
   channels.*
   The common case overnight, not an edge one. Queue it and deliver on the
   next start; answer in the channel that nothing is running; or start a
   Manager on receipt — which turns a message into spend and needs a bound
   before it is even considered. Unresolved, and it interacts with
   property 1: a queue that does not survive a restart is not continuous.

9. **Does the Manager own the conversation, or something in front of it?**
   **Robert called this semantics and declined to pick, so it is recorded
   open with the tradeoff rather than decided here.**

   | | Manager owns it | A front owns it |
   |---|---|---|
   | Who replies | The thing that did the work — no translation layer | A process that can answer when no Manager is up |
   | When nothing runs | The conversation is dead | The channel still answers, and queues |
   | Cost | Every message is an LLM turn (§3.1.1, D-1) | Cheap, always-on |
   | Risk | Nothing answers the User for hours | A second thing that can speak, which must never appear to speak FOR a Manager it is not |

   The second column is close to §3.5's watchdog in shape — a zero-token
   process that wakes the expensive one only when judgement is required —
   which is an argument for it, not a decision.

10. **How is a chat-agreed amendment written down, and by whom?** Open
    question 4 already asks WHERE a recorded decision lives. This asks who
    writes it, and the two are not the same: a Manager that writes its own
    scope amendment is editing what it will later be reviewed against,
    which is exactly the channel `RITE_LOCAL_DESIGN.md` section 5.4 closes
    — *"instructions flow from a reviewer to what it reviews, never the
    other way."* If the Manager writes it, that rule needs an explicit
    carve-out for User-agreed scope and a way to tell the two apart.

11. **What stops the channel becoming a way for a Manager to ask the User
    things it should have worked out itself?** Analysis 2 is about
    escalating too little. This is the opposite failure, and **the same
    filter cannot be tuned for both** — loosening it to catch confident
    wrong answers is exactly what invites questions a file would have
    answered. Robert's *"do you have all the information necessary to
    answer it straight away?"* is the candidate test and currently has no
    mechanism: nothing measures whether the Manager looked.

