# Handover: what is open after the DF1/DF3 night (2026-09-27)

Written by the session that did the work, for one that has the repo and none
of the context. The table rows in `V070_RELEASE_PLAN.md` (DF1–DF7) are the
record. This file is the reading order, and the reasoning behind each
"not fixed".

**What landed that night**, all on `main`:

- **#13:** a run that ends on `--sessions` is continued by the next bare
  `rite start`. Before, it silently started fresh with the conversation
  intact on disk. #13 also records the board a conversation began under from
  the same config read that wrote its opening prompt.
- **#14 and #15:** Manager mail moved out of `~/.rite` to rite's data
  directory, and `~/.rite` is no longer granted to any Manager (DF3). On
  macOS a Manager also can no longer read a sibling's `.rite/managers/<name>/`.
- **#16:** DEFECT_CLASSES class 17, with `tests/test_every_manager_grant_has_a_reason.py`
  as its guard. The fixture-placement rule was added to class 1. DF5's cause
  was found.
- **#17:** DF7 filed.

Verified end to end on macOS against real Claude and
`robbartoszewski/rite-dogfood-board`. **Not re-run on Linux**; the Landlock
halves are covered by CI only.

---

## 1. DF4: a board read right after creating an issue sees nothing

**Finding.** After `gh issue create` plus the `scheduled` label, every
GitHub LIST read missed the new issue for about 2–3 s. That covers
`gh issue list --label`, and the REST `/issues` list with the label filter and
without it. Measured on issues #8–#15, and seen again on every probe that night
(~2 s, ~3 s, ~2 s). A single-issue `GET /repos/{r}/issues/{n}` saw it at once.
`loop._ready` lists, gets `[]`, and the verdict is `idle`: a `rite start`
right after routing or filing stops with "done: the board has nothing
ready". The outcome depends on the timing of two independent things.

**Why not fixed.** It is a design change (below), not a line, and it was
found the night before a dogfood.

**What settles it.** rite keeps a per-project ledger of issue ids it created
or labelled `scheduled` (`rite board`, the broker), written outside the
boundary. `_ready` unions the list result with the ledger's recent ids, and
confirms each by single-issue GET (open and still labelled). An id leaves
the ledger once a list returns it, or once a GET shows it closed or
unlabelled. Issues a person creates in the web UI stay subject to the lag,
so the idle message should say "nothing ready as of this read", not "the
board is empty". **Done when** a `rite start` immediately after `rite board`
creates and schedules an issue sees it on its FIRST read, with no sleep and
no retry. Measure Jira before assuming either way.

## 2. DF6: the same `refused: …` lines printed on two consecutive runs

**Finding.** `supervise._say_refusals` printed two identical refused compound
commands (`cat .rite/user/permissions.json …` and `grep -i GH_CONFIG_DIR …`)
at the end of a fresh session and again at the end of the next run, which
resumed that same session.

**Why not fixed.** The cause is one of two, and the wrong fix for either
hides the other:

- `_say_refusals` re-reports refusals from the RESUMED transcript, which
  still contains the earlier cycle's entries. That would be its
  `cycle.started_at` filter not holding for a continued transcript.
- Or the resumed Manager really did re-run the same commands.

**What settles it.** Both runs used ONE transcript:
`~/Library/Application Support/rite/managers/rite-mgr-df1probe-5f53ba-lead/claude/projects/<…scratchpad-df1>/48fc33a3-75c4-4e85-b013-f1dbb4d31f15.jsonl`.

- Each command appears ONCE, stamped ~02:50–02:51: it is re-reporting.
- Each appears twice, the second ~02:57–02:58: it was re-run.

The run logs that printed them were in a session scratchpad and are gone;
the transcript is not.

Separately, the refusal line claims the engine ignored `permissions.json`
for a command "which rite's own allowlist DOES cover". Check that claim
against the compound command before trusting it.

## 3. DF7: a test that measures the temp grant when rite lives under `/tmp`

**Finding.** `test_it_cannot_rewrite_the_rite_it_runs`
(`tests/test_the_manager_profile_denies_what_it_should.py`) skips only when
the package is inside the project. With rite's `src/` under `/tmp` or
`/private/tmp`, which the Manager profile grants writable, the `touch`
succeeds and the test fails. It then leaves `rite-probe-should-not-exist`
in the package, and the checkout guard fails the run too.

**Why not fixed.** It was found at the end of the night, and it does not
affect a real install (rite lives under `~/.local/share/uv` or a checkout in
`$HOME`).

**What settles it.** Skip, saying why, when the package resolves under a temp
root the profile grants. Remove the probe in a `finally`. Keep the failure
when the package is writable anywhere else.

## 4. The DF1 blind spot: needs a DECISION, not a fix

**Finding.** DF1 is fixed: a bare `rite start` refuses to continue a
conversation that began under a different board, and names `--fresh` and
`--keep-conversation`. One case is invisible to it. A designation written by
a build before the fix has no board recorded. If such a conversation began
under a board that has since been REMOVED, it continues silently. With no
board now, rite cannot tell "never had one" from "had one that was removed".

**Why not fixed.** Every fix changes behaviour for users who did nothing
wrong:

- Refusing every unrecorded designation when no board is configured asks
  every pre-fix no-board project a question once.
- Treating unrecorded as "fresh" silently discards conversations.

Robert's call.

**What settles it.** A decision between those two, or accepting the gap.
The gap closes by itself for any conversation started or continued once
under the new build, because that records the board.

## 5. Other projects' mailboxes still under `~/.rite`

**Finding.** Development builds of 0.6.0 kept every Manager's mail under
`~/.rite/managers/`, which every Manager could read. A Manager's own box is
moved out at its next `rite start` (`mailbox._adopt_from_rite_home`), under
its run lock. Boxes of OTHER projects are not moved; every `rite start` says
which are still there (`mailbox.still_under_rite_home`).

**Why not fixed.** Moving another project's box would race that project's
supervisor, which may be reading it, and its run lock belongs to it. And
since #15 no Manager can read `~/.rite` at all, so what is left there is no
longer exposed to Managers. It is still on disk in the old place.

**What settles it.** Nothing needs to. Each box moves the first time its
Managers start. A project that never starts again keeps its old mail under
`~/.rite/managers/<checkout>/`, where the `project` file names the checkout.
Remove it by hand if it is not wanted.

## 6. A start-time warning that became false when #15 landed

**Finding.** #14 added two messages in `managers/mailbox.py`:

- `still_under_rite_home`, which says mail left under `~/.rite` is somewhere
  "EVERY Manager's sandbox on this machine can read it".
- `adoption_notes`, which calls `~/.rite` a place "which every Manager's
  sandbox can read", and a file it could not move "still READABLE by every
  Manager".

Since #15, `~/.rite` is not granted to any Manager and is denied by name on
macOS, so those sentences are now false. **A `rite start` on a machine that
still has other projects' mail there prints the alarming version.** **It
will print on Robert's Mac.** At ~03:40 no other checkout under
`~/.rite/managers/` held a message. By ~05:00 two did: `1a6c9cd31498880c`
(`/private/tmp/dfcH6M2`, 1 message, 04:17) and `87e3477dab3486f6`
(`/private/tmp/dfcjyM3`, 3 messages, 04:35). Both are scratch projects from
the DF2 sessions, apparently run from builds older than #14. They are safe to
remove, and removing them silences the false warning until the wording is
fixed.

**Why not fixed.** It was found while writing this note, after the session
was asked to stop.

**What settles it.** Reword both messages to say the mail is in the OLD
location, moved at that project's next start, and no longer readable by any
Manager's sandbox since `~/.rite` stopped being granted. Update
`tests/test_no_manager_reads_another_managers_mail.py`, which asserts the
old wording ("EVERY Manager", "still READABLE by every Manager"). This is the
prose-drift class (DEFECT_CLASSES.md, class 6), and it was introduced by the
same session that fixed the underlying exposure.

## 7. 🔴 `test_blast_radius_concurrent`: a suspected race, NOT a flake

**Finding.** `tests/test_blast_radius_concurrent.py::TestExclusionHoldsUnderSustainedConcurrency::test_no_granted_claim_is_ever_lost_and_no_path_is_held_twice`
fails intermittently in CI:

- `FileNotFoundError` on a `.rite/outbox/<…>_blocker.json` under the test's
  `tmp_path`, raised from `reporting/outbox.list_pending`.
- Three recorded occurrences:
  - `a8fd5ea`, 2026-09-26 (job not recorded).
  - `a0cb546`, 2026-09-26, run 36243078492, **Python 3.12**.
  - `916dc44`, 2026-09-27 ~04:45 UTC, **Python 3.11** (3.12 and 3.13 green).
- **So it is not 3.11-only.** It is intermittent across interpreter versions.
- A rerun has passed each time, which is how it came to be carried as an
  annoyance.

Two layers, both races on the timing of independent processes, which is
what Robert's rule forbids:

- **Immediate.** `list_pending` globs the outbox and then reads each file,
  and catches only `JSONDecodeError`/`KeyError`. A file removed between the
  glob and the read (another tick flushing it) raises. This is a
  check-then-act on the filesystem.
- **Underneath.** Two ticks should not be reconciling at once, and
  `scheduler/lock.py` is what should stop them. It is unchanged since v0.1.0.
  It was stress-measured on 2026-09-26: 8 processes for 20 s gave
  1352–1617 overlapping holders per run in a Linux container, and about 500
  false "previous lock unreadable" reclaims. macOS gave 0 overlaps but 85
  false reclaims. The likely mechanism: a lock that vanishes between
  `exists()` and the read is judged unreadable, and `_reclaim` unlinks
  whatever is at the path, possibly another tick's fresh lock.

**Why not fixed.** Found in passing at the end of the night, and the lock is
coordination: a wrong fix there does silent damage.

**Do NOT treat a red run of this test as a flake, and do not rerun until it
passes,** until the cause is named. A defect was already mislabelled as a
flake once this weekend (DF5, the other way round), and the correction came
only from counting occurrences.

**What would produce evidence**, before any fix:

1. **Per-test, not per-run.** Run the file in a loop with
   `-rfE -o junit_logging=all --junitxml=<n>.xml` and keep every XML. Count
   the failures, and keep each traceback and captured output.
2. **Load and ordering.** Run the loop alone, then alongside a CPU-bound
   load (e.g. two `yes > /dev/null`), then with `-p no:randomly` versus
   shuffled ordering if a plugin is available. Does the rate change?
   Measure on Linux (a Docker container matched to CI: a bundle clone,
   gitleaks installed, the venv on PATH, a non-root user) as well as macOS,
   since the lock's overlap was measured on Linux only.
3. **Separate the layers.** In a scratch branch, make `list_pending`
   tolerate a vanished file and rerun the loop. If the test still loses a
   claim or holds a path twice, the lock is failing on its own. If it goes
   quiet, only the symptom was fixed, and the lock is still unproven.

**Done when** the cause is named with evidence from those runs, the lock
excludes under the same stress that measured it failing (0 overlaps, 0
false reclaims), and the loop runs clean at a count that would have caught
the old rate.

## For Robert, this morning

- **A false warning will print** at `rite start` (item 6): "mail is still
  under ~/.rite, where EVERY Manager's sandbox … can read it". It is not
  true any more. Two scratch projects from the DF2 sessions left mail there.
  To silence it until the wording is fixed:

      rm -r ~/.rite/managers/1a6c9cd31498880c ~/.rite/managers/87e3477dab3486f6

  (Their `project` files name `/private/tmp/dfcH6M2` and
  `/private/tmp/dfcjyM3`. Check nothing else has appeared there first:
  `ls ~/.rite/managers/`.)
- **A Manager that ran without a board needs a fresh conversation once a
  board is configured:** `rite start <name> --fresh --sessions N --minutes M`.
  A bare `rite start` refuses and prints that exact command (DF1). Continuing
  the old one would leave the Manager believing there is no board. Use
  `--keep-conversation` only if the board really has not changed.

---

**Rules that held all night and are worth keeping.**

- A green counts only when its run's SHA equals the head being merged AND
  `main` is contained. `main` moved during four of the five PRs (#13, #15,
  #16, #17).
- Never force-push: merge `main` into the branch instead.
- Probe issues are only for `robbartoszewski/rite-dogfood-board`. Close each
  one after use.
- Board-list reads need ~3 s after creating an issue (DF4) until DF4 lands.
- ⚠ **Decide merge guards with `jq -e` over `gh run list --json`, not `grep`.**
  In these sessions `grep` is a shell function, and a `grep -qv success`
  guard failed open: #17 was merged on a red run. `main` was green after
  the merge (`72b8673`), and #17 is one docs row, but the guard failed all
  the same. Test a guard against a failing input before a real merge.

**Evidence for the scheduler-lock defect, filed elsewhere.** That red was
`tests/test_blast_radius_concurrent.py::TestExclusionHoldsUnderSustainedConcurrency::test_no_granted_claim_is_ever_lost_and_no_path_is_held_twice`
on Python 3.11 only (3.12 and 3.13 green), on #17's branch at `916dc44`,
2026-09-27 ~04:45 UTC: `FileNotFoundError` on a `.rite/outbox/…_blocker.json`
under the test's `tmp_path`. It is the known open defect (the scheduler lock
does not exclude), not a flake. It is written up as item 7.
