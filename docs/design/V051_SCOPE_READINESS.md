# v0.5.1 scope — is it ready to implement? (2026-09-20)

**READY — all nine items are specified, every open question is answered or
routed, and one answer produced a defect that is fixed rather than noted.**

Revised 2026-09-20 after the project owner answered Q1, Q2, Q5 and Q7, and
after rite-dd's review of §9.15. The earlier revision of this document said
"seven of nine"; what closed the other two is recorded below rather than
edited away.

"Ready" here means what was asked for: **someone else could pick this up
and build it without asking anything that is not on the questions list.**
Not "I have stopped writing."

---

## Status, and where the specification lives

| # | Item | State | Spec | Landed |
|---|---|---|---|---|
| 1 | Resume passes the session id | **BUILT + verified** | §9.14.9 | `edda205`~ |
| 2 | quit / finished / crashed | **BUILT + verified** | §9.14.10 | `a4d745a` |
| 3 | Duration bound on the CLI | **BUILT + verified** | §9.14.5, D-82 | `17d83ae` |
| 4 | Alias-collision check | **BUILT + verified** | §9.14.7a | `5a9acac` |
| 5 | Per-Manager identity | **BUILT** | §9.14.9, D-77 | pre-`1381bc1` |
| 6 | Prompting the session on start | **BUILT + mutation-checked** | §9.14.11a, D-90 | `a555091` |
| 7 | Managers in `rite status` | **BUILT** (rite-dd) | — | `f803a10` |
| 8 | Ctrl+C stops both; `rite manager stop` recovers | **BUILT + mutation-checked** | §9.14.12–13, D-85 | `52b64c9` |
| 9 | Issue recording (`--record-issues`) | **BUILT** (rite-dd) | §9.15, D-83..D-93 | `93f87f9` |

Found and fixed during refinement, not on the original list:

| Defect | Why it mattered | Landed |
|---|---|---|
| `days:` parsed but ignored by four callers | Saturday configured for 0 Workers: `start_worker` refused while `rite loop` reported room for 3 | `edda205` |
| `supervise` continued on any unrecognised verdict | `None`, `""`, `"Idle"`, an error string each spent a session | `857a5b6` |
| tmux `-t` prefix-matches | `ending("lead")` read `leader`'s exit status and could return FINISHED, which resumes | `3f71d5c` |
| `liveness` reads absent-as-alive | `rite start` refuses whenever the user has any tmux session open | rite-dd |

---

## Acceptance tests

Runnable invocation and expected output. Where an item is built, the test
has been run and the output is real; where it is not built, the test is the
contract and is marked as such.

### 1. Resume passes the session id — RUN

```
$ python -c "
from rite_ai.managers.transcripts import latest_session_id, project_transcript_dir
from rite_ai.managers.supervise import launch_command
# a transcript whose FILENAME differs from the sessionId inside it
print(launch_command('claude', latest_session_id(proj, t0, base)))"
claude --resume NEW-ID-INSIDE
```

The id must come from the file's **contents**, not its name. Asserted by a
fixture where the two deliberately differ.

### 2. quit / finished / crashed — RUN

```
$ pytest tests/test_manager_endings_and_resume.py -q
31 passed
```

The mapping is asserted with scripted tmux replies on every platform;
real-tmux tests stand down only when tmux supplies no status at all, and
that stand-down has its own tests bounding what it will swallow.

### 3. Duration bound — RUN

```
$ rite start planner --sessions 3
refusing to start: --minutes is required and has no default.
  ... neither suffices alone ...
$ rite start planner --sessions 3 --minutes 90     # supervise receives:
max_sessions=3, window_seconds=5400.0
```

Mutation-checked: dropping the unit conversion, dropping the argument, and
defaulting the flag each turn tests red.

### 4. Alias collision — RUN

```
$ rite projects add planner ~/proj      # project declares Manager 'planner'
refusing to register 'planner': ... declares a Manager of that name.
  `rite start planner` starts that Manager ... so this alias would never
  resolve, silently.
$ rite doctor
manager names: 'planner' is both a Manager here and your alias for /x ...
```

### 5. Per-Manager identity — RUN

```
$ rite start planner --sessions 1 --minutes 1 && ls .rite/managers/
planner
```

⚠ **Weakest of the built five.** The identity is the name passed on the
command line; a running process determines it from its argument, not from
anything it can read. That satisfies D-77 and it is what §5.4's boundary
rests on, but a Manager that loses its argument has no way to recover it.

### 6. Prompting — CONTRACT, not yet run

```
$ rite start planner --sessions 1 --minutes 30
starting Manager 'planner' ...
# the pane receives the prompt, and delivery is CONFIRMED not assumed:
$ tmux capture-pane -p -t =rite-mgr-<slug>-planner | head -1
<the prompt text>
```

And on a resumed session the prompt is NOT re-sent (D-90) — assert the
second cycle's pane does not receive it a second time.

### 7. Managers in `rite status` — rite-dd's, test theirs

### 8. Ctrl+C — CONTRACT, not yet run

```
$ rite start planner --sessions 5 --minutes 60
starting Manager 'planner' — up to 5 session(s), for up to 60 minute(s) ...
^C
stopped Manager 'planner' and its session
$ tmux has-session -t =rite-mgr-<slug>-planner ; echo $?
1
$ ls .rite/user/managers/planner.json 2>&1
No such file or directory
```

And the bound case must differ:

```
# ceiling reached, NOT Ctrl+C
ceiling reached: 1 session(s) started. ...
$ tmux has-session -t =rite-mgr-<slug>-planner ; echo $?
0                      # the pane SURVIVES — the user keeps their conversation
```

### 9. Issue recording — CONTRACT, not yet run

```
$ rite start planner --sessions 1 --minutes 30 --<flagname>
starting Manager 'planner' ...
  diagnostic recording ON: this Manager will spend tokens observing its own
  process as well as doing the work. Beta; entry format may change.

$ ls .rite/managers/planner/journal/
2026-09-20T03-14-07-observation.md

$ cat .rite/managers/planner/journal/*.md
anchor: 4f2a9c1 tests/test_gate.py:88
observed: `rite gate run` exited 0 on a file it could not open
expected: a gate that cannot read a file reports 3, not 0
inferred: the unreadable-file path returns before the exit-code branch

# and with the flag ABSENT, the CLAUDE.md must not mention the facility
$ rite start planner --sessions 1 --minutes 30
$ grep -c journal .rite/managers/planner/CLAUDE.md
0
```

⚠ The last check is the one rite-dd is right to press: *genuinely off* means
the Manager is never prompted, not merely that no file appears.

---

## Open questions — these block, or change, implementation

**Q1. (ANSWERED — D-90, §9.14.11a.) Prompting on start.**
Nothing in SPEC specifies it. The precedent is `rite sandbox start <worker>
--prompt TEXT` (§9.6) and the `/rite-start` template v0.5.0 shipped. The
obvious shape is `rite start <manager> --prompt TEXT`, sent with `send-keys`
after `settled_alive`. **I have not specified it because two things are
genuinely undecided:** whether the default prompt is the `/rite-start`
template or empty, and whether a prompt is re-sent on each resumed session
or only the first. The second matters — re-sending on resume would re-issue
an instruction into a session that already has context.

**Q2. (ROUTED — a CLI naming conflict, not an owner decision.) `rite stop <manager>` collides with `rite stop [DIRECTORY]`.** That command already means *shut down with handover —
release claims, update the board*, and already resolves an alias. §9.14.13
records this UNRESOLVED rather than assuming the `start` answer transfers,
because the existing command has side effects on the board. Same-shape fix
(intercept Manager names first) is available; whether it is right here is
yours.

**Q3. (CLOSED — see above.) Is clause 3 a refusal or a convention?** "An entry
without an anchor is not written" — is that enforced on the writing path,
or documented for the Manager to follow? §9.15 states it as a requirement
on the format and does not say which. It changes the implementation.

**Q4. INF-2 and the mandatory scenario citation.** You referred to both; a
draft of §9.15 cited them by id; **neither exists in this repository.** They
live in `~/AI/rite-dogfood/phase2/inferred-absence.yaml`, which is not a
clone of this repo. §9.15 now states the principle in its own words and
carries no id. If those rules should be normative for rite, they need to
arrive in SPEC with their own numbers.

**Q7. (ANSWERED — YES, D-88, and the reason changed the build.)** It is the only item not required for
"full-featured single Manager" to be true, so it is the natural thing to
move if the release sheds weight — rite-dd raised this and was right to.
The question is with Robert and unanswered. **Until it is answered it is
0.5.1 scope and §9.15 is written as such**, because a section written only
once its release is confirmed is a section written under time pressure, and
the scope statement is worth having whichever release carries it. If it
moves to 0.6.0 nothing is wasted: §9.15 goes with it unchanged, and the
estimate below drops by 3–4 sittings.

**Q8. (CLOSED — D-91. rite builds nothing for retrieval.) How does a reader reach entries that are never committed?**
The journal is gitignored and per-Manager — machine-local, and inside a
directory a torn-down sandbox takes with it. §9.15.4 says these entries are
the QA gate's raw material. Nothing contradicts in 0.5.1 because nothing
reads them, which is why it is recorded now as a design question rather
than in 0.6.0 as a migration. Raised by rite-dd.

**Q3 — ANSWERED (rite-dd): the refusal is on the writing path.** Written
into §9.15.3 and recorded as D-87. Left here so the answer is visible
beside the question it closed.

**Q5. (ANSWERED — `--record-issues`, D-89.)** §9.15 says it must read as diagnostic
rather than as a feature everyone should enable. I have not named it, since
naming is yours and the name is what users see.

**Q6. Is tmux's prefix-matching behaviour a change or long-standing?** Not
established. Measured on 3.7c; CI is 3.4. It does not affect correctness of
either fix — both assert the property directly — but it decides whether this
can regress differently across machines.

---

## Estimate

**Basis:** the five built items took roughly one sitting each including
review and CI, with the tmux work costing six CI cycles on its own. I am
estimating the two unbuilt items against that measured rate, not against
their apparent size.

| Work | Sittings | Why |
|---|---|---|
| Item 6 — prompting | 1 | Specified now; `send-keys` exists, but delivery must be CONFIRMED, which is where the work is |
| Item 8 — Ctrl+C / stop | **2** | The two paths share a teardown, and separating them means a signal handler plus `forget_instance` wiring. The idempotence requirement is where the second sitting goes |
| Item 9 — journal | **3–4** | Flag, conditional `CLAUDE.md`, entry format with anchor enforcement, two entry kinds, and the "genuinely off" test. The conditional-template half is the underestimated part |
| Q2 resolution + `rite stop` | 1 | Routed, not yet answered |
| **Total remaining** | **7–8** | ± the answer to Q2 |

**Confidence: low on item 9 and I would not defend the number.** Every
estimate this release has been wrong in the same direction, and the thing
that made them wrong was never the feature — it was the wiring around it.
Item 9 has more wiring than feature: a flag that must reach template
generation, and templates are the one surface I have not touched this
release.

---

## What I would cut, in order

1. **Item 6 (prompting).** Cheapest to cut because `/rite-start` already
   gives a human one thing to type, so the gap is convenience rather than
   capability. Cutting it costs the least and removes an open question.
2. **`rite stop <manager>` (the recovery half of item 8).** Keep Ctrl+C
   stopping both — that is the ordinary case and the one that leaves live
   quota burning if it is wrong. The orphan case has a workaround today
   (`tmux kill-session`), and cutting it defers Q2 rather than answering it
   badly.
3. **The retrospective half of item 9.** Observations alone still give an
   unattended run a voice, which is the whole reason the feature exists.
   Retrospectives are the half needing boundary detection and the inverted-
   metric standard — more machinery, and the half that is raw material for a
   gate that does not exist until 0.6.0.

**What I would not cut:** the anchor requirement, the observed/inferred
split, and "genuinely off when off". Cutting any of those ships the feature
in the state that makes it worse than not having it.

---

## Contradictions checked

Read against each other and against what was already there:

- ⚠ **§9.14.7a contradicted the shipped code** — it said a bare positional
  "is not available" for `rite start`; what shipped uses one. Corrected, and
  the correction records what actually resolved it.
- ⚠ **A draft of §9.15 cited two ids that do not exist in this repo** —
  inside the subsection requiring every claim to carry a verifiable anchor.
  Corrected; the failure is left recorded on the page.
- §9.15's journal path (`.rite/managers/<name>/`) agrees with D-77 and §5.4,
  and is gitignored by the existing `.rite/*` rule with no new machinery.
- §9.14.12's "pane survives a bound" agrees with the measured behaviour and
  with §9.14.3's attachability requirement.
- §9.12 (nothing unattended) is untouched: everything here runs in the
  human's foreground process.
- **Not checked:** §9.15 against §6 (ticket backends). The process/work
  distinction says work goes to the board, and I have not read §6 to confirm
  that boundary is drawn the same way there.

---

## What changed when the questions were answered (2026-09-20)

Recorded rather than edited away, because one answer produced a defect and
that is the more useful half of this document.

**Q7 — the journal stays in 0.5.1, and the REASON changed the build.** It
was kept not as a nice-to-have but because the next large unattended run is
a Bentora dogfood **run by somebody who is not the owner, with the owner not
watching.** Two things follow that were not in the spec an hour ago:

1. **It must be discoverable by somebody who has not read the spec.** An
   opt-in diagnostic nobody enables produces nothing, and the person at that
   keyboard has no reason to know the flag exists. Now required in `rite
   start --help` plus a docs line saying an unattended run is when to enable
   it. Fourth instance this week of *a capability nobody is told about*.
2. **The entries' retrieval — escalated, then WITHDRAWN (D-91).** §9.15.3a.
   The journal is gitignored, under `.rite/managers/`, inside a directory a
   torn-down sandbox takes with it. rite-dd raised this as a 0.6.0 design
   question about a future gate; the answer to *who runs the dogfood* turned
   the same gap into a live 0.5.1 defect, because the reader who cannot
   reach them is now a person. **A diagnostic whose output never leaves the
   host returns nothing to the one reader it exists for.** Closed with
   documentation rather than machinery — including that `git add -f` is
   REQUIRED, since a plain `git add` silently refuses.

**Q1 — prompting was DECIDED and had never reached the spec**, which is why
an implementation plan read it as unspecified. Now §9.14.11a. One
sub-question the decision did not reach is recorded as a **stated default,
not a decision**: the prompt is not re-sent to a resumed session.

**Q5 — `--record-issues`**, named for what it does rather than what it is.

**Q2 — routed, not escalated.** A naming conflict inside the CLI surface is
not an owner decision.

**Q6 — closed as not worth settling.** Whether tmux's target-matching
behaviour changed between 3.4 and 3.7c is unestablished; both fixes assert
the property directly on both versions, so nothing shipped depends on it,
and the only thing a CI round would buy is a sentence better left unwritten.

## Standing caveat on this document

Its author wrote nearly all of the scope it assesses, which is the wrong
arrangement for judging completeness. It goes to rite-dd before it goes to
the owner. Three of the four findings in rite-dd's review of §9.15 were
things re-reading my own text would not have produced, and one of them —
that a journal entry cannot be re-included into git — was a factual claim I
had made confidently and wrongly.

---

## Built state (2026-09-20, later)

**All nine built and CI-green on Linux.** Every acceptance test above that could be run has been run:
**100 passed** across the seven files covering items 1–6 and 8.

| item | landed |
|---|---|
| 1 resume passes the session id | verified by running |
| 2 quit / finished / crashed | `a4d745a`, prefix fix `a80cb36` |
| 3 both bounds on the CLI | `17d83ae` |
| 4 alias collision | `5a9acac` |
| 5 per-Manager identity | pre-`1381bc1` |
| 6 prompting + delivery confirmed | `a555091` |
| 7 Managers in `rite status` | rite-dd, `f803a10` |
| 8 Ctrl+C stops both; `rite manager stop` | `52b64c9` |
| 9 issue recording | rite-dd, `93f87f9` |

**Q2 resolved and built.** `rite manager stop <name>` under a new `manager`
noun; `rite stop [DIRECTORY]` untouched and still resolving aliases.
Confirmed by running both `--help`s, not by reading the diff.

### Two vacuous tests of my own, both found by mutation and not by reading

Recorded because they are the honest cost of this stretch, and because the
same technique found both.

1. **`prompt.py`'s delivery confirmation.** Replacing the entire confirm
   loop with `return Delivery(True, "sent")` left ten tests green. The
   property §9.14.11a calls load-bearing was asserted by nothing. Closed
   with a dead-pane fixture — the session exists so the checks pass and
   nothing reads, which is "accepted but never observed" without a race.
2. **The ceiling test in `test_manager_stop.py` never reached the
   ceiling.** With `max_sessions=1` and a `quit` ending, `supervise` returns
   on the ENDING first, so a mutation making a bound kill the session left
   all nine green. It passed against correct code for a reason unrelated to
   its property. Now asserts `"ceiling reached" in result.reason` before
   asserting anything about the session.

Both are instances of the rule this release arrived at: **a fix is reviewed
by someone who did not write it, or by its author running it against input
it should reject — never by its author re-reading it.**

### Item 9 reviewed by running it (2026-09-20)

I wrote §9.15 and had reviewed only rite-dd's spec work, which made me the
wrong reviewer for the words and the right one for whether the code matches
them. Exercised end to end in a scratch project rather than read:

| §9.15 requirement | result |
|---|---|
| anchor refused on the writing path, no file created | refused, `files after refusal: 0` |
| `observed` / `inferred` separate fields | separate sections in the entry |
| retrospective records cost / changed / caught-elsewhere, no verdict | three flags present, **no verdict flag** |
| `instructions()` empty when disabled, same shape when not | `''`, and `on.startswith(off)` |
| instructions name the COMMAND (item 3) | `rite journal observe` present |
| nothing in rite reads the journal | no reader found |
| start notice: copy primary, `git add -f` one way | matches D-91 |
| `--record-issues` off by default, beta in help | both |

**Beyond the spec:** a Manager name that is a path is refused, *including*
the single-segment cases `.` and `..` that contain no separator. §9.15
never required that and the entry path is built from a user-supplied name,
so it is the right instinct.

**One divergence, and the SPEC was wrong rather than the code.** §9.15.1
said the help text carries the recommendation and the beta caveat "in that
order"; the implementation leads with `BETA.` as a tag, which reads better.
A spec that fixes the order of two clauses is legislating prose style.
Relaxed to the property — neither hedges the other — with the order left to
the implementer.
