---
description: Write this project's spec from the brief — problem, scope, architecture, and a numbered decision register. Implements nothing and creates no tickets.
---

Write this project's spec. `$ARGUMENTS` is optional direction from the user —
a focus, or a document to start from. With none, start from the brief.

**This command implements nothing and creates no tickets.** Its only output
is `SPEC.md`, registered with rite. Planning the work and filing tickets come
afterwards, and are separate requests.

1. **Read what rite already knows.**
   - `.rite/config.yaml` — if `spec: paths:` lists a spec **and that file or
     directory exists**, stop and say so: `/spec` writes a project's first
     spec, so offer to amend the existing one instead. A listed path with
     nothing there is not a spec — carry on.
   - `.rite/brief.yaml` — what `rite init` gathered: name, kind, description,
     platform, languages, frameworks, architecture.
   - `.rite/modules.yaml`, `.rite/context/INDEX.md` and `.rite/kb/INDEX.md`,
     for anything the user has already provided.

2. **Ask the follow-ups the brief actually warrants** — usually two or three,
   never a fixed list. A description that names a pattern ("event sourcing",
   "multi-tenant") gets questions about that pattern. A blank description
   gets the question that matters first: what should this do, and for whom?
   Ask them in one message and wait for the answers.

   **Ask about their project, in their words — never about rite.** The
   person may have installed rite an hour ago. Leave rite's own terms
   (Worker, Owner, Manager, claims) out of the questions; they know what
   they want to build, not how rite organises the work.

   **Do not write the answers into `.rite/brief.yaml`.** They belong in the
   spec: an answer that settles something is a decision, and a question the
   user cannot answer yet is an open question.

3. **Draft the spec from this skeleton — in your reply, not on disk.** Keep
   every heading, even for a small project — fill a section with one honest
   line rather than dropping it.

   ```markdown
   # <project name>

   ## Problem
   What this is for, and who has the problem. Two or three sentences.

   ## Scope
   What the first working version does.

   ### Non-goals
   What it deliberately does not do — the things someone would otherwise
   assume are included.

   ## Architecture
   The shape of the system: its parts, where state lives, what talks to
   what. Name a pattern only if the user chose one.

   ## Decisions

   Tickets cite these as D-<number> instead of restating them. A number is
   never reused or renumbered; a reversed decision gets a new row that says
   which one it replaces.

   | D | Decision | Choice | Why |
   |---|----------|--------|-----|
   | D-1 | Scope of the first version | ... | ... |

   ## Open questions
   Raised and not yet answered. Each names who can answer it. When one is
   answered, it becomes a row in Decisions.
   ```

   **The decision register is mandatory, even with a single row.** It is
   what makes a ticket that says "implement per D-3" resolvable; a spec
   without it is prose nothing can point into. Every project decides at
   least what its first version includes and excludes, so D-1 is always
   that.

   Record a decision only when the user made it or agreed to it. A choice
   you suggested is an open question until they do.

   **Write nothing to disk in this step.** A spec the user has not agreed to
   should not be sitting in their project — someone who walks away here is
   left with a file they never approved and were never told about.

4. **Show the draft and ask them to correct it.** It is their design; you
   wrote it down. Say plainly that nothing has been written yet, so they know
   walking away costs them nothing. Explain the Decisions table in one
   sentence when you show it: each row is a choice already made, numbered so
   later work can refer to it instead of repeating it.

5. **Once they approve, write `SPEC.md` at the project root and register it:**

   ```
   rite spec add SPEC.md
   ```

   That points Workers at it and rewrites the Project spec section of this
   project's `CLAUDE.md`. Run `rite doctor` afterwards and check that the
   `spec` rows name the file and the D-number convention.

6. **Tell them to commit the three files this just changed** — `SPEC.md`,
   `.rite/config.yaml` and `CLAUDE.md` — because a Worker gets its files by
   cloning, so an uncommitted spec exists in no Worker's checkout and the
   pointer just recorded resolves to a path that is not in their tree.

   ```
   git add SPEC.md .rite/config.yaml CLAUDE.md && git commit -m "Add the project spec"
   ```

   Say it; do not run it for them. What else is uncommitted in their tree is
   theirs to judge, not yours.

7. **Stop.** Report what the spec settles and what is still open, then the
   next step: rite has no planning step, so deciding what gets built first is
   the user's — usually by talking it through with you. When they have
   decided, each piece becomes a ticket via `/refine <what to build>`. Do not
   start that yourself.
