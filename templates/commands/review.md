---
description: Run the review convention over the current change — round 1, then the terminating check.
---

Run the review convention over `$ARGUMENTS` (default: the current diff / the
work just finished).

## The checklist

```
rite review                    # the project-wide checklist
rite review --module <name>    # ...plus that repo's own, appended
```

`<name>` is a module from `.rite/modules.yaml` — the same names the module
map in `CLAUDE.md` uses. Pass the one the change touches; a name that is not
registered is refused rather than quietly giving you the project-wide list
on its own.

That output is the checklist, already merged. Its first line is a `# checklist:`
header naming which files were merged and how many items each contributed —
read it: `(absent)` against a repo you expected to contribute means you are
reviewing against fewer lines than you think. Everything after it is the
checklist. Do not open
`.rite/review-checklist.md` and stitch it together with the repo-level one by
hand — `rite review` does exactly that merge, project-wide first and the
repo's own as an addendum, and it is the only copy of it that stays right
when either file changes.

## Round 1

Spawn independent, fresh-eyes reviewers against that output using the
`reviewer-round1` agent. **One agent for ordinary work**; two or three for
anything touching a gate, a shared contract, a migration, customer data, or
user-facing copy — see `CLAUDE.md` for what counts.

**One, not two — and what is measured is the saving, not the equivalence.**
A run of aimed, single-reviewer rounds found defects that would otherwise
have shipped, including two that killed a design outright before any code was
written. That is evidence one aimed reviewer is *worth running*; it is not
evidence it matches two. Nobody has run both on the same target. What
appeared to do the work was the aim — told what to examine, told what would
count as an answer, allowed to be short — rather than the headcount, and a
second reviewer given the same unaimed brief mostly restates the first.

**What you give up, and it is not only redundancy.** Two unaimed reviewers
can disagree, and the disagreement is itself a finding: it marks a place the
design is ambiguous, which neither checklist would surface alone. One aimed
reviewer cannot produce that, and it can only be wrong in the direction its
aim pointed — so a **mis-aimed** brief has no backstop. That risk is not
covered by the exceptions below, which are about the *subject matter* being
dangerous rather than about the caller having misjudged where the danger is.
If you are not confident you know where the risk lies, that is itself a
reason to run two.

⚠ **This is a claim about round 1 only, and about count only.** Whether a
narrow brief beats a broad one is untested — the comparison it came from
changed the count, the model and the brief width at the same time, so the
saving is attributable and the "narrow finds more" part is not. If you have
budget to settle it: one broad and one narrow reviewer, same target, same
model, compare findings.

Also run `reviewer-decisions` once, briefed to the project spec's decision
register (the paths under `spec:` in `.rite/config.yaml`) and any
decision-bearing files in `.rite/context/` — does the build match what was
already decided?

For anything crossing a module boundary, also run `reviewer-seam`.

Fix what round 1 finds. Record each finding in a delta register (one row:
finding · which agent raised it · the class of defect · what changed ·
evidence — a failing test, a killed mutant, or "not measurable here,
because..."). Name the **class** of the defect before writing the fix, not
just the specific instance the reviewer demonstrated — a fix spelled against
the exact case shown closes that case and nothing else.

## The terminating check

Two fresh `reviewer-terminating` agents, scoped to round 1's **fixes**, not
its findings — and not staffed by whoever proposed those fixes.

**Still two, deliberately.** Round 1 came down to one because the evidence
supported it; nothing in that evidence touches this stage. This is the last
gate before something ships and it reviews fixes, which are written under
more pressure and less context than the code they repair. Cutting the final
check to save tokens is how a review stage becomes decorative. Give them the
register and the diff, never round 1's transcripts.

This is the last stage. If it finds something, fix it and record it in the
register — there is no round 3. A fix here that introduces a genuinely new
mechanism (not a repair of the old one) gets flagged as its own follow-up
chunk for next time, not folded back into another review loop now.

## When you're done

The register is the artifact. A change with no register entries has not been
reviewed, regardless of what agents ran.
