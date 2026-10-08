rite is a command-line orchestrator for fleets of AI coding agents. It is open source (MIT), runs locally and is in alpha. This system is how its public surfaces look and sound, starting with getrite.ai. Every page has one job: to hold up when a sceptical senior engineer reads it for ninety seconds. The visual system is quiet on purpose. The product's face is real terminal output and the mechanism behind it, not decoration.

## Content fundamentals

**Voice.** Write like a senior engineer explaining a system to another senior engineer, including the parts that are not finished. Be confident, technical, specific and unusually honest. If a sentence would look out of place in the repository's own README, cut it.

- **Concrete nouns and real commands.** Write `rite claim`, `rite plan approve` and `workers/alpha/`, not "intelligent coordination".
- **Numbers carry their conditions.** Write "132,321 grants in a four-minute soak of six concurrent Workers, zero lost". Never write a bare "132,321 grants". Every proof-point figure links to the line it comes from.
- **Say what it doesn't do in the same breath.** For example: "`rite loop` tells you the run has stalled and what is holding it. It starts nothing."
- **Short Anglo-Saxon verbs:** runs, claims, refuses, holds, checks, reports.
- **Blunt is fine:** "If you run one session at a time, you do not need this."
- **Person.** Use *you* for the reader. Use *rite* for the product, always lowercase, even at the start of a sentence. Avoid *we*, except in changelog-style blog posts where the owner is writing.
- **Casing.** Use sentence case for headings and buttons. Product nouns from the code keep their capital: Worker, Manager, Owner.
- **Spelling.** British (licence, colour, grey), except for terms that match the code: *artifact*, *judgment* in quotes from the repo.
- **Punctuation.** Use em-dash asides and parentheticals for caveats. Never use exclamation marks.
- **Emoji.** None in body copy. A single ⚠ is allowed before a real caveat.

**Banned words.** The copy linter in the handover fails the build on any of these: *revolutionary, seamless, powerful, cutting-edge, enterprise-grade, next-generation, game-changing, effortless, blazing-fast, world-class, unlock, unleash, supercharge, elevate, empower, transform your workflow, the future of, at scale, 10x, dramatically, significantly, massively, AI-powered*.

**Don't anthropomorphise.** Agents don't *collaborate*, *think*, *understand* or form a *team*. They run, claim, write and report.

**No implied social proof.** No "trusted by", logo strips, testimonials, star counts, download counts or "join thousands".

**The words that get a site mocked.** Never call the sandbox *secure*, *isolated*, *contained* or *airtight*. It bounds files, not capability. Never say "your code never leaves your machine". That is false for a Claude fleet. Never write *production-ready*, *stable*, *autonomous* or *runs unattended*. The one exception is the FAQ question "Is it production-ready?", answered "No." The copy linter catches these words and phrases, with a short reviewed allow-list of honest sentences like that one; the line-by-line §12 pass before launch catches the rest.

**Missing facts.** If copy needs a claim you can't point at a file to support, write `TODO(owner)` instead of something plausible.

**Real copy, for calibration:**

> Your agents can't skip the review step.

> A `CLAUDE.md` that says "review before you open a PR" is a request. Deep into a long session, it is a suggestion.

> rite cannot tell a crashed session from one thinking hard, and releasing a path under a live Worker is worse than a stale claim. So it names the claim, the holder and the command, and waits for you.

## Visual foundations

**Colour.** The palette is four brand colours, all warm.

- `crimson` (#B82D22) is the primary. `ember` (#E8743B) is the secondary. `brand-ink` (#211E1B) and `brand-paper` (#FAF7F2) are the neutrals.
- All greys come from mixing ink into off-white. A cool or neutral grey reads as a mistake.
- Use semantic tokens, never raw brand colours, for UI:
  - **Text:** set body text in `text` on `surface` or `surface-raised`. Use `text-muted` for meta, conditions, eyebrows and status icons. `text` is the theme's reading colour. The brand colour Ink is `brand-ink`, which is the same value as `text` in light mode only.
  - **Links:** `accent` is crimson on light and ember on dark, because crimson on ink fails at 2.7:1. Hover is `accent-hover`.
  - **Primary button:** `action` with text in `on-action`. That is off-white on crimson in light, ink on ember in dark. Never white on ember.
- **One accent per viewport.** Crimson should mean something. Use it for one primary button, links and the status label words (Not proven, Not built, Caveat), and nothing else. Step numbers, icons and diagram lines are `text-muted` or `border-strong`, never accent.
- `ember` is a mark colour on light: the gradient bar, the active-stage glow, a second data series. Never set ember text on off-white (2.8:1).
- **Never colour alone.** Status rows carry an icon and, wherever they don't work as described, a word: Not proven, Not built, Caveat or By design. `status-works` and `status-design` are plain `text`. `status-gap` is the accent, on the label word only.
- Every text pair in both themes passes WCAG AA. Each token's usage note states its ratio.

**Dark theme.** Build it, because this audience runs dark editors.

- **Colours:** the surface is ink and the text is off-white. The accent switches to ember. The terminal stays dark in both themes and sits one step below the page in dark (`term-bg`).
- **Switching:** the site follows `prefers-color-scheme`, and `data-theme="light"` or `"dark"` on `<html>` forces a theme. This system's `tokens.css` declares each theme under `[data-theme]` only. The site build generates its own `tokens.css` from the same `tokens.json`, adding a `prefers-color-scheme` block and the matching logo swap. Use that file on a live site. Declare `color-scheme` so form controls and scrollbars follow.
- **No toggle in v1.**

**Type.** Two families, both IBM Plex: Plex Sans for everything read and Plex Mono for everything typed.

- **Headings:** `rt-display` 64/68 is the home hero only. Below 1024px it drops to `rt-display-sm` 40/44, which also serves as the secondary-page H1. Then `rt-h2` 32/40 for sections and `rt-h3` 20/28 for cards, steps and FAQ.
- **Text:** `rt-lead` 20/32 (18/28 on phones) for subheads and first paragraphs, capped at 56ch. `rt-body` 17/28 for all running text, capped at `measure` (64ch, about 70–75 characters). `rt-body-sm` 15/24 for cards and conditions. `rt-meta` 13/20 for dates and captions.
- **Titles:** page H1s use the `rt-title-hero` (home) and `rt-title` (other pages and posts) classes, which step up at 1024px. In posts, `rt-h2-sm` (24/32) sets the section headings.
- **Mono:** `rt-code` 15/24 for terminal and code blocks, and `rt-code-inline` for commands in text on `surface-sunken`. `rt-eyebrow` 12/16 is uppercase, tracked +0.08em and in `text-muted`. Use an eyebrow only where it adds something the H2 doesn't. `rt-figure` 40/44 is the number in a proof point.
- **Hosting:** self-host the fonts on the live site (WOFF2, latin subset, weights 400/500/600). No third web font.

**Spacing.** Use a 4px base: `space-1` 4 through `space-10` 128.

- **Side gutters:** `space-4` (16px) on phones and `space-6` (32px) from 768px. Never more.
- **Sections:** `space-8` (64px) top and bottom on phones, `space-9` (96px) from 1024px, separated by a `hairline` rule in `border`. One band per page may sit on `surface-raised` (`rt-section-band`), for the proof strip. It drops the rule on both sides.
- **Gaps:** `space-5` (24px) between cards and inside them.

**Layout.**

- Single column inside `container` (1120px). Running text never exceeds `measure`. Two-column only where a diagram sits beside its explanation, and the diagram sits inside the step it explains at every width.
- **Layout classes:** use these instead of inline sizes.
  - `rt-page-head`: page header padding, with `rt-page-head-hero` for `space-10` at the top of the home page.
  - `rt-split`: two columns at `minmax(440px)`.
  - `rt-grid-3`: up to three columns at `minmax(300px)`.
  - `rt-cards`, `rt-proofs`: auto-fit grids that collapse to one column on a phone with no breakpoints.
  - `rt-narrow`: `narrow`, 880px, for the FAQ and the notification strip.
  - `rt-note`: an aside under a section.
- No horizontal page scroll at 375px. Terminal and code blocks scroll inside their own box.

**Borders, radii, shadows.** Use borders, not shadows. No token family for shadows exists, and none should be added.

- **Borders:** every rule is a 1px `hairline` in `border`. Control outlines use `border-strong` (3:1).
- **Radii:** `radius-md` (6px) for buttons, inputs, cards and terminals. `radius-sm` (3px) for chips, tags and inline code. `radius-lg` (10px) for the one notification panel. `radius-pill` only for stage dots.
- **No left-border accent cards.** The only left border is the prose blockquote, and it is neutral.

**States.**

- **Hover:** links thicken their underline and move to `accent-hover`. The primary button moves to `action-hover`. Secondary buttons gain `surface-raised` and an ink outline.
- **Focus:** every focusable element gets the same ring, a 2px solid `focus` outline at 2px offset, following the element's own radius. Inside the terminal the ring is ember. A skip link (`rt-skip`) appears on first Tab.
- **Touch targets:** 44px minimum height for standalone controls and links. A smaller visual, such as the chip or the terminal Copy button, gets a 44px hit area through `::after`. Links inside a sentence or a list of sources keep the line height (WCAG's inline exception).
- **Page ground:** put `rt-page` on `<body>` (or set `html { background: var(--surface) }`), so overscroll and short pages never show the browser's own canvas colour.

**Motion.** Almost none.

- Allowed: the FAQ chevron rotates in 150ms, and a page may have at most one subtle reveal.
- Respect `prefers-reduced-motion`.
- Never use animated fire, flicker, particles, parallax or a hero video.

**Imagery.**

- **Use:** real terminal output, set as selectable text in `rt-code`, copied unedited from the repository. Diagrams that show the real mechanism: the claim map and the stage chain.
- **Never use:**
  - Stock photos or people at laptops.
  - Glowing brains, neural meshes, circuit traces or robots.
  - Blue-purple gradients, glassmorphism or 3D blobs.
  - An invented dashboard. rite is a CLI, and a fake UI is the most damaging asset this site could ship.

**The flame motif.** It means focused heat where the work is, never speed or destruction.

- **Allowed:**
  - The mark in the nav lockup and the favicon.
  - One crimson→ember gradient: the 3px bar on top of the terminal.
  - `ember-glow` behind the one active element of a diagram.
  - At most one low-opacity mark texture (`rt-flame-bg`, 7%, anchored inside the container) per page.
- **Not allowed:** flames on cards, bullets or dividers, or fire puns in copy.

## Logos and assets

The brand pack is raster PNG. Use the files as supplied. Never redraw, re-letter or recolour them.

- **Light surfaces:** `Logos/rite-logo-horizontal.png` in the nav at 30px tall. The footer uses the colour mark `Logos/rite-mark.png`, because it reads in both themes.
- **Dark surfaces:** the full-colour `Logos/rite-mark.png` alone, because the pack has no horizontal lockup with an off-white wordmark. Use `Logos/rite-logo-white.png` (monochrome vertical) only where a single ink is required. ⚠ TODO(assets): ask for a horizontal lockup for dark backgrounds, and for an SVG of the flame. The pack's own README offers to regenerate a crisp vector set from one.
- **Favicons and app icons:** `Icons/` holds `favicon-*.png`. The `favicon-tile-*` versions are for dark browser tabs. It also holds `apple-touch-icon.png` and `icon-192/512`. `maskable-512` is for the manifest. `favicon.ico` ships with the site files rather than here.
- **Social:** `Social/og-banner-1200x630.png` is the default OG and Twitter image. `Social/avatar-512.png` is for GitHub and social profiles.
- **Clearspace:** at least the height of the wordmark's "i" dot around any lockup. Minimum nav height is 24px.

## Iconography

There is no icon font. Icons are inline stroke SVGs on a 20×20 grid with stroke 1.75, round caps and joins, and `currentColor`, so they take the text colour. Use the smallest set that carries meaning:

| Icon | Means | Path data (`viewBox="0 0 20 20"`) |
|---|---|---|
| check | Works / shipped | `M4 10.5l4 4 8-9` |
| warning | Not proven, Not built, Caveat | `M10 3l8 14H2z` `M10 8.5v4` `M10 15h.01` |
| external | Leaves the site (GitHub, SPEC.md) | `M7 13L13 7` `M8 7h5v5` |
| copy | Copy command | rect `x5 y5 w10 h10 rx1.5` + `M3 12V4.5A1.5 1.5 0 0 1 4.5 3H12` |
| lock | Claim held | rect `x4 y9 w12 h8 rx1.5` + `M7 9V6.5a3 3 0 0 1 6 0V9` |
| refused | Claim refused | `M5 5l10 10` `M15 5L5 15` |
| minus | By design | `M5 10h10` |
| chevron | FAQ disclosure | `M5 8l5 5 5-5` |
| rss | Blog feed | circle `cx5 cy15 r1.25` (filled) + `M4 9a7 7 0 0 1 7 7` + `M4 4a12 12 0 0 1 12 12` |

No emoji as icons. The ⚠ glyph may appear in copy before a real caveat, and nowhere else. Mark external links with the ↗ glyph as text after the label, on links and buttons alike.

## Components

The components are plain CSS classes in `components/bundle.css`, loaded after `tokens.css`. No JavaScript is required except the copy buttons. Each component's card below gives its markup.

- **Page furniture:**
  - **SiteNav** is sticky. It holds the lockup, What works · Blog · Licensing · GitHub ↗, and the version chip.
  - **SiteFooter** carries no copyright line for a company that doesn't exist.
- **Product truth:**
  - **Terminal**: real output only.
  - **InstallCommand**: the copyable install line plus the verify-first link.
  - **ProofPoint**: a figure, its conditions and a source link.
  - **StatusList**: Works today, then Not proven, Not built, Caveat and By design rows.
  - **Caveat**: the inline caveat line, with a muted warning icon.
  - **Table**: proof links and the network boundary.
- **Explanation:**
  - **StageChain**: the gated pipeline, with gate marks and the return path to plan.
  - **ClaimMap**: one path held, a second claim refused. It sits inside step 2.
  - **Steps**: How it works, each step with its own figure.
  - **FeatureCard**: capability, mechanism and caveat.
  - **FAQ**: native `<details>`.
- **Conversion:**
  - **Button**: primary is for *Read the spec* only.
  - **VersionChip**: `v0.7.0a10 · alpha`, linking to the changelog.
  - **NotifyStrip**: release notifications only, never "early access". It is not rendered without a backend.
- **Blog:**
  - **PostCard**: list item with date, category and dek.
  - **PostHeader**: back link, H1 and byline, including the version the post applies to.
  - **Prose**: article typography plus a required Sources list.
  - **Tag**: one of three fixed categories.

## The blog

Posts follow the same rules as the site, and one more: every post ends with a **Sources** list linking the files, commits or SPEC sections it describes. The build fails if a post has no sources. There are three categories, as tags:

- *Changelog note*: what changed and why, including "we fixed this" admissions.
- *Design decision*: a SPEC decision explained.
- *Measurement*: how a number was produced.

Dates use the format `8 Oct 2026`. The byline is the author's name plus "Applies to vX", and posts have no author photos. Posts never get a comments section, share buttons or a newsletter pitch beyond the release-notifications strip.
