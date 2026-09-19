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

