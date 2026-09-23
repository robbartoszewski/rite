# Design notes

Working documents behind rite's design — the reasoning, the measurements and
the superseded turns, kept because a decision without its argument is just an
assertion. They are **not** the specification (`SPEC.md`) and not the user
guide (`docs/guide.md`): several are snapshots from the day they were written
and say so in their own banners.

**Read the banners first.** Where a note has been checked against the tree,
it opens with a dated correction saying which of its claims no longer hold.
A note with no banner has not been re-checked, and its status lines are from
whenever it was written — `git log` is the authority on what is built.

## Public by default, and what that means

This directory ships. Everything tracked here is scanned by the publish gate
on every commit (`rite publish check`, and the `Publish gate` workflow), which
covers it by construction: the scan is `git ls-files`, so there is no list to
keep in sync and no way for a file here to be quietly excluded.
`tests/test_public_docs_are_scanned.py` pins that.

What the gate catches is hardcoded home paths, project-declared patterns and —
via gitleaks — secrets. What it cannot catch is judgement: a paragraph
describing a client's deployment leaks nothing a regex can see.

**So the rule is about where you save the file, not what the gate says.**
Anything carrying a home path, a real credential namespace, a customer's
infrastructure, or how and where a token is stored belongs in
`docs/private/`, which is gitignored.

⚠ **Never `git add -f` into `docs/private/`.** A file that is both tracked and
ignored stops appearing in `git status`, and that is not hypothetical here:
five of these notes were force-added into a wholly-ignored `.docs/` and drifted
49-119 lines behind their working copies with nothing ever reporting it. If a
private note needs to be public, move it and scan it, rather than forcing it.

## Where things went

This directory replaces the old `.docs/`, which was ignored wholesale — which
is why `V070_MULTI_MANAGER.md`, the design anybody taking rite over would
need, did not reach a fresh clone.
