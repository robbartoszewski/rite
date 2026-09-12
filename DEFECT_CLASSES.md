# Defect classes

Seven rehearsal rounds against this tool, plus the Bentora work alongside it,
produced roughly forty defects. Counting them is not useful. What is useful is
that they fall into ten classes, most of which recurred — and that for each
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
Bentora, written by different people who had not read each other's code.

**The last section is the list that matters.** It is the set of classes held
only by someone noticing, which is to say the prediction of what comes back.

---

## 1. The measuring instrument reports success while measuring nothing

The signature defect of this codebase and the one that has bitten every
session that went looking for it, including the ones that went looking for it
*because of this file's earlier drafts*.

A check, gate, probe or harness runs, finds nothing, and reports clean —
having never been in a position to find anything.

**Members.** Bentora: a regex for ANSI escapes that never matched, so the scan
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

**Members.** Bentora: an absent plural form quietly rendering the singular; an
undefined token resolving to a valid-looking value; English text rendering
through a Polish fallback locale — output that looks like a translation and is
not. A migration chain run forward from today's schema and therefore run from
step 0 exactly zero times, failing in order the first time someone starts
clean. rite: `kb add` of a non-HTML document producing tag-stripped binary that
sits in the index looking exactly like a good snapshot.

**Guarded by.** Content-type checking in `rite_ai.kb`. Nothing else. The
checklist's "missing or undefined input fails loudly" line.

**What still gets through.** The whole class, in rite. This is the class with
the fewest rite members and the most Bentora ones, which is worth reading as
"rite has not yet built the surface where this bites" rather than as immunity
— rite's templating and config defaulting are the places to expect it.

---

## 9. A message framed so it can be misread

The facts are right and the sentence still leads the reader somewhere false.

**Members.** `rite prepare --branch feature/RW-12` printing `ready: up to
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

**Members.** Bentora: `chat#14`'s second bullet, which fell out of every
ticket's definition of done during other work and ended up tracked by nobody
— found only because someone went looking for it. rite: the
`collect_status` → `_aggregate_line` seam, which is the one the commit under
review in round 7 was *written about*, and which all three reviewers
independently marked FAIL for having no test and no ticket owning it. The
defect I then had to fix in round 7 was sitting in precisely that seam. This
project has built a component nothing called at least seven times; a seam
nothing owns is the same shape with two owners instead of none.

**Guarded by.** The checklist's existing seam line — "every seam this change
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
