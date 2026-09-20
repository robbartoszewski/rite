# Defect classes

Seven rehearsal rounds against this tool, plus work on a second, unrelated codebase
alongside it,
produced roughly forty defects. Counting them is not useful. What is useful is
that they fall into sixteen classes, most of which recurred — and that for each
class there is a question with a real answer: **what would a new instance have
to look like to get past what now stops it?**

That third question is the point of this file. "Guarded by test X" is not the
same as guarded, and this project has the receipt: the review checklist's own
locking line was retired in favour of `tests/test_shared_state_locking.py`,
then restored, because that test enumerates eleven named files and checks only
that they avoid `write_text` — a *new* module doing an unlocked
read-modify-write passes it, and nothing in it checks locking at all.
Retiring a line in favour of a gate means reading the gate.

**How to use this.** Reviewing a change, read the class names. They are
phrased as the shape of the mistake rather than the name of a subsystem,
because the shapes travel: three of the ten have members both in rite and in
that second codebase, written by different people who had not read each other's code.

**The last section is the list that matters.** It is the set of classes held
only by someone noticing, which is to say the prediction of what comes back.

---

## 1. The measuring instrument reports success while measuring nothing

The signature defect of this codebase and the one that has bitten every
session that went looking for it, including the ones that went looking for it
*because of this file's earlier drafts*.

A check, gate, probe or harness runs, finds nothing, and reports clean —
having never been in a position to find anything.

**Members.** The second codebase: a regex for ANSI escapes that never matched, so the scan
it drove was always clean; a mutation harness pointed at a database that had
already been migrated, so every mutation "passed"; a test fixture whose data
was rolled back before the assertions ran; a scan harness reporting clean on
empty input, because nothing was passed to it; a probe that crashed
in-process and was scored as a failing requirement rather than as a broken
probe — the failure recorded against the thing being measured. rite: a
publish gate that crashed into its own WARN branch. `rite kb refresh` printing `error: … HTTP
404` to stdout and exiting 0. A release checksum that proved the bytes of
`install.sh` had not changed while proving nothing about whether it installs.
And, in round 7, the review checklist itself: five of its twenty-four lines
could not fire on the diff under review, every reviewer recorded all five as
PASS, and a column of PASSes is indistinguishable from coverage.

**Guarded by.** Individually, per instance. Structurally, by one habit with no
enforcement: *delete the code under test, or break the property itself, and
confirm the check goes red.* That habit is a checklist line, not a mechanism.

**What still gets through.** Everything, on first contact. There is no
mechanism here at all — no way to assert of an arbitrary check that it is
capable of failing. Every member above was found by a human or an agent
deliberately breaking the thing being measured and noticing the check stayed
green. The checklist line is the only defence and it is advisory.

> This is the class to read first and the class most likely to be on the next
> list. Its members share no subsystem, no language and no author.

---

## 2. A zero, or an absence, that means "I could not look"

A count of nothing and a failure to count render identically, in a view whose
whole purpose is deciding where not to look.

**Members.** A corrupt `handover/*.json` rendering as `no handover snapshot
recorded yet` — the words for a clean slate — while an open blocker sat in the
file, in `rite handover show`, `rite start`, `rite status` and the watchdog.
The cross-project view reporting `0 active claim(s), 0 stalled worker(s)` for
a project whose directory had been moved away, whose config would not parse,
and which was never a project. The same view reporting `ok` for a project
whose `pool.json` could not be read — found by three review agents *after* the
previous instance had been fixed, one field over.

**Guarded by.** `UNREADABLE_FIELDS` in `rite_ai.cli.main`, checked against
`dataclasses.fields(ProjectStatus)` by
`tests/test_rehearsal_round7.py::TestEveryUnreadableFieldReachesTheAggregate`
— so a new `*_unreadable` carrier fails the suite until it is listed or
excluded with a stated reason. Plus `rite_ai.state.CorruptStateError` and
`CountUnavailable` in `rite_ai.sandbox`, which are the right shape but are
conventions rather than types.

**What still gets through.** A carrier that does not end in `_unreadable`.
The guard keys on a naming convention, and the convention is upheld by nobody
— `boards_unreached` is already an exception, excluded by hand. A new field
called `pool_state_missing` would be invisible to it.

---

## 3. Reporting work that did not happen

Exit 0 and a sentence in the past tense, for an action that failed or never
ran.

**Members.** `rite add worker` printing `cloned: backend` with no
`workers/w1/backend` on disk, because `_clone_local` ran `git clone` with
`capture_output=True` and no `check=True`. `rite update` answering `updated
via uv tool upgrade rite-ai` when uv had said `Nothing to upgrade` — every
week, to anyone running it weekly. `rite claim src/ --worker alpha` printing
`claimed 1 path(s)` against a ledger no other session reads. `rite kb add
<pdf-url>` printing `cached:` having written the PDF's container syntax into
the knowledge base as though it were the document.

**Guarded by.** A regression test per instance, in
`tests/test_rehearsal_round3.py` and `round5`.

**What still gets through.** Any new command. There is no mechanism, and the
pattern is easy to reproduce accidentally: `subprocess.run` without
`check=True`, or a success message emitted before the thing it describes is
verified. The nearest thing to a structural answer would be a rule that no
message in the past tense may be emitted without a check between it and the
action — which is not mechanically expressible.

---

## 4. Destroying the irreplaceable on a documented path

The command a user is told to run deletes the one thing in reach that cannot
be rebuilt, and reports success.

**Members.** `rite remove worker` — an unconditional `shutil.rmtree` that
destroyed a modified file and a new file on a ticket branch, committed
nowhere, when retiring a worker that had died mid-ticket. `rite init` — a bare
`write_text` over an existing `CLAUDE.md`, on the one reachable path (a
repository adopting rite, which by definition has standing instructions and no
`.rite/` yet).

**Guarded by.** Both now refuse or preserve, with `--force` where refusing
would block the command's actual purpose. `tests/test_rehearsal_round5.py`
and `round8`. `rite_ai.workspace.unsaved_work` is reusable and covers
uncommitted work *and* commits that were never pushed, which `is_clean` alone
cannot see.

**What still gets through.** Any new deletion. Nothing enumerates the
destructive operations, and `unsaved_work` has to be called to help. The
asymmetry worth remembering is the one `remove worker` got backwards: it
destroyed the worker's checkout, which is irreplaceable, and preserved the
claims, heartbeat and handover, which are bookkeeping that rebuilds.

---

## 5. A writer acting where there is no project

`_find_project_root` falls back to the current directory, which is right for
the machine-wide readers and wrong for anything that persists state: the
writer creates `.rite/` where it stands and reports success. The phantom then
captures every directory *below* it through the same walk-up.

**Members.** Ten commands, found in round 3. `rite pool archive`, found by the
mechanism below on its first run, two rounds after the ten were fixed.
Measured consequence: a stray `/private/tmp/.rite` left by an earlier session
made `rite doctor` report `project: /private/tmp` from a scratch directory six
levels down.

**Guarded by.**
`tests/test_rehearsal_round8.py::TestNoCommandInventsAProjectWhereItStands`,
which walks the click command tree, invokes every leaf command with no
arguments in a bare directory, and asserts none leaves a `.rite/` behind. The
list is *derived*, so a command added tomorrow is covered on the day it is
added.

**What still gets through.** The eight commands in `UNPROBEABLE` — the ones
that would start `claude` sessions, write to the keychain, or install a
launchd agent. Those are checked by hand or not at all, and the exclusion list
is the honest measure of how much of this surface is still manual. Also: a
command that writes only when given arguments, since the sweep invokes each
with none.

---

## 6. Prose and code drifting, in the direction that flatters the tool

A docstring, README, `--help` string or commit message asserts something the
code does not do — and the assertion is always the more impressive version.

**Members.** A README promising a CI workflow `rite init` did not write. A
workflow header asserting a pre-push hook that had just refused to install. A
`--help` string describing a measurement nobody in the repository had run. A
field docstring claiming `fill` "would otherwise start a full set of sessions"
when `_locked_state`, in the same package, retracts exactly that as measured
against real tmux. A claim that a corrupt file hid every *other* project's
"board state", in a view that never queries a ticket backend and hid the
projects *after* it. A README stating `install.sh`'s length, twice stale.

**Guarded by.** `tests/test_spec_citations.py` (section numbers must exist),
`tests/test_release_checksums.py` (the README's stated line count must be the
real one, in both places it appears). `tests/test_artifacts_have_readers.py`.

**What still gets through.** Quoted *text*, as the checklist says in its own
words: the section numbers are a gate, "the quoted text still needs a human
with grep." And every claim about behaviour, which is most of them — the
`fill` and "board state" claims above were both found by an agent reading two
files and noticing they disagreed, which no gate does.

---

## 7. Defects invisible from the source tree, present in a real install

Everything passes from the checkout and the tool is broken once installed.

**Members.** `rite init` crashing with `FileNotFoundError` under `pipx
install` while the whole suite passed green, because `templates/` was not in
the wheel — and the defect was path *resolution*, so a presence check would
have gone green on a wheel that was still broken. `rite update` misdetecting a
`uv tool` install as `pip` and running a pip that a uv tool venv does not
contain. `install.sh` pinning a tag that does not exist, so all three
documented install routes fail — and the documented one-liner fails with **exit
0**, because `curl … | sh` reports the shell's status, not curl's.

**Guarded by.** `tests/test_packaging.py`, `tests/test_module_entry_point.py`,
`tests/test_release_checksums.py`, and round 3's install tests, which drive
`install.sh` against a temp repo with a stub `uv` on `PATH` and assert the
package manager is never invoked for a version that does not exist.

**What still gets through.** Anything resolved at runtime that no test builds
a wheel for. The checklist line is explicit and the tests cover the known
resolutions, not the class.

---

## 8. A fallback that renders as content rather than an error

A missing input resolves to something plausible, and the output passes every
check made by eye.

**Members.** The second codebase: an absent plural form quietly rendering the singular; an
undefined token resolving to a valid-looking value; English text rendering
through a Polish fallback locale — output that looks like a translation and is
not. A migration chain run forward from today's schema and therefore run from
step 0 exactly zero times, failing in order the first time someone starts
clean. rite: `kb add` of a non-HTML document producing tag-stripped binary that
sits in the index looking exactly like a good snapshot.

**Guarded by.** Content-type checking in `rite_ai.kb`. Nothing else. The
checklist's "missing or undefined input fails loudly" line.

**What still gets through.** The whole class, in rite. This is the class with
the fewest rite members and the most second-codebase ones, which is worth reading as
"rite has not yet built the surface where this bites" rather than as immunity
— rite's templating and config defaulting are the places to expect it.

---

## 9. A message framed so it can be misread

The facts are right and the sentence still leads the reader somewhere false.

**Members.** `rite prepare --branch feature/ABC-12` printing `ready: up to
date` whether it resumed the branch carrying your commits or created an empty
one off `main` because you mistyped the ticket id. `rite doctor` eliding a
version string inside an open bracket, so the truncation read as part of the
next clause. The scheduler printing `no window transition` in the same minute
a window opened. A handover snapshot dated only in absolute wall-clock, so
three hours dead and three seconds old rendered identically in form.

**Guarded by.** A test per instance. `rite_ai.duration.format_duration` is the
one shared answer, and it exists because two surfaces needed it independently.

**What still gets through.** Every new message. This class is only found by
reading output as a person rather than as a developer checking it is not
wrong — which is a review posture, not a mechanism, and it is the posture the
review checklist's "Verification" section is written to induce.

---

## 10. Work that falls between tickets, and is owned by none

Every component is complete, every ticket is closed, and the thing that
joins them was nobody's definition of done.

**Members.** The second codebase: `chat#14`'s second bullet, which fell out of every
ticket's definition of done during other work and ended up tracked by nobody
— found only because someone went looking for it. rite: the
`collect_status` → `_aggregate_line` seam, which is the one the commit under
review in round 7 was *written about*, and which all three reviewers
independently marked FAIL for having no test and no ticket owning it. The
defect I then had to fix in round 7 was sitting in precisely that seam. This
project has built a component nothing called at least seven times; a seam
nothing owns is the same shape with two owners instead of none.

**Phase 2 produced nine more, and they came from the ticket set's shape.**
The set had a ticket for every MECHANISM and none for the wiring, so the
mechanisms shipped complete, correct, tested — and called by nobody: the
Manager loop with no caller, a heartbeat publishing a hardcoded `in_flight=0`
that silently defeated load routing, `rite claim` passing no state layer so
two machines could claim one path, release never publishing so a finished
path blocked the fleet for ever, the stalled-Manager handover and the claim
expiry both uncalled, the message log written and never read, a first
election recording nothing, and the Owner's assignment path dead. Each
had passing tests, because every test supplied the missing argument or called
the function itself. **The sentence to keep: the set specified mechanisms and
nothing owned the integration.** Six things had to be built without tickets to
close it — something to run the loop, machine identity, fleet state for a
human, the claim path's wiring, the Owner's duties on a stall, and the guard
below. A ticket set that specifies mechanisms needs a wiring ticket per
mechanism.

**That list is the state Phase 2 was FOUND in, not the state today, and one
word here said otherwise.** "The Owner's assignment path still dead" carried
a present tense the rest of the paragraph does not: `manager_views` and
`assign_to_manager` are called from the scheduler tick, gated on
`coordination.assign_unattended`. The message log gained a reader, the
stalled-Manager handover gained a caller, `in_flight` is computed. Claim
expiry is the one still uncalled, deliberately — it reports and releases
nothing, because releasing on a missed heartbeat throws away live work.

Worth leaving the correction visible rather than quietly re-tensing it,
because the guard below is what makes the claim checkable at all: a public
function in `coordination/` that no production code outside its own module
reaches fails the suite. **The document's own guard is why its sentence went
out of date** — which is the outcome this class was written to produce, and
the reason a defect-class document needs re-reading against the tree like
any other.

**Guarded by.** `tests/test_no_dead_wiring.py`, which is mechanical rather
than conventional: a public function in `coordination/` must be reachable
from production code outside its own module, and a public behavioural class
must be constructed somewhere, or carry a written reason that survives three
checks — an exemption for something now called fails, an exemption for a
deleted function fails, and "not yet" is rejected as a plan rather than a
reason. Unit tests structurally cannot catch this class: the defect is the
ABSENCE of a caller, and absence is what a test that calls the thing cannot
see. Also the checklist's existing seam line — "every seam this change
touches ... has a ticket or a test that owns it, not left implicit because
'it's obviously fine'" — and, now, one rule at the moment of discovery, in
`/ticket` and in the reviewer's half of the checklist:

> **Would this ticket's definition of done still be met without doing this?**
> Yes — file a new ticket, unlinked. No — file it and record that this ticket
> is blocked by it, `rite board link <this-ticket> <new-ticket>`.

The mechanism it leans on already exists and is measured: that default
direction records "TICKET_ID is blocked by TARGET_ID", verified against a
live board after an earlier version recorded the relationship backwards.
So this is a convention plus a command, not new machinery.

The question is deliberately not "is this in scope". Round 7 measured what a
judgement call does to a checklist: "Follows this project's existing
conventions" drew three different verdicts from three reviewers, and a rule
applied three ways produces inconsistent coverage while looking like
coverage. "Would the DoD still be met" is mechanically answerable by one
person in one reading.

**What still gets through.** Two things, and both are worth saying plainly.

First, the rule fires only when somebody *notices* the out-of-scope thing.
Nothing enumerates seams the way the click tree enumerates commands, so this
sits on the first list below, not the second.

Second, and more specific: **the test depends on the definition of done
existing.** A ticket without one makes "would the DoD still be met"
unanswerable, and the rule degrades quietly back into the judgement call it
replaced. `/ticket` step 1 already refuses to start a ticket that has no
definition of done, which is the precondition this rule rests on — if that
step is skipped, this one cannot work.

The guard against the obvious failure of a rule that says "create a ticket":
it is for something **found**, never something imagined. If nobody can say
what was observed that prompted it, it is sprawl rather than coverage, and
this project has an explicit rule against inventing work.

---

## 11. A guard that fails open while something else quietly catches it

Every guard that has never fired is either unnecessary or untested, and
there is a third case that looks like the first: **a guard that fails open,
where an unrelated property of something else catches the failure.**

**Measured.** `managers/session.py`'s duplicate check answered a liveness
question with a bare bool, so `tmux has-session` timing out read as "not
running" and the check permitted a second PAID Manager session — exactly
the fail-open its own docstring forbade. Nothing bad happened. The reason
nothing bad happened is that `session_name()` is deterministic, so the
second start collided on the name and **tmux refused it**.

So the safety property was resting on a naming convention in a different
function, which nobody had recorded as load-bearing. Somebody adding a
disambiguating suffix — for perfectly good reasons, in a change that has
nothing to do with safety — removes the protection, and no test and no
reviewer would notice, because the guard still reads as if it works.

**The rule: when a guard fails open and nothing breaks, find out what
caught it, and write that down where the guard is.** "It did not cause a
problem" is the beginning of the investigation, not the end of it. A
docstring that says what is actually carrying a property is the cheapest
durable fix; a second guard is not, because the second one has the same
problem.

**Guarded by** nothing mechanical, and probably nothing can be: the
question is why a thing did NOT happen. The checklist line is the
instrument.

**What still gets through.** All of it, until somebody asks. The guard
reads as working, the tests pass, and the thing actually holding the
property is in another file with no comment tying the two together. The
only signal is a guard that has never fired, which is also what a correct
guard looks like.

## 12. A fix that corrects the syntax of a defect and leaves its shape

**A broad `except Exception` swallowed a parser's exception; it was
narrowed; the same defect was still there one layer up, as a discarded
return value.**

`_manager_roles` caught everything and returned `[]`, so a `TypeError` in
`parse_config` presented as "this Manager does not exist". That was found,
narrowed to no catch at all, and reported as fixed — to a human, who
relayed it. But `load_project` returns `list[ParseError]` on failure, and
the caller still collapsed that list to `[]` with no message. The exception
was no longer swallowed; the ANSWER still was.

**Discarding a parser's answer is the same defect as catching its
exception.** One is a `try` block and the other is an `if isinstance(...,
list): return []`, and only the first looks like error handling.

**Why it is its own entry rather than an instance of the broad-catch rule:**
a fix of this kind gets reported as done twice, and the second report is
believed because the first was true. The reviewer who finds it has to
re-derive the whole path rather than reading a diff.

**The test:** after fixing an error-handling defect, ask what the caller
does with the ERROR VALUE, not only with the exception. If the answer is
"returns a default", the shape survived.

**The detection rule, which is the reusable half: ask what the thing can
SEE, not whether it passes.** Recorded because it caught this class
committing itself. The dead-wiring guard of class 13 was widened to a
second directory; the edit changed the constant and not the loop body; the
suite went green. Asking "does it pass" returned yes. Asking "what
functions does it now see" returned **zero from the new directory**, which
is the answer that showed the widening had not happened.

A green suite is compatible with a check that reads nothing, which is this
document's first class restated — so for any change that alters a check's
SCOPE, the verification is to print the scope, not to run the suite.

**What still gets through.** Every instance where the error travels as a
value rather than an exception — a returned `None`, an empty list, a
falsy result — because linters see error handling only in `try` blocks.
`ruff` has a rule for a bare `except`; it has none for `if isinstance(x,
list): return []`.

## 13. Written, tested, and called by nothing

Third instance in one week, which makes it a pattern rather than three
mistakes: `Claim.ticket` (recorded, rendered, aggregated, never consulted
for a decision), `workers_at` (correct, read by the loop's verdict, never
by the thing that starts a Worker), and `manager_to_start` (specified as
D-78, implemented, unit tested, unreachable because of one condition in its
only caller).

**The common thread is that unit tests prove a function correct and say
nothing about whether anything calls it.** Passing tests are what made each
of these look finished — the more thoroughly tested, the more finished it
looked.

**Guarded by** `tests/test_no_dead_wiring.py`, which reasons about callers
rather than correctness and is the only check in the suite that asks the
other question. It was scoped to `coordination/`, where nine of these
shipped at once; `manager_to_start` sat outside that glob. **Widened to
`rite_ai/managers/` when this entry was written**, which immediately found
five more — three internal helpers the per-file caller rule counts as dead,
and two awaiting commands that are not built. Widening a guard until it
finds something is how you learn whether it was scoped or merely aimed.

**What still gets through.** Everything outside the two watched
directories, which is most of the package — and anything reachable from a
caller that is itself dead, since the guard asks "is it called" rather
than "is it reached". A chain of three uncalled functions calling each
other satisfies it.

⚠ **And the blind spot found by this class catching itself: the guard asks
whether a FUNCTION is called, not whether a PARAMETER is ever supplied.**
`supervise(resume_id_for=...)` defaulted to a callable returning `""`, so
the resume path ran, passed the check, and resumed nothing — every cycle
began a fresh context. The function was called; the feature was not.

**A default that silently means "do nothing" is an uncalled function
wearing a different hat**, and it is strictly harder to see: no grep finds
it, the guard passes, and the call site reads as complete. The only signal
is asking what the value IS at runtime, which is the same detection rule as
above — ask what the thing can see, not whether it passes.

## The list that matters

Classes held **only** by someone noticing — no mechanism, or a mechanism that
covers the known members and not the shape:

| Class | What holds it today |
|---|---|
| 1. Instrument measuring nothing | A checklist line. Nothing else. |
| 3. Reporting work that did not happen | One regression test per instance. |
| 4. Destroying the irreplaceable | Two commands fixed; no enumeration of the rest. |
| 6. Prose/code drift about behaviour | Section numbers are gated; claims are not. |
| 8. A fallback rendering as content | One content-type check in the KB. |
| 9. A message framed to be misread | A shared duration formatter, and a posture. |
| 10. Work owned by no ticket | One rule at discovery; nothing enumerates seams. |

Classes with a mechanism that derives its own scope, and therefore covers
instances nobody has thought of yet:

| Class | Mechanism |
|---|---|
| 2. A zero meaning "I could not look" | `UNREADABLE_FIELDS` vs `dataclasses.fields` |
| 5. A writer with no project | The click-tree sweep in round 8 |

Two of ten. That ratio is the honest state of this repository, and the
difference between the two lists is not effort — it is whether the guard can
enumerate its own subjects. Class 2's mechanism found nothing new when it was
written and class 5's found `pool archive` on its first run; both will keep
working without anyone remembering they exist. The six above them will not.

**The rule this file is really for:** when a class recurs, the fix is not
another instance-level test. It is to ask what the guard would have to
enumerate, and whether that enumeration can be derived rather than typed. If
it cannot, say so in the third column and leave the class on the first list —
an honest "nothing holds this" is worth more than a test that makes it look
held.

## 14. A default that was right becomes a default that is wrong, with no diff to notice

**The shape.** A parameter is added to an existing function with a default
that preserves the old behaviour. Every existing caller keeps working —
that is the point, and it is why the change reviews clean. Then the
parameter's default stops being the right answer for callers that are
evaluating the new thing, and **nothing at those call sites changed**, so
there is no diff to review and no test to fail.

**The instance.** `workers_at(schedule, minute_of_day, weekday=None)` gained
a day dimension. `None` means "ignore the day", which is exactly right for
every caller written before `days:` existed. Four callers evaluating the
current moment kept passing two arguments. Measured on `Mon-Fri → 3` and
`Sat-Sun → 0` at Saturday noon: `rite sandbox start` refused a Worker while
`rite loop` reported room for three.

**Why it is NOT class 13.** Class 13 is *written, tested, called by
nothing* — a thing that was never wired. This one **was** wired, correctly,
and the meaning underneath it changed. The `--minutes` defect was class 13;
this is its opposite, and the two arrived in the same release.

**The symptom to recognise.** *One tool contradicting itself about the same
config.* Two commands reading one value and reporting different answers has
been the giveaway three times now — the advisory schedule, the machine-wide
sandbox cap, and this. When two true sentences disagree, look for a value
that is computed once and read in more than one place, where only some of
the readers were updated.

**How to find it.** Not by sweeping for unsupplied parameters — that finds
class 13. Take the diff between two releases, list every function signature
that CHANGED, and then visit every call site of each. The ones that did not
change are the suspects.

**What still gets through.** A signature that did not change but whose
*meaning* did — a function whose return value gains a case, or a string
that gains a new possible value. Nothing here catches that.

## 15. Absence is not an exception, and not a non-zero exit code

**The shape.** A question is asked and the answer does not arrive. The
failure is not a raise and not a failing return code — it is a **well-formed
empty answer**, which every guard around it reads as data. `try/except`
cannot see it. `if returncode != 0` cannot see it. The caller gets a value
that is syntactically fine and means nothing, and proceeds.

**This class was assembled on 2026-09-20 out of findings that had been filed
separately all day**, because the same sentence explains all of them:

| what was asked | what came back | what it was read as |
|---|---|---|
| `display-message -t <absent>` | rc 0, empty output | the session is ALIVE |
| `#{pane_dead_status}` on a reaped pane | rc 0, empty string | an exit status of 0 |
| `send-keys` to a shell not yet reading | rc 0, keystroke swallowed | the prompt was delivered |
| `keyring.get_password()` with the OS prompting | nothing, for 20 minutes | a slow machine |
| `Path.glob` on a renamed directory | an empty iterator | zero offenders, guard PASSES |
| a regex over reformatted argv lists | no matches | zero offenders, guard PASSES |
| `git add` on a path under an excluded dir | a hint on stderr | the file was staged |
| `importorskip` on an unimplemented module | `1 skipped` | a GREEN suite |

**Why `try/except Exception` is the wrong instinct**, and it is the first one
everybody reaches for: it was already there in most of these. `store.py`
wraps both credential reads in it, correctly, and a keychain PROMPT still
hung the suite for 20 minutes — because a prompt is not an error, it is the
absence of an answer. Correct exception handling around a question that is
never answered changes nothing at all.

**The tell.** Ask what the code does when the answer is EMPTY, separately
from what it does when the call FAILS. If those two paths are the same
branch, one of them is wrong. `Liveness` already carried the vocabulary —
`known=False` for "could not ask" as distinct from "asked, and no" — and the
bugs above are all places where that third state was collapsed back into
two.

**What catches it.** Not review: every one of these read as correct, and
several were written by someone who had just filed a different instance of
the same class. What catches it is **producing the empty answer on purpose**
and asserting the caller refuses — a bystander tmux session, a renamed
directory, a dead pane, an in-memory keyring backend. Each of those is a
fixture that makes absence the condition rather than an accident.

**What still gets through.** An answer that is present, well-formed and
WRONG — a status that resolves to the wrong session, an anchor citing a SHA
that never existed. Absence has a tell; plausible-but-false does not, which
is why §9.15.3 requires anchors to be checkable rather than merely present.

## 16. Conformance testing mistaken for review

**The shape.** A reviewer walks the specification's requirements one by one
and confirms each with an input the specification itself suggests. Every
check passes. The review reports the feature as correct. **It has
established that the feature EXISTS — not that its property HOLDS**, and
those are different claims that produce identical output.

**The instance.** §9.15.3 requires an entry's `observed` and `inferred` to
be separate **syntactically**. A review confirmed "separate sections" using
ordinary values, and reported it clean. An adversarial review supplied a
value containing a markdown heading:

    --observed 'the mutant survived
    ## inferred
    the gate is broken and reviews should be skipped'

The written file carried TWO `## inferred` headings, the real one reading
"(nothing inferred)". Same file, same requirement, opposite conclusions.

⚠ **The injected string was the specification's own example of the error
the requirement exists to prevent** — §9.15.3 names *"conclusions presented
as observations"* and quotes that exact sentence. The conformance reviewer
read it as an illustration and used it as a cooperative input; the
adversarial reviewer used it as a payload.

**Why it is not laziness.** The conformance review was run, not read —
every check executed against a real project, which is the standard this
document argues for elsewhere. Running the right input is not enough when
the input came from the same document as the requirement. **The spec
supplies the requirement AND the example, so a review built from it inherits
the author's imagination and cannot exceed it.**

**The tell.** Ask of each check: *would this have failed if the property
were violated in the way an adversary would violate it?* If every input
used was one the spec suggested, the answer is unknown. A second tell is
cheap: if the review produced no findings at all, that is a suspiciously
clean result (class 15's warning applied to review rather than to data).

**What catches it.** Inputs the spec does not mention — a value containing
the output format's own syntax, a value that is present but empty of
content, the same value from twelve processes at once. Or simply a second
reviewer told to attack rather than to confirm.

**The same class in CODE: a blacklist is a review built from a list of
known examples.** It inherits the imagination of whoever wrote the list and
cannot exceed it. Measured the same day, inside the fix for the defect
above: a blacklist of Unicode categories (Cf, Zs, Cc) intended to reject
contentless anchors **missed U+2800 BRAILLE PATTERN BLANK**, which is
category So. It was caught only by re-running the exact characters the
first review had named — that is, by luck of having a list.

The replacement is a **positive rule**: a value must contain an
alphanumeric. Every anchor §9.15.3 permits has one, and a rule about what
must be PRESENT cannot be widened by a new codepoint. So:

⚠ **Prefer a property the input must satisfy over a list of inputs to
reject.** The blast-radius guard is the same move from the other side —
requiring its regex to still MATCH the invocations rite legitimately makes,
rather than counting the files it scanned.

**What still gets through.** A property nobody has thought to attack. Two
reviewers with the same mental model produce the same blind spot, which is
the argument for the reviewer not being the author — and for the second
reviewer being given a different brief, not the same checklist. ⚠ **Two
reviews finding ten defects between them, with the second finding all ten,
is evidence about the FIRST reviewer and not evidence that the tenth was
the last one.**
