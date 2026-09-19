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
- [ ] **Nothing edits the tree while a verification run is in flight.** A
      suite reports on the files it read, and if they changed underneath it
      the number it prints is about a tree that no longer exists — green on
      code nobody will ship, or red on an edit that was never the defect.
      Two sessions hit this independently within an hour on this repository,
      each losing a run. It stays silent wherever nothing snapshots the
      checkout, so the rule is the guard: start the run, then wait. If a
      run must be abandoned, abandon the VERDICT with it rather than reading
      the number.
## rite's own

Everything above is the checklist `rite init` ships to every project, and every
line of it is worked here too — `tests/test_rite_dogfoods_its_checklist.py`
fails if one is added there and not applied here, because a checklist this
project does not itself work is one nobody has used the way a user will.

What follows is rite's, earned the same way: each line is here because this
repository shipped the defect once. Two that used to be here have gone, which
is the intended lifecycle — see the note at the end.

- [ ] A claim this code makes in prose — `README.md`, `SPEC.md`, a docstring,
      a generated file, a CLI message — is checked against the code that would
      have to be true for it, in this diff. Prose and code drift in the
      direction that flatters the tool: README promised a CI workflow
      `rite init` did not write, a workflow header asserted a pre-push hook
      that had just refused to install, and a `--help` string described a
      measurement nobody in this repository had run.
- [ ] A quotation from `SPEC.md` is the text that is in `SPEC.md`. Grep for
      it. A fabricated citation is unfalsifiable until someone looks, and it
      spreads — `merge.py`'s invented "repo checklist appends to project
      checklist" reached two review templates and a test docstring, and
      retracting it in the docstring alone left it standing in the test.
      (The section NUMBERS are now a gate —
      `tests/test_spec_citations.py` fails on a citation to a section that
      does not exist. The quoted text still needs a human with grep.)
- [ ] A claim about an external tool (`git`, `gitleaks`, `gh`, `yoloai`) was
      measured against the installed binary, and a claim that came from
      somewhere else says so. `--backend sandbox-exec` is not a value yoloAI
      accepts; `sha256sum --ignore-missing` exits 0 on one macOS
      implementation and 1 on two others. Second-hand measurements are worth
      recording and are not worth restating as your own.
- [ ] Anything that resolves a path, a template or a version **at runtime** is
      exercised against a built wheel, not only the source tree. `rite init`
      crashed with `FileNotFoundError` under `pipx install` while 560 tests
      passed green from the checkout — and the defect was path *resolution*,
      not a missing file, so a presence check would have gone green on a wheel
      that was still broken.
- [ ] A function that serialises a config object writes **every** field, not
      the ones today's caller happens to set. Anything that round-trips config
      through it silently deletes the sections it dropped.
      (`tests/test_config_roundtrip_is_total.py` now walks
      `dataclasses.fields()` and round-trips the result, so `config_to_yaml`
      is covered by nobody having done anything — this line kept only for
      serialisers the gate does not reach.)

- [ ] A read-modify-write of a shared file takes the lock **and** writes
      atomically. Measured on `modules.yaml`: six concurrent registrations
      over five rounds, every round lost entries, the worst kept one of six.
      This line was retired once, on the grounds that
      `tests/test_shared_state_locking.py::test_no_durable_state_writer_is_left_using_write_text`
      had become a gate for it. It has not: that test enumerates eleven named
      files and checks only that they avoid `write_text` — a *new* module doing
      an unlocked read-modify-write passes it, and nothing in it checks locking
      at all. Restored, and the retirement is the cautionary tale: retiring a
      checklist line in favour of a gate means reading the gate.

- [ ] If this change alters a public interface — a command, a flag, an output
      line, a generated file, a platform claim — or invalidates something
      `README.md` states, the README is updated in the same change. Not every
      task: the README is a landing page and per-task edits are churn. The
      question a reviewer can answer that a release step cannot is the narrow
      one — *does this change make a sentence in the README false?* Two have
      shipped false already: that release tags are immutable (in the
      distribution section of a secrets-gate tool), and that `rite init`
      generated a CI workflow while it generated nothing. Both were caught
      long after the change that made them false.

      The third is the argument for this line. Rewriting the README to a
      landing page, a round-2 pass caught a sentence the SAME rewrite had
      just introduced — that cross-machine coordination "works the same way
      but has had far less use", when the roadmap says it is designed and
      not built. Written and caught within the hour, by asking this question
      and nothing else. A release step cannot ask it, because by then nobody
      remembers which sentence the change was supposed to touch.

- [ ] The commit message is written for whoever will actually read it, and
      after publication that is a stranger. **A commit message is read by
      someone who cannot see who asked for the change, who reviewed it, or
      what round it was** — so third-person self-reference ("the owner's
      framing"), and phrases that point at an absent conversation ("the
      material handed to me said 49", "read cold by a reviewer"), name people
      and events the reader has no access to. The test is not a list of
      banned phrases; it is whether a sentence still resolves for someone
      holding only the repository.

      Subject ≤ 50 characters, imperative. A body only where the *why* is not
      visible in the diff: "publishing v0.1.0 made every clone inherit a tag,
      so four tests that assert against HEAD failed" earns one; "cut the
      README and moved two install options" does not.

      **This is not a rule that shorter is better**, and the six published
      commits are the evidence. Four carry long bodies that were CORRECT when
      written — their audience was the next session picking work up
      mid-sequence, the tree diverged twice that week, and those messages
      were sometimes the only durable record of why something had been done.
      What changed at publication was the audience, not the quality of the
      writing. A session working overnight on an unpublished branch may still
      be right to write a long body; the failure is writing for an audience
      that has moved on without noticing it has.

      Same shape as the README line above, and generalises past commits:
      every artifact has a reader, and publication is the moment that reader
      changes.

**On retiring a line.** The template's opening says a line that keeps firing
should become a gate and a line that never fires should go. Three have moved:
`config_to_yaml`'s totality is now
`tests/test_config_roundtrip_is_total.py`, which walks the dataclasses rather
than enumerating what someone remembered — written the first time anyone
worked this checklist against rite's own code, which is the whole argument for
a project using the checklist it ships;
SPEC section numbers are now enforced by `tests/test_spec_citations.py`, so the
citation line above keeps only the half a machine cannot do; and "a fix was
checked for the defect it fixes" now lives in
`templates/agents/reviewer-terminating.md`, which is the stage that reviews
fixes — `reviewer-round1` is told fixes are out of its scope, so the one agent
reading this file could not have acted on it.

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

