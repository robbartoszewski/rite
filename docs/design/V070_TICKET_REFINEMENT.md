# Ticket refinement: a thin ticket is refined with the User before work starts

**Status: DESIGN, 2026-09-28. Nothing in it is built.** Track TR of
`V070_RELEASE_PLAN.md` carries the tickets. Read against `origin/main` at
`a75469d`; every line cited was re-checked at `9c03f3c` for the TRQ1 and
label revision of 2026-09-28, and moved citations were updated. Every file and line cited below was read there, and the files that
matter are unchanged from the `v0.6.0` tag (`3400dec`), which is what the
dogfood runs. Where a sentence says what rite *does*, it names the file.
Where it says what rite *will* do, it is this design and says "not built".

**Citation convention**, as in the release plan: a bare `§` is a section of
`SPEC.md`. This note's own parts are "part 3", never `§`.

## Part 0 — the ruling, and the requirement in Robert's words

Robert, 2026-09-28, verbatim, stating the acceptance criterion for the
v0.6.0 dogfood run:

> "I expect rite to refine the lazy tickets before proceeding - ask user
> questions, define a definition of done etc."

And, once the audit below showed rite does not:

> "Yes, I expect the auto-refinement in v0.7.0. Please create a spec and
> implementation plan in a parallel session"

**Decided:** auto-refinement is v0.7.0. **Also decided, 2026-09-28 (TRQ1):
refinement is enforced, as the standard, everywhere.** Robert, verbatim:

> "5. There aren't really any 'existing projects' so let's just implement it
> as a standard. Also, I think adding a label to tickets that are ready to
> work on (you can come up with a name for it) could be useful. For example,
> it makes it easy for the owner to filter tickets that are ready to be
> assigned vs. the tickets that need refinement"

**The second half of that ruling is a new requirement: the `ready-to-work`
label, part 3.10.** It is a view derived from the signed record, never the
authority.

**Also decided, 2026-09-28, the same day: what `scheduled` means once
refinement exists.** Robert, verbatim:

> "On 5 - I think that scheduled and refined aren't necessarily in conflict
> here. We can say that scheduled + not refined => owner starts refinement
> procedure, scheduled + refined => owner assigns it to a Manager"

So **`scheduled` without a record is not an error: it is what starts
refinement.** `scheduled` with a valid record is what the Owner may assign.
Enforcement is exactly as strong as before: no Manager is assigned the
work, and no Worker starts, without a valid signed record. What changed is
its shape. A gate that *assigns* now hands an unrefined ticket to
refinement, and only a gate that tries to *work* one refuses (part 3.7).
Part 3.4 step 0 says what bounds the Owner, so that twenty unrefined
tickets do not become twenty conversations at once.

**Robert's answers to TRQ2–TRQ9, 2026-09-28, verbatim:**

> "1. Limits (TRQ2) - the limits sound good. We can make them configurable
> for advanced users
> 2. Accept words (TRQ3) - Let's have a list of words that are also
> configurable. the current list looks good. I think I will add "proceed"
> 3. "You decide" (TRQ4) - Yes, let's have rite come back with a
> recommendation for a final confirmation
> 4. Tickets you already wrote properly (TRQ6) - Can we create some
> guardrails against overriding user input without an explicit permission?
> 5. Where questions appear (TRQ8) - DMs or a dedicated private channel with
> @rite invited. Let's make it configurable
> 6. Test commands in a definition of done (TRQ9) - makes sense
> 7. Work routed without a ticket (TRQ5) - Can we just ticket all work that
> Workers do? (in JIRA that would be chore tickets I guess)
> 8. Two Managers both managing the board (TRQ7) - I'm confused about this
> issue existing. Doesn't "Owner owns the board and assigns tickets.
> Managers manage only the tickets assigned to them" solve this?"

Where each landed:

| TRQ | ruling | where |
|---|---|---|
| TRQ2 | the limits as proposed, configurable | part 3.11 |
| TRQ3 | `ok`, `yes`, `accept`, `lgtm`, **`proceed`**, configurable | part 3.11 |
| TRQ4 | "you decide" gets a recommendation back, for a final confirmation | part 3.4 step 4 |
| TRQ6 | **reframed** as a guardrail: no agent overrides user input without explicit permission | part 3.13, and TRQ12 |
| TRQ8 | the DM, or a dedicated private channel with the app invited, configurable | part 3.12 |
| TRQ9 | Verify commands optional but explicit | unchanged from the recommendation |
| TRQ5 | **every piece of Worker work carries a ticket**; `--no-ticket` is withdrawn | part 3.14, and TRQ11 |
| TRQ7 | **dissolved**: the Owner owns the board. Refinement is the Owner's, which rite already makes singular | part 3.3 |

**Robert's answers to TRQ10–TRQ12, Q4, and commit authorship, 2026-09-29,
verbatim where given:**

- **TRQ10: "Allow it."** A session running as him may approve, and the
  record says `attested`, not "confirmed by you". The Slack DM stays the
  strong path, because a local model cannot write to it. The label must be
  findable later.
- **TRQ11:** "In most cases, I think the Owner should ask follow up
  questions (if it has any doubts) and refine straight away. If the User
  doesn't reply within some timeout, create a chore with what it's got and
  then refine later".
- **TRQ12:** "Can't the User control it by choosing if they give rite their
  token or create a separate account for them?"
- **Q4** (whether "don't implement tickets yourself" binds executor
  secondaries): "Yes, close the bypass. However, instruct that it's meant
  for chores and trivial tickets. Any serious work should be passed to
  workers."
- **Commit authorship:** leave it, on the same principle as TRQ12.

| ruling | what changes | where |
|---|---|---|
| TRQ10, (a) | an unsandboxed session may attest; the label is findable on the board and in `rite refine status` | part 3.6 |
| TRQ11, reshaped | ask at once when in doubt, refine in place; **silence makes an unrefined chore holding exactly his words**, never a parked ticket and never an invented definition of done | parts 3.4 step 7 and 3.14 |
| TRQ12, premise rejected | **rite builds no identity management**: it works under either credential choice, says which one is in use, and enforces the guardrail when the choice makes it possible | part 3.13 |
| Q4, (c) plus scope | an executor may commit, but only on a ticket-named branch through a PR, and its instructions say that path is for chores and trivial tickets | part 3.5, and TR3 and TR10 in the plan |
| authorship | documented, not built | part 3.13 |

**The principle behind TRQ12 and authorship, written once:** rite does not
invent identity. It reports which identity is in use and what follows
from it (part 3.13).

**Nothing is open with Robert on this track.**

**The test, in his words, is the one this design is held to:** a lazy
ticket, and a User who answers lazily, end in either a definition of done
the User agreed to, or a ticket that visibly waits for them. Never in work
started on a guess.

## Part 1 — what rite does today

From the read-only audit of 2026-09-28, against `v0.6.0` and `origin/main`.
There are three categories, kept separate.

| behaviour | today | where |
|---|---|---|
| A Manager asks the User clarifying questions about a ticket | **Absent.** The channel exists (`rite reply`, `mailbox.how_to_reply`). The rule about *when* to ask a question the Manager already has exists (`checkins.RULE`, `checkins.py:49`: "ask now unless the question is clearly deferrable"). Nothing tells a Manager to form questions about a thin ticket | `managers/prompt.py:65`, `managers/checkins.py:49`, `managers/mailbox.py` |
| A definition of done is established | **Instructed, in the wrong places.** `/refine` asks for one, and nothing tells a Manager to run `/refine`. The README frames it as the human's step "in your Dispatch session". `/ticket` step 1 and the Worker's `CLAUDE.md` name it only as a reason to *stop*: "say so and stop rather than guessing" | `templates/commands/refine.md`, `templates/commands/ticket.md:8`, `workspace/manage.py:773`, README "3. Tickets" |
| Anything refuses a ticket without one | **Not enforced, not even as an advisory check.** `rite board create --description` defaults to `""` (`cli/main.py:3523`). The loop's ready set is "labelled `scheduled`" (`loop/__init__.py:428`). The broker checks only that the ticket exists on the board (`managers/broker.py:168`). Nothing in `src/` reads a ticket's content to decide anything | as named |

**What the dogfood run observed** (the run's own findings file, outside
this repository, section R, 2026-09-28):

- The Claude Owner routed and assigned one-line tickets without a question
  about any of them. It could not read the Jira ticket at all (finding
  F11), and it routed anyway on the User's gloss.
- The only refinement happened in a **Worker**, which did exactly what
  `/ticket` step 1 says. It stopped, and named the three unknowns in
  KAN-7 ("which timeout, what 'configurable' means … what the new default
  should be"). **Its question went to a file inside its sandbox, which
  nobody read.** It waited 180 s and stopped. Neither the User, nor the
  Owner, nor `rite status` saw it.
- Given a two-fragment answer typed into its pane, the Worker built a
  workable definition of done by *filling the gaps with defaults it chose*.
  That is useful, and it is also the thing this design must not allow
  silently: a definition of done the User never saw.

So the defect is not the models. **rite assigns refinement to nobody, and
the one question that did get asked had no route to a person.**

## Part 2 — the requirement, as properties

Each property is testable. The tickets in track TR are written against them.

- **P1: no work starts on an unrefined ticket.** "Work" means a Worker
  started on it, a route naming it, or a Manager working it itself.
  *Instructed* by TR3 and *enforced* by TR5, as the standard (TRQ1,
  decided).
- **P2: a definition of done is never invented.** Every record's definition
  of done is either text the User accepted as proposed, or text attested by
  a session running as the person outside every boundary (part 3.6 says
  what that can and cannot prove). There is no third route. A model's statement
  that a ticket is refined changes nothing; rite writes the record.
- **P3: a question reaches a person where they already look, and its answer
  is attributed to its ticket by rite, not by a model.** A question nobody
  can see is not asked.
- **P4: refinement is bounded and fails closed.** A bounded number of
  rounds and a bounded time. When the User is silent the work is neither
  lost, nor blocked, nor guessed at: it waits, visibly unrefined, and is
  refined when the User is back (TRQ11, decided). Refinement never makes a
  Manager wait: the ticket waits, and the Manager takes other work.
- **P5: the record lives on the board.** It survives every session, it is
  readable by a cold session on any engine, and a change to the ticket's
  text invalidates it.
- **P6: one predicate.** The Owner's automatic path, the human's `/refine`,
  `/ticket`, the Worker, the loop and the broker all ask rite's one function
  whether a ticket is refined. So one path cannot refine a ticket that
  another rejects as unrefined.
- **P7: nothing load-bearing depends on the engine.** A Goose Manager on
  `qwen3:8b` refines by the same protocol as Claude. Everything that decides
  an outcome is rite's deterministic code.
- **P8: no tolerated race.** Every decision is made on one read of the
  board. What is checked is exactly what is delivered. Where two things can
  happen in either order, both orders give a correct, stated result (part 4).

## Part 3 — the design

### 3.1 States

`rite refine status <ID>` computes the state from **one single-issue read of
the board** plus rite's local round ledger (part 3.4). It never uses a list
endpoint: those lag a new issue by about 2 s (DF4), and the single-issue GET
was measured consistent.

With Robert's semantics (part 0), each state of a `scheduled` ticket says
what the Owner does with it:

| state | means | may a Manager or Worker work it? | what happens to it, if `scheduled` |
|---|---|---|---|
| `NOT REFINED` | no valid record, and no round open | no | **the Owner starts refinement**, in turn (part 3.4 step 0) |
| `ASKING` | a round is open: questions sent, no attributed answer yet. Says which round of how many, and its deadline | no | in refinement; waits for the User |
| `PROPOSED` | the latest round carries a proposal the User can accept | no | in refinement; waits for the User's word |
| `REFINED` | exactly one valid record heads the chain, and it matches the ticket's current title and description | **yes** | **the Owner assigns it** to a Manager; `ready-to-work` is on it until then |
| `STALE` | a record exists, and the title or description changed after it was written | no | **the Owner starts refinement again**, like `NOT REFINED`; the round quotes the old record so the User sees what changed |
| `WAITING FOR YOU` | the User did not answer before the round's deadline (TRQ11, decided). The open question stands and is not asked again | no | **re-presented when the User is next active** (part 3.4 step 7), without spending a round; **uses no sessions** while he is away. Does not count against K |
| `PARKED` | refinement ended without agreement, and only a person can restart it. The reason is one of: `not agreed after N rounds`, `thread unreadable`, `not started by the Manager` | no | waits for a person (part 3.4 step 7); **uses no sessions** |
| `CONFLICT` | two records claim to be the head (part 4, race 3) | no | waits for a person; reported; **uses no sessions** |
| `UNREADABLE` | the board could not be read, the comment list could not be shown complete, or the record's signature cannot be checked | no | reported; **uses no sessions**, because a session cannot read what rite could not |

`UNREADABLE` is never read as `NOT REFINED` or as `REFINED`. An unreachable
board is not an empty board (D-74), and the same applies here.

### 3.2 The record: where it lives on the board

**The record is a board comment, and the description stays the human's.**
This is forced by the code, not chosen for taste:

- `update()` replaces the body wholesale on both backends. It passes
  `--body` on GitHub (`tickets/github.py:157`) and PUTs `description` on
  Jira (`tickets/jira.py:308`), and there is no compare-and-swap on either.
  A refinement written into the description while a person edits the same
  ticket loses one of the two edits, and which one depends on which lands
  last. That is exactly the race this project does not tolerate.
- Comments are append-only on both backends (`comment()`,
  `tickets/interface.py`). A new record never destroys anything.

**What a record carries**, as a fenced block of canonical JSON that rite
writes, with a human-readable rendering above it. rite renders both from
the same JSON, so the two cannot disagree:

- `ticket`, `record_id`, `supersedes` (the `record_id` of the head that
  rite read before writing, or `null`);
- `title_sha256` and `description_sha256`: of the title and description as
  rite's read path returns them. Labels are excluded, because rite itself
  changes labels;
- `definition_of_done`: a checklist, one item or more. **Required.**
- `verify`: commands, or the explicit value `"none agreed"`. Optional, but
  never silently absent (TRQ9). A Worker started on a `"none agreed"`
  record is told so, and it must say in its report how it checked each
  item;
- `scope_in` and `scope_out`: optional;
- `provenance`: one of
  - `accepted`: the proposal's outbox id, the accepting message, both send
    times, and the channel (the Owner's DM, or this machine);
  - `attested`: a person ran `rite refine accept` at a terminal (part 3.6);
- `exchange`: the questions and the User's answers, **quoted as delivered**,
  so a cold reader sees why the definition of done says what it says;
- `mac`: an HMAC-SHA256 over the canonical JSON (everything except `mac`).
  The key is held outside every Manager and Worker boundary (part 3.8).

**Validity is one pure function of one read.** A ticket is `REFINED` if and
only if all of these hold:

1. the read returned the ticket and its comment list, **shown to be
   complete**;
2. the records' `supersedes` links form exactly one chain with one head;
3. the head's `mac` verifies;
4. the head's two hashes match the title and description in the same read;
5. the head's JSON is well formed: at least one done item, and a
   provenance of one of the two kinds.

Anything else maps to the specific non-ready state in part 3.1.

**No label is a source of truth.** Any label would be a second write beside
the comment, and the two cannot be made atomic. A label is also writable by
any person or token with access to the board. The status is computed. It is
printed by `rite board show` and in every instruction and start line that
names the ticket. The `ready-to-work` label Robert asked for (part 3.10) is
a *view* of that computed status, which rite keeps in step with it. **No
code path reads the label to decide anything.** *Not built.*

### 3.3 Who refines

**The Owner**: the one Manager holding `route` in the root
(`config/managers.py`, `routing_owner`). It is the only Manager that reads
Slack and talks to the User (SPEC §9.16.7). A lone Manager holds every duty
(`effective_duties`), so it is its own Owner.

Robert's TRQ7 answer is the rule: "Owner owns the board and assigns
tickets. Managers manage only the tickets assigned to them". **rite
already makes that role singular.** `routing_owner` returns the Owner
only when exactly one Manager holds `route`. With zero or several it
returns `""`, and nobody routes or reads Slack: fail closed.
`configuration_problems` reports that through `rite doctor`. So refinement
keyed to the Owner cannot have two refiners, and no new refusal is needed.
With no Owner, nobody refines: every unrefined `scheduled` ticket waits,
and the start line says why.

⚠ **Corrected 2026-09-28: TRQ7 came from this note's own mistake.** Its
first version gave refinement to "the Manager holding the `board` duty".
In the code, `board` has no consumer except the check on who can be Owner
(`{DECIDE, BOARD, ROUTE}` in `configuration_problems`), and no uniqueness
rule. So a second `board` holder can be configured today and does
nothing. Keying refinement to it is what made "two refiners" possible, so
Robert's confusion was well founded: the situation should not be
constructible, and keyed to the Owner it is not. A `board` duty declared
on a Manager that is not the Owner has no effect; `rite doctor` should say
so (TR5).

- **A secondary never refines and never asks the User.** It has no channel
  to them, and inventing one is the route the Worker's `question.json`
  took. It reports a gap to the Owner, which asks.
- **A Worker never refines.** It has no channel to the User either. Part
  3.7 says what it does instead.
- **Two refiners among Managers cannot happen**, because there is at most
  one Owner. A person running `/refine` at the same time as the Owner is
  still possible, and part 4's race 3 handles it.

### 3.4 The protocol: rounds

*Not built.* A **round** is one message from the Manager to the User about
one ticket that needs an answer. A refinement **attempt** is at most **N**
rounds (N = 3 by default, TRQ2, decided; configurable).

**0. What starts refinement, and what bounds it.** Under Robert's semantics
a `scheduled` ticket in `NOT REFINED` or `STALE` is refinement work for the
Owner, not a refusal. Unbounded, that is the failure the dogfood already
showed: a supervisor spent eight sessions in eighty seconds on tickets it
could not read. So the work is bounded four ways, each enforced by the
supervisor rather than asked of the model:

- **Order.** Tickets are taken oldest first, by the board's `created`
  time with the id as tie-break. Both come from the same read, so the
  order is deterministic. (Priority order is a later option, not this
  one.)
- **At most K refinements open at once per Manager** (5 by default, TRQ2).
  "Open" means `ASKING` or `PROPOSED`. A ticket beyond K stays `NOT
  REFINED`, is listed as "queued for refinement, position n", and is not
  handed over.
- **At most S new refinements started per Owner session** (3 by default,
  TRQ2), and **new starts are paced by the User.** A session is started *for
  refinement* only in two cases. The first is the first cycle that finds
  refinement work. The second is a cycle in which an attributed reply has
  arrived or a round's deadline has passed. Starts are never the *reason*
  for another session. So when the User answers nothing, rite adds no new
  questions after the first batch, and the number of sessions refinement
  can cause is at most one, plus one per reply, plus one per deadline.
  Twenty unrefined tickets on the first run therefore mean three
  conversations started, not twenty, and more only as the User engages.
- **A no-progress guard.** A ticket handed to the Owner for which no round
  was started by the end of that session counts one miss. After **two
  consecutive misses** it is `PARKED (not started by the Manager)`, is no
  longer handed over, and is reported as needing a person
  (`rite refine reopen <ID>` retries it). A Manager that cannot or will not
  start a ticket's refinement costs two sessions, not a session a minute.

`WAITING FOR YOU`, `PARKED`, `CONFLICT` and `UNREADABLE` never cause a
session. Every
refinement session also counts against the run's mandatory budget ceiling
(§9.14.5), which stays the outer bound.

**1. rite hands the ticket to the Manager.** At the start of each cycle the
supervisor, **outside the boundary**, reads each `scheduled` ticket once
(DF4's ledger decides the set, and a single GET reads each member) and
computes its state. Tickets in `ASKING` or `PROPOSED`, and those
`NOT REFINED` or `STALE` tickets that step 0 admits this session, are listed
in the Owner's instruction, with their title
and description (normalised, SPEC §6.6.1) and where each round stands.
**So a Manager never has to read the board to refine.** That is what makes
this work on Jira today, where a Manager cannot read the board at all
(F11, readiness Q3), because the supervisor can.

**2. The Manager asks, through rite, never through `rite reply`:**

```
rite refine ask <ID> --file <message.md>
```

rite **lints the message before it sends anything** and refuses it, saying
why, unless all of these hold:

- it has at most three questions, numbered;
- **from round 2 on it carries a proposal**: a definition of done the User
  can accept in one word. A lazy User is far more likely to accept a
  proposal than to answer a question, and a proposal is how the Manager's
  useful gap-filling (the Worker's, in part 1) reaches the User instead of
  staying private;
- every proposed item is tagged with its source: `ticket: "<quote>"`,
  `answer: "<quote>"`, or `proposed`. **Every quote must be an exact
  substring of the ticket text or of an answer rite delivered for this
  ticket.** A quote rite cannot find is refused.

rite then sends the message. rite writes the first line, not the model:
`<ID> · refinement, round k of N · reply in this thread`. The message is
written to the Manager's outbox, the same one `rite reply` writes, which
the Slack relay posts to the Owner's DM and `rite connect` shows. It is
**also posted on the ticket as a comment**, so a person reading the ticket
sees what was asked. On the proposal, items tagged `proposed` are rendered
by rite as *"proposed by <manager>, not from the ticket or your answers"*.
The User always sees which parts are the Manager's own.

**3. rite attributes answers deterministically.** An inbound message
answers ticket T only if either:

- it arrived in the Slack thread under T's round message. The relay's own
  header names the root (`slack.py:726`: "reply in the thread under …"),
  and the root's label is the first 40 characters of the posted text
  (`slack.py:944`), which rite made start with T's id; or
- it is a top-level message in the Owner's DM, or from `rite connect`,
  whose first token is T's id.

**Authority:** only a message rite's header marks `INSTRUCTION` (the
Owner's DM), or a header-less one (this machine), can answer or accept.
Broadcast-channel text is context and never answers (SPEC §9.16.5).

A message that matches no open round is delivered as an ordinary message.
If rounds are open, the Manager is told the message was attributed to no
ticket. **A model's judgement that a reply "obviously means RT-10" is never
used.**

**4. Accepting is a word, not a judgement.** A reply attributed to T whose
normalised text, with T's id stripped, is exactly one of the accept set
(TRQ3, decided: `ok`, `yes`, `accept`, `lgtm`, `proceed`, configurable,
part 3.11) accepts **T's latest proposal**, and only that one. Any other
text is an answer or a correction, and it feeds the next round. No model
decides whether "sounds fine I guess" is a yes. It is not; it is an
answer, and the next round proposes again.

**"You decide" (TRQ4, decided).** Robert: "Yes, let's have rite come back
with a recommendation for a final confirmation". A reply that hands the
decision back is neither an accept nor a reason to stop. **The Manager's
next round must be a complete proposal, presented as its recommendation,
asking for the one word.** Nothing is recorded until that word arrives, so
"never invented" (P2) holds unchanged. Deciding that a reply delegates is
the model's judgement. That is harmless here, because the worst a wrong
reading can cause is one extra proposal, never a record.

⚠ **It spends a round.** With N = 3: questions, then "you decide", then the
recommendation, then `ok` fits exactly. A User who delegates, is shown a
recommendation, and corrects it reaches the limit one round sooner than
one who answers. So a Manager that receives a delegation **must propose in
that same round and ask nothing further.** The lint enforces this: a
round-2-or-later message must carry a proposal (step 2).

**5. rite writes the record, outside the boundary.** On an accept, the
supervisor re-reads T with a single GET and checks two things. The first is
that the description hash still equals the one the proposal was made
against; if not, it refuses to write and tells the User that the ticket
changed since the proposal. The second is that the head it read is the one
the new record will supersede. It then posts the signed record, **reads it
back**, and confirms `REFINED` from that read before telling anyone. Only
then is the Manager told `<ID> refined: record <id>`. A failed write leaves
the round `PROPOSED (accepted, not yet written)`. It is retried at the next
cycle and said at each one.

**6. A partial answer.** Answered questions are fixed: their quotes join
the ticket's answer record in the ledger. The next round asks only what is
still open, and it proposes. Worked example with the dogfood's KAN-7
("timout is way too long, make it configurable or smth", no description):

- *Round 1* asks: which timeout (file or call)? a flag, an environment
  variable or a config key? the new default, or unchanged?
- The User answers "the http one in main.py. just make it a flag". That is
  verbatim what the dogfood's User typed, though into the Worker's pane,
  since no other route existed.
- *Round 2* proposes:
  1. the HTTP request timeout in `main.py` is set by a command-line flag
     (`answer: "the http one in main.py"`, `answer: "just make it a flag"`);
  2. the flag is `--timeout <seconds>` (*proposed*);
  3. with no flag, the timeout is unchanged (*proposed*);
  4. a test shows the flag reaching the request (*proposed*).

  It ends "Reply ok, or correct any line."
- `ok` → the record is written. Silence → part 3.4 step 7. "make it 10s"
  → round 3 proposes again, with item 3 now quoting the answer.

**7. No answer: the work waits, visibly unrefined, and nothing is invented.**

Robert, TRQ11 (decided 2026-09-29): "In most cases, I think the Owner
should ask follow up questions (if it has any doubts) and refine straight
away. If the User doesn't reply within some timeout, create a chore with
what it's got and then refine later".

- **Refine straight away.** When an instruction or a ticket reaches the
  Owner, its first round goes out in that same session. It asks questions
  if the Owner has doubts, and it is a proposal to accept if not. A round
  is open until the earlier of an attributed reply and its **deadline**:
  24 h by default, or the close of the next check-in window if that is
  sooner (TRQ2, configurable).
- **Silence is WAITING FOR YOU, not PARKED.** At the deadline the ticket
  becomes `WAITING FOR YOU`. That is declared only once rite has read the
  thread past the deadline and found nothing sent before it, judged by
  Slack's send time (`_Relayed.sent_at`), not by when rite polled (part 4,
  race 6). Until then it stays `ASKING (deadline passed, thread not yet
  read)`. A ticket waiting for the User:
  - stays unrefined and visible: never `ready-to-work`, never assigned,
    never given a Worker;
  - is **not asked again** while he is away. There are no reminders, and it
    starts no sessions;
  - **does not count against K**, the cap on open refinements, so silence
    on one ticket never blocks refinement of the others.
- **When he is back, the question comes back, and it is not a new round.**
  "Back" is an observable event, never a model's guess: the next message
  rite delivers from him (`INSTRUCTION` or header-less), or his reply to a
  check-in. At that point every ticket waiting for him has its open
  question re-presented, once, in the same thread with the same round
  number. RP1's delivery confirmation already brings unconfirmed action
  items back at each check-in, so this is that mechanism, not a second
  one.
- **A chat instruction becomes a chore, and silence does not lose it**
  (part 3.14). If he has not replied by `chore_after_minutes` (default 60,
  configurable), rite creates the chore **with exactly his words**, marked
  unrefined, and refinement continues on it.
- **What still parks, meaning only a person can restart it:**
  - **PARKED (not agreed after N rounds):** he was there, answered, and N
    rounds did not reach an accepted definition of done;
  - **PARKED (thread unreadable):** the relay was down past the horizon, or
    the root was dropped. This is said as that, and never as "no answer";
  - **PARKED (not started by the Manager):** part 3.4 step 0's no-progress
    guard.

**What TRQ11 changes in the round limits:**
- **N = 3 stays the cap on rounds with a User who is answering.** Being
  re-presented after an absence does not consume a round, so a User who is
  away for a week comes back to the question he left, not to a fourth
  message about it.
- **The deadline no longer ends refinement.** It only moves the ticket from
  asking to waiting.
- **K counts only rounds still inside their deadline,** so tickets waiting
  for the User never crowd out new ones.
- **S, the per-session start cap, is unchanged.**
- **One new setting:** `chore_after_minutes` (part 3.11).

**Parking** posts once, to the User's action destination (RP1; the
Owner's DM until RP1 lands) and on the ticket as a comment. The notice says
what is still missing and the one thing that resumes it.

**Resuming a PARKED ticket** needs a person's action, so it cannot loop:
- a reply attributed to the ticket. The notice says to reply in the DM
  starting with the id, because an old thread may be past the horizon;
- `rite refine reopen <ID>`;
- or an edit to the ticket's title or description, which changes the hash
  and starts a new attempt from `NOT REFINED`. **A User who fixes the
  ticket themselves has resumed it.**

**8. It never blocks the Manager, and it never reads as an empty board.**
Refinement is per-ticket state. While a ticket is `ASKING`, the Manager
works anything that is `REFINED`. The loop gains two verdicts, and
**`idle` is reserved for a board with nothing `scheduled` at all**:

- **`refining`**: step 0 admits refinement work this cycle. A session
  starts, as for `ready`.
- **`waiting-on-user`**: every `scheduled` ticket is `ASKING`, `PROPOSED`,
  `WAITING FOR YOU`, `PARKED`, `CONFLICT` or `UNREADABLE`, or is queued
  behind K. Nothing is in
  flight either. Its line names each ticket and what it waits for.

⚠ **This corrects a defect in this note's first version.** There, the loop
counted only `REFINED` as ready and had no `refining` verdict. A board
whose `scheduled` tickets were all unrefined would have read `idle`, "the
board has nothing ready", which ends the run
(`supervise.py`, `CONTINUE_VERDICTS`). No Owner session would ever have
started to refine anything. On `waiting-on-user` the supervisor waits **without starting sessions** until an
attributed reply arrives or a deadline passes. **That wait already exists:
DF2 built it in v0.6.0** (`8925c80`, `35beafc`: on a stop verdict,
`supervise._reason_to_wait` asks `waiting.reason()`, and when that is not
empty `_wait_for_mail` waits and spends no session; covered by
`tests/test_mail_causes_a_cycle.py`). TR2 adds one reason to it, "a
refinement round is open", instead of building a second wait.
`waiting-on-user` joins `STOP_VERDICTS` and `refining` joins
`CONTINUE_VERDICTS` (`supervise.py:142–143`). The dogfood's F12 (five
sessions in five minutes, none touching a ticket) is what happens without
the wait.

### 3.5 The two contradictions in today's text, resolved

**(1) `routing.py:484`, the Owner's routing instruction.** Today:

> "Route only what a person gave you authority for, and write each
> instruction so it can be done without asking you back."

That sentence stays true, and it now says where the asking goes: to the
User, **before** routing. Replace it with (*not built*):

> "Route only what a person gave you authority for. Ticket work goes with
> its ticket: `rite route <manager> --ticket <ID> "<what to report back>"`,
> and rite attaches the ticket's refinement record, so the other Manager
> works from the definition of done the User agreed and not from your
> summary of it. If what you were asked for is missing something you would
> otherwise have to guess, ask the User before you route — never route a
> guess, and never leave the other Manager to ask: it cannot reach the
> User. Asking back belongs before routing. After routing, the other Manager
> needs nothing from you that the record does not carry."

The secondary's half gains (*not built*):

> "If a routed ticket's record cannot be met as written — it names a file
> that does not exist, or two items contradict — reply saying exactly
> that, and stop. Do not fill the gap yourself: the Owner takes it back to
> the User."

**Q4, decided 2026-09-29: an executor Manager may do the work itself, and
only through the ticket.** Robert: "Yes, close the bypass. However,
instruct that it's meant for chores and trivial tickets. Any serious work
should be passed to workers." The rule and its enforcement:

- An executor Manager may commit a ticket's work itself only when the ticket
  is REFINED. It works on a branch named for the ticket, and the work goes
  out through a pull request. Enforced at PB1's publish step (TR10): rite
  publishes nothing whose ticket is not REFINED.
- Its instructions add the scope (*not built*): "Doing a ticket yourself is
  for chores and trivial tickets. Anything more is passed to a Worker: ask
  for one through the broker". This scope is **instructed**. rite has no
  measure of "trivial", and pretending otherwise would be a proxy.

**(2) `/refine` against `/ticket` step 1 (and the Worker's `CLAUDE.md`).**
The two disagree today because each makes its own judgement of "complete
enough". Both are rewritten to use the predicate:

- **`/ticket` step 1** becomes (*not built*):

  > "**Check the ticket is refined:** `rite refine status <ID>`. REFINED:
  > its record is this ticket's definition of done — scope, checklist,
  > Verify — and you work to it, not to the title. Anything else: do not
  > start. If the person is here in this session, refine it with them now
  > (`/refine <ID>`), then continue. Otherwise say which state rite
  > reported and stop. You do not judge whether a ticket is complete
  > enough; rite's status does."

  With one narrow exit, kept because it is real: a REFINED record that
  **cannot be met as written** (a named path that does not exist, two
  items that contradict) is reported with exactly that, and it goes back
  to refinement by `rite refine reopen <ID> --reason`. This is not
  "incomplete", and it is never a reason to invent the missing piece.
- **The Worker's "Your ticket"** (`workspace/manage.py:770`) becomes: it is
  started with the ticket id **and its record** (part 3.7). No record means
  stop and say so. It never refines, because it has no way to reach the
  User.
- **`/refine`** is now the same protocol with a person present: it asks
  the person in the session, drafts the record with the same tags and the
  same quote rule, and **ends at `rite refine accept`** (part 3.6). Its
  step 5, "Do not start the work", stays.
- The root `CLAUDE.md` gains, under "Working the queue": "A ticket rite
  does not report REFINED is not ready, however it is labelled." Under
  "Commands", `/refine` stops being described as a user-only drafting aid.

**Why a ticket cannot now be refined by one path and rejected by another:**
there is one predicate, and no path keeps a judgement of its own. The one
exit left (the record cannot be met as written) is not a rejection for
incompleteness. It reopens refinement explicitly, with its reason on the
ticket, and the next record supersedes the old one.

### 3.6 The human paths

- **`rite refine accept <ID> [--file <draft>] [--as-written]`**, run on
  the host. `--as-written` accepts the ticket's own text, for a ticket a
  human wrote properly. The command reads the ticket once, shows the
  definition of done and the hash it binds to, and signs the record.
  **What protects it is the key, not the terminal.** A sandboxed Manager
  or Worker cannot read the key, so it cannot sign (to be measured, TR0).
  **Outside every boundary, rite cannot tell the person from a model
  running as the person.**

  ⚠ **Corrected 2026-09-28: a `/dev/tty` confirmation is not a barrier.**
  This note first said `accept` would read its confirmation from
  `/dev/tty`, and that this "makes the step physically the person's". It
  was measured in this session's own Bash tool (Claude Code 2.1.280 in the
  desktop app, macOS):
  - with no terminal, `open("/dev/tty")` fails with `ENXIO`, "Device not
    configured". That much was as expected;
  - **but the model can make one.** `script -q /dev/null python3 probe.py`,
    with the answer written in after a second, gave the probe a
    controlling terminal, and it read `RT-10` from `/dev/tty`. Python's
    `pty.fork()` did the same. Both are ordinary tools a Bash-capable model
    has.

  So the prompt is dropped. A confirmation a model can type proves
  nothing, and presenting it as a gate would be the proxy error this note
  exists to avoid.
- **The Dispatch session**, and any other unsandboxed session running as
  the person (the Claude app's Code tab, Claude Code in a terminal), **can
  therefore run `accept` itself.** rite cannot see its conversation, so
  the provenance says `attested`, which means "run as the person, outside
  any boundary". It never means "confirmed by a person". `/refine` tells
  that session to ask the person and to run `accept` only after an explicit
  yes; that is **instructed, not enforced**.

  **Decided 2026-09-29 (TRQ10): "Allow it."** Two consequences follow:
  - **The Slack DM stays the strong path.** An `accepted` record is one the
    User accepted in the channel a local model cannot write to. An
    `attested` record is one a session running as the person signed. Both
    make the ticket REFINED; only the first says a person was there.
  - **The attested label is findable later.** It is recorded three ways:
    - the record's provenance: `kind: attested`, when, and the host;
    - the comment's text, which says in words that the approval was
      attested, not confirmed by the User, and carries the fixed token
      `rite-attested`. So a search finds every one: `"rite-attested"
      in:comments` on GitHub, `comment ~ "rite-attested"` in JQL. Neither
      query is measured yet (TR0);
    - `rite refine status`, which prints the provenance with every REFINED
      result, so an attested approval is never shown looking like a
      confirmed one.
- **The Dispatch session** (the person's own Claude session at the project
  root) is not a Manager and rite cannot see its conversation. So its
  `/refine` ends at `rite refine accept`, and the provenance says
  `attested`, not `accepted`. rite does not claim to know what was said in
  that session.
- **A ticket that already has a definition of done in its description.**
  TRQ6: under the fail-closed default it still takes one round, "RT-10
  already has a definition of done: <quoted>. Reply ok to start it as
  written", or a person's `rite refine accept --as-written`. The
  alternative is to trust it when the description's last editor is the
  person and not rite.

  **Measured 2026-09-28, read-only: that alternative is not safe on GitHub
  today, and it is not available on Jira.**
  - **GitHub does report the editor.** GraphQL's `Issue.editor`,
    `lastEditedAt` and `userContentEdits` exist and were read on
    `rite-dogfood-board` (#1–#25). None of those bodies has ever been
    edited, so every one read `null` and zero edits.
  - **But three kinds of writer carry the person's own identity.**
    - The person, on the web or through `gh`.
    - rite's host-side commands, which use the person's `gh` login. Across
      98 issues and pull requests in Robert's repositories, all 132
      authors, editors and commenters read were `User` accounts, and none
      was a `Bot`, rite's own handover comments included.
    - **Workers.** They write with `github_token` or
      `sandbox_token_<worker>` (`sandbox/__init__.py`,
      `resolve_worker_token`), both a "GitHub personal access token"
      (`credentials/store.py:75`), which acts as the person who made it.
  - **Only Managers use a separate identity**: a GitHub App installation
    token (`managers/github_access.py`), whose writes should appear as the
    App's bot. **This is not observed.** No `Bot` actor appears anywhere
    in his repositories, and minting an App token to edit a ticket was not
    done: it would handle the App's private key and write to a board in
    use.
  - **So "last edited by Robert" cannot be told from "last edited by a
    Worker"** on GitHub. A Worker working RT-10 could add a "definition of
    done" to RT-11's description, and it would read as Robert's. Trusting
    by editor identity would need Workers to write under their own
    identity too (App tokens, or a separate machine account). That is a
    change to Worker credentials, not a measurement, and it is not
    proposed for v0.7.0.
  - **Jira:** Managers hold no Jira credential (F11). Workers carry the
    person's own Jira API token: a Worker's launch line carries "the GitHub
    token, the JIRA token", measured on yoloAI and recorded in
    `sandbox/__init__.py` (`redact_secrets`). So there is no identity to
    compare either.

  The one round, or a person's `--as-written` at a terminal, stays the only
  safe skip.

### 3.7 Everything that consumes the predicate

*Not built.* Both halves are standard (TRQ1, decided). Under Robert's
semantics (part 0) **each gate does one of two things with an unrefined
ticket: an assigning gate routes it to refinement, and a working gate
refuses it.** The "unrefined" column says which, and what it prints.

| consumer | change | unrefined `scheduled` ticket |
|---|---|---|
| **the Owner's assignment to Managers** (`scheduler/__init__.py:617`, which also lists `scheduled`) | assigns only REFINED tickets. It is a third reader of readiness, beside the loop and the broker, and it must not have its own rule | **routes to refinement.** `NOT REFINED`/`STALE`: "RT-10: starting refinement" (or "queued for refinement, position n", behind K). `ASKING`/`PROPOSED`: "RT-10: in refinement, round k of N, waiting for you". `WAITING FOR YOU`: "RT-10: waiting for you (asked <when>)". `PARKED`: "RT-10: parked (<reason>); reply `RT-10 …` to resume". `CONFLICT`/`UNREADABLE`: "RT-10: needs a person: <why>". Never assigned, never refused |
| **loop** (`loop/__init__.py:424`, `_ready`) | REFINED counts as ready for Workers. Unrefined `scheduled` tickets are refinement work: the `refining` verdict, bounded by part 3.4 step 0 | **routes to refinement**, with the same lines. Never `idle` while anything is `scheduled` |
| **broker** (`managers/broker.py:168`) | a Worker request is an attempt to *work* the ticket | **refuses**: "refused: RT-10 has no agreed definition of done yet (NOT REFINED). It is in the queue for refinement; ask for a Worker once rite reports it REFINED." The state and its round are named |
| **`rite route --ticket`** (new flag) | a route is an attempt to have another Manager work it. On REFINED it attaches the record snapshot, as the Worker launch does | **refuses**, in the broker's words |
| **Worker launch** (`cli/main.py:5760`, `prompt = f"Work ticket {ticket}."`) | The supervisor reads the ticket once, checks it, and puts **the record itself** in the start prompt: id, checklist, scope, Verify. What was checked is what was delivered, so there is no window between the check and the Worker's read (part 4, race 4) | **refuses**, in the broker's words; reached only if something bypassed the broker |
| **assignment to a Worker** (`coordination/distribution.py:164`, which removes `scheduled` when it adds the Worker's label) | removes `ready-to-work` in the same `label()` call (part 3.10) | not reached: only REFINED tickets are distributed |
| **`rite board show`** | a status line and the record | shows the state and what happens next |
| **`/ticket`, the Worker's `CLAUDE.md`, `/refine`, the prompts** | part 3.5 | instructed: `/ticket` with a person present refines with them; otherwise it says the state and stops |
| **the review checklist** (`templates/review-checklist.md:202`) | its "would this ticket's definition of done still be met" test now has a definition of done to read: the record | — |
| **the Worker's stop** | today it lands nowhere (`question.json`, part 1). It must reach the Manager, and through the Manager `rite status`. **Whether a sandboxed Worker's handover or reply reaches the host today is not established here.** TR4 establishes it before anything relies on it | — |

**Every path that starts a Worker on a ticket must go through the broker's
check, and every reader of readiness must use the predicate.** TR5
enumerates both first (the loop, the broker and the scheduler's assignment
are the three readers known today), with a test that fails when a new one
appears. That is MM1's enumerate-first practice, because
`state.py` once claimed "every state file" and missed four writers.

### 3.8 Board by board

| | GitHub Issues | Jira |
|---|---|---|
| Can a Manager read the board? | Yes | **No** (F11: `CredentialStoreError` inside the sandbox; readiness Q3 open) |
| Who does refinement's board I/O | **the supervisor, outside the boundary, on both** | the same. The Manager is handed ticket text in its instruction and never reads the board to refine |
| Record | an issue comment; body Markdown with a fenced JSON block | a comment. ⚠ `JiraBackend.comment` sends the whole text as one text node in one paragraph (`tickets/jira.py:495`), and `adf_to_text` flattens on read. **Whether the JSON block round-trips is not measured.** The MAC is taken over the *parsed* canonical JSON, not the bytes, so whitespace changes are harmless; lost or altered characters are `UNREADABLE`, never `REFINED` |
| Reading comments | ⚠ `_ISSUE_FIELDS` (`tickets/github.py:27`) does not fetch comments today. Add them to the single read. **Completeness must be shown**, for example with a GraphQL `comments.totalCount` compared against the rows returned. Whether `gh issue view --json comments` pages through every comment is **not measured**. If completeness cannot be shown: `UNREADABLE` | the issue GET with `fields=comment` returns `total` and `maxResults` (to be confirmed, TR0). Page when short, or `UNREADABLE` |
| Questions posted on the ticket | a comment | a comment |
| A Manager forging a record | a Manager holds a GitHub token and could post a record-shaped comment. **The MAC is what defeats this**, and it holds only while the key is unreadable from inside every boundary (TR0) | a Manager has no Jira credential today (F11) |
| `ready-to-work` label (part 3.10) | added through `POST …/issues/{n}/labels` (`tickets/github.py`, `label()`), which creates a missing label; rite creates it once, beforehand, with a description (part 3.10). Removal is a separate `gh issue edit` call | added and removed in one `PUT … {"update": {"labels": […]}}` (`tickets/jira.py:456`), verified in that code's comment as accepting both at once. Jira labels are free-form and carry no description |
| `ticket_backend: none` | no board, so nothing can be recorded. With enforcement standard, nothing is ready, which is already true without a board | — |

**The key.** A random key at a path under rite's data directory, beside the
credential root (`mailbox.mail_root` shows the pattern), **granted to no
profile and denied by name on seatbelt**, as CU4 did for `cursor.key`. It
must be measured unreadable from inside a Manager and a Worker, on both
platforms, **under an ungranted root with a control**. A fixture under a
granted path proves nothing. **If a platform cannot deny it, then on that
platform the record is tamper-evident against accidents and not against a
hostile Manager, and `rite start` says so every run.**

⚠ **The key never enters an argv or an environment**, not even the
supervisor's own. CU1b section 4 (measured 2026-09-28, `9676630`) read an
engine's environment through `sysctl(KERN_PROCARGS2)` from inside the same
Manager, a sibling Manager and a yoloAI Worker. Anything placed there is
readable machine-wide. So the process that computes the MAC reads the file
itself and keeps the key in memory only. TR0 checks that with the same
reader, from inside each boundary, against every rite process that holds
it.

### 3.9 Local models

What decides an outcome is all rite's: the lint, the quote check, the
attribution, the accept word, the record write, the MAC and the status. The
model does two things: it chooses the questions and drafts the proposal.
The predictable failures of a local model, and what catches each:

| failure | what catches it |
|---|---|
| bad or too many questions | at most three a round and N rounds; the lint refuses a message with no numbered question and no proposal. **Not caught:** a *poor* question costs the User a reply. That is bounded, and it is a quality problem, not a safety one |
| an invented definition of done | cannot become a record without the User accepting that exact text. rite tags every `proposed` item as the Manager's own |
| a fabricated quote | refused: quotes must be exact substrings of the ticket or a delivered answer |
| a real quote misapplied | shown to the User beside its quote in the proposal. **Not caught mechanically.** The accept step is the check |
| "RT-10 is refined" when it is not | irrelevant: rite states the status from the board, and the model's claim is never read |
| slowness (F16: one qwen tool call took 11.5 min) | rounds are timed in hours, and refinement never holds the Manager |

**The verifier harness** (`managers/verifier.py`) runs `claude -p` in the
Owner's boundary. That means only where a Claude Owner exists. It could
annotate a proposal ("item 2 is not supported by its quote") before the
User sees it. **Recommendation: not in v0.7.0.** It is not load-bearing,
because the accept step already puts every item in front of the User, and
an all-local project would not have it. It is a later improvement, and it
is not a gate.

⚠ **An honest limit.** A lazy User will accept a mediocre proposal. rite
records that as *accepted by the User*, never as *verified*. P2 is "never
invented", not "always good".

### 3.10 The `ready-to-work` label: a view of the record, never the authority

*Not built.* Robert asked for it (part 0) so that the Owner can filter
"ready to be assigned" from "needs refinement". The dogfood showed the need:
the Owner had no way to tell a ticket it could start from one that needed
the User.

**The one thing that must not go wrong.** A label is board state that any
person, and any Manager holding a token, can add or remove. If rite read
the label as proof of refinement, adding it by hand would bypass
enforcement completely. That is the proxy-for-the-property failure this
project has catalogued all release. So:

- **The gate never reads the label.** Every consumer in part 3.7 decides
  from `scheduled` (the list) and the predicate (the signed record, part
  3.2).
- **rite is the only intended writer, and it writes what the record says.**
  The label is output, not input.

**The name: `ready-to-work`.** It is Robert's own phrase ("tickets that are
ready to work on"), it reads correctly to someone who has never seen rite's
docs, and it is one token, which Jira needs (its labels cannot contain
spaces). It is also plainly different from `scheduled`, which is the point
of the next paragraph.

**Its relationship to `scheduled`: three roles, one authority each.**

| | who sets it | means | read by the gate? |
|---|---|---|---|
| `scheduled` | a person, or rite's handover returning a ticket (`lifecycle/commands.py:164`, `coordination/refusal.py:103`) | "this should be worked": the queue, as today | **yes**, as membership of the queue |
| the signed record | rite, from the User's accept or a person's attestation | "this has an agreed definition of done" | **yes**, as the predicate |
| `ready-to-work` | **rite only** | `scheduled` **and** REFINED **and** not yet assigned | **never** |

The alternative, making `ready-to-work` *replace* `scheduled` as the queue
signal, was rejected on two counts. The gate would then read a label that
anyone can set, which is the proxy again. And a person would lose the
plain "please work this" input that `scheduled` is today. With the roles
above, **the two labels cannot disagree as decisions**, because only one
of them is ever an input. They can disagree as a *view*: the view is
stale, and the next reconciling read corrects it.

**The Owner's two filters**, on either board:

| | GitHub | Jira (JQL) |
|---|---|---|
| ready to be assigned | `label:ready-to-work` | `labels = ready-to-work` |
| needs refinement | `label:scheduled -label:ready-to-work` | `labels = scheduled AND labels not in (ready-to-work)` |

**Both filters were run against real boards on 2026-09-28, read-only,
using an existing label as a stand-in for `ready-to-work`, which no board
carries yet.**

- **Jira** (`bentora`, project `BEN`, stand-in `refined`):
  `labels = scheduled` returned 147; `… AND labels = refined` returned 5;
  `… AND labels not in (refined)` returned 142. That is 5 + 142 = 147, an
  exact split, and it includes scheduled tickets carrying other labels
  (`gate`, `bug`). `labels != refined` returned the same 142. The
  hyphenated name parses unquoted: `labels not in (ready-to-work)` ran and
  returned all 147, and `labels = ready-to-work` returned 0.
- **GitHub** (search API): on `rite-dogfood-board`, `label:scheduled` and
  `label:scheduled -label:ready-to-work` both returned 20, and the
  intersection returned 0. No issue there has two labels, so the exclusion
  was checked on a public repository: `repo:cli/cli is:issue label:bug`
  returned 2258, `+label:needs-triage` 607, and `-label:needs-triage` 1651,
  which is 607 + 1651 = 2258.
- ⚠ **Not run on `ritetest`**, the Jira site Robert set up: this session's
  Atlassian connector reaches only `bentora`. JQL is the same engine on
  every Jira Cloud site, so this is expected to hold there, but it is not
  observed there. TR7's done-when includes it.
- **A trap found on the way.** GitHub drops the positive qualifier when a
  query names the same label both ways: `label:scheduled -label:scheduled`
  returned 5, the five issues with no labels, not 0. That is harmless for
  these two filters, which name different labels, but it is why the check
  used two labels.
- **The board's own search index can lag** (DF4 measured list endpoints
  lagging by about 2 s). A filter on the board is a view of a view. When it
  matters, `rite board list --ready` is the fresh read. When the
board view may be stale (no rite process has run lately),
`rite board list --ready` and `rite board list --needs-refinement` compute
the truth from a fresh read, with each ticket's state.

**Both cases someone will produce, by hand or with a Manager's token:**

- **The label added to a ticket that is not REFINED.** Nothing starts:
  every gate reads the record and refuses with the ticket's actual state
  (NOT REFINED, STALE, …). At its next reconciling read, rite **removes the
  label** and says so twice: a line in the Owner's instruction, and
  once on the ticket as a comment ("rite removed `ready-to-work`: this
  ticket has no valid refinement record (state: NOT REFINED)"). If the same
  label keeps being re-added, rite keeps removing it. The ticket comment is
  posted **once per record state**, so a person re-adding it cannot start a
  comment war. The instruction line repeats every time, because a Manager
  adding it is worth knowing about.
- **The label removed from a ticket that is REFINED.** Nothing is blocked:
  the gate reads the record, and the ticket stays ready. At its next
  reconciling read, rite **re-adds the label** and reports it in the cycle
  line. It posts no ticket comment, because nothing is wrong with the
  ticket.

**Reconciliation: fixed at the next read, never assumed correct.**

- **Who writes.** The Owner's supervisor, and only that one among Managers
  (there is one Owner, part 3.3). Also a person's `rite refine accept`
  or `reopen`, for the ticket it touches. Also assignment, which removes the
  label in the same `label()` call that removes `scheduled`
  (`coordination/distribution.py:164`).
- **When.** At the start of every cycle; right after every record write
  (after its read-back); and on demand with `rite refine sync`, which a
  person can run on the host with no Manager running.
- **What is looked at.** The union of `list(scheduled)`,
  `list(ready-to-work)` and DF4's ledger of ids rite recently created or
  labelled. Each member is read with one single-issue GET, the desired
  label is computed from that read, and rite writes only where the label
  differs. The second list matters: it finds a label left on a ticket that
  lost `scheduled` or was assigned while rite was not running.
- **While no rite process runs, the label drifts.** It is exactly as fresh
  as the last reconcile, and each start line says how many labels it
  corrected. A board view is never the truth; `rite board list --ready` is.
- **Incomplete lists are said.** A list that returns a truncated page
  (`TicketPage.truncated`, `tickets/interface.py`) makes that cycle's
  reconciliation incomplete, and the start line says so. It never claims
  the board is reconciled.
- **A list's lag (DF4) delays the view, never a decision.** A label added in
  the last seconds may be missed by one read, and is fixed at the next.

**On GitHub the label is created once, with a description**, the first time
rite needs it: "Set and removed by rite: this ticket is scheduled and its
definition of done is agreed. Adding it by hand does nothing." A person
browsing the repository's labels then learns what it is without reading any
docs. An existing label of that name keeps its colour; rite does not
overwrite a person's customisation. `label()` would otherwise create it
grey with no description. Jira has no label descriptions, so there it is
the name alone.

### 3.11 Configuration (TRQ2 and TRQ3, decided)

Robert: "the limits sound good. We can make them configurable for advanced
users", and the accept words "also configurable. the current list looks
good. I think I will add "proceed"". *Not built.*

```yaml
# .rite/config.yaml
refinement:
  rounds: 3              # N: rounds with an answering User before PARKED
  deadline_hours: 24     # how long one round waits
  open_max: 5            # K: refinements open at once per Manager
  start_per_session: 3   # S: new refinements started per Owner session
  accept_words: [ok, yes, accept, lgtm, proceed]
  chore_after_minutes: 60  # a silent chat instruction becomes an unrefined chore (TRQ11)
  questions_to: dm       # or: channel (part 3.12)
  channel: ""            # the private channel's id, when questions_to is channel
```

Every key is optional, and the defaults are the values shown. **What is not
configurable is whether refinement is enforced (TRQ1).** The parser
refuses, rather than clamps:

- `rounds`, `open_max` and `start_per_session` below 1, and
  `start_per_session` above `open_max`;
- `deadline_hours` of zero or less. Above 24 is allowed: open round roots
  are pinned and read past Slack's 24-hour thread horizon (part 4, race 7),
  and the start line says the deadline is longer than that horizon;
- an `accept_words` entry that is empty, contains whitespace, or is one of
  `no`, `not`, `stop`, `wait`, `cancel`, `don't`. **A mistake in this list
  turns a refusal into consent**, so the list refuses the words most
  likely to be typed as the opposite of yes. Matching is case-insensitive
  and exact after rite strips the ticket id;
- `questions_to: channel` with no `channel`.
- `chore_after_minutes` below 1. It has no upper bound: a longer wait only
  delays when the words appear on the board. They are safe in
  `delivered.py` meanwhile.

Unknown keys are refused, as everywhere in `config.yaml`.

### 3.12 Where the questions go (TRQ8, decided)

Robert: "DMs or a dedicated private channel with @rite invited. Let's make
it configurable". *Not built.*

- **The private channel is a second destination, not a replacement for
  the DM.** `questions_to: dm` is the default. With `questions_to:
  channel`, refinement rounds and parking notices go to that channel. The
  check-in stays in the DM (RP1, decided item 1).
- **Authority in the channel is a deliberate, narrow amendment.** Today
  SPEC §9.16.5 treats channel text as context, whoever wrote it, including
  the Owner writing outside the DM. For refinement only, rite takes answers
  and accept words from that channel **only when Slack says the author is
  `slack.owner_user` and the message is in a refinement thread rite
  started**. Anyone else's message there reaches the Manager as context and
  never answers or accepts: a teammate in the channel cannot accept for
  him. Slack authenticates the author's id, so a message cannot forge it.
  TR3 writes the amendment into §9.16.5.
- **A channel nobody reads is the same failure as a file nobody reads.**
  So RP1's delivery confirmation (decided item 4) governs both
  destinations. A refinement round is an **action item**: it is sent by
  `rite refine ask`, which RP1 classes as action by the command that
  produced it (decided item 2). It stays pending until Robert reacts to it
  or replies, and **an unconfirmed one comes back at the next check-in,
  which goes to the DM**. So a channel he has stopped reading cannot
  swallow a question; it resurfaces where he reads. The parking notice says
  whether the question was ever confirmed seen. "No answer" and "never
  seen" are different outcomes, and he should know which one parked a
  ticket.
- **The app must actually be in the channel.** At start rite checks that
  it can post there (the existing `slack.probe`). If it cannot, the
  questions go to the DM, and the start line says why and how to invite
  the app. They are never sent to a channel that cannot receive them.
- **Depends on RP1:** reading reactions. The relay does not read them
  today (`slack.py` relays message text only).

### 3.13 A guardrail: no agent overrides user input without explicit permission (TRQ6, reframed)

Robert: "Can we create some guardrails against overriding user input
without an explicit permission?" We had asked whether a well-written
ticket should still need an "ok". **He is asking for the property
underneath that**, which is worth more than the "ok": text that is the
User's is never replaced by an agent's without the User saying so. *Not
built.*

**The two rules:**

- **G1.** An agent's edit to a ticket's title or description is never
  treated as the User's words, unless the User explicitly permits it.
- **G2.** rite itself never edits a title or description, except to
  restore the User's own text when the User asks (below). Today nothing in
  `src/` calls the backends' `update()`, and a test pins that it stays that
  way.

**What the boards record: measured 2026-09-28, read-only.**

- **GitHub.** The editor of an issue body is in GraphQL (`Issue.editor`,
  `lastEditedAt`, and `userContentEdits`, which carries each edit's editor,
  time and diff). **It is not in the REST timeline or events.** Issue #12396
  of `cli/cli` has three body edits in `userContentEdits`, and its timeline
  shows only `closed`, `commented`, `cross-referenced`, `labeled`,
  `referenced`, `subscribed` and `unlabeled` events.
- **Jira.** The changelog records every description change, with the
  author's account id and the full text before and after. BEN-62 on
  `bentora` shows two description entries, both by `robbartoszewski`,
  because they were made with his token.

**So authorship follows the credential, on both boards.** Whether "who
wrote this text" can be answered depends only on whether agents write
under an identity of their own. Where they stand today:

| writer | GitHub | Jira |
|---|---|---|
| the person | the person | the person |
| a Manager | **its own**: a GitHub App installation token (`managers/github_access.py`), so it should appear as the App's bot. Not yet observed: no bot activity exists in Robert's repositories | **nothing**: a Manager holds no Jira credential (F11) |
| a Worker | **the person**: `github_token` or `sandbox_token_<worker>` are personal access tokens (`credentials/store.py:75`, `sandbox/__init__.py` `resolve_worker_token`) | **nothing, since #97** (2026-09-29): Workers are given GitHub and Claude credentials only (`WORKER_SERVICES`), and their ticket is delivered from the host as `TICKET.md`. It was the person's Jira token before |
| rite on the host (records, labels, comments) | the person's `gh` login: all 132 actors across 98 issues and pull requests in Robert's repositories were `User` accounts | the person's token |

⚠ **Since #97 (2026-09-29), no agent holds a Jira credential at all.**
Workers lost theirs, Managers never had one (F11), and rite's own host-side
writes never edit a description (G2, pinned by a test). So on Jira today,
**every edit to a ticket's description is a person's**, whichever account
rite is configured with. That makes G1 and TRQ6's "no 'ok' for a definition
of done you wrote yourself" safe on Jira now, without any identity check.
It does not cover a person's own unsandboxed session driving Jira's API with
the configured token directly (TRQ10's class). GitHub is unchanged: Workers
still push with a token that may be the person's.

⚠ **This corrects the premise the question arrived with.** Managers do
*not* use Robert's GitHub credential; they use the App. The identity
problem on GitHub is Workers, and rite's own host-side writes. On Jira it
is everyone, because every agent that can write there does so as him.

**Decided 2026-09-29 (TRQ12): rite builds no identity management.**
Robert: "Can't the User control it by choosing if they give rite their
token or create a separate account for them?" He is right, and it replaces
this note's earlier proposal (separate agent identities built by rite)
entirely. **The User decides, by which credential they configure:**

- **Their own account.** rite's agents write as them, and rite cannot tell
  their edits from an agent's. The guardrail is **advisory**: rite still
  never edits a description itself (G2), and it instructs agents not to,
  but it cannot detect one that does.
- **A separate account for rite**, as Robert made for the dogfood. rite's
  writes carry that account, anything else is the person's, and the
  guardrail is **enforced** (below).

#### 3.13.1 The principle, written once: rite does not invent identity

**rite reports which identity is in use, and what follows from it.** This
covers the board (TRQ12) and commits alike, and it is the one rule for
every question of "who did this":
- rite never builds, mints or impersonates an identity to make one
  distinguishable;
- it works correctly whichever identity the User chose;
- it says plainly, at setup and in `rite doctor`, which one that is and
  what it means.

**What rite owes the User under it** (*not built*; TR8, reshaped):

- **It names the identity each writer uses, and says what follows.**
  - GitHub: each token rite hands an agent is looked up with `GET /user`
    (the Worker's `github_token` or `sandbox_token_<worker>`). The Managers'
    App reports its bot name. Each is compared with the `gh` login rite uses
    on the host, which is normally the person's own:
    - the same login: "Workers write to GitHub as `robbartoszewski`, which
      is also you. rite cannot tell your edits from theirs, so the guard
      against an agent overwriting your ticket text is advisory. A
      separate account for rite makes it enforced";
    - a different login: "Workers write as `rite-bot`. Your edits are
      told apart from theirs, and the guard is enforced".
  - Jira: rite cannot see which account the person edits with, so it says
    which account rite writes as (`/myself` for the configured token),
    and states both consequences conditionally: "if that is your own
    account…, if it is a separate one…".
- **It enforces the guardrail when the choice allows it, from the same one
  read** (part 3.2: `read_thread` also returns the description's edit
  history: GitHub's `userContentEdits`, Jira's changelog).
  - **G1, enforced:** a description whose latest revision was made by an
    identity rite hands to agents is `EDITED BY AGENT`. It is not refinable
    from its own text, and the User is asked, with the diff, to keep it or
    restore their own version. **Keep** is an accept word in that thread.
    **Restore** makes rite write back the person's last revision: the one
    time rite writes a description, and only on the person's word.
  - **The "ok" goes away for tickets he wrote himself** (TRQ6's friction).
    A definition of done in text that no agent identity has ever edited is
    REFINED with provenance `ticket-text`, citing that revision, under the
    MAC. Where the identities are shared, that cannot be established, so the
    one "ok" stays: rite fails closed and says why.
- **G2 in every case:** rite itself never edits a title or description,
  except the restore above. Nothing in `src/` calls the backends'
  `update()` today, and a test pins that.

**Commit authorship: documented, not built** (decided 2026-09-29, same
principle). A Worker's commits carry whatever git identity its sandbox is
given, and its pushes and pull requests carry the account of the token it
holds. If that is the User's own, agent work appears under their name,
which matches PB1's "rite's involvement being discoverable is fine" but
cannot be told apart by author. If they want it told apart, a separate
account does that. `rite doctor` states which identity commits and pull
requests go out as, and rite adds nothing to the history to make up the
difference.

**What was withdrawn:** this note's proposal that rite create a Jira
service account, mint App tokens for its own host-side writes, and strip
Workers of GitHub credentials in order to make identities distinct. PB1
still takes pushing away from Workers for its own reasons; that is no
longer part of this guardrail.

### 3.14 Every piece of Worker work carries a ticket (TRQ5, decided)

Robert: "Can we just ticket all work that Workers do? (in JIRA that would
be chore tickets I guess)". **This closes the hole instead of making it
visible, so it replaces the `--no-ticket` recommendation (TRQ5's (c))
outright.** *Not built.*

**Where rite stands today:**

- The broker already requires one. A Worker request must carry both
  `worker` and `ticket` (`managers/broker.py`).
- `rite sandbox start` accepts `--prompt` instead of `--ticket`
  (`cli/main.py:5726–5757`): a person's own launch, with no ticket.
- An executor-only secondary Manager does routed work itself, on free
  text. The dogfood's `helper` did.

**The design:**

- **Every route carries `--ticket`, and `--no-ticket` is withdrawn for
  everything.** A route to another Manager is work. There is no route that
  needs no ticket; a status question has `rite status`.
- **A User instruction that is not already a ticket becomes a chore.** The
  supervisor creates the ticket outside the boundary, at the first of two
  moments (TRQ11, decided, below): **when the User accepts** a definition
  of done for it, or **when `chore_after_minutes` passes with no reply**.
  **Its description is the User's instruction, quoted verbatim from the
  message rite delivered and marked `INSTRUCTION`**, with those message ids
  recorded. **The Owner cannot write a chore's text**, so it cannot pass off
  its own idea as the User's chore. Work the Owner initiates (something it
  found) is an ordinary ticket, filed under the "anything you find becomes
  a ticket" rule, and refined like any other.
- **`rite sandbox start --prompt "…"`** creates a chore from the prompt the
  person typed, then starts on it.
- **How a chore looks on each board.** GitHub: an issue labelled `chore`.
  Jira: issue type `Task`, which is what `JiraBackend.create` always uses
  (`tickets/jira.py`), labelled `chore`. **No custom issue type is needed**,
  so this works on a board where Robert cannot create issue types. If the
  board refuses the create (no permission, or `Task` not in the project's
  scheme), the route is refused and says the board's own error. **Nothing
  runs untracked.** `rite doctor` checks beforehand, read-only: Jira's
  `createmeta` for `Task`, and GitHub's `has_issues`.
- **Noise.** A chore closes when its work is reported done, and
  `-label:chore` hides chores in a board filter.

**Does a chore need refinement? Decided 2026-09-29 (TRQ11): yes, straight
away, and silence never loses it.** Robert: "In most cases, I think the
Owner should ask follow up questions (if it has any doubts) and refine
straight away. If the User doesn't reply within some timeout, create a
chore with what it's got and then refine later". That settles the question
between the two readings I put to him (a chore as the User's intent, or
not), in the direction of refining it, and adds what to do when he is
silent:

- **Refine straight away.** The chat instruction's first round goes out in
  the Owner's same session: questions if it has doubts, a one-word proposal
  if not ("I'll run the suite on branch X and report the result. ok?").
- **He answers:** the rounds run as for any ticket. On his accept, rite
  creates the chore with his words and posts the signed record on it, and
  it is REFINED.
- **He is silent for `chore_after_minutes`:** rite creates the chore **with
  exactly his words**, quoted from `delivered.py` with their message ids,
  and no definition of done. It is visibly unrefined:
  - its body opens with a line rite writes: "Unrefined: rite created this
    from your message at <time>. No definition of done is agreed yet; no
    work starts until one is";
  - its state is `NOT REFINED`, and then `WAITING FOR YOU` at the round's
    deadline;
  - so it is never `ready-to-work`, never assigned, and never given a
    Worker. The broker and `route --ticket` refuse it, like any unrefined
    ticket (part 3.7).
  Refinement continues on it, and its open question is re-presented when he
  is back (part 3.4 step 7). **Nothing is lost** (the words are on the
  board), **nothing is blocked** (other work goes on), and **nothing is
  invented** (no definition of done exists until he accepts one).
- **Exactly one chore per instruction, including across crashes.** The
  chore's creation is keyed by the instruction's message ids, under the
  ledger's `flock` (part 4, race 11). Accepting and timing out can land in
  the same cycle, and whichever takes the lock first creates the chore; the
  other finds it. The id is written to the ledger only after the board
  create returns. So there is a window in which a crash leaves an intent
  recorded with no id: the chore may or may not exist.
  - Finding out would need a list, which lags (DF4).
  - So rite **does not create again**. It reports "a chore for your message
    of <time> may already exist; check the board, then `rite refine reopen`"
    and waits for a person. A silent duplicate is worse than a question.
**What it costs:**

- a ticket per instruction: one to three extra board writes per route, and
  board clutter that the `chore` label and closing on completion contain;
- one word per mechanical chore (TRQ11, decided);
- on a board where rite's identity cannot create issues, every chat
  instruction is refused until that is fixed. `rite doctor` finds it first.

## Part 4 — races, one by one

Each is resolved without depending on order. Where atomicity is not
available, the result fails closed and says why.

1. **The ticket is edited while a round is open.** The proposal carries the
   description hash it was made against. At accept, the supervisor re-reads
   the ticket. A differing hash means it refuses to write and says so. If
   the edit lands *between* that re-read and the write, the record carries
   the old hash and reads `STALE` at once. Either order gives a correct,
   stated result, and never a record that matches text the User did not
   see.
2. **The ticket is edited after the record.** The hash no longer matches:
   `STALE`.
3. **Two refiners at once** (the Owner automatically, and a person's
   `/refine`). Each record names the head it superseded. Two records naming
   the same parent (or both `null`) are two heads: `CONFLICT`, and a person
   resolves it. **Not "latest wins"**, because which is latest is the order
   of two independent writes.
4. **Check against use at Worker start.** The Worker receives the record
   bytes that were checked. It does not re-read the board to find its
   definition of done. A later edit makes the ticket `STALE`, `rite status`
   reports "ticket changed since work began", and the PR cites the record
   id the work was done against.
5. **List lag (DF4).** The status comes from a single-issue GET. DF4's
   ledger governs which tickets are looked at.
6. **A reply sent just before the deadline.** Decided by Slack's send
   time, and WAITING FOR YOU only after the thread has been read past the
   deadline.
   Not by which of the reply and the deadline rite happens to see first.
7. **Thread eviction.** Today the relay reads at most `THREADS_MAX = 10`
   roots (`slack.py:83`), newest first, and drops roots older than 24 h. A
   busy Owner posting ten replies would silently stop reading an open
   round's thread: the answer is lost, and whether it is lost depends on
   how many other messages were posted before the User replied. **So open
   round roots are pinned outside the LRU list while their round is open,
   and read past their deadline, whatever their age.** At most K rounds are
   open per Manager (5 by default, TRQ2). A ticket beyond K waits in
   `NOT REFINED`, with the reason stated. It is not sent into a thread
   nobody will read.
8. **Two refining Managers.** Not constructible: refinement is the
   Owner's, and there is exactly one Owner or none (part 3.3).
   A person refining through `/refine` at the same moment is race 3.
9. **An accept in an older round's thread.** It accepts only T's latest
   proposal. An accept under a superseded proposal is answered "that
   proposal was replaced by round k; reply ok in its thread". Accepting
   text the User was later shown a revision of would be a guess at what
   they meant.
10. **The supervisor dies between writing the record and updating its
    ledger.** The board is the truth. A restart recomputes from the board,
    and finds the record and `REFINED`. A retry after a write whose
    response was lost uses a **deterministic `record_id`** (a hash of the
    ticket, the description hash and the accepting message). A second copy
    with the same id is one record, not a second head.
11. **Ledger writes** (the supervisor, a person's `reopen` or `accept`) take
    a kernel `flock` on the ticket's ledger file, the scheduler lock's fix
    (#37). The ledger holds rounds only. It never decides `REFINED`.
12. **The `ready-to-work` label against anything else writing labels.** A
    person's edit, rite's reconcile and an assignment can interleave in any
    order, and the last write wins. **No outcome depends on the result**,
    because no gate reads the label, and the next reconciling read puts it
    back in step with the record. On GitHub an assignment's add and remove
    are two calls (`github.py`, `label()`), so a crash between them can
    leave `ready-to-work` on an assigned ticket. The next reconcile removes
    it, because "not yet assigned" is part of the label's definition.

**Out of scope, and said:** several machines. The ledger is machine-local.
Two machines refining the same ticket is race 3, which holds, but the
rounds and deadlines do not travel. That is v0.8.0's shape.

## Part 5 — instructed and enforced: both are standard

**Decided by Robert, 2026-09-28 (TRQ1): enforcement is the standard, with
no opt-in, no configuration key and no exemption for existing projects.**
His reason: "There aren't really any 'existing projects' so let's just
implement it as a standard." So the `refinement.enforce` key this note
first proposed is dropped, along with the start line that would have said
enforcement was off.

| | instructed (TR1–TR4) | enforced (TR5) |
|---|---|---|
| The Owner refines before routing | the prompt says so | `route --ticket` refuses an unrefined ticket |
| A Worker starts only on a refined ticket | its start prompt carries the record, and `/ticket` checks | the broker refuses. The loop does not count it as ready for Workers |
| A bare ticket labelled `scheduled` | the Owner is handed it to refine | **starts refinement** (Robert's semantics); never assigned or worked until REFINED |
| Routed work of any kind | every route names a ticket | **enforced**: a route without `--ticket` is refused. A User's chat instruction becomes a chore ticket whose text is the User's own words (TRQ5, decided; part 3.14) |
| A human `rite loop` with no Manager | reports NOT REFINED tickets | dispatches none of them until a person refines or attests them |

**What enforcement changes.** It changes behaviour for anyone who puts a
bare ticket on a board and expects work to start, and that includes
Robert's own boards: `RT` on Jira, `rite-dogfood-board` on GitHub, and the
dogfood's `KAN`.

⚠ **Retracted, 2026-09-28: "nothing starts until each one is refined".**
The first version of this part told Robert, in TRQ1's costs, that enforcing
from the upgrade would *stop* every `scheduled` ticket on those boards until
someone refined it. Under his semantics that is not what happens: they do
not stop, they **queue for refinement**, and the Owner starts on them
itself, oldest first and bounded by part 3.4 step 0. The claim was not
invented, though. It was true of the design as first written, because
that design had no `refining` verdict and would have read an all-unrefined
board as `idle` and stopped (part 3.4 step 8). His model is what removed
it. What is actually true:

- On the first run after upgrading, **every** `scheduled` ticket is NOT
  REFINED, so **no Worker starts on any of them yet**, and the Owner begins
  refining the oldest three (S). With N = 3, that is up to three messages
  per ticket to the User, at most K open at once, and new ones only as the
  User answers.
- **A well-written ticket still needs a person's word**: one `ok`, or a
  `rite refine accept --as-written` at a terminal, under the fail-closed
  default of TRQ6. There is no blanket accept, deliberately: it would be
  an invented definition of done by another name.
- Throughput on a board of properly written tickets drops by one
  round-trip to the User per ticket.
- A project with no one reachable (no Slack, nobody at `rite connect`)
  stops at `waiting-on-user` after its first batch of questions, instead of
  working. Today it would work bare
  tickets on guesses, which is what this feature exists to remove, but
  someone who relied on that will see it stop.

## Part 6 — what this does not do

- **It does not vet ticket text** (SPEC §6.6.3). Refinement settles what
  "done" means. It does not make a hostile ticket safe.
- **It does not read answers from board comments.** A comment's author is
  an identity rite would have to authorise, and on a public repository
  anyone can comment. v0.7.0 takes answers through the Manager's channel
  only. A person who prefers to edit the ticket resumes it through the hash
  change (part 3.4 step 7).
- **It is not the scenario gate** (§7.3, D-81), although a record's
  checklist is a natural input to it.
- **It does not make a proposal good** (part 3.9's honest limit).
- **It does not work across machines** (part 4).

## Part 7 — decisions for Robert

| id | question | options | recommendation, and why |
|---|---|---|---|
| ✅ **TRQ1**: **DECIDED 2026-09-28, enforced as the standard** | Enforce refinement in code, or instruct only? | (a) enforced everywhere; (b) enforced for new projects, opt-in for existing ones; (c) opt-in everywhere; (d) any of these plus a blanket accept | **Robert chose (a):** "There aren't really any 'existing projects' so let's just implement it as a standard." No configuration key. This note had recommended (b); the ruling replaces it. (d) stays rejected. The same ruling added the `ready-to-work` label (part 3.10) |
| ✅ **TRQ2**: **DECIDED 2026-09-28** | N rounds, the deadline, K open, S started per session | as proposed | **Robert: "the limits sound good. We can make them configurable for advanced users."** Defaults N = 3, 24 h (or the next check-in if sooner), K = 5, S = 3; configurable with validation (part 3.11). The numbers remain judgement, not measurement |
| ✅ **TRQ3**: **DECIDED 2026-09-28** | The accept words | a fixed list, or a configurable one | **Robert: "Let's have a list of words that are also configurable. the current list looks good. I think I will add "proceed"."** Default `ok`, `yes`, `accept`, `lgtm`, `proceed`; words that read as refusals are refused in configuration (part 3.11) |
| ✅ **TRQ4**: **DECIDED 2026-09-28** | What "you decide" means | a refusal to proceed; a recommendation for confirmation | **Robert: "Yes, let's have rite come back with a recommendation for a final confirmation."** A delegation gets a complete proposal back, in the same round, asking for one word. It spends a round (part 3.4 step 4) |
| ✅ **TRQ5**: **DECIDED 2026-09-28** | Routed work without a ticket | (a) accept the hole; (b) require a ticket for executors; (c) `--ticket` or `--no-ticket` | **Robert: "Can we just ticket all work that Workers do? (in JIRA that would be chore tickets I guess)."** Every route carries a ticket, a chat instruction becomes a chore quoting the User's words, and `--no-ticket` is withdrawn (part 3.14). Whether a chore needs refinement is **TRQ11** |
| ✅ **TRQ6**: **REFRAMED 2026-09-28** | A ticket whose description already has a definition of done | one "ok"; trust by editor identity | **Robert: "Can we create some guardrails against overriding user input without an explicit permission?"** The property is now G1 and G2 (part 3.13). Skipping the "ok" safely needs agents to write under their own board identity: **TRQ12**. Until then, the one "ok" stays |
| ✅ **TRQ7**: **DISSOLVED 2026-09-28** | Two board-managing Managers | refuse at parse; allow | **Robert: "Doesn't "Owner owns the board and assigns tickets. Managers manage only the tickets assigned to them" solve this?"** It does. Refinement belongs to the Owner, which rite already makes singular (`routing_owner`). The question existed only because this note first keyed refinement to the `board` duty, which has no uniqueness rule and no consumer (part 3.3) |
| ✅ **TRQ8**: **DECIDED 2026-09-28** | Where questions go | the DM; a private channel | **Robert: "DMs or a dedicated private channel with @rite invited. Let's make it configurable."** The channel is a second destination; answers there count only from `owner_user`; RP1's delivery confirmation governs both destinations, and unconfirmed questions come back in the DM (part 3.12) |
| ✅ **TRQ9**: **DECIDED 2026-09-28** | Must a record have Verify commands? | required; optional but explicit | **Robert: "makes sense."** Optional but explicit (`"none agreed"`), as recommended |
| ✅ **TRQ10**: **DECIDED 2026-09-29, (a)** | May an unsandboxed session running as the person attest a definition of done? | (a) yes, labelled `attested`; (b) Slack DM only; (c) an OS presence check | **Robert: "Allow it."** The Slack DM stays the strong path (`accepted`); an attested approval is findable by its provenance, by the `rite-attested` token on the board, and in `rite refine status` (part 3.6) |
| ✅ **TRQ11**: **DECIDED 2026-09-29, reshaped** | Does a chore ticket need refinement? | (a) exempt; (b) the same predicate | **Robert: "In most cases, I think the Owner should ask follow up questions (if it has any doubts) and refine straight away. If the User doesn't reply within some timeout, create a chore with what it's got and then refine later".** So (b), plus: silence makes an unrefined chore with exactly his words after `chore_after_minutes`, and a silent ticket is `WAITING FOR YOU`, re-presented when he is back, never PARKED (parts 3.4 step 7 and 3.14) |
| ✅ **TRQ12**: **DECIDED 2026-09-29, premise rejected** | Should agents write to the board under an identity of their own? | (a) no; (b) rite builds agent identities | **Robert: "Can't the User control it by choosing if they give rite their token or create a separate account for them?"** rite builds no identity management. It works under either choice, says in setup and `rite doctor` which one is in use, and enforces the guardrail where the choice allows it. The same principle covers commit authorship: documented, not built (part 3.13) |
| ✅ **Q4**: **DECIDED 2026-09-29, (c) plus scope** | Does "don't implement tickets yourself" bind executor secondaries? | — | **Robert: "Yes, close the bypass. However, instruct that it's meant for chores and trivial tickets. Any serious work should be passed to workers."** An executor Manager may commit only on a ticket-named branch through a PR, on a REFINED ticket, and its instructions say that path is for chores and trivial tickets (part 3.5; TR3, TR10) |

## Part 8 — where I am uncertain, and what gets measured first (TR0)

- ~~Is there no controlling terminal in a model's Bash tool?~~ **Measured
  2026-09-28, and the question was the wrong one.** There is none by
  default in Claude Code, but a model can create one with `script` or
  `pty.fork()` and answer a `/dev/tty` prompt (part 3.6). The prompt is
  dropped. Goose and Cursor need no separate measurement: the same tools
  are available to any engine with a shell.
- ~~Whether GitHub comments are read completely~~: **measured 2026-09-28.**
  `gh issue view --json comments` returned 148 of 148 on cli/cli#13840
  (gh 2.98.0). rite does not rely on that: TR1's `read_thread` pages
  through GraphQL itself and counts the thread complete only when the
  comments read equal `totalCount`. Observed on the same issue: 148 over
  two pages, complete. On `main` since #83.
- ~~Whether the record survives a round trip~~: **measured 2026-09-29, on
  both boards, with `rite refine accept` (#83) against real issues.**
  - **GitHub** (`rite-dogfood-board` #44 and #45): **byte-identical.** 1564
    bytes sent and stored, same SHA-256, no CRLF rewriting, and backticks,
    a pipe, an apostrophe, a backslash and `zażółć` all survived. The
    payload parsed from what was stored equals the signed one, the MAC
    verifies against it, `rite refine status` said REFINED, an edit to the
    issue body turned it STALE, and `"rite-attested" in:comments` found it.
  - **Jira** (`ritetest` KAN-11): stored as **one paragraph holding one text
    node**, which is what `JiraBackend.comment` sends. Flattened by
    `adf_to_text`, it comes back as the sent text **plus one trailing
    newline** (1283 bytes sent, 1284 read). The payload is identical and
    the MAC verifies, **because the MAC is over the parsed payload, not the
    bytes** (part 3.2). Signing the bytes would have failed here. `rite
    refine status KAN-11` said REFINED.
  - **Cosmetic, found on Jira:** Jira renders the comment as its own markup,
    so the Markdown `**bold**` heading shows with stray asterisks. The data
    is unaffected. It is a small follow-up to render Jira comments in
    Jira's markup.
  - **A privacy defect, found on the first GitHub probe and fixed before
    #83 merged:** the attested provenance carried the machine's hostname,
    which was then posted to the board. Provenance now carries the kind,
    the time and `as_written` only, and a test fails if it names the
    machine. The word was edited out of #44's comment, which leaves that
    probe record correctly UNREADABLE.
  - **Not yet measured:** whether Jira's embedded comment list pages as
    documented. The code fails closed if it does not.
- **Whether the key is unreadable**: from inside a **macOS Manager,
  measured 2026-09-28 and pinned** (`test_no_manager_reads_the_refinement_key.py`,
  run in the macOS CI job): read, list and plant are all refused, with
  controls. **Still owed:** a Linux Manager (Landlock) and a yoloAI Worker,
  each under an ungranted root with a control;
  and whether it is absent from the argv and environment of every rite
  process that holds it, read with CU1b section 4's `KERN_PROCARGS2`
  reader.
- ~~Whether a person's edit to a ticket description can be told from
  rite's~~: **measured 2026-09-28**: not on GitHub today, because Workers
  write with the person's own token, and not on Jira (part 3.6).
- ~~Whether the Jira filter for "needs refinement" works~~: **measured
  2026-09-28** on `bentora` (an exact 5 + 142 = 147 split), and the GitHub
  filter on `cli/cli`. Not yet run on `ritetest` (part 3.10).
- ~~Whether anything a Worker writes reaches the host~~: **it does,
  observed by the dogfood session** (its `TO_REFINEMENT_SESSION.md`, one
  macOS observation with no control). yoloAI 0.11.0 tells a Worker to write
  its question to `files/question.json`. That directory is a host path
  (`yoloai files <name> path`), and the KAN-7 Worker's question was read
  from outside the sandbox. It reached the host, and nothing read it. The
  Worker-question path (DF10, #68) now reads it. Linux is untested.
- **Whether a proposal gets rubber-stamped.** This is not measurable before
  the acceptance run (TR6). It is recorded there as observed behaviour, and
  it is not a gate.
- ~~DF2's wait is not built~~: **wrong, and corrected 2026-09-28.** It
  shipped in v0.6.0 (`8925c80`, `35beafc`); part 3.4 step 8 now builds on
  it. The DF2 row carried no status line, and absence was read as "not
  built", which is the proxy-for-evidence error this note warns against.

## Part 9 — where the spec text goes once decided

Every TRQ is now decided (TRQ1–TRQ12, and Q4). This note stays the design until TR3 writes the SPEC text, by the release plan's
rule that "a spec section for an undecided feature is the defect this
project spent two releases removing". When they are answered, TR3 writes a
new SPEC §6.7 ("Refinement: the record, the predicate, the protocol"). It
amends §9.4.2 (the Worker's scope loses "stop and guess nothing" in favour
of the record) and §9.10 (the orientation table's "Tickets on the board"
row gains the refinement step), and it adds a D-number per decided TRQ.
