---
description: Digest the registered project spec into units a Worker can load one slice of, then review the result in two rounds. Writes derived units only; never edits the spec.
---

Digest this project's spec so a Worker can load the part its ticket needs instead
of the whole document. You write the unit bodies and review them. Everything
mechanical — finding what changed, hashing, checking coverage — is `rite spec`,
which spends no tokens.

**You never edit the source spec here.** If a unit cannot be written faithfully
because the source is wrong or contradicts itself, stop and say so: that is a
change to the spec, and it is the user's decision.

1. **Check the spec decomposes before spending anything on it.**

   ```
   rite spec index
   ```

   It lists the units, the hubs it pins, the index sections it excludes and the
   projected slice sizes, and ends with a verdict. **If it says the spec does not
   decompose, stop.** Workers keep reading the whole spec through its pointer,
   and that is the supported outcome for a densely interlinked document, not a
   failure to work around.

2. **Find what needs writing.**

   ```
   rite spec status
   ```

   It names every unit that is new, stale (its source changed), removed, or
   tampered with (its body no longer matches its recorded hash). Work only on
   those. A unit that is none of these is still valid, and so is its last review.

3. **Write each new or stale unit** at the path `rite spec status` prints beside
   it — a unit id is not a file name, so use the path it gives rather than
   deriving one.

   **A first digest of a large spec does not fit in one pass.** rite's own spec
   is 189 units. Write as many as you can do carefully, review those, and stop:
   the gate in step 6 reports `incomplete: N of M unit(s) digested` for that
   state, which is not drift and is an honest place to stop. Say in your report
   how many remain, so the next run picks them up. Quality per unit is what
   cannot be recovered later; coverage can.

   - Say what the source says, in fewer words, **without changing what it
     means.** Keep every qualifier, every ⚠, every "unless" and "only when"; a
     conditional turned into an absolute is the distortion this review exists to
     catch.
   - Keep identifiers as the source has them: `§5.3.3`, `D-31`. A citation is
     how a Worker gets from one unit to the next; paraphrasing one breaks the link.
   - A decision row (`D-<number>`) keeps its choice word for word. It is a
     decision already made; restating it in other words makes a second one.
   - Set `covers` to every source unit id the body represents. Merging two tiny
     adjacent sections into one unit, or splitting a long one, is fine — say so
     in `covers`, and cover everything.
   - **Quote `id` and every entry in `covers`.** Unquoted, YAML reads `8.10` as
     the number 8.1 — a different section. Write `id: '8.10'`.
   - **Do not write `source_sha` or `body_sha` yourself.** Leave them empty and
     stamp the file once its body is final:

     ```
     rite spec stamp 5.3 2.4/promotion
     ```

     A hash typed by hand is a guess, and the gate treats it as one. Stamping is
     its own step rather than something `rite spec index` does, because a stamp
     applied automatically would bless a spec change nobody read and a hand edit
     nobody made.
   - Delete the unit file for anything `rite spec status` reports as removed.

4. **Round 1 — each unit against its own source.** For every unit you wrote or
   changed, give a fresh reviewer the unit and **only** its source range — the
   file's own `source_lines`, recorded when it was stamped — and ask:

   - Does it omit a qualifier, drop a ⚠, or turn a conditional into an absolute?
   - Does it say anything the source range does not?
   - Does every citation in it still point where the source's did?

   Scoped to one unit on purpose: a reviewer holding the whole document reads
   what it expects instead of what is there. Fix what it finds and re-review
   those units.

5. **Round 2 — the set against itself.** A different question from round 1, not a
   second pass of it. Give a fresh reviewer the changed units **and their
   neighbours** — the units that cite them and the units they cite — and ask:

   - Does any unit contradict another?
   - Does any unit contradict something the source says *outside* its own range,
     which round 1 cannot see because it never looks there?
   - Did splitting separate a rule from the exception that qualifies it? That is
     the damage splitting does, and no single-unit check can see it.

   **On the first digest of a spec, and whenever the user asks for it, run round 2
   over every unit.** Otherwise it is neighbourhood-scoped, and that is a
   heuristic: a contradiction between two units with no citation between them is
   missed. Your report must say which one you ran.

6. **The gate.**

   ```
   rite spec verify
   ```

   It must pass: every source unit covered, nothing covering a unit that no longer
   exists, nothing stale, nothing hand-edited. It does not check meaning — rounds
   1 and 2 did that. Do not finish on a failing gate — with one exception,
   the `incomplete:` line above, which says the units you did write are sound
   and others are still to come. Anything else it reports is drift, and drift is
   yours to fix before you stop.

   Once it passes, `rite spec show <unit>` is what hands a Worker the text you
   just wrote, with the source range it came from.

7. **Report**, in one short list:
   - units written, changed and removed;
   - round 1: what it found and what you changed;
   - round 2: what it found, and **whether it covered every unit or only the
     changed ones and their neighbours**;
   - the output of `rite spec verify`.
