# Review checklist

Project-wide checklist for the review convention (see `CLAUDE.md`). A repo can
add its own `.rite/review-checklist.md`, which **appends** to this one rather
than replacing it.

The point is incremental quality: a known issue gets a line here and is
eliminated permanently. A line that keeps firing should become a lint rule or
a gate instead; a line that never fires should be removed.

**Three verdicts, not two.** Measured by handing this checklist to three
independent reviewers against one real change: five of these lines could not
fire on that diff at all (no dependency added, no serialiser touched, no
external tool named), and every reviewer recorded them as PASS. A PASS that
means "does not apply here" is indistinguishable in a tally from a PASS that
means "I checked and it holds", and a column of them manufactures the
appearance of coverage. So:

- **PASS** — you applied the line and it holds. If you did not check, it is
  not a PASS.
- **N/A** — the line cannot fire on this diff. Say in four words why not.
- **CANNOT-EVALUATE** — the line *should* fire here and you could not apply
  it. This is a finding about the LINE, not about the change: report it, and
  say which part of the wording defeated you. A line nobody can apply is
  worse than a missing one.

**Verification is first because it is what fires.** The other four sections
are one-liners you can check in a minute and most of them cannot fire on most
diffs; these are the ones that earned their place by shipping. Read them
against the diff before the habit of skimming sets in.

## Verification

Each of these cost a real, shipped defect somewhere before it earned a line
here — see rite's own publish-gate module for a fresh example of the first
one.

- [ ] A failure path exits non-zero and says why. Nothing fails silently to
      stderr while stdout — or the exit code — reports success; a broken
      check that reports "clean" is worse than no check at all.
- [ ] The check observes the property it claims, not a proxy for it — that
      the thing works, not that its bytes are unchanged; that the tool runs,
      not that it is on PATH. This is not only about tests: a gate, a health
      check and a test assertion all fail the same way. Delete the code under
      test, or break the property itself, and confirm the check goes red; one
      that stays green either way is not checking anything.
- [ ] A condition this code can already detect is reported where it is
      CAUSED, not only where someone thinks to ask. The question a check has
      to answer is not "does anything detect this?" but "what does the person
      doing the thing see at the moment they do it?" — a check nobody runs is
      a check that is not there. Three of these landed in one audit here: an
      installer announcing a gate that could not run on that machine, a
      health check that knew whether the hook was armed and hedged across
      both cases anyway, and a command that wrote config and left every
      generated file asserting the opposite. All three were detectable the
      whole time, by a command nobody had a reason to run just then. Where
      the condition is a function of state the tool owns, put it on the
      shared path once rather than adding a reminder to each command — the
      per-command version is the one the next command forgets.
- [ ] A test file the runner does not collect is indistinguishable from a
      test file that passed. Check the name matches what the runner actually
      collects, not what it looks like it should — a file of seven tests
      shipped here as `tests/nt.py` after a stash-and-copy, never ran, and a
      CLI message naming a command that does not exist reached users because
      the test that would have caught it was never executed. Same family as a
      gate that reports itself installed while inert: the report of a check
      that did not run reads exactly like the report of a check that passed.
      Mechanise it where the runner allows — a rule this specific should not
      depend on a reviewer remembering it.
- [ ] A test states what it depends on rather than asking the machine for
      it. A fixture that inherits a platform default, an installed tool, a
      readable keychain or a wall-clock throughput is asking a question
      instead of stating an answer — **and on the author's machine the
      machine gives the convenient reply.** Three of these shipped here in
      one day: a doctor fixture that left sandboxing at its default and so
      required yoloAI to be installed; a credential fixture that inherited
      the same default and got an empty list on a runner where it is off;
      and a concurrency harness that ran for a fixed 1.5s and then demanded
      200 iterations, which a laptop clears and a shared runner does not.
      Each was green for the author and red elsewhere, which is the worst
      direction: it reads as "works here, broken there" rather than as a
      test that was never about the thing it claimed.
- [ ] Exercised from the state a new user starts in — empty database, fresh
      clone, no config — not only from the state already on the developer's
      machine. A migration chain that has only ever been run forward from
      today's schema has been run from step 0 exactly zero times, and the
      steps fail in order the first time someone starts clean.
- [ ] A missing or undefined input fails loudly; nothing falls back to a
      plausible default where the failure would render as **content rather
      than an error**. An absent plural form that quietly renders the
      singular, an undefined token that resolves to a valid-looking value —
      output like that passes every check made by eye, which is why this
      defect ships in bulk rather than one at a time.
- [ ] Every seam this change touches — a call from one module into another,
      a schema both sides read, a contract a generated file depends on — has
      a ticket or a test that owns it, not left implicit because "it's
      obviously fine."
- [ ] Generated or templated content (a report, a summary, a register entry,
      a changelog line) states only what was actually measured. No line
      asserts a fact nobody checked.
- [ ] Something outside its own test suite actually calls this — and "this"
      is every artifact the change adds, not only a module: a template, a
      workflow file, a config key, a generated file. A test suite passing
      against a module with zero callers proves the module works and nothing
      else — this project built exactly that twice (`rite prepare`, `rite
      status`) before either had a caller, and carried a CI workflow template
      that no Python, no test and no doc referenced. Grep for the caller;
      not finding one is the finding.
- [ ] **The verdict is an exit code, read directly.** Not a summary line,
      not a grep for the word "error", and never through a pipe — `cmd |
      tail` replaces the check's exit code with `tail`'s, which is 0
      whatever happened. Written after a push loop in this repository piped
      `pytest` to `tail` and would have pushed a red suite under a line
      reading "3404 passed"; it was itself the tooling written to stop that
      class of defect. Anything that runs a check on your behalf — a script,
      a hook, a CI step, a wrapper — has to be read for this, because the
      failure is invisible by construction: a check that never runs and a
      check that passes produce the same silence.
- [ ] **A test does not satisfy the property under test with the test process
      itself.** A liveness check handed `os.getpid()` passes because pytest is
      alive, not because the code is right — the assertion still holds with
      the logic deleted, and the false positive it should have caught (a
      recorded pid the OS recycled onto something unrelated) is the exact
      defect the check exists for. The test then asserts the bug as the
      feature, and reads as coverage. Substitute the fact instead: patch the
      liveness probe, use a fixed identifier, supply the answer the code is
      supposed to derive. The general form is that a test which hands the code
      a genuine instance of the thing it is meant to detect is measuring its
      own fixture. Written after this shipped twice in one night here, in
      tests by someone actively hunting this class.
- [ ] **Nothing edits the tree while a verification run is in flight.** A
      suite reports on the files it read, and if they changed underneath it
      the number it prints is about a tree that no longer exists — green on
      code nobody will ship, or red on an edit that was never the defect.
      Two sessions hit this independently within an hour on this repository,
      each losing a run. It stays silent wherever nothing snapshots the
      checkout, so the rule is the guard: start the run, then wait.
      **And if a run must be abandoned, abandon its VERDICT and clean up
      what it started.** A killed run is not a no-op — it is a run that
      stopped halfway through owning external state, and a `finally` does
      not execute when a process is stopped by SIGTERM, which is what every
      harness sends. Measured twice here: one killed run orphaned a tmux
      session that silently blocked the next suite for fifteen minutes, and
      another left a sandbox named `rite-selftest-4751-…`, where 4751 was
      the pid of the process that had just been killed. Other suites hold
      ports, containers, rows. The next run does not fail, it hangs, and a
      hang under load is indistinguishable from slowness.

- [ ] **When one assertion in a test always fires first, it IS the test and
      the rest are decoration until proven otherwise.** Break the thing
      deliberately and record WHICH assertion catches it, not merely that
      one did. Found here on a concurrency soak: reinstating a real lock
      defect failed it 6 times out of 6, and every one was the throughput
      floor — never the two assertions that named the actual property. The
      decisive checks were present, correct, and never the thing that fired,
      so the test read as thorough while one proxy did all the work. That is
      harder to see than a test that cannot fail, because this one can.
- [ ] **Where the property has structure, assert it; where it is about the
      meaning of prose, you get a tripwire and you say so.** A version is a
      token with a shape and can be checked. "Was this release announced, or
      merely mentioned in a caveat?" is a stance, and a substring test sees
      vocabulary rather than stance — measured: a check for whether a
      changelog named the commands it shipped PASSED on a section whose only
      mention was a warning about what was least confirmed, and tightening
      the pattern did not help because the warning contained every string it
      looked for. Ship the tripwire if it catches the failure you actually
      measured, and label it, because a tripwire mistaken for a proof is how
      a real check gets deleted later as redundant.
- [ ] **A test that patches a check to its FAILURE value and then asserts
      success does not fail to catch the defect — it specifies it.** Three
      files here patched a liveness probe to False and asserted that
      starting sessions succeeded: nothing alive, three started, exit 0, all
      true at once, and the code obliged because nothing looked. Anyone
      hardening it would have found one, read it as the contract, and
      stopped. One such test is a mistake; three, by three authors at three
      times, is a process that manufactures them — each ran the suite, saw
      green, and took the existing tests as the specification. When a test
      mocks the thing under test into a state that contradicts its own
      assertion, that contradiction is the finding.

## Security

- [ ] No secrets, tokens, or credentials in committed content (source, docs,
      comments, test fixtures, commit messages).
- [ ] User input is validated at the boundary, not trusted downstream.
- [ ] No obvious injection surface (SQL, shell, template) left unparameterised.
- [ ] Dependencies added this change have no known critical advisories.

## Correctness

- [ ] The change does what the ticket asked, not more and not less. Where
      there is no ticket, the commit message is the ask — hold it to the same
      standard, and a change that does more than its own message claims is
      the finding. (Three reviewers marked this line unevaluable on the same
      diff for the same reason: no ticket existed and the line named no
      fallback.)
- [ ] Edge cases the diff touches are covered by a test that fails without
      the fix.
- [ ] No behaviour silently reversed or removed without the ticket saying so.
- [ ] Anything this change found and did not do is a ticket, not a sentence
      in the PR or the handover. The test is not "was it in scope" — it is
      **would this ticket's definition of done still be met without it?**
      Yes, file it unlinked; no, file it and link this ticket as blocked by
      it (`rite board link <this> <new>`). Filed for something observed,
      never something imagined: a follow-up nobody can point at the evidence
      for is sprawl, not coverage.

## Style

- [ ] Follows this project's existing conventions, not the reviewer's
      preference — meaning the code next to it and the vocabulary already in
      use, not a rule you would have chosen. Where a declared convention and
      the practised baseline disagree, say so and stop: that disagreement is
      the finding, and resolving it is not the reviewer's call. (Three
      reviewers returned three different verdicts on this line — one read it
      as the enforced formatter, one as the surrounding code, one as the
      repo's shared vocabulary, and the third found a real divergence the
      other two did not look for.)
- [ ] No dead code, no commented-out blocks, no leftover debug output.

## Dependencies

- [ ] New dependencies are the right size for the job — no heavy framework
      for something a few lines would do.
- [ ] Licence is compatible with this project.

