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
`reviewer-round1` agent. Two agents for ordinary work; at least three for
anything touching a gate, a shared contract, a migration, customer data, or
user-facing copy — see `CLAUDE.md` for what counts.

Also run `reviewer-decisions` once, briefed to `.rite/brief.yaml`'s
`enriched` section and any decision-bearing files in `.rite/context/` — does
the build match what was already decided?

For anything crossing a module boundary, also run `reviewer-seam`.

Fix what round 1 finds. Record each finding in a delta register (one row:
finding · which agent raised it · the class of defect · what changed ·
evidence — a failing test, a killed mutant, or "not measurable here,
because..."). Name the **class** of the defect before writing the fix, not
just the specific instance the reviewer demonstrated — a fix spelled against
the exact case shown closes that case and nothing else.

## The terminating check

Two fresh `reviewer-terminating` agents, scoped to round 1's **fixes**, not
its findings — and not staffed by whoever proposed those fixes. Give them the
register and the diff, never round 1's transcripts.

This is the last stage. If it finds something, fix it and record it in the
register — there is no round 3. A fix here that introduces a genuinely new
mechanism (not a repair of the old one) gets flagged as its own follow-up
chunk for next time, not folded back into another review loop now.

## When you're done

The register is the artifact. A change with no register entries has not been
reviewed, regardless of what agents ran.
