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

**Not decided:** the rest of what this note proposes about *how*, in
TRQ2–TRQ9 (part 7), each with a recommendation.

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
  of done is either text the User accepted as proposed, or text a person
  attested at a terminal. There is no third route. A model's statement
  that a ticket is refined changes nothing; rite writes the record.
- **P3: a question reaches a person where they already look, and its answer
  is attributed to its ticket by rite, not by a model.** A question nobody
  can see is not asked.
- **P4: refinement is bounded and fails closed.** A bounded number of
  rounds and a bounded time, ending in REFINED or PARKED. Each end is said
  to the User and on the ticket. Refinement never makes a Manager wait: the
  ticket waits, and the Manager takes other work.
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

| state | means | ready for work? |
|---|---|---|
| `NOT REFINED` | no valid record, and no round open | no |
| `ASKING` | a round is open: questions sent, no attributed answer yet. Says which round of how many, and its deadline | no |
| `PROPOSED` | the latest round carries a proposal the User can accept | no |
| `REFINED` | exactly one valid record heads the chain, and it matches the ticket's current title and description | **yes** |
| `STALE` | a record exists, and the title or description changed after it was written | no |
| `PARKED` | refinement ended without agreement. The reason is one of: `no answer`, `not agreed after N rounds`, `thread unreadable` | no |
| `CONFLICT` | two records claim to be the head (part 4, race 3) | no |
| `UNREADABLE` | the board could not be read, the comment list could not be shown complete, or the record's signature cannot be checked | no |

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

**The Manager holding the `board` duty** (`config/managers.py`: the `lead`
and `pm` presets hold it, and a lone Manager that declares nothing holds
every duty through `effective_duties`). That is the Manager that talks to
the User. In a multi-Manager root only the routing Owner reads Slack
(`routing.py`, SPEC §9.16.7).

- **A secondary never refines and never asks the User.** It has no channel
  to them, and inventing one is the route the Worker's `question.json`
  took. It reports a gap to the Owner, which asks.
- **A Worker never refines.** It has no channel to the User either. Part
  3.7 says what it does instead.
- **At most one `board` holder per root while refinement is on** (TRQ7).
  Two holders would ask the User the same questions twice and write
  competing records. The chain catches the second (part 4, race 3), and a
  parse-time refusal prevents it.

### 3.4 The protocol: rounds

*Not built.* A **round** is one message from the Manager to the User about
one ticket that needs an answer. A refinement **attempt** is at most **N**
rounds (TRQ2 proposes N = 3).

**1. rite hands the ticket to the Manager.** At the start of each cycle the
supervisor, **outside the boundary**, reads each `scheduled` ticket once
(DF4's ledger decides the set, and a single GET reads each member) and
computes its state. Tickets in `NOT REFINED`, `STALE`, `ASKING` or
`PROPOSED` are listed in the board holder's instruction, with their title
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
(TRQ3 proposes `ok`, `yes`, `accept`, `lgtm`) accepts **T's latest
proposal**, and only that one. Any other text is an answer or a correction,
and it feeds the next round. No model decides whether "sounds fine I guess"
is a yes. It is not; it is an answer, and the next round proposes again.

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

**7. No answer, and the bound.** A round is open until the earlier of an
attributed reply and its **deadline**. TRQ2 proposes 24 h, or the close of
the next check-in window if that is sooner, and never later than Slack's
thread horizon (`THREAD_HOURS`, `slack.py:95`). At the deadline:

- **PARKED (no answer) is declared only once rite has read that thread
  past the deadline** and found nothing sent before it. Send time comes
  from Slack (`_Relayed.sent_at`), not from when rite polled. A reply sent
  at 23:59 and read at 00:10 counts. While the thread has not yet been read
  past the deadline, the state stays `ASKING (deadline passed, thread not
  yet read)` (part 4, race 6).
- If the thread *cannot* be read (the relay was down past the horizon, or
  the root was dropped), the result is **PARKED (thread unreadable)**, said
  as that and never as "no answer".
- After round N with no accept: **PARKED (not agreed after N rounds)**.

**Parking** posts once, to the User's action destination (RP1; the Owner's
DM until RP1 lands) and on the ticket as a comment. The notice says what is
still missing and the one thing that resumes it. The ticket is then off the
Manager's list until a person acts. **There is no automatic re-ask**: a
reminder is a round, and rounds are bounded.

**Resuming** needs a person's action, so it cannot loop:

- a reply attributed to the ticket. The parking notice says to reply in the
  DM starting with the id, because an old thread may be past the horizon;
- `rite refine reopen <ID>`;
- or an edit to the ticket's title or description, which changes the hash
  and starts a new attempt from `NOT REFINED`. **A User who fixes the ticket
  themselves has resumed it.**

**8. It never blocks the Manager.** Refinement is per-ticket state. While a
ticket is `ASKING`, the Manager works anything that is `REFINED`. When
every scheduled ticket is waiting on the User and nothing is in flight, the
loop's verdict is a new one, `waiting-on-user`, never `idle`. Its line
names each ticket and what it waits for. The supervisor then waits
**without starting sessions** until an attributed reply arrives or a
deadline passes. That is the same "awaiting a reply" wait DF2 needs, and it
is built once (TR2 depends on it). The dogfood's F12 (five sessions in five
minutes, none touching a ticket) is what happens without it.

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

- **`rite refine accept <ID> [--file <draft>] [--as-written]`**, run by a
  person. `--as-written` accepts the ticket's own text, for a ticket a
  human wrote properly. The command reads the ticket once and shows the
  person the definition of done and the hash it binds to. **It then reads
  its confirmation from `/dev/tty`** and refuses when there is none. That
  makes the step physically the person's: a model's Bash tool is expected
  to have no controlling terminal, and piping to stdin does not reach
  `/dev/tty`. ⚠ **Not measured** in the Claude app's Bash tool, in Goose
  or in Cursor (TR0). If any of them has a tty, this is a convention there
  and not a barrier, and the note must say so. When there is no tty it
  prints the exact command for the person to run in their own terminal.
- **The Dispatch session** (the person's own Claude session at the project
  root) is not a Manager and rite cannot see its conversation. So its
  `/refine` ends at `rite refine accept`, and the provenance says
  `attested`, not `accepted`. rite does not claim to know what was said in
  that session.
- **A ticket that already has a definition of done in its description.**
  TRQ6: under the fail-closed default it still takes one round, "RT-10
  already has a definition of done: <quoted>. Reply ok to start it as
  written", or a person's `rite refine accept --as-written`. The
  alternative, trusting it when the description's last editor is not an
  identity rite gives a Manager, needs a measurement first, and on Jira
  there is no such identity to compare.

### 3.7 Everything that consumes the predicate

*Not built.* Each consumer is marked "instructed" or "enforced". Both
halves are standard (TRQ1, decided); the column says which mechanism does
the work.

| consumer | change | half |
|---|---|---|
| **Worker launch** (`cli/main.py:5760`, `prompt = f"Work ticket {ticket}."`) | The supervisor reads the ticket once, checks it, and puts **the record itself** in the start prompt: id, checklist, scope, Verify. What was checked is what was delivered, so there is no window between the check and the Worker's read (part 4, race 4) | delivery: both halves. A refusal on a missing record is enforced |
| **`rite route --ticket`** (new flag) | attaches the record snapshot to the routed text the same way | delivery: both; refusal: enforced |
| **broker** (`managers/broker.py:168`) | refuses a Worker request for a ticket that is not REFINED, and says which state it is in | enforced |
| **loop** (`loop/__init__.py:424`, `_ready`) | only REFINED counts as ready. The others are listed with their state, and the new `waiting-on-user` verdict applies | enforced |
| **the Owner's assignment to Managers** (`scheduler/__init__.py:617`, which also lists `scheduled`) | assigns only tickets that are REFINED. It is a third reader of the ready signal, beside the loop and the broker, and it must not have its own | enforced |
| **assignment to a Worker** (`coordination/distribution.py:164`, which removes `scheduled` when it adds the Worker's label) | removes `ready-to-work` in the same `label()` call (part 3.10) | view |
| **`rite board show`** | a status line and the record | both |
| **`/ticket`, the Worker's `CLAUDE.md`, `/refine`, the prompts** | part 3.5 | instructed |
| **the review checklist** (`templates/review-checklist.md:202`) | its "would this ticket's definition of done still be met" test now has a definition of done to read: the record | both |
| **the Worker's stop** | today it lands nowhere (`question.json`, part 1). It must reach the Manager, and through the Manager `rite status`. **Whether a sandboxed Worker's handover or reply reaches the host today is not established here.** TR4 establishes it before anything relies on it | both |

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
  label** and says so twice: a line in the board holder's instruction, and
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

- **Who writes.** The supervisor of the `board` holder, and only that one
  among Managers (TRQ7 makes it one). Also a person's `rite refine accept`
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
   time, and PARKED only after the thread has been read past the deadline.
   Not by which of the reply and the deadline rite happens to see first.
7. **Thread eviction.** Today the relay reads at most `THREADS_MAX = 10`
   roots (`slack.py:83`), newest first, and drops roots older than 24 h. A
   busy Owner posting ten replies would silently stop reading an open
   round's thread: the answer is lost, and whether it is lost depends on
   how many other messages were posted before the User replied. **So open
   round roots are pinned outside the LRU list while their round is open,
   and read past their deadline, whatever their age.** At most K rounds are
   open per Manager (TRQ2 proposes 5). A ticket beyond K waits in
   `NOT REFINED`, with the reason stated. It is not sent into a thread
   nobody will read.
8. **Two `board` holders.** Refused at parse while refinement is on
   (TRQ7). If one gets through anyway, race 3 catches its records.
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
| A Worker starts only on a refined ticket | its start prompt carries the record, and `/ticket` checks | the broker refuses. The loop does not count it as ready |
| A bare ticket labelled `scheduled` | listed as NOT REFINED, and a Manager is told to refine it first | **not ready**, whatever its label |
| Routed free text that is really ticket work | instructed: ticket work goes with `--ticket` | ⚠ **not enforceable as such**: rite cannot tell free text naming a ticket from any other text. TRQ5's recommendation makes every route declare `--ticket` or `--no-ticket`, and shows every `--no-ticket` route to a person |
| A human `rite loop` with no Manager | reports NOT REFINED tickets | dispatches none of them until a person refines or attests them |

**What enforcement changes, accepted with the ruling.** It changes
behaviour for anyone who puts a bare ticket on a board and expects work to
start, and that includes Robert's own boards: `RT` on Jira,
`rite-dogfood-board` on GitHub, and the dogfood's `KAN`. Listed so that the
first run after the upgrade surprises no one.

- On the first run after upgrading, **every** `scheduled` ticket is NOT
  REFINED. Nothing starts until each one is refined. With N = 3, that is up
  to three messages per ticket to the User, and at most K rounds open at
  once.
- **A well-written ticket still needs a person's word**: one `ok`, or a
  `rite refine accept --as-written` at a terminal, under the fail-closed
  default of TRQ6. There is no blanket accept, deliberately: it would be
  an invented definition of done by another name.
- Throughput on a board of properly written tickets drops by one
  round-trip to the User per ticket.
- A project with no one reachable (no Slack, nobody at `rite connect`)
  stops at `waiting-on-user` instead of working. Today it would work bare
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
| **TRQ2** | N rounds per attempt; the round deadline; K open rounds per Manager | N 2–4; deadline 12 h, 24 h, or the next check-in; K 3–8 | **N = 3, 24 h or the next check-in close if sooner, K = 5.** 24 h is Slack's thread horizon today, so a longer deadline needs the pinning in race 7 anyway. K = 5 stays inside Tier 3 with the relay's other reads |
| **TRQ3** | The accept set | a word list; also `<ID> ok` in the DM; also a batch `ok <ID> <ID>` for as-written proposals | **`ok`, `yes`, `accept`, `lgtm`, in-thread or as `<ID> ok`, with the batch form only for as-written proposals.** Deliberately small: every word added is one a model or a person could type by accident |
| **TRQ4** | Does "you decide" (delegation) count as acceptance? | yes, for the Manager's next proposal; no | **No.** The Manager answers it with a proposal that one word accepts. P2 stays absolute, and the lazy User pays one more word |
| **TRQ5** | Routed free text that is ticket work | (a) accept the hole and instruct; (b) require `--ticket` for every route to an executor-only Manager; (c) every route carries either `--ticket <ID>` (checked) or an explicit `--no-ticket` (allowed, recorded, and listed in the standup and `rite status`) | **(c)**, revised 2026-09-28 now that enforcement is the standard (the first recommendation was (a)). rite still cannot tell whether free text is ticket work, but under (c) the Owner must *say* which it is on every route, and a person can see every `--no-ticket` route. (b) is stricter, and it blocks legitimate non-ticket work such as "run the suite on branch X". ⚠ The residual hole is an Owner model marking ticket work `--no-ticket`: visible, not prevented |
| **TRQ6** | A ticket whose description already has a definition of done | one round of "reply ok to start as written"; trust it when the last editor is not a rite identity | **One round.** Trusting by editor identity needs a measurement, and on Jira there is no identity to compare |
| **TRQ7** | More than one `board` holder in a root while refinement is on | refuse at parse; allow and rely on `CONFLICT` | **Refuse at parse**, naming both |
| **TRQ8** | Where questions and parking notices go | the Owner's DM now; RP1's action destination when it lands | Both, in that order. Named so RP1's design counts refinement as an action |
| **TRQ9** | Must a record have Verify commands? | required; optional but explicit (`"none agreed"`) | **Optional but explicit.** A lazy User rarely names a command. Requiring one either parks most tickets or pushes the Manager to propose commands, which the User then accepts unread. The Worker is told when none was agreed |

## Part 8 — where I am uncertain, and what gets measured first (TR0)

- **`/dev/tty` in model tool calls.** Is there no controlling terminal in
  the Bash tool of the Claude app, Claude Code, Goose and Cursor? It is
  load-bearing for part 3.6. If any engine has one, `accept` there is a
  convention, and the note is corrected.
- **Whether GitHub comments are read completely**, on an issue with more
  than 100 comments, and the fallback to `totalCount`.
- **Whether the record survives a Jira round-trip** through a single ADF
  text node and `adf_to_text`; and whether Jira's embedded comment list
  pages.
- **Whether the key is unreadable** from inside a Manager (seatbelt,
  Landlock) and a Worker (yoloAI), under an ungranted root with a control;
  and whether it is absent from the argv and environment of every rite
  process that holds it, read with CU1b section 4's `KERN_PROCARGS2`
  reader.
- ~~Whether the Jira filter for "needs refinement" works~~: **measured
  2026-09-28** on `bentora` (an exact 5 + 142 = 147 split), and the GitHub
  filter on `cli/cli`. Not yet run on `ritetest` (part 3.10).
- **Whether a sandboxed Worker's handover or reply reaches the host**
  (TR4). The dogfood suggests not, because its question stayed in the
  sandbox. That is one observation, not a measurement.
- **Whether a proposal gets rubber-stamped.** This is not measurable before
  the acceptance run (TR6). It is recorded there as observed behaviour, and
  it is not a gate.
- **DF2's wait.** TR2 depends on the supervisor waiting between cycles
  without starting sessions. That is DF2's open fix, and if DF2 lands in a
  different shape, part 3.4 step 8 follows it.

## Part 9 — where the spec text goes once decided

This note stays the design while TRQ2–TRQ9 are open (TRQ1 is decided), by the release plan's
rule that "a spec section for an undecided feature is the defect this
project spent two releases removing". When they are answered, TR3 writes a
new SPEC §6.7 ("Refinement: the record, the predicate, the protocol"). It
amends §9.4.2 (the Worker's scope loses "stop and guess nothing" in favour
of the record) and §9.10 (the orientation table's "Tickets on the board"
row gains the refinement step), and it adds a D-number per decided TRQ.
