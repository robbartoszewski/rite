# getrite.ai — design & content handover

**For:** Claude Design (and any designer/developer building the site)
**Product:** rite — `github.com/robbartoszewski/rite`
**Site:** getrite.ai (marketing site; no app, no login)
**Brief written:** 2026-10-08, against `origin/main` at `v0.7.0a10`
**Status of this brief:** reviewed twice (see §14 Review log)

---

## 0. How to use this brief

Everything in §4 is verified against the repository. **Do not add product
claims that are not in §4.** If a section of copy needs a claim you cannot
find in §4, leave a `TODO(owner)` marker instead of writing something
plausible — this product's whole voice is built on not doing that.

Two things are deliberately *not* decided here. They are the owner's calls and
they are written as plain questions in §1. Design can build around either
answer; where the answer changes the layout, that is noted inline.

One caution about sources: the repository's own `README.md` "Planned — not
built" section and `docs/guide.md` "Roadmap" are both **behind the code** as of
2026-10-08 — they still say the local tier is unwired and that rite never
pushes, which the source and `CHANGELOG.md` contradict. §4 reflects the source
and the changelog. If you read the repo yourself, prefer `CHANGELOG.md` and
`src/` over those two sections.

---

## 1. Decisions for the owner (not decided here)

**Decision 1 — maturity framing.** The site has to say what stage rite is at.
Four honest options, with what each one costs:

| Option | Wording | Reads as | Risk |
|---|---|---|---|
| A (recommended) | **"Alpha — building in public"**, with a visible `v0.7.0a10` badge and a linked *What works, what doesn't* page | Confident, unusually candid, aimed at people who read source | Some visitors bounce on "alpha" |
| B | **"Open beta"** | More inviting, implies it holds together end to end | Overstates it: one sandboxed Worker taking a ticket all the way to a merged PR has not yet run through in one go |
| C | **"Early access"** | Implies a gate and a list to join | Odd for an MIT tool anyone can install today; invites "access to what?" |
| D | **"Research preview"** | Lowest promise | Undersells working, measured machinery; reads as a toy |

**Recommendation: A.** The product's differentiator *is* its candour about its
own limits, and the README already reads that way. Leading with "alpha" and
then showing enforced gates and measured numbers lands better with this
audience than hedged beta language. Pick one and tell Design; it affects the
hero badge, the FAQ, and whether §9.7 exists.

**Decision 2 — the commercial story.** The brief says the direction is
open-core: MIT core plus a paid management layer. **Nothing about a paid tier
exists in the repository**, so the site cannot describe one as a product.
Options: (a) say only "MIT, and it will stay MIT" and leave commercial plans
off the site entirely; (b) one short honest paragraph — "a paid management
layer for teams is planned; the core stays MIT" — with no pricing, no feature
table, no date; (c) a full pricing page. **Recommendation: (b).** Do not use
(c). **No prices anywhere — inventing a number is the owner's call, not
Design's, and not this brief's.**

**Decision 3 — waitlist or no waitlist.** An MIT tool you can install right
now does not need a waitlist, and having one weakens the "just install it"
CTA. But if the owner wants a list for the managed layer or for release
announcements, that is a real reason. Options: no email capture at all;
a one-field "tell me when 1.0 lands" strip near the footer; or a full
early-access gate. **Recommendation: the middle one**, labelled *release
notifications*, never *early access* (there is nothing gated). This changes
the final CTA block (§9.9).

---

## 2. One-liner and elevator pitch

**One-liner (use this):**
> rite runs a fleet of AI coding agents against one codebase — with exclusive
> file claims so they don't collide, and stages its own code won't let them
> skip.

**Shorter, for the `<title>` and social:**
> Orchestration for fleets of AI coding agents. Open source, local-first.

**Elevator pitch (~100 words, for an About block or a README-style intro):**
> Running one coding agent is straightforward. Running six against the same
> repository is not: they overwrite each other's files, skip the review step
> when the prompt gets long, and report a step as done that failed. rite is the
> layer that makes a fleet behave. Each agent claims the paths it is about to
> touch and the second claim on the same path is refused. Each ticket moves
> through a pipeline rite's own code drives — definition, plan, plan review,
> approach, work, verify — and a model cannot skip or reorder a stage. The plan
> is approved by a different model than wrote it. It runs on Claude, on local
> open-weight models, or both. Everything runs on your machine. MIT.

---

## 3. Audience and personas

The site has one job: **survive a skeptical senior engineer reading it for
ninety seconds.** Everything else follows from that.

**P1 — "I already run three Claude sessions and they fight."** (primary)
Senior/staff engineer or applied-AI engineer, already using Claude Code daily,
already hit the collision problem by hand. Knows what a file lock is and will
ask how yours is implemented.
*Wants:* proof the exclusion actually holds; what happens to a stale claim; how
much it costs in quota.
*Converts on:* the measured soak numbers and a link to the source.
*Lost by:* any sentence that sounds like a press release.

**P2 — "I can't send our code to an API."** Engineer at a company with a
data-residency or policy constraint, evaluating whether agent-assisted
development is possible at all on-prem.
*Wants:* to know exactly what leaves the machine and what doesn't, with the
caveats included.
*Converts on:* the local-model path and an honest network boundary section.
*Lost by:* "your code never leaves your machine" stated as an absolute — they
will immediately find the exception and stop trusting the whole page.

**P3 — "I'm choosing an orchestration approach."** Tech lead or founding
engineer comparing rite against building something in-house or against other
multi-agent frameworks.
*Wants:* the architecture, the opinionated bits, the maturity, the licence.
*Converts on:* the spec. 101 numbered decisions with reasoning is the single
most persuasive asset this product has for them.
*Lost by:* a feature grid with no mechanism behind it.

**P4 — the open-source reader / the HN commenter.** May never run it. Will
read the README, look for the overclaim, and say what they find in public.
*Wants:* to catch you exaggerating.
*Converts on:* you having already said the thing they were going to point out.
*This persona is the site's real QA.* Design for them: a visible limits page is
worth more than a testimonial.

Not an audience: non-technical buyers, executives, "AI-curious" general
visitors. Do not add copy for them.

---

## 4. What rite actually is — verified fact sheet

Verified against `origin/main` at `v0.7.0a10` (2026-10-08). Every row below is
usable in copy. **Anything not here is not usable.**

### 4.1 Shipped and measured

- **Version `0.7.0a10`. Pre-1.0 alpha. MIT licence.** Not on PyPI. Needs
  `uv` or `pipx`, `git`, Python 3.11+, and a signed-in Claude Code. Install is
  a `curl | sh` from a release tag; the repo documents two slower paths
  (verify the checksum, or clone and read it) and the reasoning. Nothing
  phones home.
- **Claim-based exclusion.** Each agent works as a named *Worker* and claims
  the files it is about to touch; the second Worker to claim the same path is
  refused. `rite claim`, `rite release`, `rite status`.
- **The exclusion is measured, not asserted.** A four-minute soak of six
  concurrent Workers: **132,321 grants, zero lost and zero held twice.** The
  same harness against the previous lock produced **309 lost updates in ten
  seconds.** (Both numbers are quotable; always give the conditions with them.)
- **Per-Worker checkouts.** `rite add worker alpha` creates `workers/alpha/`
  holding that Worker's own clone of each module.
- **A staged pipeline rite's own code enforces** (landed 2026-10-08,
  `src/rite_ai/local/stage.py`, `gates.py`). A ticket moves: definition →
  plan → plan review → approach → work → recomposition verify → delivery.
  The stage is *persisted* (a restart resumes where the work is, not where a
  session remembers being), every move goes through one guard, and each stage's
  own artifact must already exist in the state the stage claims before the
  stage can be entered. The model **cannot skip or reorder a stage.** The log
  is append-only.
- **Independent-model plan review, enforced in code.** `rite plan approve
  <ticket>` / `rite plan reject <ticket> --reason-file <path>`. The approver
  must hold the plan-review duty, must **not** be the plan's author, and must
  be a **different model** from the author; an author rite cannot place fails
  the check closed. A rejection sends the plan back to be re-authored, with
  reasons, and that loop is bounded. Before this release rite stamped its own
  approval and nobody read the plan — worth saying, as a "we fixed this"
  changelog note rather than in hero copy.
- **Nothing is delivered until the composed work passes the ticket's own
  agreed check.** Each piece passing its own check is not the ticket working.
- **The definition of done is pinned.** The signed refinement record the Worker
  was started on is snapshotted; the plan is written against it and the
  finished work is checked against it, so a ticket edited mid-flight cannot
  move the target.
- **Sandboxed Workers (macOS).** `rite sandbox start <name>` runs a Worker
  under macOS Seatbelt via [yoloAI](https://yoloai.dev); `rite init` turns the
  setting on by default on macOS. **Read the caveats in §4.3 before writing any
  security copy.**
- **Secret scanning.** Runs on pre-push and in CI, over full history.
  Suppressing a finding takes a written reason; there is no global off switch.
- **Ticket boards.** JIRA and GitHub Issues. `/refine "<idea>"` drafts a ticket
  a cold session could start — paths touched, definition of done, and a
  `Verify` section naming the command that proves it — checks the board for a
  duplicate first, and implements nothing.
- **Managers.** A Manager is a long-running session that works your board and
  talks to you, over Slack if configured. `rite start <manager> --sessions N
  --minutes M` runs **in your own foreground terminal**; Ctrl-C ends the run.
  A bare `rite start` continues that Manager's last session; `--fresh` starts
  a new one. A Manager can act on its own Workers — `rite request
  start|stop|restart|destroy|status|gate|deliver` — without you running host
  commands, and only on Workers rite started for it.
- **Engines.** Claude Code; local open-weight models through **Goose + Ollama**;
  and **Cursor** as a Manager engine. Deliberately not behind a generic
  provider abstraction — `CLAUDE.md` and `.claude/agents/` are first-class
  — and a general abstraction layer is explicitly not planned.
- **Mixed fleets.** A Claude Owner with a local secondary Manager is the setup
  this release is built for: **one machine, one project root.**
- **A local model's context window is pinned, not hoped for.** Ollama serves
  every model at 4,096 tokens by default — smaller than the agents' own system
  prompts (~19 KB for Goose, ~31 KB for another agent measured). rite refuses a
  local Manager that declares no `context_window` (minimum 32,768) and pins the
  declared window into the model on the server, so the server's default does
  not decide it.
- **A local model's report is not trusted.** In testing, a small local model
  often did not reply at all, and more than once reported a step as done that
  had failed. rite checks every reply in a separate session before the Owner
  reads it — CONFIRMED, CONTRADICTED, or COULD NOT TELL — and tells the Owner
  to check too. *The checker is also a model and can be wrong*, and the repo
  says so; keep that sentence if you use this.
- **Spec tooling for large specs.** `rite spec index` turns a spec into
  addressable units; `rite spec slice 5.3` prints that section, what it cites,
  and what it depends on — about 3% of rite's own 7,100-line spec. It refuses
  specs a slice cannot help.
- **`rite loop`** watches the queue and reports, every couple of minutes, in
  one word, why the run is where it is — everyone busy, files held, schedule
  closed, nothing waiting, or **deadlocked** (work waiting, nobody able to take
  it, holders look gone), where it stops and prints exactly what to release.
  **It starts nothing.**
- **Multi-machine claim sharing** since 0.4.0, with Owner election and failover
  — see §4.3 for its status.
- **Host hardening (0.7.0a10).** rite runs git on the host inside repositories
  a Worker can write, and git runs programs those repositories configure. rite
  now forces those off for every git command it runs. It also no longer follows
  symlinks in a Manager's own directory, closing a path where a Manager could
  have had rite overwrite an arbitrary file or send any readable file as a
  message.
- **101 numbered decisions** in `SPEC.md` (~9,000 lines), each with the question
  it answers and the reasoning.
- **`rite doctor`** checks tools, credentials and config, and exits non-zero on
  problems. **`rite update --files-only`** refreshes a project's generated files
  after an upgrade without overwriting your edits.

### 4.2 Planned / explicitly not built — do not imply these work

- **Perpetual unattended operation.** This is the north star, not a feature.
- **Unattended dispatch of Claude sessions is refused on purpose**
  (`SPEC §9.12`), because it spends your quota while nobody is watching.
  *Frame this as a judgment call, which it is — not as a missing feature.*
- **One sandboxed Worker taking a ticket all the way to a merged PR has not yet
  run through in one go.** Its parts have each been run.
- **A real sandboxed local-model turn with a live model is not measured.** The
  whole path has been measured with the model replaced by a scripted command
  inside a real sandbox; the live run is held behind an open sandbox-isolation
  issue.
- **Multi-machine coordination is implemented and unproven** — it has not been
  run on two physical machines over a real network.
- **Multiple Managers across machines** is planned for 0.8.0; today it is one
  machine, one project root.
- **Routing questions to whoever owns an area:** the table of people and areas
  is printed into the Owner's instructions. Nothing matches a question to a
  person, breaks ties, or reroutes. It is reference material, not a mechanism.
- **Nothing notices a rejected push of a Worker's work.** Practical
  consequence worth stating on the site: scope branch protection to your
  default branch, or a Worker's work exists only inside a sandbox that is
  later destroyed.
- **No gates on your code.** Your sessions run your tests and linters, but rite
  does not read the results — no coverage threshold, no accessibility pass, no
  opinion on your test strategy.
- **No capability routing.** Workers are interchangeable; assignment picks
  whichever is free, not whichever *can*.
- **Not on PyPI.**

### 4.3 The caveats that must survive into the copy

These are the ones that get a site mocked if they are smoothed over. The repo
states each one plainly; so should getrite.ai.

1. **The sandbox bounds files, not capability.** The repo's own words: a guard
   rail against mistakes, not containment. Outbound network is **not**
   restricted — Seatbelt has no network isolation. `git` runs hooks, `python -c`
   runs anything. What the profile buys is that *a mistake stays inside the
   project*. **Never write "isolated", "contained", or "secure sandbox".**
2. **A session you open yourself is not sandboxed**, whatever the setting says.
3. **Linux is thinner than macOS.** Built and tested on macOS 26.2. On Linux
   (tested Ubuntu 24.04, ARM64) the sandbox is weaker, Workers are **not
   sandboxed by default**, and reaching the tmux server — which runs outside
   the sandbox — is closed on macOS and **open on Linux**. **Windows is not
   attempted.**
4. **On Docker, file locking does not lock** — two Workers can be granted the
   same path. Run one Worker there.
5. **Quota.** rite neither meters nor throttles. N Workers burn your Claude
   quota at roughly N times one session's rate, against a quota shared with
   the Claude apps on a rolling window. Pro exhausts sooner than Max. It also
   does not end gracefully: a Worker that runs out stops where it stands, claim
   still held until you release it.
6. **A Manager has your file and network access.** It runs behind a generous
   permission allowlist (77 command families) because a Manager that must stop
   and ask is not running unattended — measured: the gated modes refused a
   Manager the ability to run `rite` or `gh` at all. Treat a Manager as having
   your own access, because a determined one does.
7. **A claim whose holder died is reported, never released.** rite cannot tell
   a crashed session from one thinking hard, and releasing a path under a live
   Worker is worse than a stale claim. So it names the claim, the holder and
   the command, and waits for you.
8. **If you run one session at a time you do not need this.** The repo says it;
   putting it on the site is a trust win and costs nothing — it disqualifies
   visitors who would have churned anyway.

---

## 5. Positioning and differentiators

**Category:** orchestration / control plane for AI coding agents. Not an agent,
not an IDE, not a model.

**The problem, in the order the audience feels it:**
1. Two agents edit the same file and one of them loses. *(Felt first, by hand.)*
2. An agent skips the review step, or marks a failed step done. *(Felt second,
   and worse.)*
3. You cannot run this on your own hardware or your own code. *(Felt by a
   specific audience, hard.)*

**Differentiators, in priority order for the site:**

1. **Enforcement, not instruction.** The stages are driven by rite's code and
   each gate re-checks the artifact — not by a line in a `CLAUDE.md` that a
   model may or may not follow. This is the hardest thing for a competitor to
   copy and the easiest thing for this audience to verify. *Lead with it.*
2. **Review by a different model than wrote the plan**, checked in code: not
   the author, a different model, and a named author or it fails closed. Very
   few things in this space enforce independence rather than ask for it.
3. **Measured, not asserted.** 132,321 grants, zero lost; 309 lost updates for
   the lock it replaced. The site should link to source for every number.
4. **Frontier and local, including mixed.** Claude Code, Goose + Ollama, Cursor
   as a Manager engine — named integrations rather than a lowest-common-
   denominator abstraction, deliberately.
5. **Local-first and on-prem capable.** Everything runs on your machine;
   nothing phones home. With a local fleet, inference stays on your hardware
   too. (Write the boundary precisely — see §9.6.)
6. **Open source, MIT.** The spec is public: 101 numbered decisions with the
   reasoning for each.
7. **Candour as a feature.** The README has a "Why you might not want it"
   section. Carrying that onto the site is positioning, not a disclaimer.

**North star (state as a goal, never as a capability):** set a fleet running,
come back to reviewed, delivered work. Today rite concentrates the hours that
need you — settling requirements, making the calls, reading what came back —
and keeps sessions moving in between.

**What we are not positioned against:** we are not claiming to be faster,
cheaper, or better than any named competitor. No comparison tables.

---

## 6. Messaging hierarchy

### 6.1 Hero headline options

**Option 1 — the problem (safest, most concrete)**
> # Many agents. One codebase. No collisions.
> Subhead: rite gives each agent exclusive claims on the files it touches, and
> drives every ticket through stages its own code won't let a model skip.
> Open source, runs on your machine. Alpha.

**Option 2 — the differentiator (recommended)**
> # Your agents can't skip the review step.
> Subhead: rite runs a fleet of coding agents against one codebase — exclusive
> file claims so they don't collide, enforced stages so none of them cuts a
> corner, and a plan approved by a different model than wrote it. Claude, local
> models, or both. MIT.

**Option 3 — the north star (highest ceiling, needs the most care)**
> # Set it running in the evening. Review it in the morning.
> Subhead: The orchestration layer for fleets of AI coding agents — claims,
> enforced stages, independent review. We're building toward unattended; here's
> exactly how far it goes today. → *What works, what doesn't*
>
> ⚠ **Only use Option 3 with that link in the hero, and only if Decision 1 is
> A.** Without the link it is a promise the product does not yet keep: a
> sandboxed Worker has not yet carried one ticket all the way through on its
> own. This is the repo's own tagline, so it is the owner's voice — but on a
> marketing page it reads as a capability claim rather than an intent.

**Recommendation: Option 2**, with Option 1's headline as the H2 of the first
section below the fold. Option 2 leads with the thing nobody else enforces,
and "can't" is a claim about code, which this audience will go and check — and
find true.

### 6.2 Core value props (the four-up below the hero)

1. **Claims, not conventions.** Each Worker claims its paths; the second claim
   on the same path is refused. 132,321 grants in a six-Worker soak, zero lost.
2. **Stages a model can't skip.** Definition, plan, review, approach, work,
   verify — persisted, gated, append-only. The stage never leads the artifact.
3. **Reviewed by a different model.** Enforced in code: not the author, a
   different model, a named author or it fails closed.
4. **Yours, on your machine.** MIT. Claude, local open-weight models, or a
   mixed fleet. Nothing phones home.

### 6.3 Proof points (every one links to source)

| Claim | Proof | Link to |
|---|---|---|
| The lock holds | 132,321 grants / 6 concurrent Workers / 4 minutes / zero lost, zero double-held; 309 lost updates for the previous lock in 10s | README |
| Stages are enforced | `stage.py` transition table + `gates.py` per-stage artifact gate, append-only log | source |
| Review is independent | approver is not the author, is a different model, fails closed on an unplaceable author | source |
| It is thought through | 101 numbered decisions, each with its question and reasoning | `SPEC.md` |
| Secrets don't leak | pre-push + CI scan over full history; suppression needs a written reason; no global off switch | README |
| It is honest | "Why you might not want it" and a planned-vs-built list, in the README | README |

Design note: proof points are the highest-value real estate on this site. Each
one should be a link, and the link should land on the actual line — this
audience clicks.

---

## 7. Tone and voice

**Confident, technical, specific, and unusually honest.** The voice is a senior
engineer explaining a system to another senior engineer, including the parts
that are not finished.

**Do:**
- Use concrete nouns and real command names: `rite claim`, `rite plan approve`,
  `workers/alpha/`.
- Give numbers with their conditions. "132,321 grants in a four-minute soak of
  six concurrent Workers" — never a bare "132,321 grants".
- Say what something does *not* do in the same breath as what it does. "The
  loop tells you the run has stalled and what is holding it. It starts nothing."
- Prefer the short Anglo-Saxon verb: *runs*, *claims*, *refuses*, *holds*,
  *checks*.
- Use em-dash asides and parentheticals for caveats. The repo's voice does.
- Let a sentence be blunt. "If you run one session at a time you do not need
  this."

**Don't:**
- No hype adjectives: *revolutionary, seamless, powerful, cutting-edge,
  enterprise-grade, next-generation, game-changing, effortless, blazing-fast,
  world-class*.
- No "unlock", "unleash", "supercharge", "elevate", "empower", "transform your
  workflow", "the future of", "at scale", "10x".
- No anthropomorphising: agents don't *collaborate*, *think*, *understand*, or
  form a *team*. They run, claim, write, and report.
- No vague intensifiers: *dramatically*, *significantly*, *massively*.
- No implied social proof: no "trusted by", no "developers love", no logo
  strip, no testimonials, no fake star counts, no "join thousands".
- No exclamation marks. No emoji in body copy (a single ⚠ before a real
  caveat is fine).
- Don't call it *AI-powered*. Everything here is AI; the word carries nothing.
- Don't write a sentence you could not point at a file to support.

**Calibration test:** if a sentence would look out of place in the repository's
own README, cut it. The README is the voice.

---

## 8. Brand and visual direction

### 8.1 Palette

| Token | Hex | Use |
|---|---|---|
| Crimson | `#B82D22` | Primary. Logo, primary CTA, key accents. Use sparingly — it should mean something. |
| Ember | `#E8743B` | Secondary accent, gradients with crimson, hover states, flame highlights, data-viz second series. |
| Ink | `#211E1B` | Body text on light; background on dark. Warm near-black, not pure black. |
| Off-white | `#FAF7F2` | Background on light; text on dark. Warm paper, not pure white. |

Derive the greys by mixing ink into off-white rather than adding a neutral grey
— the whole palette is warm and a cool grey will read as a mistake. Keep body
text at ink on off-white (or off-white on ink) and let crimson/ember carry
emphasis only.

**Dark mode:** build it. This audience runs dark editors. Ink background,
off-white text, ember as the accent (crimson on ink loses contrast — check it,
and nudge toward ember where it fails AA).

**Accessibility:** every text/background pair must pass WCAG AA (4.5:1 body,
3:1 for large text). Never put crimson text on ember or ember on crimson.
Never use colour alone to carry meaning in the shipped/planned distinction —
pair it with a label or an icon.

### 8.2 Logo and the flame motif

A **flame mark** is the identity. A brand asset pack — **`rite-brand.zip`:
logo, favicon, app icons, monochrome, social** — **exists and the owner will
provide it.** Do not redraw the mark, re-letter the wordmark, or recolour the
logo. If the pack has not arrived yet, lay out with a placeholder and a
`TODO(assets)` marker rather than improvising a flame.

**Flame motif, used well:**
- As a restrained accent: a small flame bullet on a list, a flame in the
  favicon and the hero lockup, a crimson→ember gradient on one hero element.
- As a metaphor for *heat where the work is*: an ember glow on the active
  stage in a pipeline diagram, or on an active Worker in a fleet diagram.
- As texture at very low opacity behind a single section — at most one per page.

**Flame motif, used badly (avoid):**
- Animated fire, flickering GIFs, particle effects, embers drifting up the page.
- Flames on every card, bullet, icon and divider. The motif dies from repetition.
- "Burning" or "on fire" puns in copy. One is already too many.
- Flames implying speed or destruction. The mark is about focused heat, not
  burning your repo down.

### 8.3 Imagery

**Do:**
- Terminal output. This is the product's real face — `rite status`, the claim
  refusal, the permissions banner, `rite doctor`. Set it in a good mono
  (JetBrains Mono, IBM Plex Mono, Berkeley Mono) with real text, selectable,
  not a screenshot image. Use the actual output from §4 and §9, unedited.
- **Diagrams that show the real mechanism.** The two worth drawing: (a) the
  claim — several Workers, one codebase, one path held and a second claim
  refused; (b) the pipeline — the seven stages as a chain with the gate drawn
  as the thing checking the artifact, and an arrow from *plan review* back to
  *plan* for a rejection. Hand-drawn-precise, labelled with real stage names,
  inline SVG, legible in both themes.
- Monospace type as a texture. Generous whitespace. Let the page be quiet.

**Don't:**
- No stock photography. No people at laptops. No handshakes, no "team at a
  whiteboard".
- No generic AI imagery: glowing brains, neural-network meshes, circuit-board
  traces, humanoid robots, blue-purple gradient blobs, hexagon grids.
- No 3D-rendered abstract shapes, no floating glassmorphic cards.
- No fake dashboard UI for a product that has no UI. rite is a CLI. Showing an
  invented web dashboard is the single most damaging thing this site could do
  to its credibility.
- No logo strips (there are no customers), no fabricated metrics, no screenshots
  of a terminal with invented output.

### 8.4 Layout and craft

Single-column, generous measure (~65–75ch for body), clear type scale, one
accent colour per viewport. Mobile: real 16px gutters, no horizontal scroll,
terminal blocks that scroll horizontally inside their own box rather than
breaking the page. Keep it fast — this audience notices: no web fonts beyond
two families, no analytics-heavy tag soup, no hero video. Prefers-reduced-motion
respected; nothing animates on scroll except at most one subtle reveal.

---

## 9. Sitemap and content outline (with draft copy)

**Recommended scope: one long home page plus two supporting pages.** A product
at this stage does not need eight pages, and a thin site with nothing
exaggerated beats a full one padded out.

```
/                       Home (long-scroll, the whole story)
/what-works             What works, what doesn't  ← the trust page
/licensing              MIT + the commercial note (only if Decision 2 = b)
  external → GitHub (repo, README, SPEC.md, docs/guide.md, CHANGELOG.md)
```

Do **not** build: a blog, a docs site (documentation lives in the repo — link
to it, never imply a hosted docs site exists), a pricing page, a careers page,
a customers page, a comparison page.

### 9.1 Nav

`rite` (flame lockup) · What works · Licensing · GitHub ↗
Right-aligned: a version chip reading **`v0.7.0a10 · alpha`**, linking to the
changelog. That chip is a feature. Keep it visible on scroll.

### 9.2 Hero

*Content: headline (§6.1 Option 2), subhead, two CTAs, maturity chip, and one
piece of real terminal output. No illustration.*

**Draft:**

> # Your agents can't skip the review step.
>
> rite runs a fleet of AI coding agents against one codebase: exclusive file
> claims so they don't collide, stages its own code won't let a model skip, and
> a plan approved by a different model than wrote it. Claude, local open-weight
> models, or both — all on your machine.
>
> `[ Read the spec ]` `[ GitHub ↗ ]`
>
> **Alpha, v0.7.0a10.** Pre-1.0, and honest about it →
> *What works, what doesn't*

Terminal block under the fold line — this is the whole product in six lines:

```console
$ rite claim backend/src/billing --worker alpha --ticket ABC-12
claimed 1 path(s) for alpha

$ rite claim backend/src/billing --worker beta --ticket ABC-19
claim failed: path contention
  backend/src/billing overlaps backend/src/billing (held by alpha)
```

### 9.3 The problem

*Content: three short beats, no illustration. This is where P1 nods.*

**Draft:**

> ## Many agents. One codebase.
>
> One coding agent is easy. Six against the same repository is not.
>
> **They collide.** Two sessions open the same file and one of them loses —
> silently, because neither knows the other is there.
>
> **They cut corners.** A `CLAUDE.md` that says "review before you open a PR"
> is a request. Twenty thousand tokens later it is a suggestion.
>
> **They report success.** A step that failed comes back as done. Small local
> models do this often enough that rite treats every one of their reports as
> unverified until a separate session has checked it.
>
> rite is the layer that makes a fleet behave: claims it hands out, stages it
> enforces, and reviews it refuses to skip.

### 9.4 How it works

*Content: four steps. Each step = a heading, two sentences, and either a real
command or a stage chain. Diagram (b) from §8.3 sits with step 3.*

**Draft:**

> ## How it works
>
> **1 — Write the ticket.** `/refine "export invoices as CSV"` drafts a ticket
> a cold session could start: the paths it touches, a definition of done, and a
> `Verify` section naming the command that proves it. It checks your board —
> JIRA or GitHub Issues — for a duplicate first, and implements nothing.
>
> **2 — Give each agent its own ground.** `rite add worker alpha` creates
> `workers/alpha/` with its own clone of each module. Before touching anything,
> a Worker claims its paths. The second Worker to claim the same path is
> refused, so two agents cannot edit one file.
>
> **3 — Work moves through stages, not vibes.** definition → plan → **plan
> review** → approach → work → verify → delivery. Each stage is recorded, each
> one is gated on its own artifact already existing, and a model cannot skip or
> reorder one. The plan is approved by a **different model than wrote it** —
> not the author, a different model, or it fails closed. A rejection goes back
> to the planner with reasons.
>
> **4 — You read what came back.** `rite status` shows every Worker and the
> paths it holds. `rite loop` watches the queue and tells you, in one word, why
> the run is where it is — everyone busy, files held, schedule closed, or
> **deadlocked**, where it stops and prints exactly what to release.
>
> *Starting a session is always your action. Nothing rite runs unattended opens
> a Claude session, because that would spend your quota with nobody watching.*

### 9.5 Features

*Content: six cards, each one claim + one mechanism + one caveat where there is
one. The caveat line is not a weakness; it is the differentiator.*

**Draft:**

> **Claims that actually hold.** Each Worker claims the paths it will touch;
> the second claim on the same path is refused. Measured, not asserted: a
> four-minute soak of six concurrent Workers gave 132,321 grants with zero lost
> and zero held twice. The lock it replaced lost 309 updates in ten seconds.
>
> **Stages a model can't skip.** The stage is persisted — a restart resumes
> where the work is, not where a session remembers being. Every move goes
> through one guard, the log is append-only, and a stage can't be entered until
> its own artifact already says what the stage claims.
>
> **Independent review, in code.** Approval needs a reviewer that holds the
> review duty, is not the author, and runs a different model than the author.
> An author rite can't place fails the check closed. `rite plan approve` /
> `rite plan reject --reason-file`.
>
> **Frontier and local, in one fleet.** Claude Code; local open-weight models
> through Goose and Ollama; Cursor as a Manager engine. Named integrations
> rather than a lowest-common-denominator abstraction — on purpose. A Claude
> Owner with a local secondary is the setup this release is built for: one
> machine, one project root.
>
> **Sandboxed Workers on macOS.** A Worker runs under Seatbelt, and `rite init`
> turns it on by default on macOS. Read this as written: the sandbox bounds
> **files, not capability** — outbound network isn't restricted and `python -c`
> runs anything. What it buys is that a mistake stays inside the project. On
> Linux, Workers aren't sandboxed by default.
>
> **Secrets don't walk out.** A scan runs on pre-push and in CI, over full
> history. Suppressing a finding takes a written reason, and there is no global
> off switch.

### 9.6 Local, private, yours

*Content: the precise network boundary. P2's whole decision happens here, and a
single overstatement loses them. Write the exceptions in.*

**Draft:**

> ## It runs on your machine
>
> rite is a CLI and a set of files in your repository. There is no service, no
> account, and no telemetry — nothing phones home. Claims, state and credentials
> live in your project and your home directory.
>
> **What leaves your machine is whatever your agents send.** On a Claude fleet,
> that is code going to Anthropic, exactly as it does when you use Claude Code
> yourself. On a fleet of local open-weight models through Ollama, inference
> stays on your hardware. You can mix the two, and the choice is per Manager
> and per Worker.
>
> Be precise about the edges: a Worker's sandbox does not restrict outbound
> network, pushes go to the git remote you configured, and if you wire up Slack,
> a Manager's questions and replies go through Slack. rite's own code sends
> nothing anywhere else.
>
> ⚠ Design note — the three sentences in that last paragraph are the most
> important ones on the page for persona P2. Do not cut them for length, and do
> not replace this section with "your code never leaves your machine", which is
> false for a Claude fleet.

### 9.7 What works, what doesn't *(own page, linked from the hero)*

*Content: the honest state of things, as a two-column table. Build this page
whatever Decision 1 says — it is the trust asset. Pull the rows from §4.1,
§4.2 and §4.3 verbatim; do not soften the wording.*

**Draft intro:**

> ## What works, what doesn't
>
> rite is at `v0.7.0a10` — pre-1.0, and moving. This page is the version of
> this product that a careful reader would arrive at after reading the source,
> written down in advance so you don't have to.
>
> **Works, and measured:** claim exclusion under concurrency; per-Worker
> checkouts; the staged pipeline, enforced by rite's code; independent-model
> plan review; delivery blocked until the composed work passes the ticket's own
> check; sandboxed Workers on macOS; secret scanning over full history; JIRA
> and GitHub Issues; Managers on Claude, Goose or Cursor; a local model's
> context window pinned rather than hoped for; every local-model report checked
> in a separate session.
>
> **Not there yet, and it matters:**
> - **Unattended operation is the goal, not the state.** Starting a session is
>   your action. Dispatching Claude sessions with nobody watching is refused on
>   purpose — it spends your quota unsupervised — so the loop reports a stalled
>   queue rather than working it.
> - **One sandboxed Worker has not yet carried a ticket all the way to a merged
>   PR in one run.** Each part of that path has been run.
> - **A live sandboxed local-model turn is not measured.** The path has been
>   measured with the model replaced by a scripted command, inside a real
>   sandbox.
> - **Several machines on one project is implemented and unproven** — not yet
>   run on two physical machines over a real network. Several Managers across
>   machines is planned for 0.8.0.
> - **Linux is thinner than macOS**, and Windows is not attempted.
> - **On Docker, file locking does not lock.** Run one Worker there.
> - **rite does not meter your quota.** Six Workers burn it about six times as
>   fast as one session.
> - **rite has no opinion on your code.** Your sessions run your tests; rite
>   does not read the results.
>
> **If you run one session at a time, you do not need this.**

### 9.8 Open source and licensing

*Content: short. Build per Decision 2 — the draft below is option (b).*

**Draft (Decision 2 = b):**

> ## MIT, and the spec is public
>
> rite is MIT-licensed and the design document is in the repository: **101
> numbered decisions**, each with the question it answers and the reasoning
> behind it. If you want to know why something works the way it does, it is
> written down.
>
> The core stays MIT. A paid management layer for teams is the direction, and
> there is nothing to sell yet — when there is, it will be described here
> plainly.
>
> `[ Read SPEC.md ↗ ]` `[ Browse the source ↗ ]`
>
> ⚠ Design note: no pricing, no tier table, no "coming soon" badge, no date.
> **Pricing is the owner's call and is deliberately absent from this brief.**

### 9.9 FAQ

*Content: answer the objection, don't deflect it. Short answers; link out.*

**Draft:**

> **Is it production-ready?**
> No. It is `v0.7.0a10` — pre-1.0 alpha. The claim exclusion and the staged
> pipeline are built and measured; perpetual unattended operation and a
> sandboxed Worker carrying a ticket end to end in one run are not proven yet.
> *What works, what doesn't* has the full list.
>
> **Which models does it run?**
> Claude Code for a frontier fleet, local open-weight models through Goose and
> Ollama, and Cursor as a Manager engine. Mixed fleets work on one machine with
> one project root. A local Manager must declare a context window of at least
> 32,768 — Ollama's default of 4,096 is smaller than the agents' own prompts,
> and rite refuses rather than letting it fail mysteriously.
>
> **Does it work on Linux?**
> Partly, and the difference matters. rite is built and tested on macOS 26.2.
> On Linux — tested on Ubuntu 24.04 — Managers run, but the sandbox is weaker
> and Workers are not sandboxed by default. Windows is not attempted.
>
> **Is the sandbox secure?**
> It bounds files, not capability. Outbound network is not restricted, `git`
> runs hooks and `python -c` runs anything. What it buys is that a mistake
> stays inside the project. Treat a Manager as having your own file and network
> access, because a determined one does.
>
> **What does it cost to run?**
> Your own model usage. rite neither meters nor throttles, so N Workers spend
> your Claude quota at roughly N times one session's rate — on a Claude plan,
> that quota is shared with the Claude apps on a rolling window. A local fleet
> costs you hardware instead.
>
> **Can I use it on a repository I don't own?**
> Yes, through a fork, and the shape is deliberate: the Worker pushes its branch
> to your fork and **you** open the pull request upstream. A diff going to
> someone else's project should be read by you before its maintainer sees it.
>
> **What happens if an agent dies mid-ticket?**
> Its claim is reported, never released automatically — rite cannot tell a
> crashed session from one thinking hard, and releasing a path under a live
> Worker is worse than a stale claim. It names the claim, the holder and the
> command, and waits for you.
>
> **Do I need this?**
> If you run one session at a time, no.
>
> **Is it on PyPI?**
> Not yet. Install from a release tag with `uv` or `pipx`; the repo documents
> how to verify the download first, and why you might want to.

### 9.10 Final CTA

*Content: one block, two actions. Per Decision 3, optionally a one-field
notification strip — labelled release notifications, never "early access".*

**Draft:**

> ## Run six agents. Keep your repository.
>
> rite is MIT, installs in a minute, and tells you what it cannot do.
>
> `[ Read the spec ]`  `[ GitHub ↗ ]`
>
> *Optional, per Decision 3:* Want to know when 1.0 lands?
> `[ your@email ]` `[ Notify me ]` — release notes only, nothing else.

### 9.11 Footer

Repo · SPEC.md · Guide · Changelog · Licence (MIT) · the flame mark.
No social-proof row, no newsletter pitch beyond the one strip above, no
"© 2026 rite Inc." — there is no Inc.

---

## 10. SEO

**Primary keywords** (realistic intent, low-to-mid competition):
`AI agent orchestration`, `multi-agent coding`, `parallel Claude Code sessions`,
`AI coding agent orchestration`, `local LLM coding agents`, `self-hosted AI
coding agents`, `agent file locking`.

**Secondary / long-tail:** `run multiple Claude Code sessions on one repo`,
`Ollama coding agent orchestration`, `on-prem AI coding agents`, `agent plan
review`, `Goose Ollama orchestration`, `open source agent orchestrator`.

Do not chase `AI coding assistant` or `AI agents` — the intent is wrong and the
competition is everyone.

**Home `<title>`** (≤60 chars):
`rite — orchestration for fleets of AI coding agents`

**Home meta description** (≤155 chars):
`Run many AI coding agents on one codebase: exclusive file claims, stages they can't skip, plans reviewed by a different model. Open source, local-first.`

**/what-works `<title>`:** `rite — what works, what doesn't`
**meta:** `An honest account of what rite does today at v0.7.0a10, what is measured, and what is not proven yet.`

**Also:** one H1 per page; real `<h2>`s matching §9's sections; `og:` and
`twitter:` tags using the social image from `rite-brand.zip`; a
`SoftwareApplication` JSON-LD block with `applicationCategory: DeveloperApplication`
and the MIT licence; canonical URLs; a sitemap. **No `SoftwareApplication`
rating or review markup** — there are no reviews, and inventing them is exactly
what this brief exists to prevent.

## 11. CTAs

**Primary — Read the spec** (`SPEC.md`). Unusual for a primary CTA and correct
here: the spec is the most persuasive asset the product has for P1 and P3, and
clicking it is the conversion event that matters.
**Secondary — GitHub.** Repo link, no star-count badge.
**Tertiary — Install.** The command, copyable, with a link to the verify-first
path. Never style `curl | sh` as the one true way; the repo doesn't.
**Quaternary (optional, Decision 3) — release notifications.** One field,
labelled plainly.

Button copy: *Read the spec*, *GitHub*, *Copy install command*, *What works,
what doesn't*, *Notify me*. Not: *Get started free*, *Try rite now*, *Join the
revolution*, *Request a demo* (there is nothing to demo).

---

## 12. DO NOT claim — hard list for Design

Any of these makes the site wrong. Several would get it picked apart in public.

1. ❌ Production-ready, GA, stable, battle-tested, 1.0, or any synonym.
2. ❌ Customers, users, adopters, companies, teams using it, "trusted by",
   logo strips, testimonials, case studies, star counts, download counts.
3. ❌ "Ships code while you sleep", "fully autonomous", "hands-off", "set and
   forget", "runs unattended" — unattended Claude dispatch is **refused on
   purpose**, and the loop starts nothing.
4. ❌ "Your code never leaves your machine" as an absolute. True of rite's own
   code; false of a Claude fleet.
5. ❌ "Secure", "isolated", "contained", "airtight" about the sandbox. It
   bounds files, not capability, and does not confine network.
6. ❌ Linux parity, Windows support, or Docker support without the
   locking caveat.
7. ❌ A paid tier, pricing, plans, seats, free trial, or "contact sales".
8. ❌ Any price figure, invented or implied.
9. ❌ A hosted docs site, a web dashboard, a UI, a cloud offering, an API, or
   an installer that doesn't exist. **rite is a CLI.**
10. ❌ Availability on PyPI, Homebrew, or any package manager.
11. ❌ Benchmarks about delivery speed, tickets per hour, cost savings,
    productivity multipliers, or "N× faster". None have been measured.
12. ❌ Comparisons to named competitors, or claims about what they can't do.
13. ❌ "Capability-based routing" or agents picked for their strengths —
    Workers are interchangeable and assignment picks whichever is free.
14. ❌ That rite plans your project, reviews your code quality, enforces
    coverage, or has any opinion on your tests. It does not read your test
    results.
15. ❌ That rite routes questions to the right person. The table of people and
    areas is printed; nothing matches a question to a name.
16. ❌ That multi-machine operation is proven. It is implemented and untested
    over a real network; multi-machine Managers are planned for 0.8.0.
17. ❌ SOC 2, ISO, GDPR compliance, enterprise security, audit certification,
    or any regulatory posture.
18. ❌ Any number, quote, or output not present in §4 or §9 of this brief.
19. ❌ Invented terminal output. Copy it from §9 or from the repo, unedited.
20. ❌ Mentions of the owner's employer, clients, consulting work, internal
    tickets, or any private context. This site is about rite only.

**When in doubt:** write less, link to the repo, and leave a `TODO(owner)`.

---

## 13. Deliverables checklist for Design

- [ ] Home page per §9.2–§9.6, §9.8–§9.11 (long-scroll, one H1).
- [ ] `/what-works` per §9.7 — build this regardless of Decision 1.
- [ ] `/licensing` per §9.8, only if Decision 2 = (b).
- [ ] Light and dark themes, both AA-compliant (§8.1).
- [ ] Two inline SVG diagrams: the claim refusal, and the gated stage chain
      with the rejection loop (§8.3).
- [ ] Terminal blocks as real selectable text, content copied from §9 verbatim.
- [ ] Mobile layout at 375px: 16px gutters, no horizontal page scroll.
- [ ] Favicon, app icons, OG/Twitter image from `rite-brand.zip` (owner to
      provide; placeholder + `TODO(assets)` until it arrives).
- [ ] Meta, JSON-LD, canonicals, sitemap per §10.
- [ ] A pass against §12 before handing back — line by line.
- [ ] Every number and command cross-checked against §4.

---

## 14. Review log

The brief was drafted, then reviewed twice. What each round changed:

### Round 1 — AI-slop and correctness (against the repository)

Reviewed every product claim against `origin/main` at `v0.7.0a10`. **Three
false claims removed, 25 changes in all.**

*Cut as false:*
1. **"Ships tickets while you sleep" / "set it running in the evening" as the
   hero promise.** Unattended Claude dispatch is refused on purpose
   (`SPEC §9.12`), `rite loop` starts nothing, and one sandboxed Worker has not
   yet carried a ticket end to end in a single run. Demoted to a flagged
   Option 3 with a required limits link, and restated as the north star in §5.
2. **"Your code never leaves your machine."** False for a Claude fleet.
   Replaced with the precise boundary in §9.6, including the sandbox's
   unrestricted network, the git remote and Slack.
3. **"Does it work on Linux? Yes."** Linux is thinner: weaker sandbox, Workers
   not sandboxed by default, tmux reach open. Rewritten in §9.9, with Windows
   stated as not attempted.

*Cut as slop:* "revolutionary", "enterprise-grade", "unlocks the full
potential", "at scale", "turns your agents into a coordinated engineering
team", "Orchestrate fleets of AI coding agents at scale". Added the explicit
banned-word list in §7 so the words cannot come back in.

*Corrected:*
4. **Open-core described as an existing product.** Nothing about a paid tier is
   in the repository. Moved to Decision 2 with a recommendation to say one
   honest sentence and no more; pricing explicitly marked the owner's call.
5. **"Is it production ready? Nearly."** Replaced with "No", plus the specific
   things that are and are not proven.
6. **"Claude and any Ollama model"** — overstated. Local support is Goose-only,
   and a local Manager must declare a context window ≥32,768; Cursor is a
   Manager engine. Corrected in §4.1 and §9.9.
7. **"Agents plan, review and deliver"** conflated two different paths. The
   enforced stage pipeline is real and is rite's own code; the human planning
   step is explicitly *not* rite's job. Rewritten in §9.4.
8. **Bare proof numbers.** "132,321 grants" now always carries its conditions
   (four-minute soak, six concurrent Workers), and the 309-lost-updates
   comparison was added — it is what makes the number mean something.
9. **"Sandboxed workers" as a security proof point.** Added the repo's own
   framing — a guard rail, not containment — and banned "secure" and
   "isolated" in §12. This was the likeliest thing on the page to be picked
   apart in public.
10. **"Docs"** implied a hosted documentation site. There isn't one;
    documentation is in the repo. Noted in §9 as a do-not-build.

*Added, because their absence was itself a credibility problem:* the quota
warning (§4.3.5 — N Workers burn N× quota, and a Worker that runs out holds its
claim); the Docker locking caveat; "a session you open yourself is not
sandboxed"; the stale-claim behaviour; the install reality (no PyPI,
Python 3.11+, `curl | sh` with the verify-first alternative); the shipped-vs-
planned split in §4.1/§4.2; and a note that the repo's own README "Planned"
section and the guide's roadmap are **behind the code** as of 2026-10-08, so
Design does not re-introduce stale limits as current ones.

### Round 2 — positioning and PR impact

Reviewed for whether it lands with a senior-engineer audience and whether the
maturity framing is right. **Ten structural changes.**

1. **Changed the lead.** Round 1 left "Many agents. One codebase. No
   collisions." as the hero — honest, but it positions rite as a file-lock
   utility and buries the thing nobody else does. Promoted **enforcement** to
   the hero ("Your agents can't skip the review step") and moved the collision
   line to the first section below the fold. "Can't" is a claim about code, and
   this audience will check it and find it true.
2. **Moved maturity out of the FAQ and into the hero.** A disclosure in an FAQ
   reads as hidden. A visible `v0.7.0a10 · alpha` chip in the nav, plus a
   dedicated `/what-works` page linked from the hero, converts the weakness
   into the site's most distinctive asset. Drove the Decision 1 recommendation
   toward "alpha / building in public" rather than "open beta".
3. **Reframed the unattended gap as a judgment call.** It is not a missing
   feature: dispatching Claude sessions unattended is refused because it spends
   quota with nobody watching. Stated that way in §9.4 and §9.7, which is
   stronger PR than either hiding it or apologising for it.
4. **Added "Why you might not want it" thinking throughout**, mirroring the
   README — including "if you run one session at a time you do not need this"
   in §9.7. Counter-intuitively the highest-trust content on the site and the
   most likely thing to be quoted approvingly.
5. **Added persona P4, the skeptical reader**, and made surviving them the
   site's stated job. Several §8 and §9 choices follow from it.
6. **Required every proof point to link to source** (§6.3). This audience
   clicks; an unlinked number is a liability.
7. **Rebuilt the CTA hierarchy.** "Star on GitHub" was weak and a waitlist is
   odd for an MIT tool you can install today. Primary CTA is now **Read the
   spec** — 101 numbered decisions is the strongest asset for P1 and P3 — with
   GitHub secondary and the notification strip demoted to optional under
   Decision 3, labelled *release notifications* rather than *early access*.
8. **Banned the invented dashboard** (§8.3) and any UI imagery. rite is a CLI;
   showing a web dashboard would be the most damaging single asset this site
   could ship.
9. **Required the mechanism wherever "deterministic" appears.** Show the stage
   chain and the gate, or the word is just another buzzword. Drove the two
   required diagrams in §8.3.
10. **Cut the site's scope** from six-plus pages to one long page and two
    supporting pages. A thin site with nothing exaggerated reads better at this
    stage than a padded one, and every extra page is a place for an unverified
    claim to appear.
