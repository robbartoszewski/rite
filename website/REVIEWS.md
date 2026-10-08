# Review log

The site was built from `docs/GETRITE_HANDOVER.md` and the design pack, then
reviewed **twice by independent agents that did not build it** — fresh eyes, as
asked, not self-review. Each round's findings were verified against the
repository before being applied; two findings were partly wrong and are noted
as such.

---

## Round 1 — content and correctness

Reviewed every claim on the site against `docs/GETRITE_HANDOVER.md` §4 (the
verified fact sheet), §7 (voice) and §12 (the do-not-claim list), and against
`origin/main`. **22 findings. 14 applied, 5 noted, 2 corrected, 1 ratified.**

### Found clean (stated, because it was the point of the round)

- **No §12 violations in the build.** No production-ready/stable/GA, no
  customers or social proof, no "while you sleep", no absolute "never leaves
  your machine", no secure/isolated/contained about the sandbox, no platform
  parity, no price or tier, no hosted docs/dashboard/UI, no PyPI availability,
  no benchmarks, no competitor comparisons.
- **No private context.** Grepped for the owner's other projects, client
  references and internal ticket prefixes. The only hits were `RL-6`, which is
  a *public* decision ID in `SPEC.md`, cited legitimately as a source.
- **No hype or AI slop**, no anthropomorphised agents, no exclamation marks.
- **Every number and command name correct** against `origin/main`.
- **All nine proof line anchors land on the exact line they claim.**
- **Drafts genuinely excluded** from the build, the listings, the feed and the
  sitemap.

### Applied

1. **The install one-liner installs v0.6.0, not the version the site is about.**
   `install.sh` pins `VERSION="${RITE_VERSION:-v0.6.0}"` — true even at the
   `v0.7.0a10` tag — so the command does not install the enforced stages or
   independent plan review the page describes above it. The install section now
   says so outright.
   *(Partly corrected: the reviewer said `v0.7.0a10` is not a tag. It is. The
   installer's pinned default is the real problem, and it is a bug in rite, not
   on the site — flagged to the owner.)*
2. **A link in the published blog post 404'd on GitHub Pages.** A Markdown
   `[…](/what-works)` does not pass through `url()`, so it resolved to
   `<origin>/what-works`. Fixed properly with a rehype plugin
   (`src/lib/rehype-base-urls.mjs`) that rewrites root-relative links in
   Markdown to carry the deploy base, and degrades to a no-op on the custom
   domain.
3. **Multi-machine claim sharing is not peer-to-peer.** It pushes claim and
   lease state to a **coordination git repository** — a third party for most
   users, in the one section that must not be smoothed over. Both the prose and
   the boundary table now say that.
4. **The 77-command allowlist does not apply to a Goose Manager.** Goose takes
   its permission from the environment. The table collapsed all Managers into
   one row, in the direction of sounding more bounded than rite is; it now has
   separate rows for a Claude/Cursor Manager and a local Goose one.
5. **`rite loop` is a command group, not a command.** A bare `rite loop` prints
   help. Corrected to `rite loop start`, and the description corrected to match
   the README: it writes to a log in the background rather than telling you.
6. **The FAQ repeated three feature cards almost verbatim.** Dropped the
   duplicated models question, trimmed the sandbox answer, and used the space
   for the fork objection ("Can I use it on a repository I don't own?"), which
   nothing on the site answered.
7. **The blog teaser sat inside the landmark announced as "Common questions"**,
   before the heading that names it. Moved to its own section.
8. **"Install it, or read it first." was also governing the MIT block.** Split
   into two sections.
9. **"Read the spec" appeared three times**, twice as primary, against the
   component's own stated rule. Dropped the third; the primary CTA now carries
   the external-link marker every other off-site button had.
10. **"11 things work today. 13 don't"** counted a row the same sentence calls
    "a decision, not a gap". Reworded to "13 carry a label".
11. **"Manager" appeared with no definition.** Glossed where it first matters.
12. **`copy-lint` would have failed the build** the moment a draft published,
    over a *verbatim* quote of `SPEC.md` §9.12. Added to its ALLOW list.
13. **Three code comments cited the wrong source** (the stage names' provenance,
    the blog schema's brief section) or overstated it. Corrected.
14. Smaller copy fixes: "the source … the source" on the trust page's opening
    line; "never released for you"; "the ticket's own check" → "agreed check";
    the 404 naming a domain the site isn't on; the proof strip's heading
    claiming three measurements when one is a document property; the blog's
    `<h1>` promising three categories with one post live.

### Noted, not applied

- Pinning proof links to a tag — done in Round 2 (finding 9 there).
- The blog itself is a documented deviation from brief §9 ("do not build a
  blog"), added by the owner. Ratified, not silently kept.
- Dark-mode wordmark — fixed in Round 2.

---

## Round 2 — design, UX and technical

Reviewed the running build in a browser at 375 / 768 / 1440 in both colour
schemes, plus the Pages configuration, payload and accessibility.
**36 findings: 4 blocking, 15 should-fix, 17 polish. All 4 blocking and 13
should-fix applied.**

### Found clean (measured, not asserted)

- **Base-path correctness in `dist/`: zero errors.** Every `href`, `src`,
  `content`, CSS `url()`, the sitemap, the feed and the webmanifest carry
  `/rite`.
- **No horizontal page scroll** at 375, 768 or 1440 — `scrollWidth ===
  innerWidth` on all six routes.
- **Zero console messages, zero asset 404s, zero third-party requests.**
- **Payload well inside budget:** home page 13.8 KB gzipped HTML+CSS against a
  60 KB budget; **867 bytes** of inline JS against 2 KB; exactly 6 woff2 on
  first paint (2 families × 3 weights).
- **Contrast passes AA on every rendered text pair in both themes** (muted text
  5.29–6.37:1, accents 4.94–5.72:1, focus ring ≥3:1).
- **One `<h1>` per page, no skipped heading levels, `aria-current` correct,
  native keyboard-operable FAQ, all touch targets ≥44px, `prefers-reduced-motion`
  honoured, decorative images correctly `alt=""`.**
- **Workflow mechanics correct** for Pages: permissions, concurrency, the
  artifact path, and `check` gating the upload.

### Blocking — applied

1. **Markdown code blocks rendered pure white in both themes** — a lightbox on
   the dark page, and the only pure white on the light one. Shiki writes its
   colours as an *inline* style that no stylesheet can beat, and the CSS
   consuming its dual-theme custom properties did not exist. Fixed with
   `defaultColor: false` plus rules that pin the block to the terminal's own
   surface, so it matches the hero terminal. Measured after: `#191614` in dark.
2. **No `body` margin reset.** Every full-bleed band — the nav hairline, each
   section border, the footer rule — stopped 8px short of both edges, and every
   page carried 16px of phantom vertical scroll. `html { background }` hid it.
   Measured after: `margin: 0px`, nav at `left: 0`.
3. **The sticky-footer layout was dead code.** Three pages set `flex: 1 0 auto`
   on `<main>` while `body` was `display: block`, so the footer floated
   mid-viewport with 211px of empty background below it on the 404. `.rt-page`
   is now the flex column those pages assume, on `100dvh`. Measured after:
   footer bottom 812 in an 812px viewport.
4. **A raw HTML entity printed as literal text** — "don&rsquo;t" — in the FAQ
   question added during Round 1 (escaped interpolation, not `set:html`).
   `copy-lint` had *laundered* it: its decoder resolved `&amp;` first, so the
   check never saw it. Both the copy and the laundering are fixed, and
   `copy-lint` now fails on any double-escaped entity.

### Should-fix — applied

5. **`robots.txt` hard-coded an absolute URL** — the only one outside
   `astro.config.mjs`, which made the documented "two-line switch" to
   getrite.ai untrue. Now a generated route, like the webmanifest.
6. **`robots.txt` is inert on a Pages project page** (crawlers read it only at
   the origin root). Documented in `DEPLOY.md`.
7. **The deploy workflow never ran on a pull request**, so `copy-lint` — the
   whole voice-and-claims tripwire — gated nothing: a bad copy edit would pass
   every required check, merge, and fail afterwards. It now builds and lints on
   PRs and deploys only from `main`.
8. **The workflow's own comment was factually wrong:** `ci.yml` has no `paths:`
   filter and `publish-gate.yml` is deliberately unfiltered, so the website
   filter never stopped the Python matrix running. Comment corrected with the
   one-line fix and an explicit "leave publish-gate.yml alone".
9. **Proof links pointed at `blob/main` with line anchors.** All nine were
   exact, and one insertion above any of them would silently turn each into a
   confident link to the wrong code — on a site whose whole argument is that
   the links are real. Now pinned to a `PROOF_REF` tag; verified the line
   numbers hold at `v0.7.0a10`.
10. **Canonicals had trailing slashes and internal links did not**, costing a
    301 on every nav click and splitting the link graph. `url()` now appends a
    slash to page routes and never to files.
11. **The 404 was indexable and self-canonicalising.** Now `noindex`.
12. **The boundary table had no accessible name.** Added an sr-only `<caption>`.
13. **The Copy buttons gave sighted users no feedback and failed silently.** The
    label now flips to "Copied", and a refused clipboard says "Press ⌘C" and
    selects the text instead of looking dead.
14. **17 arrow glyphs were read aloud** as "north east arrow". Now
    `aria-hidden`.
15. **`<main>` was not focusable**, so the skip link does not move focus in
    WebKit. `tabindex="-1"` added on all six.
16. **The terminal `<figure>` had no accessible name** — `aria-label` sat on a
    role-less `<pre>`. Moved to the figure as a labelled group.
17. **Dark mode showed a bare 13px flame and no wordmark**, reading as a broken
    image: the pack's horizontal lockup has an ink wordmark, and its white
    lockup is the *stacked* version, too tall for a 30px nav. The wordmark is
    now set as text in dark mode only, so light mode keeps the official
    lockup. See `BRAND-ASSETS.md` — a horizontal off-white lockup replaces this
    and lets the 88 KB light PNG go too.
18. **The nav's scroll-fade mask ran when nothing was scrollable**, dimming
    "GitHub" to ~0.58 alpha and signalling "scroll for more" when there wasn't.
    Dropped below 640px, where all four links fit.
19. **The nav was sticky on phones, contradicting its own comment** and eating
    12% of a 375×812 viewport. Now static below 640px, as documented.
20. **"132,321" read as "132 , 321"** — a monospace comma takes a full advance
    width, on the most load-bearing figure on the page. Now tabular sans.
21. Smaller: a file path broke mid-token as "bi|lling" (now breaks after the
    slashes); a code chip followed by punctuation showed a gap; two dead `id`
    attributes; `rel="noopener"` without `target`; the RSS feed's missing
    self-link and `lastBuildDate`, and a docstring that claimed full-content
    RSS; the blog index reading thin with one post ("Two more are in draft.").

### Noted, not applied

- **The two logo PNGs are 141 KB** for marks drawn at 55×30 and 13×30. Needs
  SVG from the brand tool — tracked in `BRAND-ASSETS.md`. The dark-mode fix
  above already removes one of the two fetches in dark mode.
- **37 inline `style=` attributes** duplicating spacing tokens. A refactor with
  no user-visible effect; left for a quieter moment.
- **The blog post is a 653px column in a 1120px container**, with no meta rail
  like the blog index has. A design decision for the owner.
- **`--measure: 64ch` is ~76 characters**, slightly past the comfortable band.
  `tokens.css` is generated from `tokens.json`, so this belongs upstream in the
  design system rather than as a hand edit.
- **`markdown.rehypePlugins` is deprecated in Astro 7** (it warns, it works).
  Migrate to `unified({...})` before Astro 8.
- **`astro check` emits 13 `'z' is deprecated` hints** from the content schema.
  0 errors, 0 warnings.
