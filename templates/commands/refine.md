---
description: Agree a ticket's definition of done with the person here, and record it with rite. Implements nothing.
---

Agree a definition of done for `$ARGUMENTS` with the person in this session,
and record it with `rite refine accept`. A Worker starts only on a ticket rite
reports REFINED, and it works to exactly what you record here.

**This command implements nothing.** Its only output is a signed record on
the ticket. It is not `/spec`: that writes the project's spec.

**Run it only outside a sandbox**, in a session running as the person (the
project root in Claude Code or the Claude app). Inside a Manager's or a
Worker's sandbox, `rite refine accept` cannot read the key it signs with,
and it refuses.

1. **Find the ticket.** If `$ARGUMENTS` names one, `rite refine status <ID>`
   says where it stands and `rite board show <ID>` prints it. If it is a
   phrase, not a ticket, search the board first (`rite board list`), then
   file it with the person's own words: `rite board create "<title>"
   --description "<what they said>"`. Duplicates are noise.

2. **Ask what you would otherwise have to guess.** At most three questions
   at a time, numbered, to the person here: which file or module, what
   counts as done, what must not change, how it is checked. If they say
   "you decide", do not stop: propose a complete definition of done as your
   recommendation, and ask them to confirm it.

3. **Draft the definition of done, and show where each item came from.** A
   checklist, one item or more. Tag each item: `ticket: "<exact words from
   the ticket>"`, `answer: "<exact words the person said>"`, or `proposed`
   (yours). A quote must be exact; if you cannot quote it, it is `proposed`.
   The tags are for the person to see which parts are yours; what you record
   in step 5 is each item's text without its tag.
   Add the commands that prove it (`--verify`), or say that none were agreed.
   Add what is in and out of scope where it matters.

4. **Get an explicit yes to that exact text.** Show it to the person and ask
   them to accept it or correct it. "Sounds fine I guess" is not a yes: ask
   again, with the correction folded in. Nothing is recorded until they say
   yes.

5. **Only then, record it:**

   ```
   rite refine accept <ID> --item "<item>" [--item "…"] \
       [--verify "<command>"] [--in-scope "…"] [--out-of-scope "…"]
   ```

   Or `--as-written` if the ticket already has a "Definition of done" heading
   and the person accepts it as it stands. The record says it was attested
   by a session running as the person, which is what this is. rite reads the
   ticket back and says REFINED only if the board agrees.

6. **Do not edit the ticket's title or description.** The record is what
   refines it; rewriting the person's text would replace their words with
   yours. If they want the ticket itself changed, they change it (and the
   record then reads STALE until it is agreed again).

7. **Do not start the work.** If asked to both refine and implement, refine
   first, stop, and confirm before continuing: those are two different
   requests wearing one sentence.
