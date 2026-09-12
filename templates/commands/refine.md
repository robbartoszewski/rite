---
description: Turn a ticket, or a vague phrase, into a complete ticket someone could start cold. Implements nothing.
---

Turn `$ARGUMENTS` into a ticket complete enough that any session — including
one with no memory of this conversation — could start it without asking a
clarifying question first.

**This command implements nothing.** Its only output is a well-formed
ticket.

1. **If it names an existing ticket**, read it and find the gaps: missing
   scope, no definition of done, no verify step, ambiguous acceptance
   criteria.

2. **If it's a vague phrase**, turn it into scope: what repo or module, what
   changes, what does NOT change, what "done" looks like, and how someone
   would check it mechanically.

3. **Search before creating.** Check the ticket backend for something
   already covering this — by title and by the files or modules involved.
   Duplicates are noise.

4. **Write the ticket body to full quality:**
   - What it delivers, in one or two sentences.
   - Exact paths or modules touched.
   - Definition of done — a checklist, not a vibe.
   - A `## Verify` section: the exact command(s) that prove it's done.
   - Dependencies on other tickets, named explicitly.

5. **Do not start the work.** If asked to both refine and implement, refine
   first, stop, and confirm before continuing — those are two different
   requests wearing one sentence.
