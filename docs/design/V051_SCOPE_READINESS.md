# v0.5.1 scope — is it ready to implement? (2026-09-20)

**Short answer: seven of nine are ready, two are not, and one of those two
has never been specified at all.** The gaps are named below rather than
smoothed, and the estimate is for what remains.

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
| 6 | **Prompting the session on start** | ⚠ **NOT SPECIFIED** | — | — |
| 7 | Managers in `rite status` | **BUILT** (rite-dd) | — | `f803a10` |
| 8 | Ctrl+C stops both; `rite stop` recovers | **SPECIFIED, not built** | §9.14.12–13, D-85 | — |
| 9 | Issue recording | **SPECIFIED, not built** — and the release's one scope lever, see Q7 | §9.15, D-83/84/86 | — |

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

### 6. Prompting — ⚠ NO TEST, BECAUSE NO SPEC

See open question Q1.

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

**Q1. What does "prompting the session on start" mean, and is it in 0.5.1?**
Nothing in SPEC specifies it. The precedent is `rite sandbox start <worker>
--prompt TEXT` (§9.6) and the `/rite-start` template v0.5.0 shipped. The
obvious shape is `rite start <manager> --prompt TEXT`, sent with `send-keys`
after `settled_alive`. **I have not specified it because two things are
genuinely undecided:** whether the default prompt is the `/rite-start`
template or empty, and whether a prompt is re-sent on each resumed session
or only the first. The second matters — re-sending on resume would re-issue
an instruction into a session that already has context.

**Q2. `rite stop <manager>` collides with the existing `rite stop
[DIRECTORY]`.** That command already means *shut down with handover —
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

**Q7. Does item 9 stay in v0.5.1?** It is the only item not required for
"full-featured single Manager" to be true, so it is the natural thing to
move if the release sheds weight — rite-dd raised this and was right to.
The question is with Robert and unanswered. **Until it is answered it is
0.5.1 scope and §9.15 is written as such**, because a section written only
once its release is confirmed is a section written under time pressure, and
the scope statement is worth having whichever release carries it. If it
moves to 0.6.0 nothing is wasted: §9.15 goes with it unchanged, and the
estimate below drops by 3–4 sittings.

**Q8. How does 0.6.0's gate reach entries that are never committed?**
The journal is gitignored and per-Manager — machine-local, and inside a
directory a torn-down sandbox takes with it. §9.15.4 says these entries are
the QA gate's raw material. Nothing contradicts in 0.5.1 because nothing
reads them, which is why it is recorded now as a design question rather
than in 0.6.0 as a migration. Raised by rite-dd.

**Q3 — ANSWERED (rite-dd): the refusal is on the writing path.** Written
into §9.15.3 and recorded as D-87. Left here so the answer is visible
beside the question it closed.

**Q5. What is the flag called?** §9.15 says it must read as diagnostic
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
| Q1 answered, then item 6 | 1 | Small once decided; `send-keys` already exists in this module |
| Item 8 — Ctrl+C / stop | **2** | The two paths share a teardown, and separating them means a signal handler plus `forget_instance` wiring. The idempotence requirement is where the second sitting goes |
| Item 9 — journal | **3–4** | Flag, conditional `CLAUDE.md`, entry format with anchor enforcement, two entry kinds, and the "genuinely off" test. The conditional-template half is the underestimated part |
| Q2 resolution + `rite stop` | 1 | Only if Q2 says implement it now |
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
