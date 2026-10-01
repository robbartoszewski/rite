---
description: Work a ticket end to end — claim, implement, review, open a PR, release.
---

Work ticket `$ARGUMENTS` end to end.

1. **Work to the ticket's agreed definition of done, and only that.** You do
   not judge whether a ticket is complete enough; rite's record does.

   - **A Worker started by rite:** the ticket and its agreed definition of
     done are in `TICKET.md` in your working directory. Work to its section
     "Agreed definition of done" (checklist, scope, Verify), not to the
     title, and cite its record id in your pull request. There is nothing
     for you to check: rite checked that record on the host, in the same
     read that produced the ticket text, before this sandbox existed. You
     hold no board credential and cannot read the refinement key, so do not
     try `rite board show` or `rite refine status` from inside the sandbox.
     No `TICKET.md`, or no such section: say so, and stop.
   - **A person's session, outside any sandbox:** `rite refine status <ID>`.
     REFINED prints the definition of done: work to it. Anything else: do not
     start. If the person is here, agree one with them and record it with
     `rite refine accept <ID> --item "…"`; otherwise say which state rite
     reported, and stop. UNREADABLE means rite could not check, not that
     there is no definition of done: it says why.

   **One exit, and it is not "incomplete":** a definition of done that cannot
   be met as written (a path it names does not exist, two items contradict)
   is reported with exactly that, and you stop. Never invent the missing
   piece.

   If it cites the project spec (`§5.3`, `D-31`), read those parts rather than
   the whole document:

   ```
   rite spec slice 5.3 --worker <your-name>
   ```

   It prints the section, what it cites and the sections everything depends on.
   Where the spec has been digested, `rite spec show 5.3` gives you the same
   section rewritten shorter and reviewed — and tells you when that text has
   drifted from the spec.
   **If that was not enough and you read the whole spec anyway, say so** when
   you write your handover:

   ```
   rite handover write --spec-fallback 5.3 ...
   ```

   Nobody can see a slice that came up short; the count is the only evidence
   that the slices need to be bigger.

2. **Prepare your workspace** — right repos, right branches, no residue
   from a previous task (SPEC §2.1):

   ```
   rite prepare --worker <your-name> [--branch <ticket-branch>]
   ```

   A dirty tree from a previous task **blocks and is never discarded** —
   resolve it (commit, stash, or ask) before continuing. A network error
   degrades to using whatever's already on disk; a real repository problem
   still blocks.

3. **Claim your paths.** Before touching anything:

   ```
   rite claim <paths...> --worker <your-name> --ticket <id>
   ```

   Claim at file or directory granularity, never a whole module — a
   module-wide claim blocks every other ticket touching that module even
   when the file sets don't overlap. If the claim is refused, another
   session is already on an overlapping path; do not proceed on those paths.
   If `rite claim` fails any other way (an error, not a refusal), you hold
   nothing: stop and report it rather than working unclaimed.

4. **Do the work.** Follow this project's `CLAUDE.md` and the relevant files
   in `.rite/context/` — check `.rite/context/INDEX.md` for anything whose
   "when to consult" column matches what you're touching.

   **Anything you find that is not this ticket becomes a ticket, at the
   moment you find it.** Not a note in the handover, not a line in the PR:
   those are read once, by one person, and then they are gone. One question
   decides how to file it:

   > **Would this ticket's definition of done still be met without doing
   > this?**
   >
   > - **Yes** — file it. Leave it unlinked.
   > - **No** — file it, and record that this ticket is blocked by it:
   >   `rite board link <this-ticket> <new-ticket>`. That default direction
   >   is "TICKET_ID is blocked by TARGET_ID", verified against a live
   >   board; no `--type` needed.

   Ask that question, not "is this in scope". "In scope" is a judgement
   call, and a judgement call gets answered three different ways by three
   people while the coverage looks identical either way.

   File for something **found**, never something imagined. If you cannot say
   what you observed that prompted it, it is not a ticket.

5. **Run the module's own test and lint commands.** They are in the module
   map in the project root's `CLAUDE.md`, under the heading for the module
   you touched, as `Test:` and `Lint:` — `rite init` read them out of that
   module's own manifest (`package.json`, `pyproject.toml`, `Cargo.toml`, …)
   so that nobody has to guess. Run them as written.

   Where an entry says "not detected", ask rather than inventing a command:
   a command that is wrong in a way that still exits 0 is indistinguishable
   from a passing suite, and the reviewer inherits it.

6. **Verify your own fix before a reviewer does.** A green suite says the
   project still works, not that your change does anything. Delete the fix
   and re-run whatever proves it — if the change is enforced by nothing, you
   want to find that now, not in review.

7. **Run the review convention** (`/review`) before opening a PR.

8. **Publish as the project's strategy says**, per module: `rite doctor`
   prints each module's `publish:` line. Under `commit`, stop at the commit
   on the ticket branch: nothing is pushed. Otherwise push the branch and
   open the PR, referencing the ticket, and run `rite publish check` by hand
   before pushing. A pre-push hook and a CI workflow run the same scan, but
   neither is guaranteed to be armed in this project — `core.hooksPath` can
   disable the hook with no signal, and the workflow may never have been
   installed — so run it yourself rather than assuming.

9. **Never merge a pull request yourself**, your own included. Merging after
   checks is the User's, or rite's own gated step, which merges only on a
   green matched to the exact head that contains the base's current tip.
   Do not release your claims yet — a second Worker claiming your paths
   while review is still open is exactly the collision claims exist to
   prevent.

10. **Release your claims once the work has landed** — delivered under
    `commit`, merged otherwise — not before:

    ```
    rite release --worker <your-name>
    ```

11. **Report.** One paragraph: what shipped, what's left (if anything, it is
    already a ticket by step 4 — name it, do not restate it), what
    verification you ran, including which test and lint commands you ran and
    what they printed.
